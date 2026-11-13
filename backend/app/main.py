"""EmberEye FastAPI application."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
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
        log.info("database: %s", describe_url(get_settings().database_url))
        create_all()

    return app


app = create_app()
