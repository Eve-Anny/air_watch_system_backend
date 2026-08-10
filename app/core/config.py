from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # MongoDB: local for dev, Atlas connection string for the defense deployment (Phase 0 §DEPLOYMENT).
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "air_quality"

    # Comma-separated list; e.g. "http://localhost:5173,https://your-frontend.vercel.app"
    allowed_origins: str = "http://localhost:5173"

    # A device with no reading in this many seconds is reported as "offline" (computed at read time,
    # not stored reactively — see ARCHITECTURE.md §2.3).
    device_offline_threshold_seconds: int = 180

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


settings = Settings()
