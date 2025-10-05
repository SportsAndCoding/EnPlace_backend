from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from services.auth_service import get_current_user
from services.constraints_service import ConstraintsService
from models.constraints import RecurringConstraintCreate, PTOConstraintCreate, ConstraintResponse

router = APIRouter(prefix="/api/constraints", tags=["constraints"])

@router.get("", response_model=List[ConstraintResponse])
async def get_constraints(
    restaurant_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get all active constraints for a restaurant"""
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    service = ConstraintsService()
    return await service.get_constraints(restaurant_id)

@router.post("/recurring", response_model=ConstraintResponse, status_code=status.HTTP_201_CREATED)
async def create_recurring_constraint(
    constraint: RecurringConstraintCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a recurring scheduling constraint"""
    if current_user['restaurant_id'] != constraint.restaurant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Add created_by from token
    constraint_dict = constraint.dict()
    constraint_dict['created_by'] = current_user['staff_id']
    constraint = RecurringConstraintCreate(**constraint_dict)
    
    service = ConstraintsService()
    return await service.create_recurring_constraint(constraint)

@router.post("/pto", response_model=ConstraintResponse, status_code=status.HTTP_201_CREATED)
async def create_pto_constraint(
    constraint: PTOConstraintCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a PTO constraint"""
    if current_user['restaurant_id'] != constraint.restaurant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    constraint_dict = constraint.dict()
    constraint_dict['created_by'] = current_user['staff_id']
    constraint = PTOConstraintCreate(**constraint_dict)
    
    service = ConstraintsService()
    return await service.create_pto_constraint(constraint)

@router.delete("/{constraint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_constraint(
    constraint_id: str,  # Changed from int to str (UUID)
    current_user: dict = Depends(get_current_user)
):
    """Delete a constraint (soft delete)"""
    service = ConstraintsService()
    
    constraint = await service.get_constraint_by_id(constraint_id)
    if not constraint or constraint['restaurant_id'] != current_user['restaurant_id']:
        raise HTTPException(status_code=404, detail="Constraint not found")
    
    await service.delete_constraint(constraint_id)