from fastapi import APIRouter, Depends, Request
from typing import Dict, Any
from models.staff import StaffCreate, StaffUpdate
from services.auth_service import verify_jwt_token, require_edit_permission
from services.staff_service import (
    get_staff_list,
    create_staff_member,
    update_staff_member,
    deactivate_staff_member
)

router = APIRouter()

@router.get("")
async def list_staff(current_staff: Dict[str, Any] = Depends(verify_jwt_token)):
    """Get all staff for the restaurant"""
    restaurant_id = current_staff["restaurant_id"]
    staff = await get_staff_list(restaurant_id)
    
    return {
        "success": True,
        "staff": staff
    }

@router.post("")
async def create_staff(
    staff_data: StaffCreate,
    request: Request,
    current_staff: Dict[str, Any] = Depends(require_edit_permission)
):
    """Create new staff member"""
    staff = await create_staff_member(
        staff_data=staff_data,
        created_by=current_staff["staff_id"],
        restaurant_id=current_staff["restaurant_id"],
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent")
    )
    
    return {
        "success": True,
        "message": f"{staff_data.name} has been added successfully",
        "staff": staff
    }

@router.put("/{staff_id}")
async def update_staff(
    staff_id: str,
    staff_data: StaffUpdate,
    request: Request,
    current_staff: Dict[str, Any] = Depends(require_edit_permission)
):
    """Update existing staff member"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"===== UPDATE REQUEST =====")
    logger.info(f"Staff ID: {staff_id}")
    logger.info(f"Request body: {staff_data.dict()}")
    logger.info(f"Changed by: {current_staff['staff_id']}")
    
    try:
        staff = await update_staff_member(
            staff_id=staff_id,
            staff_data=staff_data,
            changed_by=current_staff["staff_id"],
            restaurant_id=current_staff["restaurant_id"],
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent")
        )
        
        logger.info(f"Update successful for {staff_id}")
        
        return {
            "success": True,
            "message": f"{staff_data.name}'s information has been updated",
            "staff": staff
        }
    except Exception as e:
        logger.error(f"Update failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{staff_id}/deactivate")
async def deactivate_staff(
    staff_id: str,
    request: Request,
    reason: str,
    last_work_date: str,
    notes: str = None,
    current_staff: Dict[str, Any] = Depends(require_edit_permission)
):
    """Deactivate staff member (soft delete)"""
    staff = await deactivate_staff_member(
        staff_id=staff_id,
        reason=reason,
        last_work_date=last_work_date,
        notes=notes,
        changed_by=current_staff["staff_id"],
        restaurant_id=current_staff["restaurant_id"],
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent")
    )
    
    return {
        "success": True,
        "message": "Staff member has been deactivated",
        "staff": staff
    }