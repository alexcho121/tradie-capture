import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.geocode import GeocodeError, ReadableLocation
from app.main import create_app
from conftest import AUDIO, PHOTO, TX


def sql(settings, query, args=()):
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(query, args)
        conn.commit()
    finally:
        conn.close()


def verify(client, cid):
    return client.get(f"/captures/{cid}/verify").json()


# ------------------------------------------------------------------ the one-button part

def test_save_is_evidence_only(client, capture, settings):
    assert capture["status"] == "captured"
    assert capture["flags"] == ["not_structured_yet"]
    assert capture["integrity"]["events"] == 1
    assert capture["transcript"] is None
    assert all(f["value"] is None for f in capture["fields"].values())
    assert client.get(capture["audio_url"]).content == AUDIO[1]
    assert verify(client, capture["id"])["ok"] is True


def test_location_is_saved_once_and_is_tamper_evident(client, settings):
    r = client.post("/captures", files={"photo": PHOTO}, data={
        "latitude": "-33.78", "longitude": "151.08", "accuracy_m": "12", "located_at": "2026-10-09T10:43:00+11:00",
        "client_captured_at": "2026-10-09T10:43:05+11:00"})
    assert r.status_code == 201
    cap = r.json()
    assert cap["location"] == {"latitude": -33.78, "longitude": 151.08, "accuracy_m": 12.0,
                               "located_at": "2026-10-09T10:43:00+11:00", "source": "device",
                               "display_name": None, "provider": None, "attribution": None}
    assert cap["device_captured_at"] == "2026-10-09T10:43:05+11:00"
    assert verify(client, cap["id"])["ok"] is True
    html = client.get(f"/captures/{cap['id']}/record.html").text
    assert "Location reported by the phone" in html and "openstreetmap.org" in html and "(not verified)" in html
    sql(settings, "UPDATE captures SET location=? WHERE id=?",
        (json.dumps({"latitude": 0.0, "longitude": 0.0, "source": "device"}), cap["id"]))
    v = verify(client, cap["id"])
    assert v["ok"] is False and any("location" in p for p in v["problems"])


@pytest.mark.parametrize("data", [
    {"latitude": "-33.7"},
    {"latitude": "95", "longitude": "151"},
    {"latitude": "abc", "longitude": "1"},
    {"latitude": "nan", "longitude": "1"},
    {"latitude": "1", "longitude": "1", "accuracy_m": "-3"},
])
def test_bad_locations_are_rejected(client, settings, data):
    assert client.post("/captures", data=data, files={"photo": PHOTO}).status_code == 422
    assert list(settings.media_dir.iterdir()) == []


def test_missing_location_is_fine(client):
    r = client.post("/captures", data={"latitude": "", "longitude": ""}, files={"photo": PHOTO})
    assert r.status_code == 201 and r.json()["location"] is None


def test_location_can_be_enriched_after_capture_without_changing_raw_gps(client, settings, monkeypatch):
    cap = client.post("/captures", files={"photo": PHOTO}, data={
        "latitude": "-33.7769", "longitude": "151.0812", "accuracy_m": "14",
    }).json()
    calls = []
    monkeypatch.setattr(client.app.state.geocoder, "reverse", lambda lat, lon: (
        calls.append((lat, lon)) or ReadableLocation("123 Example St, Macquarie Park NSW")
    ))

    resolved = client.post(f"/captures/{cap['id']}/location/resolve")
    assert resolved.status_code == 200
    location = resolved.json()["location"]
    assert (location["latitude"], location["longitude"], location["accuracy_m"]) == (-33.7769, 151.0812, 14.0)
    assert location["display_name"] == "123 Example St, Macquarie Park NSW"
    assert location["provider"] == "nominatim"
    assert location["attribution"] == "© OpenStreetMap contributors"
    assert calls == [(-33.7769, 151.0812)]
    assert verify(client, cap["id"])["ok"] is True

    again = client.post(f"/captures/{cap['id']}/location/resolve").json()
    assert again["integrity"]["events"] == resolved.json()["integrity"]["events"]
    assert calls == [(-33.7769, 151.0812)]


def test_reverse_geocoding_failure_never_changes_or_loses_capture(client, monkeypatch):
    cap = client.post("/captures", files={"photo": PHOTO}, data={
        "latitude": "-33.7769", "longitude": "151.0812", "accuracy_m": "14",
    }).json()
    monkeypatch.setattr(client.app.state.geocoder, "reverse", lambda *_: (_ for _ in ()).throw(GeocodeError("offline")))

    resolved = client.post(f"/captures/{cap['id']}/location/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["location"]["display_name"] is None
    assert resolved.json()["location"]["latitude"] == -33.7769
    assert resolved.json()["integrity"]["events"] == 1
    assert client.get(cap["photo_url"]).content == PHOTO[1]


def test_location_resolve_without_gps_is_a_noop(client, capture, monkeypatch):
    monkeypatch.setattr(client.app.state.geocoder, "reverse", lambda *_: pytest.fail("geocoder should not run"))
    resolved = client.post(f"/captures/{capture['id']}/location/resolve")
    assert resolved.status_code == 200 and resolved.json()["location"] is None


def test_offline_retry_with_the_same_client_id_does_not_duplicate(client, settings):
    data = {"client_capture_id": "rec-014"}
    first = client.post("/captures", data=data, files={"audio": AUDIO, "photo": PHOTO})
    again = client.post("/captures", data=data, files={"audio": AUDIO, "photo": PHOTO})
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"] and again.json()["client_capture_id"] == "rec-014"
    assert len(client.get("/captures").json()) == 1
    assert len(list(settings.media_dir.iterdir())) == 1


def test_concurrent_retries_still_create_one_capture(client, settings):
    from concurrent.futures import ThreadPoolExecutor
    def upload(_):
        r = client.post("/captures", data={"client_capture_id": "rec-race"}, files={"photo": PHOTO})
        return r.status_code, r.json()["id"]
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(upload, range(6)))
    assert {cid for _, cid in results} == {results[0][1]}
    assert sorted(code for code, _ in results).count(201) == 1
    assert len(list(settings.media_dir.iterdir())) == 1


def test_bad_client_id_is_rejected(client):
    assert client.post("/captures", data={"client_capture_id": "../etc"}, files={"photo": PHOTO}).status_code == 422


def test_hashes_computed_on_the_phone_are_checked(client, settings):
    import hashlib
    good = hashlib.sha256(PHOTO[1]).hexdigest()
    assert client.post("/captures", data={"client_photo_sha256": good.upper()}, files={"photo": PHOTO}).status_code == 201
    bad = client.post("/captures", data={"client_photo_sha256": "0" * 64}, files={"photo": PHOTO})
    assert bad.status_code == 422 and "hash" in bad.json()["detail"]
    assert client.post("/captures", data={"client_photo_sha256": "xyz"}, files={"photo": PHOTO}).status_code == 422
    assert len(client.get("/captures").json()) == 1
    assert len(list(settings.media_dir.iterdir())) == 1


def test_database_from_v1_is_migrated(tmp_path):
    import sqlite3 as sq
    from app.db import Database
    path = tmp_path / "old.db"
    conn = sq.connect(path)
    conn.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, site_contacts TEXT NOT NULL DEFAULT '[]',
                               created_at TEXT NOT NULL);
        CREATE TABLE captures (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, created_at TEXT NOT NULL,
            audio_path TEXT, photo_path TEXT, audio_sha256 TEXT, photo_sha256 TEXT, transcript TEXT,
            fields TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'captured', notice_deadline TEXT,
            deadline_note TEXT, extract_meta TEXT NOT NULL DEFAULT '{}', client_meta TEXT NOT NULL DEFAULT '{}');
    """)
    conn.close()
    Database(path)
    cols = {r[1] for r in sq.connect(path).execute("PRAGMA table_info(captures)")}
    assert {"location", "client_id"} <= cols


def test_capture_without_project_uses_a_default_one(client):
    r = client.post("/captures", files={"photo": PHOTO})
    assert r.status_code == 201
    projects = client.get("/projects").json()
    assert len(projects) == 1 and projects[0]["id"] == r.json()["project_id"]


def test_capture_needs_audio_or_photo(client, project):
    assert client.post("/captures", data={"project_id": project["id"]}).status_code == 400


def test_empty_file_part_from_a_browser_form_is_ignored(client, project):
    r = client.post("/captures", data={"project_id": project["id"]},
                    files={"audio": ("", b"", "application/octet-stream"), "photo": PHOTO})
    assert r.status_code == 201 and r.json()["audio_url"] is None and r.json()["photo_url"]


def test_dev_harness_is_served(client):
    r = client.get("/dev/")
    assert r.status_code == 200 and "PRESS" in r.text


def test_root_opens_the_capture_app(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/app/"
    page = client.get("/app/")
    assert page.status_code == 200 and "TAP TO CAPTURE" in page.text and "Record now. Review later." in page.text
    for hidden_from_mvp in ("Work area", "Site contacts", "Needs review", "Request confirmation",
                            "Notice deadline", "View integrity details", ">Print<"):
        assert hidden_from_mvp not in page.text
    assert "navigator.geolocation.getCurrentPosition" in page.text


def test_app_is_installable_and_has_an_offline_shell(client):
    sw = client.get("/app/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    manifest = client.get("/app/manifest.json").json()
    assert manifest["start_url"] == "/app/" and manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert client.get("/app/" + icon["src"]).headers["content-type"] == "image/png"
    assert client.get("/app/apple-touch-icon.png").status_code == 200


def test_unknown_project_is_404_and_leaves_no_files(client, settings):
    r = client.post("/captures", data={"project_id": "nope"}, files={"audio": AUDIO})
    assert r.status_code == 404
    assert list(settings.media_dir.iterdir()) == []


def test_unsupported_and_empty_uploads_are_rejected(client, project):
    bad = client.post("/captures", data={"project_id": project["id"]},
                      files={"audio": ("x.exe", b"MZ", "application/x-msdownload")})
    assert bad.status_code == 415
    empty = client.post("/captures", data={"project_id": project["id"]},
                        files={"audio": ("a.webm", b"", "audio/webm")})
    assert empty.status_code == 400


def test_oversized_upload_is_rejected_and_cleaned_up(tmp_path, project):
    s = Settings(data_dir=tmp_path / "small", stt_provider="mock", extract_provider="rules",
                 max_upload_mb=1, rate_limit_per_hour=0)
    c = TestClient(create_app(s))
    r = c.post("/captures", files={"audio": ("a.webm", b"x" * (1_500_000), "audio/webm")})
    assert r.status_code == 413
    assert list(s.media_dir.iterdir()) == []
    assert c.get("/captures").json() == []


def test_photo_can_be_added_once_and_never_replaced(client, project):
    cap = client.post("/captures", data={"project_id": project["id"]}, files={"audio": AUDIO}).json()
    assert cap["photo_url"] is None
    added = client.post(f"/captures/{cap['id']}/photo", files={"photo": PHOTO})
    assert added.status_code == 200 and added.json()["photo_url"]
    assert client.post(f"/captures/{cap['id']}/photo", files={"photo": PHOTO}).status_code == 409
    assert verify(client, cap["id"])["ok"] is True


def test_cors_allows_the_frontend_origin(client):
    r = client.options("/captures", headers={"Origin": "https://frontend.example",
                                             "Access-Control-Request-Method": "POST"})
    assert r.headers["access-control-allow-origin"] == "*"


# ------------------------------------------------------------------ structuring

def test_extract_with_supplied_transcript(client, structured):
    f = structured["fields"]
    assert structured["status"] == "needs_review"
    assert f["requester"]["value"] == "John" and f["requester"]["source"] == "rules"
    assert f["extra_time_hours"]["value"] == 2 and f["extra_time_hours"]["confidence"] < 0.8
    assert structured["transcript"] == TX
    assert structured["extraction"]["transcript_source"] == "provided"
    assert structured["extraction"]["provider"] == "rules"


def test_stt_unavailable_does_not_lose_the_evidence(client, capture):
    r = client.post(f"/captures/{capture['id']}/extract")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "stt_unavailable"
    again = client.get(f"/captures/{capture['id']}").json()
    assert again["status"] == "captured" and again["photo_url"]
    assert verify(client, capture["id"])["ok"] is True


def test_extract_needs_audio_or_transcript(client, project):
    cap = client.post("/captures", data={"project_id": project["id"]}, files={"photo": PHOTO}).json()
    assert client.post(f"/captures/{cap['id']}/extract").status_code == 422


def test_silence_gives_an_empty_record_with_a_flag(client, capture, monkeypatch):
    # speech-to-text heard nothing: a blank structured record the worker can fill in, not an error
    monkeypatch.setattr("app.main.transcribe", lambda path, settings, contacts=None: ("", {"provider": "openai"}))
    r = client.post(f"/captures/{capture['id']}/extract")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "needs_review" and body["transcript"] == ""
    assert "no_speech" in body["flags"] and "missing:requester" in body["flags"]
    assert all(f["value"] is None for f in body["fields"].values())
    assert body["extraction"]["transcript_source"] == "stt"


def test_transcript_from_speech_to_text_is_used_and_labelled(client, capture, monkeypatch):
    monkeypatch.setattr("app.main.transcribe",
                        lambda path, settings, contacts=None: (TX, {"provider": "openai", "model": "whisper-1"}))
    body = client.post(f"/captures/{capture['id']}/extract").json()
    assert body["transcript"] == TX and body["extraction"]["transcript_source"] == "stt"
    assert body["extraction"]["stt"]["model"] == "whisper-1"


# ------------------------------------------------------------------ review and edit history

def test_full_flow_and_integrity(client, structured):
    cid = structured["id"]

    # worker edits two fields
    r = client.patch(f"/captures/{cid}/fields", json={"edits": {"location": "Level 3 bathroom", "materials": "2 elbows"}})
    assert r.status_code == 200
    body = r.json()
    assert body["fields"]["location"]["value"] == "Level 3 bathroom"
    assert body["fields"]["location"]["edited"] is True and body["fields"]["location"]["confidence"] == 1.0
    assert body["fields"]["location"]["source"] == "user"
    assert [e["field"] for e in body["edits"]] == ["location", "materials"]
    assert body["edits"][0]["old"] == "level 2 bathroom" and body["edits"][0]["new"] == "Level 3 bathroom"
    assert len(body["edits"][0]["hash"]) == 64
    assert body["status"] == "needs_review"

    # review
    reviewed = client.post(f"/captures/{cid}/review").json()
    assert reviewed["status"] == "reviewed"
    assert client.post(f"/captures/{cid}/review").json()["integrity"]["events"] == reviewed["integrity"]["events"]

    # ask the requester to confirm
    req = client.post(f"/captures/{cid}/confirmation/request", json={"to_phone": "+61 400 111 222"})
    assert req.status_code == 200
    data = req.json()
    assert data["sms_url"].startswith("sms:+61400111222?&body=")
    assert "Hi John" in data["message"] and "Where: Level 3 bathroom." in data["message"] and "YES" in data["message"]
    assert "Est. time: 2 hrs." in data["message"]
    assert data["ref"] in data["message"] and "$" not in data["message"]
    assert data["capture"]["confirmation"]["requested_at"] and not data["capture"]["confirmation"]["confirmed_at"]

    # attach the reply
    conf = client.post(f"/captures/{cid}/confirmation/confirm", data={"confirmed_by": "John", "note": "replied YES"},
                       files={"proof": ("reply.png", b"\x89PNG fake", "image/png")})
    assert conf.status_code == 200
    c = conf.json()
    assert c["status"] == "confirmed" and c["confirmation"]["confirmed_at"] and c["confirmation"]["proof_url"]
    assert c["confirmation"]["stale"] is False
    assert client.get(c["confirmation"]["proof_url"]).content == b"\x89PNG fake"

    # changing a fact afterwards makes the confirmation stale (and logs the edit)
    after = client.patch(f"/captures/{cid}/fields", json={"edits": {"extra_time_hours": 4}}).json()
    assert after["confirmation"]["stale"] is True and after["status"] == "confirmed"
    assert after["fields"]["extra_time_hours"]["value"] == 4

    # the contract notice deadline is typed in, never defaulted
    assert after["notice_deadline"] is None
    d = client.put(f"/captures/{cid}/deadline", json={"notice_deadline": "2026-10-20", "note": "clause 14.2"}).json()
    assert d["notice_deadline"] == "2026-10-20" and d["deadline_note"] == "clause 14.2"

    v = verify(client, cid)
    assert v["ok"] is True, v
    # created, extracted, 2x field_edited, reviewed, confirmation_requested, confirmed, field_edited, deadline_set
    assert v["events"] == 9 and len(v["head_hash"]) == 64
    assert "not tamper-proof" in v["note"]


def test_record_note_is_optional_logged_and_tamper_evident(client, capture, settings):
    cid = capture["id"]
    before = capture["integrity"]["events"]
    saved = client.put(f"/captures/{cid}/note", json={"note": "  John confirmed this verbally near the lift.  "})
    assert saved.status_code == 200
    body = saved.json()
    assert body["note"] == "John confirmed this verbally near the lift."
    assert body["integrity"]["events"] == before + 1
    assert verify(client, cid)["ok"] is True
    assert "John confirmed this verbally near the lift." in client.get(f"/captures/{cid}/record.html").text

    same = client.put(f"/captures/{cid}/note", json={"note": body["note"]}).json()
    assert same["integrity"]["events"] == body["integrity"]["events"]
    cleared = client.put(f"/captures/{cid}/note", json={"note": ""}).json()
    assert cleared["note"] is None and verify(client, cid)["ok"] is True

    sql(settings, "UPDATE captures SET user_note=? WHERE id=?", ("changed behind the app", cid))
    assert verify(client, cid)["ok"] is False


def test_edit_validation_and_noops(client, structured):
    cid = structured["id"]
    assert client.patch(f"/captures/{cid}/fields", json={"edits": {"colour": "red"}}).status_code == 422
    assert client.patch(f"/captures/{cid}/fields", json={"edits": {"extra_time_hours": "lots"}}).status_code == 422
    assert client.patch(f"/captures/{cid}/fields", json={"edits": {}}).status_code == 422
    before = client.get(f"/captures/{cid}").json()["integrity"]["events"]
    same = client.patch(f"/captures/{cid}/fields", json={"edits": {"requester": "John"}}).json()
    assert same["integrity"]["events"] == before and same["edits"] == []
    cleared = client.patch(f"/captures/{cid}/fields", json={"edits": {"requester": None}}).json()
    assert cleared["fields"]["requester"]["value"] is None and cleared["fields"]["requester"]["edited"] is True


def test_rerunning_the_ai_never_silently_overwrites_edits(client, structured):
    cid = structured["id"]
    client.patch(f"/captures/{cid}/fields", json={"edits": {"requester": "Johnno"}})
    blocked = client.post(f"/captures/{cid}/extract", json={"transcript": TX})
    assert blocked.status_code == 409
    forced = client.post(f"/captures/{cid}/extract?force=true", json={"transcript": TX}).json()
    assert forced["fields"]["requester"]["value"] == "John" and forced["fields"]["requester"]["edited"] is False
    # the earlier edit is still in the (append-only) history
    assert verify(client, cid)["ok"] is True


def test_review_needs_something_to_review(client, capture):
    assert client.post(f"/captures/{capture['id']}/review").status_code == 409
    client.patch(f"/captures/{capture['id']}/fields", json={"edits": {"change": "move pipe"}})
    assert client.post(f"/captures/{capture['id']}/review").json()["status"] == "reviewed"


# ------------------------------------------------------------------ confirmation guards

def test_confirmation_request_needs_a_change(client, capture):
    assert client.post(f"/captures/{capture['id']}/confirmation/request", json={}).status_code == 409


def test_confirmation_needs_proof_or_note(client, structured):
    assert client.post(f"/captures/{structured['id']}/confirmation/confirm").status_code == 400
    ok = client.post(f"/captures/{structured['id']}/confirmation/confirm", data={"note": "said yes on site"})
    assert ok.status_code == 200 and ok.json()["confirmation"]["proof_url"] is None


# ------------------------------------------------------------------ deadlines

def test_deadline_validation_and_listing(client, structured):
    cid = structured["id"]
    assert client.put(f"/captures/{cid}/deadline", json={"notice_deadline": "next week"}).status_code == 422
    today = datetime.now().date()
    client.put(f"/captures/{cid}/deadline", json={"notice_deadline": (today - timedelta(days=1)).isoformat()})
    out = client.get("/deadlines?within_days=7").json()
    assert out["items"][0]["capture_id"] == cid and out["items"][0]["overdue"] is True
    assert "does not calculate" in out["note"]
    client.put(f"/captures/{cid}/deadline", json={"notice_deadline": (today + timedelta(days=60)).isoformat()})
    assert client.get("/deadlines?within_days=7").json()["items"] == []
    assert client.get("/deadlines?within_days=90").json()["items"][0]["days_left"] >= 58
    client.put(f"/captures/{cid}/deadline", json={"notice_deadline": None})
    assert client.get("/deadlines?within_days=3650").json()["items"] == []
    assert verify(client, cid)["ok"] is True


# ------------------------------------------------------------------ tamper evidence

def test_tampering_with_the_stored_record_is_detected(client, structured, settings):
    cid = structured["id"]
    assert verify(client, cid)["ok"] is True
    fields = json.loads(sqlite3.connect(settings.db_path).execute(
        "SELECT fields FROM captures WHERE id=?", (cid,)).fetchone()[0])
    fields["extra_time_hours"]["value"] = 40
    sql(settings, "UPDATE captures SET fields=? WHERE id=?", (json.dumps(fields), cid))
    v = verify(client, cid)
    assert v["ok"] is False and any("fields" in p for p in v["problems"])


@pytest.mark.parametrize("column,value", [
    ("transcript", "something else entirely"),
    ("status", "confirmed"),
    ("notice_deadline", "2030-01-01"),
])
def test_tampering_with_other_columns_is_detected(client, structured, settings, column, value):
    sql(settings, f"UPDATE captures SET {column}=? WHERE id=?", (value, structured["id"]))
    v = verify(client, structured["id"])
    assert v["ok"] is False and any(column in p for p in v["problems"])


def test_tampering_with_the_event_log_is_detected(client, structured, settings):
    cid = structured["id"]
    sql(settings, "UPDATE events SET payload=? WHERE capture_id=? AND seq=2",
        (json.dumps({"transcript": "forged", "fields": {}}), cid))
    v = verify(client, cid)
    assert v["ok"] is False and any("event #2" in p for p in v["problems"])


def test_deleting_an_event_is_detected(client, structured, settings):
    cid = structured["id"]
    client.patch(f"/captures/{cid}/fields", json={"edits": {"location": "Level 3"}})
    client.post(f"/captures/{cid}/review")
    sql(settings, "DELETE FROM events WHERE capture_id=? AND seq=3", (cid,))
    assert verify(client, cid)["ok"] is False


def test_swapping_the_photo_or_audio_is_detected(client, capture, settings):
    cid = capture["id"]
    path = settings.media_dir / capture["photo_url"].removeprefix("/media/")
    path.write_bytes(b"\xff\xd8\xff a different photo")
    v = verify(client, cid)
    assert v["ok"] is False and any("photo" in p and "hash" in p for p in v["problems"])
    (settings.media_dir / capture["audio_url"].removeprefix("/media/")).unlink()
    assert any("audio: file is missing" in p for p in verify(client, cid)["problems"])


def test_printable_record_shows_problems_after_tampering(client, structured, settings):
    sql(settings, "UPDATE captures SET transcript='forged' WHERE id=?", (structured["id"],))
    html = client.get(f"/captures/{structured['id']}/record.html").text
    assert "Problems found" in html


# ------------------------------------------------------------------ printable record, deletion, limits

def test_record_html_is_escaped_and_honest(client, capture):
    evil = "John asked me to move <script>alert(1)</script> the pipe 300 mil on level 2"
    client.post(f"/captures/{capture['id']}/extract", json={"transcript": evil})
    client.patch(f"/captures/{capture['id']}/fields", json={"edits": {"materials": "<img src=x onerror=alert(2)>"}})
    html = client.get(f"/captures/{capture['id']}/record.html").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "not legal advice" in html.lower() and "tamper-evident" in html.lower()
    assert "Edit history" in html and "Integrity" in html


def test_record_html_labels_worker_attached_confirmation(client, structured):
    cid = structured["id"]
    client.post(f"/captures/{cid}/confirmation/confirm", data={"note": "he said yes"})
    html = client.get(f"/captures/{cid}/record.html").text
    assert "not verified by this system" in html


def test_delete_removes_rows_and_files(client, capture, settings):
    cid = capture["id"]
    assert client.delete(f"/captures/{cid}").status_code == 204
    assert client.get(f"/captures/{cid}").status_code == 404
    assert not (settings.media_dir / cid).exists()
    assert client.get(capture["photo_url"]).status_code == 404
    assert client.delete(f"/captures/{cid}").status_code == 404


def test_list_filters_by_status(client, project, structured):
    client.post("/captures", data={"project_id": project["id"]}, files={"photo": PHOTO})
    assert len(client.get("/captures").json()) == 2
    assert [c["id"] for c in client.get("/captures?status=needs_review").json()] == [structured["id"]]
    assert len(client.get("/captures?status=captured").json()) == 1


def test_paid_endpoint_is_rate_limited(tmp_path, project):
    s = Settings(data_dir=tmp_path / "rl", stt_provider="mock", extract_provider="rules", rate_limit_per_hour=2)
    c = TestClient(create_app(s))
    cid = c.post("/captures", files={"photo": PHOTO}).json()["id"]
    codes = [c.post(f"/captures/{cid}/extract?force=true", json={"transcript": TX}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_project_contacts_help_the_extractor(client, project):
    cap = client.post("/captures", data={"project_id": project["id"]}, files={"photo": PHOTO}).json()
    r = client.post(f"/captures/{cap['id']}/extract", json={"transcript": "Jon said move the switch near the door"}).json()
    assert r["fields"]["requester"]["value"] == "John"


def test_schema_and_health_describe_what_is_live(client):
    h = client.get("/health").json()
    assert h["stt"]["provider"] == "mock" and h["extraction"]["provider"] == "rules"
    s = client.get("/schema").json()
    assert [f["name"] for f in s["fields"]][:3] == ["requester", "change", "location"]
    assert s["statuses"] == ["captured", "needs_review", "reviewed", "confirmed"]
    assert "sk-" not in json.dumps(h)
