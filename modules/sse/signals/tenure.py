from datetime import date, datetime
from typing import Optional, Dict, Any


def compute_days_employed(hire_date_input: Any, target_date: date) -> Optional[int]:
    """
    Calculate the number of days between hire_date and target_date (inclusive of hire day).

    Returns None if hire_date is missing, malformed, or in the future.
    """
    if hire_date_input is None:
        return None

    # Accept both date objects and ISO-format strings
    if isinstance(hire_date_input, date):
        hire_date = hire_date_input
    elif isinstance(hire_date_input, str):
        try:
            hire_date = datetime.strptime(hire_date_input, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    else:
        return None

    if hire_date > target_date:
        return None

    return (target_date - hire_date).days


def get_tenure_bucket(days: Optional[int]) -> Optional[str]:
    """
    Map days employed to a tenure bucket using exact business-defined ranges.

    Returns bucket name as string or None if days is None or negative.
    """
    if days is None or days < 0:
        return None

    if days <= 14:
        return "onboarding"
    if days <= 30:
        return "reality_check"
    if days <= 60:
        return "sink_or_swim"
    if days <= 90:
        return "90_day_cliff"
    if days <= 180:
        return "stabilizing"
    if days <= 365:
        return "established"
    return "veteran"


def bucket_flags(bucket: Optional[str]) -> Dict[str, bool]:
    """
    Convert a tenure bucket into a set of one-hot boolean flags.

    Exactly one flag is True when bucket is valid; all False when bucket is None.
    """
    return {
        "is_onboarding": bucket == "onboarding",
        "is_reality_check": bucket == "reality_check",
        "is_sink_or_swim": bucket == "sink_or_swim",
        "is_90_day_cliff": bucket == "90_day_cliff",
        "is_stabilizing": bucket == "stabilizing",
        "is_established": bucket == "established",
        "is_veteran": bucket == "veteran",
    }


def compute_tenure_signals(staff_data: Dict[str, Any], target_date: date) -> Dict[str, Any]:
    """
    Compute all tenure-based signals for a staff member on the given target date.

    Args:
        staff_data: Dictionary containing at least "staff_row" with optional "hire_date"
        target_date: The date being evaluated (as a datetime.date)

    Returns:
        Dictionary with days employed, tenure bucket, and boolean bucket flags.
    """
    staff_row = staff_data.get("staff_row") or {}
    hire_date_input = staff_row.get("hire_date")

    days_employed = compute_days_employed(hire_date_input, target_date)
    bucket = get_tenure_bucket(days_employed)
    flags = bucket_flags(bucket)

    return {
        "days_employed": days_employed,
        "tenure_bucket": bucket,
        **flags,
    }