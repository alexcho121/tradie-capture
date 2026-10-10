# CLAUDE.md — project context for Claude Code

## What this is
**Tradie Capture** ("Capture now. Claim later."), an entry in the Lyra x Product Counsel Builders Hack
(brief: "Unlocked"; submissions close Mon 12 Oct 2026 9am Sydney time). Person: small subcontractors/tradies in Australia.
Task: record verbally instructed extra work (a "variation") as evidence on site in ~15 s, without stopping work.
Site flow: Press → Speak (max 60 s) → Press → 3 s → Photo → Saved. No form, no amount, no signature, no questions on site.
Later: speech-to-text → AI draft (6 fields) checked against the transcript → worker reviews and edits.
Confirmation by text and contract deadlines exist in the API but are deliberately not in the app UI (out of scope for the demo).
FastAPI + SQLite backend, vanilla-JS PWA, public landing page. Deployed on Render.

## Commands
```bash
pip install -r requirements.txt
python -m pytest                              # offline, must stay green (no real API calls)
uvicorn app.main:app --env-file .env --reload # website at /, capture app at /app/, API docs at /docs, raw harness at /dev/
python eval/run_eval.py --provider rules -v   # offline baseline
python eval/run_eval.py --provider openai -v  # the model the app uses; reads .env (15 drafting calls)
python eval/stt_eval.py --names "John,Sarah"  # needs OPENAI_API_KEY + clips in eval/audio/
python scripts/export_openapi.py              # regenerate docs/openapi.json after ANY endpoint/model change
```

## Hard rules (these are the product's honesty, not style)
1. `POST /captures` must never call an external service. "Saved" cannot depend on STT, the LLM or the network.
2. The model only proposes. Every AI value passes `grounding.ground_check` against the transcript. Never loosen it to make a test or demo look better.
3. Never invent or calculate facts: no default deadline, no price maths, no guessing hours ("half a day" → null).
4. Say "tamper-evident" / "edit history" / "evidence record". Never "tamper-proof", "legally binding", "verified" (a confirmation proof is *attached by the worker, not verified by the app*), "guaranteed" or "legal advice".
5. Edits are events, not overwrites: change data only through `ledger.record(...)`; `captures` columns are derived from events (`ledger.replay`) so `GET /verify` can detect tampering. Photos are never replaced.
6. Escape everything that goes into HTML (`report.py`). Uploads: allow-listed types, server-generated names, size limit.
7. Demo/test data is synthetic and must be labelled so (the website's example says "Made-up example"). Be explicit about what is real vs simulated vs untested.
8. Don't change a response shape or status name without updating `docs/contract-for-B.md` + `docs/openapi.json`.
9. Records are private per browser: every capture/project/media query is scoped by `owner_hash` (SHA-256 of the
   httpOnly `tradie_capture_owner` cookie). Never add an endpoint that reads captures or media without that scope.

## Structure
- `app/main.py` endpoints (`create_app(settings)`; `app` is built lazily so imports have no side effects).
  Owner cookie middleware; `/media/...` is served only to the owner; per-address limits plus server-wide hourly
  ceilings (`GLOBAL_AI_CALLS_PER_HOUR`, `GLOBAL_UPLOADS_PER_HOUR`) because X-Forwarded-For can be faked.
  `/health` reports providers and `storage.ephemeral` (set `EPHEMERAL_STORAGE=true` on Render free).
- `app/ledger.py` event log: `record` (append + re-materialise), `replay`, `verify_capture`, `confirmation_state`
- `app/extract.py` pipeline (quality gate → OpenAI structured output, or rules) · `grounding.py` anti-hallucination ·
  `rules.py` regex fallback · `numbers.py` spoken numbers/durations · `prompts.py`
- `app/stt.py` hosted speech-to-text (hint prompt; prompt-echo guard) · `geocode.py` best-effort Nominatim label ·
  `storage.py` uploads · `db.py` SQLite (+ additive migrations) · `report.py` printable record
- `static/site/` the public website served at `/` (`index.html`, vendored MIT `qrcode.js`, `og.png` link preview).
  Fill `LINKS.video` / `LINKS.code` at the bottom of `index.html` once the demo video and repo are public.
- `static/app/index.html` the capture UI (vanilla JS, no build step): local IndexedDB queue → `POST /captures` with
  `client_capture_id` + phone hashes + one location fix → background `POST /extract`. Labelled demo mode only when
  `/health` is unreachable and the device has never reached the real server. "Check" tag = unedited value with
  confidence below `review_below` (0.8, same constant as the server) or a grounding/low-confidence flag. A rules-only
  draft is titled "Draft", not "AI Draft". `static/app/sw.js` caches the app shell (network-first);
  **bump `CACHE` in sw.js when shell files change**. Colours: sky = information/words, yellow = action/check.
- `tests/` offline; STT/LLM are mocked (`monkeypatch` `app.extract._call_openai` / `_call_anthropic`,
  `app.main.transcribe`); `tests/test_providers.py` runs the real httpx/SDK code against a local fake server.
- `eval/golden_sentences.json` 15 synthetic sentences x 6 fields = 90 cells; each is `correct | wrong | missed | hallucinated`

## Known gaps (don't paper over them)
- Speech-to-text and drafting have run against the real OpenAI API only in the team's own test captures: Chrome (webm) on 9 Oct, and end to end on an iPhone in Safari (audio/mp4) on 10 Oct with no problems found. Not yet tested with working tradies.
- Render free web services wipe local files on restart, redeploy and after 15 min idle (and cold-start in ~1 min). For judging, use a paid instance with a persistent disk (`DATA_DIR` on the disk) or set `EPHEMERAL_STORAGE=true` so the app says so.
- On iPhone, a home-screen web app may keep separate cookies from Safari, so records made in one may not show in the other.
- No accounts, no retention policy, no push reminders. Rate limiters are in-memory and reset on restart.
- The regex baseline scores well on the team's own sentences because it was written for that phrasing; it says nothing about real speech.
- A spoken prompt-injection sentence is "in the transcript", so grounding cannot catch it (eval case g11).
- Observed on 9 Oct: STT heard "level two" as "level to" and "waste pipe" as "west pipe"; a self-introduction put the
  speaker's own name in "Requested by". The worker's review is the defence; don't claim otherwise.
