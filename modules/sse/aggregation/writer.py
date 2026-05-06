import logging
from typing import Dict, Any

from core.supabase_client import get_supabase


logger = logging.getLogger(__name__)


def update_staff_signals(
    *,
    organization_id: int,
    staff_id: str,
    target_date: str,
    signals: dict,
) -> Dict[str, Any]:
    """
    Upsert signals for a specific staff member on a specific date.

    Uses upsert to handle both insert and update cases.

    Args:
        organization_id: Restaurant identifier
        staff_id: Staff identifier (text)
        target_date: Date in 'YYYY-MM-DD' string format
        signals: Complete computed signals dictionary to store

    Returns:
        Dictionary describing the outcome of the operation.
    """
    if not staff_id or not staff_id.strip():
        return {
            "organization_id": organization_id,
            "staff_id": staff_id,
            "target_date": target_date,
            "status": "skipped",
            "reason": "missing_staff_id",
        }

    supabase = get_supabase()

    try:
        response = (
            supabase.table("sse_staff_day_metrics")
            .upsert({
                "organization_id": organization_id,
                "staff_id": staff_id,
                "target_date": target_date,
                "signals": signals,
            }, on_conflict="organization_id,staff_id,target_date")
            .execute()
        )

        if response.data:
            logger.debug(
                "Successfully upserted signals for staff %s on %s",
                staff_id,
                target_date,
            )
            return {
                "organization_id": organization_id,
                "staff_id": staff_id,
                "target_date": target_date,
                "status": "upserted",
            }
        else:
            logger.warning(
                "Upsert returned no data for staff %s on %s",
                staff_id,
                target_date,
            )
            return {
                "organization_id": organization_id,
                "staff_id": staff_id,
                "target_date": target_date,
                "status": "unknown",
                "reason": "no_data_returned",
            }

    except Exception as e:
        logger.error(
            "Failed to upsert signals for staff %s (restaurant %s) on %s: %s",
            staff_id,
            organization_id,
            target_date,
            str(e),
            exc_info=True,
        )

        return {
            "organization_id": organization_id,
            "staff_id": staff_id,
            "target_date": target_date,
            "status": "error",
            "error": str(e),
        }