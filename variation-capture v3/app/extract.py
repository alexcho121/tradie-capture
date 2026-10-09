"""Transcript -> structured record.

Pipeline:  model (or rules fallback)  ->  normalize_fields  ->  ground_check  ->  compute_flags

* The model only proposes. ground_check (grounding.py) removes anything not actually in the transcript.
* If the model is not configured or fails, the rules extractor runs, so a record is never blocked on a
  network call. `meta["fallback"]` says when that happened.
* Field shape (matches docs/openapi.json):  {"value", "confidence", "source", "edited"}
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings
from .grounding import ground_check  # noqa: F401  (re-exported)
from .prompts import SYSTEM_PROMPT, build_user_message
from .rules import rules_extract  # noqa: F401  (re-exported)
from .schemas import FIELD_NAMES, REQUIRED_FIELDS

_EMPTY_WORDS = {"", "null", "none", "n/a", "na", "unknown", "not stated", "not specified", "unspecified", "-"}


def blank_fields() -> Dict[str, Dict[str, Any]]:
    return {n: {"value": None, "confidence": 0.0, "source": None, "edited": False} for n in FIELD_NAMES}


def _clean_text(value: Any) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return str(int(value)) if float(value).is_integer() else str(value)
    if isinstance(value, (list, tuple)):
        parts = [p for p in (_clean_text(v) for v in value) if p]
        return ", ".join(parts) or None
    if isinstance(value, dict):
        return None
    text = " ".join(str(value).split())
    return None if text.lower() in _EMPTY_WORDS else text


def _clean_hours(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num) or num < 0 or num > 1000:
        return None
    return round(num, 2)


def normalize_fields(raw: Any, source: str) -> Dict[str, Dict[str, Any]]:
    """Coerce whatever the model (or rules) returned into the canonical field shape."""
    out = blank_fields()
    if not isinstance(raw, dict):
        return out
    for name in FIELD_NAMES:
        item = raw.get(name)
        if isinstance(item, dict):
            value, conf = item.get("value"), item.get("confidence")
        else:  # model returned a bare value
            value, conf = item, None
        value = _clean_hours(value) if name == "extra_time_hours" else _clean_text(value)
        if value is None:
            continue
        try:
            conf = float(conf)
            if not math.isfinite(conf):
                raise ValueError
        except (TypeError, ValueError):
            conf = 0.5
        out[name] = {"value": value, "confidence": round(min(max(conf, 0.0), 1.0), 2),
                     "source": source, "edited": False}
    return out


def compute_flags(fields: Dict[str, Dict[str, Any]], transcript: str) -> List[str]:
    """Machine-readable reasons the worker should look at this record."""
    flags: List[str] = []
    transcript = (transcript or "").strip()
    if not transcript:
        flags.append("no_speech")
    elif len(transcript.split()) < 4:
        flags.append("very_short_transcript")
    for name in FIELD_NAMES:
        f = fields[name]
        if f["value"] is None:
            if name in REQUIRED_FIELDS:
                flags.append(f"missing:{name}")
        elif f["confidence"] < 0.6 and not f.get("edited"):
            flags.append(f"low_confidence:{name}")
    return flags


def _parse_json(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    obj = json.loads(t[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("model output is not a JSON object")
    return obj


def _call_anthropic(transcript: str, contacts: List[str], settings: Settings) -> Dict[str, Any]:
    """One Messages API call. The SDK is imported lazily so the app (and tests) run without it."""
    import anthropic  # noqa: WPS433

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=30.0, max_retries=1)
    # No sampling parameters (temperature etc.): current SDK versions don't accept them on messages.create(),
    # and determinism comes from the grounding check, not from the sampler.
    resp = client.messages.create(
        model=settings.extract_model,
        max_tokens=700,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(transcript, contacts)}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    return _parse_json(text)


def extract_fields(transcript: str, contacts: Optional[List[str]],
                   settings: Settings) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Returns (fields, meta). Never raises for model/network problems."""
    contacts = contacts or []
    transcript = (transcript or "").strip()
    meta: Dict[str, Any] = {
        "provider": "rules", "model": None, "fallback": False, "error": None, "removed": [], "flags": [],
    }
    if not transcript:
        meta["provider"] = "none"
        meta["flags"] = ["no_speech"]
        return blank_fields(), meta

    raw: Optional[Dict[str, Any]] = None
    source = "rules"
    if settings.extract_provider == "anthropic" and settings.anthropic_api_key:
        try:
            raw = _call_anthropic(transcript, contacts, settings)
            source = "llm"
            meta["provider"], meta["model"] = "anthropic", settings.extract_model
        except Exception as e:  # noqa: BLE001 - any failure falls back to rules
            meta["fallback"] = True
            meta["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    if raw is None:
        raw = rules_extract(transcript, contacts)

    fields = normalize_fields(raw, source)
    fields, log = ground_check(fields, transcript, contacts)
    meta["removed"] = [e for e in log if e["action"] in ("removed", "trimmed")]
    meta["flags"] = compute_flags(fields, transcript) + [
        f"weak_grounding:{e['field']}" for e in log if e["action"] == "capped"
    ]
    return fields, meta
