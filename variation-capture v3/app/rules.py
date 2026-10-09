"""Rules-based fallback extractor.

Used when there is no ANTHROPIC_API_KEY or the model call fails, so "Saved" and a basic structured
record never depend on a network call. It is deliberately modest: regexes for simple spoken sentences
("John asked me to move the pipe 300 mil left on level 2, probably two hours"). The eval script shows
how much worse it is than the model on messy speech - say so, don't hide it.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from . import numbers as nm
from .schemas import FIELD_NAMES

_ASK_VERBS = r"(?:asked|told|said|says|requested|instructed|directed)"
_WANT_VERBS = r"(?:wants?|wanted|needs?|needed|reckons?)"
_REQ_VERBS = rf"(?:{_ASK_VERBS}|{_WANT_VERBS}|has\s+asked)"
_ROLES = (r"(?:builder|super(?:visor|intendent)?|foreman|site\s+manager|site\s+supervisor|project\s+manager|pm|"
          r"client|owner|head\s+contractor|engineer|architect|leading\s+hand|plumber|sparky|electrician|chippy)")
_NOT_NAMES = {
    "he", "she", "they", "we", "i", "it", "who", "someone", "somebody", "the", "a", "an", "client", "extra",
    "work", "this", "that", "also", "and", "so", "then", "well", "yeah", "okay", "ok", "right", "just", "now",
    "uh", "um", "maybe", "probably", "level", "unit", "need", "needs", "variation",
}
_ROOMS = [
    "bathroom", "ensuite", "bedroom", "kitchen", "laundry", "hallway", "hall", "lounge", "living room",
    "dining room", "office", "garage", "balcony", "roof", "ceiling", "stairwell", "basement", "plant room",
    "foyer", "lobby", "toilet", "pantry", "study", "patio", "deck", "carport", "lift", "corridor",
]
_ROOM_RE = r"(?:" + "|".join(sorted((r.replace(" ", r"\s+") for r in _ROOMS), key=len, reverse=True)) + r")"
_NUMWORD = r"(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_LEVEL_RE = rf"(?:level|lvl|floor|unit|apartment|apt|flat|lot|suite|room)\s*(?:number\s+)?{_NUMWORD}"
_ORD_RE = (r"(?:first|second|third|fourth|fifth|\d+(?:st|nd|rd|th))\s+"
           r"(?:floor|level|storey|story|bedroom|bathroom)")
_LOC_PIECE = rf"(?:{_ORD_RE}|{_LEVEL_RE}|{_ROOM_RE})"
_TRAILING_LOC = re.compile(
    rf"\s+(?:on|in|at|inside|within)\s+(?:the\s+)?{_LOC_PIECE}(?:\s+{_ROOM_RE})?\s*$", re.I)


def _clean(text: str) -> str:
    return " ".join((text or "").replace("’", "'").split())


# ------------------------------------------------------------------ requester

def _contact_words(contacts: List[str]) -> List[str]:
    words: List[str] = []
    for c in contacts or []:
        for w in re.findall(r"[A-Za-z][A-Za-z'-]+", c):
            if len(w) >= 2 and w not in words:
                words.append(w)
    return words


def _requester(text: str, contacts: List[str]) -> Dict[str, Any]:
    verb_after = re.compile(rf"(?:\s+from\s+[^,.;]{{1,30}}?)?\s+{_REQ_VERBS}\b", re.I)
    cwords = _contact_words(contacts)

    # 1) a known contact right next to a request verb ("Jon said ...", "per John")
    hits = []
    for m in re.finditer(r"\b[A-Za-z][A-Za-z'-]{1,}\b", text):
        tok = m.group()
        best, ratio = None, 0.0
        for cw in cwords:
            r = SequenceMatcher(None, tok.lower(), cw.lower()).ratio()
            if r > ratio:
                best, ratio = cw, r
        if best and ratio >= 0.8:
            hits.append((m.start(), m.end(), best))
    for s, e, cw in hits:
        near_verb = verb_after.match(text[e:e + 45]) is not None
        after_per = re.search(r"\b(?:per|by)\s+$", text[max(0, s - 8):s], re.I) is not None
        if near_verb or after_per:
            return {"value": cw, "confidence": 0.9}

    # 2) a capitalised word right before a request verb ("Dave said ...")
    for m in re.finditer(rf"\b([A-Z][a-z]{{1,14}})(?:\s+from\s+[^,.;]{{1,30}}?)?\s+{_REQ_VERBS}\b", text):
        if m.group(1).lower() not in _NOT_NAMES:
            return {"value": m.group(1), "confidence": 0.75}

    # 3) a role ("the builder told me ...", "per the super")
    m = re.search(rf"\b(?:the|our)\s+({_ROLES})\b[^.;,]{{0,20}}?\s+{_REQ_VERBS}\b", text, re.I)
    if m:
        return {"value": "the " + " ".join(m.group(1).lower().split()), "confidence": 0.7}
    m = re.search(rf"\bper\s+(?:the\s+)?({_ROLES})\b", text, re.I)
    if m:
        return {"value": "the " + " ".join(m.group(1).lower().split()), "confidence": 0.7}

    # 4) a contact is mentioned, but not obviously as the one who asked
    if len({h[2] for h in hits}) == 1:
        return {"value": hits[0][2], "confidence": 0.5}
    return {"value": None, "confidence": 0.0}


# ------------------------------------------------------------------ location

def _location(text: str) -> Dict[str, Any]:
    ordinal = re.search(_ORD_RE, text, re.I)
    level = re.search(_LEVEL_RE, text, re.I)
    room = re.search(_ROOM_RE, text, re.I)
    parts: List[str] = []
    if ordinal:
        parts.append(" ".join(ordinal.group(0).lower().split()))
    if level:
        parts.append(" ".join(level.group(0).lower().split()))
    if room and not (ordinal and room.group(0).lower() in ordinal.group(0).lower()):
        parts.append(" ".join(room.group(0).lower().split()))
    if not parts:
        return {"value": None, "confidence": 0.0}
    return {"value": " ".join(parts), "confidence": 0.85 if len(parts) > 1 else 0.75}


# ------------------------------------------------------------------ change

_CHANGE_PRIMARY = re.compile(rf"\b{_ASK_VERBS}\s+(?:me\s+|us\s+)?(?:to|for|that)\s+(?P<c>.+)", re.I)
_CHANGE_SECONDARY = re.compile(rf"\b{_WANT_VERBS}\s+(?:me\s+|us\s+)?(?:to\s+)?(?P<c>.+)", re.I)
_TAIL_WORDS = re.compile(
    r"(?:\s+(?:for|takes?|taking|probably|maybe|about|roughly|approximately|around|say|so|and|it'?ll|it\s+will|"
    r"that'?ll|an|extra))+\s*$", re.I)
_CUT_NEED = re.compile(r"\s+(?:and\s+)?(?:we'?ll|we\s+will|i'?ll|i\s+will|will)\s+need\b", re.I)


def _change(text: str) -> Dict[str, Any]:
    m = _CHANGE_PRIMARY.search(text) or _CHANGE_SECONDARY.search(text)
    if not m:
        return {"value": None, "confidence": 0.0}
    c = re.split(r"[,.;!?]| - |—", m.group("c"), maxsplit=1)[0]
    cuts = [d["start"] for d in nm.extract_hours(c)]
    need = _CUT_NEED.search(c)
    if need:
        cuts.append(need.start())
    if cuts:
        c = c[:min(cuts)]
    c = _TAIL_WORDS.sub("", c)
    for _ in range(2):
        c = _TRAILING_LOC.sub("", c)
    c = c.strip(" ,.;:-")
    if len(c) < 3:
        return {"value": None, "confidence": 0.0}
    return {"value": c, "confidence": 0.75}


# ------------------------------------------------------------------ hours / materials / amount

def _hours(text: str) -> Dict[str, Any]:
    found = nm.extract_hours(text)
    if not found or any(d["range"] for d in found):
        return {"value": None, "confidence": 0.0}
    values = {d["hours"] for d in found}
    if len(values) != 1:  # several different durations: do not guess which one is "the" extra time
        return {"value": None, "confidence": 0.0}
    hedged = any(d["hedged"] for d in found)
    return {"value": found[0]["hours"], "confidence": 0.6 if hedged else 0.85}


_MATERIAL_PATTERNS = [
    re.compile(r"\b(?:we'?ll|we\s+will|i'?ll|i\s+will|will|gonna|going\s+to)\s+need\s+(?:to\s+(?:get|order)\s+)?(?P<m>[^.;]+)", re.I),
    re.compile(r"\bplus\s+(?P<m>[^.;]+)", re.I),
    re.compile(r"\b(?:materials?|parts?)\s*(?:are|is|:)\s*(?P<m>[^.;]+)", re.I),
]


def _materials(text: str) -> Dict[str, Any]:
    for pat in _MATERIAL_PATTERNS:
        m = pat.search(text)
        if m:
            v = re.split(r" - |—", m.group("m"), maxsplit=1)[0].strip(" ,.;:-")
            if len(v) >= 3:
                return {"value": v, "confidence": 0.7}
    return {"value": None, "confidence": 0.0}


def _amount(text: str) -> Dict[str, Any]:
    m = re.search(r"\$\s?\d[\d,]*(?:\.\d+)?", text)
    if m:
        return {"value": m.group(0).replace("$ ", "$"), "confidence": 0.8}
    for m in re.finditer(r"\b(dollars|bucks)\b", text, re.I):
        words = re.findall(r"[\w,.]+", text[:m.start()])[-6:]
        for k in range(len(words), 0, -1):
            cand = " ".join(words[-k:])
            if nm.phrase_to_number(cand) is not None:
                return {"value": f"{cand} {m.group(1).lower()}", "confidence": 0.8}
    return {"value": None, "confidence": 0.0}


def rules_extract(transcript: str, contacts: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    text = _clean(transcript)
    contacts = contacts or []
    raw: Dict[str, Dict[str, Any]] = {n: {"value": None, "confidence": 0.0} for n in FIELD_NAMES}
    if not text:
        return raw
    raw["requester"] = _requester(text, contacts)
    raw["location"] = _location(text)
    raw["change"] = _change(text)
    raw["extra_time_hours"] = _hours(text)
    raw["materials"] = _materials(text)
    raw["amount_mentioned"] = _amount(text)
    return raw
