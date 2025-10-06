from pydantic import BaseModel, Field
from typing import Optional
from datetime import date
from uuid import UUID

class ConstraintBase(BaseModel):
    restaurant_id: int
    staff_id: str
    rule_type: str  # Changed from constraint_type

class RecurringConstraintCreate(ConstraintBase):
    rule_type: str = 'recurring'
    description: str
    recurrence_type: str
    recurrence_end_date: Optional[date] = None
    blocked_days: Optional[List[int]] = None

class PTOConstraintCreate(ConstraintBase):
    rule_type: str = 'pto'
    description: Optional[str] = None
    pto_start_date: date
    pto_end_date: date
    pto_reason: Optional[str] = None

class ConstraintResponse(BaseModel):
    id: UUID  # Changed from constraint_id
    restaurant_id: int
    staff_id: str
    staff_name: str
    rule_type: str  # Changed from constraint_type
    description: Optional[str]
    recurrence_type: Optional[str]
    recurrence_end_date: Optional[date]
    pto_start_date: Optional[date]
    pto_end_date: Optional[date]
    pto_reason: Optional[str]
    created_by: str
    created_at: str
    is_active: bool