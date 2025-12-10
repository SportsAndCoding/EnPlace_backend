from typing import Optional, Dict, Any


def compute_mood(checkin: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the raw mood rating (1-5) from the checkin, or None if no checkin."""
    if checkin is None:
        return None
    return checkin.get("mood_emoji")


def felt_safe_flag(checkin: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Return felt_safe boolean from checkin, or None if no checkin."""
    if checkin is None:
        return None
    return checkin.get("felt_safe")


def felt_fair_flag(checkin: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Return felt_fair boolean from checkin, or None if no checkin."""
    if checkin is None:
        return None
    return checkin.get("felt_fair")


def felt_respected_flag(checkin: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Return felt_respected boolean from checkin, or None if no checkin."""
    if checkin is None:
        return None
    return checkin.get("felt_respected")


def compute_emotional_signals(staff_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute all emotional signals for a single staff member on a single day.

    Extracts raw emotional metrics directly from the daily check-in (if present).
    All transformation and scoring happens in later layers.

    Args:
        staff_data: Dictionary containing:
            - "staff_row": full row from staff table
            - "checkin": daily check-in row or None

    Returns:
        Dictionary with raw emotional signal values (or None when no check-in).
    """
    checkin = staff_data.get("checkin")

    has_checkin = checkin is not None

    return {
        "mood_score": compute_mood(checkin),
        "felt_safe": felt_safe_flag(checkin),
        "felt_fair": felt_fair_flag(checkin),
        "felt_respected": felt_respected_flag(checkin),
        "checkin_submitted": has_checkin,
    }