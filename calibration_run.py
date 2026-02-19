"""
calibration_run.py v2

Tests multiple exit_modifier values to map modifier → turnover rate
with replacement hiring enabled. Uses unique restaurant_id per test.

This tells us exactly where the WITHOUT_EP and WITH_EP multipliers
need to land in en_place_effect.py.

Run: python calibration_run.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.synthetic.restaurant_profiles import get_profile
from modules.synthetic.restaurant_simulation_runner import simulate_restaurant
from modules.synthetic.en_place_effect import get_daily_effect

WEIGHTS = {
    "enthusiastic_rookie": 0.25,
    "lazy_rookie": 0.14,
    "snarky_rookie": 0.15,
    "overwhelmed_rookie": 0.10,
    "workhorse": 0.15,
    "social_glue": 0.05,
    "ghoster_in_training": 0.05,
    "burned_idealist": 0.05,
    "emerging_leader": 0.03,
    "quiet_pro": 0.01,
    "cynical_anchor": 0.01,
    "flight_risk_veteran": 0.01,
}

HEADCOUNT = 50
DAYS = 365

# Test these modifier values
MODIFIERS = [1.0, 0.80, 0.65, 0.50, 0.40, 0.30, 0.20]

# Use fast_casual as reference type
PROFILE_KEY = "fast_casual"


def make_flat_ep_config(modifier: float) -> dict:
    """Create a minimal EP config that applies a flat exit modifier all year."""
    return {
        "adoption_day": 0,  # Active from day 0
        "restaurant_effectiveness": 1.0,
        "industry_variance": 1.0,
        "without_ep": {
            "exit_modifier": modifier,
            "emotional_offset": {},
        },
        "with_ep": {
            "exit_modifier": modifier,
            "emotional_offset": {},
        },
        "stable_hire_weights": None,
    }


print("=" * 80)
print("CALIBRATION: modifier → turnover mapping (replacement hiring ON, contagion OFF)")
print("=" * 80)
print(f"Profile: {PROFILE_KEY}, Headcount: {HEADCOUNT}, Days: {DAYS}")
print()

print(f"  {'Modifier':>8} {'Exits':>6} {'Annual%':>8} {'L1 Exit%':>9} {'L2 Cliff%':>10} {'L3 PostCliff%':>14} {'Records':>8}")
print(f"  {'-'*8} {'-'*6} {'-'*8} {'-'*9} {'-'*10} {'-'*14} {'-'*8}")

profile = get_profile(PROFILE_KEY)

for mod in MODIFIERS:
    # Use different restaurant_id per modifier for variety
    rid = 5000 + int(mod * 100)

    ep_config = make_flat_ep_config(mod)

    results = simulate_restaurant(
        restaurant_id=rid,
        number_of_staff=HEADCOUNT,
        simulation_days=DAYS,
        persona_weights=WEIGHTS,
        restaurant_profile=profile,
        enable_contagion=False,
        en_place_config=ep_config,
        enable_replacement_hiring=True,
    )

    sm = results["staff_master"]
    total_records = len(sm)
    all_exits = sum(1 for s in sm if s["final_persona"] == "exit")
    annual_pct = (all_exits / HEADCOUNT) * 100

    # L1: originals
    originals = [s for s in sm if s["hire_day"] == 0]
    orig_exits = sum(1 for s in originals if s["final_persona"] == "exit")
    l1_pct = (orig_exits / len(originals) * 100) if originals else 0

    # L2: replacement cliff survival
    replacements = [s for s in sm if s["hire_day"] > 0]
    repl_eligible = [s for s in replacements if s["hire_day"] + 90 <= DAYS]
    repl_survived = sum(1 for s in repl_eligible if s["total_days"] >= 90)
    l2_pct = (repl_survived / len(repl_eligible) * 100) if repl_eligible else 0

    # L3: post-cliff exits
    cliff_survivors = [s for s in repl_eligible if s["total_days"] >= 90]
    cliff_then_exit = sum(1 for s in cliff_survivors if s["final_persona"] == "exit")
    l3_pct = ((len(cliff_survivors) - cliff_then_exit) / len(cliff_survivors) * 100) if cliff_survivors else 0

    print(f"  {mod:>8.2f} {all_exits:>6} {annual_pct:>7.0f}% {l1_pct:>8.0f}% {l2_pct:>9.0f}% {l3_pct:>13.0f}% {total_records:>8}")

print()

# Now test a few types at the proposed WITHOUT_EP and WITH_EP modifiers
print("=" * 80)
print("SPOT CHECK: Proposed multipliers by restaurant type")
print("=" * 80)

SPOT_CHECKS = [
    ("fast_casual",        0.78, 0.50),
    ("high_volume_chain",  0.70, 0.45),
    ("sports_bar",         0.50, 0.35),
    ("steakhouse",         0.36, 0.25),
    ("family_diner",       0.44, 0.30),
    ("neighborhood_bistro",0.42, 0.28),
]

print(f"  {'Type':<22} {'W/O EP':>7} {'Exits':>6} {'Ann%':>6} {'W/ EP':>7} {'Exits':>6} {'Ann%':>6} {'Delta':>6}")
print(f"  {'-'*22} {'-'*7} {'-'*6} {'-'*6} {'-'*7} {'-'*6} {'-'*6} {'-'*6}")

for pkey, without_mod, with_mod in SPOT_CHECKS:
    prof = get_profile(pkey)

    for label, mod in [("without", without_mod), ("with", with_mod)]:
        rid = 6000 + int(mod * 1000) + hash(pkey) % 100
        ep_config = make_flat_ep_config(mod)
        results = simulate_restaurant(
            restaurant_id=rid,
            number_of_staff=HEADCOUNT,
            simulation_days=DAYS,
            persona_weights=WEIGHTS,
            restaurant_profile=prof,
            enable_contagion=False,
            en_place_config=ep_config,
            enable_replacement_hiring=True,
        )
        sm = results["staff_master"]
        exits = sum(1 for s in sm if s["final_persona"] == "exit")
        ann = (exits / HEADCOUNT) * 100

        if label == "without":
            wo_exits, wo_ann = exits, ann
        else:
            w_exits, w_ann = exits, ann

    delta = wo_ann - w_ann
    print(f"  {pkey:<22} {without_mod:>7.2f} {wo_exits:>6} {wo_ann:>5.0f}% {with_mod:>7.2f} {w_exits:>6} {w_ann:>5.0f}% {delta:>+5.0f}")

print(f"\n{'='*80}")
print("Done. Adjust en_place_effect.py multipliers based on these results.")
print(f"{'='*80}")