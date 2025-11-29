from typing import Optional, Dict, Any


def extract_shift_type(schedule_row: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the raw shift_type string from schedule_row, or None if missing."""
    if schedule_row is None:
        return None
    return schedule_row.get("shift_type")


def extract_hours_scheduled_week(schedule_row: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return weekly scheduled hours as int, or None if missing or invalid."""
    if schedule_row is None:
        return None
    value = schedule_row.get("hours_scheduled_week")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_hours_scheduled_month(schedule_row: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return monthly scheduled hours as int, or None if missing or invalid."""
    if schedule_row is None:
        return None
    value = schedule_row.get("hours_scheduled_month")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_overtime_flag(schedule_row: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Return overtime flag as bool, or None if missing."""
    if schedule_row is None:
        return None
    return schedule_row.get("overtime_flag") if isinstance(schedule_row.get("overtime_flag"), bool) else None


def extract_clopen_flag(schedule_row: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Return clopen flag as bool, or None if missing."""
    if schedule_row is None:
        return None
    return schedule_row.get("clopen_flag") if isinstance(schedule_row.get("clopen_flag"), bool) else None


def extract_consecutive_days(schedule_row: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return number of consecutive working days as int, or None if missing or invalid."""
    if schedule_row is None:
        return None
    value = schedule_row.get("consecutive_days")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def compute_schedule_signals(staff_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute all schedule-related signals for a staff member on a given day.

    This function is fully defensive: if the restaurant does not use Stable Schedule Builder
    (SSB) or no schedule data exists for the day, all signals gracefully return None.

    Args:
        staff_data: Dictionary containing optional "schedule_row" and other keys.

    Returns:
        Dictionary of schedule signals with explicit presence flag.
    """
    schedule_row = staff_data.get("schedule_row")

    if schedule_row is None:
        return {
            "shift_type": None,
            "hours_scheduled_week": None,
            "hours_scheduled_month": None,
            "overtime_flag": None,
            "clopen_flag": None,
            "consecutive_days": None,
            "schedule_data_present": False,
        }

    return {
        "shift_type": extract_shift_type(schedule_row),
        "hours_scheduled_week": extract_hours_scheduled_week(schedule_row),
        "hours_scheduled_month": extract_hours_scheduled_month(schedule_row),
        "overtime_flag": extract_overtime_flag(schedule_row),
        "clopen_flag": extract_clopen_flag(schedule_row),
        "consecutive_days": extract_consecutive_days(schedule_row),
        "schedule_data_present": True,
    }