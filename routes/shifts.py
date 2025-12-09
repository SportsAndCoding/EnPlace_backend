from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional
from datetime import date, timedelta
from services.auth_service import verify_jwt_token as get_current_user
from services.shifts_service import ShiftsService
from models.shifts import ShiftCreate, ShiftUpdate, ShiftResponse, ShiftCreateResponse

router = APIRouter(prefix="/api/shifts", tags=["shifts"])

@router.post("", response_model=ShiftCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_shift(
    shift: ShiftCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new shift.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can create shifts"
        )
    
    # Verify restaurant access
    if current_user['restaurant_id'] != shift.restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    service = ShiftsService()
    
    try:
        result = await service.create_shift(
            shift_data=shift.dict(),
            created_by=current_user['staff_id']
        )
        
        return ShiftCreateResponse(
            success=True,
            shift_id=result['id'],
            message="Shift created"
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create shift: {str(e)}"
        )


@router.get("", response_model=List[ShiftResponse])
async def get_shifts(
    restaurant_id: int,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    staff_id: Optional[str] = Query(default=None),
    is_published: Optional[bool] = Query(default=None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get shifts for a restaurant.
    Defaults to current week if no dates provided.
    
    Optional filters:
    - staff_id: Filter to specific staff member
    - is_published: Filter by published status
    """
    # Verify restaurant access
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Default to current week (Mon-Sun)
    if not start_date:
        today = date.today()
        start_date = today - timedelta(days=today.weekday())  # Monday
    if not end_date:
        end_date = start_date + timedelta(days=6)  # Sunday
    
    service = ShiftsService()
    
    try:
        shifts = await service.get_shifts_by_restaurant(
            restaurant_id=restaurant_id,
            start_date=start_date,
            end_date=end_date,
            staff_id=staff_id,
            is_published=is_published
        )
        return shifts
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch shifts: {str(e)}"
        )


@router.get("/open")
async def get_open_shifts(
    restaurant_id: int,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get unassigned (open) shifts.
    Used for open shift marketplace.
    """
    # Verify restaurant access
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Default to next 14 days
    if not start_date:
        start_date = date.today()
    if not end_date:
        end_date = start_date + timedelta(days=14)
    
    service = ShiftsService()
    
    try:
        shifts = await service.get_open_shifts(
            restaurant_id=restaurant_id,
            start_date=start_date,
            end_date=end_date
        )
        
        return {
            "success": True,
            "open_shifts": shifts,
            "count": len(shifts)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch open shifts: {str(e)}"
        )


@router.get("/{shift_id}")
async def get_shift(
    shift_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get a single shift by ID"""
    service = ShiftsService()
    
    try:
        shift = await service.get_shift_by_id(
            shift_id=shift_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not shift:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shift not found"
            )
        
        return {
            "success": True,
            "shift": shift
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch shift: {str(e)}"
        )


@router.put("/{shift_id}")
async def update_shift(
    shift_id: int,
    shift: ShiftUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update an existing shift.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can update shifts"
        )
    
    service = ShiftsService()
    
    try:
        # Verify shift exists and belongs to this restaurant
        existing = await service.get_shift_by_id(
            shift_id=shift_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shift not found"
            )
        
        result = await service.update_shift(
            shift_id=shift_id,
            restaurant_id=current_user['restaurant_id'],
            update_data=shift.dict()
        )
        
        return {
            "success": True,
            "shift": result,
            "message": "Shift updated"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update shift: {str(e)}"
        )


@router.delete("/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift(
    shift_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a shift.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can delete shifts"
        )
    
    service = ShiftsService()
    
    try:
        # Verify shift exists
        existing = await service.get_shift_by_id(
            shift_id=shift_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shift not found"
            )
        
        await service.delete_shift(
            shift_id=shift_id,
            restaurant_id=current_user['restaurant_id']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete shift: {str(e)}"
        )