from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..db.mongodb import get_db
from ..services import ml_inference

router = APIRouter(prefix="/api/v1/health", tags=["health"])


@router.get("")
async def health_check(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Liveness/readiness check — used by the hosting platform and as a defense-day sanity check."""
    try:
        await db.command("ping")
        db_connected = True
    except Exception:
        db_connected = False

    return {
        "status": "ok" if db_connected else "degraded",
        "database_connected": db_connected,
        "model_status": ml_inference.MODEL_STATUS,
    }
