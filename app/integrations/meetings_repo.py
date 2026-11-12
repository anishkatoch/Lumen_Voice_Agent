"""meetings repo - audit rows for bookings made by agents on calls."""
from app.core.database import get_supabase


def create_meeting(
    user_id: str,
    agent_id: str,
    conversation_id: str | None,
    google_event_id: str | None,
    attendee_name: str | None,
    attendee_email: str | None,
    topic: str | None,
    start_time: str,
    end_time: str,
    meet_link: str | None,
) -> dict:
    db = get_supabase()
    result = db.table("meetings").insert({
        "user_id": user_id,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "google_event_id": google_event_id,
        "attendee_name": attendee_name,
        "attendee_email": attendee_email,
        "topic": topic,
        "start_time": start_time,
        "end_time": end_time,
        "meet_link": meet_link,
        "status": "booked",
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to save meeting record")
    return result.data[0]


def get_meetings(user_id: str) -> list[dict]:
    db = get_supabase()
    return (
        db.table("meetings").select("*")
        .eq("user_id", user_id).order("start_time", desc=True).execute().data
    )


def get_upcoming_meetings(user_id: str) -> list[dict]:
    """Upcoming still-booked meetings for this account, soonest first. Email
    matching is done by the caller (case-insensitive, in Python) rather than
    in SQL so 'Alice@Example.com' finds the row stored as 'alice@example.com'."""
    from datetime import datetime, timezone
    db = get_supabase()
    now_iso = datetime.now(timezone.utc).isoformat()
    return (
        db.table("meetings").select("*")
        .eq("user_id", user_id).eq("status", "booked")
        .gte("start_time", now_iso)
        .order("start_time", desc=False).execute().data
    )


def set_meeting_status(meeting_id: str, status: str) -> None:
    db = get_supabase()
    db.table("meetings").update({"status": status}).eq("id", meeting_id).execute()


def update_meeting_time(meeting_id: str, start_time: str, end_time: str) -> None:
    db = get_supabase()
    db.table("meetings").update(
        {"start_time": start_time, "end_time": end_time}
    ).eq("id", meeting_id).execute()
