# Capture now. Claim later. — backend (Part A)

> Most variation tools start with a form. We start with one button.

A tradie is told to do extra work out loud, on site, with gloves on. This backend turns **one button press + one
voice note + one photo** into a saved evidence record in about ten seconds, then does the paperwork *later*:
AI structures the note, the worker reviews it, the requester can be asked to confirm, and the worker's own
contract notice deadline is tracked.

```
On site   Press → Speak → Press → 3 s → Photo → Saved          (no form, no amount, no signature, no questions)
Later     AI structures → worker edits (logged) → requester confirms → user-entered deadline
```

Python 3.11+ · FastAPI · SQLite · hosted speech-to-text · hosted LLM. **Not legal advice.**

## Run it

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                      # keys are optional, see below
uvicorn app.main:app --env-file .env --reload
```

- Capture app: http://localhost:8000/ (redirects to `/app/`) · API docs: `/docs` · raw test harness: `/dev/`
- Tests (no network, no keys): `python -m pytest`  → 136 tests
- Eval of the structuring step: `python eval/run_eval.py --provider rules -v`

With **no keys** everything runs: structuring uses a regex fallback, speech-to-text reports "unavailable" and the
dev harness lets you type a transcript instead. Add keys to make it real:

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | – | enables hosted speech-to-text (`STT_MODEL`, default `whisper-1`; `gpt-transcribe` also supported, see below) |
| `ANTHROPIC_API_KEY` | – | enables LLM structuring (`EXTRACT_MODEL`, default `claude-haiku-5-5`; **verify the model id in the docs**) |
| `STT_PROVIDER` / `EXTRACT_PROVIDER` | auto | `openai`\|`mock` / `anthropic`\|`rules` (auto = real if the key exists) |
| `STT_LANGUAGE` | `en` | |
| `DATA_DIR` | `./data` | SQLite file + `media/` |
| `MAX_UPLOAD_MB` | 25 | per file |
| `RATE_LIMIT_PER_HOUR` | 60 | per IP on paid calls (`extract`), 2× on uploads; 0 = off |
| `CORS_ORIGINS` | `*` | comma-separated; tighten once the frontend URL is fixed |
| `GEOCODER_ENABLED` | `true` | best-effort readable locations after a capture is safely stored |
| `GEOCODER_URL` | Nominatim public reverse API | override to use another Nominatim-compatible service |
| `GEOCODER_USER_AGENT` | `TradieCapture/0.1` | identifying User-Agent required by Nominatim; set a deployment-specific value |
| `GEOCODER_TIMEOUT_SECONDS` | `4` | lookup timeout; failures leave the raw GPS capture intact |

`GET /health` says which parts are live (`stt.provider`, `extraction.provider`), so the demo can't silently run on mocks.

### Try it on a phone
Mic and camera only work on **HTTPS** (or localhost). Easiest: `cloudflared tunnel --url http://localhost:8000`
or `ngrok http 8000`, then open `https://<tunnel>/dev/` on the phone. Test on a real iPhone early:
Safari records `audio/mp4`, Chrome/Android `audio/webm`; both are accepted.

### Which speech-to-text model
OpenAI's speech-to-text guide (checked 9 Oct 2026) recommends `gpt-transcribe` for recorded speech and still lists
`whisper-1`. Both request shapes are implemented (`gpt-transcribe` gets the contact names as `keywords[]`).
The default is `whisper-1` only because its request format is long-established; **pick the model with data**: run
`eval/stt_eval.py` on the same noisy clips with `STT_MODEL=whisper-1` and `STT_MODEL=gpt-transcribe` and keep the one that
preserves more numbers and names. (That comparison is a good "assumption we tested" for the deck.) Check prices on
OpenAI's pricing page. Documented upload formats are mp3, mp4, mpeg, mpga, m4a, wav, webm (25 MB max): Chrome and Safari
recordings fit; Firefox `ogg` is stored as evidence but may be refused by the STT.

### First 15 minutes with real keys
1. `python eval/run_eval.py --provider anthropic -v` — does the model behave on the 15 sentences? Read every `hallucinated` / `wrong` line.
2. Record 3 clips through `/dev/` on your phone near some noise; check `transcript` in the response.
3. `python eval/stt_eval.py --names "John,Sarah"` on 10+ clips with noise (see the file header). This is the number that matters.

## API (full contract: `docs/contract-for-B.md`, `docs/openapi.json`, `/docs`)

| | |
|---|---|
| `POST /captures` | multipart `audio?`, `photo?`, `project_id?`, `client_meta?`, plus offline-first fields: `client_capture_id` (retry-safe), `client_captured_at`, one location fix, phone-side SHA-256s. **Saves evidence only** (no AI, no third-party calls). |
| `POST /captures/{id}/location/resolve` | best-effort reverse geocode after saving; returns the unchanged capture if GPS is absent or the provider is unavailable. |
| `POST /captures/{id}/extract` | transcribe (or accept `{"transcript"}`) → structure → ground-check. 409 if the worker edited (use `?force=true`). |
| `PATCH /captures/{id}/fields` | worker edits; every change is logged (old → new, hash). |
| `POST /captures/{id}/review` · `…/confirmation/request` · `…/confirmation/confirm` | review, build the SMS text + `sms:` link, attach the reply. |
| `PUT /captures/{id}/deadline` · `GET /deadlines` | user-typed contract notice date; list of what's due/overdue (Sydney time). |
| `GET /captures/{id}/verify` · `GET /captures/{id}/record.html` | integrity check · printable record. |

## How the AI is used (and kept honest)

```
audio ──STT──▶ transcript ──LLM (JSON only)──▶ proposal ──normalize──▶ ground_check ──▶ fields + flags
                                  │ any failure / no key                      ▲
                                  └──▶ rules fallback (regex) ────────────────┘
```

- **The model proposes; code decides.** `ground_check` (app/grounding.py) deletes any value the speaker didn't say:
  numbers must appear in the transcript (digits or words); hours must match a duration actually spoken (a range or a
  sum is rejected); names must match a spoken name (a surname the speaker didn't say is trimmed); other text needs most
  of its words in the transcript (materials stricter). Everything removed is listed in `extraction.removed`.
- No amount is ever calculated or invented; `amount_mentioned` only holds a price the speaker said, verbatim.
- No default deadline: notice periods differ per contract and from statutory timeframes, so the worker types the date.
- The transcript is treated as data in the prompt. **Limit:** a prompt-injection sentence that is itself spoken is "in the
  transcript", so grounding can't catch it (see case `g11`); the prompt and the worker's review are the defence, and the model
  can't take any action anyway — it only fills six text fields.
- A hedged hour estimate ("probably two hours") is kept but capped at 0.7 confidence, so the UI flags it.

## Tamper-evident history

Each capture has an append-only event log (`created, location_enriched, extracted, field_edited, reviewed, confirmation_requested, confirmed,
deadline_set …`), each event hashed with the previous one (`sha256(prev|seq|time|type|payload)`). The photo, audio and
confirmation proof are hashed when saved. `GET /verify` replays the log, compares it with the stored record, re-hashes
the files, and reports any mismatch. The confirmation text carries a short `Ref` taken from the head hash.

This makes later changes **detectable**. It is **not tamper-proof**: whoever controls the server and database could
rewrite everything. The server's clock, not the phone's, timestamps events (the phone's claimed time is stored separately).

## What is real, simulated, or not built (for deck page 4)

| | |
|---|---|
| **Built and unit-tested** | capture endpoint, hashed files, event log + verification, edit history, grounding checks, regex fallback, confirmation message + SMS link, deadline list, printable record, rate limiting, upload limits, GPS capture, optional readable location, retry-safe uploads |
| **Built, browser-tested with a fake camera/mic/GPS (Chromium)** | the capture app at `/app/`: tap → record → 3-2-1 → photo → saved on the phone → upload, including a capture made offline and uploaded later, and opening the app with no network after the first visit (service worker). Add to Home Screen works through `manifest.json`. **Not yet tried on a real iPhone or Android phone** |
| **Built, exercised against a local fake server only** | OpenAI speech-to-text call and Anthropic structuring call: the real SDK / HTTP client code runs end to end in the tests (this caught a real bug: the current Anthropic SDK no longer accepts `temperature`). They have **never been sent to the real APIs from here: run them once with real keys before you describe them as working** |
| **Simulated / manual** | the 15 golden sentences are synthetic; demo audio and any customer names are invented; the requester's "YES" is attached by the worker, not received by the app; no push or SMS is sent by the server |
| **Not built** | accounts/auth (data is visible to anyone with the link: demo data only), push reminders, a real SMS/email gateway, any verification that a confirmation is genuine |
| **Existed before** | FastAPI, SQLite, MediaRecorder, the hosted STT and LLM. Similar variation tools exist; the comparison belongs in the deck, with sources and access dates |

Numbers to quote only if you measured them: `eval/results.json` (structuring, synthetic sentences — the regex baseline scores
high **because it was written against the same kind of sentences; that is not accuracy on real speech**), `eval/stt_results.json`
(your own clips), and the `client_meta` timings from the dev harness for the "seconds to capture" test.

## Privacy and legal notes (not legal advice)

- The tool records the **worker's own spoken note** about instructions they were given. Don't use it to secretly record other people; recording rules vary by state — check before you describe or demo any such use.
- A record shows what was captured and when. It does not prove the work was agreed or is payable. Written-variation and notice requirements depend on the contract and law; the app only stores the date the user enters.
- Audio, photos and transcripts go to the speech and language providers you configure. Use synthetic material for the demo. There is no auth and no retention policy yet.

## Layout

```
app/        main.py (endpoints) · ledger.py (event log, replay, verify) · extract.py / grounding.py / rules.py / numbers.py
            stt.py · prompts.py · storage.py · db.py · schemas.py · report.py · hashing.py · config.py
static/app  the capture app (from the design brief)     static/dev  raw test harness
tests/      136 tests, no network       eval/   golden sentences, run_eval.py, stt_eval.py
docs/       contract-for-B.md, openapi.json (regenerate: python scripts/export_openapi.py)
```

## Readable locations

The browser requests one GPS fix when recording begins. Saving never waits for a successful fix, and `POST /captures`
never calls a third party. After upload, the app separately asks the backend to resolve the coordinates through the
public OpenStreetMap Nominatim reverse API. A successful label is added to the tamper-evident event history while the
original latitude, longitude, and accuracy remain unchanged. Failed or offline lookups return the saved capture and can
be retried when Record Detail is opened later.

This MVP caches repeated nearby lookups in each server process, sends at most one provider request per second, identifies itself with `GEOCODER_USER_AGENT`, and
shows `© OpenStreetMap contributors` beside resolved locations. The public service is appropriate only for low-volume use;
use a self-hosted or compatible provider before higher-volume deployment. See the [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/).
