"""Hash helpers for the per-capture tamper-evident event log.

hash = sha256( prev_hash | seq | at | type | canonical_json(payload) )

This makes later edits *detectable* (change one event and every later hash stops
matching). It is NOT tamper-proof: whoever controls the server and the database
could rewrite the whole chain. Say "tamper-evident", never "tamper-proof".
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def event_hash(prev_hash: str, seq: int, at: str, type_: str, payload: Any) -> str:
    material = f"{prev_hash}|{seq}|{at}|{type_}|{canonical(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def verify_chain(events: List[Dict[str, Any]]) -> List[str]:
    """Check an ordered list of events. Returns human-readable problems ([] = consistent).

    Each event needs: seq, at, type, payload (already parsed), prev_hash, hash.
    """
    problems: List[str] = []
    prev = GENESIS
    expected_seq = 1
    for ev in events:
        seq = ev["seq"]
        if seq != expected_seq:
            problems.append(f"event #{seq}: expected seq {expected_seq} (missing or reordered event)")
        if ev["prev_hash"] != prev:
            problems.append(f"event #{seq}: prev_hash does not match previous event")
        recomputed = event_hash(ev["prev_hash"], seq, ev["at"], ev["type"], ev["payload"])
        if recomputed != ev["hash"]:
            problems.append(f"event #{seq} ({ev['type']}): content does not match its hash")
        prev = ev["hash"]
        expected_seq = seq + 1
    return problems
