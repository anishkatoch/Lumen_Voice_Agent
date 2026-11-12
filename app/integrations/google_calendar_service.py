"""Google Calendar client: OAuth code exchange / token refresh, free-busy
lookup, and event creation. Used by the integrations router (OAuth flow) and
by the meeting tools on live calls.

Env vars (read at call time, not import time, so the feature is optional and
tests can monkeypatch.setenv without reimporting the module):
- GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET - from a Google Cloud OAuth client.
- GOOGLE_OAUTH_REDIRECT_URI - optional; the router falls back to building it
  from the incoming request's base URL.

All HTTP goes through httpx with short timeouts - these run inside a live
phone call where a hung request freezes the conversation (same reasoning as
PHONE_WEBHOOK_MAX_TIMEOUT_S in app/telephony/shared.py).
"""
import asyncio
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
# openid + email are included so the token response carries an id_token with
# the user's email, shown in the UI as "Connected as <email>". Without them
# Google returns no id_token at all and connected_email stays empty.
OAUTH_SCOPES = (
    "openid email "
    "https://www.googleapis.com/auth/calendar.events "
    "https://www.googleapis.com/auth/calendar.freebusy"
)

HTTP_TIMEOUT_S = 10


class GoogleCalendarNotConfigured(RuntimeError):
    """GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set on this deployment."""


class CalendarAuthError(RuntimeError):
    """The stored grant is no longer valid - the user must reconnect."""


def _client_config() -> tuple[str, str]:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise GoogleCalendarNotConfigured(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set to use calendar booking"
        )
    return client_id, client_secret


def build_auth_url(state: str, redirect_uri: str) -> str:
    client_id, _ = _client_config()
    return GOOGLE_AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        # offline + consent so Google returns a refresh_token - without
        # prompt=consent a re-connect returns no refresh_token at all and
        # the integration would silently die when the access token expires.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })


def _email_from_id_token(id_token: str | None) -> str | None:
    """Best-effort email extraction from the id_token the token endpoint
    returns. Decoded WITHOUT signature verification - acceptable only
    because the token arrived directly from Google's token endpoint over
    TLS in the same response, and the email is display-only metadata."""
    if not id_token or id_token.count(".") != 2:
        return None
    try:
        payload_b64 = id_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("email")
    except Exception:
        return None


async def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange an OAuth authorization code for tokens. Returns
    {access_token, refresh_token, token_expiry (iso), connected_email}."""
    client_id, client_secret = _client_config()
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
    if resp.status_code != 200:
        raise RuntimeError(f"Google token exchange failed ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in", 3600)))
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "token_expiry": expiry.isoformat(),
        "connected_email": _email_from_id_token(data.get("id_token")),
    }


async def _refresh_access_token(refresh_token: str) -> tuple[str, str]:
    """Returns (access_token, token_expiry_iso). Raises CalendarAuthError on
    invalid_grant (revoked/expired consent) so callers can tell the user to
    reconnect instead of retrying forever."""
    client_id, client_secret = _client_config()
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        })
    if resp.status_code != 200:
        body = resp.text[:300]
        if "invalid_grant" in body:
            raise CalendarAuthError("Google Calendar access was revoked - reconnect required")
        raise RuntimeError(f"Google token refresh failed ({resp.status_code}): {body}")
    data = resp.json()
    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in", 3600)))
    return data["access_token"], expiry.isoformat()


async def get_valid_access_token(user_id: str) -> tuple[str, str]:
    """Returns (access_token, calendar_id) for this user, refreshing the
    access token when it is missing or expires within 60 seconds."""
    from app.integrations.integrations_repo import (
        GOOGLE_CALENDAR, get_integration, update_access_token,
    )
    integration = await asyncio.to_thread(get_integration, user_id, GOOGLE_CALENDAR)
    if not integration:
        raise CalendarAuthError("No Google Calendar connected for this account")
    calendar_id = integration.get("calendar_id") or "primary"

    access_token = integration.get("access_token")
    expiry_raw = integration.get("token_expiry")
    expires_soon = True
    if access_token and expiry_raw:
        try:
            expiry = datetime.fromisoformat(str(expiry_raw).replace("Z", "+00:00"))
            expires_soon = expiry <= datetime.now(timezone.utc) + timedelta(seconds=60)
        except ValueError:
            expires_soon = True

    if not access_token or expires_soon:
        access_token, new_expiry = await _refresh_access_token(integration["refresh_token"])
        print(f"[GoogleCalendar] refreshed access token for user {user_id} (expires {new_expiry})")
        await asyncio.to_thread(
            update_access_token, user_id, GOOGLE_CALENDAR, access_token, new_expiry
        )
    return access_token, calendar_id


async def fetch_busy_intervals(
    user_id: str, time_min: datetime, time_max: datetime,
) -> list[tuple[datetime, datetime]]:
    """Free-busy lookup on the user's connected calendar. Input datetimes must
    be timezone-aware; returned intervals are timezone-aware UTC."""
    access_token, calendar_id = await get_valid_access_token(user_id)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        resp = await client.post(
            f"{CALENDAR_API}/freeBusy",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "timeMin": time_min.astimezone(timezone.utc).isoformat(),
                "timeMax": time_max.astimezone(timezone.utc).isoformat(),
                "items": [{"id": calendar_id}],
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Google freeBusy failed ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    busy_raw = (data.get("calendars", {}).get(calendar_id, {}) or {}).get("busy", [])
    intervals = []
    for b in busy_raw:
        try:
            start = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(b["end"].replace("Z", "+00:00"))
            intervals.append((start, end))
        except (KeyError, ValueError):
            continue
    return intervals


async def create_calendar_event(
    user_id: str,
    start: datetime,
    end: datetime,
    summary: str,
    description: str,
    attendee_email: str | None,
    timezone_name: str,
) -> dict:
    """Create the event (with a Google Meet link) and return Google's event
    resource. sendUpdates=all makes Google email the invite to the attendee."""
    import uuid
    access_token, calendar_id = await get_valid_access_token(user_id)
    body: dict = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
        "conferenceData": {
            "createRequest": {
                "requestId": uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    if attendee_email:
        body["attendees"] = [{"email": attendee_email}]
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        resp = await client.post(
            f"{CALENDAR_API}/calendars/{calendar_id}/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"conferenceDataVersion": 1, "sendUpdates": "all"},
            json=body,
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Google event insert failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


async def delete_calendar_event(user_id: str, event_id: str) -> None:
    """Delete (cancel) an event. sendUpdates=all makes Google email the
    cancellation to attendees. An already-deleted event (404/410) is treated
    as success - the caller's goal state is 'event gone' either way."""
    access_token, calendar_id = await get_valid_access_token(user_id)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        resp = await client.delete(
            f"{CALENDAR_API}/calendars/{calendar_id}/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"sendUpdates": "all"},
        )
    if resp.status_code not in (200, 204, 404, 410):
        raise RuntimeError(f"Google event delete failed ({resp.status_code}): {resp.text[:300]}")


async def update_calendar_event_time(
    user_id: str, event_id: str, start: datetime, end: datetime, timezone_name: str,
) -> dict:
    """Move an existing event to a new start/end. sendUpdates=all emails the
    updated invite to attendees."""
    access_token, calendar_id = await get_valid_access_token(user_id)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        resp = await client.patch(
            f"{CALENDAR_API}/calendars/{calendar_id}/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"sendUpdates": "all"},
            json={
                "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
                "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Google event update failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


async def revoke_tokens(refresh_token: str) -> None:
    """Best-effort revoke on disconnect - a failure here never blocks the
    local disconnect, the row is deleted regardless."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            await client.post(GOOGLE_REVOKE_URL, params={"token": refresh_token})
    except Exception as e:
        print(f"[GoogleCalendar] token revoke failed (ignored): {e}")
