from datetime import datetime, timezone
from app.database import get_supabase


# ── Campaigns ────────────────────────────────────────────────────────────────

def list_campaigns(user_id: str) -> list[dict]:
    db = get_supabase()
    return (
        db.table("hr_campaigns")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .execute()
        .data
    )


def get_campaign(campaign_id: str, user_id: str) -> dict | None:
    db = get_supabase()
    result = (
        db.table("hr_campaigns")
        .select("*")
        .eq("id", campaign_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def create_campaign(
    user_id: str,
    agent_id: str,
    name: str,
    questions: list[dict],
    max_retry_attempts: int = 2,
    retry_delay_minutes: int = 10,
    call_gap_minutes: int = 4,
) -> dict:
    db = get_supabase()
    result = db.table("hr_campaigns").insert({
        "user_id": user_id,
        "agent_id": agent_id,
        "name": name,
        "questions": questions,
        "status": "draft",
        "max_retry_attempts": max_retry_attempts,
        "retry_delay_minutes": retry_delay_minutes,
        "call_gap_minutes": call_gap_minutes,
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to create campaign")
    return result.data[0]


def update_campaign(campaign_id: str, user_id: str, **fields) -> dict:
    allowed = {
        "name", "questions", "status",
        "max_retry_attempts", "retry_delay_minutes", "call_gap_minutes",
    }
    update = {k: v for k, v in fields.items() if k in allowed}
    db = get_supabase()
    result = (
        db.table("hr_campaigns")
        .update(update)
        .eq("id", campaign_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Campaign not found")
    return result.data[0]


def delete_campaign(campaign_id: str, user_id: str) -> None:
    db = get_supabase()
    # Cascade candidates + their answers manually - no FK constraints in this schema (matches hr_interviews convention).
    cand_rows = (
        db.table("hr_campaign_candidates")
        .select("id")
        .eq("campaign_id", campaign_id)
        .execute()
        .data
    )
    cand_ids = [r["id"] for r in cand_rows]
    if cand_ids:
        db.table("hr_campaign_answers").delete().in_("campaign_candidate_id", cand_ids).execute()
        db.table("hr_campaign_candidates").delete().eq("campaign_id", campaign_id).execute()
    db.table("hr_campaigns").delete().eq("id", campaign_id).eq("user_id", user_id).execute()


# ── Campaign candidates (the dial queue) ────────────────────────────────────

def _attach_candidate(rows: list[dict], db) -> list[dict]:
    """Manually join hr_candidates data onto campaign-candidate rows (no FK, mirrors hr_repo._attach_candidate)."""
    candidate_ids = list({r["candidate_id"] for r in rows if r.get("candidate_id")})
    if not candidate_ids:
        return rows
    candidates = {
        c["id"]: c
        for c in db.table("hr_candidates").select("*").in_("id", candidate_ids).execute().data
    }
    for row in rows:
        c = candidates.get(row.get("candidate_id"))
        row["hr_candidates"] = (
            {"name": c["name"], "phone": c["phone"], "role": c.get("role"), "email": c.get("email")}
            if c else None
        )
    return rows


def list_campaign_candidates(campaign_id: str) -> list[dict]:
    db = get_supabase()
    rows = (
        db.table("hr_campaign_candidates")
        .select("*")
        .eq("campaign_id", campaign_id)
        .order("created_at", desc=False)
        .execute()
        .data
    )
    return _attach_candidate(rows, db)


def get_campaign_candidate(campaign_candidate_id: str) -> dict | None:
    """
    Single queue row with its parent campaign attached as row["hr_campaigns"]
    and candidate contact info as row["hr_candidates"]. Not user-scoped -
    called from the Twilio-facing TwiML webhook, mirrors hr_repo.get_interview.
    """
    db = get_supabase()
    result = db.table("hr_campaign_candidates").select("*").eq("id", campaign_candidate_id).limit(1).execute()
    if not result.data:
        return None
    row = result.data[0]
    campaign = db.table("hr_campaigns").select("*").eq("id", row["campaign_id"]).limit(1).execute()
    row["hr_campaigns"] = campaign.data[0] if campaign.data else None
    return _attach_candidate([row], db)[0]


def add_candidates_to_campaign(campaign_id: str, candidate_ids: list[str]) -> list[dict]:
    """Insert new queue rows, silently skipping candidate_ids already in this campaign."""
    db = get_supabase()
    existing = (
        db.table("hr_campaign_candidates")
        .select("candidate_id")
        .eq("campaign_id", campaign_id)
        .execute()
        .data
    )
    already_in = {r["candidate_id"] for r in existing}
    new_ids = [cid for cid in candidate_ids if cid not in already_in]
    if not new_ids:
        return []
    result = db.table("hr_campaign_candidates").insert([
        {"campaign_id": campaign_id, "candidate_id": cid, "status": "queued"}
        for cid in new_ids
    ]).execute()
    return result.data


def remove_campaign_candidate(campaign_candidate_id: str, campaign_id: str) -> None:
    db = get_supabase()
    cand_id = campaign_candidate_id
    db.table("hr_campaign_answers").delete().eq("campaign_candidate_id", cand_id).execute()
    db.table("hr_campaign_candidates").delete().eq("id", cand_id).eq("campaign_id", campaign_id).execute()


def mark_calling(campaign_candidate_id: str, call_sid: str) -> None:
    """
    Called by the dialer right after placing the Twilio call. attempt_count
    increments here (on every dial, including the first) rather than at
    retry-promotion time - max_retry_attempts is then simply "how many
    retries are allowed after the first attempt" (exhaustion: attempt_count
    > max_retry_attempts). Safe without a DB-level atomic increment because
    the campaign-level claim (active_candidate_id) already serializes all
    dial attempts for a given campaign to one at a time.
    """
    db = get_supabase()
    current = (
        db.table("hr_campaign_candidates")
        .select("attempt_count")
        .eq("id", campaign_candidate_id)
        .limit(1)
        .execute()
    )
    attempt_count = (current.data[0]["attempt_count"] if current.data else 0) + 1
    db.table("hr_campaign_candidates").update({
        "status": "calling",
        "call_sid": call_sid,
        "last_attempt_at": datetime.now(timezone.utc).isoformat(),
        "attempt_count": attempt_count,
    }).eq("id", campaign_candidate_id).execute()


def release_campaign_claim(campaign_id: str, campaign_candidate_id: str) -> None:
    """
    Frees the campaign's dial slot and marks when the call actually ended -
    the pacing anchor the dialer (Stage 4) gates call_gap_minutes off. Guarded
    on the current claim so this is safe to call from two independent writers
    (this status-callback route, and Stage 6's WS completion path) without
    one clobbering work the other already did.
    """
    db = get_supabase()
    db.table("hr_campaigns").update({
        "active_candidate_id": None,
        "last_call_ended_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", campaign_id).eq("active_candidate_id", campaign_candidate_id).execute()


def mark_candidate_completed(campaign_id: str, campaign_candidate_id: str) -> None:
    """
    Called from the WS "stop"/finally lifecycle (Stage 6) when a campaign call
    actually connected and finished normally - mirrors hr_repo.mark_interview_completed's
    unconditional-update style (by the time the WS reaches here, machine
    detection has already filtered out non-human answers, so "calling" is the
    only plausible prior state; no conditional guard needed on this write).
    Also releases the campaign's dial claim, safely - release_campaign_claim's
    own guard means a status-callback race (Stage 2) that already released it
    is a harmless no-op here, and vice versa.
    """
    db = get_supabase()
    db.table("hr_campaign_candidates").update({"status": "completed"}).eq("id", campaign_candidate_id).execute()
    release_campaign_claim(campaign_id, campaign_candidate_id)


def mark_candidate_status_from_call_sid(call_sid: str, new_status: str) -> dict | None:
    """
    Conditionally update a campaign-candidate's status by call_sid - only if
    it's still "calling", so this never clobbers a status a different writer
    (the WS lifecycle, Stage 6) already resolved for the same call. Also
    releases the campaign's dial slot so the dialer can move on.
    Returns the updated row, or None if no row matched (already resolved, or unknown call_sid).
    """
    db = get_supabase()
    result = (
        db.table("hr_campaign_candidates")
        .update({"status": new_status})
        .eq("call_sid", call_sid)
        .eq("status", "calling")
        .execute()
    )
    if not result.data:
        return None
    row = result.data[0]
    release_campaign_claim(row["campaign_id"], row["id"])
    return row


def mark_candidate_machine_detected(campaign_candidate_id: str) -> dict | None:
    """
    Twilio's synchronous AMD identified an answering machine/fax - the
    outbound TwiML route hangs up immediately, before the call pipeline ever
    starts. Treated like a no-answer for retry purposes (not a completed
    interview). Same conditional-update race-safety as
    mark_candidate_status_from_call_sid, keyed by row id since the TwiML
    route already knows campaign_candidate_id directly from the URL.
    """
    db = get_supabase()
    result = (
        db.table("hr_campaign_candidates")
        .update({"status": "no_answer"})
        .eq("id", campaign_candidate_id)
        .eq("status", "calling")
        .execute()
    )
    if not result.data:
        return None
    row = result.data[0]
    release_campaign_claim(row["campaign_id"], row["id"])
    return row


# ── Answers (recorded live, via the record_answer tool call - wired up in Stage 6) ──

def record_answer(campaign_candidate_id: str, question_id: str, question_text: str, answer_text: str) -> dict:
    db = get_supabase()
    result = db.table("hr_campaign_answers").insert({
        "campaign_candidate_id": campaign_candidate_id,
        "question_id": question_id,
        "question_text": question_text,
        "answer_text": answer_text,
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to record answer")
    return result.data[0]


def list_campaign_answers(campaign_candidate_id: str) -> list[dict]:
    db = get_supabase()
    return (
        db.table("hr_campaign_answers")
        .select("*")
        .eq("campaign_candidate_id", campaign_candidate_id)
        .order("recorded_at", desc=False)
        .execute()
        .data
    )


# ── Dialer support (Stage 4 - app/hr_campaign_service.py) ──────────────────

def list_running_campaigns() -> list[dict]:
    db = get_supabase()
    return db.table("hr_campaigns").select("*").eq("status", "running").execute().data


def get_next_queued_candidate(campaign_id: str) -> dict | None:
    """Oldest 'queued' row for this campaign, with candidate contact info joined."""
    db = get_supabase()
    result = (
        db.table("hr_campaign_candidates")
        .select("*")
        .eq("campaign_id", campaign_id)
        .eq("status", "queued")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return _attach_candidate(result.data, db)[0]


def requeue_eligible_retries(campaign_id: str, retry_delay_minutes: int, max_retry_attempts: int) -> None:
    """
    Only meaningful once a campaign has no 'queued' rows left (the whole list
    has been gone through once): promotes busy/no_answer candidates whose
    retry delay has elapsed back to 'queued', or marks them 'exhausted' once
    they've used up max_retry_attempts. Mirrors hr_repo.get_due_interviews'
    Python-side date-math pattern - no raw SQL available via the
    Supabase/PostgREST client for a single conditional-date query.
    """
    db = get_supabase()
    rows = (
        db.table("hr_campaign_candidates")
        .select("*")
        .eq("campaign_id", campaign_id)
        .in_("status", ["busy", "no_answer"])
        .execute()
        .data
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        if row["attempt_count"] > max_retry_attempts:
            db.table("hr_campaign_candidates").update({"status": "exhausted"}).eq("id", row["id"]).execute()
            continue
        last_attempt = row.get("last_attempt_at")
        if not last_attempt:
            continue
        try:
            last_attempt_dt = datetime.fromisoformat(last_attempt.replace("Z", "+00:00"))
        except Exception:
            continue
        elapsed_minutes = (now - last_attempt_dt).total_seconds() / 60
        if elapsed_minutes >= retry_delay_minutes:
            db.table("hr_campaign_candidates").update({"status": "queued"}).eq("id", row["id"]).execute()


def claim_campaign_for_dial(campaign_id: str, campaign_candidate_id: str) -> bool:
    """Atomic claim - succeeds only if no other candidate is currently in flight for this campaign."""
    db = get_supabase()
    result = (
        db.table("hr_campaigns")
        .update({"active_candidate_id": campaign_candidate_id})
        .eq("id", campaign_id)
        .is_("active_candidate_id", None)
        .execute()
    )
    return bool(result.data)


def mark_candidate_failed_and_release(campaign_id: str, campaign_candidate_id: str) -> None:
    """
    For setup failures (no Twilio credentials, no from-number, no webhook URL,
    or the Twilio REST call itself failing) where no call was ever actually
    placed - no Twilio callback will ever arrive to release the claim, so this
    does it directly. Unconditional on the candidate row (not the
    eq(status, "calling") guard the callback routes use) since the dialer
    already holds this campaign's claim exclusively at this point.
    """
    db = get_supabase()
    db.table("hr_campaign_candidates").update({"status": "failed"}).eq("id", campaign_candidate_id).execute()
    db.table("hr_campaigns").update({
        "active_candidate_id": None,
        "last_call_ended_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", campaign_id).eq("active_candidate_id", campaign_candidate_id).execute()


def maybe_complete_campaign(campaign_id: str) -> None:
    """If every candidate in the queue has reached a terminal state, mark the campaign completed."""
    db = get_supabase()
    rows = db.table("hr_campaign_candidates").select("status").eq("campaign_id", campaign_id).execute().data
    if not rows:
        return
    terminal = {"completed", "exhausted", "failed"}
    if all(r["status"] in terminal for r in rows):
        db.table("hr_campaigns").update({"status": "completed"}).eq("id", campaign_id).eq("status", "running").execute()
