"""
Staff Portal Routes
All staff-facing endpoints for the mobile/web portal
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime, timedelta
import pytz
from services.auth_service import verify_jwt_token as get_current_user
from services.staff_portal_service import StaffPortalService

def _get_today_for_restaurant(restaurant_id: int) -> date:
    """Get today's date in restaurant timezone."""
    from database.supabase_client import get_supabase
    supabase = get_supabase()
    try:
        result = supabase.table("restaurants").select("timezone").eq("id", restaurant_id).single().execute()
        tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
    except:
        tz_name = "America/New_York"
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()


router = APIRouter(prefix="/api/staff-portal", tags=["staff-portal"])


# ═══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════

class PreferencesUpdate(BaseModel):
    preferred_shift_types: Optional[List[str]] = []
    preferred_days_of_week: Optional[List[str]] = []
    trained_roles: Optional[List[str]] = []
    max_consecutive_days: Optional[int] = None
    notes: Optional[str] = None


class ProfilePhotoUpdate(BaseModel):
    photo_url: str


class AwardPointsRequest(BaseModel):
    transaction_type: str
    points: Optional[int] = None
    description: Optional[str] = None


class RedeemPointsRequest(BaseModel):
    item_id: str
    item_name: str
    cost: int


class CalloutRequest(BaseModel):
    callout_date: date
    reason: str
    shift_id: Optional[int] = None
    notes: Optional[str] = None


class VolunteerRequest(BaseModel):
    shift_id: str  # UUID from open_shifts table


class SwapRequest(BaseModel):
    shift_id: int
    reason: Optional[str] = None
    target_staff_id: Optional[str] = None

class PersonalitySubmission(BaseModel):
    scenario_rankings: dict  # {"break_room":"alex","expo_backup":"jordan",...}

# ═══════════════════════════════════════════════════════════════════
# STAFF PROFILE
# ═══════════════════════════════════════════════════════════════════

@router.get("/me")
async def get_my_profile(current_user: dict = Depends(get_current_user)):
    """Get current staff member's profile"""
    service = StaffPortalService()

    try:
        profile = await service.get_staff_profile(current_user['staff_id'])

        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Profile not found"
            )

        return {
            "success": True,
            "profile": profile
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch profile: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════
# PREFERENCES
# ═══════════════════════════════════════════════════════════════════

@router.get("/me/preferences")
async def get_my_preferences(current_user: dict = Depends(get_current_user)):
    """Get current staff member's preferences"""
    service = StaffPortalService()

    try:
        preferences = await service.get_preferences(current_user['staff_id'])

        return {
            "success": True,
            "preferences": preferences
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch preferences: {str(e)}"
        )


@router.put("/me/preferences")
async def update_my_preferences(
    preferences: PreferencesUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update current staff member's preferences"""
    service = StaffPortalService()

    try:
        result = await service.update_preferences(
            staff_id=current_user['staff_id'],
            preferences=preferences.dict()
        )

        return {
            "success": True,
            "preferences": result,
            "message": "Preferences updated"
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update preferences: {str(e)}"
        )


@router.put("/me/photo")
async def update_my_photo(
    photo_data: ProfilePhotoUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update current staff member's profile photo"""
    service = StaffPortalService()

    try:
        result = await service.update_profile_photo(
            staff_id=current_user['staff_id'],
            photo_url=photo_data.photo_url
        )

        return {
            "success": True,
            "profile": result,
            "message": "Photo updated"
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update photo: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════
# STABILITY POINTS
# ═══════════════════════════════════════════════════════════════════

# SP earning rules (points per action)
SP_RULES_DEFAULTS = {
    "journalSubmit":        {"points": 2,  "label": "Daily Check-In"},
    "emergencyShiftPickup": {"points": 5,  "label": "Emergency Coverage"},
    "openShiftAccept":      {"points": 1,  "label": "Open Shift Pickup"},
    "nudgeBoss":            {"points": 5,  "label": "Feature Request"},
    "preferencesComplete":  {"points": 3,  "label": "Profile Complete"},
    "perfectWeek":          {"points": 10, "label": "Perfect Week"},
    "swapHelp":             {"points": 2,  "label": "Swap Assist"},
    "onTimeStreak":         {"points": 1,  "label": "On-Time Bonus"},
    "personalityAssessment":{"points": 10, "label": "Work Personality Profile"},
}

def get_sp_rule(rule_key: str, restaurant_id: str) -> dict:
    """
    Fetch earning rule from DB for this restaurant.
    Falls back to SP_RULES_DEFAULTS if no DB row exists or rule is disabled.
    nudgeBoss always uses the default — it is not manager-configurable.
    """
    if rule_key == "nudgeBoss":
        return SP_RULES_DEFAULTS["nudgeBoss"]

    try:
        supabase = get_supabase()
        result = supabase.table("reward_earning_rules") \
            .select("points, is_enabled") \
            .eq("restaurant_id", restaurant_id) \
            .eq("rule_key", rule_key) \
            .limit(1) \
            .execute()

        if result.data:
            row = result.data[0]
            if not row.get("is_enabled", True):
                return None  # Rule disabled for this restaurant
            return {
                "points": row["points"],
                "label": SP_RULES_DEFAULTS.get(rule_key, {}).get("label", rule_key)
            }
    except Exception as e:
        logger.warning(f"SP rule DB lookup failed for {rule_key}, using default: {e}")

    return SP_RULES_DEFAULTS.get(rule_key)

@router.get("/stability-points")
async def get_my_stability_points(
    limit: int = Query(default=20, le=50),
    current_user: dict = Depends(get_current_user)
):
    """Get current staff member's stability points balance and history"""
    service = StaffPortalService()

    try:
        result = await service.get_stability_points(
            staff_id=current_user['staff_id'],
            limit=limit
        )

        return {
            "success": True,
            **result
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch stability points: {str(e)}"
        )


@router.post("/stability-points/award")
async def award_stability_points(
    request: AwardPointsRequest,
    current_user: dict = Depends(get_current_user)
):
    """Award stability points (self-service for tracked actions)"""
    service = StaffPortalService()

    # Validate transaction type
    if request.transaction_type not in SP_RULES_DEFAULTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid transaction type. Must be one of: {list(SP_RULES_DEFAULTS.keys())}"
        )

    rule = get_sp_rule(request.transaction_type, current_user['restaurant_id'])
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This earning rule is not enabled for your restaurant."
        )
    points = request.points or rule["points"]
    description = request.description or rule["label"]

    try:
        result = await service.award_points(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id'],
            points=points,
            transaction_type=request.transaction_type,
            description=description
        )

        return {
            "success": True,
            **result,
            "label": rule["label"]
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to award points: {str(e)}"
        )


@router.post("/stability-points/redeem")
async def redeem_stability_points(
    request: RedeemPointsRequest,
    current_user: dict = Depends(get_current_user)
):
    """Redeem stability points for a reward"""
    service = StaffPortalService()

    try:
        result = await service.redeem_points(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id'],
            item_id=request.item_id,
            item_name=request.item_name,
            cost=request.cost
        )

        return {
            "success": True,
            **result,
            "message": f"Redeemed {request.item_name}"
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to redeem points: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════
# CALLOUTS (Call in Sick)
# ═══════════════════════════════════════════════════════════════════

@router.post("/callouts")
async def create_callout(
    request: CalloutRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a callout (call in sick)"""
    service = StaffPortalService()

    try:
        result = await service.create_callout(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id'],
            callout_date=request.callout_date,
            reason=request.reason,
            shift_id=request.shift_id,
            notes=request.notes
        )

        return {
            "success": True,
            "callout": result,
            "message": "Callout submitted"
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create callout: {str(e)}"
        )


@router.get("/callouts")
async def get_my_callouts(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get callouts for current staff member"""
    service = StaffPortalService()

    try:
        callouts = await service.get_callouts(
            restaurant_id=current_user['restaurant_id'],
            start_date=start_date,
            end_date=end_date,
            staff_id=current_user['staff_id']
        )

        return {
            "success": True,
            "callouts": callouts
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch callouts: {str(e)}"
        )

@router.get("/callouts/today")
async def get_todays_callouts_for_manager(
    current_user: dict = Depends(get_current_user)
):
    """Get today's callouts for manager dashboard alert banner"""
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can view all callouts"
        )
    
    service = StaffPortalService()
    today = _get_today_for_restaurant(current_user['restaurant_id'])
    
    try:
        callouts = await service.get_callouts(
            restaurant_id=current_user['restaurant_id'],
            start_date=today,
            end_date=today
        )
        
        # Enrich with staff names and shift details
        from database.supabase_client import get_supabase
        supabase = get_supabase()
        
        enriched = []
        for callout in callouts:
            staff_result = supabase.table("staff").select("full_name, position").eq("staff_id", callout['staff_id']).single().execute()
            staff_name = staff_result.data.get('full_name', 'Unknown') if staff_result.data else 'Unknown'
            
            shift_info = None
            if callout.get('shift_id'):
                shift_result = supabase.table("sse_shifts").select("scheduled_start, scheduled_end, position").eq("id", callout['shift_id']).single().execute()
                if shift_result.data:
                    shift_info = shift_result.data
            
            enriched.append({
                **callout,
                'staff_name': staff_name,
                'shift_info': shift_info
            })
        
        return {
            "success": True,
            "callouts": enriched,
            "count": len(enriched)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch callouts: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════
# MY SCHEDULE
# ═══════════════════════════════════════════════════════════════════

@router.get("/my-schedule")
async def get_my_schedule(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get current staff member's schedule"""
    service = StaffPortalService()

    # Default to current week + next week
    if not start_date:
        today = _get_today_for_restaurant(current_user['restaurant_id'])
        start_date = today - timedelta(days=today.weekday())  # Monday
    if not end_date:
        end_date = start_date + timedelta(days=13)  # 2 weeks

    try:
        shifts = await service.get_my_schedule(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id'],
            start_date=start_date,
            end_date=end_date
        )

        return {
            "success": True,
            "shifts": shifts,
            "count": len(shifts)
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch schedule: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════
# VOLUNTEER FOR SHIFT
# ═══════════════════════════════════════════════════════════════════

@router.post("/volunteer")
async def volunteer_for_shift(
    request: VolunteerRequest,
    current_user: dict = Depends(get_current_user)
):
    """Volunteer for an open shift"""
    service = StaffPortalService()

    try:
        result = await service.volunteer_for_shift(
            staff_id=current_user['staff_id'],
            shift_id=request.shift_id,
            restaurant_id=current_user['restaurant_id']
        )

        return {
            "success": True,
            "volunteer": result,
            "message": "Volunteer request submitted"
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to volunteer: {str(e)}"
        )

@router.get("/my-open-shift-claims")
async def get_my_open_shift_claims(current_user: dict = Depends(get_current_user)):
    """Get open shifts claimed by current staff"""
    service = StaffPortalService()
    try:
        claims = await service.get_my_open_shift_claims(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id']
        )
        return {
            "success": True,
            "claims": claims,
            "count": len(claims)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch claims: {str(e)}"
        )

# ═══════════════════════════════════════════════════════════════════
# SHIFT SWAPS
# ═══════════════════════════════════════════════════════════════════

@router.post("/swap-requests")
async def create_swap_request(
    request: SwapRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a shift swap request"""
    service = StaffPortalService()

    try:
        result = await service.create_swap_request(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id'],
            shift_id=request.shift_id,
            reason=request.reason,
            target_staff_id=request.target_staff_id
        )

        return {
            "success": True,
            "swap": result,
            "message": "Swap request created"
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create swap request: {str(e)}"
        )


@router.get("/swap-requests/mine")
async def get_my_swap_requests(current_user: dict = Depends(get_current_user)):
    """Get swap requests created by current staff"""
    service = StaffPortalService()

    try:
        swaps = await service.get_my_swap_requests(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id']
        )

        return {
            "success": True,
            "swaps": swaps,
            "count": len(swaps)
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch swap requests: {str(e)}"
        )


@router.get("/shifts-by-date")
async def get_shifts_by_date(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    current_user: dict = Depends(get_current_user)
):
    """Get all shifts and staff working on a specific date"""
    from database.supabase_client import get_supabase
    supabase = get_supabase()
    
    try:
        result = supabase.table("sse_shifts") \
            .select("id, shift_date, scheduled_start, scheduled_end, position, shift_type, staff_id") \
            .eq("restaurant_id", current_user['restaurant_id']) \
            .eq("shift_date", date) \
            .execute()
        
        shifts = result.data or []
        
        staff_ids = [s['staff_id'] for s in shifts if s.get('staff_id')]
        staff_map = {}
        if staff_ids:
            staff_result = supabase.table("staff") \
                .select("staff_id, full_name, position") \
                .in_("staff_id", staff_ids) \
                .execute()
            staff_map = {s['staff_id']: s for s in (staff_result.data or [])}
        
        enriched = []
        for shift in shifts:
            staff = staff_map.get(shift.get('staff_id'), {})
            enriched.append({
                **shift,
                'staff_name': staff.get('full_name'),
                'staff_position': staff.get('position')
            })
        
        return {
            "success": True,
            "shifts": enriched,
            "count": len(enriched)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch shifts: {str(e)}"
        )

@router.get("/swap-requests/available")
async def get_available_swap_requests(current_user: dict = Depends(get_current_user)):
    """Get swap requests available for current staff to accept"""
    service = StaffPortalService()

    try:
        swaps = await service.get_available_swap_requests(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id']
        )

        return {
            "success": True,
            "swaps": swaps,
            "count": len(swaps)
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch available swaps: {str(e)}"
        )


@router.post("/swap-requests/{swap_id}/accept")
async def accept_swap_request(
    swap_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Accept a swap request from another staff member"""
    service = StaffPortalService()

    try:
        result = await service.accept_swap(
            swap_id=swap_id,
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id']
        )

        return {
            "success": True,
            "swap": result,
            "message": "Swap accepted - pending manager approval"
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to accept swap: {str(e)}"
        )


@router.delete("/swap-requests/{swap_id}")
async def cancel_swap_request(
    swap_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Cancel a swap request (only by requester)"""
    service = StaffPortalService()

    try:
        await service.cancel_swap_request(
            swap_id=swap_id,
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id']
        )

        return {
            "success": True,
            "message": "Swap request cancelled"
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel swap: {str(e)}"
        )
    
# ═══════════════════════════════════════════════════════════════════
# NUDGES (Staff requesting features)
# ═══════════════════════════════════════════════════════════════════

class NudgeRequest(BaseModel):
    module_key: str
    message: Optional[str] = None


@router.post("/nudges")
async def create_nudge(
    request: NudgeRequest,
    current_user: dict = Depends(get_current_user)
):
    """Staff nudges manager to enable a feature"""
    from database.supabase_client import get_supabase
    from datetime import datetime, timedelta
    
    valid_modules = ['openShifts', 'shiftSwap', 'schedule', 'aime', 'stableHire', 'houseGuardian']
    if request.module_key not in valid_modules:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid module. Must be one of: {valid_modules}"
        )
    
    supabase = get_supabase()
    
    try:
        # Skip cooldown check for demo restaurants (allows repeated demos)
        demo_restaurants = [1, 11]  # Demo Bistro, Baseline Grill
        is_demo = current_user.get('restaurant_id') in demo_restaurants
        
        if not is_demo:
            week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
            existing = supabase.table("nudges") \
                .select("id") \
                .eq("staff_id", current_user['staff_id']) \
                .eq("module_key", request.module_key) \
                .gte("created_at", week_ago) \
                .execute()
            if existing.data and len(existing.data) > 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="You've already requested this feature recently"
                )
        
        payload = {
            "staff_id": current_user['staff_id'],
            "restaurant_id": current_user['restaurant_id'],
            "module_key": request.module_key,
            "message": request.message,
            "status": "pending"
        }
        
        result = supabase.table("nudges") \
            .insert(payload) \
            .execute()
        
        try:
            service = StaffPortalService()
            nudge_rule = get_sp_rule("nudgeBoss", current_user['restaurant_id'])
            await service.award_points(
                staff_id=current_user['staff_id'],
                restaurant_id=current_user['restaurant_id'],
                points=nudge_rule["points"],
                transaction_type="nudgeBoss",
                description=f"Requested {request.module_key} feature"
            )
        except Exception as e:
            logger.warning(f"Failed to award nudge SP: {e}")
        
        return {
            "success": True,
            "nudge": result.data[0] if result.data else None,
            "message": "Your request has been sent to management!",
            "sp_awarded": 5
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create nudge: {str(e)}"
        )


@router.get("/nudges")
async def get_nudges_for_manager(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    current_user: dict = Depends(get_current_user)
):
    """Get nudges for manager's restaurant (Action Board)"""
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can view nudges"
        )
    
    from database.supabase_client import get_supabase
    supabase = get_supabase()
    
    try:
        query = supabase.table("nudges") \
            .select("*, staff:staff_id(full_name, position)") \
            .eq("restaurant_id", current_user['restaurant_id']) \
            .order("created_at", desc=True)
        
        if status_filter:
            query = query.eq("status", status_filter)
        
        result = query.limit(50).execute()
        
        return {
            "success": True,
            "nudges": result.data or [],
            "count": len(result.data or [])
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch nudges: {str(e)}"
        )


@router.put("/nudges/{nudge_id}/acknowledge")
async def acknowledge_nudge(
    nudge_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Manager acknowledges a nudge"""
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can acknowledge nudges"
        )
    
    from database.supabase_client import get_supabase
    from datetime import datetime, timezone
    supabase = get_supabase()
    
    try:
        result = supabase.table("nudges") \
            .update({
                "status": "acknowledged",
                "viewed_at": datetime.now(timezone.utc).isoformat(),
                "viewed_by": current_user['staff_id']
            }) \
            .eq("id", nudge_id) \
            .eq("restaurant_id", current_user['restaurant_id']) \
            .execute()
        
        return {
            "success": True,
            "nudge": result.data[0] if result.data else None
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to acknowledge nudge: {str(e)}"
        )
    
@router.put("/nudges/acknowledge-bulk")
async def acknowledge_nudges_bulk(
    nudge_ids: List[int],
    current_user: dict = Depends(get_current_user)
):
    """Manager acknowledges multiple nudges at once"""
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can acknowledge nudges"
        )
    
    from database.supabase_client import get_supabase
    supabase = get_supabase()
    
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        
        result = supabase.table("nudges") \
            .update({
                "status": "acknowledged",
                "viewed_at": now,
                "viewed_by": current_user['staff_id']
            }) \
            .in_("id", nudge_ids) \
            .eq("restaurant_id", current_user['restaurant_id']) \
            .execute()
        
        return {
            "success": True,
            "acknowledged_count": len(result.data) if result.data else 0
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to acknowledge nudges: {str(e)}"
        )
    
@router.get("/me/personality")
async def get_my_personality(current_user: dict = Depends(get_current_user)):
    """Get current staff member's personality profile"""
    service = StaffPortalService()

    try:
        profile = await service.get_personality_profile(current_user['staff_id'])
        return {
            "success": True,
            "profile": profile  # None if not completed
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch personality profile: {str(e)}"
        )


@router.post("/me/personality")
async def submit_my_personality(
    submission: PersonalitySubmission,
    current_user: dict = Depends(get_current_user)
):
    """Submit personality assessment (self-assessment by staff member)"""
    service = StaffPortalService()

    # Validate all 8 scenarios present
    valid_scenarios = {
        "break_room", "expo_backup", "schedule_surprise", "guest_complaint",
        "coworker_tension", "new_hire_shadow", "bar_rush", "manager_feedback"
    }
    submitted = set(submission.scenario_rankings.keys())
    missing = valid_scenarios - submitted
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing scenarios: {', '.join(sorted(missing))}"
        )

    # Validate character choices
    valid_characters = {"alex", "jordan", "taylor"}
    for scenario, choice in submission.scenario_rankings.items():
        if choice.lower() not in valid_characters:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid character '{choice}' for scenario '{scenario}'"
            )

    try:
        profile = await service.save_personality_profile(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id'],
            scenario_rankings=submission.scenario_rankings,
            source="self_assessment"
        )
        return {
            "success": True,
            "profile": profile,
            "points_awarded": profile.get("points_awarded", 0),
            "message": "Personality profile saved"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save personality profile: {str(e)}"
        )


# --- Manager endpoints ---

@router.get("/staff/{staff_id}/personality")
async def get_staff_personality(
    staff_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Manager views a staff member's personality profile (read-only)"""
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can view staff personality profiles"
        )

    service = StaffPortalService()

    try:
        profile = await service.get_personality_profile(staff_id)
        return {
            "success": True,
            "profile": profile
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch personality profile: {str(e)}"
        )


@router.post("/staff/{staff_id}/personality")
async def enter_staff_personality(
    staff_id: str,
    submission: PersonalitySubmission,
    current_user: dict = Depends(get_current_user)
):
    """Manager enters paper questionnaire answers for a staff member"""
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can enter staff personality data"
        )

    service = StaffPortalService()

    # Validate all 8 scenarios present
    valid_scenarios = {
        "break_room", "expo_backup", "schedule_surprise", "guest_complaint",
        "coworker_tension", "new_hire_shadow", "bar_rush", "manager_feedback"
    }
    submitted = set(submission.scenario_rankings.keys())
    missing = valid_scenarios - submitted
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing scenarios: {', '.join(sorted(missing))}"
        )

    try:
        profile = await service.save_personality_profile(
            staff_id=staff_id,
            restaurant_id=current_user['restaurant_id'],
            scenario_rankings=submission.scenario_rankings,
            source="manager_entry"
        )
        return {
            "success": True,
            "profile": profile,
            "message": "Personality profile entered for staff member"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save personality profile: {str(e)}"
        )


@router.get("/team-composition")
async def get_team_composition(current_user: dict = Depends(get_current_user)):
    """Get aggregated team personality composition for the restaurant"""
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can view team composition"
        )

    service = StaffPortalService()

    try:
        composition = await service.get_team_composition(
            restaurant_id=current_user['restaurant_id']
        )
        return {
            "success": True,
            **composition
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch team composition: {str(e)}"
        )