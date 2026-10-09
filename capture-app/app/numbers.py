"""Spoken-number and duration parsing.

Used in two places:
  * the grounding check (extract.py): "is this number / duration really in the transcript?"
  * the rules-based fallback extractor.
Speech-to-text sometimes writes "two hours", sometimes "2 hours", so both forms are understood.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

UNITS: Dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS: Dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
ORDINALS: Dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

_DIGITS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")


def _spoken_int(tokens: List[str], i: int) -> Optional[Tuple[int, int]]:
    """Parse a spoken whole number starting at tokens[i] ("three hundred and fifty" -> 350).
    Returns (value, index_after) or None."""
    total = 0
    current = 0
    last: Optional[str] = None
    j = i
    started = False
    while j < len(tokens):
        t = tokens[j]
        if t in UNITS:
            if UNITS[t] >= 10:  # teens
                if last not in (None, "hundred", "thousand", "and"):
                    break
                current += UNITS[t]
                last = "teen"
            else:
                if last not in (None, "tens", "hundred", "thousand", "and"):
                    break
                current += UNITS[t]
                last = "unit"
        elif t in TENS:
            if last not in (None, "hundred", "thousand", "and"):
                break
            current += TENS[t]
            last = "tens"
        elif t == "hundred":
            if last != "unit":
                break
            current *= 100
            last = "hundred"
        elif t == "thousand":
            if last in (None, "and"):
                break
            total += current * 1000
            current = 0
            last = "thousand"
        elif t == "and":
            nxt = tokens[j + 1] if j + 1 < len(tokens) else ""
            if last in ("hundred", "thousand") and (nxt in UNITS or nxt in TENS):
                last = "and"
            else:
                break
        else:
            break
        started = True
        j += 1
    if not started:
        return None
    if last == "and":
        j -= 1
    return total + current, j


def phrase_to_number(phrase: str) -> Optional[float]:
    """"twenty five" -> 25.0, "300" -> 300.0, "2.5" -> 2.5, anything else -> None."""
    p = (phrase or "").strip().lower()
    if not p:
        return None
    if _DIGITS.fullmatch(p):
        return float(p.replace(",", ""))
    tokens = _TOKEN.findall(p)
    parsed = _spoken_int(tokens, 0)
    if parsed and parsed[1] == len(tokens):
        return float(parsed[0])
    return None


def numbers_in(text: str, ignore_lone_one: bool = False) -> Set[float]:
    """Every number mentioned in `text`, as digits or words (including ordinals, 'one point five',
    'two and a half', 'half'). `ignore_lone_one` skips a bare "one" ("the one on the left"), which is
    usually a pronoun; use it when reading *values*, not when reading the transcript."""
    low = (text or "").lower()
    nums: Set[float] = set()
    for m in _DIGITS.finditer(low):
        nums.add(float(m.group().replace(",", "")))

    tokens = _TOKEN.findall(low)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok[0].isdigit():
            if tokens[i + 1:i + 4] == ["and", "a", "half"]:
                nums.add(float(tok) + 0.5)
            i += 1
            continue
        if tok in ORDINALS:
            nums.add(float(ORDINALS[tok]))
            i += 1
            continue
        if tok == "half":
            nums.add(0.5)
            i += 1
            continue
        parsed = _spoken_int(tokens, i)
        if not parsed:
            i += 1
            continue
        value, j = parsed
        lone_one = (j - i == 1 and tokens[i] == "one")
        if not (lone_one and ignore_lone_one):
            nums.add(float(value))
        if j < len(tokens) and tokens[j] == "point":  # "one point five"
            k = j + 1
            digits = ""
            while k < len(tokens) and tokens[k] in UNITS and UNITS[tokens[k]] < 10 and len(digits) < 3:
                digits += str(UNITS[tokens[k]])
                k += 1
            if digits:
                nums.add(float(f"{value}.{digits}"))
                j = k
        if tokens[j:j + 3] == ["and", "a", "half"]:
            nums.add(value + 0.5)
            j += 3
        i = max(j, i + 1)
    return nums


def words_to_numbers(text: str) -> str:
    """Replace spoken numbers with digits ("three hundred mil" -> "300 mil"). For comparing sentences."""
    toks = re.findall(r"[A-Za-z]+|\d+(?:\.\d+)?|\S", text or "")
    low = [t.lower() for t in toks]
    out: List[str] = []
    i = 0
    while i < len(toks):
        parsed = _spoken_int(low, i) if low[i].isalpha() else None
        if parsed:
            out.append(str(parsed[0]))
            i = parsed[1]
        else:
            out.append(toks[i])
            i += 1
    return " ".join(out)


# ------------------------------------------------------------------ durations

def _alt(words: List[str]) -> str:
    return "|".join(sorted(words, key=len, reverse=True))


_UNIT_WORDS = _alt(list(UNITS))
_TENS_WORDS = _alt(list(TENS))
_NUM = (
    rf"(?:\d+(?:\.\d+)?"
    rf"|(?:{_TENS_WORDS})(?:[\s-](?:one|two|three|four|five|six|seven|eight|nine))?"
    rf"|{_UNIT_WORDS})"
)
_HOUR = r"(?:hours?|hrs?|h)\b"
_MIN = r"(?:minutes?|mins?)\b"
_DURATION_MODIFIER = r"(?:extra|more|additional)"

_P_RANGE = re.compile(rf"\b({_NUM})\s*(?:to|or|-|–|—)\s*({_NUM})\s*{_HOUR}", re.I)
_P_N_HALF = re.compile(rf"\b({_NUM})\s+and\s+a\s+half\s+{_HOUR}", re.I)
_P_HOUR_HALF = re.compile(r"\b(?:an?|one|1)\s+hour\s+and\s+a\s+half\b", re.I)
_P_HALF_HOUR = re.compile(r"\bhalf\s+an?\s+hour\b|\bhalf[\s-]hour\b|\ba\s+half[\s-]hour\b", re.I)
_P_HOURS = re.compile(rf"\b({_NUM})(?:\s+{_DURATION_MODIFIER})?\s*{_HOUR}", re.I)
_P_MINS = re.compile(rf"\b({_NUM})\s*{_MIN}", re.I)
_P_COUPLE = re.compile(r"\b(?:a\s+)?couple\s+(?:of\s+)?hours\b", re.I)
_P_AN_HOUR = re.compile(rf"\b(?:an|one|a)\s+(?:{_DURATION_MODIFIER}\s+)?hour\b", re.I)

_HEDGE_BEFORE = re.compile(
    r"\b(?:about|approx(?:imately)?|probably|prob|roughly|maybe|around|reckon|guess|say|"
    r"something like|at least|at most|up to|estimated?|could be|might be)\b", re.I)
_HEDGE_AFTER = re.compile(r"^\W{0,3}(?:or so|ish|give or take|maybe|probably)\b", re.I)


def _hedged(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 28):start]
    after = text[end:end + 14]
    return bool(_HEDGE_BEFORE.search(before) or _HEDGE_AFTER.search(after))


def extract_hours(text: str) -> List[Dict[str, Any]]:
    """Durations the speaker mentioned, converted to hours.

    Each item: {"hours", "hedged", "text", "range", "start"}. A range ("two to three hours") yields
    one item per end, both flagged `range` - callers must not pick one of them as *the* duration.
    "half a day", "a few hours", "all day" are deliberately NOT converted.
    """
    text = text or ""
    used: List[Tuple[int, int]] = []
    found: List[Dict[str, Any]] = []

    def free(m: "re.Match[str]") -> bool:
        return all(m.end() <= s or m.start() >= e for s, e in used)

    def add(m: "re.Match[str]", hours: float, is_range: bool = False, force_hedge: bool = False) -> None:
        used.append((m.start(), m.end()))
        found.append({
            "hours": round(hours, 2),
            "hedged": force_hedge or is_range or _hedged(text, m.start(), m.end()),
            "text": m.group(0),
            "range": is_range,
            "start": m.start(),
        })

    for m in _P_RANGE.finditer(text):
        a, b = phrase_to_number(m.group(1)), phrase_to_number(m.group(2))
        if a is not None and b is not None and free(m):
            add(m, a, True)
            found.append({**found[-1], "hours": round(b, 2)})
    for m in _P_N_HALF.finditer(text):
        n = phrase_to_number(m.group(1))
        if n is not None and free(m):
            add(m, n + 0.5)
    for m in _P_HOUR_HALF.finditer(text):
        if free(m):
            add(m, 1.5)
    for m in _P_HALF_HOUR.finditer(text):
        if free(m):
            add(m, 0.5)
    for m in _P_HOURS.finditer(text):
        n = phrase_to_number(m.group(1))
        if n is not None and free(m):
            add(m, n)
    for m in _P_MINS.finditer(text):
        n = phrase_to_number(m.group(1))
        if n is not None and free(m):
            add(m, n / 60.0)
    for m in _P_COUPLE.finditer(text):
        if free(m):
            add(m, 2.0, force_hedge=True)
    for m in _P_AN_HOUR.finditer(text):
        if free(m):
            add(m, 1.0)

    found.sort(key=lambda d: d["start"])
    return found
