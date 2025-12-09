from pydantic import BaseModel, Field
from typing import Optional
from datetime import date

class ManagerLogCreate(BaseModel):
    """Request model for creating a manager daily log"""
    restaurant_id: int
    log_date: Optional[date] = None  # Defaults to today if not provided
    overall_rating: int = Field(..., ge=1, le=5, description="Overall day rating 1-5")
    felt_smooth: Optional[bool] = None
    felt_understaffed: Optional[bool] = None
    felt_chaotic: Optional[bool] = None
    felt_overstaffed: Optional[bool] = None
    notes: Optional[str] = None

class ManagerLogResponse(BaseModel):
    """Response model for a manager log"""
    id: str
    restaurant_id: int
    manager_staff_id: str
    log_date: date
    overall_rating: int
    felt_smooth: Optional[bool]
    felt_understaffed: Optional[bool]
    felt_chaotic: Optional[bool]
    felt_overstaffed: Optional[bool]
    notes: Optional[str]
    created_at: str

class ManagerLogCreateResponse(BaseModel):
    """Response after creating a manager log"""
    success: bool
    log_id: str
    message: str