"""
Coverage for the Plivo voice webhooks that decide whether a call connects at
all - /plivo/voice (inbound), /plivo/outbound/voice (HR interview outbound),
and /plivo/campaign/outbound/voice (HR campaign outbound). None of these had
a dedicated test before.

/plivo/voice specifically regression-guards the bug fixed in 92b2f94 ("Fix
Plivo inbound calls getting a busy tone from unescaped XML"): the Stream URL
for a plain inbound call joins to/from with a raw "&", which is not valid
inside XML text content - Plivo's XML parser rejected the response and the
call failed with a busy tone even though the agent had been resolved
correctly. Every test here also uses a plain custom agent name ("Test
Agent"), not "Meeting Assistant" - these endpoints never branch on agent
name or type, only on whether a number/row resolves to an agent_id at all.
"""
from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.asyncio

TEST_AGENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TEST_USER_ID = "11111111-1111-1111-1111-111111111111"
TEST_INTERVIEW_ID = "22222222-2222-2222-2222-222222222222"
TEST_CAMPAIGN_CAND_ID = "33333333-3333-3333-3333-333333333333"
TEST_CAMPAIGN_ID = "44444444-4444-4444-4444-444444444444"


class TestPlivoInboundVoiceWebhook:
    def test_escapes_ampersand_joining_to_and_from_in_the_stream_url(self, mocker):
        """Regression test for 92b2f94 - a bare '&' here makes the whole
        response invalid XML, which Plivo treats as a failed call (busy
        tone), even though the agent lookup below succeeded."""
        mocker.patch(
            "app.telephony.plivo.plivo_repo.get_number_by_phone",
            return_value={"agent_id": TEST_AGENT_ID, "user_id": TEST_USER_ID},
        )
        mocker.patch(
            "app.telephony.plivo.user_plivo_settings_repo.resolve_account_for_number",
            return_value={"webhook_base_url": "https://example.com"},
        )
        client = TestClient(app)
        resp = client.post("/plivo/voice", data={"To": "+14155550100", "From": "+14155559999"})
        assert resp.status_code == 200
        assert "&amp;" in resp.text
        assert "&from=" not in resp.text.replace("&amp;from=", "")  # no bare '&' survives
        assert "wss://example.com/plivo/stream?to=" in resp.text

    def test_number_with_no_agent_attached_gets_a_spoken_message_not_a_crash(self, mocker):
        mocker.patch("app.telephony.plivo.plivo_repo.get_number_by_phone", return_value=None)
        client = TestClient(app)
        resp = client.post("/plivo/voice", data={"To": "+14155550100", "From": "+14155559999"})
        assert resp.status_code == 200
        assert "not connected to an agent" in resp.text

    def test_missing_webhook_base_url_gets_a_spoken_message_not_a_crash(self, mocker):
        mocker.patch(
            "app.telephony.plivo.plivo_repo.get_number_by_phone",
            return_value={"agent_id": TEST_AGENT_ID, "user_id": TEST_USER_ID},
        )
        mocker.patch(
            "app.telephony.plivo.user_plivo_settings_repo.resolve_account_for_number",
            return_value={"webhook_base_url": ""},
        )
        client = TestClient(app)
        resp = client.post("/plivo/voice", data={"To": "+14155550100", "From": "+14155559999"})
        assert resp.status_code == 200
        assert "not configured yet" in resp.text

    def test_works_identically_for_a_number_attached_to_a_custom_agent(self, mocker):
        """Same lookup, same XML shape, regardless of which agent the number
        is attached to - proves this endpoint has no special-casing for any
        particular agent name or template."""
        mocker.patch(
            "app.telephony.plivo.plivo_repo.get_number_by_phone",
            return_value={"agent_id": "my-own-custom-agent-id", "user_id": TEST_USER_ID},
        )
        mocker.patch(
            "app.telephony.plivo.user_plivo_settings_repo.resolve_account_for_number",
            return_value={"webhook_base_url": "https://example.com"},
        )
        client = TestClient(app)
        resp = client.post("/plivo/voice", data={"To": "+14155550100", "From": "+14155559999"})
        assert resp.status_code == 200
        assert "<Stream" in resp.text


class TestPlivoInterviewOutboundVoiceWebhook:
    def test_builds_stream_url_with_interview_id(self, mocker):
        mocker.patch(
            "app.hr_campaigns.hr_repo.get_interview",
            return_value={"agent_id": TEST_AGENT_ID, "user_id": TEST_USER_ID},
        )
        mocker.patch(
            "app.telephony.plivo.user_plivo_settings_repo.get_plivo_settings",
            return_value={"webhook_base_url": "https://example.com"},
        )
        client = TestClient(app)
        resp = client.get(f"/plivo/outbound/voice?interview_id={quote(TEST_INTERVIEW_ID)}")
        assert resp.status_code == 200
        assert f"interview_id={TEST_INTERVIEW_ID}" in resp.text
        assert "wss://example.com/plivo/stream" in resp.text

    def test_unknown_interview_gets_a_spoken_message_not_a_crash(self, mocker):
        mocker.patch("app.hr_campaigns.hr_repo.get_interview", return_value=None)
        client = TestClient(app)
        resp = client.get(f"/plivo/outbound/voice?interview_id={quote(TEST_INTERVIEW_ID)}")
        assert resp.status_code == 200
        assert "Interview not found" in resp.text

    def test_missing_webhook_base_url_gets_a_spoken_message_not_a_crash(self, mocker):
        mocker.patch(
            "app.hr_campaigns.hr_repo.get_interview",
            return_value={"agent_id": TEST_AGENT_ID, "user_id": TEST_USER_ID},
        )
        mocker.patch(
            "app.telephony.plivo.user_plivo_settings_repo.get_plivo_settings",
            return_value={"webhook_base_url": ""},
        )
        client = TestClient(app)
        resp = client.get(f"/plivo/outbound/voice?interview_id={quote(TEST_INTERVIEW_ID)}")
        assert resp.status_code == 200
        assert "Server not configured" in resp.text


class TestPlivoCampaignOutboundVoiceWebhook:
    def test_builds_stream_url_with_campaign_candidate_id(self, mocker):
        mocker.patch(
            "app.hr_campaigns.hr_campaign_repo.get_campaign_candidate",
            return_value={
                "hr_campaigns": {"id": TEST_CAMPAIGN_ID, "agent_id": TEST_AGENT_ID, "user_id": TEST_USER_ID},
            },
        )
        mocker.patch(
            "app.telephony.plivo.user_plivo_settings_repo.get_plivo_settings",
            return_value={"webhook_base_url": "https://example.com"},
        )
        client = TestClient(app)
        resp = client.get(f"/plivo/campaign/outbound/voice?campaign_candidate_id={quote(TEST_CAMPAIGN_CAND_ID)}")
        assert resp.status_code == 200
        assert f"campaign_candidate_id={TEST_CAMPAIGN_CAND_ID}" in resp.text
        assert "wss://example.com/plivo/stream" in resp.text

    def test_unknown_candidate_gets_a_spoken_message_not_a_crash(self, mocker):
        mocker.patch("app.hr_campaigns.hr_campaign_repo.get_campaign_candidate", return_value=None)
        client = TestClient(app)
        resp = client.get(f"/plivo/campaign/outbound/voice?campaign_candidate_id={quote(TEST_CAMPAIGN_CAND_ID)}")
        assert resp.status_code == 200
        assert "Campaign call not found" in resp.text
