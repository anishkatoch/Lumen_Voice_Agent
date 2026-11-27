# Backend Problems & Fixes

---

## 1. CORS Blocking All API Requests

**Problem:**
Frontend was served from `http://localhost:8003` and `http://0.0.0.0:8003` but neither was in the CORS whitelist. The browser sends an `Origin` header with every request, and FastAPI was rejecting them with "Disallowed CORS origin", which the browser surfaced as "Failed to fetch".

**Root Cause:**
`config.yaml` only had `http://localhost:3000` and the Vercel URL. When FastAPI started serving the frontend directly on port 8003, the origin changed but the whitelist was never updated.

**Fix:**
Added `http://localhost:8003` and `http://0.0.0.0:8003` to `cors.allowed_origins` in `config.yaml`.

---

## 2. `"error" in data` False Positive

**Problem:**
Supabase returns `{"error": null}` on some success responses. The check `if "error" in data` was treating these as errors and raising HTTP exceptions on valid responses.

**Root Cause:**
Python's `in` operator checks for key existence, not truthiness of the value.

**Fix:**
Changed to `data.get("error")` which is only truthy when the error field actually has a value.

---

## 3. Duplicate Email Shows "Failed to Fetch" Instead of Clear Message

**Problem:**
When a user tried to sign up with an already-registered email, Supabase returns 200 with no `access_token` (to prevent user enumeration). The backend returned `confirmation_required`, which confused the frontend into showing a generic error.

**Root Cause:**
Supabase intentionally hides whether an email exists. The backend had no way to distinguish "email needs confirmation" from "email already taken".

**Fix:**
After getting no `access_token`, the backend attempts a sign-in with the same credentials. If sign-in succeeds, the email is already registered — raise HTTP 400 with a clear message: "An account with this email already exists. Please sign in instead."

---

## 4. Missing Conversation Ownership Check

**Problem:**
Any authenticated user could read any other user's conversation messages by guessing a conversation UUID.

**Root Cause:**
The `GET /api/conversations/{id}/messages` endpoint only checked if the conversation existed, not whether it belonged to the requesting user.

**Fix:**
Added `get_conversation_by_id()` in `conversation_repo.py` and checked that `conversation.user_id == current_user["sub"]`. Returns 404 if the conversation doesn't belong to the user.

---

## 5. Blocking Supabase Calls on Async Event Loop

**Problem:**
All repository functions (Supabase SDK calls) are synchronous but were called directly in async FastAPI route handlers. This blocked the entire event loop during every DB call, preventing other requests from being handled.

**Root Cause:**
The Supabase Python SDK is synchronous. Calling it directly in `async def` handlers blocks the event loop.

**Fix:**
Wrapped all repo calls in `asyncio.to_thread()` so they run in a thread pool without blocking the event loop.

---

## 6. Password Reset Link Redirecting to Vercel Instead of Localhost

**Problem:**
Supabase password reset emails contained links pointing to `https://lumen-agent-phi.vercel.app/auth` instead of the local development server.

**Root Cause:**
The Supabase project's "Site URL" was configured to the production Vercel URL. All auth redirect emails use this URL.

**Fix:**
Replaced email link-based reset with OTP (one-time password) flow. User enters their email, receives a 6-digit code, enters the code + new password directly in the app. No redirect links needed, works on any environment.

---

## 7. OTP Email Rate Limit

**Problem:**
After multiple password reset attempts, Supabase returned `429 over_email_send_rate_limit` and no OTP was sent. The backend silently ignored this and returned success to the frontend.

**Root Cause:**
Supabase free tier has email rate limits. The backend was not checking the response status from the OTP endpoint.

**Fix:**
Added status code check on the OTP endpoint response. If 429 is returned, raise HTTP 429 with a clear message: "Too many attempts. Please wait a few minutes and try again."

---

## 8. Barge-In Not Working — Backend Already in LISTENING When User Speaks

**Problem:**
When the user tried to interrupt the AI mid-answer, the interruption was treated as a new fresh question rather than a barge-in. The AI would finish speaking its full previous answer, then answer the new question.

**Root Cause:**
The backend sends all TTS MP3 chunks to the frontend almost instantly over the network (e.g. 388 chunks in ~1 second), then immediately switches state from SPEAKING → LISTENING. But the frontend takes much longer to actually play those chunks as audio (30+ seconds of speech). By the time the user spoke to interrupt, the backend was already in LISTENING state and treated the speech as a normal new question, not a barge-in.

**Fix:**
Implemented a playback synchronization protocol between frontend and backend:
- Frontend sends `playback_started` when it begins playing the audio → backend stays in SPEAKING state
- Frontend sends `playback_ended` when audio finishes playing → backend switches to LISTENING
- If user speaks while backend is in SPEAKING → real barge-in triggers: TTS task cancelled, `stop_audio` sent to frontend, speech collected immediately as new question

---

## 9. Barge-In Cancels TTS But Pipeline Crashes

**Problem:**
When barge-in cancelled the TTS task, the `run_pipeline` coroutine that was `await`-ing the TTS task received a `CancelledError` and crashed. The new question was never processed because the pipeline was dead.

**Root Cause:**
`await session.tts_task` propagates `CancelledError` up to the caller (`run_pipeline`), killing the entire pipeline coroutine.

**Fix:**
Wrapped `await session.tts_task` in a try/except for `asyncio.CancelledError` so the pipeline continues cleanly after barge-in.

---

## 10. Barge-In Discarded the Speech That Triggered It

**Problem:**
When barge-in was detected, the speech buffer was cleared and `speech_chunks` reset to 0. The audio chunk that actually triggered the barge-in was thrown away. The user had to speak again from scratch for the new question to be processed.

**Root Cause:**
The barge-in handler did `speech_buffer.clear(); speech_chunks = 0` and then `continue`d, discarding the triggering audio chunk.

**Fix:**
Changed to `speech_buffer.clear(); speech_buffer.append(audio_bytes); speech_chunks = 1` — the triggering chunk is kept as the first chunk of the new utterance, so the user's question is captured from the very first word.

---

## 11. `audio_end` Sent Even After Barge-In

**Problem:**
After barge-in cancelled the TTS stream, the `finally` block in `run_tts_and_send` still sent `audio_end` to the frontend. This caused the frontend to decode and play whatever partial MP3 chunks had already been buffered, resulting in a brief burst of the old answer playing after the interruption.

**Root Cause:**
The `finally` block ran unconditionally regardless of whether barge-in had occurred.

**Fix:**
Added a `barged_in` flag. If barge-in occurred, `audio_end` is suppressed. The frontend already received `stop_audio` and cleared its buffer, so no partial audio plays.

---

## 12. VAD Not Catching Words (RMS vs Neural)

**Problem:**
Voice activity detection was missing real speech. RMS energy-based VAD could not distinguish speech from background noise — threshold was too sensitive or too strict regardless of tuning.

**Fix:**
Replaced RMS VAD with Silero neural VAD (PyTorch model). Splits each 250ms audio chunk into 512-sample windows, runs Silero on each, uses the max probability across windows. Threshold formula: `min + (1 - sensitivity) × (max - min)` — all values configurable in `config.yaml`.
**Files:** `app/vad_service.py`, patched `~/.cache/torch/hub/snakers4_silero-vad_master/`

---

## 13. No Module Named torchaudio (Silero Crash)

**Problem:**
`No module named 'torchaudio'` — Silero hub files imported torchaudio at top level, crashing on load and falling back silently to RMS.

**Fix:**
Patched the cached hub files to make torchaudio import optional (`try/except ImportError`) and removed it from the dependencies list in `hubconf.py`.
**Files:** `~/.cache/torch/hub/snakers4_silero-vad_master/hubconf.py`, `utils_vad.py`

---

## 14. Silero Chunk Size Error

**Problem:**
`Provided number of samples is 4000, Supported values: 256 or 512` — passing the full 250ms chunk (4000 samples at 16kHz) directly to Silero.

**Fix:**
Split each 250ms chunk into 512-sample windows and run the model on each window separately. Take the max probability across all windows.
**Files:** `app/vad_service.py`

---

## 15. STT Timeout at 3 Seconds

**Problem:**
Deepgram returning `None` exactly at 3.00s on every utterance — looked like no speech detected but was actually hitting the timeout.

**Fix:**
Increased `stt.timeout_seconds: 3` → `15` in `config.yaml`.
**Files:** `config.yaml`

---

## 16. DATABASE_URL — Dollar Sign in Password Stripped by dotenv

**Problem:**
Password `@Lumenagent0634$` — dotenv was expanding `$` as a shell variable, resulting in the wrong password being sent to Postgres. Auth failure: `password authentication failed for user "postgres"`.

**Fix:**
- Separated `DATABASE_URL` into individual env vars: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- Single-quoted the password in `.env` to prevent dotenv expansion: `DB_PASSWORD='@Lumenagent0634$'`
- Used psycopg2 `creator` function in Alembic `env.py` — passes raw password directly, no URL encoding or configparser involved
**Files:** `app/config.py`, `migrations/env.py`, `.env`

---

## 17. Alembic `invalid interpolation syntax` (configparser % issue)

**Problem:**
`quote_plus` encodes `$` as `%24`. Alembic passes the URL through configparser internally which treats `%24` as a variable interpolation placeholder → `ValueError: invalid interpolation syntax`.

**Fix:**
Removed `config.set_main_option("sqlalchemy.url", ...)` entirely. Pass `DATABASE_URL` directly to `create_engine` in `run_migrations_online()`, bypassing configparser completely.
**Files:** `migrations/env.py`

---

## 18. Alembic — `relation "conversations" already exists`

**Problem:**
Running `alembic upgrade head` failed because the conversations table was manually created before Alembic was set up. Alembic had no record of migration 0001 and tried to create the table again.

**Fix:**
Stamp the existing schema: `.venv/bin/alembic stamp 0001` — tells Alembic the DB is already at revision 0001. Then `alembic upgrade head` runs only the new migrations.

---

## 19. Session History Growing Unbounded

**Problem:**
`get_session_messages` loaded the entire conversation history for every LLM call. For long sessions this adds unnecessary tokens and DB load.

**Fix:**
Capped at last 20 messages with `rows[-limit:]` in `get_session_messages`.
**Files:** `app/message_repo.py`

---

## 20. Performance — Pipeline Too Slow (~2.5–3s latency)

**Problem:**
Users waited 2.5–3 seconds before hearing the first word of the AI response.

**Fixes applied:**
- VAD silence: 4 chunks (1s) → 2 chunks (500ms) — saves ~500ms
- GPT-4o → GPT-4o-mini — saves ~800ms TTFT
- ElevenLabs `eleven_turbo_v2_5` → `eleven_flash_v2_5` — saves ~150ms
- Deepgram streaming STT: open live connection when speech starts, stream chunks in real-time, transcript ready when silence is detected — saves ~300ms
- Audio format: `mp3_44100_128` → `pcm_16000` — zero MP3 codec overhead
- Sentence splitting: added `,` and `;` as split points so TTS starts on shorter chunks
- Pre-warm OpenAI + ElevenLabs TCP connections on startup — saves ~300ms on first call
**Files:** `config.yaml`, `app/deepgram_service.py`, `app/main.py`, `app/llm_service.py`, `app/message_repo.py`

---

## 21. Session Cap + Auto-Expiry Not Enforced

**Problem:**
No limit on voice sessions per user. Old sessions accumulated forever in the database.

**Fix:**
- Max 5 sessions per authorized user — oldest auto-deleted when 6th is created (`enforce_session_cap`)
- Sessions with last message older than 5 days auto-deleted on next session start (`cleanup_expired_sessions`)
- Guest users excluded from both rules
- Both limits configurable in `config.yaml` under `sessions:`
**Files:** `app/conversation_repo.py`, `app/main.py`, `config.yaml`

---

## 22. `NameError: name 'Response' is not defined` on Every Twilio Route

**Problem:**
Every request to `/twilio/voice` and `/twilio/outbound/voice` crashed with `NameError: name 'Response' is not defined`. The routes returned a 500 to Twilio, causing all inbound and outbound calls to fail silently.

**Root Cause:**
`from fastapi.responses import FileResponse` was already imported but `Response` — the plain class used to return raw XML — was never added to the import. Python raises `NameError` at runtime when the name is referenced.

**Fix:**
Changed import to `from fastapi.responses import FileResponse, Response`.
**Files:** `app/main.py`

---

## 23. ElevenLabs TTS Wrong Audio Format for Twilio — Garbage Audio

**Problem:**
Twilio phone calls produced no intelligible audio. The candidate heard only static or silence even though the AI was "responding".

**Root Cause:**
`text_to_speech_stream()` returned MP3 bytes (format `mp3_44100_128`). These bytes were then passed directly into `pcm16k_to_twilio_payload()`, which expected raw int16 PCM. The function treated MP3 header/frame bytes as audio samples, producing garbage μ-law output.

Twilio Media Streams requires G.711 μ-law at 8 kHz mono. MP3 is entirely incompatible.

**Fix:**
Created a dedicated `text_to_speech_twilio()` async generator in `app/twilio_call_service.py`:
- **ElevenLabs path:** requests `output_format="ulaw_8000"` — native μ-law 8 kHz, zero conversion needed, yields raw bytes directly to Twilio.
- **OpenAI TTS path:** receives PCM 16 kHz chunks, downsamples to 8 kHz via `resample_16k_to_8k()`, then μ-law encodes via `mulaw_encode()`.

The old `text_to_speech_stream()` is kept intact for the browser WebSocket path.
**Files:** `app/twilio_call_service.py`, `app/main.py`

---

## 24. Interview Stuck in `calling` Status After Crash or Disconnect

**Problem:**
If the Twilio WebSocket disconnected unexpectedly (network drop, server restart, exception mid-call), the interview record stayed in `status = "calling"` forever. The scheduler would never retry it, and the HR dashboard showed it perpetually as in-progress.

**Root Cause:**
`mark_interview_completed` was only called inside the `elif event == "stop":` branch. Any other exit path (exception, client disconnect, `KeyboardInterrupt`) bypassed it.

**Fix:**
Added a `_call_ended` boolean flag. The `stop` event sets it to `True` and calls completion. A `finally` block at the end of the entire WebSocket handler calls `mark_interview_completed` only if `_call_ended` is still `False` — catching every abnormal exit path.
**Files:** `app/main.py`

---

## 25. Double-Calling the Same Candidate in Multi-Worker Setups

**Problem:**
When running multiple Uvicorn workers (or after a server restart mid-tick), two workers could both detect the same pending interview and both initiate a Twilio call. The candidate would receive two simultaneous calls.

**Root Cause:**
The scheduler queried `status = 'pending'`, made the Twilio REST call, then updated `status = 'calling'`. The window between the query and the update allowed a second worker to also see `pending` and also call.

**Fix:**
Optimistic lock: before making the Twilio call, update `status = 'calling'` with a conditional `WHERE status = 'pending'`. If the update returns no rows, another worker already claimed the interview — `continue` to the next. Only if the update succeeds does this worker proceed to call.
**Files:** `app/hr_scheduler.py`

---

## 26. Concurrent Pipeline Tasks Overlapping Audio

**Problem:**
If the candidate spoke while the AI was still generating a response, a second `run_pipeline` task started in parallel. Both tasks streamed TTS simultaneously, causing overlapping garbled audio on the call.

**Root Cause:**
No guard prevented a second `run_pipeline` from being created via `asyncio.create_task` while the first was still running.

**Fix:**
Added `is_processing: bool` flag inside the Twilio WS handler. `run_pipeline` sets it `True` at entry and `False` in a `finally` block. Any new audio chunks received while `is_processing` is `True` are discarded (and any active Deepgram stream is closed).
**Files:** `app/main.py`

---

## 27. ElevenLabs Sync Iterator Blocking the Async Event Loop

**Problem:**
During Twilio calls, the entire WebSocket event loop froze while ElevenLabs was streaming audio. Twilio `media` events queued up and the call appeared to hang.

**Root Cause:**
`for chunk in client.text_to_speech.convert_as_stream(...)` is a synchronous blocking iterator. Calling it directly inside an `async def` function blocks the event loop for the entire duration of TTS generation — no other coroutines can run.

**Fix:**
Moved the sync iterator to a thread pool via `loop.run_in_executor(None, _stream_to_queue)`. The thread pushes chunks into an `asyncio.Queue`. The async generator `await queue.get()`'s each chunk, yielding control back to the event loop between chunks. A `None` sentinel signals completion.
**Files:** `app/twilio_call_service.py`

---

## 28. Twilio Path Using Batch STT Instead of Streaming

**Problem:**
Twilio phone calls had 1–2 second extra STT latency compared to browser calls. The AI response was noticeably slower to start on phone calls.

**Root Cause:**
The Twilio WebSocket handler was calling `transcribe_audio_smart()` — which uploads the full accumulated audio buffer as a WAV file and waits for the Deepgram prerecorded API to respond. The browser WebSocket used `StreamingTranscriber` (live Deepgram connection) which starts transcribing as the user speaks.

**Fix:**
Replaced the Twilio STT path with `get_streaming_transcriber()` — same `StreamingTranscriber` class used by the browser. Opens a Deepgram live connection on the first speech chunk, streams each PCM chunk incrementally, calls `finish()` on silence detection. Transcript is ready almost instantly when the user stops speaking.
**Files:** `app/main.py`

---

## 29. No Auto-Webhook Configuration When Adding a Phone Number or Saving Credentials

**Problem:**
HR admins had to manually go into the Twilio console and configure the Voice Webhook URL every time they added a phone number or updated the `webhook_base_url`. Missing this step caused all inbound calls to fail with "not configured".

**Root Cause:**
No code path triggered Twilio's REST API to set `VoiceUrl` on the phone number SID after save.

**Fix:**
Created `app/twilio_webhook_config.py` with two functions:
- `configure_number_webhook(user_id, number_id, ...)` — looks up the phone number's Twilio SID via REST, then sets its `VoiceUrl` to `{webhook_base_url}/twilio/voice`.
- `configure_all_user_numbers(user_id, ...)` — calls the above for every phone number belonging to the user.

Both are fired as `asyncio.create_task(...)` background tasks (non-blocking) from:
- `POST /api/twilio/numbers` — after creating a new number, if credentials are already saved.
- `PUT /api/twilio/settings` — after saving/updating credentials, for all existing numbers.

Migration `0015_twilio_number_sid.py` adds a `twilio_number_sid` column to cache the SID and avoid redundant lookups.
**Files:** `app/twilio_webhook_config.py`, `app/main.py`, `migrations/versions/0015_twilio_number_sid.py`

---

## 30. `get_due_interviews` No Upper-Bound Time Window — Retrying Old Calls

**Problem:**
The scheduler would attempt to call interviews that were missed hours or even days earlier (e.g., server was down). This resulted in surprise calls to candidates long after the interview slot had passed.

**Root Cause:**
`get_due_interviews` only checked `trigger_at <= now` with no upper bound. Any past-due interview, regardless of how old, was returned as due.

**Fix:**
Added a 2-hour upper-bound check: `(now - trigger_at).total_seconds() < 7200`. Interviews whose trigger time was more than 2 hours ago are silently skipped.
**Files:** `app/hr_repo.py`

---

## 31. Agent Had No Knowledge of the Scheduled Interview Time

**Problem:**
The AI agent on phone calls had no idea when the actual interview was scheduled or how many minutes before the interview the call was happening. It couldn't say "Your interview is in 30 minutes" or reference the scheduled time.

**Root Cause:**
The `extra_system_context` injected into the agent's system prompt only included the candidate's name, role, resume, and questions — not the interview datetime or the lead time.

**Fix:**
Formatted `scheduled_at` as a human-readable string (`"Wednesday, July 15, 2026 at 10:30 AM UTC"`) and added two lines to the system context:
- `"The interview is scheduled for: {sched_formatted}."`
- `"You are calling {call_lead} minutes before the interview time as a pre-interview reminder."`
**Files:** `app/main.py`

---

## 32. `PGRST204` — Missing Column on Production DB After Deploy

**Problem:**
After deploying, saving Twilio settings threw `Could not find the 'twilio_number_sid' column of 'twilio_phone_numbers' in the schema cache`, logged repeatedly as an unretrieved task exception.

**Root Cause:**
Migration `0015_twilio_number_sid.py` existed in the codebase and was deployed, but had never actually been run against the live Supabase database — migrations are a separate step from deploying code, and this one was missed.

**Fix:**
Ran `alembic upgrade head` directly against the production DB (via the direct/session connection, not the transaction pooler) to apply the pending migration. Code and schema now match.
**Files:** `migrations/versions/0015_twilio_number_sid.py`

---

## 33. Docker Only Ran the Backend — Frontend Never Served

**Problem:**
After deploying to Hugging Face Spaces, visiting the site showed a bare API or an old cached page — the actual Next.js frontend was never present in the container at all.

**Root Cause:**
The `Dockerfile` only built and ran the FastAPI backend. It never ran `npm install && npm run build` for `frontend/`, so `frontend/out` never existed inside the image.

**Fix:**
Converted to a multi-stage Dockerfile: stage 1 (`node:20-alpine`) installs frontend deps and runs `npm run build` (the frontend uses `output: "export"`, so this produces `frontend/out`); stage 2 copies only `frontend/out` into the Python runtime image. FastAPI's existing static-file-serving block (mounted only `if os.path.isdir(_FRONTEND)`) then picks it up automatically.
**Files:** `Dockerfile`, `app/main.py`

---

## 34. HF Spaces Health Check Timed Out — No Fallback Root Route

**Problem:**
`Launch timed out, workload was not healthy after 30 min`, even though the container logs showed Uvicorn had started successfully and wasn't crashing.

**Root Cause:**
The `/` route was only registered `if os.path.isdir(_FRONTEND)` — if the frontend build step ever failed or produced nothing, there was *no route at all* for `/`. HF Spaces' health probe hitting `/` got a 404 forever and never marked the Space healthy.

**Fix:**
Added an `else` branch registering a fallback `/` route that always returns `{"status": "ok", ...}` with HTTP 200, decoupling container health from frontend build success, plus a `[Startup] WARNING` log line so a missing frontend build is now visible instead of silently 404ing.
**Files:** `app/main.py`

---

## 35. `print()` Logs Invisible in Docker — Blocked Diagnostics

**Problem:**
Diagnostic `print()` statements added to trace a bug frequently never appeared in the Hugging Face Space's live logs, even though the code was confirmed deployed and running.

**Root Cause:**
Python defaults to block-buffering stdout when it isn't attached to a TTY (true for any Docker container's captured stdout). Output sits in an internal buffer and only flushes intermittently — e.g. right after an unrelated `logging`-based access-log line that happens to force a flush of the same underlying stream — making some prints appear and others (from the same run) never show up at all.

**Fix:**
Added `ENV PYTHONUNBUFFERED=1` to the Dockerfile, forcing every `print()` call to flush immediately. Also affects local dev servers run with output redirected to a file — same fix applies there (`PYTHONUNBUFFERED=1 uvicorn ...`).
**Files:** `Dockerfile`

---

## 36. Twilio Not Forwarding URL Query Params on `<Stream>` — Inbound Calls Died Instantly

**Problem:**
Every inbound call connected (`WebSocket accepted`) then closed immediately, with zero logging in between — no greeting, no error.

**Root Cause:**
The inbound/outbound TwiML built the Media Stream URL as `wss://host/twilio/stream?to=+1XXXXXXXXXX` and read `to` as a FastAPI query parameter. Twilio does not reliably forward URL query strings on the `<Stream url="...">` element to the WebSocket — the handler always received `to=""`, hit the "no active number found" guard, and closed the socket before any diagnostic code could run.

**Fix:**
Switched to Twilio's documented mechanism for passing call data: `<Stream url="wss://host/twilio/stream"><Parameter name="to" value="+1XXXXXXXXXX" /></Stream>`. The WebSocket handler now resolves `to`/`interview_id` from the `"start"` event's `customParameters` (which Twilio does deliver reliably) instead of from query params, and the agent/user/conversation setup was restructured to run once that event arrives rather than immediately on connect.
**Files:** `app/main.py`

---

## 37. Duplicate Active Phone Number Rows — Non-Deterministic Inbound Routing

**Problem:**
The same physical Twilio number was registered as `is_active: true` under two different app users/agents simultaneously. Inbound call routing could unpredictably resolve to either agent depending on row order.

**Root Cause:**
`get_number_by_phone()` queries `.eq("phone_number", ...).eq("is_active", True).limit(1)` with no tiebreaker — if two rows both match, which one comes back is undefined.

**Fix:**
Deactivated the stale row (`is_active = false`) so only the intended agent's row remains active for that number. The underlying ambiguity (nothing prevents two active rows for the same number) is still a latent risk worth a uniqueness constraint if it recurs.
**Files:** (data fix in `twilio_phone_numbers` table, no code change)

---

## 38. `save_message()` Called With Wrong Argument Order in Twilio Pipeline

**Problem:**
The agent transcribed the caller correctly but never replied — `psycopg2`/PostgREST error: `invalid input syntax for type uuid: "user"`.

**Root Cause:**
`save_message(conversation_id, user_id, role, content)` is the real signature, but the Twilio WebSocket handler called it as `save_message(conversation_id, "user", user_message, user_id)` — swapping `role` and `user_id`, so the literal string `"user"` was inserted into the `user_id` UUID column.

**Fix:**
Corrected both call sites (user message and assistant reply) in `run_pipeline` to match the real parameter order.
**Files:** `app/main.py`

---

## 39. G.711 μ-law Decode Table Had a Bit-Shift Bug — Incoming Call Audio Crushed to Near-Silence

**Problem:**
On phone calls, VAD almost never detected the caller speaking — RMS/probability stayed near zero even while the caller was clearly talking.

**Root Cause:**
`_build_decode_table()`'s mantissa shift used `(exp + 2)` instead of the correct `(exp + 3)` from the standard ITU-T G.711 μ-law formula. Verified against the reference algorithm: **all 256 possible byte values decoded incorrectly**, reconstructing real speech at roughly 1/3–1/2 its true amplitude. TTS output (encoding) used a separate, correct logarithmic companding formula and was unaffected — only incoming caller audio was broken.

**Fix:**
Rewrote `_build_decode_table()` to match the standard algorithm exactly (verified against a reference implementation for all 256 byte values, plus a round-trip amplitude test). This bug likely degraded every phone call since the file was written, not just ones from this session.
**Files:** `app/twilio_call_service.py`

---

## 40. Silero VAD Never Actually Ran on Twilio Audio — Windowing Bug

**Problem:**
Even after fixing the decode table, VAD probability stayed exactly `0.000` on every single Twilio audio chunk, regardless of real content.

**Root Cause:**
Silero requires a minimum 512-sample window (32ms) per call. Twilio sends 20ms chunks — 320 samples at 16kHz after upsampling, shorter than one window. `range(0, len(audio_array) - WINDOW + 1, WINDOW)` evaluates to `range(0, -191, 512)` — an **empty range** — so the model inference loop never executed at all, and `max_prob` fell through to its hardcoded default of `0.0` every time, independent of audio content.

**Fix:**
Pad audio shorter than the window up to `WINDOW` samples before running inference, guaranteeing at least one real model call. Later refined further (see #43) to avoid synthetic zero-padding in favor of a real audio window.
**Files:** `app/vad_service.py`

---

## 41. `retrieve_agent_context()` Called With a Signature That Doesn't Exist

**Problem:**
Once VAD and Deepgram were fixed and a real transcript finally reached the pipeline, it crashed: `TypeError: retrieve_agent_context() got an unexpected keyword argument 'doc_ids'`.

**Root Cause:**
The real signature is `(query, agent_id, user_id, scopes, top_k)` — it filters by `agent_id` + scope (`"personal"`/`"global"`), not by an explicit document ID list. The Twilio pipeline called it with `query=..., agent_id=..., doc_ids=...`, a parameter that has never existed on this function. It was the *only* call site for this function anywhere in the codebase, so the bug had zero chance of being caught by any other code path.

**Fix:**
Changed the call to match the real signature, passing `user_id` and defaulting `scopes` to `["personal", "global"]` — the same default the working browser multi-agent path already uses.
**Files:** `app/main.py`

---

## 42. `retrieve_user_kb_context()` Called With a Signature That Doesn't Exist

**Problem:**
Same class of bug as #41, next in the pipeline: `TypeError: retrieve_user_kb_context() got an unexpected keyword argument 'user_id'`.

**Root Cause:**
The real signature is `(query, doc_ids, top_k)` — it has no concept of `user_id` at all; it filters by an explicit list of document IDs. The pipeline called it with `user_id=user_id, query=user_message`.

**Fix:**
Fetch the user's personal KB document IDs via `get_user_documents(user_id)` first, then call `retrieve_user_kb_context(user_message, user_doc_ids)` with the correct positional signature.
**Files:** `app/main.py`

---

## 43. Twilio Phone Calls Had No Barge-In At All

**Problem:**
Callers could not interrupt the agent — it always finished speaking its full response before processing anything the caller said in the meantime.

**Root Cause:**
The "media" event handler unconditionally dropped all incoming audio while `is_speaking or is_processing` was true (`continue`, no VAD check at all). The browser WebSocket path already had real barge-in; the Twilio path never did.

**Fix:**
While the agent is speaking/processing, incoming audio is now checked against the agent's `barge_in_sensitivity`. On real speech, cancels the in-flight speak/pipeline task, sends Twilio a `{"event": "clear"}` message to flush already-queued outbound audio, and immediately starts listening to the new utterance.
**Files:** `app/main.py`

---

## 44. Barge-In False-Triggered on a Single Spurious VAD Spike

**Problem:**
A call was interrupted (agent's response cancelled entirely, no audio ever played) even though the caller had not said anything — confirmed by the logs showing no `[Twilio TTS]` line had even fired yet.

**Root Cause:**
Barge-in cancelled the agent's task the instant a *single* chunk crossed the barge-in threshold — no debounce. A lone anomalous VAD reading (line noise, brief artifact) was as effective as sustained real speech at killing an irreplaceable in-flight response. (Synthetic reproduction with white noise, isolated blips, and tonal bursts did not conclusively reproduce the exact spike, so the precise root cause of that one instance is unconfirmed — but the fix is correct regardless of cause.)

**Fix:**
Barge-in now requires 4 consecutive above-threshold chunks (~80ms of sustained signal) before committing to cancel, instead of firing on the first chunk.
**Files:** `app/main.py`

---

## 45. Zero-Padding Isolated VAD Chunks Produced Noisy, Unstable Predictions

**Problem:**
Related to #40/#44 — feeding Silero a synthetic zero-padded 20ms fragment (with the model's internal state reset fresh on every single call) is a usage pattern the model was never designed for, and is a plausible contributor to unreliable readings.

**Root Cause:**
Each Twilio chunk was padded independently rather than given real, contiguous audio context.

**Fix:**
Replaced synthetic zero-padding with a real 2-chunk rolling window — each VAD call now uses the tail of the *previous* real chunk plus the current one, giving Silero genuine audio instead of a padded fragment, for both the normal-listening and barge-in checks.
**Files:** `app/main.py`

---

## 46. Deepgram Streaming Transcription Intermittently Lost the Caller's Utterance

**Problem:**
Some calls (not all — roughly 1 in 4 in repeated local testing) had the caller speak clearly, VAD/finalization logic fire correctly, `dg.finish()` get called — and return `None`. No crash, no error, no reply: the utterance was just silently dropped.

**Root Cause:**
Unconfirmed exactly — most likely a timing/handshake issue on Deepgram's live WebSocket connection specific to that stream (each `StreamingTranscriber` opens an entirely fresh `DeepgramClient` and connection per call, so prewarming a separate throwaway connection at startup did **not** fix it, confirmed by testing).

**Fix:**
Buffer the raw utterance audio alongside what's sent to the streaming connection. If `dg.finish()` returns empty despite genuine detected speech, fall back to Deepgram's prerecorded API (`transcribe_audio_smart`) on the buffered audio, so a lost streaming transcript no longer means the call goes silent.
**Files:** `app/main.py`

---

## 47. Zero Test Coverage on the Twilio Pipeline Let 5+ Real Bugs Ship Undetected

**Problem:**
Bugs #38, #41, and #42 (wrong function-call signatures) all had zero prior test coverage — each was the *only* call site for its function anywhere in the codebase, so nothing could have caught the mismatch before a live phone call did.

**Fix:**
Added `tests/test_twilio_stream_pipeline.py` — a full WebSocket integration test for `/twilio/stream` plus targeted regression tests. Key technique: internal calls are validated with `inspect.signature(real_func).bind(*args, **kwargs)`, which raises immediately on a mismatch — the same class of error that shipped to production in #41/#42, now caught in CI. Also covers: mu-law decode correctness vs. the ITU-T reference algorithm, VAD short-chunk windowing, and barge-in debounce logic (mirrored as a pure function, matching this file's existing convention for the scheduler timing tests).
**Files:** `tests/test_twilio_stream_pipeline.py`

---

## 48. Pre-Existing Test Suite Failures Unrelated to Any Session Change

**Problem:**
8 tests failed on a full suite run — none of them touching Twilio code.

**Root Cause (three separate causes):**
1. `conftest.py`'s fake `SUPABASE_ANON_KEY`/`SUPABASE_SERVICE_ROLE_KEY` (`"fake-anon-key"`) didn't match the newer `supabase-py` library's JWT-format validation regex.
2. `generate_response_stream` was refactored at some point to call a newer multi-provider `_get_llm_client()` instead of `_get_openai()`, but 6 tests across two files still mocked the old function — so their mocks silently did nothing and the real (fake-keyed) client attempted a real network call.
3. `GET /api/agents` also calls `seed_default_agents()`, which wasn't mocked in two tests, triggering an unmocked Supabase call.

**Fix:**
Updated the fake keys to a JWT-like format; repointed the relevant mocks from `_get_openai` to `_get_llm_client` (matching its real 3-tuple return shape); added the missing `seed_default_agents` mock. Full suite: 271/271 passing.
**Files:** `tests/conftest.py`, `tests/test_agents.py`, `tests/test_services.py`

---

## 49. Turn-Completion Heuristic Added Unwanted 1s Latency — False Positive From STT Lag, Not a Real Pause

**Problem:**
A new "did the caller actually finish talking?" heuristic (checks the live partial transcript for trailing fillers before ending a turn) was meant to reduce premature cutoffs, but live-tested on a real phone call it instead added a spurious ~1 second of latency on an already-complete question. In a worse case (simulated call, finite trailing silence) the same bug caused the turn to never finalize at all.

**Root Cause:**
Confirmed via live debug logging: Deepgram's *interim* transcript lags the actual audio by a word or two. At the exact moment the silence timer fired, the caller's fully-finished question ("...leave policy?") had only recognized "...about the" so far. The heuristic's filler-word list included ordinary words (`the`, `and`, `is`, `a`, etc.) — exactly what any STT-lag cutoff looks like, regardless of whether the speaker actually paused there. It wasn't reading a hesitation, it was reading recognition lag.

**Fix:**
Narrowed the single-word filler list in `sounds_unfinished()` to only non-lexical disfluency sounds (`um`, `uh`, `umm`, `uhh`) — the one class of signal STT lag can't fake, since Deepgram would never transcribe a disfluency sound that wasn't actually spoken. Dropped conjunctions/articles/copulas entirely. Verified against the exact failing transcript plus a batch of other sentences.
**Files:** `app/turn_heuristics.py`

---

## 50. Caller Hangup Mid-Response Crashed a Background Task With an Unhandled RuntimeError

**Problem:**
If the call ended (hangup, disconnect, or clean "stop") while `run_pipeline` was still generating/speaking a reply, the background task kept running after the socket closed, then crashed: `RuntimeError: Cannot call "send" once a close message has been sent.` Wasted LLM/TTS calls for a reply nobody could hear, plus noisy unretrieved-exception logs. Found via live testing with `scripts/simulate_twilio_call.py` — more likely to hit the longer/slower a response is.

**Root Cause:**
The "stop" event handler (and the `finally` block covering every other exit path) never cancelled `_active_task` — only barge-in did. The task, created fire-and-forget via `asyncio.create_task`, had no reason to stop on its own once the websocket was gone.

**Fix:**
Cancel `_active_task` in `twilio_stream_endpoint`'s `finally` block, alongside the existing Deepgram-stream cleanup, so any in-flight greeting/response is stopped the moment the call ends for any reason.
**Files:** `app/main.py`

---

## 51. Whisper Fallback Crashed on VAD Blips — `audio_too_short` 400 Error

**Problem:**
`[Whisper] Error: Error code: 400 - {'error': {'message': 'Audio file is too short. Minimum audio length is 0.1 seconds.', ...}, 'usage': {'seconds': 0}}` — a wasted, failing API call on every short VAD blip that reached the Whisper fallback.

**Root Cause:**
`StreamingTranscriber.finish()` falls back to OpenAI Whisper whenever Deepgram's live stream produced no transcript, guarded only by `if self._raw_chunks:` — a non-empty-list check, not a minimum-duration check. A brief VAD false-positive (a tiny number of chunks, or near-silence) satisfies that check while still producing a WAV with next to no actual audio, which Whisper's API correctly rejects. `transcribe_wav_whisper()` (used by other fallback paths too) had the same gap — nothing validated duration before making the API call.

**Fix:**
Added `_wav_duration_seconds()` (reads frame count / sample rate via the `wave` module) and a shared `_MIN_AUDIO_SECONDS = 0.15` floor, applied in both `StreamingWhisperTranscriber.finish()` and `transcribe_wav_whisper()` directly — one guard covers every caller instead of requiring each one to pre-check. Below the floor, returns `None` immediately (no transcript, no wasted/failing API call) instead of ever hitting OpenAI with audio that's guaranteed to be rejected.
**Files:** `app/openai_whisper_service.py`

---

## 52. Plivo Calls Stuck on `"calling"` Forever — Missing Status-Map Entry

**Problem:**
Plivo interview calls occasionally never left `status = "calling"` in `hr_interviews`, even though the call had genuinely ended (completed or failed) on Plivo's side.

**Root Cause:**
`_PLIVO_CALL_STATUS_MAP` had no entry for `"completed"` — unlike `_TWILIO_CALL_STATUS_MAP`, which already carried it as an explicit safety net for when the media-stream websocket's own `"stop"` event handler never fires. When Plivo's status-callback webhook delivered `CallStatus=completed`, it mapped to nothing and the row was silently abandoned.

**Fix:**
Added `"completed": "completed"` to `_PLIVO_CALL_STATUS_MAP`, mirroring Twilio's map.
**Files:** `app/routers/telephony_plivo.py`, `tests/test_plivo_status_callbacks.py`

---

## 53. No Path to Resolve a Stuck `"calling"` Row When the Provider Never Reports Back

**Problem:**
Even with #52 fixed, a call whose provider webhook never reaches us at all (dead ngrok tunnel, a future status value with no mapping) still had zero path out of `"calling"` — the row stayed stuck forever and the HR dashboard showed it perpetually in-progress.

**Fix:**
Added a periodic reconciliation sweep (`run_reconciliation_loop`, every 5 minutes, 20-minute grace period) that resolves any `hr_interviews` row stuck on `"calling"` past its grace period using only data already recorded for that call: a real conversation (`messages` rows) exists → `completed`; a Call Analytics row exists with no conversation → `failed`, with a reason; no record at all → a new `"unknown"` status rather than guessing. Started as a background task from `app/main.py`'s startup handler.
**Files:** `app/hr_campaigns/call_reconciliation.py` (new), `app/hr_campaigns/hr_repo.py`, `app/main.py`, `tests/test_call_reconciliation.py`

---

## 54. Deepgram STT Path Selection Indistinguishable From the Logs

**Problem:**
A live Deepgram streaming connect, a live transcript actually arriving, and a fallback to OpenAI Whisper via config all looked identical in the logs — no way to tell which STT path a given call actually used without adding ad hoc prints each time.

**Fix:**
Added permanent debug logging at each decision point in the Deepgram streaming connect/transcript path.
**Files:** `app/voice/deepgram_service.py`

---

## 55. Plivo Calls Playing No Audio to the Caller

**Problem:**
On live Plivo phone calls the caller heard total silence for the whole call — not even the opening greeting — while STT, LLM, and reply generation all ran and logged normally.

**Root Cause:**
`playAudio`'s `media.contentType` was sent as `"audio/x-mulaw;rate=8000"` — the `;rate=` suffixed form belongs on the `<Stream>` XML tag's `contentType` attribute, not on an individual `playAudio` websocket message, where Plivo's documented schema wants the bare MIME type (`"audio/x-mulaw"`) with `sampleRate` as its own separate field. Plivo accepted the malformed value over the websocket without erroring, so nothing in the logs indicated why — it just silently never played the audio.

**Fix:**
Send `contentType: "audio/x-mulaw"` with `sampleRate: 8000` as a separate field in the `playAudio` message.
**Files:** `app/routers/telephony_plivo.py`

---

## 56. Phone Calls Loaded the Agent Owner's Personal Memory — Leakable to the Caller

**Problem:**
Phone calls (Twilio/Plivo) loaded the agent owner's `long_term_memory`, which could make the LLM address a caller by the owner's own remembered name or city — a real data/identity leak between the account owner and whoever they're calling.

**Fix:**
Stopped phone calls from loading owner long-term memory at all; only genuinely per-call facts (candidate name, schedule, resume, notes, questions) go into the call's system context.
**Files:** `app/telephony/shared.py`

---

## 57. Campaign Dial Slot Never Released When a Campaign Candidate's Call Got Stuck

**Problem:**
The #53 reconciliation sweep only covered `hr_interviews` — a `hr_campaign_candidates` row stuck on `"calling"` never released its campaign's `active_candidate_id` dial-slot claim, blocking every later candidate in that campaign from ever being dialed, not just the stuck one.

**Fix:**
Extended the reconciliation sweep to `hr_campaign_candidates`, resolving the row the same way as #53 and calling `release_campaign_claim()` so the campaign's dial slot frees up regardless of outcome.
**Files:** `app/hr_campaigns/call_reconciliation.py`, `app/hr_campaigns/hr_campaign_repo.py`

---

## 58. CI Failed Immediately on Every Test — `KeyError: 'DB_HOST'`

**Problem:**
Every test that imports `app.main` failed at fixture setup with `KeyError: 'DB_HOST'` the first time this workflow actually ran in CI, even though it worked fine locally.

**Root Cause:**
`app/core/config.py` requires `DB_HOST`/`DB_USER`/`DB_PASSWORD` with no `.get()` fallback, by design. Locally a real `.env` always provided them, masking the gap; CI has no `.env` file at all.

**Fix:**
Added fake values for all three to `tests/conftest.py`.
**Files:** `tests/conftest.py`

---

## 59. Three Separate CI-Only Flaky Failures, All Masked Locally by a Real `.env`

**Problem:**
Several tests passed locally but failed or flaked in CI, where there's no real `.env` and no real network/DB access.

**Root Cause (three separate causes):**
1. `app/telephony/plivo/plivo_repo.py` imports `get_supabase` by name directly, so patching `app.core.database.get_supabase` (what most scheduler/campaign tests did) never actually intercepted it — those tests were hitting a real network call, which happened to return empty rows against a real local dev Supabase project and so "worked" by accident.
2. `tests/test_protected.py`'s conversation-listing tests never mocked the DB at all, for the same direct-import reason, and happened to read real matching rows from the real dev database.
3. A campaign-call test in `test_twilio_stream_pipeline.py` used one fixed `time.sleep(0.3)` to cover two different async races at once.

**Fix:**
Mocked `app.telephony.plivo.plivo_repo.get_number_for_agent` directly; added proper DB mocks to the two `test_protected.py` tests; replaced the fixed sleep with a `_wait_until()` poll helper gating on each actual condition instead of a guessed duration.
**Files:** `tests/test_hr_campaigns.py`, `tests/test_protected.py`, `tests/test_twilio_hr.py`, `tests/test_twilio_stream_pipeline.py`

---

## 60. Agent Overwrote an Already-Answered Question's Recorded Answer

**Problem:**
Found on a real test call: the model occasionally answered a later question but called `record_answer` with an earlier, already-answered `question_id` — silently overwriting it with a stale/mismatched value, so the real question's answer was never recorded.

**Fix:**
Added `validate_question_answer()` in `app/telephony/shared.py` — rejects reusing an already-answered id, giving the model the same chance to notice and correct itself that an unknown id already got.
**Files:** `app/telephony/shared.py`, `tests/test_telephony_shared.py`

---

## 61. Filler Phrase Still Spoken on Every Tool Call Despite Fix #64

**Problem:**
A plain "yes"/"no" answer to `record_answer` (a quick DB write with nothing slow to cover) still got a filler phrase like "let me pull that up" every single time, even after the unconditional-filler fix in #64.

**Root Cause:**
That earlier fix only removed the *unconditional* filler; the general delay-gated filler task was still live and reliably fired anyway, because `detect_tool_call` and `stream_tool_result_response` are two sequential LLM round-trips that exceed the filler delay on their own even with nothing actually slow happening.

**Fix:**
Cancel the filler task outright the moment a `record_answer` tool call is detected; webhook tool calls (genuinely can be slow) still get it.
**Files:** `app/routers/telephony_plivo.py`, `app/routers/telephony_twilio.py`, `app/telephony/shared.py`

---

## 62. Opening Greeting Missing From Call Analytics

**Problem:**
The agent's opening line (`greeting_override` or `first_message`) was spoken on every phone call but never saved — Call Analytics was always missing the first turn, unlike every other turn in the conversation.

**Fix:**
Added `_speak_greeting()` in both `telephony_twilio.py` and `telephony_plivo.py` to save the greeting like any other assistant turn.
**Files:** `app/routers/telephony_twilio.py`, `app/routers/telephony_plivo.py`

---

## 63. Agent Launched Into Interview Questions Without Waiting for the Candidate to Confirm Identity

**Problem:**
Live-tested: a candidate saying "wrong person" or just "who's calling" could get run over by the agent launching straight into interview content right after the greeting.

**Root Cause:**
Interview/campaign call prompts told the LLM to "greet them... ask the questions" as one uninterrupted block, with no instruction to wait for a reply first.

**Fix:**
Prompt now explicitly tells the model the greeting already happened and to listen to the candidate's reply before proceeding. Later moved out of hardcoded per-call context into the editable `hr_instructions` prompt — see #67.
**Files:** `app/telephony/shared.py`

---

## 64. Unconditional Filler Phrase Stacked on Every Tool Call

**Problem:**
Live call: a plain "yes"/"no" answer got two mismatched filler phrases spoken back to back.

**Root Cause:**
An unconditional filler phrase was spoken every time a tool call (e.g. `record_answer`) was detected, regardless of whether any real delay had occurred.

**Fix:**
Removed the unconditional filler — the existing delay-gated filler task already covers the case where one is actually needed. Fully resolved only after #61, which found the delay-gated task still fired on its own for this exact call shape.
**Files:** `app/routers/telephony_plivo.py`, `app/routers/telephony_twilio.py`

---

## 65. Candidate Resume Given to the LLM With No Instruction to Use It

**Problem:**
Candidate resume text was dumped into the call prompt with no instruction telling the model to actually use it — it would guess at background questions instead of reading the resume it already had.

**Fix:**
Added an instruction telling the LLM to answer resume/background questions from that text (knowledge-base documents already had the equivalent instruction at the agent level; this closed the same gap for the per-candidate resume).
**Files:** `app/telephony/shared.py`

---

## 66. Switching Twilio/Plivo Server Base URL (e.g. ngrok) Overwrote the Only Saved One

**Problem:**
Account settings stored a single `webhook_base_url`. Switching between a real domain and an ngrok tunnel (or between ngrok tunnels) meant re-typing the URL every time — the previous one was simply gone.

**Fix:**
Added a `webhook_base_urls` column (migration `0024`) storing multiple saved URLs with one marked active; the existing `webhook_base_url` column still drives every real call path unchanged. Frontend settings UI updated to manage the list and switch the active one.
**Files:** `app/telephony/plivo/user_plivo_settings_repo.py`, `app/telephony/twilio/user_twilio_settings_repo.py`, `migrations/versions/0024_multiple_webhook_base_urls.py`, `frontend/app/agents/page.tsx`, `frontend/lib/api.ts`

---

## 67. Call-Behavior Instructions Hardcoded Where the Product User Couldn't See or Edit Them

**Problem:**
The wait-for-confirmation (#63) and resume-usage (#65) instructions were hardcoded into `app/telephony/shared.py`'s per-call context — invisible and unchangeable from the agent settings UI.

**Fix:**
Moved both into `_DEFAULT_HR_INSTRUCTIONS` (the agent's own editable `hr_instructions` field), leaving `shared.py`'s per-call context holding only facts that genuinely can't live in a static template (candidate name, schedule time, resume content, notes, questions). Default for newly seeded agents only — existing rows keep their current `hr_instructions`.
**Files:** `app/agents/agent_repo.py`, `app/telephony/shared.py`

---

## 68. `ELEVENLABS_VOICE_ID` Override Undocumented

**Problem:**
Config already read `ELEVENLABS_VOICE_ID` from the environment as an override for the default TTS voice, but it wasn't listed in `.env.example` — no way to discover it existed without reading the source.

**Fix:**
Added it to `.env.example`.
**Files:** `.env.example`

---

## 69. Per-Turn Call Latency — Several Fixed Costs Stacking on Every Single Turn

**Problem:**
The caller-stops-speaking-to-first-agent-audio critical path carried a handful of fixed costs on every turn, across Twilio, Plivo, and the browser pipeline: `StreamingTranscriber.finish()` blocked on Deepgram's socket close, which has a 0.5s sleep built into the SDK; any agent with a tool configured (KB, interview, campaign, meeting booking, webhook) waited for the *entire* LLM completion before speaking a word, because tool-aware completions weren't streamed; a new ElevenLabs/OpenAI/Azure TTS client — and a new TLS handshake — was built per sentence instead of reused; and call setup ran its conversation-insert, functions lookup, KB-eligibility check, and meeting-tools check one after another instead of concurrently.

**Fix:**
- STT: `finish()` sends Deepgram a `Finalize` message and returns as soon as the flushed transcript arrives; the actual socket close happens in the background.
- LLM: new `detect_tool_call_stream` streams tool-aware completions sentence-by-sentence for all three pipelines; `AsyncOpenAI` clients are cached per provider/key/base_url instead of rebuilt per call.
- TTS: ElevenLabs client cached process-wide; its sync HTTP stream is consumed in a worker thread with thread-safe queue handoff instead of blocking the event loop; OpenAI TTS and Azure Speech stream audio out as generated instead of collecting the full response first.
- Call setup: the conversation insert, functions lookup, KB eligibility, and meeting-tools check in `_setup_call` now run concurrently in both phone routers; admin config serves stale values and refreshes in the background instead of blocking on a Supabase read each time.
**Files:** `app/voice/deepgram_service.py`, `app/voice/llm_service.py`, `app/voice/elevenlabs_service.py`, `app/voice/openai_tts_service.py`, `app/voice/azure_speech_tts_service.py`, `app/telephony/shared.py`, `app/routers/telephony_plivo.py`, `app/routers/telephony_twilio.py`, `app/routers/websockets.py`, `app/main.py`, `app/admin/admin_config_service.py`, `config.yaml`

---

## 70. No-Tools Phone Calls Silently Dropped the Phone-Specific System Prompt

**Problem:**
On a phone call for an agent with no tools configured, the LLM lost the phone-specific system context — agent name, HR interview/campaign context — that the tool-enabled path already built and used correctly.

**Root Cause:**
The no-tools branch passed `agent.get("system_prompt")` straight through as the override, instead of `base_system` — the already-assembled string that bakes in the phone context. The tools branch used `base_system` correctly; only the no-tools branch had this gap.

**Fix:**
Pass `base_system` on the no-tools path too, matching the tools path.
**Files:** `app/routers/telephony_plivo.py`, `app/routers/telephony_twilio.py`

---

## 71. HR Interview Retry Re-Asked Questions Already Answered on a Prior Attempt

**Problem:**
When a failed/dropped interview call was retried, the candidate could be asked a question they had already answered on the earlier attempt.

**Root Cause:**
The question list rebuilt for a retry call included every configured question, with no check against what had already been recorded in a prior attempt.

**Fix:**
Skip questions already recorded in a prior call attempt when rebuilding the question list on retry.
**Files:** `app/telephony/shared.py`

---

## 72. Cancelling a Scheduled Call Could Fail Silently and Keep Retrying Forever

**Problem:**
If the cancel-call request to the backend failed, the frontend gave no error — the call looked cancelled to the HR user, but the scheduler kept finding it and retrying.

**Fix:**
Added error/success feedback to the frontend cancel action so a failed cancel is visible instead of leaving a scheduled call silently pending.
**Files:** `frontend/app/agents/page.tsx`

---

## 73. CI Test Pollution — `hr_campaign_repo`/`hr_repo` Froze a Mocked `get_supabase`

**Problem:**
Campaign/interview tests failed intermittently in CI depending on test run order, with a `KeyError` on some table name a test's fake Supabase client never registered.

**Root Cause:**
Both modules imported `get_supabase` by name at module level. If their first import in a test run happened to land inside another test's `app.core.database.get_supabase` mock (as in `test_call_reconciliation`'s local-import call path), they permanently bound to that test's fake client for the rest of the suite.

**Fix:**
Switched both to a local import on each call, matching the existing `admin_config_service` fix, so they always read the current, properly-scoped `get_supabase`.
**Files:** `app/hr_campaigns/hr_campaign_repo.py`, `app/hr_campaigns/hr_repo.py`

---

## 74. Plivo Inbound Calls Failing Agent Lookup Due to Phone Number Format

**Problem:**
Every Plivo inbound call played "This number is not connected to an agent" even when the number was correctly assigned to an agent.

**Root Cause:**
Plivo's inbound voice webhook posts `"To"` without a leading `+`, but numbers are stored in E.164 with one — `get_number_by_phone`'s exact match never hit.

**Fix:**
Match against both the `+` and bare forms of the number.
**Files:** `app/telephony/plivo/plivo_repo.py`

---

## 75. Plivo Inbound Calls Getting a Busy Tone From Unescaped XML

**Problem:**
Plivo inbound calls failed with a busy tone, even though the webhook returned 200 with the agent correctly resolved — no voice, no greeting, nothing.

**Root Cause:**
`plivo_voice_webhook` built the Stream URL for plain inbound calls by joining the `to`/`from` query params with a raw `&`, embedded directly as XML text content. A bare `&` is not valid XML, so Plivo's parser rejected the response outright. The campaign/interview Stream URLs never hit this because they only ever carry one query param.

**Fix:**
Escape the URL to `&amp;` before embedding it in the XML response.
**Files:** `app/routers/telephony_plivo.py`

---

## 76. Zero Webhook-Level Test Coverage on Plivo Let #74/#75 Ship Without a Regression Guard

**Problem:**
Twilio's inbound/interview-outbound/campaign-outbound voice webhooks already had full test coverage proving they work the same for any custom agent, not just the built-in templates. Plivo's equivalent webhooks (`/plivo/voice`, `/plivo/outbound/voice`, `/plivo/campaign/outbound/voice`) had none at all — bugs #74 and #75 could only have been caught by a live phone call.

**Fix:**
Added `tests/test_plivo_voice_webhooks.py` (9 tests) covering inbound, interview-outbound, and campaign-outbound webhook construction and fallback paths, including a dedicated regression guard for the #75 ampersand-escaping bug, each run against a generic custom agent rather than "Meeting Assistant" to prove no agent-name special-casing exists anywhere in the routing path. Also extended `tests/test_telephony_shared.py` with inbound call-routing tests confirming `resolve_call_context` is purely agent-id-driven.
**Files:** `tests/test_plivo_voice_webhooks.py`, `tests/test_telephony_shared.py`

---

## 77. No Way to Turn the Turn-Taking Heuristic Off, and It Missed Callers Trailing Off Mid-Number

**Problem:**
The unfinished-turn heuristic (added in #49) had no admin control — if it ever misbehaved for a particular setup, the only fix was a code change. It also didn't catch a caller trailing off mid-number (a phone number, a time, a price split across partials by a natural pause), so the plain silence timer could cut them off there too.

**Fix:**
Added a `turn_heuristic_enabled` admin config flag (default on) that Twilio and Plivo both read live; added a read-only `/api/admin/turn-heuristics` endpoint exposing the actual filler/connector/number word lists so the admin panel never hardcodes a second copy that could drift from the code; extended `sounds_unfinished()` to also flag a trailing bare number.
**Files:** `app/admin/admin_config_service.py`, `app/routers/admin.py`, `app/voice/turn_heuristics.py`, `app/routers/telephony_plivo.py`, `app/routers/telephony_twilio.py`

---

## 78. Meeting Booking Closed Too Early by Default, and Accepted Times Off the Real Slot Grid

**Problem:**
New agents defaulted to a 5pm booking close, earlier than most people want for a bookable call window. Separately, `book_meeting`/`reschedule_meeting` would accept any time string the model proposed, including a time `check_availability` would never have actually offered — outside business hours, or off the half-hour grid.

**Fix:**
Moved `booking_hours_end`'s default from `17:00` to `21:00` (migration `0031`, with a backfill for existing agents still sitting on the old default — any agent that deliberately set a different closing time, including one that happens to also be `17:00`, keeps it). Added `_slot_is_on_grid()` and used it to reject `book_meeting`/`reschedule_meeting` for a time that isn't actually a valid slot, telling the model to call `check_availability` and offer a real one instead of silently booking the stray time.
**Files:** `app/agents/agent_repo.py`, `app/integrations/meeting_tools.py`, `migrations/versions/0031_booking_hours_default_9pm.py`

---

## 79. `check_availability` Called Again After the Caller Already Picked a Time

**Problem:**
Once a caller picked one of the times the agent had just offered, the agent would re-check availability and read out a fresh list of alternatives instead of locking in the answer — sounded like it forgot what was just said, and cost an extra calendar round trip and LLM turn per re-check.

**Root Cause:**
The `check_availability` tool description told the model to call it "before offering or confirming any meeting time", with no exception for a time already offered in the same conversation.

**Fix:**
Tightened the tool description and the booking flow instructions: call `check_availability` once per day, and once the caller names one of the already-offered times, confirm it in one short sentence and move on — never re-check or re-list alternatives unless the caller asks for a different day.
**Files:** `app/integrations/meeting_tools.py`

---

## 80. Global Toggle for Spoken Filler Phrases

**Problem:**
The delay-gated filler phrase (see #61/#64) had no admin control — every agent, on every call (phone and browser), always got it with no way to turn it off account-wide.

**Fix:**
Added an admin-controlled toggle (mirroring the turn-taking heuristic pattern from #77) that gates filler phrases uniformly at call start for every agent.
**Files:** `app/admin/admin_config_service.py`, `app/routers/admin.py`, `app/routers/telephony_plivo.py`, `app/routers/telephony_twilio.py`, `app/routers/websockets.py`

---

*The entries below (81+) were found while auditing the full commit history for issues that had never been logged here at all — not just ones from a single session.*

---

## 81. Three Separate CI-Only Failures: Stale Embedding Mocks, Admin Config Fragility, Flaky Fixed Sleeps

**Problem:**
RAG/doc-ingestion tests still patched the old direct OpenAI client, but embedding calls had moved behind a shared `app.core.embeddings.embed()` helper — the mocks silently did nothing. Separately, `admin_config_service._load()` raised on any transient Supabase hiccup instead of falling back to the known-good defaults, aborting every call (every pipeline checks `voice_pipeline` at call setup) on a momentary blip. And several `test_twilio_stream_pipeline.py` tests gated on a fixed `time.sleep()` for a background task instead of polling, flaking on a slower CI runner.

**Fix:**
Repointed the stale mocks to the real embedding client; made `_load()` catch and fall back to `DEFAULTS` instead of raising; replaced the fixed sleeps with the existing `_wait_until()` poll helper used elsewhere in the file.
**Files:** `tests/test_agents.py`, `tests/test_services.py`, `app/admin/admin_config_service.py`, `tests/test_twilio_stream_pipeline.py`

---

## 82. Filler Phrases Were Always Random, Never Matched the Situation

**Problem:**
The filler phrase played before a slow tool call/RAG lookup was picked at random from the agent's list every time, regardless of whether the caller had just asked a question or just given information — and a short list could repeat the exact same phrase back-to-back.

**Fix:**
Classify the caller's last message (question / info given / neither) with a cheap keyword check (no extra LLM call, since the filler must be picked before the real reply exists) and pick a filler that fits. `filler_phrases` lines can now be scoped with a `"question:"`/`"info:"`/`"default:"` prefix — unprefixed lines still work as before. Also tracks the last filler actually spoken per call and avoids repeating it back-to-back when the category has more than one option.
**Files:** `app/voice/llm_service.py`, `app/routers/telephony_plivo.py`, `app/routers/telephony_twilio.py`, `app/routers/websockets.py`

---

## 83. Stale Pooled DB Connection Silently Dropped the Final Message of a Call

**Problem:**
On some calls, the very last message (assistant or user) never made it into the database — no error, no exception, just missing.

**Root Cause:**
A pooled Supabase connection that the remote host had already closed was reused for the final write; the request failed with `httpx.TransportError`, uncaught.

**Fix:**
Retry DB writes/reads once on a transient connection error, resetting the cached Supabase client first. Also logs exceptions from the fire-and-forget greeting/`run_pipeline` task instead of letting them disappear as "Task exception was never retrieved".
**Files:** `app/conversations/message_repo.py`, `app/core/database.py`

---

## 84. Every Phone Turn Paid for an Embedding + pgvector Query Regardless of Relevance

**Problem:**
The Cascade/Classic/Realtime phone pipelines ran RAG/KB retrieval eagerly on every single turn, even turns with nothing to search for — an embedding call plus a pgvector query per turn, whether or not it was relevant.

**Fix:**
Replaced eager per-turn retrieval with an on-demand `search_knowledge_base` tool the model calls only when it actually needs it. Offering that tool is itself gated: with one KB document selected, any non-empty `kb_instructions` is enough; with two or more, a document is only searchable if `kb_instructions` actually names it. This is the origin of the document-naming rule that the frontend's auto-suggested KB instructions (Frontend #21) exists to satisfy.
**Files:** `app/telephony/shared.py`, `app/routers/telephony_plivo.py`, `app/routers/telephony_twilio.py`

---

## 85. Candidates Asked for a Phone Number They Were Already Being Called On

**Problem:**
On outbound HR calls, the agent would ask the candidate for their phone number even though the call was already connected to that exact number.

**Fix:**
Inject the number being dialed into the call context so the agent knows it already has it, instead of asking.
**Files:** `app/telephony/shared.py`

---

## 86. RAG Embedding Calls Crashed Live Retrieval When the Plain OpenAI Key Was Deactivated

**Problem:**
Unlike chat completions (which already had an Azure-first/OpenAI-fallback path), RAG embedding calls went straight to plain OpenAI with no fallback — deactivating that key crashed retrieval on every live call that needed it.

**Fix:**
Route embedding calls through Azure OpenAI first via a new `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, falling back to plain OpenAI only if Azure is unconfigured or fails.
**Files:** `app/agents/agent_rag_service.py`

---

## 87. Browser Voice Calls Played a Filler Phrase on Every Tool Call, Even Instant Ones

**Problem:**
In the browser WebSocket pipeline, a filler phrase played unconditionally before every tool call, even when the webhook or lookup responded instantly — the same class of bug fixed for the phone pipelines in #64.

**Fix:**
A filler now only fires if nothing real has been said within `RETRIEVAL_FILLER_DELAY_SECONDS` (1.2s) of the lookup/LLM round-trip starting, and is cancelled as soon as real speech begins — mirroring the phone pipelines' existing behavior.
**Files:** `app/routers/websockets.py`

---

## 88. `/admin` Served the Agents App's HTML Instead of the Admin Panel

**Problem:**
Visiting `/admin` loaded the Agents SPA, which then redirected unauthenticated visitors to `/signin` — the actual admin panel build was never reachable at all. Found while QA-testing the admin portal end-to-end in a real browser.

**Root Cause:**
Copy-paste bug: the `/admin` and `/admin/` routes pointed at `frontend/out/agents` instead of `frontend/out/admin`.

**Fix:**
Pointed both routes at the correct build directory.
**Files:** `app/main.py`

---

## 89. Startup Prewarm Logged a Scary 401 by Hitting the Wrong OpenAI Account

**Problem:**
The startup prewarm ping hit plain OpenAI directly and logged a 401 error whenever that key was deactivated — even though Azure OpenAI (the actual primary provider for live LLM calls) was working fine.

**Fix:**
Prewarm Azure OpenAI instead of plain OpenAI at startup.
**Files:** `app/main.py`

---

## 90. `check_availability` Ignored Afternoon-Only Requests

**Problem:**
Found in live testing: asking for an afternoon slot specifically didn't reliably surface afternoon availability — a free morning could fill the whole 5-slot-capped answer, so the agent wrongly told the caller the afternoon was unavailable.

**Root Cause:**
The tool had no way to raise the start of the day's scan, so the slot cap was always filled from the start of business hours first.

**Fix:**
Added an `after` argument (HH:MM) the model sets to `12:00`/`15:00` for an afternoon/late-afternoon request, raising the scan's start time for that day; a bad value just falls back to the normal booking hours.
**Files:** `app/integrations/meeting_tools.py`

---

## 91. Realtime Phone Bridge Added a Duplicate `search_knowledge_base` Tool

**Problem:**
On the Realtime voice pipeline, a call could end up with two identical `search_knowledge_base` tool entries.

**Root Cause:**
The Realtime bridge added its own copy without checking whether `_setup_call` had already added one.

**Fix:**
Skip adding it again if `_setup_call` already did.
**Files:** `app/voice/realtime_service.py`, `app/telephony/shared.py`

---

## 92. A Paid-Tier-Only ElevenLabs Voice Failed Every Call With No Audio and No Visible Error

**Problem:**
If the configured ElevenLabs voice was outside the connected account's actual plan (e.g. a paid-tier library voice on a free account), every call using TTS failed with a 402 from ElevenLabs — silently, with no audio ever reaching the caller, even though a transcript still got recorded as if everything had worked.

**Root Cause:**
The voice was only settable via the `ELEVENLABS_VOICE_ID` env var, with nothing checking whether that voice was actually usable on the connected account's plan.

**Fix:**
Added `GET /api/admin/elevenlabs-voices`, listing the account's actual voices with a `free` flag; added a live `tts_elevenlabs_voice_id` admin config key (empty = fall back to the env default), read by both the browser/WS and phone TTS call sites; admin UI voice picker defaults to free voices with an audio preview and a "show all" toggle for paid-tier ones.
**Files:** `app/routers/admin.py`, `app/admin/admin_config_service.py`, `frontend/app/admin/page.tsx`

---

## 93. `duplicate_agent` Silently Dropped `hr_instructions` and `selected_kb_doc_ids`

**Problem:**
Duplicating an agent lost its "HR scheduler" instructions box and its selected knowledge-base documents — the copy looked the same in most fields but silently reset these two.

**Root Cause:**
`duplicate_agent` whitelist-copies specific fields from the source agent; `hr_instructions` and `selected_kb_doc_ids` were real columns it simply never included in that list.

**Fix:**
Added both fields to the copy. Covered by a direct unit test.
**Files:** `app/agents/agent_repo.py`, `tests/test_agents.py`

---

## 94. Agents Had No Way to End a Call Themselves

**Problem:**
Once a phone conversation had genuinely concluded, the agent had no way to hang up - it could only keep talking or wait for the caller to hang up first, on every pipeline (Twilio, Plivo, and the Realtime bridge).

**Fix:**
Added a built-in `end_call` tool the model can call once a conversation has genuinely finished, gated by an admin-level switch and a per-agent `end_call_tool_enabled` toggle (on by default, with an "Add back"/"Remove" control on the agent page). A grace period lets the caller speak again and cancel the hangup before it actually happens. Wired into Twilio, Plivo, and the Realtime bridge.
**Files:** `app/routers/telephony_twilio.py`, `app/routers/telephony_plivo.py`, `app/routers/websockets.py`, `app/telephony/twilio/twilio_repo.py`, `app/telephony/plivo/plivo_repo.py`, `app/telephony/shared.py`, `app/voice/realtime_service.py`, `app/agents/agent_repo.py`, `app/routers/agents.py`, `migrations/versions/0032_agent_end_call_toggle.py`, `frontend/app/agents/page.tsx`

---

## 95. Realtime Voice Pipeline Had No Language Pin, Could Drift Into the Caller's Language Mid-Call

**Problem:**
`input_audio_transcription` had no language hint, so Whisper auto-detected the caller's spoken language per utterance, and the session instructions gave the model no language guidance either - a call could silently drift out of English.

**Fix:**
Pinned transcription to `en` and added an instruction telling the model to always respond in English unless the caller explicitly asks to switch.
**Files:** `app/routers/websockets.py`, `app/telephony/shared.py`, `app/voice/realtime_service.py`

---

## 96. Cascade Pipeline Followed an STT Mis-Transcription Into Hindi/Marathi

**Problem:**
Reproduced via a real browser call: the Azure/OpenAI transcriber occasionally transliterates Indian-accented English into Devanagari script even with `language=en` set, and the cascade LLM prompt had no language guard, so the model followed the mis-transcription into Hindi/Marathi instead of staying in English.

**Fix:**
Added a prompt hint biasing transcription toward English, plus the same "always respond in English unless asked" guard already used on the Realtime pipeline (see #95).
**Files:** `app/voice/llm_service.py`, `app/voice/openai_whisper_service.py`

---

## 97. No Way for Users to Report an Issue or Share Feedback

**Problem:**
If something went wrong in a call or in the console, a user had no way to tell anyone - there was no feedback channel at all, and admins had no visibility into what users were running into.

**Fix:**
Added `POST /api/feedback` (optionally tagged to an agent) and `GET /api/feedback/mine`, backed by a new `feedback` table; a "Report an issue / Feedback" entry in the console's account menu opens a modal to submit one; admins review, resolve, and reopen reports from a new Feedback tab in the admin portal.
**Files:** `app/feedback/feedback_repo.py`, `app/routers/feedback.py`, `app/routers/admin.py`, `app/main.py`, `migrations/versions/0033_agent_feedback.py`, `frontend/app/agents/page.tsx`, `frontend/app/admin/page.tsx`, `frontend/lib/api.ts`

---

## 98. No Per-Agent Control Over Answer Length or Depth

**Problem:**
Agents had no way to match how much they said to what was actually asked - a narrow question could get a long answer dumping everything the agent knew, with no clarifying question when the caller's request was ambiguous.

**Fix:**
Added `smart_response_enabled` (on by default), which instructs the model to answer at the depth the question actually calls for, offer a relevant follow-up only when there's a natural one, and ask a short clarifying question instead of guessing on ambiguous requests. Added a per-agent toggle in the UI.
**Files:** `app/telephony/shared.py`, `app/routers/websockets.py`, `app/agents/agent_repo.py`, `app/routers/agents.py`, `migrations/versions/0034_agent_smart_response.py`, `frontend/app/agents/page.tsx`, `frontend/lib/api.ts`
