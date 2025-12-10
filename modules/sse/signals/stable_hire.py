from typing import Optional, Dict, Any


def extract_stability_score(profile: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract stability score (0-100) from hiring candidate profile."""
    if profile is None:
        return None
    value = profile.get("stability_score")
    if isinstance(value, int) and 0 <= value <= 100:
        return value
    return None


def extract_cliff_risk(profile: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract 90-day cliff risk percent from hiring candidate profile."""
    if profile is None:
        return None
    value = profile.get("cliff_risk_percent")
    if isinstance(value, int) and 0 <= value <= 100:
        return value
    return None


def extract_recommendation(profile: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract hire recommendation string."""
    if profile is None:
        return None
    value = profile.get("recommendation")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def extract_fingerprint(profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Extract behavioral fingerprint JSONB."""
    if profile is None:
        return None
    value = profile.get("fingerprint")
    if isinstance(value, dict):
        return value
    return None


def compute_stable_hire_signals(staff_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract Stable Hire signals for a staff member.

    Links staff back to their hiring_candidates record via hired_staff_id.

    Args:
        staff_data: Dictionary that may contain "stable_hire_profile"
                   (the hiring_candidates row that resulted in this hire)

    Returns:
        Dictionary of Stable Hire signals with explicit presence flag.
    """
    profile = staff_data.get("stable_hire_profile")

    if profile is None:
        return {
            "pre_hire_stability_score": None,
            "pre_hire_cliff_risk": None,
            "pre_hire_recommendation": None,
            "behavioral_fingerprint": None,
            "stable_hire_present": False,
        }

    return {
        "pre_hire_stability_score": extract_stability_score(profile),
        "pre_hire_cliff_risk": extract_cliff_risk(profile),
        "pre_hire_recommendation": extract_recommendation(profile),
        "behavioral_fingerprint": extract_fingerprint(profile),
        "stable_hire_present": True,
    }