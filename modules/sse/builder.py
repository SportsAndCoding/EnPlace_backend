from datetime import date
from typing import Dict, Any, Optional


def _validate_staff_row(staff_row: Dict[str, Any]) -> Optional[str]:
    """
    Validate that the staff_row contains the minimum required fields.
    Returns staff_id if valid, None otherwise.
    """
    if not isinstance(staff_row, dict):
        return None
    return staff_row.get("staff_id") if isinstance(staff_row.get("staff_id"), str) else None


def build_staff_day_input(
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
    Assemble a canonical staff-day input object for the SSE signal pipeline.

    This function combines all raw data sources for a single staff member on a single day
    into a standardized structure. It is fully defensive — missing or malformed inputs
    are normalized to None rather than raising errors.

    Args:
        staff_row: Full row from the staff table (required)
        checkin: Daily check-in row from aime_daily_checkins, or None
        schedule_row: Schedule data from Stable Schedule Builder, or None
        osm_stats: Open Shift Marketplace statistics, or None
        swap_stats: Shift Swap module statistics, or None
        attendance_row: Attendance/clock-in data (future), or None
        stable_hire_profile: Pre-hire profile from Stable Hire, or None
        restaurant_id: Restaurant ID (used to ensure consistency)
        target_date: The date being processed

    Returns:
        Dictionary in the canonical staff-day input format expected by all signal modules.
    """
    # Basic validation — if staff_row is invalid, we still return a partial object
    # to avoid crashing the entire pipeline
    staff_id = _validate_staff_row(staff_row)

    if staff_id is None:
        # Even with invalid staff_row, we return a safe structure
        # This allows downstream logging without breaking the job
        staff_row = {}

    # Ensure restaurant_id is correct (override if necessary)
    if isinstance(staff_row, dict):
        staff_row = {**staff_row, "restaurant_id": restaurant_id}

    return {
        "restaurant_id": int(restaurant_id),
        "staff_id": staff_id,   # Never create fake IDs
        "target_date": target_date.isoformat(),

        "staff_row": staff_row,
        "checkin": checkin,
        "schedule_row": schedule_row,
        "osm_stats": osm_stats,
        "swap_stats": swap_stats,
        "attendance_row": attendance_row,
        "stable_hire_profile": stable_hire_profile,

        "invalid_staff_row": staff_id is None,
    }