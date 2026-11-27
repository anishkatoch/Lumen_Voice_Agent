"""
Tests for the built-in end_call tool (app.telephony.shared.build_end_call_tool)
offered on every phone call by default, and the hangup mechanics around it in
app/routers/telephony_twilio.py's twilio_stream_endpoint.

The whole feature exists to let the model hang up a call itself once the
conversation has genuinely finished - and the one thing it must never do is
cut a call off mid-conversation. These tests cover:
  - the tool is offered by default, and the admin kill switch actually
    removes it (falling back to the pre-existing no-tools code path)
  - calling it ends the call (after its grace period) without a redundant
    extra LLM round-trip
  - the caller speaking again before the grace period elapses cancels the
    pending hangup outright, instead of the call ending underneath them
"""
import asyncio
import base64
import json
import time

import pytest
from starlette.testclient import TestClient

pytestmark = pytest.mark.asyncio

TEST_AGENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TEST_USER_ID = "11111111-1111-1111-1111-111111111111"
TEST_PHONE = "+14155550100"
STREAM_SID = "MZtest0000000000000000000000000000"


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
    return {
        "id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "user_id": TEST_USER_ID,
        "phone_number": TEST_PHONE, "agent_id": TEST_AGENT_ID, "is_active": True,
        **kw,
    }


class _FakeStreamingTranscriber:
    """Records send() calls; finish() returns whatever transcript is current -
    a mutable holder (not a fixed string) so one test can simulate two
    separate utterances in the same call."""
    def __init__(self, transcript="hello there"):
        self.transcript = transcript
        self.sent_chunks = []

    async def start(self):
        pass

    async def send(self, pcm_bytes: bytes):
        self.sent_chunks.append(pcm_bytes)

    async def finish(self):
        return self.transcript

    def abort(self):
        pass

    def get_last_partial(self):
        return self.transcript


def _mulaw_media_event(payload_bytes: bytes = None) -> str:
    payload_bytes = payload_bytes or (bytes([0x00]) * 160)
    return json.dumps({
        "event": "media",
        "streamSid": STREAM_SID,
        "media": {"payload": base64.b64encode(payload_bytes).decode()},
    })


def _wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def mock_pipeline(mocker):
    mocker.patch("app.conversations.message_repo.get_session_messages", return_value=[])
    mocker.patch("app.agents.agent_repo.get_agent_functions", return_value=[])
    mocker.patch("app.kb.user_kb_repo.get_user_documents", return_value=[])
    mocker.patch("app.conversations.memory_repo.get_user_memory", return_value=None)
    mocker.patch("app.telephony.twilio.twilio_repo.get_number_by_phone", return_value=_phone_record())
    mocker.patch("app.agents.agent_repo.get_agent", return_value=_agent())
    mocker.patch("app.conversations.conversation_repo.set_call_status")
    mocker.patch("app.conversations.conversation_repo.set_call_duration_and_cost")

    save_message_calls = []

    def fake_save_message(*args, **kwargs):
        save_message_calls.append((args, kwargs))
        return {"id": "msg-id"}

    mocker.patch("app.conversations.message_repo.save_message", side_effect=fake_save_message)

    conv_db = mocker.MagicMock()
    conv_db.table.return_value.insert.return_value.execute.return_value.data = [{"id": "conv-id"}]
    mocker.patch("app.core.database.get_supabase", return_value=conv_db)

    async def fake_text_to_speech_twilio(text):
        yield bytes([0xFF]) * 80

    mocker.patch("app.telephony.twilio.twilio_call_service.text_to_speech_twilio", side_effect=fake_text_to_speech_twilio)

    return {"save_message_calls": save_message_calls}


def _fake_detect_speech_factory(speech_chunks: int = 5):
    """First `speech_chunks` reports speech, then silence forever after -
    enough to finalize exactly one utterance with no barge-in false alarms."""
    n = {"count": 0}

    async def fake_detect_speech(audio_bytes, sensitivity=0.5):
        n["count"] += 1
        return n["count"] <= speech_chunks
    return fake_detect_speech


def _make_resettable_vad(speech_chunks: int = 5):
    """Like _fake_detect_speech_factory, but reset()-able for tests that
    simulate more than one caller utterance in the same call. Patching
    app.voice.vad_service.detect_speech again mid-test would NOT work -
    twilio_stream_endpoint does `from app.voice.vad_service import
    detect_speech` once, at the top of its own function body, binding that
    one reference for the rest of the call - a later mocker.patch() call
    only replaces the attribute on the module, which that already-running
    endpoint never looks at again. Returning the same async function object
    for the whole test and mutating its closed-over counter sidesteps that
    entirely. A plain async function (not a callable class instance) matters
    here too - mock's AsyncMock auto-detection needs a real coroutine
    function to await side_effect's result correctly."""
    state = {"count": 0}

    async def fake_detect_speech(audio_bytes, sensitivity=0.5):
        state["count"] += 1
        return state["count"] <= speech_chunks

    fake_detect_speech.reset = lambda: state.update(count=0)
    return fake_detect_speech


class TestBuildEndCallTool:
    def test_shape_matches_other_synthetic_tools(self):
        from app.telephony.shared import build_end_call_tool
        tool = build_end_call_tool()
        assert tool["name"] == "end_call"
        assert tool["kind"] == "internal_end_call"
        assert isinstance(tool.get("description"), str) and len(tool["description"]) > 20
        assert any(p["name"] == "reason" for p in tool["parameters"])


class TestEndCallOfferedByDefault:
    """The tool must be present on every call by default, unless the admin
    kill switch or the agent's own end_call_tool_enabled column turns it off
    (either one is enough to remove it - see the two tests below)."""

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
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            _wait_until(lambda: "tools" in seen_tools)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        assert "tools" in seen_tools, "detect_tool_call_stream was never called"
        names = {t["function"]["name"] for t in seen_tools["tools"]}
        assert "end_call" in names, f"expected end_call to be offered by default, got {names}"

    def test_admin_kill_switch_removes_it_and_falls_back_to_plain_path(self, mocker, mock_pipeline):
        """With end_call_tool_enabled=false and no other tools configured,
        _tool_functions must end up empty - proving the tool was actually
        left out, not just hidden - so run_pipeline takes the pre-existing
        no-tools generate_response_stream path instead of the tool-aware one."""
        transcriber = _FakeStreamingTranscriber(transcript="hello")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=_fake_detect_speech_factory())

        def fake_admin_get(key):
            if key == "end_call_tool_enabled":
                return "false"
            return "true"  # turn_heuristic_enabled / filler_phrases_enabled, and non-"realtime" voice_pipeline
        mocker.patch("app.admin.admin_config_service.get", side_effect=fake_admin_get)

        detect_tool_call_calls = []

        async def fake_detect_tool_call_stream(conv_messages, system, tools):
            detect_tool_call_calls.append(tools)
            yield ("text", "should not be reached")

        mocker.patch("app.voice.llm_service.detect_tool_call_stream", side_effect=fake_detect_tool_call_stream)

        plain_path_called = {"yes": False}

        async def fake_generate_response_stream(
            user_message, session_history, rag_context, long_term_memory,
            system_prompt_override=None, kb_instructions=None,
        ):
            plain_path_called["yes"] = True
            yield "Hi, how can I help?"

        mocker.patch("app.voice.llm_service.generate_response_stream", side_effect=fake_generate_response_stream)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            _wait_until(lambda: plain_path_called["yes"])
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        assert plain_path_called["yes"], "expected the no-tools plain path to run when the tool is disabled"
        assert not detect_tool_call_calls, (
            f"detect_tool_call_stream should never be called with the tool disabled and no other "
            f"tools configured, got {detect_tool_call_calls}"
        )

    def test_per_agent_toggle_removes_it_even_with_admin_switch_on(self, mocker, mock_pipeline):
        """end_call_tool_enabled=False on the agent row itself must remove the
        tool just like the admin kill switch does, even though the admin
        switch stays on - the agent's own "Custom functions" toggle in the
        UI has to actually take effect, not just the global one."""
        mocker.patch("app.agents.agent_repo.get_agent", return_value=_agent(end_call_tool_enabled=False))

        transcriber = _FakeStreamingTranscriber(transcript="hello")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=_fake_detect_speech_factory())
        mocker.patch("app.admin.admin_config_service.get", return_value="true")

        detect_tool_call_calls = []

        async def fake_detect_tool_call_stream(conv_messages, system, tools):
            detect_tool_call_calls.append(tools)
            yield ("text", "should not be reached")

        mocker.patch("app.voice.llm_service.detect_tool_call_stream", side_effect=fake_detect_tool_call_stream)

        plain_path_called = {"yes": False}

        async def fake_generate_response_stream(
            user_message, session_history, rag_context, long_term_memory,
            system_prompt_override=None, kb_instructions=None,
        ):
            plain_path_called["yes"] = True
            yield "Hi, how can I help?"

        mocker.patch("app.voice.llm_service.generate_response_stream", side_effect=fake_generate_response_stream)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            _wait_until(lambda: plain_path_called["yes"])
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        assert plain_path_called["yes"], "expected the no-tools plain path to run when the agent disabled it"
        assert not detect_tool_call_calls, (
            f"detect_tool_call_stream should never be called when the agent's own toggle disabled "
            f"end_call and no other tools are configured, got {detect_tool_call_calls}"
        )


class TestEndCallEndsTheCallGracefully:
    """Calling end_call must not cut the goodbye off, must not trigger a
    redundant extra LLM round-trip, and must mark the call 'completed' (not
    'failed') once its grace period elapses."""

    def test_goodbye_then_end_call_closes_the_call_as_completed(self, mocker, mock_pipeline):
        # Short grace period so the test doesn't wait the real 3s default.
        mocker.patch("app.telephony.shared.END_CALL_GRACE_SECONDS", 0.3)

        transcriber = _FakeStreamingTranscriber(transcript="that's all, goodbye")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=_fake_detect_speech_factory())

        tts_calls = []

        async def recording_tts(text):
            tts_calls.append(text)
            yield bytes([0xFF]) * 80
        mocker.patch("app.telephony.twilio.twilio_call_service.text_to_speech_twilio", side_effect=recording_tts)

        async def fake_detect_tool_call_stream(conv_messages, system, tools):
            yield ("text", "Thanks for calling, take care!")
            yield ("tool", ("end_call", {"reason": "caller said goodbye"}, "call_1"))

        mocker.patch("app.voice.llm_service.detect_tool_call_stream", side_effect=fake_detect_tool_call_stream)

        mock_stream_tool_result = mocker.patch("app.voice.llm_service.stream_tool_result_response")

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            _wait_until(lambda: "Thanks for calling, take care!" in tts_calls)
            # Keep the media stream alive (as Twilio would, silence included)
            # well past the shortened grace period, so the server's own
            # receive loop gets a chance to notice _end_call_requested and
            # break on its own - this is the real hangup path, not the test
            # tearing the socket down itself.
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
                time.sleep(0.02)

        mock_stream_tool_result.assert_not_called()

        from app.conversations.conversation_repo import set_call_status
        _wait_until(lambda: set_call_status.called)
        args = set_call_status.call_args.args
        assert args[1] == "completed", f"expected end_call to end the call as 'completed', got {args}"
        assert args[2] is None, f"expected no failure_reason from an end_call hangup, got {args}"

    def test_caller_speaking_again_cancels_the_pending_hangup(self, mocker, mock_pipeline):
        """If the caller says something right after the goodbye - before the
        grace period elapses - the call must keep going, not end underneath
        them. Uses a generous grace period relative to the test's own
        near-instant VAD/transcription mocks, so the cancellation has a wide
        margin to land in time."""
        mocker.patch("app.telephony.shared.END_CALL_GRACE_SECONDS", 1.5)

        transcriber = _FakeStreamingTranscriber(transcript="that's all, goodbye")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)
        vad_script = _make_resettable_vad()
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=vad_script)

        tts_calls = []

        async def recording_tts(text):
            tts_calls.append(text)
            yield bytes([0xFF]) * 80
        mocker.patch("app.telephony.twilio.twilio_call_service.text_to_speech_twilio", side_effect=recording_tts)

        call_round = {"n": 0}

        async def fake_detect_tool_call_stream(conv_messages, system, tools):
            call_round["n"] += 1
            if call_round["n"] == 1:
                yield ("text", "Thanks for calling, take care!")
                yield ("tool", ("end_call", {"reason": "caller said goodbye"}, "call_1"))
            else:
                yield ("text", "Oh, sure - what else can I help with?")

        mocker.patch("app.voice.llm_service.detect_tool_call_stream", side_effect=fake_detect_tool_call_stream)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            _wait_until(lambda: "Thanks for calling, take care!" in tts_calls)

            # The caller immediately speaks again - swap the transcript and
            # send a fresh speech+silence burst, well inside the 1.5s grace
            # window, before it ever gets a chance to fire.
            transcriber.transcript = "wait, actually, one more thing"
            vad_script.reset()
            for _ in range(40):
                ws.send_text(_mulaw_media_event())

            _wait_until(lambda: "Oh, sure - what else can I help with?" in tts_calls, timeout=5.0)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        assert call_round["n"] == 2, f"expected a second turn after the caller spoke again, got {call_round['n']} turn(s)"
        assert "Oh, sure - what else can I help with?" in tts_calls, (
            f"the call ended instead of continuing after the caller spoke again, got tts_calls={tts_calls}"
        )
