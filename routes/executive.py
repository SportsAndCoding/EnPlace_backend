# routes/executive.py
"""
Executive Portal Routes
=======================
Multi-unit portfolio view for restaurant group executives (CPOs, CEOs).
Reads from restaurant_daily_snapshot — never hits raw checkin/flight_risk tables.
Restricted to restaurant_executive portal_access (founder_ceo also permitted).
"""
import logging
from datetime import date, timedelta
from fastapi import APIRouter, HTTPException, Depends
from database.supabase_client import get_supabase
from services.auth_service import verify_jwt_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/executive", tags=["executive"])


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH GUARD
# ═══════════════════════════════════════════════════════════════════════════════

async def verify_executive(current_staff: dict = Depends(verify_jwt_token)):
    allowed = {"restaurant_executive", "founder_ceo"}
    if current_staff.get("portal_access") not in allowed:
        raise HTTPException(status_code=403, detail="Executive access required")
    return current_staff


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_group_id_for_staff(supabase, staff_id: str) -> int:
    """
    founder_ceo has no group_id — return group 1 (the demo group).
    restaurant_executive has a group_id stored on their staff record via
    a separate group_members approach. For now, we store group_id in
    a staff metadata field. We use restaurant_id=NULL + notes field
    until the group_members table is built. Simple lookup via staff notes
    is fragile — instead we'll accept group_id as a query param with
    a fallback to group 1 for founder_ceo.
    """
    pass  # resolved at endpoint level


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/group/{group_id}/summary")
async def get_group_summary(
    group_id: int,
    current_staff: dict = Depends(verify_executive),
):
    """
    Portfolio summary for all restaurants in a group.
    Returns today's snapshot for each location plus group-level rollups.
    """
    supabase = get_supabase()
    today = date.today().isoformat()

    # Verify group exists
    group_result = supabase.table("restaurant_groups").select("id, name").eq(
        "id", group_id
    ).execute()
    if not group_result.data:
        raise HTTPException(status_code=404, detail="Group not found")

    group = group_result.data[0]

    # Get all restaurants in group
    restaurants_result = supabase.table("restaurants").select(
        "id, name, location_label, status, subscription_status"
    ).eq("group_id", group_id).execute()

    restaurants = restaurants_result.data or []
    if not restaurants:
        return {"success": True, "group": group, "locations": [], "rollup": {}}

    restaurant_ids = [r["id"] for r in restaurants]
    restaurant_map = {r["id"]: r for r in restaurants}

    # Get today's snapshots for all restaurants in one query
    snapshots_result = supabase.table("restaurant_daily_snapshot").select("*").in_(
        "restaurant_id", restaurant_ids
    ).eq("snapshot_date", today).execute()

    # Build location list with snapshot data attached
    snapshot_map = {s["restaurant_id"]: s for s in (snapshots_result.data or [])}

    locations = []
    for rid in restaurant_ids:
        r = restaurant_map[rid]
        snap = snapshot_map.get(rid, {})
        locations.append({
            "restaurant_id":           rid,
            "name":                    r["name"],
            "location_label":          r.get("location_label") or r["name"],
            "status":                  r["status"],
            "subscription_status":     r["subscription_status"],
            "snapshot_date":           snap.get("snapshot_date", today),
            "checkin_completion_rate": snap.get("checkin_completion_rate", 0),
            "active_staff_count":      snap.get("active_staff_count", 0),
            "avg_mood_7d":             snap.get("avg_mood_7d", 0),
            "mood_trend":              snap.get("mood_trend", 0),
            "sma_score":               snap.get("sma_score", 0),
            "flight_risk_critical":    snap.get("flight_risk_critical", 0),
            "flight_risk_high":        snap.get("flight_risk_high", 0),
            "flight_risk_elevated":    snap.get("flight_risk_elevated", 0),
            "active_escalations":      snap.get("active_escalations", 0),
            "safe_rate":               snap.get("safe_rate", 0),
            "fair_rate":               snap.get("fair_rate", 0),
            "respected_rate":          snap.get("respected_rate", 0),
            "has_snapshot":            rid in snapshot_map,
        })

    # Sort: worst SMA first (problems bubble to top)
    locations.sort(key=lambda x: x["sma_score"])

    # Group-level rollups
    snapped = [l for l in locations if l["has_snapshot"]]
    total_staff    = sum(l["active_staff_count"] for l in snapped)
    total_critical = sum(l["flight_risk_critical"] for l in snapped)
    total_high     = sum(l["flight_risk_high"] for l in snapped)
    total_esc      = sum(l["active_escalations"] for l in snapped)
    avg_sma        = round(sum(l["sma_score"] for l in snapped) / len(snapped), 1) if snapped else 0
    avg_completion = round(sum(l["checkin_completion_rate"] for l in snapped) / len(snapped), 1) if snapped else 0
    avg_mood       = round(sum(l["avg_mood_7d"] for l in snapped) / len(snapped), 2) if snapped else 0

    rollup = {
        "total_locations":       len(locations),
        "locations_with_data":   len(snapped),
        "total_active_staff":    total_staff,
        "portfolio_sma":         avg_sma,
        "avg_completion_rate":   avg_completion,
        "avg_mood":              avg_mood,
        "total_critical_risk":   total_critical,
        "total_high_risk":       total_high,
        "total_active_esc":      total_esc,
        "attention_needed":      len([l for l in snapped if l["sma_score"] < 50 or l["flight_risk_critical"] > 2]),
    }

    return {
        "success":   True,
        "group":     group,
        "locations": locations,
        "rollup":    rollup,
        "as_of":     today,
    }


@router.get("/group/{group_id}/location/{restaurant_id}/trend")
async def get_location_trend(
    group_id: int,
    restaurant_id: int,
    days: int = 14,
    current_staff: dict = Depends(verify_executive),
):
    """
    14-day trend data for a single location.
    Used to render sparklines / detail drilldown.
    """
    supabase = get_supabase()

    if days > 30:
        days = 30

    since = (date.today() - timedelta(days=days)).isoformat()

    result = supabase.table("restaurant_daily_snapshot").select(
        "snapshot_date, sma_score, avg_mood_7d, checkin_completion_rate, "
        "flight_risk_critical, flight_risk_high, active_escalations, "
        "active_staff_count, safe_rate, fair_rate, respected_rate"
    ).eq("restaurant_id", restaurant_id).gte(
        "snapshot_date", since
    ).order("snapshot_date", desc=False).execute()

    # Verify this restaurant belongs to the group
    r = supabase.table("restaurants").select("id, name, location_label, group_id").eq(
        "id", restaurant_id
    ).execute()

    if not r.data or r.data[0].get("group_id") != group_id:
        raise HTTPException(status_code=403, detail="Restaurant not in this group")

    restaurant = r.data[0]

    return {
        "success":     True,
        "restaurant":  restaurant,
        "trend":       result.data or [],
        "days":        days,
    }


@router.get("/group/{group_id}/alerts")
async def get_group_alerts(
    group_id: int,
    current_staff: dict = Depends(verify_executive),
):
    """
    Portfolio-wide attention items:
    - Locations with SMA below 50
    - Locations with critical flight risk count > 2
    - Locations with active escalations
    All pulled from today's snapshots.
    """
    supabase = get_supabase()
    today = date.today().isoformat()

    restaurants_result = supabase.table("restaurants").select(
        "id, name, location_label"
    ).eq("group_id", group_id).execute()

    if not restaurants_result.data:
        return {"success": True, "alerts": []}

    restaurant_ids = [r["id"] for r in restaurants_result.data]
    restaurant_map = {r["id"]: r for r in restaurants_result.data}

    snapshots_result = supabase.table("restaurant_daily_snapshot").select(
        "restaurant_id, sma_score, flight_risk_critical, flight_risk_high, "
        "active_escalations, checkin_completion_rate, avg_mood_7d, mood_trend"
    ).in_("restaurant_id", restaurant_ids).eq("snapshot_date", today).execute()

    alerts = []
    for snap in (snapshots_result.data or []):
        rid = snap["restaurant_id"]
        label = restaurant_map.get(rid, {}).get("location_label") or restaurant_map.get(rid, {}).get("name", f"Location {rid}")

        if snap.get("sma_score", 100) < 50:
            alerts.append({
                "restaurant_id": rid,
                "location":      label,
                "type":          "low_sma",
                "severity":      "high",
                "message":       f"SMA score is {snap['sma_score']} — below healthy threshold",
                "value":         snap["sma_score"],
            })

        if snap.get("flight_risk_critical", 0) > 2:
            alerts.append({
                "restaurant_id": rid,
                "location":      label,
                "type":          "critical_flight_risk",
                "severity":      "critical",
                "message":       f"{snap['flight_risk_critical']} staff at critical flight risk",
                "value":         snap["flight_risk_critical"],
            })

        if snap.get("active_escalations", 0) > 0:
            alerts.append({
                "restaurant_id": rid,
                "location":      label,
                "type":          "active_escalation",
                "severity":      "moderate",
                "message":       f"{snap['active_escalations']} unresolved escalation(s)",
                "value":         snap["active_escalations"],
            })

        if snap.get("checkin_completion_rate", 100) < 50:
            alerts.append({
                "restaurant_id": rid,
                "location":      label,
                "type":          "low_completion",
                "severity":      "moderate",
                "message":       f"Check-in completion at {snap['checkin_completion_rate']}% — staff may not be engaging",
                "value":         snap["checkin_completion_rate"],
            })

        if snap.get("mood_trend", 0) < -0.3:
            alerts.append({
                "restaurant_id": rid,
                "location":      label,
                "type":          "declining_mood",
                "severity":      "moderate",
                "message":       f"Mood declining ({snap['mood_trend']:+.2f} vs prior period)",
                "value":         snap["mood_trend"],
            })

    # Sort: critical first
    severity_order = {"critical": 0, "high": 1, "moderate": 2}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 3))

    return {"success": True, "alerts": alerts, "as_of": today}