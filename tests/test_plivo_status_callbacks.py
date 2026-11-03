"""
Regression test for a real production bug: _PLIVO_CALL_STATUS_MAP was
missing a "completed" entry that Twilio's equivalent map has as an explicit
safety net ("safety net if the WS 'stop' event never fired"). Without it, a
Plivo call that ends without the /plivo/stream websocket itself updating the
row (e.g. it never connects to the media stream) left hr_interviews.status
and conversations.call_status stuck on "calling"/"ringing" forever, since
Plivo's final status-callback (CallStatus=completed) mapped to None and the
callback silently did nothing.
"""
import pytest

pytestmark = pytest.mark.asyncio

CALL_UUID = "2a80e4bb-2cb2-4b90-8691-8ad44009d7bc"


class _FakeForm(dict):
    pass


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def form(self):
        return _FakeForm(self._payload)


async def test_completed_status_resolves_stuck_interview(mocker):
    from app.routers.telephony_plivo import plivo_interview_status_callback

    mock_mark_interview = mocker.patch(
        "app.hr_campaigns.hr_repo.mark_interview_status_by_call_sid", return_value=None
    )
    mock_set_status = mocker.patch(
        "app.conversations.conversation_repo.set_call_status_by_sid_if_unconnected", return_value=None
    )

    request = _FakeRequest({"CallUUID": CALL_UUID, "CallStatus": "completed"})
    await plivo_interview_status_callback(request)

    mock_mark_interview.assert_called_once_with(CALL_UUID, "completed")
    mock_set_status.assert_called_once()
    args = mock_set_status.call_args.args
    assert args[0] == CALL_UUID
    assert args[1] == "completed"


async def test_unmapped_status_does_nothing(mocker):
    """"ringing"/"in-progress" etc. are deliberately not in the map - they're
    intermediate states, not a final outcome, so the row must be left alone."""
    from app.routers.telephony_plivo import plivo_interview_status_callback

    mock_mark_interview = mocker.patch("app.hr_campaigns.hr_repo.mark_interview_status_by_call_sid")
    mock_set_status = mocker.patch("app.conversations.conversation_repo.set_call_status_by_sid_if_unconnected")

    request = _FakeRequest({"CallUUID": CALL_UUID, "CallStatus": "ringing"})
    await plivo_interview_status_callback(request)

    mock_mark_interview.assert_not_called()
    mock_set_status.assert_not_called()
