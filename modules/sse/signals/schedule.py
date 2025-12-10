from datetime import datetime
from typing import Optional, Dict, Any, List


def compute_shift_hours(shift: Dict[str, Any]) -> float:
    """Calculate hours for a single shift from start/end times."""
    start = shift.get("scheduled_start")
    end = shift.get("scheduled_end")
    
    if not start or not end:
        return 0.0
    
    try:
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if isinstance(end, str):
            end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        
        hours = (end - start).total_seconds() / 3600
        return max(0, hours)
    except:
        return 0.0


def extract_shift_type(shifts: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """Return shift_type from today's shift, or None if no shifts."""
    if not shifts:
        return None
    return shifts[0].get("shift_type")


def compute_hours_today(shifts: Optional[List[Dict[str, Any]]]) -> float:
    """Sum hours from all shifts on this day."""
    if not shifts:
        return 0.0
    return sum(compute_shift_hours(s) for s in shifts)


def detect_clopen(shifts_today: Optional[List[Dict[str, Any]]], shifts_yesterday: Optional[List[Dict[str, Any]]]) -> bool:
    """
    Detect close-open pattern: closed last night, opening this morning.
    A clopen is when yesterday's shift ended after 10pm and today's starts before 10am.
    """
    if not shifts_today or not shifts_yesterday:
        return False
    
    try:
        yesterday_ends = []
        for s in shifts_yesterday:
            end = s.get("scheduled_end")
            if end:
                if isinstance(end, str):
                    end = datetime.fromisoformat(end.replace("Z", "+00:00"))
                yesterday_ends.append(end)
        
        today_starts = []
        for s in shifts_today:
            start = s.get("scheduled_start")
            if start:
                if isinstance(start, str):
                    start = datetime.fromisoformat(start.replace("Z", "+00:00"))
                today_starts.append(start)
        
        if not yesterday_ends or not today_starts:
            return False
        
        last_end = max(yesterday_ends)
        first_start = min(today_starts)
        
        hours_between = (first_start - last_end).total_seconds() / 3600
        ended_late = last_end.hour >= 22
        starts_early = first_start.hour < 10
        
        return ended_late and starts_early and hours_between < 10
        
    except:
        return False


def compute_schedule_signals(staff_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute all schedule-related signals for a staff member on a given day.

    Args:
        staff_data: Dictionary containing:
            - "shifts_today": list of shift rows for target date
            - "shifts_yesterday": list of shift rows for day before (for clopen detection)
            - "shifts_week": list of shift rows for the week (for weekly hours)

    Returns:
        Dictionary of schedule signals with explicit presence flag.
    """
    shifts_today = staff_data.get("shifts_today") or []
    shifts_yesterday = staff_data.get("shifts_yesterday") or []
    shifts_week = staff_data.get("shifts_week") or []

    if not shifts_today and not shifts_week:
        return {
            "shift_type": None,
            "hours_today": 0.0,
            "hours_week": 0.0,
            "is_clopen": False,
            "shifts_this_week": 0,
            "schedule_data_present": False,
        }

    hours_today = compute_hours_today(shifts_today)
    hours_week = sum(compute_shift_hours(s) for s in shifts_week)
    is_clopen = detect_clopen(shifts_today, shifts_yesterday)

    return {
        "shift_type": extract_shift_type(shifts_today),
        "hours_today": round(hours_today, 2),
        "hours_week": round(hours_week, 2),
        "is_clopen": is_clopen,
        "shifts_this_week": len(shifts_week),
        "schedule_data_present": True,
    }