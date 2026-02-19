"""
modules/synthetic/en_place_effect.py

En Place Effect Engine v3 — Calibrated for Replacement Hiring

CALIBRATION BASIS (modifier 1.0, replacement hiring ON):
  - 50 staff, 365 days → 105 exits, 210% annual turnover
  - L1 originals: 76% exit (38/50)
  - L2 cliff failure: 52% of replacements exit within 90 days
  - L3 post-cliff: 27% of 90d survivors still exit

TWO LEVERS:

  1. EXIT PROBABILITY MODIFIER — multiplier on daily exit probability.
     WITHOUT EP: type-specific multiplier producing industry-realistic rates.
     WITH EP: lower multiplier producing En Place network rates.

  2. EMOTIONAL OFFSET — shifts to felt_fair/respected/safe probabilities.
     Compounds through the 30-day rolling window, creating persistent
     emotional climate improvement that reduces exit triggers.

THIRD LEVER (new in v3):

  3. STABLE HIRE PERSONA SHIFT — replacement hires made after EP adoption
     use different persona weights. Stable Hire screens out high-risk
     candidates (overwhelmed_rookie, ghoster_in_training) and favors
     resilient profiles (enthusiastic_rookie, workhorse). This directly
     attacks the 90-day cliff failure rate (Level 2).

COHORT DESIGN:
  All 100 restaurants adopt EP at day 183. Each restaurant is its own
  control — pre-adoption (days 0-182) vs post-adoption (days 183-365).

VOLATILITY:
  Per-restaurant variance via three deterministic axes ensures
  no two restaurants perform identically.

ESTIMATED MODIFIER-TO-TURNOVER MAPPING (non-linear due to churn cycle):
  modifier 1.00 → ~210% annual turnover
  modifier 0.80 → ~160%
  modifier 0.65 → ~120%
  modifier 0.55 → ~95%
  modifier 0.45 → ~75%
  modifier 0.35 → ~55%
  modifier 0.28 → ~42%
"""

import hashlib
from typing import Dict, Any, Optional


# =====================================================================
# INDUSTRY BASELINE EXIT MULTIPLIERS (Without En Place)
#
# These produce realistic industry turnover rates when applied to
# the base simulation WITH replacement hiring.
#
# Mapping: modifier → approximate annual turnover
#   0.80 → ~160%, 0.65 → ~120%, 0.55 → ~95%, 0.45 → ~75%, 0.35 → ~55%
# =====================================================================

_WITHOUT_EP_EXIT_MULTIPLIERS: Dict[str, float] = {
    "fast_casual":        0.78,   # Target ~150% (QSR industry avg)
    "high_volume_chain":  0.70,   # Target ~130%
    "college_town_cafe":  0.63,   # Target ~115% (seasonal + student workforce)
    "airport_restaurant": 0.55,   # Target ~95%
    "sports_bar":         0.50,   # Target ~85%
    "bar_and_grille":     0.49,   # Target ~83%
    "hotel_restaurant":   0.47,   # Target ~80%
    "upscale_casual":     0.45,   # Target ~75%
    "family_diner":       0.44,   # Target ~73%
    "breakfast_cafe":     0.44,   # Target ~73%
    "neighborhood_bistro":0.42,   # Target ~70%
    "steakhouse":         0.36,   # Target ~58% (fine dining retains better)
}

# =====================================================================
# EN PLACE NETWORK EXIT MULTIPLIERS (With En Place active)
#
# Target: 30-40% reduction from industry baseline.
# Combined with emotional offsets and Stable Hire, produces mid-50s
# aggregate across the network.
# =====================================================================

_WITH_EP_EXIT_MULTIPLIERS: Dict[str, float] = {
    "fast_casual":        0.50,   # 150% → ~85%  (43% reduction)
    "high_volume_chain":  0.45,   # 130% → ~75%  (42% reduction)
    "college_town_cafe":  0.42,   # 115% → ~68%  (41% reduction)
    "airport_restaurant": 0.38,   # 95%  → ~60%  (37% reduction)
    "sports_bar":         0.35,   # 85%  → ~55%  (35% reduction)
    "bar_and_grille":     0.34,   # 83%  → ~53%  (36% reduction)
    "hotel_restaurant":   0.32,   # 80%  → ~48%  (40% reduction)
    "upscale_casual":     0.31,   # 75%  → ~46%  (39% reduction)
    "family_diner":       0.30,   # 73%  → ~44%  (40% reduction)
    "breakfast_cafe":     0.30,   # 73%  → ~44%  (40% reduction)
    "neighborhood_bistro":0.28,   # 70%  → ~42%  (40% reduction)
    "steakhouse":         0.25,   # 58%  → ~36%  (38% reduction)
}

# =====================================================================
# EMOTIONAL OFFSETS
# Without EP: no feedback loop → staff feel less heard
# With EP: anonymous check-ins → staff feel valued
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
# Post-EP replacement hires use these shifted weights.
# Stable Hire psychological screening:
#   - Filters out high-risk profiles (overwhelmed, ghosters)
#   - Favors resilient, engaged candidates
#   - Directly reduces 90-day cliff failure rate (Level 2)
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
    "enthusiastic_rookie":   0.32,   # +0.07: better candidates found
    "lazy_rookie":           0.10,   # -0.04: some screened out
    "snarky_rookie":         0.10,   # -0.05: attitude flagged in screening
    "overwhelmed_rookie":    0.03,   # -0.07: anxiety/fit issues detected
    "workhorse":             0.22,   # +0.07: experienced hires prioritized
    "social_glue":           0.08,   # +0.03: team players identified
    "ghoster_in_training":   0.01,   # -0.04: reliability red flags caught
    "burned_idealist":       0.02,   # -0.03: burnout history detected
    "emerging_leader":       0.06,   # +0.03: leadership potential spotted
    "quiet_pro":             0.03,   # +0.02: steady performers valued
    "cynical_anchor":        0.02,   # +0.01: experience valued despite attitude
    "flight_risk_veteran":   0.01,   # same: hard to detect in screening
}


# =====================================================================
# PER-RESTAURANT VOLATILITY ENGINE
# =====================================================================

def _deterministic_variance(restaurant_id: int, salt: str, low: float, high: float) -> float:
    """Deterministic float in [low, high] for a restaurant + salt."""
    seed = hashlib.sha256(f"{restaurant_id}:{salt}".encode()).hexdigest()
    normalized = (int(seed[:12], 16) % 10000) / 10000.0
    return low + normalized * (high - low)


def _compute_restaurant_effectiveness(restaurant_id: int) -> float:
    """
    Per-restaurant EP effectiveness (three-axis variance).

    management_quality (0.65 - 1.35): Does the GM use EP recommendations?
    staff_adoption     (0.70 - 1.30): Do staff check in honestly?
    culture_baseline   (0.80 - 1.20): Was culture already decent?

    Clamped to [0.60, 1.50]. Floor ensures even poorly-adopted EP
    restaurants see meaningful benefit.
    """
    mgmt = _deterministic_variance(restaurant_id, "mgmt_quality", 0.65, 1.35)
    adopt = _deterministic_variance(restaurant_id, "staff_adoption", 0.70, 1.30)
    culture = _deterministic_variance(restaurant_id, "culture_baseline", 0.80, 1.20)
    raw = mgmt * adopt * culture
    return max(0.60, min(1.50, raw))


def _compute_industry_variance(restaurant_id: int) -> float:
    """
    Per-restaurant baseline variance (natural industry variation).
    Returns multiplier in [0.75, 1.25].
    """
    return _deterministic_variance(restaurant_id, "industry_variance", 0.75, 1.25)


# =====================================================================
# ADOPTION RAMP
# =====================================================================

def _adoption_ramp(days_since_adoption: int) -> float:
    """
    Returns 0.0 to 1.0 representing EP effect activation.
    Day 0: 0.10, Day 7: 0.30, Day 14: 0.55, Day 30: 0.85, Day 60+: 1.00
    """
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
    """
    Generate complete En Place effect configuration for a restaurant.

    Returns config dict with all parameters needed by the simulation runner.
    """
    if adoption_day is None:
        return {
            "adoption_day": None,
            "restaurant_effectiveness": 1.0,
            "industry_variance": 1.0,
            "without_ep": {"exit_modifier": 1.0, "emotional_offset": {}},
            "with_ep": {"exit_modifier": 1.0, "emotional_offset": {}},
            "stable_hire_weights": None,
        }

    effectiveness = _compute_restaurant_effectiveness(restaurant_id)
    industry_var = _compute_industry_variance(restaurant_id)

    # Type-specific base multipliers
    without_base = _WITHOUT_EP_EXIT_MULTIPLIERS.get(profile_key, 0.50)
    with_base = _WITH_EP_EXIT_MULTIPLIERS.get(profile_key, 0.35)

    # Apply industry variance to WITHOUT (natural variation)
    without_exit_mod = without_base * industry_var

    # Apply effectiveness to WITH (how well this restaurant uses EP)
    # effectiveness > 1.0 → EP over-delivers (lower modifier)
    # effectiveness < 1.0 → EP under-delivers (higher modifier)
    with_exit_mod = with_base / effectiveness

    # Clamp: can't be worse than without, can't go below 0.15
    with_exit_mod = max(0.15, min(without_exit_mod * 0.90, with_exit_mod))

    # Emotional offsets with variance
    without_emotional = {
        k: round(v * industry_var, 4)
        for k, v in _WITHOUT_EP_EMOTIONAL_OFFSET.items()
    }
    with_emotional = {
        k: round(v * effectiveness, 4)
        for k, v in _WITH_EP_EMOTIONAL_OFFSET.items()
    }

    # Stable Hire weights (effectiveness modulates how much the shift helps)
    # High effectiveness = more of the ideal shift applied
    # Low effectiveness = weights stay closer to default
    stable_hire = {}
    for persona in DEFAULT_PERSONA_WEIGHTS:
        default_w = DEFAULT_PERSONA_WEIGHTS[persona]
        ideal_w = STABLE_HIRE_PERSONA_WEIGHTS[persona]
        # Interpolate based on effectiveness (0.6 → 60% of shift, 1.5 → 100%)
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
    }


def get_daily_effect(
    en_place_config: Dict[str, Any],
    day_index: int,
) -> Dict[str, Any]:
    """
    Get the EP effect for a specific simulation day.
    Handles transition from without → with EP including adoption ramp.
    """
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

    # Before adoption — full industry baseline
    if day_index < adoption_day:
        return {
            "en_place_active": False,
            "exit_modifier": without["exit_modifier"],
            "emotional_offset": without["emotional_offset"],
            "ramp_factor": 0.0,
        }

    # After adoption — ramp up to full EP effect
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