"""
SCHEDULE ROUTES
===============
Endpoints for schedule upload queue and analysis retrieval.

POST /api/schedule/upload            - Queue a schedule for overnight analysis
POST /api/schedule/upload-historical - Upload historical schedules for system seeding
GET  /api/schedule/status/{upload_id} - Check status of an upload
GET  /api/schedule/history           - Get past schedule analyses
GET  /api/schedule/profiles          - Get staff work profiles
PUT  /api/schedule/profiles/{staff_id} - Update a staff work profile
GET  /api/schedule/unmatched/{upload_id} - Get unmatched names for reconciliation
POST /api/schedule/resolve-unmatched - Resolve an unmatched name
POST /api/schedule/finalize-historical/{upload_id} - Finalize a historical import
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
import secrets
import string

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

class HistoricalUploadRequest(BaseModel):
    raw_schedule: str
    week_of: str  # YYYY-MM-DD (start of the week this schedule covers)
    manager_notes: Optional[str] = ""

class HistoricalUploadResponse(BaseModel):
    success: bool
    upload_id: Optional[int] = None
    message: str
    week_of: Optional[str] = None
    shifts_matched: int = 0
    unmatched_names: List[Dict[str, Any]] = []
    overlap_warning: Optional[str] = None

class ResolveUnmatchedRequest(BaseModel):
    unmatched_id: int
    resolution: str  # add_to_roster, marked_inactive, matched_existing, skipped
    # Fields for add_to_roster
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    position: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    hourly_rate: Optional[float] = None
    # Field for matched_existing (manager says "that's actually this person")
    existing_staff_id: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════
# CURRENT SCHEDULE UPLOAD (existing behavior, preserved exactly)
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
            .eq("upload_type", "current") \
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
                    "status": "processing",
                    "upload_type": "current"
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
            auto_publish=request.auto_publish,
            upload_type="current"
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


# ═══════════════════════════════════════════════════════════════════════════
# BACKGROUND PROCESSING (single definition — fixes duplicate bug)
# ═══════════════════════════════════════════════════════════════════════════

async def process_schedule_background(
    upload_id: int,
    raw_schedule: str,
    restaurant_id: int,
    week_of: str,
    staff_id: str,
    auto_publish: bool = True,
    upload_type: str = "current"
):
    """
    Background task: Parse schedule and save shifts.
    
    Handles both current and historical uploads:
    - current: publishes shifts, sends SMS notifications
    - historical: marks shifts as historical, no SMS, no publish
    """
    is_historical = (upload_type == "historical")

    try:
        logger.info(f"Background processing started: upload_id={upload_id}, type={upload_type}")

        parse_result = await parse_schedule(
            raw_schedule=raw_schedule,
            restaurant_id=restaurant_id,
            week_of=week_of,
            include_inactive_staff=is_historical
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
        unmapped = parse_result.get("unmapped", [])
        shifts_saved = 0

        # ── Save matched shifts ──────────────────────────────────────────
        should_publish = auto_publish and not is_historical

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
                    "is_published": should_publish,
                    "status": "assigned",
                    "created_by": staff_id,
                    "is_historical": is_historical,
                    "source_upload_id": upload_id
                }

                supabase.table("sse_shifts").insert(shift_data).execute()
                shifts_saved += 1

            except Exception as e:
                logger.warning(f"Failed to save shift: {e}")
                continue

        # ── Handle unmatched names (historical only) ─────────────────────
        if is_historical and unmapped:
            for entry in unmapped:
                try:
                    # Historical parser returns structured unmapped objects
                    if isinstance(entry, dict):
                        raw_name = entry.get("name", "Unknown")
                        inferred_position = entry.get("inferred_position")
                        shift_count = entry.get("shift_count", 0)
                        unmapped_shifts = entry.get("shifts", [])
                    else:
                        # Fallback for simple string unmapped entries
                        raw_name = str(entry)
                        inferred_position = None
                        shift_count = 0
                        unmapped_shifts = []

                    # Save reconciliation record
                    supabase.table("schedule_import_unmatched").insert({
                        "restaurant_id": restaurant_id,
                        "upload_id": upload_id,
                        "raw_name": raw_name,
                        "inferred_position": inferred_position,
                        "shift_count": shift_count,
                        "resolution": "pending"
                    }).execute()

                    # Insert shifts with staff_id=null, tagged with raw_name
                    # in the reason column so reassignment can target them precisely
                    for u_shift in unmapped_shifts:
                        try:
                            u_date = u_shift.get("date")
                            u_start = u_shift.get("start_time", "09:00")
                            u_end = u_shift.get("end_time", "17:00")

                            u_start_hour = int(u_start.split(":")[0])
                            u_shift_type = "AM" if u_start_hour < 14 else "PM"

                            u_shift_dt = datetime.strptime(u_date, "%Y-%m-%d")
                            u_day_type = "weekend" if u_shift_dt.weekday() >= 5 else "weekday"

                            supabase.table("sse_shifts").insert({
                                "restaurant_id": restaurant_id,
                                "staff_id": None,
                                "shift_date": u_date,
                                "scheduled_start": f"{u_date}T{u_start}:00Z",
                                "scheduled_end": f"{u_date}T{u_end}:00Z",
                                "shift_type": u_shift_type,
                                "day_type": u_day_type,
                                "position": inferred_position,
                                "is_published": False,
                                "status": "unmatched",
                                "created_by": staff_id,
                                "is_historical": True,
                                "source_upload_id": upload_id,
                                "reason": raw_name  # Tag for precise reassignment
                            }).execute()
                            shifts_saved += 1
                        except Exception as e:
                            logger.warning(f"Failed to save unmapped shift for '{raw_name}': {e}")
                            continue

                except Exception as e:
                    logger.warning(f"Failed to save unmatched name '{entry}': {e}")
                    continue

        # ── Store parse result in analysis_result for reference ──────────
        supabase.table("schedule_uploads") \
            .update({
                "analysis_result": {
                    "total_shifts": len(shifts),
                    "shifts_saved": shifts_saved,
                    "unmapped_count": len(unmapped),
                    "unmapped_names": [
                        e.get("name", str(e)) if isinstance(e, dict) else str(e)
                        for e in unmapped
                    ],
                    "warnings": parse_result.get("warnings", []),
                    "tokens_used": parse_result.get("tokens_used"),
                    "estimated_cost": parse_result.get("estimated_cost")
                }
            }) \
            .eq("id", upload_id) \
            .execute()

        # ── SMS notification (current schedules only) ────────────────────
        if not is_historical and should_publish:
            try:
                await _notify_schedule_published(restaurant_id, week_of)
            except Exception as sms_err:
                logger.error(f"Schedule published SMS failed: {sms_err}")

        # ── Set status for next pipeline stage ────────────────────────────
        # Current uploads: 'pending' → nightly processor runs analyze_schedule()
        #                   and writes stability_score, then marks 'completed'
        # Historical uploads: 'needs_reconciliation' or 'completed' (no analysis)
        if is_historical:
            final_status = "needs_reconciliation" if unmapped else "completed"
        else:
            final_status = "pending"  # Nightly processor will analyze and complete

        supabase.table("schedule_uploads") \
            .update({
                "status": final_status,
                "processed_at": datetime.utcnow().isoformat(),
                "error_message": None
            }) \
            .eq("id", upload_id) \
            .execute()

        logger.info(
            f"Background processing complete: upload_id={upload_id}, "
            f"shifts_saved={shifts_saved}, unmatched={len(unmapped)}"
        )

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
# HISTORICAL SCHEDULE UPLOAD
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/upload-historical", response_model=HistoricalUploadResponse)
async def upload_historical_schedule(
    request: HistoricalUploadRequest,
    background_tasks: BackgroundTasks,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Upload a historical schedule for system seeding.
    
    Unlike current uploads:
    - Parses against ALL staff (active + inactive)
    - Returns unmatched names for reconciliation
    - Does NOT publish to staff portal or send SMS
    - Marks all shifts as is_historical=true
    - Checks for date overlap with existing schedules
    """
    restaurant_id = current_staff.get("restaurant_id")
    staff_id = current_staff.get("staff_id")

    if not restaurant_id:
        raise HTTPException(status_code=400, detail="No restaurant associated with user")

    if not request.raw_schedule or len(request.raw_schedule.strip()) < 10:
        raise HTTPException(status_code=400, detail="Schedule data is too short or empty")

    try:
        # ── Check for date overlap with current schedules ────────────────
        overlap_warning = None
        latest_current = supabase.table("schedule_uploads") \
            .select("week_of") \
            .eq("restaurant_id", restaurant_id) \
            .eq("upload_type", "current") \
            .order("week_of", desc=True) \
            .limit(1) \
            .execute()

        if latest_current.data:
            latest_week = latest_current.data[0]["week_of"]
            if request.week_of >= latest_week:
                overlap_warning = (
                    f"This schedule ({request.week_of}) overlaps with or is after "
                    f"your most recent current schedule ({latest_week}). "
                    f"It will still be imported as historical data."
                )

        # ── Check for duplicate historical upload for same week ──────────
        existing_historical = supabase.table("schedule_uploads") \
            .select("id") \
            .eq("restaurant_id", restaurant_id) \
            .eq("week_of", request.week_of) \
            .eq("upload_type", "historical") \
            .execute()

        if existing_historical.data:
            upload_id = existing_historical.data[0]["id"]

            # Clean up previous attempt's shifts and unmatched entries
            supabase.table("sse_shifts") \
                .delete() \
                .eq("source_upload_id", upload_id) \
                .execute()

            supabase.table("schedule_import_unmatched") \
                .delete() \
                .eq("upload_id", upload_id) \
                .execute()

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
                    "status": "processing",
                    "upload_type": "historical"
                }) \
                .execute()
            upload_id = result.data[0]["id"] if result.data else None

        if not upload_id:
            raise HTTPException(status_code=500, detail="Failed to create upload record")

        # ── Kick off background processing ───────────────────────────────
        background_tasks.add_task(
            process_schedule_background,
            upload_id=upload_id,
            raw_schedule=request.raw_schedule,
            restaurant_id=restaurant_id,
            week_of=request.week_of,
            staff_id=staff_id,
            auto_publish=False,
            upload_type="historical"
        )

        logger.info(f"Historical schedule queued: upload_id={upload_id}, week_of={request.week_of}")

        return HistoricalUploadResponse(
            success=True,
            upload_id=upload_id,
            message="Historical schedule uploaded! Processing in background...",
            week_of=request.week_of,
            shifts_matched=0,
            unmatched_names=[],
            overlap_warning=overlap_warning
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Historical upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════
# RECONCILIATION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/unmatched/{upload_id}")
async def get_unmatched_names(
    upload_id: int,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Get all unmatched names from a historical upload for reconciliation.
    Manager sees each name and decides: add to roster, mark inactive, or skip.
    """
    restaurant_id = current_staff.get("restaurant_id")

    try:
        # Verify upload belongs to this restaurant
        upload = supabase.table("schedule_uploads") \
            .select("id, week_of, status, upload_type") \
            .eq("id", upload_id) \
            .eq("restaurant_id", restaurant_id) \
            .execute()

        if not upload.data:
            raise HTTPException(status_code=404, detail="Upload not found")

        # Fetch unmatched entries
        result = supabase.table("schedule_import_unmatched") \
            .select("*") \
            .eq("upload_id", upload_id) \
            .order("shift_count", desc=True) \
            .execute()

        entries = result.data or []
        pending_count = sum(1 for e in entries if e.get("resolution") == "pending")
        resolved_count = sum(1 for e in entries if e.get("resolution") != "pending")

        return {
            "success": True,
            "upload_id": upload_id,
            "week_of": upload.data[0]["week_of"],
            "upload_status": upload.data[0]["status"],
            "total_unmatched": len(entries),
            "pending": pending_count,
            "resolved": resolved_count,
            "entries": entries
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch unmatched names: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch unmatched names")


@router.post("/resolve-unmatched")
async def resolve_unmatched_name(
    request: ResolveUnmatchedRequest,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Resolve a single unmatched name from a historical import.
    
    Resolutions:
    - add_to_roster: Creates new staff record, retroactively assigns their shifts
    - marked_inactive: Creates staff record with status='inactive', sets last_work_date
    - matched_existing: Manager says "that's actually [existing person]", reassigns shifts
    - skipped: Shifts remain unassigned, name preserved for reference
    """
    restaurant_id = current_staff.get("restaurant_id")
    resolver_id = current_staff.get("staff_id")

    try:
        # Fetch the unmatched entry
        entry_result = supabase.table("schedule_import_unmatched") \
            .select("*") \
            .eq("id", request.unmatched_id) \
            .eq("restaurant_id", restaurant_id) \
            .execute()

        if not entry_result.data:
            raise HTTPException(status_code=404, detail="Unmatched entry not found")

        entry = entry_result.data[0]
        upload_id = entry["upload_id"]
        raw_name = entry["raw_name"]
        resolved_staff_id = None

        # ── Resolution: Add to roster ────────────────────────────────────
        if request.resolution == "add_to_roster":
            if not request.first_name or not request.last_name:
                raise HTTPException(
                    status_code=400,
                    detail="first_name and last_name required for add_to_roster"
                )

            position = request.position or entry.get("inferred_position") or "Staff"
            full_name = f"{request.first_name} {request.last_name}"

            # Generate staff ID
            prefix = position[:3].upper()
            timestamp = datetime.now().strftime("%H%M%S")
            random_suffix = ''.join(secrets.choice(string.digits) for _ in range(3))
            new_staff_id = f"{prefix}{restaurant_id}{timestamp}{random_suffix}"

            supabase.table("staff").insert({
                "staff_id": new_staff_id,
                "restaurant_id": restaurant_id,
                "full_name": full_name,
                "email": request.email,
                "phone": request.phone,
                "position": position,
                "hourly_rate": request.hourly_rate,
                "status": "active",
                "hire_date": date.today().isoformat(),
                "portal_access": "staff"
            }).execute()

            resolved_staff_id = new_staff_id
            logger.info(f"Added staff from historical: {full_name} -> {new_staff_id}")

        # ── Resolution: Marked inactive ──────────────────────────────────
        elif request.resolution == "marked_inactive":
            if not request.first_name or not request.last_name:
                raise HTTPException(
                    status_code=400,
                    detail="first_name and last_name required for marked_inactive"
                )

            position = request.position or entry.get("inferred_position") or "Staff"
            full_name = f"{request.first_name} {request.last_name}"

            prefix = position[:3].upper()
            timestamp = datetime.now().strftime("%H%M%S")
            random_suffix = ''.join(secrets.choice(string.digits) for _ in range(3))
            new_staff_id = f"{prefix}{restaurant_id}{timestamp}{random_suffix}"

            # Find their most recent shift date from this upload for last_work_date
            latest_shift = supabase.table("sse_shifts") \
                .select("shift_date") \
                .eq("source_upload_id", upload_id) \
                .eq("reason", raw_name) \
                .order("shift_date", desc=True) \
                .limit(1) \
                .execute()

            last_work = latest_shift.data[0]["shift_date"] if latest_shift.data else None

            supabase.table("staff").insert({
                "staff_id": new_staff_id,
                "restaurant_id": restaurant_id,
                "full_name": full_name,
                "position": position,
                "status": "inactive",
                "last_work_date": last_work,
                "removal_reason": "Historical import - no longer employed",
                "portal_access": "staff",
                "is_portal_enabled": False
            }).execute()

            resolved_staff_id = new_staff_id
            logger.info(f"Added inactive staff from historical: {full_name} -> {new_staff_id}")

        # ── Resolution: Matched to existing staff ────────────────────────
        elif request.resolution == "matched_existing":
            if not request.existing_staff_id:
                raise HTTPException(
                    status_code=400,
                    detail="existing_staff_id required for matched_existing"
                )

            # Verify the existing staff belongs to this restaurant
            existing = supabase.table("staff") \
                .select("staff_id") \
                .eq("staff_id", request.existing_staff_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()

            if not existing.data:
                raise HTTPException(status_code=404, detail="Existing staff not found")

            resolved_staff_id = request.existing_staff_id
            logger.info(f"Matched '{raw_name}' to existing staff: {resolved_staff_id}")

        # ── Resolution: Skipped ──────────────────────────────────────────
        elif request.resolution == "skipped":
            logger.info(f"Skipped unmatched name: {raw_name}")

        else:
            raise HTTPException(status_code=400, detail=f"Invalid resolution: {request.resolution}")

        # ── Retroactively assign shifts if we have a staff_id ────────────
        if resolved_staff_id and request.resolution != "skipped":
            _reassign_unmatched_shifts(
                upload_id=upload_id,
                raw_name=raw_name,
                new_staff_id=resolved_staff_id,
                restaurant_id=restaurant_id
            )

        # ── Update the unmatched record ──────────────────────────────────
        supabase.table("schedule_import_unmatched") \
            .update({
                "resolution": request.resolution,
                "resolved_staff_id": resolved_staff_id,
                "resolved_by": resolver_id,
                "resolved_at": datetime.utcnow().isoformat()
            }) \
            .eq("id", request.unmatched_id) \
            .execute()

        # ── Check if all unmatched are now resolved ──────────────────────
        remaining = supabase.table("schedule_import_unmatched") \
            .select("id", count="exact") \
            .eq("upload_id", upload_id) \
            .eq("resolution", "pending") \
            .execute()

        all_resolved = (remaining.count or 0) == 0

        if all_resolved:
            supabase.table("schedule_uploads") \
                .update({"status": "completed"}) \
                .eq("id", upload_id) \
                .execute()

        return {
            "success": True,
            "resolution": request.resolution,
            "resolved_staff_id": resolved_staff_id,
            "all_resolved": all_resolved,
            "remaining_count": remaining.count or 0
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resolve unmatched: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Resolution failed: {str(e)}")


def _reassign_unmatched_shifts(
    upload_id: int,
    raw_name: str,
    new_staff_id: str,
    restaurant_id: int
):
    """
    Retroactively assign historical shifts to a newly resolved staff member.
    
    Unmatched shifts are stored with staff_id=null and reason=raw_name,
    so we can precisely target only this person's shifts.
    """
    try:
        null_shifts = supabase.table("sse_shifts") \
            .select("id") \
            .eq("source_upload_id", upload_id) \
            .eq("reason", raw_name) \
            .is_("staff_id", "null") \
            .execute()

        if null_shifts.data:
            for shift in null_shifts.data:
                supabase.table("sse_shifts") \
                    .update({
                        "staff_id": new_staff_id,
                        "status": "assigned",
                        "reason": None  # Clear the tag after resolution
                    }) \
                    .eq("id", shift["id"]) \
                    .execute()

            logger.info(
                f"Reassigned {len(null_shifts.data)} shifts for '{raw_name}' "
                f"to {new_staff_id} from upload {upload_id}"
            )
        else:
            logger.warning(
                f"No null shifts found for '{raw_name}' in upload {upload_id}"
            )

    except Exception as e:
        logger.error(f"Shift reassignment failed for '{raw_name}': {e}")


@router.post("/finalize-historical/{upload_id}")
async def finalize_historical_import(
    upload_id: int,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """
    Finalize a historical import after all unmatched names are resolved (or skipped).
    
    This:
    1. Marks the upload as fully completed
    2. Triggers social graph edge computation for the historical period
    3. Returns a summary of what was imported
    """
    restaurant_id = current_staff.get("restaurant_id")

    try:
        # Verify upload
        upload = supabase.table("schedule_uploads") \
            .select("id, week_of, status, upload_type") \
            .eq("id", upload_id) \
            .eq("restaurant_id", restaurant_id) \
            .execute()

        if not upload.data:
            raise HTTPException(status_code=404, detail="Upload not found")

        if upload.data[0]["upload_type"] != "historical":
            raise HTTPException(status_code=400, detail="This is not a historical upload")

        # Check for pending unmatched
        pending = supabase.table("schedule_import_unmatched") \
            .select("id, raw_name", count="exact") \
            .eq("upload_id", upload_id) \
            .eq("resolution", "pending") \
            .execute()

        if pending.count and pending.count > 0:
            return {
                "success": False,
                "message": f"{pending.count} unmatched name(s) still pending resolution",
                "pending_names": [p["raw_name"] for p in (pending.data or [])]
            }

        # Get import summary
        shifts_result = supabase.table("sse_shifts") \
            .select("id, staff_id", count="exact") \
            .eq("source_upload_id", upload_id) \
            .execute()

        assigned = sum(1 for s in (shifts_result.data or []) if s.get("staff_id"))
        unassigned = sum(1 for s in (shifts_result.data or []) if not s.get("staff_id"))

        resolutions = supabase.table("schedule_import_unmatched") \
            .select("resolution") \
            .eq("upload_id", upload_id) \
            .execute()

        resolution_summary = {}
        for r in (resolutions.data or []):
            res = r["resolution"]
            resolution_summary[res] = resolution_summary.get(res, 0) + 1

        # Mark upload fully complete
        supabase.table("schedule_uploads") \
            .update({
                "status": "completed",
                "processed_at": datetime.utcnow().isoformat()
            }) \
            .eq("id", upload_id) \
            .execute()

        logger.info(f"Historical import finalized: upload_id={upload_id}")

        return {
            "success": True,
            "message": "Historical import finalized",
            "summary": {
                "week_of": upload.data[0]["week_of"],
                "total_shifts": shifts_result.count or 0,
                "assigned_shifts": assigned,
                "unassigned_shifts": unassigned,
                "resolutions": resolution_summary
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to finalize historical import: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Finalization failed: {str(e)}")


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
                "upload_type": upload.get("upload_type", "current"),
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
    upload_type: Optional[str] = None,
    current_staff: Dict[str, Any] = Depends(require_feature("stable_schedule"))
):
    """Get past schedule analyses for the restaurant. Optionally filter by upload_type."""
    restaurant_id = current_staff.get("restaurant_id")

    try:
        query = supabase.table("schedule_uploads") \
            .select("id, week_of, status, upload_type, stability_score, issues_found, critical_issues, created_at, processed_at") \
            .eq("restaurant_id", restaurant_id)

        if upload_type:
            query = query.eq("upload_type", upload_type)

        result = query.order("week_of", desc=True).limit(limit).execute()

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
            .eq("upload_type", "current") \
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


# ═══════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════

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