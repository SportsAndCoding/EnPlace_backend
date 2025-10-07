from pydantic import BaseModel
from typing import List, Literal
from datetime import date, time

class ShiftRemoval(BaseModel):
    action: Literal["remove"]
    shift_id: str  # UUID of the shift to remove

class ShiftAddition(BaseModel):
    action: Literal["add"]
    staff_id: str
    date: str  # ISO format: "2025-10-13"
    start_time: str  # "18:00:00"
    end_time: str  # "22:00:00"
    position: str

# Union type for either action
ShiftChange = ShiftRemoval | ShiftAddition

class UpdateScheduleRequest(BaseModel):
    changes: List[ShiftChange]

class UpdateScheduleResponse(BaseModel):
    success: bool
    shifts_added: int
    shifts_removed: int
    updated_metrics: dict
    warnings: List[str]  # Constraint violations, coverage gaps, etc.