"""
Unit tests for app/telephony/shared.py - pure logic shared by the Twilio and
Plivo call pipelines, tested directly rather than through the full WS/VAD
pipeline where that's impractical (see validate_question_answer's docstring).
"""
from app.telephony.shared import validate_question_answer

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
