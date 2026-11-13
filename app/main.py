"""Thin FastAPI app factory.

All route handlers live in app/routers/*.py (one module per domain, each an
APIRouter included below). This module only wires up the app itself:
middleware, startup/shutdown behavior, router registration, and serving the
built Next.js frontend.
"""
import asyncio
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import CORS_ORIGINS

app = FastAPI(title="Voice Agent API")


@app.on_event("startup")
async def _prewarm():
    """Pre-warm OpenAI and ElevenLabs TCP connections so first user call is fast."""
    async def _warm_openai():
        from app.core.config import AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENT
        if AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT:
            try:
                from openai import AsyncAzureOpenAI
                c = AsyncAzureOpenAI(
                    api_key=AZURE_OPENAI_API_KEY,
                    azure_endpoint=AZURE_OPENAI_ENDPOINT,
                    api_version=AZURE_OPENAI_API_VERSION,
                )
                await c.chat.completions.create(
                    model=AZURE_OPENAI_DEPLOYMENT, messages=[{"role": "user", "content": "hi"}], max_tokens=1
                )
                print("[Prewarm] Azure OpenAI ✓")
                return
            except Exception as e:
                print(f"[Prewarm] Azure OpenAI failed: {e}")

        try:
            from openai import AsyncOpenAI
            from app.core.config import OPENAI_API_KEY
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
            from app.core.config import ELEVENLABS_API_KEY
            client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
            await asyncio.to_thread(client.voices.get_all)
            print("[Prewarm] ElevenLabs ✓")
        except Exception as e:
            print(f"[Prewarm] ElevenLabs failed: {e}")

    async def _warm_deepgram():
        # Without this, the FIRST live StreamingTranscriber connection in a fresh
        # process silently loses the caller's audio (WS/TLS handshake to Deepgram
        # isn't fully ready when .send() starts firing) - confirmed by testing:
        # first call on a cold process returns no transcript, second call succeeds.
        try:
            from app.voice.deepgram_service import StreamingTranscriber
            st = StreamingTranscriber()
            await st.start()
            await st.send(b"\x00\x00" * 320)  # one silent 20ms frame
            await st.finish()
            print("[Prewarm] Deepgram ✓")
        except Exception as e:
            print(f"[Prewarm] Deepgram failed: {e}")

    asyncio.create_task(_warm_openai())
    asyncio.create_task(_warm_elevenlabs())
    asyncio.create_task(_warm_deepgram())

    # HR outbound call scheduler - checks every 60s for due interviews
    from app.hr_campaigns.hr_scheduler import run_scheduler
    asyncio.create_task(run_scheduler())

    # HR call campaigns - separate sequential dial queue, checks every 30s
    from app.hr_campaigns.hr_campaign_service import run_campaign_dialer
    asyncio.create_task(run_campaign_dialer())

    # Safety net: resolves hr_interviews rows abandoned in "calling" when a
    # provider never delivers a final status (dead webhook tunnel, or a
    # provider status value with no mapping) - checks every 5 minutes
    from app.hr_campaigns.call_reconciliation import run_reconciliation_loop
    asyncio.create_task(run_reconciliation_loop())

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ───────────────────────────────────────────────────────────
from app.routers import (
    auth as auth_router,
    conversations as conversations_router,
    health as health_router,
    agents as agents_router,
    telephony_twilio as telephony_twilio_router,
    telephony_plivo as telephony_plivo_router,
    hr_campaigns as hr_campaigns_router,
    kb as kb_router,
    user_kb as user_kb_router,
    admin as admin_router,
    ingest as ingest_router,
    websockets as websockets_router,
    integrations as integrations_router,
)

app.include_router(auth_router.router)
app.include_router(conversations_router.router)
app.include_router(health_router.router)
app.include_router(agents_router.router)
app.include_router(telephony_twilio_router.router)
app.include_router(telephony_plivo_router.router)
app.include_router(hr_campaigns_router.router)
app.include_router(kb_router.router)
app.include_router(user_kb_router.router)
app.include_router(admin_router.router)
app.include_router(ingest_router.router)
app.include_router(websockets_router.router)
app.include_router(integrations_router.router)


# ─── Serve Next.js static export ────────────────────────────────────────────
_FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend", "out")

if os.path.isdir(_FRONTEND):
    # Mount _next JS/CSS chunks
    app.mount("/_next", StaticFiles(directory=os.path.join(_FRONTEND, "_next")), name="next_assets")

    @app.get("/favicon.ico", include_in_schema=False)
    async def _favicon():
        return FileResponse(os.path.join(_FRONTEND, "favicon.ico"))

    @app.get("/google4930711495cc3e19.html", include_in_schema=False)
    async def _google_site_verification():
        return FileResponse(os.path.join(_FRONTEND, "google4930711495cc3e19.html"))

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
        return FileResponse(os.path.join(_FRONTEND, "admin", "index.html"))

    @app.get("/privacy/", include_in_schema=False)
    @app.get("/privacy", include_in_schema=False)
    async def _privacy_page():
        return FileResponse(os.path.join(_FRONTEND, "privacy", "index.html"))

    @app.get("/terms/", include_in_schema=False)
    @app.get("/terms", include_in_schema=False)
    async def _terms_page():
        return FileResponse(os.path.join(_FRONTEND, "terms", "index.html"))

    @app.get("/", include_in_schema=False)
    async def _root_page():
        return FileResponse(os.path.join(_FRONTEND, "index.html"))
else:
    print(f"[Startup] WARNING: frontend build not found at {_FRONTEND} - serving API-only root")

    @app.get("/", include_in_schema=False)
    async def _root_page_fallback():
        return {"status": "ok", "note": "frontend build not found in this container"}
