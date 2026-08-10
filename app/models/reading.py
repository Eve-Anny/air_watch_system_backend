from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class RawSensorValues(BaseModel):
    """
    Matches the firmware's JSON payload shape (firmware/src/network/api_client.cpp). Fields are
    Optional because the firmware sends JSON null for any sensor that failed to read that cycle
    (ARCHITECTURE.md §1.2's "raw values" — never fabricated, never silently dropped).
    """

    co_mg_m3: Optional[float] = None
    voc_index: Optional[float] = None
    pm1_0_ug_m3: Optional[float] = None
    pm2_5_ug_m3: Optional[float] = None
    pm10_ug_m3: Optional[float] = None
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    pressure_hpa: Optional[float] = None


class ReadingIngestIn(BaseModel):
    device_id: str = Field(..., min_length=1)
    timestamp: datetime
    raw: RawSensorValues


class IngestionAck(BaseModel):
    """Lightweight response to the firmware — not the full enriched document (keeps the POST
    response small for a resource-constrained ESP32 client)."""

    status: str = "ok"
    reading_id: str
    device_id: str
    category: str
    dominant_pollutant: Optional[str] = None
    alerts_triggered: list[str] = []
