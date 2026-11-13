"""Live FIRMS fetch endpoints - the network is an enhancement, not a dependency."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Aoi
from ..schemas import LiveFetchRequest, LiveFetchResponse
from ..seed.live_ingest import ingest_firms_for_aoi

router = APIRouter()


@router.post("/live/fetch", response_model=LiveFetchResponse)
def live_fetch(payload: LiveFetchRequest, db: Session = Depends(get_db)) -> LiveFetchResponse:
    aoi = db.get(Aoi, payload.aoi_id)
    if aoi is None:
        raise HTTPException(404, "AOI not found")
    result = ingest_firms_for_aoi(db, aoi, day_range=payload.day_range, day=payload.day)
    return LiveFetchResponse(
        status=result["status"],  # type: ignore[arg-type]
        mode=result["mode"], message=result["message"], inserted=result["inserted"],
        deduped=result["deduped"], rejected=result["rejected"],
        per_source=result["per_source"],
    )


@router.get("/live/status")
def live_status(db: Session = Depends(get_db)) -> dict:
    from ..ingest.firms_client import check_key
    from ..routers.meta import _firms_status

    return {"firms_key": check_key(), "history": _firms_status(db).model_dump(mode="json")}
