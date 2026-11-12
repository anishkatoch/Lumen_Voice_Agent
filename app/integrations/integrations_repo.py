"""user_integrations repo - per-user OAuth connections (Google Calendar).

Tokens are encrypted at rest with Fernet. The key comes from the
INTEGRATION_TOKEN_KEY env var when set (a standard 44-char urlsafe-base64
Fernet key); otherwise it is derived from SUPABASE_JWT_SECRET (falling back
to SUPABASE_SERVICE_ROLE_KEY) so existing deployments work without a new
required secret. Rotating whichever secret the key derives from invalidates
stored tokens - get_integration treats undecryptable tokens as "not
connected" rather than crashing call setup, so the worst case is the user
having to reconnect their calendar.
"""
import base64
import hashlib
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from app.core.database import get_supabase

GOOGLE_CALENDAR = "google_calendar"


def _fernet() -> Fernet:
    explicit = os.environ.get("INTEGRATION_TOKEN_KEY", "")
    if explicit:
        return Fernet(explicit.encode())
    seed = os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not seed:
        raise RuntimeError(
            "No key material for integration token encryption - set INTEGRATION_TOKEN_KEY "
            "or SUPABASE_JWT_SECRET"
        )
    derived = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())
    return Fernet(derived)


def encrypt_token(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str | None:
    """Returns None (not an exception) when the token can't be decrypted -
    e.g. the encryption key rotated, or a legacy plaintext row. Callers
    treat None as 'not connected'."""
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None


def upsert_integration(
    user_id: str,
    provider: str,
    access_token: str,
    refresh_token: str | None,
    token_expiry: str | None,
    connected_email: str | None,
    calendar_id: str = "primary",
) -> dict:
    """Insert or replace this user's connection for `provider`. A re-connect
    without a new refresh_token (Google only returns one on first consent
    unless prompt=consent is forced) keeps the previously stored one."""
    db = get_supabase()
    existing = (
        db.table("user_integrations").select("id, refresh_token")
        .eq("user_id", user_id).eq("provider", provider).limit(1).execute().data
    )
    enc_refresh = encrypt_token(refresh_token) if refresh_token else (
        existing[0]["refresh_token"] if existing else ""
    )
    row = {
        "user_id": user_id,
        "provider": provider,
        "access_token": encrypt_token(access_token),
        "refresh_token": enc_refresh,
        "token_expiry": token_expiry,
        "connected_email": connected_email,
        "calendar_id": calendar_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if existing:
        result = db.table("user_integrations").update(row).eq("id", existing[0]["id"]).execute()
    else:
        result = db.table("user_integrations").insert(row).execute()
    if not result.data:
        raise RuntimeError("Failed to save integration")
    return result.data[0]


def get_integration(user_id: str, provider: str) -> dict | None:
    """Returns the row with access_token/refresh_token DECRYPTED, or None if
    not connected (including rows whose refresh token no longer decrypts)."""
    db = get_supabase()
    result = (
        db.table("user_integrations").select("*")
        .eq("user_id", user_id).eq("provider", provider).limit(1).execute()
    )
    if not result.data:
        return None
    row = dict(result.data[0])
    refresh = decrypt_token(row.get("refresh_token") or "")
    if not refresh:
        return None
    row["refresh_token"] = refresh
    row["access_token"] = decrypt_token(row.get("access_token") or "")
    return row


def update_access_token(user_id: str, provider: str, access_token: str, token_expiry: str) -> None:
    db = get_supabase()
    db.table("user_integrations").update({
        "access_token": encrypt_token(access_token),
        "token_expiry": token_expiry,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", user_id).eq("provider", provider).execute()


def delete_integration(user_id: str, provider: str) -> None:
    db = get_supabase()
    db.table("user_integrations").delete().eq("user_id", user_id).eq("provider", provider).execute()
