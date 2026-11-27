"""
Mirrors tests/test_end_call_tool.py for the /plivo/stream endpoint - a
lighter smoke check (not the full set) that the same end_call wiring added
to app/routers/telephony_plivo.py actually works through Plivo's own message
shapes (playAudio/clearAudio events, query-string call context, "callId"
instead of a Twilio "callSid"), not just Twilio's.
"""
import json
import time

import pytest
from starlette.testclient import TestClient

pytestmark = pytest.mark.asyncio

TEST_AGENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TEST_USER_ID = "11111111-1111-1111-1111-111111111111"
TEST_PHONE = "+14155550100"


def _agent(**kw):
    return {
        "id": TEST_AGENT_ID, "user_id": TEST_USER_ID, "name": "Test Agent",
        "system_prompt": "You are a helpful assistant.",
        "first_message_enabled": True, "first_message": "Hi there, how can I help?",
        "barge_in_sensitivity": 0.2, "selected_kb_doc_ids": [],
        "kb_instructions": None, "hr_instructions": None,
        **kw,
    }


def _phone_record(**kw):
    return {"agent_id": TEST_AGENT_ID, "user_id": TEST_USER_ID, **kw}


class _FakeStreamingTranscriber:
    def __init__(self, transcript="hello there"):
        self.transcript = transcript

    async def start(self):
        pass

    async def send(self, pcm_bytes: bytes):
        pass

    async def finish(self):
        return self.transcript

    def abort(self):
        pass

    def get_last_partial(self):
        return self.transcript


def _media_event(payload_bytes: bytes = None) -> str:
    import base64
    payload_bytes = payload_bytes or (bytes([0x00]) * 160)
    return json.dumps({"event": "media", "media": {"payload": base64.b64encode(payload_bytes).decode()}})


def _wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _fake_detect_speech_factory(speech_chunks: int = 5):
    n = {"count": 0}

    async def fake_detect_speech(audio_bytes, sensitivity=0.5):
        n["count"] += 1
        return n["count"] <= speech_chunks
    return fake_detect_speech


@pytest.fixture
def mock_pipeline(mocker):
    mocker.patch("app.conversations.message_repo.get_session_messages", return_value=[])
    mocker.patch("app.agents.agent_repo.get_agent_functions", return_value=[])
    mocker.patch("app.kb.user_kb_repo.get_user_documents", return_value=[])
    mocker.patch("app.conversations.memory_repo.get_user_memory", return_value=None)
    mocker.patch("app.telephony.plivo.plivo_repo.get_number_by_phone", return_value=_phone_record())
    mocker.patch("app.agents.agent_repo.get_agent", return_value=_agent())
    mocker.patch("app.conversations.conversation_repo.set_call_status")
    mocker.patch("app.conversations.conversation_repo.set_call_duration_and_cost")
    mocker.patch("app.conversations.message_repo.save_message", return_value={"id": "msg-id"})

    conv_db = mocker.MagicMock()
    conv_db.table.return_value.insert.return_value.execute.return_value.data = [{"id": "conv-id"}]
    mocker.patch("app.core.database.get_supabase", return_value=conv_db)

    async def fake_text_to_speech_plivo(text):
        yield bytes([0xFF]) * 80

    mocker.patch("app.telephony.plivo.plivo_call_service.text_to_speech_plivo", side_effect=fake_text_to_speech_plivo)


class TestPlivoEndCallOfferedByDefault:
    def test_present_in_tools_passed_to_the_model_by_default(self, mocker, mock_pipeline):
        transcriber = _FakeStreamingTranscriber(transcript="hello")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=_fake_detect_speech_factory())

        seen_tools = {}

        async def capture_tools(conv_messages, system, tools):
            seen_tools["tools"] = tools
            yield ("text", "Hi, how can I help?")

        mocker.patch("app.voice.llm_service.detect_tool_call_stream", side_effect=capture_tools)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect(f"/plivo/stream?to={TEST_PHONE}") as ws:
            ws.send_text(json.dumps({"event": "start", "start": {}}))
            for _ in range(40):
                ws.send_text(_media_event())
            _wait_until(lambda: "tools" in seen_tools)
            ws.send_text(json.dumps({"event": "stop"}))

        assert "tools" in seen_tools, "detect_tool_call_stream was never called"
        names = {t["function"]["name"] for t in seen_tools["tools"]}
        assert "end_call" in names, f"expected end_call to be offered by default, got {names}"


class TestPlivoEndCallEndsTheCallGracefully:
    def test_goodbye_then_end_call_closes_the_call_as_completed(self, mocker, mock_pipeline):
        mocker.patch("app.telephony.shared.END_CALL_GRACE_SECONDS", 0.3)

        transcriber = _FakeStreamingTranscriber(transcript="that's all, goodbye")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=_fake_detect_speech_factory())

        tts_calls = []

        async def recording_tts(text):
            tts_calls.append(text)
            yield bytes([0xFF]) * 80
        mocker.patch("app.telephony.plivo.plivo_call_service.text_to_speech_plivo", side_effect=recording_tts)

        async def fake_detect_tool_call_stream(conv_messages, system, tools):
            yield ("text", "Thanks for calling, take care!")
            yield ("tool", ("end_call", {"reason": "caller said goodbye"}, "call_1"))

        mocker.patch("app.voice.llm_service.detect_tool_call_stream", side_effect=fake_detect_tool_call_stream)
        mock_stream_tool_result = mocker.patch("app.voice.llm_service.stream_tool_result_response")

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect(f"/plivo/stream?to={TEST_PHONE}") as ws:
            ws.send_text(json.dumps({"event": "start", "start": {}}))
            for _ in range(40):
                ws.send_text(_media_event())
            _wait_until(lambda: "Thanks for calling, take care!" in tts_calls)
            for _ in range(40):
                ws.send_text(_media_event())
                time.sleep(0.02)

        mock_stream_tool_result.assert_not_called()

        from app.conversations.conversation_repo import set_call_status
        _wait_until(lambda: set_call_status.called)
        args = set_call_status.call_args.args
        assert args[1] == "completed", f"expected end_call to end the call as 'completed', got {args}"
        assert args[2] is None, f"expected no failure_reason from an end_call hangup, got {args}"
