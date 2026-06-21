"""
Auth route tests — Supabase HTTP calls are mocked at the module level.
"""
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

pytestmark = pytest.mark.asyncio


def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


# ── /auth/signup ─────────────────────────────────────────────────────

async def test_signup_confirmation_required(client, mocker):
    """Supabase returns 200 with user but no access_token → confirmation flow."""
    mock_resp = _resp(200, {"user": {"id": "uid", "email": "a@b.com", "user_metadata": {}}})
    mocker.patch("app.main.httpx.AsyncClient", return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(post=AsyncMock(return_value=mock_resp))),
        __aexit__=AsyncMock(return_value=False),
    ))
    r = await client.post("/auth/signup", json={"email": "a@b.com", "password": "pass123"})
    assert r.status_code == 200
    assert r.json()["message"] == "confirmation_required"


async def test_signup_success(client, mocker):
    mock_resp = _resp(200, {
        "access_token": "tok", "refresh_token": "ref",
        "user": {"id": "uid", "email": "a@b.com", "user_metadata": {"full_name": "Alice"}},
    })
    mocker.patch("app.main.httpx.AsyncClient", return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(post=AsyncMock(return_value=mock_resp))),
        __aexit__=AsyncMock(return_value=False),
    ))
    r = await client.post("/auth/signup", json={"email": "a@b.com", "password": "pass123", "full_name": "Alice"})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == "tok"
    assert body["user"]["full_name"] == "Alice"


async def test_signup_supabase_error(client, mocker):
    mock_resp = _resp(400, {"error": "invalid_request", "error_description": "Email already registered"})
    mocker.patch("app.main.httpx.AsyncClient", return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(post=AsyncMock(return_value=mock_resp))),
        __aexit__=AsyncMock(return_value=False),
    ))
    r = await client.post("/auth/signup", json={"email": "a@b.com", "password": "pass123"})
    assert r.status_code == 400
    assert "Email already registered" in r.json()["detail"]


async def test_signup_invalid_email(client):
    r = await client.post("/auth/signup", json={"email": "not-an-email", "password": "pass123"})
    assert r.status_code == 422


async def test_signup_missing_password(client):
    r = await client.post("/auth/signup", json={"email": "a@b.com"})
    assert r.status_code == 422


# ── /auth/signin ─────────────────────────────────────────────────────

async def test_signin_success(client, mocker):
    mock_resp = _resp(200, {
        "access_token": "tok", "refresh_token": "ref",
        "user": {"id": "uid", "email": "a@b.com", "user_metadata": {}},
    })
    mocker.patch("app.main.httpx.AsyncClient", return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(post=AsyncMock(return_value=mock_resp))),
        __aexit__=AsyncMock(return_value=False),
    ))
    r = await client.post("/auth/signin", json={"email": "a@b.com", "password": "pass123"})
    assert r.status_code == 200
    assert r.json()["access_token"] == "tok"


async def test_signin_wrong_password(client, mocker):
    mock_resp = _resp(400, {"error": "invalid_grant", "error_description": "Invalid login credentials"})
    mocker.patch("app.main.httpx.AsyncClient", return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(post=AsyncMock(return_value=mock_resp))),
        __aexit__=AsyncMock(return_value=False),
    ))
    r = await client.post("/auth/signin", json={"email": "a@b.com", "password": "wrong"})
    assert r.status_code == 401


async def test_signin_invalid_email(client):
    r = await client.post("/auth/signin", json={"email": "bad", "password": "x"})
    assert r.status_code == 422


# ── /auth/refresh ────────────────────────────────────────────────────

async def test_refresh_success(client, mocker):
    mock_resp = _resp(200, {"access_token": "new-tok", "refresh_token": "new-ref"})
    mocker.patch("app.main.httpx.AsyncClient", return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(post=AsyncMock(return_value=mock_resp))),
        __aexit__=AsyncMock(return_value=False),
    ))
    r = await client.post("/auth/refresh", json={"refresh_token": "old-ref"})
    assert r.status_code == 200
    assert r.json()["access_token"] == "new-tok"


async def test_refresh_expired(client, mocker):
    mock_resp = _resp(400, {"error": "invalid_grant"})
    mocker.patch("app.main.httpx.AsyncClient", return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(post=AsyncMock(return_value=mock_resp))),
        __aexit__=AsyncMock(return_value=False),
    ))
    r = await client.post("/auth/refresh", json={"refresh_token": "bad"})
    assert r.status_code == 401


# ── /auth/signout ────────────────────────────────────────────────────

async def test_signout_requires_auth(client):
    r = await client.post("/auth/signout")
    assert r.status_code == 403


async def test_signout_success(client, auth_headers):
    r = await client.post("/auth/signout", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["message"] == "Signed out successfully"
