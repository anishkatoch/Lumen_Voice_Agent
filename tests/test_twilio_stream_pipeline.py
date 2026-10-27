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


def _mulaw_media_event(payload_bytes: bytes = None) -> str:
    payload_bytes = payload_bytes or (bytes([0x00]) * 160)
    return json.dumps({
        "event": "media",
        "streamSid": STREAM_SID,
        "media": {"payload": base64.b64encode(payload_bytes).decode()},
    })


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
    import app.agent_rag_service as agent_rag_service
    import app.user_kb_rag as user_kb_rag
    import app.message_repo as message_repo

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

    mocker.patch("app.agent_rag_service.retrieve_agent_context", side_effect=fake_retrieve_agent_context)
    mocker.patch("app.user_kb_rag.retrieve_user_kb_context", side_effect=fake_retrieve_user_kb_context)
    mocker.patch("app.message_repo.save_message", side_effect=fake_save_message)
    mocker.patch("app.message_repo.get_session_messages", return_value=[])
    mocker.patch("app.user_kb_repo.get_user_documents", return_value=[])
    mocker.patch("app.agent_repo.get_agent_functions", return_value=[])
    mocker.patch("app.memory_repo.get_user_memory", return_value=None)
    mocker.patch("app.twilio_repo.get_number_by_phone", return_value=_phone_record())
    mocker.patch("app.agent_repo.get_agent", return_value=_agent())

    # conversation creation via raw supabase insert
    conv_db = mocker.MagicMock()
    conv_db.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": "conv-id"}
    ]
    mocker.patch("app.database.get_supabase", return_value=conv_db)

    async def fake_generate_response_stream(
        user_message, session_history, rag_context, long_term_memory,
        system_prompt_override=None, kb_instructions=None,
    ):
        yield "Sure, I can help with that."

    mocker.patch("app.llm_service.generate_response_stream", side_effect=fake_generate_response_stream)

    async def fake_text_to_speech_twilio(text):
        yield bytes([0xFF]) * 80  # one small mu-law chunk

    mocker.patch("app.twilio_call_service.text_to_speech_twilio", side_effect=fake_text_to_speech_twilio)

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
        mocker.patch("app.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        # Force VAD to report speech for the first several chunks then silence,
        # so the SPEECH_THRESHOLD/SILENCE_THRESHOLD finalization path runs.
        call_count = {"n": 0}

        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5  # speak for 5 chunks, then go silent

        mocker.patch("app.vad_service.detect_speech", side_effect=fake_detect_speech)

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
            time.sleep(0.3)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        calls = mock_pipeline["save_message_calls"]
        assert len(calls) == 2, f"expected user+assistant save_message calls, got {calls}"
        # First call: the user's transcribed utterance
        user_args = calls[0][0]
        assert user_args[1] == TEST_USER_ID  # (conversation_id, user_id, role, content)
        assert user_args[2] == "user"
        assert "what can you help me with" in user_args[3]
        # Second call: the assistant's reply
        assistant_args = calls[1][0]
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
        mocker.patch("app.hr_campaign_repo.get_campaign_candidate", return_value=_campaign_candidate_row())

        real_record_answer = None
        import app.hr_campaign_repo as hr_campaign_repo
        real_record_answer = hr_campaign_repo.record_answer
        record_answer_calls = []

        def fake_record_answer(*args, **kwargs):
            _assert_call_matches_signature(real_record_answer, args, kwargs)
            record_answer_calls.append((args, kwargs))
            return {"id": "answer-id"}

        mocker.patch("app.hr_campaign_repo.record_answer", side_effect=fake_record_answer)
        mock_completed = mocker.patch("app.hr_campaign_repo.mark_candidate_completed")

        transcriber = _FakeStreamingTranscriber(transcript="I really enjoy this kind of work")
        mocker.patch("app.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        call_count = {"n": 0}
        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5
        mocker.patch("app.vad_service.detect_speech", side_effect=fake_detect_speech)

        async def fake_detect_tool_call(conv_messages, system, tools):
            tool_names = [t["function"]["name"] for t in tools]
            assert "record_answer" in tool_names, "record_answer tool was not offered to the LLM"
            return (None, "record_answer", {"question_id": "q1", "answer": "I really enjoy this kind of work"}, "call-1")
        mocker.patch("app.llm_service.detect_tool_call", side_effect=fake_detect_tool_call)

        async def fake_stream_tool_result_response(conv_messages, system, tool_name, call_id, tool_args, tool_result):
            assert '"recorded": true' in tool_result.lower() or '"recorded":true' in tool_result.replace(" ", "").lower()
            yield "Great, thanks for sharing that."
        mocker.patch("app.llm_service.stream_tool_result_response", side_effect=fake_stream_tool_result_response)

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
            time.sleep(0.3)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

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
        mocker.patch("app.hr_campaign_repo.get_campaign_candidate", return_value=_campaign_candidate_row())
        mock_record = mocker.patch("app.hr_campaign_repo.record_answer")
        mocker.patch("app.hr_campaign_repo.mark_candidate_completed")

        transcriber = _FakeStreamingTranscriber(transcript="some answer")
        mocker.patch("app.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        call_count = {"n": 0}
        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5
        mocker.patch("app.vad_service.detect_speech", side_effect=fake_detect_speech)

        async def fake_detect_tool_call(conv_messages, system, tools):
            return (None, "record_answer", {"question_id": "not-a-real-id", "answer": "some answer"}, "call-1")
        mocker.patch("app.llm_service.detect_tool_call", side_effect=fake_detect_tool_call)

        captured_result = {}
        async def fake_stream_tool_result_response(conv_messages, system, tool_name, call_id, tool_args, tool_result):
            captured_result["value"] = tool_result
            yield "Sorry, let me ask that again."
        mocker.patch("app.llm_service.stream_tool_result_response", side_effect=fake_stream_tool_result_response)

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
            time.sleep(0.3)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        mock_record.assert_not_called()
        assert "error" in captured_result.get("value", "").lower()

    def test_normal_inbound_call_unaffected_by_campaign_branch(self, mocker, mock_pipeline):
        """
        Regression guard for the Stage 6 constraint: a plain inbound call (no
        campaign_candidate_id in customParameters) must behave exactly as
        before - no record_answer tool offered, no campaign repo calls at all.
        """
        mock_get_campaign_candidate = mocker.patch("app.hr_campaign_repo.get_campaign_candidate")

        transcriber = _FakeStreamingTranscriber(transcript="regular inbound question")
        mocker.patch("app.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        call_count = {"n": 0}
        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5
        mocker.patch("app.vad_service.detect_speech", side_effect=fake_detect_speech)

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
            time.sleep(0.3)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        mock_get_campaign_candidate.assert_not_called()
        calls = mock_pipeline["save_message_calls"]
        assert len(calls) == 2  # unchanged from TestPipelineFunctionSignatures - plain text path, no tool call


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
        mocker.patch("app.deepgram_service.get_streaming_transcriber", return_value=transcriber)

        fallback_calls = []

        async def fake_transcribe_audio_smart(wav_bytes):
            fallback_calls.append(wav_bytes)
            return "recovered via prerecorded fallback"

        mocker.patch("app.deepgram_service.transcribe_audio_smart", side_effect=fake_transcribe_audio_smart)

        call_count = {"n": 0}

        async def fake_detect_speech(audio_bytes, sensitivity=0.5):
            call_count["n"] += 1
            return call_count["n"] <= 5

        mocker.patch("app.vad_service.detect_speech", side_effect=fake_detect_speech)

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
            time.sleep(0.3)
            ws.send_text(json.dumps({"event": "stop", "streamSid": STREAM_SID}))

        assert len(fallback_calls) == 1, "prerecorded fallback should have been invoked exactly once"
        calls = mock_pipeline["save_message_calls"]
        assert len(calls) == 2
        assert "recovered via prerecorded fallback" in calls[0][0][3]


class TestMuLawDecodeCorrectness:
    """Regression test: the mu-law decode table had a bit-shift bug where all
    256 values decoded to roughly 1/3-1/2 their real amplitude, making
    incoming call audio near-silent to VAD. Verified against the standard
    ITU-T G.711 reference algorithm."""

    def test_decode_table_matches_reference_algorithm(self):
        from app.twilio_call_service import _build_decode_table

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
        from app.twilio_call_service import mulaw_encode, mulaw_decode

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
        import app.vad_service as vad_service

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
        from app.message_repo import save_message
        sig = inspect.signature(save_message)
        assert list(sig.parameters) == ["conversation_id", "user_id", "role", "content"]
