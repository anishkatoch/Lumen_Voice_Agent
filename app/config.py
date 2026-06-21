import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).parent.parent
_cfg: dict = {}


def _load() -> dict:
    global _cfg
    if not _cfg:
        with open(_ROOT / "config.yaml") as f:
            _cfg = yaml.safe_load(f)
    return _cfg


def cfg(*keys: str, default=None):
    """Traverse nested config keys. cfg('llm', 'model') → config.yaml llm.model"""
    node = _load()
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, default)
        if node is None:
            return default
    return node


# ─── Secrets from .env ───────────────────────────────────────────────
DATABASE_URL            = os.environ["DATABASE_URL"]
SUPABASE_URL            = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY       = os.environ["SUPABASE_ANON_KEY"]
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_ANON_KEY"]
SUPABASE_JWT_SECRET     = os.environ.get("SUPABASE_JWT_SECRET", "")
OPENAI_API_KEY          = os.environ["OPENAI_API_KEY"]
DEEPGRAM_API_KEY        = os.environ["DEEPGRAM_API_KEY"]
ELEVENLABS_API_KEY      = os.environ["ELEVENLABS_API_KEY"]
WEBHOOK_SECRET          = os.environ.get("WEBHOOK_SECRET", "")

# ─── Non-secrets from config.yaml ────────────────────────────────────
STORAGE_BUCKET          = cfg("storage", "bucket", default="documents")

CORS_ORIGINS            = cfg("cors", "allowed_origins", default=[])

VAD_SENSITIVITY         = float(cfg("vad", "barge_in_sensitivity", default=0.2))
VAD_SILENCE_LIMIT       = int(cfg("vad", "silence_limit_chunks", default=6))
VAD_MIN_SPEECH          = int(cfg("vad", "min_speech_chunks", default=2))

LLM_MODEL               = cfg("llm", "model", default="gpt-4o")
LLM_TIMEOUT             = int(cfg("llm", "timeout_seconds", default=10))
LLM_MAX_TOKENS          = int(cfg("llm", "max_tokens", default=500))
LLM_TEMPERATURE         = float(cfg("llm", "temperature", default=0.7))
MEMORY_MODEL            = cfg("llm", "memory_model", default="gpt-4o-mini")
MEMORY_MAX_TOKENS       = int(cfg("llm", "memory_max_tokens", default=300))
MEMORY_UPDATE_EVERY     = int(cfg("llm", "memory_update_every", default=10))

STT_MODEL               = cfg("stt", "model", default="nova-2")
STT_LANGUAGE            = cfg("stt", "language", default="en-US")
STT_TIMEOUT             = int(cfg("stt", "timeout_seconds", default=8))

TTS_MODEL_ID            = cfg("tts", "model_id", default="eleven_turbo_v2_5")
TTS_VOICE_ID            = os.environ.get("ELEVENLABS_VOICE_ID") or cfg("tts", "voice_id", default="JBFqnCBsd6RMkjVDRZzb")
TTS_OUTPUT_FORMAT       = cfg("tts", "output_format", default="mp3_44100_128")
TTS_STABILITY           = float(cfg("tts", "stability", default=0.5))
TTS_SIMILARITY_BOOST    = float(cfg("tts", "similarity_boost", default=0.75))
TTS_STYLE               = float(cfg("tts", "style", default=0.0))
TTS_SPEAKER_BOOST       = bool(cfg("tts", "use_speaker_boost", default=True))

WS_MAX_USERS            = int(cfg("websocket", "max_concurrent_users", default=50))

RAG_TOP_K               = int(cfg("rag", "top_k", default=3))
RAG_EMBEDDING_MODEL     = cfg("rag", "embedding_model", default="text-embedding-3-small")
