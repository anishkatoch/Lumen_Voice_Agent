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

async def build_qa_payload(call_sid: str | None) -> dict:
    """Per-question answered/not-answered status for an interview or campaign
    screening call, for Call Analytics. Conversations don't store a direct FK
    to hr_interviews/hr_campaign_candidates, so this joins via call_sid - the
    one field both sides already share. Shared by the per-user and admin QA
    routes, which each do their own ownership check before calling this."""
    if not call_sid:
        return {"questions": []}

    from app.hr_campaigns.hr_repo import get_interview_by_call_sid, list_interview_answers
    interview = await asyncio.to_thread(get_interview_by_call_sid, call_sid)
    if interview:
        questions = [
            {"id": f"q{i+1}", "text": line.strip()}
            for i, line in enumerate((interview.get("specific_questions") or "").splitlines())
            if line.strip()
        ]
        answers = await asyncio.to_thread(list_interview_answers, interview["id"])
    else:
        from app.hr_campaigns.hr_campaign_repo import get_campaign_candidate_by_call_sid, list_campaign_answers
        candidate = await asyncio.to_thread(get_campaign_candidate_by_call_sid, call_sid)
        if not candidate:
            return {"questions": []}
        campaign = candidate.get("hr_campaigns") or {}
        questions = [
            {"id": q.get("id"), "text": q.get("text", "")}
            for q in (campaign.get("questions") or []) if q.get("text")
        ]
        answers = await asyncio.to_thread(list_campaign_answers, candidate["id"])

    answer_by_id = {a["question_id"]: a["answer_text"] for a in answers}
    return {
        "questions": [
            {"question": q["text"], "answered": q["id"] in answer_by_id, "answer": answer_by_id.get(q["id"])}
            for q in questions
        ]
    }


@router.get("/api/conversations/{conversation_id}/qa")
async def get_conversation_qa(conversation_id: str, current_user: dict = Depends(get_current_user)):
    from app.core.database import get_supabase
    db = get_supabase()
    convo = await asyncio.to_thread(
        lambda: db.table("conversations").select("user_id, call_sid").eq("id", conversation_id).limit(1).execute()
    )
    if not convo.data or convo.data[0]["user_id"] != current_user["sub"]:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await build_qa_payload(convo.data[0].get("call_sid"))
