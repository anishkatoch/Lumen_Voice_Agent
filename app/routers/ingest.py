"""Document ingestion routes and the storage webhook that triggers it."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.dependencies import get_current_user
from app.core.config import WEBHOOK_SECRET, STORAGE_BUCKET

router = APIRouter(tags=["ingest"])


@router.post("/api/ingest-all-dev")
async def ingest_all_documents_dev():
    """No-auth version for local testing only. Remove before going to production."""
    return await _ingest_all_impl()

@router.post("/api/ingest-all")
async def ingest_all_documents(current_user: dict = Depends(get_current_user)):
    return await _ingest_all_impl()

async def _ingest_all_impl():
    from app.agents.doc_processor import ingest_document
    from app.core.database import get_supabase

    db = get_supabase()
    try:
        files = db.storage.from_(STORAGE_BUCKET).list()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list bucket: {e}")

    md_files = [f["name"] for f in files if f["name"].endswith(".md")]
    if not md_files:
        return {"status": "no .md files found in bucket", "bucket": STORAGE_BUCKET}

    results = []
    for file_name in md_files:
        try:
            chunks = await ingest_document(file_name)
            results.append({"file": file_name, "chunks": chunks, "status": "ok"})
            print(f"[Ingest] {file_name} → {chunks} chunks")
        except Exception as e:
            results.append({"file": file_name, "status": "error", "error": str(e)})
            print(f"[Ingest] {file_name} → ERROR: {e}")

    return {"ingested": results}

@router.post("/webhook/new-document")
async def new_document_webhook(payload: dict, request: Request):
    from app.agents.doc_processor import ingest_document

    if WEBHOOK_SECRET:
        auth_header = request.headers.get("authorization", "")
        if auth_header != f"Bearer {WEBHOOK_SECRET}":
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    record = payload.get("record", {})
    file_name = record.get("name") or payload.get("name") or payload.get("file_name")
    bucket_id = record.get("bucket_id", "")

    print(f"[Webhook] type={payload.get('type')} bucket={bucket_id} file={file_name}")

    if not file_name:
        return {"status": "ignored", "reason": "no file_name in payload"}
    if not file_name.endswith(".md"):
        return {"status": "ignored", "reason": f"not a .md file: {file_name}"}
    if bucket_id and bucket_id != STORAGE_BUCKET:
        return {"status": "ignored", "reason": f"wrong bucket: {bucket_id}"}

    async def _ingest_task():
        try:
            await ingest_document(file_name)
        except Exception as e:
            print(f"[Webhook] Ingest failed for {file_name}: {e}")
    asyncio.create_task(_ingest_task())
    print(f"[Webhook] Queued ingestion for: {file_name}")
    return {"status": "processing", "file": file_name}
