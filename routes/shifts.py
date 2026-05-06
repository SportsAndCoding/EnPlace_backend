from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from typing import List, Optional
from datetime import date, datetime, timedelta
import pytz
from services.auth_service import verify_jwt_token as get_current_user
from services.shifts_service import ShiftsService
from models.shifts import ShiftCreate, ShiftUpdate, ShiftResponse, ShiftCreateResponse
from services.twilio_service import send_sms
import logging

logger = logging.getLogger(__name__)

def _get_today_for_restaurant(organization_id: int) -> date:
    """Get today's date in restaurant timezone."""
    from database.supabase_client import get_supabase
    supabase = get_supabase()
    try:
        result = supabase.table("organizations").select("timezone").eq("id", organization_id).single().execute()
        tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
    except:
        tz_name = "America/New_York"
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()


router = APIRouter(prefix="/api/shifts", tags=["shifts"])

@router.post("", response_model=ShiftCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_shift(
    shift: ShiftCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new shift.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can create shifts"
        )
    
    # Verify restaurant access
    if current_user['organization_id'] != shift.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    service = ShiftsService()
    
    try:
        result = await service.create_shift(
            shift_data=shift.dict(),
            created_by=current_user['staff_id']
        )
        
        # Check if this is a same-day open shift - send SMS alerts
        try:
            await _notify_open_shift_if_eligible(
                shift_data=shift.dict(),
                shift_id=result['id'],
                organization_id=shift.organization_id
            )
        except Exception as sms_err:
            logger.error(f"Open shift SMS notification failed: {sms_err}")
            # Don't fail the shift creation if SMS fails
        
        return ShiftCreateResponse(
            success=True,
            shift_id=result['id'],
            message="Shift created"
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create shift: {str(e)}"
        )


@router.get("", response_model=List[ShiftResponse])
async def get_shifts(
    organization_id: int,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    staff_id: Optional[str] = Query(default=None),
    is_published: Optional[bool] = Query(default=None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get shifts for a restaurant.
    Defaults to current week if no dates provided.
    
    Optional filters:
    - staff_id: Filter to specific staff member
    - is_published: Filter by published status
    """
    # Verify restaurant access
    if current_user['organization_id'] != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Default to current week (Mon-Sun)
    if not start_date:
        today = _get_today_for_restaurant(organization_id)
        start_date = today - timedelta(days=today.weekday())  # Monday
    if not end_date:
        end_date = start_date + timedelta(days=6)  # Sunday
    
    service = ShiftsService()
    
    try:
        shifts = await service.get_shifts_by_restaurant(
            organization_id=organization_id,
            start_date=start_date,
            end_date=end_date,
            staff_id=staff_id,
            is_published=is_published
        )
        return shifts
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch shifts: {str(e)}"
        )


@router.get("/open")
async def get_open_shifts(
    organization_id: int,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get unassigned (open) shifts.
    Used for open shift marketplace.
    """
    # Verify restaurant access
    if current_user['organization_id'] != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Default to next 14 days
    if not start_date:
        start_date = _get_today_for_restaurant(organization_id)
    if not end_date:
        end_date = start_date + timedelta(days=14)
    
    service = ShiftsService()
    
    try:
        shifts = await service.get_open_shifts(
            organization_id=organization_id,
            start_date=start_date,
            end_date=end_date
        )
        
        return {
            "success": True,
            "open_shifts": shifts,
            "count": len(shifts)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch open shifts: {str(e)}"
        )

@router.get("/open/pending")
async def get_pending_open_shift_claims(
    current_user: dict = Depends(get_current_user)
):
    """Get open shifts with pending claims awaiting manager approval"""
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can view pending claims"
        )
    
    service = ShiftsService()
    try:
        claims = await service.get_pending_open_shift_claims(
            organization_id=current_user['organization_id']
        )
        return {
            "success": True,
            "claims": claims,
            "count": len(claims)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch pending claims: {str(e)}"
        )

@router.get("/my")
async def get_my_shifts(
    current_staff: dict = Depends(get_current_user),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    include_coworkers: bool = Query(default=False)
):
    """Get authenticated staff member's own shifts"""
    from database.supabase_client import get_supabase
    
    staff_id = current_staff.get("staff_id")
    organization_id = current_staff.get("organization_id")
    
    if not staff_id or not organization_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    
    # Default to 2 weeks from today
    today = _get_today_for_restaurant(organization_id)
    if not start_date:
        start_date = today
    if not end_date:
        end_date = start_date + timedelta(days=14)
    
    supabase = get_supabase()
    
    # Get this staff member's shifts
    result = supabase.table("sse_shifts")\
        .select("id, shift_date, scheduled_start, scheduled_end, position, status")\
        .eq("staff_id", staff_id)\
        .eq("organization_id", organization_id)\
        .gte("shift_date", start_date.isoformat())\
        .lte("shift_date", end_date.isoformat())\
        .order("shift_date")\
        .execute()
    
    shifts = result.data or []
    
    # Optionally include coworkers for each shift date
    if include_coworkers and shifts:
        shift_dates = list(set(s["shift_date"] for s in shifts))
        
        # Get all shifts on those dates (excluding current staff)
        coworker_result = supabase.table("sse_shifts")\
            .select("shift_date, scheduled_start, scheduled_end, staff_id")\
            .eq("organization_id", organization_id)\
            .in_("shift_date", shift_dates)\
            .neq("staff_id", staff_id)\
            .not_.is_("staff_id", "null")\
            .execute()
        
        coworker_shifts = coworker_result.data or []
        
        # Get coworker names
        coworker_ids = list(set(c["staff_id"] for c in coworker_shifts))
        if coworker_ids:
            staff_result = supabase.table("staff")\
                .select("staff_id, full_name, position")\
                .in_("staff_id", coworker_ids)\
                .execute()
            staff_map = {s["staff_id"]: s for s in (staff_result.data or [])}
        else:
            staff_map = {}
        
        # Build coworkers by date
        coworkers_by_date = {}
        for c in coworker_shifts:
            d = c["shift_date"]
            if d not in coworkers_by_date:
                coworkers_by_date[d] = []
            staff_info = staff_map.get(c["staff_id"], {})
            coworkers_by_date[d].append({
                "staff_id": c["staff_id"],
                "full_name": staff_info.get("full_name", "Unknown"),
                "position": staff_info.get("position"),
                "scheduled_start": c["scheduled_start"],
                "scheduled_end": c["scheduled_end"]
            })
        
        # Attach coworkers to each shift
        for shift in shifts:
            shift["coworkers"] = coworkers_by_date.get(shift["shift_date"], [])
    
    return {
        "success": True,
        "staff_id": staff_id,
        "date_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "shifts": shifts
    }


@router.get("/{shift_id}")
async def get_shift(
    shift_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get a single shift by ID"""
    service = ShiftsService()
    
    try:
        shift = await service.get_shift_by_id(
            shift_id=shift_id,
            organization_id=current_user['organization_id']
        )
        
        if not shift:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shift not found"
            )
        
        return {
            "success": True,
            "shift": shift
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch shift: {str(e)}"
        )


@router.put("/{shift_id}")
async def update_shift(
    shift_id: int,
    shift: ShiftUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update an existing shift.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can update shifts"
        )
    
    service = ShiftsService()
    
    try:
        # Verify shift exists and belongs to this restaurant
        existing = await service.get_shift_by_id(
            shift_id=shift_id,
            organization_id=current_user['organization_id']
        )
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shift not found"
            )
        
        result = await service.update_shift(
            shift_id=shift_id,
            organization_id=current_user['organization_id'],
            update_data=shift.dict()
        )
        
        return {
            "success": True,
            "shift": result,
            "message": "Shift updated"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update shift: {str(e)}"
        )


@router.delete("/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift(
    shift_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a shift.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can delete shifts"
        )
    
    service = ShiftsService()
    
    try:
        # Verify shift exists
        existing = await service.get_shift_by_id(
            shift_id=shift_id,
            organization_id=current_user['organization_id']
        )
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shift not found"
            )
        
        await service.delete_shift(
            shift_id=shift_id,
            organization_id=current_user['organization_id']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete shift: {str(e)}"
        )
    
@router.get("/{shift_id}/volunteers")
async def get_shift_volunteers(
    shift_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    Get volunteers for a specific shift.
    Returns staff info + hours this week + response time.
    """
    service = ShiftsService()
    
    try:
        volunteers = await service.get_shift_volunteers(
            shift_id=shift_id,
            organization_id=current_user['organization_id']
        )
        
        return {
            "success": True,
            "volunteers": volunteers,
            "count": len(volunteers)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch volunteers: {str(e)}"
        )
    
@router.put("/open/{shift_id}")
async def update_open_shift(
    shift_id: str,
    update_data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Update an open shift (marketplace)"""
    if current_user.get('portal_access') != 'manager':
        raise HTTPException(status_code=403, detail="Only managers can update shifts")
    
    service = ShiftsService()
    result = await service.update_open_shift(
        shift_id=shift_id,
        organization_id=current_user['organization_id'],
        update_data=update_data
    )
    
    if not result:
        raise HTTPException(status_code=404, detail="Shift not found")
    
    return {"success": True, "shift": result}

@router.get("/open/{shift_id}/volunteers")
async def get_open_shift_volunteers(
    shift_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get all volunteers for an open shift (manager view)"""
    if current_user.get('portal_access') != 'manager':
        raise HTTPException(status_code=403, detail="Manager access required")
    
    service = ShiftsService()
    try:
        volunteers = await service.get_open_shift_volunteers(
            shift_id=shift_id,
            organization_id=current_user['organization_id']
        )
        return {
            "success": True,
            "volunteers": volunteers,
            "count": len(volunteers)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/open/{shift_id}/select-volunteer")
async def select_volunteer(
    shift_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Manager selects a volunteer for an open shift"""
    if current_user.get('portal_access') != 'manager':
        raise HTTPException(status_code=403, detail="Manager access required")
    
    body = await request.json()
    staff_id = body.get("staff_id")
    
    if not staff_id:
        raise HTTPException(status_code=400, detail="staff_id required")
    
    service = ShiftsService()
    try:
        result = await service.select_volunteer(
            shift_id=shift_id,
            staff_id=staff_id,
            organization_id=current_user['organization_id']
        )
        return {
            "success": True,
            "shift": result
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
async def _notify_open_shift_if_eligible(shift_data: dict, shift_id: int, organization_id: int):
    """
    Send SMS to eligible staff if this is a same-day open shift.
    
    Criteria:
    - No staff_id assigned (open shift)
    - Shift is TODAY
    - Shift starts 2+ hours from now
    """
    from database.supabase_client import get_supabase
    from datetime import datetime, timedelta
    import pytz
    
    # Only open shifts (no staff assigned)
    if shift_data.get('staff_id'):
        return
    
    # Get restaurant timezone
    supabase = get_supabase()
    rest_result = supabase.table("organizations").select("timezone").eq("id", organization_id).single().execute()
    tz_name = rest_result.data.get("timezone", "America/New_York") if rest_result.data else "America/New_York"
    tz = pytz.timezone(tz_name)
    
    now = datetime.now(tz)
    today = now.date()
    
    # Check if shift is today
    shift_date = shift_data.get('shift_date')
    if isinstance(shift_date, str):
        shift_date = datetime.fromisoformat(shift_date).date()
    
    if shift_date != today:
        return
    
    # Check if shift starts 2+ hours from now
    scheduled_start = shift_data.get('scheduled_start')
    if isinstance(scheduled_start, str):
        scheduled_start = datetime.fromisoformat(scheduled_start.replace('Z', '+00:00'))
    
    if scheduled_start.astimezone(tz) < now + timedelta(hours=2):
        return
    
    # Get position for this shift
    position = shift_data.get('position', '')
    
    # Find eligible staff: active, SMS enabled, not already scheduled at this time
    staff_result = supabase.table("staff")\
        .select("staff_id, full_name, phone, position")\
        .eq("organization_id", organization_id)\
        .eq("status", "active")\
        .eq("sms_notifications_enabled", True)\
        .not_.is_("phone", "null")\
        .execute()
    
    if not staff_result.data:
        return
    
    # Format shift time for message
    start_time = scheduled_start.astimezone(tz).strftime("%-I:%M%p").lower()
    scheduled_end = shift_data.get('scheduled_end')
    if isinstance(scheduled_end, str):
        scheduled_end = datetime.fromisoformat(scheduled_end.replace('Z', '+00:00'))
    end_time = scheduled_end.astimezone(tz).strftime("%-I:%M%p").lower()
    
    sent_count = 0
    for staff in staff_result.data:
        # Optional: filter by matching position
        # if position and staff.get('position') != position:
        #     continue
        
        phone = staff.get('phone')
        if not phone:
            continue
        
        first_name = staff['full_name'].split()[0] if staff.get('full_name') else ''
        message = f"Hey {first_name}! Open shift today: {position} {start_time}-{end_time}. Claim it: https://app.en-place.ai/staff-portal"
        
        result = send_sms(phone, message)
        if result.get('success'):
            sent_count += 1
    
    logger.info(f"Open shift SMS sent to {sent_count} staff for shift {shift_id}")