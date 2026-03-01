"""
services/team_composition_model.py

Research-backed team composition model for the Staff Stability Engine.

TIER 1: Configurable ideal ratios per restaurant format + position-persona
affinity scoring. Defaults derived from meta-analytic research (Peeters et al.
2006, Bell 2007, Neuman et al. 1999, Barry & Stewart 1997, Zhang et al. 2021)
and hospitality-specific studies (Tracey/Sturman/Tews 2007, Cooper et al. 2017).

TIER 2 (future): Replace defaults with real retention outcome data from the
En Place restaurant network as it accumulates.

Key research principles encoded:
  - Steady Operators as plurality on every shift (high mean conscientiousness)
  - Flight Risk cap (weakest-link effect — Bell 2007, Halfhill et al. 2005)
  - At least some Social Navigators (extraversion diversity — Neuman et al. 1999)
  - Too-many-of-anything hurts (TMGT curvilinear effects — Le et al. 2011)
  - Position-persona affinity varies by role (Tews et al. 2011, Azar 2015)

Usage:
    from services.team_composition_model import analyze_team_composition

    result = analyze_team_composition(
        persona_counts={"steadyOperator": 16, "quietContributor": 11, ...},
        total_assessed=39,
        restaurant_type="casual_dining",
        position_persona_map={"Server": {"steadyOperator": 5, ...}, ...}
    )
"""

from __future__ import annotations
from typing import Dict, List, Any, Optional


# ═══════════════════════════════════════════════════════════════
# FORMAT-SPECIFIC IDEAL RATIOS
# Source: Research synthesis (see Section 3 of composition research)
# These are starting defaults — replaced by network data in Tier 2
# ═══════════════════════════════════════════════════════════════

FORMAT_PROFILES = {
    "fine_dining": {
        "label": "Fine Dining",
        "ideal": {
            "steadyOperator": 0.50,
            "quietContributor": 0.25,
            "socialNavigator": 0.20,
            "flightRisk": 0.05,
        },
        "flight_risk_cap": 0.08,   # Hard ceiling — flag if exceeded
        "description": "Deliberate tempo, deep guest interaction, hierarchical brigade",
    },
    "casual_dining": {
        "label": "Casual Dining",
        "ideal": {
            "steadyOperator": 0.40,
            "quietContributor": 0.15,
            "socialNavigator": 0.30,
            "flightRisk": 0.15,
        },
        "flight_risk_cap": 0.18,
        "description": "Moderate tempo, friendly service, collaborative teams",
    },
    "fast_casual": {
        "label": "Fast Casual / QSR",
        "ideal": {
            "steadyOperator": 0.45,
            "quietContributor": 0.15,
            "socialNavigator": 0.15,
            "flightRisk": 0.25,
        },
        "flight_risk_cap": 0.28,
        "description": "High-speed, transactional, assembly-line roles",
    },
    "high_volume_bar": {
        "label": "High-Volume Bar",
        "ideal": {
            "steadyOperator": 0.30,
            "quietContributor": 0.10,
            "socialNavigator": 0.45,
            "flightRisk": 0.15,
        },
        "flight_risk_cap": 0.18,
        "description": "Intense bursts, social energy, conflict-prone",
    },
}

DEFAULT_FORMAT = "casual_dining"


# ═══════════════════════════════════════════════════════════════
# POSITION-PERSONA AFFINITY SCORES
# How well each persona fits each position (1.0 = neutral, >1 = good fit)
# Source: Tews et al. 2011, Azar 2015, Cooper et al. 2017, Shani et al. 2014
# ═══════════════════════════════════════════════════════════════

POSITION_AFFINITY = {
    "General Manager": {
        "steadyOperator": 1.4,
        "quietContributor": 0.7,
        "socialNavigator": 1.2,
        "flightRisk": 0.3,
    },
    "Assistant Manager": {
        "steadyOperator": 1.3,
        "quietContributor": 0.8,
        "socialNavigator": 1.2,
        "flightRisk": 0.4,
    },
    "Manager": {
        "steadyOperator": 1.3,
        "quietContributor": 0.8,
        "socialNavigator": 1.2,
        "flightRisk": 0.4,
    },
    "Executive Chef": {
        "steadyOperator": 1.4,
        "quietContributor": 1.0,
        "socialNavigator": 0.9,
        "flightRisk": 0.3,
    },
    "Sous Chef": {
        "steadyOperator": 1.3,
        "quietContributor": 1.1,
        "socialNavigator": 0.9,
        "flightRisk": 0.4,
    },
    "Line Cook": {
        "steadyOperator": 1.3,
        "quietContributor": 1.2,
        "socialNavigator": 0.8,
        "flightRisk": 0.6,
    },
    "Prep Cook": {
        "steadyOperator": 1.2,
        "quietContributor": 1.3,
        "socialNavigator": 0.7,
        "flightRisk": 0.7,
    },
    "Server": {
        "steadyOperator": 1.1,
        "quietContributor": 0.7,
        "socialNavigator": 1.3,
        "flightRisk": 0.5,
    },
    "Bartender": {
        "steadyOperator": 1.0,
        "quietContributor": 0.6,
        "socialNavigator": 1.3,
        "flightRisk": 0.5,
    },
    "Host": {
        "steadyOperator": 1.0,
        "quietContributor": 0.6,
        "socialNavigator": 1.4,
        "flightRisk": 0.5,
    },
    "Busser": {
        "steadyOperator": 1.2,
        "quietContributor": 1.1,
        "socialNavigator": 0.8,
        "flightRisk": 0.7,
    },
    "Dishwasher": {
        "steadyOperator": 1.1,
        "quietContributor": 1.3,
        "socialNavigator": 0.6,
        "flightRisk": 0.8,
    },
}

# Fallback for positions not in the map
DEFAULT_AFFINITY = {
    "steadyOperator": 1.1,
    "quietContributor": 1.0,
    "socialNavigator": 1.0,
    "flightRisk": 0.6,
}


# ═══════════════════════════════════════════════════════════════
# PERSONA METADATA (labels, icons, descriptions)
# ═══════════════════════════════════════════════════════════════

PERSONA_META = {
    "steadyOperator": {
        "label": "Steady Operators",
        "short": "Steady Operator",
        "icon": "⚓",
        "description": "consistent, reliable workers who anchor your shifts",
    },
    "quietContributor": {
        "label": "Quiet Contributors",
        "short": "Quiet Contributor",
        "icon": "🔧",
        "description": "independent, heads-down workers who catch issues early",
    },
    "socialNavigator": {
        "label": "Social Navigators",
        "short": "Social Navigator",
        "icon": "🧭",
        "description": "team connectors who read the room and keep morale up",
    },
    "flightRisk": {
        "label": "Flight Risks",
        "short": "Flight Risk",
        "icon": "⚡",
        "description": "independent spirits who need active support to stay engaged",
    },
}


# ═══════════════════════════════════════════════════════════════
# MAIN ANALYSIS FUNCTION
# ═══════════════════════════════════════════════════════════════

def analyze_team_composition(
    persona_counts: Dict[str, int],
    total_assessed: int,
    restaurant_type: str = DEFAULT_FORMAT,
    position_persona_map: Optional[Dict[str, Dict[str, int]]] = None,
) -> Dict[str, Any]:
    """
    Analyze team composition against research-backed ideal ratios.

    Parameters
    ----------
    persona_counts : dict
        {"steadyOperator": 16, "quietContributor": 11, "socialNavigator": 8, "flightRisk": 4}
    total_assessed : int
        Total number of staff with completed assessments.
    restaurant_type : str
        One of: fine_dining, casual_dining, fast_casual, high_volume_bar
    position_persona_map : dict, optional
        {"Server": {"steadyOperator": 5, "socialNavigator": 3, ...}, ...}
        If provided, generates position-level affinity insights.

    Returns
    -------
    dict with: format_profile, actual_ratios, deviations, gap_analysis,
               alerts, position_insights, overall_health_score
    """

    profile = FORMAT_PROFILES.get(restaurant_type, FORMAT_PROFILES[DEFAULT_FORMAT])
    ideal = profile["ideal"]

    if total_assessed == 0:
        return {
            "format_profile": profile,
            "actual_ratios": {},
            "deviations": {},
            "gap_analysis": None,
            "alerts": [],
            "position_insights": [],
            "overall_health_score": None,
        }

    # ── Actual ratios ──
    actual = {}
    for persona in ideal:
        count = persona_counts.get(persona, 0)
        actual[persona] = round(count / total_assessed, 3) if total_assessed > 0 else 0

    # ── Deviations from ideal ──
    deviations = {}
    for persona in ideal:
        diff = actual.get(persona, 0) - ideal[persona]
        deviations[persona] = {
            "actual_pct": round(actual.get(persona, 0) * 100, 1),
            "ideal_pct": round(ideal[persona] * 100, 1),
            "diff_pct": round(diff * 100, 1),
            "status": _deviation_status(diff, persona),
        }

    # ── Alerts (evidence-backed thresholds) ──
    alerts = _generate_alerts(persona_counts, actual, total_assessed, profile)

    # ── Gap analysis (research-informed recommendation) ──
    gap_analysis = _generate_gap_analysis(deviations, actual, profile)

    # ── Position insights ──
    position_insights = []
    if position_persona_map:
        position_insights = _analyze_positions(position_persona_map)

    # ── Overall health score (0-100) ──
    health = _compute_health_score(deviations, alerts)

    return {
        "format_profile": {
            "type": restaurant_type,
            "label": profile["label"],
            "description": profile["description"],
            "ideal_ratios": {k: round(v * 100, 1) for k, v in ideal.items()},
        },
        "actual_ratios": {k: round(v * 100, 1) for k, v in actual.items()},
        "deviations": deviations,
        "gap_analysis": gap_analysis,
        "alerts": alerts,
        "position_insights": position_insights,
        "overall_health_score": health,
    }


# ═══════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════

def _deviation_status(diff: float, persona: str) -> str:
    """Classify deviation as healthy, moderate, or critical."""
    # Flight risk over-representation is always bad
    if persona == "flightRisk" and diff > 0.05:
        return "critical"
    if persona == "flightRisk" and diff > 0:
        return "watch"

    abs_diff = abs(diff)
    if abs_diff <= 0.08:
        return "healthy"
    elif abs_diff <= 0.15:
        return "moderate"
    else:
        return "critical"


def _generate_alerts(
    counts: Dict[str, int],
    actual: Dict[str, float],
    total: int,
    profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Generate evidence-backed alerts."""
    alerts = []
    flight_count = counts.get("flightRisk", 0)
    flight_ratio = actual.get("flightRisk", 0)
    cap = profile.get("flight_risk_cap", 0.18)
    social_count = counts.get("socialNavigator", 0)

    # Alert 1: Flight Risk exceeds cap (Bell 2007 weakest-link effect)
    if flight_ratio > cap:
        alerts.append({
            "severity": "critical",
            "type": "flight_risk_cap",
            "title": "Flight Risk concentration too high",
            "message": (
                f"You have {flight_count} Flight Risks ({round(flight_ratio * 100)}% of assessed staff). "
                f"Research shows even one disengaged team member measurably degrades shift performance. "
                f"Focus retention interventions on these individuals or redistribute across shifts."
            ),
            "source": "Bell 2007 (weakest-link effect); Halfhill et al. 2005",
        })

    # Alert 2: Zero Social Navigators (Neuman et al. 1999 — need extraversion diversity)
    if social_count == 0 and total >= 5:
        alerts.append({
            "severity": "high",
            "type": "no_social_navigators",
            "title": "No Social Navigators on team",
            "message": (
                "Your team has no Social Navigators. Research shows teams need at least some "
                "extraversion diversity for optimal cohesion. Without team connectors, "
                "morale issues may go undetected and turnover contagion risk increases."
            ),
            "source": "Neuman et al. 1999; Tews et al. 2013",
        })

    # Alert 3: Too many Social Navigators (Barry & Stewart 1997 — dominance competition)
    social_ratio = actual.get("socialNavigator", 0)
    if social_ratio > 0.50:
        alerts.append({
            "severity": "moderate",
            "type": "social_navigator_excess",
            "title": "Social Navigator concentration high",
            "message": (
                f"Social Navigators make up {round(social_ratio * 100)}% of your team. "
                f"Research shows teams with too many extraverts experience lower cohesion "
                f"and dominance competition. Consider balancing with Steady Operators in new hires."
            ),
            "source": "Barry & Stewart 1997; Curşeu et al. 2019",
        })

    # Alert 4: Steady Operator shortage (Peeters et al. 2006 — need high conscientiousness mean)
    steady_ratio = actual.get("steadyOperator", 0)
    ideal_steady = profile["ideal"].get("steadyOperator", 0.40)
    if steady_ratio < ideal_steady * 0.6:  # Below 60% of ideal
        alerts.append({
            "severity": "high",
            "type": "steady_operator_shortage",
            "title": "Not enough Steady Operators",
            "message": (
                f"Only {round(steady_ratio * 100)}% of your team are Steady Operators "
                f"(ideal: {round(ideal_steady * 100)}%). These are your reliable anchors. "
                f"Teams with low mean conscientiousness show consistently lower performance."
            ),
            "source": "Peeters et al. 2006; Barrick et al. 1998",
        })

    return alerts


def _generate_gap_analysis(
    deviations: Dict[str, Dict],
    actual: Dict[str, float],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Generate hiring-focused gap analysis."""

    # Find the persona with the largest negative deviation (excluding flightRisk)
    hire_candidates = {}
    for persona in ["steadyOperator", "quietContributor", "socialNavigator"]:
        dev = deviations.get(persona, {})
        diff = dev.get("diff_pct", 0)
        if diff < 0:
            hire_candidates[persona] = diff

    # Find overrepresented persona
    surplus_candidates = {}
    for persona in ["steadyOperator", "quietContributor", "socialNavigator"]:
        dev = deviations.get(persona, {})
        diff = dev.get("diff_pct", 0)
        if diff > 5:  # Only flag if meaningfully over
            surplus_candidates[persona] = diff

    if not hire_candidates:
        # Team is at or above ideal for all hirable personas
        flight_dev = deviations.get("flightRisk", {}).get("diff_pct", 0)
        if flight_dev > 5:
            meta = PERSONA_META["flightRisk"]
            return {
                "priority": "reduce_flight_risk",
                "underrepresented": None,
                "overrepresented": "flightRisk",
                "recommendation": (
                    f"Your team ratios are healthy across core personas, but Flight Risks are "
                    f"{deviations['flightRisk']['actual_pct']}% vs the {deviations['flightRisk']['ideal_pct']}% "
                    f"target. Focus retention interventions on these individuals — "
                    f"research shows their departure often triggers contagion in connected staff."
                ),
                "hiring_action": "Prioritize Steady Operators in new hires to dilute Flight Risk concentration.",
            }
        return {
            "priority": "healthy",
            "underrepresented": None,
            "overrepresented": None,
            "recommendation": (
                "Your team composition is well-balanced for a "
                f"{profile['label'].lower()} environment. Continue monitoring as staff turn over."
            ),
            "hiring_action": None,
        }

    # Biggest gap
    most_needed = min(hire_candidates, key=hire_candidates.get)
    most_surplus = max(surplus_candidates, key=surplus_candidates.get) if surplus_candidates else None
    meta_needed = PERSONA_META[most_needed]
    dev_needed = deviations[most_needed]

    recommendation = (
        f"Your team is at {dev_needed['actual_pct']}% {meta_needed['label']} "
        f"vs the {dev_needed['ideal_pct']}% ideal for {profile['label'].lower()}. "
    )

    if most_surplus:
        meta_surplus = PERSONA_META[most_surplus]
        dev_surplus = deviations[most_surplus]
        recommendation += (
            f"Meanwhile, {meta_surplus['label']} are over-represented at "
            f"{dev_surplus['actual_pct']}% (ideal: {dev_surplus['ideal_pct']}%). "
        )

    recommendation += (
        f"In upcoming hires, lean toward {meta_needed['label']} — "
        f"{meta_needed['description']}."
    )

    return {
        "priority": "hire",
        "underrepresented": most_needed,
        "overrepresented": most_surplus,
        "recommendation": recommendation,
        "hiring_action": f"Prioritize {meta_needed['short']} profiles in Stable Hire candidate evaluation.",
    }


def _analyze_positions(
    position_persona_map: Dict[str, Dict[str, int]],
) -> List[Dict[str, Any]]:
    """Identify position-persona mismatches using affinity scores."""
    insights = []

    for position, persona_counts in position_persona_map.items():
        affinity = POSITION_AFFINITY.get(position, DEFAULT_AFFINITY)
        total_in_position = sum(persona_counts.values())

        if total_in_position == 0:
            continue

        # Find dominant persona for this position
        dominant = max(persona_counts, key=persona_counts.get)
        dominant_count = persona_counts[dominant]
        dominant_affinity = affinity.get(dominant, 1.0)

        # Check if the dominant persona is a poor fit
        if dominant_affinity < 0.8 and dominant_count > 1:
            # Find the best-fit persona for this position
            best_fit = max(affinity, key=affinity.get)
            best_fit_count = persona_counts.get(best_fit, 0)
            meta = PERSONA_META.get(best_fit, {})

            insights.append({
                "position": position,
                "issue": "persona_mismatch",
                "severity": "moderate" if dominant_affinity >= 0.6 else "high",
                "message": (
                    f"Most {position}s are {PERSONA_META.get(dominant, {}).get('label', dominant)} "
                    f"({dominant_count}/{total_in_position}), but this role has higher affinity for "
                    f"{meta.get('label', best_fit)} profiles. "
                    f"Consider this when backfilling {position} positions."
                ),
            })

        # Check for Flight Risks in high-impact positions
        flight_in_position = persona_counts.get("flightRisk", 0)
        if flight_in_position > 0 and affinity.get("flightRisk", 1.0) < 0.5:
            insights.append({
                "position": position,
                "issue": "flight_risk_in_critical_role",
                "severity": "high",
                "message": (
                    f"You have {flight_in_position} Flight Risk(s) in the {position} role. "
                    f"This is a high-impact position where disengagement is especially costly. "
                    f"Prioritize retention check-ins with these individuals."
                ),
            })

    return insights


def _compute_health_score(
    deviations: Dict[str, Dict],
    alerts: List[Dict],
) -> int:
    """
    Compute overall team composition health (0-100).
    100 = perfect match to ideal ratios, no alerts.
    """
    # Start at 100, deduct for deviations and alerts
    score = 100.0

    # Deduct for ratio deviations (up to 40 points)
    for persona, dev in deviations.items():
        abs_diff = abs(dev.get("diff_pct", 0))
        if persona == "flightRisk":
            # Penalize over-representation of flight risk more heavily
            if dev.get("diff_pct", 0) > 0:
                score -= abs_diff * 1.5
        else:
            score -= abs_diff * 0.6

    # Deduct for alerts (up to 40 points)
    alert_penalties = {"critical": 15, "high": 10, "moderate": 5}
    for alert in alerts:
        score -= alert_penalties.get(alert.get("severity", ""), 0)

    return max(0, min(100, round(score)))