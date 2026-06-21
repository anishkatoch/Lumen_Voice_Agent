import pytest

pytestmark = pytest.mark.asyncio


async def test_root(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert r.json()["message"] == "API is running"


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
