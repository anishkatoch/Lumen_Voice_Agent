import asyncio
import json
import os
import time

import httpx
import jwt
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from typing import List, Optional

ADMIN_EMAIL = "katochaneesh@gmail.com"

from app.config import (
    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_JWT_SECRET,
    WEBHOOK_SECRET, STORAGE_BUCKET, CORS_ORIGINS,
    VAD_SENSITIVITY, VAD_BARGE_IN_SENSITIVITY, VAD_SILENCE_LIMIT, VAD_MIN_SPEECH,
    MEMORY_UPDATE_EVERY,
)

app = FastAPI(title="Voice Agent API")


@app.on_event("startup")
async def _prewarm():
    """Pre-warm OpenAI and ElevenLabs TCP connections so first user call is fast."""
    async def _warm_openai():
        try:
            from openai import AsyncOpenAI
            from app.config import OPENAI_API_KEY
            c = AsyncOpenAI(api_key=OPENAI_API_KEY)
            await c.chat.completions.create(
                model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], max_tokens=1
            )
            print("[Prewarm] OpenAI ✓")
        except Exception as e:
            print(f"[Prewarm] OpenAI failed: {e}")

    async def _warm_elevenlabs():
        try:
            from elevenlabs.client import ElevenLabs
            from app.config import ELEVENLABS_API_KEY
            client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
            await asyncio.to_thread(client.voices.get_all)
            print("[Prewarm] ElevenLabs ✓")
        except Exception as e:
            print(f"[Prewarm] ElevenLabs failed: {e}")

    asyncio.create_task(_warm_openai())
    asyncio.create_task(_warm_elevenlabs())

    # HR outbound call scheduler — checks every 60s for due interviews
    from app.hr_scheduler import run_scheduler
    asyncio.create_task(run_scheduler())

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

# ─── JWT Verification (supports both HS256 and ES256) ────────────────
_jwks_cache: Optional[dict] = None

async def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache:
        return _jwks_cache
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")
        _jwks_cache = resp.json()
    return _jwks_cache

async def decode_supabase_token(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "HS256")
    kid = header.get("kid")

    if alg != "HS256" and kid:
        jwks = await _get_jwks()
        for key_data in jwks.get("keys", []):
            if key_data.get("kid") == kid:
                if alg.startswith("ES"):
                    public_key = ECAlgorithm.from_jwk(json.dumps(key_data))
                else:
                    public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
                return jwt.decode(token, public_key, algorithms=[alg], options={"verify_aud": False})
        raise jwt.InvalidTokenError("No matching key found in JWKS")

    if not SUPABASE_JWT_SECRET:
        raise jwt.InvalidTokenError("SUPABASE_JWT_SECRET not configured")
    return jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], options={"verify_aud": False})


# ─── Pydantic Models ────────────────────────────────────────────────

class SignUpRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None

class SignInRequest(BaseModel):
    email: EmailStr
    password: str

class AuthResponse(BaseModel):
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    user: Optional[dict] = None
    message: Optional[str] = None

class RefreshRequest(BaseModel):
    refresh_token: str


# ─── JWT Dependency ──────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    token = credentials.credentials
    try:
        return await decode_supabase_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# ─── Auth Routes ─────────────────────────────────────────────────────

@app.post("/auth/signup", response_model=AuthResponse)
async def sign_up(body: SignUpRequest):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "password": body.password, "data": {"full_name": body.full_name}},
        )
    data = resp.json()
    if resp.status_code != 200 or data.get("error"):
        msg = data.get("error_description") or data.get("msg") or "Signup failed"
        raise HTTPException(status_code=400, detail=msg)
    # Supabase returns 200 with no access_token for duplicate emails (user enumeration protection)
    if "access_token" not in data:
        # Check if user already exists by attempting sign-in — if it succeeds, email is taken
        async with httpx.AsyncClient() as client:
            check = await client.post(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
                headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                json={"email": body.email, "password": body.password},
            )
        if check.status_code == 200:
            raise HTTPException(status_code=400, detail="An account with this email already exists. Please sign in instead.")
        return AuthResponse(message="confirmation_required")
    return AuthResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        user={
            "id": data["user"]["id"],
            "email": data["user"]["email"],
            "full_name": data["user"].get("user_metadata", {}).get("full_name"),
        },
    )


class GuestRequest(BaseModel):
    name: str
    email: EmailStr

@app.post("/auth/guest", response_model=AuthResponse)
async def guest_login(body: GuestRequest):
    import secrets
    from app.config import SUPABASE_SERVICE_ROLE_KEY

    # Generate a random password — guest never needs to know it
    random_password = secrets.token_urlsafe(24)

    # Use admin API to create user with email auto-confirmed (no OTP needed)
    async with httpx.AsyncClient() as client:
        create = await client.post(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "email": body.email,
                "password": random_password,
                "email_confirm": True,
                "user_metadata": {"full_name": body.name, "is_guest": True},
            },
        )

    created = create.json()

    # If email already exists, just sign them in directly
    if create.status_code != 200:
        msg = created.get("msg") or created.get("error") or ""
        if "already" not in msg.lower() and "exists" not in msg.lower():
            raise HTTPException(status_code=400, detail="Could not create guest account. Try a different email.")

    # Sign in to get tokens (works whether newly created or already exists)
    async with httpx.AsyncClient() as client:
        signin = await client.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "password": random_password},
        )

    signin_data = signin.json()
    if signin.status_code != 200 or not signin_data.get("access_token"):
        # Email exists with a different password — account already registered
        raise HTTPException(status_code=400, detail="This email is already registered. Please sign in instead.")

    return AuthResponse(
        access_token=signin_data["access_token"],
        refresh_token=signin_data["refresh_token"],
        user={
            "id": signin_data["user"]["id"],
            "email": signin_data["user"]["email"],
            "full_name": body.name,
        },
    )


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class VerifyOtpRequest(BaseModel):
    email: EmailStr
    token: str
    password: str

class VerifySignupOtpRequest(BaseModel):
    email: EmailStr
    token: str

@app.post("/auth/verify-signup-otp", response_model=AuthResponse)
async def verify_signup_otp(body: VerifySignupOtpRequest):
    """Verify OTP for new account signup — no password update, just return tokens."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/verify",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "token": body.token, "type": "signup"},
        )
    data = resp.json()
    if resp.status_code != 200 or not data.get("access_token"):
        msg = data.get("msg") or data.get("error_description") or "Invalid or expired OTP"
        raise HTTPException(status_code=400, detail=msg)

    return AuthResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        user={
            "id": data["user"]["id"],
            "email": data["user"]["email"],
            "full_name": data["user"].get("user_metadata", {}).get("full_name"),
        },
    )


@app.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/otp",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "create_user": False},
        )
    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a few minutes and try again.")
    return {"message": "If an account exists for this email, a 6-digit OTP has been sent."}

@app.post("/auth/verify-otp")
async def verify_otp(body: VerifyOtpRequest):
    # Step 1: verify OTP to get a session
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/verify",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "token": body.token, "type": "email"},
        )
    data = resp.json()
    if resp.status_code != 200 or not data.get("access_token"):
        msg = data.get("msg") or data.get("error_description") or "Invalid or expired OTP"
        raise HTTPException(status_code=400, detail=msg)

    session_token = data["access_token"]

    # Step 2: use session token to update password
    async with httpx.AsyncClient() as client:
        update = await client.put(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {session_token}",
                "Content-Type": "application/json",
            },
            json={"password": body.password},
        )
    if update.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to update password")

    return {"message": "Password updated successfully"}


@app.post("/auth/signin", response_model=AuthResponse)
async def sign_in(body: SignInRequest):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "password": body.password},
        )
    data = resp.json()
    if resp.status_code != 200 or data.get("error"):
        msg = data.get("error_description") or data.get("msg") or "Invalid credentials"
        raise HTTPException(status_code=401, detail=msg)
    return AuthResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        user={
            "id": data["user"]["id"],
            "email": data["user"]["email"],
            "full_name": data["user"].get("user_metadata", {}).get("full_name"),
        },
    )


@app.post("/auth/refresh")
async def refresh_token(body: RefreshRequest):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"refresh_token": body.refresh_token},
        )
    data = resp.json()
    if resp.status_code != 200 or "error" in data:
        raise HTTPException(status_code=401, detail="Refresh token invalid or expired")
    return {"access_token": data["access_token"], "refresh_token": data["refresh_token"]}


@app.post("/auth/signout")
async def sign_out(current_user: dict = Depends(get_current_user)):
    return {"message": "Signed out successfully"}


# ─── Protected REST Routes ────────────────────────────────────────────

@app.get("/api/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user.get("sub"),
        "email": current_user.get("email"),
        "full_name": current_user.get("user_metadata", {}).get("full_name"),
        "role": current_user.get("role", "authenticated"),
    }


@app.get("/api/conversations")
async def list_conversations(current_user: dict = Depends(get_current_user)):
    from app.conversation_repo import get_conversations
    return await asyncio.to_thread(get_conversations, current_user["sub"])


@app.get("/api/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, current_user: dict = Depends(get_current_user)):
    from app.conversation_repo import get_conversation_by_id
    from app.message_repo import get_conversation_history
    convo = await asyncio.to_thread(get_conversation_by_id, conversation_id)
    if not convo or convo.get("user_id") != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await asyncio.to_thread(get_conversation_history, conversation_id)


@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.get("/api/status")
def read_root():
    return {"message": "API is running"}


# ─── Agent Pydantic Models ────────────────────────────────────────────

class AgentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    barge_in_sensitivity: float = 0.20

class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    barge_in_sensitivity: Optional[float] = None
    first_message_enabled: Optional[bool] = None
    first_message: Optional[str] = None
    is_active: Optional[bool] = None
    kb_instructions: Optional[str] = None
    hr_instructions: Optional[str] = None
    selected_kb_doc_ids: Optional[List[str]] = None

class AgentFunctionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    method: str = "POST"
    url: str = ""
    timeout_ms: int = 120000
    headers: dict = {}
    query_params: dict = {}
    body_schema: Optional[str] = None
    payload_args_only: bool = False
    parameters: Optional[List[dict]] = None
    trigger_type: str = "llm_tool_call"

class AgentFunctionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    method: Optional[str] = None
    url: Optional[str] = None
    timeout_ms: Optional[int] = None
    headers: Optional[dict] = None
    query_params: Optional[dict] = None
    body_schema: Optional[str] = None
    payload_args_only: Optional[bool] = None
    parameters: Optional[List[dict]] = None
    trigger_type: Optional[str] = None

class KBAccessRequest(BaseModel):
    user_email: str

class PhoneNumberCreate(BaseModel):
    phone_number: str
    friendly_name: Optional[str] = None

class PhoneNumberUpdate(BaseModel):
    friendly_name: Optional[str] = None
    is_active: Optional[bool] = None

class PhoneNumberAttach(BaseModel):
    agent_id: str

class TwilioSettingsUpdate(BaseModel):
    account_sid: Optional[str] = None
    auth_token: Optional[str] = None
    webhook_base_url: Optional[str] = None

class CandidateCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    role: Optional[str] = None
    resume_text: Optional[str] = None
    notes: Optional[str] = None

class CandidateUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    resume_text: Optional[str] = None
    notes: Optional[str] = None

class InterviewCreate(BaseModel):
    candidate_id: str
    agent_id: str
    scheduled_at: str
    call_lead_minutes: int = 30
    specific_questions: Optional[str] = None

class InterviewUpdate(BaseModel):
    scheduled_at: Optional[str] = None
    call_lead_minutes: Optional[int] = None
    specific_questions: Optional[str] = None
    agent_id: Optional[str] = None
    status: Optional[str] = None


# ─── Agent Routes ─────────────────────────────────────────────────────

@app.get("/api/agents")
async def list_agents(current_user: dict = Depends(get_current_user)):
    from app.agent_repo import get_agents, seed_default_agents
    await asyncio.to_thread(seed_default_agents, current_user["sub"])
    return await asyncio.to_thread(get_agents, current_user["sub"])


@app.post("/api/agents", status_code=201)
async def create_agent(body: AgentCreate, current_user: dict = Depends(get_current_user)):
    from app.agent_repo import create_agent
    return await asyncio.to_thread(
        create_agent, current_user["sub"], body.name, body.description,
        body.system_prompt, body.barge_in_sensitivity,
    )


@app.get("/api/agents/{agent_id}")
async def get_agent_detail(agent_id: str, current_user: dict = Depends(get_current_user)):
    from app.agent_repo import get_agent
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@app.put("/api/agents/{agent_id}")
async def update_agent_detail(agent_id: str, body: AgentUpdate, current_user: dict = Depends(get_current_user)):
    from app.agent_repo import update_agent
    try:
        return await asyncio.to_thread(
            update_agent, agent_id, current_user["sub"],
            **body.model_dump(exclude_unset=True),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/agents/{agent_id}/duplicate", status_code=201)
async def duplicate_agent_route(agent_id: str, current_user: dict = Depends(get_current_user)):
    from app.agent_repo import duplicate_agent
    try:
        return await asyncio.to_thread(duplicate_agent, agent_id, current_user["sub"])
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/agents/{agent_id}", status_code=204)
async def delete_agent_route(agent_id: str, current_user: dict = Depends(get_current_user)):
    from app.agent_repo import get_agent, delete_agent
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    await asyncio.to_thread(delete_agent, agent_id, current_user["sub"])


# ─── Agent Documents ──────────────────────────────────────────────────

@app.get("/api/agents/{agent_id}/documents")
async def list_agent_documents(agent_id: str, current_user: dict = Depends(get_current_user)):
    from app.agent_repo import get_agent, get_agent_documents, get_agent_storage_used
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    docs = await asyncio.to_thread(get_agent_documents, agent_id)
    used = await asyncio.to_thread(get_agent_storage_used, agent_id)
    return {"documents": docs, "storage_used_bytes": used, "storage_limit_bytes": 5 * 1024 * 1024}


@app.post("/api/agents/{agent_id}/documents", status_code=201)
async def upload_agent_document(
    agent_id: str,
    file: UploadFile = File(...),
    scope: str = Form("personal"),
    current_user: dict = Depends(get_current_user),
):
    from app.agent_repo import (
        get_agent, get_agent_storage_used, create_agent_document,
        get_kb_permission, MAX_STORAGE_BYTES,
    )
    from app.agent_doc_processor import extract_text, process_document

    # Ownership check
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Scope check for global uploads
    if scope == "global":
        has_permission = await asyncio.to_thread(get_kb_permission, current_user["sub"])
        if not has_permission:
            raise HTTPException(status_code=403, detail="Global KB upload requires admin approval")

    # Validate file type
    fname = file.filename or ""
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext not in ("pdf", "docx", "txt"):
        raise HTTPException(status_code=400, detail="Only PDF, DOCX, and TXT files are supported")

    # Read file
    file_bytes = await file.read()
    file_size = len(file_bytes)

    # Check storage limit (5MB total per agent)
    used = await asyncio.to_thread(get_agent_storage_used, agent_id)
    if used + file_size > MAX_STORAGE_BYTES:
        remaining = MAX_STORAGE_BYTES - used
        raise HTTPException(
            status_code=413,
            detail=f"Storage limit exceeded. You have {remaining / 1024:.1f} KB remaining out of 5 MB.",
        )

    # Extract text
    try:
        content = await asyncio.to_thread(extract_text, file_bytes, ext)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to extract text: {e}")

    if not content.strip():
        raise HTTPException(status_code=422, detail="Document appears to be empty or unreadable")

    # Store document record
    doc = await asyncio.to_thread(
        create_agent_document, agent_id, current_user["sub"],
        fname, file_size, ext, scope, content,
    )

    # Process chunks + embeddings in background
    async def _process():
        try:
            count = await asyncio.to_thread(
                process_document, doc["id"], agent_id, current_user["sub"], scope, content,
            )
            print(f"[Agent Doc] Processed {fname}: {count} chunks")
        except Exception as e:
            print(f"[Agent Doc] Processing failed for {fname}: {e}")
    asyncio.create_task(_process())

    return {**doc, "chunk_processing": "background"}


@app.delete("/api/agents/{agent_id}/documents/{doc_id}", status_code=204)
async def delete_agent_document(agent_id: str, doc_id: str, current_user: dict = Depends(get_current_user)):
    from app.agent_repo import get_agent, delete_agent_document
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    await asyncio.to_thread(delete_agent_document, doc_id, agent_id)


# ─── Agent Functions ──────────────────────────────────────────────────

@app.get("/api/agents/{agent_id}/functions")
async def list_agent_functions(agent_id: str, current_user: dict = Depends(get_current_user)):
    from app.agent_repo import get_agent, get_agent_functions
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    return await asyncio.to_thread(get_agent_functions, agent_id)


@app.post("/api/agents/{agent_id}/functions", status_code=201)
async def create_agent_function_route(
    agent_id: str, body: AgentFunctionCreate, current_user: dict = Depends(get_current_user)
):
    from app.agent_repo import get_agent, create_agent_function
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    return await asyncio.to_thread(
        create_agent_function, agent_id,
        body.name, body.description, body.method, body.url,
        body.timeout_ms, body.headers, body.query_params,
        body.body_schema, body.payload_args_only,
        body.parameters or [], body.trigger_type,
    )


@app.put("/api/agents/{agent_id}/functions/{func_id}")
async def update_agent_function_route(
    agent_id: str, func_id: str, body: AgentFunctionUpdate,
    current_user: dict = Depends(get_current_user)
):
    from app.agent_repo import get_agent, update_agent_function
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        return await asyncio.to_thread(
            update_agent_function, func_id, agent_id,
            **{k: v for k, v in body.model_dump().items() if v is not None},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/agents/{agent_id}/functions/{func_id}", status_code=204)
async def delete_agent_function_route(
    agent_id: str, func_id: str, current_user: dict = Depends(get_current_user)
):
    from app.agent_repo import get_agent, delete_agent_function
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    await asyncio.to_thread(delete_agent_function, func_id, agent_id)


# ─── Twilio Phone Numbers ──────────────────────────────────────────────

@app.get("/api/twilio/numbers")
async def list_phone_numbers(current_user: dict = Depends(get_current_user)):
    from app.twilio_repo import get_phone_numbers
    numbers = await asyncio.to_thread(get_phone_numbers, current_user["sub"])
    return numbers


@app.post("/api/twilio/numbers", status_code=201)
async def add_phone_number(
    body: PhoneNumberCreate, current_user: dict = Depends(get_current_user)
):
    from app.twilio_repo import create_phone_number
    from app.user_twilio_settings_repo import get_twilio_settings
    from app.twilio_webhook_config import configure_number_webhook
    try:
        number = await asyncio.to_thread(
            create_phone_number, current_user["sub"], body.phone_number, body.friendly_name
        )
        # Auto-configure Twilio webhook if credentials are already set
        settings = await asyncio.to_thread(get_twilio_settings, current_user["sub"])
        sid = settings.get("account_sid", "")
        token = settings.get("auth_token", "")
        base = (settings.get("webhook_base_url") or "").strip()
        if sid and token and base:
            asyncio.create_task(configure_number_webhook(
                current_user["sub"], number["id"], sid, token, base
            ))
        return number
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/twilio/numbers/{number_id}")
async def update_phone_number_route(
    number_id: str, body: PhoneNumberUpdate, current_user: dict = Depends(get_current_user)
):
    from app.twilio_repo import update_phone_number
    try:
        return await asyncio.to_thread(
            update_phone_number,
            number_id,
            current_user["sub"],
            **body.model_dump(exclude_none=True),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/twilio/numbers/{number_id}/attach")
async def attach_number_route(
    number_id: str, body: PhoneNumberAttach, current_user: dict = Depends(get_current_user)
):
    from app.twilio_repo import attach_number_to_agent
    try:
        return await asyncio.to_thread(
            attach_number_to_agent, number_id, current_user["sub"], body.agent_id
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/twilio/numbers/{number_id}/attach")
async def detach_number_route(
    number_id: str, current_user: dict = Depends(get_current_user)
):
    from app.twilio_repo import detach_number_from_agent
    try:
        return await asyncio.to_thread(
            detach_number_from_agent, number_id, current_user["sub"]
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/twilio/numbers/{number_id}", status_code=204)
async def delete_phone_number_route(
    number_id: str, current_user: dict = Depends(get_current_user)
):
    from app.twilio_repo import delete_phone_number
    await asyncio.to_thread(delete_phone_number, number_id, current_user["sub"])


@app.get("/api/twilio/settings")
async def get_twilio_settings_route(current_user: dict = Depends(get_current_user)):
    from app.user_twilio_settings_repo import get_twilio_settings
    settings = await asyncio.to_thread(get_twilio_settings, current_user["sub"])
    # Never return raw auth token — mask it
    masked = {**settings}
    if masked.get("auth_token"):
        masked["auth_token"] = "•" * 8
        masked["auth_token_set"] = True
    else:
        masked["auth_token_set"] = False
    return masked


@app.put("/api/twilio/settings")
async def update_twilio_settings_route(
    body: TwilioSettingsUpdate, current_user: dict = Depends(get_current_user)
):
    from app.user_twilio_settings_repo import get_twilio_settings, upsert_twilio_settings
    from app.twilio_webhook_config import configure_all_user_numbers
    existing = await asyncio.to_thread(get_twilio_settings, current_user["sub"])
    # Keep existing auth_token if the request sends the masked placeholder
    auth_token = body.auth_token
    if auth_token and set(auth_token) == {"•"}:
        auth_token = existing.get("auth_token", "")
    saved = await asyncio.to_thread(
        upsert_twilio_settings,
        current_user["sub"],
        body.account_sid,
        auth_token,
        body.webhook_base_url,
    )
    # Auto-configure webhooks on all phone numbers if credentials are complete
    sid = body.account_sid or ""
    base = (body.webhook_base_url or "").strip()
    if sid and auth_token and base:
        asyncio.create_task(configure_all_user_numbers(
            current_user["sub"], sid, auth_token, base
        ))
    return saved


# ─── HR Candidates ────────────────────────────────────────────────────

@app.get("/api/hr/candidates")
async def list_candidates_route(current_user: dict = Depends(get_current_user)):
    from app.hr_repo import list_candidates
    return await asyncio.to_thread(list_candidates, current_user["sub"])


@app.post("/api/hr/candidates", status_code=201)
async def create_candidate_route(body: CandidateCreate, current_user: dict = Depends(get_current_user)):
    from app.hr_repo import create_candidate
    return await asyncio.to_thread(
        create_candidate, current_user["sub"],
        body.name, body.phone, body.email, body.role, body.resume_text, None, body.notes
    )


@app.post("/api/hr/candidates/{candidate_id}/resume")
async def upload_candidate_resume(
    candidate_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    from app.hr_repo import get_candidate, update_candidate
    candidate = await asyncio.to_thread(get_candidate, candidate_id, current_user["sub"])
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    content = await file.read()
    resume_text = ""
    fname = file.filename or ""
    try:
        if fname.lower().endswith(".pdf"):
            import fitz
            doc = fitz.open(stream=content, filetype="pdf")
            resume_text = "\n".join(page.get_text() for page in doc)
        elif fname.lower().endswith(".docx"):
            import docx, io
            d = docx.Document(io.BytesIO(content))
            resume_text = "\n".join(p.text for p in d.paragraphs)
        else:
            resume_text = content.decode("utf-8", errors="ignore")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read file: {e}")

    return await asyncio.to_thread(
        update_candidate, candidate_id, current_user["sub"],
        resume_text=resume_text.strip(), resume_file_name=fname
    )


@app.put("/api/hr/candidates/{candidate_id}")
async def update_candidate_route(
    candidate_id: str, body: CandidateUpdate, current_user: dict = Depends(get_current_user)
):
    from app.hr_repo import update_candidate
    try:
        return await asyncio.to_thread(
            update_candidate, candidate_id, current_user["sub"],
            **body.model_dump(exclude_none=True)
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/hr/candidates/{candidate_id}", status_code=204)
async def delete_candidate_route(candidate_id: str, current_user: dict = Depends(get_current_user)):
    from app.hr_repo import delete_candidate
    await asyncio.to_thread(delete_candidate, candidate_id, current_user["sub"])


# ─── HR Interviews ─────────────────────────────────────────────────────

@app.get("/api/hr/interviews")
async def list_interviews_route(current_user: dict = Depends(get_current_user)):
    from app.hr_repo import list_interviews
    return await asyncio.to_thread(list_interviews, current_user["sub"])


@app.post("/api/hr/interviews", status_code=201)
async def create_interview_route(body: InterviewCreate, current_user: dict = Depends(get_current_user)):
    from app.hr_repo import create_interview
    return await asyncio.to_thread(
        create_interview, current_user["sub"],
        body.candidate_id, body.agent_id, body.scheduled_at,
        body.call_lead_minutes, body.specific_questions
    )


@app.put("/api/hr/interviews/{interview_id}")
async def update_interview_route(
    interview_id: str, body: InterviewUpdate, current_user: dict = Depends(get_current_user)
):
    from app.hr_repo import update_interview
    try:
        return await asyncio.to_thread(
            update_interview, interview_id, current_user["sub"],
            **body.model_dump(exclude_none=True)
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/hr/interviews/{interview_id}", status_code=204)
async def delete_interview_route(interview_id: str, current_user: dict = Depends(get_current_user)):
    from app.hr_repo import delete_interview
    await asyncio.to_thread(delete_interview, interview_id, current_user["sub"])


# ─── Twilio Outbound (HR Interviews) ──────────────────────────────────

@app.get("/twilio/outbound/voice")
async def twilio_outbound_twiml(interview_id: str = "", request: Request = None):
    """Twilio fetches this TwiML when an outbound call connects."""
    from app.hr_repo import get_interview

    interview = await asyncio.to_thread(get_interview, interview_id) if interview_id else None
    if not interview:
        xml = '<?xml version="1.0"?><Response><Say>Interview not found.</Say></Response>'
        return Response(content=xml, media_type="application/xml")

    from app.user_twilio_settings_repo import get_twilio_settings
    settings = await asyncio.to_thread(get_twilio_settings, interview["user_id"])
    base_url = (settings.get("webhook_base_url") or "").strip().rstrip("/")
    if not base_url:
        xml = '<?xml version="1.0"?><Response><Say>Server not configured.</Say></Response>'
        return Response(content=xml, media_type="application/xml")

    host = base_url.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/twilio/stream"
    # Custom data must travel via <Parameter>, not a query string on the Stream
    # url — Twilio does not reliably forward URL query params to the WebSocket.
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="interview_id" value="{interview_id}" />
    </Stream>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


# ─── KB Access ────────────────────────────────────────────────────────

@app.get("/api/kb/my-permission")
async def my_kb_permission(current_user: dict = Depends(get_current_user)):
    from app.agent_repo import get_kb_permission
    allowed = await asyncio.to_thread(get_kb_permission, current_user["sub"])
    return {"can_upload_global_kb": allowed}


@app.post("/api/kb/request-access")
async def request_kb_access(body: KBAccessRequest, current_user: dict = Depends(get_current_user)):
    from app.agent_repo import create_kb_request
    result = await asyncio.to_thread(create_kb_request, current_user["sub"], body.user_email)
    return result


# ─── Admin Routes ─────────────────────────────────────────────────────

def _require_admin(current_user: dict) -> None:
    if current_user.get("email") != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Admin access required")


@app.get("/api/admin/kb-requests")
async def admin_list_kb_requests(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.agent_repo import get_kb_requests
    return await asyncio.to_thread(get_kb_requests)


@app.post("/api/admin/kb-requests/{request_id}/approve")
async def admin_approve_kb_request(request_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.agent_repo import resolve_kb_request
    try:
        return await asyncio.to_thread(resolve_kb_request, request_id, current_user["sub"], True)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/admin/kb-requests/{request_id}/reject")
async def admin_reject_kb_request(request_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.agent_repo import resolve_kb_request
    try:
        return await asyncio.to_thread(resolve_kb_request, request_id, current_user["sub"], False)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/admin/users/{user_id}/revoke-kb")
async def admin_revoke_kb(user_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.agent_repo import revoke_kb_permission
    await asyncio.to_thread(revoke_kb_permission, user_id)
    return {"status": "revoked", "user_id": user_id}


@app.get("/api/admin/kb-users")
async def admin_list_kb_users(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.agent_repo import get_kb_permitted_users
    return await asyncio.to_thread(get_kb_permitted_users)


@app.get("/api/admin/is-admin")
async def check_is_admin(current_user: dict = Depends(get_current_user)):
    return {"is_admin": current_user.get("email") == ADMIN_EMAIL}


# ─── Admin System Config ──────────────────────────────────────────────────────

@app.get("/api/admin/config")
async def get_admin_config(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    from app.admin_config_service import get_all
    return get_all()


@app.put("/api/admin/config")
async def update_admin_config(request: Request, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    body = await request.json()
    ALLOWED = {
        "stt_provider", "tts_provider", "tts_openai_voice", "tts_openai_model",
        "llm_provider", "llm_model", "llm_api_key", "llm_base_url",
        "user_kb_max_mb", "agent_kb_max_mb",
    }
    updates = {k: str(v) for k, v in body.items() if k in ALLOWED}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid config keys provided")
    from app.admin_config_service import save, get_all
    save(updates)
    return get_all()


@app.post("/api/ingest-all-dev")
async def ingest_all_documents_dev():
    """No-auth version for local testing only. Remove before going to production."""
    return await _ingest_all_impl()


@app.post("/api/ingest-all")
async def ingest_all_documents(current_user: dict = Depends(get_current_user)):
    return await _ingest_all_impl()


async def _ingest_all_impl():
    from app.doc_processor import ingest_document
    from app.database import get_supabase

    db = get_supabase()
    try:
        files = db.storage.from_(STORAGE_BUCKET).list()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list bucket: {e}")

    md_files = [f["name"] for f in files if f["name"].endswith(".md")]
    if not md_files:
        return {"status": "no .md files found in bucket", "bucket": STORAGE_BUCKET}

    results = []
    for file_name in md_files:
        try:
            chunks = await ingest_document(file_name)
            results.append({"file": file_name, "chunks": chunks, "status": "ok"})
            print(f"[Ingest] {file_name} → {chunks} chunks")
        except Exception as e:
            results.append({"file": file_name, "status": "error", "error": str(e)})
            print(f"[Ingest] {file_name} → ERROR: {e}")

    return {"ingested": results}


# ─── Webhook: Auto Document Ingestion ────────────────────────────────

@app.post("/webhook/new-document")
async def new_document_webhook(payload: dict, request: Request):
    from app.doc_processor import ingest_document

    if WEBHOOK_SECRET:
        auth_header = request.headers.get("authorization", "")
        if auth_header != f"Bearer {WEBHOOK_SECRET}":
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    record = payload.get("record", {})
    file_name = record.get("name") or payload.get("name") or payload.get("file_name")
    bucket_id = record.get("bucket_id", "")

    print(f"[Webhook] type={payload.get('type')} bucket={bucket_id} file={file_name}")

    if not file_name:
        return {"status": "ignored", "reason": "no file_name in payload"}
    if not file_name.endswith(".md"):
        return {"status": "ignored", "reason": f"not a .md file: {file_name}"}
    if bucket_id and bucket_id != STORAGE_BUCKET:
        return {"status": "ignored", "reason": f"wrong bucket: {bucket_id}"}

    async def _ingest_task():
        try:
            await ingest_document(file_name)
        except Exception as e:
            print(f"[Webhook] Ingest failed for {file_name}: {e}")
    asyncio.create_task(_ingest_task())
    print(f"[Webhook] Queued ingestion for: {file_name}")
    return {"status": "processing", "file": file_name}


# ─── Twilio Inbound Calling ───────────────────────────────────────────

@app.post("/twilio/voice")
async def twilio_voice_webhook(request: Request):
    """Twilio calls this when someone dials a connected number. Returns TwiML."""
    from app.twilio_repo import get_number_by_phone
    from app.user_twilio_settings_repo import get_twilio_settings

    form = await request.form()
    to_number = str(form.get("To", ""))
    print(f"[Twilio Voice] Inbound call, To={to_number!r}")

    # Look up the number record to find the owner's webhook base URL
    number_record = await asyncio.to_thread(get_number_by_phone, to_number)
    if not number_record or not number_record.get("agent_id"):
        print(f"[Twilio Voice] No active number_record with agent_id for To={to_number!r} (record={number_record})")
        xml = '<?xml version="1.0"?><Response><Say>This number is not connected to an agent.</Say></Response>'
        return Response(content=xml, media_type="application/xml")

    settings = await asyncio.to_thread(get_twilio_settings, number_record["user_id"])
    base_url = (settings.get("webhook_base_url") or "").strip().rstrip("/")
    if not base_url:
        print(f"[Twilio Voice] No webhook_base_url configured for user_id={number_record['user_id']!r}")
        xml = '<?xml version="1.0"?><Response><Say>This number is not configured yet.</Say></Response>'
        return Response(content=xml, media_type="application/xml")

    # Strip protocol — WebSocket URL needs just host+path
    host = base_url.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/twilio/stream"

    # Custom data must travel via <Parameter>, not a query string on the Stream
    # url — Twilio does not reliably forward URL query params to the WebSocket.
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="to" value="{to_number}" />
    </Stream>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/twilio/stream")
async def twilio_stream_endpoint(websocket: WebSocket):
    """Handles Twilio Media Streams — bidirectional audio for inbound and outbound phone calls."""
    from app.twilio_call_service import twilio_payload_to_pcm16k, text_to_speech_twilio
    from app.vad_service import detect_speech
    from app.deepgram_service import get_streaming_transcriber
    from app.llm_service import (
        generate_response_stream, build_system_prompt,
        FILLER_PHRASES, detect_tool_call, build_openai_tool,
        stream_tool_result_response, stream_text_as_sentences,
    )
    from app.elevenlabs_service import text_to_speech_stream
    from app.message_repo import save_message, get_session_messages
    from app.agent_repo import get_agent, get_agent_functions
    from app.memory_repo import get_user_memory
    from app.agent_rag_service import retrieve_agent_context, format_agent_context
    from app.user_kb_rag import retrieve_user_kb_context, format_user_kb_context
    import json, random, base64

    await websocket.accept()

    agent_id: str = ""
    user_id: str = ""
    interview_id: str = ""
    extra_system_context: str = ""  # injected for outbound HR calls
    greeting_override: str | None = None
    agent: dict | None = None
    conversation_id: str | None = None
    _tool_functions: list = []
    initial_memory = None

    stream_sid: str | None = None
    is_speaking = False     # True while TTS audio is being sent to Twilio
    is_processing = False   # True while LLM pipeline is running
    _call_ended = False     # tracks whether we've already marked the interview done
    _dg_stream = None       # active StreamingTranscriber (opened on first speech chunk)
    speech_chunks = 0
    silence_chunks = 0
    SPEECH_THRESHOLD = 3    # chunks needed to confirm speech started
    SILENCE_THRESHOLD = 25  # ~1.5 s at 8 kHz / 20 ms chunks

    async def _setup_call(custom_params: dict) -> bool:
        """Resolve agent/user/conversation from the Twilio 'start' event's
        customParameters. Twilio does not reliably forward query strings on the
        <Stream> url, so call context (to / interview_id) travels via <Parameter>
        tags instead, which arrive here once the stream actually starts."""
        nonlocal agent_id, user_id, interview_id, extra_system_context, greeting_override
        nonlocal agent, conversation_id, _tool_functions, initial_memory

        interview_id = custom_params.get("interview_id", "")
        to = custom_params.get("to", "")

        if interview_id:
            # Outbound HR call — load candidate + interview context
            from app.hr_repo import get_interview
            interview = await asyncio.to_thread(get_interview, interview_id)
            if not interview:
                print(f"[Twilio Stream] Closing: no interview found for interview_id={interview_id!r}")
                return False
            agent_id = interview["agent_id"]
            user_id = interview["user_id"]
            candidate = interview.get("hr_candidates") or {}
            c_name = candidate.get("name", "the candidate")
            c_role = candidate.get("role", "")
            c_resume = candidate.get("resume_text", "")
            c_notes = candidate.get("notes", "")
            questions = interview.get("specific_questions", "")
            call_lead = interview.get("call_lead_minutes", 30)
            sched_at_raw = interview.get("scheduled_at", "")
            sched_formatted = ""
            if sched_at_raw:
                try:
                    from datetime import datetime as _dt
                    _sdt = _dt.fromisoformat(sched_at_raw.replace("Z", "+00:00"))
                    sched_formatted = _sdt.strftime("%A, %B %d, %Y at %I:%M %p UTC")
                except Exception:
                    sched_formatted = sched_at_raw
            extra_system_context = (
                f"\n\n--- CURRENT CALL CONTEXT ---"
                f"\nYou are calling {c_name} for a{'n' if c_role and c_role[0].lower() in 'aeiou' else ''} {c_role or 'interview'} interview."
                + (f"\nThe interview is scheduled for: {sched_formatted}." if sched_formatted else "")
                + f"\nYou are calling {call_lead} minutes before the interview time as a pre-interview reminder."
                + (f"\n\nCandidate resume:\n{c_resume}" if c_resume else "")
                + (f"\n\nHR notes about this candidate:\n{c_notes}" if c_notes else "")
                + (f"\n\nSpecific questions to ask this candidate:\n{questions}" if questions else "")
                + "\n\nStart by greeting them by name and confirming you're calling from HR."
                + "\nMention the interview time when greeting them. Ask the specific questions listed. Be professional and friendly."
            )
            greeting_override = f"Hello, may I speak with {c_name}? This is an automated call from HR regarding your upcoming interview."
        else:
            # Inbound call — resolve via phone number
            from app.twilio_repo import get_number_by_phone
            print(f"[Twilio Stream] Inbound call, to={to!r}")
            number_record = await asyncio.to_thread(get_number_by_phone, to) if to else None
            if not number_record or not number_record.get("agent_id"):
                print(f"[Twilio Stream] Closing: no active number_record with agent_id for to={to!r} (record={number_record})")
                return False
            agent_id = number_record["agent_id"]
            user_id = number_record["user_id"]
            print(f"[Twilio Stream] Resolved agent_id={agent_id} user_id={user_id}")

        agent = await asyncio.to_thread(get_agent, agent_id)
        if not agent:
            print(f"[Twilio Stream] Closing: get_agent({agent_id!r}) returned None")
            return False
        print(f"[Twilio Stream] Agent loaded: {agent.get('name')}")

        # Create conversation record
        from app.database import get_supabase
        _db = get_supabase()
        def _create_conv():
            res = _db.table("conversations").insert({
                "user_id": user_id,
                "title": f"Phone call — {agent['name']}",
                "agent_id": agent_id,
            }).execute()
            return res.data[0]
        conversation = await asyncio.to_thread(_create_conv)
        conversation_id = conversation["id"]

        # Load tool functions and memory
        _all_fns = await asyncio.to_thread(get_agent_functions, agent_id)
        _tool_functions = [f for f in _all_fns if f.get("url") and f.get("trigger_type") == "llm_tool_call"]
        try:
            initial_memory = await asyncio.to_thread(get_user_memory, user_id)
        except Exception:
            initial_memory = None

        return True

    async def _send_mulaw(mulaw_bytes: bytes):
        """Send raw μ-law 8 kHz bytes to Twilio (base64-encoded)."""
        if not stream_sid:
            return
        payload = base64.b64encode(mulaw_bytes).decode()
        await websocket.send_text(json.dumps({
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": payload},
        }))

    async def _execute_webhook_twilio(fn: dict, args: dict) -> str:
        import httpx
        method = (fn.get("method") or "POST").upper()
        url = fn["url"]
        headers = fn.get("headers") or {}
        timeout_s = (fn.get("timeout_ms") or 30000) / 1000
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as hclient:
                if method == "GET":
                    resp = await hclient.get(url, params=args, headers=headers)
                elif method == "DELETE":
                    resp = await hclient.delete(url, params=args, headers=headers)
                else:
                    resp = await hclient.request(method, url, json=args, headers=headers)
            try:
                return json.dumps(resp.json())
            except Exception:
                return json.dumps({"response": resp.text, "status": resp.status_code})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _speak(text: str):
        """TTS → μ-law → send to Twilio, set/unset is_speaking flag."""
        nonlocal is_speaking
        is_speaking = True
        try:
            async for mulaw_chunk in text_to_speech_twilio(text):
                await _send_mulaw(mulaw_chunk)
        finally:
            is_speaking = False

    async def run_pipeline(user_message: str):
        nonlocal is_processing
        is_processing = True
        try:
            session_msgs = await asyncio.to_thread(get_session_messages, conversation_id)
            await asyncio.to_thread(save_message, conversation_id, user_id, "user", user_message)

            doc_ids = agent.get("selected_kb_doc_ids") or []
            rag_ctx = ""
            if doc_ids:
                raw = await asyncio.to_thread(
                    retrieve_agent_context, query=user_message, agent_id=agent_id, doc_ids=doc_ids
                )
                rag_ctx = format_agent_context(raw)
            kb_rag = await asyncio.to_thread(
                retrieve_user_kb_context, user_id=user_id, query=user_message
            )
            kb_ctx = format_user_kb_context(kb_rag) if kb_rag else ""
            full_rag = "\n\n".join(filter(None, [rag_ctx, kb_ctx]))

            base_system = (agent.get("system_prompt") or "") + extra_system_context
            system_str = build_system_prompt(
                base_system or None,
                initial_memory,
                full_rag,
                agent.get("kb_instructions") or None,
            )
            history = [{"role": m["role"], "content": m["content"]} for m in session_msgs]
            full_reply_parts: list[str] = []

            if _tool_functions:
                tools = [build_openai_tool(f) for f in _tool_functions]
                conv_msgs = history + [{"role": "user", "content": user_message}]
                text, tool_name, tool_args, call_id = await detect_tool_call(conv_msgs, system_str, tools)

                if tool_name:
                    filler = random.choice(FILLER_PHRASES)
                    await _speak(filler)
                    full_reply_parts.append(filler)
                    fn = next((f for f in _tool_functions if f["name"] == tool_name), None)
                    tool_result = await _execute_webhook_twilio(fn, tool_args) if fn else "{}"
                    async for sentence in stream_tool_result_response(
                        conv_msgs, system_str, tool_name, call_id, tool_args, tool_result
                    ):
                        await _speak(sentence)
                        full_reply_parts.append(sentence)
                else:
                    async for sentence in stream_text_as_sentences(text or ""):
                        await _speak(sentence)
                        full_reply_parts.append(sentence)
            else:
                async for sentence in generate_response_stream(
                    user_message=user_message,
                    session_history=history,
                    rag_context=full_rag,
                    long_term_memory=initial_memory,
                    system_prompt_override=agent.get("system_prompt") or None,
                    kb_instructions=agent.get("kb_instructions") or None,
                ):
                    await _speak(sentence)
                    full_reply_parts.append(sentence)

            full_reply = " ".join(full_reply_parts).strip()
            if full_reply:
                await asyncio.to_thread(save_message, conversation_id, user_id, "assistant", full_reply)
        finally:
            is_processing = False

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("event")

            if event == "start":
                stream_sid = data["start"]["streamSid"]
                custom_params = data["start"].get("customParameters") or {}
                print(f"[Twilio Stream] start event — customParameters={custom_params}")
                if not await _setup_call(custom_params):
                    break
                print(f"[Twilio] Call started — stream {stream_sid}, agent {agent['name']}")
                greeting = greeting_override or (
                    agent.get("first_message") if agent.get("first_message_enabled") else None
                )
                if greeting:
                    asyncio.create_task(_speak(greeting))

            elif event == "media":
                if not agent:
                    continue  # "start" hasn't completed setup yet
                # Drop audio while agent is speaking or pipeline is running
                if is_speaking or is_processing:
                    if _dg_stream:
                        try:
                            await _dg_stream.finish()
                        except Exception:
                            pass
                        _dg_stream = None
                        speech_chunks = 0
                        silence_chunks = 0
                    continue

                pcm16k = twilio_payload_to_pcm16k(data["media"]["payload"])
                has_speech = await detect_speech(pcm16k)

                if has_speech:
                    speech_chunks += 1
                    silence_chunks = 0
                    # Open streaming transcriber on first speech chunk
                    if _dg_stream is None:
                        try:
                            _dg_stream = get_streaming_transcriber()
                            await _dg_stream.start()
                        except Exception as e:
                            print(f"[Twilio Deepgram] Failed to start stream: {e}")
                            _dg_stream = None
                    if _dg_stream:
                        await _dg_stream.send(pcm16k)
                else:
                    silence_chunks += 1
                    if speech_chunks > 0 and _dg_stream:
                        await _dg_stream.send(pcm16k)  # send trailing silence so Deepgram finalizes

                    if speech_chunks >= SPEECH_THRESHOLD and silence_chunks >= SILENCE_THRESHOLD:
                        dg = _dg_stream
                        _dg_stream = None
                        speech_chunks = 0
                        silence_chunks = 0

                        if dg:
                            transcript = await dg.finish()
                            if transcript and transcript.strip():
                                print(f"[Twilio] User said: {transcript.strip()}")
                                asyncio.create_task(run_pipeline(transcript.strip()))

            elif event == "stop":
                print(f"[Twilio] Call ended — stream {stream_sid}")
                _call_ended = True
                if interview_id:
                    from app.hr_repo import mark_interview_completed
                    await asyncio.to_thread(mark_interview_completed, interview_id)
                break

    except Exception as e:
        if "disconnect" not in str(e).lower():
            print(f"[Twilio WS] Error: {e}")
    finally:
        # Clean up any open Deepgram stream
        if _dg_stream:
            try:
                await _dg_stream.finish()
            except Exception:
                pass
        # Always mark interview done on any exit path (crash, disconnect, clean stop)
        if interview_id and not _call_ended:
            try:
                from app.hr_repo import mark_interview_completed
                await asyncio.to_thread(mark_interview_completed, interview_id)
            except Exception:
                pass
        try:
            await websocket.close()
        except Exception:
            pass


# ─── WebSocket: Voice Pipeline ────────────────────────────────────────

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    from app.websocket_manager import manager, UserSession, AgentState
    from app.conversation_repo import create_conversation, enforce_session_cap, cleanup_expired_sessions
    from app.message_repo import save_message, get_session_messages
    from app.deepgram_service import StreamingTranscriber, get_streaming_transcriber, transcribe_audio_smart
    from app.memory_repo import get_user_memory, upsert_user_memory
    from app.elevenlabs_service import text_to_speech_stream
    from app.rag_service import retrieve_context, format_rag_context
    from app.llm_service import generate_response, generate_response_stream, generate_memory_summary
    from app.vad_service import detect_speech, build_wav_bytes

    try:
        payload = await decode_supabase_token(token)
        user_id = payload["sub"]
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[WS AUTH FAILED] {type(e).__name__}: {e}")
        await websocket.close(code=4001, reason="Unauthorized")
        return

    if manager.is_full():
        await websocket.close(code=1008, reason="Server at capacity")
        return

    await websocket.accept()

    # Session housekeeping — skip for guest users
    is_guest = payload.get("user_metadata", {}).get("is_guest", False)
    if not is_guest:
        from app.config import SESSION_MAX_PER_USER, SESSION_EXPIRY_DAYS
        await asyncio.to_thread(cleanup_expired_sessions, user_id, SESSION_EXPIRY_DAYS)
        await asyncio.to_thread(enforce_session_cap, user_id, SESSION_MAX_PER_USER)

    conversation = await asyncio.to_thread(create_conversation, user_id)
    conversation_id = conversation["id"]

    session = UserSession(
        user_id=user_id,
        websocket=websocket,
        conversation_id=conversation_id,
        sensitivity=VAD_SENSITIVITY,
    )
    manager.add_session(session)

    # Pre-load memory into local cache — avoids DB hit on every turn
    try:
        _initial_memory = await asyncio.to_thread(get_user_memory, user_id)
    except Exception as e:
        print(f"[WS] Failed to load memory: {e}")
        _initial_memory = None
    _memory_cache: dict = {"summary": _initial_memory}

    await manager.send_json(websocket, {"type": "ready", "conversation_id": conversation_id})

    async def speak_greeting(text: str):
        from app.elevenlabs_service import text_to_speech_stream
        session.state = AgentState.SPEAKING
        await manager.send_json(websocket, {"type": "state", "state": "SPEAKING"})
        await manager.send_json(websocket, {"type": "reply", "text": text})
        try:
            async for chunk in text_to_speech_stream(text):
                if session.state != AgentState.SPEAKING:
                    break
                await manager.send_bytes(websocket, chunk)
            await manager.send_json(websocket, {"type": "sentence_end"})
            await manager.send_json(websocket, {"type": "audio_end"})
        except Exception as e:
            print(f"[Greeting] TTS error: {e}")
        session.state = AgentState.LISTENING
        await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})

    import random
    _greetings = [
        "Hey! How can I help you today?",
        "Hi there! What can I do for you?",
        "Hello! I'm here and listening. How can I assist you?",
        "Hey, good to have you here! What's on your mind?",
    ]
    asyncio.create_task(speak_greeting(random.choice(_greetings)))

    async def run_pipeline(pcm_bytes: bytes, sr: int = 16000, transcript_override: str | None = None, dg_stream=None):
        try:
            t0 = time.time()

            if transcript_override:
                transcript = transcript_override
                print(f"[Pipeline] Text input: {repr(transcript)}")
            elif dg_stream:
                # Streaming path: transcript was built in real-time — just finalize
                transcript = await dg_stream.finish()
                print(f"[Timing] STT (streaming): {time.time()-t0:.2f}s — {repr(transcript)}")
            else:
                # Fallback: send full audio to prerecorded API
                print(f"[Pipeline] Starting — audio={len(pcm_bytes)}B @ {sr}Hz")
                wav_bytes = build_wav_bytes(pcm_bytes, sample_rate=sr)
                transcript = await transcribe_audio_smart(wav_bytes)
                print(f"[Timing] STT (prerecorded): {time.time()-t0:.2f}s — {repr(transcript)}")

            if not transcript:
                await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                session.state = AgentState.LISTENING
                return

            if not transcript_override:
                await manager.send_json(websocket, {"type": "transcript", "text": transcript})

            t1 = time.time()
            rag_chunks, session_history = await asyncio.gather(
                retrieve_context(transcript),
                asyncio.to_thread(get_session_messages, conversation_id),
            )
            async def _save_user_msg():
                try:
                    await asyncio.to_thread(save_message, conversation_id, user_id, "user", transcript)
                except Exception as e:
                    print(f"[WS] Failed to save user message: {e}")
            asyncio.create_task(_save_user_msg())
            rag_context = format_rag_context(rag_chunks)
            long_term_memory = _memory_cache["summary"]  # use cached memory — no DB hit
            print(f"[Timing] RAG+History fetch: {time.time()-t1:.2f}s")

            # Stream LLM sentence by sentence → pipe each sentence to TTS immediately
            t2 = time.time()
            full_reply_parts: list[str] = []
            first_sentence = True

            session.state = AgentState.SPEAKING
            session.speaking_started_at = time.time()
            session.barge_in_armed = False  # only armed after frontend confirms playback started
            await manager.send_json(websocket, {"type": "state", "state": "SPEAKING"})

            try:
                async for sentence in generate_response_stream(
                    user_message=transcript,
                    session_history=session_history,
                    rag_context=rag_context,
                    long_term_memory=long_term_memory,
                ):
                    if session.state != AgentState.SPEAKING:
                        break  # barge-in happened

                    if first_sentence:
                        print(f"[Timing] LLM first sentence: {time.time()-t2:.2f}s — '{sentence}'")
                        first_sentence = False

                    full_reply_parts.append(sentence)

                    # Stream this sentence's TTS chunks immediately
                    t_tts = time.time()
                    chunk_count = 0
                    async for chunk in text_to_speech_stream(sentence):
                        if session.state != AgentState.SPEAKING:
                            break
                        if chunk_count == 0:
                            print(f"[Timing] TTS first chunk for sentence: {time.time()-t_tts:.2f}s")
                        chunk_count += 1
                        await manager.send_bytes(websocket, chunk)

                    # Tell frontend this sentence is complete — play it now
                    if session.state == AgentState.SPEAKING:
                        await manager.send_json(websocket, {"type": "sentence_end"})

            except asyncio.CancelledError:
                session.state = AgentState.LISTENING

            full_reply = " ".join(full_reply_parts)
            if full_reply:
                await manager.send_json(websocket, {"type": "reply", "text": full_reply})
                async def _save_assistant_msg(reply=full_reply):
                    try:
                        await asyncio.to_thread(save_message, conversation_id, user_id, "assistant", reply)
                    except Exception as e:
                        print(f"[WS] Failed to save assistant message: {e}")
                asyncio.create_task(_save_assistant_msg())
            session.message_count += 1
            print(f"[Timing] Total LLM+TTS: {time.time()-t2:.2f}s")

            if session.state == AgentState.SPEAKING:
                await manager.send_json(websocket, {"type": "audio_end"})
            else:
                await manager.send_json(websocket, {"type": "stop_audio"})

            if session.message_count % MEMORY_UPDATE_EVERY == 0:
                asyncio.create_task(update_memory(user_id, conversation_id))

        except Exception as e:
            print(f"[WS] Pipeline error: {e}")
            import traceback; traceback.print_exc()
            session.state = AgentState.LISTENING
            await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})

    async def _analyze_session_sentiment(conv_id: str):
        """Background task — runs after session ends, never blocks the pipeline."""
        try:
            from app.sentiment_service import analyze_sentiment
            from app.message_repo import get_session_messages
            from app.conversation_repo import update_conversation_sentiment

            messages = await asyncio.to_thread(get_session_messages, conv_id)
            if not messages:
                return

            # Build plain transcript: "User: ... Assistant: ..."
            lines = []
            for m in messages:
                role = "User" if m.get("role") == "user" else "Assistant"
                lines.append(f"{role}: {m.get('content', '')}")
            transcript = "\n".join(lines)

            result = await analyze_sentiment(transcript)
            await asyncio.to_thread(update_conversation_sentiment, conv_id, result)
        except Exception as e:
            print(f"[Sentiment] Task failed for {conv_id}: {e}")

    async def update_memory(uid: str, conv_id: str):
        try:
            old_summary = _memory_cache["summary"]
            messages = await asyncio.to_thread(get_session_messages, conv_id)
            new_summary = await generate_memory_summary(old_summary, messages)
            await asyncio.to_thread(upsert_user_memory, uid, new_summary)
            _memory_cache["summary"] = new_summary
        except Exception as e:
            print(f"[WS] Failed to update memory: {e}")

    speech_buffer: list[bytes] = []
    silence_chunks = 0
    speech_chunks = 0
    sample_rate = 16000
    _dg_stream: StreamingTranscriber | None = None

    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data and data["bytes"]:
                audio_bytes = data["bytes"]

                if session.state == AgentState.SPEAKING:
                    # Only check barge-in AFTER frontend confirms playback has started.
                    # Before that, ambient mic noise / echo of the user's last utterance
                    # would trigger barge-in before a single LLM sentence is generated.
                    if not session.barge_in_armed:
                        continue
                    has_speech = await detect_speech(audio_bytes, session.barge_in_sensitivity)
                    if has_speech:
                        print("[BARGE-IN] Triggered!")
                        session.state = AgentState.LISTENING
                        session.barge_in_armed = False
                        await manager.send_json(websocket, {"type": "stop_audio"})
                        await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                        speech_buffer.clear()
                        speech_buffer.append(audio_bytes)
                        silence_chunks = 0
                        speech_chunks = 1
                    continue

                if session.state == AgentState.PROCESSING:
                    continue

                if session.state == AgentState.LISTENING:
                    has_speech = await detect_speech(audio_bytes, session.sensitivity)
                    print(f"[VAD] chunk={len(audio_bytes)}B speech={has_speech} speech_chunks={speech_chunks} silence={silence_chunks}")

                    if has_speech:
                        speech_buffer.append(audio_bytes)
                        speech_chunks += 1
                        silence_chunks = 0
                        if speech_chunks == 1:
                            print("[VAD] Speech started — opening STT stream")
                            await manager.send_json(websocket, {"type": "state", "state": "PROCESSING"})
                            _dg_stream = get_streaming_transcriber()
                            try:
                                await _dg_stream.start()
                                # Send all buffered chunks (in case buffer had earlier chunks)
                                await _dg_stream.send(audio_bytes)
                            except Exception as e:
                                print(f"[Deepgram Stream] Failed to start: {e}")
                                _dg_stream = None
                        elif _dg_stream:
                            await _dg_stream.send(audio_bytes)
                    else:
                        if speech_chunks > 0:
                            silence_chunks += 1
                            speech_buffer.append(audio_bytes)
                            if _dg_stream:
                                await _dg_stream.send(audio_bytes)

                            if silence_chunks >= VAD_SILENCE_LIMIT:
                                print(f"[VAD] Utterance end — speech={speech_chunks} chunks")
                                if speech_chunks >= VAD_MIN_SPEECH:
                                    trimmed = speech_buffer[:speech_chunks + 1]
                                    full_audio = b"".join(trimmed)
                                    print(f"[VAD] Trimmed audio: {len(full_audio)}B")
                                    session.state = AgentState.PROCESSING
                                    asyncio.create_task(run_pipeline(full_audio, sample_rate, dg_stream=_dg_stream))
                                    _dg_stream = None
                                else:
                                    print("[VAD] Too short, discarding")
                                    if _dg_stream:
                                        asyncio.create_task(_dg_stream.finish())
                                        _dg_stream = None
                                    session.state = AgentState.LISTENING
                                    await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                                speech_buffer.clear()
                                silence_chunks = 0
                                speech_chunks = 0

            elif "text" in data and data["text"]:
                try:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "set_sensitivity":
                        session.sensitivity = float(msg.get("value", 0.5))
                    elif msg.get("type") == "sample_rate":
                        sample_rate = int(msg.get("value", 16000))
                        print(f"[WS] Client sample rate: {sample_rate}Hz")
                    elif msg.get("type") == "playback_started":
                        print("[WS] Frontend playback started — arming barge-in")
                        session.barge_in_armed = True
                    elif msg.get("type") == "playback_ended":
                        print("[WS] Frontend playback ended")
                        session.barge_in_armed = False
                        if session.state == AgentState.SPEAKING:
                            session.state = AgentState.LISTENING
                            await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                    elif msg.get("type") == "text_input":
                        text = msg.get("text", "").strip()
                        if text and session.state == AgentState.LISTENING:
                            print(f"[WS] Text input: {repr(text)}")
                            session.state = AgentState.PROCESSING
                            await manager.send_json(websocket, {"type": "state", "state": "PROCESSING"})
                            await manager.send_json(websocket, {"type": "transcript", "text": text})
                            asyncio.create_task(run_pipeline(b"", sample_rate, transcript_override=text))
                except json.JSONDecodeError:
                    pass

    except (WebSocketDisconnect, ConnectionResetError):
        pass
    except RuntimeError as e:
        if "disconnect message" not in str(e):
            print(f"[WS] Unexpected error: {e}")
            import traceback; traceback.print_exc()
    except Exception as e:
        print(f"[WS] Unexpected error: {e}")
        import traceback; traceback.print_exc()
    finally:
        manager.remove_session(user_id)
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(update_memory(user_id, conversation_id))
            loop.create_task(_analyze_session_sentiment(conversation_id))


# ─── User Knowledge Base ─────────────────────────────────────────────────────

@app.get("/api/user-kb/documents")
async def list_user_kb_documents(current_user: dict = Depends(get_current_user)):
    user_id = current_user["sub"]
    from app.user_kb_repo import get_user_documents, get_user_storage_used, get_user_kb_limit
    docs = await asyncio.to_thread(get_user_documents, user_id)
    used = await asyncio.to_thread(get_user_storage_used, user_id)
    limit = get_user_kb_limit()
    return {"documents": docs, "storage_used_bytes": used, "storage_limit_bytes": limit}


@app.post("/api/user-kb/documents", status_code=201)
async def upload_user_kb_document(
    file: UploadFile,
    display_name: str = Form(...),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["sub"]
    from app.user_kb_repo import get_user_storage_used, create_user_document, get_user_kb_limit
    from app.agent_doc_processor import extract_text, process_user_document

    file_bytes = await file.read()
    fname = file.filename or ""
    file_type = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if file_type not in ("pdf", "docx", "txt"):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use PDF, DOCX, or TXT.")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="File is empty.")

    from app.user_kb_repo import MAX_USER_KB_FILE_BYTES
    if len(file_bytes) > MAX_USER_KB_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum 3 MB per file.")

    storage_used = await asyncio.to_thread(get_user_storage_used, user_id)
    limit = get_user_kb_limit()
    if storage_used + len(file_bytes) > limit:
        remaining = limit - storage_used
        raise HTTPException(status_code=413, detail=f"Storage limit exceeded. {remaining / 1024 / 1024:.1f} MB remaining of 10 MB total.")

    try:
        content = await asyncio.to_thread(extract_text, file_bytes, file_type)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract text: {e}")

    if not content.strip():
        raise HTTPException(status_code=400, detail="Document appears to be empty after text extraction.")

    doc = await asyncio.to_thread(
        create_user_document, user_id, display_name.strip(), fname, len(file_bytes), file_type, content
    )
    asyncio.create_task(asyncio.to_thread(process_user_document, doc["id"], user_id, content))
    return doc


@app.delete("/api/user-kb/documents/{doc_id}", status_code=204)
async def delete_user_kb_document(doc_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["sub"]
    from app.user_kb_repo import delete_user_document
    await asyncio.to_thread(delete_user_document, doc_id, user_id)


# ─── Agent Voice WebSocket ───────────────────────────────────────────────────

@app.websocket("/ws/agent/{agent_id}/{token}")
async def agent_websocket_endpoint(websocket: WebSocket, agent_id: str, token: str, docs: str = ""):
    """Voice session tied to a specific agent — uses agent system prompt + RAG over agent docs."""
    from app.websocket_manager import manager, UserSession, AgentState
    from app.conversation_repo import create_conversation, enforce_session_cap, cleanup_expired_sessions
    from app.message_repo import save_message, get_session_messages
    from app.deepgram_service import StreamingTranscriber, get_streaming_transcriber, transcribe_audio_smart
    from app.memory_repo import get_user_memory, upsert_user_memory
    from app.elevenlabs_service import text_to_speech_stream
    from app.agent_rag_service import retrieve_agent_context, format_agent_context
    from app.agent_repo import get_agent
    from app.llm_service import generate_response_stream, generate_memory_summary
    from app.vad_service import detect_speech, build_wav_bytes
    from app.user_kb_rag import retrieve_user_kb_context, format_user_kb_context

    # Auth
    try:
        payload = await decode_supabase_token(token)
        user_id = payload["sub"]
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Load agent + ownership check
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != user_id:
        await websocket.close(code=4003, reason="Agent not found")
        return

    agent_sensitivity = float(agent.get("barge_in_sensitivity", VAD_BARGE_IN_SENSITIVITY))
    _hr = (agent.get("hr_instructions") or "").strip()
    agent_system_prompt = (
        (agent.get("system_prompt") or "") + (f"\n\n--- HR INSTRUCTIONS ---\n{_hr}" if _hr else "")
    ) or None
    agent_kb_instructions = agent.get("kb_instructions") or None
    doc_ids = [d.strip() for d in docs.split(",") if d.strip()] if docs else []

    # Load active tool functions for this agent
    from app.agent_repo import get_agent_functions
    _all_fns = await asyncio.to_thread(get_agent_functions, agent_id)
    _tool_functions = [f for f in _all_fns if f.get("url") and f.get("trigger_type") == "llm_tool_call"]

    async def _execute_webhook(fn: dict, args: dict) -> str:
        """Call the configured webhook and return its response as a JSON string."""
        import httpx
        method = (fn.get("method") or "POST").upper()
        url = fn["url"]
        headers = fn.get("headers") or {}
        timeout_s = (fn.get("timeout_ms") or 30000) / 1000
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as hclient:
                if method == "GET":
                    resp = await hclient.get(url, params=args, headers=headers)
                elif method == "DELETE":
                    resp = await hclient.delete(url, params=args, headers=headers)
                else:
                    resp = await hclient.request(method, url, json=args, headers=headers)
            try:
                return json.dumps(resp.json())
            except Exception:
                return json.dumps({"response": resp.text, "status": resp.status_code})
        except Exception as e:
            print(f"[Webhook] {fn['name']} error: {e}")
            return json.dumps({"error": str(e)})

    if manager.is_full():
        await websocket.close(code=1008, reason="Server at capacity")
        return

    await websocket.accept()

    # Session housekeeping
    is_guest = payload.get("user_metadata", {}).get("is_guest", False)
    if not is_guest:
        from app.config import SESSION_MAX_PER_USER, SESSION_EXPIRY_DAYS
        await asyncio.to_thread(cleanup_expired_sessions, user_id, SESSION_EXPIRY_DAYS)
        await asyncio.to_thread(enforce_session_cap, user_id, SESSION_MAX_PER_USER)

    # Create conversation linked to this agent
    from app.database import get_supabase
    db_sync = get_supabase()
    def _create_agent_conv():
        result = db_sync.table("conversations").insert({
            "user_id": user_id,
            "title": f"{agent['name']} session",
            "agent_id": agent_id,
        }).execute()
        return result.data[0]
    conversation = await asyncio.to_thread(_create_agent_conv)
    conversation_id = conversation["id"]

    session = UserSession(
        user_id=user_id,
        websocket=websocket,
        conversation_id=conversation_id,
        sensitivity=VAD_SENSITIVITY,
    )
    session.barge_in_sensitivity = agent_sensitivity
    manager.add_session(session)

    try:
        _initial_memory = await asyncio.to_thread(get_user_memory, user_id)
    except Exception:
        _initial_memory = None
    _memory_cache: dict = {"summary": _initial_memory}

    # RAG scope — frontend can update mid-session via set_rag_scope message
    _rag_scopes: list[str] = ["personal", "global"]  # default = both

    await manager.send_json(websocket, {
        "type": "ready",
        "conversation_id": conversation_id,
        "agent_id": agent_id,
        "agent_name": agent["name"],
    })

    async def speak_agent_greeting(text: str):
        from app.elevenlabs_service import text_to_speech_stream
        session.state = AgentState.SPEAKING
        await manager.send_json(websocket, {"type": "state", "state": "SPEAKING"})
        await manager.send_json(websocket, {"type": "reply", "text": text})
        try:
            async for chunk in text_to_speech_stream(text):
                if session.state != AgentState.SPEAKING:
                    break
                await manager.send_bytes(websocket, chunk)
            await manager.send_json(websocket, {"type": "sentence_end"})
            await manager.send_json(websocket, {"type": "audio_end"})
        except Exception as e:
            print(f"[Agent Greeting] TTS error: {e}")
        session.state = AgentState.LISTENING
        await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})

    import random
    _agent_greetings = [
        f"Hey! I'm {agent['name']}. How can I help you today?",
        f"Hi there! {agent['name']} here. What can I do for you?",
        f"Hello! I'm {agent['name']} and I'm ready to assist. What's on your mind?",
        f"Hey, good to have you here! I'm {agent['name']}. How can I assist you?",
    ]
    asyncio.create_task(speak_agent_greeting(random.choice(_agent_greetings)))

    async def run_agent_pipeline(pcm_bytes: bytes, sr: int = 16000,
                                 transcript_override: str | None = None, dg_stream=None):
        try:
            t0 = time.time()
            if transcript_override:
                transcript = transcript_override
            elif dg_stream:
                transcript = await dg_stream.finish()
                print(f"[Agent Pipeline] STT: {time.time()-t0:.2f}s — {repr(transcript)}")
            else:
                wav_bytes = build_wav_bytes(pcm_bytes, sample_rate=sr)
                transcript = await transcribe_audio_smart(wav_bytes)

            if not transcript:
                await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                session.state = AgentState.LISTENING
                return

            if not transcript_override:
                await manager.send_json(websocket, {"type": "transcript", "text": transcript})

            t1 = time.time()
            gather_tasks = [
                asyncio.to_thread(retrieve_agent_context, transcript, agent_id, user_id, _rag_scopes),
                asyncio.to_thread(get_session_messages, conversation_id),
            ]
            if doc_ids:
                gather_tasks.append(asyncio.to_thread(retrieve_user_kb_context, transcript, doc_ids))
            gather_results = await asyncio.gather(*gather_tasks)
            rag_chunks = gather_results[0]
            session_history = gather_results[1]
            user_kb_chunks = gather_results[2] if doc_ids else []
            asyncio.create_task(asyncio.to_thread(save_message, conversation_id, user_id, "user", transcript))

            agent_rag_context = format_agent_context(rag_chunks)
            user_kb_context_str = format_user_kb_context(user_kb_chunks)
            rag_context = "\n\n".join(filter(None, [agent_rag_context, user_kb_context_str]))
            long_term_memory = _memory_cache["summary"]
            print(f"[Agent Pipeline] RAG+History: {time.time()-t1:.2f}s, scopes={_rag_scopes}, kb_docs={len(doc_ids)}")

            session.state = AgentState.SPEAKING
            session.speaking_started_at = time.time()
            session.barge_in_armed = False
            await manager.send_json(websocket, {"type": "state", "state": "SPEAKING"})

            from app.llm_service import (
                build_system_prompt, build_openai_tool,
                detect_tool_call, stream_tool_result_response,
                stream_text_as_sentences, FILLER_PHRASES,
            )

            system_str = build_system_prompt(
                agent_system_prompt, long_term_memory, rag_context, agent_kb_instructions
            )
            conv_messages = list(session_history) + [{"role": "user", "content": transcript}]

            async def _tts_stream_sentences(sentence_gen):
                """Stream sentences through TTS, returning the full reply text."""
                parts: list[str] = []
                first = True
                t2 = time.time()
                try:
                    async for sentence in sentence_gen:
                        if session.state != AgentState.SPEAKING:
                            break
                        if first:
                            print(f"[Agent Pipeline] LLM first sentence: {time.time()-t2:.2f}s")
                            first = False
                        parts.append(sentence)
                        t_tts = time.time()
                        n = 0
                        async for chunk in text_to_speech_stream(sentence):
                            if session.state != AgentState.SPEAKING:
                                break
                            if n == 0:
                                print(f"[Agent Pipeline] TTS first chunk: {time.time()-t_tts:.2f}s")
                            n += 1
                            await manager.send_bytes(websocket, chunk)
                        if session.state == AgentState.SPEAKING:
                            await manager.send_json(websocket, {"type": "sentence_end"})
                except asyncio.CancelledError:
                    session.state = AgentState.LISTENING
                return " ".join(parts)

            full_reply = ""
            try:
                if _tool_functions:
                    # ── Tool-call path ──────────────────────────────────────────
                    tools = [build_openai_tool(f) for f in _tool_functions]
                    text, tool_name, tool_args, call_id = await detect_tool_call(
                        conv_messages, system_str, tools
                    )
                    if tool_name and call_id:
                        print(f"[Agent Pipeline] Tool call: {tool_name}({tool_args})")
                        # Speak a filler phrase immediately so the user doesn't hear silence
                        filler = random.choice(FILLER_PHRASES)
                        async for chunk in text_to_speech_stream(filler):
                            if session.state != AgentState.SPEAKING:
                                break
                            await manager.send_bytes(websocket, chunk)
                        await manager.send_json(websocket, {"type": "sentence_end"})

                        # Execute the webhook
                        fn = next((f for f in _tool_functions if f["name"] == tool_name), None)
                        tool_result = json.dumps({"error": "function not found"})
                        if fn:
                            tool_result = await _execute_webhook(fn, tool_args)
                            print(f"[Agent Pipeline] Webhook result: {tool_result[:120]}")

                        # Second LLM call streams the actual answer using the tool result
                        full_reply = await _tts_stream_sentences(
                            stream_tool_result_response(
                                conv_messages, system_str, tool_name, call_id, tool_args, tool_result
                            )
                        )
                    else:
                        # LLM chose to answer directly without calling any tool
                        full_reply = await _tts_stream_sentences(
                            stream_text_as_sentences(text or "")
                        )
                else:
                    # ── Normal streaming path (no tools configured) ─────────────
                    full_reply = await _tts_stream_sentences(
                        generate_response_stream(
                            user_message=transcript,
                            session_history=session_history,
                            rag_context=rag_context,
                            long_term_memory=long_term_memory,
                            system_prompt_override=agent_system_prompt,
                            kb_instructions=agent_kb_instructions,
                        )
                    )
            except asyncio.CancelledError:
                session.state = AgentState.LISTENING

            if full_reply:
                await manager.send_json(websocket, {"type": "reply", "text": full_reply})
                asyncio.create_task(asyncio.to_thread(save_message, conversation_id, user_id, "assistant", full_reply))
            session.message_count += 1

            if session.state == AgentState.SPEAKING:
                await manager.send_json(websocket, {"type": "audio_end"})
            else:
                await manager.send_json(websocket, {"type": "stop_audio"})

            if session.message_count % MEMORY_UPDATE_EVERY == 0:
                asyncio.create_task(_update_agent_memory(user_id, conversation_id))

        except Exception as e:
            print(f"[Agent WS] Pipeline error: {e}")
            import traceback; traceback.print_exc()
            session.state = AgentState.LISTENING
            await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})

    async def _update_agent_memory(uid: str, conv_id: str):
        try:
            messages = await asyncio.to_thread(get_session_messages, conv_id)
            new_summary = await generate_memory_summary(_memory_cache["summary"], messages)
            await asyncio.to_thread(upsert_user_memory, uid, new_summary)
            _memory_cache["summary"] = new_summary
        except Exception as e:
            print(f"[Agent WS] Memory update failed: {e}")

    async def _analyze_agent_session_sentiment(conv_id: str):
        try:
            from app.sentiment_service import analyze_sentiment
            from app.conversation_repo import update_conversation_sentiment
            messages = await asyncio.to_thread(get_session_messages, conv_id)
            if not messages:
                return
            lines = []
            for m in messages:
                role = "User" if m.get("role") == "user" else "Assistant"
                lines.append(f"{role}: {m.get('content', '')}")
            result = await analyze_sentiment("\n".join(lines))
            await asyncio.to_thread(update_conversation_sentiment, conv_id, result)
        except Exception as e:
            print(f"[Agent Sentiment] Failed for {conv_id}: {e}")

    speech_buffer: list[bytes] = []
    silence_chunks = 0
    speech_chunks = 0
    sample_rate = 16000
    _dg_stream: StreamingTranscriber | None = None

    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data and data["bytes"]:
                audio_bytes = data["bytes"]

                if session.state == AgentState.SPEAKING:
                    if not session.barge_in_armed:
                        continue
                    has_speech = await detect_speech(audio_bytes, session.barge_in_sensitivity)
                    if has_speech:
                        session.state = AgentState.LISTENING
                        session.barge_in_armed = False
                        await manager.send_json(websocket, {"type": "stop_audio"})
                        await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                        speech_buffer.clear()
                        speech_buffer.append(audio_bytes)
                        silence_chunks = 0
                        speech_chunks = 1
                    continue

                if session.state == AgentState.PROCESSING:
                    continue

                if session.state == AgentState.LISTENING:
                    has_speech = await detect_speech(audio_bytes, session.sensitivity)
                    if has_speech:
                        speech_buffer.append(audio_bytes)
                        speech_chunks += 1
                        silence_chunks = 0
                        if speech_chunks == 1:
                            await manager.send_json(websocket, {"type": "state", "state": "PROCESSING"})
                            _dg_stream = get_streaming_transcriber()
                            try:
                                await _dg_stream.start()
                                await _dg_stream.send(audio_bytes)
                            except Exception as e:
                                print(f"[Agent Deepgram] Stream failed: {e}")
                                _dg_stream = None
                        elif _dg_stream:
                            await _dg_stream.send(audio_bytes)
                    else:
                        if speech_chunks > 0:
                            silence_chunks += 1
                            speech_buffer.append(audio_bytes)
                            if _dg_stream:
                                await _dg_stream.send(audio_bytes)
                            if silence_chunks >= VAD_SILENCE_LIMIT:
                                if speech_chunks >= VAD_MIN_SPEECH:
                                    trimmed = speech_buffer[:speech_chunks + 1]
                                    full_audio = b"".join(trimmed)
                                    session.state = AgentState.PROCESSING
                                    asyncio.create_task(run_agent_pipeline(full_audio, sample_rate, dg_stream=_dg_stream))
                                    _dg_stream = None
                                else:
                                    if _dg_stream:
                                        asyncio.create_task(_dg_stream.finish())
                                        _dg_stream = None
                                    session.state = AgentState.LISTENING
                                    await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                                speech_buffer.clear()
                                silence_chunks = 0
                                speech_chunks = 0

            elif "text" in data and data["text"]:
                try:
                    msg = json.loads(data["text"])
                    msg_type = msg.get("type")
                    if msg_type == "set_sensitivity":
                        session.sensitivity = float(msg.get("value", 0.5))
                    elif msg_type == "set_barge_in_sensitivity":
                        session.barge_in_sensitivity = float(msg.get("value", agent_sensitivity))
                    elif msg_type == "set_rag_scope":
                        scope = msg.get("scope", "both")
                        if scope == "both":
                            _rag_scopes[:] = ["personal", "global"]
                        elif scope in ("personal", "global"):
                            _rag_scopes[:] = [scope]
                        print(f"[Agent WS] RAG scope updated: {_rag_scopes}")
                    elif msg_type == "sample_rate":
                        sample_rate = int(msg.get("value", 16000))
                    elif msg_type == "playback_started":
                        session.barge_in_armed = True
                    elif msg_type == "playback_ended":
                        session.barge_in_armed = False
                        if session.state == AgentState.SPEAKING:
                            session.state = AgentState.LISTENING
                            await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                    elif msg_type == "text_input":
                        text = msg.get("text", "").strip()
                        if text and session.state == AgentState.LISTENING:
                            session.state = AgentState.PROCESSING
                            await manager.send_json(websocket, {"type": "state", "state": "PROCESSING"})
                            await manager.send_json(websocket, {"type": "transcript", "text": text})
                            asyncio.create_task(run_agent_pipeline(b"", sample_rate, transcript_override=text))
                except json.JSONDecodeError:
                    pass

    except (WebSocketDisconnect, ConnectionResetError):
        pass
    except RuntimeError as e:
        if "disconnect message" not in str(e):
            print(f"[Agent WS] Error: {e}")
            import traceback; traceback.print_exc()
    except Exception as e:
        print(f"[Agent WS] Error: {e}")
        import traceback; traceback.print_exc()
    finally:
        manager.remove_session(user_id)
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_update_agent_memory(user_id, conversation_id))
            loop.create_task(_analyze_agent_session_sentiment(conversation_id))


# ─── Serve Next.js static export ────────────────────────────────────────────
_FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend", "out")

if os.path.isdir(_FRONTEND):
    # Mount _next JS/CSS chunks
    app.mount("/_next", StaticFiles(directory=os.path.join(_FRONTEND, "_next")), name="next_assets")

    @app.get("/favicon.ico", include_in_schema=False)
    async def _favicon():
        return FileResponse(os.path.join(_FRONTEND, "favicon.ico"))

    @app.get("/auth/", include_in_schema=False)
    @app.get("/auth", include_in_schema=False)
    async def _auth_callback():
        return FileResponse(os.path.join(_FRONTEND, "auth", "index.html"))

    @app.get("/forgot-password/", include_in_schema=False)
    @app.get("/forgot-password", include_in_schema=False)
    async def _forgot_page():
        return FileResponse(os.path.join(_FRONTEND, "forgot-password", "index.html"))

    @app.get("/signin/", include_in_schema=False)
    @app.get("/signin", include_in_schema=False)
    async def _signin_page():
        return FileResponse(os.path.join(_FRONTEND, "signin", "index.html"))

    @app.get("/signup/", include_in_schema=False)
    @app.get("/signup", include_in_schema=False)
    async def _signup_page():
        return FileResponse(os.path.join(_FRONTEND, "signup", "index.html"))

    @app.get("/agent/", include_in_schema=False)
    @app.get("/agent", include_in_schema=False)
    async def _agent_page():
        return FileResponse(os.path.join(_FRONTEND, "agent", "index.html"))

    @app.get("/agents/", include_in_schema=False)
    @app.get("/agents", include_in_schema=False)
    async def _agents_page():
        return FileResponse(os.path.join(_FRONTEND, "agents", "index.html"))

    @app.get("/voice-sessions/", include_in_schema=False)
    @app.get("/voice-sessions", include_in_schema=False)
    async def _voice_sessions_page():
        return FileResponse(os.path.join(_FRONTEND, "agents", "index.html"))

    @app.get("/admin/", include_in_schema=False)
    @app.get("/admin", include_in_schema=False)
    async def _admin_page():
        return FileResponse(os.path.join(_FRONTEND, "agents", "index.html"))

    @app.get("/", include_in_schema=False)
    async def _root_page():
        return FileResponse(os.path.join(_FRONTEND, "index.html"))
else:
    print(f"[Startup] WARNING: frontend build not found at {_FRONTEND} — serving API-only root")

    @app.get("/", include_in_schema=False)
    async def _root_page_fallback():
        return {"status": "ok", "note": "frontend build not found in this container"}
