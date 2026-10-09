"""Speech-to-text via a hosted API (OpenAI-style /audio/transcriptions) or a mock.

Design notes
- We record the whole clip first and transcribe afterwards (site noise makes streaming STT unreliable).
- Names / trade words are passed as a *spelling hint*. Hint text can be echoed back by the model on silent or noisy
  audio, so a transcript made only of hint words is discarded (`prompt_echo`); otherwise a contact's name could
  "appear" in the record without anyone saying it.
- Two request shapes, chosen by STT_MODEL (checked against OpenAI's speech-to-text guide, Oct 2026):
    whisper-1 / gpt-4o-*-transcribe : prompt=<hint>, language=en
    gpt-transcribe                  : prompt=<context>, keywords[]=<one per name>, languages[]=en  (no `language`)
  No `temperature` / `response_format` is sent: the default response is JSON with a `text` field for all of them.
- STT failing must never lose the evidence: callers catch SttError and keep the capture.
- Documented upload formats: mp3, mp4, mpeg, mpga, m4a, wav, webm (25 MB). `.ogg` is stored as evidence but may be
  rejected here; that surfaces as an SttError and the worker types the details instead.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import Settings

OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"

_MIME = {
    ".webm": "audio/webm",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}
_TRADE_WORDS = ["variation", "mil", "mm", "rough-in", "conduit", "plasterboard", "downlight", "power point"]
_CONTEXT = "A tradesperson's voice note on a construction site about extra work they were asked to do."


class SttError(Exception):
    """Transcription was not possible (not configured, network error, API error)."""


def is_gpt_transcribe(model: str) -> bool:
    return (model or "").lower().startswith("gpt-transcribe")


def _names(contacts: Optional[List[str]]) -> List[str]:
    return [c for c in (contacts or []) if c][:20]


def build_prompt_hint(contacts: Optional[List[str]]) -> str:
    """Free-text hint for whisper-1 style models."""
    hint = ", ".join(_TRADE_WORDS)
    names = _names(contacts)
    if names:
        hint += ", " + ", ".join(names)
    return hint[:600]


def build_keywords(contacts: Optional[List[str]]) -> List[str]:
    """Keyword list for gpt-transcribe: single-line, no < or > (the API rejects the whole request otherwise)."""
    out: List[str] = []
    for kw in _names(contacts) + _TRADE_WORDS:
        kw = " ".join(re.sub(r"[<>\r\n]+", " ", kw).split())[:60]
        if kw and kw not in out:
            out.append(kw)
    return out


def build_form(settings: Settings, contacts: Optional[List[str]]) -> Tuple[Dict[str, Any], str]:
    """-> (multipart form fields, text used for the echo check)."""
    model = settings.stt_model
    if is_gpt_transcribe(model):
        keywords = build_keywords(contacts)
        data: Dict[str, Any] = {"model": model, "prompt": _CONTEXT}
        if keywords:
            data["keywords[]"] = keywords
        if settings.stt_language:
            data["languages[]"] = [settings.stt_language]
        return data, _CONTEXT + " " + " ".join(keywords)
    hint = build_prompt_hint(contacts)
    data = {"model": model, "prompt": hint}
    if settings.stt_language:
        data["language"] = settings.stt_language
    return data, hint


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def is_prompt_echo(transcript: str, hint: str) -> bool:
    """True if every word of the transcript comes from the hint (nobody actually said anything)."""
    words = _words(transcript)
    if not words:
        return False
    hint_words = set(_words(hint))
    return all(w in hint_words for w in words)


def transcribe(path: Path, settings: Settings, contacts: Optional[List[str]] = None) -> Tuple[str, Dict[str, Any]]:
    """Returns (transcript, meta). Raises SttError if it cannot transcribe."""
    if settings.stt_provider != "openai":
        raise SttError("Speech-to-text is in mock mode (no OPENAI_API_KEY). Send a transcript instead.")
    if not settings.openai_api_key:
        raise SttError("OPENAI_API_KEY is not set.")

    data, echo_text = build_form(settings, contacts)
    started = time.monotonic()
    try:
        with open(path, "rb") as f:
            files = {"file": (path.name, f, _MIME.get(path.suffix.lower(), "application/octet-stream"))}
            resp = httpx.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data=data,
                files=files,
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
    except httpx.HTTPError as e:
        raise SttError(f"STT request failed ({type(e).__name__})") from e

    if resp.status_code != 200:
        raise SttError(f"STT API returned {resp.status_code}: {resp.text[:200]}")
    try:
        text = (resp.json().get("text") or "").strip()
    except (ValueError, AttributeError) as e:
        raise SttError("STT API returned an unexpected response") from e

    meta: Dict[str, Any] = {
        "provider": "openai",
        "model": settings.stt_model,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "prompt_echo": False,
    }
    if is_prompt_echo(text, echo_text):
        meta["prompt_echo"] = True
        text = ""
    return text, meta
