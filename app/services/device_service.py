from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..core.config import settings

DEFAULT_SENSOR_CONFIG = {
    "co_sensor": "MQ-7",
    "voc_sensor": "MQ-135",
    "pm_sensor": "PMS5003",
    "env_sensor": "BME280",
    "expansion": [],
}


async def upsert_device_on_reading(db: AsyncIOMotorDatabase, device_id: str) -> None:
    """
    Devices auto-register on first reading — a confirmed decision (ARCHITECTURE.md §7): no prior
    POST /api/v1/devices call is required for demo-day robustness. Subsequent readings just bump
    last_seen_at. POST /api/v1/devices remains available for pre-naming/pre-configuring a device
    before it's ever powered on.
    """
    now = datetime.now(timezone.utc)
    await db.devices.update_one(
        {"device_id": device_id},
        {
            "$set": {"last_seen_at": now, "status": "online"},
            "$setOnInsert": {
                "device_id": device_id,
                "name": device_id,
                "location": {"label": None, "lat": None, "lng": None},
                "sensor_config": DEFAULT_SENSOR_CONFIG,
                "registered_at": now,
                "firmware_version": None,
                "schema_version": 1,
            },
        },
        upsert=True,
    )


def compute_status(last_seen_at: Optional[datetime]) -> str:
    """
    Status is computed at read time from last_seen_at, not trusted from the stored field — ESP32
    firmware has no graceful "going offline" signal, so a stored status would go stale
    (ARCHITECTURE.md §2.3).
    """
    if last_seen_at is None:
        return "offline"
    now = datetime.now(timezone.utc)
    last_seen = last_seen_at if last_seen_at.tzinfo else last_seen_at.replace(tzinfo=timezone.utc)
    age_seconds = (now - last_seen).total_seconds()
    return "online" if age_seconds <= settings.device_offline_threshold_seconds else "offline"
