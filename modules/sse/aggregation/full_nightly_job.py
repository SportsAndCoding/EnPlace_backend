import logging
from datetime import date, timedelta
from typing import Dict, Any, List
from collections import defaultdict

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
            .eq("status", "Active")
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

        staff_ids = [row["staff_id"] for row in staff_rows]

        # Date calculations
        yesterday = target_date - timedelta(days=1)
        week_start = target_date - timedelta(days=target_date.weekday())
        week_end = week_start + timedelta(days=6)

        # Fetch check-ins for target date
        checkins_resp = (
            supabase.table("sse_daily_checkins")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .eq("checkin_date", str(target_date))
            .in_("staff_id", staff_ids)
            .execute()
        )

        # Fetch shifts for the week (covers today, yesterday, and weekly totals)
        shifts_resp = (
            supabase.table("sse_shifts")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .gte("shift_date", str(week_start))
            .lte("shift_date", str(week_end))
            .in_("staff_id", staff_ids)
            .execute()
        )

        # Fetch stable hire profiles (candidates who were hired)
        stable_hire_resp = (
            supabase.table("hiring_candidates")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .in_("hired_staff_id", staff_ids)
            .execute()
        )

        # OSM, Swap, Attendance - not yet implemented, will return empty
        osm_resp_data = []
        swap_resp_data = []
        attendance_resp_data = []

        # Index check-ins by staff_id
        checkins_by_staff = {item["staff_id"]: item for item in (checkins_resp.data or [])}

        # Index shifts by staff_id and date
        shifts_by_staff_date = defaultdict(lambda: defaultdict(list))
        for shift in (shifts_resp.data or []):
            sid = shift.get("staff_id")
            sdate = shift.get("shift_date")
            if sid and sdate:
                shifts_by_staff_date[sid][sdate].append(shift)

        # Build shifts_today, shifts_yesterday, shifts_week per staff
        def get_shifts_for_staff(staff_id: str) -> Dict[str, List]:
            staff_shifts = shifts_by_staff_date.get(staff_id, {})
            return {
                "today": staff_shifts.get(str(target_date), []),
                "yesterday": staff_shifts.get(str(yesterday), []),
                "week": [s for date_shifts in staff_shifts.values() for s in date_shifts],
            }

        # Index stable hire profiles by hired_staff_id
        stable_hire_by_staff = {
            item["hired_staff_id"]: item 
            for item in (stable_hire_resp.data or []) 
            if item.get("hired_staff_id")
        }

        # OSM, Swap, Attendance by staff (empty for now)
        osm_stats_by_staff = {}
        swap_stats_by_staff = {}
        attendance_by_staff = {}

        # Run the full per-staff pipeline
        result = run_restaurant_pipeline(
            restaurant_id=restaurant_id,
            target_date=target_date,
            staff_rows=staff_rows,
            checkins_by_staff=checkins_by_staff,
            get_shifts_for_staff=get_shifts_for_staff,
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