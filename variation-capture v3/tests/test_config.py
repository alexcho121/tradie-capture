from pathlib import Path

import pytest

from app.config import load_settings


def test_openai_stt_and_extraction_environment_settings_are_loaded():
    settings = load_settings({
        "STT_PROVIDER": "openai",
        "STT_MODEL": "gpt-transcribe",
        "EXTRACT_PROVIDER": "openai",
        "EXTRACT_MODEL": "extract-model",
        "OPENAI_API_KEY": "test-key",
    })
    assert settings.stt_provider == "openai"
    assert settings.stt_model == "gpt-transcribe"
    assert settings.extract_provider == "openai"
    assert settings.extract_model == "extract-model"
    assert settings.openai_api_key == "test-key"


@pytest.mark.parametrize("env", [
    {"STT_PROVIDER": "openai", "STT_MODEL": "stt-model", "EXTRACT_PROVIDER": "rules"},
    {"STT_PROVIDER": "mock", "EXTRACT_PROVIDER": "openai", "EXTRACT_MODEL": "extract-model"},
])
def test_openai_provider_requires_an_api_key(env):
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        load_settings(env)


def test_openai_provider_requires_models_from_environment():
    with pytest.raises(ValueError, match="STT_MODEL is required"):
        load_settings({"STT_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"})
    with pytest.raises(ValueError, match="EXTRACT_MODEL is required"):
        load_settings({"STT_PROVIDER": "mock", "EXTRACT_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"})


def test_env_example_has_production_providers_and_an_empty_key():
    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    assert "STT_PROVIDER=openai" in text and "EXTRACT_PROVIDER=openai" in text
    assert "STT_MODEL=\n" in text and "EXTRACT_MODEL=\n" in text
    assert "OPENAI_API_KEY=\n" in text and "your_openai_api_key_here" not in text
