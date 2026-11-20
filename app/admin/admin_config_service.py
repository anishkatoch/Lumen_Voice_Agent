"""
Admin configuration service.
Stores provider/limit settings in the admin_config table.
In-memory cache with 5-second TTL so changes propagate to new sessions quickly.

Stale-while-revalidate: once the cache has been filled, a stale read returns
the cached values immediately and refreshes in a background thread. get() is
called synchronously from async hot paths on every live call turn (LLM client
resolution, TTS, STT setup), where a blocking Supabase round trip used to
stall the event loop - and with it the audio receive loop of every active
call - each time the 5s TTL expired.
"""
import threading
import time
from datetime import datetime, timezone
from app.core.database import get_supabase

_cache: dict[str, str] = {}
_cache_time: float = 0.0
CACHE_TTL = 5.0
_refresh_lock = threading.Lock()
_refreshing = False

# Defaults used when a key has no DB row
DEFAULTS: dict[str, str] = {
    "stt_provider":      "deepgram",    # deepgram | openai_whisper
    "tts_provider":      "elevenlabs",  # elevenlabs | openai_tts
    "tts_elevenlabs_voice_id": "",      # empty = use ELEVENLABS_VOICE_ID env default
    "tts_openai_voice":  "alloy",       # alloy | echo | fable | onyx | nova | shimmer
    "tts_openai_model":  "tts-1",       # tts-1 | tts-1-hd
    "llm_provider":      "openai",      # openai | groq | anthropic | ollama
    "llm_model":         "gpt-4o-mini",
    "llm_api_key":       "",            # override; empty = use env var
    "llm_base_url":      "",            # used for ollama or custom openai-compat endpoints
    "user_kb_max_mb":    "5",
    "agent_kb_max_mb":   "5",
    # The three voice pipelines:
    #   cascade        - Deepgram/Whisper STT -> LLM provider above -> ElevenLabs/OpenAI TTS (per stt_provider/tts_provider)
    #   openai_classic - single-vendor cascade: OpenAI STT (gpt-4o-transcribe/Whisper) -> LLM -> OpenAI TTS, always, regardless of stt_provider/tts_provider
    #   realtime       - OpenAI Realtime API, speech-to-speech, no separate STT/LLM/TTS steps
    "voice_pipeline":    "cascade",
    "realtime_voice":    "marin",       # marin | cedar | alloy | ash | ballad | coral | echo | sage | shimmer | verse
    # Model/deployment name overrides - empty string = use the env/config.yaml
    # default (app.core.config) unchanged. Lets an admin change the model a
    # live call actually uses without a redeploy.
    "azure_openai_deployment":            "",  # overrides AZURE_OPENAI_DEPLOYMENT (the real primary chat model when Azure is configured)
    "azure_openai_embedding_deployment":  "",  # overrides AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    "azure_openai_transcribe_deployment": "",  # overrides AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT
    "stt_model":           "",          # overrides STT_MODEL (Deepgram model, e.g. nova-2)
    "tts_elevenlabs_model_id": "",      # overrides TTS_MODEL_ID (ElevenLabs model, e.g. eleven_turbo_v2_5)
    "azure_speech_voice":  "",          # overrides AZURE_SPEECH_VOICE
    "realtime_model":      "",          # overrides REALTIME_MODEL
    # Call retry on no-answer/failure - standalone HR interviews and new HR
    # campaigns' default retry settings both read these.
    "call_retry_interval_minutes": "3",
    "call_retry_max_attempts":     "5",
    # Phone calls only (Twilio/Plivo) - lets a stalled turn (caller mid-number,
    # "um", trailing connector phrase) get a short extension instead of being
    # cut off. "false" disables the extension outright, so every turn ends on
    # the plain silence timer alone. See app/voice/turn_heuristics.py.
    "turn_heuristic_enabled": "true",
}


def _load() -> None:
    global _cache, _cache_time
    try:
        db = get_supabase()
        rows = db.table("admin_config").select("key,value").execute().data
        _cache = {**DEFAULTS, **{r["key"]: r["value"] for r in rows}}
    except Exception as e:
        # A transient DB/network hiccup here must not abort every live call
        # that checks e.g. voice_pipeline mid-setup - fall back to defaults
        # and retry after the normal TTL rather than raising.
        print(f"[AdminConfig] Failed to load config, using defaults: {e}")
        _cache = dict(DEFAULTS)
    _cache_time = time.monotonic()


def _refresh_in_background() -> None:
    global _refreshing
    try:
        _load()
    finally:
        with _refresh_lock:
            _refreshing = False


def get_all() -> dict[str, str]:
    global _refreshing
    if not _cache:
        # First read in the process: nothing to serve stale, load inline.
        _load()
    elif time.monotonic() - _cache_time > CACHE_TTL:
        start = False
        with _refresh_lock:
            if not _refreshing:
                _refreshing = True
                start = True
        if start:
            threading.Thread(target=_refresh_in_background, daemon=True).start()
    return dict(_cache)


def get(key: str) -> str:
    return get_all().get(key, DEFAULTS.get(key, ""))


def save(updates: dict[str, str]) -> None:
    db = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"key": k, "value": str(v), "updated_at": now} for k, v in updates.items()]
    db.table("admin_config").upsert(rows).execute()
    # Update cache immediately so next call in the same process sees the change
    if _cache:
        _cache.update(updates)
