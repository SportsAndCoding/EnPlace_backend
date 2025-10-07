from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from services.auth_service import verify_jwt_token as get_current_user
from services.scheduling_service import SchedulingService

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

class OptimizeRequest(BaseModel):
    restaurant_id: int
    pay_period_start: str  # ISO format: "2025-10-14"
    pay_period_end: str    # ISO format: "2025-10-27"

@router.post("/optimize")
async def optimize_schedule(
    request: OptimizeRequest,
    current_user: dict = Depends(get_current_user)
):
    """Run AI schedule optimization"""
    
    # Verify user has access to this restaurant
    if current_user['restaurant_id'] != request.restaurant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    service = SchedulingService()
    
    try:
        result = await service.optimize_schedule(
            restaurant_id=request.restaurant_id,
            pay_period_start=request.pay_period_start,
            pay_period_end=request.pay_period_end,
            created_by=current_user['staff_id']
        )
        
        return {
            "success": True,
            **result
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Optimization failed: {str(e)}"
        )
    

@router.get("/{schedule_id}/shifts")
async def get_schedule_shifts(
    schedule_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get all shifts for a schedule"""
    service = SchedulingService()
    
    try:
        shifts = await service.get_shifts(schedule_id, current_user['restaurant_id'])
        return {
            "success": True,
            "shifts": shifts
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch shifts: {str(e)}"
        )
    
@router.get("/latest")
async def get_latest_schedule(
    restaurant_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get the most recent schedule for a restaurant"""
    
    # Verify user has access to this restaurant
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    service = SchedulingService()
    
    try:
        latest_schedule = await service.get_latest_schedule(restaurant_id)
        
        if not latest_schedule:
            return {
                "success": False,
                "message": "No schedules found for this restaurant"
            }
        
        return {
            "success": True,
            "schedule": latest_schedule
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch latest schedule: {str(e)}"
        )
    
from models.schedule_edits import UpdateScheduleRequest, ShiftChange

@router.put("/{schedule_id}/update")
async def update_schedule_shifts(
    schedule_id: str,
    request: UpdateScheduleRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Apply manual edits to a schedule
    
    Body example:
    {
      "changes": [
        {
          "action": "remove",
          "shift_id": "uuid-of-shift-to-remove"
        },
        {
          "action": "add",
          "staff_id": "STAFF001",
          "date": "2025-10-13",
          "start_time": "18:00:00",
          "end_time": "22:00:00",
          "position": "Server"
        }
      ]
    }
    """
    
    service = SchedulingService()
    
    try:
        result = await service.update_schedule_shifts(
            schedule_id=schedule_id,
            restaurant_id=current_user['restaurant_id'],
            changes=[change.dict() for change in request.changes],
            updated_by=current_user['staff_id']
        )
        
        return {
            "success": True,
            **result
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update schedule: {str(e)}"
        )
    
@router.post("/{schedule_id}/approve")
async def approve_schedule(
    schedule_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Approve a generated schedule
    
    Process:
    1. Converts draft (generated_schedules/shifts) to approved tables
    2. Calculates coverage gaps with priority
    3. Returns summary for Step 5 (Coverage Gaps UI)
    
    Returns:
        - approved_schedule_id: UUID of approved schedule
        - shifts_count: Number of shifts approved
        - total_cost: Final labor cost
        - gaps: Organized by priority (mission_critical, emergency, standard)
    """
    service = SchedulingService()
    
    try:
        result = await service.approve_schedule(
            schedule_id=schedule_id,
            approved_by=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id']
        )
        
        return {
            "success": True,
            **result
        }
    
    except Exception as e:
        import traceback
        print(f"Approval failed: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Failed to approve schedule: {str(e)}"
        )