"""Tests for user-submitted issue reports / feedback and the admin review endpoints."""
import pytest

pytestmark = pytest.mark.asyncio

from tests.conftest import TEST_USER_ID, make_token

TEST_AGENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ADMIN_EMAIL = "katochaneesh@gmail.com"


def admin_token() -> str:
    import time, jwt, os
    secret = os.environ["SUPABASE_JWT_SECRET"]
    now = int(time.time())
    payload = {"sub": "admin-user-id", "email": ADMIN_EMAIL, "role": "authenticated", "iat": now, "exp": now + 3600}
    return jwt.encode(payload, secret, algorithm="HS256")


def _agent(agent_id=TEST_AGENT_ID, user_id=TEST_USER_ID):
    return {"id": agent_id, "user_id": user_id, "name": "Diet Agent"}


def _feedback(fb_id="fb-1", status="open", feedback_type="issue", agent_id=None, agent_name=None):
    return {
        "id": fb_id, "user_id": TEST_USER_ID, "user_email": "test@example.com",
        "agent_id": agent_id, "agent_name": agent_name,
        "type": feedback_type, "message": "Something broke", "status": status,
        "created_at": "2026-11-27T00:00:00+00:00", "resolved_at": None, "resolved_by": None,
    }


@pytest.fixture
async def client():
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _auth(token=None):
    return {"Authorization": f"Bearer {token or make_token()}"}


def _admin_auth():
    return {"Authorization": f"Bearer {admin_token()}"}


# ─────────────────────────────────────────────────────────────────────────────
# Submitting feedback
# ─────────────────────────────────────────────────────────────────────────────

async def test_submit_feedback_without_agent(client, mocker):
    mocker.patch("app.feedback.feedback_repo.create_feedback", return_value=_feedback())
    res = await client.post("/api/feedback", headers=_auth(), json={"type": "issue", "message": "Something broke"})
    assert res.status_code == 201
    assert res.json()["status"] == "open"


async def test_submit_feedback_with_agent_success(client, mocker):
    mocker.patch("app.agents.agent_repo.get_agent", return_value=_agent())
    mocker.patch(
        "app.feedback.feedback_repo.create_feedback",
        return_value=_feedback(agent_id=TEST_AGENT_ID, agent_name="Diet Agent"),
    )
    res = await client.post(
        "/api/feedback", headers=_auth(),
        json={"type": "feedback", "message": "Loving this agent", "agent_id": TEST_AGENT_ID},
    )
    assert res.status_code == 201
    assert res.json()["agent_name"] == "Diet Agent"


async def test_submit_feedback_with_agent_not_owned_returns_404(client, mocker):
    mocker.patch("app.agents.agent_repo.get_agent", return_value=_agent(user_id="someone-else"))
    res = await client.post(
        "/api/feedback", headers=_auth(),
        json={"type": "issue", "message": "x", "agent_id": TEST_AGENT_ID},
    )
    assert res.status_code == 404


async def test_submit_feedback_invalid_type_returns_400(client):
    res = await client.post("/api/feedback", headers=_auth(), json={"type": "bogus", "message": "x"})
    assert res.status_code == 400


async def test_submit_feedback_empty_message_returns_400(client):
    res = await client.post("/api/feedback", headers=_auth(), json={"type": "issue", "message": "   "})
    assert res.status_code == 400


async def test_submit_feedback_requires_auth(client):
    res = await client.post("/api/feedback", json={"type": "issue", "message": "x"})
    assert res.status_code == 403


async def test_list_my_feedback(client, mocker):
    mocker.patch("app.feedback.feedback_repo.get_user_feedback", return_value=[_feedback()])
    res = await client.get("/api/feedback/mine", headers=_auth())
    assert res.status_code == 200
    assert len(res.json()) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Admin review
# ─────────────────────────────────────────────────────────────────────────────

async def test_admin_list_feedback_forbidden_for_non_admin(client):
    res = await client.get("/api/admin/feedback", headers=_auth())
    assert res.status_code == 403


async def test_admin_list_feedback_success(client, mocker):
    mocker.patch("app.feedback.feedback_repo.get_all_feedback", return_value=[_feedback()])
    res = await client.get("/api/admin/feedback", headers=_admin_auth())
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert data[0]["status"] == "open"


async def test_admin_resolve_feedback_success(client, mocker):
    mocker.patch("app.feedback.feedback_repo.resolve_feedback", return_value=_feedback(status="resolved"))
    res = await client.post("/api/admin/feedback/fb-1/resolve", headers=_admin_auth())
    assert res.status_code == 200
    assert res.json()["status"] == "resolved"


async def test_admin_resolve_feedback_not_found(client, mocker):
    mocker.patch("app.feedback.feedback_repo.resolve_feedback", side_effect=RuntimeError("Feedback not found"))
    res = await client.post("/api/admin/feedback/bad-id/resolve", headers=_admin_auth())
    assert res.status_code == 404


async def test_admin_reopen_feedback_success(client, mocker):
    mocker.patch("app.feedback.feedback_repo.reopen_feedback", return_value=_feedback(status="open"))
    res = await client.post("/api/admin/feedback/fb-1/reopen", headers=_admin_auth())
    assert res.status_code == 200
    assert res.json()["status"] == "open"


async def test_admin_resolve_forbidden_for_non_admin(client):
    res = await client.post("/api/admin/feedback/fb-1/resolve", headers=_auth())
    assert res.status_code == 403
