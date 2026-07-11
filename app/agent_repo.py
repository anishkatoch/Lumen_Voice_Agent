from datetime import datetime, timezone
from app.database import get_supabase

ADMIN_EMAIL = "katochaneesh@gmail.com"
MAX_STORAGE_BYTES = 5 * 1024 * 1024  # default — overridden by admin config


def get_agent_kb_limit() -> int:
    """Return the current agent KB limit in bytes (reads from admin config)."""
    try:
        from app.admin_config_service import get as get_config
        return int(get_config("agent_kb_max_mb")) * 1024 * 1024
    except Exception:
        return MAX_STORAGE_BYTES


# ── Default agents seeded for every new user ─────────────────────────────────

_DEFAULT_AGENTS = [
    {
        "name": "Meeting Assistant",
        "description": "Schedules meetings, checks calendar availability, and sets up appointments automatically.",
        "system_prompt": (
            "You are a professional meeting scheduling assistant. "
            "Your job is to help users book meetings, check calendar availability, "
            "and set up appointments. Be concise and friendly. "
            "When someone wants to schedule a meeting, ask for: their name, preferred date and time, "
            "meeting purpose, and duration. Confirm all details before booking. "
            "Respond naturally as if in a phone conversation — no bullet points or markdown."
        ),
        "first_message_enabled": True,
        "first_message": "Hello! I'm your meeting assistant. How can I help you schedule something today?",
    },
    {
        "name": "HR Support Agent",
        "description": "Answers HR queries, handles interview scheduling, and supports employee onboarding.",
        "system_prompt": (
            "You are a helpful HR support agent. "
            "You assist employees and candidates with HR-related questions including: "
            "interview scheduling, onboarding steps, leave policies, benefits, and general HR inquiries. "
            "Be empathetic, professional, and concise. "
            "For interview scheduling, collect: candidate name, contact details, role applied for, "
            "and preferred interview slot. Confirm everything before finalizing. "
            "Respond naturally for a voice conversation — no bullet points or markdown."
        ),
        "first_message_enabled": True,
        "first_message": "Hi there! This is the HR support line. How can I assist you today?",
    },
]


def seed_default_agents(user_id: str) -> None:
    """Ensure each default agent exists for the user (by name); skips if already present."""
    db = get_supabase()
    existing_names = {
        r["name"]
        for r in db.table("agents").select("name").eq("user_id", user_id).execute().data
    }
    for template in _DEFAULT_AGENTS:
        if template["name"] not in existing_names:
            db.table("agents").insert({"user_id": user_id, **template}).execute()


# ── Agent CRUD ────────────────────────────────────────────────────────────────

def create_agent(user_id: str, name: str, description: str | None = None,
                 system_prompt: str | None = None, barge_in_sensitivity: float = 0.20) -> dict:
    db = get_supabase()
    result = db.table("agents").insert({
        "user_id": user_id,
        "name": name,
        "description": description,
        "system_prompt": system_prompt,
        "barge_in_sensitivity": barge_in_sensitivity,
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to create agent")
    return result.data[0]


def get_agents(user_id: str) -> list[dict]:
    db = get_supabase()
    return db.table("agents").select("*").eq("user_id", user_id).order("created_at", desc=False).execute().data


def get_agent(agent_id: str) -> dict | None:
    db = get_supabase()
    result = db.table("agents").select("*").eq("id", agent_id).limit(1).execute()
    return result.data[0] if result.data else None


def duplicate_agent(agent_id: str, user_id: str) -> dict:
    db = get_supabase()
    original = db.table("agents").select("*").eq("id", agent_id).eq("user_id", user_id).limit(1).execute()
    if not original.data:
        raise RuntimeError("Agent not found")
    src = original.data[0]
    result = db.table("agents").insert({
        "user_id": user_id,
        "name": f"{src['name']} (copy)",
        "description": src.get("description"),
        "system_prompt": src.get("system_prompt"),
        "barge_in_sensitivity": src.get("barge_in_sensitivity", 0.20),
        "first_message_enabled": src.get("first_message_enabled", True),
        "first_message": src.get("first_message"),
        "is_active": src.get("is_active", True),
        "kb_instructions": src.get("kb_instructions"),
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to duplicate agent")
    return result.data[0]


def update_agent(agent_id: str, user_id: str, **fields) -> dict:
    allowed = {"name", "description", "system_prompt", "barge_in_sensitivity",
               "first_message_enabled", "first_message", "is_active", "kb_instructions",
               "selected_kb_doc_ids", "hr_instructions"}
    update = {k: v for k, v in fields.items() if k in allowed}
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    db = get_supabase()
    result = db.table("agents").update(update).eq("id", agent_id).eq("user_id", user_id).execute()
    if not result.data:
        raise RuntimeError("Agent not found or not owned by user")
    return result.data[0]


def delete_agent(agent_id: str, user_id: str) -> None:
    db = get_supabase()
    # Cascade deletes documents + chunks via FK
    db.table("agents").delete().eq("id", agent_id).eq("user_id", user_id).execute()


# ── Document management ───────────────────────────────────────────────────────

def get_agent_storage_used(agent_id: str) -> int:
    """Total bytes of all documents for this agent."""
    db = get_supabase()
    rows = db.table("agent_documents").select("file_size").eq("agent_id", agent_id).execute().data
    return sum(r["file_size"] for r in rows)


def create_agent_document(agent_id: str, user_id: str, file_name: str,
                           file_size: int, file_type: str, scope: str, content: str) -> dict:
    db = get_supabase()
    result = db.table("agent_documents").insert({
        "agent_id": agent_id,
        "user_id": user_id,
        "file_name": file_name,
        "file_size": file_size,
        "file_type": file_type,
        "scope": scope,
        "content": content,
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to create document record")
    return result.data[0]


def get_agent_documents(agent_id: str) -> list[dict]:
    db = get_supabase()
    return db.table("agent_documents").select("id, file_name, file_size, file_type, scope, created_at").eq("agent_id", agent_id).order("created_at", desc=False).execute().data


def delete_agent_document(doc_id: str, agent_id: str) -> None:
    db = get_supabase()
    # Cascade deletes chunks via FK
    db.table("agent_documents").delete().eq("id", doc_id).eq("agent_id", agent_id).execute()


# ── KB Permissions ────────────────────────────────────────────────────────────

def get_kb_permission(user_id: str) -> bool:
    db = get_supabase()
    result = db.table("user_kb_permissions").select("can_upload_global_kb").eq("user_id", user_id).limit(1).execute()
    if not result.data:
        return False
    return bool(result.data[0].get("can_upload_global_kb", False))


def create_kb_request(user_id: str, user_email: str) -> dict:
    db = get_supabase()
    # Only one pending request per user at a time
    existing = db.table("kb_access_requests").select("id, status").eq("user_id", user_id).eq("status", "pending").limit(1).execute().data
    if existing:
        return existing[0]
    result = db.table("kb_access_requests").insert({
        "user_id": user_id,
        "user_email": user_email,
        "status": "pending",
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to create KB request")
    return result.data[0]


def get_kb_requests() -> list[dict]:
    db = get_supabase()
    return db.table("kb_access_requests").select("*").order("created_at", desc=True).execute().data


def resolve_kb_request(request_id: str, admin_user_id: str, approve: bool) -> dict:
    db = get_supabase()
    new_status = "approved" if approve else "rejected"
    result = db.table("kb_access_requests").update({
        "status": new_status,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "resolved_by": admin_user_id,
    }).eq("id", request_id).execute()
    if not result.data:
        raise RuntimeError("Request not found")

    if approve:
        # Get user_id from request to grant permission
        req = result.data[0]
        _grant_kb_permission(req["user_id"], admin_user_id)

    return result.data[0]


def _grant_kb_permission(user_id: str, granted_by: str) -> None:
    db = get_supabase()
    existing = db.table("user_kb_permissions").select("user_id").eq("user_id", user_id).limit(1).execute().data
    if existing:
        db.table("user_kb_permissions").update({
            "can_upload_global_kb": True,
            "granted_at": datetime.now(timezone.utc).isoformat(),
            "granted_by": granted_by,
        }).eq("user_id", user_id).execute()
    else:
        db.table("user_kb_permissions").insert({
            "user_id": user_id,
            "can_upload_global_kb": True,
            "granted_at": datetime.now(timezone.utc).isoformat(),
            "granted_by": granted_by,
        }).execute()


def revoke_kb_permission(user_id: str) -> None:
    db = get_supabase()
    db.table("user_kb_permissions").update({
        "can_upload_global_kb": False,
    }).eq("user_id", user_id).execute()


# ── Agent Functions ───────────────────────────────────────────────────────────

def get_agent_functions(agent_id: str) -> list[dict]:
    db = get_supabase()
    return db.table("agent_functions").select("*").eq("agent_id", agent_id).order("created_at", desc=False).execute().data


def create_agent_function(
    agent_id: str,
    name: str,
    description: str | None,
    method: str,
    url: str,
    timeout_ms: int,
    headers: dict,
    query_params: dict,
    body_schema: str | None,
    payload_args_only: bool,
    parameters: list | None = None,
    trigger_type: str = "llm_tool_call",
) -> dict:
    db = get_supabase()
    result = db.table("agent_functions").insert({
        "agent_id":          agent_id,
        "name":              name,
        "description":       description,
        "method":            method,
        "url":               url,
        "timeout_ms":        timeout_ms,
        "headers":           headers,
        "query_params":      query_params,
        "body_schema":       body_schema,
        "payload_args_only": payload_args_only,
        "parameters":        parameters or [],
        "trigger_type":      trigger_type,
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to create agent function")
    return result.data[0]


def update_agent_function(func_id: str, agent_id: str, **fields) -> dict:
    allowed = {"name", "description", "method", "url", "timeout_ms",
               "headers", "query_params", "body_schema", "payload_args_only",
               "parameters", "trigger_type"}
    update = {k: v for k, v in fields.items() if k in allowed}
    db = get_supabase()
    result = db.table("agent_functions").update(update).eq("id", func_id).eq("agent_id", agent_id).execute()
    if not result.data:
        raise RuntimeError("Function not found")
    return result.data[0]


def delete_agent_function(func_id: str, agent_id: str) -> None:
    db = get_supabase()
    db.table("agent_functions").delete().eq("id", func_id).eq("agent_id", agent_id).execute()


def get_kb_permitted_users() -> list[dict]:
    db = get_supabase()
    return db.table("user_kb_permissions").select("*").eq("can_upload_global_kb", True).execute().data
