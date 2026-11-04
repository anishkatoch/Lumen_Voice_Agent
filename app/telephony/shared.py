"""Shared logic and constants used by both the Twilio and Plivo telephony
pipelines. Pulled out here so that a fix to call-context resolution, prompt
assembly, or KB retrieval lands once instead of needing to be hand-copied
into both app/routers/telephony_plivo.py and app/routers/telephony_twilio.py."""
import asyncio

# Phone calls can't tolerate a long silent wait on a webhook/tool call - while
# one is in flight, is_processing stays True and the caller's mic is only
# checked against the (intentionally strict) barge-in VAD threshold, not
# normal listening, so a slow/unreachable webhook makes the call look dead.
# Hard-cap regardless of the per-function timeout_ms an admin configured.


PHONE_WEBHOOK_MAX_TIMEOUT_S = 15

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
        questions_raw = interview.get("specific_questions", "") or ""
        # One question per line - each non-empty line is tracked separately
        # (answered/not answered + the actual answer) via the record_answer
        # tool below, same mechanism campaign calls already use.
        ctx.questions = [
            {"id": f"q{i+1}", "text": line.strip()}
            for i, line in enumerate(questions_raw.splitlines())
            if line.strip()
        ]
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
            + "\n\nStart by greeting them by name and confirming you're calling from HR."
            + "\nMention the interview time when greeting them. Ask the specific questions listed. Be professional and friendly."
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
        questions_list = ctx.campaign_data.get("questions") or []
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
            + "\n\nStart by greeting them by name and confirming you're calling regarding their application."
            + "\nBe professional and friendly. Ask one question at a time and wait for their answer."
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
    sessions - phone calls were missing that entirely until this was added."""
    hr = (agent.get("hr_instructions") or "").strip()
    name = (agent.get("name") or "").strip()
    return (
        (f"Your name is {name}. If asked your name, say {name}.\n\n" if name else "")
        + (agent.get("system_prompt") or "")
        + (f"\n\n--- HR INSTRUCTIONS ---\n{hr}" if hr else "")
        + extra_system_context
    )


async def retrieve_phone_rag_context(user_message: str, agent: dict, agent_id: str, user_id: str) -> tuple[str, bool]:
    """Fetch RAG context for a phone call turn: agent-level KB (document_chunks,
    scoped by agent_id) plus account-level KB docs (user_document_chunks),
    restricted to the documents this agent actually has selected.

    Returns (full_rag_text, has_kb) - has_kb is True only if this agent has
    at least one KB document selected, so build_system_prompt can skip the
    "no relevant documents found" filler entirely for agents with no KB
    configured, instead of appending it as boilerplate on every single turn."""
    from app.agents.agent_rag_service import retrieve_agent_context, format_agent_context
    from app.kb.user_kb_rag import retrieve_user_kb_context, format_user_kb_context
    from app.kb.user_kb_repo import get_user_documents

    doc_ids = agent.get("selected_kb_doc_ids") or []
    rag_ctx = ""
    if doc_ids:
        raw = await asyncio.to_thread(
            retrieve_agent_context, user_message, agent_id, user_id, ["personal", "global"]
        )
        rag_ctx = format_agent_context(raw)

    # Restrict to docs this agent has selected, intersected with docs this
    # account actually owns - previously this queried ALL of the account's
    # documents regardless of what was selected on this agent, making the
    # per-agent KB selector cosmetic.
    kb_ctx = ""
    if doc_ids:
        owned_ids = {d["id"] for d in await asyncio.to_thread(get_user_documents, user_id)}
        selected_owned_ids = [d for d in doc_ids if d in owned_ids]
        if selected_owned_ids:
            kb_rag = await asyncio.to_thread(retrieve_user_kb_context, user_message, selected_owned_ids)
            kb_ctx = format_user_kb_context(kb_rag) if kb_rag else ""

    full_rag = "\n\n".join(filter(None, [rag_ctx, kb_ctx]))
    return full_rag, bool(doc_ids)
