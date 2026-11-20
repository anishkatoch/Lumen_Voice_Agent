"""
Unit tests for app/telephony/shared.py - pure logic shared by the Twilio and
Plivo call pipelines, tested directly rather than through the full WS/VAD
pipeline where that's impractical (see validate_question_answer's docstring).
"""
from app.telephony.shared import resolve_call_context, validate_question_answer

_QUESTIONS = [{"id": "q1", "text": "Why this role?"}, {"id": "q2", "text": "Notice period?"}]


def test_known_unanswered_question_id_is_accepted():
    assert validate_question_answer("q1", _QUESTIONS, answered_question_ids=set()) is None


def test_unknown_question_id_is_rejected():
    error = validate_question_answer("not-a-real-id", _QUESTIONS, answered_question_ids=set())
    assert error is not None
    assert "not-a-real-id" in error


def test_already_answered_question_id_is_rejected():
    """The bug found live on 2026-11-04: the model answered q2 but called
    record_answer with q1 again (already answered), silently overwriting it
    instead of recording the real q2 answer."""
    error = validate_question_answer("q1", _QUESTIONS, answered_question_ids={"q1"})
    assert error is not None
    assert "q1" in error
    assert "already" in error.lower()


def test_different_question_id_is_accepted_even_if_another_was_already_answered():
    assert validate_question_answer("q2", _QUESTIONS, answered_question_ids={"q1"}) is None


# -- resolve_call_context: inbound routing is agent-name/type agnostic --------
#
# An inbound call (no interview_id, no campaign_candidate_id) is resolved
# purely from the dialed number's own agent_id - resolve_call_context never
# reads the agent's name or type. These tests pin that down explicitly so a
# future change can't quietly special-case "Meeting Assistant" (the only
# built-in agent template with inbound calling exercised so far) and leave a
# person's own custom agent unable to receive calls the same way.

async def test_inbound_call_routes_to_whatever_agent_a_custom_number_is_attached_to():
    def get_number_by_phone(phone: str):
        assert phone == "+15551234567"
        return {"agent_id": "custom-agent-1", "user_id": "user-1"}

    ctx = await resolve_call_context(
        interview_id="", campaign_candidate_id="", to="+15551234567",
        get_number_by_phone=get_number_by_phone, log_label="Test",
    )
    assert ctx is not None
    assert ctx.agent_id == "custom-agent-1"
    assert ctx.user_id == "user-1"
    # Inbound calls get no synthesized context/greeting override - the
    # agent's own first_message and system_prompt drive the call as-is,
    # identical to how a built-in template agent is handled.
    assert ctx.extra_system_context == ""
    assert ctx.greeting_override is None
    assert ctx.questions == []


async def test_inbound_call_routing_is_identical_for_a_built_in_template_agent():
    """Same lookup, same shape of result - whether the attached agent_id
    happens to belong to the seeded "Meeting Assistant" template or a
    person's own custom agent makes no difference to this function."""
    def get_number_by_phone(phone: str):
        return {"agent_id": "meeting-assistant-id", "user_id": "user-1"}

    ctx = await resolve_call_context(
        interview_id="", campaign_candidate_id="", to="+15559876543",
        get_number_by_phone=get_number_by_phone, log_label="Test",
    )
    assert ctx is not None
    assert ctx.agent_id == "meeting-assistant-id"


async def test_inbound_call_closes_when_dialed_number_has_no_number_record():
    ctx = await resolve_call_context(
        interview_id="", campaign_candidate_id="", to="+15550000000",
        get_number_by_phone=lambda phone: None, log_label="Test",
    )
    assert ctx is None


async def test_inbound_call_closes_when_number_record_has_no_agent_attached():
    ctx = await resolve_call_context(
        interview_id="", campaign_candidate_id="", to="+15550000000",
        get_number_by_phone=lambda phone: {"agent_id": None, "user_id": "user-1"},
        log_label="Test",
    )
    assert ctx is None
