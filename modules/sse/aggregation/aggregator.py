import logging
from datetime import date
from typing import Dict, Any

from core.supabase_client import get_supabase

from modules.sse.signals.emotional import compute_emotional_signals
from modules.sse.signals.tenure import compute_tenure_signals
from modules.sse.signals.schedule import compute_schedule_signals
from modules.sse.signals.osm import compute_osm_signals
from modules.sse.signals.swap import compute_swap_signals
from modules.sse.signals.attendance import compute_attendance_signals
from modules.sse.signals.stable_hire import compute_stable_hire_signals


logger = logging.getLogger(__name__)


def aggregate_signals(staff_data: Dict[str, Any], target_date: date) -> Dict[str, Any]:
    """
    Run all individual signal modules on a unified staff-day input and merge their outputs.

    This function orchestrates the raw signal extraction layer of the SSE engine.
    It calls every signal module safely, with tenure computed first for clarity and future use.

    Args:
        staff_data: Unified staff-day input from builder.build_staff_day_input()
        target_date: The date being processed (datetime.date)

    Returns:
        Complete merged signals dictionary ready for storage in sse_staff_day_metrics.
    """
    staff_id = staff_data.get("staff_id")
    restaurant_id = staff_data.get("restaurant_id")

    result: Dict[str, Any] = {
        "staff_id": staff_id,
        "restaurant_id": restaurant_id,
        "target_date": target_date.isoformat(),
        "is_valid": staff_id is not None and staff_id != "unknown",
    }

    # Compute tenure first — it's foundational and useful for debugging
    try:
        tenure_signals = compute_tenure_signals(staff_data, target_date)
        if isinstance(tenure_signals, dict):
            result.update(tenure_signals)
    except Exception as e:
        logger.error(
            "Tenure signal computation failed for staff %s on %s: %s",
            staff_id,
            target_date,
            str(e),
            exc_info=True,
        )

    # All other signal modules (no date dependency)
    signal_functions = [
        compute_emotional_signals,
        compute_schedule_signals,
        compute_osm_signals,
        compute_swap_signals,
        compute_attendance_signals,
        compute_stable_hire_signals,
    ]

    for func in signal_functions:
        try:
            signals = func(staff_data)
            if isinstance(signals, dict):
                result.update(signals)
        except Exception as e:
            logger.error(
                "Signal module %s failed for staff %s on %s: %s",
                func.__name__,
                staff_id,
                target_date,
                str(e),
                exc_info=True,
            )

    return result