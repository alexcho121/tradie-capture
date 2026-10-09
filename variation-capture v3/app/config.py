"""Runtime settings. No third-party deps; everything comes from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    stt_provider: str = "mock"            # "openai" | "mock"
    stt_model: str = "whisper-1"
    stt_language: str = "en"
    openai_api_key: str = ""
    extract_provider: str = "rules"       # "anthropic" | "rules"
    extract_model: str = "claude-haiku-5-5"
    anthropic_api_key: str = ""
    max_upload_mb: int = 25
    rate_limit_per_hour: int = 60         # 0 disables
    cors_origins: Tuple[str, ...] = ("*",)

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "app.db"

    @property
    def media_dir(self) -> Path:
        return Path(self.data_dir) / "media"


def _int(value: Optional[str], default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    e = os.environ if env is None else env
    openai_key = (e.get("OPENAI_API_KEY") or "").strip()
    anthropic_key = (e.get("ANTHROPIC_API_KEY") or "").strip()

    stt = (e.get("STT_PROVIDER") or "").strip().lower() or ("openai" if openai_key else "mock")
    ext = (e.get("EXTRACT_PROVIDER") or "").strip().lower() or ("anthropic" if anthropic_key else "rules")

    origins = tuple(o.strip() for o in (e.get("CORS_ORIGINS") or "*").split(",") if o.strip()) or ("*",)

    return Settings(
        data_dir=Path(e.get("DATA_DIR") or "./data"),
        stt_provider=stt,
        stt_model=(e.get("STT_MODEL") or "whisper-1").strip(),
        stt_language=(e.get("STT_LANGUAGE") or "en").strip(),
        openai_api_key=openai_key,
        extract_provider=ext,
        extract_model=(e.get("EXTRACT_MODEL") or "claude-haiku-5-5").strip(),
        anthropic_api_key=anthropic_key,
        max_upload_mb=_int(e.get("MAX_UPLOAD_MB"), 25),
        rate_limit_per_hour=_int(e.get("RATE_LIMIT_PER_HOUR"), 60),
        cors_origins=origins,
    )
