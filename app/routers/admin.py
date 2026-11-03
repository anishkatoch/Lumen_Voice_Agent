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
        "stt_provider", "tts_provider", "tts_openai_voice", "tts_openai_model",
        "llm_provider", "llm_model", "llm_api_key", "llm_base_url",
        "user_kb_max_mb", "agent_kb_max_mb",
    }
    updates = {k: str(v) for k, v in body.items() if k in ALLOWED}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid config keys provided")
    from app.admin.admin_config_service import save, get_all
    save(updates)
    return get_all()
