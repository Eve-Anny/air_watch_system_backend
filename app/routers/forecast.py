from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..db.mongodb import get_db
from ..services import ml_inference

router = APIRouter(prefix="/api/v1/forecast", tags=["forecast"])


@router.get("")
async def get_forecast(
    device_id: str,
    horizon_hours: int = Query(6, ge=1, le=24),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Response contract shape is unchanged from Phase 4 (the Phase 6 frontend can rely on it) — only
    services/ml_inference.py's internals changed once Phase 5's real models were wired in. Each
    prediction step also carries `pollutant_ratios` (WHO-guideline ratios, same units as
    `who_scores.ratios` on a reading) so the frontend can chart the forecast on the same
    guideline-multiple axis TrendChart.tsx uses for history, instead of raw mg/m3-vs-ug/m3 values.

    Fetches the 2 most recent readings, not just the latest: the trained models use a lag1 feature
    (each pollutant's previous-hour rolling average), so a second data point is needed to build real
    features. Falls back gracefully (see ml_inference._extract_features) if there's no previous
    reading yet — e.g. a device's very first reading.
    """
    recent = await db.readings.find({"device_id": device_id}).sort("timestamp", -1).limit(2).to_list(length=2)
    latest = recent[0] if recent else None
    previous = recent[1] if len(recent) > 1 else None

    ml_now = ml_inference.predict_classification_now(latest, previous)
    predictions = ml_inference.predict_forecast(latest, previous, horizon_hours)

    return {
        "data": {
            "device_id": device_id,
            "generated_at": datetime.now(timezone.utc),
            "model_status": ml_inference.MODEL_STATUS,
            "ml_classification_now": ml_now,
            "horizon_hours": horizon_hours,
            "predictions": predictions,
        },
        "meta": {"count": len(predictions)},
    }
