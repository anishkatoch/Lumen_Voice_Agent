"""
Admin configuration service.
Stores provider/limit settings in the admin_config table.
In-memory cache with 5-second TTL so changes propagate to new sessions quickly.
"""
import time
from datetime import datetime, timezone
from app.database import get_supabase

_cache: dict[str, str] = {}
_cache_time: float = 0.0
CACHE_TTL = 5.0

# Defaults used when a key has no DB row
DEFAULTS: dict[str, str] = {
    "stt_provider":      "deepgram",    # deepgram | openai_whisper
    "tts_provider":      "elevenlabs",  # elevenlabs | openai_tts
    "tts_openai_voice":  "alloy",       # alloy | echo | fable | onyx | nova | shimmer
    "tts_openai_model":  "tts-1",       # tts-1 | tts-1-hd
    "llm_provider":      "openai",      # openai | groq | anthropic | ollama
    "llm_model":         "gpt-4o-mini",
    "llm_api_key":       "",            # override; empty = use env var
    "llm_base_url":      "",            # used for ollama or custom openai-compat endpoints
    "user_kb_max_mb":    "5",
    "agent_kb_max_mb":   "5",
}


def _load() -> None:
    global _cache, _cache_time
    db = get_supabase()
    rows = db.table("admin_config").select("key,value").execute().data
    _cache = {**DEFAULTS, **{r["key"]: r["value"] for r in rows}}
    _cache_time = time.monotonic()


def get_all() -> dict[str, str]:
    if time.monotonic() - _cache_time > CACHE_TTL or not _cache:
        _load()
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
