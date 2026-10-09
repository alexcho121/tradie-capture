# Capture App

Capture App saves evidence of verbally requested extra work without putting a form in the capture flow.

## Capture flow

```text
Press → Speak → Press → 3-second countdown → Photo → Saved
```

The capture is saved before transcription or extraction begins. Camera, location, transcription, extraction, or
reverse-geocoding failures do not discard a successfully recorded voice note.

Each record can contain:

- the original voice recording
- the original photo, when the camera was available
- device capture time and server receipt time
- one GPS fix with reported accuracy
- a reverse-geocoded readable location when available
- the speech-to-text transcript
- an editable AI Draft
- an optional worker note

The browser stores pending captures in IndexedDB and retries evidence uploads when the connection returns. A client
capture ID makes upload retries idempotent.

## AI pipeline

```text
Audio
  → OpenAI speech-to-text
  → deterministic quality gate
  → OpenAI structured extraction
  → grounding and numeric validation
  → AI Draft
```

The AI Draft contains:

- Requested by
- Work requested
- Work area
- Extra time
- Materials
- Amount

Missing, unclear, or unsupported values remain empty. The extraction prompt forbids guessing, and backend grounding
removes values and numbers that are not supported by the transcript. A lightweight deterministic gate skips
extraction for empty, clearly too-short, or filler-only transcripts without making another AI call.

Original audio, photo, timestamps, and GPS remain unchanged. The transcript and AI Draft are stored as derived content
separate from that evidence. Editing the draft uses the existing audited field-edit history and never alters the
original evidence.

Record Detail shows the original evidence, transcript, AI Draft, readable location, and optional Note. Delete is a
secondary overflow action with permanent-delete confirmation.

## Technology

- Python 3.11+
- FastAPI
- SQLite
- browser MediaRecorder, camera, Geolocation, IndexedDB, and service worker APIs
- OpenAI speech-to-text and structured extraction

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `.env` with provider and model values appropriate for the deployment:

```dotenv
STT_PROVIDER=openai
STT_MODEL=<speech-to-text model>

EXTRACT_PROVIDER=openai
EXTRACT_MODEL=<structured extraction model>

OPENAI_API_KEY=
```

Put the real API key only in the local `.env`; `.env` is ignored by Git. Startup fails clearly when an OpenAI provider
is selected without its required model or API key. Mock speech-to-text and rules extraction remain available for tests
and local development.

Start the application from this directory:

```bash
uvicorn app.main:app --env-file .env --reload
```

- Capture app: `http://localhost:8000/` or `/app/`
- API documentation: `/docs`
- Raw development harness: `/dev/`

Microphone and camera access require HTTPS or localhost. Test the complete capture flow on the intended phones and
browsers because recording formats and permission behavior vary by platform.

## Configuration

| Variable | Meaning |
|---|---|
| `STT_PROVIDER` | `openai` for production or `mock` for development |
| `STT_MODEL` | OpenAI speech-to-text model ID supplied through the environment |
| `EXTRACT_PROVIDER` | `openai` for production or `rules` for deterministic fallback/development |
| `EXTRACT_MODEL` | OpenAI structured extraction model ID supplied through the environment |
| `OPENAI_API_KEY` | OpenAI credential; keep it only in local/deployment secrets |
| `STT_LANGUAGE` | Speech language hint; defaults to `en` |
| `DATA_DIR` | SQLite database and media directory; defaults to `./data` |
| `MAX_UPLOAD_MB` | Maximum size per uploaded file |
| `RATE_LIMIT_PER_HOUR` | Per-IP limit for paid calls; `0` disables it |
| `CORS_ORIGINS` | Comma-separated allowed frontend origins |
| `GEOCODER_ENABLED` | Enables best-effort readable location enrichment |
| `GEOCODER_URL` | Nominatim-compatible reverse-geocoding endpoint |
| `GEOCODER_USER_AGENT` | Deployment-specific identifier for the geocoder |
| `GEOCODER_TIMEOUT_SECONDS` | Reverse-geocoding timeout |

`GET /health` reports the active speech-to-text and extraction providers without exposing credentials.

## Evidence and integrity

Every capture has an append-only, hash-linked event history. Original media hashes are recorded when evidence is
saved. The verification endpoint replays the history, checks materialized record state, and re-hashes stored files.

This makes later changes detectable; it does not make a server controlled by an attacker tamper-proof. Device capture
time and GPS are device-reported. Server receipt time remains available for audit.

Reverse geocoding happens only after the capture is stored. It adds a readable label while retaining the original
latitude, longitude, accuracy, and capture-time fix.

## Main API calls

| Call | Purpose |
|---|---|
| `POST /captures` | Save original evidence and offline metadata |
| `POST /captures/{id}/location/resolve` | Best-effort readable location enrichment |
| `POST /captures/{id}/extract` | Transcribe, quality-check, extract, and ground an AI Draft |
| `PATCH /captures/{id}/fields` | Save audited AI Draft edits |
| `PUT /captures/{id}/note` | Save or clear the optional Note |
| `GET /captures` / `GET /captures/{id}` | List or read records |
| `GET /captures/{id}/verify` | Verify event and file integrity |
| `DELETE /captures/{id}` | Permanently remove a record and its stored evidence |

The full frontend contract is in `docs/contract-for-B.md`; machine-readable API documentation is available from
`/docs` and `docs/openapi.json`.

## Tests

The suite uses temporary databases and local fake provider servers; it does not require real credentials or external
network calls.

```bash
python -m pytest
python -m compileall -q app tests
```

Evaluation helpers for extraction sentences and recorded speech live under `eval/`.

## Repository layout

```text
app/         FastAPI endpoints, storage, extraction, grounding, integrity, and configuration
static/app/  Capture browser application and PWA assets
static/dev/  Raw development harness
tests/       API, provider, extraction, integrity, geocoding, and numeric tests
eval/        Synthetic extraction cases and speech evaluation helpers
docs/        Frontend contract and OpenAPI snapshot
scripts/     OpenAPI export helper
```

## Current limitations

- There is no account or authentication layer; use synthetic/demo data until deployment access controls exist.
- Reverse geocoding uses a Nominatim-compatible service and should be configured appropriately for deployment volume.
- A record captures evidence and derived notes; it does not determine contractual entitlement or provide legal advice.
