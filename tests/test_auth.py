"""
Auth route tests — Supabase HTTP calls are mocked at the module level.
"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock

pytestmark = pytest.mark.asyncio


def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


def _multi_client(*responses):
    """Mock httpx.AsyncClient that returns different responses per call."""
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=list(responses))
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_http)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _single_client(resp):
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=resp)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_http)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ── /auth/signup ─────────────────────────────────────────────────────

async def test_signup_confirmation_required(client, mocker):
    """No access_token + sign-in fails → email needs confirmation."""
    signup_resp = _resp(200, {"user": {"id": "uid", "email": "a@b.com", "user_metadata": {}}})
    signin_resp = _resp(400, {"error": "invalid_credentials"})
    mocker.patch("app.main.httpx.AsyncClient", side_effect=[
        _multi_client(signup_resp, signin_resp),
        _single_client(signin_resp),
    ])
    r = await client.post("/auth/signup", json={"email": "a@b.com", "password": "pass123"})
    assert r.status_code == 200
    assert r.json()["message"] == "confirmation_required"


async def test_signup_duplicate_email(client, mocker):
    """No access_token + sign-in succeeds → duplicate email error."""
    signup_resp = _resp(200, {"user": {"id": "uid", "email": "a@b.com", "user_metadata": {}}})
    signin_resp = _resp(200, {
        "access_token": "tok", "refresh_token": "ref",
        "user": {"id": "uid", "email": "a@b.com", "user_metadata": {}},
    })
    mock_cm1 = _single_client(signup_resp)
    mock_cm2 = _single_client(signin_resp)
    mocker.patch("app.main.httpx.AsyncClient", side_effect=[mock_cm1, mock_cm2])
    r = await client.post("/auth/signup", json={"email": "a@b.com", "password": "pass123"})
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


async def test_signup_success(client, mocker):
    mock_resp = _resp(200, {
        "access_token": "tok", "refresh_token": "ref",
        "user": {"id": "uid", "email": "a@b.com", "user_metadata": {"full_name": "Alice"}},
    })
    mocker.patch("app.main.httpx.AsyncClient", return_value=_single_client(mock_resp))
    r = await client.post("/auth/signup", json={"email": "a@b.com", "password": "pass123", "full_name": "Alice"})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == "tok"
    assert body["user"]["full_name"] == "Alice"


async def test_signup_supabase_error(client, mocker):
    mock_resp = _resp(400, {"error": "invalid_request", "error_description": "Email already registered"})
    mocker.patch("app.main.httpx.AsyncClient", return_value=_single_client(mock_resp))
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
    mocker.patch("app.main.httpx.AsyncClient", return_value=_single_client(mock_resp))
    r = await client.post("/auth/signin", json={"email": "a@b.com", "password": "pass123"})
    assert r.status_code == 200
    assert r.json()["access_token"] == "tok"


async def test_signin_wrong_password(client, mocker):
    mock_resp = _resp(400, {"error": "invalid_grant", "error_description": "Invalid login credentials"})
    mocker.patch("app.main.httpx.AsyncClient", return_value=_single_client(mock_resp))
    r = await client.post("/auth/signin", json={"email": "a@b.com", "password": "wrong"})
    assert r.status_code == 401


async def test_signin_invalid_email(client):
    r = await client.post("/auth/signin", json={"email": "bad", "password": "x"})
    assert r.status_code == 422


# ── /auth/refresh ────────────────────────────────────────────────────

async def test_refresh_success(client, mocker):
    mock_resp = _resp(200, {"access_token": "new-tok", "refresh_token": "new-ref"})
    mocker.patch("app.main.httpx.AsyncClient", return_value=_single_client(mock_resp))
    r = await client.post("/auth/refresh", json={"refresh_token": "old-ref"})
    assert r.status_code == 200
    assert r.json()["access_token"] == "new-tok"


async def test_refresh_expired(client, mocker):
    mock_resp = _resp(400, {"error": "invalid_grant"})
    mocker.patch("app.main.httpx.AsyncClient", return_value=_single_client(mock_resp))
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


# ── /auth/forgot-password ────────────────────────────────────────────

async def test_forgot_password_success(client, mocker):
    mock_resp = _resp(200, {})
    mocker.patch("app.main.httpx.AsyncClient", return_value=_single_client(mock_resp))
    r = await client.post("/auth/forgot-password", json={"email": "a@b.com"})
    assert r.status_code == 200
    assert "OTP" in r.json()["message"]


async def test_forgot_password_rate_limited(client, mocker):
    mock_resp = _resp(429, {"error_code": "over_email_send_rate_limit"})
    mocker.patch("app.main.httpx.AsyncClient", return_value=_single_client(mock_resp))
    r = await client.post("/auth/forgot-password", json={"email": "a@b.com"})
    assert r.status_code == 429
    assert "wait" in r.json()["detail"].lower()


async def test_forgot_password_invalid_email(client):
    r = await client.post("/auth/forgot-password", json={"email": "not-an-email"})
    assert r.status_code == 422


# ── /auth/verify-otp ────────────────────────────────────────────────

async def test_verify_otp_success(client, mocker):
    verify_resp = _resp(200, {"access_token": "sess-tok"})
    update_resp = _resp(200, {"id": "uid"})
    mock_cm1 = _single_client(verify_resp)
    # PUT call needs a different mock
    mock_http2 = AsyncMock()
    mock_http2.put = AsyncMock(return_value=update_resp)
    mock_cm2 = AsyncMock()
    mock_cm2.__aenter__ = AsyncMock(return_value=mock_http2)
    mock_cm2.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("app.main.httpx.AsyncClient", side_effect=[mock_cm1, mock_cm2])
    r = await client.post("/auth/verify-otp", json={"email": "a@b.com", "token": "123456", "password": "newpass123"})
    assert r.status_code == 200
    assert "updated" in r.json()["message"].lower()


async def test_verify_otp_wrong_code(client, mocker):
    verify_resp = _resp(400, {"msg": "Token has expired or is invalid"})
    mocker.patch("app.main.httpx.AsyncClient", return_value=_single_client(verify_resp))
    r = await client.post("/auth/verify-otp", json={"email": "a@b.com", "token": "000000", "password": "newpass123"})
    assert r.status_code == 400
    assert "invalid" in r.json()["detail"].lower() or "expired" in r.json()["detail"].lower()
