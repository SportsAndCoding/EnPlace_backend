import logging
from datetime import date
from typing import Dict, Any, Optional

from modules.sse.builder import build_staff_day_input
from modules.sse.aggregation.aggregator import aggregate_signals
from modules.sse.aggregation.writer import update_staff_signals


logger = logging.getLogger(__name__)


def run_staff_pipeline(
    *,
    staff_row: Dict[str, Any],
    checkin: Optional[Dict[str, Any]],
    schedule_row: Optional[Dict[str, Any]],
    osm_stats: Optional[Dict[str, Any]],
    swap_stats: Optional[Dict[str, Any]],
    attendance_row: Optional[Dict[str, Any]],
    stable_hire_profile: Optional[Dict[str, Any]],
    restaurant_id: int,
    target_date: date,
) -> Dict[str, Any]:
    """
    Execute the complete SSE pipeline for a single staff member on a single date.

    Phases:
    1. Build unified staff-day input object
    2. Compute all raw signals via aggregator
    3. Persist final signals to sse_staff_day_metrics

    This function is fully fault-tolerant and never raises exceptions.
    All errors are logged and returned in the result dictionary.
    """
    try:
        # Phase 1: Build canonical staff-day input
        staff_input = build_staff_day_input(
            staff_row=staff_row,
            checkin=checkin,
            schedule_row=schedule_row,
            osm_stats=osm_stats,
            swap_stats=swap_stats,
            attendance_row=attendance_row,
            stable_hire_profile=stable_hire_profile,
            restaurant_id=restaurant_id,
            target_date=target_date,
        )

        staff_id = staff_input.get("staff_id")

        if staff_id is None:
            logger.warning(
                "Skipping SSE pipeline for restaurant %s on %s due to missing/invalid staff_id",
                restaurant_id,
                target_date,
            )
            return {
                "restaurant_id": restaurant_id,
                "staff_id": None,
                "target_date": target_date.isoformat(),
                "status": "skipped_invalid_staff",
            }

        # Phase 2: Aggregate all raw signals
        signals = aggregate_signals(staff_input, target_date)

        # Phase 3: Write signals to Supabase
        write_result = update_staff_signals(
            restaurant_id=restaurant_id,
            staff_id=staff_id,
            target_date=signals["target_date"],
            signals=signals,
        )

        # Final result
        return {
            "restaurant_id": restaurant_id,
            "staff_id": staff_id,
            "target_date": target_date.isoformat(),
            "status": write_result.get("status", "unknown"),
            "write_result": write_result,
        }

    except Exception as e:
        logger.error(
            "Unexpected error in SSE staff pipeline for restaurant %s, staff %s, date %s: %s",
            restaurant_id,
            staff_row.get("staff_id") if isinstance(staff_row, dict) else "unknown",
            target_date,
            str(e),
            exc_info=True,
        )
        return {
            "restaurant_id": restaurant_id,
            "staff_id": staff_row.get("staff_id") if isinstance(staff_row, dict) else None,
            "target_date": target_date.isoformat(),
            "status": "error",
            "error": str(e),
        }