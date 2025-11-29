from typing import Optional, Dict, Any, List


def extract_risk_score_pre_hire(profile: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract pre-hire risk score (0–100 integer), or None if missing/invalid."""
    if profile is None:
        return None
    value = profile.get("risk_score_pre_hire")
    if isinstance(value, int) and 0 <= value <= 100:
        return value
    return None


def extract_work_style(profile: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract work style category string, normalize, or return None if invalid."""
    if profile is None:
        return None
    value = profile.get("work_style")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def extract_reliability_assessment(profile: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract reliability assessment (1–5 integer), or None if out of range."""
    if profile is None:
        return None
    value = profile.get("reliability_assessment")
    if isinstance(value, int) and 1 <= value <= 5:
        return value
    return None


def extract_social_comfort_level(profile: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract social comfort level (1–5 integer), or None if out of range."""
    if profile is None:
        return None
    value = profile.get("social_comfort_level")
    if isinstance(value, int) and 1 <= value <= 5:
        return value
    return None


def extract_interview_red_flags(profile: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Extract list of interview red flags as list[str], or None if not present/valid."""
    if profile is None:
        return None
    value = profile.get("interview_red_flags")
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        cleaned = [item.strip() for item in value if item and isinstance(item, str)]
        return cleaned if cleaned else None
    return None


def extract_strength_indicators(profile: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Extract list of strength indicators as list[str], or None if not present/valid."""
    if profile is None:
        return None
    value = profile.get("strength_indicators")
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        cleaned = [item.strip() for item in value if item and isinstance(item, str)]
        return cleaned if cleaned else None
    return None


def compute_stable_hire_signals(staff_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract raw Stable Hire pre-hire signals for a staff member.

    This function is fully defensive: if the staff member was not hired through Stable Hire
    or no profile data exists, all signals return None and stable_hire_present is False.

    Args:
        staff_data: Dictionary that may contain "stable_hire_profile"

    Returns:
        Dictionary of raw Stable Hire signals with explicit presence flag.
    """
    profile = staff_data.get("stable_hire_profile")

    if profile is None:
        return {
            "risk_score_pre_hire": None,
            "work_style": None,
            "reliability_assessment": None,
            "social_comfort_level": None,
            "interview_red_flags": None,
            "strength_indicators": None,
            "stable_hire_present": False,
        }

    return {
        "risk_score_pre_hire": extract_risk_score_pre_hire(profile),
        "work_style": extract_work_style(profile),
        "reliability_assessment": extract_reliability_assessment(profile),
        "social_comfort_level": extract_social_comfort_level(profile),
        "interview_red_flags": extract_interview_red_flags(profile),
        "strength_indicators": extract_strength_indicators(profile),
        "stable_hire_present": True,
    }