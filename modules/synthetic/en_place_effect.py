"""
modules/synthetic/en_place_effect.py

En Place Effect Engine v4 — Calibrated from Empirical Data

CALIBRATION BASIS (modifier 1.0, replacement hiring, same rid):
  Modifier → Annual Turnover (50 staff, 365 days, fast_casual):
    1.00 → 216%    0.50 → 144%    0.30 → 122%    0.20 → 90%
    0.80 → 198%    0.40 → 128%    0.25 → 108%

  Stable Hire effect at modifier 0.35:
    Normal weights → 128% annual, Stable Hire weights → 64% annual (50% reduction)

THREE LEVERS:

  1. EXIT MODIFIER — scales daily exit probability.
     WITHOUT EP: type-specific modifier producing realistic industry rates.
     WITH EP: ~20-25% lower modifier (modest improvement for originals/L3).

  2. EMOTIONAL OFFSET — shifts felt_fair/respected/safe probabilities.
     Compounds through 30-day rolling window. Provides L1 and L3 benefit.

  3. STABLE HIRE PERSONA WEIGHTS — post-EP replacement hires use shifted
     persona distribution. Screens out high-risk candidates (overwhelmed,
     ghosters), favors resilient profiles. This is the BIGGEST lever —
     directly breaks the 90-day cliff churn cycle (L2).

INDUSTRY BENCHMARKS (annual turnover incl replacement churn):
  QSR / Fast Casual:    130-150%
  High Volume Chain:    100-120%
  College Town / Cafe:   90-100%
  Airport:               80-90%
  Sports Bar:            75-85%
  Bar & Grille:          73-80%
  Hotel Restaurant:      70-78%
  Full-Service Casual:   68-75%
  Family / Breakfast:    65-75%
  Neighborhood Bistro:   60-70%
  Fine Dining:           50-60%
"""

import hashlib
from typing import Dict, Any, Optional


# =====================================================================
# INDUSTRY BASELINE EXIT MULTIPLIERS (Without En Place)
#
# Calibrated from empirical modifier-to-turnover curve.
# Interpolated targets with per-restaurant variance applied on top.
# =====================================================================

_WITHOUT_EP_EXIT_MULTIPLIERS: Dict[str, float] = {
    "fast_casual":        0.35,   # → ~128% (QSR/fast casual range)
    "high_volume_chain":  0.28,   # → ~115% (high volume chain)
    "college_town_cafe":  0.23,   # → ~100% (seasonal student workforce)
    "airport_restaurant": 0.20,   # → ~90%  (transient labor market)
    "sports_bar":         0.18,   # → ~83%  (bar/nightlife churn)
    "bar_and_grille":     0.17,   # → ~80%  (casual dining)
    "hotel_restaurant":   0.16,   # → ~77%  (hotel F&B)
    "upscale_casual":     0.15,   # → ~74%  (upscale casual)
    "family_diner":       0.15,   # → ~74%  (family dining)
    "breakfast_cafe":     0.15,   # → ~74%  (breakfast/brunch)
    "neighborhood_bistro":0.14,   # → ~70%  (neighborhood spot)
    "steakhouse":         0.11,   # → ~58%  (fine dining retains)
}

# =====================================================================
# EN PLACE NETWORK EXIT MULTIPLIERS (With En Place active)
#
# ~20-25% lower than WITHOUT_EP modifier.
# This provides modest L1 (original staff) and L3 (post-cliff) benefit.
# The heavy lifting on L2 (cliff survival) comes from Stable Hire.
# =====================================================================

_WITH_EP_EXIT_MULTIPLIERS: Dict[str, float] = {
    "fast_casual":        0.27,   # 23% reduction from 0.35
    "high_volume_chain":  0.22,   # 21% reduction from 0.28
    "college_town_cafe":  0.18,   # 22% reduction from 0.23
    "airport_restaurant": 0.16,   # 20% reduction from 0.20
    "sports_bar":         0.14,   # 22% reduction from 0.18
    "bar_and_grille":     0.13,   # 24% reduction from 0.17
    "hotel_restaurant":   0.12,   # 25% reduction from 0.16
    "upscale_casual":     0.12,   # 20% reduction from 0.15
    "family_diner":       0.11,   # 27% reduction from 0.15
    "breakfast_cafe":     0.11,   # 27% reduction from 0.15
    "neighborhood_bistro":0.11,   # 21% reduction from 0.14
    "steakhouse":         0.08,   # 27% reduction from 0.11
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
#
# The biggest lever. Calibration showed 50% reduction in annual turnover
# just from persona weight shift (128% → 64% at same modifier).
#
# Post-EP replacement hires use these weights. Stable Hire screens:
#   - overwhelmed_rookie: 10% → 3% (anxiety/fit issues detected)
#   - ghoster_in_training: 5% → 1% (reliability red flags caught)
#   - burned_idealist: 5% → 2% (burnout history detected)
#   - lazy_rookie: 14% → 10% (attitude flagged)
#   - snarky_rookie: 15% → 10% (attitude flagged)
#
# Favors resilient candidates:
#   - enthusiastic_rookie: 25% → 32% (engaged candidates found)
#   - workhorse: 15% → 22% (experienced hires prioritized)
#   - social_glue: 5% → 8% (team players identified)
#   - emerging_leader: 3% → 6% (leadership potential spotted)
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
    """
    Per-restaurant EP effectiveness. Three axes:
      management_quality (0.65-1.35), staff_adoption (0.70-1.30),
      culture_baseline (0.80-1.20). Clamped to [0.60, 1.50].
    """
    mgmt = _deterministic_variance(restaurant_id, "mgmt_quality", 0.65, 1.35)
    adopt = _deterministic_variance(restaurant_id, "staff_adoption", 0.70, 1.30)
    culture = _deterministic_variance(restaurant_id, "culture_baseline", 0.80, 1.20)
    return max(0.60, min(1.50, mgmt * adopt * culture))


def _compute_industry_variance(restaurant_id: int) -> float:
    """Per-restaurant baseline variance [0.80, 1.20]."""
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
        }

    effectiveness = _compute_restaurant_effectiveness(restaurant_id)
    industry_var = _compute_industry_variance(restaurant_id)

    without_base = _WITHOUT_EP_EXIT_MULTIPLIERS.get(profile_key, 0.18)
    with_base = _WITH_EP_EXIT_MULTIPLIERS.get(profile_key, 0.14)

    # Industry variance on WITHOUT (natural variation)
    without_exit_mod = without_base * industry_var

    # Effectiveness on WITH (how well this restaurant uses EP)
    # Higher effectiveness → lower modifier → better retention
    with_exit_mod = with_base / effectiveness

    # Clamp: WITH can't exceed WITHOUT, floor at 0.05
    with_exit_mod = max(0.05, min(without_exit_mod * 0.92, with_exit_mod))

    without_emotional = {
        k: round(v * industry_var, 4)
        for k, v in _WITHOUT_EP_EMOTIONAL_OFFSET.items()
    }
    with_emotional = {
        k: round(v * effectiveness, 4)
        for k, v in _WITH_EP_EMOTIONAL_OFFSET.items()
    }

    # Stable Hire weights scaled by effectiveness
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