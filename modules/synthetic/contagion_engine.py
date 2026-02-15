"""
modules/synthetic/contagion_engine.py

Applies social graph influence to individual staff emotions and exit
probabilities. This is the bridge between the graph structure and the
existing simulation pipeline.

Two modes of contagion:
1. AMBIENT: Daily mood influence from graph neighbors (subtle, continuous).
   Applied AFTER daily_emotion_simulator, BEFORE daily_behavior.
2. SHOCK: Exit event triggers elevated risk for connected staff (acute).
   Returns multipliers consumed by persona_evolution on the next day.

INPUT CONTRACTS:
    staff_emotions[staff_id] keys (from daily_emotion_simulator output):
        mood_emoji: int (1-5)
        felt_safe: bool
        felt_fair: bool
        felt_respected: bool

    StaffGraph (from social_graph.py):
        .nodes: Dict[str, StaffNode]
        .edges: Dict[Tuple, GraphEdge]
        ._edge_key(a, b) -> Tuple
        .get_node_neighbors(staff_id) -> list[dict]

    StaffNode attributes used:
        rolling_mood, persona, current_mood, tenure_days

OUTPUT CONTRACTS:
    apply_mood_contagion() -> modified emotions dict (same schema, mood_emoji may change)
    apply_exit_shock() -> {staff_id: float} exit probability multipliers
    compute_retention_priority() -> sorted list of retention rankings
    accumulate_shock_modifiers() -> merges multiple shocks into one modifier dict

DETERMINISM: All operations are deterministic given the same inputs.
    No random number generation — mood contagion is a weighted average,
    and exit shock multipliers are computed formulas.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from modules.synthetic.social_graph import StaffGraph, StaffNode


# ---------------------------------------------------------------------------
# Exit reason contagion multipliers
# ---------------------------------------------------------------------------
# How much does the *reason* someone left amplify the shock to connected staff?
# A dignity violation ("felt disrespected") spreads fear more than a quiet departure.

EXIT_REASON_MULTIPLIERS: Dict[str, float] = {
    # Emotional exits — high contagion (visible suffering, shared grievance)
    "critically low mood - emotional breakdown": 1.2,
    "critically low mood - emotional breakdown / quit": 1.2,
    "felt disrespected - dignity issue": 1.5,
    "felt treated unfairly - resentment": 1.4,
    "severe disrespect and unfairness - immediate quit": 1.6,
    "didn't feel safe - toxic environment": 1.3,
    "consistently unsafe environment - quit for wellbeing": 1.4,

    # Tenure-based exits — lower contagion (expected, less alarming)
    "early mismatch - job not as expected": 0.5,
    "reality check failed - couldn't adapt": 0.6,
    "didn't find their place - never integrated": 0.7,

    # General — baseline contagion
    "accumulated frustration - found better opportunity": 1.0,
}

# Persona sensitivity to team disruption (how much a neighbor's exit affects them)
PERSONA_SHOCK_SENSITIVITY: Dict[str, float] = {
    "social_glue":          1.4,  # most affected — team is their identity
    "burned_idealist":      1.3,  # validates their negative worldview
    "flight_risk_veteran":  1.3,  # already on the edge, this pushes them
    "emerging_leader":      1.2,  # feels responsibility, stress
    "enthusiastic_rookie":  1.1,  # shakes confidence in the new job
    "overwhelmed_rookie":   1.1,  # adds to feeling everything is falling apart
    "snarky_rookie":        0.9,  # makes a joke about it, but feels it
    "ghoster_in_training":  0.8,  # barely engaged anyway
    "lazy_rookie":          0.7,  # doesn't care much
    "workhorse":            0.6,  # head down, keeps grinding
    "quiet_pro":            0.5,  # emotionally insulated, just adjusts
    "cynical_anchor":       0.3,  # "told you so" — expects it, unbothered
}


# ---------------------------------------------------------------------------
# 1. AMBIENT MOOD CONTAGION
# ---------------------------------------------------------------------------

def apply_mood_contagion(
    *,
    staff_emotions: Dict[str, Dict[str, Any]],
    graph: "StaffGraph",
    contagion_strength: float = 0.12,
    day_index: int = 0,
    mood_buffer: Optional[Dict[str, float]] = None,
) -> tuple:
    """
    Apply ambient mood influence from graph neighbors.

    For each staff member, compute the weighted average mood of their
    graph neighbors. Shift their mood toward the neighbor average,
    scaled by contagion_strength and edge weight.

    The mood_buffer solves a critical quantization problem: mood_emoji
    is an integer 1-5, but contagion deltas are small floats (~0.12).
    Without a buffer, +0.12 on mood=2 rounds back to 2 and the
    pressure never accumulates. The buffer tracks the continuous
    fractional accumulation across days.

    Parameters
    ----------
    staff_emotions : dict
        {staff_id: emotion_output_dict} from daily_emotion_simulator.
        Keys: mood_emoji (int 1-5), felt_safe, felt_fair, felt_respected.
    graph : StaffGraph
        Current social graph with edges and nodes.
    contagion_strength : float
        Maximum mood shift from neighbors per day. Default 0.12.
    day_index : int
        Current day (for logging/debugging only).
    mood_buffer : dict or None
        {staff_id: float} continuous mood values carried from previous day.
        If None, each staff member starts from their raw mood_emoji.

    Returns
    -------
    tuple of (adjusted_emotions, updated_mood_buffer)
        adjusted_emotions: Modified copy of staff_emotions with adjusted
            mood_emoji values. Original dict is NOT mutated. Boolean
            fields are NOT modified.
        updated_mood_buffer: {staff_id: float} continuous mood values
            for the runner to carry forward to the next day.
    """
    if mood_buffer is None:
        mood_buffer = {}

    # Deep copy to avoid mutation
    adjusted = {}
    for sid, emo in staff_emotions.items():
        adjusted[sid] = dict(emo)

    new_buffer: Dict[str, float] = {}

    for sid in staff_emotions:
        node = graph.nodes.get(sid)
        if node is None or not node.is_active:
            # No graph presence — carry raw mood, no contagion
            new_buffer[sid] = float(staff_emotions[sid]["mood_emoji"])
            continue

        # Get weighted neighbors from the graph edges
        neighbors = _get_weighted_neighbors(sid, graph)

        # Start from buffered continuous mood if available,
        # otherwise from today's raw integer mood
        base_mood = mood_buffer.get(sid, float(staff_emotions[sid]["mood_emoji"]))

        # Anchor toward today's raw mood to prevent runaway drift.
        # The emotion simulator already computed today's "true" mood.
        # The buffer blends with it — not overriding, but not ignoring
        # social pressure either. At 0.15, sustained contagion from
        # a team of mood=4 can lift a mood=3 person to 4 over ~10 days.
        # But a fundamentally mood=2 burned_idealist in a mood=4 team
        # will only shift to 3 — social pressure modulates, doesn't override.
        raw_mood = float(staff_emotions[sid]["mood_emoji"])
        anchor_weight = 0.15
        base_mood = base_mood * (1.0 - anchor_weight) + raw_mood * anchor_weight

        if not neighbors:
            # No connections — just store anchored value
            new_buffer[sid] = max(1.0, min(5.0, base_mood))
            adjusted[sid]["mood_emoji"] = max(1, min(5, round(base_mood)))
            continue

        # Compute weighted average neighbor mood (using THEIR buffer values)
        total_weight = 0.0
        weighted_mood_sum = 0.0

        for neighbor_id, edge_weight in neighbors:
            neighbor_emo = staff_emotions.get(neighbor_id)
            if neighbor_emo is None:
                continue
            # Use neighbor's buffered mood if available
            neighbor_mood = mood_buffer.get(
                neighbor_id, float(neighbor_emo["mood_emoji"])
            )
            weighted_mood_sum += edge_weight * neighbor_mood
            total_weight += edge_weight

        if total_weight <= 0:
            new_buffer[sid] = max(1.0, min(5.0, base_mood))
            adjusted[sid]["mood_emoji"] = max(1, min(5, round(base_mood)))
            continue

        neighbor_avg_mood = weighted_mood_sum / total_weight

        # Mood delta: pull toward neighbor average
        raw_delta = contagion_strength * (neighbor_avg_mood - base_mood)

        # Cap the delta
        capped_delta = max(-contagion_strength, min(contagion_strength, raw_delta))

        # Apply to continuous mood
        continuous_mood = base_mood + capped_delta
        continuous_mood = max(1.0, min(5.0, continuous_mood))

        # Store continuous value in buffer for next day
        new_buffer[sid] = round(continuous_mood, 4)

        # Quantize to integer for the emotion output
        adjusted[sid]["mood_emoji"] = max(1, min(5, round(continuous_mood)))

        # Debug fields
        adjusted[sid]["_contagion_delta"] = round(capped_delta, 4)
        adjusted[sid]["_neighbor_avg_mood"] = round(neighbor_avg_mood, 2)
        adjusted[sid]["_mood_continuous"] = round(continuous_mood, 4)

    return adjusted, new_buffer


def _get_weighted_neighbors(
    staff_id: str,
    graph: "StaffGraph",
) -> List[tuple]:
    """
    Get (neighbor_id, edge_weight) pairs from the graph.
    Only includes active neighbors.
    """
    neighbors = []
    for (s, t), edge in graph.edges.items():
        other = None
        if s == staff_id:
            other = t
        elif t == staff_id:
            other = s
        if other is None:
            continue

        other_node = graph.nodes.get(other)
        if other_node is None or not other_node.is_active:
            continue

        neighbors.append((other, edge.weight))

    return neighbors


# ---------------------------------------------------------------------------
# 2. EXIT SHOCK
# ---------------------------------------------------------------------------

def apply_exit_shock(
    *,
    exited_staff_id: str,
    exit_reason: str,
    graph: "StaffGraph",
) -> Dict[str, float]:
    """
    When a staff member exits, compute elevated exit risk multipliers
    for connected staff.

    The shock multiplier is applied to persona_evolution's exit probability
    check on the NEXT day: exit_prob * modifier.

    Parameters
    ----------
    exited_staff_id : str
        The staff member who just quit.
    exit_reason : str
        The reason string from persona_evolution (used to determine
        how contagious this departure is).
    graph : StaffGraph
        Current social graph (the exited node should still be present
        at this point — removal happens AFTER shock calculation).

    Returns
    -------
    dict
        {staff_id: exit_probability_multiplier} for each connected neighbor.
        Multiplier > 1.0 means elevated risk. Unconnected staff are not
        included (implicitly 1.0).

        The refactored restaurant runner applies these multipliers to
        the next day's evolve_persona() call.
    """
    modifiers: Dict[str, float] = {}

    # Get the exit reason's contagion multiplier
    reason_multiplier = _match_exit_reason(exit_reason)

    # Get all neighbors and their edge weights
    neighbors = _get_weighted_neighbors(exited_staff_id, graph)
    if not neighbors:
        return modifiers

    for neighbor_id, edge_weight in neighbors:
        neighbor_node = graph.nodes.get(neighbor_id)
        if neighbor_node is None or not neighbor_node.is_active:
            continue

        # Persona sensitivity: how much does this persona type care
        # when a teammate leaves?
        persona_sensitivity = PERSONA_SHOCK_SENSITIVITY.get(
            neighbor_node.persona, 0.8
        )

        # Mood vulnerability: unhappy staff are more susceptible to shock
        # mood=1 -> 1.6x, mood=3 -> 1.0x, mood=5 -> 0.4x
        mood_vulnerability = 1.0 + (3.0 - neighbor_node.rolling_mood) * 0.3

        # Edge weight factor: stronger relationship = bigger impact
        # Normalize to reasonable range (typical strong edge ~2-4)
        edge_factor = min(2.0, edge_weight / 2.0)

        # Combine all factors into a single multiplier
        # The multiplier inflates the base exit probability, so:
        #   1.0 = no effect
        #   1.5 = 50% more likely to quit tomorrow
        #   2.0 = double the quit probability
        shock_intensity = (
            edge_factor *
            reason_multiplier *
            persona_sensitivity *
            mood_vulnerability
        )

        # Convert intensity to a multiplier (additive over base 1.0)
        # Scale it so typical shocks produce 1.2-2.0x multipliers,
        # not 5-10x (which would override all other factors)
        multiplier = 1.0 + shock_intensity * 0.3

        # Cap at 3.0x — even the worst shock shouldn't guarantee exit
        multiplier = min(3.0, max(1.0, multiplier))

        modifiers[neighbor_id] = round(multiplier, 3)

    return modifiers


def _match_exit_reason(reason: str) -> float:
    """
    Match an exit reason string to its contagion multiplier.
    Uses substring matching since persona_evolution reasons
    can vary slightly.
    """
    # Try exact match first
    if reason in EXIT_REASON_MULTIPLIERS:
        return EXIT_REASON_MULTIPLIERS[reason]

    # Substring matching for partial hits
    reason_lower = reason.lower()
    if "disrespect" in reason_lower:
        return 1.5
    if "unfair" in reason_lower:
        return 1.4
    if "unsafe" in reason_lower or "safe" in reason_lower:
        return 1.3
    if "mood" in reason_lower or "emotional" in reason_lower:
        return 1.2
    if "mismatch" in reason_lower:
        return 0.5
    if "adapt" in reason_lower:
        return 0.6
    if "never integrated" in reason_lower:
        return 0.7

    # Default: baseline contagion
    return 1.0


# ---------------------------------------------------------------------------
# 3. SHOCK ACCUMULATION
# ---------------------------------------------------------------------------

def accumulate_shock_modifiers(
    *existing_modifiers: Dict[str, float],
) -> Dict[str, float]:
    """
    Merge multiple shock modifier dicts into one.

    When multiple people quit on the same day (or shocks carry over
    from yesterday), their effects compound multiplicatively.

    Example:
        Person A quits -> Billy gets 1.4x modifier
        Person B quits -> Billy gets 1.2x modifier
        Combined: Billy gets 1.4 * 1.2 = 1.68x modifier

    Parameters
    ----------
    *existing_modifiers : dicts
        Any number of {staff_id: multiplier} dicts to combine.

    Returns
    -------
    dict
        {staff_id: combined_multiplier} for all affected staff.
        Capped at 4.0x to prevent runaway cascades in simulation.
    """
    combined: Dict[str, float] = {}

    for modifier_dict in existing_modifiers:
        for staff_id, multiplier in modifier_dict.items():
            if staff_id in combined:
                combined[staff_id] *= multiplier
            else:
                combined[staff_id] = multiplier

    # Cap combined modifiers
    for staff_id in combined:
        combined[staff_id] = round(min(4.0, combined[staff_id]), 3)

    return combined


def decay_shock_modifiers(
    modifiers: Dict[str, float],
    decay_rate: float = 0.4,
) -> Dict[str, float]:
    """
    Decay shock modifiers over time.

    Exit shocks don't last forever — the emotional impact fades.
    Each day, the modifier decays toward 1.0 (no effect).

    Parameters
    ----------
    modifiers : dict
        {staff_id: multiplier} from previous day.
    decay_rate : float
        How much of the excess modifier is removed per day.
        0.4 means 40% of the "above 1.0" portion decays each day.
        A 2.0x modifier decays as: 2.0 -> 1.6 -> 1.36 -> 1.22 -> 1.13
        Effectively gone in ~5 days.

    Returns
    -------
    dict
        Decayed modifiers. Entries at or below 1.01 are removed.
    """
    decayed: Dict[str, float] = {}

    for staff_id, multiplier in modifiers.items():
        if multiplier <= 1.01:
            continue

        excess = multiplier - 1.0
        new_excess = excess * (1.0 - decay_rate)

        if new_excess > 0.01:
            decayed[staff_id] = round(1.0 + new_excess, 3)

    return decayed


# ---------------------------------------------------------------------------
# 4. RETENTION PRIORITY
# ---------------------------------------------------------------------------

def compute_retention_priority(
    *,
    graph: "StaffGraph",
) -> List[Dict[str, Any]]:
    """
    Rank staff by retention priority — the money output for managers.

    This is a convenience wrapper around graph.get_criticality_ranking()
    that adds the human-readable "top_reason" explanation.

    The manager sees: "Sarah — CRITICAL — Losing her likely triggers
    2.3 additional departures. She bridges the morning and evening crews."

    Returns
    -------
    list[dict]
        Sorted highest priority first. Each entry:
        {
            staff_id, persona, retention_score, priority_tier, tier_color,
            cascade_risk, graph_criticality, flight_risk, replacement_difficulty,
            role_label, role_icon, current_mood, tenure_days, mood_color,
            top_reason  (human-readable explanation)
        }
    """
    ranking = graph.get_criticality_ranking()

    for entry in ranking:
        entry["top_reason"] = _generate_top_reason(entry)

    return ranking


def _generate_top_reason(entry: Dict[str, Any]) -> str:
    """
    Generate a human-readable explanation for why this person matters.

    The phrasing is designed for a restaurant manager who doesn't know
    what "betweenness centrality" means. They need to understand WHY
    this person is important in 10 words or less.
    """
    role = entry.get("role_label", "peripheral")
    cascade = entry.get("cascade_risk", 0)
    flight = entry.get("flight_risk", 0)
    tier = entry.get("priority_tier", "standard")
    persona = entry.get("persona", "")
    tenure = entry.get("tenure_days", 0)

    # Critical tier: lead with cascade danger
    if tier == "critical":
        if role == "glue_person":
            return f"Team anchor — losing them risks {cascade:.1f} additional departures"
        if role == "bridge":
            return f"Connects separate crews — losing them risks {cascade:.1f} departures"
        if flight > 0.6:
            return f"High flight risk AND high team impact — urgent retention priority"
        return f"Core team member — losing them risks {cascade:.1f} departures"

    # Important tier: balance of cascade and flight risk
    if tier == "important":
        if flight > 0.5 and cascade > 1.0:
            return "At risk of leaving and would take others with them"
        if role == "bridge":
            return "Bridges different shifts — loss would fragment the team"
        if role == "glue_person":
            return "Holds the team together socially — important to keep happy"
        if cascade > 1.0:
            return f"Connected to {cascade:.0f}+ people who might follow them out"
        return "Reliable presence with meaningful team connections"

    # Standard tier
    if tier == "standard":
        if tenure > 180:
            return "Experienced — steady contributor, moderate team connections"
        if role == "hub":
            return "Well-liked within their group"
        return "Solid team member with normal attrition risk"

    # Low tier
    if role == "peripheral":
        return "Operates independently — low team disruption if they leave"
    if persona in ("lazy_rookie", "ghoster_in_training"):
        return "Low engagement — departure has minimal team impact"
    return "New or loosely connected — standard onboarding support"


# ---------------------------------------------------------------------------
# 5. WHAT-IF ANALYSIS (manager portal feature)
# ---------------------------------------------------------------------------

def simulate_what_if(
    *,
    graph: "StaffGraph",
    target_staff_id: str,
    iterations: int = 200,
) -> Dict[str, Any]:
    """
    The "Billy asks for a raise" feature.

    Wraps graph.simulate_cascade() with additional context for the
    manager portal — the cascade result plus a cost framing.

    Parameters
    ----------
    graph : StaffGraph
    target_staff_id : str
        The staff member the manager is evaluating.
    iterations : int
        Monte Carlo iterations for cascade simulation.

    Returns
    -------
    dict with:
        cascade: full cascade simulation result (with before/after viz data)
        retention_summary: {
            priority_tier, role_label, top_reason,
            connected_staff_count, strongest_connection
        }
        cost_framing: {
            expected_cascade_size, estimated_replacement_cost_multiplier,
            risk_narrative
        }
    """
    graph.ensure_centrality()

    # Run cascade simulation
    cascade = graph.simulate_cascade(target_staff_id, iterations=iterations)

    # Get this person's node data
    node = graph.nodes.get(target_staff_id)
    if node is None:
        return {
            "cascade": cascade,
            "retention_summary": {"priority_tier": "unknown", "top_reason": "Unknown staff member"},
            "cost_framing": {"expected_cascade_size": 0, "risk_narrative": "No data available"},
        }

    # Get their neighbors
    neighbors = _get_weighted_neighbors(target_staff_id, graph)
    strongest = None
    if neighbors:
        neighbors.sort(key=lambda x: -x[1])
        strongest_node = graph.nodes.get(neighbors[0][0])
        if strongest_node:
            strongest = {
                "staff_id": neighbors[0][0],
                "persona": strongest_node.persona,
                "edge_weight": round(neighbors[0][1], 2),
            }

    # Build ranking entry for this person
    ranking_entry = {
        "role_label": node.role_label,
        "priority_tier": node.priority_tier,
        "cascade_risk": cascade["expected_additional_exits"],
        "flight_risk": max(0.0, (3.5 - node.rolling_mood) / 3.5),
        "replacement_difficulty": min(1.0, node.tenure_days / 365.0),
        "persona": node.persona,
        "tenure_days": node.tenure_days,
    }
    top_reason = _generate_top_reason(ranking_entry)

    # Cost framing
    cascade_size = cascade["expected_additional_exits"]
    # Each departure costs roughly 1x that position (includes hiring,
    # training, lost productivity). The target + cascade = total impact.
    total_departures = 1 + cascade_size
    cost_multiplier = round(total_departures, 1)

    if cascade_size >= 3.0:
        risk_narrative = (
            f"Losing this person likely triggers a cascade of "
            f"{cascade_size:.1f} additional departures. "
            f"Total estimated impact: {total_departures:.1f} positions to refill."
        )
    elif cascade_size >= 1.5:
        risk_narrative = (
            f"This departure would likely pull {cascade_size:.1f} others "
            f"toward the exit. Real cost extends well beyond one replacement."
        )
    elif cascade_size >= 0.5:
        risk_narrative = (
            f"Moderate ripple risk — about {cascade_size:.1f} connected "
            f"staff may become flight risks after this departure."
        )
    else:
        risk_narrative = (
            "Low cascade risk. This departure would be absorbed "
            "by the team without significant follow-on losses."
        )

    return {
        "cascade": cascade,
        "retention_summary": {
            "staff_id": target_staff_id,
            "persona": node.persona,
            "tenure_days": node.tenure_days,
            "current_mood": node.current_mood,
            "priority_tier": node.priority_tier,
            "role_label": node.role_label,
            "top_reason": top_reason,
            "connected_staff_count": len(neighbors),
            "strongest_connection": strongest,
        },
        "cost_framing": {
            "expected_cascade_size": round(cascade_size, 2),
            "worst_case_cascade": cascade["worst_case_exits"],
            "total_departure_estimate": round(total_departures, 1),
            "estimated_replacement_cost_multiplier": cost_multiplier,
            "risk_narrative": risk_narrative,
            "severity": cascade["cascade_severity"],
        },
    }