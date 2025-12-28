"""
RECRUITING ROUTES
Endpoints for candidate scoring and resume parsing
"""
from fastapi import APIRouter, HTTPException, status, Body
from typing import Optional
from pydantic import BaseModel
from services.recruiting_service import RecruitingService

router = APIRouter(prefix="/api/recruiting", tags=["recruiting"])


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ResumeParseRequest(BaseModel):
    resume_text: str
    source: Optional[str] = "Indeed Paste"


class ScoreCandidateRequest(BaseModel):
    candidate_id: str


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/parse-resume")
async def parse_resume(request: ResumeParseRequest):
    """
    Parse resume text and return extracted data + score preview.
    Does NOT save to database.
    """
    if not request.resume_text or len(request.resume_text.strip()) < 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume text too short"
        )
    
    service = RecruitingService()
    
    try:
        result = await service.parse_resume(request.resume_text)
        return {
            "success": True,
            **result
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse resume: {str(e)}"
        )


@router.post("/parse-and-save")
async def parse_and_save_resume(request: ResumeParseRequest):
    """
    Parse resume text, score it, and save as new candidate.
    Creates automatic note with extraction summary.
    """
    if not request.resume_text or len(request.resume_text.strip()) < 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume text too short"
        )
    
    service = RecruitingService()
    
    try:
        result = await service.parse_and_save_resume(
            resume_text=request.resume_text,
            source=request.source or "Indeed Paste"
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save candidate: {str(e)}"
        )


@router.post("/score/{candidate_id}")
async def score_candidate(candidate_id: str):
    """
    Calculate and update score for a specific candidate.
    Returns detailed score breakdown.
    """
    service = RecruitingService()
    
    try:
        result = await service.score_candidate(candidate_id)
        return {
            "success": True,
            **result
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to score candidate: {str(e)}"
        )


@router.post("/score-all-unscored")
async def score_all_unscored():
    """
    Score all candidates that don't have a score yet.
    Useful for backfilling existing candidates.
    """
    service = RecruitingService()
    
    try:
        result = await service.score_all_unscored()
        return {
            "success": True,
            **result
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to score candidates: {str(e)}"
        )


@router.get("/score-distribution")
async def get_score_distribution():
    """
    Get analytics on score distribution across all candidates.
    """
    service = RecruitingService()
    
    try:
        result = await service.get_score_distribution()
        return {
            "success": True,
            **result
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get distribution: {str(e)}"
        )


@router.get("/weights")
async def get_scoring_weights():
    """
    Return current scoring weights configuration.
    Useful for transparency and debugging.
    """
    from services.recruiting_service import SCORING_WEIGHTS, EXPERIENCE_KEYWORDS, AVAILABILITY_SCORES
    
    return {
        "success": True,
        "weights": SCORING_WEIGHTS,
        "experience_keywords_count": len(EXPERIENCE_KEYWORDS),
        "availability_keywords_count": len(AVAILABILITY_SCORES)
    }