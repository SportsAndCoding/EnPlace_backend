from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from services.auth_service import verify_jwt_token as get_current_user, require_edit_permission
from database.supabase_client import get_supabase


class RestaurantUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    timezone: Optional[str] = None
    operating_hours: Optional[Dict[str, Any]] = None
    pay_frequency: Optional[str] = None
    next_pay_date: Optional[str] = None

router = APIRouter(prefix="/api/restaurants", tags=["restaurants"])

@router.get("/{organization_id}")
async def get_restaurant(
    organization_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get restaurant settings including operating hours and staffing ratios"""
    
    # Verify user has access to this restaurant
    if current_user['organization_id'] != organization_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    supabase = get_supabase()
    
    try:
        response = supabase.from_('restaurants') \
            .select('id, name, address, timezone, operating_hours, staffing_ratios, role_ratios, allow_overtime, status, pay_frequency, next_pay_date') \
            .eq('id', organization_id) \
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


@router.put("/{organization_id}")
async def update_restaurant(
    organization_id: int,
    update_data: RestaurantUpdate,
    current_user: dict = Depends(require_edit_permission)
):
    """Update restaurant settings"""
    if current_user['organization_id'] != organization_id:
        raise HTTPException(status_code=403, detail="Access denied")

    supabase = get_supabase()

    try:
        updates = {}
        if update_data.name is not None:
            updates['name'] = update_data.name
        if update_data.address is not None:
            updates['address'] = update_data.address
        if update_data.phone is not None:
            updates['phone'] = update_data.phone
        if update_data.timezone is not None:
            updates['timezone'] = update_data.timezone
        if update_data.operating_hours is not None:
            updates['operating_hours'] = update_data.operating_hours
        if update_data.pay_frequency is not None:
            updates['pay_frequency'] = update_data.pay_frequency
        if update_data.next_pay_date is not None:
            updates['next_pay_date'] = update_data.next_pay_date

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.from_('restaurants') \
            .update(updates) \
            .eq('id', organization_id) \
            .execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Restaurant not found")

        return {
            "success": True,
            "message": "Restaurant settings updated",
            "restaurant": response.data[0]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update restaurant: {str(e)}"
        )


@router.get("/restaurants/{organization_id}/operating-settings")
async def get_operating_settings(
    organization_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get restaurant operating hours and settings"""
    
    result = await supabase.table('restaurant_operating_settings')\
        .select('*')\
        .eq('organization_id', organization_id)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Operating settings not found")
    
    return result.data[0]


@router.get("/{organization_id}/operating-settings")
async def get_operating_settings(
    organization_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get restaurant operating hours and settings"""
    
    supabase = get_supabase()
    
    result = supabase.table('restaurant_operating_settings')\
        .select('*')\
        .eq('organization_id', organization_id)\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Operating settings not found")
    
    return result.data[0]


@router.post("/{organization_id}/operating-settings")
async def update_operating_settings(
    organization_id: int,
    settings: dict,
    current_user: dict = Depends(get_current_user)
):
    """Update restaurant operating settings"""
    
    supabase = get_supabase()
    
    # Validate required fields
    required_fields = [
        'prep_start_time', 'prep_positions', 'prep_staff_count',
        'doors_open_time', 'doors_close_time', 'last_seating_time',
        'kitchen_close_time', 'cleanup_positions', 'cleanup_staff_count'
    ]
    
    for field in required_fields:
        if field not in settings:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
    
    result = supabase.table('restaurant_operating_settings')\
        .upsert({
            'organization_id': organization_id,
            **settings
        })\
        .execute()
    
    return {'success': True, 'data': result.data[0]}

@router.get("/{organization_id}/modules")
async def get_restaurant_modules(
    organization_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get enabled modules for a restaurant - reads from restaurants.has_* columns (source of truth)"""
    if current_user['organization_id'] != organization_id:
        raise HTTPException(status_code=403, detail="Access denied")
    supabase = get_supabase()
    try:
        result = supabase.table("organizations") \
            .select("has_open_shift_marketplace, has_shift_swap, has_schedule_optimizer, has_stable_hire, has_house_guardian") \
            .eq("id", organization_id) \
            .single() \
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Restaurant not found")
        
        r = result.data
        modules = {
            'openShifts': r.get('has_open_shift_marketplace', False),
            'shiftSwap': r.get('has_shift_swap', False),
            'schedule': r.get('has_schedule_optimizer', False),
            'stableHire': r.get('has_stable_hire', False),
            'houseGuardian': r.get('has_house_guardian', False),
            'aime': True  # Always included with base subscription
        }
        
        return {
            "success": True,
            "organization_id": organization_id,
            "modules": modules
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch modules: {str(e)}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch modules: {str(e)}"
        )