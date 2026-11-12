"""
Tests for standalone HR interview call retry (app/hr_campaigns/hr_repo.py).

Before this, a single no-answer/failure permanently failed an hr_interviews
row - there was no attempt_count/next_attempt_at and no retry budget at all,
unlike hr_campaign_candidates which already had one. These cover the new
requeue/exhaust decision (_retry_or_finalize) and its two config knobs
(call_retry_max_attempts/call_retry_interval_minutes), which are admin-portal
settings (app/admin/admin_config_service.py DEFAULTS) rather than hardcoded.
"""
from datetime import datetime, timezone, timedelta

from unittest.mock import MagicMock

INTERVIEW_ID = "c698a495-007f-4a1b-84d1-3118265faa24"
CALL_SID = "2a80e4bb-2cb2-4b90-8691-8ad44009d7bc"


def _interview(**kw):
    base = {
        "id": INTERVIEW_ID,
        "scheduled_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "call_lead_minutes": 30,
        "status": "pending",
        "attempt_count": 0,
        "next_attempt_at": None,
    }
    base.update(kw)
    return base


def _patch_admin_config(mocker, max_attempts="5", interval_minutes="3"):
    def _get(key):
        return {"call_retry_max_attempts": max_attempts, "call_retry_interval_minutes": interval_minutes}[key]
    return mocker.patch("app.admin.admin_config_service.get", side_effect=_get)


# ── get_due_interviews: retry rows ──────────────────────────────────────────

def test_get_due_interviews_includes_elapsed_retry(mocker):
    from app.hr_campaigns import hr_repo

    due_retry = _interview(
        id="due", status="pending",
        next_attempt_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        # scheduled_at is ancient - would fail the first-attempt staleness
        # window on its own, proving the retry branch skips that check.
        scheduled_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    )
    not_yet_due = _interview(
        id="not_due", status="pending",
        next_attempt_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [due_retry, not_yet_due]
    mock_db.table.return_value.select.return_value.in_.return_value.execute.return_value.data = []
    mocker.patch("app.hr_campaigns.hr_repo.get_supabase", return_value=mock_db)

    result = hr_repo.get_due_interviews()

    assert [r["id"] for r in result] == ["due"]


def test_get_due_interviews_first_attempt_window_unaffected(mocker):
    """A row that never retried (next_attempt_at is null) still uses the
    original scheduled_at/call_lead_minutes window, unchanged."""
    from app.hr_campaigns import hr_repo

    due_first = _interview(
        id="due_first",
        scheduled_at=(datetime.now(timezone.utc) + timedelta(minutes=25)).isoformat(),
        call_lead_minutes=30,
    )
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [due_first]
    mocker.patch("app.hr_campaigns.hr_repo.get_supabase", return_value=mock_db)

    result = hr_repo.get_due_interviews()

    assert [r["id"] for r in result] == ["due_first"]


# ── mark_interview_failed / mark_interview_no_response ──────────────────────

def test_mark_interview_failed_requeues_under_budget(mocker):
    from app.hr_campaigns import hr_repo

    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"attempt_count": 2}
    ]
    _patch_admin_config(mocker, max_attempts="5", interval_minutes="3")
    mocker.patch("app.hr_campaigns.hr_repo.get_supabase", return_value=mock_db)

    hr_repo.mark_interview_failed(INTERVIEW_ID)

    update_call = mock_db.table.return_value.update.call_args
    payload = update_call.args[0]
    assert payload["status"] == "pending"
    next_at = datetime.fromisoformat(payload["next_attempt_at"])
    assert next_at > datetime.now(timezone.utc) + timedelta(minutes=2)


def test_mark_interview_failed_finalizes_when_exhausted(mocker):
    from app.hr_campaigns import hr_repo

    mock_db = MagicMock()
    # attempt_count=5, call_retry_max_attempts=5 -> max_retries_after_first=4 -> 5 > 4 -> exhausted
    mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"attempt_count": 5}
    ]
    _patch_admin_config(mocker, max_attempts="5", interval_minutes="3")
    mocker.patch("app.hr_campaigns.hr_repo.get_supabase", return_value=mock_db)

    hr_repo.mark_interview_failed(INTERVIEW_ID)

    payload = mock_db.table.return_value.update.call_args.args[0]
    assert payload == {"status": "failed"}


def test_mark_interview_failed_respects_overridden_admin_attempts(mocker):
    """Proves the budget is actually read from admin config, not hardcoded -
    with max_attempts=2, attempt_count=2 is already exhausted."""
    from app.hr_campaigns import hr_repo

    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"attempt_count": 2}
    ]
    _patch_admin_config(mocker, max_attempts="2", interval_minutes="3")
    mocker.patch("app.hr_campaigns.hr_repo.get_supabase", return_value=mock_db)

    hr_repo.mark_interview_failed(INTERVIEW_ID)

    payload = mock_db.table.return_value.update.call_args.args[0]
    assert payload == {"status": "failed"}


def test_mark_interview_no_response_requeues_when_still_completed(mocker):
    from app.hr_campaigns import hr_repo

    mock_db = MagicMock()
    claim_exec = mock_db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute
    claim_exec.return_value.data = [{"id": INTERVIEW_ID, "attempt_count": 1}]
    _patch_admin_config(mocker, max_attempts="5", interval_minutes="3")
    mocker.patch("app.hr_campaigns.hr_repo.get_supabase", return_value=mock_db)

    hr_repo.mark_interview_no_response(INTERVIEW_ID)

    # First update() call is the conditional "completed"->"no_response" claim,
    # second is _retry_or_finalize's requeue write.
    calls = mock_db.table.return_value.update.call_args_list
    assert calls[0].args[0] == {"status": "no_response"}
    assert calls[1].args[0]["status"] == "pending"
    assert "next_attempt_at" in calls[1].args[0]


def test_mark_interview_no_response_noop_when_not_completed(mocker):
    """Guarded on status='completed' - if another writer already resolved
    this interview, mark_interview_no_response must not touch it at all."""
    from app.hr_campaigns import hr_repo

    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    mocker.patch("app.hr_campaigns.hr_repo.get_supabase", return_value=mock_db)

    hr_repo.mark_interview_no_response(INTERVIEW_ID)

    assert mock_db.table.return_value.update.call_count == 1


# ── mark_interview_status_by_call_sid: busy/no_answer/failed are retry-aware ─

def test_mark_interview_status_by_call_sid_requeues_no_answer(mocker):
    from app.hr_campaigns import hr_repo

    mock_db = MagicMock()
    claim_exec = mock_db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute
    claim_exec.return_value.data = [{"id": INTERVIEW_ID, "attempt_count": 1, "call_sid": CALL_SID}]
    _patch_admin_config(mocker, max_attempts="5", interval_minutes="3")
    mocker.patch("app.hr_campaigns.hr_repo.get_supabase", return_value=mock_db)

    row = hr_repo.mark_interview_status_by_call_sid(CALL_SID, "no_answer")

    assert row is not None
    calls = mock_db.table.return_value.update.call_args_list
    assert calls[0].args[0] == {"status": "no_answer"}
    assert calls[1].args[0]["status"] == "pending"


# ── update_interview: rescheduling resets the retry budget ─────────────────

def test_update_interview_rescheduling_resets_attempt_count(mocker):
    """An exhausted/failed interview that gets rescheduled (scheduled_at
    changed) must start its retry budget over, not inherit a stale
    attempt_count that could exhaust it again on the very next failure."""
    from app.hr_campaigns import hr_repo

    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": INTERVIEW_ID}
    ]
    mocker.patch("app.hr_campaigns.hr_repo.get_supabase", return_value=mock_db)

    hr_repo.update_interview(INTERVIEW_ID, "user-1", scheduled_at="2026-12-01T10:00:00+00:00", status="pending")

    payload = mock_db.table.return_value.update.call_args.args[0]
    assert payload["attempt_count"] == 0
    assert payload["next_attempt_at"] is None


def test_mark_interview_status_by_call_sid_completed_is_not_retried(mocker):
    from app.hr_campaigns import hr_repo

    mock_db = MagicMock()
    claim_exec = mock_db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute
    claim_exec.return_value.data = [{"id": INTERVIEW_ID, "attempt_count": 1, "call_sid": CALL_SID}]
    mocker.patch("app.hr_campaigns.hr_repo.get_supabase", return_value=mock_db)

    hr_repo.mark_interview_status_by_call_sid(CALL_SID, "completed")

    # Only the one write - no retry decision made for a successful outcome.
    assert mock_db.table.return_value.update.call_count == 1
