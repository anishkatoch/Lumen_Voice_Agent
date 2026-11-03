"""Per-user knowledge-base document routes."""
import asyncio
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth.dependencies import get_current_user

router = APIRouter(tags=["kb:user"])


@router.get("/api/user-kb/documents")
async def list_user_kb_documents(current_user: dict = Depends(get_current_user)):
    user_id = current_user["sub"]
    from app.kb.user_kb_repo import get_user_documents, get_user_storage_used, get_user_kb_limit
    docs = await asyncio.to_thread(get_user_documents, user_id)
    used = await asyncio.to_thread(get_user_storage_used, user_id)
    limit = get_user_kb_limit()
    return {"documents": docs, "storage_used_bytes": used, "storage_limit_bytes": limit}

@router.post("/api/user-kb/documents", status_code=201)
async def upload_user_kb_document(
    file: UploadFile,
    display_name: str = Form(...),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["sub"]
    from app.kb.user_kb_repo import get_user_storage_used, create_user_document, get_user_kb_limit
    from app.agents.agent_doc_processor import extract_text, process_user_document

    file_bytes = await file.read()
    fname = file.filename or ""
    file_type = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if file_type not in ("pdf", "docx", "txt"):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use PDF, DOCX, or TXT.")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="File is empty.")

    from app.kb.user_kb_repo import MAX_USER_KB_FILE_BYTES
    if len(file_bytes) > MAX_USER_KB_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum 5 MB per file.")

    storage_used = await asyncio.to_thread(get_user_storage_used, user_id)
    limit = get_user_kb_limit()
    if storage_used + len(file_bytes) > limit:
        remaining = limit - storage_used
        raise HTTPException(status_code=413, detail=f"Storage limit exceeded. {remaining / 1024 / 1024:.1f} MB remaining of 10 MB total.")

    try:
        content = await asyncio.to_thread(extract_text, file_bytes, file_type)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract text: {e}")

    if not content.strip():
        raise HTTPException(status_code=400, detail="Document appears to be empty after text extraction.")

    doc = await asyncio.to_thread(
        create_user_document, user_id, display_name.strip(), fname, len(file_bytes), file_type, content
    )
    asyncio.create_task(asyncio.to_thread(process_user_document, doc["id"], user_id, content))
    return doc

@router.delete("/api/user-kb/documents/{doc_id}", status_code=204)
async def delete_user_kb_document(doc_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["sub"]
    from app.kb.user_kb_repo import delete_user_document
    await asyncio.to_thread(delete_user_document, doc_id, user_id)
