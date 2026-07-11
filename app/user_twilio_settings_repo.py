from app.database import get_supabase


def get_twilio_settings(user_id: str) -> dict:
    db = get_supabase()
    result = (
        db.table("user_twilio_settings")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return {"account_sid": "", "auth_token": "", "webhook_base_url": ""}


def upsert_twilio_settings(
    user_id: str,
    account_sid: str | None = None,
    auth_token: str | None = None,
    webhook_base_url: str | None = None,
) -> dict:
    db = get_supabase()
    existing = (
        db.table("user_twilio_settings")
        .select("id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    payload = {
        "user_id": user_id,
        "account_sid": account_sid or "",
        "auth_token": auth_token or "",
        "webhook_base_url": (webhook_base_url or "").rstrip("/"),
    }
    if existing.data:
        result = (
            db.table("user_twilio_settings")
            .update({k: v for k, v in payload.items() if k != "user_id"})
            .eq("user_id", user_id)
            .execute()
        )
    else:
        result = db.table("user_twilio_settings").insert(payload).execute()
    return result.data[0] if result.data else payload
