from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import date

class StaffBase(BaseModel):
    name: str
    position: str
    hireDate: date
    payRate: Optional[float] = None
    phone: Optional[str] = None
    skills: List[str] = []
    notes: Optional[str] = None
    portal_access: str = "none"
    can_edit_staff: bool = False
    strategic_alerts_only: Optional[bool] = None

class StaffCreate(StaffBase):
    email: EmailStr
    restaurant_id: int

class StaffUpdate(StaffBase):
    email: Optional[EmailStr] = None

class StaffResponse(StaffBase):
    id: int
    email: str
    status: str
    restaurant_id: int
    hire_date: date
    
    class Config:
        from_attributes = True