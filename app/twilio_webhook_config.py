"""
Auto-configure Twilio phone number webhooks via the Twilio REST API.

When the user adds a number or saves their credentials, we automatically
set the VoiceUrl on every phone number in our DB so inbound calls route
to our /twilio/voice endpoint without any manual Twilio console work.
"""

import httpx
from app.database import get_supabase


async def _get_number_sid(account_sid: str, auth_token: str, phone_number: str) -> str | None:
    """Look up the Twilio SID (PN...) for a given phone number."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, auth=(account_sid, auth_token), params={"PhoneNumber": phone_number})
            if resp.status_code == 200:
                numbers = resp.json().get("incoming_phone_numbers", [])
                if numbers:
                    return numbers[0]["sid"]
    except Exception as e:
        print(f"[Twilio Webhook] Failed to fetch SID for {phone_number}: {e}")
    return None


async def _set_voice_url(account_sid: str, auth_token: str, number_sid: str, voice_url: str) -> bool:
    """Set VoiceUrl on a Twilio phone number by its SID."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers/{number_sid}.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                auth=(account_sid, auth_token),
                data={"VoiceUrl": voice_url, "VoiceMethod": "POST"},
            )
            if resp.status_code == 200:
                print(f"[Twilio Webhook] Configured {number_sid} → {voice_url}")
                return True
            else:
                print(f"[Twilio Webhook] Failed to set VoiceUrl on {number_sid}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Twilio Webhook] HTTP error setting VoiceUrl: {e}")
    return False


async def configure_number_webhook(user_id: str, number_id: str, account_sid: str, auth_token: str, webhook_base_url: str) -> None:
    """Fetch the Twilio SID for one phone number and set its VoiceUrl."""
    db = get_supabase()
    row = db.table("twilio_phone_numbers").select("*").eq("id", number_id).eq("user_id", user_id).limit(1).execute()
    if not row.data:
        return
    record = row.data[0]
    phone_number = record["phone_number"]
    voice_url = webhook_base_url.rstrip("/") + "/twilio/voice"

    number_sid = record.get("twilio_number_sid") or await _get_number_sid(account_sid, auth_token, phone_number)
    if not number_sid:
        print(f"[Twilio Webhook] Could not find Twilio SID for {phone_number}")
        return

    # Store SID for future use
    db.table("twilio_phone_numbers").update({"twilio_number_sid": number_sid}).eq("id", number_id).execute()

    await _set_voice_url(account_sid, auth_token, number_sid, voice_url)


async def configure_all_user_numbers(user_id: str, account_sid: str, auth_token: str, webhook_base_url: str) -> None:
    """Auto-configure webhooks on all phone numbers for this user."""
    db = get_supabase()
    numbers = db.table("twilio_phone_numbers").select("*").eq("user_id", user_id).execute().data
    voice_url = webhook_base_url.rstrip("/") + "/twilio/voice"

    for record in numbers:
        number_sid = record.get("twilio_number_sid") or await _get_number_sid(account_sid, auth_token, record["phone_number"])
        if not number_sid:
            print(f"[Twilio Webhook] No SID for {record['phone_number']}, skipping")
            continue
        if not record.get("twilio_number_sid"):
            db.table("twilio_phone_numbers").update({"twilio_number_sid": number_sid}).eq("id", record["id"]).execute()
        await _set_voice_url(account_sid, auth_token, number_sid, voice_url)
