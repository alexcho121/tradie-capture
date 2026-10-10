"""The evaluation script must be able to score the model the app really uses (OpenAI), not only the rules."""
import importlib.util
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parent.parent / "eval" / "run_eval.py"
spec = importlib.util.spec_from_file_location("run_eval", EVAL)
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)

ENV = {"STT_PROVIDER": "openai", "STT_MODEL": "stt-model", "EXTRACT_PROVIDER": "openai",
       "EXTRACT_MODEL": "draft-model", "OPENAI_API_KEY": "test-key"}


def test_openai_eval_uses_the_openai_key_and_model():
    s = run_eval.build_settings("openai", None, ENV)
    assert (s.extract_provider, s.extract_model, s.openai_api_key) == ("openai", "draft-model", "test-key")
    assert run_eval.build_settings("openai", "other-model", ENV).extract_model == "other-model"


def test_auto_eval_passes_the_openai_key_through():
    s = run_eval.build_settings("auto", None, ENV)
    assert s.extract_provider == "openai" and s.openai_api_key == "test-key"


def test_openai_eval_explains_a_missing_key():
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        run_eval.build_settings("openai", None, {"STT_PROVIDER": "mock", "EXTRACT_PROVIDER": "rules"})


def test_dotenv_loader_never_overrides_the_real_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nEXTRACT_MODEL=from-file\nNEW_VALUE='quoted'\n", encoding="utf-8")
    monkeypatch.setenv("EXTRACT_MODEL", "from-shell")
    monkeypatch.delenv("NEW_VALUE", raising=False)
    run_eval.load_dotenv(env_file)
    import os
    assert os.environ["EXTRACT_MODEL"] == "from-shell" and os.environ["NEW_VALUE"] == "quoted"


def test_openai_eval_ignores_speech_to_text_settings_and_accepts_model_flag():
    env = {"STT_PROVIDER": "openai", "STT_MODEL": "", "EXTRACT_PROVIDER": "openai", "EXTRACT_MODEL": "",
           "OPENAI_API_KEY": "test-key"}
    s = run_eval.build_settings("openai", "flag-model", env)   # blank STT_MODEL and EXTRACT_MODEL in .env
    assert (s.extract_provider, s.extract_model, s.openai_api_key) == ("openai", "flag-model", "test-key")
    with pytest.raises(SystemExit, match="EXTRACT_MODEL"):
        run_eval.build_settings("openai", None, env)
