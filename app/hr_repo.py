from datetime import datetime, timezone
from app.database import get_supabase


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
    return _attach_candidate(rows, db)


def get_interview(interview_id: str) -> dict | None:
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
    db.table("hr_interviews").delete().eq("id", interview_id).eq("user_id", user_id).execute()


def get_due_interviews() -> list[dict]:
    """
    Return interviews that should be called now:
    scheduled_at - call_lead_minutes <= now AND status = 'pending'
    Uses a raw RPC or client-side filtering.
    """
    from datetime import datetime, timezone
    db = get_supabase()
    result = db.table("hr_interviews").select("*").eq("status", "pending").execute()
    rows = _attach_candidate(result.data, db)
    now = datetime.now(timezone.utc)
    due = []
    for row in rows:
        try:
            scheduled = datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00"))
            lead_minutes = row.get("call_lead_minutes", 30)
            call_at = scheduled.replace(tzinfo=scheduled.tzinfo or timezone.utc)
            from datetime import timedelta
            trigger_at = call_at - timedelta(minutes=lead_minutes)
            # Only call if within the window: trigger time has passed but not more than 2 hours ago
            if trigger_at <= now and (now - trigger_at).total_seconds() < 7200:
                due.append(row)
        except Exception:
            pass
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


def mark_interview_failed(interview_id: str) -> None:
    db = get_supabase()
    db.table("hr_interviews").update({"status": "failed"}).eq("id", interview_id).execute()
