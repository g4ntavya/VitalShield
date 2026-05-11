"""VitalShield configuration via pydantic-settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Gemini ──────────────────────────────────────────────────────────────────
    gemini_api_key: str = ""

    # ── FHIR ───────────────────────────────────────────────────────────────────
    fhir_base_url: str = "http://localhost:8080/fhir"
    fhir_access_token: str = "demo_token"

    # ── SMART on FHIR ──────────────────────────────────────────────────────────
    smart_client_id: str = "vitalshield"
    smart_client_secret: str = ""
    smart_redirect_uri: str = "http://localhost:8000/callback"

    # ── Server ─────────────────────────────────────────────────────────────────
    vitalshield_host: str = "0.0.0.0"
    vitalshield_port: int = 8000
    vitalshield_log_level: str = "INFO"

    # ── Temporal Engine ────────────────────────────────────────────────────────
    temporal_window_hours: int = 72
    temporal_max_gap_minutes: int = 30

    # ── Privacy ────────────────────────────────────────────────────────────────
    he_enabled: bool = True
    zk_enabled: bool = False

    # ── Demo ───────────────────────────────────────────────────────────────────
    demo_mode: bool = True
    demo_data_path: Path = Path("./data/synthea/fhir")


@lru_cache
def get_settings() -> Settings:
    return Settings()
