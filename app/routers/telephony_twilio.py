"""Twilio telephony routes: numbers/settings/accounts CRUD, outbound TwiML,
status callbacks, inbound voice webhook, and the Media Streams websocket.

The websocket handler (twilio_stream_endpoint) is moved verbatim from the
pre-refactor app/main.py - it is the most heavily-debugged part of this
codebase (barge-in support, turn heuristics, 20ms audio chunking) and its
internal logic/ordering is intentionally left untouched.
"""
import asyncio
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.telephony.shared import PHONE_WEBHOOK_MAX_TIMEOUT_S, _CALL_STATUS_REASONS, log_task_exception
from app.core.config import (
    VAD_SENSITIVITY, VAD_BARGE_IN_SENSITIVITY, VAD_SILENCE_LIMIT, VAD_MIN_SPEECH,
    MEMORY_UPDATE_EVERY,
)

router = APIRouter(tags=["telephony:twilio"])


class PhoneNumberCreate(BaseModel):
    phone_number: str
    friendly_name: Optional[str] = None
    twilio_account_id: Optional[str] = None

class PhoneNumberUpdate(BaseModel):
    friendly_name: Optional[str] = None
    is_active: Optional[bool] = None
    twilio_account_id: Optional[str] = None

class PhoneNumberAttach(BaseModel):
    agent_id: str

class TwilioSettingsUpdate(BaseModel):
    account_sid: Optional[str] = None
    auth_token: Optional[str] = None
    webhook_base_url: Optional[str] = None

class TwilioAccountCreate(BaseModel):
    label: Optional[str] = None
    account_sid: Optional[str] = None
    auth_token: Optional[str] = None
    webhook_base_url: Optional[str] = None
    is_default: Optional[bool] = False
    webhook_base_urls: Optional[List[str]] = None

class TwilioAccountUpdate(BaseModel):
    label: Optional[str] = None
    account_sid: Optional[str] = None
    auth_token: Optional[str] = None
    webhook_base_url: Optional[str] = None
    webhook_base_urls: Optional[List[str]] = None

@router.get("/api/twilio/numbers")
async def list_phone_numbers(current_user: dict = Depends(get_current_user)):
    from app.telephony.twilio.twilio_repo import get_phone_numbers
    numbers = await asyncio.to_thread(get_phone_numbers, current_user["sub"])
    return numbers

@router.post("/api/twilio/numbers", status_code=201)
async def add_phone_number(
    body: PhoneNumberCreate, current_user: dict = Depends(get_current_user)
):
    from app.telephony.twilio.twilio_repo import create_phone_number
    from app.telephony.twilio.user_twilio_settings_repo import resolve_account_for_number
    from app.telephony.twilio.twilio_webhook_config import configure_number_webhook
    try:
        number = await asyncio.to_thread(
            create_phone_number, current_user["sub"], body.phone_number, body.friendly_name,
            body.twilio_account_id,
        )
        # Auto-configure Twilio webhook using the credentials of whichever
        # account this number is assigned to (or the default account).
        settings = await asyncio.to_thread(
            resolve_account_for_number, {"twilio_account_id": body.twilio_account_id}, current_user["sub"]
        )
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

@router.put("/api/twilio/numbers/{number_id}")
async def update_phone_number_route(
    number_id: str, body: PhoneNumberUpdate, current_user: dict = Depends(get_current_user)
):
    from app.telephony.twilio.twilio_repo import update_phone_number
    fields = body.model_dump(exclude_none=True)
    # Empty string means "use the default account" - converted to a real
    # NULL so it clears any specific account assignment in the DB.
    if fields.get("twilio_account_id") == "":
        fields["twilio_account_id"] = None
    try:
        return await asyncio.to_thread(
            update_phone_number,
            number_id,
            current_user["sub"],
            **fields,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/api/twilio/numbers/{number_id}/attach")
async def attach_number_route(
    number_id: str, body: PhoneNumberAttach, current_user: dict = Depends(get_current_user)
):
    from app.telephony.twilio.twilio_repo import attach_number_to_agent
    try:
        return await asyncio.to_thread(
            attach_number_to_agent, number_id, current_user["sub"], body.agent_id
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/twilio/numbers/{number_id}/attach")
async def detach_number_route(
    number_id: str, current_user: dict = Depends(get_current_user)
):
    from app.telephony.twilio.twilio_repo import detach_number_from_agent
    try:
        return await asyncio.to_thread(
            detach_number_from_agent, number_id, current_user["sub"]
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/twilio/numbers/{number_id}", status_code=204)
async def delete_phone_number_route(
    number_id: str, current_user: dict = Depends(get_current_user)
):
    from app.telephony.twilio.twilio_repo import delete_phone_number
    await asyncio.to_thread(delete_phone_number, number_id, current_user["sub"])

@router.get("/api/twilio/settings")
async def get_twilio_settings_route(current_user: dict = Depends(get_current_user)):
    from app.telephony.twilio.user_twilio_settings_repo import get_twilio_settings
    settings = await asyncio.to_thread(get_twilio_settings, current_user["sub"])
    # Never return raw auth token - mask it
    masked = {**settings}
    if masked.get("auth_token"):
        masked["auth_token"] = "•" * 8
        masked["auth_token_set"] = True
    else:
        masked["auth_token_set"] = False
    return masked

@router.put("/api/twilio/settings")
async def update_twilio_settings_route(
    body: TwilioSettingsUpdate, current_user: dict = Depends(get_current_user)
):
    from app.telephony.twilio.user_twilio_settings_repo import get_twilio_settings, upsert_twilio_settings
    from app.telephony.twilio.twilio_webhook_config import configure_all_user_numbers
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

def _mask_twilio_account(account: dict) -> dict:
    masked = {**account}
    if masked.get("auth_token"):
        masked["auth_token"] = "•" * 8
        masked["auth_token_set"] = True
    else:
        masked["auth_token_set"] = False
    return masked

@router.get("/api/twilio/accounts")
async def list_twilio_accounts_route(current_user: dict = Depends(get_current_user)):
    from app.telephony.twilio.user_twilio_settings_repo import list_twilio_accounts
    accounts = await asyncio.to_thread(list_twilio_accounts, current_user["sub"])
    return [_mask_twilio_account(a) for a in accounts]

@router.post("/api/twilio/accounts", status_code=201)
async def create_twilio_account_route(
    body: TwilioAccountCreate, current_user: dict = Depends(get_current_user)
):
    from app.telephony.twilio.user_twilio_settings_repo import create_twilio_account
    account = await asyncio.to_thread(
        create_twilio_account,
        current_user["sub"], body.label, body.account_sid, body.auth_token,
        body.webhook_base_url, bool(body.is_default), body.webhook_base_urls,
    )
    return _mask_twilio_account(account)

@router.put("/api/twilio/accounts/{account_id}")
async def update_twilio_account_route(
    account_id: str, body: TwilioAccountUpdate, current_user: dict = Depends(get_current_user)
):
    from app.telephony.twilio.user_twilio_settings_repo import get_twilio_account, update_twilio_account
    from app.telephony.twilio.twilio_webhook_config import configure_numbers_for_account
    try:
        existing = await asyncio.to_thread(get_twilio_account, account_id, current_user["sub"])
        if not existing:
            raise HTTPException(status_code=404, detail="Twilio account not found")
        # Keep existing auth_token if the request sends the masked placeholder
        auth_token = body.auth_token
        if auth_token and set(auth_token) == {"•"}:
            auth_token = existing.get("auth_token", "")
        fields = body.model_dump(exclude_none=True, exclude={"auth_token"})
        if auth_token is not None:
            fields["auth_token"] = auth_token
        account = await asyncio.to_thread(update_twilio_account, account_id, current_user["sub"], **fields)
        sid = account.get("account_sid") or ""
        token = account.get("auth_token") or ""
        base = (account.get("webhook_base_url") or "").strip()
        if sid and token and base:
            asyncio.create_task(configure_numbers_for_account(
                current_user["sub"], account_id, bool(account.get("is_default")), sid, token, base
            ))
        return _mask_twilio_account(account)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/twilio/accounts/{account_id}", status_code=204)
async def delete_twilio_account_route(
    account_id: str, current_user: dict = Depends(get_current_user)
):
    from app.telephony.twilio.user_twilio_settings_repo import delete_twilio_account
    await asyncio.to_thread(delete_twilio_account, account_id, current_user["sub"])

@router.post("/api/twilio/accounts/{account_id}/set-default")
async def set_default_twilio_account_route(
    account_id: str, current_user: dict = Depends(get_current_user)
):
    from app.telephony.twilio.user_twilio_settings_repo import set_default_twilio_account
    try:
        account = await asyncio.to_thread(set_default_twilio_account, account_id, current_user["sub"])
        return _mask_twilio_account(account)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/twilio/outbound/voice")
async def twilio_outbound_twiml(interview_id: str = "", request: Request = None):
    """Twilio fetches this TwiML when an outbound call connects."""
    from app.hr_campaigns.hr_repo import get_interview

    interview = await asyncio.to_thread(get_interview, interview_id) if interview_id else None
    if not interview:
        xml = '<?xml version="1.0"?><Response><Say>Interview not found.</Say></Response>'
        return Response(content=xml, media_type="application/xml")

    from app.telephony.twilio.user_twilio_settings_repo import get_twilio_settings
    settings = await asyncio.to_thread(get_twilio_settings, interview["user_id"])
    base_url = (settings.get("webhook_base_url") or "").strip().rstrip("/")
    if not base_url:
        xml = '<?xml version="1.0"?><Response><Say>Server not configured.</Say></Response>'
        return Response(content=xml, media_type="application/xml")

    host = base_url.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/twilio/stream"
    # Custom data must travel via <Parameter>, not a query string on the Stream
    # url - Twilio does not reliably forward URL query params to the WebSocket.
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="interview_id" value="{interview_id}" />
    </Stream>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")

_TWILIO_CALL_STATUS_MAP = {
    "busy": "busy",
    "no-answer": "no_answer",
    "failed": "failed",
    "canceled": "failed",
    "completed": "completed",  # safety net if the WS "stop" event never fired
}

@router.post("/twilio/campaign/status-callback")
async def twilio_campaign_status_callback(request: Request):
    form = await request.form()
    call_sid = str(form.get("CallSid", ""))
    call_status = str(form.get("CallStatus", ""))
    new_status = _TWILIO_CALL_STATUS_MAP.get(call_status)

    if call_sid and new_status:
        from app.hr_campaigns.hr_campaign_repo import mark_candidate_status_from_call_sid
        await asyncio.to_thread(mark_candidate_status_from_call_sid, call_sid, new_status)

        from app.conversations.conversation_repo import set_call_status_by_sid_if_unconnected
        await asyncio.to_thread(
            set_call_status_by_sid_if_unconnected, call_sid, new_status,
            _CALL_STATUS_REASONS.get(call_status),
        )

    return Response(status_code=204)

@router.post("/twilio/interview/status-callback")
async def twilio_interview_status_callback(request: Request):
    """Mirrors twilio_campaign_status_callback, for single-scheduled HR
    interview calls (app/hr_scheduler.py) - same reasoning, same map."""
    form = await request.form()
    call_sid = str(form.get("CallSid", ""))
    call_status = str(form.get("CallStatus", ""))
    new_status = _TWILIO_CALL_STATUS_MAP.get(call_status)

    if call_sid and new_status:
        from app.hr_campaigns.hr_repo import mark_interview_status_by_call_sid
        # Pass the specific outcome through (busy/no_answer/failed/completed)
        # instead of collapsing everything non-"completed" to a generic
        # "failed" - mark_interview_status_by_call_sid's own "calling" guard
        # already keeps this from racing the WS stream lifecycle.
        await asyncio.to_thread(mark_interview_status_by_call_sid, call_sid, new_status)

        from app.conversations.conversation_repo import set_call_status_by_sid_if_unconnected
        await asyncio.to_thread(
            set_call_status_by_sid_if_unconnected, call_sid, new_status,
            _CALL_STATUS_REASONS.get(call_status),
        )

    return Response(status_code=204)

@router.get("/twilio/campaign/outbound/voice")
async def twilio_campaign_outbound_twiml(campaign_candidate_id: str = "", AnsweredBy: str = "", request: Request = None):
    """
    Twilio fetches this TwiML when an outbound campaign call connects. Uses
    SYNCHRONOUS answering-machine detection (Twilio's default - no AsyncAmd):
    Twilio decides human vs machine before ever fetching this URL, and
    delivers the verdict as the AnsweredBy query param on this same GET
    request (the dialer places calls with Method="GET", same convention as
    the existing hr_interviews outbound flow). This means there is no race
    between AMD and <Connect><Stream> - for a machine, we hang up immediately
    and never open the stream at all.
    """
    from app.hr_campaigns.hr_campaign_repo import get_campaign_candidate, mark_candidate_machine_detected

    row = await asyncio.to_thread(get_campaign_candidate, campaign_candidate_id) if campaign_candidate_id else None
    campaign = row.get("hr_campaigns") if row else None
    if not row or not campaign:
        xml = '<?xml version="1.0"?><Response><Say>Campaign call not found.</Say></Response>'
        return Response(content=xml, media_type="application/xml")

    if AnsweredBy.startswith("machine") or AnsweredBy == "fax":
        await asyncio.to_thread(mark_candidate_machine_detected, campaign_candidate_id)
        xml = '<?xml version="1.0"?><Response><Hangup/></Response>'
        return Response(content=xml, media_type="application/xml")

    from app.telephony.twilio.user_twilio_settings_repo import get_twilio_settings
    settings = await asyncio.to_thread(get_twilio_settings, campaign["user_id"])
    base_url = (settings.get("webhook_base_url") or "").strip().rstrip("/")
    if not base_url:
        xml = '<?xml version="1.0"?><Response><Say>Server not configured.</Say></Response>'
        return Response(content=xml, media_type="application/xml")

    host = base_url.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/twilio/stream"
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="campaign_candidate_id" value="{campaign_candidate_id}" />
    </Stream>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")

@router.post("/twilio/voice")
async def twilio_voice_webhook(request: Request):
    """Twilio calls this when someone dials a connected number. Returns TwiML."""
    from app.telephony.twilio.twilio_repo import get_number_by_phone
    from app.telephony.twilio.user_twilio_settings_repo import resolve_account_for_number

    form = await request.form()
    to_number = str(form.get("To", ""))
    from_number = str(form.get("From", ""))
    print(f"[Twilio Voice] Inbound call, To={to_number!r}")

    # Look up the number record to find the owner's webhook base URL
    number_record = await asyncio.to_thread(get_number_by_phone, to_number)
    if not number_record or not number_record.get("agent_id"):
        print(f"[Twilio Voice] No active number_record with agent_id for To={to_number!r} (record={number_record})")
        xml = '<?xml version="1.0"?><Response><Say>This number is not connected to an agent.</Say></Response>'
        return Response(content=xml, media_type="application/xml")

    settings = await asyncio.to_thread(resolve_account_for_number, number_record, number_record["user_id"])
    base_url = (settings.get("webhook_base_url") or "").strip().rstrip("/")
    if not base_url:
        print(f"[Twilio Voice] No webhook_base_url configured for user_id={number_record['user_id']!r}")
        xml = '<?xml version="1.0"?><Response><Say>This number is not configured yet.</Say></Response>'
        return Response(content=xml, media_type="application/xml")

    # Strip protocol - WebSocket URL needs just host+path
    host = base_url.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/twilio/stream"

    # Custom data must travel via <Parameter>, not a query string on the Stream
    # url - Twilio does not reliably forward URL query params to the WebSocket.
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="to" value="{to_number}" />
      <Parameter name="from" value="{from_number}" />
    </Stream>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")

@router.websocket("/twilio/stream")
async def twilio_stream_endpoint(websocket: WebSocket):
    """Handles Twilio Media Streams - bidirectional audio for inbound and outbound phone calls."""
    from app.telephony.twilio.twilio_call_service import twilio_payload_to_pcm16k, text_to_speech_twilio
    from app.voice.vad_service import detect_speech
    from app.voice.deepgram_service import get_streaming_transcriber
    from app.voice.llm_service import (
        generate_response_stream, build_system_prompt,
        pick_filler_phrase, classify_filler_category, detect_tool_call, build_openai_tool,
        stream_tool_result_response, stream_text_as_sentences,
    )
    from app.voice.elevenlabs_service import text_to_speech_stream
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
    campaign_candidate_id: str = ""  # set only for HR call-campaign calls (Stage 6)
    campaign_id: str = ""            # parent campaign, needed to release the dialer's claim
    campaign_data: dict | None = None  # holds questions, for context + record_answer validation
    call_questions: list = []  # trackable questions for interview or campaign calls
    _answered_question_ids: set = set()  # guards against re-recording a question_id, see _record_answer_tool
    extra_system_context: str = ""  # injected for outbound HR calls
    greeting_override: str | None = None
    agent: dict | None = None
    conversation_id: str | None = None
    _tool_functions: list = []
    _kb_documents: list = []  # docs search_knowledge_base may touch - see get_eligible_kb_documents
    initial_memory = None

    stream_sid: str | None = None
    is_speaking = False     # True while TTS audio is being sent to Twilio
    is_processing = False   # True while LLM pipeline is running
    # Bumped every time a new task takes over speaking/processing (greeting,
    # a fresh run_pipeline, or a barge-in). asyncio.Task.cancel() is
    # cooperative - the cancelled task's own `finally` can still run well
    # after a newer task has already started and set is_speaking/is_processing
    # True, and would otherwise stomp that newer task's state back to False.
    # Each task captures its own generation at creation time and only resets
    # the shared flag if it's still the current generation when it finishes.
    _speech_gen = 0
    _call_ended = False     # tracks whether we've already marked the interview done
    _call_failure_reason: str | None = None  # set on genuine errors, surfaced in Call Analytics
    # Last filler phrase actually spoken, so the next one can avoid
    # repeating it back-to-back - see pick_filler_phrase in llm_service.py.
    _last_filler: str | None = None
    _dg_stream = None       # active StreamingTranscriber (opened on first speech chunk)
    _active_task: asyncio.Task | None = None  # current greeting/run_pipeline task, cancellable on barge-in
    _barge_in_streak = 0    # consecutive chunks over threshold while agent is speaking/processing
    _prev_pcm16k = b""      # previous chunk, borrowed to give VAD a real (non-padded) window
    _utterance_buffer = bytearray()  # mirrors what's sent to Deepgram, for prerecorded fallback
    speech_chunks = 0
    silence_chunks = 0
    _turn_extensions_used = 0  # how many times the heuristic has already extended this utterance's wait
    SPEECH_THRESHOLD = 3    # chunks needed to confirm speech started
    SILENCE_THRESHOLD = 25  # ~1.5 s at 8 kHz / 20 ms chunks
    TURN_HEURISTIC_EXTRA_CHUNKS = 50    # extra silence tolerance per extension (~1 s at 20 ms/chunk)
    TURN_HEURISTIC_MAX_EXTENSIONS = 1   # cap so a stuck/garbled transcript can't stall the turn forever
    BARGE_IN_STREAK_NEEDED = 4  # ~80ms of sustained signal required before cancelling the agent
    RETRIEVAL_FILLER_DELAY_SECONDS = 1.2  # speak a filler if RAG/LLM hasn't produced anything by then

    async def _setup_call(custom_params: dict, twilio_call_sid: str | None) -> bool:
        """Resolve agent/user/conversation from the Twilio 'start' event's
        customParameters. Twilio does not reliably forward query strings on the
        <Stream> url, so call context (to / interview_id) travels via <Parameter>
        tags instead, which arrive here once the stream actually starts."""
        nonlocal agent_id, user_id, interview_id, extra_system_context, greeting_override
        nonlocal agent, conversation_id, _tool_functions, _kb_documents, initial_memory
        nonlocal campaign_candidate_id, campaign_id, campaign_data, call_questions

        interview_id = custom_params.get("interview_id", "")
        campaign_candidate_id = custom_params.get("campaign_candidate_id", "")
        to = custom_params.get("to", "")
        inbound_from = custom_params.get("from", "")

        from app.telephony.shared import resolve_call_context
        from app.telephony.twilio.twilio_repo import get_number_by_phone
        ctx = await resolve_call_context(interview_id, campaign_candidate_id, to, get_number_by_phone, "Twilio Stream")
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
            print(f"[Twilio Stream] Closing: get_agent({agent_id!r}) returned None")
            return False
        print(f"[Twilio Stream] Agent loaded: {agent.get('name')}")

        # Create conversation record - or reuse one already created at dial-time
        # (HR interview/campaign calls eagerly record a "ringing" row before
        # Twilio ever answers, so busy/no-answer calls - which never reach this
        # point at all - still show up in Call Analytics; see hr_scheduler.py /
        # hr_campaign_service.py). Falling through to a fresh insert keeps
        # inbound calls, which have no such row, working exactly as before.
        from app.core.database import get_supabase
        _db = get_supabase()
        def _create_conv():
            if twilio_call_sid:
                try:
                    existing = (
                        _db.table("conversations").select("*")
                        .eq("call_sid", twilio_call_sid).eq("call_status", "ringing")
                        .limit(1).execute()
                    )
                    if existing.data:
                        res = _db.table("conversations").update({"call_status": "in_progress"}) \
                            .eq("id", existing.data[0]["id"]).execute()
                        return res.data[0] if res.data else existing.data[0]
                except Exception as e:
                    # Non-fatal - at worst this creates a second row instead of
                    # reusing the eagerly-created one; see the except below for
                    # the case where Call Analytics columns don't exist at all.
                    print(f"[Twilio Stream] Call Analytics reuse lookup failed (non-fatal): {e}")
            try:
                res = _db.table("conversations").insert({
                    "user_id": user_id,
                    "title": f"Phone call - {agent['name']}",
                    "agent_id": agent_id,
                    "call_status": "in_progress",
                    "call_sid": twilio_call_sid,
                    "to_number": to or None,
                    "from_number": inbound_from or None,
                }).execute()
                return res.data[0]
            except Exception as e:
                # call_status/call_sid/to_number/from_number (migrations
                # 0017/0026) may not exist yet on this database - a live phone
                # call must never fail to connect just because Call Analytics
                # hasn't been migrated in. Retry with only the pre-Call-Analytics
                # columns.
                print(f"[Twilio Stream] Insert with Call Analytics columns failed, retrying without them "
                      f"(did you run `alembic upgrade head`?): {e}")
                res = _db.table("conversations").insert({
                    "user_id": user_id,
                    "title": f"Phone call - {agent['name']}",
                    "agent_id": agent_id,
                }).execute()
                return res.data[0]
        conversation = await asyncio.to_thread(_create_conv)
        conversation_id = conversation["id"]

        # Load tool functions and memory
        _all_fns = await asyncio.to_thread(get_agent_functions, agent_id)
        _tool_functions = [f for f in _all_fns if f.get("url") and f.get("trigger_type") == "llm_tool_call"]

        if call_questions:
            # Synthesized in-memory tool, not stored in agent_functions - only
            # ever present for interview/campaign calls. Dispatched by a "kind"
            # marker (checked at the call site below), not by name, since a real
            # webhook-backed agent_functions row could theoretically share the
            # name "record_answer" and _execute_webhook_twilio indexes fn["url"]
            # without .get(), which would KeyError/500 mid-call on a collision.
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
        # app/routers/telephony_plivo.py and app/routers/websockets.py.
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
        """Handler for the synthesized "internal_record_answer" tool (interview
        and campaign calls). See validate_question_answer for what's rejected
        and why."""
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
        app/routers/telephony_plivo.py's _execute_kb_search."""
        from app.telephony.shared import retrieve_phone_rag_context, resolve_kb_search_doc_ids
        restrict_ids = resolve_kb_search_doc_ids(args, _kb_documents)
        full_rag, _ = await retrieve_phone_rag_context(
            args.get("query") or fallback_query, agent, agent_id, user_id, restrict_doc_ids=restrict_ids,
        )
        return full_rag or "No relevant information found in the knowledge base."

    async def _speak(text: str, gen: int):
        """TTS → μ-law → send to Twilio, set/unset is_speaking flag."""
        nonlocal is_speaking
        is_speaking = True
        try:
            async for mulaw_chunk in text_to_speech_twilio(text):
                await _send_mulaw(mulaw_chunk)
        finally:
            if gen == _speech_gen:
                is_speaking = False

    async def _speak_greeting(text: str, gen: int):
        """Like _speak(), but also saves the line to Call Analytics - the
        opening greeting (the fixed greeting_override for interview/campaign
        calls, or the agent's first_message for inbound calls) is the only
        assistant turn in the whole call that bypasses run_pipeline, so
        without this it was the one line in every transcript Call Analytics
        never showed (found 2026-11-04, reported as "the first message is
        not shown")."""
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
        _stuck_watchdog_task = asyncio.create_task(_warn_if_stuck_processing("Twilio"))

        async def _cover_retrieval_delay():
            nonlocal _last_filler
            # RAG lookups + the LLM's own "thinking" time can occasionally run
            # long before anything is said. If nothing has been spoken within
            # this window, say a filler phrase so the caller hears something
            # instead of dead air, rather than cancelling or rushing the work.
            await asyncio.sleep(RETRIEVAL_FILLER_DELAY_SECONDS)
            category = classify_filler_category(user_message)
            filler = pick_filler_phrase(agent, category, last_used=_last_filler)
            _last_filler = filler
            full_reply_parts.append(filler)
            await _speak(filler, gen)

        async def _speak_covering_delay(text: str):
            """Like _speak(), but also cancels the pending retrieval filler -
            whatever gets said first (real content or the filler itself) wins,
            so the two never overlap or double up."""
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
                    fn = next((f for f in _tool_functions if f["name"] == tool_name), None)
                    if fn and fn.get("kind") == "internal_record_answer":
                        # record_answer is always fast (a DB write, no network call) -
                        # there's never a real delay worth covering, so the filler is
                        # cancelled outright rather than left to the timer. Left to the
                        # timer, detect_tool_call + stream_tool_result_response's two
                        # sequential LLM round-trips reliably exceed the filler delay
                        # on their own even though nothing slow is actually happening,
                        # so a filler like "let me pull that up" was speaking on nearly
                        # every plain yes/no answer (found 2026-11-04).
                        if _retrieval_filler_task and not _retrieval_filler_task.done():
                            _retrieval_filler_task.cancel()
                        tool_result = await _record_answer_tool(tool_args)
                    elif fn and fn.get("kind") == "internal_kb_search":
                        tool_result = await _execute_kb_search(tool_args, user_message)
                    elif fn and fn.get("kind") in ("internal_check_availability", "internal_book_meeting"):
                        # Calendar HTTP calls genuinely take time - leave the
                        # retrieval filler running so it can cover the wait.
                        from app.integrations.meeting_tools import execute_meeting_tool
                        tool_result = await execute_meeting_tool(fn, tool_args, agent, user_id, conversation_id)
                    elif fn:
                        # A webhook call genuinely can be slow (an external HTTP
                        # request) - leave the retrieval filler task running so it
                        # can still cover that if needed.
                        tool_result = await _execute_webhook_twilio(fn, tool_args)
                    else:
                        tool_result = "{}"
                    async for sentence in stream_tool_result_response(
                        conv_msgs, system_str, tool_name, call_id, tool_args, tool_result
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
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("event")

            if event == "start":
                stream_sid = data["start"]["streamSid"]
                twilio_call_sid = data["start"].get("callSid") or None
                custom_params = data["start"].get("customParameters") or {}
                print(f"[Twilio Stream] start event - customParameters={custom_params}")
                if not await _setup_call(custom_params, twilio_call_sid):
                    break
                print(f"[Twilio] Call started - stream {stream_sid}, agent {agent['name']}")

                from app.admin.admin_config_service import get as get_admin_config
                if await asyncio.to_thread(get_admin_config, "voice_pipeline") == "realtime":
                    from app.telephony.shared import run_realtime_telephony_bridge

                    def _parse_media_payload(data: dict) -> str | None:
                        return (data.get("media") or {}).get("payload")

                    async def _send_audio_frame(b64_payload: str):
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": b64_payload},
                        }))

                    async def _send_clear():
                        await websocket.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))

                    _call_failure_reason = await run_realtime_telephony_bridge(
                        websocket, log_label="Twilio", agent=agent, agent_id=agent_id, user_id=user_id,
                        conversation_id=conversation_id, extra_system_context=extra_system_context,
                        greeting_override=greeting_override, call_questions=call_questions,
                        interview_id=interview_id, campaign_candidate_id=campaign_candidate_id,
                        tool_functions=_tool_functions, parse_media_payload=_parse_media_payload,
                        send_audio_frame=_send_audio_frame, send_clear=_send_clear,
                    )
                    # Not _call_ended = True - no mark_interview/campaign_completed
                    # call happened above, so leave that to the finally block below.
                    break

                greeting = greeting_override or (
                    agent.get("first_message") if agent.get("first_message_enabled") else None
                )
                if greeting:
                    _speech_gen += 1
                    _active_task = asyncio.create_task(_speak_greeting(greeting, _speech_gen))
                    _active_task.add_done_callback(lambda t: log_task_exception(t, "Twilio"))

            elif event == "media":
                if not agent:
                    continue  # "start" hasn't completed setup yet

                pcm16k = twilio_payload_to_pcm16k(data["media"]["payload"])
                # Twilio chunks (20ms/320 samples) are shorter than Silero's minimum
                # window (32ms/512 samples). Borrow the tail of the previous chunk to
                # give VAD a real, contiguous window instead of zero-padding a lone
                # short chunk, which the model was never designed to see.
                vad_window = (_prev_pcm16k + pcm16k)[-1024:]
                _prev_pcm16k = pcm16k

                if is_speaking or is_processing:
                    # Barge-in: check if the caller is talking over the agent. Require a
                    # short sustained streak (not a single chunk) before committing to
                    # cancel - a lone high-confidence chunk is as likely to be a brief
                    # noise/line artifact as it is real, sustained speech, and cancelling
                    # the agent's only in-flight response is not reversible.
                    barge_sensitivity = agent.get("barge_in_sensitivity", VAD_BARGE_IN_SENSITIVITY)
                    has_speech = await detect_speech(vad_window, barge_sensitivity)
                    if not has_speech:
                        _barge_in_streak = 0
                        if _dg_stream:
                            try:
                                await _dg_stream.finish()
                            except Exception as e:
                                print(f"[Twilio] Failed to close Deepgram stream on false-positive barge-in (non-fatal): {e}")
                            _dg_stream = None
                            speech_chunks = 0
                            silence_chunks = 0
                            _turn_extensions_used = 0
                            _utterance_buffer = bytearray()
                        continue
                    _barge_in_streak += 1
                    if _barge_in_streak < BARGE_IN_STREAK_NEEDED:
                        continue
                    print("[Twilio] Barge-in detected - interrupting agent")
                    _barge_in_streak = 0
                    if _active_task and not _active_task.done():
                        _active_task.cancel()
                    is_speaking = False
                    is_processing = False
                    if stream_sid:
                        await websocket.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))
                    _dg_stream = None
                    speech_chunks = 0
                    silence_chunks = 0
                    _turn_extensions_used = 0
                    _utterance_buffer = bytearray()
                    # fall through - treat this chunk as the start of new speech

                has_speech = await detect_speech(vad_window, VAD_SENSITIVITY)

                if has_speech:
                    speech_chunks += 1
                    silence_chunks = 0
                    _turn_extensions_used = 0  # real speech resumed - give the next pause a fresh budget
                    # Open streaming transcriber on first speech chunk
                    if _dg_stream is None:
                        try:
                            _dg_stream = get_streaming_transcriber()
                            await _dg_stream.start()
                        except Exception as e:
                            print(f"[Twilio Deepgram] Failed to start stream: {e}")
                            _dg_stream = None
                    _utterance_buffer.extend(pcm16k)
                    if _dg_stream:
                        await _dg_stream.send(pcm16k)
                else:
                    silence_chunks += 1
                    if speech_chunks > 0:
                        _utterance_buffer.extend(pcm16k)
                        if _dg_stream:
                            await _dg_stream.send(pcm16k)  # send trailing silence so Deepgram finalizes

                    # Silence alone doesn't know if the caller finished a
                    # thought or just paused mid-sentence ("...and, um...").
                    # Each used extension pushes the required silence further
                    # out, giving them more time instead of cutting them off.
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
                            # Streaming connection occasionally loses audio (e.g. cold
                            # connection handshake timing) - fall back to the
                            # prerecorded API on the buffered audio rather than
                            # silently dropping the caller's utterance.
                            print("[Twilio] Streaming transcript empty, falling back to prerecorded API")
                            transcript = await transcribe_audio_smart(build_wav_bytes(raw_utterance))
                        if transcript and transcript.strip():
                            print(f"[Twilio] User said: {transcript.strip()}")
                            _speech_gen += 1
                            _active_task = asyncio.create_task(run_pipeline(transcript.strip(), _speech_gen))
                            _active_task.add_done_callback(lambda t: log_task_exception(t, "Twilio"))

            elif event == "stop":
                print(f"[Twilio] Call ended - stream {stream_sid}")
                _call_ended = True
                if interview_id:
                    from app.hr_campaigns.hr_repo import mark_interview_completed
                    await asyncio.to_thread(mark_interview_completed, interview_id)
                elif campaign_candidate_id:
                    from app.hr_campaigns.hr_campaign_repo import mark_candidate_completed
                    await asyncio.to_thread(mark_candidate_completed, campaign_id, campaign_candidate_id)
                break

    except WebSocketDisconnect as e:
        # Twilio closes the media stream with code 1000/1001 at the end of a
        # normal call - str(e) on this exception renders as "(1000, '')",
        # which previously got stored as the failure_reason, marking a
        # perfectly successful call as "failed" in Call Analytics.
        if e.code not in (1000, 1001):
            print(f"[Twilio WS] Disconnected with code {e.code}: {e.reason!r}")
            _call_failure_reason = f"WebSocket closed with code {e.code}" + (f": {e.reason}" if e.reason else "")
    except Exception as e:
        if "disconnect" not in str(e).lower():
            print(f"[Twilio WS] Error: {e}")
            _call_failure_reason = str(e)[:500]
    finally:
        # The call is over (hangup, disconnect, or clean "stop") - an
        # in-flight greeting/run_pipeline task left running would keep
        # generating and speaking a reply nobody can hear, then crash with
        # an unhandled "Cannot call send once a close message has been
        # sent" once it tries to write to the socket we're about to close.
        if _active_task and not _active_task.done():
            _active_task.cancel()
        # Clean up any open Deepgram stream
        if _dg_stream:
            try:
                await _dg_stream.finish()
            except Exception as e:
                print(f"[Twilio] Failed to close Deepgram stream during cleanup (non-fatal): {e}")
        # Always mark interview done on any exit path (crash, disconnect, clean
        # stop). This is the ONLY path reached for the realtime pipeline (which
        # always leaves _call_ended False, see above) and is effectively the
        # primary path for any call that errors before a clean "stop" event -
        # _call_failure_reason is already final by this point (set by the
        # except blocks above, which run before finally), so branch on it
        # instead of always marking "completed" regardless of what happened.
        if interview_id and not _call_ended:
            try:
                if _call_failure_reason:
                    from app.hr_campaigns.hr_repo import mark_interview_failed
                    await asyncio.to_thread(mark_interview_failed, interview_id)
                else:
                    from app.hr_campaigns.hr_repo import mark_interview_completed
                    await asyncio.to_thread(mark_interview_completed, interview_id)
            except Exception as e:
                print(f"[Twilio] Failed to record interview outcome for interview_id={interview_id}: {e}")
        # Same fallback for campaign calls - mirrors the interview_id path above
        if campaign_candidate_id and not _call_ended:
            try:
                if _call_failure_reason and twilio_call_sid:
                    from app.hr_campaigns.hr_campaign_repo import mark_candidate_status_from_call_sid
                    await asyncio.to_thread(mark_candidate_status_from_call_sid, twilio_call_sid, "failed")
                elif _call_failure_reason:
                    print(f"[Twilio] Call for campaign_candidate_id={campaign_candidate_id} failed "
                          f"({_call_failure_reason!r}) but no call_sid was captured - "
                          f"falling back to marking it completed, since there is no way to "
                          f"target the update without one.")
                    from app.hr_campaigns.hr_campaign_repo import mark_candidate_completed
                    await asyncio.to_thread(mark_candidate_completed, campaign_id, campaign_candidate_id)
                else:
                    from app.hr_campaigns.hr_campaign_repo import mark_candidate_completed
                    await asyncio.to_thread(mark_candidate_completed, campaign_id, campaign_candidate_id)
            except Exception as e:
                print(f"[Twilio] Failed to record campaign candidate outcome for campaign_candidate_id={campaign_candidate_id}: {e}")
        # Call Analytics — record the final outcome. Best-effort:
        # finalize_call_outcome swallows its own errors, and conversation_id
        # may be None if setup failed before a row was ever created (nothing
        # to record in that case).
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
                print(f"[Twilio] Call summary generation failed (non-fatal): {e}")
        try:
            await websocket.close()
        except Exception as e:
            # Expected when the client already closed the socket first - logged
            # at this verbosity only so a genuine close failure isn't invisible.
            print(f"[Twilio] websocket.close() raised during cleanup (likely already closed): {e}")
