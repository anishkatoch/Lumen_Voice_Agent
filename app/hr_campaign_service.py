"""
Background dialer for HR call campaigns - sequential outbound queue with
pacing and retry, distinct from app/hr_scheduler.py's single-scheduled-time
interview flow (that file is not modified by this feature at all).

Loop shape mirrors hr_scheduler.py's run_scheduler()/_check_and_call(), but
each campaign dials at most one candidate at a time, gated by:
  - a per-campaign claim (hr_campaigns.active_candidate_id) so only one call
    is ever in flight per campaign
  - call_gap_minutes measured from last_call_ended_at (call-END time, not
    dial-start time - anchoring on dial time would mean the gap check reads
    as already-expired for any call that actually runs a few minutes)
  - a retry pass, only once the whole queue has been dialed at least once,
    re-queueing busy/no_answer candidates after retry_delay_minutes
"""

import asyncio
import httpx

_TICK_SECONDS = 30


async def run_campaign_dialer():
    """Run forever - check every 30 seconds for campaigns ready to dial."""
    print("[HR Campaign Dialer] Started")
    while True:
        try:
            await _check_and_dial()
        except Exception as e:
            print(f"[HR Campaign Dialer] Error: {e}")
        await asyncio.sleep(_TICK_SECONDS)


async def _check_and_dial():
    from app.hr_campaign_repo import (
        list_running_campaigns, get_next_queued_candidate, requeue_eligible_retries,
        maybe_complete_campaign,
    )

    campaigns = await asyncio.to_thread(list_running_campaigns)
    if not campaigns:
        return

    for campaign in campaigns:
        campaign_id = campaign["id"]

        if campaign.get("active_candidate_id"):
            continue  # a call is already in flight for this campaign

        if not _gap_elapsed(campaign):
            continue  # still pausing between calls

        candidate_row = await asyncio.to_thread(get_next_queued_candidate, campaign_id)
        if not candidate_row:
            # Nothing queued - see if any busy/no_answer candidates are now
            # eligible for retry (only makes sense once the list is exhausted).
            await asyncio.to_thread(
                requeue_eligible_retries, campaign_id,
                campaign["retry_delay_minutes"], campaign["max_retry_attempts"],
            )
            candidate_row = await asyncio.to_thread(get_next_queued_candidate, campaign_id)

        if not candidate_row:
            # Still nothing dialable - either waiting out retry delays, or
            # the whole queue has reached a terminal state.
            await asyncio.to_thread(maybe_complete_campaign, campaign_id)
            continue

        await _dial_candidate(campaign, candidate_row)


def _gap_elapsed(campaign: dict) -> bool:
    last_ended = campaign.get("last_call_ended_at")
    if not last_ended:
        return True  # never called anyone yet for this campaign
    from datetime import datetime, timezone
    try:
        last_ended_dt = datetime.fromisoformat(last_ended.replace("Z", "+00:00"))
    except Exception:
        return True
    elapsed_minutes = (datetime.now(timezone.utc) - last_ended_dt).total_seconds() / 60
    return elapsed_minutes >= campaign["call_gap_minutes"]


async def _record_call_analytics(fn, *args, **kwargs):
    """Runs a Call Analytics write off the event loop and never lets it
    propagate - a DB/migration hiccup here must never stall the rest of
    _check_and_dial's campaign loop (record_failed_call/create_conversation
    already guard themselves individually; this is the belt-and-braces
    backstop at the call site - mirrors app/hr_scheduler.py)."""
    try:
        await asyncio.to_thread(fn, *args, **kwargs)
    except Exception as e:
        print(f"[HR Campaign Dialer] Call Analytics write failed (non-fatal): {e}")


async def _dial_candidate(campaign: dict, candidate_row: dict):
    from app.hr_campaign_repo import claim_campaign_for_dial, mark_calling, mark_candidate_failed_and_release
    from app.user_twilio_settings_repo import get_twilio_settings
    from app.twilio_repo import get_from_number
    from app.conversation_repo import create_conversation, record_failed_call

    campaign_id = campaign["id"]
    campaign_candidate_id = candidate_row["id"]
    agent_id = campaign.get("agent_id")
    candidate = candidate_row.get("hr_candidates") or {}
    candidate_name = candidate.get("name") or "candidate"
    call_title = f"Campaign call - {candidate_name}"

    claimed = await asyncio.to_thread(claim_campaign_for_dial, campaign_id, campaign_candidate_id)
    if not claimed:
        return  # another tick/worker already claimed this campaign's dial slot

    user_id = campaign["user_id"]
    settings = await asyncio.to_thread(get_twilio_settings, user_id)
    base_url = (settings.get("webhook_base_url") or "").strip().rstrip("/")
    account_sid = settings.get("account_sid", "")
    auth_token = settings.get("auth_token", "")

    to_number = candidate.get("phone", "")

    if not to_number:
        print(f"[HR Campaign Dialer] Missing phone for campaign_candidate {campaign_candidate_id}")
        await asyncio.to_thread(mark_candidate_failed_and_release, campaign_id, campaign_candidate_id)
        await _record_call_analytics(record_failed_call, user_id, call_title, agent_id, "Candidate has no phone number on file")
        return

    if not (base_url and account_sid and auth_token):
        print(f"[HR Campaign Dialer] Missing Twilio config for campaign_candidate {campaign_candidate_id}")
        await asyncio.to_thread(mark_candidate_failed_and_release, campaign_id, campaign_candidate_id)
        await _record_call_analytics(record_failed_call, user_id, call_title, agent_id, "No Twilio account connected for this user")
        return

    from_number = await asyncio.to_thread(get_from_number, user_id, campaign["agent_id"])
    if not from_number:
        print(f"[HR Campaign Dialer] No Twilio phone number configured for user {user_id}")
        await asyncio.to_thread(mark_candidate_failed_and_release, campaign_id, campaign_candidate_id)
        await _record_call_analytics(record_failed_call, user_id, call_title, agent_id, "No active Twilio phone number configured")
        return

    twiml_url = f"{base_url}/twilio/campaign/outbound/voice?campaign_candidate_id={campaign_candidate_id}"
    status_callback_url = f"{base_url}/twilio/campaign/status-callback"

    call_sid, fail_reason = await _place_twilio_call(
        account_sid, auth_token, from_number, to_number, twiml_url, status_callback_url,
    )
    if call_sid:
        print(f"[HR Campaign Dialer] Call initiated: {call_sid} → {to_number} (campaign {campaign_id})")
        await asyncio.to_thread(mark_calling, campaign_candidate_id, call_sid)
        # Eagerly record in Call Analytics - see the matching comment in
        # app/hr_scheduler.py for why this can't wait for the WS stream to connect.
        await _record_call_analytics(
            create_conversation, user_id, call_title,
            agent_id=agent_id, call_sid=call_sid, call_status="ringing",
        )
    else:
        await asyncio.to_thread(mark_candidate_failed_and_release, campaign_id, campaign_candidate_id)
        await _record_call_analytics(record_failed_call, user_id, call_title, agent_id, fail_reason or "Call could not be placed")


async def _place_twilio_call(
    account_sid: str, auth_token: str, from_number: str, to_number: str,
    twiml_url: str, status_callback_url: str,
) -> tuple[str | None, str | None]:
    """
    Places the call with synchronous answering-machine detection (Twilio's
    default - no AsyncAmd) so the outbound TwiML route can decide human vs
    machine before ever opening the media stream. See
    twilio_campaign_outbound_twiml in app/main.py.
    Returns (call_sid, None) on success, or (None, short_human_reason) on
    failure - the reason is shown as-is in Call Analytics, so keep it short.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json",
                auth=(account_sid, auth_token),
                data={
                    "From": from_number,
                    "To": to_number,
                    "Url": twiml_url,
                    "Method": "GET",
                    "MachineDetection": "Enable",
                    "StatusCallback": status_callback_url,
                },
            )
            data = resp.json()
            if resp.status_code in (200, 201):
                return data.get("sid"), None
            print(f"[HR Campaign Dialer] Twilio error {resp.status_code}: {data}")
            return None, "Twilio rejected the call - check the phone number and account status"
    except Exception as e:
        print(f"[HR Campaign Dialer] HTTP error: {e}")
        return None, "Could not reach Twilio to place the call"
