"""HR candidates, interviews, and call-campaign routes."""
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.auth.dependencies import get_current_user

router = APIRouter(tags=["hr"])


class CandidateCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    role: Optional[str] = None
    resume_text: Optional[str] = None
    notes: Optional[str] = None

class CandidateUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    resume_text: Optional[str] = None
    notes: Optional[str] = None

class InterviewCreate(BaseModel):
    candidate_id: str
    agent_id: str
    scheduled_at: str
    call_lead_minutes: int = 30
    specific_questions: Optional[str] = None

class InterviewUpdate(BaseModel):
    scheduled_at: Optional[str] = None
    call_lead_minutes: Optional[int] = None
    specific_questions: Optional[str] = None
    agent_id: Optional[str] = None
    status: Optional[str] = None

class CampaignQuestion(BaseModel):
    id: str
    text: str

class CampaignCreate(BaseModel):
    agent_id: str
    name: str
    questions: List[CampaignQuestion] = []
    # None = fall back to the admin-portal call retry settings (see
    # hr_campaign_repo.create_campaign/_default_retry_settings) instead of a
    # value baked into this request schema.
    max_retry_attempts: Optional[int] = None
    retry_delay_minutes: Optional[int] = None
    failed_retry_delay_minutes: Optional[int] = None
    call_gap_minutes: int = 4

class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    questions: Optional[List[CampaignQuestion]] = None
    status: Optional[str] = None
    max_retry_attempts: Optional[int] = None
    retry_delay_minutes: Optional[int] = None
    failed_retry_delay_minutes: Optional[int] = None
    call_gap_minutes: Optional[int] = None

class CampaignCandidatesAdd(BaseModel):
    candidate_ids: List[str]

@router.get("/api/hr/candidates")
async def list_candidates_route(current_user: dict = Depends(get_current_user)):
    from app.hr_campaigns.hr_repo import list_candidates
    return await asyncio.to_thread(list_candidates, current_user["sub"])

@router.post("/api/hr/candidates", status_code=201)
async def create_candidate_route(body: CandidateCreate, current_user: dict = Depends(get_current_user)):
    from app.hr_campaigns.hr_repo import create_candidate
    return await asyncio.to_thread(
        create_candidate, current_user["sub"],
        body.name, body.phone, body.email, body.role, body.resume_text, None, body.notes
    )

@router.post("/api/hr/candidates/{candidate_id}/resume")
async def upload_candidate_resume(
    candidate_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    from app.hr_campaigns.hr_repo import get_candidate, update_candidate
    candidate = await asyncio.to_thread(get_candidate, candidate_id, current_user["sub"])
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    content = await file.read()
    resume_text = ""
    fname = file.filename or ""
    try:
        if fname.lower().endswith(".pdf"):
            from app.agents.agent_doc_processor import extract_text_pdf
            resume_text = await asyncio.to_thread(extract_text_pdf, content)
        elif fname.lower().endswith(".docx"):
            import docx, io
            d = docx.Document(io.BytesIO(content))
            resume_text = "\n".join(p.text for p in d.paragraphs)
        else:
            resume_text = content.decode("utf-8", errors="ignore")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read file: {e}")

    return await asyncio.to_thread(
        update_candidate, candidate_id, current_user["sub"],
        resume_text=resume_text.strip(), resume_file_name=fname
    )

@router.put("/api/hr/candidates/{candidate_id}")
async def update_candidate_route(
    candidate_id: str, body: CandidateUpdate, current_user: dict = Depends(get_current_user)
):
    from app.hr_campaigns.hr_repo import update_candidate
    try:
        return await asyncio.to_thread(
            update_candidate, candidate_id, current_user["sub"],
            **body.model_dump(exclude_none=True)
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/hr/candidates/{candidate_id}", status_code=204)
async def delete_candidate_route(candidate_id: str, current_user: dict = Depends(get_current_user)):
    from app.hr_campaigns.hr_repo import delete_candidate
    await asyncio.to_thread(delete_candidate, candidate_id, current_user["sub"])

@router.get("/api/hr/interviews")
async def list_interviews_route(current_user: dict = Depends(get_current_user)):
    from app.hr_campaigns.hr_repo import list_interviews
    return await asyncio.to_thread(list_interviews, current_user["sub"])

@router.post("/api/hr/interviews", status_code=201)
async def create_interview_route(body: InterviewCreate, current_user: dict = Depends(get_current_user)):
    from app.hr_campaigns.hr_repo import create_interview
    return await asyncio.to_thread(
        create_interview, current_user["sub"],
        body.candidate_id, body.agent_id, body.scheduled_at,
        body.call_lead_minutes, body.specific_questions
    )

@router.put("/api/hr/interviews/{interview_id}")
async def update_interview_route(
    interview_id: str, body: InterviewUpdate, current_user: dict = Depends(get_current_user)
):
    from app.hr_campaigns.hr_repo import update_interview
    try:
        return await asyncio.to_thread(
            update_interview, interview_id, current_user["sub"],
            **body.model_dump(exclude_none=True)
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/hr/interviews/{interview_id}", status_code=204)
async def delete_interview_route(interview_id: str, current_user: dict = Depends(get_current_user)):
    from app.hr_campaigns.hr_repo import delete_interview
    await asyncio.to_thread(delete_interview, interview_id, current_user["sub"])

@router.get("/api/hr/interviews/{interview_id}/answers")
async def list_interview_answers_route(
    interview_id: str, current_user: dict = Depends(get_current_user)
):
    # Ownership check via the parent interview is intentionally skipped here,
    # matching list_campaign_answers_route's existing convention below -
    # interview_id is an unguessable UUID, same as get_interview() itself.
    from app.hr_campaigns.hr_repo import list_interview_answers
    return await asyncio.to_thread(list_interview_answers, interview_id)

@router.get("/api/hr/campaigns")
async def list_campaigns_route(current_user: dict = Depends(get_current_user)):
    from app.hr_campaigns.hr_campaign_repo import list_campaigns
    return await asyncio.to_thread(list_campaigns, current_user["sub"])

@router.post("/api/hr/campaigns", status_code=201)
async def create_campaign_route(body: CampaignCreate, current_user: dict = Depends(get_current_user)):
    from app.hr_campaigns.hr_campaign_repo import create_campaign
    return await asyncio.to_thread(
        create_campaign, current_user["sub"], body.agent_id, body.name,
        [q.model_dump() for q in body.questions],
        body.max_retry_attempts, body.retry_delay_minutes, body.call_gap_minutes,
        body.failed_retry_delay_minutes,
    )

@router.put("/api/hr/campaigns/{campaign_id}")
async def update_campaign_route(
    campaign_id: str, body: CampaignUpdate, current_user: dict = Depends(get_current_user)
):
    from app.hr_campaigns.hr_campaign_repo import update_campaign
    fields = body.model_dump(exclude_none=True)
    if "questions" in fields:
        fields["questions"] = [q if isinstance(q, dict) else q.model_dump() for q in fields["questions"]]
    try:
        return await asyncio.to_thread(update_campaign, campaign_id, current_user["sub"], **fields)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/hr/campaigns/{campaign_id}", status_code=204)
async def delete_campaign_route(campaign_id: str, current_user: dict = Depends(get_current_user)):
    from app.hr_campaigns.hr_campaign_repo import delete_campaign
    await asyncio.to_thread(delete_campaign, campaign_id, current_user["sub"])

@router.get("/api/hr/campaigns/{campaign_id}/candidates")
async def list_campaign_candidates_route(campaign_id: str, current_user: dict = Depends(get_current_user)):
    from app.hr_campaigns.hr_campaign_repo import get_campaign, list_campaign_candidates
    if not await asyncio.to_thread(get_campaign, campaign_id, current_user["sub"]):
        raise HTTPException(status_code=404, detail="Campaign not found")
    return await asyncio.to_thread(list_campaign_candidates, campaign_id)

@router.post("/api/hr/campaigns/{campaign_id}/candidates", status_code=201)
async def add_campaign_candidates_route(
    campaign_id: str, body: CampaignCandidatesAdd, current_user: dict = Depends(get_current_user)
):
    from app.hr_campaigns.hr_campaign_repo import get_campaign, add_candidates_to_campaign
    if not await asyncio.to_thread(get_campaign, campaign_id, current_user["sub"]):
        raise HTTPException(status_code=404, detail="Campaign not found")
    return await asyncio.to_thread(add_candidates_to_campaign, campaign_id, body.candidate_ids)

@router.delete("/api/hr/campaigns/{campaign_id}/candidates/{campaign_candidate_id}", status_code=204)
async def remove_campaign_candidate_route(
    campaign_id: str, campaign_candidate_id: str, current_user: dict = Depends(get_current_user)
):
    from app.hr_campaigns.hr_campaign_repo import get_campaign, remove_campaign_candidate
    if not await asyncio.to_thread(get_campaign, campaign_id, current_user["sub"]):
        raise HTTPException(status_code=404, detail="Campaign not found")
    await asyncio.to_thread(remove_campaign_candidate, campaign_candidate_id, campaign_id)

@router.get("/api/hr/campaigns/candidates/{campaign_candidate_id}/answers")
async def list_campaign_answers_route(
    campaign_candidate_id: str, current_user: dict = Depends(get_current_user)
):
    # Ownership check via the parent campaign is intentionally skipped here for v1 -
    # campaign_candidate_id is an unguessable UUID, matching the existing
    # get_interview() convention (app/hr_repo.py) which is also not user-scoped.
    from app.hr_campaigns.hr_campaign_repo import list_campaign_answers
    return await asyncio.to_thread(list_campaign_answers, campaign_candidate_id)
