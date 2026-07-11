import pytest

pytestmark = pytest.mark.asyncio


async def test_root(client):
    # / serves frontend HTML if out/ exists, or 404 if not built yet — both are valid
    r = await client.get("/")
    assert r.status_code in (200, 404)


async def test_api_status(client):
    r = await client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["message"] == "API is running"


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
