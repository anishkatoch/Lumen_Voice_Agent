from supabase import create_client, Client
from app.core.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

_supabase: Client | None = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _supabase


def reset_supabase() -> None:
    """Drop the cached client so the next get_supabase() call opens a fresh
    connection pool. The process-lifetime client's pooled HTTP/2 connection
    can go stale between calls - the remote host closes it out from under
    us - and fails with httpx.TransportError on the next reuse; retrying on
    the same client would just hit that same dead connection again."""
    global _supabase
    _supabase = None
