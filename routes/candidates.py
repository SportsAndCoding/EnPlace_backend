from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Optional
from services.auth_service import verify_jwt_token as get_current_user
from services.candidates_service import CandidatesService
from models.candidates import (
    CandidateCreate,
    CandidateUpdate,
    ScenarioRankings,
    CandidateCreateResponse
)

router = APIRouter(prefix="/api/candidates", tags=["candidates"])

@router.post("", response_model=CandidateCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_candidate(
    candidate: CandidateCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new candidate in the hiring pipeline.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can create candidates"
        )
    
    # Verify restaurant access
    if current_user['restaurant_id'] != candidate.restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    service = CandidatesService()
    
    try:
        result = await service.create_candidate(candidate.dict())
        
        return CandidateCreateResponse(
            success=True,
            candidate_id=str(result['id']),
            candidate_code=result['candidate_code'],
            message="Candidate created"
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create candidate: {str(e)}"
        )


@router.get("")
async def get_candidates(
    restaurant_id: int,
    status: Optional[str] = Query(default=None),
    role: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get candidates for a restaurant.
    
    Optional filters:
    - status: 'open', 'interviewed', 'hired', 'rejected'
    - role: 'server', 'line_cook', 'dishwasher', etc.
    """
    # Verify restaurant access
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    service = CandidatesService()
    
    try:
        candidates = await service.get_candidates_by_restaurant(
            restaurant_id=restaurant_id,
            status=status,
            role=role
        )
        
        stats = await service.get_stats(restaurant_id)
        
        return {
            "success": True,
            "candidates": candidates,
            "count": len(candidates),
            "stats": stats
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch candidates: {str(e)}"
        )


@router.get("/{candidate_id}")
async def get_candidate(
    candidate_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a single candidate"""
    service = CandidatesService()
    
    try:
        candidate = await service.get_candidate_by_id(
            candidate_id=candidate_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not candidate:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Candidate not found"
            )
        
        return {
            "success": True,
            "candidate": candidate
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch candidate: {str(e)}"
        )


@router.put("/{candidate_id}")
async def update_candidate(
    candidate_id: str,
    candidate: CandidateUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update a candidate.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can update candidates"
        )
    
    service = CandidatesService()
    
    try:
        # Verify candidate exists
        existing = await service.get_candidate_by_id(
            candidate_id=candidate_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Candidate not found"
            )
        
        result = await service.update_candidate(
            candidate_id=candidate_id,
            restaurant_id=current_user['restaurant_id'],
            update_data=candidate.dict()
        )
        
        return {
            "success": True,
            "candidate": result,
            "message": "Candidate updated"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update candidate: {str(e)}"
        )


@router.post("/{candidate_id}/score")
async def score_candidate(
    candidate_id: str,
    rankings: ScenarioRankings,
    current_user: dict = Depends(get_current_user)
):
    """
    Calculate stability score from scenario rankings.
    Managers only.
    
    Rankings should be a dict like:
    {
        "break_room": "alex",
        "expo_backup": "jordan",
        "schedule_surprise": "alex",
        ...
    }
    
    Valid choices: "alex", "jordan", "taylor"
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can score candidates"
        )
    
    service = CandidatesService()
    
    try:
        # Verify candidate exists
        existing = await service.get_candidate_by_id(
            candidate_id=candidate_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Candidate not found"
            )
        
        result = await service.score_candidate(
            candidate_id=candidate_id,
            restaurant_id=current_user['restaurant_id'],
            scenario_rankings=rankings.scenario_rankings
        )
        
        return {
            "success": True,
            "candidate": result,
            "stability_score": result["stability_score"],
            "cliff_risk_percent": result["cliff_risk_percent"],
            "recommendation": result["recommendation"],
            "fingerprint": result["fingerprint"],
            "message": "Candidate scored"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to score candidate: {str(e)}"
        )