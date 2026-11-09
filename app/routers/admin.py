"""Admin-only routes: KB access requests and system config."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.dependencies import get_current_user

router = APIRouter(tags=["admin"])


ADMIN_EMAIL = "katochaneesh@gmail.com"

def _require_admin(current_user: dict) -> None:
    if current_user.get("email") != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Admin access required")

@router.get("/api/admin/kb-requests")
async def admin_list_kb_requests(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.agents.agent_repo import get_kb_requests
    return await asyncio.to_thread(get_kb_requests)

@router.post("/api/admin/kb-requests/{request_id}/approve")
async def admin_approve_kb_request(request_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.agents.agent_repo import resolve_kb_request
    try:
        return await asyncio.to_thread(resolve_kb_request, request_id, current_user["sub"], True)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/api/admin/kb-requests/{request_id}/reject")
async def admin_reject_kb_request(request_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.agents.agent_repo import resolve_kb_request
    try:
        return await asyncio.to_thread(resolve_kb_request, request_id, current_user["sub"], False)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/api/admin/users/{user_id}/revoke-kb")
async def admin_revoke_kb(user_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.agents.agent_repo import revoke_kb_permission
    await asyncio.to_thread(revoke_kb_permission, user_id)
    return {"status": "revoked", "user_id": user_id}

@router.get("/api/admin/kb-users")
async def admin_list_kb_users(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.agents.agent_repo import get_kb_permitted_users
    return await asyncio.to_thread(get_kb_permitted_users)

@router.get("/api/admin/is-admin")
async def check_is_admin(current_user: dict = Depends(get_current_user)):
    return {"is_admin": current_user.get("email") == ADMIN_EMAIL}

@router.get("/api/admin/config")
async def get_admin_config(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.admin.admin_config_service import get_all
    return get_all()

@router.put("/api/admin/config")
async def update_admin_config(request: Request, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    body = await request.json()
    ALLOWED = {
        "stt_provider", "tts_provider", "tts_elevenlabs_voice_id", "tts_openai_voice", "tts_openai_model",
        "llm_provider", "llm_model", "llm_api_key", "llm_base_url",
        "user_kb_max_mb", "agent_kb_max_mb",
        "voice_pipeline", "realtime_voice",
    }
    updates = {k: str(v) for k, v in body.items() if k in ALLOWED}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid config keys provided")
    from app.admin.admin_config_service import save, get_all
    save(updates)
    return get_all()

@router.get("/api/admin/status")
async def admin_system_status(current_user: dict = Depends(get_current_user)):
    """Live view of which providers are actually configured via env vars, and
    which one will really handle the next call given the current admin config
    (e.g. Azure OpenAI silently taking over as primary when llm_provider is
    "openai" - this is otherwise invisible from the Providers tab)."""
    _require_admin(current_user)
    import os
    from app.admin.admin_config_service import get_all
    from app.core.config import (
        AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT,
        OPENAI_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY,
    )

    cfg = get_all()
    azure_configured = bool(AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT)
    llm_provider = cfg.get("llm_provider", "openai")

    if llm_provider == "openai" and azure_configured:
        effective_llm = {"provider": "azure_openai", "model": AZURE_OPENAI_DEPLOYMENT,
                          "note": "Azure OpenAI is used first; plain OpenAI is only the fallback if Azure errors."}
    else:
        effective_llm = {"provider": llm_provider, "model": cfg.get("llm_model"), "note": None}

    return {
        "providers": {
            "azure_openai": {"configured": azure_configured, "endpoint": AZURE_OPENAI_ENDPOINT or None,
                              "deployment": AZURE_OPENAI_DEPLOYMENT if azure_configured else None},
            "openai":       {"configured": bool(OPENAI_API_KEY)},
            "groq":         {"configured": bool(os.environ.get("GROQ_API_KEY"))},
            "anthropic":    {"configured": bool(os.environ.get("ANTHROPIC_API_KEY") or cfg.get("llm_api_key"))},
            "deepgram":     {"configured": bool(DEEPGRAM_API_KEY)},
            "elevenlabs":   {"configured": bool(ELEVENLABS_API_KEY)},
        },
        "active_pipeline": {
            "voice_pipeline": cfg.get("voice_pipeline", "cascade"),
            # stt/tts only describe the "cascade" pipeline - openai_classic
            # always uses OpenAI for both regardless of these, and realtime
            # has neither a separate STT nor TTS step at all.
            "stt": "openai_whisper" if cfg.get("voice_pipeline") == "openai_classic" else cfg.get("stt_provider"),
            "tts": "openai_tts" if cfg.get("voice_pipeline") == "openai_classic" else cfg.get("tts_provider"),
            "llm": effective_llm,
            "realtime_voice": cfg.get("realtime_voice"),
        },
    }

@router.get("/api/admin/elevenlabs-voices")
async def admin_list_elevenlabs_voices(current_user: dict = Depends(get_current_user)):
    """Voices this ElevenLabs account can actually use right now - premade
    voices plus anything added to the account's Voice Library - so the admin
    can only ever pick a voice that won't fail mid-call for being outside the
    current plan."""
    _require_admin(current_user)
    from app.core.config import ELEVENLABS_API_KEY
    if not ELEVENLABS_API_KEY:
        raise HTTPException(status_code=400, detail="ELEVENLABS_API_KEY is not configured")

    def _fetch():
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        resp = client.voices.get_all()
        voices = []
        for v in resp.voices:
            # available_for_tiers empty/None means no subscription-tier
            # restriction - usable on any plan including free.
            free = not v.available_for_tiers
            voices.append({
                "voice_id": v.voice_id,
                "name": v.name,
                "category": v.category,
                "preview_url": v.preview_url,
                "free": free,
            })
        return voices

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ElevenLabs error: {e}")

@router.post("/api/admin/test-llm")
async def admin_test_llm(current_user: dict = Depends(get_current_user)):
    """Fires one real, minimal completion through the currently configured LLM
    chain (same resolution order a live call would use) and reports whether it
    actually works - this is what answers "I can't tell if my model is set up
    right" without needing to place a real voice call."""
    _require_admin(current_user)
    from app.voice.llm_service import test_llm_connection
    return await test_llm_connection()
