from pydantic import BaseModel, Field
from typing import Optional, Dict
from datetime import datetime

class CandidateCreate(BaseModel):
    """Request model for creating a candidate"""
    restaurant_id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: str  # 'server', 'line_cook', 'dishwasher', etc.
    gm_notes: Optional[str] = None

class CandidateUpdate(BaseModel):
    """Request model for updating a candidate"""
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None  # 'open', 'interviewed', 'hired', 'rejected'
    scenario_rankings: Optional[Dict[str, str]] = None
    gm_notes: Optional[str] = None
    interviewed_at: Optional[datetime] = None
    decision_at: Optional[datetime] = None
    hired_at: Optional[datetime] = None

class ScenarioRankings(BaseModel):
    """Request model for scoring a candidate"""
    scenario_rankings: Dict[str, str]  # {"break_room": "alex", "expo_backup": "jordan", ...}

class CandidateResponse(BaseModel):
    """Response model for a candidate"""
    id: str
    restaurant_id: int
    candidate_code: str
    name: str
    email: Optional[str]
    phone: Optional[str]
    role: str
    status: str
    scenario_rankings: Optional[Dict]
    stability_score: Optional[int]
    cliff_risk_percent: Optional[int]
    recommendation: Optional[str]
    fingerprint: Optional[Dict]
    gm_notes: Optional[str]
    applied_at: Optional[datetime]
    interviewed_at: Optional[datetime]
    decision_at: Optional[datetime]
    hired_at: Optional[datetime]
    hired_staff_id: Optional[str]
    created_at: datetime
    updated_at: datetime

class CandidateCreateResponse(BaseModel):
    """Response after creating a candidate"""
    success: bool
    candidate_id: str
    candidate_code: str
    message: str