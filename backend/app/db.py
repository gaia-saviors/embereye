"""Database engine, session factory and declarative base.

Portable across PostgreSQL (production, docker-compose) and SQLite
(local dev, CI). All model constructs are dialect-portable by design:
no ARRAY columns, no vendor-specific DDL.

`describe_url` / `mask_url` are the single source of truth for how scripts
and the app log *where* the data lives - never a password.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# DB URL introspection (used by scripts/backfill_firms.py, scripts/audit_real.py
# and the app startup log). Masks credentials, prints engine + host/path.
# ---------------------------------------------------------------------------
def mask_url(url: str) -> str:
    """Credentials masked: postgresql+psycopg://user:***@host:port/dbname."""
    try:
        from sqlalchemy.engine import make_url

        u = make_url(url)
        if u.password:
            u = u.set(password="***")
        return str(u)
    except Exception:  # pragma: no cover - defensive
        return "<unparseable-db-url>"


def describe_url(url: str) -> str:
    """Human one-liner: engine + host:port or file path + database name."""
    try:
        from sqlalchemy.engine import make_url

        u = make_url(url)
        backend = u.get_backend_name()
        if backend == "sqlite":
            # make_url().database keeps the leading "/" of absolute paths
            # (sqlite:////tmp/x.db -> /tmp/x.db) and relative paths verbatim
            # (sqlite:///./embereye_dev.db -> ./embereye_dev.db).
            return f"sqlite file: {u.database or '(in-memory)'}"
        driver = u.get_driver_name() or ""
        return (f"{backend} ({driver}) host {u.host}:{u.port or 5432}, "
                f"database {u.database}")
    except Exception:  # pragma: no cover - defensive
        return "<unparseable-db-url>"


def database_url_from_env() -> str | None:
    """The env var(s) the app and scripts actually read - or None.

    The Settings default (sqlite:///./embereye_dev.db) is a convenience for
    tests/local dev only. Scripts must NOT fall back to it silently: they
    resolve the URL via this function and fail loudly when it is unset
    (the v1.0.3 lesson: 171k real rows landed in an unnoticed SQLite file).
    """
    import os

    for name in ("EMBEREBEYE_DATABASE_URL", "EMBEREBEYE_DB_URL"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _make_engine(url: str):
    kwargs: dict = {"echo": get_settings().sql_echo}
    if url.startswith("sqlite"):
        from sqlalchemy.pool import NullPool
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 60}
        kwargs["poolclass"] = NullPool
    else:
        kwargs.update({"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20})
    return create_engine(url, **kwargs)


engine = _make_engine(get_settings().database_url)

if engine.url.get_backend_name() == "sqlite":

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - env specific
        cursor = dbapi_conn.cursor()
        cursor.execute(f"PRAGMA journal_mode={get_settings().sqlite_journal_mode}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    from . import models  # noqa: F401 - register mappings

    Base.metadata.create_all(bind=engine)
    ensure_schema()


# Additive migrations for databases created before v1.0.5 (existing docker
# volumes): Hotspot provenance columns (v1.0.2) and the Phase 2 columns
# (recurring_source tag, comparable-subset harmonized series with prediction
# intervals and separate static/night layers, season censoring).
# Idempotent, dialect-portable; booleans get a server default so existing
# rows read as False instead of NULL.
_HOTSPOT_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("firms_product", "ALTER TABLE hotspots ADD COLUMN firms_product VARCHAR(8)"),
    ("firms_type", "ALTER TABLE hotspots ADD COLUMN firms_type INTEGER"),
)

_PHASE2_MIGRATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "hotspots": (
        ("recurring_source", "ALTER TABLE hotspots ADD COLUMN recurring_source BOOLEAN DEFAULT FALSE"),
    ),
    "harmonized_daily": (
        ("n_comparable_modis", "ALTER TABLE harmonized_daily ADD COLUMN n_comparable_modis INTEGER DEFAULT 0"),
        ("n_comparable_viirs", "ALTER TABLE harmonized_daily ADD COLUMN n_comparable_viirs INTEGER DEFAULT 0"),
        ("n_harmonized_lo", "ALTER TABLE harmonized_daily ADD COLUMN n_harmonized_lo FLOAT"),
        ("n_harmonized_hi", "ALTER TABLE harmonized_daily ADD COLUMN n_harmonized_hi FLOAT"),
        ("frp_harmonized_lo_mw", "ALTER TABLE harmonized_daily ADD COLUMN frp_harmonized_lo_mw FLOAT"),
        ("frp_harmonized_hi_mw", "ALTER TABLE harmonized_daily ADD COLUMN frp_harmonized_hi_mw FLOAT"),
        ("n_static", "ALTER TABLE harmonized_daily ADD COLUMN n_static INTEGER DEFAULT 0"),
        ("n_night", "ALTER TABLE harmonized_daily ADD COLUMN n_night INTEGER DEFAULT 0"),
        ("static_share", "ALTER TABLE harmonized_daily ADD COLUMN static_share FLOAT"),
    ),
    "season_metrics": (
        ("is_partial", "ALTER TABLE season_metrics ADD COLUMN is_partial BOOLEAN DEFAULT FALSE"),
    ),
}


def ensure_schema() -> None:
    """Add missing columns to existing tables (no destructive changes)."""
    inspector = inspect(engine)
    names = inspector.get_table_names()
    is_sqlite = engine.dialect.name == "sqlite"
    for table, migrations in _PHASE2_MIGRATIONS.items():
        if table not in names:
            continue
        present = {c["name"] for c in inspector.get_columns(table)}
        for column, ddl in migrations:
            if column in present:
                continue
            stmt = ddl.replace("DEFAULT FALSE", "DEFAULT 0") if is_sqlite else ddl
            with engine.begin() as conn:
                conn.execute(text(stmt))
    if "hotspots" not in names:
        return
    present = {c["name"] for c in inspector.get_columns("hotspots")}
    for column, ddl in _HOTSPOT_MIGRATIONS:
        if column not in present:
            with engine.begin() as conn:
                conn.execute(text(ddl))
