"""
Tests for document ingestion endpoints and webhook.
"""
import pytest

pytestmark = pytest.mark.asyncio


# ── /api/ingest-all-dev ───────────────────────────────────────────────

async def test_ingest_dev_no_files(client, mock_supabase):
    mock_supabase.storage.from_.return_value.list.return_value = []
    r = await client.post("/api/ingest-all-dev")
    assert r.status_code == 200
    assert r.json()["status"] == "no .md files found in bucket"


async def test_ingest_dev_with_md_files(client, mock_supabase, mocker):
    mock_supabase.storage.from_.return_value.list.return_value = [
        {"name": "faq.md"}, {"name": "readme.txt"}   # only .md processed
    ]
    mock_supabase.storage.from_.return_value.download.return_value = b"# Title\nSome content here."
    mocker.patch("app.doc_processor.embed_text", return_value=[0.1] * 1536)

    r = await client.post("/api/ingest-all-dev")
    assert r.status_code == 200
    ingested = r.json()["ingested"]
    assert len(ingested) == 1
    assert ingested[0]["file"] == "faq.md"
    assert ingested[0]["status"] == "ok"


async def test_ingest_dev_handles_file_error(client, mock_supabase, mocker):
    mock_supabase.storage.from_.return_value.list.return_value = [{"name": "bad.md"}]
    mock_supabase.storage.from_.return_value.download.return_value = None   # → ValueError
    mocker.patch("app.rag_service.embed_text", return_value=[0.1] * 1536)

    r = await client.post("/api/ingest-all-dev")
    assert r.status_code == 200
    result = r.json()["ingested"][0]
    assert result["status"] == "error"


# ── /api/ingest-all (auth required) ──────────────────────────────────

async def test_ingest_all_requires_auth(client):
    r = await client.post("/api/ingest-all")
    assert r.status_code == 403


async def test_ingest_all_with_auth(client, auth_headers, mock_supabase):
    mock_supabase.storage.from_.return_value.list.return_value = []
    r = await client.post("/api/ingest-all", headers=auth_headers)
    assert r.status_code == 200


# ── /webhook/new-document ─────────────────────────────────────────────

async def test_webhook_ignores_non_md(client):
    r = await client.post("/webhook/new-document", json={
        "type": "INSERT",
        "record": {"name": "image.png", "bucket_id": "documents"}
    })
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert "not a .md" in r.json()["reason"]


async def test_webhook_ignores_wrong_bucket(client):
    r = await client.post("/webhook/new-document", json={
        "type": "INSERT",
        "record": {"name": "file.md", "bucket_id": "other-bucket"}
    })
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert "wrong bucket" in r.json()["reason"]


async def test_webhook_ignores_missing_filename(client):
    r = await client.post("/webhook/new-document", json={"type": "INSERT", "record": {}})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


async def test_webhook_queues_md_file(client, mocker):
    mocker.patch("app.main.asyncio.create_task")
    mocker.patch("app.doc_processor.ingest_document")

    r = await client.post("/webhook/new-document", json={
        "type": "INSERT",
        "record": {"name": "guide.md", "bucket_id": "documents"}
    })
    assert r.status_code == 200
    assert r.json()["status"] == "processing"
    assert r.json()["file"] == "guide.md"


async def test_webhook_rejects_bad_secret(client, mocker):
    mocker.patch("app.main.WEBHOOK_SECRET", "real-secret")
    r = await client.post(
        "/webhook/new-document",
        json={"record": {"name": "file.md", "bucket_id": "documents"}},
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert r.status_code == 401


async def test_webhook_accepts_correct_secret(client, mocker):
    mocker.patch("app.main.WEBHOOK_SECRET", "real-secret")
    mocker.patch("app.main.asyncio.create_task")
    mocker.patch("app.doc_processor.ingest_document")

    r = await client.post(
        "/webhook/new-document",
        json={"record": {"name": "file.md", "bucket_id": "documents"}},
        headers={"Authorization": "Bearer real-secret"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "processing"
