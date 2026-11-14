"""Public SQLite deployment must remain strictly read-only."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


def test_sqlite_read_only_uri_allows_select_and_rejects_write(tmp_path):
    database = tmp_path / "public.db"
    writer = create_engine(f"sqlite:///{database}")
    with writer.begin() as connection:
        connection.execute(text("CREATE TABLE sample (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO sample (id) VALUES (1)"))
    writer.dispose()

    # SQLAlchemy's SQLite URI form needs the explicit file: prefix when
    # query parameters (mode=ro) are interpreted by sqlite3 rather than as a
    # filename suffix.
    reader = create_engine(f"sqlite:///file:{database}?mode=ro&uri=true")
    with reader.connect() as connection:
        assert connection.execute(text("SELECT id FROM sample")).scalar_one() == 1
    with pytest.raises(OperationalError, match="readonly|read-only"):
        with reader.begin() as connection:
            connection.execute(text("INSERT INTO sample (id) VALUES (2)"))
    reader.dispose()
