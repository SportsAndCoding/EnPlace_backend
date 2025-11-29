"""
modules/synth/persona_evolution.py

Persona Evolution Engine for the En Place synthetic staffing simulator.
Determines whether a staff member’s underlying persona changes as their tenure
and long-term emotional trends evolve.

All transitions are deterministic and based only on tenure milestones and
rolling 30-day emotional averages.
"""

from typing import Dict, Any
from modules.synthetic.personas import PERSONA_DEFINITIONS


# Stage classification based on persona key
_ROOKIE_PERSONAS = {
    "enthusiastic_rookie",
    "lazy_rookie",
    "snarky_rookie",
    "overwhelmed_rookie",
}

_MID_PERSONAS = {
    "workhorse",
    "ghoster_in_training",
    "burned_idealist",
    "social_glue",
    "emerging_leader",
}

_LONG_PERSONAS = {
    "quiet_pro",
    "cynical_anchor",
    "flight_risk_veteran",
}


def _get_stage(persona_key: str) -> str:
    """Return 'rookie', 'mid', or 'long' for a valid persona key."""
    if persona_key in _ROOKIE_PERSONAS:
        return "rookie"
    if persona_key in _MID_PERSONAS:
        return "mid"
    if persona_key in _LONG_PERSONAS:
        return "long"
    return "unknown"


def _same_stage(current_persona: str, candidate_persona: str) -> bool:
    """Return True if both personas belong to the same tenure stage."""
    return _get_stage(current_persona) == _get_stage(candidate_persona)


def evolve_persona(
    *,
    current_persona: str,
    tenure_days: int,
    rolling_mood_avg: float,
    rolling_stress_avg: float,
    rolling_energy_avg: float,
    rolling_fairness_avg: float,
) -> Dict[str, Any]:
    """
    Evaluate whether a staff member's persona should evolve based on tenure
    and long-term emotional trends.

    Args:
        current_persona: Current persona key (must exist in PERSONA_DEFINITIONS)
        tenure_days: Total days employed
        rolling_mood_avg: 0–10 rolling average mood
        rolling_stress_avg: 0–10 rolling average stress
        rolling_energy_avg: 0–10 rolling average energy
        rolling_fairness_avg: 0–10 rolling average perceived fairness

    Returns:
        dict with keys:
            - new_persona (str): either a new persona key or "exit"
            - changed (bool)
            - reason (str): human-readable explanation
    """
    # ------------------------------------------------------------------ #
    # 1. Safety checks
    # ------------------------------------------------------------------ #
    if current_persona not in PERSONA_DEFINITIONS:
        return {
            "new_persona": current_persona,
            "changed": False,
            "reason": f"Invalid current_persona '{current_persona}' – no change applied",
        }

    mood = rolling_mood_avg
    stress = rolling_stress_avg
    energy = rolling_energy_avg
    fairness = rolling_fairness_avg

    # ------------------------------------------------------------------ #
    # 2. Immediate churn / quit conditions (overrides everything)
    # ------------------------------------------------------------------ #
    if stress >= 8.5 and fairness <= 4.0:
        return {
            "new_persona": "exit",
            "changed": True,
            "reason": "extreme stress combined with severe perceived unfairness → quit",
        }
    if mood <= 3.0:
        return {
            "new_persona": "exit",
            "changed": True,
            "reason": "critically low mood → emotional breakdown / quit",
        }
    if energy <= 2.5:
        return {
            "new_persona": "exit",
            "changed": True,
            "reason": "complete energy depletion → burnout / quit",
        }

    current_stage = _get_stage(current_persona)

    # ------------------------------------------------------------------ #
    # 3. Rookie phase – no evolution before day 90
    # ------------------------------------------------------------------ #
    if current_stage == "rookie" and tenure_days < 90:
        return {
            "new_persona": current_persona,
            "changed": False,
            "reason": "still in rookie phase (tenure < 90 days)",
        }

    # ------------------------------------------------------------------ #
    # 4. Rookie → Mid-stage evolution (90 ≤ tenure < 365)
    # ------------------------------------------------------------------ #
    if current_stage == "rookie" and 90 <= tenure_days < 365:
        # Promotion paths – evaluated in priority order
        if mood >= 7.0 and energy >= 7.0 and stress <= 5.5 and fairness >= 6.5:
            return {
                "new_persona": "workhorse",
                "changed": True,
                "reason": "strong performance and satisfaction → promoted to workhorse",
            }
        if mood >= 7.8 and fairness >= 7.0 and energy >= 7.2:
            return {
                "new_persona": "social_glue",
                "changed": True,
                "reason": "high morale and strong team orientation → became social glue",
            }
        if mood >= 7.5 and fairness >= 7.5 and stress <= 5.5:
            return {
                "new_persona": "emerging_leader",
                "changed": True,
                "reason": "excellent mood and fairness perception → emerging leader",
            }
        # Devolution paths
        if stress >= 7.0 and energy <= 5.0 and fairness <= 5.5:
            return {
                "new_persona": "ghoster_in_training",
                "changed": True,
                "reason": "high stress and low energy → sliding toward ghosting behavior",
            }
        if stress >= 7.5 and mood <= 5.0 and fairness <= 5.0:
            return {
                "new_persona": "burned_idealist",
                "changed": True,
                "reason": "severe stress and disillusionment → became burned idealist",
            }

    # ------------------------------------------------------------------ #
    # 5. Mid → Long-stage evolution (tenure ≥ 365)
    # ------------------------------------------------------------------ #
    if current_stage == "mid" and tenure_days >= 365:
        if mood >= 7.0 and energy >= 7.0 and stress <= 5.0:
            return {
                "new_persona": "quiet_pro",
                "changed": True,
                "reason": "sustained high performance with low stress → matured into quiet pro",
            }
        if stress >= 6.5 and mood <= 5.0:
            return {
                "new_persona": "cynical_anchor",
                "changed": True,
                "reason": "chronic stress and low mood → settled into cynical anchor",
            }
        if stress >= 7.0 and fairness <= 4.0 and mood <= 5.0:
            return {
                "new_persona": "flight_risk_veteran",
                "changed": True,
                "reason": "very high stress and deep unfairness perception → became flight risk veteran",
            }

    # ------------------------------------------------------------------ #
    # 6. No evolution triggered
    # ------------------------------------------------------------------ #
    return {
        "new_persona": current_persona,
        "changed": False,
        "reason": "no evolution criteria met at current tenure and emotional state",
    }