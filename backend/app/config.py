from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_ROOT / ".env")


def _path(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()


def _origins() -> List[str]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    canvas_base_url: str = "https://q.utoronto.ca"
    database_path: Path = _path("DATABASE_PATH", "./data/school_sync.db")
    secret_key_path: Path = _path("SECRET_KEY_PATH", "./data/.encryption_key")
    vapid_key_path: Path = _path("VAPID_KEY_PATH", "./data/.vapid_private_key.pem")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "google/gemini-3.8-flash")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    sync_interval_seconds: int = int(os.getenv("SYNC_INTERVAL_SECONDS", "3600"))
    notification_interval_seconds: int = int(os.getenv("NOTIFICATION_INTERVAL_SECONDS", "30"))
    academic_term_season: str = os.getenv("ACADEMIC_TERM_SEASON", "fall").strip().casefold()
    academic_term_year: int = int(os.getenv("ACADEMIC_TERM_YEAR", "2026"))
    academic_term_start: str = os.getenv("ACADEMIC_TERM_START", "2026-09-08")
    academic_term_end: str = os.getenv("ACADEMIC_TERM_END", "2026-12-08")
    academic_term_breaks: str = os.getenv(
        "ACADEMIC_TERM_BREAKS", "2026-10-12,2026-10-26:2026-10-30"
    )
    timetable_base_url: str = "https://api.easi.utoronto.ca/ttb"
    cors_origins: List[str] = None  # type: ignore[assignment]
    max_download_bytes: int = int(os.getenv("MAX_DOWNLOAD_BYTES", str(20 * 1024 * 1024)))
    app_url: str = os.getenv("APP_URL", "http://localhost:8766")
    vapid_subject: str = os.getenv("VAPID_SUBJECT", "mailto:local@school-sync.invalid")

    def __post_init__(self) -> None:
        object.__setattr__(self, "cors_origins", _origins())


settings = Settings()
