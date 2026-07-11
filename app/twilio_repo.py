from app.database import get_supabase


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
    """Look up an active number record by the E.164 phone number string."""
    db = get_supabase()
    result = (
        db.table("twilio_phone_numbers")
        .select("*")
        .eq("phone_number", phone_number)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def create_phone_number(
    user_id: str,
    phone_number: str,
    friendly_name: str | None = None,
) -> dict:
    db = get_supabase()
    result = (
        db.table("twilio_phone_numbers")
        .insert(
            {
                "user_id": user_id,
                "phone_number": phone_number.strip(),
                "friendly_name": friendly_name,
            }
        )
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to add phone number")
    return result.data[0]


def update_phone_number(number_id: str, user_id: str, **fields) -> dict:
    allowed = {"friendly_name", "is_active"}
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
