"""
services/social_graph_service.py

Service layer for social graph API endpoints.
Queries the production tables populated by the nightly graph pipeline
and shapes responses for the frontend.

All visualization directives (colors, sizes, icons) are pre-computed
by the nightly pipeline and stored in the tables. This service layer
does zero business logic — it reads and shapes.
"""

from datetime import date, timedelta
from typing import Dict, List, Any, Optional

from database.supabase_client import supabase


def get_graph_snapshot(organization_id: int) -> Dict[str, Any]:
    """
    Build the full graph visualization payload from the latest metrics.

    Returns:
        {
            nodes: [{staff_id, name, persona, mood_color, tier_color,
                     role_icon, size_factor, priority_tier, ...}],
            edges: [{source, target, weight, color, thickness, opacity}],
            metadata: {staff_count, edge_count, density, avg_mood},
            legend: {mood_colors, tier_colors, edge_colors, role_icons}
        }
    """
    today = date.today().isoformat()

    # Get latest metrics (try today, fall back to most recent)
    metrics = _get_latest_metrics(organization_id, today)

    if not metrics:
        return {
            "nodes": [],
            "edges": [],
            "metadata": {"staff_count": 0, "edge_count": 0},
            "legend": _get_legend(),
        }

    # Get staff names for display
    staff_ids = [m["staff_id"] for m in metrics]
    staff_names = _get_staff_names(organization_id, staff_ids)

    # Build nodes from metrics
    nodes = []
    for m in metrics:
        nodes.append({
            "staff_id": m["staff_id"],
            "name": staff_names.get(m["staff_id"], m["staff_id"][:8]),
            "role_label": m.get("role_label"),
            "priority_tier": m.get("priority_tier"),
            "retention_score": m.get("retention_score"),
            "cascade_risk": m.get("cascade_risk"),
            "top_reason": m.get("top_reason"),
            "connected_staff_count": m.get("connected_staff_count", 0),
            # Visualization directives (frontend reads directly)
            "mood_color": m.get("mood_color", "#EAB308"),
            "tier_color": m.get("tier_color", "#3B82F6"),
            "role_icon": m.get("role_icon", "circle"),
            "size_factor": m.get("size_factor", 0.5),
        })

    # Get edges
    edge_result = supabase.table("staff_graph_edges") \
        .select("staff_id_a, staff_id_b, weight, edge_type_weights") \
        .eq("organization_id", organization_id) \
        .execute()

    # Edge type -> color mapping
    edge_colors = {
        "shift_cowork": "#94A3B8",
        "swap_pickup": "#8B5CF6",
        "osm_pickup": "#06B6D4",
        "mood_sync": "#F59E0B",
    }

    edges = []
    active_ids = set(staff_ids)
    for row in (edge_result.data or []):
        if row["staff_id_a"] not in active_ids or row["staff_id_b"] not in active_ids:
            continue

        weight = float(row.get("weight", 0))
        if weight <= 0:
            continue

        # Determine primary edge type for color
        type_weights = row.get("edge_type_weights") or {}
        primary_type = max(type_weights, key=type_weights.get) if type_weights else "shift_cowork"

        edges.append({
            "source": row["staff_id_a"],
            "target": row["staff_id_b"],
            "weight": round(weight, 2),
            "color": edge_colors.get(primary_type, "#94A3B8"),
            "thickness": round(0.5 + weight * 1.5, 1),
            "opacity": round(min(1.0, 0.3 + weight * 0.2), 2),
            "primary_type": primary_type,
        })

    # Metadata
    avg_mood_values = [m.get("retention_score", 0) for m in metrics if m.get("retention_score")]
    metadata = {
        "staff_count": len(nodes),
        "edge_count": len(edges),
        "density": round(
            len(edges) / max(1, len(nodes) * (len(nodes) - 1) / 2), 3
        ) if len(nodes) > 1 else 0,
        "calculated_date": metrics[0].get("calculated_date") if metrics else today,
    }

    return {
        "nodes": nodes,
        "edges": edges,
        "metadata": metadata,
        "legend": _get_legend(),
    }


def get_retention_ranking(organization_id: int) -> Dict[str, Any]:
    """
    Get staff sorted by retention priority.

    Returns:
        {
            ranking: [{staff_id, name, priority_tier, retention_score,
                       role_label, cascade_risk, top_reason, ...}],
            summary: {total, critical, important, standard, low}
        }
    """
    today = date.today().isoformat()
    metrics = _get_latest_metrics(organization_id, today)

    if not metrics:
        return {"ranking": [], "summary": _empty_summary()}

    staff_ids = [m["staff_id"] for m in metrics]
    staff_names = _get_staff_names(organization_id, staff_ids)

    ranking = []
    tier_counts = {"critical": 0, "important": 0, "standard": 0, "low": 0}

    for m in metrics:
        tier = m.get("priority_tier", "standard")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        ranking.append({
            "staff_id": m["staff_id"],
            "name": staff_names.get(m["staff_id"], m["staff_id"][:8]),
            "priority_tier": tier,
            "retention_score": m.get("retention_score"),
            "role_label": m.get("role_label"),
            "cascade_risk": m.get("cascade_risk"),
            "cascade_severity": m.get("cascade_severity"),
            "worst_case_exits": m.get("worst_case_exits"),
            "flight_risk": m.get("flight_risk"),
            "top_reason": m.get("top_reason"),
            "connected_staff_count": m.get("connected_staff_count", 0),
            "mood_color": m.get("mood_color"),
            "tier_color": m.get("tier_color"),
            "role_icon": m.get("role_icon"),
        })

    # Already sorted by retention_score desc from the query
    return {
        "ranking": ranking,
        "summary": {
            "total": len(ranking),
            **tier_counts,
        },
    }


def get_cascade_analysis(
    organization_id: int,
    staff_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Get pre-computed cascade analysis for a specific staff member.

    Returns the what-if result with visualization data, or None if
    no analysis exists (staff is low-priority, so cascade wasn't
    pre-computed by the nightly pipeline).
    """
    today = date.today().isoformat()

    # Try today first, then most recent
    result = supabase.table("staff_cascade_analysis") \
        .select("*") \
        .eq("organization_id", organization_id) \
        .eq("target_staff_id", staff_id) \
        .order("analysis_date", desc=True) \
        .limit(1) \
        .execute()

    if not result.data:
        return None

    row = result.data[0]

    # Get staff name
    names = _get_staff_names(organization_id, [staff_id])
    target_name = names.get(staff_id, staff_id[:8])

    # Get names for at-risk staff
    at_risk = row.get("at_risk_staff") or []
    at_risk_ids = [a.get("staff_id") for a in at_risk if a.get("staff_id")]
    at_risk_names = _get_staff_names(organization_id, at_risk_ids) if at_risk_ids else {}

    for a in at_risk:
        if a.get("staff_id"):
            a["name"] = at_risk_names.get(a["staff_id"], a["staff_id"][:8])

    return {
        "target_staff_id": staff_id,
        "target_name": target_name,
        "analysis_date": row.get("analysis_date"),
        "cascade_severity": row.get("cascade_severity"),
        "expected_additional_exits": row.get("expected_additional_exits"),
        "worst_case_exits": row.get("worst_case_exits"),
        "total_departure_estimate": row.get("total_departure_estimate"),
        "cost_multiplier": row.get("cost_multiplier"),
        "risk_narrative": row.get("risk_narrative"),
        "at_risk_staff": at_risk,
        "visualization": {
            "before": row.get("cascade_viz_before", {}),
            "after": row.get("cascade_viz_after", {}),
            "removed_edges": row.get("removed_edges", []),
        },
    }


def get_graph_history(
    organization_id: int,
    days: int = 30,
) -> Dict[str, Any]:
    """
    Get graph metric trends over time for trend charts.

    Returns daily aggregates: staff count, tier distribution,
    avg retention score, total cascade risk.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    result = supabase.table("staff_graph_metrics") \
        .select("calculated_date, priority_tier, retention_score, cascade_risk") \
        .eq("organization_id", organization_id) \
        .gte("calculated_date", cutoff) \
        .order("calculated_date") \
        .execute()

    if not result.data:
        return {"days": [], "summary": {}}

    # Aggregate by date
    daily: Dict[str, Dict[str, Any]] = {}
    for row in result.data:
        d = row["calculated_date"]
        if d not in daily:
            daily[d] = {
                "date": d,
                "staff_count": 0,
                "critical": 0,
                "important": 0,
                "standard": 0,
                "low": 0,
                "total_retention_score": 0,
                "total_cascade_risk": 0,
            }

        day = daily[d]
        day["staff_count"] += 1
        tier = row.get("priority_tier", "standard")
        day[tier] = day.get(tier, 0) + 1
        day["total_retention_score"] += float(row.get("retention_score") or 0)
        day["total_cascade_risk"] += float(row.get("cascade_risk") or 0)

    # Compute averages
    history = []
    for d in sorted(daily.keys()):
        day = daily[d]
        count = day["staff_count"]
        history.append({
            "date": d,
            "staff_count": count,
            "critical": day["critical"],
            "important": day["important"],
            "standard": day["standard"],
            "low": day["low"],
            "avg_retention_score": round(day["total_retention_score"] / count, 3) if count else 0,
            "avg_cascade_risk": round(day["total_cascade_risk"] / count, 2) if count else 0,
        })

    return {
        "days": history,
        "period_start": history[0]["date"] if history else None,
        "period_end": history[-1]["date"] if history else None,
        "total_days": len(history),
    }


# ------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------

def _get_latest_metrics(
    organization_id: int,
    today: str,
) -> List[Dict[str, Any]]:
    """
    Get the most recent metrics for a restaurant.
    Tries today first, falls back to most recent date.
    """
    # Try today
    result = supabase.table("staff_graph_metrics") \
        .select("*") \
        .eq("organization_id", organization_id) \
        .eq("calculated_date", today) \
        .order("retention_score", desc=True) \
        .execute()

    if result.data:
        return result.data

    # Fall back to most recent
    result = supabase.table("staff_graph_metrics") \
        .select("*") \
        .eq("organization_id", organization_id) \
        .order("calculated_date", desc=True) \
        .limit(1) \
        .execute()

    if not result.data:
        return []

    latest_date = result.data[0]["calculated_date"]

    result = supabase.table("staff_graph_metrics") \
        .select("*") \
        .eq("organization_id", organization_id) \
        .eq("calculated_date", latest_date) \
        .order("retention_score", desc=True) \
        .execute()

    return result.data or []


def _get_staff_names(
    organization_id: int,
    staff_ids: List[str],
) -> Dict[str, str]:
    """Get staff_id -> full_name mapping."""
    if not staff_ids:
        return {}

    result = supabase.table("staff") \
        .select("staff_id, full_name") \
        .eq("organization_id", organization_id) \
        .in_("staff_id", staff_ids) \
        .execute()

    return {
        row["staff_id"]: row["full_name"]
        for row in (result.data or [])
        if row.get("full_name")
    }


def _get_legend() -> Dict[str, Any]:
    """Static legend for graph visualization."""
    return {
        "mood_colors": {
            "1": "#DC2626",
            "2": "#F97316",
            "3": "#EAB308",
            "4": "#84CC16",
            "5": "#22C55E",
        },
        "tier_colors": {
            "critical": "#DC2626",
            "important": "#F97316",
            "standard": "#3B82F6",
            "low": "#9CA3AF",
        },
        "edge_colors": {
            "shift_cowork": "#94A3B8",
            "swap_pickup": "#8B5CF6",
            "osm_pickup": "#06B6D4",
            "mood_sync": "#F59E0B",
        },
        "role_icons": {
            "glue_person": "heart",
            "bridge": "git-branch",
            "hub": "star",
            "peripheral": "circle",
        },
    }


def _empty_summary() -> Dict[str, int]:
    return {"total": 0, "critical": 0, "important": 0, "standard": 0, "low": 0}