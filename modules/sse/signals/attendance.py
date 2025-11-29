from typing import Optional, Dict, Any


def extract_late_arrival(attendance_row: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Extract late arrival flag as bool, or None if missing or invalid."""
    if attendance_row is None:
        return None
    value = attendance_row.get("late_arrival")
    return value if isinstance(value, bool) else None


def extract_late_minutes(attendance_row: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract number of minutes late as non-negative int, or None if invalid."""
    if attendance_row is None:
        return None
    value = attendance_row.get("late_minutes")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def extract_early_departure(attendance_row: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Extract early departure flag as bool, or None if missing or invalid."""
    if attendance_row is None:
        return None
    value = attendance_row.get("early_departure")
    return value if isinstance(value, bool) else None


def extract_call_out(attendance_row: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Extract call-out (notified absence) flag as bool, or None if invalid."""
    if attendance_row is None:
        return None
    value = attendance_row.get("call_out")
    return value if isinstance(value, bool) else None


def extract_call_out_reason(attendance_row: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract call-out reason category as string, or None if missing or empty."""
    if attendance_row is None:
        return None
    value = attendance_row.get("call_out_reason")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def extract_no_call_no_show(attendance_row: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Extract no-call no-show flag as bool, or None if invalid."""
    if attendance_row is None:
        return None
    value = attendance_row.get("no_call_no_show")
    return value if isinstance(value, bool) else None


def extract_shift_worked(attendance_row: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Extract whether the shift was worked at all, or None if invalid."""
    if attendance_row is None:
        return None
    value = attendance_row.get("shift_worked")
    return value if isinstance(value, bool) else None


def compute_attendance_signals(staff_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract raw attendance signals for a staff member on a given day.

    This module is intentionally defensive and future-proof. In early versions of En Place,
    real attendance data is not available — this module will return all None values
    until proper clock-in/clock-out integration is added.

    Args:
        staff_data: Dictionary that may contain "attendance_row"

    Returns:
        Dictionary of raw attendance signals with explicit presence flag.
    """
    attendance_row = staff_data.get("attendance_row")

    if attendance_row is None:
        return {
            "late_arrival": None,
            "late_minutes": None,
            "early_departure": None,
            "call_out": None,
            "call_out_reason": None,
            "no_call_no_show": None,
            "shift_worked": None,
            "attendance_data_present": False,
        }

    return {
        "late_arrival": extract_late_arrival(attendance_row),
        "late_minutes": extract_late_minutes(attendance_row),
        "early_departure": extract_early_departure(attendance_row),
        "call_out": extract_call_out(attendance_row),
        "call_out_reason": extract_call_out_reason(attendance_row),
        "no_call_no_show": extract_no_call_no_show(attendance_row),
        "shift_worked": extract_shift_worked(attendance_row),
        "attendance_data_present": True,
    }