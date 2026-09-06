from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from ..core.serialization import serialize_reading
from ..db.mongodb import get_db
from ..models.reading import IngestionAck, ReadingIngestIn
from ..services import alert_engine, device_service, scoring_engine

router = APIRouter(prefix="/api/v1/readings", tags=["readings"])

# Excludes archived readings from a query filter - {"$ne": True} matches both {"archived": false}
# and documents that predate this field entirely (missing field), so existing/older readings are
# treated as not-archived without needing a migration.
_NOT_ARCHIVED = {"$ne": True}


@router.post("", response_model=IngestionAck, status_code=http_status.HTTP_201_CREATED)
async def ingest_reading(payload: ReadingIngestIn, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Ingestion pipeline (ARCHITECTURE.md §1.2): persist raw -> compute rolling windows/WHO
    scores/category/dominant pollutant -> evaluate alert rules -> persist any triggered alerts.
    """
    received_at = datetime.now(timezone.utc)

    await device_service.upsert_device_on_reading(db, payload.device_id)

    reading_doc = {
        "device_id": payload.device_id,
        "timestamp": payload.timestamp,
        "received_at": received_at,
        "raw": payload.raw.model_dump(),
        "archived": False,
        "schema_version": 1,
    }
    insert_result = await db.readings.insert_one(reading_doc)
    reading_id = insert_result.inserted_id

    scoring = await scoring_engine.score_reading(db, payload.device_id, payload.timestamp)

    await db.readings.update_one(
        {"_id": reading_id},
        {
            "$set": {
                "rolling_averages": scoring["rolling_averages"],
                "who_scores": scoring["who_scores"],
                "dominant_pollutant": scoring["dominant_pollutant"],
                "category": scoring["category"],
            }
        },
    )

    triggered = await alert_engine.evaluate_alerts(db, payload.device_id, str(reading_id), scoring)

    return IngestionAck(
        reading_id=str(reading_id),
        device_id=payload.device_id,
        category=scoring["category"],
        dominant_pollutant=scoring["dominant_pollutant"],
        alerts_triggered=triggered,
    )


@router.get("/latest")
async def latest_reading(device_id: Optional[str] = None, db: AsyncIOMotorDatabase = Depends(get_db)):
    if device_id:
        doc = await db.readings.find_one(
            {"device_id": device_id, "archived": _NOT_ARCHIVED}, sort=[("timestamp", -1)]
        )
        data = [serialize_reading(doc)] if doc else []
    else:
        # Latest reading per device, across all devices.
        pipeline = [
            {"$match": {"archived": _NOT_ARCHIVED}},
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$device_id", "doc": {"$first": "$$ROOT"}}},
        ]
        grouped = await db.readings.aggregate(pipeline).to_list(length=None)
        data = [serialize_reading(g["doc"]) for g in grouped]

    return {"data": data, "meta": {"count": len(data)}}


@router.get("")
async def historical_readings(
    device_id: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = Query(100, le=1000),
    skip: int = Query(0, ge=0),
    archived_only: bool = Query(
        False,
        description="Show ONLY archived readings (for a 'view archived / restore' UI), instead of "
        "the default of only non-archived readings. These two modes are mutually exclusive - there "
        "is no 'show everything regardless of archived state' mode, since every caller of this "
        "endpoint wants one or the other, never a mix.",
    ),
    include_archived: bool = Query(
        False,
        description="Include both active and archived readings. Intended for complete exports; ignored when archived_only is true.",
    ),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    query: dict = {}
    if device_id:
        query["device_id"] = device_id
    if start or end:
        query["timestamp"] = {}
        if start:
            query["timestamp"]["$gte"] = start
        if end:
            query["timestamp"]["$lte"] = end
    if archived_only:
        query["archived"] = True
    elif not include_archived:
        query["archived"] = _NOT_ARCHIVED

    cursor = db.readings.find(query).sort("timestamp", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)
    total = await db.readings.count_documents(query)

    return {
        "data": [serialize_reading(d) for d in docs],
        "meta": {"count": len(docs), "total": total, "limit": limit, "skip": skip},
    }


@router.patch("/{reading_id}/archive")
async def archive_reading(reading_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Soft-archive: sets archived=true rather than deleting the document. Archived readings are
    excluded from default queries (this endpoint, /latest, and the scoring engine's rolling-average
    computation - see services/scoring_engine.py) but remain in MongoDB and can be restored via
    the unarchive endpoint below - a deliberate choice given this project's rolling-window scoring
    depends on trustworthy history; an accidental bulk mistake here shouldn't be unrecoverable.
    """
    try:
        oid = ObjectId(reading_id)
    except InvalidId:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid reading_id")

    result = await db.readings.find_one_and_update(
        {"_id": oid}, {"$set": {"archived": True}}, return_document=ReturnDocument.AFTER
    )
    if not result:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "reading not found")
    return serialize_reading(result)


@router.patch("/{reading_id}/unarchive")
async def unarchive_reading(reading_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    try:
        oid = ObjectId(reading_id)
    except InvalidId:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid reading_id")

    result = await db.readings.find_one_and_update(
        {"_id": oid}, {"$set": {"archived": False}}, return_document=ReturnDocument.AFTER
    )
    if not result:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "reading not found")
    return serialize_reading(result)
