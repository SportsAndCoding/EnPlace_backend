"""
modules/synthetic/en_place_effect.py

En Place Effect Engine v5 — With Life Event Baseline

THE ZERO-TURNOVER PROBLEM (v4):
  Post-EP showed 0% turnover for most restaurants because:
  1. Survivors at day 183 are disproportionately veterans (base_exit_prob=0.001)
  2. Low WITH_EP modifiers (0.08-0.15) on already-low base prob → essentially zero exits
  3. Zero exits → zero replacement hires → L2 unmeasurable → story broken

THE FIX — LIFE EVENT EXIT RATE:
  Even the best restaurant loses people to life changes: moving, going back
  to school, family emergency, career change, pregnancy, etc. These exits
  are UNAVOIDABLE and not influenced by En Place.

  life_event_daily_prob = 0.00085 → ~27% annual baseline turnover

  This represents the floor of unavoidable turnover. En Place reduces
  PREVENTABLE turnover (the other 73% of exits), not ALL turnover.

  Pre-EP: life events barely change the numbers (churn cycle dominates)
  Post-EP: life events create enough exits for realistic 35-50% annual + L2 measurability

  Maps directly to the En Place narrative:
    Industry baseline: 75-150% annual (depending on type)
    En Place network:  45-65% annual (unavoidable ~27% + some preventable)
    Long-term goal:    40% (theoretical floor at 33% unavoidable + 7%)

THREE LEVERS:

  1. EXIT MODIFIER — scales daily exit probability from persona_evolution.
     WITHOUT EP: type-specific modifier (industry baseline).
     WITH EP: ~20-25% lower (modest improvement on preventable exits).

  2. EMOTIONAL OFFSET — shifts felt_fair/respected/safe probabilities.
     Compounds through 30-day rolling window.

  3. STABLE HIRE — post-EP replacement hires use shifted persona weights.
     Screens out high-risk candidates. Breaks the 90-day cliff cycle.

  + LIFE EVENT BASELINE — flat daily exit probability representing
     unavoidable turnover. NOT modified by EP. Applied in runner.
"""

import hashlib
from typing import Dict, Any, Optional


# =====================================================================
# LIFE EVENT BASELINE
#
# Unavoidable exits that EP cannot prevent. Applied as a separate
# daily check in the simulation runner, independent of EP modifier.
#
# 0.00085/day → ~27% annual (1 - (1-0.00085)^365 = 0.267)
# This is the theoretical floor of turnover for any restaurant.
# =====================================================================

LIFE_EVENT_DAILY_PROB = 0.00085


# =====================================================================
# INDUSTRY BASELINE EXIT MULTIPLIERS (Without En Place)
#
# Applied to persona_evolution's daily exit probability.
# Combined with life events, produces industry-realistic annual rates.
#
# These drive the PREVENTABLE portion of turnover.
# Total pre-EP annual = life_events(~27%) + modified_churn(varies by type)
# =====================================================================

_WITHOUT_EP_EXIT_MULTIPLIERS: Dict[str, float] = {
    "fast_casual":        0.35,   # +churn → ~130-150% total
    "high_volume_chain":  0.28,   # +churn → ~100-120% total
    "college_town_cafe":  0.23,   # +churn → ~90-100% total
    "airport_restaurant": 0.20,   # +churn → ~80-90% total
    "sports_bar":         0.18,   # +churn → ~75-85% total
    "bar_and_grille":     0.17,   # +churn → ~73-80% total
    "hotel_restaurant":   0.16,   # +churn → ~70-78% total
    "upscale_casual":     0.15,   # +churn → ~68-75% total
    "family_diner":       0.15,   # +churn → ~65-75% total
    "breakfast_cafe":     0.15,   # +churn → ~65-75% total
    "neighborhood_bistro":0.14,   # +churn → ~60-70% total
    "steakhouse":         0.11,   # +churn → ~50-60% total
}

# =====================================================================
# EN PLACE NETWORK EXIT MULTIPLIERS (With En Place active)
#
# ~20-25% lower than WITHOUT modifier.
# Reduces PREVENTABLE exits. Life events still occur independently.
# Combined with Stable Hire on replacements → 45-65% total post-EP.
# =====================================================================

_WITH_EP_EXIT_MULTIPLIERS: Dict[str, float] = {
    "fast_casual":        0.27,
    "high_volume_chain":  0.22,
    "college_town_cafe":  0.18,
    "airport_restaurant": 0.16,
    "sports_bar":         0.14,
    "bar_and_grille":     0.13,
    "hotel_restaurant":   0.12,
    "upscale_casual":     0.12,
    "family_diner":       0.11,
    "breakfast_cafe":     0.11,
    "neighborhood_bistro":0.11,
    "steakhouse":         0.08,
}

# =====================================================================
# EMOTIONAL OFFSETS
# =====================================================================

_WITHOUT_EP_EMOTIONAL_OFFSET: Dict[str, float] = {
    "felt_fair_prob":      -0.06,
    "felt_respected_prob": -0.05,
    "felt_safe_prob":      -0.02,
}

_WITH_EP_EMOTIONAL_OFFSET: Dict[str, float] = {
    "felt_fair_prob":       0.08,
    "felt_respected_prob":  0.06,
    "felt_safe_prob":       0.03,
}

# =====================================================================
# STABLE HIRE PERSONA WEIGHTS
# =====================================================================

DEFAULT_PERSONA_WEIGHTS: Dict[str, float] = {
    "enthusiastic_rookie":   0.25,
    "lazy_rookie":           0.14,
    "snarky_rookie":         0.15,
    "overwhelmed_rookie":    0.10,
    "workhorse":             0.15,
    "social_glue":           0.05,
    "ghoster_in_training":   0.05,
    "burned_idealist":       0.05,
    "emerging_leader":       0.03,
    "quiet_pro":             0.01,
    "cynical_anchor":        0.01,
    "flight_risk_veteran":   0.01,
}

STABLE_HIRE_PERSONA_WEIGHTS: Dict[str, float] = {
    "enthusiastic_rookie":   0.32,
    "lazy_rookie":           0.10,
    "snarky_rookie":         0.10,
    "overwhelmed_rookie":    0.03,
    "workhorse":             0.22,
    "social_glue":           0.08,
    "ghoster_in_training":   0.01,
    "burned_idealist":       0.02,
    "emerging_leader":       0.06,
    "quiet_pro":             0.03,
    "cynical_anchor":        0.02,
    "flight_risk_veteran":   0.01,
}


# =====================================================================
# PER-RESTAURANT VOLATILITY
# =====================================================================

def _deterministic_variance(restaurant_id: int, salt: str, low: float, high: float) -> float:
    seed = hashlib.sha256(f"{restaurant_id}:{salt}".encode()).hexdigest()
    normalized = (int(seed[:12], 16) % 10000) / 10000.0
    return low + normalized * (high - low)


def _compute_restaurant_effectiveness(restaurant_id: int) -> float:
    mgmt = _deterministic_variance(restaurant_id, "mgmt_quality", 0.65, 1.35)
    adopt = _deterministic_variance(restaurant_id, "staff_adoption", 0.70, 1.30)
    culture = _deterministic_variance(restaurant_id, "culture_baseline", 0.80, 1.20)
    return max(0.60, min(1.50, mgmt * adopt * culture))


def _compute_industry_variance(restaurant_id: int) -> float:
    return _deterministic_variance(restaurant_id, "industry_variance", 0.80, 1.20)


# =====================================================================
# ADOPTION RAMP
# =====================================================================

def _adoption_ramp(days_since_adoption: int) -> float:
    if days_since_adoption <= 0:
        return 0.10
    elif days_since_adoption <= 7:
        return 0.10 + (0.20 * days_since_adoption / 7)
    elif days_since_adoption <= 14:
        return 0.30 + (0.25 * (days_since_adoption - 7) / 7)
    elif days_since_adoption <= 30:
        return 0.55 + (0.30 * (days_since_adoption - 14) / 16)
    elif days_since_adoption <= 60:
        return 0.85 + (0.15 * (days_since_adoption - 30) / 30)
    else:
        return 1.0


# =====================================================================
# PUBLIC API
# =====================================================================

def get_en_place_config(
    restaurant_id: int,
    profile_key: str,
    adoption_day: Optional[int] = None,
) -> Dict[str, Any]:
    if adoption_day is None:
        return {
            "adoption_day": None,
            "restaurant_effectiveness": 1.0,
            "industry_variance": 1.0,
            "without_ep": {"exit_modifier": 1.0, "emotional_offset": {}},
            "with_ep": {"exit_modifier": 1.0, "emotional_offset": {}},
            "stable_hire_weights": None,
            "life_event_daily_prob": LIFE_EVENT_DAILY_PROB,
        }

    effectiveness = _compute_restaurant_effectiveness(restaurant_id)
    industry_var = _compute_industry_variance(restaurant_id)

    without_base = _WITHOUT_EP_EXIT_MULTIPLIERS.get(profile_key, 0.18)
    with_base = _WITH_EP_EXIT_MULTIPLIERS.get(profile_key, 0.14)

    without_exit_mod = without_base * industry_var
    with_exit_mod = with_base / effectiveness
    with_exit_mod = max(0.05, min(without_exit_mod * 0.92, with_exit_mod))

    without_emotional = {
        k: round(v * industry_var, 4)
        for k, v in _WITHOUT_EP_EMOTIONAL_OFFSET.items()
    }
    with_emotional = {
        k: round(v * effectiveness, 4)
        for k, v in _WITH_EP_EMOTIONAL_OFFSET.items()
    }

    stable_hire = {}
    for persona in DEFAULT_PERSONA_WEIGHTS:
        default_w = DEFAULT_PERSONA_WEIGHTS[persona]
        ideal_w = STABLE_HIRE_PERSONA_WEIGHTS[persona]
        shift_pct = min(1.0, effectiveness)
        stable_hire[persona] = round(
            default_w + (ideal_w - default_w) * shift_pct, 4
        )

    return {
        "adoption_day": adoption_day,
        "restaurant_effectiveness": round(effectiveness, 3),
        "industry_variance": round(industry_var, 3),
        "without_ep": {
            "exit_modifier": round(without_exit_mod, 4),
            "emotional_offset": without_emotional,
        },
        "with_ep": {
            "exit_modifier": round(with_exit_mod, 4),
            "emotional_offset": with_emotional,
        },
        "stable_hire_weights": stable_hire,
        "life_event_daily_prob": LIFE_EVENT_DAILY_PROB,
    }


def get_daily_effect(
    en_place_config: Dict[str, Any],
    day_index: int,
) -> Dict[str, Any]:
    adoption_day = en_place_config.get("adoption_day")

    if adoption_day is None:
        return {
            "en_place_active": False,
            "exit_modifier": 1.0,
            "emotional_offset": {},
            "ramp_factor": 0.0,
        }

    without = en_place_config["without_ep"]
    with_ep = en_place_config["with_ep"]

    if day_index < adoption_day:
        return {
            "en_place_active": False,
            "exit_modifier": without["exit_modifier"],
            "emotional_offset": without["emotional_offset"],
            "ramp_factor": 0.0,
        }

    days_since = day_index - adoption_day
    ramp = _adoption_ramp(days_since)

    exit_mod = without["exit_modifier"] + ramp * (
        with_ep["exit_modifier"] - without["exit_modifier"]
    )

    emotional_offset = {}
    all_keys = set(
        list(without["emotional_offset"].keys()) +
        list(with_ep["emotional_offset"].keys())
    )
    for key in all_keys:
        wo_val = without["emotional_offset"].get(key, 0.0)
        we_val = with_ep["emotional_offset"].get(key, 0.0)
        emotional_offset[key] = wo_val + ramp * (we_val - wo_val)

    return {
        "en_place_active": True,
        "exit_modifier": round(exit_mod, 4),
        "emotional_offset": {k: round(v, 4) for k, v in emotional_offset.items()},
        "ramp_factor": round(ramp, 3),
    }