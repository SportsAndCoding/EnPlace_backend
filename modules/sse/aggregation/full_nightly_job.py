import logging
from datetime import date, timedelta
from typing import Dict, Any, List

from core.supabase_client import get_supabase
from modules.sse.aggregation.restaurant_pipeline import run_restaurant_pipeline


logger = logging.getLogger(__name__)


def run_restaurant_job(restaurant_id: int, target_date: date) -> Dict[str, Any]:
    """Fetch all required data for one restaurant and run the full per-staff pipeline."""
    supabase = get_supabase()

    try:
        # Fetch active staff for this restaurant
        staff_response = (
            supabase.table("staff")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .eq("status", "active")
            .execute()
        )
        staff_rows = staff_response.data or []

        if not staff_rows:
            logger.info("No active staff found for restaurant %s on %s", restaurant_id, target_date)
            return {
                "restaurant_id": restaurant_id,
                "staff_count": 0,
                "status": "skipped_no_staff",
            }

        # Build staff_id → row mapping for stable hire lookup
        staff_ids = [row["staff_id"] for row in staff_rows]

        # Fetch all optional per-staff daily data
        checkins_resp = (
            supabase.table("aime_daily_checkins")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .eq("checkin_date", str(target_date))
            .in_("staff_id", staff_ids)
            .execute()
        )
        schedules_resp = (
            supabase.table("staff_schedules")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .eq("schedule_date", str(target_date))
            .in_("staff_id", staff_ids)
            .execute()
        )
        osm_resp = (
            supabase.table("osm_stats_daily")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .eq("stats_date", str(target_date))
            .in_("staff_id", staff_ids)
            .execute()
        )
        swap_resp = (
            supabase.table("swap_stats_daily")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .eq("stats_date", str(target_date))
            .in_("staff_id", staff_ids)
            .execute()
        )
        attendance_resp = (
            supabase.table("attendance_daily")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .eq("attendance_date", str(target_date))
            .in_("staff_id", staff_ids)
            .execute()
        )
        stable_hire_resp = (
            supabase.table("stable_hire_profiles")
            .select("*")
            .in_("staff_id", staff_ids)
            .execute()
        )

        # Index everything by staff_id
        checkins_by_staff = {item["staff_id"]: item for item in (checkins_resp.data or [])}
        schedules_by_staff = {item["staff_id"]: item for item in (schedules_resp.data or [])}
        osm_stats_by_staff = {item["staff_id"]: item for item in (osm_resp.data or [])}
        swap_stats_by_staff = {item["staff_id"]: item for item in (swap_resp.data or [])}
        attendance_by_staff = {item["staff_id"]: item for item in (attendance_resp.data or [])}
        stable_hire_by_staff = {item["staff_id"]: item for item in (stable_hire_resp.data or [])}

        # Run the full per-staff pipeline
        result = run_restaurant_pipeline(
            restaurant_id=restaurant_id,
            target_date=target_date,
            staff_rows=staff_rows,
            checkins_by_staff=checkins_by_staff,
            schedules_by_staff=schedules_by_staff,
            osm_stats_by_staff=osm_stats_by_staff,
            swap_stats_by_staff=swap_stats_by_staff,
            attendance_by_staff=attendance_by_staff,
            stable_hire_by_staff=stable_hire_by_staff,
        )

        return result

    except Exception as e:
        logger.error(
            "Failed to process restaurant %s on %s: %s",
            restaurant_id,
            target_date,
            str(e),
            exc_info=True,
        )
        return {
            "restaurant_id": restaurant_id,
            "staff_count": len(staff_rows) if "staff_rows" in locals() else 0,
            "status": "error",
            "error": str(e),
        }


def run_full_nightly_job(target_date: date | None = None) -> Dict[str, Any]:
    """Top-level orchestrator for the nightly SSE aggregation job."""
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    logger.info("Starting full SSE nightly job for date: %s", target_date)

    supabase = get_supabase()

    try:
        # Fetch all restaurants
        restaurants_resp = supabase.table("restaurants").select("id").execute()
        restaurant_rows = restaurants_resp.data or []
        restaurant_ids = [row["id"] for row in restaurant_rows]

        logger.info("Found %d restaurants to process", len(restaurant_ids))

        results = []

        for restaurant_id in restaurant_ids:
            logger.info("Processing restaurant %s for date %s", restaurant_id, target_date)
            try:
                restaurant_result = run_restaurant_job(restaurant_id, target_date)
                results.append(restaurant_result)
            except Exception as e:
                logger.error(
                    "Unexpected error in restaurant job for %s on %s: %s",
                    restaurant_id,
                    target_date,
                    str(e),
                    exc_info=True,
                )
                results.append({
                    "restaurant_id": restaurant_id,
                    "status": "error",
                    "error": str(e),
                })

        logger.info("Completed full SSE nightly job for %s", target_date)

        return {
            "target_date": target_date.isoformat(),
            "restaurant_count": len(restaurant_ids),
            "results": results,
            "status": "complete",
        }

    except Exception as e:
        logger.error(
            "Critical failure in full SSE nightly job for %s: %s",
            target_date,
            str(e),
            exc_info=True,
        )
        return {
            "target_date": target_date.isoformat() if target_date else None,
            "restaurant_count": 0,
            "results": [],
            "status": "failed",
            "error": str(e),
        }