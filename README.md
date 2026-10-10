# Tradie Capture

Tradie Capture is a simple field tool for tradies and subcontractors to quickly record verbal extra-work requests before the details get forgotten.

The idea is simple:

**Capture now. Claim later.**

Instead of stopping to fill out a variation form, the user can record what was requested, take a photo, and save the evidence in a few seconds.

## Why we built it

A lot of extra work on construction sites starts as a quick verbal request.

The problem is that the request may never get properly written down, which can make it harder to prove later:
- who asked for the change
- what work was requested
- where it happened
- how much extra time was involved

Tradie Capture is designed around the moment the request happens, when the worker is usually busy and does not want to do paperwork.

## How it works

1. Tap to start a capture
2. Say what was requested
3. Stop recording
4. Take a photo
5. The app saves the original evidence
6. AI creates a structured draft from the recording

Example:

> John asked me to move the waste pipe 300 millimetres left in the Level 2 bathroom. It will take about two extra hours.

The app can turn that into:

- Requested by: John
- Work requested: Move the waste pipe 300 mm left
- Work area: Level 2 bathroom
- Extra time: 2 hours

If something is missing or uncertain, the app does not guess.

## Main features

- Voice capture
- Photo evidence
- Automatic timestamp
- GPS and approximate address
- Speech-to-text
- AI-generated structured draft
- Conservative grounding to reduce unsupported AI output
- Editable draft
- Original audio and photo kept separately from the AI draft
- Offline upload queue
- 60-second recording limit
- Per-browser private records for the public demo
- Protected audio and photo access

## Tech stack

### Backend
- Python
- FastAPI
- SQLite

### Frontend
- HTML
- CSS
- JavaScript
- PWA / IndexedDB

### AI
- OpenAI `gpt-4o-mini-transcribe`
- OpenAI `gpt-4o-mini`

### Other
- OpenStreetMap Nominatim for reverse geocoding
- Render for deployment

## Live demo

- Website: https://tradie-capture.onrender.com/
- Capture app: https://tradie-capture.onrender.com/app/

The app is designed mainly for mobile use because it uses the microphone, camera and location.
This is a hackathon demo: examples are made up, and saved records can be cleared when the server restarts.

## Run locally

Quickest way, from the `capture-app` directory (sets everything up, then opens the browser):

```bash
bash scripts/run_local.sh
```

Without an `OPENAI_API_KEY` in `.env` it runs without AI (captures still save). Or step by step:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in STT_MODEL, EXTRACT_MODEL and OPENAI_API_KEY
uvicorn app.main:app --env-file .env --reload
```

Open http://localhost:8000/ for the website and http://localhost:8000/app/ for the capture app.
Microphone and camera need HTTPS or localhost. Never commit `.env`; it holds the API key.

## Deploying (Render)

- Typical settings: root directory `capture-app`, start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Set the variables from `.env.example` in the Render dashboard (the API key only there, never in the repo).
- Render free web services lose local files on every restart, redeploy and after 15 minutes without traffic, and
  take about a minute to wake up. Either use a paid instance with a persistent disk and set `DATA_DIR` to the disk's
  mount path, or set `EPHEMERAL_STORAGE=true` so the app tells visitors their records can disappear.
- `GLOBAL_AI_CALLS_PER_HOUR` and `GLOBAL_UPLOADS_PER_HOUR` cap use across all visitors. Also set a monthly budget
  limit on the OpenAI project that owns the key.

## Checks

```bash
python -m pytest                              # offline test suite
python eval/run_eval.py --provider openai -v  # 15 made-up site sentences, 90 fields, scored against the real model
```
