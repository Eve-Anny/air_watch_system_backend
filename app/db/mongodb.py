from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from ..core.config import settings


class MongoDB:
    client: AsyncIOMotorClient | None = None
    db: AsyncIOMotorDatabase | None = None


mongodb = MongoDB()


async def connect_to_mongo() -> None:
    mongodb.client = AsyncIOMotorClient(settings.mongodb_uri)
    mongodb.db = mongodb.client[settings.mongodb_db_name]
    await _create_indexes(mongodb.db)


async def close_mongo_connection() -> None:
    if mongodb.client:
        mongodb.client.close()


async def _create_indexes(db: AsyncIOMotorDatabase) -> None:
    # device_id + timestamp compound index (minimum required by Phase 4 spec) — backs every
    # historical/latest-reading query and the rolling-average aggregations in scoring_engine.py.
    await db.readings.create_index([("device_id", 1), ("timestamp", -1)])

    # Backs GET /api/v1/alerts (history) and the alert engine's open-alert lookup / de-dup logic.
    await db.alerts.create_index([("device_id", 1), ("triggered_at", -1)])
    await db.alerts.create_index([("device_id", 1), ("pollutant", 1), ("resolved_at", 1)])

    await db.devices.create_index("device_id", unique=True)


def get_db() -> AsyncIOMotorDatabase:
    return mongodb.db
