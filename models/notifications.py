from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class NotificationCreate(BaseModel):
    """Request model for creating a notification"""
    recipient_id: Optional[str] = None  # None = broadcast to all managers
    restaurant_id: int
    title: str
    message: str
    type: str  # 'swap_request', 'pto_request', 'coverage_gap', 'escalation', 'system'
    related_id: Optional[str] = None  # Link to related entity

class NotificationUpdate(BaseModel):
    """Request model for updating a notification"""
    is_read: Optional[bool] = None

class NotificationResponse(BaseModel):
    """Response model for a notification"""
    id: str
    recipient_id: Optional[str]
    restaurant_id: int
    title: str
    message: str
    type: str
    related_id: Optional[str]
    is_read: bool
    created_at: datetime

class NotificationCreateResponse(BaseModel):
    """Response after creating a notification"""
    success: bool
    notification_id: str
    message: str