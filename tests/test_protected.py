"""
Tests for protected REST endpoints and auth middleware.
"""
import pytest

pytestmark = pytest.mark.asyncio

from tests.conftest import TEST_USER_ID, TEST_CONV_ID


# ── auth middleware ───────────────────────────────────────────────────

async def test_no_token_returns_403(client):
    r = await client.get("/api/me")
    assert r.status_code == 403


async def test_expired_token_returns_401(client, expired_token):
    r = await client.get("/api/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"].lower()


async def test_invalid_token_returns_401(client):
    r = await client.get("/api/me", headers={"Authorization": "Bearer garbage.token.here"})
    assert r.status_code == 401


# ── /api/me ───────────────────────────────────────────────────────────

async def test_get_me(client, auth_headers):
    r = await client.get("/api/me", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == TEST_USER_ID
    assert body["email"] == "test@example.com"
    assert body["role"] == "authenticated"


# ── /api/conversations ────────────────────────────────────────────────

async def test_list_conversations_requires_auth(client):
    r = await client.get("/api/conversations")
    assert r.status_code == 403


async def test_list_conversations(client, auth_headers):
    r = await client.get("/api/conversations", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert r.json()[0]["id"] == TEST_CONV_ID


# ── /api/conversations/{id}/messages ─────────────────────────────────

async def test_get_messages_requires_auth(client):
    r = await client.get(f"/api/conversations/{TEST_CONV_ID}/messages")
    assert r.status_code == 403


async def test_get_messages_own_conversation(client, auth_headers):
    r = await client.get(f"/api/conversations/{TEST_CONV_ID}/messages", headers=auth_headers)
    assert r.status_code == 200
    msgs = r.json()
    assert isinstance(msgs, list)
    assert msgs[0]["role"] == "user"


async def test_get_messages_other_users_conversation(client, mocker, auth_headers):
    """Should return 404 when the conversation belongs to a different user."""
    other_user_id = "99999999-9999-9999-9999-999999999999"
    mock_db = mocker.MagicMock()
    mock_db.table("conversations").select().eq().limit().execute.return_value.data = [{
        "id": TEST_CONV_ID, "user_id": other_user_id   # different owner
    }]
    mocker.patch("app.conversation_repo.get_supabase", return_value=mock_db)

    r = await client.get(f"/api/conversations/{TEST_CONV_ID}/messages", headers=auth_headers)
    assert r.status_code == 404


async def test_get_messages_nonexistent_conversation(client, mocker, auth_headers):
    mock_db = mocker.MagicMock()
    mock_db.table("conversations").select().eq().limit().execute.return_value.data = []
    mocker.patch("app.conversation_repo.get_supabase", return_value=mock_db)

    r = await client.get(f"/api/conversations/nonexistent-id/messages", headers=auth_headers)
    assert r.status_code == 404
