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
    stt_model: str = ""
    stt_language: str = "en"
    openai_api_key: str = ""
    extract_provider: str = "rules"       # "openai" | "anthropic" | "rules"
    extract_model: str = ""
    anthropic_api_key: str = ""
    max_upload_mb: int = 25
    rate_limit_per_hour: int = 60         # 0 disables
    cors_origins: Tuple[str, ...] = ("*",)
    geocoder_enabled: bool = True
    geocoder_url: str = "https://nominatim.openstreetmap.org/reverse"
    geocoder_user_agent: str = "TradieCapture/0.1"
    geocoder_timeout_seconds: float = 4.0

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


def _bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def _float(value: Optional[str], default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    e = os.environ if env is None else env
    openai_key = (e.get("OPENAI_API_KEY") or "").strip()
    anthropic_key = (e.get("ANTHROPIC_API_KEY") or "").strip()

    stt = (e.get("STT_PROVIDER") or "").strip().lower() or ("openai" if openai_key else "mock")
    ext = (e.get("EXTRACT_PROVIDER") or "").strip().lower() or ("anthropic" if anthropic_key else "rules")

    origins = tuple(o.strip() for o in (e.get("CORS_ORIGINS") or "*").split(",") if o.strip()) or ("*",)

    settings = Settings(
        data_dir=Path(e.get("DATA_DIR") or "./data"),
        stt_provider=stt,
        stt_model=(e.get("STT_MODEL") or "").strip(),
        stt_language=(e.get("STT_LANGUAGE") or "en").strip(),
        openai_api_key=openai_key,
        extract_provider=ext,
        extract_model=(e.get("EXTRACT_MODEL") or ("claude-haiku-5-5" if ext == "anthropic" else "")).strip(),
        anthropic_api_key=anthropic_key,
        max_upload_mb=_int(e.get("MAX_UPLOAD_MB"), 25),
        rate_limit_per_hour=_int(e.get("RATE_LIMIT_PER_HOUR"), 60),
        cors_origins=origins,
        geocoder_enabled=_bool(e.get("GEOCODER_ENABLED"), True),
        geocoder_url=(e.get("GEOCODER_URL") or "https://nominatim.openstreetmap.org/reverse").strip(),
        geocoder_user_agent=(e.get("GEOCODER_USER_AGENT") or "TradieCapture/0.1").strip(),
        geocoder_timeout_seconds=_float(e.get("GEOCODER_TIMEOUT_SECONDS"), 4.0),
    )
    return validate_settings(settings)


def validate_settings(settings: Settings) -> Settings:
    """Fail during startup when a selected hosted provider is incomplete."""
    openai_uses = [name for name, provider in (("STT_PROVIDER", settings.stt_provider),
                                                ("EXTRACT_PROVIDER", settings.extract_provider))
                   if provider == "openai"]
    if openai_uses and not settings.openai_api_key:
        raise ValueError(f"OPENAI_API_KEY is required when {' or '.join(openai_uses)}=openai")
    if settings.stt_provider == "openai":
        if not settings.stt_model:
            raise ValueError("STT_MODEL is required when STT_PROVIDER=openai")
    if settings.extract_provider == "openai":
        if not settings.extract_model:
            raise ValueError("EXTRACT_MODEL is required when EXTRACT_PROVIDER=openai")
    return settings
