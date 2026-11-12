"""
End-to-end tests for the /twilio/stream WebSocket handler - the pipeline that
Twilio phone calls actually run through (VAD -> Deepgram -> RAG -> LLM -> TTS
-> barge-in). This is the area where real production bugs were found and
fixed (mu-law decode, VAD windowing, wrong function-call signatures in
run_pipeline, barge-in false positives, lost Deepgram transcripts) - each of
those had zero prior coverage, which is why they went undetected. These
tests target exactly that class of bug.

Key technique: mocks for internal functions are built from the REAL function
signature (via inspect.signature / autospec), so a call like
retrieve_agent_context(query=..., doc_ids=...) against a signature of
(query, agent_id, user_id, scopes, top_k) raises TypeError immediately,
the same way the real bug did in production.
"""
import base64
import inspect
import json
import time

import numpy as np
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

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
    """Records send() calls; finish() returns a preset transcript."""
    def __init__(self, transcript="hello there"):
        self._transcript = transcript
        self.sent_chunks = []
        self.started = False
        self.finished = False

    async def start(self):
        self.started = True

    async def send(self, pcm_bytes: bytes):
        self.sent_chunks.append(pcm_bytes)

    async def finish(self):
        self.finished = True
        return self._transcript

    def get_last_partial(self):
        return self._transcript


def _mulaw_media_event(payload_bytes: bytes = None) -> str:
    payload_bytes = payload_bytes or (bytes([0x00]) * 160)
    return json.dumps({
        "event": "media",
        "streamSid": STREAM_SID,
        "media": {"payload": base64.b64encode(payload_bytes).decode()},
    })


def _wait_until(predicate, timeout=2.0, interval=0.02):
    """Poll predicate() until truthy or timeout - used instead of a fixed
    time.sleep() where the thing being waited on (a mock recording a call)
    is directly observable, so a slow/loaded CI runner gets as long as it
    needs instead of racing a hardcoded duration."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _assert_call_matches_signature(real_func, call_args, call_kwargs):
    """Fail with a clear message if call_args/kwargs don't bind to real_func's signature."""
    sig = inspect.signature(real_func)
    try:
        sig.bind(*call_args, **call_kwargs)
    except TypeError as e:
        pytest.fail(
            f"Call to {real_func.__module__}.{real_func.__name__} does not match its "
            f"real signature {sig} - args={call_args} kwargs={call_kwargs}: {e}"
        )


@pytest.fixture
def mock_pipeline(mocker):
    """
    Mocks every external dependency of twilio_stream_endpoint's run_pipeline,
    wrapping the RAG/message functions so any call that doesn't match the
    real signature fails the test immediately (this is what would have
    caught the retrieve_agent_context/retrieve_user_kb_context/save_message
    signature bugs before they ever reached production).
    """
    import app.agents.agent_rag_service as agent_rag_service
    import app.kb.user_kb_rag as user_kb_rag
    import app.conversations.message_repo as message_repo

    real_retrieve_agent_context = agent_rag_service.retrieve_agent_context
    real_retrieve_user_kb_context = user_kb_rag.retrieve_user_kb_context
    real_save_message = message_repo.save_message

    save_message_calls = []

    def fake_retrieve_agent_context(*args, **kwargs):
        _assert_call_matches_signature(real_retrieve_agent_context, args, kwargs)
        return []

    def fake_retrieve_user_kb_context(*args, **kwargs):
        _assert_call_matches_signature(real_retrieve_user_kb_context, args, kwargs)
        return []

    def fake_save_message(*args, **kwargs):
        _assert_call_matches_signature(real_save_message, args, kwargs)
        save_message_calls.append((args, kwargs))
        return {"id": "msg-id"}

    mocker.patch("app.agents.agent_rag_service.retrieve_agent_context", side_effect=fake_retrieve_agent_context)
    mocker.patch("app.kb.user_kb_rag.retrieve_user_kb_context", side_effect=fake_retrieve_user_kb_context)
    mocker.patch("app.conversations.message_repo.save_message", side_effect=fake_save_message)
    mocker.patch("app.conversations.message_repo.get_session_messages", return_value=[])
    mocker.patch("app.kb.user_kb_repo.get_user_documents", return_value=[])
    mocker.patch("app.agents.agent_repo.get_agent_functions", return_value=[])
    mocker.patch("app.conversations.memory_repo.get_user_memory", return_value=None)
    mocker.patch("app.telephony.twilio.twilio_repo.get_number_by_phone", return_value=_phone_record())
    mocker.patch("app.agents.agent_repo.get_agent", return_value=_agent())

    # conversation creation via raw supabase insert
    conv_db = mocker.MagicMock()
    conv_db.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": "conv-id"}
    ]
    mocker.patch("app.core.database.get_supabase", return_value=conv_db)

    async def fake_generate_response_stream(
        user_message, session_history, rag_context, long_term_memory,
        system_prompt_override=None, kb_instructions=None,
    ):
        yield "Sure, I can help with that."

    mocker.patch("app.voice.llm_service.generate_response_stream", side_effect=fake_generate_response_stream)

    async def fake_text_to_speech_twilio(text):
        yield bytes([0xFF]) * 80  # one small mu-law chunk

    mocker.patch("app.telephony.twilio.twilio_call_service.text_to_speech_twilio", side_effect=fake_text_to_speech_twilio)

    return {"save_message_calls": save_message_calls}


class TestPipelineFunctionSignatures:
    """
    Drives a full inbound call through /twilio/stream with real speech
    detected, and verifies every internal call matches its real function
    signature - this is a permanent regression test for the class of bug
    where run_pipeline() called retrieve_agent_context/retrieve_user_kb_context
    /save_message with stale/wrong keyword arguments.
    """

    def test_full_utterance_triggers_correctly_shaped_calls(self, mocker, mock_pipeline):
        transcriber = _FakeStreamingTranscriber(transcript="what can you help me with")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        # Force VAD to report speech for the first several chunks then silence,
        # so the SPEECH_THRESHOLD/SILENCE_THRESHOLD finalization path runs.
        call_count = {"n": 0}

        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5  # speak for 5 chunks, then go silent

        mocker.patch("app.voice.vad_service.detect_speech", side_effect=fake_detect_speech)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            # enough chunks to cover 5 "speech" + 25+ "silence" for finalization
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            # Let the background run_pipeline task (fire-and-forget, matching
            # real Twilio behavior - never awaited by the receive loop) finish
            # sending its reply and saving the assistant message before the
            # `with` block closes the socket underneath it. Without this, the
            # close races the background task and its send_text() call can
            # crash with "Cannot call send once a close message has been sent",
            # silently dropping the assistant save_message call this test
            # asserts on - a real timing characteristic, not a mocking gap.
            # Polls instead of a fixed sleep - a hardcoded duration flaked on
            # a slower/loaded CI runner once more async work (KB eligibility
            # lookup, situational filler selection) landed in the hot path.
            _wait_until(lambda: len(mock_pipeline["save_message_calls"]) >= 3)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        calls = mock_pipeline["save_message_calls"]
        assert len(calls) == 3, f"expected greeting+user+assistant save_message calls, got {calls}"
        # First call: the agent's opening greeting (first_message), spoken before any user turn
        greeting_args = calls[0][0]
        assert greeting_args[2] == "assistant"
        assert greeting_args[3] == "Hi there, how can I help?"
        # Second call: the user's transcribed utterance
        user_args = calls[1][0]
        assert user_args[1] == TEST_USER_ID  # (conversation_id, user_id, role, content)
        assert user_args[2] == "user"
        assert "what can you help me with" in user_args[3]
        # Third call: the assistant's reply
        assistant_args = calls[2][0]
        assert assistant_args[2] == "assistant"


TEST_CAMPAIGN_ID = "ffffffff-1111-1111-1111-ffffffffffff"
TEST_CAMPAIGN_CAND_ID = "eeeeeeee-2222-2222-2222-eeeeeeeeeeee"


def _campaign_candidate_row(**kw):
    return {
        "id": TEST_CAMPAIGN_CAND_ID, "campaign_id": TEST_CAMPAIGN_ID,
        "candidate_id": "dddddddd-2222-2222-2222-dddddddddddd", "status": "calling",
        "hr_campaigns": {
            "id": TEST_CAMPAIGN_ID, "agent_id": TEST_AGENT_ID, "user_id": TEST_USER_ID,
            "questions": [{"id": "q1", "text": "Why this role?"}, {"id": "q2", "text": "Notice period?"}],
        },
        "hr_candidates": {"name": "Priya Sharma", "phone": "+919876543210", "role": "Engineer"},
        **kw,
    }


class TestCampaignCallLiveToolRecording:
    """
    Drives a full campaign call through /twilio/stream (Stage 6) - the only
    stage that touches the shared pipeline also used by inbound calls and
    interview_id outbound calls (both still covered, unmodified, by
    TestPipelineFunctionSignatures above and TestDeepgramFallback below).
    Verifies the record_answer tool is offered, dispatched via its "kind"
    marker (not by name, avoiding a collision with webhook-backed
    agent_functions), and that hr_campaign_repo.record_answer is called with
    the correct real signature.
    """

    def test_record_answer_tool_call_writes_correctly_shaped_answer(self, mocker, mock_pipeline):
        mocker.patch("app.hr_campaigns.hr_campaign_repo.get_campaign_candidate", return_value=_campaign_candidate_row())

        real_record_answer = None
        import app.hr_campaigns.hr_campaign_repo as hr_campaign_repo
        real_record_answer = hr_campaign_repo.record_answer
        record_answer_calls = []

        def fake_record_answer(*args, **kwargs):
            _assert_call_matches_signature(real_record_answer, args, kwargs)
            record_answer_calls.append((args, kwargs))
            return {"id": "answer-id"}

        mocker.patch("app.hr_campaigns.hr_campaign_repo.record_answer", side_effect=fake_record_answer)
        mock_completed = mocker.patch("app.hr_campaigns.hr_campaign_repo.mark_candidate_completed")

        transcriber = _FakeStreamingTranscriber(transcript="I really enjoy this kind of work")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        call_count = {"n": 0}
        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=fake_detect_speech)

        async def fake_detect_tool_call(conv_messages, system, tools):
            tool_names = [t["function"]["name"] for t in tools]
            assert "record_answer" in tool_names, "record_answer tool was not offered to the LLM"
            return (None, "record_answer", {"question_id": "q1", "answer": "I really enjoy this kind of work"}, "call-1")
        mocker.patch("app.voice.llm_service.detect_tool_call", side_effect=fake_detect_tool_call)

        async def fake_stream_tool_result_response(conv_messages, system, tool_name, call_id, tool_args, tool_result):
            assert '"recorded": true' in tool_result.lower() or '"recorded":true' in tool_result.replace(" ", "").lower()
            yield "Great, thanks for sharing that."
        mocker.patch("app.voice.llm_service.stream_tool_result_response", side_effect=fake_stream_tool_result_response)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"campaign_candidate_id": TEST_CAMPAIGN_CAND_ID}},
            }))
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            # Let the background run_pipeline task (fire-and-forget, matching
            # real Twilio behavior - never awaited by the receive loop) finish
            # sending its reply and saving the assistant message before the
            # `with` block closes the socket underneath it. Without this, the
            # close races the background task and its send_text() call can
            # crash with "Cannot call send once a close message has been sent",
            # silently dropping the assistant save_message call this test
            # asserts on - a real timing characteristic, not a mocking gap.
            # Polls instead of a fixed sleep - a hardcoded duration flaked on
            # a slower/loaded CI runner (found 2026-11-04) because it just
            # wasn't long enough there, while passing reliably on a dev machine.
            _wait_until(lambda: len(record_answer_calls) >= 1)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))
            # The server processes "stop" (and awaits mark_candidate_completed)
            # concurrently with this thread sending it - send_text() only
            # guarantees the frame was written, not that the server has
            # finished reacting to it. Without this wait, the `with` block's
            # own exit (closing the socket) can race ahead of that and the
            # assertion below sees 0 calls (found 2026-11-04, after switching
            # the pre-stop wait above off a fixed sleep - that fixed sleep's
            # slack had been incidentally absorbing this second race too).
            _wait_until(lambda: mock_completed.called)

        assert len(record_answer_calls) == 1
        args = record_answer_calls[0][0]
        assert args[0] == TEST_CAMPAIGN_CAND_ID
        assert args[1] == "q1"
        assert args[2] == "Why this role?"
        assert args[3] == "I really enjoy this kind of work"

        mock_completed.assert_called_once_with(TEST_CAMPAIGN_ID, TEST_CAMPAIGN_CAND_ID)

    def test_unknown_question_id_is_rejected_not_inserted(self, mocker, mock_pipeline):
        """The LLM has no enum constraint on question_id (build_openai_tool doesn't
        support one) - a hallucinated id must be rejected server-side, not silently inserted."""
        mocker.patch("app.hr_campaigns.hr_campaign_repo.get_campaign_candidate", return_value=_campaign_candidate_row())
        mock_record = mocker.patch("app.hr_campaigns.hr_campaign_repo.record_answer")
        mocker.patch("app.hr_campaigns.hr_campaign_repo.mark_candidate_completed")

        transcriber = _FakeStreamingTranscriber(transcript="some answer")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        call_count = {"n": 0}
        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=fake_detect_speech)

        async def fake_detect_tool_call(conv_messages, system, tools):
            return (None, "record_answer", {"question_id": "not-a-real-id", "answer": "some answer"}, "call-1")
        mocker.patch("app.voice.llm_service.detect_tool_call", side_effect=fake_detect_tool_call)

        captured_result = {}
        async def fake_stream_tool_result_response(conv_messages, system, tool_name, call_id, tool_args, tool_result):
            captured_result["value"] = tool_result
            yield "Sorry, let me ask that again."
        mocker.patch("app.voice.llm_service.stream_tool_result_response", side_effect=fake_stream_tool_result_response)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"campaign_candidate_id": TEST_CAMPAIGN_CAND_ID}},
            }))
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            # Let the background run_pipeline task (fire-and-forget, matching
            # real Twilio behavior - never awaited by the receive loop) finish
            # sending its reply and saving the assistant message before the
            # `with` block closes the socket underneath it. Without this, the
            # close races the background task and its send_text() call can
            # crash with "Cannot call send once a close message has been sent",
            # silently dropping the assistant save_message call this test
            # asserts on - a real timing characteristic, not a mocking gap.
            # Polls instead of a fixed sleep - see the comment on the same
            # pattern in TestPipelineFunctionSignatures above.
            _wait_until(lambda: "value" in captured_result)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        mock_record.assert_not_called()
        assert "error" in captured_result.get("value", "").lower()

    def test_normal_inbound_call_unaffected_by_campaign_branch(self, mocker, mock_pipeline):
        """
        Regression guard for the Stage 6 constraint: a plain inbound call (no
        campaign_candidate_id in customParameters) must behave exactly as
        before - no record_answer tool offered, no campaign repo calls at all.
        """
        mock_get_campaign_candidate = mocker.patch("app.hr_campaigns.hr_campaign_repo.get_campaign_candidate")

        transcriber = _FakeStreamingTranscriber(transcript="regular inbound question")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        call_count = {"n": 0}
        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=fake_detect_speech)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            # Let the background run_pipeline task (fire-and-forget, matching
            # real Twilio behavior - never awaited by the receive loop) finish
            # sending its reply and saving the assistant message before the
            # `with` block closes the socket underneath it. Without this, the
            # close races the background task and its send_text() call can
            # crash with "Cannot call send once a close message has been sent",
            # silently dropping the assistant save_message call this test
            # asserts on - a real timing characteristic, not a mocking gap.
            # Polls instead of a fixed sleep - see the comment on the same
            # pattern in TestPipelineFunctionSignatures above.
            _wait_until(lambda: len(mock_pipeline["save_message_calls"]) >= 3)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        mock_get_campaign_candidate.assert_not_called()
        calls = mock_pipeline["save_message_calls"]
        assert len(calls) == 3  # greeting + user + assistant, same shape as TestPipelineFunctionSignatures


def _apply_barge_in_debounce(vad_results: list[bool], needed_streak: int = 4) -> list[bool]:
    """
    Mirror of the barge-in streak-counting logic in twilio_stream_endpoint's
    media handler: returns, for each chunk, whether that chunk actually
    triggers a cancel (only True once `needed_streak` consecutive
    above-threshold chunks have been seen).
    """
    streak = 0
    fired = []
    for has_speech in vad_results:
        if not has_speech:
            streak = 0
            fired.append(False)
            continue
        streak += 1
        fired.append(streak >= needed_streak)
    return fired


class TestBargeInDebounce:
    """Regression test: a single spurious high-confidence VAD chunk (line
    noise, brief artifact) previously cancelled the agent's in-flight
    response immediately and irreversibly. Barge-in now requires a sustained
    streak of consecutive above-threshold chunks before committing."""

    def test_single_spurious_chunk_never_fires(self):
        results = [False, False, True, False, False, False]
        fired = _apply_barge_in_debounce(results)
        assert not any(fired), "a lone chunk must never trigger barge-in"

    def test_three_consecutive_chunks_not_enough(self):
        results = [True, True, True, False]
        fired = _apply_barge_in_debounce(results, needed_streak=4)
        assert not any(fired)

    def test_sustained_speech_fires_exactly_once_streak_reached(self):
        results = [True, True, True, True, True]
        fired = _apply_barge_in_debounce(results, needed_streak=4)
        assert fired == [False, False, False, True, True]

    def test_streak_resets_on_interruption(self):
        """True, True, False, True, True, True, True - the reset means the
        streak needed 4 fresh consecutive Trues after the False, not just
        4 Trues total."""
        results = [True, True, False, True, True, True, True]
        fired = _apply_barge_in_debounce(results, needed_streak=4)
        assert fired == [False, False, False, False, False, False, True]


class TestDeepgramFallback:
    """Regression test: Deepgram's live streaming connection intermittently
    returns no transcript despite real speech (confirmed via repeated local
    testing - a cold/timing issue on the streaming connection). run_pipeline
    must fall back to the prerecorded API on the buffered raw audio rather
    than silently dropping the caller's utterance."""

    def test_empty_streaming_transcript_falls_back_to_prerecorded(self, mocker, mock_pipeline):
        # Streaming transcriber "succeeds" technically but returns nothing -
        # exactly what was observed in production.
        transcriber = _FakeStreamingTranscriber(transcript=None)
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        fallback_calls = []

        async def fake_transcribe_audio_smart(wav_bytes):
            fallback_calls.append(wav_bytes)
            return "recovered via prerecorded fallback"

        mocker.patch("app.voice.deepgram_service.transcribe_audio_smart", side_effect=fake_transcribe_audio_smart)

        call_count = {"n": 0}

        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5

        mocker.patch("app.voice.vad_service.detect_speech", side_effect=fake_detect_speech)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            # Let the background run_pipeline task (fire-and-forget, matching
            # real Twilio behavior - never awaited by the receive loop) finish
            # sending its reply and saving the assistant message before the
            # `with` block closes the socket underneath it. Without this, the
            # close races the background task and its send_text() call can
            # crash with "Cannot call send once a close message has been sent",
            # silently dropping the assistant save_message call this test
            # asserts on - a real timing characteristic, not a mocking gap.
            # Polls instead of a fixed sleep - see the comment on the same
            # pattern in TestPipelineFunctionSignatures above.
            _wait_until(lambda: len(mock_pipeline["save_message_calls"]) >= 3)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        assert len(fallback_calls) == 1, "prerecorded fallback should have been invoked exactly once"
        calls = mock_pipeline["save_message_calls"]
        assert len(calls) == 3  # greeting + user (recovered via fallback) + assistant reply
        assert "recovered via prerecorded fallback" in calls[1][0][3]


class TestMuLawDecodeCorrectness:
    """Regression test: the mu-law decode table had a bit-shift bug where all
    256 values decoded to roughly 1/3-1/2 their real amplitude, making
    incoming call audio near-silent to VAD. Verified against the standard
    ITU-T G.711 reference algorithm."""

    def test_decode_table_matches_reference_algorithm(self):
        from app.telephony.twilio.twilio_call_service import _build_decode_table

        BIAS = 0x84

        def reference_ulaw2linear(u_val):
            u_val = (~u_val) & 0xFF
            t = ((u_val & 0x0F) << 3) + BIAS
            t <<= (u_val & 0x70) >> 4
            return (t - BIAS) if not (u_val & 0x80) else (BIAS - t)

        table = _build_decode_table()
        for byte in range(256):
            assert int(table[byte]) == reference_ulaw2linear(byte), f"byte 0x{byte:02x} mismatch"

    def test_decoded_speech_level_audio_is_not_near_silent(self):
        """A realistic speech-level tone must decode back to a realistic
        amplitude, not be crushed toward zero (the exact symptom that made
        VAD unable to detect real callers speaking)."""
        from app.telephony.twilio.twilio_call_service import mulaw_encode, mulaw_decode

        t = np.linspace(0, 1, 8000, dtype=np.float32)
        tone = (np.sin(2 * np.pi * 200 * t) * 10000).astype(np.int16)
        decoded = np.frombuffer(mulaw_decode(mulaw_encode(tone.tobytes())), dtype=np.int16)
        rms_normalized = float(np.sqrt(np.mean((decoded.astype(np.float32) / 32768.0) ** 2)))
        assert rms_normalized > 0.1, f"decoded RMS {rms_normalized} is near-silent - decode table regressed"


class TestVADWindowing:
    """Regression test: Silero requires a minimum 512-sample window: a naive
    range(0, len-WINDOW+1, WINDOW) over a shorter Twilio chunk (320 samples)
    is an empty range, so the model call loop never runs and probability is
    hardcoded to 0.0 regardless of actual audio content. Verify short chunks
    still invoke the model at least once."""

    def test_short_chunk_still_invokes_model(self, mocker):
        import app.voice.vad_service as vad_service

        call_lengths = []
        fake_model = mocker.MagicMock()

        def fake_model_call(tensor, sample_rate):
            call_lengths.append(len(tensor))
            return mocker.MagicMock(item=lambda: 0.9, __float__=lambda self: 0.9)

        fake_model.side_effect = fake_model_call
        fake_model.reset_states = mocker.MagicMock()
        mocker.patch.object(vad_service, "_load_model", return_value=fake_model)

        # 320 samples (20ms @ 16kHz) - shorter than Silero's 512-sample window
        short_chunk = (np.zeros(320, dtype=np.int16)).tobytes()
        vad_service._detect_speech_sync(short_chunk, sensitivity=0.5)

        assert len(call_lengths) >= 1, "model was never called - the windowing bug has regressed"
        assert call_lengths[0] == 512, "chunk should be padded up to the full 512-sample window"


class TestSaveMessageSignature:
    """Regression test: the Twilio pipeline called save_message(conversation_id,
    'user', content, user_id) - wrong order vs. the real
    (conversation_id, user_id, role, content) - which inserted the string
    'user' into a UUID column and crashed with a Postgres error."""

    def test_real_signature_is_conversation_user_role_content(self):
        from app.conversations.message_repo import save_message
        sig = inspect.signature(save_message)
        assert list(sig.parameters) == ["conversation_id", "user_id", "role", "content"]


class TestBargeInStopsGenerationAndSpeech:
    """Regression test for a real concern: when the caller barges in, does
    the interruption actually stop BOTH halves of the response pipeline -
    the LLM generating more text, and the TTS audio actually being spoken -
    or could one keep running after the other appears to stop? Verified via
    a slow, multi-sentence fake LLM stream with a real delay between
    sentences, so there's a window to interrupt mid-response and check both
    sides of the gap."""

    async def test_barge_in_cancels_both_llm_stream_and_tts(self, mocker, mock_pipeline):
        import asyncio

        transcriber = _FakeStreamingTranscriber(transcript="what is the leave policy")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        # First ~5 chunks are the caller's initial question (speech), then
        # silence to finalize it. Every chunk after that (the barge-in burst,
        # sent later once the agent is mid-response) reports sustained speech.
        def fake_detect_speech_factory():
            n = {"count": 0}

            async def fake_detect_speech(audio_bytes, sensitivity=0.5):
                n["count"] += 1
                if n["count"] <= 5:
                    return True
                if n["count"] <= 30:
                    return False
                return True
            return fake_detect_speech

        mocker.patch("app.voice.vad_service.detect_speech", side_effect=fake_detect_speech_factory())

        generation_state = {"cancelled": False, "yielded": []}

        async def slow_multi_sentence_stream(
            user_message, session_history, rag_context, long_term_memory,
            system_prompt_override=None, kb_instructions=None,
        ):
            sentences = ["Sentence one.", "Sentence two.", "Sentence three.", "Sentence four."]
            try:
                for s in sentences:
                    generation_state["yielded"].append(s)
                    yield s
                    await asyncio.sleep(0.4)
            except asyncio.CancelledError:
                generation_state["cancelled"] = True
                raise

        mocker.patch("app.voice.llm_service.generate_response_stream", side_effect=slow_multi_sentence_stream)

        tts_calls = []

        async def recording_text_to_speech_twilio(text):
            tts_calls.append(text)
            yield bytes([0xFF]) * 80

        mocker.patch(
            "app.telephony.twilio.twilio_call_service.text_to_speech_twilio", side_effect=recording_text_to_speech_twilio
        )

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            # Caller's initial question: 5 speech chunks + exactly 25 silence
            # chunks to finalize (matches SPEECH_THRESHOLD/SILENCE_THRESHOLD) -
            # no extra chunks, so none of them spill into the barge-in check
            # against the response that's about to start.
            for _ in range(30):
                ws.send_text(_mulaw_media_event())

            # Let "Sentence one." get spoken, then interrupt mid-response
            # (well before "Sentence two." at the 0.4s mark). Polls for the
            # observable event instead of a fixed sleep - a hardcoded
            # duration flaked on a slower/loaded CI runner.
            _wait_until(lambda: "Sentence one." in tts_calls)
            for _ in range(6):  # > BARGE_IN_STREAK_NEEDED (4)
                ws.send_text(_mulaw_media_event())

            # Give the cancellation time to propagate, and long enough that
            # "Sentence two."/"three."/"four." would have been spoken by now
            # if the interruption hadn't actually stopped the old response.
            _wait_until(lambda: generation_state["cancelled"], timeout=3.0)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        assert "Sentence one." in tts_calls, f"expected the first sentence to have been spoken, got {tts_calls}"
        assert generation_state["cancelled"], "barge-in should cancel the in-flight LLM stream, not just stop speaking"
        for later in ("Sentence two.", "Sentence three.", "Sentence four."):
            assert later not in tts_calls, (
                f"{later!r} was spoken after barge-in - interruption didn't actually stop "
                f"the old response's audio, got tts_calls={tts_calls}"
            )


class TestCallEndCancelsInFlightResponse:
    """Regression test: if the caller hangs up (or the stream otherwise
    ends) while run_pipeline is still generating/speaking a reply, the
    background task used to keep running after the socket closed, then
    crashed with an unhandled RuntimeError('Cannot call "send" once a close
    message has been sent') - wasted LLM/TTS calls for a reply nobody could
    hear, and noisy unretrieved-exception logs. The fix cancels the
    in-flight task in twilio_stream_endpoint's finally block."""

    async def test_hangup_mid_response_cancels_pipeline_task(self, mocker, mock_pipeline):
        import asyncio

        transcriber = _FakeStreamingTranscriber(transcript="what is the leave policy")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        call_count = {"n": 0}

        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5  # 5 speech chunks, then silence to finalize
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=fake_detect_speech)

        generation_state = {"cancelled": False}

        async def slow_multi_sentence_stream(
            user_message, session_history, rag_context, long_term_memory,
            system_prompt_override=None, kb_instructions=None,
        ):
            try:
                for s in ["Sentence one.", "Sentence two.", "Sentence three."]:
                    yield s
                    await asyncio.sleep(0.4)
            except asyncio.CancelledError:
                generation_state["cancelled"] = True
                raise

        mocker.patch("app.voice.llm_service.generate_response_stream", side_effect=slow_multi_sentence_stream)

        tts_calls = []

        async def recording_text_to_speech_twilio(text):
            tts_calls.append(text)
            yield bytes([0xFF]) * 80

        mocker.patch(
            "app.telephony.twilio.twilio_call_service.text_to_speech_twilio", side_effect=recording_text_to_speech_twilio
        )

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            for _ in range(30):  # 5 speech + exactly 25 silence chunks to finalize
                ws.send_text(_mulaw_media_event())

            # "Sentence one." gets spoken, then the caller hangs up - well
            # before "Sentence two." at the 0.4s mark - instead of a barge-in.
            # Polls for the observable event instead of a fixed sleep - a
            # hardcoded duration flaked on a slower/loaded CI runner.
            _wait_until(lambda: "Sentence one." in tts_calls)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

            # Give the server time to process "stop" and run its cleanup
            # while the connection is still open - exiting the `with` block
            # right away masks the bug (the client-side teardown races with,
            # and can hide, what the orphaned task does on the server side).
            _wait_until(lambda: generation_state["cancelled"], timeout=3.0)

        assert "Sentence one." in tts_calls, f"expected the first sentence to have been spoken, got {tts_calls}"
        assert generation_state["cancelled"], "ending the call should cancel the in-flight LLM stream"
        for later in ("Sentence two.", "Sentence three."):
            assert later not in tts_calls, (
                f"{later!r} was spoken after the call ended - the pipeline task wasn't cancelled "
                f"on hangup, got tts_calls={tts_calls}"
            )


class _FakeTwilioWebSocket:
    """Drives twilio_stream_endpoint directly instead of through the real
    ASGI/thread transport - deterministic, unlike racing a background
    thread's disconnect delivery against a sleep."""

    def __init__(self, messages, end_exc: Exception | None = None):
        self._messages = list(messages)
        self.sent: list[str] = []
        # Defaults to the normal-close case (Starlette's WebSocketDisconnect,
        # code 1000) once messages run out - pass a different exception (e.g.
        # a plain RuntimeError) to simulate a genuine mid-call crash instead.
        self._end_exc = end_exc if end_exc is not None else WebSocketDisconnect(code=1000)

    async def accept(self):
        pass

    async def receive_text(self):
        if self._messages:
            return self._messages.pop(0)
        raise self._end_exc

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code=1000):
        pass


class TestNormalDisconnectNotMarkedFailed:
    """Regression test: when the client (Twilio) closes the media-stream
    websocket normally - without sending a "stop" event first - Starlette
    raises WebSocketDisconnect(code=1000), whose str() renders as "(1000, '')".
    That string used to get stored as the call's failure_reason, marking a
    perfectly ordinary call end as "failed" in Call Analytics."""

    async def test_client_closing_without_stop_event_marks_completed(self, mocker, mock_pipeline):
        mock_set_status = mocker.patch("app.conversations.conversation_repo.set_call_status")

        transcriber = _FakeStreamingTranscriber(transcript="hello there")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)
        mocker.patch("app.voice.vad_service.detect_speech", return_value=False)

        from app.routers.telephony_twilio import twilio_stream_endpoint
        fake_ws = _FakeTwilioWebSocket([json.dumps({
            "event": "start",
            "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
        })])
        await twilio_stream_endpoint(fake_ws)

        mock_set_status.assert_called_once()
        args = mock_set_status.call_args.args
        assert args[1] == "completed", f"expected a normal client close to be 'completed', got {args}"
        assert args[2] is None, f"expected no failure_reason for a normal close, got {args}"


TEST_INTERVIEW_ID = "77777777-3333-3333-3333-777777777777"


class TestMidCallCrashMarksOutcomeFailed:
    """Regression test for a real bug: when an interview or campaign call
    connects and then genuinely crashes mid-stream (a non-1000/1001
    WebSocketDisconnect, or any other exception) - as opposed to a clean
    "stop" event or an ordinary client close - the finally block's fallback
    used to unconditionally call mark_interview_completed/mark_candidate_completed
    regardless of _call_failure_reason, so hr_interviews/hr_campaign_candidates
    showed "completed" for a call Call Analytics correctly recorded as
    "failed". This is also the ONLY path ever taken for Plivo calls (which
    never deliver a WS "stop" event) and for the realtime pipeline on Twilio
    (which also leaves _call_ended False), so the bug wasn't crash-only."""

    async def test_interview_crash_marks_failed_not_completed(self, mocker, mock_pipeline):
        from app.telephony.shared import CallContext
        ctx = CallContext()
        ctx.agent_id = TEST_AGENT_ID
        ctx.user_id = TEST_USER_ID
        ctx.interview_id = TEST_INTERVIEW_ID
        mocker.patch("app.telephony.shared.resolve_call_context", return_value=ctx)
        mocker.patch("app.conversations.conversation_repo.set_call_status")
        mocker.patch("app.conversations.conversation_repo.set_call_duration_and_cost")
        mock_failed = mocker.patch("app.hr_campaigns.hr_repo.mark_interview_failed")
        mock_completed = mocker.patch("app.hr_campaigns.hr_repo.mark_interview_completed")

        from app.routers.telephony_twilio import twilio_stream_endpoint
        fake_ws = _FakeTwilioWebSocket(
            [json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "callSid": "CAcrash0000000000000000000000000000",
                          "customParameters": {"interview_id": TEST_INTERVIEW_ID}},
            })],
            end_exc=RuntimeError("simulated mid-call crash"),
        )
        await twilio_stream_endpoint(fake_ws)

        mock_failed.assert_called_once_with(TEST_INTERVIEW_ID)
        mock_completed.assert_not_called()

    async def test_campaign_crash_marks_failed_not_completed(self, mocker, mock_pipeline):
        mocker.patch("app.hr_campaigns.hr_campaign_repo.get_campaign_candidate", return_value=_campaign_candidate_row())
        mocker.patch("app.conversations.conversation_repo.set_call_status")
        mocker.patch("app.conversations.conversation_repo.set_call_duration_and_cost")
        mock_status_from_sid = mocker.patch("app.hr_campaigns.hr_campaign_repo.mark_candidate_status_from_call_sid")
        mock_completed = mocker.patch("app.hr_campaigns.hr_campaign_repo.mark_candidate_completed")

        from app.routers.telephony_twilio import twilio_stream_endpoint
        call_sid = "CAcrash1111111111111111111111111111"
        fake_ws = _FakeTwilioWebSocket(
            [json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "callSid": call_sid,
                          "customParameters": {"campaign_candidate_id": TEST_CAMPAIGN_CAND_ID}},
            })],
            end_exc=RuntimeError("simulated mid-call crash"),
        )
        await twilio_stream_endpoint(fake_ws)

        mock_status_from_sid.assert_called_once_with(call_sid, "failed")
        mock_completed.assert_not_called()


class TestRetrievalFiller:
    """When RAG/LLM latency exceeds RETRIEVAL_FILLER_DELAY_SECONDS (1.2s) with
    nothing spoken yet, run_pipeline should say a filler phrase to cover the
    gap instead of leaving the caller in silence."""

    async def test_slow_llm_response_triggers_filler_phrase(self, mocker, mock_pipeline):
        from app.voice.llm_service import FILLER_PHRASES

        transcriber = _FakeStreamingTranscriber(transcript="tell me about my order")
        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            fake_detect_speech.n += 1
            return fake_detect_speech.n <= 5  # speak for 5 chunks, then go silent
        fake_detect_speech.n = 0
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=fake_detect_speech)

        import asyncio

        async def slow_generate_response_stream(
            user_message, session_history, rag_context, long_term_memory,
            system_prompt_override=None, kb_instructions=None,
        ):
            await asyncio.sleep(1.5)  # longer than RETRIEVAL_FILLER_DELAY_SECONDS (1.2s)
            yield "Here is your order status."

        mocker.patch("app.voice.llm_service.generate_response_stream", side_effect=slow_generate_response_stream)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect("/twilio/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": STREAM_SID, "customParameters": {"to": TEST_PHONE}},
            }))
            for _ in range(40):
                ws.send_text(_mulaw_media_event())
            # Give the watchdog (1.2s) + the slow fake LLM (1.5s) + TTS time
            # to finish before the `with` block tears the socket down. Polls
            # instead of a fixed sleep - a hardcoded duration flaked on a
            # slower/loaded CI runner.
            _wait_until(lambda: len(mock_pipeline["save_message_calls"]) >= 3, timeout=8.0)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        calls = mock_pipeline["save_message_calls"]
        assistant_calls = [c for c in calls if c[0][2] == "assistant"]
        assert assistant_calls, f"expected an assistant save_message call, got {calls}"
        # Last, not first - the opening greeting is also an "assistant" save_message
        # call now, and it's always the first one, well before this turn's reply.
        full_reply = assistant_calls[-1][0][3]

        assert any(phrase in full_reply for phrase in FILLER_PHRASES), (
            f"expected a filler phrase to cover the slow LLM response, got: {full_reply!r}"
        )
        assert "Here is your order status." in full_reply, (
            f"expected the real reply to still be spoken after the filler, got: {full_reply!r}"
        )
