"""
Background scheduler that checks every 60 seconds for interviews due to be called
and initiates outbound Twilio calls.
"""

import asyncio
import httpx


async def _initiate_outbound_call(interview: dict, settings: dict, base_url: str) -> tuple[str | None, str | None]:
    """
    Use Twilio REST API to dial the candidate.
    Returns (call_sid, None) on success, or (None, short_human_reason) on failure -
    the reason is shown as-is in Call Analytics, so it must stay short and plain.
    """
    account_sid = settings.get("account_sid", "")
    auth_token = settings.get("auth_token", "")
    if not account_sid or not auth_token:
        print(f"[HR Scheduler] No Twilio credentials for user {interview['user_id']}")
        return None, "No Twilio account connected for this user"

    candidate = interview.get("hr_candidates") or {}
    to_number = candidate.get("phone", "")
    if not to_number:
        print(f"[HR Scheduler] No phone number for candidate in interview {interview['id']}")
        return None, "Candidate has no phone number on file"

    # We need a 'from' number - use the first active attached phone number for this user+agent
    from app.core.database import get_supabase
    db = get_supabase()
    from_result = (
        db.table("twilio_phone_numbers")
        .select("phone_number, twilio_account_id")
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
            .select("phone_number, twilio_account_id")
            .eq("user_id", interview["user_id"])
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
    if not from_result.data:
        print(f"[HR Scheduler] No Twilio phone number configured for user {interview['user_id']}")
        return None, "No active Twilio phone number configured"

    from_number = from_result.data[0]["phone_number"]

    # If this number is assigned to a specific Twilio account, that account's
    # credentials must be used to place the call - Twilio rejects a "From"
    # number dialed with another account's credentials. Falls back to the
    # default `settings` already resolved above when the number isn't
    # assigned to a specific account.
    twilio_account_id = from_result.data[0].get("twilio_account_id")
    if twilio_account_id:
        from app.telephony.twilio.user_twilio_settings_repo import get_twilio_account
        account = get_twilio_account(twilio_account_id, interview["user_id"])
        if account and account.get("account_sid") and account.get("auth_token"):
            account_sid = account["account_sid"]
            auth_token = account["auth_token"]

    twiml_url = f"{base_url}/twilio/outbound/voice?interview_id={interview['id']}"
    status_callback_url = f"{base_url}/twilio/interview/status-callback"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json",
                auth=(account_sid, auth_token),
                data={
                    "From": from_number, "To": to_number, "Url": twiml_url, "Method": "GET",
                    "StatusCallback": status_callback_url,
                },
            )
            data = resp.json()
            if resp.status_code in (200, 201):
                print(f"[HR Scheduler] Call initiated: {data.get('sid')} → {to_number}")
                return data.get("sid"), None
            else:
                print(f"[HR Scheduler] Twilio error {resp.status_code}: {data}")
                return None, "Twilio rejected the call - check the phone number and account status"
    except Exception as e:
        print(f"[HR Scheduler] HTTP error: {e}")
        return None, "Could not reach Twilio to place the call"


async def _initiate_outbound_call_plivo(interview: dict, plivo_settings: dict, base_url: str) -> tuple[str | None, str | None]:
    """Plivo counterpart of _initiate_outbound_call - used only when the
    interview's agent has an active Plivo number attached to it (checked by
    the caller before this is ever reached), so Twilio-only users are
    completely unaffected."""
    auth_id = plivo_settings.get("auth_id", "")
    auth_token = plivo_settings.get("auth_token", "")
    if not auth_id or not auth_token:
        return None, "No Plivo account connected for this user"

    candidate = interview.get("hr_candidates") or {}
    to_number = candidate.get("phone", "")
    if not to_number:
        return None, "Candidate has no phone number on file"

    from app.telephony.plivo.plivo_repo import get_from_number
    from_result = get_from_number(interview["user_id"], interview["agent_id"])
    if not from_result:
        return None, "No active Plivo phone number configured"
    from_number = from_result["phone_number"]

    plivo_account_id = from_result.get("plivo_account_id")
    if plivo_account_id:
        from app.telephony.plivo.user_plivo_settings_repo import get_plivo_account
        account = get_plivo_account(plivo_account_id, interview["user_id"])
        if account and account.get("auth_id") and account.get("auth_token"):
            auth_id = account["auth_id"]
            auth_token = account["auth_token"]

    answer_url = f"{base_url}/plivo/outbound/voice?interview_id={interview['id']}"
    hangup_url = f"{base_url}/plivo/interview/status-callback"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.plivo.com/v1/Account/{auth_id}/Call/",
                auth=(auth_id, auth_token),
                json={
                    "from": from_number, "to": to_number, "answer_url": answer_url,
                    "answer_method": "GET", "hangup_url": hangup_url,
                },
            )
            data = resp.json()
            if resp.status_code in (200, 201, 202):
                call_uuid = data.get("request_uuid")
                print(f"[HR Scheduler] Plivo call initiated: {call_uuid} → {to_number}")
                return call_uuid, None
            print(f"[HR Scheduler] Plivo error {resp.status_code}: {data}")
            return None, "Plivo rejected the call - check the phone number and account status"
    except Exception as e:
        print(f"[HR Scheduler] Plivo HTTP error: {e}")
        return None, "Could not reach Plivo to place the call"


async def run_scheduler():
    """Run forever - check every 60 seconds for due interviews."""
    print("[HR Scheduler] Started")
    while True:
        try:
            await _check_and_call()
        except Exception as e:
            print(f"[HR Scheduler] Error: {e}")
        await asyncio.sleep(60)


async def _record_call_analytics(fn, *args, **kwargs):
    """Runs a Call Analytics write off the event loop and never lets it
    propagate - a DB/migration hiccup here must never stall real interview
    calls for the rest of this tick (record_failed_call/create_conversation
    already guard themselves individually, but this is the belt-and-braces
    backstop at the call site, so a bug in either can never take the loop
    down with it)."""
    try:
        await asyncio.to_thread(fn, *args, **kwargs)
    except Exception as e:
        print(f"[HR Scheduler] Call Analytics write failed (non-fatal): {e}")


async def _check_and_call():
    from app.hr_campaigns.hr_repo import get_due_interviews, mark_interview_calling, mark_interview_failed
    from app.telephony.twilio.user_twilio_settings_repo import get_twilio_settings
    from app.conversations.conversation_repo import create_conversation, record_failed_call

    due = await asyncio.to_thread(get_due_interviews)
    if not due:
        return

    print(f"[HR Scheduler] {len(due)} interview(s) due")

    for interview in due:
        user_id = interview["user_id"]
        agent_id = interview.get("agent_id")
        candidate_name = (interview.get("hr_candidates") or {}).get("name") or "candidate"
        call_title = f"Interview call - {candidate_name}"

        # Optimistically mark as calling BEFORE the Twilio call to prevent double-dialing
        # (if multiple workers run or the server crashes mid-call).
        # The conditional update only succeeds if status is still 'pending'.
        from app.core.database import get_supabase
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

        # Provider is decided purely by which number is attached to this
        # agent: if a Plivo number is attached, dial over Plivo; otherwise
        # fall through to the pre-existing Twilio path unchanged, so a user
        # who never connects Plivo sees zero behavior change. Any lookup
        # error here (DB hiccup, etc.) must never break the proven Twilio
        # path below - it degrades to "no Plivo number found".
        plivo_from = None
        try:
            from app.telephony.plivo.plivo_repo import get_number_for_agent as _plivo_number_for_agent
            plivo_from = await asyncio.to_thread(_plivo_number_for_agent, user_id, agent_id)
        except Exception as e:
            print(f"[HR Scheduler] Plivo number lookup failed (non-fatal, falling back to Twilio): {e}")
        if plivo_from:
            from app.telephony.plivo.user_plivo_settings_repo import resolve_account_for_number
            plivo_settings = await asyncio.to_thread(resolve_account_for_number, plivo_from, user_id)
            plivo_base_url = (plivo_settings.get("webhook_base_url") or "").rstrip("/")
            if not plivo_base_url:
                await asyncio.to_thread(mark_interview_failed, interview["id"])
                await _record_call_analytics(
                    record_failed_call, user_id, call_title, agent_id,
                    "No webhook URL configured in Plivo settings",
                )
                continue
            call_sid, fail_reason = await _initiate_outbound_call_plivo(interview, plivo_settings, plivo_base_url)
            if call_sid:
                await asyncio.to_thread(
                    lambda: _db.table("hr_interviews").update({"call_sid": call_sid}).eq("id", interview["id"]).execute()
                )
                await _record_call_analytics(
                    create_conversation, user_id, call_title,
                    agent_id=agent_id, call_sid=call_sid, call_status="ringing",
                )
            else:
                await asyncio.to_thread(mark_interview_failed, interview["id"])
                await _record_call_analytics(record_failed_call, user_id, call_title, agent_id, fail_reason or "Call could not be placed")
            continue

        settings = await asyncio.to_thread(get_twilio_settings, user_id)
        base_url = (settings.get("webhook_base_url") or "").rstrip("/")
        if not base_url:
            await asyncio.to_thread(mark_interview_failed, interview["id"])
            await _record_call_analytics(
                record_failed_call, user_id, call_title, agent_id,
                "No webhook URL configured in Twilio settings",
            )
            continue

        call_sid, fail_reason = await _initiate_outbound_call(interview, settings, base_url)
        if call_sid:
            # Update with the call SID now that we have it
            await asyncio.to_thread(
                lambda: _db.table("hr_interviews").update({"call_sid": call_sid}).eq("id", interview["id"]).execute()
            )
            # Eagerly record the call in Call Analytics — Twilio never calls our
            # TwiML URL at all for busy/no-answer/failed, so the WS stream handler
            # would otherwise never see (or report) those outcomes. The
            # status-callback webhook resolves this row once Twilio knows the
            # outcome; the live pipeline resolves it instead if the call connects.
            await _record_call_analytics(
                create_conversation, user_id, call_title,
                agent_id=agent_id, call_sid=call_sid, call_status="ringing",
            )
        else:
            await asyncio.to_thread(mark_interview_failed, interview["id"])
            await _record_call_analytics(record_failed_call, user_id, call_title, agent_id, fail_reason or "Call could not be placed")
