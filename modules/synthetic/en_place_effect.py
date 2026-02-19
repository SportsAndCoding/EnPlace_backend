"""
modules/synthetic/en_place_effect.py

En Place Effect Engine for the synthetic staffing simulation.

Computes the "with En Place" vs "without En Place" modifiers that create
the before/after story in the simulation data. Two levers:

1. EXIT PROBABILITY MODIFIER — multiplier applied to daily exit probability
   in persona_evolution.py (via the existing exit_probability_modifier param).

2. EMOTIONAL OFFSET — shifts to felt_fair_prob and felt_respected_prob baselines
   in daily_emotion_simulator.py. These compound over the 30-day rolling window
   and feed back into exit probability through the emotional_multiplier.

VOLATILITY: Each restaurant gets a deterministic "local effectiveness" score
derived from three independent variance axes:
  - management_quality: Does the manager actually use EP's recommendations?
  - staff_adoption: Do staff check in honestly via the anonymous system?
  - culture_baseline: Was the existing culture already decent or toxic?

These multiply together so some restaurants barely benefit from EP (bad manager
who ignores alerts) while others see dramatic improvement (engaged manager +
honest staff + culture that was almost good enough already).

COHORT TYPES:
  - "control"  : Never adopts EP. Runs with industry-baseline penalties all year.
  - "adopter"  : Starts without EP, adopts on a specified day. Shows the bend.
  - "day1"     : On EP from day 0. Best-case reference.

CALIBRATION TARGETS (aggregate, with variance):
  Without EP:  75-80% annual turnover (some restaurants 60%, some 95%+)
  With EP:     52-58% annual turnover (some 42%, some 68%)
  Long-term:   40% (33% unavoidable life changes + 7% wiggle room)

CALIBRATION v2 NOTES:
  Base simulation produces ~71% turnover at modifier 1.0.
  To hit 55% target, the effective exit modifier must be ~0.50-0.55 center.
  Previous v1 had WITH_EP multipliers at 0.64-0.73 → only reached 67%.
  v2 lowers base WITH_EP multipliers to 0.45-0.58 range.
  Effectiveness variance tightened: floor raised so even poorly-adopted
  EP restaurants still see meaningful benefit.
"""

import hashlib
from typing import Dict, Any, Optional


# =====================================================================
# INDUSTRY BASELINE EXIT MULTIPLIERS (Without En Place)
# Applied to the raw simulation's daily exit probability.
# These push turnover UP toward industry averages.
#
# v2: Slightly lowered from v1 to bring control from 82.9% → 77-80%.
# =====================================================================

_WITHOUT_EP_EXIT_MULTIPLIERS: Dict[str, float] = {
    "fast_casual":        1.45,   # Industry 110-130%
    "high_volume_chain":  1.35,   # Industry 95-105%
    "college_town_cafe":  1.22,   # Industry 85-95%
    "airport_restaurant": 1.18,   # Industry 78-85%
    "sports_bar":         1.12,   # Industry 75-80%
    "bar_and_grille":     1.10,   # Industry 75-80%
    "hotel_restaurant":   1.10,   # Industry 75-80%
    "family_diner":       1.08,   # Industry 75-80%
    "breakfast_cafe":     1.08,   # Industry 75-80%
    "neighborhood_bistro":1.05,   # Industry 70-80%
    "upscale_casual":     1.05,   # Industry 70-80%
    "steakhouse":         0.92,   # Industry 50-70% (fine dining retains better)
}

# =====================================================================
# EN PLACE NETWORK EXIT MULTIPLIERS (With En Place active)
# These push turnover DOWN toward mid-50s.
#
# v2: Lowered significantly. Base sim is 71% at 1.0 modifier.
# To reach 55% average, center of effective modifier must be ~0.50.
# These base values get further modified by per-restaurant effectiveness.
# =====================================================================

_WITH_EP_EXIT_MULTIPLIERS: Dict[str, float] = {
    "fast_casual":        0.52,   # Target 54-58% (from 110%+ baseline)
    "high_volume_chain":  0.54,   # Target 55-58%
    "college_town_cafe":  0.53,   # Target 54-57%
    "airport_restaurant": 0.56,   # Target 56-59%
    "sports_bar":         0.55,   # Target 55-58%
    "bar_and_grille":     0.52,   # Target 53-56%
    "hotel_restaurant":   0.50,   # Target 52-55%
    "family_diner":       0.47,   # Target 50-53%
    "breakfast_cafe":     0.47,   # Target 50-53%
    "neighborhood_bistro":0.45,   # Target 48-52%
    "upscale_casual":     0.48,   # Target 50-54%
    "steakhouse":         0.55,   # Target 45-50% (already lower base)
}

# =====================================================================
# EMOTIONAL OFFSETS
# Shifts to felt_fair_prob and felt_respected_prob in the emotion simulator.
# Without EP: staff don't feel heard → lower fairness/respect perception.
# With EP: anonymous check-ins + manager intervention → staff feel valued.
#
# v2: Boosted ~50% from v1. These compound through the 30-day rolling
# window and amplify the exit probability delta over time.
# =====================================================================

_WITHOUT_EP_EMOTIONAL_OFFSET: Dict[str, float] = {
    "felt_fair_prob":      -0.10,   # No anonymous feedback → less fairness
    "felt_respected_prob": -0.08,   # No voice → less respect
    "felt_safe_prob":      -0.04,   # Slight safety perception dip
}

_WITH_EP_EMOTIONAL_OFFSET: Dict[str, float] = {
    "felt_fair_prob":       0.09,   # Anonymous check-ins → feel heard
    "felt_respected_prob":  0.07,   # Manager follows up on signals → feel valued
    "felt_safe_prob":       0.03,   # House Guardian monitoring → slight safety boost
}


# =====================================================================
# PER-RESTAURANT VOLATILITY ENGINE
# =====================================================================

def _deterministic_variance(restaurant_id: int, salt: str, low: float, high: float) -> float:
    """
    Generate a deterministic variance factor for a restaurant.
    Returns a float in [low, high], uniformly distributed.
    Same restaurant_id + salt always produces the same value.
    """
    seed = hashlib.sha256(f"{restaurant_id}:{salt}".encode()).hexdigest()
    normalized = (int(seed[:12], 16) % 10000) / 10000.0  # 0.0 to 0.9999
    return low + normalized * (high - low)


def _compute_restaurant_effectiveness(restaurant_id: int) -> float:
    """
    Compute a per-restaurant "EP effectiveness" multiplier.

    Three independent axes (v2: tightened ranges, raised floors):
      management_quality (0.65 - 1.40): Does the GM use EP's recommendations?
      staff_adoption     (0.70 - 1.30): Do staff check in honestly?
      culture_baseline   (0.75 - 1.25): Was culture already decent?

    Product ranges from ~0.34 (worst case) to ~2.28 (best case).
    Centered around ~1.0 on average.

    v2: Clamped to [0.55, 1.6] (raised floor from 0.4).
    This ensures even poorly-adopted EP restaurants see real benefit.
    The worst EP restaurant should still noticeably outperform control.
    """
    mgmt = _deterministic_variance(restaurant_id, "mgmt_quality", 0.65, 1.40)
    adopt = _deterministic_variance(restaurant_id, "staff_adoption", 0.70, 1.30)
    culture = _deterministic_variance(restaurant_id, "culture_baseline", 0.75, 1.25)

    raw = mgmt * adopt * culture
    return max(0.55, min(1.6, raw))


def _compute_industry_variance(restaurant_id: int) -> float:
    """
    Compute per-restaurant variance for the WITHOUT EP (industry baseline) group.

    This represents natural variation: some restaurants have great leadership
    even without tools, some are disaster zones. Family businesses, cultural
    factors, local labor markets all play a role.

    Returns multiplier in [0.70, 1.30]:
      0.70 = naturally well-run (lower turnover than industry avg)
      1.00 = average
      1.30 = poorly managed (higher turnover than industry avg)

    v2: Narrowed from [0.70, 1.35] to [0.70, 1.30] to bring control
    aggregate down slightly from 82.9%.
    """
    return _deterministic_variance(restaurant_id, "industry_variance", 0.70, 1.30)


# =====================================================================
# ADOPTION RAMP
# EP doesn't flip on like a switch. There's a 30-day ramp as staff
# start checking in, managers learn the dashboard, signals accumulate.
# =====================================================================

def _adoption_ramp(days_since_adoption: int) -> float:
    """
    Returns 0.0 to 1.0 representing how much of the EP effect is active.

    Day 0:   0.10 (tool is installed, minimal data)
    Day 7:   0.30 (first week of check-ins, initial signals)
    Day 14:  0.55 (two weeks of data, patterns emerging)
    Day 30:  0.85 (rolling averages meaningful, manager trained)
    Day 60+: 1.00 (full effect)
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
    Generate the complete En Place effect configuration for a restaurant.

    Parameters
    ----------
    restaurant_id : int
        Unique restaurant ID (used for deterministic variance).
    profile_key : str
        Restaurant type key from restaurant_profiles.py.
    adoption_day : int or None
        Day index when EP activates.
        None  = no EP simulation (backward compatible, raw simulation).
        0     = EP from day 0 (day-1 network).
        N     = EP activates on day N (adopter).
        9999  = EP never activates within 365-day sim (control group).

    Returns
    -------
    dict with keys:
        adoption_day: int or None
        restaurant_effectiveness: float (per-restaurant EP effectiveness)
        industry_variance: float (per-restaurant baseline variance)
        without_ep: {exit_modifier: float, emotional_offset: dict}
        with_ep: {exit_modifier: float, emotional_offset: dict}
    """
    if adoption_day is None:
        # Backward compatible: no EP effect simulation
        return {
            "adoption_day": None,
            "restaurant_effectiveness": 1.0,
            "industry_variance": 1.0,
            "without_ep": {"exit_modifier": 1.0, "emotional_offset": {}},
            "with_ep": {"exit_modifier": 1.0, "emotional_offset": {}},
        }

    # Per-restaurant deterministic variance
    effectiveness = _compute_restaurant_effectiveness(restaurant_id)
    industry_var = _compute_industry_variance(restaurant_id)

    # Type-specific base multipliers
    without_base = _WITHOUT_EP_EXIT_MULTIPLIERS.get(profile_key, 1.10)
    with_base = _WITH_EP_EXIT_MULTIPLIERS.get(profile_key, 0.52)

    # Apply industry variance to the WITHOUT multiplier
    # A well-run restaurant without EP still has lower turnover than average
    without_exit_mod = without_base * industry_var

    # Apply effectiveness variance to the WITH multiplier
    # effectiveness > 1.0 = EP helps MORE (lower exit modifier = lower turnover)
    # effectiveness < 1.0 = EP helps LESS (modifier stays higher)
    #
    # v2: Direct scaling approach. The with_base is already calibrated
    # for effectiveness=1.0. We scale inversely:
    #   effectiveness 1.5 → modifier drops further (EP over-delivers)
    #   effectiveness 0.6 → modifier rises (EP under-delivers)
    #
    # Formula: with_exit_mod = with_base / effectiveness
    # This means effectiveness 0.55 → mod = with_base/0.55 (higher, less benefit)
    #          effectiveness 1.60 → mod = with_base/1.60 (lower, more benefit)
    with_exit_mod = with_base / effectiveness

    # Clamp: can't be worse than without, can't go below hard floor
    with_exit_mod = max(0.30, min(without_exit_mod * 0.92, with_exit_mod))

    # Emotional offsets with variance applied
    without_emotional = {
        k: v * industry_var for k, v in _WITHOUT_EP_EMOTIONAL_OFFSET.items()
    }
    with_emotional = {
        k: v * effectiveness for k, v in _WITH_EP_EMOTIONAL_OFFSET.items()
    }

    return {
        "adoption_day": adoption_day,
        "restaurant_effectiveness": round(effectiveness, 3),
        "industry_variance": round(industry_var, 3),
        "without_ep": {
            "exit_modifier": round(without_exit_mod, 4),
            "emotional_offset": {k: round(v, 4) for k, v in without_emotional.items()},
        },
        "with_ep": {
            "exit_modifier": round(with_exit_mod, 4),
            "emotional_offset": {k: round(v, 4) for k, v in with_emotional.items()},
        },
    }


def get_daily_effect(
    en_place_config: Dict[str, Any],
    day_index: int,
) -> Dict[str, Any]:
    """
    Get the EP effect for a specific simulation day.

    Handles the transition from without → with EP, including the
    adoption ramp-up period.

    Parameters
    ----------
    en_place_config : dict
        Output from get_en_place_config().
    day_index : int
        Current simulation day.

    Returns
    -------
    dict with:
        en_place_active: bool
        exit_modifier: float
        emotional_offset: dict
        ramp_factor: float (0-1, how much of EP effect is active)
    """
    adoption_day = en_place_config.get("adoption_day")

    # No EP simulation — return neutral values
    if adoption_day is None:
        return {
            "en_place_active": False,
            "exit_modifier": 1.0,
            "emotional_offset": {},
            "ramp_factor": 0.0,
        }

    without = en_place_config["without_ep"]
    with_ep = en_place_config["with_ep"]

    # Before adoption day — full industry baseline penalty
    if day_index < adoption_day:
        return {
            "en_place_active": False,
            "exit_modifier": without["exit_modifier"],
            "emotional_offset": without["emotional_offset"],
            "ramp_factor": 0.0,
        }

    # After adoption day — ramp up to full EP effect
    days_since = day_index - adoption_day
    ramp = _adoption_ramp(days_since)

    # Interpolate between without and with based on ramp
    exit_mod = without["exit_modifier"] + ramp * (with_ep["exit_modifier"] - without["exit_modifier"])

    emotional_offset = {}
    for key in set(list(without["emotional_offset"].keys()) + list(with_ep["emotional_offset"].keys())):
        wo_val = without["emotional_offset"].get(key, 0.0)
        we_val = with_ep["emotional_offset"].get(key, 0.0)
        emotional_offset[key] = wo_val + ramp * (we_val - wo_val)

    return {
        "en_place_active": True,
        "exit_modifier": round(exit_mod, 4),
        "emotional_offset": {k: round(v, 4) for k, v in emotional_offset.items()},
        "ramp_factor": round(ramp, 3),
    }