"""Knowledge-base access-request routes."""
import asyncio
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import get_current_user

router = APIRouter(tags=["kb"])


class KBAccessRequest(BaseModel):
    user_email: str

@router.get("/api/kb/my-permission")
async def my_kb_permission(current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import get_kb_permission
    allowed = await asyncio.to_thread(get_kb_permission, current_user["sub"])
    return {"can_upload_global_kb": allowed}

@router.post("/api/kb/request-access")
async def request_kb_access(body: KBAccessRequest, current_user: dict = Depends(get_current_user)):
    from app.agents.agent_repo import create_kb_request
    result = await asyncio.to_thread(create_kb_request, current_user["sub"], body.user_email)
    return result
