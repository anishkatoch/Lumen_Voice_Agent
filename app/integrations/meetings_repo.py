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
