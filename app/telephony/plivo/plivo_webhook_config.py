"""
Auto-configure Plivo phone number webhooks via the Plivo REST API.

Unlike Twilio (which sets a VoiceUrl directly on the number), Plivo routes
inbound calls through an "Application" object (answer_url lives on the
Application, and the number just points at an app_id). So configuring a
number here means: find-or-create an Application for this webhook base
URL, then assign its app_id to the number.
"""

import httpx
from app.core.database import get_supabase

_APP_NAME_PREFIX = "lumen-agent-"


def _message_url(webhook_base_url: str) -> str:
    return webhook_base_url.rstrip("/") + "/plivo/sms/inbound"


async def _ensure_message_url(auth_id: str, auth_token: str, app_id: str, message_url: str) -> None:
    """Point the Application's inbound-SMS webhook at us. Applications made
    before the email-by-text feature only have an answer_url, so this is
    what retrofits them; it is a no-op when already set."""
    url = f"https://api.plivo.com/v1/Account/{auth_id}/Application/{app_id}/"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, auth=(auth_id, auth_token))
            if resp.status_code == 200 and resp.json().get("message_url") == message_url:
                return
            resp = await client.post(
                url, auth=(auth_id, auth_token),
                json={"message_url": message_url, "message_method": "POST"},
            )
            if resp.status_code in (200, 202):
                print(f"[Plivo Webhook] Set message_url on app {app_id}")
            else:
                print(f"[Plivo Webhook] Failed to set message_url on app {app_id}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Plivo Webhook] HTTP error setting message_url: {e}")


async def _find_or_create_app(auth_id: str, auth_token: str, answer_url: str, message_url: str | None = None) -> str | None:
    """Return the app_id of a Plivo Application whose answer_url matches,
    creating one if none exists yet."""
    app_name = _APP_NAME_PREFIX + str(abs(hash(answer_url)) % (10**10))
    base = f"https://api.plivo.com/v1/Account/{auth_id}/Application/"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(base, auth=(auth_id, auth_token), params={"limit": 20})
            if resp.status_code == 200:
                for app in resp.json().get("objects", []):
                    if app.get("answer_url") == answer_url:
                        app_id = app.get("app_id")
                        if message_url and app.get("message_url") != message_url:
                            await _ensure_message_url(auth_id, auth_token, app_id, message_url)
                        return app_id

            payload = {
                "app_name": app_name,
                "answer_url": answer_url,
                "answer_method": "POST",
            }
            if message_url:
                payload["message_url"] = message_url
                payload["message_method"] = "POST"
            resp = await client.post(base, auth=(auth_id, auth_token), json=payload)
            if resp.status_code in (200, 201):
                return resp.json().get("app_id")
            print(f"[Plivo Webhook] Failed to create application: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Plivo Webhook] HTTP error creating application: {e}")
    return None


async def _assign_app_to_number(auth_id: str, auth_token: str, phone_number: str, app_id: str) -> bool:
    """Plivo numbers are identified without the leading '+' in the API path."""
    number_path = phone_number.lstrip("+")
    url = f"https://api.plivo.com/v1/Account/{auth_id}/Number/{number_path}/"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, auth=(auth_id, auth_token), json={"app_id": app_id})
            if resp.status_code in (200, 202):
                print(f"[Plivo Webhook] Configured {phone_number} → app {app_id}")
                return True
            print(f"[Plivo Webhook] Failed to assign app to {phone_number}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Plivo Webhook] HTTP error assigning app: {e}")
    return False


async def configure_number_webhook(user_id: str, number_id: str, auth_id: str, auth_token: str, webhook_base_url: str) -> None:
    """Fetch/create the Plivo Application for this webhook base URL and
    assign it to one phone number."""
    db = get_supabase()
    row = db.table("plivo_phone_numbers").select("*").eq("id", number_id).eq("user_id", user_id).limit(1).execute()
    if not row.data:
        return
    record = row.data[0]
    answer_url = webhook_base_url.rstrip("/") + "/plivo/voice"
    message_url = _message_url(webhook_base_url)

    if record.get("plivo_app_id"):
        app_id = record["plivo_app_id"]
        await _ensure_message_url(auth_id, auth_token, app_id, message_url)
    else:
        app_id = await _find_or_create_app(auth_id, auth_token, answer_url, message_url)
    if not app_id:
        print(f"[Plivo Webhook] Could not resolve application for {record['phone_number']}")
        return

    if not record.get("plivo_app_id"):
        db.table("plivo_phone_numbers").update({"plivo_app_id": app_id}).eq("id", number_id).execute()

    await _assign_app_to_number(auth_id, auth_token, record["phone_number"], app_id)


async def configure_all_user_numbers(user_id: str, auth_id: str, auth_token: str, webhook_base_url: str) -> None:
    """Auto-configure webhooks on all Plivo numbers for this user."""
    db = get_supabase()
    numbers = db.table("plivo_phone_numbers").select("*").eq("user_id", user_id).execute().data
    answer_url = webhook_base_url.rstrip("/") + "/plivo/voice"
    app_id = await _find_or_create_app(auth_id, auth_token, answer_url, _message_url(webhook_base_url))
    if not app_id:
        return
    for record in numbers:
        if not record.get("plivo_app_id"):
            db.table("plivo_phone_numbers").update({"plivo_app_id": app_id}).eq("id", record["id"]).execute()
        await _assign_app_to_number(auth_id, auth_token, record["phone_number"], app_id)


async def configure_numbers_for_account(
    user_id: str, account_id: str, is_default: bool, auth_id: str, auth_token: str, webhook_base_url: str
) -> None:
    """Auto-configure webhooks on the phone numbers that belong to this
    specific Plivo account. Mirrors app/twilio_webhook_config.py's
    configure_numbers_for_account."""
    db = get_supabase()
    numbers = db.table("plivo_phone_numbers").select("*").eq("user_id", user_id).execute().data
    answer_url = webhook_base_url.rstrip("/") + "/plivo/voice"
    app_id = await _find_or_create_app(auth_id, auth_token, answer_url, _message_url(webhook_base_url))
    if not app_id:
        return

    for record in numbers:
        owns_number = record.get("plivo_account_id") == account_id or (
            is_default and not record.get("plivo_account_id")
        )
        if not owns_number:
            continue
        if not record.get("plivo_app_id"):
            db.table("plivo_phone_numbers").update({"plivo_app_id": app_id}).eq("id", record["id"]).execute()
        await _assign_app_to_number(auth_id, auth_token, record["phone_number"], app_id)
