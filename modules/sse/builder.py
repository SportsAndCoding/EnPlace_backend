from datetime import date
from typing import Dict, Any, Optional, List


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
    shifts_today: Optional[List[Dict[str, Any]]],
    shifts_yesterday: Optional[List[Dict[str, Any]]],
    shifts_week: Optional[List[Dict[str, Any]]],
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
        checkin: Daily check-in row from sse_daily_checkins, or None
        shifts_today: List of shifts for target_date from sse_shifts, or None
        shifts_yesterday: List of shifts for day before (clopen detection), or None
        shifts_week: List of shifts for the week (weekly hours), or None
        osm_stats: Open Shift Marketplace statistics, or None
        swap_stats: Shift Swap module statistics, or None
        attendance_row: Attendance/clock-in data (future), or None
        stable_hire_profile: Pre-hire profile from hiring_candidates, or None
        restaurant_id: Restaurant ID (used to ensure consistency)
        target_date: The date being processed

    Returns:
        Dictionary in the canonical staff-day input format expected by all signal modules.
    """
    staff_id = _validate_staff_row(staff_row)

    if staff_id is None:
        staff_row = {}

    if isinstance(staff_row, dict):
        staff_row = {**staff_row, "restaurant_id": restaurant_id}

    return {
        "restaurant_id": int(restaurant_id),
        "staff_id": staff_id,
        "target_date": target_date.isoformat(),

        # Core data
        "staff_row": staff_row,
        "checkin": checkin,
        
        # Schedule data (from sse_shifts)
        "shifts_today": shifts_today or [],
        "shifts_yesterday": shifts_yesterday or [],
        "shifts_week": shifts_week or [],
        
        # Optional module data
        "osm_stats": osm_stats,
        "swap_stats": swap_stats,
        "attendance_row": attendance_row,
        "stable_hire_profile": stable_hire_profile,

        "invalid_staff_row": staff_id is None,
    }