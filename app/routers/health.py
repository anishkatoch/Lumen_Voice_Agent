"""Simple health/status endpoints."""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health():
    return {"status": "ok"}

@router.get("/api/status")
def read_root():
    return {"message": "API is running"}
