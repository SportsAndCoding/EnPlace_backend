from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class EscalationCreate(BaseModel):
    """Request model for creating an escalation event"""
    restaurant_id: int
    event_type: str  # 'burnout', 'fairness', 'retention', 'alignment'
    severity: str = "moderate"  # 'mild', 'moderate', 'serious', 'critical'
    severity_score: Optional[int] = Field(None, ge=0, le=100)
    primary_staff_id: Optional[str] = None
    affected_role: Optional[str] = None
    trigger_reason: str
    source_type: str = "mood"  # 'mood' (anonymize) or 'schedule' (can name)
    next_action_deadline: Optional[datetime] = None

class EscalationUpdate(BaseModel):
    """Request model for updating an escalation event"""
    status: Optional[str] = None  # 'active', 'escalated', 'monitoring', 'resolved'
    current_step: Optional[int] = Field(None, ge=1, le=7)
    severity: Optional[str] = None
    severity_score: Optional[int] = Field(None, ge=0, le=100)
    next_action_deadline: Optional[datetime] = None
    resolution: Optional[str] = None  # 'retained', 'departed_voluntarily', 'closed'

class HistoryEntryCreate(BaseModel):
    """Request model for adding a history entry"""
    step_number: int = Field(..., ge=1, le=7)
    action_taken: str
    actor_name: Optional[str] = None  # For external actors or system

class EscalationResponse(BaseModel):
    """Response model for an escalation event"""
    id: str
    restaurant_id: int
    event_type: str
    severity: str
    severity_score: Optional[int]
    status: str
    current_step: int
    primary_staff_id: Optional[str]
    affected_role: Optional[str]
    trigger_reason: str
    source_type: Optional[str] = None  # 'mood' or 'schedule'
    triggered_at: datetime
    next_action_deadline: Optional[datetime]
    resolved_at: Optional[datetime]
    resolution: Optional[str]
    created_by: Optional[str]
    auto_created: bool
    created_at: datetime
    updated_at: datetime

class EscalationCreateResponse(BaseModel):
    """Response after creating an escalation"""
    success: bool
    escalation_id: str
    message: str