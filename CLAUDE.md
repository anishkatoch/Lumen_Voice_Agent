# CLAUDE.md

Guidance for Claude Code (and any other AI assistant) working in this repo.

## Style rules (hard constraints)

- Never use an em dash (—) anywhere in this repo: not in code, comments, commit messages, PR descriptions, or any file written to this repo. Use a hyphen, a colon, or split into two sentences instead.
- Never add Claude as a contributor or co-author anywhere in this repo. Do not add `Co-Authored-By: Claude ...` (or any AI attribution) to commit messages, and do not add "Generated with Claude Code" or similar lines to PR descriptions. Commits and PRs should read as if written solely by the human author.

## What this is

A voice AI agent backend (FastAPI) with a Next.js admin/agents frontend. It handles phone calls (Twilio and Plivo telephony) and browser-based voice sessions, routes them through STT -> LLM -> TTS (or a speech-to-speech Realtime pipeline), does RAG over per-agent and per-user knowledge bases, and runs HR interview/campaign calling on top of the same call pipeline. Supabase (Postgres) is the database; OpenAI and Azure OpenAI are the LLM/embedding/transcription providers, with ElevenLabs and Azure AI Speech as additional TTS options.

## Architecture

### Entry points
- `app/main.py` - FastAPI app, routers mounted here.
- `app/routers/` - one router per concern: `telephony_twilio.py`, `telephony_plivo.py`, `websockets.py` (browser voice), `admin.py`, `agents.py`, `auth.py`, `conversations.py`, `hr_campaigns.py`, `ingest.py`, `kb.py`, `user_kb.py`, `health.py`.

### Voice pipelines
There are three selectable pipelines (admin config key `voice_pipeline`, read via `app/admin/admin_config_service.py`, default `"cascade"`):
- `cascade` - Deepgram/Whisper STT -> LLM provider -> ElevenLabs/OpenAI TTS (per `stt_provider`/`tts_provider` admin settings).
- `openai_classic` - single-vendor cascade: OpenAI STT -> LLM -> OpenAI TTS, regardless of `stt_provider`/`tts_provider`.
- `realtime` - OpenAI Realtime API, speech-to-speech, bypassing separate STT/LLM/TTS steps entirely (`app/voice/realtime_service.py`).

Logic shared across the Twilio, Plivo, and browser pipelines lives in `app/telephony/shared.py` (call-context resolution, system prompt assembly, KB tool eligibility/scoping, the Realtime tool-calling bridge). Per-transport specifics live in `app/routers/telephony_twilio.py`, `app/routers/telephony_plivo.py`, `app/routers/websockets.py` - these three files intentionally mirror each other's structure; a fix in one usually needs the same fix in the other two.

### Database
- `app/core/database.py` - `get_supabase()` returns a cached, module-level Supabase client; `reset_supabase()` drops the cache to force a fresh connection pool (used to recover from stale pooled connections).
- Repos are plain modules (`*_repo.py`) wrapping `get_supabase()` calls - no ORM.
- Watch out for module-level `from app.core.database import get_supabase` (binds the function reference at import time) vs. a local import inside a function (re-reads the current attribute each call). The latter is required wherever tests need to mock `get_supabase` per-test; the former silently freezes whatever `get_supabase` was at first import, for the rest of the process. `app/admin/admin_config_service.py` had exactly this bug (see commit `c2eca98`): fixed by making the DB call fail open to `DEFAULTS` instead of raising, since this lookup runs on every call's setup path and a transient DB hiccup must not abort the whole call.

### RAG / knowledge base
- Agent-level KB: `document_chunks` table, scoped by `agent_id` + scope, via `app/agents/agent_rag_service.py`.
- Account-level KB: `user_document_chunks`, scoped by specific `document_id`s, via `app/kb/user_kb_rag.py`.
- Both are combined for phone calls via `retrieve_phone_rag_context` in `app/telephony/shared.py`.
- All embedding calls (agent KB, user KB, document ingestion) go through `app/core/embeddings.py`'s `embed()` - Azure OpenAI first if configured, plain OpenAI as fallback. Do not instantiate an OpenAI client directly in a new call site; use `embed()`.
- KB search on a phone call is on-demand (a tool call the LLM can choose to make), not eager every turn, and only offered at all when the agent's `kb_instructions` names actual content to search. See `get_eligible_kb_documents`/`filter_kb_documents_by_instructions` in `app/telephony/shared.py` for the exact eligibility rules when multiple documents are selected.

### Fire-and-forget call tasks
Per-turn work (greeting, `run_pipeline`) runs as a fire-and-forget `asyncio.Task`, cancelled cooperatively on barge-in via a generation counter. Background task exceptions are logged via `log_task_exception` (done-callback) rather than disappearing silently - keep wrapping new fire-and-forget tasks the same way.

## Testing

- Run backend tests: `uv run pytest` (from repo root). Run frontend tests: `cd frontend && npm test`.
- `tests/conftest.py` sets fake env vars (including a non-resolvable `SUPABASE_URL`) before any app module imports, so a test that doesn't mock a DB/network call all the way down will hit a real DNS failure, not a clean mock miss. If a test fails with `getaddrinfo failed` / `Name or service not known`, that is a missing mock, not flakiness - trace which call is unmocked rather than adding a retry or a sleep.
- Prefer polling an observable condition over a fixed `time.sleep()` when waiting on a fire-and-forget background task in a websocket test - see the `_wait_until` helper in `tests/test_twilio_stream_pipeline.py`. A fixed sleep that "works locally" will flake on a slower/loaded CI runner as soon as the hot path gets a bit more async work in it.
- Before trusting a passing local run, consider running the full suite (`uv run pytest`, no path filter) rather than just the changed test file - this repo has had real cross-file test pollution from module-level import caching (see the `admin_config_service` case above); a file passing in isolation is not sufficient proof.

## CI

`.github/workflows/publish.yml` runs backend tests + frontend lint/build/test on every push/PR to `main`, then deploys to a Hugging Face Space on push to `main`. A red run on `main` should be fixed, not ignored or force-pushed around.
