"""
Unit test for the end_call tool's handling inside
app.telephony.shared.run_realtime_telephony_bridge (the OpenAI Realtime API
pipeline) - separate from tests/test_end_call_tool.py, which covers the
cascade (Deepgram/LLM/TTS) pipeline's handling of the same tool in
app/routers/telephony_twilio.py.

Drives run_realtime_telephony_bridge directly with a fake RealtimeSession and
a fake websocket, instead of going through a real OpenAI connection - this
is the one dependency real_telephony tests can't fake through a websocket
alone, since RealtimeSession is constructed internally by the function.
"""
import asyncio
import json

import pytest

pytestmark = pytest.mark.asyncio


class _FakeRealtimeSession:
    """Stands in for app.voice.realtime_service.RealtimeSession. events() is
    fed by a queue the test pushes scripted Realtime API events into, so the
    test controls exactly when the model "calls" end_call."""

    def __init__(self, *a, **kw):
        self.closed = False
        self.sent_results: list[tuple] = []
        self.bytes_per_ms = 8.0
        self._queue: asyncio.Queue = asyncio.Queue()

    def push(self, event: dict):
        self._queue.put_nowait(event)

    async def connect(self):
        pass

    async def say(self, text):
        pass

    async def append_audio_b64(self, payload):
        pass

    async def cancel_response(self):
        pass

    async def truncate(self, item_id, audio_end_ms):
        pass

    async def create_response(self):
        pass

    async def send_tool_result(self, call_id, output, create_response=True):
        self.sent_results.append((call_id, output, create_response))

    async def events(self):
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self):
        self.closed = True


class _FakeRealtimeWebSocket:
    """Delivers a steady stream of 'media' events (as Twilio/Plivo would,
    continuously, silence included) every few ms, forever - the test caps
    how long it waits via asyncio.wait_for rather than this ever ending on
    its own, matching how a real live call's media never just stops."""

    def __init__(self):
        self.receive_count = 0

    async def receive_text(self):
        self.receive_count += 1
        await asyncio.sleep(0.01)
        return json.dumps({"event": "media", "media": {"payload": "AA=="}})


async def _noop_async(*a, **kw):
    pass


def _agent():
    return {"first_message_enabled": False, "system_prompt": "You are a helpful assistant.", "name": "Test Agent"}


class TestRealtimeEndCall:
    async def test_end_call_tool_ends_the_call_without_a_followup_response(self, mocker):
        from app.telephony import shared

        mocker.patch.object(shared, "END_CALL_GRACE_SECONDS", 0.2)
        mocker.patch("app.admin.admin_config_service.get", return_value="marin")
        mocker.patch("app.conversations.message_repo.save_message", return_value={"id": "msg-id"})

        fake_session = _FakeRealtimeSession()
        mocker.patch("app.voice.realtime_service.RealtimeSession", return_value=fake_session)

        # The model says its goodbye (an audio_transcript.done event, not
        # audio itself - irrelevant to this test) then calls end_call, in
        # the same turn, exactly as the tool's description asks for.
        fake_session.push({
            "type": "response.function_call_arguments.done",
            "call_id": "call_1", "name": "end_call",
            "arguments": json.dumps({"reason": "caller said goodbye"}),
        })

        ws = _FakeRealtimeWebSocket()

        result = await asyncio.wait_for(
            shared.run_realtime_telephony_bridge(
                ws, log_label="Test", agent=_agent(), agent_id="agent-1", user_id="user-1",
                conversation_id="conv-1", extra_system_context="", greeting_override=None,
                call_questions=[], interview_id="", campaign_candidate_id="",
                tool_functions=[], parse_media_payload=lambda d: (d.get("media") or {}).get("payload"),
                send_audio_frame=_noop_async, send_clear=_noop_async, kb_documents=[],
            ),
            timeout=3.0,
        )

        assert result is None, f"expected a clean end (no failure reason), got {result!r}"
        assert fake_session.closed, "expected the Realtime session to be closed"
        assert fake_session.sent_results == [("call_1", json.dumps({"ending": True}), False)], (
            f"expected exactly one tool result for end_call with create_response=False "
            f"(no redundant follow-up turn), got {fake_session.sent_results}"
        )

    async def test_caller_speaking_again_cancels_the_pending_hangup(self, mocker):
        from app.telephony import shared

        mocker.patch.object(shared, "END_CALL_GRACE_SECONDS", 0.2)
        mocker.patch("app.admin.admin_config_service.get", return_value="marin")
        mocker.patch("app.conversations.message_repo.save_message", return_value={"id": "msg-id"})

        fake_session = _FakeRealtimeSession()
        mocker.patch("app.voice.realtime_service.RealtimeSession", return_value=fake_session)

        fake_session.push({
            "type": "response.function_call_arguments.done",
            "call_id": "call_1", "name": "end_call",
            "arguments": json.dumps({"reason": "caller said goodbye"}),
        })
        # The caller is heard speaking again well inside the 0.2s grace
        # window - this must cancel the pending hangup outright.
        fake_session.push({"type": "input_audio_buffer.speech_started"})

        ws = _FakeRealtimeWebSocket()

        with pytest.raises(asyncio.TimeoutError):
            # Nothing ever tells the bridge to stop in this test (no "stop"
            # event, no disconnect) - if the hangup wasn't actually
            # cancelled, it would return well within this window instead of
            # genuinely timing out.
            await asyncio.wait_for(
                shared.run_realtime_telephony_bridge(
                    ws, log_label="Test", agent=_agent(), agent_id="agent-1", user_id="user-1",
                    conversation_id="conv-1", extra_system_context="", greeting_override=None,
                    call_questions=[], interview_id="", campaign_candidate_id="",
                    tool_functions=[], parse_media_payload=lambda d: (d.get("media") or {}).get("payload"),
                    send_audio_frame=_noop_async, send_clear=_noop_async, kb_documents=[],
                ),
                timeout=0.6,
            )

        assert ws.receive_count > 20, (
            f"expected the call to keep running well past the grace period, "
            f"got only {ws.receive_count} receive_text() calls"
        )
