"""Code-level anti-hallucination check.

The prompt tells the model not to invent anything; this module *enforces* it. Every value the model
returns is compared with the transcript, and anything the speaker did not say is removed (or trimmed)
and logged. Digits are the dangerous part (a wrong "350mm" on an evidence record), so numbers are
checked first and a field with an unspoken number is removed whole.

Known limit: if a prompt-injection sentence is itself in the transcript, its words ARE grounded.
That case relies on the prompt and on the worker reviewing the record.
"""
from __future__ import annotations

import copy
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

from . import numbers as nm
from .schemas import FIELD_NAMES

STOPWORDS: Set[str] = {
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "is", "it", "we", "i", "up",
    "by", "as", "with", "from", "that", "this", "be", "are", "was", "so", "me", "my", "our", "us",
    "some", "more", "extra", "new", "then", "than", "into", "onto", "off", "out",
}

# value-word -> words the speaker might have said instead (speech-to-text / trade slang)
SYNONYMS: Dict[str, Set[str]] = {
    "mm": {"mil", "mils", "mill", "mills", "millimetre", "millimetres", "millimeter", "millimeters"},
    "cm": {"centimetre", "centimetres", "centimeter", "centimeters", "cent", "cents"},
    "m": {"metre", "metres", "meter", "meters"},
    "metres": {"metre", "meters", "meter", "m"},
    "level": {"floor", "storey", "story", "lvl"},
    "floor": {"level", "storey", "story", "lvl"},
    "lvl": {"level", "floor"},
    "supervisor": {"super"},
    "super": {"supervisor"},
    "manager": {"pm"},
    "bedroom": {"bed", "beds"},
    "bathroom": {"bath"},
    "power": {"gpo", "outlet", "socket"},
    "point": {"gpo", "outlet", "socket"},
}


# Share of the value's content words that must appear in the transcript. Materials are stricter:
# "copper pipe" when the speaker only said "pipe" is exactly the kind of invented detail that matters.
MIN_OVERLAP: Dict[str, float] = {"change": 0.5, "location": 0.5, "materials": 0.8}


def tokens(text: str) -> List[str]:
    return re.findall(r"[a-z]+|\d+(?:\.\d+)?", (text or "").lower().replace("’", "'"))


def content_words(text: str) -> List[str]:
    return [t for t in tokens(text) if t.isalpha() and len(t) >= 2 and t not in STOPWORDS]


def grounded_word(word: str, transcript_tokens: Set[str]) -> bool:
    """Is `word` (from the model's value) present in the transcript - exactly, as a synonym, or as a close form
    (shared first four letters: pipe/pipes, relocate/relocation)?"""
    if word in transcript_tokens:
        return True
    if any(s in transcript_tokens for s in SYNONYMS.get(word, ())):
        return True
    if len(word) >= 4:
        prefix = word[:4]
        return any(len(t) >= 4 and t[:4] == prefix for t in transcript_tokens)
    return False


def name_matches(word: str, transcript_tokens: Set[str]) -> bool:
    """Stricter than grounded_word: names must be spelled (almost) the way they were said."""
    if word in transcript_tokens or any(s in transcript_tokens for s in SYNONYMS.get(word, ())):
        return True
    return any(len(t) >= 2 and SequenceMatcher(None, word, t).ratio() >= 0.8 for t in transcript_tokens)


def _fmt(nums: List[float]) -> str:
    return ", ".join(str(int(n)) if float(n).is_integer() else str(n) for n in sorted(nums))


def ground_check(fields: Dict[str, Dict[str, Any]], transcript: str,
                 contacts: Optional[List[str]] = None) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Returns (checked_fields, log). Each log entry: {field, action, reason, was, now}.
    action is "removed", "trimmed" or "capped" (value kept, confidence lowered)."""
    out = copy.deepcopy(fields)
    log: List[Dict[str, Any]] = []
    t_tokens = set(tokens(transcript))
    t_nums = nm.numbers_in(transcript)
    durations = nm.extract_hours(transcript)

    def drop(name: str, reason: str) -> None:
        log.append({"field": name, "action": "removed", "reason": reason, "was": out[name]["value"], "now": None})
        out[name].update({"value": None, "confidence": 0.0, "source": None})

    def cap(name: str, ceiling: float, reason: str) -> None:
        if out[name]["confidence"] > ceiling:
            log.append({"field": name, "action": "capped", "reason": reason,
                        "was": out[name]["confidence"], "now": ceiling})
            out[name]["confidence"] = ceiling

    for name in FIELD_NAMES:
        f = out[name]
        value = f["value"]
        if value is None:
            continue

        if name == "extra_time_hours":
            matches = [d for d in durations if abs(d["hours"] - float(value)) <= 0.06]
            if not matches:
                drop(name, "that duration was not said in the recording")
            elif all(d["range"] for d in matches):
                drop(name, "the speaker gave a range, not a single duration")
            else:
                if all(d["hedged"] for d in matches):
                    cap(name, 0.7, "the speaker hedged the time")
            continue

        if name == "requester":
            words = re.findall(r"[A-Za-z][A-Za-z'’-]*", str(value))
            kept: List[str] = []
            dropped: List[str] = []
            for w in words:
                low = w.lower().strip("'’-")
                if low in STOPWORDS or name_matches(low, t_tokens):
                    kept.append(w)
                else:
                    dropped.append(w)
            if not [w for w in kept if w.lower() not in STOPWORDS]:
                drop(name, "that name was not said in the recording")
            elif dropped:
                new = " ".join(kept)
                log.append({"field": name, "action": "trimmed",
                            "reason": f"words not said in the recording: {', '.join(dropped)}",
                            "was": value, "now": new})
                f["value"] = new
                f["confidence"] = min(f["confidence"], 0.7)
            continue

        # change / location / materials / amount_mentioned: numbers first, then words
        value_nums = nm.numbers_in(str(value), ignore_lone_one=True)
        missing = [n for n in value_nums if not any(abs(n - t) < 1e-9 for t in t_nums)]
        if missing:
            drop(name, f"number not said in the recording: {_fmt(missing)}")
            continue
        if name == "amount_mentioned":
            if not value_nums:
                drop(name, "no amount in the value")
            continue
        words = content_words(str(value))
        if words:
            hits = sum(1 for w in words if grounded_word(w, t_tokens))
            overlap = hits / len(words)
            if overlap < MIN_OVERLAP.get(name, 0.5):
                drop(name, f"only {hits} of {len(words)} words appear in the recording")
            elif overlap < 0.8:
                cap(name, 0.7, f"only {hits} of {len(words)} words appear in the recording")
    return out, log
