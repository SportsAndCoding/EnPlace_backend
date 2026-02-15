"""
modules/nightly_pipeline/graph_pipeline.py

Nightly social graph processing for live restaurants.

Called by run_nightly_pipeline.py as a step in the nightly sequence.
For each active restaurant:
    1. Load persisted graph edges from staff_graph_edges
    2. Load active staff and their current state
    3. Generate today's organic pairwise events
    4. Update graph (decay old edges, reinforce with new events)
    5. Recompute centrality and role classifications
    6. Run cascade analysis for critical/important staff
    7. Persist updated edges back to staff_graph_edges
    8. Write per-staff metrics to staff_graph_metrics
    9. Write cascade results to staff_cascade_analysis
    10. Manage shock modifiers (apply decay, clean up expired)
    11. Update mood buffer

Fault-tolerant: one restaurant failure doesn't kill the batch.
Logs progress to stdout (captured by pipeline logging).

PLACEMENT IN NIGHTLY PIPELINE:
    Insert as step between flight risk scoring and restaurant metrics,
    so graph data is available when metrics are calculated.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

from database.supabase_client import supabase
from modules.synthetic.social_graph import StaffGraph
from modules.organic.pairwise_events_from_organic import generate_organic_pairwise_events
from modules.synthetic.contagion_engine import (
    apply_exit_shock,
    accumulate_shock_modifiers,
    decay_shock_modifiers,
    compute_retention_priority,
    simulate_what_if,
)


logger = logging.getLogger(__name__)

# Only auto-run cascade analysis for these tiers
CASCADE_TIERS = {"critical", "important"}

# Max staff to run cascade analysis for (prevents runaway compute)
MAX_CASCADE_TARGETS = 20


# ------------------------------------------------------------------
# MAIN ENTRY POINT
# ------------------------------------------------------------------

def run_graph_pipeline(
    target_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Run graph processing for all active restaurants.

    Parameters
    ----------
    target_date : date or None
        Date to process. Defaults to yesterday.

    Returns
    -------
    dict with job summary: restaurants_processed, total_edges_updated,
    total_staff_scored, total_cascades_computed, errors.
    """
    if target_date is None:
        import pytz
        eastern = pytz.timezone("America/New_York")
        target_date = datetime.now(eastern).date() - timedelta(days=1)

    logger.info("Starting graph pipeline for date: %s", target_date)

    # Get active restaurants
    try:
        result = supabase.table("restaurants") \
            .select("id, name") \
            .eq("status", "active") \
            .execute()
        restaurants = result.data or []
    except Exception as e:
        logger.critical("Failed to fetch restaurants: %s", e)
        return {
            "target_date": str(target_date),
            "status": "failed",
            "error": str(e),
        }

    if not restaurants:
        logger.warning("No active restaurants found")
        return {
            "target_date": str(target_date),
            "status": "complete",
            "restaurants_processed": 0,
        }

    # Process each restaurant
    total_stats = {
        "restaurants_processed": 0,
        "restaurants_failed": 0,
        "total_edges_updated": 0,
        "total_staff_scored": 0,
        "total_cascades_computed": 0,
    }
    errors: List[Dict[str, Any]] = []

    for restaurant in restaurants:
        rid = restaurant["id"]
        rname = restaurant.get("name", f"ID:{rid}")

        try:
            stats = process_restaurant_graph(rid, target_date)
            total_stats["restaurants_processed"] += 1
            total_stats["total_edges_updated"] += stats.get("edges_updated", 0)
            total_stats["total_staff_scored"] += stats.get("staff_scored", 0)
            total_stats["total_cascades_computed"] += stats.get("cascades_computed", 0)

            logger.info(
                "Graph processed %s: %d edges, %d staff scored, %d cascades",
                rname,
                stats.get("edges_updated", 0),
                stats.get("staff_scored", 0),
                stats.get("cascades_computed", 0),
            )

        except Exception as e:
            total_stats["restaurants_failed"] += 1
            errors.append({"restaurant_id": rid, "error": str(e)})
            logger.error(
                "Graph pipeline failed for %s (ID %d): %s",
                rname, rid, e,
                exc_info=True,
            )

    return {
        "target_date": str(target_date),
        "status": "complete",
        **total_stats,
        "errors": errors,
    }


# ------------------------------------------------------------------
# PER-RESTAURANT PROCESSING
# ------------------------------------------------------------------

def process_restaurant_graph(
    restaurant_id: int,
    target_date: date,
) -> Dict[str, int]:
    """
    Full graph processing cycle for one restaurant on one date.

    Returns dict of stats: edges_updated, staff_scored, cascades_computed.
    """
    # Step 1: Load active staff
    active_staff = _load_active_staff(restaurant_id)
    if len(active_staff) < 2:
        # Need at least 2 people to have a graph
        return {"edges_updated": 0, "staff_scored": 0, "cascades_computed": 0}

    # Step 2: Build graph from persisted edges + staff state
    graph = _load_graph_from_db(restaurant_id, active_staff)

    # Step 3: Generate organic pairwise events for today
    events = generate_organic_pairwise_events(restaurant_id, target_date)

    # Step 4: Update graph (decay + reinforce)
    day_index = (target_date - date(2025, 1, 1)).days  # consistent day numbering
    graph.update_daily(day_index, events)

    # Step 5: Recompute centrality
    graph.compute_centrality()

    # Step 6: Get retention priority ranking
    ranking = compute_retention_priority(graph=graph)

    # Step 7: Persist updated edges
    edges_updated = _persist_graph_edges(restaurant_id, graph, target_date)

    # Step 8: Write per-staff metrics
    staff_scored = _write_staff_graph_metrics(
        restaurant_id, graph, ranking, target_date
    )

    # Step 9: Run cascade analysis for critical/important staff
    cascades_computed = _run_cascade_analysis(
        restaurant_id, graph, ranking, target_date
    )

    # Step 10: Decay existing shock modifiers
    _decay_shock_modifiers_db(restaurant_id)

    # Step 11: Update mood buffer
    _update_mood_buffer(restaurant_id, graph)

    return {
        "edges_updated": edges_updated,
        "staff_scored": staff_scored,
        "cascades_computed": cascades_computed,
    }


# ------------------------------------------------------------------
# DATA LOADERS
# ------------------------------------------------------------------

def _load_active_staff(restaurant_id: int) -> List[Dict[str, Any]]:
    """Load active staff with their current state from the staff table."""
    result = supabase.table("staff") \
        .select("staff_id, position, hire_date, status") \
        .eq("restaurant_id", restaurant_id) \
        .eq("status", "active") \
        .execute()

    staff = []
    today = date.today()
    for row in (result.data or []):
        tenure_days = 0
        if row.get("hire_date"):
            try:
                hire = date.fromisoformat(row["hire_date"])
                tenure_days = (today - hire).days
            except (ValueError, TypeError):
                pass

        staff.append({
            "staff_id": row["staff_id"],
            "position": row.get("position", ""),
            "tenure_days": tenure_days,
        })

    return staff


def _load_graph_from_db(
    restaurant_id: int,
    active_staff: List[Dict[str, Any]],
) -> StaffGraph:
    """
    Reconstruct a StaffGraph from persisted edges and staff state.
    """
    graph = StaffGraph(restaurant_id=restaurant_id)

    # Add active staff as nodes
    # We don't know their persona in production — default to "workhorse"
    # The persona only matters for cascade sensitivity, and in production
    # we use the actual flight_risk score instead.
    for staff in active_staff:
        graph.add_node(staff["staff_id"], persona="workhorse")
        graph.update_node_state(
            staff["staff_id"],
            tenure_days=staff["tenure_days"],
        )

    # Load persisted edges
    edge_result = supabase.table("staff_graph_edges") \
        .select("staff_id_a, staff_id_b, weight, edge_type_weights, last_interaction_date") \
        .eq("restaurant_id", restaurant_id) \
        .execute()

    active_ids = {s["staff_id"] for s in active_staff}

    for row in (edge_result.data or []):
        sid_a = row["staff_id_a"]
        sid_b = row["staff_id_b"]

        # Skip edges where one party is no longer active
        if sid_a not in active_ids or sid_b not in active_ids:
            continue

        edge = graph._get_or_create_edge(sid_a, sid_b)
        edge.weight = float(row.get("weight", 0))
        edge.edge_type_weights = row.get("edge_type_weights", {})

        if row.get("last_interaction_date"):
            try:
                lid = date.fromisoformat(row["last_interaction_date"])
                edge.last_interaction_day = (lid - date(2025, 1, 1)).days
            except (ValueError, TypeError):
                pass

    # Load latest check-in moods to populate node state
    _populate_node_moods(restaurant_id, graph, active_ids)

    return graph


def _populate_node_moods(
    restaurant_id: int,
    graph: StaffGraph,
    active_ids: set,
):
    """
    Load recent check-in moods and flight risk data to populate
    graph node rolling_mood and current_mood.
    """
    # Get most recent check-in per staff (last 30 days)
    cutoff = (date.today() - timedelta(days=30)).isoformat()

    checkin_result = supabase.table("checkins") \
        .select("staff_id, mood_rating, checkin_date") \
        .eq("restaurant_id", restaurant_id) \
        .gte("checkin_date", cutoff) \
        .order("checkin_date", desc=True) \
        .execute()

    # Compute rolling mood per staff
    mood_history: Dict[str, List[int]] = {}
    for row in (checkin_result.data or []):
        sid = row["staff_id"]
        if sid not in active_ids or row.get("mood_rating") is None:
            continue
        if sid not in mood_history:
            mood_history[sid] = []
        mood_history[sid].append(row["mood_rating"])

    for sid, moods in mood_history.items():
        if not moods:
            continue
        rolling = sum(moods) / len(moods)
        current = moods[0]  # most recent (ordered desc)
        graph.update_node_state(
            sid,
            current_mood=current,
            rolling_mood=rolling,
        )

    # Also pull flight risk rates if available
    today_str = date.today().isoformat()
    fr_result = supabase.table("staff_flight_risk") \
        .select("staff_id, safe_rate, fair_rate, respected_rate") \
        .eq("restaurant_id", restaurant_id) \
        .eq("calculated_date", today_str) \
        .execute()

    for row in (fr_result.data or []):
        sid = row["staff_id"]
        if sid not in active_ids:
            continue
        graph.update_node_state(
            sid,
            rolling_safe_rate=float(row["safe_rate"]) if row.get("safe_rate") else 0.7,
            rolling_fair_rate=float(row["fair_rate"]) if row.get("fair_rate") else 0.7,
            rolling_respected_rate=float(row["respected_rate"]) if row.get("respected_rate") else 0.7,
        )


# ------------------------------------------------------------------
# DATA WRITERS
# ------------------------------------------------------------------

def _persist_graph_edges(
    restaurant_id: int,
    graph: StaffGraph,
    target_date: date,
) -> int:
    """
    Write graph edges back to staff_graph_edges.
    Uses upsert on the unique constraint (restaurant_id, staff_id_a, staff_id_b).
    Deletes edges that have decayed below threshold.
    """
    # Delete all existing edges for this restaurant, then re-insert active ones.
    # This is simpler than per-edge upsert and handles pruned edges automatically.
    try:
        supabase.table("staff_graph_edges") \
            .delete() \
            .eq("restaurant_id", restaurant_id) \
            .execute()
    except Exception as e:
        logger.warning("Could not clear edges for restaurant %d: %s", restaurant_id, e)

    # Build rows from graph state
    rows = []
    now = datetime.utcnow().isoformat()

    for (sid_a, sid_b), edge in graph.edges.items():
        if edge.weight <= 0:
            continue

        # Enforce alphabetical ordering for the constraint
        a, b = (sid_a, sid_b) if sid_a < sid_b else (sid_b, sid_a)

        # Convert day_index back to date
        lid = date(2025, 1, 1) + timedelta(days=edge.last_interaction_day)

        rows.append({
            "restaurant_id": restaurant_id,
            "staff_id_a": a,
            "staff_id_b": b,
            "weight": round(edge.weight, 4),
            "edge_type_weights": edge.edge_type_weights,
            "last_interaction_date": lid.isoformat(),
            "updated_at": now,
        })

    if rows:
        # Batch insert
        for i in range(0, len(rows), 500):
            batch = rows[i:i + 500]
            try:
                supabase.table("staff_graph_edges").insert(batch).execute()
            except Exception as e:
                logger.error("Edge insert failed for restaurant %d: %s", restaurant_id, e)

    return len(rows)


def _write_staff_graph_metrics(
    restaurant_id: int,
    graph: StaffGraph,
    ranking: List[Dict[str, Any]],
    target_date: date,
) -> int:
    """
    Write per-staff graph metrics for today.
    Follows staff_flight_risk pattern: delete existing for this date, insert fresh.
    """
    date_str = target_date.isoformat()

    # Delete existing metrics for this date
    try:
        supabase.table("staff_graph_metrics") \
            .delete() \
            .eq("restaurant_id", restaurant_id) \
            .eq("calculated_date", date_str) \
            .execute()
    except Exception:
        pass

    if not ranking:
        return 0

    rows = []
    for entry in ranking:
        sid = entry["staff_id"]
        node = graph.nodes.get(sid)
        if node is None:
            continue

        rows.append({
            "staff_id": sid,
            "restaurant_id": restaurant_id,
            "calculated_date": date_str,
            "betweenness_centrality": round(node.betweenness_centrality, 3),
            "eigenvector_centrality": round(node.eigenvector_centrality, 3),
            "degree_centrality": round(node.degree_centrality, 3),
            "composite_criticality": round(node.composite_criticality, 3),
            "role_label": entry.get("role_label"),
            "priority_tier": entry.get("priority_tier"),
            "retention_score": round(entry.get("retention_score", 0), 3),
            "cascade_risk": round(entry.get("cascade_risk", 0), 2),
            "cascade_severity": entry.get("cascade_severity"),
            "worst_case_exits": entry.get("worst_case_exits"),
            "flight_risk": round(entry.get("flight_risk", 0), 3),
            "replacement_difficulty": round(entry.get("replacement_difficulty", 0), 3),
            "top_reason": entry.get("top_reason"),
            "connected_staff_count": entry.get("connected_staff_count", 0),
            "strongest_connection_id": entry.get("strongest_connection_id"),
            "mood_color": entry.get("mood_color"),
            "tier_color": entry.get("tier_color"),
            "role_icon": entry.get("role_icon"),
            "size_factor": entry.get("size_factor"),
        })

    if rows:
        for i in range(0, len(rows), 500):
            batch = rows[i:i + 500]
            try:
                supabase.table("staff_graph_metrics").insert(batch).execute()
            except Exception as e:
                logger.error("Metrics insert failed for restaurant %d: %s", restaurant_id, e)

    return len(rows)


def _run_cascade_analysis(
    restaurant_id: int,
    graph: StaffGraph,
    ranking: List[Dict[str, Any]],
    target_date: date,
) -> int:
    """
    Auto-compute cascade analysis for critical and important tier staff.
    """
    date_str = target_date.isoformat()

    # Delete existing cascade analyses for this date
    try:
        supabase.table("staff_cascade_analysis") \
            .delete() \
            .eq("restaurant_id", restaurant_id) \
            .eq("analysis_date", date_str) \
            .execute()
    except Exception:
        pass

    targets = [
        entry for entry in ranking
        if entry.get("priority_tier") in CASCADE_TIERS
    ][:MAX_CASCADE_TARGETS]

    if not targets:
        return 0

    rows = []
    for entry in targets:
        sid = entry["staff_id"]

        try:
            what_if = simulate_what_if(
                graph=graph,
                target_staff_id=sid,
                iterations=100,
            )
        except Exception as e:
            logger.warning("Cascade sim failed for %s: %s", sid, e)
            continue

        cascade = what_if["cascade"]
        cost = what_if["cost_framing"]

        rows.append({
            "restaurant_id": restaurant_id,
            "target_staff_id": sid,
            "analysis_date": date_str,
            "cascade_severity": cost.get("severity"),
            "expected_additional_exits": cost.get("expected_cascade_size", 0),
            "worst_case_exits": cost.get("worst_case_cascade", 0),
            "at_risk_staff": cascade.get("at_risk_staff", [])[:10],
            "cascade_viz_before": cascade.get("node_states_before", {}),
            "cascade_viz_after": cascade.get("node_states_after", {}),
            "removed_edges": cascade.get("removed_edges", []),
            "total_departure_estimate": cost.get("total_departure_estimate"),
            "cost_multiplier": cost.get("estimated_replacement_cost_multiplier"),
            "risk_narrative": cost.get("risk_narrative"),
        })

    if rows:
        try:
            supabase.table("staff_cascade_analysis").insert(rows).execute()
        except Exception as e:
            logger.error("Cascade insert failed for restaurant %d: %s", restaurant_id, e)

    return len(rows)


# ------------------------------------------------------------------
# SHOCK MODIFIER MANAGEMENT
# ------------------------------------------------------------------

def _decay_shock_modifiers_db(restaurant_id: int):
    """
    Decay shock modifiers and delete expired ones.
    Called once per restaurant per nightly run.
    """
    result = supabase.table("staff_shock_modifiers") \
        .select("id, staff_id, modifier") \
        .eq("restaurant_id", restaurant_id) \
        .execute()

    if not result.data:
        return

    now = datetime.utcnow().isoformat()
    to_delete = []
    to_update = []

    for row in result.data:
        modifier = float(row["modifier"])

        if modifier <= 1.01:
            to_delete.append(row["id"])
            continue

        # Decay: 40% of excess removed per day
        excess = modifier - 1.0
        new_excess = excess * 0.6  # 1.0 - 0.4
        new_modifier = 1.0 + new_excess

        if new_modifier <= 1.01:
            to_delete.append(row["id"])
        else:
            to_update.append({
                "id": row["id"],
                "modifier": round(new_modifier, 3),
                "updated_at": now,
            })

    # Delete expired
    for mod_id in to_delete:
        try:
            supabase.table("staff_shock_modifiers") \
                .delete() \
                .eq("id", mod_id) \
                .execute()
        except Exception:
            pass

    # Update decayed
    for update in to_update:
        try:
            supabase.table("staff_shock_modifiers") \
                .update({"modifier": update["modifier"], "updated_at": update["updated_at"]}) \
                .eq("id", update["id"]) \
                .execute()
        except Exception:
            pass


def _update_mood_buffer(restaurant_id: int, graph: StaffGraph):
    """
    Persist mood buffer values for continuous contagion accumulation.
    Simple upsert: one row per staff.
    """
    now = datetime.utcnow().isoformat()

    for sid, node in graph.nodes.items():
        if not node.is_active:
            continue

        # The mood buffer value is the node's current rolling_mood
        # In production, this gets refined by apply_mood_contagion
        # over multiple nightly runs.
        buffered_mood = round(node.rolling_mood, 4)

        try:
            supabase.table("staff_mood_buffer") \
                .upsert({
                    "restaurant_id": restaurant_id,
                    "staff_id": sid,
                    "buffered_mood": buffered_mood,
                    "updated_at": now,
                }) \
                .execute()
        except Exception:
            pass