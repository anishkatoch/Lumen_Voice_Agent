"""
Mirrors tests/test_twilio_stream_pipeline.py::TestTurnHeuristicToggle for the
/plivo/stream endpoint. app/routers/telephony_plivo.py got the identical edit
to read the admin "Turn-taking" toggle (turn_heuristic_enabled) live instead
of the frozen app.core.config constant - this proves that edit actually took
in the Plivo router too, not just Twilio's.
"""
import base64
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
        self._transcript = transcript
        self.started = False

    async def start(self):
        self.started = True

    async def send(self, pcm_bytes: bytes):
        pass

    async def finish(self):
        return self._transcript

    def abort(self):
        pass

    def get_last_partial(self):
        return self._transcript


def _media_event(payload_bytes: bytes = None) -> str:
    payload_bytes = payload_bytes or (bytes([0x00]) * 160)
    return json.dumps({"event": "media", "media": {"payload": base64.b64encode(payload_bytes).decode()}})


def _wait_until(predicate, timeout=2.0, interval=0.02):
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
    mocker.patch("app.telephony.plivo.plivo_repo.get_number_by_phone", return_value=_phone_record())
    mocker.patch("app.agents.agent_repo.get_agent", return_value=_agent())

    save_message_calls = []

    def fake_save_message(*args, **kwargs):
        save_message_calls.append((args, kwargs))
        return {"id": "msg-id"}

    mocker.patch("app.conversations.message_repo.save_message", side_effect=fake_save_message)

    conv_db = mocker.MagicMock()
    conv_db.table.return_value.insert.return_value.execute.return_value.data = [{"id": "conv-id"}]
    mocker.patch("app.core.database.get_supabase", return_value=conv_db)

    async def fake_generate_response_stream(
        user_message, session_history, rag_context, long_term_memory,
        system_prompt_override=None, kb_instructions=None,
    ):
        yield "Sure, I can help with that."

    mocker.patch("app.voice.llm_service.generate_response_stream", side_effect=fake_generate_response_stream)

    async def fake_text_to_speech_plivo(text):
        yield bytes([0xFF]) * 80

    mocker.patch("app.telephony.plivo.plivo_call_service.text_to_speech_plivo", side_effect=fake_text_to_speech_plivo)

    return {"save_message_calls": save_message_calls}


class TestPlivoTurnHeuristicToggle:
    def _drive(self, mocker, enabled: bool, transcript="nine"):
        transcriber = _FakeStreamingTranscriber(transcript=transcript)
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)
        mocker.patch("app.voice.turn_heuristics.sounds_unfinished", return_value=True)

        def fake_admin_get(key):
            if key == "turn_heuristic_enabled":
                return "true" if enabled else "false"
            return "cascade"

        mocker.patch("app.admin.admin_config_service.get", side_effect=fake_admin_get)

        call_count = {"n": 0}

        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5

        mocker.patch("app.voice.vad_service.detect_speech", side_effect=fake_detect_speech)

        from app.main import app
        return TestClient(app)

    def test_disabled_ignores_unfinished_signal_and_finalizes_at_plain_threshold(self, mocker, mock_pipeline):
        client = self._drive(mocker, enabled=False)
        with client.websocket_connect(f"/plivo/stream?to={TEST_PHONE}") as ws:
            ws.send_text(json.dumps({"event": "start", "start": {}}))
            for _ in range(5 + 25):
                ws.send_text(_media_event())
            # Wait for the background run_pipeline task to finish BEFORE
            # sending "stop" - connection teardown cancels any still-in-flight
            # active task, so sending stop first would race that cancellation
            # instead of testing finalization.
            _wait_until(lambda: len(mock_pipeline["save_message_calls"]) >= 2)
            ws.send_text(json.dumps({"event": "stop"}))

        calls = mock_pipeline["save_message_calls"]
        assert len(calls) >= 2, f"expected the turn to finalize with the toggle off, got {calls}"
        assert "nine" in calls[1][0][3]

    def test_enabled_extends_wait_then_still_finalizes(self, mocker, mock_pipeline):
        client = self._drive(mocker, enabled=True)
        with client.websocket_connect(f"/plivo/stream?to={TEST_PHONE}") as ws:
            ws.send_text(json.dumps({"event": "start", "start": {}}))
            for _ in range(5 + 30):
                ws.send_text(_media_event())
            not_yet = _wait_until(lambda: len(mock_pipeline["save_message_calls"]) >= 2, timeout=0.5)
            assert not not_yet, "turn finalized before the extended budget was exhausted"
            assert len(mock_pipeline["save_message_calls"]) == 1

            for _ in range(45):
                ws.send_text(_media_event())
            _wait_until(lambda: len(mock_pipeline["save_message_calls"]) >= 2)
            ws.send_text(json.dumps({"event": "stop"}))

        calls = mock_pipeline["save_message_calls"]
        assert len(calls) >= 2, f"expected the turn to eventually finalize once extended, got {calls}"
        assert "nine" in calls[1][0][3]
