"""
SALES ROUTES
Endpoints for sales portal: leads, activities, deals, commissions, AI parsing
"""
from fastapi import APIRouter, HTTPException, status, Depends, Query
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from services.sales_service import SalesService, LEAD_STAGES, ACTIVITY_TYPES
from services.auth_service import verify_jwt_token
from services.rep_scheduling_service import RepSchedulingService

router = APIRouter(prefix="/api/sales", tags=["sales"])


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ParseCallNotesRequest(BaseModel):
    notes_text: str
    lead_id: Optional[str] = None


class CreateLeadRequest(BaseModel):
    restaurant_name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    city_state: Optional[str] = None
    lead_source: Optional[str] = "cold_walk"
    estimated_value: Optional[int] = None
    notes: Optional[str] = None


class UpdateLeadRequest(BaseModel):
    restaurant_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    city_state: Optional[str] = None
    lead_source: Optional[str] = None
    stage: Optional[str] = None
    estimated_value: Optional[int] = None
    notes: Optional[str] = None


class UpdateStageRequest(BaseModel):
    stage: str


class CreateActivityRequest(BaseModel):
    lead_id: str
    activity_type: str
    content: str
    outcome: Optional[str] = None
    follow_up_date: Optional[str] = None


class CreateDealRequest(BaseModel):
    lead_id: str
    monthly_value: int
    contract_months: Optional[int] = 12
    captain_id: Optional[str] = None

class SetFieldHoursRequest(BaseModel):
    field_hours: Dict[str, Any]  # {"monday": {"start": "09:00", "end": "17:00"}, ...}


class UpdateBookingSettingsRequest(BaseModel):
    timezone: Optional[str] = None
    booking_slug: Optional[str] = None


class CreateOverrideRequest(BaseModel):
    override_date: str  # YYYY-MM-DD
    start_time: Optional[str] = None  # HH:MM (null = full day)
    end_time: Optional[str] = None
    reason: Optional[str] = None


class BookDemoRequest(BaseModel):
    restaurant_name: str
    contact_name: str
    contact_email: str
    contact_phone: str
    appointment_date: str  # YYYY-MM-DD
    start_time: str  # HH:MM
    location_address: Optional[str] = None
    location_notes: Optional[str] = None


class CreateAppointmentRequest(BaseModel):
    restaurant_name: str
    contact_name: str
    appointment_date: str  # YYYY-MM-DD
    start_time: str  # HH:MM
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    location_address: Optional[str] = None
    location_notes: Optional[str] = None
    lead_id: Optional[str] = None
    notes: Optional[str] = None


class UpdateAppointmentRequest(BaseModel):
    restaurant_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    location_address: Optional[str] = None
    location_notes: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None

# ═══════════════════════════════════════════════════════════════════════════════
# HELPER: Check sales portal access
# ═══════════════════════════════════════════════════════════════════════════════

SALES_ROLES = ['sales_rep', 'sales_captain', 'sales_director', 'founder_ceo']


def require_sales_access(current_staff: dict) -> dict:
    """Verify user has sales portal access"""
    portal_access = current_staff.get('portal_access')
    if portal_access not in SALES_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sales portal access required"
        )
    return current_staff


def get_sales_role(portal_access: str) -> str:
    """Normalize portal_access to sales role"""
    if portal_access == 'founder_ceo':
        return 'sales_director'  # CEO sees everything
    return portal_access


# ═══════════════════════════════════════════════════════════════════════════════
# AI PARSING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/parse-notes")
async def parse_call_notes(
    request: ParseCallNotesRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """
    Parse call notes using AI. Returns structured data preview.
    Does NOT save to database.
    """
    require_sales_access(current_staff)
    
    if not request.notes_text or len(request.notes_text.strip()) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notes text too short"
        )
    
    service = SalesService()
    
    try:
        result = await service.parse_call_notes(request.notes_text)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse notes: {str(e)}"
        )


@router.post("/parse-and-save")
async def parse_and_save_call_notes(
    request: ParseCallNotesRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """
    Parse call notes, create/update lead, and log activity.
    The main voice logger endpoint.
    """
    require_sales_access(current_staff)
    
    if not request.notes_text or len(request.notes_text.strip()) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notes text too short"
        )
    
    service = SalesService()
    rep_id = current_staff.get('staff_id')
    
    try:
        result = await service.parse_and_save_call_notes(
            notes_text=request.notes_text,
            rep_id=rep_id,
            lead_id=request.lead_id
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# LEADS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/leads")
async def get_leads(
    stage: Optional[str] = Query(None, description="Filter by stage"),
    limit: int = Query(100, le=500),
    current_staff: dict = Depends(verify_jwt_token)
):
    """
    Get leads. Filtered by role permissions.
    - sales_rep: own leads only
    - sales_captain: own + team leads  
    - sales_director/founder_ceo: all leads
    """
    require_sales_access(current_staff)
    
    service = SalesService()
    rep_id = current_staff.get('staff_id')
    role = get_sales_role(current_staff.get('portal_access'))
    
    # TODO: Get team_ids for captains from a team membership table
    team_ids = None
    
    try:
        leads = await service.get_leads(rep_id, role, team_ids, stage, limit)
        return {
            "success": True,
            "leads": leads,
            "count": len(leads)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get leads: {str(e)}"
        )


@router.get("/leads/{lead_id}")
async def get_lead(
    lead_id: str,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get a single lead with its activities"""
    require_sales_access(current_staff)
    
    service = SalesService()
    
    try:
        lead = await service.get_lead_by_id(lead_id)
        if not lead:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead not found"
            )
        return {
            "success": True,
            "lead": lead
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get lead: {str(e)}"
        )


@router.post("/leads")
async def create_lead(
    request: CreateLeadRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Create a new lead manually"""
    require_sales_access(current_staff)
    
    service = SalesService()
    rep_id = current_staff.get('staff_id')
    
    try:
        lead = await service.create_lead({
            'restaurant_name': request.restaurant_name,
            'contact_name': request.contact_name,
            'contact_email': request.contact_email,
            'contact_phone': request.contact_phone,
            'city_state': request.city_state,
            'lead_source': request.lead_source,
            'assigned_rep_id': rep_id,
            'estimated_value': request.estimated_value,
            'notes': request.notes
        })
        return {
            "success": True,
            "lead": lead
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create lead: {str(e)}"
        )


@router.put("/leads/{lead_id}")
async def update_lead(
    lead_id: str,
    request: UpdateLeadRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Update an existing lead"""
    require_sales_access(current_staff)
    
    service = SalesService()
    
    # Validate stage if provided
    if request.stage and request.stage not in LEAD_STAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid stage. Must be one of: {LEAD_STAGES}"
        )
    
    try:
        lead = await service.update_lead(lead_id, request.model_dump(exclude_none=True))
        if not lead:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead not found"
            )
        return {
            "success": True,
            "lead": lead
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update lead: {str(e)}"
        )


@router.put("/leads/{lead_id}/stage")
async def update_lead_stage(
    lead_id: str,
    request: UpdateStageRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Update lead pipeline stage"""
    require_sales_access(current_staff)
    
    if request.stage not in LEAD_STAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid stage. Must be one of: {LEAD_STAGES}"
        )
    
    service = SalesService()
    
    try:
        lead = await service.update_lead_stage(lead_id, request.stage)
        return {
            "success": True,
            "lead": lead
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update stage: {str(e)}"
        )


@router.delete("/leads/{lead_id}")
async def delete_lead(
    lead_id: str,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Delete a lead"""
    require_sales_access(current_staff)
    
    service = SalesService()
    
    try:
        await service.delete_lead(lead_id)
        return {
            "success": True,
            "message": "Lead deleted"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete lead: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVITIES ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/leads/{lead_id}/activities")
async def get_lead_activities(
    lead_id: str,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get all activities for a lead"""
    require_sales_access(current_staff)
    
    service = SalesService()
    
    try:
        activities = await service.get_activities_for_lead(lead_id)
        return {
            "success": True,
            "activities": activities
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get activities: {str(e)}"
        )


@router.post("/activities")
async def create_activity(
    request: CreateActivityRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Create a new activity manually"""
    require_sales_access(current_staff)
    
    if request.activity_type not in ACTIVITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid activity type. Must be one of: {ACTIVITY_TYPES}"
        )
    
    service = SalesService()
    rep_id = current_staff.get('staff_id')
    
    try:
        activity = await service.create_activity({
            'lead_id': request.lead_id,
            'rep_id': rep_id,
            'activity_type': request.activity_type,
            'content': request.content,
            'outcome': request.outcome,
            'follow_up_date': request.follow_up_date
        })
        return {
            "success": True,
            "activity": activity
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create activity: {str(e)}"
        )


@router.get("/activities/recent")
async def get_recent_activities(
    days: int = Query(7, le=30),
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get recent activities for current rep"""
    require_sales_access(current_staff)
    
    service = SalesService()
    rep_id = current_staff.get('staff_id')
    
    try:
        activities = await service.get_rep_activities(rep_id, days)
        return {
            "success": True,
            "activities": activities
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get activities: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# DEALS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/deals")
async def get_deals(
    status: Optional[str] = Query(None),
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get deals. Filtered by role permissions."""
    require_sales_access(current_staff)
    
    service = SalesService()
    rep_id = current_staff.get('staff_id')
    role = get_sales_role(current_staff.get('portal_access'))
    
    try:
        deals = await service.get_deals(rep_id, role, None, status)
        return {
            "success": True,
            "deals": deals
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get deals: {str(e)}"
        )


@router.post("/deals")
async def create_deal(
    request: CreateDealRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """
    Create a deal from a closed lead.
    Automatically generates commission records.
    """
    require_sales_access(current_staff)
    
    service = SalesService()
    rep_id = current_staff.get('staff_id')
    
    try:
        result = await service.create_deal(
            lead_id=request.lead_id,
            rep_id=rep_id,
            monthly_value=request.monthly_value,
            contract_months=request.contract_months,
            captain_id=request.captain_id
        )
        return {
            "success": True,
            **result
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create deal: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# COMMISSIONS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/commissions")
async def get_commissions(
    status: Optional[str] = Query(None, description="Filter: pending, paid, disputed"),
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get commissions for current rep"""
    require_sales_access(current_staff)
    
    service = SalesService()
    rep_id = current_staff.get('staff_id')
    
    try:
        commissions = await service.get_commissions(rep_id, status)
        return {
            "success": True,
            "commissions": commissions
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get commissions: {str(e)}"
        )


@router.get("/commissions/summary")
async def get_commission_summary(
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get commission summary (YTD, pending, paid)"""
    require_sales_access(current_staff)
    
    service = SalesService()
    rep_id = current_staff.get('staff_id')
    
    try:
        summary = await service.get_commission_summary(rep_id)
        return {
            "success": True,
            **summary
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get summary: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def get_dashboard(
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get dashboard stats for current rep"""
    require_sales_access(current_staff)
    
    service = SalesService()
    rep_id = current_staff.get('staff_id')
    role = get_sales_role(current_staff.get('portal_access'))
    
    try:
        stats = await service.get_dashboard_stats(rep_id, role)
        return {
            "success": True,
            **stats
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get dashboard: {str(e)}"
        )


@router.get("/leaderboard")
async def get_leaderboard(
    metric: str = Query("deals", description="deals, revenue, or activities"),
    limit: int = Query(10, le=50),
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get sales leaderboard"""
    require_sales_access(current_staff)
    
    if metric not in ['deals', 'revenue', 'activities']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid metric. Must be: deals, revenue, or activities"
        )
    
    service = SalesService()
    
    try:
        leaderboard = await service.get_leaderboard(metric, limit)
        return {
            "success": True,
            "leaderboard": leaderboard,
            "metric": metric
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get leaderboard: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# DEMO ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/demo-access")
async def get_demo_access(
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get demo portal credentials and tips"""
    require_sales_access(current_staff)
    
    service = SalesService()
    
    return {
        "success": True,
        **service.get_demo_credentials()
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS (for frontend reference)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/constants")
async def get_constants():
    """Get constants for frontend (stages, activity types)"""
    return {
        "success": True,
        "stages": LEAD_STAGES,
        "activity_types": ACTIVITY_TYPES
    }

@router.get("/demo-reports")
async def get_demo_reports(
    current_staff: dict = Depends(verify_jwt_token)
):
    """
    Get static demo versions of House Guardian and Stable Schedule Builder reports.
    Always available regardless of day of week - for sales demos.
    """
    require_sales_access(current_staff)
    
    # Static House Guardian Weekly Report - compelling demo data
    house_guardian_report = {
        "id": "demo_hg_report",
        "type": "house_guardian_report",
        "priority": "warning",
        "title": "🏠 House Guardian: Week of Jan 6",
        "description": "1 category flagged • 3 equipment issues • 847 notes scanned",
        "time_ago": "This week",
        "action": "View Summary",
        "secondary_action": None,
        "smm_boost": 0,
        "is_network_report": False,
        "report_content": {
            "all_clear": False,
            "categories_flagged": ["harassment"],
            "categories_clear": ["theft", "drugs", "threats", "bullying"],
            "operational_themes": [
                {"type": "equipment", "issue": "AC Unit", "mentions": 4},
                {"type": "equipment", "issue": "Ice Machine", "mentions": 3},
                {"type": "equipment", "issue": "POS Terminal #2", "mentions": 2}
            ],
            "sentiment_samples": [
                {"role": "Server", "text": "busy night but tips were great. team worked well together"},
                {"role": "Line Cook", "text": "new prep system is working. less chaos during rush"},
                {"role": "Bartender", "text": "one customer got handsy, manager handled it immediately"}
            ],
            "notes_scanned": 847,
            "week_start": "2026-01-06",
            "week_end": "2026-01-12"
        }
    }
    
    # Static Stable Schedule Builder Report - compelling demo data
    stable_schedule_report = {
        "id": "demo_ssb_report",
        "type": "schedule_report",
        "priority": "info",
        "title": "📅 Schedule Analysis: Week of Jan 6",
        "description": "Stability Score: 87% • 3 issues prevented • 2 open shifts remain",
        "time_ago": "This week",
        "action": "View Details",
        "secondary_action": None,
        "smm_boost": 0,
        "report_content": {
            "stability_score": 87,
            "coverage_percent": 94,
            "issues_found": 5,
            "issues_prevented": 3,
            "critical_issues": 0,
            "open_shifts": 2,
            "overtime_risk": 1,
            "insights": [
                {"type": "prevented", "text": "Blocked double-shift for Maria S. (burnout risk)"},
                {"type": "prevented", "text": "Filled Friday PM gap using availability preferences"},
                {"type": "prevented", "text": "Rebalanced Saturday to avoid 3 closers calling out"},
                {"type": "warning", "text": "Sunday brunch still needs 1 server, 1 busser"},
                {"type": "suggestion", "text": "Consider cross-training Alex T. for host backup"}
            ],
            "week_of": "2026-01-06"
        }
    }
    
    return {
        "success": True,
        "reports": [house_guardian_report, stable_schedule_report],
        "demo_tips": [
            "House Guardian: Click 'View Summary' to show the harassment flag - explain anonymous escalation",
            "Equipment issues: 'AC mentioned 4 times this week - time to call repair?'",
            "SSB: 'We prevented 3 scheduling mistakes before they happened'",
            "The Billy Moment: 'When Billy calls in sick, who covers? We already know.'"
        ]
    }

@router.get("/book/{slug}")
async def get_rep_public_profile(slug: str):
    """
    PUBLIC: Get rep info for booking page.
    Returns name, photo, timezone for the booking UI.
    """
    service = RepSchedulingService()
    
    try:
        rep = await service.get_rep_by_slug(slug)
        if not rep:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sales representative not found"
            )
        
        return {
            "success": True,
            "rep": {
                "name": rep['full_name'],
                "photo_url": rep.get('profile_photo_url'),
                "timezone": rep.get('timezone', 'America/New_York'),
                "position": rep.get('position', 'Sales Representative')
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get rep info: {str(e)}"
        )


@router.get("/book/{slug}/availability")
async def get_booking_availability(
    slug: str,
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD")
):
    """
    PUBLIC: Get available dates for booking calendar.
    Returns which dates have availability.
    """
    service = RepSchedulingService()
    
    try:
        rep = await service.get_rep_by_slug(slug)
        if not rep:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sales representative not found"
            )
        
        from datetime import datetime
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        # Limit to 60 days max
        if (end - start).days > 60:
            end = start + timedelta(days=60)
        
        availability = await service.get_available_dates(rep['staff_id'], start, end)
        
        return {
            "success": True,
            "availability": availability,
            "timezone": rep.get('timezone', 'America/New_York')
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get availability: {str(e)}"
        )


@router.get("/book/{slug}/slots")
async def get_booking_slots(
    slug: str,
    date: str = Query(..., description="Date YYYY-MM-DD")
):
    """
    PUBLIC: Get available time slots for a specific date.
    """
    service = RepSchedulingService()
    
    try:
        rep = await service.get_rep_by_slug(slug)
        if not rep:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sales representative not found"
            )
        
        from datetime import datetime
        target_date = datetime.strptime(date, '%Y-%m-%d').date()
        
        slots = await service.get_available_slots(
            rep['staff_id'], 
            target_date,
            rep.get('timezone', 'America/New_York')
        )
        
        return {
            "success": True,
            "date": date,
            "slots": slots,
            "timezone": rep.get('timezone', 'America/New_York')
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get slots: {str(e)}"
        )


@router.post("/book/{slug}")
async def book_demo_public(slug: str, request: BookDemoRequest):
    """
    PUBLIC: Restaurant books a demo with a rep.
    Creates appointment and returns confirmation.
    """
    service = RepSchedulingService()
    
    try:
        from datetime import datetime
        appt_date = datetime.strptime(request.appointment_date, '%Y-%m-%d').date()
        appt_time = datetime.strptime(request.start_time, '%H:%M').time()
        
        result = await service.book_demo(
            slug=slug,
            restaurant_name=request.restaurant_name,
            contact_name=request.contact_name,
            contact_email=request.contact_email,
            contact_phone=request.contact_phone,
            appointment_date=appt_date,
            start_time=appt_time,
            location_address=request.location_address,
            location_notes=request.location_notes
        )
        
        return {
            "success": True,
            "message": "Demo scheduled successfully!",
            **result
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This time slot was just booked. Please select another."
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to book demo: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# REP AUTHENTICATED ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/my/booking-settings")
async def get_my_booking_settings(
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get current rep's booking settings (timezone, slug)"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        rep = await service.get_rep_by_id(staff_id)
        if not rep:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Rep not found"
            )
        
        return {
            "success": True,
            "settings": {
                "timezone": rep.get('timezone', 'America/New_York'),
                "booking_slug": rep.get('booking_slug'),
                "booking_url": f"https://app.en-place.ai/book/{rep.get('booking_slug')}" if rep.get('booking_slug') else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get settings: {str(e)}"
        )


@router.put("/my/booking-settings")
async def update_my_booking_settings(
    request: UpdateBookingSettingsRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Update rep's timezone and/or booking slug"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        result = await service.update_rep_booking_settings(
            staff_id=staff_id,
            timezone=request.timezone,
            booking_slug=request.booking_slug
        )
        
        return {
            "success": True,
            "settings": {
                "timezone": result.get('timezone'),
                "booking_slug": result.get('booking_slug'),
                "booking_url": f"https://app.en-place.ai/book/{result.get('booking_slug')}" if result.get('booking_slug') else None
            }
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update settings: {str(e)}"
        )


@router.get("/my/field-hours")
async def get_my_field_hours(
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get rep's weekly field hours template"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        field_hours = await service.get_field_hours(staff_id)
        return {
            "success": True,
            **field_hours
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get field hours: {str(e)}"
        )


@router.put("/my/field-hours")
async def set_my_field_hours(
    request: SetFieldHoursRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Set rep's weekly field hours template"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        result = await service.set_field_hours(staff_id, request.field_hours)
        return {
            "success": True,
            **result
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set field hours: {str(e)}"
        )


@router.get("/my/overrides")
async def get_my_overrides(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get rep's availability overrides for a date range"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        from datetime import datetime
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        overrides = await service.get_overrides(staff_id, start, end)
        return {
            "success": True,
            "overrides": overrides
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get overrides: {str(e)}"
        )


@router.post("/my/overrides")
async def create_my_override(
    request: CreateOverrideRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Create an availability override (block time)"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        from datetime import datetime
        override_date = datetime.strptime(request.override_date, '%Y-%m-%d').date()
        
        start_time = None
        end_time = None
        if request.start_time:
            start_time = datetime.strptime(request.start_time, '%H:%M').time()
        if request.end_time:
            end_time = datetime.strptime(request.end_time, '%H:%M').time()
        
        override = await service.create_override(
            staff_id=staff_id,
            override_date=override_date,
            start_time=start_time,
            end_time=end_time,
            reason=request.reason
        )
        
        return {
            "success": True,
            "override": override
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create override: {str(e)}"
        )


@router.delete("/my/overrides/{override_id}")
async def delete_my_override(
    override_id: int,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Delete an availability override"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        await service.delete_override(override_id, staff_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete override: {str(e)}"
        )


@router.get("/my/appointments")
async def get_my_appointments(
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    status: Optional[str] = Query(None, description="Filter by status"),
    current_staff: dict = Depends(verify_jwt_token)
):
    """Get rep's appointments"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        from datetime import datetime
        start = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
        end = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
        
        appointments = await service.get_appointments(staff_id, start, end, status)
        return {
            "success": True,
            "appointments": appointments,
            "count": len(appointments)
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get appointments: {str(e)}"
        )


@router.post("/my/appointments")
async def create_my_appointment(
    request: CreateAppointmentRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Rep manually creates an appointment (from field booking)"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        from datetime import datetime
        appt_date = datetime.strptime(request.appointment_date, '%Y-%m-%d').date()
        appt_time = datetime.strptime(request.start_time, '%H:%M').time()
        
        appointment = await service.create_appointment(
            staff_id=staff_id,
            restaurant_name=request.restaurant_name,
            contact_name=request.contact_name,
            appointment_date=appt_date,
            start_time=appt_time,
            contact_email=request.contact_email,
            contact_phone=request.contact_phone,
            location_address=request.location_address,
            location_notes=request.location_notes,
            lead_id=request.lead_id,
            booked_by='rep',
            notes=request.notes
        )
        
        return {
            "success": True,
            "appointment": appointment
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This time slot is already booked"
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create appointment: {str(e)}"
        )


@router.put("/my/appointments/{appointment_id}")
async def update_my_appointment(
    appointment_id: int,
    request: UpdateAppointmentRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Update an appointment"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        updates = request.dict(exclude_none=True)
        appointment = await service.update_appointment(appointment_id, staff_id, updates)
        
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        return {
            "success": True,
            "appointment": appointment
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update appointment: {str(e)}"
        )


@router.delete("/my/appointments/{appointment_id}")
async def cancel_my_appointment(
    appointment_id: int,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Cancel an appointment"""
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        await service.cancel_appointment(appointment_id, staff_id)
        return {"success": True, "message": "Appointment cancelled"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel appointment: {str(e)}"
        )


@router.get("/my/schedule")
async def get_my_schedule_view(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    current_staff: dict = Depends(verify_jwt_token)
):
    """
    Get combined schedule view for rep - shows appointments + blocked times + available slots.
    Useful for the rep's schedule management page.
    """
    require_sales_access(current_staff)
    
    service = RepSchedulingService()
    staff_id = current_staff.get('staff_id')
    
    try:
        from datetime import datetime
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        # Get all data
        appointments = await service.get_appointments(staff_id, start, end)
        overrides = await service.get_overrides(staff_id, start, end)
        field_hours = await service.get_field_hours(staff_id)
        
        # Get availability for each date
        availability = await service.get_available_dates(staff_id, start, end)
        
        return {
            "success": True,
            "schedule": {
                "appointments": appointments,
                "overrides": overrides,
                "field_hours": field_hours.get('field_hours', {}),
                "availability_by_date": availability
            }
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get schedule: {str(e)}"
        )
@router.post("/reset-demo-checkins")
async def reset_demo_checkins(
    current_staff: dict = Depends(require_sales_access)
):
    """Reset today's Demo Bistro check-ins so reps can re-demo the daily journal."""
    service = SalesService()
    result = await service.reset_demo_checkins()
    return result