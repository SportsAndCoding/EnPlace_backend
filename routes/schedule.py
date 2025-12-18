"""
SCHEDULE ROUTES
===============
Endpoints for schedule parsing and analysis.

POST /api/schedule/analyze - Parse and analyze a draft schedule
GET /api/schedule/profiles - Get staff work profiles for a restaurant
PUT /api/schedule/profiles/{staff_id} - Update a staff work profile
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

from services.auth_service import verify_jwt_token
from services.schedule_parser_service import parse_schedule
from services.schedule_analysis_service import analyze_schedule

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


# ═══════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════

class AnalyzeScheduleRequest(BaseModel):
    raw_schedule: str
    week_of: str  # YYYY-MM-DD
    manager_notes: Optional[str] = ""

class AnalyzeScheduleResponse(BaseModel):
    success: bool
    parse_result: Optional[Dict[str, Any]] = None
    analysis: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class WorkProfileUpdate(BaseModel):
    hired_shift: Optional[str] = None
    hired_days: Optional[List[str]] = None
    hired_hours_target: Optional[int] = None
    unavailable_days: Optional[List[str]] = None
    unavailable_before: Optional[str] = None  # HH:MM
    unavailable_after: Optional[str] = None   # HH:MM
    availability_reason: Optional[str] = None
    preferred_shift: Optional[str] = None
    preferred_days: Optional[List[str]] = None
    preferred_max_hours: Optional[int] = None
    preference_notes: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/analyze", response_model=AnalyzeScheduleResponse)
async def analyze_schedule_endpoint(
    request: AnalyzeScheduleRequest,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Parse raw schedule data and return full analysis.
    
    Flow:
    1. GPT-4o-mini parses messy schedule → normalized shifts
    2. Analysis engine scores fairness, fatigue, preferences
    3. Returns complete analysis matching frontend SCHEDULE_DATA structure
    """
    restaurant_id = current_staff.get("restaurant_id")
    
    if not restaurant_id:
        raise HTTPException(status_code=400, detail="No restaurant associated with user")
    
    if not request.raw_schedule or len(request.raw_schedule.strip()) < 10:
        raise HTTPException(status_code=400, detail="Schedule data is too short or empty")
    
    try:
        # Step 1: Parse the raw schedule
        logger.info(f"Parsing schedule for restaurant {restaurant_id}, week of {request.week_of}")
        
        parse_result = await parse_schedule(
            raw_schedule=request.raw_schedule,
            restaurant_id=restaurant_id,
            week_of=request.week_of
        )
        
        if not parse_result.get("success"):
            return AnalyzeScheduleResponse(
                success=False,
                parse_result=parse_result,
                error=parse_result.get("error", "Failed to parse schedule")
            )
        
        shifts = parse_result.get("shifts", [])
        
        if not shifts:
            return AnalyzeScheduleResponse(
                success=False,
                parse_result=parse_result,
                error="No shifts could be extracted from the schedule"
            )
        
        logger.info(f"Parsed {len(shifts)} shifts, {len(parse_result.get('unmapped', []))} unmapped names")
        
        # Step 2: Run full analysis
        analysis_result = await analyze_schedule(
            shifts=shifts,
            restaurant_id=restaurant_id,
            week_of=request.week_of,
            manager_notes=request.manager_notes or ""
        )
        
        logger.info(f"Analysis complete. Stability score: {analysis_result['analysis']['scores']['stabilityScore']}")
        
        return AnalyzeScheduleResponse(
            success=True,
            parse_result={
                "shifts_parsed": len(shifts),
                "unmapped_names": parse_result.get("unmapped", []),
                "warnings": parse_result.get("warnings", []),
                "tokens_used": parse_result.get("tokens_used", 0),
                "estimated_cost": parse_result.get("estimated_cost", 0)
            },
            analysis=analysis_result
        )
        
    except Exception as e:
        logger.error(f"Schedule analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.get("/profiles")
async def get_work_profiles(
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Get all staff work profiles for the restaurant.
    Used to display/edit availability and preferences.
    """
    from supabase import create_client
    from config.settings import SUPABASE_URL, SUPABASE_KEY
    
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    restaurant_id = current_staff.get("restaurant_id")
    
    try:
        # Get profiles with staff names
        result = supabase.table("staff_work_profile") \
            .select("*, staff(full_name, position)") \
            .eq("restaurant_id", restaurant_id) \
            .execute()
        
        profiles = []
        for p in (result.data or []):
            staff_info = p.pop("staff", {}) or {}
            profiles.append({
                **p,
                "full_name": staff_info.get("full_name", "Unknown"),
                "position": staff_info.get("position", "Staff")
            })
        
        return {
            "success": True,
            "profiles": profiles
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch work profiles: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch profiles")


@router.put("/profiles/{staff_id}")
async def update_work_profile(
    staff_id: str,
    update: WorkProfileUpdate,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Update a staff member's work profile.
    Manager can edit availability, preferences, etc.
    """
    from supabase import create_client
    from config.settings import SUPABASE_URL, SUPABASE_KEY
    
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    restaurant_id = current_staff.get("restaurant_id")
    
    # Build update dict (only non-None fields)
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    # Add timestamp
    update_data["updated_at"] = datetime.utcnow().isoformat()
    
    # If updating preferences, set preference_updated_at
    pref_fields = ["preferred_shift", "preferred_days", "preferred_max_hours", "preference_notes"]
    if any(f in update_data for f in pref_fields):
        update_data["preference_updated_at"] = datetime.utcnow().isoformat()
    
    try:
        # Check if profile exists
        existing = supabase.table("staff_work_profile") \
            .select("id") \
            .eq("staff_id", staff_id) \
            .eq("restaurant_id", restaurant_id) \
            .execute()
        
        if existing.data:
            # Update existing
            result = supabase.table("staff_work_profile") \
                .update(update_data) \
                .eq("staff_id", staff_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
        else:
            # Create new profile
            update_data["staff_id"] = staff_id
            update_data["restaurant_id"] = restaurant_id
            result = supabase.table("staff_work_profile") \
                .insert(update_data) \
                .execute()
        
        return {
            "success": True,
            "profile": result.data[0] if result.data else None
        }
        
    except Exception as e:
        logger.error(f"Failed to update work profile: {e}")
        raise HTTPException(status_code=500, detail="Failed to update profile")


@router.post("/publish")
async def publish_schedule(
    request: Dict[str, Any],
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Publish analyzed schedule to sse_shifts and create SSE events.
    
    Request body:
    - shifts: List of shifts to publish
    - sse_events: List of auto-generated events to create
    - override_reason: If publishing with unresolved issues
    - override_notes: Manager notes for override
    """
    from supabase import create_client
    from config.settings import SUPABASE_URL, SUPABASE_KEY
    
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    restaurant_id = current_staff.get("restaurant_id")
    manager_id = current_staff.get("staff_id")
    
    shifts = request.get("shifts", [])
    sse_events = request.get("sse_events", [])
    override_reason = request.get("override_reason")
    override_notes = request.get("override_notes", "")
    
    if not shifts:
        raise HTTPException(status_code=400, detail="No shifts to publish")
    
    try:
        published_count = 0
        
        # Insert/update shifts
        for shift in shifts:
            shift_data = {
                "restaurant_id": restaurant_id,
                "staff_id": shift.get("staff_id"),
                "shift_date": shift.get("date"),
                "scheduled_start": f"{shift.get('date')}T{shift.get('start_time')}:00",
                "scheduled_end": f"{shift.get('date')}T{shift.get('end_time')}:00",
                "position": shift.get("position"),
                "shift_type": classify_shift_type(shift.get("start_time"), shift.get("end_time")),
                "status": "scheduled",
                "is_published": True
            }
            
            # Upsert based on date + staff
            supabase.table("sse_shifts").upsert(
                shift_data,
                on_conflict="restaurant_id,staff_id,shift_date"
            ).execute()
            
            published_count += 1
        
        # Create SSE escalation events
        events_created = 0
        for event in sse_events:
            if event.get("autoCreated"):
                event_data = {
                    "restaurant_id": restaurant_id,
                    "event_type": event.get("type"),
                    "severity": event.get("severity"),
                    "status": "active",
                    "trigger_source": "schedule_analysis",
                    "trigger_details": event.get("trigger"),
                    "affected_staff_count": 1,
                    "created_by": manager_id
                }
                
                supabase.table("sse_escalation_events").insert(event_data).execute()
                events_created += 1
        
        return {
            "success": True,
            "shifts_published": published_count,
            "events_created": events_created,
            "override_logged": override_reason is not None
        }
        
    except Exception as e:
        logger.error(f"Failed to publish schedule: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Publish failed: {str(e)}")


def classify_shift_type(start_time: str, end_time: str) -> str:
    """Classify shift as AM, PM, or Close based on times."""
    try:
        start_hour = int(start_time.split(":")[0])
        end_hour = int(end_time.split(":")[0])
        
        if start_hour < 12:
            return "AM"
        elif end_hour >= 22 or end_hour <= 2:
            return "Close"
        else:
            return "PM"
    except:
        return "PM"