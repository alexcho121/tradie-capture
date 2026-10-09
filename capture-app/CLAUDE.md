# CLAUDE.md — project context for Claude Code

## What this is
Backend (FastAPI + SQLite) for **"Capture now. Claim later."**, an entry in the Lyra x Product Counsel Builders Hack
(brief: "Unlocked"; submissions close Mon 12 Oct 2026 9am Sydney time). Person: small subcontractors/tradies in Australia.
Task: record verbally instructed extra work (a "variation") as evidence on site in ~10 s, without stopping work.
Site flow: Press → Speak → Press → 3 s → Photo → Saved. No form, no amount, no signature, no questions on site.
Later: AI structures the note → worker edits → requester confirms → user-entered contract notice deadline.
Part A (this repo): engineering/AI. Part B: frontend/UX/demo/site/deck, building against `docs/contract-for-B.md`.

## Commands
```bash
pip install -r requirements.txt
python -m pytest                              # 129 tests, offline, must stay green
uvicorn app.main:app --env-file .env --reload # capture app at /app/, docs at /docs, raw harness at /dev/
python eval/run_eval.py --provider rules -v   # or --provider anthropic (needs ANTHROPIC_API_KEY)
python eval/stt_eval.py --names "John,Sarah"  # needs OPENAI_API_KEY + clips in eval/audio/
python scripts/export_openapi.py              # regenerate docs/openapi.json after ANY endpoint/model change
```

## Hard rules (these are the product's honesty, not style)
1. `POST /captures` must never call an external service. "Saved" cannot depend on STT, the LLM or the network.
2. The model only proposes. Every AI value passes `grounding.ground_check` against the transcript. Never loosen it to make a test or demo look better.
3. Never invent or calculate facts: no default deadline, no price maths, no guessing hours ("half a day" → null).
4. Say "tamper-evident" / "edit history" / "evidence record". Never "tamper-proof", "legally binding", "verified" (a confirmation proof is *attached by the worker, not verified by the app*), or "legal advice".
5. Edits are events, not overwrites: change data only through `ledger.record(...)`; `captures` columns are derived from events (`ledger.replay`) so `GET /verify` can detect tampering. Photos are never replaced.
6. Escape everything that goes into HTML (`report.py`). Uploads: allow-listed types, server-generated names, size limit.
7. Demo/test data is synthetic and must be labelled so. Be explicit in docs about what is real vs simulated vs untested.
8. Don't change a response shape or status name without updating `docs/contract-for-B.md` + `docs/openapi.json` and telling Part B.

## Structure
- `app/main.py` endpoints (`create_app(settings)`; `app` is built lazily so imports have no side effects)
- `app/ledger.py` event log: `record` (append + re-materialise), `replay`, `verify_capture`, `confirmation_state`
- `app/extract.py` pipeline · `grounding.py` anti-hallucination · `rules.py` regex fallback · `numbers.py` spoken numbers/durations · `prompts.py`
- `static/app/index.html` the capture UI (vanilla JS, no build step): local IndexedDB queue → `POST /captures` with
  `client_capture_id` + phone hashes + one location fix → background `POST /extract`. Falls back to a labelled demo mode
  when `/health` is unreachable and the device has never reached the real server (otherwise it stays live and queues).
  `static/app/sw.js` caches only the app shell (network-first) so the app opens offline; **bump `CACHE` in sw.js when
  shell files change**. Visual rules come from B's brief: charcoal, restrained yellow, one action per screen.
- `app/stt.py` hosted speech-to-text (hint prompt; prompt-echo guard) · `storage.py` uploads · `db.py` SQLite · `report.py` printable record
- `tests/` offline; models/STT are mocked (`monkeypatch` `app.extract._call_anthropic`, `app.main.transcribe`), and
  `tests/test_providers.py` runs the real SDK/httpx code against a local fake server (`ANTHROPIC_BASE_URL`, `app.stt.OPENAI_URL`)
- `eval/golden_sentences.json` synthetic sentences; each field is `correct | wrong | missed | hallucinated`

## Known gaps (don't paper over them)
- STT and Anthropic calls are tested only against a local fake server, never the real APIs; verify model ids/prices in the provider docs before relying on defaults.
- The installed Anthropic SDK (1.x) has no `temperature` parameter on `messages.create`; don't add sampling params back.
- STT: `whisper-1` → `prompt` + `language`; `gpt-transcribe` → `prompt` + `keywords[]` + `languages[]` (never both language fields). No `temperature`/`response_format`.
- No auth, no retention policy, no push reminders, no offline mode. Rate limiter is in-memory, trusts `X-Forwarded-For`.
- The regex baseline scores well on the team's own sentences because it was written for that phrasing; it says nothing about real speech.
- A spoken prompt-injection sentence is "in the transcript", so grounding cannot catch it (eval case g11).
