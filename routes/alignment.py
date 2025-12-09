from fastapi import APIRouter, Depends, HTTPException, Query
from services.auth_service import verify_jwt_token as get_current_user
from services.alignment_service import AlignmentService

router = APIRouter(prefix="/api/alignment", tags=["alignment"])

@router.get("")
async def get_alignment(
    restaurant_id: int,
    days: int = Query(default=7, ge=1, le=30),
    current_user: dict = Depends(get_current_user)
):
    """
    Get Staff-Manager Alignment scores.
    
    Compares staff check-ins with manager daily logs to identify
    perception gaps and calculate alignment scores.
    
    Query params:
    - restaurant_id: Restaurant to analyze
    - days: Number of days to analyze (default 7, max 30)
    
    Returns:
    - sma_score: Overall alignment score (0-100)
    - emotional_alignment: How staff felt overall
    - operational_alignment: How closely manager and staff saw same days
    - perception_gaps: Specific days with misalignment
    - role_cluster_risk: Risk scores by position/role
    """
    # Verify restaurant access
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(
            status_code=403, 
            detail="Access denied"
        )
    
    service = AlignmentService()
    
    try:
        alignment_data = await service.get_alignment_data(
            restaurant_id=restaurant_id,
            days=days
        )
        
        return {
            "success": True,
            **alignment_data
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to calculate alignment: {str(e)}"
        )