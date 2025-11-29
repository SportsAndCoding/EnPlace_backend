import json
import logging
from datetime import date
from typing import Dict, Any

from core.supabase_client import get_supabase


logger = logging.getLogger(__name__)


def process_restaurant(restaurant_id: int, target_date: date) -> Dict[str, Any]:
    """
    Process a single restaurant for a specific target date as part of the nightly SSE pipeline.

    This function:
    - Fetches all active staff for the restaurant
    - Fetches their daily check-ins (if any) for the target date
    - Prepares per-staff data containers
    - Inserts a stub row into sse_staff_day_metrics for each staff member
    - Returns a summary of the operation

    No signal calculation is performed yet — this is the data-loading scaffold.

    Args:
        restaurant_id: The ID of the restaurant to process
        target_date: The date (YYYY-MM-DD) being processed

    Returns:
        Dict containing processing summary or error information
    """
    supabase = get_supabase()

    try:
        # Step 1: Fetch all active staff for this restaurant
        staff_response = (
            supabase.table("staff")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .eq("status", "active")
            .execute()
        )

        if not staff_response.data:
            logger.info(
                "No active staff found for restaurant %s on %s",
                restaurant_id,
                target_date,
            )
            return {
                "restaurant_id": restaurant_id,
                "target_date": str(target_date),
                "staff_count": 0,
                "checkins_found": 0,
                "status": "success",
            }

        staff_list = staff_response.data

        # Step 2: Fetch all check-ins for this restaurant and target date
        checkins_response = (
            supabase.table("aime_daily_checkins")
            .select("*")
            .eq("restaurant_id", restaurant_id)
            .eq("checkin_date", target_date)
            .execute()
        )

        checkins_data = checkins_response.data or []
        checkins_by_staff = {c["staff_id"]: c for c in checkins_data}

        # Step 3: Build per-staff data structure and prepare SSE inserts
        sse_inserts = []

        for staff in staff_list:
            staff_id = staff["staff_id"]
            checkin = checkins_by_staff.get(staff_id)

            # Prepare the stub row for sse_staff_day_metrics
            sse_row = {
                "restaurant_id": restaurant_id,
                "staff_id": staff_id,
                "target_date": str(target_date),
                "signals": json.dumps({}),  # Empty JSON object — signals added later
            }
            sse_inserts.append(sse_row)

        # Step 4: Insert all stub SSE records in a single batch
        if sse_inserts:
            insert_response = (
                supabase.table("sse_staff_day_metrics")
                .insert(sse_inserts)
                .execute()
            )

            # Log warning if insert failed but don't crash the job
            if insert_response.data is None:
                logger.warning(
                    "Failed to insert SSE stubs for restaurant %s on %s: %s",
                    restaurant_id,
                    target_date,
                    insert_response,
                )

        # Step 5: Return summary
        return {
            "restaurant_id": restaurant_id,
            "target_date": str(target_date),
            "staff_count": len(staff_list),
            "checkins_found": len(checkins_data),
            "status": "success",
        }

    except Exception as e:
        logger.error(
            "Error processing restaurant %s for date %s: %s",
            restaurant_id,
            target_date,
            str(e),
            exc_info=True,
        )
        return {
            "restaurant_id": restaurant_id,
            "target_date": str(target_date),
            "status": "error",
            "error": str(e),
        }