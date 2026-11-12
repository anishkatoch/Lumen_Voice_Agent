"""Rows for the "text us your email" flow - see app/telephony/sms_email_capture.py.

get_supabase is imported inside each function on purpose (see CLAUDE.md):
a module-level import freezes whatever get_supabase was at first import,
which breaks per-test mocking and the reset_supabase() recovery path.
"""
from datetime import datetime, timezone

TABLE = "sms_email_requests"


def normalize_number(number: str | None) -> str:
    return "".join(ch for ch in (number or "") if ch.isdigit())


def create_request(
    user_id: str, agent_id: str, conversation_id: str | None,
    agent_number: str, caller_number: str, message_uuid: str | None,
    provider: str = "plivo",
) -> dict:
    from app.core.database import get_supabase
    res = get_supabase().table(TABLE).insert({
        "user_id": user_id,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "provider": provider,
        "agent_number": normalize_number(agent_number),
        "caller_number": normalize_number(caller_number),
        "message_uuid": message_uuid,
        "status": "pending",
    }).execute()
    return res.data[0] if res.data else {}


def get_latest_for_conversation(conversation_id: str) -> dict | None:
    from app.core.database import get_supabase
    res = (
        get_supabase().table(TABLE).select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def mark_reply(caller_number: str, agent_number: str, reply_text: str, email: str | None) -> dict | None:
    """Attach an inbound SMS to the newest pending request between these two
    numbers. Returns the updated row, or None when nothing was waiting (an
    unrelated text to the agent's number)."""
    from app.core.database import get_supabase
    db = get_supabase()
    pending = (
        db.table(TABLE).select("id")
        .eq("caller_number", normalize_number(caller_number))
        .eq("agent_number", normalize_number(agent_number))
        .eq("status", "pending")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not pending.data:
        return None
    res = (
        db.table(TABLE).update({
            "status": "received",
            "reply_text": reply_text,
            "email": email,
            "replied_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", pending.data[0]["id"])
        .execute()
    )
    return res.data[0] if res.data else None
