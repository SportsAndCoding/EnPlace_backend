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
        organization_id = current_user.get("organization_id")
        if not organization_id:
            raise HTTPException(status_code=400, detail="No organization_id in token")
        staff_id = current_user.get("staff_id")
        data = get_dashboard_data(organization_id, staff_id=staff_id)
        return data
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))