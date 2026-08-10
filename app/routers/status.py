from typing import Optional

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..db.mongodb import get_db

router = APIRouter(prefix="/api/v1/status", tags=["status"])


@router.get("")
async def current_status(device_id: Optional[str] = None, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Frontend-shaped summary view — distinct from GET /readings/latest's raw stored-document shape
    (ARCHITECTURE.md §3): current category, dominant pollutant, and per-pollutant WHO ratios, for
    one device or all devices.
    """
    if device_id:
        doc = await db.readings.find_one({"device_id": device_id}, sort=[("timestamp", -1)])
        docs = [doc] if doc else []
    else:
        pipeline = [
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$device_id", "doc": {"$first": "$$ROOT"}}},
        ]
        grouped = await db.readings.aggregate(pipeline).to_list(length=None)
        docs = [g["doc"] for g in grouped]

    data = [
        {
            "device_id": d["device_id"],
            "timestamp": d["timestamp"],
            "category": d.get("category", "Good"),
            "dominant_pollutant": d.get("dominant_pollutant"),
            "who_scores": d.get("who_scores", {}),
        }
        for d in docs
    ]

    return {"data": data, "meta": {"count": len(data)}}
