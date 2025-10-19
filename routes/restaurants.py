from fastapi import APIRouter, Depends, HTTPException
from services.auth_service import verify_jwt_token as get_current_user
from database.supabase_client import get_supabase

router = APIRouter(prefix="/api/restaurants", tags=["restaurants"])

@router.get("/{restaurant_id}")
async def get_restaurant(
    restaurant_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get restaurant settings including operating hours and staffing ratios"""
    
    # Verify user has access to this restaurant
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    supabase = get_supabase()
    
    try:
        response = supabase.from_('restaurants') \
            .select('id, name, operating_hours, staffing_ratios, role_ratios, allow_overtime') \
            .eq('id', restaurant_id) \
            .single() \
            .execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Restaurant not found")
        
        return {
            "success": True,
            **response.data
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch restaurant: {str(e)}"
        )