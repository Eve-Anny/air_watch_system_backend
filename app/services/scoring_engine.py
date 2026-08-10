"""
WHO-aligned rolling-window scoring engine — implements ARCHITECTURE.md §4.

Rolling averages are computed via a live MongoDB aggregation ($match + $avg) at ingestion time
rather than a maintained streaming cache — a confirmed design decision (ARCHITECTURE.md §7):
simpler, no second source of truth to keep in sync, and adequate at this project's expected
demo-scale (a handful of devices, ~1 reading/minute each).
"""

from datetime import datetime, timedelta
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..core import constants


async def _rolling_average(
    db: AsyncIOMotorDatabase,
    device_id: str,
    field: str,
    window: timedelta,
    as_of: datetime,
) -> Optional[float]:
    start = as_of - window
    pipeline = [
        {
            "$match": {
                "device_id": device_id,
                "timestamp": {"$gte": start, "$lte": as_of},
                field: {"$ne": None},
                # Archived readings (readings router's /archive endpoint) are excluded from rolling
                # averages, not just from the UI - an archived reading shouldn't silently keep
                # influencing live scoring.
                "archived": {"$ne": True},
            }
        },
        {"$group": {"_id": None, "avg": {"$avg": f"${field}"}}},
    ]
    result = await db.readings.aggregate(pipeline).to_list(length=1)
    return result[0]["avg"] if result else None


def _band_for_ratio(ratio: Optional[float]) -> str:
    if ratio is None:
        # No rolling-average data yet for this pollutant this cycle (e.g. a brand-new device's
        # first-ever reading with a failed sensor) — treat as Good rather than fabricate a band.
        return "Good"
    for upper_bound, band in constants.CATEGORY_BANDS:
        if ratio <= upper_bound:
            return band
    return constants.CATEGORY_HAZARDOUS


def _band_for_voc(index: Optional[float]) -> str:
    if index is None:
        return "Good"
    for upper_bound, band in constants.VOC_BANDS:
        if index <= upper_bound:
            return band
    return constants.VOC_HAZARDOUS


async def score_reading(db: AsyncIOMotorDatabase, device_id: str, timestamp: datetime) -> dict:
    """
    Must be called AFTER the triggering raw reading has already been persisted — the rolling-average
    aggregation queries the readings collection directly and needs to see the new document (it's
    always within its own window, since window = [as_of - width, as_of]).
    """
    co_1h = await _rolling_average(db, device_id, "raw.co_mg_m3", constants.ROLLING_WINDOW_CO_PRIMARY, timestamp)
    co_15min = await _rolling_average(db, device_id, "raw.co_mg_m3", constants.ROLLING_WINDOW_CO_SPIKE, timestamp)
    pm2_5_24h = await _rolling_average(db, device_id, "raw.pm2_5_ug_m3", constants.ROLLING_WINDOW_PM2_5, timestamp)
    pm10_24h = await _rolling_average(db, device_id, "raw.pm10_ug_m3", constants.ROLLING_WINDOW_PM10, timestamp)
    voc_1h = await _rolling_average(db, device_id, "raw.voc_index", constants.ROLLING_WINDOW_VOC, timestamp)

    rolling_averages = {
        "co_mg_m3_1h": co_1h,
        "co_mg_m3_15min": co_15min,  # fast-spike override input only — see services/alert_engine.py
        "pm2_5_ug_m3_24h": pm2_5_24h,
        "pm10_ug_m3_24h": pm10_24h,
        "voc_index_1h": voc_1h,
    }

    co_ratio = (co_1h / constants.CO_1H_GUIDELINE_MG_M3) if co_1h is not None else None
    pm2_5_ratio = (pm2_5_24h / constants.PM2_5_24H_GUIDELINE_UG_M3) if pm2_5_24h is not None else None
    pm10_ratio = (pm10_24h / constants.PM10_24H_GUIDELINE_UG_M3) if pm10_24h is not None else None

    who_scores = {
        "co": {"ratio": co_ratio, "band": _band_for_ratio(co_ratio)},
        "pm2_5": {"ratio": pm2_5_ratio, "band": _band_for_ratio(pm2_5_ratio)},
        "pm10": {"ratio": pm10_ratio, "band": _band_for_ratio(pm10_ratio)},
        "voc": {
            "ratio": None,
            "band": _band_for_voc(voc_1h),
            "note": "non-WHO, indoor air quality proxy",
        },
    }

    # Dominant pollutant / overall category = worst-case among WHO-backed pollutants only. VOC
    # never drives the overall category — a confirmed decision, ARCHITECTURE.md §4.2/§7.
    who_backed_ratios = {"co": co_ratio, "pm2_5": pm2_5_ratio, "pm10": pm10_ratio}
    scored = {pollutant: r for pollutant, r in who_backed_ratios.items() if r is not None}
    if scored:
        dominant_pollutant = max(scored, key=scored.get)
        category = who_scores[dominant_pollutant]["band"]
    else:
        dominant_pollutant = None
        category = "Good"

    return {
        "rolling_averages": rolling_averages,
        "who_scores": who_scores,
        "dominant_pollutant": dominant_pollutant,
        "category": category,
    }
