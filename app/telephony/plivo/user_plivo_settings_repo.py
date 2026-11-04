from app.core.database import get_supabase


def _clean_urls(urls: list[str] | None) -> list[str]:
    """Mirrors app/telephony/twilio/user_twilio_settings_repo.py's helper."""
    if not urls:
        return []
    return [u.strip().rstrip("/") for u in urls if u and u.strip()]


def get_plivo_settings(user_id: str) -> dict:
    """Returns the user's default Plivo account. Mirrors
    app/user_twilio_settings_repo.py's get_twilio_settings."""
    db = get_supabase()
    result = (
        db.table("user_plivo_settings")
        .select("*")
        .eq("user_id", user_id)
        .eq("is_default", True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    result = (
        db.table("user_plivo_settings")
        .select("*")
        .eq("user_id", user_id)
        .order("updated_at")
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return {"auth_id": "", "auth_token": "", "webhook_base_url": ""}


def upsert_plivo_settings(
    user_id: str,
    auth_id: str | None = None,
    auth_token: str | None = None,
    webhook_base_url: str | None = None,
) -> dict:
    """Creates or updates the user's default Plivo account."""
    db = get_supabase()
    existing = (
        db.table("user_plivo_settings")
        .select("id")
        .eq("user_id", user_id)
        .eq("is_default", True)
        .limit(1)
        .execute()
    )
    payload = {
        "user_id": user_id,
        "auth_id": auth_id or "",
        "auth_token": auth_token or "",
        "webhook_base_url": (webhook_base_url or "").rstrip("/"),
    }
    if existing.data:
        result = (
            db.table("user_plivo_settings")
            .update({k: v for k, v in payload.items() if k != "user_id"})
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        payload["label"] = "Default"
        payload["is_default"] = True
        result = db.table("user_plivo_settings").insert(payload).execute()
    return result.data[0] if result.data else payload


def list_plivo_accounts(user_id: str) -> list[dict]:
    db = get_supabase()
    return (
        db.table("user_plivo_settings")
        .select("*")
        .eq("user_id", user_id)
        .order("updated_at")
        .execute()
        .data
    )


def get_plivo_account(account_id: str, user_id: str) -> dict | None:
    db = get_supabase()
    result = (
        db.table("user_plivo_settings")
        .select("*")
        .eq("id", account_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def create_plivo_account(
    user_id: str,
    label: str | None = None,
    auth_id: str | None = None,
    auth_token: str | None = None,
    webhook_base_url: str | None = None,
    is_default: bool = False,
    webhook_base_urls: list[str] | None = None,
) -> dict:
    db = get_supabase()
    existing = db.table("user_plivo_settings").select("id").eq("user_id", user_id).execute()
    make_default = is_default or not existing.data
    if make_default:
        db.table("user_plivo_settings").update({"is_default": False}).eq("user_id", user_id).execute()
    saved_urls = _clean_urls(webhook_base_urls) or ([webhook_base_url.rstrip("/")] if webhook_base_url else [])
    payload = {
        "user_id": user_id,
        "label": label or "Plivo account",
        "auth_id": auth_id or "",
        "auth_token": auth_token or "",
        "webhook_base_url": (webhook_base_url or "").rstrip("/"),
        "webhook_base_urls": saved_urls,
        "is_default": make_default,
    }
    result = db.table("user_plivo_settings").insert(payload).execute()
    if not result.data:
        raise RuntimeError("Failed to add Plivo account")
    return result.data[0]


def update_plivo_account(account_id: str, user_id: str, **fields) -> dict:
    allowed = {"label", "auth_id", "auth_token", "webhook_base_url", "webhook_base_urls"}
    update = {k: v for k, v in fields.items() if k in allowed}
    if "webhook_base_url" in update:
        update["webhook_base_url"] = (update["webhook_base_url"] or "").rstrip("/")
    if "webhook_base_urls" in update:
        update["webhook_base_urls"] = _clean_urls(update["webhook_base_urls"])
    db = get_supabase()
    result = (
        db.table("user_plivo_settings")
        .update(update)
        .eq("id", account_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Plivo account not found")
    return result.data[0]


def set_default_plivo_account(account_id: str, user_id: str) -> dict:
    db = get_supabase()
    account = get_plivo_account(account_id, user_id)
    if not account:
        raise RuntimeError("Plivo account not found")
    db.table("user_plivo_settings").update({"is_default": False}).eq("user_id", user_id).execute()
    result = (
        db.table("user_plivo_settings")
        .update({"is_default": True})
        .eq("id", account_id)
        .eq("user_id", user_id)
        .execute()
    )
    return result.data[0] if result.data else account


def delete_plivo_account(account_id: str, user_id: str) -> None:
    db = get_supabase()
    account = get_plivo_account(account_id, user_id)
    if not account:
        return
    db.table("plivo_phone_numbers").update({"plivo_account_id": None}).eq(
        "plivo_account_id", account_id
    ).execute()
    db.table("user_plivo_settings").delete().eq("id", account_id).eq("user_id", user_id).execute()
    if account.get("is_default"):
        remaining = (
            db.table("user_plivo_settings")
            .select("id")
            .eq("user_id", user_id)
            .order("updated_at")
            .limit(1)
            .execute()
        )
        if remaining.data:
            db.table("user_plivo_settings").update({"is_default": True}).eq(
                "id", remaining.data[0]["id"]
            ).execute()


def resolve_account_for_number(number_record: dict, user_id: str) -> dict:
    """Credentials for the Plivo account that owns this phone number, or
    the user's default account if the number isn't assigned to one."""
    account_id = (number_record or {}).get("plivo_account_id")
    if account_id:
        account = get_plivo_account(account_id, user_id)
        if account:
            return account
    return get_plivo_settings(user_id)
