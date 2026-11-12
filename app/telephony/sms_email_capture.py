"""Capture the caller's email by SMS reply during a live phone call.

Spelling an email out loud is the weakest step of meeting booking (STT
mishears letters, the model has to read it back, the caller has to
correct it). On a phone call we already know the caller's number, so the
agent can instead text them and ask for a reply. The voice call and the
SMS are independent channels: the conversation keeps going while the
reply is outstanding, and when Plivo's message webhook delivers it the
live call is told through an in-process listener (notify_reply) and the
model gets the address in its next turn. The row in sms_email_requests
is the durable copy, so a reply that lands after hangup is kept too.

Two synthetic tools, gated like the meeting tools (only offered when the
meeting tools are on the call AND the transport knows both numbers):
- request_email_by_sms: send the text, remember that we are waiting
- check_sms_reply: what came back so far (for when the caller says
  "sent it" or the Realtime bridge, which has no proactive injection)
"""
import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

# Off for now: callers speak their email and the model spells it back (see
# build_meeting_system_context). Flip to True to offer the tools below on
# Plivo calls again; the webhook and sender stay wired up either way.
SMS_EMAIL_CAPTURE_ENABLED = False

KIND_REQUEST_EMAIL = "internal_sms_request_email"
KIND_CHECK_REPLY = "internal_sms_check_reply"
SMS_EMAIL_KINDS = (KIND_REQUEST_EMAIL, KIND_CHECK_REPLY)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SPOKEN_AT_RE = re.compile(r"\s*\(?\bat\b\)?\s*", re.IGNORECASE)
_SPOKEN_DOT_RE = re.compile(r"\s*\(?\bdot\b\)?\s*", re.IGNORECASE)

_SEND_FAILED_MSG = (
    "The text message could not be sent to this phone right now. Ask the caller to "
    "spell out their email address instead."
)


@dataclass
class SmsCaptureContext:
    user_id: str
    agent_id: str
    agent_name: str
    conversation_id: str | None
    agent_number: str
    caller_number: str
    auth_id: str
    auth_token: str


def extract_email(text: str) -> str | None:
    """First email in a text, tolerating the spoken 'john at gmail dot com'
    form people sometimes type back. Trailing punctuation is dropped."""
    candidate = text or ""
    if "@" not in candidate:
        candidate = _SPOKEN_AT_RE.sub("@", candidate)
        candidate = _SPOKEN_DOT_RE.sub(".", candidate)
    match = _EMAIL_RE.search(candidate)
    return match.group(0).rstrip(".") if match else None


def build_request_email_tool() -> dict:
    return {
        "name": "request_email_by_sms",
        "kind": KIND_REQUEST_EMAIL,
        "description": (
            "Send the caller a text message asking them to reply with their email address, "
            "instead of having them spell it out loud. Use it when they offer to text it, "
            "when the spelling is not coming through clearly, or when they say they would "
            "rather type it. The call continues normally while you wait."
        ),
        "parameters": [],
    }


def build_check_reply_tool() -> dict:
    return {
        "name": "check_sms_reply",
        "kind": KIND_CHECK_REPLY,
        "description": (
            "Check whether the caller has replied to the text message with their email "
            "address yet. Call it when they say they have sent it."
        ),
        "parameters": [],
    }


def build_sms_email_tools() -> list[dict]:
    return [build_request_email_tool(), build_check_reply_tool()]


def build_sms_email_system_context() -> str:
    return (
        "\n\n--- EMAIL BY TEXT MESSAGE ---"
        "\nYou can text the caller's phone to collect their email address instead of having "
        "them spell it out: offer this when you need their email, and use request_email_by_sms "
        "if they prefer it or the spelling is unclear. After sending, tell them a text is on "
        "its way, ask them to reply to it with their email whenever convenient, and keep the "
        "conversation going - do not go silent waiting. You will be told when the reply "
        "arrives; if they say they have sent it and you have not been told, call "
        "check_sms_reply. Use the texted address exactly as received, without spelling it back."
    )


def build_sms_text(agent_name: str) -> str:
    who = (agent_name or "").strip()
    intro = f"Hi, this is {who}. " if who else "Hi. "
    return intro + "Please reply to this message with your email address so we can send your meeting invite."


# conversation_id -> callbacks invoked (as tasks) when that call's reply lands.
_reply_listeners: dict[str, list[Callable[[dict], Awaitable[None]]]] = {}


def register_reply_listener(conversation_id: str, callback: Callable[[dict], Awaitable[None]]) -> None:
    _reply_listeners.setdefault(conversation_id, []).append(callback)


def unregister_reply_listeners(conversation_id: str) -> None:
    _reply_listeners.pop(conversation_id, None)


def notify_reply(row: dict) -> int:
    """Fire the live call's listeners for this reply. Returns how many were
    scheduled (0 when the call is already over - the row still holds it)."""
    from app.telephony.shared import log_task_exception
    listeners = _reply_listeners.get(row.get("conversation_id") or "", [])
    for cb in listeners:
        task = asyncio.create_task(cb(row))
        task.add_done_callback(lambda t: log_task_exception(t, "SMS reply"))
    return len(listeners)


async def handle_inbound_sms(from_number: str, to_number: str, text: str) -> dict | None:
    """Webhook entry point: attach the text to the pending request between
    these numbers (if any), then wake the live call."""
    from app.telephony.sms_email_repo import mark_reply
    email = extract_email(text)
    row = await asyncio.to_thread(mark_reply, from_number, to_number, text, email)
    if not row:
        print(f"[SMS] inbound text from {from_number} to {to_number} matched no pending request - ignored")
        return None
    fired = notify_reply(row)
    print(f"[SMS] reply for conversation {row.get('conversation_id')}: email={email!r}, listeners={fired}")
    return row


async def execute_sms_email_tool(fn: dict, args: dict, ctx: SmsCaptureContext | None) -> str:
    """Shared by the cascade pipeline and the Realtime bridge. Returns spoken
    English for the model; never raises."""
    if ctx is None:
        return "This tool is not available on this call."
    try:
        kind = fn.get("kind")
        if kind == KIND_REQUEST_EMAIL:
            return await _request_email(ctx)
        if kind == KIND_CHECK_REPLY:
            return await _check_reply(ctx)
        return "This tool is not available on this call."
    except Exception as e:
        print(f"[SMS] tool {fn.get('name')} failed: {e!r}")
        return _SEND_FAILED_MSG


async def _request_email(ctx: SmsCaptureContext) -> str:
    from app.telephony.plivo.plivo_sms_service import send_plivo_sms
    from app.telephony.sms_email_repo import create_request, get_latest_for_conversation

    if ctx.conversation_id:
        latest = await asyncio.to_thread(get_latest_for_conversation, ctx.conversation_id)
        if latest and latest.get("status") == "received" and latest.get("email"):
            return (f"The caller already replied by text with the email address {latest['email']}. "
                    "Use it - there is no need to send another text.")
        if latest and latest.get("status") == "pending":
            return ("A text was already sent to the caller's phone and no reply has arrived yet. "
                    "Ask them to check their messages and reply to it; keep the conversation going.")

    message_uuid, error = await send_plivo_sms(
        ctx.auth_id, ctx.auth_token, ctx.agent_number, ctx.caller_number, build_sms_text(ctx.agent_name),
    )
    if error is not None:
        return _SEND_FAILED_MSG
    await asyncio.to_thread(
        create_request, ctx.user_id, ctx.agent_id, ctx.conversation_id,
        ctx.agent_number, ctx.caller_number, message_uuid,
    )
    print(f"[SMS] email request sent to {ctx.caller_number} for conversation {ctx.conversation_id}")
    return ("A text message was sent to the caller's phone. Tell them it is on its way and to reply "
            "to it with their email address whenever convenient, then continue the conversation. "
            "You will be told when the reply arrives.")


async def _check_reply(ctx: SmsCaptureContext) -> str:
    from app.telephony.sms_email_repo import get_latest_for_conversation
    latest = await asyncio.to_thread(get_latest_for_conversation, ctx.conversation_id) if ctx.conversation_id else None
    if not latest:
        return "No text message has been sent on this call yet. Use request_email_by_sms first."
    if latest.get("status") != "received":
        return ("No reply has arrived yet. Ask the caller to check their messages and reply, and "
                "carry on with the conversation in the meantime.")
    if latest.get("email"):
        return (f"The caller replied by text with the email address {latest['email']}. "
                "Use it exactly as given - no need to spell it back.")
    return (f"The caller replied by text with: {latest.get('reply_text') or ''!r}, but that does not "
            "contain a valid email address. Ask them to reply again with just the email, or to spell it out.")


def format_reply_for_transcript(row: dict) -> str:
    """How the texted reply enters the call transcript - as the caller's own
    turn, since it genuinely is something the caller said, just typed."""
    text = (row.get("reply_text") or "").strip()
    return f"[Replied by text message] {text}" if text else "[Replied by text message]"
