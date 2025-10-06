from fastapi import APIRouter, Depends, HTTPException
from services.auth_service import verify_jwt_token as get_current_user
from services.demand_service import DemandService
from database.supabase_client import get_supabase
from typing import List, Dict

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

@router.post("/overrides")
async def save_demand_overrides(
    request: Dict,
    current_user: dict = Depends(get_current_user)
):
    """Save manager's demand adjustments for this pay period"""
    
    restaurant_id = request.get('restaurant_id')
    pay_period_start = request.get('pay_period_start')
    pay_period_end = request.get('pay_period_end')
    overrides = request.get('overrides', [])
    
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    supabase = get_supabase()
    
    # Delete existing overrides for this pay period
    supabase.from_('pay_period_demand_overrides') \
        .delete() \
        .eq('restaurant_id', restaurant_id) \
        .eq('pay_period_start', pay_period_start) \
        .eq('pay_period_end', pay_period_end) \
        .execute()
    
    # Insert new overrides
    records = []
    for override in overrides:
        records.append({
            'restaurant_id': restaurant_id,
            'pay_period_start': pay_period_start,
            'pay_period_end': pay_period_end,
            'day_type': override['day_type'],
            'hour': override['hour'],
            'covers_per_hour': override['covers_per_hour']
        })
    
    if records:
        supabase.from_('pay_period_demand_overrides') \
            .insert(records) \
            .execute()
    
    return {"success": True, "overrides_saved": len(records)}