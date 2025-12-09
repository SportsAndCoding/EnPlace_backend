from pydantic import BaseModel, Field
from typing import Optional
from datetime import date

class CheckinCreate(BaseModel):
    """Request model for creating a daily check-in"""
    staff_id: str
    restaurant_id: int
    mood_emoji: int = Field(..., ge=1, le=5, description="Mood rating 1-5")
    felt_safe: Optional[bool] = None
    felt_fair: Optional[bool] = None
    felt_respected: Optional[bool] = None
    notes: Optional[str] = None

class CheckinResponse(BaseModel):
    """Response model for a check-in"""
    id: str
    staff_id: str
    restaurant_id: int
    checkin_date: date
    mood_emoji: int
    felt_safe: Optional[bool]
    felt_fair: Optional[bool]
    felt_respected: Optional[bool]
    notes: Optional[str]
    created_at: str

class CheckinCreateResponse(BaseModel):
    """Response after creating a check-in"""
    success: bool
    checkin_id: str
    message: str