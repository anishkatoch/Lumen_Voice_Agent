"""
Tests for the HR call-campaign feature (sequential outbound dial queue with
retry, distinct from the single-scheduled-time hr_interviews flow).

Scenarios covered:
  - Campaign CRUD via HTTP API
  - Adding/removing candidates from a campaign's queue
  - Twilio status-callback route (busy/no-answer/failed/completed handling)
  - Conditional-update repo logic (idempotent against a race with the WS lifecycle)
"""
import pytest
from unittest.mock import MagicMock

pytestmark = pytest.mark.asyncio

from tests.conftest import TEST_USER_ID, make_token

TEST_AGENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TEST_CAMPAIGN_ID = "cccccccc-1111-1111-1111-cccccccccccc"
TEST_CAND_ID = "dddddddd-1111-1111-1111-dddddddddddd"  # hr_candidates.id
TEST_CAMPAIGN_CAND_ID = "eeeeeeee-1111-1111-1111-eeeeeeeeeeee"  # hr_campaign_candidates.id
TEST_CALL_SID = "CAtestcampaign000000000000000000000000"


def _auth(user_id=TEST_USER_ID):
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def _campaign(**kw):
    return {
        "id": TEST_CAMPAIGN_ID, "user_id": TEST_USER_ID, "agent_id": TEST_AGENT_ID,
        "name": "Q3 Screening", "questions": [{"id": "q1", "text": "Why this role?"}],
        "status": "draft", "active_candidate_id": None, "last_call_ended_at": None,
        "max_retry_attempts": 2, "retry_delay_minutes": 10, "call_gap_minutes": 4,
        "created_at": "2026-07-17T00:00:00+00:00",
        **kw,
    }


def _campaign_candidate(**kw):
    return {
        "id": TEST_CAMPAIGN_CAND_ID, "campaign_id": TEST_CAMPAIGN_ID, "candidate_id": TEST_CAND_ID,
        "status": "queued", "attempt_count": 0, "last_attempt_at": None, "call_sid": None,
        "created_at": "2026-07-17T00:00:00+00:00",
        **kw,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 - Campaign CRUD
# ══════════════════════════════════════════════════════════════════════════════

class TestCampaignCRUD:
    async def test_list_campaigns(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.list_campaigns", return_value=[_campaign()])
        resp = await client.get("/api/hr/campaigns", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()[0]["name"] == "Q3 Screening"

    async def test_create_campaign(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.create_campaign", return_value=_campaign())
        resp = await client.post(
            "/api/hr/campaigns", headers=_auth(),
            json={"agent_id": TEST_AGENT_ID, "name": "Q3 Screening",
                  "questions": [{"id": "q1", "text": "Why this role?"}]},
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "draft"

    async def test_create_campaign_requires_auth(self, client):
        resp = await client.post("/api/hr/campaigns", json={"agent_id": "x", "name": "x", "questions": []})
        assert resp.status_code == 403

    async def test_update_campaign_status(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.update_campaign", return_value=_campaign(status="running"))
        resp = await client.put(
            f"/api/hr/campaigns/{TEST_CAMPAIGN_ID}", headers=_auth(), json={"status": "running"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    async def test_update_campaign_not_found(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.update_campaign", side_effect=RuntimeError("Campaign not found"))
        resp = await client.put(f"/api/hr/campaigns/{TEST_CAMPAIGN_ID}", headers=_auth(), json={"status": "running"})
        assert resp.status_code == 404

    async def test_delete_campaign(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.delete_campaign", return_value=None)
        resp = await client.delete(f"/api/hr/campaigns/{TEST_CAMPAIGN_ID}", headers=_auth())
        assert resp.status_code == 204


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 - Campaign candidate queue
# ══════════════════════════════════════════════════════════════════════════════

class TestCampaignCandidates:
    async def test_list_campaign_candidates(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.get_campaign", return_value=_campaign())
        mocker.patch("app.hr_campaign_repo.list_campaign_candidates", return_value=[_campaign_candidate()])
        resp = await client.get(f"/api/hr/campaigns/{TEST_CAMPAIGN_ID}/candidates", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()[0]["status"] == "queued"

    async def test_list_candidates_404_for_unknown_campaign(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.get_campaign", return_value=None)
        resp = await client.get(f"/api/hr/campaigns/{TEST_CAMPAIGN_ID}/candidates", headers=_auth())
        assert resp.status_code == 404

    async def test_add_candidates_to_campaign(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.get_campaign", return_value=_campaign())
        mocker.patch("app.hr_campaign_repo.add_candidates_to_campaign", return_value=[_campaign_candidate()])
        resp = await client.post(
            f"/api/hr/campaigns/{TEST_CAMPAIGN_ID}/candidates", headers=_auth(),
            json={"candidate_ids": [TEST_CAND_ID]},
        )
        assert resp.status_code == 201
        assert len(resp.json()) == 1

    async def test_remove_campaign_candidate(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.get_campaign", return_value=_campaign())
        mock_remove = mocker.patch("app.hr_campaign_repo.remove_campaign_candidate", return_value=None)
        resp = await client.delete(
            f"/api/hr/campaigns/{TEST_CAMPAIGN_ID}/candidates/{TEST_CAMPAIGN_CAND_ID}", headers=_auth()
        )
        assert resp.status_code == 204
        mock_remove.assert_called_once_with(TEST_CAMPAIGN_CAND_ID, TEST_CAMPAIGN_ID)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 - Twilio status-callback route
# ══════════════════════════════════════════════════════════════════════════════

class TestStatusCallbackRoute:
    """/twilio/campaign/status-callback - no auth (Twilio-facing webhook, matches /twilio/voice convention)."""

    @pytest.mark.parametrize("twilio_status,expected_internal_status", [
        ("busy", "busy"),
        ("no-answer", "no_answer"),
        ("failed", "failed"),
        ("canceled", "failed"),
        ("completed", "completed"),
    ])
    async def test_terminal_statuses_update_the_row(self, client, mocker, twilio_status, expected_internal_status):
        mock_mark = mocker.patch("app.hr_campaign_repo.mark_candidate_status_from_call_sid", return_value=_campaign_candidate())
        resp = await client.post(
            "/twilio/campaign/status-callback",
            data={"CallSid": TEST_CALL_SID, "CallStatus": twilio_status},
        )
        assert resp.status_code == 204
        mock_mark.assert_called_once_with(TEST_CALL_SID, expected_internal_status)

    @pytest.mark.parametrize("twilio_status", ["queued", "ringing", "in-progress", "initiated"])
    async def test_non_terminal_statuses_are_ignored(self, client, mocker, twilio_status):
        mock_mark = mocker.patch("app.hr_campaign_repo.mark_candidate_status_from_call_sid")
        resp = await client.post(
            "/twilio/campaign/status-callback",
            data={"CallSid": TEST_CALL_SID, "CallStatus": twilio_status},
        )
        assert resp.status_code == 204
        mock_mark.assert_not_called()

    async def test_missing_call_sid_does_not_crash(self, client, mocker):
        mock_mark = mocker.patch("app.hr_campaign_repo.mark_candidate_status_from_call_sid")
        resp = await client.post("/twilio/campaign/status-callback", data={"CallStatus": "busy"})
        assert resp.status_code == 204
        mock_mark.assert_not_called()

    async def test_no_auth_required(self, client, mocker):
        """Matches the /twilio/voice convention - Twilio can't send a JWT."""
        mocker.patch("app.hr_campaign_repo.mark_candidate_status_from_call_sid", return_value=None)
        resp = await client.post(
            "/twilio/campaign/status-callback", data={"CallSid": TEST_CALL_SID, "CallStatus": "busy"}
        )
        assert resp.status_code == 204


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 - Campaign outbound TwiML + synchronous AMD (Stage 3)
# ══════════════════════════════════════════════════════════════════════════════

def _campaign_candidate_with_campaign(**kw):
    row = _campaign_candidate(**kw)
    row["hr_campaigns"] = _campaign()
    row["hr_candidates"] = {"name": "Priya Sharma", "phone": "+919876543210", "role": "Engineer"}
    return row


class TestCampaignOutboundTwiML:
    """/twilio/campaign/outbound/voice - mirrors TestTwiMLRoutes for /twilio/outbound/voice."""

    async def test_human_answer_returns_stream_twiml(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.get_campaign_candidate", return_value=_campaign_candidate_with_campaign())
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value={"webhook_base_url": "https://example.ngrok.io"})

        resp = await client.get(
            f"/twilio/campaign/outbound/voice?campaign_candidate_id={TEST_CAMPAIGN_CAND_ID}&AnsweredBy=human"
        )
        assert resp.status_code == 200
        assert "<Connect>" in resp.text
        assert "<Stream" in resp.text
        assert "wss://" in resp.text
        assert TEST_CAMPAIGN_CAND_ID in resp.text
        assert "<Hangup" not in resp.text

    @pytest.mark.parametrize("answered_by", ["machine_start", "machine_end_beep", "machine_end_silence", "machine_end_other", "fax"])
    async def test_machine_or_fax_hangs_up_without_connecting_stream(self, client, mocker, answered_by):
        mocker.patch("app.hr_campaign_repo.get_campaign_candidate", return_value=_campaign_candidate_with_campaign())
        mock_marked = mocker.patch("app.hr_campaign_repo.mark_candidate_machine_detected",
                                    return_value=_campaign_candidate(status="no_answer"))
        settings_mock = mocker.patch("app.user_twilio_settings_repo.get_twilio_settings")

        resp = await client.get(
            f"/twilio/campaign/outbound/voice?campaign_candidate_id={TEST_CAMPAIGN_CAND_ID}&AnsweredBy={answered_by}"
        )
        assert resp.status_code == 200
        assert "<Hangup" in resp.text
        assert "<Connect>" not in resp.text
        mock_marked.assert_called_once_with(TEST_CAMPAIGN_CAND_ID)
        # Machine/fax path never needs webhook_base_url - the pipeline never starts
        settings_mock.assert_not_called()

    async def test_unknown_campaign_candidate_returns_error_twiml(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.get_campaign_candidate", return_value=None)
        resp = await client.get("/twilio/campaign/outbound/voice?campaign_candidate_id=nonexistent&AnsweredBy=human")
        assert resp.status_code == 200
        assert "<Say>" in resp.text
        assert "<Connect>" not in resp.text

    async def test_missing_campaign_candidate_id_returns_error_twiml(self, client):
        resp = await client.get("/twilio/campaign/outbound/voice")
        assert resp.status_code == 200
        assert "<Connect>" not in resp.text

    async def test_missing_webhook_base_url_returns_error_twiml(self, client, mocker):
        mocker.patch("app.hr_campaign_repo.get_campaign_candidate", return_value=_campaign_candidate_with_campaign())
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings", return_value={"webhook_base_url": ""})

        resp = await client.get(
            f"/twilio/campaign/outbound/voice?campaign_candidate_id={TEST_CAMPAIGN_CAND_ID}&AnsweredBy=human"
        )
        assert resp.status_code == 200
        assert "<Connect>" not in resp.text

    async def test_stream_url_carries_campaign_candidate_id_as_parameter_not_query_string(self, client, mocker):
        """
        Regression guard: Twilio does not reliably forward URL query strings on
        <Stream url="...">, confirmed this session for the interview_id/to flow.
        Campaign context must travel via <Parameter>, not appended to the url itself.
        """
        mocker.patch("app.hr_campaign_repo.get_campaign_candidate", return_value=_campaign_candidate_with_campaign())
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value={"webhook_base_url": "https://example.ngrok.io"})

        resp = await client.get(
            f"/twilio/campaign/outbound/voice?campaign_candidate_id={TEST_CAMPAIGN_CAND_ID}&AnsweredBy=human"
        )
        assert f'<Stream url="wss://example.ngrok.io/twilio/stream">' in resp.text
        assert f'<Parameter name="campaign_candidate_id" value="{TEST_CAMPAIGN_CAND_ID}" />' in resp.text


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 - Conditional-update repo logic (idempotency / race safety)
# ══════════════════════════════════════════════════════════════════════════════

class TestMarkCandidateStatusFromCallSid:
    """
    Unit-tests the conditional UPDATE ... WHERE status='calling' logic directly
    against a mocked Supabase client, mirroring TestSchedulerDeduplication's
    _make_claim_db pattern in tests/test_twilio_hr.py.
    """

    def _make_db(self, mocker, *, update_returns):
        db = mocker.MagicMock()
        chain = mocker.MagicMock()
        chain.eq.return_value = chain
        chain.execute.return_value.data = update_returns
        db.table.return_value.update.return_value = chain
        return db

    def test_updates_row_when_status_is_still_calling(self, mocker):
        row = _campaign_candidate(status="busy", call_sid=TEST_CALL_SID)
        db = self._make_db(mocker, update_returns=[row])
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)
        mock_release = mocker.patch("app.hr_campaign_repo.release_campaign_claim")

        from app.hr_campaign_repo import mark_candidate_status_from_call_sid
        result = mark_candidate_status_from_call_sid(TEST_CALL_SID, "busy")

        assert result == row
        mock_release.assert_called_once_with(row["campaign_id"], row["id"])

    def test_returns_none_and_does_not_release_when_already_resolved(self, mocker):
        """
        Simulates the race with the WS lifecycle (Stage 6): if that path already
        flipped the row to 'completed', the conditional UPDATE matches zero rows
        - this callback must be a safe no-op, not clobber the resolved state.
        """
        db = self._make_db(mocker, update_returns=[])  # no rows matched WHERE status='calling'
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)
        mock_release = mocker.patch("app.hr_campaign_repo.release_campaign_claim")

        from app.hr_campaign_repo import mark_candidate_status_from_call_sid
        result = mark_candidate_status_from_call_sid(TEST_CALL_SID, "busy")

        assert result is None
        mock_release.assert_not_called()


class TestMarkCandidateMachineDetected:
    def _make_db(self, mocker, *, update_returns):
        db = mocker.MagicMock()
        chain = mocker.MagicMock()
        chain.eq.return_value = chain
        chain.execute.return_value.data = update_returns
        db.table.return_value.update.return_value = chain
        return db

    def test_sets_no_answer_and_releases_claim(self, mocker):
        row = _campaign_candidate(status="no_answer")
        db = self._make_db(mocker, update_returns=[row])
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)
        mock_release = mocker.patch("app.hr_campaign_repo.release_campaign_claim")

        from app.hr_campaign_repo import mark_candidate_machine_detected
        result = mark_candidate_machine_detected(TEST_CAMPAIGN_CAND_ID)

        assert result["status"] == "no_answer"
        mock_release.assert_called_once_with(row["campaign_id"], row["id"])

    def test_noop_when_already_resolved(self, mocker):
        db = self._make_db(mocker, update_returns=[])
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)
        mock_release = mocker.patch("app.hr_campaign_repo.release_campaign_claim")

        from app.hr_campaign_repo import mark_candidate_machine_detected
        result = mark_candidate_machine_detected(TEST_CAMPAIGN_CAND_ID)

        assert result is None
        mock_release.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 - Dialer pacing (app/hr_campaign_service.py::_gap_elapsed)
# ══════════════════════════════════════════════════════════════════════════════

from datetime import datetime, timedelta, timezone


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


class TestGapElapsed:
    """
    Regression coverage for the pacing-anchor bug found during planning:
    the gap must be measured from call-END time (last_call_ended_at), not
    dial-start time - anchoring on dial time would read as already-expired
    for any call that actually runs a few minutes.
    """

    def test_never_called_anyone_yet_is_always_elapsed(self):
        from app.hr_campaign_service import _gap_elapsed
        assert _gap_elapsed({"last_call_ended_at": None, "call_gap_minutes": 4}) is True

    def test_gap_not_yet_elapsed(self):
        from app.hr_campaign_service import _gap_elapsed
        campaign = {"last_call_ended_at": _iso(minutes_ago=1), "call_gap_minutes": 4}
        assert _gap_elapsed(campaign) is False

    def test_gap_elapsed(self):
        from app.hr_campaign_service import _gap_elapsed
        campaign = {"last_call_ended_at": _iso(minutes_ago=5), "call_gap_minutes": 4}
        assert _gap_elapsed(campaign) is True

    def test_gap_exactly_at_boundary_counts_as_elapsed(self):
        from app.hr_campaign_service import _gap_elapsed
        campaign = {"last_call_ended_at": _iso(minutes_ago=4.01), "call_gap_minutes": 4}
        assert _gap_elapsed(campaign) is True

    def test_malformed_timestamp_fails_open(self):
        """A parse failure must not permanently wedge the campaign - treat as elapsed."""
        from app.hr_campaign_service import _gap_elapsed
        assert _gap_elapsed({"last_call_ended_at": "not-a-date", "call_gap_minutes": 4}) is True


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 - Dialer repo functions (claim, retry sweep, completion)
# ══════════════════════════════════════════════════════════════════════════════

class TestClaimCampaignForDial:
    def test_claim_succeeds_when_unclaimed(self, mocker):
        db = mocker.MagicMock()
        chain = mocker.MagicMock()
        chain.eq.return_value = chain
        chain.is_.return_value = chain
        chain.execute.return_value.data = [_campaign(active_candidate_id=TEST_CAMPAIGN_CAND_ID)]
        db.table.return_value.update.return_value = chain
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)

        from app.hr_campaign_repo import claim_campaign_for_dial
        assert claim_campaign_for_dial(TEST_CAMPAIGN_ID, TEST_CAMPAIGN_CAND_ID) is True
        chain.is_.assert_called_once_with("active_candidate_id", None)

    def test_claim_fails_when_already_claimed(self, mocker):
        db = mocker.MagicMock()
        chain = mocker.MagicMock()
        chain.eq.return_value = chain
        chain.is_.return_value = chain
        chain.execute.return_value.data = []  # WHERE active_candidate_id IS NULL matched nothing
        db.table.return_value.update.return_value = chain
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)

        from app.hr_campaign_repo import claim_campaign_for_dial
        assert claim_campaign_for_dial(TEST_CAMPAIGN_ID, TEST_CAMPAIGN_CAND_ID) is False


class TestMarkCalling:
    def test_increments_attempt_count_from_current_value(self, mocker):
        db = mocker.MagicMock()
        select_chain = mocker.MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.limit.return_value = select_chain
        select_chain.execute.return_value.data = [{"attempt_count": 1}]

        update_chain = mocker.MagicMock()
        update_chain.eq.return_value = update_chain

        # Memoize per table name - db.table("x") must return the SAME mock every
        # call, or a later assertion re-calling db.table("x") gets a fresh mock
        # with no recorded call history from what actually ran.
        _tables: dict = {}
        def table_router(name):
            if name not in _tables:
                t = mocker.MagicMock()
                t.select.return_value = select_chain
                t.update.return_value = update_chain
                _tables[name] = t
            return _tables[name]
        db.table.side_effect = table_router
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)

        from app.hr_campaign_repo import mark_calling
        mark_calling(TEST_CAMPAIGN_CAND_ID, TEST_CALL_SID)

        update_call_kwargs = db.table("hr_campaign_candidates").update.call_args[0][0]
        assert update_call_kwargs["attempt_count"] == 2
        assert update_call_kwargs["status"] == "calling"
        assert update_call_kwargs["call_sid"] == TEST_CALL_SID

    def test_first_ever_attempt_starts_at_one(self, mocker):
        db = mocker.MagicMock()
        select_chain = mocker.MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.limit.return_value = select_chain
        select_chain.execute.return_value.data = []  # row not found - defensive default

        update_chain = mocker.MagicMock()
        update_chain.eq.return_value = update_chain

        # Memoize per table name - db.table("x") must return the SAME mock every
        # call, or a later assertion re-calling db.table("x") gets a fresh mock
        # with no recorded call history from what actually ran.
        _tables: dict = {}
        def table_router(name):
            if name not in _tables:
                t = mocker.MagicMock()
                t.select.return_value = select_chain
                t.update.return_value = update_chain
                _tables[name] = t
            return _tables[name]
        db.table.side_effect = table_router
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)

        from app.hr_campaign_repo import mark_calling
        mark_calling(TEST_CAMPAIGN_CAND_ID, TEST_CALL_SID)

        update_call_kwargs = db.table("hr_campaign_candidates").update.call_args[0][0]
        assert update_call_kwargs["attempt_count"] == 1


class TestRequeueEligibleRetries:
    def _make_db(self, mocker, rows):
        db = mocker.MagicMock()
        select_chain = mocker.MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.in_.return_value = select_chain
        select_chain.execute.return_value.data = rows
        update_chain = mocker.MagicMock()
        update_chain.eq.return_value = update_chain

        # Memoize per table name - db.table("x") must return the SAME mock every
        # call, or a later assertion re-calling db.table("x") gets a fresh mock
        # with no recorded call history from what actually ran.
        _tables: dict = {}
        def table_router(name):
            if name not in _tables:
                t = mocker.MagicMock()
                t.select.return_value = select_chain
                t.update.return_value = update_chain
                _tables[name] = t
            return _tables[name]
        db.table.side_effect = table_router
        return db, update_chain

    def test_promotes_candidate_past_retry_delay(self, mocker):
        row = _campaign_candidate(status="busy", attempt_count=1, last_attempt_at=_iso(minutes_ago=15))
        db, update_chain = self._make_db(mocker, [row])
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)

        from app.hr_campaign_repo import requeue_eligible_retries
        requeue_eligible_retries(TEST_CAMPAIGN_ID, retry_delay_minutes=10, max_retry_attempts=2)

        db.table("hr_campaign_candidates").update.assert_any_call({"status": "queued"})

    def test_does_not_promote_before_retry_delay(self, mocker):
        row = _campaign_candidate(status="busy", attempt_count=1, last_attempt_at=_iso(minutes_ago=2))
        db, update_chain = self._make_db(mocker, [row])
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)

        from app.hr_campaign_repo import requeue_eligible_retries
        requeue_eligible_retries(TEST_CAMPAIGN_ID, retry_delay_minutes=10, max_retry_attempts=2)

        db.table.assert_any_call("hr_campaign_candidates")
        assert mocker.call({"status": "queued"}) not in db.table("hr_campaign_candidates").update.call_args_list

    def test_exhausts_candidate_past_max_retries(self, mocker):
        row = _campaign_candidate(status="no_answer", attempt_count=3, last_attempt_at=_iso(minutes_ago=15))
        db, update_chain = self._make_db(mocker, [row])
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)

        from app.hr_campaign_repo import requeue_eligible_retries
        requeue_eligible_retries(TEST_CAMPAIGN_ID, retry_delay_minutes=10, max_retry_attempts=2)

        db.table("hr_campaign_candidates").update.assert_any_call({"status": "exhausted"})


class TestMaybeCompleteCampaign:
    def test_marks_completed_when_all_terminal(self, mocker):
        db = mocker.MagicMock()
        select_chain = mocker.MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.execute.return_value.data = [{"status": "completed"}, {"status": "exhausted"}, {"status": "failed"}]
        update_chain = mocker.MagicMock()
        update_chain.eq.return_value = update_chain

        # Memoize per table name - db.table("x") must return the SAME mock every
        # call, or a later assertion re-calling db.table("x") gets a fresh mock
        # with no recorded call history from what actually ran.
        _tables: dict = {}
        def table_router(name):
            if name not in _tables:
                t = mocker.MagicMock()
                t.select.return_value = select_chain
                t.update.return_value = update_chain
                _tables[name] = t
            return _tables[name]
        db.table.side_effect = table_router
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)

        from app.hr_campaign_repo import maybe_complete_campaign
        maybe_complete_campaign(TEST_CAMPAIGN_ID)

        db.table("hr_campaigns").update.assert_called_once_with({"status": "completed"})

    def test_does_not_complete_while_candidates_still_active(self, mocker):
        db = mocker.MagicMock()
        select_chain = mocker.MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.execute.return_value.data = [{"status": "completed"}, {"status": "queued"}]

        def table_router(name):
            t = mocker.MagicMock()
            t.select.return_value = select_chain
            return t
        db.table.side_effect = table_router
        mocker.patch("app.hr_campaign_repo.get_supabase", return_value=db)

        from app.hr_campaign_repo import maybe_complete_campaign
        maybe_complete_campaign(TEST_CAMPAIGN_ID)

        db.table.assert_called_once_with("hr_campaign_candidates")  # never touched hr_campaigns


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 8 - Dialer orchestration (app/hr_campaign_service.py)
# ══════════════════════════════════════════════════════════════════════════════

class TestDialCandidate:
    """_dial_candidate - mocking every collaborator to isolate the orchestration logic."""

    async def test_successful_dial_marks_calling(self, mocker):
        mocker.patch("app.hr_campaign_repo.claim_campaign_for_dial", return_value=True)
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings", return_value={
            "webhook_base_url": "https://example.ngrok.io", "account_sid": "ACfake", "auth_token": "faketoken",
        })
        mocker.patch("app.twilio_repo.get_from_number", return_value="+15550001111")
        mocker.patch("app.hr_campaign_service._place_twilio_call", new=mocker.AsyncMock(return_value=(TEST_CALL_SID, None)))
        mock_mark_calling = mocker.patch("app.hr_campaign_repo.mark_calling")
        mock_mark_failed = mocker.patch("app.hr_campaign_repo.mark_candidate_failed_and_release")
        mock_create_conv = mocker.patch("app.conversation_repo.create_conversation")

        from app.hr_campaign_service import _dial_candidate
        await _dial_candidate(_campaign(), _campaign_candidate_with_campaign())

        mock_mark_calling.assert_called_once_with(TEST_CAMPAIGN_CAND_ID, TEST_CALL_SID)
        mock_mark_failed.assert_not_called()
        mock_create_conv.assert_called_once()
        assert mock_create_conv.call_args.kwargs["call_sid"] == TEST_CALL_SID
        assert mock_create_conv.call_args.kwargs["call_status"] == "ringing"

    async def test_claim_race_lost_does_nothing(self, mocker):
        mocker.patch("app.hr_campaign_repo.claim_campaign_for_dial", return_value=False)
        mock_settings = mocker.patch("app.user_twilio_settings_repo.get_twilio_settings")

        from app.hr_campaign_service import _dial_candidate
        await _dial_candidate(_campaign(), _campaign_candidate_with_campaign())

        mock_settings.assert_not_called()  # never got further than the lost claim

    async def test_missing_credentials_marks_failed_not_busy(self, mocker):
        mocker.patch("app.hr_campaign_repo.claim_campaign_for_dial", return_value=True)
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings", return_value={
            "webhook_base_url": "", "account_sid": "", "auth_token": "",
        })
        mock_mark_failed = mocker.patch("app.hr_campaign_repo.mark_candidate_failed_and_release")
        mocker.patch("app.conversation_repo.record_failed_call")

        from app.hr_campaign_service import _dial_candidate
        await _dial_candidate(_campaign(), _campaign_candidate_with_campaign())

        mock_mark_failed.assert_called_once_with(TEST_CAMPAIGN_ID, TEST_CAMPAIGN_CAND_ID)

    async def test_missing_from_number_marks_failed(self, mocker):
        mocker.patch("app.hr_campaign_repo.claim_campaign_for_dial", return_value=True)
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings", return_value={
            "webhook_base_url": "https://example.ngrok.io", "account_sid": "ACfake", "auth_token": "faketoken",
        })
        mocker.patch("app.twilio_repo.get_from_number", return_value=None)
        mock_mark_failed = mocker.patch("app.hr_campaign_repo.mark_candidate_failed_and_release")
        mocker.patch("app.conversation_repo.record_failed_call")

        from app.hr_campaign_service import _dial_candidate
        await _dial_candidate(_campaign(), _campaign_candidate_with_campaign())

        mock_mark_failed.assert_called_once_with(TEST_CAMPAIGN_ID, TEST_CAMPAIGN_CAND_ID)

    async def test_twilio_api_failure_marks_failed(self, mocker):
        mocker.patch("app.hr_campaign_repo.claim_campaign_for_dial", return_value=True)
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings", return_value={
            "webhook_base_url": "https://example.ngrok.io", "account_sid": "ACfake", "auth_token": "faketoken",
        })
        mocker.patch("app.twilio_repo.get_from_number", return_value="+15550001111")
        mocker.patch("app.hr_campaign_service._place_twilio_call",
                     new=mocker.AsyncMock(return_value=(None, "Twilio rejected the call - check the phone number and account status")))
        mock_mark_failed = mocker.patch("app.hr_campaign_repo.mark_candidate_failed_and_release")
        mock_record_failed = mocker.patch("app.conversation_repo.record_failed_call")

        from app.hr_campaign_service import _dial_candidate
        await _dial_candidate(_campaign(), _campaign_candidate_with_campaign())

        mock_mark_failed.assert_called_once_with(TEST_CAMPAIGN_ID, TEST_CAMPAIGN_CAND_ID)
        mock_record_failed.assert_called_once()
        assert "rejected" in mock_record_failed.call_args.args[-1]

    async def test_sync_amd_enabled_on_the_twilio_call(self, mocker):
        """MachineDetection must be set - this is what makes Stage 3's AnsweredBy query param arrive."""
        mocker.patch("app.hr_campaign_repo.claim_campaign_for_dial", return_value=True)
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings", return_value={
            "webhook_base_url": "https://example.ngrok.io", "account_sid": "ACfake", "auth_token": "faketoken",
        })
        mocker.patch("app.twilio_repo.get_from_number", return_value="+15550001111")
        mocker.patch("app.hr_campaign_repo.mark_calling")
        mocker.patch("app.conversation_repo.create_conversation")

        captured = {}
        async def fake_post(url, auth, data):
            captured.update(data)
            class R:
                status_code = 201
                def json(self): return {"sid": TEST_CALL_SID}
            return R()

        mock_client = mocker.MagicMock()
        mock_client.__aenter__ = mocker.AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = mocker.AsyncMock(return_value=False)
        mock_client.post = fake_post
        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        from app.hr_campaign_service import _dial_candidate
        await _dial_candidate(_campaign(), _campaign_candidate_with_campaign())

        assert captured.get("MachineDetection") == "Enable"
        assert "StatusCallback" in captured


class TestCheckAndDial:
    """_check_and_dial - high-level orchestration, mocking all repo calls."""

    async def test_skips_campaign_with_call_already_in_flight(self, mocker):
        mocker.patch("app.hr_campaign_repo.list_running_campaigns",
                      return_value=[_campaign(active_candidate_id=TEST_CAMPAIGN_CAND_ID)])
        mock_next = mocker.patch("app.hr_campaign_repo.get_next_queued_candidate")

        from app.hr_campaign_service import _check_and_dial
        await _check_and_dial()

        mock_next.assert_not_called()

    async def test_skips_campaign_when_gap_not_elapsed(self, mocker):
        mocker.patch("app.hr_campaign_repo.list_running_campaigns",
                      return_value=[_campaign(active_candidate_id=None, last_call_ended_at=_iso(minutes_ago=1))])
        mock_next = mocker.patch("app.hr_campaign_repo.get_next_queued_candidate")

        from app.hr_campaign_service import _check_and_dial
        await _check_and_dial()

        mock_next.assert_not_called()

    async def test_dials_when_candidate_queued_and_gap_elapsed(self, mocker):
        mocker.patch("app.hr_campaign_repo.list_running_campaigns",
                      return_value=[_campaign(active_candidate_id=None, last_call_ended_at=None)])
        mocker.patch("app.hr_campaign_repo.get_next_queued_candidate", return_value=_campaign_candidate_with_campaign())
        mock_dial = mocker.patch("app.hr_campaign_service._dial_candidate", new=mocker.AsyncMock())

        from app.hr_campaign_service import _check_and_dial
        await _check_and_dial()

        mock_dial.assert_called_once()

    async def test_triggers_retry_sweep_when_nothing_queued(self, mocker):
        mocker.patch("app.hr_campaign_repo.list_running_campaigns",
                      return_value=[_campaign(active_candidate_id=None, last_call_ended_at=None)])
        # First call (before sweep) finds nothing; sweep runs; second call still finds nothing.
        mocker.patch("app.hr_campaign_repo.get_next_queued_candidate", side_effect=[None, None])
        mock_requeue = mocker.patch("app.hr_campaign_repo.requeue_eligible_retries")
        mocker.patch("app.hr_campaign_repo.maybe_complete_campaign")

        from app.hr_campaign_service import _check_and_dial
        await _check_and_dial()

        mock_requeue.assert_called_once()

    async def test_checks_completion_when_nothing_dialable(self, mocker):
        mocker.patch("app.hr_campaign_repo.list_running_campaigns",
                      return_value=[_campaign(active_candidate_id=None, last_call_ended_at=None)])
        mocker.patch("app.hr_campaign_repo.get_next_queued_candidate", side_effect=[None, None])
        mocker.patch("app.hr_campaign_repo.requeue_eligible_retries")
        mock_complete = mocker.patch("app.hr_campaign_repo.maybe_complete_campaign")

        from app.hr_campaign_service import _check_and_dial
        await _check_and_dial()

        mock_complete.assert_called_once_with(TEST_CAMPAIGN_ID)

    async def test_no_running_campaigns_returns_early(self, mocker):
        mocker.patch("app.hr_campaign_repo.list_running_campaigns", return_value=[])
        mock_next = mocker.patch("app.hr_campaign_repo.get_next_queued_candidate")

        from app.hr_campaign_service import _check_and_dial
        await _check_and_dial()

        mock_next.assert_not_called()
