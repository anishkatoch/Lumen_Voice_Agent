"""
Background scheduler that checks every 60 seconds for interviews due to be called
and initiates outbound Twilio calls.
"""

import asyncio
import httpx


async def _initiate_outbound_call(interview: dict, settings: dict, base_url: str) -> str | None:
    """
    Use Twilio REST API to dial the candidate.
    Returns the call SID, or None on failure.
    """
    account_sid = settings.get("account_sid", "")
    auth_token = settings.get("auth_token", "")
    if not account_sid or not auth_token:
        print(f"[HR Scheduler] No Twilio credentials for user {interview['user_id']}")
        return None

    candidate = interview.get("hr_candidates") or {}
    to_number = candidate.get("phone", "")
    if not to_number:
        print(f"[HR Scheduler] No phone number for candidate in interview {interview['id']}")
        return None

    # We need a 'from' number — use the first active attached phone number for this user+agent
    from app.database import get_supabase
    db = get_supabase()
    from_result = (
        db.table("twilio_phone_numbers")
        .select("phone_number")
        .eq("user_id", interview["user_id"])
        .eq("agent_id", interview["agent_id"])
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not from_result.data:
        # Fall back to any active number for this user
        from_result = (
            db.table("twilio_phone_numbers")
            .select("phone_number")
            .eq("user_id", interview["user_id"])
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
    if not from_result.data:
        print(f"[HR Scheduler] No Twilio phone number configured for user {interview['user_id']}")
        return None

    from_number = from_result.data[0]["phone_number"]
    twiml_url = f"{base_url}/twilio/outbound/voice?interview_id={interview['id']}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json",
                auth=(account_sid, auth_token),
                data={"From": from_number, "To": to_number, "Url": twiml_url, "Method": "GET"},
            )
            data = resp.json()
            if resp.status_code in (200, 201):
                print(f"[HR Scheduler] Call initiated: {data.get('sid')} → {to_number}")
                return data.get("sid")
            else:
                print(f"[HR Scheduler] Twilio error {resp.status_code}: {data}")
                return None
    except Exception as e:
        print(f"[HR Scheduler] HTTP error: {e}")
        return None


async def run_scheduler():
    """Run forever — check every 60 seconds for due interviews."""
    print("[HR Scheduler] Started")
    while True:
        try:
            await _check_and_call()
        except Exception as e:
            print(f"[HR Scheduler] Error: {e}")
        await asyncio.sleep(60)


async def _check_and_call():
    from app.hr_repo import get_due_interviews, mark_interview_calling, mark_interview_failed
    from app.user_twilio_settings_repo import get_twilio_settings

    due = await asyncio.to_thread(get_due_interviews)
    if not due:
        return

    print(f"[HR Scheduler] {len(due)} interview(s) due")

    for interview in due:
        user_id = interview["user_id"]

        # Optimistically mark as calling BEFORE the Twilio call to prevent double-dialing
        # (if multiple workers run or the server crashes mid-call).
        # The conditional update only succeeds if status is still 'pending'.
        from app.database import get_supabase
        _db = get_supabase()
        _claim = (
            _db.table("hr_interviews")
            .update({"status": "calling"})
            .eq("id", interview["id"])
            .eq("status", "pending")
            .execute()
        )
        if not _claim.data:
            # Another worker already claimed this interview
            continue

        settings = await asyncio.to_thread(get_twilio_settings, user_id)
        base_url = (settings.get("webhook_base_url") or "").rstrip("/")
        if not base_url:
            await asyncio.to_thread(mark_interview_failed, interview["id"])
            continue

        call_sid = await _initiate_outbound_call(interview, settings, base_url)
        if call_sid:
            # Update with the call SID now that we have it
            await asyncio.to_thread(
                lambda: _db.table("hr_interviews").update({"call_sid": call_sid}).eq("id", interview["id"]).execute()
            )
        else:
            await asyncio.to_thread(mark_interview_failed, interview["id"])
