from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from ..core.serialization import serialize_alert
from ..db.mongodb import get_db
from ..models.alert import AlertAcknowledgeIn

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(
    device_id: Optional[str] = None,
    level: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = Query(100, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    query: dict = {}
    if device_id:
        query["device_id"] = device_id
    if level:
        query["level"] = level
    if acknowledged is not None:
        query["acknowledged"] = acknowledged
    if start or end:
        query["triggered_at"] = {}
        if start:
            query["triggered_at"]["$gte"] = start
        if end:
            query["triggered_at"]["$lte"] = end

    cursor = db.alerts.find(query).sort("triggered_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    data = [serialize_alert(d) for d in docs]
    return {"data": data, "meta": {"count": len(data)}}


@router.get("/active")
async def active_alerts(device_id: Optional[str] = None, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Shortcut for unresolved alerts — what the dashboard's live alert panel polls."""
    query: dict = {"resolved_at": None}
    if device_id:
        query["device_id"] = device_id

    docs = await db.alerts.find(query).sort("triggered_at", -1).to_list(length=None)
    data = [serialize_alert(d) for d in docs]
    return {"data": data, "meta": {"count": len(data)}}


@router.patch("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str, payload: AlertAcknowledgeIn, db: AsyncIOMotorDatabase = Depends(get_db)
):
    try:
        oid = ObjectId(alert_id)
    except InvalidId:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid alert_id")

    result = await db.alerts.find_one_and_update(
        {"_id": oid},
        {
            "$set": {
                "acknowledged": True,
                "acknowledged_at": datetime.now(timezone.utc),
                "acknowledged_by": payload.acknowledged_by,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "alert not found")
    return serialize_alert(result)
