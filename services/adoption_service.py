"""
EN PLACE - ADOPTION METRICS SERVICE
====================================
Computes platform adoption KPIs for restaurant owners.
Answers: "Is my team actually using En Place?"

Metrics:
  - Staff check-in completion rate (daily/weekly)
  - Manager login frequency
  - Manager daily log submission rate
  - Escalation response rate
  - Overall adoption health score
"""

import logging
from datetime import datetime, timedelta, date, timezone
from typing import Dict, Any, List
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def get_adoption_metrics(restaurant_id: int) -> Dict[str, Any]:
    """
    Compute all adoption metrics for a restaurant.
    Returns a complete adoption dashboard payload.
    """
    supabase = get_supabase()
    today = datetime.now(timezone.utc).date()
    seven_days_ago = today - timedelta(days=7)
    thirty_days_ago = today - timedelta(days=30)

    # ─────────────────────────────────────────────────────────
    # FETCH RAW DATA
    # ─────────────────────────────────────────────────────────

    # Active staff
    staff_result = supabase.table("staff") \
        .select("staff_id, full_name, position, portal_access, last_login, is_owner, status") \
        .eq("restaurant_id", restaurant_id) \
        .eq("status", "active") \
        .execute()
    all_staff = staff_result.data or []
    managers = [s for s in all_staff if s.get("portal_access") == "manager"]
    non_manager_staff = [s for s in all_staff if s.get("portal_access") != "manager"]

    # Check-ins (last 7 days)
    checkins_7d = supabase.table("sse_daily_checkins") \
        .select("staff_id, checkin_date") \
        .eq("restaurant_id", restaurant_id) \
        .gte("checkin_date", seven_days_ago.isoformat()) \
        .execute()
    checkins_7d_data = checkins_7d.data or []

    # Check-ins (today)
    checkins_today = [c for c in checkins_7d_data if c.get("checkin_date") == today.isoformat()]

    # Manager daily logs (last 7 days)
    logs_7d = supabase.table("manager_daily_logs") \
        .select("manager_staff_id, log_date") \
        .eq("restaurant_id", restaurant_id) \
        .gte("log_date", seven_days_ago.isoformat()) \
        .execute()
    logs_7d_data = logs_7d.data or []

    # Escalation actions (last 30 days)
    esc_events = supabase.table("sse_escalation_events") \
        .select("id") \
        .eq("restaurant_id", restaurant_id) \
        .gte("triggered_at", thirty_days_ago.isoformat()) \
        .execute()
    esc_ids = [e["id"] for e in (esc_events.data or [])]

    esc_actions = []
    if esc_ids:
        # Get history entries with manager actions (not system)
        for esc_id in esc_ids[:50]:  # Cap to avoid huge queries
            history = supabase.table("sse_escalation_history") \
                .select("actor_staff_id, completed_at") \
                .eq("event_id", esc_id) \
                .neq("actor_staff_id", None) \
                .execute()
            esc_actions.extend(history.data or [])

    # ─────────────────────────────────────────────────────────
    # COMPUTE METRICS
    # ─────────────────────────────────────────────────────────

    checkin_metrics = _compute_checkin_metrics(all_staff, checkins_today, checkins_7d_data, today)
    manager_metrics = _compute_manager_metrics(managers, logs_7d_data, today)
    escalation_metrics = _compute_escalation_metrics(esc_ids, esc_actions)
    health_score = _compute_health_score(checkin_metrics, manager_metrics, escalation_metrics)

    return {
        "success": True,
        "restaurant_id": restaurant_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "health_score": health_score,
        "checkins": checkin_metrics,
        "managers": manager_metrics,
        "escalations": escalation_metrics,
        "staff_count": len(all_staff),
        "manager_count": len(managers)
    }


def _compute_checkin_metrics(
    all_staff: List[Dict],
    checkins_today: List[Dict],
    checkins_7d: List[Dict],
    today: date
) -> Dict[str, Any]:
    """Staff check-in completion rates."""

    # Exclude owners and managers from check-in expectations
    eligible_staff = [s for s in all_staff if s.get("portal_access") != "manager" or not s.get("is_owner")]
    eligible_ids = {s["staff_id"] for s in eligible_staff}
    total_eligible = len(eligible_ids)

    if total_eligible == 0:
        return {
            "today_rate": 0,
            "today_count": 0,
            "today_total": 0,
            "week_rate": 0,
            "week_avg_daily": 0,
            "staff_never_checked_in": [],
            "staff_streak_leaders": []
        }

    # Today's rate
    today_staff_ids = {c["staff_id"] for c in checkins_today if c["staff_id"] in eligible_ids}
    today_rate = round(len(today_staff_ids) / total_eligible * 100) if total_eligible else 0

    # Weekly rate (unique staff who checked in at least once)
    week_staff_ids = {c["staff_id"] for c in checkins_7d if c["staff_id"] in eligible_ids}
    week_rate = round(len(week_staff_ids) / total_eligible * 100) if total_eligible else 0

    # Average daily check-ins this week
    days_with_data = len({c["checkin_date"] for c in checkins_7d})
    week_avg_daily = round(len(checkins_7d) / max(days_with_data, 1), 1)

    # Staff who never checked in this week
    never_checked = [
        {"staff_id": s["staff_id"], "name": s["full_name"], "position": s["position"]}
        for s in eligible_staff
        if s["staff_id"] not in week_staff_ids
    ]

    # Check-in frequency leaders (most check-ins this week)
    from collections import Counter
    checkin_counts = Counter(c["staff_id"] for c in checkins_7d if c["staff_id"] in eligible_ids)
    streak_leaders = []
    for staff_id, count in checkin_counts.most_common(5):
        staff = next((s for s in all_staff if s["staff_id"] == staff_id), None)
        if staff:
            streak_leaders.append({
                "name": staff["full_name"],
                "position": staff["position"],
                "checkins": count
            })

    return {
        "today_rate": today_rate,
        "today_count": len(today_staff_ids),
        "today_total": total_eligible,
        "week_rate": week_rate,
        "week_avg_daily": week_avg_daily,
        "staff_never_checked_in": never_checked[:10],
        "staff_streak_leaders": streak_leaders
    }


def _compute_manager_metrics(
    managers: List[Dict],
    logs_7d: List[Dict],
    today: date
) -> Dict[str, Any]:
    """Manager engagement metrics."""

    manager_details = []
    for mgr in managers:
        sid = mgr["staff_id"]
        last_login = mgr.get("last_login")

        # Days since last login
        if last_login:
            try:
                login_dt = datetime.fromisoformat(last_login.replace("Z", "+00:00"))
                days_since = (datetime.now(timezone.utc) - login_dt).days
            except Exception:
                days_since = None
        else:
            days_since = None

        # Manager log count this week
        log_count = len([l for l in logs_7d if l.get("manager_staff_id") == sid])

        # Login status
        if days_since is None:
            login_status = "never"
        elif days_since == 0:
            login_status = "today"
        elif days_since <= 2:
            login_status = "recent"
        elif days_since <= 7:
            login_status = "stale"
        else:
            login_status = "inactive"

        manager_details.append({
            "staff_id": sid,
            "name": mgr["full_name"],
            "position": mgr["position"],
            "is_owner": mgr.get("is_owner", False),
            "last_login": last_login,
            "days_since_login": days_since,
            "login_status": login_status,
            "logs_this_week": log_count
        })

    # Aggregate
    active_managers = len([m for m in manager_details if m["login_status"] in ["today", "recent"]])
    total_managers = len(manager_details)
    login_rate = round(active_managers / total_managers * 100) if total_managers else 0

    total_logs = len(logs_7d)
    expected_logs = total_managers * 7  # One log per manager per day
    log_rate = round(total_logs / expected_logs * 100) if expected_logs else 0

    return {
        "login_rate": min(login_rate, 100),
        "active_count": active_managers,
        "total_count": total_managers,
        "log_submission_rate": min(log_rate, 100),
        "total_logs_this_week": total_logs,
        "details": manager_details
    }


def _compute_escalation_metrics(
    esc_ids: List[str],
    esc_actions: List[Dict]
) -> Dict[str, Any]:
    """Escalation response tracking."""

    total_escalations = len(esc_ids)
    total_actions = len(esc_actions)
    responded_escalations = len({a.get("event_id") for a in esc_actions if a.get("event_id")})

    response_rate = round(responded_escalations / total_escalations * 100) if total_escalations else 100

    return {
        "total_30d": total_escalations,
        "responded": responded_escalations,
        "response_rate": response_rate,
        "total_actions": total_actions
    }


def _compute_health_score(
    checkin_metrics: Dict,
    manager_metrics: Dict,
    escalation_metrics: Dict
) -> Dict[str, Any]:
    """
    Overall adoption health score (0-100).

    Weights:
      - Staff check-in rate (7d):   40%
      - Manager login rate:         30%
      - Manager log submission:     15%
      - Escalation response rate:   15%
    """
    checkin_score = checkin_metrics.get("week_rate", 0)
    login_score = manager_metrics.get("login_rate", 0)
    log_score = manager_metrics.get("log_submission_rate", 0)
    esc_score = escalation_metrics.get("response_rate", 100)

    weighted = (
        checkin_score * 0.40 +
        login_score * 0.30 +
        log_score * 0.15 +
        esc_score * 0.15
    )
    score = round(weighted)

    if score >= 80:
        status = "healthy"
        message = "Your team is actively using En Place"
    elif score >= 50:
        status = "warning"
        message = "Some team members need encouragement to engage"
    else:
        status = "critical"
        message = "Low adoption puts your investment at risk"

    return {
        "score": score,
        "status": status,
        "message": message,
        "breakdown": {
            "checkin_rate": checkin_score,
            "manager_login_rate": login_score,
            "manager_log_rate": log_score,
            "escalation_response_rate": esc_score
        }
    }