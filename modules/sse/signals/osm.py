from typing import Optional, Dict, Any


def extract_offers_received(osm_stats: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract number of shift pickup offers received, safely converting valid numeric types."""
    if osm_stats is None:
        return None
    value = osm_stats.get("shift_pickup_offered")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_offers_accepted(osm_stats: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract number of shift pickup offers accepted."""
    if osm_stats is None:
        return None
    value = osm_stats.get("shift_pickup_accepted")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_offers_declined(osm_stats: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract number of shift pickup offers declined."""
    if osm_stats is None:
        return None
    value = osm_stats.get("shift_pickup_declined")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_drops_requested(osm_stats: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract number of shift drop requests made."""
    if osm_stats is None:
        return None
    value = osm_stats.get("shift_drop_requested")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_swaps_requested(osm_stats: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract number of shift swap requests made."""
    if osm_stats is None:
        return None
    value = osm_stats.get("shift_swap_requested")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_schedule_changes_requested(osm_stats: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract number of general schedule change requests made."""
    if osm_stats is None:
        return None
    value = osm_stats.get("schedule_change_requested")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_preferred_shift_match_rate(osm_stats: Optional[Dict[str, Any]]) -> Optional[float]:
    """Extract preferred shift match rate as float in [0.0, 1.0], or None if invalid."""
    if osm_stats is None:
        return None
    value = osm_stats.get("preferred_shift_match_rate")
    if isinstance(value, float) and 0.0 <= value <= 1.0:
        return value
    if isinstance(value, int) and value in (0, 1):
        return float(value)
    return None


def compute_osm_signals(staff_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute all Open Shift Marketplace (OSM) behavioral signals for a staff member.

    This function is fully defensive: if the restaurant does not use OSM or no OSM data
    is available, all signals return None and osm_data_present is False.

    Args:
        staff_data: Dictionary containing optional "osm_stats" key.

    Returns:
        Dictionary of OSM-related signals with explicit presence flag.
    """
    osm_stats = staff_data.get("osm_stats")

    if osm_stats is None:
        return {
            "offers_received": None,
            "offers_accepted": None,
            "offers_declined": None,
            "drops_requested": None,
            "swaps_requested": None,
            "schedule_changes_requested": None,
            "preferred_shift_match_rate": None,
            "osm_data_present": False,
        }

    return {
        "offers_received": extract_offers_received(osm_stats),
        "offers_accepted": extract_offers_accepted(osm_stats),
        "offers_declined": extract_offers_declined(osm_stats),
        "drops_requested": extract_drops_requested(osm_stats),
        "swaps_requested": extract_swaps_requested(osm_stats),
        "schedule_changes_requested": extract_schedule_changes_requested(osm_stats),
        "preferred_shift_match_rate": extract_preferred_shift_match_rate(osm_stats),
        "osm_data_present": True,
    }