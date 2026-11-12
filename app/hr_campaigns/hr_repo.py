from datetime import datetime, timezone
from app.core.database import get_supabase


# ── Candidates ────────────────────────────────────────────────────────────────

def list_candidates(user_id: str) -> list[dict]:
    db = get_supabase()
    return (
        db.table("hr_candidates")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .execute()
        .data
    )


def get_candidate(candidate_id: str, user_id: str) -> dict | None:
    db = get_supabase()
    result = (
        db.table("hr_candidates")
        .select("*")
        .eq("id", candidate_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def create_candidate(
    user_id: str,
    name: str,
    phone: str,
    email: str | None = None,
    role: str | None = None,
    resume_text: str | None = None,
    resume_file_name: str | None = None,
    notes: str | None = None,
) -> dict:
    db = get_supabase()
    result = db.table("hr_candidates").insert({
        "user_id": user_id,
        "name": name,
        "phone": phone.strip(),
        "email": email,
        "role": role,
        "resume_text": resume_text,
        "resume_file_name": resume_file_name,
        "notes": notes,
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to create candidate")
    return result.data[0]


def update_candidate(candidate_id: str, user_id: str, **fields) -> dict:
    allowed = {"name", "phone", "email", "role", "resume_text", "resume_file_name", "notes"}
    update = {k: v for k, v in fields.items() if k in allowed}
    db = get_supabase()
    result = (
        db.table("hr_candidates")
        .update(update)
        .eq("id", candidate_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Candidate not found")
    return result.data[0]


def delete_candidate(candidate_id: str, user_id: str) -> None:
    db = get_supabase()
    # Clean up dependent rows first - hr_interviews/hr_interview_answers have
    # no DB-level FK/cascade onto hr_candidates, so deleting the candidate
    # without this orphans them permanently (same reasoning as
    # remove_campaign_candidate's hr_campaign_answers cleanup below).
    interview_ids = [
        r["id"] for r in
        db.table("hr_interviews").select("id").eq("candidate_id", candidate_id).execute().data
    ]
    if interview_ids:
        db.table("hr_interview_answers").delete().in_("interview_id", interview_ids).execute()
        db.table("hr_interviews").delete().in_("id", interview_ids).execute()
    campaign_candidate_ids = [
        r["id"] for r in
        db.table("hr_campaign_candidates").select("id").eq("candidate_id", candidate_id).execute().data
    ]
    if campaign_candidate_ids:
        db.table("hr_campaign_answers").delete().in_("campaign_candidate_id", campaign_candidate_ids).execute()
        db.table("hr_campaign_candidates").delete().in_("id", campaign_candidate_ids).execute()
    db.table("hr_candidates").delete().eq("id", candidate_id).eq("user_id", user_id).execute()


# ── Interviews ────────────────────────────────────────────────────────────────

def _attach_candidate(interviews: list[dict], db) -> list[dict]:
    """Manually join candidate data onto interview rows (no FK needed)."""
    candidate_ids = list({r["candidate_id"] for r in interviews if r.get("candidate_id")})
    if not candidate_ids:
        return interviews
    candidates = {
        c["id"]: c
        for c in db.table("hr_candidates").select("*").in_("id", candidate_ids).execute().data
    }
    for row in interviews:
        c = candidates.get(row.get("candidate_id"))
        row["hr_candidates"] = (
            {"name": c["name"], "phone": c["phone"], "role": c.get("role"),
             "email": c.get("email"), "resume_text": c.get("resume_text"), "notes": c.get("notes")}
            if c else None
        )
    return interviews


def _attach_call_duration(interviews: list[dict]) -> list[dict]:
    """Attaches call_duration_seconds from Call Analytics (looked up by
    call_sid) onto each interview row - best-effort, never raises; a row
    with no call yet (or a lookup failure) just keeps duration_seconds=None."""
    from app.conversations.conversation_repo import get_call_durations_by_sids
    call_sids = [r["call_sid"] for r in interviews if r.get("call_sid")]
    durations = get_call_durations_by_sids(call_sids)
    for row in interviews:
        call = durations.get(row.get("call_sid"))
        row["call_duration_seconds"] = call.get("duration_seconds") if call else None
    return interviews


def list_interviews(user_id: str) -> list[dict]:
    db = get_supabase()
    rows = (
        db.table("hr_interviews")
        .select("*")
        .eq("user_id", user_id)
        .order("scheduled_at", desc=False)
        .execute()
        .data
    )
    return _attach_call_duration(_attach_candidate(rows, db))


def get_interview(interview_id: str) -> dict | None:
    """Used on the call-setup hot path (resolve_call_context) as well as by
    the interview list - deliberately doesn't attach call duration like
    list_interviews does, since that would add an extra Call Analytics
    lookup to every single call's setup for data that can't exist yet at
    that point anyway (the call hasn't happened)."""
    db = get_supabase()
    result = (
        db.table("hr_interviews")
        .select("*")
        .eq("id", interview_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    rows = _attach_candidate(result.data, db)
    return rows[0]


def create_interview(
    user_id: str,
    candidate_id: str,
    agent_id: str,
    scheduled_at: str,
    call_lead_minutes: int = 30,
    specific_questions: str | None = None,
) -> dict:
    db = get_supabase()
    result = db.table("hr_interviews").insert({
        "user_id": user_id,
        "candidate_id": candidate_id,
        "agent_id": agent_id,
        "scheduled_at": scheduled_at,
        "call_lead_minutes": call_lead_minutes,
        "specific_questions": specific_questions,
        "status": "pending",
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to create interview")
    return result.data[0]


def update_interview(interview_id: str, user_id: str, **fields) -> dict:
    allowed = {"scheduled_at", "call_lead_minutes", "specific_questions", "agent_id", "status", "called_at", "call_sid"}
    update = {k: v for k, v in fields.items() if k in allowed}
    if "scheduled_at" in update:
        # Rescheduling is a fresh first attempt - a stale attempt_count from
        # a previous (possibly exhausted) run must not carry over and
        # immediately exhaust the new schedule's retry budget.
        update["attempt_count"] = 0
        update["next_attempt_at"] = None
    db = get_supabase()
    result = (
        db.table("hr_interviews")
        .update(update)
        .eq("id", interview_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Interview not found")
    return result.data[0]


def delete_interview(interview_id: str, user_id: str) -> None:
    db = get_supabase()
    db.table("hr_interview_answers").delete().eq("interview_id", interview_id).execute()
    db.table("hr_interviews").delete().eq("id", interview_id).eq("user_id", user_id).execute()


def get_due_interviews() -> list[dict]:
    """
    Return interviews that should be called now - two cases:
    - First attempt (next_attempt_at is null): scheduled_at - call_lead_minutes
      <= now, and not more than 2 hours stale.
    - A pending retry (next_attempt_at set by mark_interview_failed/
      mark_interview_no_response after a no-answer/failure): due once
      next_attempt_at itself has passed - the original scheduled_at window
      doesn't apply here, this is a redial, not the first attempt.
    """
    from datetime import datetime, timezone, timedelta
    db = get_supabase()
    result = db.table("hr_interviews").select("*").eq("status", "pending").execute()
    rows = _attach_candidate(result.data, db)
    now = datetime.now(timezone.utc)
    due = []
    for row in rows:
        next_attempt_at = row.get("next_attempt_at")
        if next_attempt_at:
            try:
                next_dt = datetime.fromisoformat(next_attempt_at.replace("Z", "+00:00"))
                if next_dt <= now:
                    due.append(row)
            except Exception as e:
                print(f"[HR Scheduler] Could not evaluate retry due-ness for interview {row.get('id')} "
                      f"(next_attempt_at={next_attempt_at!r}) - skipping it this tick: {e}")
            continue
        try:
            scheduled = datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00"))
            lead_minutes = row.get("call_lead_minutes", 30)
            call_at = scheduled.replace(tzinfo=scheduled.tzinfo or timezone.utc)
            trigger_at = call_at - timedelta(minutes=lead_minutes)
            # Only call if within the window: trigger time has passed but not more than 2 hours ago
            if trigger_at <= now and (now - trigger_at).total_seconds() < 7200:
                due.append(row)
        except Exception as e:
            print(f"[HR Scheduler] Could not evaluate due-ness for interview {row.get('id')} "
                  f"(scheduled_at={row.get('scheduled_at')!r}) - skipping it this tick: {e}")
    return due


def mark_interview_calling(interview_id: str, call_sid: str) -> None:
    db = get_supabase()
    db.table("hr_interviews").update({
        "status": "calling",
        "called_at": datetime.now(timezone.utc).isoformat(),
        "call_sid": call_sid,
    }).eq("id", interview_id).execute()


def mark_interview_completed(interview_id: str) -> None:
    db = get_supabase()
    db.table("hr_interviews").update({"status": "completed"}).eq("id", interview_id).execute()


def _retry_or_finalize(interview_id: str, terminal_status: str, current_row: dict) -> None:
    """Shared retry decision for a failed/no-response outcome, mirroring
    hr_campaign_repo.requeue_eligible_retries' requeue/exhaust split (applied
    inline here rather than on its own sweep, since standalone interviews
    have no dialer loop checking back the way campaigns do). Reads the
    admin-configured call_retry_max_attempts/call_retry_interval_minutes so
    both the retry budget and delay are live admin-portal settings.

    attempt_count is incremented at dial time (hr_scheduler.py's claim step,
    mirroring hr_campaign_repo.mark_calling) - call_retry_max_attempts is the
    total number of dials allowed (including the first), so exhaustion is
    attempt_count > call_retry_max_attempts - 1."""
    from datetime import datetime, timezone, timedelta
    from app.admin.admin_config_service import get as get_config

    db = get_supabase()
    attempt_count = current_row.get("attempt_count") or 0
    try:
        max_attempts = int(get_config("call_retry_max_attempts"))
    except (TypeError, ValueError):
        max_attempts = 5
    try:
        interval_minutes = int(get_config("call_retry_interval_minutes"))
    except (TypeError, ValueError):
        interval_minutes = 3
    max_retries_after_first = max(0, max_attempts - 1)

    if attempt_count > max_retries_after_first:
        db.table("hr_interviews").update({"status": terminal_status}).eq("id", interview_id).execute()
        return
    next_attempt_at = (datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)).isoformat()
    db.table("hr_interviews").update({
        "status": "pending",
        "next_attempt_at": next_attempt_at,
    }).eq("id", interview_id).execute()


def mark_interview_failed(interview_id: str) -> None:
    """Terminal on a hard failure (setup error, transport error) - unless the
    admin-configured retry budget hasn't been used up yet, in which case this
    requeues the interview for another attempt instead (see _retry_or_finalize).
    Not guarded on current status: callable from setup-failure paths
    (hr_scheduler.py) before a call_sid even exists, where "calling" is
    already the only plausible state."""
    db = get_supabase()
    result = db.table("hr_interviews").select("attempt_count").eq("id", interview_id).limit(1).execute()
    current_row = result.data[0] if result.data else {}
    _retry_or_finalize(interview_id, "failed", current_row)


def mark_interview_no_response(interview_id: str) -> None:
    """Downgrades an interview from 'completed' to 'no_response' once the WS
    lifecycle has read back the call's message history and found the
    candidate never said anything. Called after mark_interview_completed
    (which runs eagerly, right on the "stop" event, before that history read
    is even attempted) rather than replacing it - this is just a follow-up
    correction. Conditional on still being 'completed' so a genuine 'failed'
    outcome set by a different writer in the meantime is never clobbered.
    Also retry-aware, same requeue/exhaust decision as mark_interview_failed -
    a connected-but-silent call is just as much a non-outcome as a no-answer."""
    db = get_supabase()
    claim = (
        db.table("hr_interviews")
        .update({"status": "no_response"})
        .eq("id", interview_id)
        .eq("status", "completed")
        .execute()
    )
    if not claim.data:
        return
    _retry_or_finalize(interview_id, "no_response", claim.data[0])


def record_interview_answer(interview_id: str, question_id: str, question_text: str, answer_text: str) -> dict:
    db = get_supabase()
    result = db.table("hr_interview_answers").insert({
        "interview_id": interview_id,
        "question_id": question_id,
        "question_text": question_text,
        "answer_text": answer_text,
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to record answer")
    return result.data[0]


def list_interview_answers(interview_id: str) -> list[dict]:
    db = get_supabase()
    return (
        db.table("hr_interview_answers")
        .select("*")
        .eq("interview_id", interview_id)
        .order("recorded_at", desc=False)
        .execute()
        .data
    )


def mark_interview_status(interview_id: str, status: str) -> None:
    """Conditionally update an interview's status by id - only if it's still
    'calling', so this never clobbers a status something else already
    resolved for the same interview (same guard as mark_interview_status_by_call_sid,
    for the case where call_sid isn't available to key off of). "failed"/
    "no_response" are retry-aware - see _retry_or_finalize - everything else
    (e.g. "unknown", "completed") is written as-is."""
    db = get_supabase()
    result = db.table("hr_interviews").update({"status": status}).eq("id", interview_id).eq("status", "calling").execute()
    if status in ("failed", "no_response") and result.data:
        _retry_or_finalize(interview_id, status, result.data[0])


def mark_interview_status_by_call_sid(call_sid: str, new_status: str) -> dict | None:
    """Conditionally update an interview's status by call_sid - only if it's
    still 'calling', so a status-callback race never clobbers a status the
    WS stream lifecycle already resolved for the same call (mirrors
    hr_campaign_repo.mark_candidate_status_from_call_sid).
    Returns the updated row, or None if no row matched.

    "busy"/"no_answer"/"failed" are retry-aware (see _retry_or_finalize,
    which may immediately overwrite this write back to "pending" for a
    retry) - this is the provider status-callback path, so it's the primary
    place a plain no-answer/busy signal (no WS stream ever connected) is
    actually seen for a standalone interview call."""
    db = get_supabase()
    result = (
        db.table("hr_interviews")
        .update({"status": new_status})
        .eq("call_sid", call_sid)
        .eq("status", "calling")
        .execute()
    )
    if not result.data:
        return None
    row = result.data[0]
    if new_status in ("busy", "no_answer", "failed"):
        _retry_or_finalize(row["id"], "failed", row)
    return row


def get_interview_by_call_sid(call_sid: str) -> dict | None:
    db = get_supabase()
    result = db.table("hr_interviews").select("*").eq("call_sid", call_sid).limit(1).execute()
    return result.data[0] if result.data else None
