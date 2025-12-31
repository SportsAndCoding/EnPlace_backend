"""
HOUSE GUARDIAN API ROUTES
=========================
Endpoints for House Guardian alerts management.

Add to app.py:
    from routes.house_guardian import router as house_guardian_router
    app.include_router(house_guardian_router)
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel

from services.auth_service import verify_jwt_token
from services.feature_gate import require_feature
from config.settings import SUPABASE_URL, SUPABASE_KEY
from supabase import create_client

router = APIRouter(prefix="/api/house-guardian", tags=["house-guardian"])

# Initialize Supabase
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ═══════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════

class AlertUpdateRequest(BaseModel):
    status: Optional[str] = None  # active, investigating, resolved, dismissed
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[str] = None
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution_notes: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/alerts")
async def get_alerts(
    status: Optional[str] = None,
    current_staff: Dict[str, Any] = Depends(require_feature("house_guardian"))
):
    """
    Get House Guardian alerts for the current restaurant.
    
    Optional filters:
    - status: active, investigating, resolved, dismissed
    """
    restaurant_id = current_staff.get("restaurant_id")
    
    if not restaurant_id:
        raise HTTPException(status_code=400, detail="Restaurant ID required")
    
    try:
        query = supabase.table("house_guardian_alerts") \
            .select("*") \
            .eq("restaurant_id", restaurant_id) \
            .order("created_at", desc=True)
        
        if status:
            query = query.eq("status", status)
        
        result = query.execute()
        
        return {
            "success": True,
            "alerts": result.data or []
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts/{alert_id}")
async def get_alert(
    alert_id: str,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Get a specific House Guardian alert by ID.
    """
    restaurant_id = current_staff.get("restaurant_id")
    
    try:
        result = supabase.table("house_guardian_alerts") \
            .select("*") \
            .eq("id", alert_id) \
            .eq("restaurant_id", restaurant_id) \
            .single() \
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Alert not found")
        
        return result.data
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/alerts/{alert_id}")
async def update_alert(
    alert_id: str,
    update_data: AlertUpdateRequest,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Update a House Guardian alert (acknowledge, investigate, resolve, dismiss).
    """
    restaurant_id = current_staff.get("restaurant_id")
    staff_id = current_staff.get("staff_id")
    
    try:
        # Verify alert belongs to this restaurant
        existing = supabase.table("house_guardian_alerts") \
            .select("id") \
            .eq("id", alert_id) \
            .eq("restaurant_id", restaurant_id) \
            .single() \
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Alert not found")
        
        # Build update dict
        update_dict = {}
        
        if update_data.status:
            update_dict["status"] = update_data.status
        
        if update_data.acknowledged_at:
            update_dict["acknowledged_at"] = update_data.acknowledged_at
            update_dict["acknowledged_by"] = staff_id
        
        if update_data.resolved_at:
            update_dict["resolved_at"] = update_data.resolved_at
            update_dict["resolved_by"] = staff_id
        
        if update_data.resolution_notes:
            update_dict["resolution_notes"] = update_data.resolution_notes
        
        update_dict["updated_at"] = datetime.utcnow().isoformat()
        
        # Perform update
        result = supabase.table("house_guardian_alerts") \
            .update(update_dict) \
            .eq("id", alert_id) \
            .execute()
        
        return {
            "success": True,
            "alert": result.data[0] if result.data else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weekly-report")
async def get_weekly_report(
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Get the latest weekly House Guardian report.
    This is the "all clear" or operational insights digest.
    """
    restaurant_id = current_staff.get("restaurant_id")
    
    try:
        result = supabase.table("house_guardian_weekly_reports") \
            .select("*") \
            .eq("restaurant_id", restaurant_id) \
            .order("week_end", desc=True) \
            .limit(1) \
            .execute()
        
        if not result.data:
            return {
                "success": True,
                "report": None,
                "message": "No weekly report available yet"
            }
        
        return {
            "success": True,
            "report": result.data[0]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))