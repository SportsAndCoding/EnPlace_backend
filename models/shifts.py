from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime

class ShiftCreate(BaseModel):
    """Request model for creating a shift"""
    restaurant_id: int
    staff_id: Optional[str] = None  # Can be unassigned (open shift)
    shift_date: date
    scheduled_start: datetime
    scheduled_end: datetime
    shift_type: str  # 'morning', 'afternoon', 'evening', 'closing'
    day_type: str  # 'weekday', 'weekend'
    is_published: bool = False

class ShiftUpdate(BaseModel):
    """Request model for updating a shift"""
    staff_id: Optional[str] = None
    shift_date: Optional[date] = None
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    shift_type: Optional[str] = None
    day_type: Optional[str] = None
    is_published: Optional[bool] = None

class ShiftResponse(BaseModel):
    """Response model for a shift"""
    id: int
    restaurant_id: int
    staff_id: Optional[str]
    shift_date: date
    scheduled_start: datetime
    scheduled_end: datetime
    shift_type: str
    day_type: str
    is_published: bool
    created_by: Optional[str]
    created_at: datetime

class ShiftCreateResponse(BaseModel):
    """Response after creating a shift"""
    success: bool
    shift_id: int
    message: str