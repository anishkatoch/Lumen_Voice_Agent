"""
Tests for the hr_interviews "calling" safety net (app/hr_campaigns/call_reconciliation.py).
Covers the real stuck rows found 2026-11-04: a call that genuinely connected
and talked (should resolve to completed), one that never connected and never
got a final provider callback (should resolve to failed), and one with no
Call Analytics record at all (should resolve to the dedicated "unknown"
status rather than being guessed either way).
"""
from datetime import datetime, timezone, timedelta

import pytest

INTERVIEW_ID = "c698a495-007f-4a1b-84d1-3118265faa24"
CALL_SID = "2a80e4bb-2cb2-4b90-8691-8ad44009d7bc"
CONVERSATION_ID = "5a022ca8-090a-43d0-9c54-8643074d2fec"


def _interview(**kw):
    base = {
        "id": INTERVIEW_ID,
        "call_sid": CALL_SID,
        "scheduled_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
        "status": "calling",
    }
    base.update(kw)
    return base


class _FakeTable:
    """Minimal stand-in for the chained supabase-py query builder, scripted
    per-table so each test controls exactly what each select() returns."""

    def __init__(self, data=None, count=None):
        self._data = data or []
        self._count = count

    def select(self, *a, **kw):
        return self

    def update(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def execute(self):
        return _Result(self._data, self._count)


class _Result:
    def __init__(self, data, count):
        self.data = data
        self.count = count


class _FakeDb:
    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return self._tables[name]


def test_reconcile_with_real_conversation_marks_completed(mocker):
    from app.hr_campaigns import call_reconciliation as cr

    fake_db = _FakeDb({
        "conversations": _FakeTable([{"id": CONVERSATION_ID, "call_status": "ringing"}]),
        "messages": _FakeTable([], count=3),
    })
    mocker.patch("app.core.database.get_supabase", return_value=fake_db)
    mock_mark = mocker.patch("app.hr_campaigns.hr_repo.mark_interview_status")
    mock_set_status = mocker.patch("app.conversations.conversation_repo.set_call_status")

    cr._reconcile_one(_interview())

    mock_mark.assert_called_once_with(INTERVIEW_ID, "completed")
    mock_set_status.assert_called_once_with(CONVERSATION_ID, "completed", None)


def test_reconcile_with_no_messages_marks_failed(mocker):
    from app.hr_campaigns import call_reconciliation as cr

    fake_db = _FakeDb({
        "conversations": _FakeTable([{"id": CONVERSATION_ID, "call_status": "ringing"}]),
        "messages": _FakeTable([], count=0),
    })
    mocker.patch("app.core.database.get_supabase", return_value=fake_db)
    mock_mark = mocker.patch("app.hr_campaigns.hr_repo.mark_interview_status")
    mock_set_status = mocker.patch("app.conversations.conversation_repo.set_call_status")

    cr._reconcile_one(_interview())

    mock_mark.assert_called_once_with(INTERVIEW_ID, "failed")
    args = mock_set_status.call_args.args
    assert args[0] == CONVERSATION_ID
    assert args[1] == "failed"
    assert args[2]


def test_reconcile_with_no_conversation_row_marks_unknown(mocker):
    from app.hr_campaigns import call_reconciliation as cr

    fake_db = _FakeDb({"conversations": _FakeTable([])})
    mocker.patch("app.core.database.get_supabase", return_value=fake_db)
    mock_mark = mocker.patch("app.hr_campaigns.hr_repo.mark_interview_status")
    mock_set_status = mocker.patch("app.conversations.conversation_repo.set_call_status")

    cr._reconcile_one(_interview())

    mock_mark.assert_called_once_with(INTERVIEW_ID, "unknown")
    mock_set_status.assert_not_called()


def test_reconcile_with_no_call_sid_marks_unknown(mocker):
    from app.hr_campaigns import call_reconciliation as cr

    mock_mark = mocker.patch("app.hr_campaigns.hr_repo.mark_interview_status")
    mock_set_status = mocker.patch("app.conversations.conversation_repo.set_call_status")

    cr._reconcile_one(_interview(call_sid=None))

    mock_mark.assert_called_once_with(INTERVIEW_ID, "unknown")
    mock_set_status.assert_not_called()


def test_reconcile_syncs_from_already_resolved_conversation(mocker):
    """Call Analytics already has a terminal status for this call_sid (the
    live pipeline resolved it) - hr_interviews just never heard about it."""
    from app.hr_campaigns import call_reconciliation as cr

    fake_db = _FakeDb({
        "conversations": _FakeTable([{"id": CONVERSATION_ID, "call_status": "completed"}]),
    })
    mocker.patch("app.core.database.get_supabase", return_value=fake_db)
    mock_mark = mocker.patch("app.hr_campaigns.hr_repo.mark_interview_status")
    mock_set_status = mocker.patch("app.conversations.conversation_repo.set_call_status")

    cr._reconcile_one(_interview())

    mock_mark.assert_called_once_with(INTERVIEW_ID, "completed")
    mock_set_status.assert_not_called()


def test_reconcile_syncs_no_response_distinct_from_failed(mocker):
    """A call that connected but captured no speech ("no_response") must sync
    to its own status, not collapse into "failed" - a real transport/pipeline
    error and "nobody said anything" are different outcomes for the HR team
    reviewing interview results."""
    from app.hr_campaigns import call_reconciliation as cr

    fake_db = _FakeDb({
        "conversations": _FakeTable([{"id": CONVERSATION_ID, "call_status": "no_response"}]),
    })
    mocker.patch("app.core.database.get_supabase", return_value=fake_db)
    mock_mark = mocker.patch("app.hr_campaigns.hr_repo.mark_interview_status")
    mock_set_status = mocker.patch("app.conversations.conversation_repo.set_call_status")

    cr._reconcile_one(_interview())

    mock_mark.assert_called_once_with(INTERVIEW_ID, "no_response")
    mock_set_status.assert_not_called()


CAMPAIGN_CANDIDATE_ID = "9e1e0e5a-3a9b-4e9b-9b1a-3a9b4e9b9b1a"
CAMPAIGN_ID = "c2a9b4e9-3a9b-4e9b-9b1a-3a9b4e9b9b1a"


def _campaign_candidate(**kw):
    base = {
        "id": CAMPAIGN_CANDIDATE_ID,
        "campaign_id": CAMPAIGN_ID,
        "call_sid": CALL_SID,
        "status": "calling",
        "last_attempt_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
    }
    base.update(kw)
    return base


def test_reconcile_campaign_candidate_syncs_no_response_as_completed(mocker):
    """Campaign candidates have no dedicated "no_response" status (unlike
    hr_interviews) - a "no_response" conversation outcome should sync to
    "completed", the same as the live-pipeline fallback does, not "failed"."""
    from app.hr_campaigns import call_reconciliation as cr

    fake_db = _FakeDb({
        "conversations": _FakeTable([{"id": CONVERSATION_ID, "call_status": "no_response"}]),
        "hr_campaign_candidates": _FakeTable([]),
    })
    mocker.patch("app.core.database.get_supabase", return_value=fake_db)
    mock_release = mocker.patch("app.hr_campaigns.hr_campaign_repo.release_campaign_claim")
    mocker.patch("app.hr_campaigns.hr_campaign_repo.maybe_complete_campaign")
    mock_set_status = mocker.patch("app.conversations.conversation_repo.set_call_status")

    cr._reconcile_campaign_candidate(_campaign_candidate())

    mock_release.assert_called_once_with(CAMPAIGN_ID, CAMPAIGN_CANDIDATE_ID)
    mock_set_status.assert_not_called()


def test_get_stale_calling_interviews_filters_by_grace_period(mocker):
    from app.hr_campaigns import call_reconciliation as cr

    recent = _interview(id="recent", scheduled_at=datetime.now(timezone.utc).isoformat())
    stale = _interview(id="stale", scheduled_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    fake_db = _FakeDb({"hr_interviews": _FakeTable([recent, stale])})
    mocker.patch("app.core.database.get_supabase", return_value=fake_db)

    result = cr._get_stale_calling_interviews(grace_minutes=20)

    assert [r["id"] for r in result] == ["stale"]
