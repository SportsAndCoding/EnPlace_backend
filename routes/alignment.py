from fastapi import APIRouter, Depends, HTTPException, Query
from services.auth_service import verify_jwt_token as get_current_user
from services.alignment_service import AlignmentService
from database.supabase_client import supabase
from datetime import datetime, timezone
import os
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alignment", tags=["alignment"])


@router.get("")
async def get_alignment(
    organization_id: int,
    days: int = Query(default=7, ge=1, le=30),
    current_user: dict = Depends(get_current_user)
):
    """
    Get Staff-Manager Alignment scores.
    """
    if current_user['organization_id'] != organization_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    service = AlignmentService()
    try:
        alignment_data = await service.get_alignment_data(
            organization_id=organization_id,
            days=days
        )
        return {"success": True, **alignment_data}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to calculate alignment: {str(e)}"
        )


@router.post("/synthetic/recalculate")
async def recalculate_synthetic_sma(
    api_key: str = Query(None, description="API key for scheduled job authentication")
):
    """
    Nightly job to recalculate SMA scores for all synthetic restaurants.
    POST /api/alignment/synthetic/recalculate?api_key=enplace-monitor-2025
    """
    expected_key = os.environ.get("MONITORING_JOB_KEY", "enplace-monitor-2025")
    if api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    try:
        results = await _recalculate_all_synthetic_sma()
        return {
            "success": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": results
        }
    except Exception as e:
        logger.error(f"Synthetic SMA recalculation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _recalculate_all_synthetic_sma() -> dict:
    """Compute and store SMA scores for all synthetic restaurants."""
    
    max_day_result = supabase.table("synthetic_daily_emotions") \
        .select("day_index") \
        .order("day_index", desc=True) \
        .limit(1) \
        .execute()

    if not max_day_result.data:
        return {"error": "No emotion data found", "updated": 0}

    max_day = max_day_result.data[0]["day_index"]
    recent_start = max_day - 7

    # Fetch staff emotions with pagination
    all_emotions = []
    offset = 0
    batch_size = 1000

    while True:
        emotions_result = supabase.table("synthetic_daily_emotions") \
            .select("organization_id, day_index, mood_emoji") \
            .gte("day_index", recent_start) \
            .range(offset, offset + batch_size - 1) \
            .execute()
        if not emotions_result.data:
            break
        all_emotions.extend(emotions_result.data)
        if len(emotions_result.data) < batch_size:
            break
        offset += batch_size

    if not all_emotions:
        return {"error": "No recent emotion data", "updated": 0}

    manager_result = supabase.table("synthetic_manager_logs") \
        .select("organization_id, day_index, overall_rating") \
        .gte("day_index", recent_start) \
        .execute()

    if not manager_result.data:
        return {"error": "No manager logs found", "updated": 0}

    # Aggregate staff mood by restaurant+day
    staff_by_day = {}
    for row in all_emotions:
        key = (row["organization_id"], row["day_index"])
        if key not in staff_by_day:
            staff_by_day[key] = []
        if row.get("mood_emoji") is not None:
            staff_by_day[key].append(row["mood_emoji"])

    manager_by_day = {
        (row["organization_id"], row["day_index"]): row.get("overall_rating")
        for row in manager_result.data
    }

    # Calculate SMA per restaurant
    restaurant_alignments = {}
    for (rid, day), moods in staff_by_day.items():
        if not moods:
            continue
        staff_avg = sum(moods) / len(moods)
        manager_rating = manager_by_day.get((rid, day))
        if manager_rating is None:
            continue
        if rid not in restaurant_alignments:
            restaurant_alignments[rid] = {"aligned": 0, "total": 0}
        restaurant_alignments[rid]["total"] += 1
        if abs(staff_avg - manager_rating) <= 1.0:
            restaurant_alignments[rid]["aligned"] += 1

    # Compute scores
    scores = {}
    for rid, data in restaurant_alignments.items():
        if data["total"] > 0:
            scores[rid] = int(round((data["aligned"] / data["total"]) * 100))

    # Update database
    updated = 0
    errors = 0
    for organization_id, sma_score in scores.items():
        try:
            supabase.table("synthetic_organizations") \
                .update({"sma_score": sma_score}) \
                .eq("organization_id", organization_id) \
                .execute()
            updated += 1
        except Exception as e:
            logger.error(f"Error updating restaurant {organization_id}: {e}")
            errors += 1

    score_values = list(scores.values())
    return {
        "restaurants_processed": len(scores),
        "updated": updated,
        "errors": errors,
        "day_range": f"{recent_start}-{max_day}",
        "score_stats": {
            "min": min(score_values) if score_values else None,
            "max": max(score_values) if score_values else None,
            "avg": round(sum(score_values) / len(score_values), 1) if score_values else None
        }
    }