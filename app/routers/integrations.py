"""Google Calendar OAuth connection routes.

One calendar connection per account (not per agent) - every agent the user
enables meeting booking on books into the same connected calendar. The
callback arrives from Google's redirect with no Authorization header, so the
user identity rides in a short-lived signed `state` JWT minted by /connect.
"""
import asyncio
import os
import time

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth.dependencies import get_current_user
from app.core.config import SUPABASE_JWT_SECRET

router = APIRouter(tags=["integrations"])

_STATE_PURPOSE = "google_calendar_oauth"
_STATE_TTL_S = 600


def _redirect_uri(request: Request) -> str:
    configured = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "")
    if configured:
        return configured
    return str(request.base_url).rstrip("/") + "/api/integrations/google/callback"


@router.get("/api/integrations/google/connect")
async def google_connect(request: Request, current_user: dict = Depends(get_current_user)):
    from app.integrations.google_calendar_service import GoogleCalendarNotConfigured, build_auth_url
    state = jwt.encode(
        {"sub": current_user["sub"], "purpose": _STATE_PURPOSE, "exp": int(time.time()) + _STATE_TTL_S},
        SUPABASE_JWT_SECRET, algorithm="HS256",
    )
    try:
        return {"auth_url": build_auth_url(state, _redirect_uri(request))}
    except GoogleCalendarNotConfigured:
        raise HTTPException(status_code=503, detail="Google Calendar integration is not configured on this server")


@router.get("/api/integrations/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    # User-facing redirect target - the agents page shows the result banner.
    if error or not code:
        return RedirectResponse(url="/agents?google_calendar=denied")
    try:
        payload = jwt.decode(state, SUPABASE_JWT_SECRET, algorithms=["HS256"])
        if payload.get("purpose") != _STATE_PURPOSE or not payload.get("sub"):
            raise jwt.InvalidTokenError("wrong purpose")
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    user_id = payload["sub"]

    from app.integrations.google_calendar_service import exchange_code
    from app.integrations.integrations_repo import GOOGLE_CALENDAR, upsert_integration
    try:
        tokens = await exchange_code(code, _redirect_uri(request))
        await asyncio.to_thread(
            upsert_integration, user_id, GOOGLE_CALENDAR,
            tokens["access_token"], tokens.get("refresh_token"),
            tokens.get("token_expiry"), tokens.get("connected_email"),
        )
    except Exception as e:
        print(f"[GoogleCalendar] OAuth callback failed for user {user_id}: {e}")
        return RedirectResponse(url="/agents?google_calendar=error")
    return RedirectResponse(url="/agents?google_calendar=connected")


@router.get("/api/integrations/google/status")
async def google_status(current_user: dict = Depends(get_current_user)):
    from app.integrations.integrations_repo import GOOGLE_CALENDAR, get_integration
    integration = await asyncio.to_thread(get_integration, current_user["sub"], GOOGLE_CALENDAR)
    if not integration:
        return {"connected": False, "email": None}
    return {"connected": True, "email": integration.get("connected_email")}


@router.delete("/api/integrations/google")
async def google_disconnect(current_user: dict = Depends(get_current_user)):
    from app.integrations.google_calendar_service import revoke_tokens
    from app.integrations.integrations_repo import GOOGLE_CALENDAR, delete_integration, get_integration
    integration = await asyncio.to_thread(get_integration, current_user["sub"], GOOGLE_CALENDAR)
    if integration and integration.get("refresh_token"):
        await revoke_tokens(integration["refresh_token"])
    await asyncio.to_thread(delete_integration, current_user["sub"], GOOGLE_CALENDAR)
    return {"connected": False}


@router.get("/api/meetings")
async def list_meetings(current_user: dict = Depends(get_current_user)):
    from app.integrations.meetings_repo import get_meetings
    return await asyncio.to_thread(get_meetings, current_user["sub"])
