"""
Safety net for hr_interviews rows, and hr_campaign_candidates/hr_campaigns
rows, abandoned in "calling" forever.

Normally a row leaves "calling" one of two ways: the media-stream websocket's
own lifecycle (app/routers/telephony_plivo.py / telephony_twilio.py) resolves
it directly, or the provider's status-callback webhook resolves it via
mark_interview_status_by_call_sid / mark_candidate_status_from_call_sid /
mark_candidate_machine_detected. Both depend on something reaching our
server - a dead webhook tunnel, or (as found 2026-11-04) a provider status
value with no mapping entry, means neither path ever fires, and nothing else
ever revisits the row. This loop periodically sweeps for exactly that case,
using only data already recorded for the call - it never queries the
provider's API and never touches a row that still has a live path to resolve
on its own (the `grace_minutes` window, and the `status = 'calling'` guard on
every write here, keep it from racing an in-flight call).

For campaigns specifically, a stuck "calling" row also means the campaign's
own `active_candidate_id` dial-slot claim never gets released
(release_campaign_claim is only ever called from the same two paths above),
which stalls every future candidate in that campaign indefinitely, not just
the stuck one - found 2026-11-04, same day as the interview case above, while
auditing this file's own pattern for a missing campaign equivalent.
"""
import asyncio
from datetime import datetime, timezone, timedelta

RECONCILE_INTERVAL_S = 300
STALE_GRACE_MINUTES = 20


def _get_stale_calling_interviews(grace_minutes: int = STALE_GRACE_MINUTES) -> list[dict]:
    from app.core.database import get_supabase
    db = get_supabase()
    result = db.table("hr_interviews").select("*").eq("status", "calling").execute()
    now = datetime.now(timezone.utc)
    stale = []
    for row in result.data or []:
        try:
            scheduled = datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00"))
            if (now - scheduled) > timedelta(minutes=grace_minutes):
                stale.append(row)
        except Exception:
            pass
    return stale


def _reconcile_one(interview: dict) -> None:
    from app.hr_campaigns.hr_repo import mark_interview_status
    from app.conversations.conversation_repo import set_call_status
    from app.core.database import get_supabase

    interview_id = interview["id"]
    call_sid = interview.get("call_sid")

    if not call_sid:
        # Never got far enough to even have a call_sid - nothing recorded
        # anywhere to check the outcome against.
        mark_interview_status(interview_id, "unknown")
        return

    db = get_supabase()
    conv_result = (
        db.table("conversations").select("id, call_status").eq("call_sid", call_sid).limit(1).execute()
    )
    conv = conv_result.data[0] if conv_result.data else None

    if conv is None:
        # No Call Analytics row for this call_sid either - genuinely no
        # record of what happened, so this gets its own status rather than
        # being guessed as completed or failed.
        mark_interview_status(interview_id, "unknown")
        return

    conversation_id = conv["id"]
    call_status = conv.get("call_status")

    if call_status not in ("ringing", None):
        # The live pipeline already reached a verdict - hr_interviews just
        # never heard about it (the websocket resolved conversations but
        # couldn't resolve interview_id, or a similar race). Sync to match
        # instead of re-deriving it independently.
        mark_interview_status(interview_id, "completed" if call_status == "completed" else "failed")
        return

    msg_count_result = (
        db.table("messages").select("id", count="exact").eq("conversation_id", conversation_id).limit(1).execute()
    )
    had_conversation = (msg_count_result.count or 0) > 0

    if had_conversation:
        # Messages exist, so a real conversation happened on this call even
        # though no provider ever confirmed a final status for it.
        mark_interview_status(interview_id, "completed")
        set_call_status(conversation_id, "completed", None)
    else:
        mark_interview_status(interview_id, "failed")
        set_call_status(
            conversation_id, "failed",
            "No conversation took place and no final call status was ever received - "
            "the call likely wasn't answered, or ended before connecting.",
        )


def _get_stale_calling_campaign_candidates(grace_minutes: int = STALE_GRACE_MINUTES) -> list[dict]:
    from app.core.database import get_supabase
    db = get_supabase()
    result = db.table("hr_campaign_candidates").select("*").eq("status", "calling").execute()
    now = datetime.now(timezone.utc)
    stale = []
    for row in result.data or []:
        last_attempt = row.get("last_attempt_at")
        if not last_attempt:
            continue
        try:
            attempt_dt = datetime.fromisoformat(last_attempt.replace("Z", "+00:00"))
            if (now - attempt_dt) > timedelta(minutes=grace_minutes):
                stale.append(row)
        except Exception:
            pass
    return stale


def _reconcile_campaign_candidate(row: dict) -> None:
    """Mirrors _reconcile_one above, but for a campaign candidate - and also
    releases the campaign's active_candidate_id dial-slot claim, which is the
    part that actually matters here: without releasing it, every other
    candidate in the same campaign stays stuck behind this one forever."""
    from app.hr_campaigns.hr_campaign_repo import release_campaign_claim, maybe_complete_campaign
    from app.conversations.conversation_repo import set_call_status
    from app.core.database import get_supabase

    campaign_candidate_id = row["id"]
    campaign_id = row["campaign_id"]
    call_sid = row.get("call_sid")
    db = get_supabase()

    def _finish(status: str, conversation_id: str | None = None,
                call_status: str | None = None, failure_reason: str | None = None) -> None:
        # Guarded on status="calling" so this never clobbers a status the
        # provider's status-callback or the WS lifecycle already resolved in
        # the gap between the query above and this write.
        db.table("hr_campaign_candidates").update({"status": status}) \
            .eq("id", campaign_candidate_id).eq("status", "calling").execute()
        release_campaign_claim(campaign_id, campaign_candidate_id)
        if conversation_id and call_status:
            set_call_status(conversation_id, call_status, failure_reason)
        maybe_complete_campaign(campaign_id)

    if not call_sid:
        # Never got far enough to even have a call_sid - no setup failure
        # path released the claim either (see mark_candidate_failed_and_release),
        # so nothing else will ever free this campaign's dial slot.
        _finish("failed")
        return

    conv_result = db.table("conversations").select("id, call_status").eq("call_sid", call_sid).limit(1).execute()
    conv = conv_result.data[0] if conv_result.data else None

    if conv is None:
        _finish("failed")
        return

    conversation_id = conv["id"]
    call_status = conv.get("call_status")

    if call_status not in ("ringing", None):
        _finish("completed" if call_status == "completed" else "failed")
        return

    msg_count_result = (
        db.table("messages").select("id", count="exact").eq("conversation_id", conversation_id).limit(1).execute()
    )
    had_conversation = (msg_count_result.count or 0) > 0

    if had_conversation:
        _finish("completed", conversation_id, "completed", None)
    else:
        _finish(
            "failed", conversation_id, "failed",
            "No conversation took place and no final call status was ever received - "
            "the call likely wasn't answered, or ended before connecting.",
        )


async def run_reconciliation_loop():
    while True:
        try:
            stale = await asyncio.to_thread(_get_stale_calling_interviews)
            for interview in stale:
                await asyncio.to_thread(_reconcile_one, interview)

            stale_candidates = await asyncio.to_thread(_get_stale_calling_campaign_candidates)
            for candidate in stale_candidates:
                await asyncio.to_thread(_reconcile_campaign_candidate, candidate)
        except Exception as e:
            print(f"[Call Reconciliation] Error: {e}")
        await asyncio.sleep(RECONCILE_INTERVAL_S)
