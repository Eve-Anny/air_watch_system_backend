"""
Alert prioritization — implements ARCHITECTURE.md §5.

Generalization decision: all four pollutants (co, pm2_5, pm10, voc) map band -> level identically
(Good=None, Moderate=Info, Unhealthy=Warning, Hazardous=Critical). ARCHITECTURE.md's alert table
calls out "VOC enters Unhealthy" as an explicit Warning-level example specifically because VOC uses
its own non-WHO band scale (§4.3) — this generalizes that consistently across all four bands rather
than treating VOC as a special case artificially capped below Critical.

The CO 15-minute fast-spike override (§4.1/§5) is layered on top as an independent trigger, tracked
under its own pollutant key "co_spike" so its audit trail doesn't overwrite/collide with CO's normal
1-hour-band alert history — the two are genuinely different triggering conditions.

De-dup rule (§5): one open (unresolved) alert per (device_id, pollutant) at a time. A new alert
document is only written when the desired level changes from the currently open alert (or there is
none); unchanged bands across cycles are a no-op, not a new document.
"""

from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..core import constants

GUIDELINE_BY_POLLUTANT = {
    "co": constants.CO_1H_GUIDELINE_MG_M3,
    "pm2_5": constants.PM2_5_24H_GUIDELINE_UG_M3,
    "pm10": constants.PM10_24H_GUIDELINE_UG_M3,
}

# Human-readable display names — "pm2_5".upper() renders as the misleading "PM2_5", not "PM2.5".
DISPLAY_NAME_BY_POLLUTANT = {"co": "CO", "pm2_5": "PM2.5", "pm10": "PM10"}

ROLLING_FIELD_BY_POLLUTANT = {
    "co": "co_mg_m3_1h",
    "pm2_5": "pm2_5_ug_m3_24h",
    "pm10": "pm10_ug_m3_24h",
    "voc": "voc_index_1h",
}


def _build_message(pollutant: str, value: Optional[float], ratio: Optional[float], band: str) -> str:
    value_str = f"{value:.2f}" if value is not None else "N/A"

    if pollutant == "voc":
        return f"VOC index rolling average ({value_str}) is in the {band} band (non-WHO, indoor air quality proxy)"

    if pollutant == "co_spike":
        return (
            f"CO 15-minute rolling average ({value_str} mg/m3) exceeds the WHO 15-minute guideline "
            f"({constants.CO_15MIN_GUIDELINE_MG_M3} mg/m3) - fast-spike safety override"
        )

    guideline = GUIDELINE_BY_POLLUTANT[pollutant]
    ratio_str = f"{ratio:.2f}" if ratio is not None else "N/A"
    name = DISPLAY_NAME_BY_POLLUTANT[pollutant]
    return f"{name} rolling average ({value_str}) is {ratio_str}x the WHO guideline ({guideline})"


async def _get_open_alert(db: AsyncIOMotorDatabase, device_id: str, pollutant: str) -> Optional[dict]:
    return await db.alerts.find_one({"device_id": device_id, "pollutant": pollutant, "resolved_at": None})


async def _apply_alert_state(
    db: AsyncIOMotorDatabase,
    device_id: str,
    reading_id: str,
    pollutant: str,
    band: str,
    value: Optional[float],
    ratio: Optional[float],
    category: str,
    now: datetime,
    triggered_ids: list[str],
) -> None:
    desired_level = constants.ALERT_LEVEL_BY_BAND[band]
    open_alert = await _get_open_alert(db, device_id, pollutant)

    if desired_level is None:
        if open_alert:
            await db.alerts.update_one({"_id": open_alert["_id"]}, {"$set": {"resolved_at": now}})
        return

    if open_alert and open_alert["level"] == desired_level:
        return  # same band as last cycle - no new document (§5 de-dup rule)

    if open_alert:
        # level changed (escalation or de-escalation) - resolve the old one, open a new one so the
        # history shows the progression rather than silently overwriting it.
        await db.alerts.update_one({"_id": open_alert["_id"]}, {"$set": {"resolved_at": now}})

    doc = {
        "device_id": device_id,
        "reading_id": reading_id,
        "level": desired_level,
        "pollutant": pollutant,
        "category_at_trigger": category,
        "message": _build_message(pollutant, value, ratio, band),
        "value": value,
        "who_ratio": ratio,
        "triggered_at": now,
        "acknowledged": False,
        "acknowledged_at": None,
        "acknowledged_by": None,
        "resolved_at": None,
        "schema_version": 1,
    }
    result = await db.alerts.insert_one(doc)
    triggered_ids.append(str(result.inserted_id))


async def evaluate_alerts(db: AsyncIOMotorDatabase, device_id: str, reading_id: str, scoring: dict) -> list[str]:
    now = datetime.now(timezone.utc)
    who_scores = scoring["who_scores"]
    rolling = scoring["rolling_averages"]
    category = scoring["category"]
    triggered_ids: list[str] = []

    for pollutant in ("co", "pm2_5", "pm10", "voc"):
        score = who_scores[pollutant]
        value = rolling.get(ROLLING_FIELD_BY_POLLUTANT[pollutant])
        await _apply_alert_state(
            db, device_id, reading_id, pollutant, score["band"], value, score["ratio"], category, now, triggered_ids
        )

    # CO fast-spike override: independent of the 1-hour band, forces Critical if the 15-minute
    # rolling mean alone exceeds the WHO 15-minute guideline. Also resolves any previously-open
    # co_spike alert once the spike subsides, via the same band->level machinery (band="Good" maps
    # to desired_level=None).
    co_15min = rolling.get("co_mg_m3_15min")
    spike_band = constants.CATEGORY_HAZARDOUS if (
        co_15min is not None and co_15min > constants.CO_15MIN_GUIDELINE_MG_M3
    ) else "Good"
    await _apply_alert_state(
        db, device_id, reading_id, "co_spike", spike_band, co_15min, None, category, now, triggered_ids
    )

    return triggered_ids
