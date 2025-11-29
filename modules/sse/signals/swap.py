from typing import Optional, Dict, Any


def extract_swaps_requested(swap_stats: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract number of shift swaps requested, safely converting valid numeric types."""
    if swap_stats is None:
        return None
    value = swap_stats.get("swaps_requested")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_swaps_approved(swap_stats: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract number of shift swaps approved."""
    if swap_stats is None:
        return None
    value = swap_stats.get("swaps_approved")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_swaps_denied(swap_stats: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract number of shift swaps denied."""
    if swap_stats is None:
        return None
    value = swap_stats.get("swaps_denied")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def extract_swap_success_rate(swap_stats: Optional[Dict[str, Any]]) -> Optional[float]:
    """Extract swap success rate as float in [0.0, 1.0], or None if invalid."""
    if swap_stats is None:
        return None
    value = swap_stats.get("swap_success_rate")
    if isinstance(value, float) and 0.0 <= value <= 1.0:
        return value
    if isinstance(value, int) and value in (0, 1):
        return float(value)
    return None


def compute_swap_signals(staff_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute all Shift Swap behavioral signals for a staff member.

    This function is fully defensive: if the restaurant does not use the Shift Swap
    module or no swap data is available, all signals return None and swap_data_present
    is set to False.

    Args:
        staff_data: Dictionary containing optional "swap_stats" key.

    Returns:
        Dictionary of swap-related signals with explicit presence flag.
    """
    swap_stats = staff_data.get("swap_stats")

    if swap_stats is None:
        return {
            "swaps_requested": None,
            "swaps_approved": None,
            "swaps_denied": None,
            "swap_success_rate": None,
            "swap_data_present": False,
        }

    return {
        "swaps_requested": extract_swaps_requested(swap_stats),
        "swaps_approved": extract_swaps_approved(swap_stats),
        "swaps_denied": extract_swaps_denied(swap_stats),
        "swap_success_rate": extract_swap_success_rate(swap_stats),
        "swap_data_present": True,
    }