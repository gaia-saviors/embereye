"""EmberEye FastAPI application."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import create_all, describe_url
from .routers import analytics, aois, calendar, export, hotspots, live, meta

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("embereye.app")


def create_app() -> FastAPI:
    cfg = get_settings()
    app = FastAPI(
        title="EmberEye API",
        version=cfg.app_version,
        description=(
            "Harmonization of MODIS and VIIRS hot spots - burning activity "
            "calendar, GIS analytics and export. Gaia Saviors, NASA Space "
            "Apps Challenge 2026."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    @app.middleware("http")
    async def public_read_only_guard(request: Request, call_next):
        cfg = get_settings()
        if (
            cfg.public_read_only
            and request.url.path.startswith("/api/")
            and request.method not in {"GET", "HEAD", "OPTIONS"}
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "This public EmberEye demo is read-only."},
                headers={"Cache-Control": "no-store"},
            )
        return await call_next(request)

    # Added after the guard so CORS wraps and decorates its 403 responses too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Result-Truncated",
            "X-Result-Total",
            "X-Result-Total-Exact",
            "X-Result-Limit",
            "X-Export-Truncated",
            "X-Export-Total",
            "X-Export-Limit",
        ],
    )
    app.include_router(meta.router, prefix="/api", tags=["system"])
    app.include_router(aois.router, prefix="/api", tags=["aois"])
    app.include_router(hotspots.router, prefix="/api", tags=["hotspots"])
    app.include_router(calendar.router, prefix="/api", tags=["calendar"])
    app.include_router(analytics.router, prefix="/api", tags=["analytics"])
    app.include_router(export.router, prefix="/api", tags=["export"])
    app.include_router(live.router, prefix="/api", tags=["live"])

    @app.on_event("startup")
    def _startup() -> None:
        # Say exactly where the data lives (never credentials) - the
        # silent-SQLite-fallback lesson from the v1.0.3 backfill.
        cfg = get_settings()
        log.info("database: %s", describe_url(cfg.database_url))
        if cfg.public_read_only:
            # The public Compose profile starts only after its one-shot seeder
            # completes. A prebuilt SQLite archive also already has its schema.
            # Do not run create_all/ensure_schema on a read-only deployment.
            log.info("public read-only mode: skipping schema DDL; database must be initialized before startup")
        else:
            create_all()

    return app


app = create_app()
