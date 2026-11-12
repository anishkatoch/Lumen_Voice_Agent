"""Plivo telephony routes - mirrors telephony_twilio.py exactly (same CRUD
shape, same masking of secrets, same auto-webhook-config-on-save behavior),
just pointed at the Plivo repos/tables instead.

The websocket handler (plivo_stream_endpoint) is moved verbatim from the
pre-refactor app/main.py for the same reasons as the Twilio one.
"""
import asyncio
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.telephony.shared import PHONE_WEBHOOK_MAX_TIMEOUT_S, _CALL_STATUS_REASONS, log_task_exception
from app.routers.telephony_twilio import PhoneNumberAttach
from app.core.config import (
    VAD_SENSITIVITY, VAD_BARGE_IN_SENSITIVITY, VAD_SILENCE_LIMIT, VAD_MIN_SPEECH,
    MEMORY_UPDATE_EVERY,
)

router = APIRouter(tags=["telephony:plivo"])


class PlivoPhoneNumberCreate(BaseModel):
    phone_number: str
    friendly_name: Optional[str] = None
    plivo_account_id: Optional[str] = None

class PlivoPhoneNumberUpdate(BaseModel):
    friendly_name: Optional[str] = None
    is_active: Optional[bool] = None
    plivo_account_id: Optional[str] = None

class PlivoSettingsUpdate(BaseModel):
    auth_id: Optional[str] = None
    auth_token: Optional[str] = None
    webhook_base_url: Optional[str] = None

class PlivoAccountCreate(BaseModel):
    label: Optional[str] = None
    auth_id: Optional[str] = None
    auth_token: Optional[str] = None
    webhook_base_url: Optional[str] = None
    is_default: Optional[bool] = False
    webhook_base_urls: Optional[List[str]] = None

class PlivoAccountUpdate(BaseModel):
    label: Optional[str] = None
    auth_id: Optional[str] = None
    auth_token: Optional[str] = None
    webhook_base_url: Optional[str] = None
    webhook_base_urls: Optional[List[str]] = None

@router.get("/api/plivo/numbers")
async def list_plivo_phone_numbers(current_user: dict = Depends(get_current_user)):
    from app.telephony.plivo.plivo_repo import get_phone_numbers
    return await asyncio.to_thread(get_phone_numbers, current_user["sub"])

@router.post("/api/plivo/numbers", status_code=201)
async def add_plivo_phone_number(
    body: PlivoPhoneNumberCreate, current_user: dict = Depends(get_current_user)
):
    from app.telephony.plivo.plivo_repo import create_phone_number
    from app.telephony.plivo.user_plivo_settings_repo import resolve_account_for_number
    from app.telephony.plivo.plivo_webhook_config import configure_number_webhook
    try:
        number = await asyncio.to_thread(
            create_phone_number, current_user["sub"], body.phone_number, body.friendly_name,
            body.plivo_account_id,
        )
        settings = await asyncio.to_thread(
            resolve_account_for_number, {"plivo_account_id": body.plivo_account_id}, current_user["sub"]
        )
        auth_id = settings.get("auth_id", "")
        token = settings.get("auth_token", "")
        base = (settings.get("webhook_base_url") or "").strip()
        if auth_id and token and base:
            asyncio.create_task(configure_number_webhook(
                current_user["sub"], number["id"], auth_id, token, base
            ))
        return number
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/api/plivo/numbers/{number_id}")
async def update_plivo_phone_number_route(
    number_id: str, body: PlivoPhoneNumberUpdate, current_user: dict = Depends(get_current_user)
):
    from app.telephony.plivo.plivo_repo import update_phone_number
    fields = body.model_dump(exclude_none=True)
    if fields.get("plivo_account_id") == "":
        fields["plivo_account_id"] = None
    try:
        return await asyncio.to_thread(
            update_phone_number, number_id, current_user["sub"], **fields,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/api/plivo/numbers/{number_id}/attach")
async def attach_plivo_number_route(
    number_id: str, body: PhoneNumberAttach, current_user: dict = Depends(get_current_user)
):
    from app.telephony.plivo.plivo_repo import attach_number_to_agent
    try:
        return await asyncio.to_thread(
            attach_number_to_agent, number_id, current_user["sub"], body.agent_id
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/plivo/numbers/{number_id}/attach")
async def detach_plivo_number_route(
    number_id: str, current_user: dict = Depends(get_current_user)
):
    from app.telephony.plivo.plivo_repo import detach_number_from_agent
    try:
        return await asyncio.to_thread(
            detach_number_from_agent, number_id, current_user["sub"]
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/plivo/numbers/{number_id}", status_code=204)
async def delete_plivo_phone_number_route(
    number_id: str, current_user: dict = Depends(get_current_user)
):
    from app.telephony.plivo.plivo_repo import delete_phone_number
    await asyncio.to_thread(delete_phone_number, number_id, current_user["sub"])

@router.get("/api/plivo/settings")
async def get_plivo_settings_route(current_user: dict = Depends(get_current_user)):
    from app.telephony.plivo.user_plivo_settings_repo import get_plivo_settings
    settings = await asyncio.to_thread(get_plivo_settings, current_user["sub"])
    masked = {**settings}
    if masked.get("auth_token"):
        masked["auth_token"] = "•" * 8
        masked["auth_token_set"] = True
    else:
        masked["auth_token_set"] = False
    return masked

@router.put("/api/plivo/settings")
async def update_plivo_settings_route(
    body: PlivoSettingsUpdate, current_user: dict = Depends(get_current_user)
):
    from app.telephony.plivo.user_plivo_settings_repo import get_plivo_settings, upsert_plivo_settings
    from app.telephony.plivo.plivo_webhook_config import configure_all_user_numbers
    existing = await asyncio.to_thread(get_plivo_settings, current_user["sub"])
    auth_token = body.auth_token
    if auth_token and set(auth_token) == {"•"}:
        auth_token = existing.get("auth_token", "")
    saved = await asyncio.to_thread(
        upsert_plivo_settings,
        current_user["sub"],
        body.auth_id,
        auth_token,
        body.webhook_base_url,
    )
    auth_id = body.auth_id or ""
    base = (body.webhook_base_url or "").strip()
    if auth_id and auth_token and base:
        asyncio.create_task(configure_all_user_numbers(
            current_user["sub"], auth_id, auth_token, base
        ))
    return saved

def _mask_plivo_account(account: dict) -> dict:
    masked = {**account}
    if masked.get("auth_token"):
        masked["auth_token"] = "•" * 8
        masked["auth_token_set"] = True
    else:
        masked["auth_token_set"] = False
    return masked

@router.get("/api/plivo/accounts")
async def list_plivo_accounts_route(current_user: dict = Depends(get_current_user)):
    from app.telephony.plivo.user_plivo_settings_repo import list_plivo_accounts
    accounts = await asyncio.to_thread(list_plivo_accounts, current_user["sub"])
    return [_mask_plivo_account(a) for a in accounts]

@router.post("/api/plivo/accounts", status_code=201)
async def create_plivo_account_route(
    body: PlivoAccountCreate, current_user: dict = Depends(get_current_user)
):
    from app.telephony.plivo.user_plivo_settings_repo import create_plivo_account
    account = await asyncio.to_thread(
        create_plivo_account,
        current_user["sub"], body.label, body.auth_id, body.auth_token,
        body.webhook_base_url, bool(body.is_default), body.webhook_base_urls,
    )
    return _mask_plivo_account(account)

@router.put("/api/plivo/accounts/{account_id}")
async def update_plivo_account_route(
    account_id: str, body: PlivoAccountUpdate, current_user: dict = Depends(get_current_user)
):
    from app.telephony.plivo.user_plivo_settings_repo import get_plivo_account, update_plivo_account
    from app.telephony.plivo.plivo_webhook_config import configure_numbers_for_account
    try:
        existing = await asyncio.to_thread(get_plivo_account, account_id, current_user["sub"])
        if not existing:
            raise HTTPException(status_code=404, detail="Plivo account not found")
        auth_token = body.auth_token
        if auth_token and set(auth_token) == {"•"}:
            auth_token = existing.get("auth_token", "")
        fields = body.model_dump(exclude_none=True, exclude={"auth_token"})
        if auth_token is not None:
            fields["auth_token"] = auth_token
        account = await asyncio.to_thread(update_plivo_account, account_id, current_user["sub"], **fields)
        auth_id = account.get("auth_id") or ""
        token = account.get("auth_token") or ""
        base = (account.get("webhook_base_url") or "").strip()
        if auth_id and token and base:
            asyncio.create_task(configure_numbers_for_account(
                current_user["sub"], account_id, bool(account.get("is_default")), auth_id, token, base
            ))
        return _mask_plivo_account(account)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/plivo/accounts/{account_id}", status_code=204)
async def delete_plivo_account_route(
    account_id: str, current_user: dict = Depends(get_current_user)
):
    from app.telephony.plivo.user_plivo_settings_repo import delete_plivo_account
    await asyncio.to_thread(delete_plivo_account, account_id, current_user["sub"])

@router.post("/api/plivo/accounts/{account_id}/set-default")
async def set_default_plivo_account_route(
    account_id: str, current_user: dict = Depends(get_current_user)
):
    from app.telephony.plivo.user_plivo_settings_repo import set_default_plivo_account
    try:
        account = await asyncio.to_thread(set_default_plivo_account, account_id, current_user["sub"])
        return _mask_plivo_account(account)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/plivo/outbound/voice")
@router.post("/plivo/outbound/voice")
async def plivo_outbound_xml(interview_id: str = "", request: Request = None):
    """Plivo fetches this when an outbound HR interview call connects."""
    from app.hr_campaigns.hr_repo import get_interview

    interview = await asyncio.to_thread(get_interview, interview_id) if interview_id else None
    if not interview:
        xml = '<?xml version="1.0"?><Response><Speak>Interview not found.</Speak></Response>'
        return Response(content=xml, media_type="application/xml")

    from app.telephony.plivo.user_plivo_settings_repo import get_plivo_settings
    settings = await asyncio.to_thread(get_plivo_settings, interview["user_id"])
    base_url = (settings.get("webhook_base_url") or "").strip().rstrip("/")
    if not base_url:
        xml = '<?xml version="1.0"?><Response><Speak>Server not configured.</Speak></Response>'
        return Response(content=xml, media_type="application/xml")

    host = base_url.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/plivo/stream?interview_id={quote(interview_id)}"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">{ws_url}</Stream>
</Response>"""
    return Response(content=xml, media_type="application/xml")

_PLIVO_CALL_STATUS_MAP = {
    "busy": "busy",
    "no-answer": "no_answer",
    "failed": "failed",
    "timeout": "no_answer",
    "completed": "completed",  # safety net if the WS "stop" event never fired - mirrors _TWILIO_CALL_STATUS_MAP
}

@router.post("/plivo/campaign/status-callback")
async def plivo_campaign_status_callback(request: Request):
    """Mirrors twilio_campaign_status_callback - handles outcomes Plivo
    never fetches the answer_url for at all (busy/no-answer/failed)."""
    try:
        form = await request.form()
        payload = dict(form)
    except Exception:
        payload = await request.json()
    call_uuid = str(payload.get("CallUUID", ""))
    call_status = str(payload.get("CallStatus", "")).lower()
    new_status = _PLIVO_CALL_STATUS_MAP.get(call_status)

    if call_uuid and new_status:
        from app.hr_campaigns.hr_campaign_repo import mark_candidate_status_from_call_sid
        await asyncio.to_thread(mark_candidate_status_from_call_sid, call_uuid, new_status)

        from app.conversations.conversation_repo import set_call_status_by_sid_if_unconnected
        await asyncio.to_thread(
            set_call_status_by_sid_if_unconnected, call_uuid, new_status,
            _CALL_STATUS_REASONS.get(call_status.replace("-", "_"), _CALL_STATUS_REASONS.get(call_status)),
        )
    return Response(status_code=204)

@router.post("/plivo/interview/status-callback")
async def plivo_interview_status_callback(request: Request):
    """Mirrors twilio_interview_status_callback for Plivo-dialed interviews."""
    try:
        form = await request.form()
        payload = dict(form)
    except Exception:
        payload = await request.json()
    call_uuid = str(payload.get("CallUUID", ""))
    call_status = str(payload.get("CallStatus", "")).lower()
    new_status = _PLIVO_CALL_STATUS_MAP.get(call_status)

    if call_uuid and new_status:
        from app.hr_campaigns.hr_repo import mark_interview_status_by_call_sid
        # Pass the specific outcome through (busy/no_answer/failed/completed)
        # instead of collapsing everything non-"completed" to a generic
        # "failed" - mirrors telephony_twilio.twilio_interview_status_callback.
        await asyncio.to_thread(mark_interview_status_by_call_sid, call_uuid, new_status)

        from app.conversations.conversation_repo import set_call_status_by_sid_if_unconnected
        await asyncio.to_thread(
            set_call_status_by_sid_if_unconnected, call_uuid, new_status,
            _CALL_STATUS_REASONS.get(call_status),
        )
    return Response(status_code=204)

@router.get("/plivo/campaign/outbound/voice")
@router.post("/plivo/campaign/outbound/voice")
async def plivo_campaign_outbound_xml(campaign_candidate_id: str = "", request: Request = None):
    """Plivo fetches this when an outbound campaign call connects. Unlike
    Twilio's synchronous AMD (verdict arrives on this same request), Plivo's
    answering-machine detection is asynchronous and reported via a separate
    callback - so, additively, this endpoint does not attempt AMD at all and
    simply connects the stream. The campaign dialer in hr_campaign_service.py
    only enables AMD on the Twilio path."""
    from app.hr_campaigns.hr_campaign_repo import get_campaign_candidate

    row = await asyncio.to_thread(get_campaign_candidate, campaign_candidate_id) if campaign_candidate_id else None
    campaign = row.get("hr_campaigns") if row else None
    if not row or not campaign:
        xml = '<?xml version="1.0"?><Response><Speak>Campaign call not found.</Speak></Response>'
        return Response(content=xml, media_type="application/xml")

    from app.telephony.plivo.user_plivo_settings_repo import get_plivo_settings
    settings = await asyncio.to_thread(get_plivo_settings, campaign["user_id"])
    base_url = (settings.get("webhook_base_url") or "").strip().rstrip("/")
    if not base_url:
        xml = '<?xml version="1.0"?><Response><Speak>Server not configured.</Speak></Response>'
        return Response(content=xml, media_type="application/xml")

    host = base_url.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/plivo/stream?campaign_candidate_id={quote(campaign_candidate_id)}"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">{ws_url}</Stream>
</Response>"""
    return Response(content=xml, media_type="application/xml")

@router.post("/plivo/voice")
async def plivo_voice_webhook(request: Request):
    """Plivo calls this when someone dials a connected number. Returns PlivoXML."""
    from app.telephony.plivo.plivo_repo import get_number_by_phone
    from app.telephony.plivo.user_plivo_settings_repo import resolve_account_for_number

    try:
        form = await request.form()
        to_number = str(form.get("To", ""))
        from_number = str(form.get("From", ""))
    except Exception:
        to_number = str(request.query_params.get("To", ""))
        from_number = str(request.query_params.get("From", ""))
    print(f"[Plivo Voice] Inbound call, To={to_number!r}")

    number_record = await asyncio.to_thread(get_number_by_phone, to_number)
    if not number_record or not number_record.get("agent_id"):
        print(f"[Plivo Voice] No active number_record with agent_id for To={to_number!r} (record={number_record})")
        xml = '<?xml version="1.0"?><Response><Speak>This number is not connected to an agent.</Speak></Response>'
        return Response(content=xml, media_type="application/xml")

    settings = await asyncio.to_thread(resolve_account_for_number, number_record, number_record["user_id"])
    base_url = (settings.get("webhook_base_url") or "").strip().rstrip("/")
    if not base_url:
        print(f"[Plivo Voice] No webhook_base_url configured for user_id={number_record['user_id']!r}")
        xml = '<?xml version="1.0"?><Response><Speak>This number is not configured yet.</Speak></Response>'
        return Response(content=xml, media_type="application/xml")

    host = base_url.replace("https://", "").replace("http://", "")
    # Plivo reliably forwards query params on the Stream URL (unlike Twilio),
    # so call context travels there directly instead of via <Parameter> tags.
    # The number must be percent-encoded - a raw "+" in a query string is
    # parsed back as a space, which silently broke the inbound number lookup.
    ws_url = f"wss://{host}/plivo/stream?to={quote(to_number)}&from={quote(from_number)}"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">{ws_url}</Stream>
</Response>"""
    return Response(content=xml, media_type="application/xml")

@router.websocket("/plivo/stream")
async def plivo_stream_endpoint(websocket: WebSocket):
    """Handles Plivo Audio Streaming - bidirectional audio for inbound and
    outbound phone calls. Structurally mirrors twilio_stream_endpoint above
    (same VAD/barge-in/turn-taking/tool-calling pipeline); the only real
    differences are Plivo's start/media/stop message shapes and its
    playAudio/clearAudio outbound commands."""
    from app.telephony.plivo.plivo_call_service import plivo_payload_to_pcm16k, plivo_l16_payload_to_pcm16k, text_to_speech_plivo
    from app.voice.vad_service import detect_speech
    from app.voice.deepgram_service import get_streaming_transcriber
    from app.voice.llm_service import (
        generate_response_stream, build_system_prompt,
        pick_filler_phrase, classify_filler_category, detect_tool_call, build_openai_tool,
        stream_tool_result_response, stream_text_as_sentences,
    )
    from app.conversations.message_repo import save_message, get_session_messages
    from app.agents.agent_repo import get_agent, get_agent_functions
    from app.voice.deepgram_service import transcribe_audio_smart
    from app.voice.vad_service import build_wav_bytes
    from app.voice.turn_heuristics import sounds_unfinished
    from app.core.config import TURN_HEURISTIC_ENABLED
    import json, base64

    await websocket.accept()

    agent_id: str = ""
    user_id: str = ""
    interview_id: str = ""
    campaign_candidate_id: str = ""
    campaign_id: str = ""
    campaign_data: dict | None = None
    call_questions: list = []  # trackable questions for interview or campaign calls
    _answered_question_ids: set = set()  # guards against re-recording a question_id, see _record_answer_tool
    extra_system_context: str = ""
    greeting_override: str | None = None
    agent: dict | None = None
    conversation_id: str | None = None
    _tool_functions: list = []
    _kb_documents: list = []  # docs search_knowledge_base may touch - see get_eligible_kb_documents
    initial_memory = None

    stream_id: str | None = None
    _is_mulaw = True  # overwritten once the "start" event reports the actual negotiated encoding
    is_speaking = False
    is_processing = False
    # Bumped every time a new task takes over speaking/processing (greeting,
    # a fresh run_pipeline, or a barge-in). asyncio.Task.cancel() is
    # cooperative - the cancelled task's own `finally` can still run well
    # after a newer task has already started and set is_speaking/is_processing
    # True, and would otherwise stomp that newer task's state back to False.
    # Each task captures its own generation at creation time and only resets
    # the shared flag if it's still the current generation when it finishes.
    _speech_gen = 0
    _call_ended = False
    _call_failure_reason: str | None = None
    # Last filler phrase actually spoken, so the next one can avoid
    # repeating it back-to-back - see pick_filler_phrase in llm_service.py.
    _last_filler: str | None = None
    _dg_stream = None
    _active_task: asyncio.Task | None = None
    _barge_in_streak = 0
    _prev_pcm16k = b""
    _utterance_buffer = bytearray()
    speech_chunks = 0
    silence_chunks = 0
    _turn_extensions_used = 0
    SPEECH_THRESHOLD = 3
    SILENCE_THRESHOLD = 25
    TURN_HEURISTIC_EXTRA_CHUNKS = 50
    TURN_HEURISTIC_MAX_EXTENSIONS = 1
    BARGE_IN_STREAK_NEEDED = 4
    RETRIEVAL_FILLER_DELAY_SECONDS = 1.2

    async def _setup_call(query_params: dict, plivo_call_uuid: str | None) -> bool:
        """Resolve agent/user/conversation. Unlike Twilio, Plivo forwards the
        Stream URL's query string reliably, so call context (to / interview_id
        / campaign_candidate_id) is read straight from it rather than from a
        'start' event payload field."""
        nonlocal agent_id, user_id, interview_id, extra_system_context, greeting_override
        nonlocal agent, conversation_id, _tool_functions, _kb_documents, initial_memory
        nonlocal campaign_candidate_id, campaign_id, campaign_data, call_questions

        interview_id = query_params.get("interview_id", "")
        campaign_candidate_id = query_params.get("campaign_candidate_id", "")
        to = query_params.get("to", "")
        inbound_from = query_params.get("from", "")

        from app.telephony.shared import resolve_call_context
        from app.telephony.plivo.plivo_repo import get_number_by_phone
        ctx = await resolve_call_context(interview_id, campaign_candidate_id, to, get_number_by_phone, "Plivo Stream")
        if ctx is None:
            return False
        agent_id = ctx.agent_id
        user_id = ctx.user_id
        extra_system_context = ctx.extra_system_context
        greeting_override = ctx.greeting_override
        campaign_id = ctx.campaign_id
        campaign_data = ctx.campaign_data
        call_questions = ctx.questions

        agent = await asyncio.to_thread(get_agent, agent_id)
        if not agent:
            print(f"[Plivo Stream] Closing: get_agent({agent_id!r}) returned None")
            return False
        print(f"[Plivo Stream] Agent loaded: {agent.get('name')}")

        from app.core.database import get_supabase
        _db = get_supabase()
        def _create_conv():
            if plivo_call_uuid:
                try:
                    existing = (
                        _db.table("conversations").select("*")
                        .eq("call_sid", plivo_call_uuid).eq("call_status", "ringing")
                        .limit(1).execute()
                    )
                    if existing.data:
                        res = _db.table("conversations").update({"call_status": "in_progress"}) \
                            .eq("id", existing.data[0]["id"]).execute()
                        return res.data[0] if res.data else existing.data[0]
                except Exception as e:
                    print(f"[Plivo Stream] Call Analytics reuse lookup failed (non-fatal): {e}")
            try:
                res = _db.table("conversations").insert({
                    "user_id": user_id,
                    "title": f"Phone call - {agent['name']}",
                    "agent_id": agent_id,
                    "call_status": "in_progress",
                    "call_sid": plivo_call_uuid,
                    "to_number": to or None,
                    "from_number": inbound_from or None,
                }).execute()
                return res.data[0]
            except Exception as e:
                print(f"[Plivo Stream] Insert with Call Analytics columns failed, retrying without them "
                      f"(did you run `alembic upgrade head`?): {e}")
                res = _db.table("conversations").insert({
                    "user_id": user_id,
                    "title": f"Phone call - {agent['name']}",
                    "agent_id": agent_id,
                }).execute()
                return res.data[0]
        conversation = await asyncio.to_thread(_create_conv)
        conversation_id = conversation["id"]

        _all_fns = await asyncio.to_thread(get_agent_functions, agent_id)
        _tool_functions = [f for f in _all_fns if f.get("url") and f.get("trigger_type") == "llm_tool_call"]

        if call_questions:
            from app.telephony.shared import build_record_answer_tool
            _tool_functions = _tool_functions + [build_record_answer_tool(call_questions)]

        # Only offer the tool for documents kb_instructions actually names -
        # a doc merely being selected isn't an instruction to use it. An
        # agent with docs attached but nothing written about them there
        # should behave as if it has no KB at all, and a second selected
        # document kb_instructions never mentions should never be searched
        # just because the first one is (see get_eligible_kb_documents).
        from app.telephony.shared import get_eligible_kb_documents, build_kb_search_tool
        _kb_documents = await get_eligible_kb_documents(agent, user_id)
        if _kb_documents:
            _tool_functions = _tool_functions + [build_kb_search_tool(_kb_documents)]

        # Meeting booking tools - gated like the KB tool above: the per-agent
        # setting must be on AND the owner's Google Calendar actually
        # connected (get_meeting_tool_functions). The prompt block rides in
        # extra_system_context so both the cascade path and the Realtime
        # bridge receive it without extra plumbing. Mirrors
        # app/routers/telephony_twilio.py and app/routers/websockets.py.
        from app.integrations.meeting_tools import get_meeting_tool_functions, build_meeting_system_context
        _meeting_tools = await get_meeting_tool_functions(agent, user_id)
        if _meeting_tools:
            _tool_functions = _tool_functions + _meeting_tools
            extra_system_context += build_meeting_system_context(agent)

        # Never inject the agent OWNER's long_term_memory into a phone call -
        # the caller on a phone call is never the account owner (unlike the
        # browser agent_websocket_endpoint, where it genuinely is), so this
        # would otherwise mislead the LLM into addressing whoever's on the
        # line by the owner's own remembered name/details (e.g. "Anurag,
        # based in Berlin..."), since both get labeled "the user" in the
        # prompt. The row in user_memory is left alone - this only stops it
        # from being read here.
        initial_memory = None

        return True

    async def _send_mulaw(mulaw_bytes: bytes):
        """Send raw μ-law 8 kHz bytes to Plivo via its playAudio command.

        contentType here must be the bare MIME type ("audio/x-mulaw") with no
        ";rate=" suffix - that suffixed form belongs on the <Stream> XML tag's
        contentType attribute, not on playAudio's media.contentType. Plivo
        accepts the suffixed form on the wire without erroring, but silently
        never plays the audio - the caller hears nothing and nothing in our
        logs indicates why."""
        payload = base64.b64encode(mulaw_bytes).decode()
        await websocket.send_text(json.dumps({
            "event": "playAudio",
            "media": {
                "contentType": "audio/x-mulaw",
                "sampleRate": 8000,
                "payload": payload,
            },
        }))

    async def _execute_webhook_plivo(fn: dict, args: dict) -> str:
        import httpx
        method = (fn.get("method") or "POST").upper()
        url = fn["url"]
        headers = fn.get("headers") or {}
        timeout_s = min((fn.get("timeout_ms") or 30000) / 1000, PHONE_WEBHOOK_MAX_TIMEOUT_S)
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

    async def _record_answer_tool(args: dict) -> str:
        """Mirrors app/routers/telephony_twilio.py's _record_answer_tool."""
        from app.telephony.shared import record_question_answer, validate_question_answer
        question_id = args.get("question_id", "")
        answer_text = args.get("answer", "")
        try:
            error = validate_question_answer(question_id, call_questions, _answered_question_ids)
            if error:
                return json.dumps({"error": error})
            question_map = {q["id"]: q["text"] for q in call_questions}
            await record_question_answer(
                interview_id, campaign_candidate_id, question_id, question_map[question_id], answer_text
            )
            _answered_question_ids.add(question_id)
            return json.dumps({"recorded": True})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _execute_kb_search(args: dict, fallback_query: str) -> str:
        """Backs the search_knowledge_base tool (built by
        app.telephony.shared.build_kb_search_tool) - the model calls this only
        on turns it decides actually need the knowledge base, instead of every
        turn paying for an embedding call + pgvector query regardless of
        relevance. Scoped to whichever eligible document the model named
        (resolve_kb_search_doc_ids) so a second selected-but-unmentioned
        document never gets searched just because this tool exists. Mirrors
        app/routers/telephony_twilio.py's _execute_kb_search."""
        from app.telephony.shared import retrieve_phone_rag_context, resolve_kb_search_doc_ids
        restrict_ids = resolve_kb_search_doc_ids(args, _kb_documents)
        full_rag, _ = await retrieve_phone_rag_context(
            args.get("query") or fallback_query, agent, agent_id, user_id, restrict_doc_ids=restrict_ids,
        )
        return full_rag or "No relevant information found in the knowledge base."

    async def _speak(text: str, gen: int):
        nonlocal is_speaking
        is_speaking = True
        try:
            async for mulaw_chunk in text_to_speech_plivo(text):
                await _send_mulaw(mulaw_chunk)
        finally:
            if gen == _speech_gen:
                is_speaking = False

    async def _speak_greeting(text: str, gen: int):
        """Like _speak(), but also saves the line to Call Analytics - mirrors
        app/routers/telephony_twilio.py's _speak_greeting (same bug, same fix:
        the opening greeting is the one assistant turn that bypasses
        run_pipeline, so it never got saved)."""
        await _speak(text, gen)
        await asyncio.to_thread(save_message, conversation_id, user_id, "assistant", text)

    async def _warn_if_stuck_processing(label: str):
        """While is_processing is True, the caller's mic is only checked against
        the strict barge-in VAD threshold, not normal listening - a slow webhook
        or LLM call makes the line look dead with no obvious signal in the logs
        why. Surface it explicitly instead of making this diagnosable only by
        reverse-engineering VAD threshold numbers."""
        await asyncio.sleep(10)
        print(f"[{label}] run_pipeline still processing after 10s (slow webhook/LLM?) - "
              f"caller's speech is gated by the barge-in threshold, not normal listening, until this resolves")

    async def run_pipeline(user_message: str, gen: int):
        nonlocal is_processing
        is_processing = True
        full_reply_parts: list[str] = []
        _retrieval_filler_task: asyncio.Task | None = None
        _stuck_watchdog_task = asyncio.create_task(_warn_if_stuck_processing("Plivo"))

        async def _cover_retrieval_delay():
            nonlocal _last_filler
            await asyncio.sleep(RETRIEVAL_FILLER_DELAY_SECONDS)
            category = classify_filler_category(user_message)
            filler = pick_filler_phrase(agent, category, last_used=_last_filler)
            _last_filler = filler
            full_reply_parts.append(filler)
            await _speak(filler, gen)

        async def _speak_covering_delay(text: str):
            nonlocal _retrieval_filler_task
            if _retrieval_filler_task and not _retrieval_filler_task.done():
                _retrieval_filler_task.cancel()
            await _speak(text, gen)

        try:
            _retrieval_filler_task = asyncio.create_task(_cover_retrieval_delay())

            session_msgs = await asyncio.to_thread(get_session_messages, conversation_id)
            await asyncio.to_thread(save_message, conversation_id, user_id, "user", user_message)

            from app.telephony.shared import build_phone_system_prompt

            base_system = build_phone_system_prompt(agent, extra_system_context)
            # No eager RAG fetch here - when the agent has a KB selected,
            # _tool_functions (built in _setup_call) includes a
            # search_knowledge_base tool so the model can pull doc content
            # only on turns it decides actually need it, instead of every
            # turn paying for an embedding call + pgvector query against docs
            # that are usually irrelevant to what the caller just said.
            system_str = build_system_prompt(
                base_system or None,
                initial_memory,
                "",
                None,
                has_kb=False,
            )
            history = [{"role": m["role"], "content": m["content"]} for m in session_msgs]

            if _tool_functions:
                tools = [build_openai_tool(f) for f in _tool_functions]
                conv_msgs = history + [{"role": "user", "content": user_message}]
                text, tool_name, tool_args, call_id = await detect_tool_call(conv_msgs, system_str, tools)

                if tool_name:
                    from app.integrations.meeting_tools import MEETING_KINDS, execute_meeting_tool
                    from app.voice.llm_service import tool_steps_to_messages

                    async def _dispatch_tool(fn: dict | None, args: dict) -> str:
                        if fn and fn.get("kind") == "internal_record_answer":
                            # Mirrors app/routers/telephony_twilio.py's fix - record_answer
                            # is always fast (a DB write), so the filler is cancelled
                            # outright rather than left to fire on the two sequential LLM
                            # round-trips (detect_tool_call + stream_tool_result_response)
                            # that reliably exceed its delay even with nothing slow going on.
                            if _retrieval_filler_task and not _retrieval_filler_task.done():
                                _retrieval_filler_task.cancel()
                            return await _record_answer_tool(args)
                        if fn and fn.get("kind") == "internal_kb_search":
                            return await _execute_kb_search(args, user_message)
                        if fn and fn.get("kind") in MEETING_KINDS:
                            # Calendar HTTP calls genuinely take time - leave the
                            # retrieval filler running so it can cover the wait.
                            return await execute_meeting_tool(fn, args, agent, user_id, conversation_id)
                        if fn:
                            return await _execute_webhook_plivo(fn, args)
                        return "{}"

                    # Up to 3 chained tool calls per caller turn - mirrors
                    # app/routers/telephony_twilio.py (see the comment there).
                    executed: list[tuple] = []
                    final_text: str | None = None
                    while tool_name and len(executed) < 3:
                        print(f"[Plivo] tool call {len(executed) + 1}: {tool_name}({tool_args})")
                        fn = next((f for f in _tool_functions if f["name"] == tool_name), None)
                        tool_result = await _dispatch_tool(fn, tool_args)
                        executed.append((tool_name, call_id, tool_args, tool_result))
                        final_text, tool_name, tool_args, call_id = await detect_tool_call(
                            conv_msgs + tool_steps_to_messages(executed), system_str, tools
                        )
                    if not tool_name and final_text:
                        async for sentence in stream_text_as_sentences(final_text):
                            await _speak_covering_delay(sentence)
                            full_reply_parts.append(sentence)
                    else:
                        last_name, last_id, last_args, last_result = executed[-1]
                        async for sentence in stream_tool_result_response(
                            conv_msgs + tool_steps_to_messages(executed[:-1]),
                            system_str, last_name, last_id, last_args, last_result
                        ):
                            await _speak_covering_delay(sentence)
                            full_reply_parts.append(sentence)
                else:
                    async for sentence in stream_text_as_sentences(text or ""):
                        await _speak_covering_delay(sentence)
                        full_reply_parts.append(sentence)
            else:
                async for sentence in generate_response_stream(
                    user_message=user_message,
                    session_history=history,
                    rag_context="",
                    long_term_memory=initial_memory,
                    system_prompt_override=agent.get("system_prompt") or None,
                    kb_instructions=agent.get("kb_instructions") or None,
                ):
                    await _speak_covering_delay(sentence)
                    full_reply_parts.append(sentence)

            full_reply = " ".join(full_reply_parts).strip()
            if full_reply:
                await asyncio.to_thread(save_message, conversation_id, user_id, "assistant", full_reply)
        finally:
            if _retrieval_filler_task and not _retrieval_filler_task.done():
                _retrieval_filler_task.cancel()
            if not _stuck_watchdog_task.done():
                _stuck_watchdog_task.cancel()
            if gen == _speech_gen:
                is_processing = False

    try:
        query_params = dict(websocket.query_params)

        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("event")

            if event == "start":
                start_info = data.get("start") or {}
                stream_id = start_info.get("streamId")
                plivo_call_uuid = start_info.get("callId")
                media_format = start_info.get("mediaFormat") or {}
                negotiated_encoding = (media_format.get("encoding") or "").lower()
                # Trust what Plivo actually negotiated, not what our Stream XML
                # asked for - if it isn't μ-law, decoding it with mulaw_decode
                # would silently corrupt every chunk (manifests as needing to
                # shout for the VAD to notice anything).
                _is_mulaw = ("mulaw" in negotiated_encoding) or (negotiated_encoding == "")
                print(f"[Plivo Stream] start event - streamId={stream_id} callId={plivo_call_uuid} mediaFormat={media_format}")
                if not await _setup_call(query_params, plivo_call_uuid):
                    break
                print(f"[Plivo] Call started - stream {stream_id}, agent {agent['name']}")

                from app.admin.admin_config_service import get as get_admin_config
                if await asyncio.to_thread(get_admin_config, "voice_pipeline") == "realtime":
                    from app.telephony.shared import run_realtime_telephony_bridge

                    def _parse_media_payload(data: dict) -> str | None:
                        media = data.get("media") or {}
                        if media.get("track") not in (None, "inbound"):
                            return None
                        return media.get("payload")

                    async def _send_audio_frame(b64_payload: str):
                        await websocket.send_text(json.dumps({
                            "event": "playAudio",
                            "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": b64_payload},
                        }))

                    async def _send_clear():
                        await websocket.send_text(json.dumps({"event": "clearAudio", "streamId": stream_id}))

                    _call_failure_reason = await run_realtime_telephony_bridge(
                        websocket, log_label="Plivo", agent=agent, agent_id=agent_id, user_id=user_id,
                        conversation_id=conversation_id, extra_system_context=extra_system_context,
                        greeting_override=greeting_override, call_questions=call_questions,
                        interview_id=interview_id, campaign_candidate_id=campaign_candidate_id,
                        tool_functions=_tool_functions, parse_media_payload=_parse_media_payload,
                        send_audio_frame=_send_audio_frame, send_clear=_send_clear,
                    )
                    # Not _call_ended = True - unlike the "stop" event path, no
                    # mark_interview/campaign_completed call happened above, so
                    # leave that to the finally block's own fallback for it.
                    break

                greeting = greeting_override or (
                    agent.get("first_message") if agent.get("first_message_enabled") else None
                )
                if greeting:
                    _speech_gen += 1
                    _active_task = asyncio.create_task(_speak_greeting(greeting, _speech_gen))
                    _active_task.add_done_callback(lambda t: log_task_exception(t, "Plivo"))

            elif event == "media":
                if not agent:
                    continue

                media = data.get("media") or {}
                if media.get("track") not in (None, "inbound"):
                    continue  # ignore our own outbound track if Plivo echoes it back

                pcm16k = (
                    plivo_payload_to_pcm16k(media["payload"])
                    if _is_mulaw
                    else plivo_l16_payload_to_pcm16k(media["payload"])
                )
                vad_window = (_prev_pcm16k + pcm16k)[-1024:]
                _prev_pcm16k = pcm16k

                if is_speaking or is_processing:
                    barge_sensitivity = agent.get("barge_in_sensitivity", VAD_BARGE_IN_SENSITIVITY)
                    has_speech = await detect_speech(vad_window, barge_sensitivity)
                    if not has_speech:
                        _barge_in_streak = 0
                        if _dg_stream:
                            try:
                                await _dg_stream.finish()
                            except Exception as e:
                                print(f"[Plivo] Failed to close Deepgram stream on false-positive barge-in (non-fatal): {e}")
                            _dg_stream = None
                            speech_chunks = 0
                            silence_chunks = 0
                            _turn_extensions_used = 0
                            _utterance_buffer = bytearray()
                        continue
                    _barge_in_streak += 1
                    if _barge_in_streak < BARGE_IN_STREAK_NEEDED:
                        continue
                    print("[Plivo] Barge-in detected - interrupting agent")
                    _barge_in_streak = 0
                    if _active_task and not _active_task.done():
                        _active_task.cancel()
                    is_speaking = False
                    is_processing = False
                    await websocket.send_text(json.dumps({"event": "clearAudio", "streamId": stream_id}))
                    _dg_stream = None
                    speech_chunks = 0
                    silence_chunks = 0
                    _turn_extensions_used = 0
                    _utterance_buffer = bytearray()

                has_speech = await detect_speech(vad_window, VAD_SENSITIVITY)

                if has_speech:
                    speech_chunks += 1
                    silence_chunks = 0
                    _turn_extensions_used = 0
                    if _dg_stream is None:
                        try:
                            _dg_stream = get_streaming_transcriber()
                            await _dg_stream.start()
                        except Exception as e:
                            print(f"[Plivo Deepgram] Failed to start stream: {e}")
                            _dg_stream = None
                    _utterance_buffer.extend(pcm16k)
                    if _dg_stream:
                        await _dg_stream.send(pcm16k)
                else:
                    silence_chunks += 1
                    if speech_chunks > 0:
                        _utterance_buffer.extend(pcm16k)
                        if _dg_stream:
                            await _dg_stream.send(pcm16k)

                    required_silence = SILENCE_THRESHOLD + _turn_extensions_used * TURN_HEURISTIC_EXTRA_CHUNKS
                    if speech_chunks >= SPEECH_THRESHOLD and silence_chunks >= required_silence:
                        if (TURN_HEURISTIC_ENABLED
                                and _turn_extensions_used < TURN_HEURISTIC_MAX_EXTENSIONS
                                and _dg_stream and sounds_unfinished(_dg_stream.get_last_partial())):
                            _turn_extensions_used += 1
                            continue

                        dg = _dg_stream
                        _dg_stream = None
                        speech_chunks = 0
                        silence_chunks = 0
                        _turn_extensions_used = 0
                        raw_utterance = bytes(_utterance_buffer)
                        _utterance_buffer = bytearray()

                        transcript = await dg.finish() if dg else None
                        if not (transcript and transcript.strip()) and raw_utterance:
                            print("[Plivo] Streaming transcript empty, falling back to prerecorded API")
                            transcript = await transcribe_audio_smart(build_wav_bytes(raw_utterance))
                        if transcript and transcript.strip():
                            print(f"[Plivo] User said: {transcript.strip()}")
                            _speech_gen += 1
                            _active_task = asyncio.create_task(run_pipeline(transcript.strip(), _speech_gen))
                            _active_task.add_done_callback(lambda t: log_task_exception(t, "Plivo"))

            elif event == "stop":
                # Defensive only - per Plivo's protocol reference, call-end is
                # reported via an HTTP status callback, never a WS "stop"
                # event, so this branch is not expected to fire in practice.
                # The real call-end handling is the `finally` block below,
                # which runs once Plivo closes the socket.
                print(f"[Plivo] Call ended - stream {stream_id}")
                _call_ended = True
                if interview_id:
                    from app.hr_campaigns.hr_repo import mark_interview_completed
                    await asyncio.to_thread(mark_interview_completed, interview_id)
                elif campaign_candidate_id:
                    from app.hr_campaigns.hr_campaign_repo import mark_candidate_completed
                    await asyncio.to_thread(mark_candidate_completed, campaign_id, campaign_candidate_id)
                break

    except WebSocketDisconnect as e:
        # Plivo closes the media stream with code 1000/1001 at the end of a
        # normal call - str(e) on this exception renders as "(1000, '')",
        # which previously got stored as the failure_reason, marking a
        # perfectly successful call as "failed" in Call Analytics.
        if e.code not in (1000, 1001):
            print(f"[Plivo WS] Disconnected with code {e.code}: {e.reason!r}")
            _call_failure_reason = f"WebSocket closed with code {e.code}" + (f": {e.reason}" if e.reason else "")
    except Exception as e:
        if "disconnect" not in str(e).lower():
            print(f"[Plivo WS] Error: {e}")
            _call_failure_reason = str(e)[:500]
    finally:
        if _active_task and not _active_task.done():
            _active_task.cancel()
        if _dg_stream:
            try:
                await _dg_stream.finish()
            except Exception as e:
                print(f"[Plivo] Failed to close Deepgram stream during cleanup (non-fatal): {e}")
        # Always mark interview done on any exit path. Plivo reports call-end
        # via an HTTP status callback, never a WS "stop" event (see the
        # comment on the "stop" branch above), so _call_ended is effectively
        # NEVER True here - this fallback is the primary path for every
        # single Plivo HR/campaign call, not just crashes. _call_failure_reason
        # is already final by this point, so branch on it instead of always
        # marking "completed" regardless of what actually happened.
        if interview_id and not _call_ended:
            try:
                if _call_failure_reason:
                    from app.hr_campaigns.hr_repo import mark_interview_failed
                    await asyncio.to_thread(mark_interview_failed, interview_id)
                else:
                    from app.hr_campaigns.hr_repo import mark_interview_completed
                    await asyncio.to_thread(mark_interview_completed, interview_id)
            except Exception as e:
                print(f"[Plivo] Failed to record interview outcome for interview_id={interview_id}: {e}")
        if campaign_candidate_id and not _call_ended:
            try:
                if _call_failure_reason and plivo_call_uuid:
                    from app.hr_campaigns.hr_campaign_repo import mark_candidate_status_from_call_sid
                    await asyncio.to_thread(mark_candidate_status_from_call_sid, plivo_call_uuid, "failed")
                elif _call_failure_reason:
                    print(f"[Plivo] Call for campaign_candidate_id={campaign_candidate_id} failed "
                          f"({_call_failure_reason!r}) but no call_sid was captured - "
                          f"falling back to marking it completed, since there is no way to "
                          f"target the update without one.")
                    from app.hr_campaigns.hr_campaign_repo import mark_candidate_completed
                    await asyncio.to_thread(mark_candidate_completed, campaign_id, campaign_candidate_id)
                else:
                    from app.hr_campaigns.hr_campaign_repo import mark_candidate_completed
                    await asyncio.to_thread(mark_candidate_completed, campaign_id, campaign_candidate_id)
            except Exception as e:
                print(f"[Plivo] Failed to record campaign candidate outcome for campaign_candidate_id={campaign_candidate_id}: {e}")
        if conversation_id:
            from app.telephony.shared import finalize_call_outcome
            final_status, history = await finalize_call_outcome(conversation_id, _call_failure_reason)
            if interview_id and final_status == "no_response":
                from app.hr_campaigns.hr_repo import mark_interview_no_response
                await asyncio.to_thread(mark_interview_no_response, interview_id)
            try:
                from app.conversations.conversation_repo import set_call_summary
                from app.voice.llm_service import generate_call_summary
                if history:
                    summary = await generate_call_summary(history)
                    if summary:
                        await asyncio.to_thread(set_call_summary, conversation_id, summary)
            except Exception as e:
                print(f"[Plivo] Call summary generation failed (non-fatal): {e}")
        try:
            await websocket.close()
        except Exception as e:
            print(f"[Plivo] websocket.close() raised during cleanup (likely already closed): {e}")
