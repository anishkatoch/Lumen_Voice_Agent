from app.core.database import get_supabase


def get_phone_numbers(user_id: str) -> list[dict]:
    db = get_supabase()
    return (
        db.table("twilio_phone_numbers")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .execute()
        .data
    )


def get_phone_number(number_id: str, user_id: str) -> dict | None:
    db = get_supabase()
    result = (
        db.table("twilio_phone_numbers")
        .select("*")
        .eq("id", number_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_number_by_phone(phone_number: str) -> dict | None:
    """Look up an active number record by the E.164 phone number string.
    A number set to direction='outbound' is caller-ID-only, and direction='none'
    is not used by Lumen at all (freeing the number up for another agent) -
    inbound calls to either are not routed to an agent, so both are excluded here.

    Nothing stops the same phone_number string from existing in more than one
    row (e.g. re-adding a number and attaching it to a different agent leaves
    the old row behind instead of replacing it) - ordered by created_at desc
    so the most recently (re)assigned row wins instead of whichever one the
    database happened to return first, which was silently routing live calls
    to a stale agent (found live 2026-11-27, same bug in plivo_repo.py)."""
    db = get_supabase()
    result = (
        db.table("twilio_phone_numbers")
        .select("*")
        .eq("phone_number", phone_number)
        .eq("is_active", True)
        .neq("direction", "outbound")
        .neq("direction", "none")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def create_phone_number(
    user_id: str,
    phone_number: str,
    friendly_name: str | None = None,
    twilio_account_id: str | None = None,
) -> dict:
    db = get_supabase()
    result = (
        db.table("twilio_phone_numbers")
        .insert(
            {
                "user_id": user_id,
                "phone_number": phone_number.strip(),
                "friendly_name": friendly_name,
                "twilio_account_id": twilio_account_id,
            }
        )
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to add phone number")
    return result.data[0]


def update_phone_number(number_id: str, user_id: str, **fields) -> dict:
    allowed = {"friendly_name", "is_active", "twilio_account_id", "direction"}
    update = {k: v for k, v in fields.items() if k in allowed}
    db = get_supabase()
    result = (
        db.table("twilio_phone_numbers")
        .update(update)
        .eq("id", number_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Phone number not found")
    return result.data[0]


def attach_number_to_agent(number_id: str, user_id: str, agent_id: str) -> dict:
    db = get_supabase()
    result = (
        db.table("twilio_phone_numbers")
        .update({"agent_id": agent_id})
        .eq("id", number_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Phone number not found")
    return result.data[0]


def detach_number_from_agent(number_id: str, user_id: str) -> dict:
    db = get_supabase()
    result = (
        db.table("twilio_phone_numbers")
        .update({"agent_id": None})
        .eq("id", number_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Phone number not found")
    return result.data[0]


def delete_phone_number(number_id: str, user_id: str) -> None:
    db = get_supabase()
    db.table("twilio_phone_numbers").delete().eq("id", number_id).eq(
        "user_id", user_id
    ).execute()


def get_from_number(user_id: str, agent_id: str) -> dict | None:
    """
    Pick a 'from' number for an outbound call: the agent's own attached
    number, falling back to any other active number for the user. Returns
    the phone_number plus the twilio_account_id it's assigned to (None if
    it's not assigned to a specific Twilio account - caller should fall
    back to the user's default account). Additive helper for the campaign
    dialer (app/hr_campaign_service.py) - mirrors the inline lookup in
    app/hr_scheduler.py's _initiate_outbound_call without modifying that
    existing, working function.
    """
    db = get_supabase()
    # direction='inbound' numbers are answer-only and direction='none' numbers
    # aren't used by Lumen at all, so neither is ever used as the outbound
    # caller ID; 'outbound' and 'both' qualify.
    result = (
        db.table("twilio_phone_numbers")
        .select("phone_number, twilio_account_id")
        .eq("user_id", user_id)
        .eq("agent_id", agent_id)
        .eq("is_active", True)
        .neq("direction", "inbound")
        .neq("direction", "none")
        .limit(1)
        .execute()
    )
    if not result.data:
        result = (
            db.table("twilio_phone_numbers")
            .select("phone_number, twilio_account_id")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .neq("direction", "inbound")
            .neq("direction", "none")
            .limit(1)
            .execute()
        )
    return result.data[0] if result.data else None
