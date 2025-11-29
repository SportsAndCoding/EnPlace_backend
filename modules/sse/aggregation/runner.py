import logging
from datetime import date, timedelta
from typing import Dict, List, Any

from core.supabase_client import get_supabase
from modules.sse.aggregation.processor import process_restaurant


logger = logging.getLogger(__name__)


def run_sse_aggregation(target_date: date | None = None) -> Dict[str, Any]:
    """
    Nightly orchestration function for the SSE aggregation pipeline.

    This function:
    - Determines the target date (defaults to yesterday)
    - Fetches all restaurants
    - Processes each restaurant by calling process_restaurant()
    - Collects results and logs progress
    - Returns a comprehensive job summary

    This runner is intentionally serial and fault-tolerant — individual restaurant
    failures will not stop the entire job.

    Args:
        target_date: Optional date to process. If None, defaults to yesterday.

    Returns:
        Dict containing job execution summary including per-restaurant results.
    """
    # Determine target date — default to yesterday
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    logger.info("Starting SSE aggregation job for date: %s", target_date)

    supabase = get_supabase()

    try:
        # Fetch all restaurant IDs
        response = supabase.table("restaurants").select("id").execute()

        if not response.data:
            logger.warning("No restaurants found in the database.")
            return {
                "target_date": str(target_date),
                "restaurant_count": 0,
                "results": [],
                "status": "complete",
            }

        restaurant_ids = [row["id"] for row in response.data]
        restaurant_count = len(restaurant_ids)

        logger.info("Found %d restaurants to process", restaurant_count)

        results: List[Dict[str, Any]] = []

        # Process each restaurant sequentially
        for idx, restaurant_id in enumerate(restaurant_ids, start=1):
            logger.info(
                "Processing restaurant %d/%d (ID: %s)",
                idx,
                restaurant_count,
                restaurant_id,
            )

            try:
                result = process_restaurant(restaurant_id, target_date)
                results.append(result)

                if result["status"] == "error":
                    logger.error(
                        "Failed to process restaurant %s: %s",
                        restaurant_id,
                        result.get("error"),
                    )
                else:
                    logger.info(
                        "Successfully processed restaurant %s (%d staff, %d checkins)",
                        restaurant_id,
                        result.get("staff_count", 0),
                        result.get("checkins_found", 0),
                    )

            except Exception as e:
                # This catch-all ensures one bad restaurant never crashes the whole job
                error_result = {
                    "restaurant_id": restaurant_id,
                    "target_date": str(target_date),
                    "status": "error",
                    "error": str(e),
                }
                results.append(error_result)
                logger.error(
                    "Unexpected error processing restaurant %s: %s",
                    restaurant_id,
                    str(e),
                    exc_info=True,
                )

        logger.info(
            "SSE aggregation job completed for %s. Processed %d restaurants.",
            target_date,
            restaurant_count,
        )

        return {
            "target_date": str(target_date),
            "restaurant_count": restaurant_count,
            "results": results,
            "status": "complete",
        }

    except Exception as e:
        logger.critical(
            "Critical failure in SSE aggregation runner: %s",
            str(e),
            exc_info=True,
        )
        return {
            "target_date": str(target_date),
            "restaurant_count": 0,
            "results": [],
            "status": "failed",
            "error": str(e),
        }