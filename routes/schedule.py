"""
SCHEDULE ROUTES
===============
Endpoints for schedule upload queue and analysis retrieval.

POST /api/schedule/upload - Queue a schedule for overnight analysis
GET /api/schedule/status/{upload_id} - Check status of an upload
GET /api/schedule/history - Get past schedule analyses
GET /api/schedule/profiles - Get staff work profiles
PUT /api/schedule/profiles/{staff_id} - Update a staff work profile
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, date, timedelta

from supabase import create_client, Client
from config.settings import SUPABASE_URL, SUPABASE_KEY
from services.auth_service import verify_jwt_token
from services.feature_gate import require_feature

import logging
from services.schedule_parser_service import parse_schedule
from services.twilio_service import send_sms
import asyncio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedule", tags=["schedule"])

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ═══════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════

class UploadScheduleRequest(BaseModel):
    raw_schedule: str
    week_of: str  # YYYY-MM-DD
    manager_notes: Optional[str] = ""
    auto_publish: Optional[bool] = True

class UploadScheduleResponse(BaseModel):
    success: bool
    upload_id: Optional[int] = None
    message: str
    week_of: Optional[str] = None
    shifts_imported: int = 0
    unmapped_names: List[str] = []

class WorkProfileUpdate(BaseModel):
    hired_shift: Optional[str] = None
    hired_days: Optional[List[str]] = None
    hired_hours_target: Optional[int] = None
    unavailable_days: Optional[List[str]] = None
    unavailable_before: Optional[str] = None
    unavailable_after: Optional[str] = None
    availability_reason: Optional[str] = None
    preferred_shift: Optional[str] = None
    preferred_days: Optional[List[str]] = None
    preferred_max_hours: Optional[int] = None
    preference_notes: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════
# UPLOAD ENDPOINT (QUEUE FOR OVERNIGHT PROCESSING)
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/upload", response_model=UploadScheduleResponse)
async def upload_schedule(
    request: UploadScheduleRequest,
    background_tasks: BackgroundTasks,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Upload a schedule - saves immediately, parsing runs in background.
    Frontend should poll /status/{upload_id} for completion.
    """
    restaurant_id = current_staff.get("restaurant_id")
    staff_id = current_staff.get("staff_id")

    if not restaurant_id:
        raise HTTPException(status_code=400, detail="No restaurant associated with user")

    if not request.raw_schedule or len(request.raw_schedule.strip()) < 10:
        raise HTTPException(status_code=400, detail="Schedule data is too short or empty")

    try:
        existing = supabase.table("schedule_uploads") \
            .select("id") \
            .eq("restaurant_id", restaurant_id) \
            .eq("week_of", request.week_of) \
            .execute()

        if existing.data:
            upload_id = existing.data[0]["id"]
            supabase.table("schedule_uploads") \
                .update({
                    "raw_schedule": request.raw_schedule,
                    "manager_notes": request.manager_notes,
                    "uploaded_by": staff_id,
                    "status": "processing",
                    "error_message": None,
                    "created_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", upload_id) \
                .execute()
        else:
            result = supabase.table("schedule_uploads") \
                .insert({
                    "restaurant_id": restaurant_id,
                    "uploaded_by": staff_id,
                    "week_of": request.week_of,
                    "raw_schedule": request.raw_schedule,
                    "manager_notes": request.manager_notes,
                    "status": "processing"
                }) \
                .execute()
            upload_id = result.data[0]["id"] if result.data else None

        if not upload_id:
            raise HTTPException(status_code=500, detail="Failed to create upload record")

        background_tasks.add_task(
            process_schedule_background,
            upload_id=upload_id,
            raw_schedule=request.raw_schedule,
            restaurant_id=restaurant_id,
            week_of=request.week_of,
            staff_id=staff_id,
            auto_publish=request.auto_publish
        )

        logger.info(f"Schedule queued for background processing: upload_id={upload_id}")

        return UploadScheduleResponse(
            success=True,
            upload_id=upload_id,
            message="Schedule uploaded! Processing in background...",
            week_of=request.week_of,
            shifts_imported=0,
            unmapped_names=[]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Schedule upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


async def process_schedule_background(
    upload_id: int,
    raw_schedule: str,
    restaurant_id: int,
    week_of: str,
    staff_id: str,
    auto_publish: bool = True
):
    """Background task: Parse schedule and save shifts."""
    try:
        logger.info(f"Background processing started: upload_id={upload_id}")

        parse_result = await parse_schedule(
            raw_schedule=raw_schedule,
            restaurant_id=restaurant_id,
            week_of=week_of
        )

        if not parse_result.get("success"):
            supabase.table("schedule_uploads") \
                .update({
                    "status": "failed",
                    "error_message": parse_result.get("error", "Parse failed"),
                    "processed_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", upload_id) \
                .execute()
            logger.error(f"Background parse failed: upload_id={upload_id}")
            return

        shifts = parse_result.get("shifts", [])
        shifts_saved = 0

        if auto_publish:
            for shift in shifts:
                try:
                    shift_date = shift.get("date")
                    start_time = shift.get("start_time", "09:00")
                    end_time = shift.get("end_time", "17:00")

                    start_hour = int(start_time.split(":")[0])
                    shift_type = "AM" if start_hour < 14 else "PM"

                    shift_dt = datetime.strptime(shift_date, "%Y-%m-%d")
                    day_type = "weekend" if shift_dt.weekday() >= 5 else "weekday"

                    shift_data = {
                        "restaurant_id": restaurant_id,
                        "staff_id": shift.get("staff_id"),
                        "shift_date": shift_date,
                        "scheduled_start": f"{shift_date}T{start_time}:00Z",
                        "scheduled_end": f"{shift_date}T{end_time}:00Z",
                        "shift_type": shift_type,
                        "day_type": day_type,
                        "position": shift.get("position"),
                        "is_published": True,
                        "status": "assigned",
                        "created_by": staff_id
                    }

                    supabase.table("sse_shifts").insert(shift_data).execute()
                    shifts_saved += 1

                except Exception as e:
                    logger.warning(f"Failed to save shift: {e}")
                    continue

        # Send SMS to all staff about new schedule
        try:
            await _notify_schedule_published(restaurant_id, week_of)
        except Exception as sms_err:
            logger.error(f"Schedule published SMS failed: {sms_err}")
            # Don't fail the upload if SMS fails
        
        supabase.table("schedule_uploads") \
            .update({
                "status": "completed",
                "processed_at": datetime.utcnow().isoformat(),
                "error_message": None
            }) \
            .eq("id", upload_id) \
            .execute()
        logger.info(f"Background processing complete: upload_id={upload_id}, shifts_saved={shifts_saved}")

    except Exception as e:
        logger.error(f"Background processing crashed: upload_id={upload_id}, error={e}", exc_info=True)
        try:
            supabase.table("schedule_uploads") \
                .update({
                    "status": "failed",
                    "error_message": str(e),
                    "processed_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", upload_id) \
                .execute()
        except:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# STATUS & HISTORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

async def process_schedule_background(
    upload_id: int,
    raw_schedule: str,
    restaurant_id: int,
    week_of: str,
    staff_id: str
):
    """
    Background task: Parse schedule and save shifts.
    Updates schedule_uploads status when complete.
    """
    try:
        logger.info(f"Background processing started: upload_id={upload_id}")

        parse_result = await parse_schedule(
            raw_schedule=raw_schedule,
            restaurant_id=restaurant_id,
            week_of=week_of
        )

        if not parse_result.get("success"):
            supabase.table("schedule_uploads") \
                .update({
                    "status": "failed",
                    "error_message": parse_result.get("error", "Parse failed"),
                    "processed_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", upload_id) \
                .execute()
            logger.error(f"Background parse failed: upload_id={upload_id}")
            return

        shifts = parse_result.get("shifts", [])

        shifts_saved = 0
        for shift in shifts:
            try:
                shift_date = shift.get("date")
                start_time = shift.get("start_time", "09:00")
                end_time = shift.get("end_time", "17:00")

                start_hour = int(start_time.split(":")[0])
                shift_type = "AM" if start_hour < 14 else "PM"

                shift_dt = datetime.strptime(shift_date, "%Y-%m-%d")
                day_type = "weekend" if shift_dt.weekday() >= 5 else "weekday"

                shift_data = {
                    "restaurant_id": restaurant_id,
                    "staff_id": shift.get("staff_id"),
                    "shift_date": shift_date,
                    "scheduled_start": f"{shift_date}T{start_time}:00Z",
                    "scheduled_end": f"{shift_date}T{end_time}:00Z",
                    "shift_type": shift_type,
                    "day_type": day_type,
                    "position": shift.get("position"),
                    "is_published": True,
                    "status": "assigned",
                    "created_by": staff_id
                }

                supabase.table("sse_shifts").insert(shift_data).execute()
                shifts_saved += 1

            except Exception as e:
                logger.warning(f"Failed to save shift: {e}")
                continue

        supabase.table("schedule_uploads") \
            .update({
                "status": "completed",
                "processed_at": datetime.utcnow().isoformat(),
                "error_message": None
            }) \
            .eq("id", upload_id) \
            .execute()

        logger.info(f"Background processing complete: upload_id={upload_id}, shifts_saved={shifts_saved}")

    except Exception as e:
        logger.error(f"Background processing crashed: upload_id={upload_id}, error={e}", exc_info=True)
        try:
            supabase.table("schedule_uploads") \
                .update({
                    "status": "failed",
                    "error_message": str(e),
                    "processed_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", upload_id) \
                .execute()
        except:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# STATUS & HISTORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# STATUS & HISTORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/status/{upload_id}")
async def get_upload_status(
    upload_id: int,
    current_staff: Dict[str, Any] = Depends(require_feature("stable_schedule"))
):
    """Get status of a specific schedule upload."""
    restaurant_id = current_staff.get("restaurant_id")
    
    try:
        result = supabase.table("schedule_uploads") \
            .select("*") \
            .eq("id", upload_id) \
            .eq("restaurant_id", restaurant_id) \
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Upload not found")
        
        upload = result.data[0]
        
        return {
            "success": True,
            "upload": {
                "id": upload["id"],
                "week_of": upload["week_of"],
                "status": upload["status"],
                "stability_score": upload.get("stability_score"),
                "issues_found": upload.get("issues_found", 0),
                "critical_issues": upload.get("critical_issues", 0),
                "created_at": upload["created_at"],
                "processed_at": upload.get("processed_at"),
                "error_message": upload.get("error_message")
            },
            "analysis": upload.get("analysis_result") if upload["status"] == "completed" else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch upload status: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch status")


@router.get("/history")
async def get_schedule_history(
    limit: int = 10,
    current_staff: Dict[str, Any] = Depends(require_feature("stable_schedule"))
):
    """Get past schedule analyses for the restaurant."""
    restaurant_id = current_staff.get("restaurant_id")
    
    try:
        result = supabase.table("schedule_uploads") \
            .select("id, week_of, status, stability_score, issues_found, critical_issues, created_at, processed_at") \
            .eq("restaurant_id", restaurant_id) \
            .order("week_of", desc=True) \
            .limit(limit) \
            .execute()
        
        return {
            "success": True,
            "uploads": result.data or []
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch schedule history: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch history")


@router.get("/latest")
async def get_latest_analysis(
    current_staff: Dict[str, Any] = Depends(require_feature("stable_schedule"))
):
    """Get the most recent completed schedule analysis."""
    restaurant_id = current_staff.get("restaurant_id")
    
    try:
        result = supabase.table("schedule_uploads") \
            .select("*") \
            .eq("restaurant_id", restaurant_id) \
            .eq("status", "completed") \
            .order("week_of", desc=True) \
            .limit(1) \
            .execute()
        
        if not result.data:
            return {
                "success": True,
                "has_analysis": False,
                "message": "No completed analyses yet"
            }
        
        upload = result.data[0]
        
        return {
            "success": True,
            "has_analysis": True,
            "upload": {
                "id": upload["id"],
                "week_of": upload["week_of"],
                "stability_score": upload.get("stability_score"),
                "issues_found": upload.get("issues_found", 0),
                "critical_issues": upload.get("critical_issues", 0),
                "processed_at": upload.get("processed_at")
            },
            "analysis": upload.get("analysis_result")
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch latest analysis: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch analysis")


# ═══════════════════════════════════════════════════════════════════════════
# WORK PROFILES
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/profiles")
async def get_work_profiles(
    current_staff: Dict[str, Any] = Depends(require_feature("stable_schedule"))
):
    """Get all staff work profiles for the restaurant."""
    restaurant_id = current_staff.get("restaurant_id")
    
    try:
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
    current_staff: Dict[str, Any] = Depends(require_feature("stable_schedule"))
):
    """Update a staff member's work profile."""
    restaurant_id = current_staff.get("restaurant_id")
    
    update_data = {k: v for k, v in update.dict().items() if v is not None}
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    update_data["updated_at"] = datetime.utcnow().isoformat()
    
    pref_fields = ["preferred_shift", "preferred_days", "preferred_max_hours", "preference_notes"]
    if any(f in update_data for f in pref_fields):
        update_data["preference_updated_at"] = datetime.utcnow().isoformat()
    
    try:
        existing = supabase.table("staff_work_profile") \
            .select("id") \
            .eq("staff_id", staff_id) \
            .eq("restaurant_id", restaurant_id) \
            .execute()
        
        if existing.data:
            result = supabase.table("staff_work_profile") \
                .update(update_data) \
                .eq("staff_id", staff_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
        else:
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
    
# ═══════════════════════════════════════════════════════════════════════════
# PREVENTED ISSUES (Manager clicked "I'll Fix This")
# ═══════════════════════════════════════════════════════════════════════════

class PreventedIssueCreate(BaseModel):
    week_of: str
    schedule_upload_id: Optional[int] = None
    issue_type: str
    severity: str
    title: str
    description: Optional[str] = None
    affected_staff_ids: Optional[List[str]] = None
    affected_staff_names: Optional[List[str]] = None


@router.post("/prevented")
async def log_prevented_issue(
    issue: PreventedIssueCreate,
    current_staff: Dict[str, Any] = Depends(require_feature("stable_schedule"))
):
    """
    Log an issue that the manager will fix before publishing.
    Tracks what En Place helped prevent.
    """
    restaurant_id = current_staff.get("restaurant_id")
    staff_id = current_staff.get("staff_id")

    if not restaurant_id:
        raise HTTPException(status_code=400, detail="No restaurant associated with user")

    try:
        result = supabase.table("schedule_prevented_issues") \
            .insert({
                "restaurant_id": restaurant_id,
                "schedule_upload_id": issue.schedule_upload_id,
                "week_of": issue.week_of,
                "issue_type": issue.issue_type,
                "severity": issue.severity,
                "title": issue.title,
                "description": issue.description,
                "affected_staff_ids": issue.affected_staff_ids,
                "affected_staff_names": issue.affected_staff_names,
                "prevented_by": staff_id
            }) \
            .execute()

        logger.info(f"Prevented issue logged: restaurant={restaurant_id}, type={issue.issue_type}")

        return {
            "success": True,
            "prevented_issue": result.data[0] if result.data else None,
            "message": "Issue logged as prevented"
        }

    except Exception as e:
        logger.error(f"Failed to log prevented issue: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to log issue: {str(e)}")


@router.get("/prevented/stats")
async def get_prevented_stats(
    current_staff: Dict[str, Any] = Depends(require_feature("stable_schedule"))
):
    """
    Get prevented issue statistics for analytics.
    """
    restaurant_id = current_staff.get("restaurant_id")

    try:
        result = supabase.table("schedule_prevented_issues") \
            .select("id, issue_type, severity, prevented_at") \
            .eq("restaurant_id", restaurant_id) \
            .execute()

        issues = result.data or []
        total = len(issues)
        by_type = {}
        by_severity = {}

        for issue in issues:
            t = issue.get("issue_type", "unknown")
            s = issue.get("severity", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            by_severity[s] = by_severity.get(s, 0) + 1

        return {
            "success": True,
            "stats": {
                "total_prevented": total,
                "by_type": by_type,
                "by_severity": by_severity,
                "high_severity_prevented": by_severity.get("high", 0)
            }
        }

    except Exception as e:
        logger.error(f"Failed to fetch prevented stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch stats")
    
async def _notify_schedule_published(restaurant_id: int, week_of: str):
    """
    Send SMS to all SMS-enabled staff when schedule is published.
    """
    # Get restaurant name
    rest_result = supabase.table("restaurants").select("name").eq("id", restaurant_id).single().execute()
    restaurant_name = rest_result.data.get("name", "your restaurant") if rest_result.data else "your restaurant"
    
    # Format week range for message
    try:
        week_start = datetime.strptime(week_of, "%Y-%m-%d")
        week_end = week_start + timedelta(days=6)
        week_range = f"{week_start.strftime('%b %d')}-{week_end.strftime('%d')}"
    except:
        week_range = "this week"
    
    # Get all SMS-enabled staff
    staff_result = supabase.table("staff")\
        .select("staff_id, full_name, phone")\
        .eq("restaurant_id", restaurant_id)\
        .eq("status", "active")\
        .eq("sms_notifications_enabled", True)\
        .not_.is_("phone", "null")\
        .execute()
    
    if not staff_result.data:
        logger.info(f"No SMS-enabled staff for restaurant {restaurant_id}")
        return
    
    sent_count = 0
    for staff in staff_result.data:
        phone = staff.get("phone")
        if not phone:
            continue
        
        first_name = staff["full_name"].split()[0] if staff.get("full_name") else ""
        message = f"Hey {first_name}! New schedule posted for {week_range}. Check your shifts: https://app.en-place.ai/staff-portal"
        
        result = send_sms(phone, message)
        if result.get("success"):
            sent_count += 1
    
    logger.info(f"Schedule published SMS sent to {sent_count} staff for restaurant {restaurant_id}")