from typing import Optional

from pydantic import BaseModel


class DeviceLocation(BaseModel):
    label: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class SensorConfig(BaseModel):
    co_sensor: str = "MQ-7"
    voc_sensor: str = "MQ-135"
    pm_sensor: str = "PMS5003"
    env_sensor: str = "BME280"
    # Reserved for the NO2/SO2/O3 expansion path (Phase 0) — e.g. ["NO2:MiCS-2714"].
    expansion: list[str] = []


class DeviceCreate(BaseModel):
    device_id: str
    name: Optional[str] = None
    location: Optional[DeviceLocation] = None
    sensor_config: Optional[SensorConfig] = None
    firmware_version: Optional[str] = None


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[DeviceLocation] = None
