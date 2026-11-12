"""
Tests for the Google Calendar OAuth routes (app/routers/integrations.py):
connect URL minting, the signed-state callback, status, and disconnect.
Google itself is always mocked (see tests/conftest.py - an unmocked HTTP
call here fails DNS, not a clean mock miss).
"""
import time

import jwt
import pytest
from unittest.mock import AsyncMock

from tests.conftest import JWT_SECRET, TEST_USER_ID

pytestmark = pytest.mark.asyncio


def _set_google_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")


def _make_state(user_id: str = TEST_USER_ID, purpose: str = "google_calendar_oauth",
                expired: bool = False) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "purpose": purpose, "exp": (now - 10) if expired else (now + 600)},
        JWT_SECRET, algorithm="HS256",
    )


class TestConnect:
    async def test_returns_google_auth_url_with_state(self, client, auth_headers, monkeypatch):
        _set_google_env(monkeypatch)
        resp = await client.get("/api/integrations/google/connect", headers=auth_headers)
        assert resp.status_code == 200
        url = resp.json()["auth_url"]
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "test-client-id.apps.googleusercontent.com" in url
        assert "state=" in url
        assert "access_type=offline" in url
        assert "prompt=consent" in url

    async def test_unconfigured_server_returns_503(self, client, auth_headers, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        resp = await client.get("/api/integrations/google/connect", headers=auth_headers)
        assert resp.status_code == 503

    async def test_requires_auth(self, client, monkeypatch):
        _set_google_env(monkeypatch)
        resp = await client.get("/api/integrations/google/connect")
        assert resp.status_code in (401, 403)


class TestCallback:
    async def test_valid_state_stores_tokens_and_redirects(self, client, monkeypatch, mocker):
        _set_google_env(monkeypatch)
        mocker.patch(
            "app.integrations.google_calendar_service.exchange_code",
            new=AsyncMock(return_value={
                "access_token": "at", "refresh_token": "rt",
                "token_expiry": "2030-01-01T00:00:00+00:00",
                "connected_email": "owner@example.com",
            }),
        )
        upsert = mocker.patch("app.integrations.integrations_repo.upsert_integration")
        resp = await client.get(
            "/api/integrations/google/callback",
            params={"code": "auth-code", "state": _make_state()},
        )
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/agents?google_calendar=connected"
        args = upsert.call_args[0]
        assert args[0] == TEST_USER_ID
        assert args[1] == "google_calendar"
        assert args[2] == "at" and args[3] == "rt"

    async def test_tampered_state_is_rejected(self, client, monkeypatch, mocker):
        _set_google_env(monkeypatch)
        upsert = mocker.patch("app.integrations.integrations_repo.upsert_integration")
        resp = await client.get(
            "/api/integrations/google/callback",
            params={"code": "auth-code", "state": "not-a-jwt"},
        )
        assert resp.status_code == 400
        upsert.assert_not_called()

    async def test_expired_state_is_rejected(self, client, monkeypatch):
        _set_google_env(monkeypatch)
        resp = await client.get(
            "/api/integrations/google/callback",
            params={"code": "auth-code", "state": _make_state(expired=True)},
        )
        assert resp.status_code == 400

    async def test_wrong_purpose_token_is_rejected(self, client, monkeypatch):
        """An ordinary session JWT (also HS256 with the same secret) must not
        work as OAuth state - the purpose claim is the discriminator."""
        _set_google_env(monkeypatch)
        resp = await client.get(
            "/api/integrations/google/callback",
            params={"code": "auth-code", "state": _make_state(purpose="something_else")},
        )
        assert resp.status_code == 400

    async def test_user_denied_consent_redirects_without_error(self, client):
        resp = await client.get(
            "/api/integrations/google/callback",
            params={"error": "access_denied"},
        )
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/agents?google_calendar=denied"

    async def test_exchange_failure_redirects_to_error_not_500(self, client, monkeypatch, mocker):
        _set_google_env(monkeypatch)
        mocker.patch(
            "app.integrations.google_calendar_service.exchange_code",
            new=AsyncMock(side_effect=RuntimeError("Google token exchange failed (400)")),
        )
        resp = await client.get(
            "/api/integrations/google/callback",
            params={"code": "bad-code", "state": _make_state()},
        )
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/agents?google_calendar=error"


class TestStatusAndDisconnect:
    async def test_status_connected(self, client, auth_headers, mocker):
        mocker.patch(
            "app.integrations.integrations_repo.get_integration",
            return_value={"connected_email": "owner@example.com", "refresh_token": "rt"},
        )
        resp = await client.get("/api/integrations/google/status", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"connected": True, "email": "owner@example.com"}

    async def test_status_not_connected(self, client, auth_headers, mocker):
        mocker.patch("app.integrations.integrations_repo.get_integration", return_value=None)
        resp = await client.get("/api/integrations/google/status", headers=auth_headers)
        assert resp.json() == {"connected": False, "email": None}

    async def test_disconnect_revokes_and_deletes(self, client, auth_headers, mocker):
        mocker.patch(
            "app.integrations.integrations_repo.get_integration",
            return_value={"refresh_token": "rt"},
        )
        revoke = mocker.patch(
            "app.integrations.google_calendar_service.revoke_tokens", new=AsyncMock()
        )
        delete = mocker.patch("app.integrations.integrations_repo.delete_integration")
        resp = await client.delete("/api/integrations/google", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"connected": False}
        revoke.assert_awaited_once_with("rt")
        delete.assert_called_once_with(TEST_USER_ID, "google_calendar")
