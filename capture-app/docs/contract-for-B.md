# Frontend contract

Live API documentation is available at `/docs`. The machine-readable snapshot is `docs/openapi.json`.

## Capture flow

The browser capture flow is fixed:

```text
PRESS → speak → PRESS → 3-second countdown → photo → Saved
```

The ready screen shows a short speaking guide before recording, as two fixed lines so a phrase is never split:

```text
Who asked · What changed
Where exactly · Extra time/materials
```

The guide is informational and does not create capture form fields. Recording stops by itself at 60 seconds; the
recording screen says so and counts down the last 10 seconds.

The fast path is:

```text
start MediaRecorder and request one GPS fix
stop MediaRecorder
count down for three seconds and capture one camera frame when available
save audio, optional photo, device time, and optional GPS in IndexedDB
show Saved
upload with POST /captures when online
request readable location enrichment independently
request transcription and extraction independently
```

`POST /captures` performs no AI or reverse-geocoding call. Evidence upload success is independent from transcription,
extraction, and readable-location enrichment.

Camera or GPS failure does not block a valid voice capture. Omit unavailable file or location fields rather than
sending empty file parts. At least audio or photo is required by the API.

Camera and microphone access require HTTPS or localhost. iOS Safari commonly records `audio/mp4`; Chromium browsers
commonly record `audio/webm`. Send the Blob's actual media type.

## Offline queue

Pending evidence is stored in IndexedDB before upload. The queue retries when connectivity returns.

| `POST /captures` field | Meaning |
|---|---|
| `client_capture_id` | Device-generated idempotency key. A retry with the same ID returns the existing capture. |
| `client_captured_at` | Device clock at capture. Returned as `device_captured_at`. |
| `latitude`, `longitude`, `accuracy_m`, `located_at` | One device-reported GPS fix taken when recording starts. |
| `client_audio_sha256`, `client_photo_sha256` | Device-computed hashes checked against uploaded bytes. |
| `audio`, `photo`, `project_id`, `client_meta` | Original evidence, project context, and device-reported timing metadata. |

For the normal user-facing capture time, use `device_captured_at` when present and fall back to `created_at`. The server
receipt time remains in `created_at` for audit and ordering when no device time exists.

## Original evidence and derived content

Keep these categories distinct.

Original evidence and capture context:

- original audio
- original photo, when available
- device capture time
- server receipt time
- raw GPS coordinates, accuracy, and fix time
- reverse-geocoded readable location added after saving

Derived processing content:

- speech-to-text transcript
- Requested by (`fields.requester`)
- Work requested (`fields.change`)
- Work area (`fields.location`)
- Extra time (`fields.extra_time_hours`)
- Materials (`fields.materials`)
- Amount (`fields.amount_mentioned`)

User-authored content:

- optional worker Note, which is separate from extraction fields
- audited edits to AI Draft fields

Editing derived fields must use `PATCH /captures/{id}/fields`. The backend records each changed field in the edit
history. Editing must not replace audio, photo, timestamps, GPS, readable location, or transcript.

## AI processing

The production pipeline is:

```text
audio
  → OpenAI speech-to-text
  → deterministic quality gate
  → OpenAI structured extraction
  → normalization, grounding, and numeric validation
  → AI Draft
```

The deterministic quality gate runs after speech-to-text and before extraction. Empty, clearly too-short, or
filler-only transcripts are skipped without another AI call. A useful partial note can pass; the speaker does not need
to mention every draft field.

The extraction model is instructed to return null when a value is missing, unclear, or not explicitly supported by the
transcript. Backend grounding removes unsupported names, text, durations, and numbers before fields reach the frontend.

AI processing states in Record Detail:

- Processing: show a concise progress state while the request is running.
- Skipped by the quality gate: explain that there was not enough detail for a reliable draft and that original evidence
  remains saved. Do not render six empty rows.
- Failed: show that the AI Draft is unavailable without implying evidence capture failed.
- Success: show all six user-facing fields; use `Not mentioned` for an individual null value.

If a record has audio and processing is missing or failed, opening Record Detail while online may retry the existing
`POST /captures/{id}/extract` call once. The browser guards the retry per record so rendering and location updates do not
create a loop. Reverse-geocoding retry remains independent.

## Current Record Detail UI

Record Detail contains:

- project and capture time
- original voice note
- original photo or an unavailable state
- transcript
- AI Draft with a secondary Edit action (titled "Draft" with a note when `extraction.provider` is `rules`)
- readable location, or the raw coordinates when no label is available, with accuracy and an "Open in map" link
- optional Note
- a secondary overflow menu containing Delete record

A value gets a **Check** tag when it is not empty, not edited, and either its `confidence` is below `review_below`
(`GET /schema`, currently 0.8) or `flags` contains `low_confidence:<field>` / `weak_grounding:<field>`. Note that a
model can already return 0.7 for a hedged time, in which case there is no flag, so the confidence test matters.

AI Draft is read-only by default. Tapping Edit reveals the six editable fields and Save/Cancel actions. Save uses the
audited field-edit endpoint; Cancel makes no changes.

Delete uses `DELETE /captures/{id}` after a destructive confirmation. Successful deletion returns to Records. Failed
deletion keeps the record visible. There is no soft-delete or recovery workflow.

## `CaptureOut`

Every capture response uses the same object shape. Fields always contain all six canonical backend keys.

```jsonc
{
  "id": "735c4051a18a",
  "client_capture_id": "3f1c…",
  "project_id": "…",
  "created_at": "2026-10-09T08:00:00.000Z",
  "device_captured_at": "2026-10-09T03:00:00+11:00",
  "location": {
    "latitude": -33.7769,
    "longitude": 151.0812,
    "accuracy_m": 15,
    "located_at": "…",
    "source": "device",
    "display_name": "Macquarie Park NSW",
    "provider": "nominatim",
    "attribution": "© OpenStreetMap contributors"
  },
  "audio_url": "/media/<id>/audio.webm",
  "photo_url": "/media/<id>/photo.jpg",
  "transcript": "John asked me to move the pipe …",
  "fields": {
    "requester": {"value": "John", "confidence": 0.9, "source": "llm", "edited": false},
    "change": {"value": "move the pipe", "confidence": 0.9, "source": "llm", "edited": false},
    "location": {"value": "level 2 bathroom", "confidence": 0.9, "source": "llm", "edited": false},
    "extra_time_hours": {"value": 2, "confidence": 0.7, "source": "llm", "edited": false},
    "materials": {"value": null, "confidence": 0, "source": null, "edited": false},
    "amount_mentioned": {"value": null, "confidence": 0, "source": null, "edited": false}
  },
  "status": "needs_review",
  "flags": [],
  "edits": [],
  "note": null,
  "extraction": {
    "provider": "openai",
    "model": "<configured model>",
    "fallback": false,
    "error": null,
    "removed": [],
    "quality_gate": {"passed": true, "word_count": 12},
    "transcript_source": "stt"
  },
  "integrity": {"events": 2, "head_hash": "<64 hex>"},
  "client_meta": {"speak_ms": 4100, "to_photo_ms": 9300}
}
```

## `GET /health`

```json
{"ok": true, "stt": {"provider": "openai", "model": "…"}, "extraction": {"provider": "openai", "model": "…"},
 "storage": {"ephemeral": false}, "time": "…"}
```

`storage.ephemeral` is `true` when the host wipes saved files on restart (env `EPHEMERAL_STORAGE=true`, e.g. Render
free). The Records screen then tells the visitor that saved records can be cleared.

## Privacy and limits

- Records, projects and media are private to the browser that made them: the server sets an httpOnly cookie
  (`tradie_capture_owner`) and scopes every query to it. Another browser gets `404` for the same IDs.
- `429` means a limit was reached: per address (`RATE_LIMIT_PER_HOUR`) or server-wide (`GLOBAL_AI_CALLS_PER_HOUR`,
  `GLOBAL_UPLOADS_PER_HOUR`). On upload, keep the capture in the IndexedDB queue and retry later; on extract, show
  the draft as unavailable (evidence is still saved).
- `POST /captures/{id}/extract` with a typed `transcript` returns `403` unless `ALLOW_TRANSCRIPT_OVERRIDE=true`
  (development only).

## Website

`GET /` serves the public landing page from `static/site/index.html`; the app stays at `/app/`. Put the demo video and
repository links in `LINKS` at the bottom of that page; empty links stay hidden.

Possible extraction providers include `openai`, `anthropic` for legacy-compatible deployments, `rules` for fallback or
development, and `none` when the quality gate skips extraction.

## Calls used by the browser app

| Call | Use |
|---|---|
| `POST /projects`, `GET /projects`, `PUT /projects/{id}` | Project selection and optional site-contact spelling context. |
| `POST /captures` | Save original evidence and offline metadata. |
| `GET /captures` | Populate Records. The row title uses extracted change, then project name. |
| `GET /captures/{id}` | Load Record Detail. |
| `POST /captures/{id}/photo` | Attach a missing photo once; an existing photo cannot be replaced. |
| `POST /captures/{id}/location/resolve` | Add a readable location without changing raw GPS. |
| `POST /captures/{id}/extract` | Transcribe (if needed) and structure the stored recording. |
| `PATCH /captures/{id}/fields` | Save audited AI Draft edits. |
| `PUT /captures/{id}/note` | Save or clear the optional Note. |
| `DELETE /captures/{id}` | Permanently delete the record, events, and stored media. |

Other backend review, confirmation, deadline, printable-record, and integrity endpoints remain available for API
compatibility but are not part of the normal capture UI.

## Development without provider credentials

For local tests, construct settings with mock speech-to-text and rules extraction. With
`ALLOW_TRANSCRIPT_OVERRIDE=true` (never in public), you can exercise the browser against a typed transcript by calling
`POST /captures/{id}/extract` with:

```json
{"transcript": "John asked me to move the pipe in the level 2 bathroom."}
```

Production configuration selects OpenAI for both stages and supplies model IDs and `OPENAI_API_KEY` through environment
or deployment secrets. Never put a real key in `.env.example` or tracked files.
