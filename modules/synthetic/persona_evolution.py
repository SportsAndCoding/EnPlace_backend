"""
modules/synthetic/persona_evolution.py

Persona Evolution Engine for the En Place synthetic staffing simulator.
Determines whether a staff member's underlying persona changes based on
tenure and long-term emotional trends.

UPDATED: Now works with organic-matching schema:
- mood: 1-5 scale (rolling average)
- safe_rate: 0-1 (percentage of days felt safe)
- fair_rate: 0-1 (percentage of days felt fair)
- respected_rate: 0-1 (percentage of days felt respected)

Target: ~55% annual turnover, 66% within 90-day cliff, 34% after.
"""

import hashlib
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

# Tenure bucket definitions with base daily exit probabilities
# Mood thresholds are now on 1-5 scale
_TENURE_EXIT_CONFIG = {
    # Days 0-7: "First Week Shock" - immediate mismatch detection
    "first_week": {
        "range": (0, 7),
        "base_exit_prob": 0.008,
        "mood_threshold": 2.5,      # On 1-5 scale
        "respect_weight": 0.3,      # Less about respect early, more about fit
    },
    # Days 8-14: "Reality Check" - honeymoon ends
    "reality_check": {
        "range": (8, 14),
        "base_exit_prob": 0.012,
        "mood_threshold": 2.3,
        "respect_weight": 0.5,
    },
    # Days 15-30: "Sink or Swim" - make or break period
    "sink_or_swim": {
        "range": (15, 30),
        "base_exit_prob": 0.010,
        "mood_threshold": 2.2,
        "respect_weight": 0.6,
    },
    # Days 31-60: "Proving Ground" - building or breaking
    "proving_ground": {
        "range": (31, 60),
        "base_exit_prob": 0.006,
        "mood_threshold": 2.0,
        "respect_weight": 0.7,
    },
    # Days 61-90: "The Cliff" - critical decision point
    "the_cliff": {
        "range": (61, 90),
        "base_exit_prob": 0.008,
        "mood_threshold": 2.0,
        "respect_weight": 0.8,
    },
    # Days 91-120: "Stabilizing" - if they made it this far...
    "stabilizing": {
        "range": (91, 120),
        "base_exit_prob": 0.003,
        "mood_threshold": 1.8,
        "respect_weight": 0.85,
    },
    # Days 121-180: "Established" - lower but still present risk
    "established": {
        "range": (121, 180),
        "base_exit_prob": 0.002,
        "mood_threshold": 1.7,
        "respect_weight": 0.9,
    },
    # Days 180+: "Veteran" - external factors dominate
    "veteran": {
        "range": (181, 9999),
        "base_exit_prob": 0.001,
        "mood_threshold": 1.5,
        "respect_weight": 0.9,
    },
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


def _get_tenure_bucket(tenure_days: int) -> Dict[str, Any]:
    """Return the tenure configuration for the given day count."""
    for bucket_name, config in _TENURE_EXIT_CONFIG.items():
        start, end = config["range"]
        if start <= tenure_days <= end:
            return config
    return _TENURE_EXIT_CONFIG["veteran"]


def _deterministic_random(staff_id: str, tenure_days: int, salt: str = "") -> float:
    """Generate a deterministic random float [0, 1) based on staff_id and day."""
    seed_str = f"{staff_id}:{tenure_days}:{salt}"
    hash_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    return (hash_val % 1_000_000) / 1_000_000


def _calculate_exit_probability(
    *,
    staff_id: str,
    current_persona: str,
    tenure_days: int,
    mood: float,           # 1-5 scale
    safe_rate: float,      # 0-1 (% days felt safe)
    fair_rate: float,      # 0-1 (% days felt fair)
    respected_rate: float, # 0-1 (% days felt respected)
) -> tuple[float, str]:
    """
    Calculate the probability of exit based on tenure, persona, and emotional state.
    Returns (probability, reason).
    """
    bucket = _get_tenure_bucket(tenure_days)
    base_prob = bucket["base_exit_prob"]
    mood_threshold = bucket["mood_threshold"]
    respect_weight = bucket["respect_weight"]
    
    # Get persona sensitivities
    persona_def = PERSONA_DEFINITIONS.get(current_persona, {})
    fairness_sensitivity = persona_def.get("fairness_sensitivity", 0.5)
    respect_sensitivity = persona_def.get("respect_sensitivity", 0.5)
    safety_sensitivity = persona_def.get("safety_sensitivity", 0.5)
    
    # Calculate risk factors (all produce 0-1 where higher = more risk)
    # Mood risk: how far below threshold (scaled for 1-5 range)
    mood_risk = max(0, (mood_threshold - mood) / mood_threshold) if mood < mood_threshold else 0
    
    # Boolean field risks: invert the rates (low rate = high risk)
    safe_risk = max(0, (0.7 - safe_rate) / 0.7) if safe_rate < 0.7 else 0
    fair_risk = max(0, (0.6 - fair_rate) / 0.6) if fair_rate < 0.6 else 0
    respect_risk = max(0, (0.6 - respected_rate) / 0.6) if respected_rate < 0.6 else 0
    
    # Combine risks with persona sensitivities
    emotional_multiplier = 1.0 + (
        mood_risk * 2.0 +
        safe_risk * safety_sensitivity * 1.5 +
        fair_risk * fairness_sensitivity * 1.5 +
        respect_risk * respect_sensitivity * respect_weight * 2.0
    )
    
    # Persona-specific multipliers
    persona_multiplier = {
        "burned_idealist": 2.5,
        "flight_risk_veteran": 2.0,
        "ghoster_in_training": 1.8,
        "overwhelmed_rookie": 1.6,
        "lazy_rookie": 1.4,
        "snarky_rookie": 1.2,
        "cynical_anchor": 0.6,
        "quiet_pro": 0.4,
        "workhorse": 0.5,
        "social_glue": 0.6,
        "emerging_leader": 0.5,
        "enthusiastic_rookie": 0.8,
    }.get(current_persona, 1.0)
    
    final_prob = base_prob * emotional_multiplier * persona_multiplier
    final_prob = min(0.15, final_prob)  # Cap at 15% daily exit probability
    
    # Determine primary reason
    if mood_risk > 0.5:
        reason = "critically low mood - emotional breakdown"
    elif respect_risk > 0.5:
        reason = "felt disrespected - dignity issue"
    elif fair_risk > 0.5:
        reason = "felt treated unfairly - resentment"
    elif safe_risk > 0.5:
        reason = "didn't feel safe - toxic environment"
    elif tenure_days <= 14:
        reason = "early mismatch - job not as expected"
    elif tenure_days <= 30:
        reason = "reality check failed - couldn't adapt"
    elif tenure_days <= 90:
        reason = "didn't find their place - never integrated"
    else:
        reason = "accumulated frustration - found better opportunity"
    
    return final_prob, reason


def evolve_persona(
    *,
    current_persona: str,
    tenure_days: int,
    rolling_mood: float,        # 1-5 average
    rolling_safe_rate: float,   # 0-1
    rolling_fair_rate: float,   # 0-1
    rolling_respected_rate: float,  # 0-1
    staff_id: str = "unknown",
) -> Dict[str, Any]:
    """
    Evaluate whether a staff member's persona should evolve based on tenure
    and long-term emotional trends.

    Args:
        current_persona: Current persona key (must exist in PERSONA_DEFINITIONS)
        tenure_days: Total days employed
        rolling_mood: 1-5 rolling average mood
        rolling_safe_rate: 0-1 percentage of days felt safe
        rolling_fair_rate: 0-1 percentage of days felt fair
        rolling_respected_rate: 0-1 percentage of days felt respected
        staff_id: Staff identifier for deterministic randomness

    Returns:
        dict with keys:
            - new_persona (str): either a new persona key or "exit"
            - changed (bool)
            - reason (str): human-readable explanation
    """
    # Safety checks
    if current_persona not in PERSONA_DEFINITIONS:
        return {
            "new_persona": current_persona,
            "changed": False,
            "reason": f"Invalid current_persona '{current_persona}' - no change applied",
        }

    mood = rolling_mood
    safe_rate = rolling_safe_rate
    fair_rate = rolling_fair_rate
    respected_rate = rolling_respected_rate

    # ------------------------------------------------------------------ #
    # Immediate/guaranteed exit conditions (very extreme)
    # ------------------------------------------------------------------ #
    if mood <= 1.3:
        return {
            "new_persona": "exit",
            "changed": True,
            "reason": "critically low mood - emotional breakdown / quit",
        }
    if respected_rate <= 0.2 and fair_rate <= 0.3:
        return {
            "new_persona": "exit",
            "changed": True,
            "reason": "severe disrespect and unfairness - immediate quit",
        }
    if safe_rate <= 0.3:
        return {
            "new_persona": "exit",
            "changed": True,
            "reason": "consistently unsafe environment - quit for wellbeing",
        }

    # ------------------------------------------------------------------ #
    # Probabilistic exit based on tenure and emotional state
    # ------------------------------------------------------------------ #
    exit_prob, exit_reason = _calculate_exit_probability(
        staff_id=staff_id,
        current_persona=current_persona,
        tenure_days=tenure_days,
        mood=mood,
        safe_rate=safe_rate,
        fair_rate=fair_rate,
        respected_rate=respected_rate,
    )
    
    roll = _deterministic_random(staff_id, tenure_days, "exit_check")
    if roll < exit_prob:
        return {
            "new_persona": "exit",
            "changed": True,
            "reason": exit_reason,
        }

    current_stage = _get_stage(current_persona)

    # ------------------------------------------------------------------ #
    # Rookie -> Mid-stage evolution (tenure >= 30)
    # ------------------------------------------------------------------ #
    if current_stage == "rookie" and tenure_days >= 30:
        # Promotion paths
        if mood >= 4.0 and safe_rate >= 0.85 and fair_rate >= 0.80 and respected_rate >= 0.80:
            return {
                "new_persona": "workhorse",
                "changed": True,
                "reason": "strong performance and satisfaction - promoted to workhorse",
            }
        if mood >= 4.2 and fair_rate >= 0.85 and respected_rate >= 0.85:
            return {
                "new_persona": "social_glue",
                "changed": True,
                "reason": "high morale and team orientation - became social glue",
            }
        if mood >= 4.0 and fair_rate >= 0.85 and safe_rate >= 0.90:
            return {
                "new_persona": "emerging_leader",
                "changed": True,
                "reason": "excellent mood and fairness perception - emerging leader",
            }
        # Devolution paths
        if mood <= 2.8 and respected_rate <= 0.55 and fair_rate <= 0.55:
            return {
                "new_persona": "ghoster_in_training",
                "changed": True,
                "reason": "low mood and disrespect - sliding toward ghosting behavior",
            }
        if mood <= 2.5 and fair_rate <= 0.45:
            return {
                "new_persona": "burned_idealist",
                "changed": True,
                "reason": "severe unfairness and low mood - became burned idealist",
            }

    # ------------------------------------------------------------------ #
    # Mid -> Long-stage evolution (tenure >= 180)
    # ------------------------------------------------------------------ #
    if current_stage == "mid" and tenure_days >= 180:
        if mood >= 3.8 and safe_rate >= 0.90 and fair_rate >= 0.80:
            return {
                "new_persona": "quiet_pro",
                "changed": True,
                "reason": "sustained high performance - matured into quiet pro",
            }
        if mood <= 2.8 and fair_rate <= 0.55:
            return {
                "new_persona": "cynical_anchor",
                "changed": True,
                "reason": "chronic low mood and unfairness - settled into cynical anchor",
            }
        if mood <= 2.5 and fair_rate <= 0.40 and respected_rate <= 0.45:
            return {
                "new_persona": "flight_risk_veteran",
                "changed": True,
                "reason": "deep unfairness and disrespect - became flight risk veteran",
            }

    # ------------------------------------------------------------------ #
    # No evolution triggered
    # ------------------------------------------------------------------ #
    return {
        "new_persona": current_persona,
        "changed": False,
        "reason": "no evolution criteria met at current tenure and emotional state",
    }