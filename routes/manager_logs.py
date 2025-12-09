from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List
from datetime import date, timedelta
from services.auth_service import verify_jwt_token as get_current_user
from services.manager_logs_service import ManagerLogsService
from models.manager_logs import ManagerLogCreate, ManagerLogResponse, ManagerLogCreateResponse

router = APIRouter(prefix="/api/manager-logs", tags=["manager-logs"])

@router.post("", response_model=ManagerLogCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_manager_log(
    log: ManagerLogCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Submit a daily manager perception log.
    Only managers can submit logs.
    One log per restaurant per day.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Only managers can submit daily logs"
        )
    
    # Verify restaurant access
    if current_user['restaurant_id'] != log.restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Access denied"
        )
    
    service = ManagerLogsService()
    
    try:
        result = await service.create_log(
            log_data=log.dict(),
            manager_staff_id=current_user['staff_id']
        )
        
        return ManagerLogCreateResponse(
            success=True,
            log_id=str(result['id']),
            message="Daily log recorded"
        )
        
    except ValueError as e:
        # Already logged today
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create log: {str(e)}"
        )


@router.get("", response_model=List[ManagerLogResponse])
async def get_manager_logs(
    restaurant_id: int,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get manager logs for a restaurant within a date range.
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
    
    service = ManagerLogsService()
    
    try:
        logs = await service.get_logs_by_restaurant(
            restaurant_id=restaurant_id,
            start_date=start_date,
            end_date=end_date
        )
        return logs
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch logs: {str(e)}"
        )


@router.get("/today")
async def get_today_log(
    current_user: dict = Depends(get_current_user)
):
    """
    Check if restaurant already has a manager log for today.
    Used by manager portal to show/hide log button.
    """
    service = ManagerLogsService()
    
    try:
        log = await service.get_today_log(current_user['restaurant_id'])
        
        if log:
            return {
                "success": True,
                "logged": True,
                "log": log
            }
        else:
            return {
                "success": True,
                "logged": False,
                "log": None
            }
            
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check status: {str(e)}"
        )