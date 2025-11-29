import logging
from datetime import date
from typing import Dict, Any, List, Optional

from modules.sse.aggregation.run_staff_pipeline import run_staff_pipeline


logger = logging.getLogger(__name__)


def run_restaurant_pipeline(
    *,
    restaurant_id: int,
    target_date: date,
    staff_rows: List[Dict[str, Any]],
    checkins_by_staff: Dict[str, Dict[str, Any]],
    schedules_by_staff: Dict[str, Dict[str, Any]],
    osm_stats_by_staff: Dict[str, Dict[str, Any]],
    swap_stats_by_staff: Dict[str, Dict[str, Any]],
    attendance_by_staff: Dict[str, Dict[str, Any]],
    stable_hire_by_staff: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Run the complete SSE pipeline for all active staff in a single restaurant on a single date.

    This function orchestrates per-staff processing using pre-fetched data.
    It never raises exceptions — all errors are caught and included in the results.
    """
    logger.info(
        "Starting SSE staff pipeline for restaurant %s on %s (%d staff)",
        restaurant_id,
        target_date,
        len(staff_rows),
    )

    staff_results: List[Dict[str, Any]] = []

    for staff_row in staff_rows:
        staff_id = staff_row.get("staff_id") if isinstance(staff_row, dict) else None

        try:
            result = run_staff_pipeline(
                staff_row=staff_row,
                checkin=checkins_by_staff.get(staff_id),
                schedule_row=schedules_by_staff.get(staff_id),
                osm_stats=osm_stats_by_staff.get(staff_id),
                swap_stats=swap_stats_by_staff.get(staff_id),
                attendance_row=attendance_by_staff.get(staff_id),
                stable_hire_profile=stable_hire_by_staff.get(staff_id),
                restaurant_id=restaurant_id,
                target_date=target_date,
            )
            staff_results.append(result)

        except Exception as e:
            logger.error(
                "Unexpected error processing staff %s in restaurant %s on %s: %s",
                staff_id,
                restaurant_id,
                target_date,
                str(e),
                exc_info=True,
            )
            staff_results.append(
                {
                    "restaurant_id": restaurant_id,
                    "staff_id": staff_id,
                    "target_date": target_date.isoformat(),
                    "status": "error",
                    "error": str(e),
                }
            )

    logger.info(
        "Completed SSE staff pipeline for restaurant %s on %s",
        restaurant_id,
        target_date,
    )

    return {
        "restaurant_id": restaurant_id,
        "target_date": target_date.isoformat(),
        "staff_count": len(staff_rows),
        "results": staff_results,
        "status": "complete",
    }