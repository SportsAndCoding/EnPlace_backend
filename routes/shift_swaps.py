from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Optional
from services.auth_service import verify_jwt_token as get_current_user
from services.shift_swaps_service import ShiftSwapsService
from services.feature_gate import require_feature

router = APIRouter(prefix="/api/shift-swaps", tags=["shift-swaps"])


@router.get("")
async def get_shift_swaps(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    include_past: bool = Query(default=False),
    current_user: dict = Depends(require_feature("shift_swap"))
):
    """
    Get shift swap requests for the restaurant.
    
    Filters:
    - status: pending, approved, rejected, cancelled
    - include_past: if False, only shows swaps for future shifts
    """
    service = ShiftSwapsService()
    
    try:
        swaps = await service.get_swaps(
            organization_id=current_user['organization_id'],
            status_filter=status_filter,
            include_past=include_past
        )
        
        return {
            "success": True,
            "swaps": swaps,
            "count": len(swaps)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch shift swaps: {str(e)}"
        )


@router.get("/{swap_id}")
async def get_shift_swap(
    swap_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get a single shift swap by ID"""
    service = ShiftSwapsService()
    
    try:
        swap = await service.get_swap_by_id(
            swap_id=swap_id,
            organization_id=current_user['organization_id']
        )
        
        if not swap:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shift swap not found"
            )
        
        return {
            "success": True,
            "swap": swap
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch shift swap: {str(e)}"
        )


@router.put("/{swap_id}/approve")
async def approve_swap(
    swap_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Approve a shift swap request.
    Managers only.
    """
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can approve swaps"
        )
    
    service = ShiftSwapsService()
    
    try:
        result = await service.approve_swap(
            swap_id=swap_id,
            organization_id=current_user['organization_id'],
            decided_by=current_user['staff_id']
        )
        
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shift swap not found"
            )
        
        return {
            "success": True,
            "message": "Swap approved",
            "swap": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to approve swap: {str(e)}"
        )


@router.put("/{swap_id}/reject")
async def reject_swap(
    swap_id: int,
    notes: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Reject a shift swap request.
    Managers only.
    """
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can reject swaps"
        )
    
    service = ShiftSwapsService()
    
    try:
        result = await service.reject_swap(
            swap_id=swap_id,
            organization_id=current_user['organization_id'],
            decided_by=current_user['staff_id'],
            notes=notes
        )
        
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shift swap not found"
            )
        
        return {
            "success": True,
            "message": "Swap rejected",
            "swap": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reject swap: {str(e)}"
        )