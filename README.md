# Lumen Agent Backend

Real-time voice AI backend. The user speaks into the browser, audio streams over WebSocket, the server transcribes it (Deepgram), retrieves relevant context from a knowledge base (RAG via Supabase + pgvector), generates a reply (OpenAI GPT-4o), and streams audio back (ElevenLabs TTS).

---

## Architecture — end to end

```
Browser
  │
  │  WebSocket (raw PCM audio chunks, 16 kHz mono int16)
  ▼
FastAPI  /ws/{token}
  │
  ├── VAD (torch RMS energy) ──► silence detected? → buffer complete utterance
  │
  ├── Deepgram nova-2 ──────────► transcript text
  │
  ├── OpenAI text-embedding-3-small ──► query vector
  │
  ├── Supabase pgvector match_documents() ──► top-3 relevant chunks
  │
  ├── OpenAI GPT-4o ────────────► reply text
  │       (system prompt includes: session history + long-term memory + RAG context)
  │
  └── ElevenLabs eleven_turbo_v2_5 ──► MP3 audio chunks streamed back over WebSocket
```

### Barge-in
While the server is speaking (SPEAKING state), incoming audio is still monitored by VAD. If the user speaks, the TTS stream is cancelled immediately and the server goes back to LISTENING.

### Long-term memory
Every 10 messages, GPT-4o-mini summarises the conversation and upserts a `user_memory` row per user. This summary is injected into every future system prompt so the agent remembers things across sessions.

### Document ingestion (RAG)
`.md` files are uploaded to Supabase Storage. The `/api/ingest-all` endpoint (or the `/webhook/new-document` webhook) downloads each file, splits it by heading, embeds each chunk with `text-embedding-3-small`, and stores the vectors in the `documents` table. The `match_documents` SQL function does cosine similarity search at query time.

---

## Tech stack

| Layer | Service / Library |
|---|---|
| API framework | FastAPI + uvicorn |
| Auth | Supabase Auth (JWT HS256 / ES256) |
| Database | Supabase (PostgreSQL + pgvector) |
| STT | Deepgram nova-2 |
| LLM | OpenAI GPT-4o |
| TTS | ElevenLabs eleven_turbo_v2_5 |
| VAD | Torch RMS energy |
| Embeddings | OpenAI text-embedding-3-small |
| Package manager | uv |
| DB migrations | Alembic |

---

## Project structure

```
lumen_agent_backend/
├── app/
│   ├── main.py              # FastAPI app, all routes, WebSocket pipeline
│   ├── config.py            # Single source of config — reads .env + config.yaml
│   ├── models.py            # SQLAlchemy models (schema source of truth)
│   ├── database.py          # Supabase client singleton
│   ├── websocket_manager.py # Connection manager, session state
│   ├── vad_service.py       # Voice activity detection (RMS energy)
│   ├── deepgram_service.py  # Speech-to-text
│   ├── llm_service.py       # GPT-4o response + memory summarisation
│   ├── elevenlabs_service.py# Text-to-speech streaming
│   ├── rag_service.py       # Embed query + retrieve chunks
│   ├── doc_processor.py     # Download .md → chunk → embed → store
│   ├── md_extractor.py      # Parse markdown into heading-based chunks
│   ├── conversation_repo.py # DB ops for conversations table
│   ├── message_repo.py      # DB ops for messages table
│   ├── memory_repo.py       # DB ops for user_memory table
│   └── document_repo.py     # DB ops for documents table (vector search)
├── migrations/
│   ├── env.py               # Alembic env — reads DATABASE_URL, imports models
│   └── versions/
│       └── 0001_initial_schema.py  # Creates all tables + pgvector + match_documents fn
├── config.yaml              # Non-secret tunables (models, timeouts, VAD, CORS...)
├── .env                     # Secrets — NOT committed to git
├── .env.example             # Template — commit this, fill values locally / on AWS
├── pyproject.toml           # Dependencies
├── uv.lock                  # Pinned dependency lock file
├── alembic.ini              # Alembic config
└── Dockerfile               # Production container
```

---

## Configuration

### What goes in `.env` (secrets)

| Variable | Where to find it |
|---|---|
| `DATABASE_URL` | Supabase → Project Settings → Database → URI (transaction pooler) |
| `SUPABASE_URL` | Supabase → Project Settings → API → Project URL |
| `SUPABASE_ANON_KEY` | Supabase → Project Settings → API → anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Project Settings → API → service_role key |
| `SUPABASE_JWT_SECRET` | Supabase → Project Settings → API → JWT Secret |
| `OPENAI_API_KEY` | platform.openai.com → API keys |
| `DEEPGRAM_API_KEY` | console.deepgram.com → API keys |
| `ELEVENLABS_API_KEY` | elevenlabs.io → Profile → API key |
| `WEBHOOK_SECRET` | Any string you choose — set same value in Supabase webhook header |

### What goes in `config.yaml` (tunables, safe to commit)

All non-secret settings: model names, timeouts, VAD sensitivity, TTS voice settings, CORS origins, storage bucket name, max concurrent users, RAG top-k. Edit this file to change behaviour without touching code.

---

## Local setup (one-time)

**Requirements:** Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/)

```bash
# 1. Clone
git clone <repo-url>
cd lumen_agent_backend

# 2. Copy and fill secrets
cp .env.example .env
# Open .env and fill in all the API keys

# 3. Run database migrations
uv run alembic upgrade head

# 4. Upload knowledge base documents
#    Put your .md files in Supabase Storage bucket "documents", then:
uv run uvicorn app.main:app --host 0.0.0.0 --port 8004 --reload
# Call POST /api/ingest-all-dev (no auth) once to index the documents

# 5. That's it — the server is running
```

`uv run` automatically creates a virtual environment and installs all dependencies from `uv.lock` on first run. No `pip install`, no manual venv.

---

## Running on AWS (EC2 / ECS)

### Option A — Docker (recommended)

```bash
# Build
docker build -t lumen-agent-backend .

# Run — pass secrets as environment variables
docker run -d \
  -p 8004:8004 \
  -e DATABASE_URL="postgresql://..." \
  -e SUPABASE_URL="https://..." \
  -e SUPABASE_ANON_KEY="..." \
  -e SUPABASE_SERVICE_ROLE_KEY="..." \
  -e SUPABASE_JWT_SECRET="..." \
  -e OPENAI_API_KEY="..." \
  -e DEEPGRAM_API_KEY="..." \
  -e ELEVENLABS_API_KEY="..." \
  lumen-agent-backend
```

On AWS ECS/Fargate, pass the same env vars via **Task Definition environment variables** or pull them from **AWS Secrets Manager / Parameter Store**.

### Option B — EC2 directly (no Docker)

```bash
# Install uv on the EC2 instance
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone repo
git clone <repo-url>
cd lumen_agent_backend

# Create .env with all secrets
nano .env

# Run migrations once
uv run alembic upgrade head

# Start server (use systemd or screen/tmux to keep it running)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8004
```

---

## Database migrations (Alembic)

The `DATABASE_URL` in `.env` must point directly to PostgreSQL (not the Supabase REST API).

```bash
# Apply all pending migrations (run this on every deploy)
uv run alembic upgrade head

# Create a new migration after editing app/models.py
uv run alembic revision --autogenerate -m "describe your change"
uv run alembic upgrade head

# Roll back one migration
uv run alembic downgrade -1

# See migration history
uv run alembic history

# See current DB state
uv run alembic current
```

---

## API reference

### Auth

| Method | Path | Description |
|---|---|---|
| POST | `/auth/signup` | Register with email + password |
| POST | `/auth/signin` | Sign in, returns access + refresh tokens |
| POST | `/auth/refresh` | Exchange refresh token for new access token |
| POST | `/auth/signout` | Sign out (invalidates session) |

### Protected REST (requires `Authorization: Bearer <token>`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/me` | Current user info |
| GET | `/api/conversations` | List user's conversations |
| GET | `/api/conversations/{id}/messages` | Messages in a conversation |
| GET | `/api/health` | Health check |
| POST | `/api/ingest-all` | Re-index all `.md` files from Supabase Storage |

### Dev only

| Method | Path | Description |
|---|---|---|
| POST | `/api/ingest-all-dev` | Same as `/api/ingest-all` but no auth (remove in prod) |

### Webhook

| Method | Path | Description |
|---|---|---|
| POST | `/webhook/new-document` | Called by Supabase on Storage INSERT — auto-ingests new `.md` files |

### WebSocket

```
ws://host:8004/ws/{supabase_jwt_token}
```

**Client → Server messages (binary):** Raw PCM audio, 16 kHz, mono, int16

**Client → Server messages (JSON):**
```json
{ "type": "sample_rate", "value": 16000 }
{ "type": "set_sensitivity", "value": 0.3 }
```

**Server → Client messages (JSON):**
```json
{ "type": "ready", "conversation_id": "uuid" }
{ "type": "state", "state": "LISTENING" }
{ "type": "state", "state": "PROCESSING" }
{ "type": "state", "state": "SPEAKING" }
{ "type": "transcript", "text": "what the user said" }
{ "type": "reply", "text": "what the agent replied" }
{ "type": "audio_end" }
{ "type": "stop_audio" }
```

**Server → Client (binary):** MP3 audio chunks (ElevenLabs TTS stream)

---

## WebSocket state machine

```
LISTENING ──(speech detected)──► PROCESSING ──(reply ready)──► SPEAKING
    ▲                                                               │
    └───────────────(audio_end sent)────────────────────────────────┘
    ▲
    └───(barge-in: user speaks while SPEAKING)── cancel TTS ──────►┘
```

---

## Document ingestion flow

```
1. Upload file.md to Supabase Storage bucket "documents"
2. Supabase webhook fires POST /webhook/new-document
3. Server downloads file.md from Storage
4. md_extractor splits it into chunks by ## heading
5. Each chunk is embedded with OpenAI text-embedding-3-small (1536 dims)
6. Chunks saved to documents table with pgvector column
7. At query time: match_documents() finds top-3 cosine-similar chunks
```

---

## Environment variables quick reference

```bash
# Required — all must be set or the server will not start
DATABASE_URL
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
SUPABASE_JWT_SECRET
OPENAI_API_KEY
DEEPGRAM_API_KEY
ELEVENLABS_API_KEY

# Optional
WEBHOOK_SECRET          # Shared secret for Supabase webhook validation
ELEVENLABS_VOICE_ID     # Override the voice set in config.yaml
```
