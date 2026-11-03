"""Conversation history routes."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_current_user

router = APIRouter(tags=["conversations"])


@router.get("/api/conversations")
async def list_conversations(current_user: dict = Depends(get_current_user)):
    from app.conversations.conversation_repo import get_conversations
    return await asyncio.to_thread(get_conversations, current_user["sub"])

@router.get("/api/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, current_user: dict = Depends(get_current_user)):
    from app.conversations.conversation_repo import get_conversation_by_id
    from app.conversations.message_repo import get_conversation_history
    convo = await asyncio.to_thread(get_conversation_by_id, conversation_id)
    if not convo or convo.get("user_id") != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await asyncio.to_thread(get_conversation_history, conversation_id)
