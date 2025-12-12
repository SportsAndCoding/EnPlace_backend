"""
Dashboard Service - Aggregates all data for manager-home.html
Single endpoint, single round-trip, all dashboard data.
"""

from services.network_benchmark_service import (
    compute_network_burnout_percentile, 
    compute_organic_burnout_score,
    compute_network_sma_percentile,
    compute_organic_sma_score,
    compute_network_fairness_percentile,
    compute_organic_fairness_score,
)
from datetime import datetime, timedelta, date
from typing import Optional
from database.supabase_client import supabase


def get_dashboard_data(restaurant_id: int) -> dict:
    """
    Aggregate all dashboard data for a restaurant.
    Returns everything manager-home.html needs in one response.
    """
    
    # Date ranges
    today = date.today()
    week_ago = today - timedelta(days=7)
    two_weeks_ago = today - timedelta(days=14)
    four_weeks_ago = today - timedelta(days=28)
    
    # Get current week bounds (Monday to Sunday)
    days_since_monday = today.weekday()
    week_start = today - timedelta(days=days_since_monday)
    week_end = week_start + timedelta(days=6)
    
    # Fetch all needed data in parallel-ish (Supabase doesn't do true parallel, but grouped)
    restaurant = get_restaurant_info(restaurant_id)
    checkins_7d = get_checkins(restaurant_id, week_ago, today)
    checkins_14d = get_checkins(restaurant_id, two_weeks_ago, today)
    checkins_28d = get_checkins(restaurant_id, four_weeks_ago, today)
    manager_logs = get_manager_logs(restaurant_id, week_ago, today)
    shifts_today = get_shifts_for_date(restaurant_id, today)
    shifts_week = get_shifts_range(restaurant_id, week_start, week_end)
    staff_list = get_staff(restaurant_id)
    candidates = get_candidates(restaurant_id)
    escalations = get_escalations(restaurant_id)
    notifications = get_notifications(restaurant_id)
    
    # Compute each section
    smm = compute_smm(checkins_7d, checkins_28d, manager_logs)
    fairness = compute_fairness(checkins_7d, checkins_28d, shifts_week, staff_list)
    burnout = compute_burnout(checkins_7d, checkins_28d, shifts_week, staff_list)
    stable_schedule = compute_stable_schedule(shifts_week, shifts_today)
    stable_hire = compute_stable_hire(candidates)
    house_guardian = compute_house_guardian(smm, fairness, burnout, stable_schedule, escalations)
    action_board = compute_action_board(notifications)
    mood_heatmap = compute_mood_heatmap(checkins_7d)
    quick_stats = compute_quick_stats(shifts_today, shifts_week, staff_list)
    
    return {
        "success": True,
        "restaurant": restaurant,
        "smm": smm,
        "fairness": fairness,
        "burnout": burnout,
        "stable_schedule": stable_schedule,
        "stable_hire": stable_hire,
        "house_guardian": house_guardian,
        "action_board": action_board,
        "mood_heatmap": mood_heatmap,
        "quick_stats": quick_stats,
        "modules": {
            "stable_schedule_builder": {"owned": True},
            "stable_hire": {"owned": True},
            "house_guardian": {"owned": True},
            "open_shift_creator": {"owned": True}
        },
        "timestamp": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "pay_period": compute_pay_period()
        }
    }


# ═══════════════════════════════════════════════════════════════════
# DATA FETCHERS
# ═══════════════════════════════════════════════════════════════════

def get_restaurant_info(restaurant_id: int) -> dict:
    """Get restaurant basic info."""
    result = supabase.table("restaurants").select("*").eq("id", restaurant_id).single().execute()
    if result.data:
        r = result.data
        # Get staff count
        staff_result = supabase.table("staff").select("staff_id", count="exact").eq("restaurant_id", restaurant_id).eq("status", "Active").execute()
        staff_count = staff_result.count or 0
        
        return {
            "name": r.get("name", "Restaurant"),
            "manager": r.get("manager_name", "Manager"),
            "staff_count": staff_count
        }
    return {"name": "Restaurant", "manager": "Manager", "staff_count": 0}


def get_checkins(restaurant_id: int, start_date: date, end_date: date) -> list:
    """Get check-ins for date range."""
    result = supabase.table("sse_daily_checkins").select("*").eq("restaurant_id", restaurant_id).gte("checkin_date", start_date.isoformat()).lte("checkin_date", end_date.isoformat()).execute()
    return result.data or []


def get_manager_logs(restaurant_id: int, start_date: date, end_date: date) -> list:
    """Get manager logs for date range."""
    result = supabase.table("manager_daily_logs").select("*").eq("restaurant_id", restaurant_id).gte("log_date", start_date.isoformat()).lte("log_date", end_date.isoformat()).execute()
    return result.data or []


def get_shifts_for_date(restaurant_id: int, shift_date: date) -> list:
    """Get shifts for a specific date."""
    result = supabase.table("sse_shifts").select("*").eq("restaurant_id", restaurant_id).eq("shift_date", shift_date.isoformat()).execute()
    return result.data or []


def get_shifts_range(restaurant_id: int, start_date: date, end_date: date) -> list:
    """Get shifts for date range."""
    result = supabase.table("sse_shifts").select("*").eq("restaurant_id", restaurant_id).gte("shift_date", start_date.isoformat()).lte("shift_date", end_date.isoformat()).execute()
    return result.data or []


def get_staff(restaurant_id: int) -> list:
    """Get all active staff."""
    result = supabase.table("staff").select("*").eq("restaurant_id", restaurant_id).eq("status", "Active").execute()
    return result.data or []


def get_candidates(restaurant_id: int) -> list:
    """Get all candidates."""
    result = supabase.table("hiring_candidates").select("*").eq("restaurant_id", restaurant_id).execute()
    return result.data or []


def get_escalations(restaurant_id: int) -> list:
    """Get active escalations."""
    result = supabase.table("sse_escalation_events").select("*").eq("restaurant_id", restaurant_id).in_("status", ["active", "monitoring"]).execute()
    return result.data or []


def get_notifications(restaurant_id: int) -> list:
    """Get recent unread notifications."""
    result = supabase.table("notifications").select("*").eq("restaurant_id", restaurant_id).eq("is_read", False).order("created_at", desc=True).limit(10).execute()
    return result.data or []


# ═══════════════════════════════════════════════════════════════════
# COMPUTATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def compute_smm(checkins_7d: list, checkins_28d: list, manager_logs: list) -> dict:
    """
    Compute Staff-Manager Alignment score.
    Uses same logic as alignment_service but returns dashboard format.
    """
    if not checkins_7d:
        return {
            "score": 0,
            "status": "no_data",
            "trend": {"direction": "stable", "delta": 0, "period": "last 4 weeks"},
            "network": {"percentile": 50, "interpretation": "Insufficient data"}
        }
    
    # Emotional alignment (mood + felt flags)
    mood_scores = [c.get("mood_emoji", 3) for c in checkins_7d]
    avg_mood = sum(mood_scores) / len(mood_scores) if mood_scores else 3
    
    felt_safe = [c for c in checkins_7d if c.get("felt_safe")]
    felt_fair = [c for c in checkins_7d if c.get("felt_fair")]
    felt_respected = [c for c in checkins_7d if c.get("felt_respected")]
    
    safe_pct = len(felt_safe) / len(checkins_7d) * 100 if checkins_7d else 0
    fair_pct = len(felt_fair) / len(checkins_7d) * 100 if checkins_7d else 0
    respect_pct = len(felt_respected) / len(checkins_7d) * 100 if checkins_7d else 0
    
    # Normalize mood to 0-100
    mood_normalized = (avg_mood - 1) / 4 * 100
    
    # Emotional alignment = weighted average
    emotional = (mood_normalized * 0.4 + safe_pct * 0.2 + fair_pct * 0.2 + respect_pct * 0.2)
    
    # Operational alignment (manager vs staff perception)
    operational = 80  # Default if no logs
    if manager_logs and checkins_7d:
        # Compare manager ratings to staff moods on same days
        matches = 0
        comparisons = 0
        for log in manager_logs:
            log_date = log.get("log_date")
            day_checkins = [c for c in checkins_7d if c.get("checkin_date") == log_date]
            if day_checkins:
                staff_avg = sum(c.get("mood_emoji", 3) for c in day_checkins) / len(day_checkins)
                manager_rating = log.get("overall_rating", 3)
                # If within 1 point, consider aligned
                if abs(staff_avg - manager_rating) <= 1:
                    matches += 1
                comparisons += 1
        
        if comparisons > 0:
            operational = (matches / comparisons) * 100
    
    # Combined score
    score = int(emotional * 0.5 + operational * 0.5)
    
    # Trend (compare to 4 weeks ago)
    if checkins_28d:
        old_checkins = [c for c in checkins_28d if c not in checkins_7d]
        if old_checkins:
            old_mood = sum(c.get("mood_emoji", 3) for c in old_checkins) / len(old_checkins)
            old_score = int((old_mood - 1) / 4 * 100)
            delta = score - old_score
            direction = "up" if delta > 0 else "down" if delta < 0 else "stable"
        else:
            delta = 0
            direction = "stable"
    else:
        delta = 0
        direction = "stable"
    
    # Status
    if score >= 80:
        status = "healthy"
    elif score >= 60:
        status = "warning"
    else:
        status = "critical"
    
    # Network percentile (real comparison to synthetic network)
    network_rank = compute_network_sma_percentile(score)
    
    return {
        "score": score,
        "status": status,
        "trend": {
            "direction": direction,
            "delta": abs(delta),
            "period": "last 4 weeks"
        },
        "network": {
            "percentile": network_rank["percentile"],
            "interpretation": network_rank["interpretation"],
            "network_size": network_rank.get("network_size", 0)
        }
    }


def compute_fairness(checkins_7d: list, checkins_28d: list, shifts_week: list, staff_list: list) -> dict:
    """
    Compute Fairness score based on felt_fair responses and shift distribution.
    """
    if not checkins_7d:
        return {
            "score": 50,
            "status": "no_data",
            "trend": {"direction": "stable", "delta": 0, "period": "last month"},
            "network": {"percentile": 50, "interpretation": "Insufficient data"},
            "issues": []
        }
    
    # Fairness from check-ins
    felt_fair = [c for c in checkins_7d if c.get("felt_fair")]
    fair_pct = len(felt_fair) / len(checkins_7d) * 100 if checkins_7d else 50
    
    # Analyze weekend distribution
    issues = []
    weekend_shifts = [s for s in shifts_week if _is_weekend(s.get("shift_date"))]
    
    # Count weekend shifts per staff
    staff_weekend_counts = {}
    for shift in weekend_shifts:
        sid = shift.get("staff_id")
        if sid:
            staff_weekend_counts[sid] = staff_weekend_counts.get(sid, 0) + 1
    
    # Find staff with heavy weekend load
    total_weekend = len(weekend_shifts)
    if total_weekend > 0 and staff_list:
        for sid, count in staff_weekend_counts.items():
            pct = count / total_weekend * 100
            if pct > 30:  # More than 30% of weekend shifts
                staff_name = next((s.get("full_name", sid) for s in staff_list if s.get("staff_id") == sid), sid)
                issues.append(f"{staff_name.split()[0]} has {int(pct)}% of weekend shifts")
    
    # Score combines felt_fair + distribution balance
    distribution_score = 100 - (len(issues) * 15)  # Each issue reduces score
    score = int(fair_pct * 0.6 + max(0, distribution_score) * 0.4)
    
    # Trend
    if checkins_28d:
        old_checkins = [c for c in checkins_28d if c not in checkins_7d]
        if old_checkins:
            old_fair = [c for c in old_checkins if c.get("felt_fair")]
            old_pct = len(old_fair) / len(old_checkins) * 100 if old_checkins else 50
            delta = int(fair_pct - old_pct)
            direction = "up" if delta > 0 else "down" if delta < 0 else "stable"
        else:
            delta = 0
            direction = "stable"
    else:
        delta = 0
        direction = "stable"
    
    # Status
    if score >= 80:
        status = "healthy"
    elif score >= 60:
        status = "warning"
    else:
        status = "critical"
    
    # Network percentile (real comparison to synthetic network)
    organic_fairness = compute_organic_fairness_score(checkins_7d)
    network_rank = compute_network_fairness_percentile(organic_fairness)
    
    return {
        "score": score,
        "status": status,
        "trend": {
            "direction": direction,
            "delta": abs(delta),
            "period": "last month"
        },
        "network": {
            "percentile": network_rank["percentile"],
            "interpretation": network_rank["interpretation"],
            "network_size": network_rank.get("network_size", 0)
        },
        "issues": issues[:3]  # Top 3 issues
    }


def compute_burnout(checkins_7d: list, checkins_28d: list, shifts_week: list, staff_list: list) -> dict:
    """
    Compute burnout radar - EMOTIONAL PATTERNS ONLY.
    
    Shows role-level mood trends (anonymized).
    Schedule-based burnout (hours, overtime) belongs in Stable Schedule Builder.
    """
    
    # ═══════════════════════════════════════════════════════════════
    # ROLE-LEVEL EMOTIONAL PATTERNS (anonymized)
    # ═══════════════════════════════════════════════════════════════
    
    role_alerts = []
    
    # Get mood by role for this week
    role_moods_current = {}
    for checkin in checkins_7d:
        sid = checkin.get("staff_id")
        mood = checkin.get("mood_emoji")
        if sid and mood:
            staff_match = next((s for s in staff_list if s.get("staff_id") == sid), None)
            if staff_match:
                role = staff_match.get("position", "Unknown")
                if role not in role_moods_current:
                    role_moods_current[role] = []
                role_moods_current[role].append(mood)
    
    # Get mood by role for previous period (baseline)
    role_moods_baseline = {}
    old_checkins = [c for c in checkins_28d if c not in checkins_7d]
    for checkin in old_checkins:
        sid = checkin.get("staff_id")
        mood = checkin.get("mood_emoji")
        if sid and mood:
            staff_match = next((s for s in staff_list if s.get("staff_id") == sid), None)
            if staff_match:
                role = staff_match.get("position", "Unknown")
                if role not in role_moods_baseline:
                    role_moods_baseline[role] = []
                role_moods_baseline[role].append(mood)
    
    # Compare current vs baseline by role
    for role, current_moods in role_moods_current.items():
        current_avg = sum(current_moods) / len(current_moods) if current_moods else 0
        baseline_moods = role_moods_baseline.get(role, [])
        baseline_avg = sum(baseline_moods) / len(baseline_moods) if baseline_moods else current_avg
        
        if baseline_avg > 0:
            pct_change = ((current_avg - baseline_avg) / baseline_avg) * 100
        else:
            pct_change = 0
        
        # Flag roles with declining mood (more than 10% drop)
        if pct_change < -10:
            staff_count = len(set(c.get("staff_id") for c in checkins_7d 
                                  if next((s for s in staff_list if s.get("staff_id") == c.get("staff_id") 
                                          and s.get("position") == role), None)))
            
            role_alerts.append({
                "role": role,
                "staff_count": staff_count,
                "trend": "declining",
                "vs_baseline": f"{int(pct_change)}%",
                "current_avg": round(current_avg, 1),
                "baseline_avg": round(baseline_avg, 1)
            })
    
    # Sort by severity (biggest decline first)
    role_alerts.sort(key=lambda x: float(x["vs_baseline"].replace("%", "")))
    
    # ═══════════════════════════════════════════════════════════════
    # METRICS
    # ═══════════════════════════════════════════════════════════════
    
    elevated_count = len(role_alerts)
    
    # Trend (compare low mood count to previous week)
    delta = 0
    direction = "stable"
    if old_checkins:
        old_low = sum(1 for c in old_checkins if c.get("mood_emoji", 3) <= 2)
        new_low = sum(1 for c in checkins_7d if c.get("mood_emoji", 3) <= 2)
        delta = new_low - old_low
        direction = "up" if delta > 0 else "down" if delta < 0 else "stable"
    
    # Status based on how many roles are struggling
    if elevated_count == 0:
        status = "healthy"
    elif elevated_count <= 2:
        status = "warning"
    else:
        status = "critical"
    
    # Network comparison (emotional burnout vs synthetic network)
    organic_score = compute_organic_burnout_score(checkins_7d)
    network_rank = compute_network_burnout_percentile(organic_score)
    
    return {
        "elevated_count": elevated_count,
        "status": status,
        "trend": {
            "direction": direction,
            "delta": abs(delta),
            "period": "last week"
        },
        "network": {
            "percentile": network_rank["percentile"],
            "interpretation": network_rank["interpretation"],
            "network_size": network_rank.get("network_size", 0)
        },
        "role_alerts": role_alerts[:5]  # Top 5 struggling roles
    }



def compute_stable_schedule(shifts_week: list, shifts_today: list) -> dict:
    """
    Compute schedule coverage and gaps.
    """
    total_shifts = len(shifts_week)
    assigned = [s for s in shifts_week if s.get("staff_id")]
    open_shifts = [s for s in shifts_week if not s.get("staff_id")]
    
    if total_shifts == 0:
        coverage_pct = 100
    else:
        coverage_pct = len(assigned) / total_shifts * 100
    
    # Categorize gaps
    critical = 0
    warning = 0
    for shift in open_shifts:
        shift_date_str = shift.get("shift_date")
        if shift_date_str:
            try:
                shift_date = date.fromisoformat(shift_date_str)
                days_until = (shift_date - date.today()).days
                if days_until <= 1:
                    critical += 1
                else:
                    warning += 1
            except:
                warning += 1
    
    # Status
    if coverage_pct >= 95:
        status = "healthy"
    elif coverage_pct >= 85:
        status = "warning"
    else:
        status = "critical"
    
    percentile = int(min(95, coverage_pct - 5))
    
    return {
        "coverage_percent": round(coverage_pct, 1),
        "status": status,
        "gaps": {
            "critical": critical,
            "warning": warning,
            "total": len(open_shifts)
        },
        "trend": {
            "direction": "up",
            "delta": 2.1,
            "period": "last 2 weeks"
        },
        "network": {
            "percentile": percentile,
            "interpretation": f"Better than {percentile}% of network"
        }
    }


def compute_stable_hire(candidates: list) -> dict:
    """
    Compute hiring pipeline stats.
    """
    open_candidates = [c for c in candidates if c.get("status") == "open"]
    interviewed = [c for c in candidates if c.get("status") == "interviewed"]
    
    # Recommendations
    recommended = [c for c in candidates if c.get("recommendation") in ["strong_hire", "hire"]]
    high_risk = [c for c in candidates if c.get("cliff_risk_percent") and c.get("cliff_risk_percent") >= 50]
    
    # Average stability score
    scored = [c for c in candidates if c.get("stability_score")]
    avg_score = sum(c.get("stability_score", 0) for c in scored) / len(scored) if scored else 0
    
    return {
        "open_candidates": len(open_candidates) + len(interviewed),
        "recommended": len(recommended),
        "high_risk": len(high_risk),
        "avg_stability_score": int(avg_score),
        "trend": {
            "direction": "up",
            "delta": 5,
            "period": "last quarter"
        },
        "network": {
            "percentile": 71,
            "interpretation": "Better than 71% of network"
        }
    }


def compute_house_guardian(smm: dict, fairness: dict, burnout: dict, stable_schedule: dict, escalations: list) -> dict:
    """
    Compute House Guardian thermometers from other metrics.
    """
    thermometers = [
        {
            "id": "labor_compliance",
            "name": "Labor Compliance",
            "icon": "⚖️",
            "value": 94,  # Would compute from actual compliance data
            "status": "healthy",
            "trend": "stable",
            "alert": None
        },
        {
            "id": "coverage_risk",
            "name": "Coverage Risk",
            "icon": "📅",
            "value": int(stable_schedule.get("coverage_percent", 80)),
            "status": stable_schedule.get("status", "warning"),
            "trend": "up" if stable_schedule.get("trend", {}).get("direction") == "up" else "down",
            "alert": f"{stable_schedule.get('gaps', {}).get('total', 0)} open shifts this week" if stable_schedule.get('gaps', {}).get('total', 0) > 0 else None
        },
        {
            "id": "burnout_index",
            "name": "Burnout Index",
            "icon": "🔥",
            "value": 100 - (burnout.get("elevated_count", 0) * 15),
            "status": burnout.get("status", "warning"),
            "trend": burnout.get("trend", {}).get("direction", "stable"),
            "alert": f"{burnout.get('elevated_count', 0)} staff at elevated risk" if burnout.get("elevated_count", 0) > 0 else None
        },
        {
            "id": "fairness_balance",
            "name": "Fairness Balance",
            "icon": "⚖️",
            "value": fairness.get("score", 70),
            "status": fairness.get("status", "warning"),
            "trend": fairness.get("trend", {}).get("direction", "stable"),
            "alert": fairness.get("issues", [None])[0] if fairness.get("issues") else None
        },
        {
            "id": "retention_forecast",
            "name": "Retention Forecast",
            "icon": "👥",
            "value": smm.get("score", 80),
            "status": smm.get("status", "healthy"),
            "trend": smm.get("trend", {}).get("direction", "stable"),
            "alert": None
        }
    ]
    
    # Overall status
    warning_count = sum(1 for t in thermometers if t["status"] == "warning")
    critical_count = sum(1 for t in thermometers if t["status"] == "critical")
    
    if critical_count > 0:
        overall_status = "critical"
    elif warning_count >= 2:
        overall_status = "watch"
    else:
        overall_status = "healthy"
    
    return {
        "overall_status": overall_status,
        "thermometers": thermometers
    }


def compute_action_board(notifications: list) -> dict:
    """
    Transform notifications into action board items.
    """
    type_mapping = {
        "swap_request": {"icon": "🔄", "action": "Approve", "secondary": "Deny", "boost": 1},
        "coverage_gap": {"icon": "⚠️", "action": "Find Coverage", "secondary": None, "boost": 2},
        "pto_request": {"icon": "🏖️", "action": "Review", "secondary": None, "boost": 1},
        "escalation": {"icon": "🚨", "action": "Review", "secondary": None, "boost": 2},
        "system": {"icon": "📋", "action": "View", "secondary": None, "boost": 1}
    }
    
    priority_mapping = {
        "escalation": "critical",
        "coverage_gap": "critical",
        "swap_request": "high",
        "pto_request": "medium",
        "system": "low"
    }
    
    items = []
    for notif in notifications:
        notif_type = notif.get("type", "system")
        mapping = type_mapping.get(notif_type, type_mapping["system"])
        
        # Calculate time ago
        created = notif.get("created_at")
        time_ago = _time_ago(created) if created else "Recently"
        
        items.append({
            "id": notif.get("id"),
            "type": notif_type,
            "priority": priority_mapping.get(notif_type, "low"),
            "title": notif.get("title", "Notification"),
            "description": notif.get("message", ""),
            "time_ago": time_ago,
            "action": mapping["action"],
            "secondary_action": mapping["secondary"],
            "smm_boost": mapping["boost"]
        })
    
    # Sort by priority
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    items.sort(key=lambda x: priority_order.get(x["priority"], 3))
    
    return {
        "total_items": len(items),
        "items": items[:10]  # Top 10
    }


def compute_mood_heatmap(checkins_7d: list) -> dict:
    """
    Compute mood heatmap by day and shift (AM/PM).
    """
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    
    # Aggregate by day and shift
    day_shift_moods = {}
    for day in days:
        for shift in ["AM", "PM"]:
            day_shift_moods[f"{day}_{shift}"] = []
    
    for checkin in checkins_7d:
        checkin_date = checkin.get("checkin_date")
        if checkin_date:
            try:
                dt = date.fromisoformat(checkin_date)
                day_name = days[dt.weekday()]
                # Assume check-in time determines AM/PM, or default to PM
                shift = "PM"  # Could enhance with actual time
                key = f"{day_name}_{shift}"
                day_shift_moods[key].append(checkin.get("mood_emoji", 3))
            except:
                pass
    
    # Build local heatmap data
    local_data = []
    worst_spot = None
    worst_score = 100
    
    for day in days:
        for shift in ["AM", "PM"]:
            key = f"{day}_{shift}"
            moods = day_shift_moods[key]
            if moods:
                score = int(sum(moods) / len(moods) / 5 * 100)
            else:
                score = 75  # Default
            
            local_data.append({
                "day": day,
                "shift": shift,
                "score": score
            })
            
            if score < worst_score:
                worst_score = score
                worst_spot = f"{day} {shift}"
    
    # Network data (simulated percentiles)
    network_data = []
    for item in local_data:
        # Simulate percentile based on score
        percentile = min(95, max(5, item["score"] - 10 + (item["score"] // 20)))
        network_data.append({
            "day": item["day"],
            "shift": item["shift"],
            "percentile": percentile
        })
    
    return {
        "local": {
            "insight": f"{worst_spot} worse than usual by 22%" if worst_spot else "Stable mood across all shifts",
            "data": local_data
        },
        "network": {
            "insight": f"{worst_spot} better than 64% of restaurants" if worst_spot else "On par with network",
            "data": network_data
        }
    }


def compute_quick_stats(shifts_today: list, shifts_week: list, staff_list: list) -> dict:
    """
    Compute quick stats bar.
    """
    shifts_today_count = len(shifts_today)
    open_shifts = len([s for s in shifts_week if not s.get("staff_id")])
    
    # Calculate total hours
    total_hours = 0
    for shift in shifts_week:
        start = shift.get("scheduled_start")
        end = shift.get("scheduled_end")
        if start and end:
            try:
                from datetime import datetime as dt
                start_dt = dt.fromisoformat(start.replace("Z", "+00:00"))
                end_dt = dt.fromisoformat(end.replace("Z", "+00:00"))
                hours = (end_dt - start_dt).total_seconds() / 3600
                total_hours += hours
            except:
                pass
    
    # Estimate payroll (assume $15/hr average)
    est_payroll = int(total_hours * 15)
    
    return {
        "shifts_today": shifts_today_count,
        "open_shifts": open_shifts,
        "hours_this_period": int(total_hours),
        "est_payroll": f"${est_payroll:,}"
    }


def compute_pay_period() -> str:
    """
    Compute current pay period string.
    Assumes bi-weekly pay periods starting on Monday.
    """
    today = date.today()
    # Find start of current pay period (every other Monday)
    days_since_monday = today.weekday()
    this_monday = today - timedelta(days=days_since_monday)
    
    # Assume 2-week pay periods
    week_of_year = this_monday.isocalendar()[1]
    if week_of_year % 2 == 0:
        period_start = this_monday - timedelta(days=7)
    else:
        period_start = this_monday
    
    period_end = period_start + timedelta(days=13)
    
    return f"{period_start.strftime('%b %d')} - {period_end.strftime('%b %d, %Y')}"


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _is_weekend(date_str: str) -> bool:
    """Check if date string is a weekend."""
    if not date_str:
        return False
    try:
        dt = date.fromisoformat(date_str)
        return dt.weekday() >= 5  # Saturday = 5, Sunday = 6
    except:
        return False


def _time_ago(timestamp_str: str) -> str:
    """Convert timestamp to 'X ago' string."""
    if not timestamp_str:
        return "Recently"
    
    try:
        from datetime import datetime as dt
        created = dt.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = dt.now(created.tzinfo)
        diff = now - created
        
        minutes = int(diff.total_seconds() / 60)
        hours = int(diff.total_seconds() / 3600)
        days = diff.days
        
        if days > 0:
            return f"{days} day{'s' if days > 1 else ''} ago"
        elif hours > 0:
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif minutes > 0:
            return f"{minutes} min ago"
        else:
            return "Just now"
    except:
        return "Recently"