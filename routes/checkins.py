from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List
from datetime import date, timedelta
from services.auth_service import verify_jwt_token as get_current_user
from services.checkins_service import CheckinsService
from models.checkins import CheckinCreate, CheckinResponse, CheckinCreateResponse

router = APIRouter(prefix="/api/checkins", tags=["checkins"])

@router.post("", response_model=CheckinCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_checkin(
    checkin: CheckinCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Submit a daily mood check-in.
    Staff can only check in for themselves.
    One check-in per day per staff member.
    """
    # Staff can only submit check-ins for themselves
    if current_user['staff_id'] != checkin.staff_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Can only submit check-ins for yourself"
        )
    
    # Verify restaurant access
    if current_user['restaurant_id'] != checkin.restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Access denied"
        )
    
    service = CheckinsService()
    
    try:
        result = await service.create_checkin(checkin.dict())
        
        return CheckinCreateResponse(
            success=True,
            checkin_id=str(result['id']),
            message="Check-in recorded"
        )
        
    except ValueError as e:
        # Already checked in today
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create check-in: {str(e)}"
        )


@router.get("", response_model=List[CheckinResponse])
async def get_checkins(
    restaurant_id: int,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get check-ins for a restaurant within a date range.
    Defaults to last 7 days if no dates provided.
    """
    # Verify restaurant access
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Access denied"
        )
    
    # Default to last 7 days
    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=7)
    
    service = CheckinsService()
    
    try:
        checkins = await service.get_checkins_by_restaurant(
            restaurant_id=restaurant_id,
            start_date=start_date,
            end_date=end_date
        )
        return checkins
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch check-ins: {str(e)}"
        )


@router.get("/today")
async def get_my_today_checkin(
    current_user: dict = Depends(get_current_user)
):
    """
    Check if current user already checked in today.
    Used by staff portal to show/hide check-in button.
    """
    service = CheckinsService()
    
    try:
        checkin = await service.get_today_checkin(current_user['staff_id'])
        
        if checkin:
            return {
                "success": True,
                "checked_in": True,
                "checkin": checkin
            }
        else:
            return {
                "success": True,
                "checked_in": False,
                "checkin": None
            }
            
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check status: {str(e)}"
        )