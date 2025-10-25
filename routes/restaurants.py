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
    
@router.get("/restaurants/{restaurant_id}/operating-settings")
async def get_operating_settings(
    restaurant_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get restaurant operating hours and settings"""
    
    result = await supabase.table('restaurant_operating_settings')\
        .select('*')\
        .eq('restaurant_id', restaurant_id)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Operating settings not found")
    
    return result.data[0]


@router.post("/restaurants/{restaurant_id}/operating-settings")
async def update_operating_settings(
    restaurant_id: int,
    settings: dict,
    current_user: dict = Depends(get_current_user),
    _: bool = Depends(require_edit_permission)
):
    """Update restaurant operating settings"""
    
    # Validate required fields
    required_fields = [
        'prep_start_time', 'prep_positions', 'prep_staff_count',
        'doors_open_time', 'doors_close_time', 'last_seating_time',
        'kitchen_close_time', 'cleanup_positions', 'cleanup_staff_count'
    ]
    
    for field in required_fields:
        if field not in settings:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
    
    result = await supabase.table('restaurant_operating_settings')\
        .upsert({
            'restaurant_id': restaurant_id,
            **settings
        })\
        .execute()
    
    return {'success': True, 'data': result.data[0]}