from app.hashing import GENESIS, canonical, event_hash, sha256_bytes, sha256_file, verify_chain


def make_chain(n=3):
    events, prev = [], GENESIS
    for i in range(1, n + 1):
        payload = {"n": i, "text": "café", "x": 1.5}
        at = f"2026-10-09T05:0{i}:00.000Z"
        h = event_hash(prev, i, at, "field_edited", payload)
        events.append({"seq": i, "at": at, "type": "field_edited", "payload": payload, "prev_hash": prev, "hash": h})
        prev = h
    return events


def test_canonical_is_key_order_independent():
    assert canonical({"b": 1, "a": [2, 1]}) == canonical({"a": [2, 1], "b": 1})
    assert canonical({"é": 1}) == '{"é":1}'


def test_valid_chain_has_no_problems():
    assert verify_chain(make_chain()) == []


def test_event_hash_depends_on_every_part():
    base = event_hash(GENESIS, 1, "t", "created", {"a": 1})
    assert base != event_hash("1" * 64, 1, "t", "created", {"a": 1})
    assert base != event_hash(GENESIS, 2, "t", "created", {"a": 1})
    assert base != event_hash(GENESIS, 1, "u", "created", {"a": 1})
    assert base != event_hash(GENESIS, 1, "t", "reviewed", {"a": 1})
    assert base != event_hash(GENESIS, 1, "t", "created", {"a": 2})


def test_modified_payload_is_detected():
    ev = make_chain()
    ev[1]["payload"]["n"] = 99
    assert any("event #2" in p and "hash" in p for p in verify_chain(ev))


def test_deleted_middle_event_is_detected():
    ev = make_chain()
    del ev[1]
    assert verify_chain(ev)


def test_reordered_events_are_detected():
    ev = make_chain()
    ev[0], ev[1] = ev[1], ev[0]
    assert verify_chain(ev)


def test_rewriting_one_event_and_its_hash_breaks_the_next_link():
    ev = make_chain()
    ev[0]["payload"]["n"] = 42
    ev[0]["hash"] = event_hash(ev[0]["prev_hash"], 1, ev[0]["at"], ev[0]["type"], ev[0]["payload"])
    problems = verify_chain(ev)
    assert any("event #2" in p and "prev_hash" in p for p in problems)


def test_file_hash_matches_bytes(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    assert sha256_file(p) == sha256_bytes(b"hello")
