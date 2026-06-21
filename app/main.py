import asyncio
import json
import os

import httpx
import jwt
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from typing import Optional

from app.config import (
    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_JWT_SECRET,
    WEBHOOK_SECRET, STORAGE_BUCKET, CORS_ORIGINS,
    VAD_SENSITIVITY, VAD_SILENCE_LIMIT, VAD_MIN_SPEECH,
    MEMORY_UPDATE_EVERY,
)

app = FastAPI(title="Voice Agent API")

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


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class VerifyOtpRequest(BaseModel):
    email: EmailStr
    token: str
    password: str

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

    asyncio.create_task(ingest_document(file_name))
    print(f"[Webhook] Queued ingestion for: {file_name}")
    return {"status": "processing", "file": file_name}


# ─── WebSocket: Voice Pipeline ────────────────────────────────────────

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    from app.websocket_manager import manager, UserSession, AgentState
    from app.conversation_repo import create_conversation
    from app.message_repo import save_message, get_session_messages
    from app.memory_repo import get_user_memory, upsert_user_memory
    from app.deepgram_service import transcribe_audio
    from app.elevenlabs_service import text_to_speech_stream
    from app.rag_service import retrieve_context, format_rag_context
    from app.llm_service import generate_response, generate_memory_summary
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

    conversation = await asyncio.to_thread(create_conversation, user_id)
    conversation_id = conversation["id"]

    session = UserSession(
        user_id=user_id,
        websocket=websocket,
        conversation_id=conversation_id,
        sensitivity=VAD_SENSITIVITY,
    )
    manager.add_session(session)

    await manager.send_json(websocket, {"type": "ready", "conversation_id": conversation_id})

    async def run_tts_and_send(text: str):
        session.state = AgentState.SPEAKING
        await manager.send_json(websocket, {"type": "state", "state": "SPEAKING"})
        barged_in = False
        try:
            async for chunk in text_to_speech_stream(text):
                if session.state != AgentState.SPEAKING:
                    barged_in = True
                    break
                await manager.send_bytes(websocket, chunk)
        except asyncio.CancelledError:
            barged_in = True
        except Exception as e:
            print(f"[WS] TTS stream error: {e}")
        finally:
            if not barged_in:
                # Tell frontend all chunks are sent — it will notify us when done playing
                await manager.send_json(websocket, {"type": "audio_end"})

    async def run_pipeline(pcm_bytes: bytes, sr: int = 16000):
        try:
            print(f"[Pipeline] Starting — audio={len(pcm_bytes)}B @ {sr}Hz")
            wav_bytes = build_wav_bytes(pcm_bytes, sample_rate=sr)
            transcript = await transcribe_audio(wav_bytes)
            print(f"[Pipeline] Transcript: {repr(transcript)}")

            if not transcript:
                await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                session.state = AgentState.LISTENING
                return

            await manager.send_json(websocket, {"type": "transcript", "text": transcript})
            await asyncio.to_thread(save_message, conversation_id, user_id, "user", transcript)

            rag_chunks = await retrieve_context(transcript)
            rag_context = format_rag_context(rag_chunks)
            long_term_memory = await asyncio.to_thread(get_user_memory, user_id)
            session_history = await asyncio.to_thread(get_session_messages, conversation_id)

            reply = await generate_response(
                user_message=transcript,
                session_history=session_history,
                rag_context=rag_context,
                long_term_memory=long_term_memory,
            )

            await manager.send_json(websocket, {"type": "reply", "text": reply})
            await asyncio.to_thread(save_message, conversation_id, user_id, "assistant", reply)
            session.message_count += 1

            session.tts_task = asyncio.create_task(run_tts_and_send(reply))
            try:
                await session.tts_task
            except asyncio.CancelledError:
                # Barge-in cancelled TTS — this is expected, not an error
                pass

            if session.message_count % MEMORY_UPDATE_EVERY == 0:
                asyncio.create_task(update_memory(user_id, conversation_id))

        except Exception as e:
            print(f"[WS] Pipeline error: {e}")
            import traceback; traceback.print_exc()
            session.state = AgentState.LISTENING
            await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})

    async def update_memory(uid: str, conv_id: str):
        old_summary = await asyncio.to_thread(get_user_memory, uid)
        messages = await asyncio.to_thread(get_session_messages, conv_id)
        new_summary = await generate_memory_summary(old_summary, messages)
        await asyncio.to_thread(upsert_user_memory, uid, new_summary)

    speech_buffer: list[bytes] = []
    silence_chunks = 0
    speech_chunks = 0
    sample_rate = 16000

    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data and data["bytes"]:
                audio_bytes = data["bytes"]

                if session.state == AgentState.SPEAKING:
                    has_speech = await detect_speech(audio_bytes, session.sensitivity)
                    print(f"[BARGE-IN VAD] chunk={len(audio_bytes)}B has_speech={has_speech} sensitivity={session.sensitivity}")
                    if has_speech:
                        print("[BARGE-IN] Triggered! Cancelling TTS.")
                        # Barge-in: cancel TTS and start collecting the new question
                        if session.tts_task and not session.tts_task.done():
                            session.tts_task.cancel()
                        session.state = AgentState.LISTENING
                        await manager.send_json(websocket, {"type": "stop_audio"})
                        await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                        # Start collecting the barge-in speech immediately
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
                            print("[VAD] Speech started")
                            await manager.send_json(websocket, {"type": "state", "state": "PROCESSING"})
                    else:
                        if speech_chunks > 0:
                            silence_chunks += 1
                            speech_buffer.append(audio_bytes)

                            if silence_chunks >= VAD_SILENCE_LIMIT:
                                print(f"[VAD] Utterance end — speech={speech_chunks} chunks")
                                if speech_chunks >= VAD_MIN_SPEECH:
                                    full_audio = b"".join(speech_buffer)
                                    session.state = AgentState.PROCESSING
                                    asyncio.create_task(run_pipeline(full_audio, sample_rate))
                                else:
                                    print("[VAD] Too short, discarding")
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
                        # Frontend confirmed it started playing — stay in SPEAKING
                        print("[WS] Frontend playback started")
                        session.state = AgentState.SPEAKING
                    elif msg.get("type") == "playback_ended":
                        # Frontend finished playing — now switch to LISTENING
                        print("[WS] Frontend playback ended")
                        if session.state == AgentState.SPEAKING:
                            session.state = AgentState.LISTENING
                            await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                except json.JSONDecodeError:
                    pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        msg = str(e)
        if "disconnect message" not in msg and "WebSocket" not in msg:
            print(f"[WS] Unexpected error: {e}")
    finally:
        manager.remove_session(user_id)
        # Use get_event_loop to safely schedule after disconnect
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(update_memory(user_id, conversation_id))


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

    @app.get("/", include_in_schema=False)
    async def _root_page():
        return FileResponse(os.path.join(_FRONTEND, "index.html"))
