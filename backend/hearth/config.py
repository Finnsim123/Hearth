"""Process configuration — only what must exist before the UI can run.

Everything else (HA connection, persons, bindings, schedules) lives in SQLite
and is configured through the web UI. See docs/DATA_MODEL.md.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "HEARTH_"}

    secret: str  # required — Fernet key material for token encryption
    data_dir: Path = Path("/data")
    host: str = "0.0.0.0"
    port: int = 8420
    log_level: str = "INFO"

    influx_url: str = "http://influxdb:8086"
    influx_org: str = "hearth"
    influx_token: str = ""

    # Job cadences (seconds) — overridable in UI later
    window_builder_interval: int = 300
    discovery_interval: int = 86_400

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hearth.db"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


settings = Settings()  # import-time singleton; tests construct their own
