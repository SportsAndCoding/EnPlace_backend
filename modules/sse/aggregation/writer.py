import logging
from typing import Dict, Any

from core.supabase_client import get_supabase


logger = logging.getLogger(__name__)


def update_staff_signals(
    *,
    restaurant_id: int,
    staff_id: str,
    target_date: str,
    signals: dict,
) -> Dict[str, Any]:
    """
    Update the signals JSONB column for a specific staff member on a specific date.

    This function performs a safe update on the existing stub row in sse_staff_day_metrics.
    All errors are caught and returned in the result dictionary to preserve nightly job stability.

    Args:
        restaurant_id: Restaurant identifier
        staff_id: Staff identifier (text)
        target_date: Date in 'YYYY-MM-DD' string format
        signals: Complete computed signals dictionary to store

    Returns:
        Dictionary describing the outcome of the update operation.
    """
    if not staff_id or not staff_id.strip():
        return {
            "restaurant_id": restaurant_id,
            "staff_id": staff_id,
            "target_date": target_date,
            "status": "skipped",
            "reason": "missing_staff_id",
        }

    supabase = get_supabase()

    try:
        response = (
            supabase.table("sse_staff_day_metrics")
            .update({"signals": signals})
            .eq("restaurant_id", restaurant_id)
            .eq("staff_id", staff_id)
            .eq("target_date", target_date)
            .execute()
        )

        # Check whether any row was actually matched and updated
        if not response.data:
            # No rows matched the filter — this usually means the stub row is missing
            logger.warning(
                "Signals update matched zero rows for staff %s (restaurant %s) on %s — "
                "stub row may not exist or identifiers mismatched",
                staff_id,
                restaurant_id,
                target_date,
            )
            return {
                "restaurant_id": restaurant_id,
                "staff_id": staff_id,
                "target_date": target_date,
                "status": "no_row_updated",
                "reason": "zero_rows_matched",
            }

        logger.debug(
            "Successfully updated signals for staff %s on %s",
            staff_id,
            target_date,
        )

        return {
            "restaurant_id": restaurant_id,
            "staff_id": staff_id,
            "target_date": target_date,
            "status": "updated",
        }

    except Exception as e:
        logger.error(
            "Failed to update signals for staff %s (restaurant %s) on %s: %s",
            staff_id,
            restaurant_id,
            target_date,
            str(e),
            exc_info=True,
        )

        return {
            "restaurant_id": restaurant_id,
            "staff_id": staff_id,
            "target_date": target_date,
            "status": "error",
            "error": str(e),
        }