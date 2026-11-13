"""NASA FIRMS Area API client.

Failure philosophy: the network is an enhancement, never a dependency.
Every call returns a FetchResult with an explicit status; no exception
escapes this module. Retry policy: bounded exponential backoff, Honour the
documented 5,000-transactions/10-min quota by never auto-looping.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

import httpx

from ..config import Settings, get_settings

SOURCES_MODIS = ["MODIS_NRT"]
SOURCES_VIIRS = ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]
# S-NPP NRT ceased 2026-11-01 (FIRMS notice); archive sources for history:
SOURCES_VIIRS_ARCHIVE = ["VIIRS_SNPP_SP"]
SOURCE_DEFAULTS = {
    "MODIS": SOURCES_MODIS,
    "VIIRS": SOURCES_VIIRS,
}


@dataclass(slots=True)
class FetchResult:
    source: str
    status: str                 # OK | HTTP_ERROR | UNREACHABLE | NOT_CONFIGURED | EMPTY
    http_status: int | None = None
    text: str = ""
    error: str = ""
    elapsed_ms: int = 0
    attempts: int = 0
    url: str = field(default="", repr=False)


def build_area_url(
    settings: Settings, source: str, bbox: tuple[float, float, float, float],
    day_range: int, day: date | None
) -> str:
    west, south, east, north = bbox
    area = f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"
    url = (
        f"{settings.firms_base_url.rstrip('/')}/api/area/csv/"
        f"{settings.firms_map_key}/{source}/{area}/{day_range}"
    )
    if day is not None:
        url += f"/{day.isoformat()}"
    return url


def fetch_area_csv(
    source: str,
    bbox: tuple[float, float, float, float],
    day_range: int,
    day: date | None = None,
    settings: Settings | None = None,
) -> FetchResult:
    """Fetch one FIRMS area CSV. Never raises."""
    cfg = settings or get_settings()
    if not cfg.firms_map_key:
        return FetchResult(source=source, status="NOT_CONFIGURED", error="FIRMS_MAP_KEY not set")
    url = build_area_url(cfg, source, bbox, day_range, day)

    last_error = ""
    last_status: int | None = None
    start = time.monotonic()
    attempts_allowed = max(1, cfg.firms_max_retries + 1)
    for attempt in range(attempts_allowed):
        try:
            with httpx.Client(timeout=cfg.firms_timeout_seconds) as client:
                resp = client.get(url, headers={"User-Agent": "EmberEye/1.0 (NASA Space Apps 2026)"})
            last_status = resp.status_code
            if resp.status_code == 200:
                elapsed = int((time.monotonic() - start) * 1000)
                text = resp.text
                if not text.strip() or text.strip().lower().startswith("invalid"):
                    return FetchResult(source, "EMPTY", resp.status_code, text,
                                       "empty or rejected payload", elapsed, attempt + 1, url)
                return FetchResult(source, "OK", resp.status_code, text, "", elapsed, attempt + 1, url)
            last_error = f"HTTP {resp.status_code}"
        except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
            last_error = type(exc).__name__
        if attempt + 1 < attempts_allowed:
            time.sleep(1.5 * (2 ** attempt))
    elapsed = int((time.monotonic() - start) * 1000)
    status = "HTTP_ERROR" if last_status is not None else "UNREACHABLE"
    return FetchResult(source, status, last_status, "", last_error, elapsed, attempts_allowed, url)


def check_key() -> dict:
    """FIRMS MAP_KEY status probe; returns a plain dict with explicit keys."""
    cfg = get_settings()
    if not cfg.firms_map_key:
        return {"key_configured": False, "reachable": None, "detail": "no key"}
    url = f"{cfg.firms_base_url.rstrip('/')}/api/map_key/{cfg.firms_map_key}"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers={"User-Agent": "EmberEye/1.0"})
        if resp.status_code == 200:
            try:
                return {"key_configured": True, "reachable": True, "detail": resp.json()}
            except ValueError:
                return {"key_configured": True, "reachable": True, "detail": "non-json"}
        return {"key_configured": True, "reachable": True,
                "detail": f"HTTP {resp.status_code}"}
    except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
        return {"key_configured": True, "reachable": False, "detail": type(exc).__name__}
