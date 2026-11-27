"""User-submitted issue reports and general feedback, optionally scoped to one agent."""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import get_current_user

router = APIRouter(tags=["feedback"])

VALID_TYPES = {"issue", "feedback"}


class FeedbackCreate(BaseModel):
    type: str = "issue"
    message: str
    agent_id: Optional[str] = None


@router.post("/api/feedback", status_code=201)
async def submit_feedback(body: FeedbackCreate, current_user: dict = Depends(get_current_user)):
    if body.type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="type must be 'issue' or 'feedback'")
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    agent_name = None
    if body.agent_id:
        from app.agents.agent_repo import get_agent
        agent = await asyncio.to_thread(get_agent, body.agent_id)
        if not agent or agent["user_id"] != current_user["sub"]:
            raise HTTPException(status_code=404, detail="Agent not found")
        agent_name = agent["name"]

    from app.feedback.feedback_repo import create_feedback
    return await asyncio.to_thread(
        create_feedback, current_user["sub"], current_user.get("email", ""),
        body.type, message, body.agent_id, agent_name,
    )


@router.get("/api/feedback/mine")
async def list_my_feedback(current_user: dict = Depends(get_current_user)):
    from app.feedback.feedback_repo import get_user_feedback
    return await asyncio.to_thread(get_user_feedback, current_user["sub"])
