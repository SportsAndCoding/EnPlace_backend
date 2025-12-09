"""
Dashboard Route - Single endpoint for manager-home.html
"""

from fastapi import APIRouter, Depends, HTTPException
from services.dashboard_service import get_dashboard_data
from services.auth_service import verify_jwt_token as get_current_user

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("")
async def get_dashboard(current_user: dict = Depends(get_current_user)):
    """
    Get all dashboard data for manager-home.html.
    Single endpoint, single round-trip.
    """
    try:
        restaurant_id = current_user.get("restaurant_id")
        if not restaurant_id:
            raise HTTPException(status_code=400, detail="No restaurant_id in token")
        
        data = get_dashboard_data(restaurant_id)
        return data
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))