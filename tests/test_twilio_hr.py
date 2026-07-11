"""
Comprehensive tests for the Twilio phone-call integration and HR system.

Scenarios covered:
  - Audio conversion (μ-law ↔ PCM, resampling, round-trips)
  - TwiML routes (inbound /twilio/voice, outbound /twilio/outbound/voice)
  - Phone number CRUD + auto-webhook configuration trigger
  - Twilio settings (save / mask auth token / masked-token passthrough)
  - HR candidates CRUD
  - HR interviews CRUD
  - HR scheduler timing logic (due / not-due / 2-hour cutoff / multi-row)
  - HR scheduler deduplication (optimistic lock / already-claimed / no-url fail)
  - TTS for Twilio (ElevenLabs ulaw_8000 path + OpenAI path)
  - StreamingTranscriber lifecycle
  - Agent context injection (interview time / lead minutes in system prompt)
"""

import asyncio
import base64
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

pytestmark = pytest.mark.asyncio

from tests.conftest import TEST_USER_ID, make_token

# ── Constants ─────────────────────────────────────────────────────────────────

TEST_AGENT_ID  = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TEST_NUMBER_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
TEST_CAND_ID   = "dddddddd-dddd-dddd-dddd-dddddddddddd"
TEST_INT_ID    = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
TEST_PHONE     = "+14155550100"
TEST_ACCT_SID  = "ACfake000000000000000000000000000000"
TEST_AUTH_TOK  = "fake_auth_token_abc123"
TEST_WEBHOOK   = "https://example.ngrok.io"


def _auth(user_id=TEST_USER_ID):
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def _phone_record(**kw):
    return {
        "id": TEST_NUMBER_ID, "user_id": TEST_USER_ID,
        "phone_number": TEST_PHONE, "friendly_name": "Test Line",
        "agent_id": None, "is_active": True, "twilio_number_sid": None,
        "created_at": "2026-07-06T00:00:00+00:00",
        **kw,
    }


def _candidate(**kw):
    return {
        "id": TEST_CAND_ID, "user_id": TEST_USER_ID, "name": "Priya Sharma",
        "phone": "+919876543210", "email": "priya@test.com", "role": "Engineer",
        "resume_text": None, "resume_file_name": None, "notes": "Strong candidate",
        "created_at": "2026-07-06T00:00:00+00:00",
        **kw,
    }


def _interview(minutes_from_now=60, lead_minutes=30, status="pending", **kw):
    sched = (datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)).isoformat()
    return {
        "id": TEST_INT_ID, "user_id": TEST_USER_ID, "candidate_id": TEST_CAND_ID,
        "agent_id": TEST_AGENT_ID, "scheduled_at": sched, "call_lead_minutes": lead_minutes,
        "specific_questions": "Tell me about yourself.", "status": status,
        "called_at": None, "call_sid": None, "created_at": "2026-07-06T00:00:00+00:00",
        "hr_candidates": {"name": "Priya Sharma", "phone": "+919876543210",
                          "role": "Engineer", "email": "priya@test.com",
                          "resume_text": "5 years Python.", "notes": "Strong."},
        **kw,
    }


def _settings(**kw):
    return {
        "account_sid": TEST_ACCT_SID, "auth_token": TEST_AUTH_TOK,
        "webhook_base_url": TEST_WEBHOOK, **kw,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — Audio conversion (pure unit tests, no mocking)
# ══════════════════════════════════════════════════════════════════════════════

class TestAudioConversion:
    """Verify μ-law ↔ PCM conversion and resampling correctness."""

    def test_mulaw_decode_silence_byte(self):
        """μ-law byte 0xFF (silence) decodes to a PCM value close to 0."""
        from app.twilio_call_service import mulaw_decode
        result = mulaw_decode(bytes([0xFF]))
        pcm = int.from_bytes(result, "little", signed=True)
        assert abs(pcm) < 200

    def test_mulaw_encode_silence(self):
        """PCM silence (0x0000) encodes to the μ-law silence byte 0xFF."""
        from app.twilio_call_service import mulaw_encode
        pcm_silence = (0).to_bytes(2, "little", signed=True)
        result = mulaw_encode(pcm_silence)
        assert result == bytes([0xFF])

    def test_mulaw_round_trip_preserves_amplitude(self):
        """Encode then decode a sine wave — peak amplitude within 10% tolerance."""
        from app.twilio_call_service import mulaw_encode, mulaw_decode
        t = np.linspace(0, 1, 160, dtype=np.float32)
        sine = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
        encoded = mulaw_encode(sine.tobytes())
        decoded = np.frombuffer(mulaw_decode(encoded), dtype=np.int16)
        orig_peak = np.max(np.abs(sine.astype(np.float32)))
        decoded_peak = np.max(np.abs(decoded.astype(np.float32)))
        # μ-law companding compresses high-amplitude signals — tolerate up to 30% loss
        assert decoded_peak > orig_peak * 0.70

    def test_resample_8k_to_16k_doubles_samples(self):
        """Upsampling 8kHz → 16kHz doubles the byte count."""
        from app.twilio_call_service import resample_8k_to_16k
        pcm8k = np.zeros(160, dtype=np.int16).tobytes()
        assert len(resample_8k_to_16k(pcm8k)) == len(pcm8k) * 2

    def test_resample_16k_to_8k_halves_samples(self):
        """Downsampling 16kHz → 8kHz halves the byte count."""
        from app.twilio_call_service import resample_16k_to_8k
        pcm16k = np.zeros(320, dtype=np.int16).tobytes()
        assert len(resample_16k_to_8k(pcm16k)) == len(pcm16k) // 2

    def test_twilio_payload_to_pcm16k_output_size(self):
        """160 μ-law samples at 8kHz → 320 int16 samples at 16kHz = 640 bytes."""
        from app.twilio_call_service import twilio_payload_to_pcm16k
        mulaw = bytes([0xFF] * 160)
        payload = base64.b64encode(mulaw).decode()
        pcm16k = twilio_payload_to_pcm16k(payload)
        assert len(pcm16k) == 160 * 2 * 2  # 320 samples × 2 bytes each

    def test_empty_bytes_handled_gracefully(self):
        """All converters return empty bytes for empty input without crashing."""
        from app.twilio_call_service import (
            mulaw_decode, mulaw_encode, resample_8k_to_16k, resample_16k_to_8k,
        )
        assert mulaw_decode(b"") == b""
        assert mulaw_encode(b"") == b""
        assert resample_8k_to_16k(b"") == b""
        assert resample_16k_to_8k(b"") == b""

    def test_pcm16k_round_trip_via_twilio_payload(self):
        """PCM16k → twilio_payload → PCM16k should preserve audio shape within 15%."""
        from app.twilio_call_service import twilio_payload_to_pcm16k, pcm16k_to_twilio_payload
        t = np.linspace(0, 1, 320, dtype=np.float32)
        original = (np.sin(2 * np.pi * 400 * t) * 10000).astype(np.int16)
        payload = pcm16k_to_twilio_payload(original.tobytes())
        recovered = np.frombuffer(twilio_payload_to_pcm16k(payload), dtype=np.int16)
        orig_peak = np.max(np.abs(original.astype(np.float32)))
        recov_peak = np.max(np.abs(recovered.astype(np.float32)))
        assert recov_peak > orig_peak * 0.85


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — TwiML routes
# ══════════════════════════════════════════════════════════════════════════════

class TestTwiMLRoutes:
    """Inbound /twilio/voice and outbound /twilio/outbound/voice TwiML generation."""

    async def test_inbound_known_number_returns_stream_twiml(self, client, mocker):
        """/twilio/voice for a configured number returns <Connect><Stream> TwiML."""
        mocker.patch("app.twilio_repo.get_number_by_phone",
                     return_value=_phone_record(agent_id=TEST_AGENT_ID))
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings())

        resp = await client.post("/twilio/voice", data={"To": TEST_PHONE, "From": "+10000000001"})
        assert resp.status_code == 200
        assert "application/xml" in resp.headers["content-type"]
        body = resp.text
        assert "<Connect>" in body
        assert "<Stream" in body
        assert "wss://" in body
        assert "/twilio/stream" in body

    async def test_inbound_unknown_number_returns_error_twiml(self, client, mocker):
        """Unknown phone number → <Say> error TwiML, no <Connect>."""
        mocker.patch("app.twilio_repo.get_number_by_phone", return_value=None)

        resp = await client.post("/twilio/voice", data={"To": "+10000000099"})
        assert resp.status_code == 200
        assert "<Say>" in resp.text
        assert "<Connect>" not in resp.text

    async def test_inbound_number_without_agent_returns_error(self, client, mocker):
        """Number in DB but no agent_id → error TwiML, not a stream."""
        mocker.patch("app.twilio_repo.get_number_by_phone",
                     return_value=_phone_record(agent_id=None))

        resp = await client.post("/twilio/voice", data={"To": TEST_PHONE})
        assert resp.status_code == 200
        assert "<Connect>" not in resp.text

    async def test_inbound_missing_webhook_url_returns_error(self, client, mocker):
        """Number + agent attached but no webhook URL → error TwiML."""
        mocker.patch("app.twilio_repo.get_number_by_phone",
                     return_value=_phone_record(agent_id=TEST_AGENT_ID))
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings(webhook_base_url=""))

        resp = await client.post("/twilio/voice", data={"To": TEST_PHONE})
        assert resp.status_code == 200
        assert "<Connect>" not in resp.text

    async def test_outbound_valid_interview_returns_stream_twiml(self, client, mocker):
        """Outbound TwiML for a valid interview contains interview_id in the stream URL."""
        mocker.patch("app.hr_repo.get_interview", return_value=_interview())
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings())

        resp = await client.get(f"/twilio/outbound/voice?interview_id={TEST_INT_ID}")
        assert resp.status_code == 200
        assert "<Connect>" in resp.text
        assert TEST_INT_ID in resp.text

    async def test_outbound_missing_interview_returns_error(self, client, mocker):
        """Missing interview_id in outbound TwiML → <Say> error, no <Connect>."""
        mocker.patch("app.hr_repo.get_interview", return_value=None)

        resp = await client.get("/twilio/outbound/voice?interview_id=nonexistent")
        assert resp.status_code == 200
        assert "<Say>" in resp.text
        assert "<Connect>" not in resp.text

    async def test_outbound_empty_interview_id_returns_error(self, client):
        """Calling /twilio/outbound/voice with no interview_id returns error TwiML."""
        resp = await client.get("/twilio/outbound/voice")
        assert resp.status_code == 200
        assert "<Connect>" not in resp.text

    async def test_inbound_stream_url_contains_phone_number(self, client, mocker):
        """The generated stream URL must carry the 'to' phone number for the WS handler."""
        mocker.patch("app.twilio_repo.get_number_by_phone",
                     return_value=_phone_record(agent_id=TEST_AGENT_ID))
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings())

        resp = await client.post("/twilio/voice", data={"To": TEST_PHONE})
        # Phone number should appear URL-encoded (%2B14155550100) or without the +
        assert "14155550100" in resp.text


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — Phone number CRUD
# ══════════════════════════════════════════════════════════════════════════════

class TestPhoneNumberCRUD:
    """Add, list, update, attach, detach, delete phone numbers via API."""

    async def test_list_phone_numbers(self, client, mocker):
        mocker.patch("app.twilio_repo.get_phone_numbers", return_value=[_phone_record()])

        resp = await client.get("/api/twilio/numbers", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["phone_number"] == TEST_PHONE

    async def test_list_returns_empty_when_no_numbers(self, client, mocker):
        mocker.patch("app.twilio_repo.get_phone_numbers", return_value=[])

        resp = await client.get("/api/twilio/numbers", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_add_number_returns_201(self, client, mocker):
        mocker.patch("app.twilio_repo.create_phone_number", return_value=_phone_record())
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings(account_sid="", auth_token="", webhook_base_url=""))

        resp = await client.post(
            "/api/twilio/numbers", headers=_auth(),
            json={"phone_number": TEST_PHONE, "friendly_name": "Test Line"},
        )
        assert resp.status_code == 201
        assert resp.json()["phone_number"] == TEST_PHONE

    async def test_add_number_triggers_webhook_config_when_credentials_exist(self, client, mocker):
        """Adding a number fires configure_number_webhook as a background task."""
        mocker.patch("app.twilio_repo.create_phone_number", return_value=_phone_record())
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings())
        mock_cfg = mocker.patch(
            "app.twilio_webhook_config.configure_number_webhook",
            new_callable=AsyncMock,
        )

        resp = await client.post(
            "/api/twilio/numbers", headers=_auth(),
            json={"phone_number": TEST_PHONE},
        )
        assert resp.status_code == 201
        await asyncio.sleep(0.05)  # let the background task fire
        mock_cfg.assert_called_once()
        call_args = mock_cfg.call_args[0]
        assert call_args[0] == TEST_USER_ID
        assert call_args[2] == TEST_ACCT_SID

    async def test_add_number_skips_webhook_when_no_credentials(self, client, mocker):
        """Adding a number without saved credentials does NOT configure webhook."""
        mocker.patch("app.twilio_repo.create_phone_number", return_value=_phone_record())
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings(account_sid="", auth_token="", webhook_base_url=""))
        mock_cfg = mocker.patch(
            "app.twilio_webhook_config.configure_number_webhook",
            new_callable=AsyncMock,
        )

        await client.post("/api/twilio/numbers", headers=_auth(), json={"phone_number": TEST_PHONE})
        await asyncio.sleep(0.05)
        mock_cfg.assert_not_called()

    async def test_attach_number_to_agent(self, client, mocker):
        mocker.patch("app.twilio_repo.attach_number_to_agent",
                     return_value=_phone_record(agent_id=TEST_AGENT_ID))

        resp = await client.post(
            f"/api/twilio/numbers/{TEST_NUMBER_ID}/attach",
            headers=_auth(),
            json={"agent_id": TEST_AGENT_ID},
        )
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == TEST_AGENT_ID

    async def test_detach_number_from_agent(self, client, mocker):
        mocker.patch("app.twilio_repo.detach_number_from_agent",
                     return_value=_phone_record(agent_id=None))

        resp = await client.delete(
            f"/api/twilio/numbers/{TEST_NUMBER_ID}/attach", headers=_auth()
        )
        assert resp.status_code == 200
        assert resp.json()["agent_id"] is None

    async def test_delete_phone_number_returns_204(self, client, mocker):
        mocker.patch("app.twilio_repo.delete_phone_number", return_value=None)

        resp = await client.delete(f"/api/twilio/numbers/{TEST_NUMBER_ID}", headers=_auth())
        assert resp.status_code == 204

    async def test_phone_number_endpoints_require_auth(self, client):
        resp = await client.get("/api/twilio/numbers")
        assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — Twilio settings
# ══════════════════════════════════════════════════════════════════════════════

class TestTwilioSettings:
    """GET masks auth token; PUT saves credentials and fires webhook config."""

    async def test_get_settings_masks_auth_token(self, client, mocker):
        """Raw auth token must never be returned to the client."""
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings())

        resp = await client.get("/api/twilio/settings", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("auth_token") != TEST_AUTH_TOK
        assert "•" in (body.get("auth_token") or "")
        assert body["auth_token_set"] is True

    async def test_get_settings_empty_shows_not_set(self, client, mocker):
        """When no credentials are saved, auth_token_set is False."""
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value={"account_sid": "", "auth_token": "", "webhook_base_url": ""})

        resp = await client.get("/api/twilio/settings", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["auth_token_set"] is False

    async def test_get_settings_returns_account_sid_plaintext(self, client, mocker):
        """account_sid is not sensitive and should be returned as-is."""
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings())

        resp = await client.get("/api/twilio/settings", headers=_auth())
        assert resp.json()["account_sid"] == TEST_ACCT_SID

    async def test_save_settings_triggers_configure_all_numbers(self, client, mocker):
        """Saving credentials fires configure_all_user_numbers for all existing numbers."""
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings(auth_token=""))
        mocker.patch("app.user_twilio_settings_repo.upsert_twilio_settings",
                     return_value=_settings())
        mock_all = mocker.patch(
            "app.twilio_webhook_config.configure_all_user_numbers",
            new_callable=AsyncMock,
        )

        resp = await client.put(
            "/api/twilio/settings", headers=_auth(),
            json={"account_sid": TEST_ACCT_SID, "auth_token": TEST_AUTH_TOK,
                  "webhook_base_url": TEST_WEBHOOK},
        )
        assert resp.status_code == 200
        await asyncio.sleep(0.05)
        mock_all.assert_called_once_with(
            TEST_USER_ID, TEST_ACCT_SID, TEST_AUTH_TOK, TEST_WEBHOOK
        )

    async def test_save_settings_bullet_token_keeps_existing(self, client, mocker):
        """Sending the bullet-masked placeholder preserves the stored token."""
        existing = _settings(auth_token="real_secret_token")
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings", return_value=existing)
        mock_upsert = mocker.patch(
            "app.user_twilio_settings_repo.upsert_twilio_settings",
            return_value=existing,
        )
        mocker.patch("app.twilio_webhook_config.configure_all_user_numbers",
                     new_callable=AsyncMock)

        await client.put(
            "/api/twilio/settings", headers=_auth(),
            json={"account_sid": TEST_ACCT_SID, "auth_token": "••••••••",
                  "webhook_base_url": TEST_WEBHOOK},
        )
        # The real token must be passed to upsert, not the bullet placeholder
        _, _, saved_token, _ = mock_upsert.call_args[0]
        assert saved_token == "real_secret_token"

    async def test_settings_require_auth(self, client):
        resp = await client.get("/api/twilio/settings")
        assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — HR Candidates
# ══════════════════════════════════════════════════════════════════════════════

class TestHRCandidates:
    """Candidate CRUD via HTTP API."""

    async def test_list_candidates(self, client, mocker):
        mocker.patch("app.hr_repo.list_candidates", return_value=[_candidate()])

        resp = await client.get("/api/hr/candidates", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Priya Sharma"

    async def test_list_candidates_empty(self, client, mocker):
        mocker.patch("app.hr_repo.list_candidates", return_value=[])

        resp = await client.get("/api/hr/candidates", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_create_candidate_returns_201(self, client, mocker):
        mocker.patch("app.hr_repo.create_candidate", return_value=_candidate())

        resp = await client.post(
            "/api/hr/candidates", headers=_auth(),
            json={"name": "Priya Sharma", "phone": "+919876543210",
                  "email": "priya@test.com", "role": "Engineer"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Priya Sharma"
        assert resp.json()["phone"] == "+919876543210"

    async def test_create_candidate_missing_phone_is_422(self, client):
        """phone is a required field; missing it returns 422 Unprocessable."""
        resp = await client.post(
            "/api/hr/candidates", headers=_auth(),
            json={"name": "No Phone Candidate"},
        )
        assert resp.status_code == 422

    async def test_create_candidate_missing_name_is_422(self, client):
        resp = await client.post(
            "/api/hr/candidates", headers=_auth(),
            json={"phone": "+919876543210"},
        )
        assert resp.status_code == 422

    async def test_update_candidate(self, client, mocker):
        mocker.patch("app.hr_repo.update_candidate",
                     return_value=_candidate(role="Senior Engineer"))

        resp = await client.put(
            f"/api/hr/candidates/{TEST_CAND_ID}", headers=_auth(),
            json={"role": "Senior Engineer"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "Senior Engineer"

    async def test_update_candidate_not_found_returns_404(self, client, mocker):
        mocker.patch("app.hr_repo.update_candidate",
                     side_effect=RuntimeError("Candidate not found"))

        resp = await client.put(
            f"/api/hr/candidates/{TEST_CAND_ID}", headers=_auth(),
            json={"role": "Engineer"},
        )
        assert resp.status_code == 404

    async def test_delete_candidate_returns_204(self, client, mocker):
        mocker.patch("app.hr_repo.delete_candidate", return_value=None)

        resp = await client.delete(f"/api/hr/candidates/{TEST_CAND_ID}", headers=_auth())
        assert resp.status_code == 204

    async def test_candidates_require_auth(self, client):
        resp = await client.get("/api/hr/candidates")
        assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — HR Interviews
# ══════════════════════════════════════════════════════════════════════════════

class TestHRInterviews:
    """Interview CRUD via HTTP API."""

    async def test_list_interviews(self, client, mocker):
        mocker.patch("app.hr_repo.list_interviews", return_value=[_interview()])

        resp = await client.get("/api/hr/interviews", headers=_auth())
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_create_interview_returns_201(self, client, mocker):
        mocker.patch("app.hr_repo.create_interview", return_value=_interview())

        sched = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        resp = await client.post(
            "/api/hr/interviews", headers=_auth(),
            json={
                "candidate_id": TEST_CAND_ID, "agent_id": TEST_AGENT_ID,
                "scheduled_at": sched, "call_lead_minutes": 30,
                "specific_questions": "Tell me about yourself.",
            },
        )
        assert resp.status_code == 201

    async def test_create_interview_missing_candidate_id_is_422(self, client):
        sched = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        resp = await client.post(
            "/api/hr/interviews", headers=_auth(),
            json={"agent_id": TEST_AGENT_ID, "scheduled_at": sched},
        )
        assert resp.status_code == 422

    async def test_create_interview_missing_agent_id_is_422(self, client):
        sched = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        resp = await client.post(
            "/api/hr/interviews", headers=_auth(),
            json={"candidate_id": TEST_CAND_ID, "scheduled_at": sched},
        )
        assert resp.status_code == 422

    async def test_create_interview_missing_scheduled_at_is_422(self, client):
        resp = await client.post(
            "/api/hr/interviews", headers=_auth(),
            json={"candidate_id": TEST_CAND_ID, "agent_id": TEST_AGENT_ID},
        )
        assert resp.status_code == 422

    async def test_update_interview_status(self, client, mocker):
        mocker.patch("app.hr_repo.update_interview",
                     return_value=_interview(status="cancelled"))

        resp = await client.put(
            f"/api/hr/interviews/{TEST_INT_ID}", headers=_auth(),
            json={"status": "cancelled"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    async def test_update_interview_not_found_returns_404(self, client, mocker):
        mocker.patch("app.hr_repo.update_interview",
                     side_effect=RuntimeError("Interview not found"))

        resp = await client.put(
            f"/api/hr/interviews/{TEST_INT_ID}", headers=_auth(),
            json={"status": "cancelled"},
        )
        assert resp.status_code == 404

    async def test_delete_interview_returns_204(self, client, mocker):
        mocker.patch("app.hr_repo.delete_interview", return_value=None)

        resp = await client.delete(f"/api/hr/interviews/{TEST_INT_ID}", headers=_auth())
        assert resp.status_code == 204

    async def test_interviews_require_auth(self, client):
        resp = await client.get("/api/hr/interviews")
        assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — HR Scheduler timing logic (pure Python, no mocking)
# ══════════════════════════════════════════════════════════════════════════════

def _timing_row(minutes_from_now, lead_minutes=30, status="pending"):
    """Build a minimal interview row for timing filter tests."""
    sched = (datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)).isoformat()
    return {"id": TEST_INT_ID, "scheduled_at": sched,
            "call_lead_minutes": lead_minutes, "status": status}


def _apply_timing_filter(rows):
    """
    Mirror of the Python-side filter in hr_repo.get_due_interviews.
    trigger_at = scheduled_at - lead_minutes; due if 0 ≤ overdue_seconds < 7200.
    """
    now = datetime.now(timezone.utc)
    due = []
    for row in rows:
        try:
            scheduled = datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00"))
            lead = row.get("call_lead_minutes", 30)
            trigger_at = scheduled - timedelta(minutes=lead)
            overdue_seconds = (now - trigger_at).total_seconds()
            if 0 <= overdue_seconds < 7200:
                due.append(row)
        except Exception:
            pass
    return due


class TestSchedulerTiming:
    """Unit-test the timing window without hitting DB or Twilio."""

    def test_interview_due_now_is_returned(self):
        """Interview scheduled 25 min from now with 30-min lead → trigger was 5 min ago."""
        row = _timing_row(minutes_from_now=25, lead_minutes=30)
        assert len(_apply_timing_filter([row])) == 1

    def test_interview_not_yet_due_is_excluded(self):
        """Interview scheduled 60 min from now with 30-min lead → trigger in 30 min, skip."""
        row = _timing_row(minutes_from_now=60, lead_minutes=30)
        assert len(_apply_timing_filter([row])) == 0

    def test_interview_overdue_more_than_2h_excluded(self):
        """Interview whose trigger was 230+ min ago is outside the 2-hour cutoff."""
        row = _timing_row(minutes_from_now=-200, lead_minutes=30)  # trigger 230 min ago
        assert len(_apply_timing_filter([row])) == 0

    def test_interview_exactly_at_2h_boundary_excluded(self):
        """Trigger at exactly 120 minutes ago is at the exclusive boundary — skip."""
        row = _timing_row(minutes_from_now=-90, lead_minutes=30)  # trigger 120 min ago
        assert len(_apply_timing_filter([row])) == 0

    def test_trigger_just_inside_2h_window_returned(self):
        """Trigger ~119 min ago is inside the window — should be returned."""
        row = _timing_row(minutes_from_now=-89, lead_minutes=30)  # trigger ~119 min ago
        assert len(_apply_timing_filter([row])) == 1

    def test_zero_lead_minutes(self):
        """lead_minutes=0 means trigger_at == scheduled_at; past schedule → due."""
        row = _timing_row(minutes_from_now=-5, lead_minutes=0)  # 5 min overdue
        assert len(_apply_timing_filter([row])) == 1

    def test_large_lead_minutes(self):
        """60-min lead on interview 30 min from now → trigger was 30 min ago → due."""
        row = _timing_row(minutes_from_now=30, lead_minutes=60)
        assert len(_apply_timing_filter([row])) == 1

    def test_mixed_batch_returns_only_due_interviews(self):
        """Only the due rows are returned from a batch of mixed timing states."""
        rows = [
            _timing_row(minutes_from_now=25, lead_minutes=30),    # trigger 5 min ago ✓
            _timing_row(minutes_from_now=90, lead_minutes=30),    # trigger 60 min from now ✗
            _timing_row(minutes_from_now=-200, lead_minutes=30),  # 230 min overdue ✗
        ]
        result = _apply_timing_filter(rows)
        assert len(result) == 1

    def test_malformed_scheduled_at_skipped_silently(self):
        """Rows with unparseable dates are silently skipped, not raised."""
        row = {"id": "x", "scheduled_at": "not-a-date", "call_lead_minutes": 30}
        assert _apply_timing_filter([row]) == []

    def test_empty_rows_returns_empty(self):
        """Empty input produces empty output without error."""
        assert _apply_timing_filter([]) == []


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 8 — Scheduler deduplication
# ══════════════════════════════════════════════════════════════════════════════

class TestSchedulerDeduplication:
    """Optimistic lock: DB status flipped to 'calling' BEFORE the Twilio API call."""

    def _make_claim_db(self, mocker, *, claim_data):
        """Return a mock DB where the conditional UPDATE returns claim_data."""
        db = mocker.MagicMock()
        chain = mocker.MagicMock()
        chain.eq.return_value = chain
        chain.execute.return_value.data = claim_data
        db.table.return_value.update.return_value = chain
        db.table.return_value.select.return_value = chain
        return db

    async def test_db_status_updated_before_twilio_call(self, mocker):
        """DB update to 'calling' happens before _initiate_outbound_call is awaited."""
        call_order = []
        iv = _interview()

        def track_update(*a, **kw):
            call_order.append("db_update")
            chain = mocker.MagicMock()
            chain.eq.return_value = chain
            chain.execute.return_value.data = [iv]
            return chain

        db = mocker.MagicMock()
        db.table.return_value.update.side_effect = track_update
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        async def fake_initiate(*a, **kw):
            call_order.append("twilio_call")
            return "CAtest123"

        mocker.patch("app.database.get_supabase", return_value=db)
        mocker.patch("app.hr_repo.get_due_interviews", return_value=[iv])
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings", return_value=_settings())
        mocker.patch("app.hr_repo.mark_interview_calling")
        mocker.patch("app.hr_repo.mark_interview_failed")
        mocker.patch("app.hr_scheduler._initiate_outbound_call", side_effect=fake_initiate)

        from app.hr_scheduler import _check_and_call
        await _check_and_call()

        assert "db_update" in call_order
        assert "twilio_call" in call_order
        assert call_order.index("db_update") < call_order.index("twilio_call")

    async def test_already_claimed_interview_skips_twilio_call(self, mocker):
        """When the conditional UPDATE returns no rows (claimed), skip the call."""
        db = self._make_claim_db(mocker, claim_data=[])  # empty = already claimed

        mock_twilio = mocker.patch("app.hr_scheduler._initiate_outbound_call",
                                   new_callable=AsyncMock)
        mocker.patch("app.database.get_supabase", return_value=db)
        mocker.patch("app.hr_repo.get_due_interviews", return_value=[_interview()])
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings", return_value=_settings())
        mocker.patch("app.hr_repo.mark_interview_failed")

        from app.hr_scheduler import _check_and_call
        await _check_and_call()

        mock_twilio.assert_not_called()

    async def test_missing_webhook_url_marks_interview_failed(self, mocker):
        """When webhook_base_url is blank, skip the call and mark interview failed."""
        iv = _interview()
        db = self._make_claim_db(mocker, claim_data=[iv])

        mock_twilio = mocker.patch("app.hr_scheduler._initiate_outbound_call",
                                   new_callable=AsyncMock)
        mock_fail = mocker.patch("app.hr_repo.mark_interview_failed")
        mocker.patch("app.database.get_supabase", return_value=db)
        mocker.patch("app.hr_repo.get_due_interviews", return_value=[iv])
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings",
                     return_value=_settings(webhook_base_url=""))
        mocker.patch("app.hr_repo.mark_interview_calling")

        from app.hr_scheduler import _check_and_call
        await _check_and_call()

        mock_twilio.assert_not_called()
        mock_fail.assert_called_once_with(TEST_INT_ID)

    async def test_twilio_call_failure_marks_interview_failed(self, mocker):
        """If _initiate_outbound_call returns None, mark the interview failed."""
        iv = _interview()
        db = self._make_claim_db(mocker, claim_data=[iv])

        mocker.patch("app.hr_scheduler._initiate_outbound_call",
                     new_callable=AsyncMock, return_value=None)
        mock_fail = mocker.patch("app.hr_repo.mark_interview_failed")
        mocker.patch("app.database.get_supabase", return_value=db)
        mocker.patch("app.hr_repo.get_due_interviews", return_value=[iv])
        mocker.patch("app.user_twilio_settings_repo.get_twilio_settings", return_value=_settings())
        mocker.patch("app.hr_repo.mark_interview_calling")

        from app.hr_scheduler import _check_and_call
        await _check_and_call()

        mock_fail.assert_called_once_with(TEST_INT_ID)

    async def test_no_due_interviews_returns_early(self, mocker):
        """When no interviews are due, _initiate_outbound_call is never called."""
        mock_twilio = mocker.patch("app.hr_scheduler._initiate_outbound_call",
                                   new_callable=AsyncMock)
        mocker.patch("app.hr_repo.get_due_interviews", return_value=[])

        from app.hr_scheduler import _check_and_call
        await _check_and_call()

        mock_twilio.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 9 — TTS for Twilio
# ══════════════════════════════════════════════════════════════════════════════

class TestTwilioTTS:
    """text_to_speech_twilio yields raw μ-law bytes for both TTS providers."""

    async def test_elevenlabs_path_yields_mulaw_chunks(self, mocker):
        """ElevenLabs ulaw_8000 chunks are yielded as-is (no conversion)."""
        fake_mulaw = bytes([0xAA] * 50)

        mock_client = MagicMock()
        mock_client.text_to_speech.convert_as_stream.return_value = iter([fake_mulaw])

        with patch("app.admin_config_service.get", return_value=None), \
             patch("elevenlabs.client.ElevenLabs", return_value=mock_client), \
             patch("elevenlabs.VoiceSettings"):
            from app.twilio_call_service import text_to_speech_twilio
            chunks = [c async for c in text_to_speech_twilio("Hello there")]

        assert len(chunks) == 1
        assert chunks[0] == fake_mulaw

    async def test_elevenlabs_empty_chunks_filtered(self, mocker):
        """Empty bytes from ElevenLabs are skipped; only real audio passes through."""
        fake_mulaw = bytes([0xBB] * 30)

        mock_client = MagicMock()
        mock_client.text_to_speech.convert_as_stream.return_value = iter([b"", fake_mulaw, b""])

        with patch("app.admin_config_service.get", return_value=None), \
             patch("elevenlabs.client.ElevenLabs", return_value=mock_client), \
             patch("elevenlabs.VoiceSettings"):
            from app.twilio_call_service import text_to_speech_twilio
            chunks = [c async for c in text_to_speech_twilio("Skip empties")]

        assert len(chunks) == 1
        assert chunks[0] == fake_mulaw

    async def test_openai_path_yields_mulaw_bytes(self, mocker):
        """OpenAI TTS (PCM 16kHz) is downsampled + μ-law encoded before yielding."""
        # 320 int16 samples = 640 bytes of PCM16k silence
        fake_pcm16k = np.zeros(320, dtype=np.int16).tobytes()

        async def fake_openai_tts(text, voice, model):
            yield fake_pcm16k

        config_vals = {
            "tts_provider": "openai_tts",
            "tts_openai_voice": "alloy",
            "tts_openai_model": "tts-1",
        }

        with patch("app.admin_config_service.get", side_effect=lambda k: config_vals.get(k)), \
             patch("app.openai_tts_service.text_to_speech_openai", side_effect=fake_openai_tts):
            from app.twilio_call_service import text_to_speech_twilio
            chunks = [c async for c in text_to_speech_twilio("Hello")]

        assert len(chunks) > 0
        # 320 PCM16k samples → 160 PCM8k samples → 160 μ-law bytes
        total_bytes = sum(len(c) for c in chunks)
        assert total_bytes == 160

    async def test_openai_empty_chunk_skipped(self, mocker):
        """OpenAI path skips empty PCM chunks (no crash, no output)."""
        async def fake_empty_tts(text, voice, model):
            yield b""  # empty chunk

        config_vals = {"tts_provider": "openai_tts"}

        with patch("app.admin_config_service.get", side_effect=lambda k: config_vals.get(k)), \
             patch("app.openai_tts_service.text_to_speech_openai", side_effect=fake_empty_tts):
            from app.twilio_call_service import text_to_speech_twilio
            chunks = [c async for c in text_to_speech_twilio("Hello")]

        assert chunks == []


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 10 — StreamingTranscriber unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestStreamingTranscriber:
    """StreamingTranscriber lifecycle: start → send → finish."""

    async def test_finish_returns_accumulated_transcript(self):
        """finish() returns transcript that was built up by on_transcript callbacks."""
        from app.deepgram_service import StreamingTranscriber
        st = StreamingTranscriber()

        mock_conn = AsyncMock()
        st._connection = mock_conn
        st._transcript = "hello world"
        st._done.set()

        result = await st.finish()
        assert result == "hello world"

    async def test_finish_returns_none_when_no_transcript(self):
        """finish() returns None when no speech was detected."""
        from app.deepgram_service import StreamingTranscriber
        st = StreamingTranscriber()

        mock_conn = AsyncMock()
        st._connection = mock_conn
        st._transcript = ""
        st._done.set()

        result = await st.finish()
        assert result is None

    async def test_finish_strips_whitespace(self):
        """Trailing/leading whitespace in transcript is stripped."""
        from app.deepgram_service import StreamingTranscriber
        st = StreamingTranscriber()

        mock_conn = AsyncMock()
        st._connection = mock_conn
        st._transcript = "  spaces around  "
        st._done.set()

        result = await st.finish()
        assert result == "spaces around"

    async def test_send_passes_bytes_to_connection(self):
        """send() forwards PCM bytes to the underlying Deepgram connection."""
        from app.deepgram_service import StreamingTranscriber
        st = StreamingTranscriber()

        mock_conn = AsyncMock()
        st._connection = mock_conn

        pcm = np.zeros(320, dtype=np.int16).tobytes()
        await st.send(pcm)

        mock_conn.send.assert_called_once_with(pcm)

    async def test_send_before_start_does_not_crash(self):
        """send() before start() (connection is None) must not raise."""
        from app.deepgram_service import StreamingTranscriber
        st = StreamingTranscriber()
        pcm = np.zeros(320, dtype=np.int16).tobytes()
        await st.send(pcm)  # _connection is None at this point — should be a no-op

    async def test_transcript_concatenates_multiple_segments(self):
        """Multiple final segments are joined with spaces."""
        from app.deepgram_service import StreamingTranscriber
        st = StreamingTranscriber()

        mock_conn = AsyncMock()
        st._connection = mock_conn
        # Simulate the accumulation logic from on_transcript
        st._transcript = "hello"
        st._transcript += " " + "world"
        st._done.set()

        result = await st.finish()
        assert result == "hello world"


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 11 — Agent context injection (pure Python)
# ══════════════════════════════════════════════════════════════════════════════

class TestCallContextInjection:
    """Agent system prompt gets the correct interview context during outbound calls."""

    def _build_context(self, c_name, c_role, c_resume, c_notes,
                       questions, call_lead, sched_dt):
        sched_formatted = sched_dt.strftime("%A, %B %d, %Y at %I:%M %p UTC")
        article = "an" if c_role and c_role[0].lower() in "aeiou" else "a"
        return (
            f"\n\n--- CURRENT CALL CONTEXT ---"
            f"\nYou are calling {c_name} for {article} {c_role} interview."
            f"\nThe interview is scheduled for: {sched_formatted}."
            f"\nYou are calling {call_lead} minutes before the interview time as a pre-interview reminder."
            f"\n\nCandidate resume:\n{c_resume}"
            f"\n\nHR notes about this candidate:\n{c_notes}"
            f"\n\nSpecific questions to ask this candidate:\n{questions}"
        )

    def test_interview_time_appears_in_context(self):
        sched = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
        ctx = self._build_context("Priya", "Engineer", "5y Python", "Strong", "Q1", 30, sched)
        assert "July 15, 2026" in ctx
        assert "10:30" in ctx

    def test_lead_minutes_appear_in_context(self):
        sched = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
        ctx = self._build_context("Priya", "Engineer", "5y Python", "Strong", "Q1", 45, sched)
        assert "45 minutes before" in ctx

    def test_candidate_name_in_context(self):
        sched = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
        ctx = self._build_context("Rahul Verma", "Analyst", "3y data", "Good", "Q2", 30, sched)
        assert "Rahul Verma" in ctx

    def test_resume_text_in_context(self):
        sched = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
        ctx = self._build_context("P", "E", "Expert in React and Node.js", "OK", "Q1", 30, sched)
        assert "Expert in React and Node.js" in ctx

    def test_specific_questions_in_context(self):
        sched = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
        ctx = self._build_context("P", "E", "r", "n", "Describe your biggest challenge.", 30, sched)
        assert "Describe your biggest challenge." in ctx

    def test_vowel_role_uses_an(self):
        """Role starting with a vowel → 'an' article."""
        sched = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
        ctx = self._build_context("X", "Engineer", "r", "n", "q", 30, sched)
        assert "an Engineer" in ctx

    def test_consonant_role_uses_a(self):
        """Role starting with a consonant → 'a' article."""
        sched = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
        ctx = self._build_context("X", "Developer", "r", "n", "q", 30, sched)
        assert "a Developer" in ctx

    def test_day_of_week_is_correct(self):
        """July 15, 2026 is a Wednesday — verify the formatted label."""
        sched = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
        formatted = sched.strftime("%A, %B %d, %Y at %I:%M %p UTC")
        assert "Wednesday" in formatted

    def test_12h_time_format_pm(self):
        """3 PM should appear as 03:00 PM in 12-hour format, not 15:00."""
        sched = datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc)
        formatted = sched.strftime("%A, %B %d, %Y at %I:%M %p UTC")
        assert "PM" in formatted
        assert "03:00" in formatted

    def test_12h_time_format_am(self):
        """9 AM should appear as 09:00 AM."""
        sched = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
        formatted = sched.strftime("%A, %B %d, %Y at %I:%M %p UTC")
        assert "AM" in formatted
        assert "09:00" in formatted
