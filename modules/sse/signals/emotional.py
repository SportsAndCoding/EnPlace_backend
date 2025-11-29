from typing import Optional, Dict, Any


def compute_mood(checkin: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the raw mood rating (1–10) from the checkin, or None if no checkin."""
    if checkin is None:
        return None
    return checkin.get("mood_rating")


def compute_energy(checkin: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the raw energy level (1–10) from the checkin, or None if no checkin."""
    if checkin is None:
        return None
    return checkin.get("energy_level")


def compute_stress(checkin: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the raw stress level (1–10) from the checkin, or None if no checkin."""
    if checkin is None:
        return None
    return checkin.get("stress_level")


def compute_workload_satisfaction(checkin: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the raw workload satisfaction (1–10) from the checkin, or None if no checkin."""
    if checkin is None:
        return None
    return checkin.get("workload_satisfaction")


def felt_safe_flag(checkin: Optional[Dict[str, Any]]) -> None:
    """Placeholder for future 'felt safe' flag. Currently always None."""
    return None


def felt_fair_flag(checkin: Optional[Dict[str, Any]]) -> None:
    """Placeholder for future 'felt fair' flag. Currently always None."""
    return None


def felt_supported_flag(checkin: Optional[Dict[str, Any]]) -> None:
    """Placeholder for future 'felt supported' flag. Currently always None."""
    return None


def compute_emotional_signals(staff_data: Dict[str, Any]) -> Dict[str, Optional[int]]:
    """
    Compute all emotional signals for a single staff member on a single day.

    This function extracts raw emotional metrics directly from the daily check-in
    (if present) and returns them unchanged. All transformation and scoring happens
    in later layers.

    Args:
        staff_data: Dictionary containing:
            - "staff_row": full row from staff table
            - "checkin": daily check-in row or None

    Returns:
        Dictionary with raw emotional signal values (or None when no check-in).
    """
    checkin = staff_data.get("checkin")

    return {
        "mood_score": compute_mood(checkin),
        "energy_score": compute_energy(checkin),
        "stress_score": compute_stress(checkin),
        "workload_score": compute_workload_satisfaction(checkin),
        "felt_safe_flag": felt_safe_flag(checkin),
        "felt_fair_flag": felt_fair_flag(checkin),
        "felt_supported_flag": felt_supported_flag(checkin),
    }