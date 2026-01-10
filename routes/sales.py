"""
SALES ROUTES
Endpoints for sales portal: leads, activities, deals, commissions, AI parsing
"""
from fastapi import APIRouter, HTTPException, status, Depends, Query
from typing import Optional, List
from pydantic import BaseModel
from services.sales_service import SalesService, LEAD_STAGES, ACTIVITY_TYPES
from services.auth_service import verify_jwt_token

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