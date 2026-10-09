"""Per-capture, append-only, hash-chained event log.

The `events` table is the source of truth. The derived columns on `captures`
(transcript, fields, status, deadline, file hashes ...) are re-materialised from the
events after every write, so `verify_capture` can replay the log and compare it with
what is stored: edit a row or swap a file behind the app's back and verification fails.

Tamper-EVIDENT, not tamper-proof: someone who controls the server and the DB could
rewrite the whole chain. The head hash can be copied somewhere outside (e.g. into the
confirmation SMS) to make that harder.
"""
from __future__ import annotations

import copy
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Settings
from .db import dumps, loads
from .hashing import canonical, event_hash, sha256_bytes, sha256_file, verify_chain, GENESIS
from .schemas import FIELD_NAMES

EVENT_TYPES = (
    "created",
    "photo_added",
    "extracted",
    "field_edited",
    "reviewed",
    "confirmation_requested",
    "confirmed",
    "deadline_set",
    "note_set",
    "location_enriched",
)

MATERIALIZED = (
    "transcript",
    "fields",
    "status",
    "notice_deadline",
    "deadline_note",
    "user_note",
    "audio_sha256",
    "photo_sha256",
    "extract_meta",
    "client_meta",
    "location",
)

INTEGRITY_NOTE = (
    "Tamper-evident, not tamper-proof: any later change to this record or its files is detectable, "
    "but whoever controls the server could rewrite the whole history."
)


def now_iso() -> str:
    d = datetime.now(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def blank_field() -> Dict[str, Any]:
    return {"value": None, "confidence": 0.0, "source": None, "edited": False}


# ---------------------------------------------------------------- append / load

def append_event(conn: sqlite3.Connection, capture_id: str, type_: str, payload: Dict[str, Any],
                 at: Optional[str] = None) -> Dict[str, Any]:
    """Append one event. Must run inside Database.tx() so seq cannot race."""
    if type_ not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {type_}")
    last = conn.execute(
        "SELECT seq, hash FROM events WHERE capture_id=? ORDER BY seq DESC LIMIT 1", (capture_id,)
    ).fetchone()
    seq = (last["seq"] + 1) if last else 1
    prev = last["hash"] if last else GENESIS
    at = at or now_iso()
    digest = event_hash(prev, seq, at, type_, payload)
    conn.execute(
        "INSERT INTO events (capture_id, seq, at, type, payload, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
        (capture_id, seq, at, type_, canonical(payload), prev, digest),
    )
    return {"seq": seq, "at": at, "type": type_, "payload": payload, "prev_hash": prev, "hash": digest}


def load_events(conn: sqlite3.Connection, capture_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT seq, at, type, payload, prev_hash, hash FROM events WHERE capture_id=? ORDER BY seq",
        (capture_id,),
    ).fetchall()
    return [
        {
            "seq": r["seq"],
            "at": r["at"],
            "type": r["type"],
            "payload": loads(r["payload"], {}),
            "prev_hash": r["prev_hash"],
            "hash": r["hash"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------- replay

def replay(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rebuild the derived record state purely from the events."""
    s: Dict[str, Any] = {
        "transcript": None,
        "fields": {},
        "status": "captured",
        "notice_deadline": None,
        "deadline_note": None,
        "user_note": None,
        "audio_sha256": None,
        "photo_sha256": None,
        "extract_meta": {},
        "client_meta": {},
        "location": None,
    }
    for ev in events:
        t, p = ev["type"], ev["payload"]
        if t == "created":
            s["audio_sha256"] = p.get("audio_sha256")
            s["photo_sha256"] = p.get("photo_sha256")
            s["client_meta"] = copy.deepcopy(p.get("client_meta") or {})
            s["location"] = copy.deepcopy(p.get("location"))
        elif t == "photo_added":
            s["photo_sha256"] = p.get("photo_sha256")
        elif t == "extracted":
            s["transcript"] = p.get("transcript")
            s["fields"] = copy.deepcopy(p.get("fields") or {})
            s["extract_meta"] = {k: copy.deepcopy(v) for k, v in p.items() if k not in ("transcript", "fields")}
            s["status"] = "needs_review"
        elif t == "field_edited":
            new = p.get("new")
            s["fields"][p.get("field")] = {
                "value": new,
                "confidence": 1.0 if new not in (None, "") else 0.0,
                "source": "user",
                "edited": True,
            }
        elif t == "reviewed":
            if s["status"] != "confirmed":
                s["status"] = "reviewed"
        elif t == "confirmed":
            s["status"] = "confirmed"
        elif t == "deadline_set":
            s["notice_deadline"] = p.get("notice_deadline")
            s["deadline_note"] = p.get("note")
        elif t == "note_set":
            s["user_note"] = p.get("note")
        elif t == "location_enriched" and s["location"] is not None:
            for key in ("display_name", "provider", "attribution"):
                if p.get(key):
                    s["location"][key] = p[key]
        # confirmation_requested: no derived column; see confirmation_state()
    return s


def materialize(conn: sqlite3.Connection, capture_id: str) -> Dict[str, Any]:
    state = replay(load_events(conn, capture_id))
    conn.execute(
        "UPDATE captures SET transcript=?, fields=?, status=?, notice_deadline=?, deadline_note=?, user_note=?, "
        "audio_sha256=?, photo_sha256=?, extract_meta=?, client_meta=?, location=? WHERE id=?",
        (
            state["transcript"],
            dumps(state["fields"]),
            state["status"],
            state["notice_deadline"],
            state["deadline_note"],
            state["user_note"],
            state["audio_sha256"],
            state["photo_sha256"],
            dumps(state["extract_meta"]),
            dumps(state["client_meta"]),
            dumps(state["location"]) if state["location"] is not None else None,
            capture_id,
        ),
    )
    return state


def record(conn: sqlite3.Connection, capture_id: str, type_: str, payload: Dict[str, Any],
           at: Optional[str] = None) -> Dict[str, Any]:
    """Append an event and refresh the derived columns, in the caller's transaction."""
    ev = append_event(conn, capture_id, type_, payload, at)
    materialize(conn, capture_id)
    return ev


# ---------------------------------------------------------------- views over the log

def facts_digest(fields: Dict[str, Any]) -> str:
    """Fingerprint of the six AI-fillable values, so we can tell when a confirmation went stale."""
    facts = {n: (fields.get(n) or {}).get("value") for n in FIELD_NAMES}
    return sha256_bytes(canonical(facts).encode("utf-8"))


def edit_history(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "at": e["at"],
            "field": e["payload"].get("field"),
            "old": e["payload"].get("old"),
            "new": e["payload"].get("new"),
            "hash": e["hash"],
        }
        for e in events
        if e["type"] == "field_edited"
    ]


def confirmation_state(events: List[Dict[str, Any]], fields: Dict[str, Any]) -> Dict[str, Any]:
    req = conf = None
    for e in events:
        if e["type"] == "confirmation_requested":
            req = e
        elif e["type"] == "confirmed":
            conf = e
    st: Dict[str, Any] = {
        "requested_at": req["at"] if req else None,
        "requested_to": (req["payload"].get("to_name") or req["payload"].get("to_phone")) if req else None,
        "confirmed_at": conf["at"] if conf else None,
        "confirmed_by": conf["payload"].get("confirmed_by") if conf else None,
        "note": conf["payload"].get("note") if conf else None,
        "proof_path": conf["payload"].get("proof_path") if conf else None,
        "stale": False,
    }
    ref = conf or req
    if ref is not None:
        # Fields changed after the request / confirmation -> the confirmation no longer matches the record.
        st["stale"] = ref["payload"].get("facts_digest") != facts_digest(fields)
    return st


# ---------------------------------------------------------------- verification

def _check_file(settings: Settings, rel: Optional[str], expected: Optional[str], label: str,
                problems: List[str]) -> None:
    if rel is None and expected is None:
        return
    if rel is None or expected is None:
        problems.append(f"{label}: stored file path and logged hash disagree")
        return
    root = Path(settings.media_dir).resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        problems.append(f"{label}: file path points outside the media folder")
        return
    if not path.is_file():
        problems.append(f"{label}: file is missing")
        return
    if sha256_file(path) != expected:
        problems.append(f"{label}: file does not match the hash logged at capture time")


def verify_capture(conn: sqlite3.Connection, settings: Settings, capture_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
    if row is None:
        return None
    events = load_events(conn, capture_id)
    problems: List[str] = []
    if not events:
        problems.append("no events recorded for this capture")
    problems += verify_chain(events)

    state = replay(events)
    stored = {
        "transcript": row["transcript"],
        "fields": loads(row["fields"], {}),
        "status": row["status"],
        "notice_deadline": row["notice_deadline"],
        "deadline_note": row["deadline_note"],
        "user_note": row["user_note"],
        "audio_sha256": row["audio_sha256"],
        "photo_sha256": row["photo_sha256"],
        "extract_meta": loads(row["extract_meta"], {}),
        "client_meta": loads(row["client_meta"], {}),
        "location": loads(row["location"], None),
    }
    for key in MATERIALIZED:
        if canonical(stored[key]) != canonical(state[key]):
            problems.append(f"stored {key} differs from what the event history says")

    _check_file(settings, row["audio_path"], state["audio_sha256"], "audio", problems)
    _check_file(settings, row["photo_path"], state["photo_sha256"], "photo", problems)
    for e in events:
        if e["type"] == "confirmed" and e["payload"].get("proof_path"):
            _check_file(settings, e["payload"].get("proof_path"), e["payload"].get("proof_sha256"),
                        "confirmation proof", problems)

    return {
        "ok": not problems,
        "events": len(events),
        "head_hash": events[-1]["hash"] if events else None,
        "problems": problems,
        "checked_at": now_iso(),
        "note": INTEGRITY_NOTE,
    }
