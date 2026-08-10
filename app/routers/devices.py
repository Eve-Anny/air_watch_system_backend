from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from ..core.serialization import serialize_device
from ..db.mongodb import get_db
from ..models.device import DeviceCreate, DeviceUpdate
from ..services import device_service

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


def _with_computed_status(doc: dict) -> dict:
    doc["status"] = device_service.compute_status(doc.get("last_seen_at"))
    return doc


@router.get("")
async def list_devices(db: AsyncIOMotorDatabase = Depends(get_db)):
    docs = await db.devices.find({}).to_list(length=None)
    data = [_with_computed_status(serialize_device(d)) for d in docs]
    return {"data": data, "meta": {"count": len(data)}}


@router.post("", status_code=http_status.HTTP_201_CREATED)
async def register_device(payload: DeviceCreate, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Pre-registers/pre-names a device before it's ever powered on. Not required before a device
    can POST readings — see services/device_service.upsert_device_on_reading's auto-register path."""
    existing = await db.devices.find_one({"device_id": payload.device_id})
    if existing:
        raise HTTPException(http_status.HTTP_409_CONFLICT, "device_id already registered")

    now = datetime.now(timezone.utc)
    doc = {
        "device_id": payload.device_id,
        "name": payload.name or payload.device_id,
        "location": (payload.location.model_dump() if payload.location else {"label": None, "lat": None, "lng": None}),
        "sensor_config": (
            payload.sensor_config.model_dump() if payload.sensor_config else device_service.DEFAULT_SENSOR_CONFIG
        ),
        "status": "offline",
        "last_seen_at": None,
        "registered_at": now,
        "firmware_version": payload.firmware_version,
        "schema_version": 1,
    }
    await db.devices.insert_one(doc)
    return _with_computed_status(serialize_device(doc))


@router.get("/{device_id}")
async def get_device(device_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await db.devices.find_one({"device_id": device_id})
    if not doc:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "device not found")
    return _with_computed_status(serialize_device(doc))


@router.patch("/{device_id}")
async def update_device(device_id: str, payload: DeviceUpdate, db: AsyncIOMotorDatabase = Depends(get_db)):
    update_fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not update_fields:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "no fields to update")

    result = await db.devices.find_one_and_update(
        {"device_id": device_id},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "device not found")
    return _with_computed_status(serialize_device(result))
