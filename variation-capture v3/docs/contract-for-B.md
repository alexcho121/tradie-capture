# API contract for the frontend (B)

Live docs: `/docs` (Swagger UI) on any running server. Machine-readable: `docs/openapi.json`.
CORS is open (`*`), so the frontend can live on another host (Vercel/Netlify/etc.) and call this API directly.

## The only flow that has to be fast

```
PRESS            -> start MediaRecorder (audio only)
PRESS (again)    -> stop; 3 s countdown; grab one frame from the camera
POST /captures   -> multipart: audio, photo, client_meta (JSON string), client_captured_at (ISO)
                    201 = "Saved". Show it immediately. Nothing below may block this.
POST /captures/{id}/location/resolve  (fire and forget) -> best-effort readable location
POST /captures/{id}/extract    (fire and forget; 5-15 s)  -> AI structuring, shows up as "Needs review"
```

- `/captures` does **no AI and no network calls to third parties**. It must succeed with every external service down.
- Do not send empty file parts. Omit `audio` or `photo` if you don't have them. At least one is required.
- iOS Safari records `audio/mp4`, Chrome/Android `audio/webm`. Send whatever the Blob's type is; the server accepts both.
- Camera and mic need **HTTPS** (or `localhost`).

A working version of this flow, built from the design brief, is at **`/app/`** (`static/app/index.html`; `/` redirects there).
Use it as the reference implementation or replace it with your own UI.

## Offline-first capture (answers to B's alignment questions)

One tap produces **one record** that holds audio + photo + time + location. The app saves it on the phone first
(IndexedDB), shows "Saved", then uploads when there is a connection:

| Form field on `POST /captures` | What it is |
|---|---|
| `client_capture_id` | id generated on the phone (`crypto.randomUUID()`). Retrying with the same id returns the existing capture with **200** instead of creating a duplicate. The first upload wins. |
| `client_captured_at` | phone clock at capture (ISO). Shown as `device_captured_at`, labelled *not verified*. |
| `latitude`, `longitude`, `accuracy_m`, `located_at` | **one** location fix taken when recording starts (no tracking). Empty = no fix; that's fine. Shown as `location`, `source: "device"`. Raw coordinates are retained after readable-location enrichment. |
| `client_audio_sha256`, `client_photo_sha256` | SHA-256 computed on the phone at capture. If the bytes that arrive don't match: **422** (keep it queued and retry). |
| `audio`, `photo`, `project_id`, `client_meta` | as before |

Times: `created_at` = when the **server received** it (authoritative, in the hash chain). `device_captured_at` = what the phone said.
Offline, these can be hours apart. Show both, don't merge them.

Statuses and structured extraction remain available to backend clients, but the normal MVP frontend deliberately does
not show them. JSON keys are snake_case (`project_id`); in Swift use `keyDecodingStrategy = .convertFromSnakeCase`.

Physical / Action Button capture: not possible from a web page. Keep a single `startCapture()` in the UI code so a native
shell could call it later.

## Everything the UI needs is on one object: `CaptureOut`

```jsonc
{
  "id": "735c4051a18a",
  "client_capture_id": "3f1c…",                   // the id the phone generated, or null
  "project_id": "…",
  "created_at": "2026-10-09T04:29:39.912Z",      // server receipt time, UTC. Format for Sydney in the UI.
  "device_captured_at": "2026-10-09T04:29:31.020Z", // phone clock, not verified
  "location": {"latitude": -33.7769, "longitude": 151.0812, "accuracy_m": 15, "located_at": "…", "source": "device", "display_name": "Macquarie Park NSW", "provider": "nominatim", "attribution": "© OpenStreetMap contributors"},
  "audio_url": "/media/<id>/audio.webm",          // prefix with the API origin
  "photo_url": "/media/<id>/photo.jpg",
  "transcript": "John asked me to move …",        // speech-to-text output; may contain errors; null until extracted
  "fields": {                                     // ALWAYS all six keys, even when empty
    "requester":        {"value": "John",  "confidence": 0.9,  "source": "llm", "edited": false},
    "change":           {"value": "move the bathroom pipe 300 mil to the left", "confidence": 0.9, "source": "llm", "edited": false},
    "location":         {"value": "level 2", "confidence": 0.9, "source": "llm", "edited": false},
    "extra_time_hours": {"value": 2,       "confidence": 0.7,  "source": "llm", "edited": false},   // number
    "materials":        {"value": null,    "confidence": 0,    "source": null,  "edited": false},
    "amount_mentioned": {"value": null,    "confidence": 0,    "source": null,  "edited": false}    // only if the speaker said a price
  },
  "status": "captured | needs_review | reviewed | confirmed",
  "flags": ["missing:requester", "low_confidence:change", "no_speech", "not_structured_yet", "weak_grounding:location"],
  "edits": [{"at": "…", "field": "location", "old": "level 2", "new": "Level 3", "hash": "<64 hex>"}],
  "confirmation": {"requested_at": null, "requested_to": null, "confirmed_at": null, "confirmed_by": null,
                   "note": null, "proof_url": null, "stale": false},
  "note": null,                                 // optional worker note; separate from AI fields and confirmation
  "notice_deadline": null,                        // "YYYY-MM-DD", typed in by the user. NEVER default it.
  "deadline_note": null,
  "extraction": {"provider": "anthropic|rules|none", "model": "…", "fallback": false, "removed": [], "transcript_source": "stt|provided"},
  "integrity": {"events": 3, "head_hash": "<64 hex>"},
  "client_meta": {"speak_ms": 4100, "to_photo_ms": 9300}   // whatever you sent; device-reported, not trusted
}
```

Differences from the first sketch we agreed: a sixth field `amount_mentioned`; each field also has `source` and `edited`;
status `captured` exists (saved but not structured yet); `flags`, `extraction`, `integrity` are new.

## Normal MVP frontend

The field UI shows only project selection, capture, the records list, original audio and photo, capture time,
automatic location, and the optional worker Note. It does not expose structured extraction, review status,
confirmation, deadlines, or integrity details. Those backend capabilities remain in this contract for compatibility.
Demo data must be labelled synthetic on screen.

## Other calls

| Call | Use |
|---|---|
| `POST /projects`, `GET /projects`, `PUT /projects/{id}` | job + site contacts (contacts improve name spelling; optional, a default project is created if you skip it) |
| `POST /captures/{id}/photo` | add the photo later if it wasn't ready (once only; photos can't be replaced) |
| `POST /captures/{id}/location/resolve` | attempt a readable approximate location after saving; failure returns the unchanged capture |
| `POST /captures/{id}/review` | "I've checked it" |
| `PUT /captures/{id}/note` `{note}` | save or clear the optional worker note; `null` clears |
| `POST /captures/{id}/confirmation/request` `{to_name?, to_phone?}` | returns `message` + `sms_url`; open `sms_url` to launch the SMS app |
| `POST /captures/{id}/confirmation/confirm` (multipart `proof?`, `confirmed_by?`, `note?`) | attach the reply screenshot |
| `PUT /captures/{id}/deadline` `{notice_deadline, note}` | `null` clears |
| `GET /captures/{id}/verify` | integrity check result (`ok`, `problems[]`, `head_hash`) |
| `GET /captures/{id}/record.html` | printable evidence record: link to it, or open in a new tab and print |
| `DELETE /captures/{id}` | delete the capture and its files |

## Dev without keys

With no API keys the server still works: structuring uses a regex fallback and speech-to-text is unavailable.
So during UI work call `POST /captures/{id}/extract` with `{"transcript": "…"}` (sentences in `eval/golden_sentences.json`)
and label that as simulated on screen.
