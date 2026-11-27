"""Shared logic and constants used by both the Twilio and Plivo telephony
pipelines. Pulled out here so that a fix to call-context resolution, prompt
assembly, or KB retrieval lands once instead of needing to be hand-copied
into both app/routers/telephony_plivo.py and app/routers/telephony_twilio.py."""
import asyncio
import re

# Phone calls can't tolerate a long silent wait on a webhook/tool call - while
# one is in flight, is_processing stays True and the caller's mic is only
# checked against the (intentionally strict) barge-in VAD threshold, not
# normal listening, so a slow/unreachable webhook makes the call look dead.
# Hard-cap regardless of the per-function timeout_ms an admin configured.


PHONE_WEBHOOK_MAX_TIMEOUT_S = 15

# Grace period between the end_call tool firing and the call actually being
# torn down. The farewell line the model spoke right before calling the tool
# has already been fully SENT to the provider by that point, but "sent" isn't
# "played" - Twilio/Plivo still have to actually play those queued audio
# frames out over the phone line, and there is no ack telling us when that
# finishes. Closing the connection too soon would truncate the caller's last
# bit of audio mid-word, which is exactly the failure mode this feature must
# never cause - so this errs generous rather than tight. Cancelled (see
# schedule_end_call) the instant the caller is heard speaking again, so a
# "wait, actually..." right after the goodbye overrides the hangup instead of
# the call ending underneath them.
END_CALL_GRACE_SECONDS = 3.0

# Shared pooled HTTP client for webhook tool calls. Webhooks fire mid-turn
# (the caller is waiting on the line), and a fresh httpx.AsyncClient per call
# paid a new TCP+TLS handshake every time; the pool reuses connections to
# webhook hosts hit more than once in a call. Timeouts are per-request.
_webhook_http_client = None


def get_webhook_http_client():
    global _webhook_http_client
    if _webhook_http_client is None:
        import httpx
        _webhook_http_client = httpx.AsyncClient()
    return _webhook_http_client


async def execute_webhook_request(fn: dict, args: dict) -> str:
    """Run one admin-configured webhook tool call and render the result as a
    JSON string for the LLM. Never raises - errors come back as {"error": ...}
    so a flaky webhook can't kill the live call. Shared by the Twilio, Plivo,
    browser, and Realtime pipelines."""
    import json
    method = (fn.get("method") or "POST").upper()
    url = fn["url"]
    headers = fn.get("headers") or {}
    timeout_s = min((fn.get("timeout_ms") or 30000) / 1000, PHONE_WEBHOOK_MAX_TIMEOUT_S)
    try:
        hclient = get_webhook_http_client()
        if method == "GET":
            resp = await hclient.get(url, params=args, headers=headers, timeout=timeout_s)
        elif method == "DELETE":
            resp = await hclient.delete(url, params=args, headers=headers, timeout=timeout_s)
        else:
            resp = await hclient.request(method, url, json=args, headers=headers, timeout=timeout_s)
        try:
            return json.dumps(resp.json())
        except Exception:
            return json.dumps({"response": resp.text, "status": resp.status_code})
    except Exception as e:
        return json.dumps({"error": str(e)})


def log_task_exception(task: "asyncio.Task", label: str) -> None:
    """Done-callback for the fire-and-forget greeting/run_pipeline task.
    Without this, any exception inside it (a failed DB write, a bug in the
    LLM/tool pipeline) disappears except for Python's generic 'Task
    exception was never retrieved' warning at some later, unrelated point -
    no indication of what failed or where. A barge-in replacing the task
    with a new one is an expected cancellation, not an error, so it's not
    logged."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        print(f"[{label}] Unhandled exception in background task: {exc!r}")


_CALL_STATUS_REASONS = {
    "busy": "The line was busy.",
    "no-answer": "The call was not answered.",
    "failed": "The call could not be connected.",
    "canceled": "The call was canceled before it connected.",
}


class CallContext:
    """Resolved outcome of _setup_call's interview/campaign/inbound branches."""
    def __init__(self):
        self.agent_id: str = ""
        self.user_id: str = ""
        self.extra_system_context: str = ""
        self.greeting_override: str | None = None
        self.campaign_id: str = ""
        self.campaign_data: dict | None = None
        self.interview_id: str = ""
        # Trackable questions for this call, shared shape for both interview
        # and campaign calls: [{"id": "q1", "text": "..."}, ...]. Empty for
        # inbound calls and interviews with no specific_questions configured.
        self.questions: list[dict] = []


async def resolve_call_context(
    interview_id: str,
    campaign_candidate_id: str,
    to: str,
    get_number_by_phone,
    log_label: str,
) -> CallContext | None:
    """Resolve agent_id/user_id/extra_system_context/greeting_override for a
    phone call from (in priority order) a scheduled interview, a call-campaign
    candidate, or - for neither - an inbound call matched by the dialed number.

    get_number_by_phone is passed in since Plivo and Twilio each have their
    own repo module for it (app.telephony.plivo.plivo_repo /
    app.telephony.twilio.twilio_repo); everything else here is identical
    between the two providers. Returns None if the call should be closed
    (caller already logged why)."""
    ctx = CallContext()

    if interview_id:
        from app.hr_campaigns.hr_repo import get_interview
        interview = await asyncio.to_thread(get_interview, interview_id)
        if not interview:
            print(f"[{log_label}] Closing: no interview found for interview_id={interview_id!r}")
            return None
        ctx.agent_id = interview["agent_id"]
        ctx.user_id = interview["user_id"]
        ctx.interview_id = interview_id
        candidate = interview.get("hr_candidates") or {}
        c_name = candidate.get("name", "the candidate")
        c_role = candidate.get("role", "")
        c_resume = candidate.get("resume_text", "")
        c_notes = candidate.get("notes", "")
        c_phone = candidate.get("phone", "")
        questions_raw = interview.get("specific_questions", "") or ""
        # One question per line - each non-empty line is tracked separately
        # (answered/not answered + the actual answer) via the record_answer
        # tool below, same mechanism campaign calls already use.
        all_questions = [
            {"id": f"q{i+1}", "text": line.strip()}
            for i, line in enumerate(questions_raw.splitlines())
            if line.strip()
        ]
        # A retry (no-answer/failed on a prior attempt) reuses this same
        # interview_id, and prior attempts' answers are already recorded in
        # hr_interview_answers - drop those so a retry doesn't re-ask
        # questions the candidate already answered on an earlier attempt.
        from app.hr_campaigns.hr_repo import list_interview_answers
        already_answered = {
            a["question_id"] for a in await asyncio.to_thread(list_interview_answers, interview_id)
        }
        ctx.questions = [q for q in all_questions if q["id"] not in already_answered]
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
        questions_to_ask = "\n".join(f"- {q['text']}" for q in ctx.questions)
        id_mapping = "\n".join(f'  "{q["text"]}" -> question_id="{q["id"]}"' for q in ctx.questions)
        ctx.extra_system_context = (
            f"\n\n--- CURRENT CALL CONTEXT ---"
            f"\nYou are calling {c_name} for a{'n' if c_role and c_role[0].lower() in 'aeiou' else ''} {c_role or 'interview'} interview."
            + (f"\nThe interview is scheduled for: {sched_formatted}." if sched_formatted else "")
            + f"\nYou are calling {call_lead} minutes before the interview time as a pre-interview reminder."
            + f"\nThe first message in this conversation is the greeting you already spoke, asking to speak with {c_name} by name."
            + (
                f"\nYou are calling {c_name} at {c_phone}, their number on file - you already have it. "
                f"Never ask for their phone number or contact number; if they ask how you called them "
                f"or question whether you have their number, confirm you're already calling the number "
                f"on file ({c_phone}) and that you'll use it to follow up."
                if c_phone else ""
            )
            + (f"\n\nCandidate resume:\n{c_resume}" if c_resume else "")
            + (f"\n\nHR notes about this candidate:\n{c_notes}" if c_notes else "")
            + (
                f"\n\nAsk this candidate the following specific questions, one at a time:\n{questions_to_ask}"
                f"\n\nIf an answer seems incomplete, unclear, or doesn't actually address the question, "
                f"you may ask ONE clarifying follow-up before moving on - never more than one per question. "
                f"After the candidate's answer (and any single follow-up) is good enough to record, call the "
                f"record_answer tool. Use this mapping to choose the correct question_id argument - this "
                f"mapping is for your internal use only, never say a question_id out loud to the caller:\n{id_mapping}"
                if ctx.questions else ""
            )
        )
        ctx.greeting_override = f"Hello, may I speak with {c_name}? This is an automated call from HR regarding your upcoming interview."
        return ctx

    if campaign_candidate_id:
        from app.hr_campaigns.hr_campaign_repo import get_campaign_candidate
        row = await asyncio.to_thread(get_campaign_candidate, campaign_candidate_id)
        ctx.campaign_data = row.get("hr_campaigns") if row else None
        if not row or not ctx.campaign_data:
            print(f"[{log_label}] Closing: no campaign_candidate found for campaign_candidate_id={campaign_candidate_id!r}")
            return None
        ctx.campaign_id = ctx.campaign_data["id"]
        ctx.agent_id = ctx.campaign_data["agent_id"]
        ctx.user_id = ctx.campaign_data["user_id"]
        candidate = row.get("hr_candidates") or {}
        c_name = candidate.get("name", "the candidate")
        c_resume = candidate.get("resume_text", "")
        c_notes = candidate.get("notes", "")
        c_phone = candidate.get("phone", "")
        all_questions_list = ctx.campaign_data.get("questions") or []
        # Same retry-dedup as the interview branch above - this
        # campaign_candidate_id is reused across retries, and earlier
        # attempts' answers are already in hr_campaign_answers.
        from app.hr_campaigns.hr_campaign_repo import list_campaign_answers
        already_answered = {
            a["question_id"] for a in await asyncio.to_thread(list_campaign_answers, campaign_candidate_id)
        }
        questions_list = [q for q in all_questions_list if q.get("id") not in already_answered]
        ctx.questions = [
            {"id": q.get("id"), "text": q.get("text", "")}
            for q in questions_list if q.get("text")
        ]
        # Spoken text and internal ids are kept in separate lists - a combined
        # "1. [id=q1] question text" format got read aloud verbatim by the LLM
        # in live testing ("bracket id q2 close bracket..."), and the model
        # also invented an extra question beyond the configured list.
        questions_to_ask = "\n".join(
            f"- {q.get('text', '')}" for q in questions_list if q.get("text")
        )
        id_mapping = "\n".join(
            f'  "{q.get("text", "")}" -> question_id="{q.get("id")}"'
            for q in questions_list if q.get("text")
        )
        ctx.extra_system_context = (
            f"\n\n--- CURRENT CALL CONTEXT ---"
            f"\nYou are calling {c_name} for a brief screening call."
            + f"\nThe first message in this conversation is the greeting you already spoke, asking to speak with {c_name} by name."
            + (
                f"\nYou are calling {c_name} at {c_phone}, their number on file - you already have it. "
                f"Never ask for their phone number or contact number; if they ask how you called them "
                f"or question whether you have their number, confirm you're already calling the number "
                f"on file ({c_phone}) and that you'll use it to follow up."
                if c_phone else ""
            )
            + (f"\n\nCandidate resume:\n{c_resume}" if c_resume else "")
            + (f"\n\nHR notes about this candidate:\n{c_notes}" if c_notes else "")
            + (
                f"\n\nThese are the ONLY questions to ask, in this exact order. Do not ask "
                f"any other questions and do not invent additional ones:\n{questions_to_ask}"
                f"\n\nAfter the candidate answers a question, call the record_answer tool. "
                f"Use this mapping to choose the correct question_id argument - this mapping "
                f"is for your internal use only, never say a question_id or the word "
                f"'mapping' out loud to the caller:\n{id_mapping}"
                if questions_to_ask else ""
            )
            + "\nOnce every question above has been asked and answered, thank them and end the call politely."
        )
        ctx.greeting_override = f"Hello, may I speak with {c_name}? This is a quick screening call regarding your application."
        return ctx

    print(f"[{log_label}] Inbound call, to={to!r}")
    number_record = await asyncio.to_thread(get_number_by_phone, to) if to else None
    if not number_record or not number_record.get("agent_id"):
        print(f"[{log_label}] Closing: no active number_record with agent_id for to={to!r} (record={number_record})")
        return None
    ctx.agent_id = number_record["agent_id"]
    ctx.user_id = number_record["user_id"]
    print(f"[{log_label}] Resolved agent_id={ctx.agent_id} user_id={ctx.user_id}")
    return ctx


def build_record_answer_tool(questions: list[dict]) -> dict:
    """The record_answer tool synthesized for calls with trackable questions
    (interviews and campaign screening calls alike) - not stored in
    agent_functions, only ever present for these calls."""
    question_ids = ", ".join(q.get("id", "") for q in questions if q.get("id"))
    return {
        "name": "record_answer",
        "kind": "internal_record_answer",
        "description": "Record the candidate's answer to one of the specific questions for this call.",
        "parameters": [
            {"name": "question_id", "type": "string",
             "description": f"The question ID being answered. Must be exactly one of: {question_ids}"},
            {"name": "answer", "type": "string",
             "description": "The candidate's answer, summarized concisely."},
        ],
    }


def build_end_call_tool() -> dict:
    """Synthetic tool (same shape/convention as build_record_answer_tool and
    build_kb_search_tool) offered on EVERY phone call by default. Each router
    (telephony_twilio.py/telephony_plivo.py) adds this only when BOTH the
    admin-wide 'end_call_tool_enabled' kill switch and the agent's own
    'end_call_tool_enabled' column (default true, toggled from the "Custom
    functions" card in the agents UI) allow it. Lets the model hang up the
    call itself once the conversation has genuinely finished, instead of the
    caller sitting on an open line nobody is using.

    Deliberately does nothing by itself beyond flagging intent - no audio, no
    immediate hangup. The description below is the only thing standing
    between this and a call getting cut off mid-sentence, so it is written
    to push the model toward speaking its goodbye FIRST, as an ordinary
    reply, in the same turn, and only calling this tool right after. The
    actual hangup is deferred by END_CALL_GRACE_SECONDS and aborted outright
    if the caller is heard speaking again before that elapses - see
    schedule_end_call and its call sites in telephony_twilio.py /
    telephony_plivo.py / this file's own run_realtime_telephony_bridge."""
    return {
        "name": "end_call",
        "kind": "internal_end_call",
        "description": (
            "End the phone call. Only call this once the conversation is genuinely "
            "over - for example the caller has said goodbye, thanks, or bye, has "
            "explicitly asked to end the call or hang up, or there is truly nothing "
            "left to discuss and they are not waiting on you for anything. Always "
            "speak a short, natural closing line out loud FIRST, as your normal "
            "reply (e.g. \"Thanks for calling, take care!\"), and only call this "
            "tool right after that line in the same turn - never before it, and "
            "never silently with nothing said. Do NOT call this if the caller is "
            "still asking something, might still want to talk, or you are not "
            "completely sure the conversation is finished - when in doubt, keep "
            "talking instead of ending the call."
        ),
        "parameters": [
            {"name": "reason", "type": "string",
             "description": "Brief internal note on why the call is ending, e.g. "
                             "'caller said goodbye'. Never say this out loud."},
        ],
    }


def schedule_end_call(set_requested) -> "asyncio.Task":
    """Returns a running, cancellable task that calls set_requested() (no
    args) after END_CALL_GRACE_SECONDS elapses uninterrupted. Each pipeline
    passes a tiny closure that flips its own local '_end_call_requested'
    flag - the flag/task pair itself has to live in each call's own scope
    (Twilio/Plivo cascade, the Realtime bridge), since each already has its
    own receive loop and its own caller-speech detection to cancel this
    against; only the delay/cancel mechanics are shared here."""
    async def _wait():
        try:
            await asyncio.sleep(END_CALL_GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        set_requested()
    return asyncio.create_task(_wait())


def _normalize_doc_ref(text: str) -> str:
    """Collapse case/punctuation/extension differences so a document's
    display_name can be matched against free-form kb_instructions text -
    "Employee_Referral Policy.pdf" needs to match instructions written as
    "...employee referral policy questions..."."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def filter_kb_documents_by_instructions(kb_instructions: str, documents: list[dict]) -> list[dict]:
    """Keep only the documents (each {"id", "display_name"}) kb_instructions
    actually permits.

    With zero or one document selected there's nothing to disambiguate - any
    non-empty kb_instructions ("use the attached documents...") unambiguously
    refers to the one document there is, so it's eligible without needing to
    be named. With two or more selected, a generic instruction like that
    can't tell them apart, so only the documents actually named in
    kb_instructions are eligible: if an agent has two documents selected but
    kb_instructions only talks about one of them by name, the other must
    never be searched just because *some* KB instructions exist - same
    principle as build_phone_system_prompt's "no kb_instructions at all means
    no KB tool at all", one level more specific."""
    normalized_instructions = _normalize_doc_ref(kb_instructions or "")
    if not normalized_instructions:
        return []
    if len(documents) <= 1:
        return list(documents)
    eligible = []
    for doc in documents:
        name = doc.get("display_name") or ""
        stem = name.rsplit(".", 1)[0] if "." in name else name
        normalized_name = _normalize_doc_ref(stem)
        if normalized_name and normalized_name in normalized_instructions:
            eligible.append(doc)
    return eligible


async def get_eligible_kb_documents(agent: dict, user_id: str) -> list[dict]:
    """Resolve this agent's selected_kb_doc_ids to the {"id", "display_name"}
    rows this user actually owns, then filter to the ones kb_instructions
    actually references (filter_kb_documents_by_instructions). This is the
    full set of documents this call's search_knowledge_base tool is allowed
    to touch - empty if there's no kb_instructions, no selected docs, or none
    of the selected docs are actually named in kb_instructions."""
    doc_ids = set(agent.get("selected_kb_doc_ids") or [])
    kb_instructions = agent.get("kb_instructions") or ""
    if not doc_ids or not kb_instructions.strip():
        return []
    from app.kb.user_kb_repo import get_user_documents
    owned = await asyncio.to_thread(get_user_documents, user_id)
    selected_owned = [d for d in owned if d["id"] in doc_ids]
    return filter_kb_documents_by_instructions(kb_instructions, selected_owned)


def build_kb_search_tool(documents: list[dict]) -> dict:
    """Synthetic tool (same shape as build_record_answer_tool - never stored
    in agent_functions) that lets the model pull knowledge-base content only
    when it decides a turn actually needs it, instead of every turn paying
    for an embedding call + pgvector query against docs that are usually
    irrelevant to what the caller just said.

    `documents` is the eligible set from get_eligible_kb_documents - already
    filtered down to the documents kb_instructions actually names. Only
    called (and only added to a call's tools) when that list is non-empty.
    When there's more than one eligible document, a `document` parameter is
    added so the model picks which one to search per-turn instead of pooling
    every eligible document's chunks into one search; its valid values are
    spelled out in the description rather than enforced as a JSON-schema
    enum (build_openai_tool/build_realtime_tool don't support one - see
    validate_question_answer's docstring for the same tradeoff on
    record_answer's question_id), so the caller must still validate the
    returned value instead of trusting it."""
    params = [{"name": "query", "type": "string", "description": "What to search for"}]
    if len(documents) > 1:
        names = ", ".join(f'"{d["display_name"]}"' for d in documents)
        params.append({
            "name": "document",
            "type": "string",
            "description": f"Which document to search. Must be exactly one of: {names}",
        })
    return {
        "name": "search_knowledge_base",
        "kind": "internal_kb_search",
        "description": (
            "Search the knowledge base for information relevant to the caller's "
            "question. You have no real knowledge of what these documents contain "
            "until you call this - call it before answering ANY question about "
            "their subject matter, even one you feel confident about, instead of "
            "guessing from general/training knowledge. Only skip it for things "
            "already established earlier in this same conversation (e.g. the "
            "conversation's own history, or a document excerpt already returned "
            "by an earlier call to this tool this turn)."
        ),
        "parameters": params,
    }


def resolve_kb_search_doc_ids(args: dict, documents: list[dict]) -> list[str]:
    """Map a search_knowledge_base tool call's `document` argument back to a
    doc id to restrict the search to. Falls back to every eligible document
    (never to anything outside `documents` - that set is already the full
    extent of what's allowed) if the model omitted the argument or named
    something that doesn't match, e.g. a hallucinated name."""
    requested = (args.get("document") or "").strip().lower()
    matched = [d for d in documents if (d.get("display_name") or "").strip().lower() == requested]
    if matched:
        return [matched[0]["id"]]
    return [d["id"] for d in documents]


def validate_question_answer(question_id: str, call_questions: list[dict], answered_question_ids: set) -> str | None:
    """Returns an error message if a record_answer tool call should be
    rejected, else None. Two cases: an unknown id (the LLM has no enum
    constraint available to stop it hallucinating one - build_openai_tool
    doesn't support one), or an id already recorded earlier in this same
    call. The latter was found live (2026-11-04): the model occasionally
    answers a later, different question but reuses an earlier question's
    id, silently overwriting it with a mismatched answer while the real
    question goes unrecorded. Pulled out as a pure function, separate from
    the record_answer tool handlers in telephony_twilio.py/telephony_plivo.py,
    so this has a direct unit test - driving it through two separate real
    conversation turns over the full WS/VAD pipeline turned out to be too
    racy to do reliably."""
    question_map = {q["id"]: q["text"] for q in call_questions}
    if question_id not in question_map:
        return f"Unknown question_id {question_id!r}"
    if question_id in answered_question_ids:
        return (
            f"question_id {question_id!r} was already recorded earlier in this call - "
            f"if this is a different question, double check the mapping and use its own id."
        )
    return None


async def record_question_answer(
    interview_id: str, campaign_candidate_id: str,
    question_id: str, question_text: str, answer_text: str,
) -> None:
    """Persist a record_answer tool call to whichever table matches this
    call's type - hr_interview_answers for scheduled interviews,
    hr_campaign_answers for campaign screening calls."""
    if interview_id:
        from app.hr_campaigns.hr_repo import record_interview_answer
        await asyncio.to_thread(record_interview_answer, interview_id, question_id, question_text, answer_text)
    elif campaign_candidate_id:
        from app.hr_campaigns.hr_campaign_repo import record_answer
        await asyncio.to_thread(record_answer, campaign_candidate_id, question_id, question_text, answer_text)


def build_phone_system_prompt(agent: dict, extra_system_context: str) -> str:
    """Combine the agent's own name + persona prompt + hr_instructions +
    call-specific context into the base system prompt string for a phone
    call. The name line ensures the model can correctly answer "what's your
    name" mid-call even though phone calls (unlike the browser WS path)
    never speak a templated greeting that states it out loud. Also mirrors
    what app/routers/websockets.py does for hr_instructions on browser
    sessions - phone calls were missing that entirely until this was added.

    kb_instructions is folded in here too, unconditionally, rather than left
    to build_system_prompt's rag_context/has_kb gating - the phone pipelines
    fetch KB content on demand via a search_knowledge_base tool rather than
    an eager per-turn retrieval, so this is the only place left where the
    agent's own KB guidance (e.g. "use them as your source of truth") still
    reaches the model."""
    hr = (agent.get("hr_instructions") or "").strip()
    kb = (agent.get("kb_instructions") or "").strip()
    name = (agent.get("name") or "").strip()
    return (
        "Always respond in English unless the caller explicitly asks you to switch languages.\n\n"
        + (f"Your name is {name}. If asked your name, say {name}.\n\n" if name else "")
        + (agent.get("system_prompt") or "")
        + (f"\n\n--- HR INSTRUCTIONS ---\n{hr}" if hr else "")
        + (f"\n\n--- KNOWLEDGE BASE INSTRUCTIONS ---\n{kb}" if kb else "")
        + extra_system_context
    )


async def run_realtime_telephony_bridge(
    websocket,
    *,
    log_label: str,
    agent: dict,
    agent_id: str,
    user_id: str,
    conversation_id: str,
    extra_system_context: str,
    greeting_override: str | None,
    call_questions: list[dict],
    interview_id: str,
    campaign_candidate_id: str,
    tool_functions: list[dict],
    parse_media_payload,          # (data: dict) -> str | None - base64 mu-law payload, or None to ignore this message
    send_audio_frame,             # async (b64_payload: str) -> None - relay to the caller, provider's own wire format
    send_clear,                   # async () -> None - stop whatever's currently queued for playback (barge-in)
    sms_capture=None,             # SmsCaptureContext when the email-by-text tools are on this call (Plivo only so far)
    kb_documents: list | None = None,  # pass _setup_call's get_eligible_kb_documents result to skip refetching it here
) -> str | None:
    """Bridges a phone call directly to OpenAI's Realtime API - no VAD, no
    Deepgram, no llm_service, no separate TTS call. Caller audio (already
    g711_ulaw/8kHz base64 from Plivo/Twilio) is forwarded to the Realtime
    session byte-for-byte and its audio comes back the same way, since both
    providers' native telephony encoding is exactly what the Realtime API
    calls "g711_ulaw". Shared between telephony_plivo.py and
    telephony_twilio.py - call this once "start" has been consumed (so
    agent/conversation are already resolved); it owns the receive loop for
    every message after that and returns a failure reason string (or None
    on a clean end) for the caller's own existing Call Analytics wrap-up.
    """
    import asyncio
    import base64
    import json
    from fastapi import WebSocketDisconnect
    from app.voice.realtime_service import RealtimeSession, build_realtime_tool
    from app.conversations.message_repo import save_message
    from app.admin.admin_config_service import get as get_admin_config

    tools = [build_realtime_tool(f) for f in tool_functions]
    # Only offer the tool for documents kb_instructions actually names - a
    # doc merely being selected isn't an instruction to use it (see
    # get_eligible_kb_documents). An agent with docs attached but nothing
    # written about them there should behave as if it has no KB at all.
    # Skip adding it when the caller's _setup_call already put it in
    # tool_functions (both phone routers do) - otherwise the Realtime
    # session is configured with two tools named search_knowledge_base,
    # which the browser bridge in app/routers/websockets.py explicitly
    # warns against.
    # kb_documents, when the caller passes it, is _setup_call's own
    # get_eligible_kb_documents result - refetching here repeated the same
    # DB lookups during call setup for nothing.
    _kb_documents = kb_documents if kb_documents is not None else await get_eligible_kb_documents(agent, user_id)
    if _kb_documents and not any(f.get("kind") == "internal_kb_search" for f in tool_functions):
        tools = tools + [build_realtime_tool(build_kb_search_tool(_kb_documents))]

    voice = await asyncio.to_thread(get_admin_config, "realtime_voice")
    instructions = build_phone_system_prompt(agent, extra_system_context)
    session = RealtimeSession(instructions=instructions, tools=tools, audio_format="g711_ulaw", voice=voice)
    await session.connect()

    _answered_question_ids: set = set()
    _failure_reason: str | None = None
    _active_item_id: str | None = None
    _bytes_out_for_item: dict[str, int] = {}
    _end_call_requested = False  # flips True once the end_call tool's grace period elapses uninterrupted
    _end_call_task: "asyncio.Task | None" = None  # pending grace-period timer for the above, cancelled if the caller speaks again first

    async def _execute_tool(name: str, args: dict) -> str:
        nonlocal _end_call_task
        if name == "end_call":
            print(f"[{log_label}] end_call tool invoked (reason={(args.get('reason') or '')!r}) - "
                  f"ending call in {END_CALL_GRACE_SECONDS}s unless the caller speaks again")
            if _end_call_task and not _end_call_task.done():
                _end_call_task.cancel()

            def _mark_end_call():
                nonlocal _end_call_requested
                _end_call_requested = True
            _end_call_task = schedule_end_call(_mark_end_call)
            return json.dumps({"ending": True})
        if name == "search_knowledge_base":
            restrict_ids = resolve_kb_search_doc_ids(args, _kb_documents)
            full_rag, _ = await retrieve_phone_rag_context(
                args.get("query", ""), agent, agent_id, user_id, restrict_doc_ids=restrict_ids,
            )
            return full_rag or "No relevant information found."
        fn = next((f for f in tool_functions if f["name"] == name), None)
        if fn and fn.get("kind") == "internal_record_answer":
            question_id = args.get("question_id", "")
            answer_text = args.get("answer", "")
            error = validate_question_answer(question_id, call_questions, _answered_question_ids)
            if error:
                return json.dumps({"error": error})
            question_map = {q["id"]: q["text"] for q in call_questions}
            await record_question_answer(
                interview_id, campaign_candidate_id, question_id, question_map[question_id], answer_text
            )
            _answered_question_ids.add(question_id)
            return json.dumps({"recorded": True})
        from app.integrations.meeting_tools import MEETING_KINDS, execute_meeting_tool
        if fn and fn.get("kind") in MEETING_KINDS:
            return await execute_meeting_tool(fn, args, agent, user_id, conversation_id)
        from app.telephony.sms_email_capture import SMS_EMAIL_KINDS, execute_sms_email_tool
        if fn and fn.get("kind") in SMS_EMAIL_KINDS:
            return await execute_sms_email_tool(fn, args, sms_capture)
        if not fn:
            return json.dumps({"error": "function not found"})
        return await execute_webhook_request(fn, args)

    async def _drain_realtime_events():
        nonlocal _active_item_id, _failure_reason, _end_call_task
        try:
            async for event in session.events():
                etype = event.get("type")

                if etype == "response.audio.delta":
                    item_id = event.get("item_id")
                    delta_b64 = event.get("delta") or ""
                    _active_item_id = item_id
                    _bytes_out_for_item[item_id] = (
                        _bytes_out_for_item.get(item_id, 0) + len(base64.b64decode(delta_b64))
                    )
                    await send_audio_frame(delta_b64)

                elif etype == "input_audio_buffer.speech_started":
                    if _end_call_task and not _end_call_task.done():
                        print(f"[{log_label}] Caller started speaking - cancelling pending end_call hangup")
                        _end_call_task.cancel()
                        _end_call_task = None
                    if _active_item_id:
                        await session.cancel_response()
                        elapsed_ms = int(_bytes_out_for_item.get(_active_item_id, 0) / session.bytes_per_ms)
                        await session.truncate(_active_item_id, elapsed_ms)
                        await send_clear()
                        _active_item_id = None

                elif etype == "conversation.item.input_audio_transcription.completed":
                    transcript = (event.get("transcript") or "").strip()
                    if transcript:
                        print(f"[{log_label}] User said: {transcript}")
                        await asyncio.to_thread(save_message, conversation_id, user_id, "user", transcript)

                elif etype == "response.audio_transcript.done":
                    transcript = (event.get("transcript") or "").strip()
                    if transcript:
                        await asyncio.to_thread(save_message, conversation_id, user_id, "assistant", transcript)

                elif etype == "response.function_call_arguments.done":
                    call_id = event.get("call_id")
                    name = event.get("name")
                    try:
                        args = json.loads(event.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    print(f"[{log_label}] Realtime tool call: {name}({args})")
                    result = await _execute_tool(name, args)
                    # end_call already said its goodbye as the preceding text
                    # in this same model turn - asking the model to react to
                    # the tool result would just prompt a redundant (and
                    # possibly off-script) follow-up reply right as the call
                    # is ending, so skip create_response for it alone.
                    await session.send_tool_result(call_id, result, create_response=(name != "end_call"))

                elif etype == "response.done":
                    _active_item_id = None

                elif etype == "error":
                    print(f"[{log_label}] Realtime error: {event.get('error')}")
        except Exception as e:
            _failure_reason = str(e)[:500]

    drain_task = asyncio.create_task(_drain_realtime_events())

    greeting = greeting_override or (agent.get("first_message") if agent.get("first_message_enabled") else None)
    if greeting:
        await session.say(greeting)
        await asyncio.to_thread(save_message, conversation_id, user_id, "assistant", greeting)

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("event")
            # Only honor a pending end_call once the model isn't mid-item
            # (no in-flight audio item) - belt-and-suspenders alongside the
            # speech_started cancellation above, in case the grace period
            # elapsed in the same instant the caller started a new turn.
            if _end_call_requested and not _active_item_id:
                print(f"[{log_label}] Ending call - end_call tool requested hangup")
                break
            if event == "media":
                payload = parse_media_payload(data)
                if payload:
                    await session.append_audio_b64(payload)
            elif event == "stop":
                break
    except WebSocketDisconnect as e:
        if e.code not in (1000, 1001):
            _failure_reason = f"WebSocket closed with code {e.code}" + (f": {e.reason}" if e.reason else "")
    except Exception as e:
        if "disconnect" not in str(e).lower():
            _failure_reason = str(e)[:500]
    finally:
        if _end_call_task and not _end_call_task.done():
            _end_call_task.cancel()
        drain_task.cancel()
        await session.close()

    return _failure_reason


async def retrieve_phone_rag_context(
    user_message: str, agent: dict, agent_id: str, user_id: str,
    restrict_doc_ids: list[str] | None = None,
) -> tuple[str, bool]:
    """Fetch RAG context for a phone call turn: agent-level KB (document_chunks,
    scoped by agent_id) plus account-level KB docs (user_document_chunks),
    restricted to the documents this agent actually has selected.

    restrict_doc_ids, when given, further narrows the account-level KB
    lookup to exactly those ids instead of every selected+owned document -
    used by the Plivo/Twilio/Realtime pipelines' search_knowledge_base tool
    to scope a turn's search to the one document the model picked (via
    resolve_kb_search_doc_ids) rather than pooling every eligible document's
    chunks together. Does not affect the agent-level lookup above, which has
    no per-document identity to restrict by.

    Returns (full_rag_text, has_kb) - has_kb is True only if this agent has
    at least one KB document selected, so build_system_prompt can skip the
    "no relevant documents found" filler entirely for agents with no KB
    configured, instead of appending it as boilerplate on every single turn."""
    from app.agents.agent_rag_service import retrieve_agent_context, format_agent_context
    from app.kb.user_kb_rag import retrieve_user_kb_context, format_user_kb_context
    from app.kb.user_kb_repo import get_user_documents

    doc_ids = agent.get("selected_kb_doc_ids") or []
    rag_ctx = ""
    kb_ctx = ""
    if doc_ids:
        # Restrict to docs this agent has selected, intersected with docs this
        # account actually owns - previously this queried ALL of the account's
        # documents regardless of what was selected on this agent, making the
        # per-agent KB selector cosmetic.
        if restrict_doc_ids is not None:
            selected_owned_ids = restrict_doc_ids
        else:
            owned_ids = {d["id"] for d in await asyncio.to_thread(get_user_documents, user_id)}
            selected_owned_ids = [d for d in doc_ids if d in owned_ids]

        # Both lookups embed the same query and hit different tables - they
        # are independent, so run them concurrently instead of paying two
        # sequential embedding + pgvector round trips on a live call turn.
        agent_task = asyncio.to_thread(
            retrieve_agent_context, user_message, agent_id, user_id, ["personal", "global"]
        )
        if selected_owned_ids:
            kb_task = asyncio.to_thread(retrieve_user_kb_context, user_message, selected_owned_ids)
            raw, kb_rag = await asyncio.gather(agent_task, kb_task)
            kb_ctx = format_user_kb_context(kb_rag) if kb_rag else ""
        else:
            raw = await agent_task
        rag_ctx = format_agent_context(raw)

    full_rag = "\n\n".join(filter(None, [rag_ctx, kb_ctx]))
    return full_rag, bool(doc_ids)


async def finalize_call_outcome(conversation_id: str, failure_reason: str | None) -> tuple[str, list[dict]]:
    """
    Decides and records a telephony call's final Call Analytics outcome -
    called once, from the Twilio/Plivo WS lifecycle's finally block, for every
    call that reached this point (so excludes busy/no-answer/failed calls that
    never connected to the media stream at all - those are resolved earlier by
    the provider status-callback route instead).

    Distinguishes three outcomes instead of collapsing everything that
    connected into "completed":
      - "failed"      - a genuine pipeline/transport error occurred
      - "no_response" - the call connected but the other party never said a
                         word (picked up and immediately hung up, stayed
                         silent, voicemail that wasn't caught by AMD, etc.)
      - "completed"   - at least one "user"-role message was captured

    Reading back the message history is best-effort: if it fails (e.g. a
    transient DB hiccup), this fails open to "completed" rather than losing
    the call's outcome entirely - same fail-open posture as the rest of
    Call Analytics' writes.

    Returns (status, history) so the caller can reuse the already-fetched
    history for summary generation instead of fetching it twice.
    """
    from app.conversations.conversation_repo import set_call_status, set_call_duration_and_cost
    from app.conversations.message_repo import get_conversation_history

    history: list[dict] = []
    status = "failed" if failure_reason else "completed"
    if not failure_reason:
        try:
            history = await asyncio.to_thread(get_conversation_history, conversation_id)
            if not any(m.get("role") == "user" for m in history):
                status = "no_response"
        except Exception as e:
            print(f"[Call Analytics] Could not read back message history for {conversation_id}, "
                  f"defaulting to 'completed': {e}")

    await asyncio.to_thread(set_call_status, conversation_id, status, failure_reason)
    await asyncio.to_thread(set_call_duration_and_cost, conversation_id)
    return status, history
