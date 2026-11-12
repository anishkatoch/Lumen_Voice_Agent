"""Agent CRUD, agent documents, and agent functions routes."""
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.auth.dependencies import get_current_user

router = APIRouter(tags=["agents"])


class AgentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    barge_in_sensitivity: float = 0.20

class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    barge_in_sensitivity: Optional[float] = None
    first_message_enabled: Optional[bool] = None
    first_message: Optional[str] = None
    is_active: Optional[bool] = None
    kb_instructions: Optional[str] = None
    hr_instructions: Optional[str] = None
    selected_kb_doc_ids: Optional[List[str]] = None
    filler_phrases: Optional[str] = None
    meeting_booking_enabled: Optional[bool] = None
    meeting_duration_minutes: Optional[int] = None
    meeting_buffer_minutes: Optional[int] = None
    meeting_timezone: Optional[str] = None
    booking_hours_start: Optional[str] = None
    booking_hours_end: Optional[str] = None
    meeting_instructions: Optional[str] = None

class AgentFunctionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    method: str = "POST"
    url: str = ""
    timeout_ms: int = 15000
    headers: dict = {}
    query_params: dict = {}
    body_schema: Optional[str] = None
    payload_args_only: bool = False
    parameters: Optional[List[dict]] = None
    trigger_type: str = "llm_tool_call"

class AgentFunctionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    method: Optional[str] = None
    url: Optional[str] = None
    timeout_ms: Optional[int] = None
    headers: Optional[dict] = None
    query_params: Optional[dict] = None
    body_schema: Optional[str] = None
    payload_args_only: Optional[bool] = None
    parameters: Optional[List[dict]] = None
    trigger_type: Optional[str] = None

@router.get("/api/agents")
async def list_agents(current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import get_agents, seed_default_agents
    await asyncio.to_thread(seed_default_agents, current_user["sub"])
    return await asyncio.to_thread(get_agents, current_user["sub"])

@router.post("/api/agents", status_code=201)
async def create_agent(body: AgentCreate, current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import create_agent
    return await asyncio.to_thread(
        create_agent, current_user["sub"], body.name, body.description,
        body.system_prompt, body.barge_in_sensitivity,
    )

@router.get("/api/agents/{agent_id}")
async def get_agent_detail(agent_id: str, current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import get_agent
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

@router.put("/api/agents/{agent_id}")
async def update_agent_detail(agent_id: str, body: AgentUpdate, current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import update_agent
    try:
        return await asyncio.to_thread(
            update_agent, agent_id, current_user["sub"],
            **body.model_dump(exclude_unset=True),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/api/agents/{agent_id}/duplicate", status_code=201)
async def duplicate_agent_route(agent_id: str, current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import duplicate_agent
    try:
        return await asyncio.to_thread(duplicate_agent, agent_id, current_user["sub"])
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/agents/{agent_id}", status_code=204)
async def delete_agent_route(agent_id: str, current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import get_agent, delete_agent
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    await asyncio.to_thread(delete_agent, agent_id, current_user["sub"])

@router.get("/api/agents/{agent_id}/documents")
async def list_agent_documents(agent_id: str, current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import get_agent, get_agent_documents, get_agent_storage_used
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    docs = await asyncio.to_thread(get_agent_documents, agent_id)
    used = await asyncio.to_thread(get_agent_storage_used, agent_id)
    return {"documents": docs, "storage_used_bytes": used, "storage_limit_bytes": 5 * 1024 * 1024}

@router.post("/api/agents/{agent_id}/documents", status_code=201)
async def upload_agent_document(
    agent_id: str,
    file: UploadFile = File(...),
    scope: str = Form("personal"),
    current_user: dict = Depends(get_current_user),
):
    from app.agents.agent_repo import (
        get_agent, get_agent_storage_used, create_agent_document,
        get_kb_permission, MAX_STORAGE_BYTES,
    )
    from app.agents.agent_doc_processor import extract_text, process_document

    # Ownership check
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Scope check for global uploads
    if scope == "global":
        has_permission = await asyncio.to_thread(get_kb_permission, current_user["sub"])
        if not has_permission:
            raise HTTPException(status_code=403, detail="Global KB upload requires admin approval")

    # Validate file type
    fname = file.filename or ""
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext not in ("pdf", "docx", "txt"):
        raise HTTPException(status_code=400, detail="Only PDF, DOCX, and TXT files are supported")

    # Read file
    file_bytes = await file.read()
    file_size = len(file_bytes)

    # Check storage limit (5MB total per agent)
    used = await asyncio.to_thread(get_agent_storage_used, agent_id)
    if used + file_size > MAX_STORAGE_BYTES:
        remaining = MAX_STORAGE_BYTES - used
        raise HTTPException(
            status_code=413,
            detail=f"Storage limit exceeded. You have {remaining / 1024:.1f} KB remaining out of 5 MB.",
        )

    # Extract text
    try:
        content = await asyncio.to_thread(extract_text, file_bytes, ext)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to extract text: {e}")

    if not content.strip():
        raise HTTPException(status_code=422, detail="Document appears to be empty or unreadable")

    # Store document record
    doc = await asyncio.to_thread(
        create_agent_document, agent_id, current_user["sub"],
        fname, file_size, ext, scope, content,
    )

    # Process chunks + embeddings in background
    async def _process():
        try:
            count = await asyncio.to_thread(
                process_document, doc["id"], agent_id, current_user["sub"], scope, content,
            )
            print(f"[Agent Doc] Processed {fname}: {count} chunks")
        except Exception as e:
            print(f"[Agent Doc] Processing failed for {fname}: {e}")
    asyncio.create_task(_process())

    return {**doc, "chunk_processing": "background"}

@router.delete("/api/agents/{agent_id}/documents/{doc_id}", status_code=204)
async def delete_agent_document(agent_id: str, doc_id: str, current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import get_agent, delete_agent_document
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    await asyncio.to_thread(delete_agent_document, doc_id, agent_id)

@router.get("/api/agents/{agent_id}/functions")
async def list_agent_functions(agent_id: str, current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import get_agent, get_agent_functions
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    return await asyncio.to_thread(get_agent_functions, agent_id)

@router.post("/api/agents/{agent_id}/functions", status_code=201)
async def create_agent_function_route(
    agent_id: str, body: AgentFunctionCreate, current_user: dict = Depends(get_current_user)
):
    from app.agents.agent_repo import get_agent, create_agent_function
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    return await asyncio.to_thread(
        create_agent_function, agent_id,
        body.name, body.description, body.method, body.url,
        body.timeout_ms, body.headers, body.query_params,
        body.body_schema, body.payload_args_only,
        body.parameters or [], body.trigger_type,
    )

@router.put("/api/agents/{agent_id}/functions/{func_id}")
async def update_agent_function_route(
    agent_id: str, func_id: str, body: AgentFunctionUpdate,
    current_user: dict = Depends(get_current_user)
):
    from app.agents.agent_repo import get_agent, update_agent_function
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        return await asyncio.to_thread(
            update_agent_function, func_id, agent_id,
            **{k: v for k, v in body.model_dump().items() if v is not None},
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/agents/{agent_id}/functions/{func_id}", status_code=204)
async def delete_agent_function_route(
    agent_id: str, func_id: str, current_user: dict = Depends(get_current_user)
):
    from app.agents.agent_repo import get_agent, delete_agent_function
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Agent not found")
    await asyncio.to_thread(delete_agent_function, func_id, agent_id)
