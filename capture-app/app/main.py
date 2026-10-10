"""FastAPI app: capture -> structure -> review -> confirm -> deadline, with a tamper-evident event log.

Run:  uvicorn app.main:app --env-file .env --reload        (docs at /docs, dev harness at /dev/)
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import ledger
from .config import Settings, load_settings, validate_settings
from .db import Database, dumps, loads
from .extract import blank_fields, compute_flags, extract_fields
from .geocode import GeocodeError, ReverseGeocoder
from .report import SYDNEY, render_record_html, to_sydney
from .schemas import (
    FIELD_LABELS, FIELD_NAMES, REQUIRED_FIELDS, STATUSES,
    CaptureOut, ConfirmationRequestIn, ConfirmationRequestOut, DeadlineIn, ExtractIn, FieldsPatch,
    ProjectIn, ProjectOut, RecordNoteIn,
)
from .storage import media_url, pick_ext, save_upload
from .stt import SttError, transcribe

BASE_DIR = Path(__file__).resolve().parent.parent
REVIEW_BELOW = 0.8  # fields with confidence under this (or empty) should show "Needs review"
OWNER_COOKIE = "tradie_capture_owner"
OWNER_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,80}$")


class RateLimiter:
    """Tiny in-memory sliding-window limiter so a public demo can't burn the paid STT/LLM quota.
    Used per client address and, with the single key "*", as a server-wide ceiling. The per-address key comes from
    X-Forwarded-For, which a client can fake; the server-wide ceiling is what bounds cost. Resets on restart."""

    def __init__(self, per_hour: int, message: str = "Too many requests from this address. Try again later."):
        self.per_hour = per_hour
        self.message = message
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        if self.per_hour <= 0:
            return
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= self.per_hour:
                raise HTTPException(status_code=429, detail=self.message)
            q.append(now)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "unknown")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def hash_owner_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_CLIENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def parse_location(lat: Optional[str], lon: Optional[str], accuracy: Optional[str],
                   located_at: Optional[str]) -> Optional[Dict[str, Any]]:
    """One device-reported fix taken at capture time. Empty strings mean 'no fix' (permission denied, timeout)."""
    lat, lon = (lat or "").strip(), (lon or "").strip()
    if not lat and not lon:
        return None
    if not lat or not lon:
        raise HTTPException(status_code=422, detail="Send both latitude and longitude, or neither")
    try:
        la, lo = float(lat), float(lon)
    except ValueError:
        raise HTTPException(status_code=422, detail="latitude and longitude must be numbers")
    if not (math.isfinite(la) and math.isfinite(lo) and -90 <= la <= 90 and -180 <= lo <= 180):
        raise HTTPException(status_code=422, detail="latitude/longitude out of range")
    loc: Dict[str, Any] = {"latitude": round(la, 6), "longitude": round(lo, 6), "source": "device"}
    if accuracy and accuracy.strip():
        try:
            acc = float(accuracy)
        except ValueError:
            raise HTTPException(status_code=422, detail="accuracy_m must be a number")
        if not math.isfinite(acc) or acc < 0:
            raise HTTPException(status_code=422, detail="accuracy_m must be 0 or more")
        loc["accuracy_m"] = round(acc, 1)
    if located_at and located_at.strip():
        loc["located_at"] = located_at.strip()[:40]
    return loc


def clean_sha256(value: Optional[str], name: str) -> Optional[str]:
    if not value or not value.strip():
        return None
    v = value.strip().lower()
    if not _SHA256.match(v):
        raise HTTPException(status_code=422, detail=f"{name} must be a hex SHA-256")
    return v


def real_upload(upload: Optional[UploadFile]) -> Optional[UploadFile]:
    """A browser form with an empty file input still sends a part with no filename and no bytes: treat as absent."""
    if upload is None or (not upload.filename and not upload.size):
        return None
    return upload


def parse_client_meta(raw: Optional[str], captured_at: Optional[str]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if raw:
        if len(raw) > 4000:
            raise HTTPException(status_code=400, detail="client_meta is too large")
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="client_meta must be a JSON object")
        if isinstance(parsed, dict):
            for k, v in list(parsed.items())[:30]:
                if v is None or isinstance(v, (str, int, float, bool)):
                    meta[str(k)[:40]] = v[:200] if isinstance(v, str) else v
    if captured_at:
        meta["client_captured_at"] = captured_at[:40]
    return meta


def serialize_capture(row: Any, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    stored = loads(row["fields"], {})
    fields = blank_fields()
    for name in FIELD_NAMES:
        if name in stored:
            for key in ("value", "confidence", "source", "edited"):
                if key in stored[name]:
                    fields[name][key] = stored[name][key]
    meta = loads(row["extract_meta"], {})
    conf = ledger.confirmation_state(events, stored)
    proof_path = conf.pop("proof_path")

    if not any(e["type"] == "extracted" for e in events):
        flags = ["not_structured_yet"]
    else:
        flags = compute_flags(fields, row["transcript"] or "")
        flags += [f for f in meta.get("flags", [])
                  if f.startswith("weak_grounding:") and not fields[f.split(":", 1)[1]]["edited"]]
    extraction = {k: meta[k] for k in ("provider", "model", "fallback", "error", "removed", "quality_gate", "transcript_source", "stt", "latency_ms")
                  if k in meta}
    client_meta = loads(row["client_meta"], {})
    return {
        "id": row["id"],
        "client_capture_id": row["client_id"],
        "project_id": row["project_id"],
        "created_at": row["created_at"],
        "device_captured_at": client_meta.get("client_captured_at"),
        "location": loads(row["location"], None),
        "audio_url": media_url(row["audio_path"]),
        "photo_url": media_url(row["photo_path"]),
        "transcript": row["transcript"],
        "fields": fields,
        "status": row["status"],
        "edits": ledger.edit_history(events),
        "confirmation": {**conf, "proof_url": media_url(proof_path)},
        "note": row["user_note"],
        "notice_deadline": row["notice_deadline"],
        "deadline_note": row["deadline_note"],
        "flags": flags,
        "extraction": extraction,
        "integrity": {"events": len(events), "head_hash": events[-1]["hash"] if events else None},
        "client_meta": client_meta,
    }


def project_out(row: Any) -> Dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "site_contacts": loads(row["site_contacts"], []),
            "created_at": row["created_at"]}


def compose_confirmation_message(to_name: Optional[str], row: Any, fields: Dict[str, Any], ref: str) -> str:
    when = to_sydney(row["created_at"]).split(",")[0].rsplit(" ", 1)[0]  # "Fri 9 Oct"
    change = str(fields["change"]["value"]).rstrip(" .")
    text = f"Hi{' ' + to_name if to_name else ''}, confirming the extra work you asked for on {when}: {change}."
    loc = (fields.get("location") or {}).get("value")
    if loc:
        text += f" Where: {loc}."
    hrs = (fields.get("extra_time_hours") or {}).get("value")
    if hrs is not None:
        text += f" Est. time: {float(hrs):g} hrs."
    return text + f" Reply YES if that's right. (Ref {ref})"


def parse_deadline_date(value: str) -> Optional[date]:
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SYDNEY)
        return dt.astimezone(SYDNEY).date()
    except ValueError:
        return None


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = validate_settings(settings) if settings is not None else load_settings()
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    geocoder = ReverseGeocoder(settings.geocoder_url, settings.geocoder_user_agent,
                               settings.geocoder_timeout_seconds)
    max_bytes = settings.max_upload_mb * 1024 * 1024
    paid_limit = RateLimiter(settings.rate_limit_per_hour)  # STT + LLM calls
    upload_limit = RateLimiter(settings.rate_limit_per_hour * 2 if settings.rate_limit_per_hour > 0 else 0)
    global_paid_limit = RateLimiter(settings.global_ai_calls_per_hour,
                                    "The demo has reached its hourly limit for AI drafts. "
                                    "Your capture is saved; try again later.")
    global_upload_limit = RateLimiter(settings.global_uploads_per_hour,
                                      "The demo server is busy. Your capture stays on this phone and uploads later.")

    def check_paid(request: Request) -> None:
        paid_limit.check(client_ip(request))
        global_paid_limit.check("*")

    def check_upload(request: Request) -> None:
        upload_limit.check(client_ip(request))
        global_upload_limit.check("*")

    app = FastAPI(
        title="Capture now. Claim later. - API",
        version="0.1.0",
        description=("Backend for the one-button variation capture app. Evidence first (audio + photo, hashed), "
                     "AI structuring second (grounded against the transcript), human review third. "
                     "Not legal advice."),
    )
    app.state.settings = settings
    app.state.db = db
    app.state.geocoder = geocoder

    @app.middleware("http")
    async def guard(request: Request, call_next):
        token = request.cookies.get(OWNER_COOKIE, "")
        new_token = not OWNER_TOKEN_RE.fullmatch(token)
        if new_token:
            token = secrets.token_urlsafe(32)
        request.state.owner_hash = hash_owner_token(token)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_bytes * 3 + (1 << 20):
            response = JSONResponse({"detail": "Request too large"}, status_code=413)
        else:
            response = await call_next(request)
        if new_token:
            forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
            secure = request.url.scheme == "https" or forwarded_proto == "https"
            response.set_cookie(OWNER_COOKIE, token, max_age=31536000, httponly=True,
                                secure=secure, samesite="lax", path="/")
        protected = request.url.path.startswith(("/captures", "/media", "/projects", "/deadlines"))
        if new_token or protected:
            response.headers["Cache-Control"] = "private, no-store"
            vary = {part.strip() for part in response.headers.get("Vary", "").split(",") if part.strip()}
            response.headers["Vary"] = ", ".join(sorted(vary | {"Cookie"}))
        path = request.url.path
        if (path == "/" or path.startswith(("/app", "/site", "/dev"))) and "cache-control" not in response.headers:
            # Website and app files: always ask the server whether there is a newer version (a cheap 304 when not),
            # so an update shows on the next visit instead of an old copy the browser kept for hours.
            response.headers["Cache-Control"] = "no-cache"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_methods=["*"], allow_headers=["*"])

    # ------------------------------------------------------------ helpers
    def owner_hash(request: Request) -> str:
        return request.state.owner_hash

    def get_row(conn, capture_id: str, owner: str):
        row = conn.execute("SELECT * FROM captures WHERE id=? AND owner_hash=?", (capture_id, owner)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Capture not found")
        return row

    def present(conn, capture_id: str, owner: str) -> Dict[str, Any]:
        return serialize_capture(get_row(conn, capture_id, owner), ledger.load_events(conn, capture_id))

    def ensure_project(conn, project_id: Optional[str], owner: str):
        if project_id:
            row = conn.execute("SELECT * FROM projects WHERE id=? AND owner_hash=?", (project_id, owner)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Project not found")
            return row
        row = conn.execute("SELECT * FROM projects WHERE owner_hash=? ORDER BY created_at DESC LIMIT 1",
                           (owner,)).fetchone()
        if row is not None:
            return row
        pid = new_id()
        conn.execute("INSERT INTO projects (id, owner_hash, name, site_contacts, created_at) VALUES (?,?,?,?,?)",
                     (pid, owner, "My site", "[]", ledger.now_iso()))
        return conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()

    # ------------------------------------------------------------ meta
    site_page = BASE_DIR / "static" / "site" / "index.html"

    @app.get("/", include_in_schema=False)
    def root():
        # The public website (landing page) lives at "/"; the capture app itself is at /app/.
        if site_page.is_file():
            return FileResponse(site_page, media_type="text/html; charset=utf-8")
        return RedirectResponse(url="/app/")

    @app.get("/health")
    def health():
        stt_live = settings.stt_provider == "openai" and bool(settings.openai_api_key)
        extract_live = ((settings.extract_provider == "openai" and bool(settings.openai_api_key)) or
                        (settings.extract_provider == "anthropic" and bool(settings.anthropic_api_key)))
        return {
            "ok": True,
            "stt": {"provider": "openai" if stt_live else "mock", "model": settings.stt_model if stt_live else None},
            "extraction": {"provider": settings.extract_provider if extract_live else "rules",
                           "model": settings.extract_model if extract_live else None},
            # ephemeral = the host wipes stored records on restart/redeploy (e.g. Render free); the app tells users
            "storage": {"ephemeral": settings.ephemeral_storage},
            "time": ledger.now_iso(),
        }

    @app.get("/schema")
    def schema():
        return {
            "fields": [{"name": n, "label": FIELD_LABELS[n], "required": n in REQUIRED_FIELDS,
                        "type": "number" if n == "extra_time_hours" else "text"} for n in FIELD_NAMES],
            "statuses": list(STATUSES),
            "event_types": list(ledger.EVENT_TYPES),
            "review_below": REVIEW_BELOW,
        }

    # ------------------------------------------------------------ projects
    @app.post("/projects", response_model=ProjectOut, status_code=201)
    def create_project(request: Request, body: ProjectIn):
        pid = new_id()
        with db.tx() as conn:
            conn.execute("INSERT INTO projects (id, owner_hash, name, site_contacts, created_at) VALUES (?,?,?,?,?)",
                         (pid, owner_hash(request), body.name, dumps(body.site_contacts), ledger.now_iso()))
            return project_out(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())

    @app.get("/projects", response_model=List[ProjectOut])
    def list_projects(request: Request):
        with db.session() as conn:
            return [project_out(r) for r in conn.execute(
                "SELECT * FROM projects WHERE owner_hash=? ORDER BY created_at DESC", (owner_hash(request),))]

    @app.put("/projects/{project_id}", response_model=ProjectOut)
    def update_project(project_id: str, request: Request, body: ProjectIn):
        with db.tx() as conn:
            owner = owner_hash(request)
            ensure_project(conn, project_id, owner)
            conn.execute("UPDATE projects SET name=?, site_contacts=? WHERE id=? AND owner_hash=?",
                         (body.name, dumps(body.site_contacts), project_id, owner))
            return project_out(conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())

    # ------------------------------------------------------------ capture (the one-button part)
    def capture_by_client_id(client_id: str, owner: str) -> Optional[Dict[str, Any]]:
        with db.session() as conn:
            row = conn.execute("SELECT id FROM captures WHERE client_id=? AND owner_hash=?",
                               (client_id, owner)).fetchone()
            return present(conn, row["id"], owner) if row else None

    @app.post("/captures", response_model=CaptureOut, status_code=201)
    def create_capture(
        request: Request,
        response: Response,
        project_id: Optional[str] = Form(None),
        audio: Optional[UploadFile] = File(None),
        photo: Optional[UploadFile] = File(None),
        client_meta: Optional[str] = Form(None),
        client_captured_at: Optional[str] = Form(None),
        client_capture_id: Optional[str] = Form(None, description="id generated on the phone; a retry with the "
                                                               "same id returns the existing capture (200)"),
        latitude: Optional[str] = Form(None),
        longitude: Optional[str] = Form(None),
        accuracy_m: Optional[str] = Form(None),
        located_at: Optional[str] = Form(None),
        client_audio_sha256: Optional[str] = Form(None, description="SHA-256 computed on the phone at capture"),
        client_photo_sha256: Optional[str] = Form(None, description="SHA-256 computed on the phone at capture"),
    ):
        """Save the evidence. No AI, no questions: this must succeed even if every external service is down.

        Offline-first: the app may store a capture locally and upload it later, possibly more than once.
        Send the same `client_capture_id` every time: the first upload wins, retries get it back with 200."""
        owner = owner_hash(request)
        client_id = (client_capture_id or "").strip() or None
        if client_id is not None:
            if not _CLIENT_ID.match(client_id):
                raise HTTPException(status_code=422, detail="client_capture_id: 1-64 letters, digits, _ . : -")
            existing = capture_by_client_id(client_id, owner)
            if existing is not None:
                response.status_code = 200
                return existing
        audio, photo = real_upload(audio), real_upload(photo)
        if audio is None and photo is None:
            raise HTTPException(status_code=400, detail="Send at least an audio or a photo")
        meta = parse_client_meta(client_meta, client_captured_at)
        location = parse_location(latitude, longitude, accuracy_m, located_at)
        want_audio = clean_sha256(client_audio_sha256, "client_audio_sha256")
        want_photo = clean_sha256(client_photo_sha256, "client_photo_sha256")
        check_upload(request)  # after validation, so junk requests can't use up the shared hourly ceiling
        capture_id = new_id()
        cdir = settings.media_dir / capture_id
        try:
            a_rel = a_sha = p_rel = p_sha = None
            if audio is not None:
                name = "audio" + pick_ext("audio", audio.content_type, audio.filename)
                _, a_sha = save_upload(audio, cdir / name, max_bytes)
                a_rel = f"{capture_id}/{name}"
            if photo is not None:
                name = "photo" + pick_ext("photo", photo.content_type, photo.filename)
                _, p_sha = save_upload(photo, cdir / name, max_bytes)
                p_rel = f"{capture_id}/{name}"
            for label, want, got in (("audio", want_audio, a_sha), ("photo", want_photo, p_sha)):
                if want is not None and want != got:
                    raise HTTPException(status_code=422, detail=f"The {label} received does not match the hash "
                                                                f"computed on the phone. Upload it again.")
            try:
                with db.tx() as conn:
                    project = ensure_project(conn, project_id, owner)
                    created_at = ledger.now_iso()
                    conn.execute(
                        "INSERT INTO captures (id, owner_hash, project_id, created_at, audio_path, photo_path, client_id) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (capture_id, owner, project["id"], created_at, a_rel, p_rel, client_id))
                    ledger.record(conn, capture_id, "created", {
                        "project_id": project["id"], "audio_sha256": a_sha, "photo_sha256": p_sha,
                        "client_meta": meta, "location": location, "client_capture_id": client_id,
                    }, at=created_at)
                    return present(conn, capture_id, owner)
            except sqlite3.IntegrityError:
                # two uploads of the same offline capture raced: keep the first, drop this copy
                existing = capture_by_client_id(client_id, owner) if client_id else None
                if existing is None:
                    raise
                shutil.rmtree(cdir, ignore_errors=True)
                response.status_code = 200
                return existing
        except BaseException:
            shutil.rmtree(cdir, ignore_errors=True)
            raise

    @app.post("/captures/{capture_id}/photo", response_model=CaptureOut)
    def add_photo(capture_id: str, request: Request, photo: UploadFile = File(...)):
        owner = owner_hash(request)
        with db.session() as conn:
            if get_row(conn, capture_id, owner)["photo_path"]:
                raise HTTPException(status_code=409, detail="This capture already has a photo. Start a new capture instead.")
        check_upload(request)
        name = f"photo-{new_id()[:6]}" + pick_ext("photo", photo.content_type, photo.filename)
        dest = settings.media_dir / capture_id / name
        _, sha = save_upload(photo, dest, max_bytes)
        try:
            with db.tx() as conn:
                if get_row(conn, capture_id, owner)["photo_path"]:
                    raise HTTPException(status_code=409, detail="This capture already has a photo.")
                conn.execute("UPDATE captures SET photo_path=? WHERE id=?", (f"{capture_id}/{name}", capture_id))
                ledger.record(conn, capture_id, "photo_added", {"photo_sha256": sha})
                return present(conn, capture_id, owner)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------ AI structuring
    @app.post("/captures/{capture_id}/extract", response_model=CaptureOut)
    def extract(capture_id: str, request: Request, body: Optional[ExtractIn] = None,
                force: bool = Query(False, description="Overwrite the worker's edits")):
        """Structure the stored transcript, or transcribe the stored audio first. A transcript may be supplied
        only when the explicit development override is enabled."""
        owner = owner_hash(request)
        with db.session() as conn:
            row = get_row(conn, capture_id, owner)
            project = conn.execute("SELECT * FROM projects WHERE id=?", (row["project_id"],)).fetchone()
        contacts = loads(project["site_contacts"], []) if project is not None else []
        if not force and any(f.get("edited") for f in loads(row["fields"], {}).values()):
            raise HTTPException(status_code=409, detail="You have edited this record; re-running the AI would overwrite "
                                                        "your edits. Pass ?force=true to do that anyway.")
        supplied_transcript = (body.transcript or "").strip() if body else ""
        if supplied_transcript and not settings.allow_transcript_override:
            raise HTTPException(status_code=403, detail="Transcript override is disabled")
        transcript = supplied_transcript or (row["transcript"] or "").strip()
        source, stt_meta = ("provided" if supplied_transcript else "stored"), None
        if not transcript and not row["audio_path"]:
            raise HTTPException(status_code=422, detail="This capture has no stored audio or transcript")
        check_paid(request)  # after the cheap checks, so failed requests don't use up the shared hourly ceiling
        if not transcript:
            try:
                transcript, stt_meta = transcribe(settings.media_dir / row["audio_path"], settings, contacts)
            except SttError as e:
                raise HTTPException(status_code=503, detail={"code": "stt_unavailable", "message": str(e)})
            source = "stt"
        fields, meta = extract_fields(transcript, contacts, settings)
        meta["transcript_source"] = source
        if stt_meta:
            meta["stt"] = stt_meta
        with db.tx() as conn:
            ledger.record(conn, capture_id, "extracted", {"transcript": transcript, "fields": fields, **meta})
            return present(conn, capture_id, owner)

    # ------------------------------------------------------------ read
    @app.get("/captures", response_model=List[CaptureOut])
    def list_captures(request: Request, project_id: Optional[str] = None, status: Optional[str] = None,
                      limit: int = Query(100, ge=1, le=200)):
        sql, args = "SELECT * FROM captures WHERE owner_hash=?", [owner_hash(request)]
        if project_id:
            sql += " AND project_id=?"
            args.append(project_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += (" ORDER BY COALESCE(julianday(json_extract(client_meta, '$.client_captured_at')), "
                "julianday(created_at)) DESC LIMIT ?")
        args.append(limit)
        with db.session() as conn:
            return [serialize_capture(r, ledger.load_events(conn, r["id"])) for r in conn.execute(sql, args).fetchall()]

    @app.get("/captures/{capture_id}", response_model=CaptureOut)
    def get_capture(capture_id: str, request: Request):
        with db.session() as conn:
            return present(conn, capture_id, owner_hash(request))

    @app.post("/captures/{capture_id}/location/resolve", response_model=CaptureOut)
    def resolve_capture_location(capture_id: str, request: Request):
        """Best-effort readable location enrichment after evidence has already been saved."""
        owner = owner_hash(request)
        with db.session() as conn:
            capture = present(conn, capture_id, owner)
        location = capture.get("location")
        if not settings.geocoder_enabled or not location or location.get("display_name"):
            return capture
        try:
            readable = app.state.geocoder.reverse(location["latitude"], location["longitude"])
        except GeocodeError:
            return capture
        if readable is None:
            return capture
        with db.tx() as conn:
            current = loads(get_row(conn, capture_id, owner)["location"], None)
            if current and not current.get("display_name"):
                ledger.record(conn, capture_id, "location_enriched", {
                    "display_name": readable.display_name,
                    "provider": readable.provider,
                    "attribution": readable.attribution,
                })
            return present(conn, capture_id, owner)

    # ------------------------------------------------------------ review (edits are logged, never silent)
    @app.patch("/captures/{capture_id}/fields", response_model=CaptureOut)
    def edit_fields(capture_id: str, request: Request, body: FieldsPatch):
        owner = owner_hash(request)
        with db.tx() as conn:
            current = loads(get_row(conn, capture_id, owner)["fields"], {})
            for name, new in body.edits.items():
                old = (current.get(name) or {}).get("value")
                if old != new:
                    ledger.record(conn, capture_id, "field_edited", {"field": name, "old": old, "new": new})
            return present(conn, capture_id, owner)

    @app.post("/captures/{capture_id}/review", response_model=CaptureOut)
    def mark_reviewed(capture_id: str, request: Request):
        owner = owner_hash(request)
        with db.tx() as conn:
            row = get_row(conn, capture_id, owner)
            if row["status"] in ("reviewed", "confirmed"):
                return present(conn, capture_id, owner)
            if row["status"] == "captured" and not any(
                    f.get("value") is not None for f in loads(row["fields"], {}).values()):
                raise HTTPException(status_code=409, detail="Nothing to review yet: structure the capture "
                                                            "(POST /extract) or fill the fields in first.")
            ledger.record(conn, capture_id, "reviewed", {})
            return present(conn, capture_id, owner)

    @app.put("/captures/{capture_id}/note", response_model=CaptureOut)
    def set_record_note(capture_id: str, request: Request, body: RecordNoteIn):
        """Save an optional worker note without changing the original evidence or extracted fields."""
        owner = owner_hash(request)
        with db.tx() as conn:
            row = get_row(conn, capture_id, owner)
            if row["user_note"] != body.note:
                ledger.record(conn, capture_id, "note_set", {"note": body.note})
            return present(conn, capture_id, owner)

    # ------------------------------------------------------------ confirmation (evidence strengthening)
    @app.post("/captures/{capture_id}/confirmation/request", response_model=ConfirmationRequestOut)
    def confirmation_request(capture_id: str, request: Request, body: ConfirmationRequestIn):
        owner = owner_hash(request)
        with db.tx() as conn:
            row = get_row(conn, capture_id, owner)
            stored = loads(row["fields"], {})
            fields = blank_fields()
            for name, f in stored.items():
                if name in fields:
                    fields[name].update(f)
            if not fields["change"]["value"]:
                raise HTTPException(status_code=409, detail="Fill in what the extra work is before asking for confirmation")
            ref = ledger.load_events(conn, capture_id)[-1]["hash"][:8]
            requester = fields["requester"]["value"]
            to_name = body.to_name or (requester if requester and not str(requester).lower().startswith("the ") else None)
            message = compose_confirmation_message(to_name, row, fields, ref)
            sms_url = f"sms:{body.to_phone or ''}?&body={quote(message, safe='')}"
            ledger.record(conn, capture_id, "confirmation_requested", {
                "to_name": to_name, "to_phone": body.to_phone, "message": message, "channel": "sms_link",
                "facts_digest": ledger.facts_digest(stored), "ref": ref,
            })
            return {"message": message, "sms_url": sms_url, "ref": ref,
                    "capture": present(conn, capture_id, owner)}

    @app.post("/captures/{capture_id}/confirmation/confirm", response_model=CaptureOut)
    def confirmation_confirm(
        capture_id: str, request: Request,
        proof: Optional[UploadFile] = File(None),
        confirmed_by: Optional[str] = Form(None),
        note: Optional[str] = Form(None),
    ):
        """The worker attaches the requester's reply (screenshot) and/or a note. We store and hash it;
        we do NOT verify that it is genuine, and the record says so."""
        owner = owner_hash(request)
        proof = real_upload(proof)
        note = " ".join((note or "").split())[:500] or None
        confirmed_by = " ".join((confirmed_by or "").split())[:80] or None
        if proof is None and not note:
            raise HTTPException(status_code=400, detail="Attach a screenshot of the reply or write what they said")
        with db.session() as conn:
            get_row(conn, capture_id, owner)
        check_upload(request)
        dest, rel, sha = None, None, None
        if proof is not None:
            name = f"proof-{new_id()[:8]}" + pick_ext("proof", proof.content_type, proof.filename)
            dest = settings.media_dir / capture_id / name
            _, sha = save_upload(proof, dest, max_bytes)
            rel = f"{capture_id}/{name}"
        try:
            with db.tx() as conn:
                stored = loads(get_row(conn, capture_id, owner)["fields"], {})
                ledger.record(conn, capture_id, "confirmed", {
                    "proof_path": rel, "proof_sha256": sha, "confirmed_by": confirmed_by, "note": note,
                    "facts_digest": ledger.facts_digest(stored), "verified_by_system": False,
                })
                return present(conn, capture_id, owner)
        except BaseException:
            if dest is not None:
                dest.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------ contract notice deadline (typed by the user)
    @app.put("/captures/{capture_id}/deadline", response_model=CaptureOut)
    def set_deadline(capture_id: str, request: Request, body: DeadlineIn):
        owner = owner_hash(request)
        with db.tx() as conn:
            row = get_row(conn, capture_id, owner)
            if row["notice_deadline"] != body.notice_deadline or row["deadline_note"] != body.note:
                ledger.record(conn, capture_id, "deadline_set",
                              {"notice_deadline": body.notice_deadline, "note": body.note})
            return present(conn, capture_id, owner)

    @app.get("/deadlines")
    def deadlines(request: Request, within_days: int = Query(30, ge=0, le=3650)):
        today = datetime.now(SYDNEY).date()
        items: List[Dict[str, Any]] = []
        with db.session() as conn:
            for r in conn.execute("SELECT * FROM captures WHERE owner_hash=? AND notice_deadline IS NOT NULL",
                                  (owner_hash(request),)).fetchall():
                d = parse_deadline_date(r["notice_deadline"])
                if d is None:
                    continue
                days_left = (d - today).days
                if days_left > within_days:
                    continue
                f = loads(r["fields"], {})
                items.append({
                    "capture_id": r["id"], "project_id": r["project_id"], "notice_deadline": r["notice_deadline"],
                    "note": r["deadline_note"], "days_left": days_left, "overdue": days_left < 0,
                    "status": r["status"],
                    "change": (f.get("change") or {}).get("value"), "location": (f.get("location") or {}).get("value"),
                })
        items.sort(key=lambda x: x["days_left"])
        return {"today": today.isoformat(), "timezone": "Australia/Sydney", "items": items,
                "note": "These are the dates the worker typed in from their own contract. "
                        "This tool does not calculate or check notice periods."}

    # ------------------------------------------------------------ integrity + printable record
    @app.get("/captures/{capture_id}/verify")
    def verify(capture_id: str, request: Request):
        with db.session() as conn:
            get_row(conn, capture_id, owner_hash(request))
            result = ledger.verify_capture(conn, settings, capture_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Capture not found")
        return result

    @app.get("/captures/{capture_id}/record.html", response_class=HTMLResponse)
    def record_html(capture_id: str, request: Request):
        with db.session() as conn:
            row = get_row(conn, capture_id, owner_hash(request))
            events = ledger.load_events(conn, capture_id)
            integrity = ledger.verify_capture(conn, settings, capture_id)
            project = conn.execute("SELECT * FROM projects WHERE id=?", (row["project_id"],)).fetchone()
        cap = serialize_capture(row, events)
        return HTMLResponse(render_record_html(cap, project_out(project) if project else {}, events, integrity or {}))

    @app.delete("/captures/{capture_id}", status_code=204)
    def delete_capture(capture_id: str, request: Request):
        with db.tx() as conn:
            get_row(conn, capture_id, owner_hash(request))
            conn.execute("DELETE FROM events WHERE capture_id=?", (capture_id,))
            conn.execute("DELETE FROM captures WHERE id=?", (capture_id,))
        shutil.rmtree(settings.media_dir / capture_id, ignore_errors=True)
        return Response(status_code=204)

    # ------------------------------------------------------------ protected media + static UI
    @app.get("/media/{capture_id}/{filename:path}", include_in_schema=False)
    def protected_media(capture_id: str, filename: str, request: Request):
        owner = owner_hash(request)
        rel_path = f"{capture_id}/{filename}"
        with db.session() as conn:
            row = get_row(conn, capture_id, owner)
            allowed = {p for p in (row["audio_path"], row["photo_path"]) if p}
            confirmation = ledger.confirmation_state(ledger.load_events(conn, capture_id), loads(row["fields"], {}))
            if confirmation.get("proof_path"):
                allowed.add(confirmation["proof_path"])
        if rel_path not in allowed:
            raise HTTPException(status_code=404, detail="Media not found")
        path = settings.media_dir / rel_path
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Media not found")
        return FileResponse(path)

    # /app = the capture UI, /dev = test harness, /site = landing-page assets (the page itself is served at "/")
    for mount, folder in (("/app", "app"), ("/dev", "dev"), ("/site", "site")):
        path = BASE_DIR / "static" / folder
        if path.is_dir():
            app.mount(mount, StaticFiles(directory=str(path), html=True), name=folder)
    return app


_app: Optional[FastAPI] = None


def __getattr__(name: str):  # PEP 562: `uvicorn app.main:app` builds the app lazily, imports stay side-effect free
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
