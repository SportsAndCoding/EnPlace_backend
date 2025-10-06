from fastapi import APIRouter, Depends, HTTPException
from services.auth_service import verify_jwt_token as get_current_user
from services.demand_service import DemandService

router = APIRouter(prefix="/api/demand", tags=["demand"])

@router.get("/patterns")
async def get_demand_patterns(
    restaurant_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get default demand patterns for a restaurant"""
    
    # Verify user has access to this restaurant
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    service = DemandService()
    patterns = await service.get_patterns(restaurant_id)
    
    return patterns