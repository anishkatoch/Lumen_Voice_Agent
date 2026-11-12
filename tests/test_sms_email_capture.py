"""
Tests for collecting the caller's email by SMS reply during a Plivo call:
- extract_email (plain, embedded, the typed-out "at / dot" form, none)
- the request_email_by_sms / check_sms_reply handlers (Plivo + DB mocked)
- handle_inbound_sms waking the live call's listener
- the /plivo/sms/inbound webhook route
- send_plivo_sms against a mocked Plivo Message API
- plivo_webhook_config retrofitting message_url onto an existing Application
- the whole thing end to end through /plivo/stream: tool offered, text
  sent mid-call, reply arrives while the call is live, lands as the
  caller's next turn
"""
import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

pytestmark = pytest.mark.asyncio

USER_ID = "11111111-1111-1111-1111-111111111111"
AGENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CONV_ID = "conv-id"
AGENT_NUMBER = "+14155550100"
CALLER_NUMBER = "+918894441697"


def _ctx(**kw):
    from app.telephony.sms_email_capture import SmsCaptureContext
    return SmsCaptureContext(
        user_id=USER_ID, agent_id=AGENT_ID, agent_name="Lumen", conversation_id=CONV_ID,
        agent_number=AGENT_NUMBER, caller_number=CALLER_NUMBER, auth_id="MAXXXX", auth_token="tok",
        **kw,
    )


def _request_fn():
    from app.telephony.sms_email_capture import build_request_email_tool
    return build_request_email_tool()


def _check_fn():
    from app.telephony.sms_email_capture import build_check_reply_tool
    return build_check_reply_tool()


# ── extract_email ────────────────────────────────────────────────────────────

class TestExtractEmail:
    def test_plain(self):
        from app.telephony.sms_email_capture import extract_email
        assert extract_email("anish.k@example.com") == "anish.k@example.com"

    def test_embedded_with_trailing_punctuation(self):
        from app.telephony.sms_email_capture import extract_email
        assert extract_email("Sure! my email is John_Doe+x@mail.co.uk.") == "John_Doe+x@mail.co.uk"

    def test_typed_out_at_and_dot(self):
        from app.telephony.sms_email_capture import extract_email
        assert extract_email("john at gmail dot com") == "john@gmail.com"

    def test_none_when_absent(self):
        from app.telephony.sms_email_capture import extract_email
        assert extract_email("call me back later") is None
        assert extract_email("") is None


# ── request_email_by_sms ─────────────────────────────────────────────────────

class TestRequestEmail:
    async def test_sends_text_and_records_request(self, mocker):
        from app.telephony.sms_email_capture import execute_sms_email_tool
        mocker.patch("app.telephony.sms_email_repo.get_latest_for_conversation", return_value=None)
        send = mocker.patch("app.telephony.plivo.plivo_sms_service.send_plivo_sms",
                            new=AsyncMock(return_value=("uuid-1", None)))
        create = mocker.patch("app.telephony.sms_email_repo.create_request", return_value={"id": "r1"})

        out = await execute_sms_email_tool(_request_fn(), {}, _ctx())

        send.assert_awaited_once()
        auth_id, token, src, dst, text = send.await_args.args
        assert (auth_id, token, src, dst) == ("MAXXXX", "tok", AGENT_NUMBER, CALLER_NUMBER)
        assert "Lumen" in text and "email" in text.lower()
        create.assert_called_once_with(USER_ID, AGENT_ID, CONV_ID, AGENT_NUMBER, CALLER_NUMBER, "uuid-1")
        assert "text message was sent" in out
        assert "continue the conversation" in out

    async def test_send_failure_falls_back_to_spelling(self, mocker):
        from app.telephony.sms_email_capture import execute_sms_email_tool
        mocker.patch("app.telephony.sms_email_repo.get_latest_for_conversation", return_value=None)
        mocker.patch("app.telephony.plivo.plivo_sms_service.send_plivo_sms",
                     new=AsyncMock(return_value=(None, "Plivo returned 400")))
        create = mocker.patch("app.telephony.sms_email_repo.create_request")

        out = await execute_sms_email_tool(_request_fn(), {}, _ctx())

        create.assert_not_called()
        assert "spell out" in out

    async def test_does_not_resend_while_pending(self, mocker):
        from app.telephony.sms_email_capture import execute_sms_email_tool
        mocker.patch("app.telephony.sms_email_repo.get_latest_for_conversation",
                     return_value={"status": "pending"})
        send = mocker.patch("app.telephony.plivo.plivo_sms_service.send_plivo_sms", new=AsyncMock())

        out = await execute_sms_email_tool(_request_fn(), {}, _ctx())

        send.assert_not_awaited()
        assert "already sent" in out

    async def test_reuses_an_already_received_email(self, mocker):
        from app.telephony.sms_email_capture import execute_sms_email_tool
        mocker.patch("app.telephony.sms_email_repo.get_latest_for_conversation",
                     return_value={"status": "received", "email": "a@b.co"})
        send = mocker.patch("app.telephony.plivo.plivo_sms_service.send_plivo_sms", new=AsyncMock())

        out = await execute_sms_email_tool(_request_fn(), {}, _ctx())

        send.assert_not_awaited()
        assert "a@b.co" in out

    async def test_without_context_tool_is_unavailable(self):
        from app.telephony.sms_email_capture import execute_sms_email_tool
        assert "not available" in await execute_sms_email_tool(_request_fn(), {}, None)

    async def test_never_raises(self, mocker):
        from app.telephony.sms_email_capture import execute_sms_email_tool
        mocker.patch("app.telephony.sms_email_repo.get_latest_for_conversation", side_effect=RuntimeError("db down"))
        out = await execute_sms_email_tool(_request_fn(), {}, _ctx())
        assert "spell out" in out


# ── check_sms_reply ──────────────────────────────────────────────────────────

class TestCheckReply:
    async def test_nothing_sent_yet(self, mocker):
        from app.telephony.sms_email_capture import execute_sms_email_tool
        mocker.patch("app.telephony.sms_email_repo.get_latest_for_conversation", return_value=None)
        assert "No text message has been sent" in await execute_sms_email_tool(_check_fn(), {}, _ctx())

    async def test_still_pending(self, mocker):
        from app.telephony.sms_email_capture import execute_sms_email_tool
        mocker.patch("app.telephony.sms_email_repo.get_latest_for_conversation", return_value={"status": "pending"})
        assert "No reply has arrived yet" in await execute_sms_email_tool(_check_fn(), {}, _ctx())

    async def test_received_with_email(self, mocker):
        from app.telephony.sms_email_capture import execute_sms_email_tool
        mocker.patch("app.telephony.sms_email_repo.get_latest_for_conversation",
                     return_value={"status": "received", "email": "x@y.io", "reply_text": "x@y.io"})
        out = await execute_sms_email_tool(_check_fn(), {}, _ctx())
        assert "x@y.io" in out and "no need to spell it back" in out

    async def test_received_without_email(self, mocker):
        from app.telephony.sms_email_capture import execute_sms_email_tool
        mocker.patch("app.telephony.sms_email_repo.get_latest_for_conversation",
                     return_value={"status": "received", "email": None, "reply_text": "ok will do"})
        out = await execute_sms_email_tool(_check_fn(), {}, _ctx())
        assert "does not contain a valid email" in out


# ── inbound reply -> live call listener ──────────────────────────────────────

class TestHandleInboundSms:
    async def test_marks_reply_and_wakes_listener(self, mocker):
        from app.telephony.sms_email_capture import (
            handle_inbound_sms, register_reply_listener, unregister_reply_listeners,
        )
        row = {"id": "r1", "conversation_id": CONV_ID, "reply_text": "me@x.com", "email": "me@x.com"}
        mark = mocker.patch("app.telephony.sms_email_repo.mark_reply", return_value=row)
        got = []

        async def listener(r):
            got.append(r)

        register_reply_listener(CONV_ID, listener)
        try:
            out = await handle_inbound_sms("918894441697", "14155550100", "me@x.com")
            for _ in range(20):
                if got:
                    break
                await asyncio.sleep(0.01)
        finally:
            unregister_reply_listeners(CONV_ID)

        assert out == row
        mark.assert_called_once_with("918894441697", "14155550100", "me@x.com", "me@x.com")
        assert got == [row]

    async def test_unmatched_text_is_ignored(self, mocker):
        from app.telephony.sms_email_capture import handle_inbound_sms
        mocker.patch("app.telephony.sms_email_repo.mark_reply", return_value=None)
        assert await handle_inbound_sms("1", "2", "hello") is None


class TestInboundWebhookRoute:
    async def test_webhook_acks_with_empty_plivo_xml(self, client, mocker):
        mark = mocker.patch("app.telephony.sms_email_repo.mark_reply", return_value=None)
        resp = await client.post(
            "/plivo/sms/inbound",
            data={"From": "918894441697", "To": "14155550100", "Text": "a@b.co", "MessageUUID": "m1"},
        )
        assert resp.status_code == 200
        assert "<Response>" in resp.text
        mark.assert_called_once_with("918894441697", "14155550100", "a@b.co", "a@b.co")


# ── Plivo HTTP ───────────────────────────────────────────────────────────────

def _fake_http(mocker, *, get=None, post=None):
    """httpx.AsyncClient stand-in: `get`/`post` are (status_code, json) tuples."""
    def _resp(pair):
        r = mocker.MagicMock()
        r.status_code, payload = pair
        r.json.return_value = payload
        r.text = json.dumps(payload)
        return r
    client = mocker.MagicMock()
    client.__aenter__.return_value = client
    client.get = AsyncMock(return_value=_resp(get or (200, {})))
    client.post = AsyncMock(return_value=_resp(post or (202, {})))
    mocker.patch("httpx.AsyncClient", return_value=client)
    return client


class TestSendPlivoSms:
    async def test_posts_digits_only_numbers_and_returns_uuid(self, mocker):
        from app.telephony.plivo.plivo_sms_service import send_plivo_sms
        http = _fake_http(mocker, post=(202, {"message_uuid": ["abc-123"]}))

        uuid, err = await send_plivo_sms("MAXXXX", "tok", "+1 415 555 0100", "+91 88944 41697", "hi")

        assert (uuid, err) == ("abc-123", None)
        url = http.post.await_args.args[0]
        body = http.post.await_args.kwargs["json"]
        assert url == "https://api.plivo.com/v1/Account/MAXXXX/Message/"
        assert body == {"src": "14155550100", "dst": "918894441697", "text": "hi"}
        assert http.post.await_args.kwargs["auth"] == ("MAXXXX", "tok")

    async def test_non_2xx_is_an_error_not_an_exception(self, mocker):
        from app.telephony.plivo.plivo_sms_service import send_plivo_sms
        _fake_http(mocker, post=(400, {"error": "invalid destination"}))
        uuid, err = await send_plivo_sms("MAXXXX", "tok", AGENT_NUMBER, CALLER_NUMBER, "hi")
        assert uuid is None and "400" in err

    async def test_missing_credentials_short_circuit(self, mocker):
        from app.telephony.plivo.plivo_sms_service import send_plivo_sms
        http = _fake_http(mocker)
        uuid, err = await send_plivo_sms("", "", AGENT_NUMBER, CALLER_NUMBER, "hi")
        assert uuid is None and err
        http.post.assert_not_awaited()


class TestWebhookConfigMessageUrl:
    async def test_existing_app_without_message_url_gets_one(self, mocker):
        from app.telephony.plivo.plivo_webhook_config import _find_or_create_app
        http = _fake_http(
            mocker,
            get=(200, {"objects": [{"app_id": "app-1", "answer_url": "https://h/plivo/voice", "message_url": None}]}),
            post=(202, {}),
        )
        app_id = await _find_or_create_app("MAXXXX", "tok", "https://h/plivo/voice", "https://h/plivo/sms/inbound")
        assert app_id == "app-1"
        # The second GET is _ensure_message_url re-reading the app; the fake
        # returns the same (no message_url) payload so it proceeds to update.
        update = http.post.await_args
        assert update.args[0] == "https://api.plivo.com/v1/Account/MAXXXX/Application/app-1/"
        assert update.kwargs["json"] == {"message_url": "https://h/plivo/sms/inbound", "message_method": "POST"}

    async def test_new_app_is_created_with_message_url(self, mocker):
        from app.telephony.plivo.plivo_webhook_config import _find_or_create_app
        http = _fake_http(mocker, get=(200, {"objects": []}), post=(201, {"app_id": "app-new"}))
        app_id = await _find_or_create_app("MAXXXX", "tok", "https://h/plivo/voice", "https://h/plivo/sms/inbound")
        assert app_id == "app-new"
        body = http.post.await_args.kwargs["json"]
        assert body["answer_url"] == "https://h/plivo/voice"
        assert body["message_url"] == "https://h/plivo/sms/inbound"
        assert body["message_method"] == "POST"


# ── end to end through /plivo/stream ─────────────────────────────────────────

def _wait_until(predicate, timeout=6.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _plivo_media_event() -> str:
    return json.dumps({
        "event": "media",
        "media": {"track": "inbound", "payload": base64.b64encode(bytes([0x00]) * 160).decode()},
    })


class _FakeTranscriber:
    def __init__(self, transcript):
        self._transcript = transcript

    async def start(self):
        pass

    async def send(self, pcm):
        pass

    async def finish(self):
        return self._transcript

    def get_last_partial(self):
        return self._transcript


def test_sms_email_capture_is_off_by_default():
    """Callers speak their email for now; the SMS path stays built but unused."""
    from app.telephony.sms_email_capture import SMS_EMAIL_CAPTURE_ENABLED
    assert SMS_EMAIL_CAPTURE_ENABLED is False


class TestPlivoStreamEmailByText:
    """Inbound Plivo call on a meeting-enabled agent: the caller asks to be
    texted, the model calls request_email_by_sms, the text goes out, the
    call carries on, the reply arrives while the call is still live and
    shows up as the caller's next turn without them saying anything."""

    def test_reply_lands_mid_call_as_a_caller_turn(self, mocker):
        mocker.patch("app.telephony.sms_email_capture.SMS_EMAIL_CAPTURE_ENABLED", True)
        agent = {
            "id": AGENT_ID, "user_id": USER_ID, "name": "Lumen",
            "system_prompt": "You book meetings.", "first_message_enabled": True,
            "first_message": "Hi, how can I help?", "barge_in_sensitivity": 0.2,
            "selected_kb_doc_ids": [], "kb_instructions": None, "hr_instructions": None,
            "meeting_booking_enabled": True, "meeting_duration_minutes": 30,
            "meeting_buffer_minutes": 0, "meeting_timezone": "UTC",
            "booking_hours_start": "09:00", "booking_hours_end": "17:00",
        }
        number_record = {"id": "n1", "user_id": USER_ID, "phone_number": AGENT_NUMBER,
                         "agent_id": AGENT_ID, "is_active": True, "plivo_account_id": None}

        mocker.patch("app.agents.agent_repo.get_agent", return_value=agent)
        mocker.patch("app.agents.agent_repo.get_agent_functions", return_value=[])
        mocker.patch("app.telephony.plivo.plivo_repo.get_number_by_phone", return_value=number_record)
        mocker.patch("app.telephony.plivo.user_plivo_settings_repo.resolve_account_for_number",
                     return_value={"auth_id": "MAXXXX", "auth_token": "tok"})
        mocker.patch("app.integrations.integrations_repo.get_integration", return_value={"refresh_token": "rt"})
        mocker.patch("app.telephony.shared.get_eligible_kb_documents", new=AsyncMock(return_value=[]))
        mocker.patch("app.admin.admin_config_service.get", return_value="cascade")
        mocker.patch("app.telephony.shared.finalize_call_outcome", new=AsyncMock(return_value=("completed", [])))

        conv_db = mocker.MagicMock()
        conv_db.table.return_value.insert.return_value.execute.return_value.data = [{"id": CONV_ID}]
        mocker.patch("app.core.database.get_supabase", return_value=conv_db)

        saved = []
        mocker.patch("app.conversations.message_repo.save_message",
                     side_effect=lambda conv, user, role, content: saved.append((role, content)) or {"id": "m"})
        mocker.patch("app.conversations.message_repo.get_session_messages", return_value=[])

        async def fake_tts(text):
            yield bytes([0xFF]) * 80
        mocker.patch("app.telephony.plivo.plivo_call_service.text_to_speech_plivo", side_effect=fake_tts)

        mocker.patch("app.voice.deepgram_service.get_streaming_transcriber",
                     return_value=_FakeTranscriber("can you just text me instead"))
        vad_calls = {"n": 0}

        async def fake_detect_speech(audio, sensitivity=0.5):
            vad_calls["n"] += 1
            return vad_calls["n"] <= 5
        mocker.patch("app.voice.vad_service.detect_speech", side_effect=fake_detect_speech)

        # DB side of the SMS flow
        mocker.patch("app.telephony.sms_email_repo.get_latest_for_conversation", return_value=None)
        create_request = mocker.patch("app.telephony.sms_email_repo.create_request", return_value={"id": "r1"})
        reply_row = {"id": "r1", "conversation_id": CONV_ID,
                     "reply_text": "my email is anish@example.com", "email": "anish@example.com"}
        mocker.patch("app.telephony.sms_email_repo.mark_reply", return_value=reply_row)

        # The "carrier": sending the text schedules the caller's reply to hit
        # the inbound webhook handler a moment later, on the call's own loop.
        sms_sent = []

        async def fake_send_plivo_sms(auth_id, auth_token, src, dst, text):
            sms_sent.append((auth_id, auth_token, src, dst, text))

            async def _caller_replies():
                await asyncio.sleep(0.2)
                from app.telephony.sms_email_capture import handle_inbound_sms
                await handle_inbound_sms("918894441697", "14155550100", "my email is anish@example.com")
            asyncio.get_running_loop().create_task(_caller_replies())
            return "uuid-1", None
        mocker.patch("app.telephony.plivo.plivo_sms_service.send_plivo_sms", side_effect=fake_send_plivo_sms)

        llm_turns = []

        async def fake_detect_tool_call(conv_messages, system, tools):
            names = [t["function"]["name"] for t in tools]
            llm_turns.append((conv_messages[-1], names))
            n = len(llm_turns)
            if n == 1:
                assert "request_email_by_sms" in names and "check_sms_reply" in names
                assert "book_meeting" in names
                assert "EMAIL BY TEXT MESSAGE" in system
                return (None, "request_email_by_sms", {}, "call-1")
            if n == 2:
                assert conv_messages[-1]["role"] == "tool"
                assert "text message was sent" in conv_messages[-1]["content"]
                return ("A text is on its way, reply whenever you can.", None, {}, None)
            return ("Got it, thanks.", None, {}, None)
        mocker.patch("app.voice.llm_service.detect_tool_call", side_effect=fake_detect_tool_call)

        from app.main import app
        client = TestClient(app)
        with client.websocket_connect(f"/plivo/stream?to=%2B14155550100&from=%2B918894441697") as ws:
            ws.send_text(json.dumps({"event": "start", "start": {"streamId": "s1", "mediaFormat": {}}}))
            assert _wait_until(lambda: len(saved) >= 1), "greeting never saved"
            for _ in range(40):
                ws.send_text(_plivo_media_event())
            assert _wait_until(lambda: len(saved) >= 5), f"expected 5 saved turns, got {saved}"

        roles = [r for r, _ in saved]
        assert roles == ["assistant", "user", "assistant", "user", "assistant"]
        assert saved[1][1] == "can you just text me instead"
        assert saved[3][1] == "[Replied by text message] my email is anish@example.com"
        assert sms_sent and sms_sent[0][:4] == ("MAXXXX", "tok", AGENT_NUMBER, CALLER_NUMBER)
        create_request.assert_called_once()
        # The reply's turn reached the model as a normal caller message
        third_user_msg = llm_turns[2][0]
        assert third_user_msg["role"] == "user" and "anish@example.com" in third_user_msg["content"]
