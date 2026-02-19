"""
calibration_run.py

Quick calibration: measures what the simulation produces at exit_modifier=1.0
WITH replacement hiring enabled. No contagion (fast). No Supabase.

Run: python calibration_run.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.synthetic.restaurant_profiles import get_profile
from modules.synthetic.restaurant_simulation_runner import simulate_restaurant

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

TYPES = [
    "fast_casual", "high_volume_chain", "sports_bar", "bar_and_grille",
    "college_town_cafe", "airport_restaurant", "hotel_restaurant",
    "upscale_casual", "family_diner", "breakfast_cafe",
    "neighborhood_bistro", "steakhouse",
]

HEADCOUNT = 50
DAYS = 365
ADOPTION_DAY = 183

print("=" * 70)
print("CALIBRATION RUN — modifier 1.0, replacement hiring ON, contagion OFF")
print("=" * 70)

for pkey in TYPES:
    profile = get_profile(pkey)
    results = simulate_restaurant(
        restaurant_id=999,
        number_of_staff=HEADCOUNT,
        simulation_days=DAYS,
        persona_weights=WEIGHTS,
        restaurant_profile=profile,
        enable_contagion=False,
        en_place_config=None,
        enable_replacement_hiring=True,
    )

    sm = results["staff_master"]
    total_records = len(sm)
    all_exits = sum(1 for s in sm if s["final_persona"] == "exit")

    # Split by period (pre day 183 vs post day 183)
    pre_exits = sum(1 for s in sm if s.get("exit_day") and s["exit_day"] <= ADOPTION_DAY)
    post_exits = sum(1 for s in sm if s.get("exit_day") and s["exit_day"] > ADOPTION_DAY)
    pre_ann = (pre_exits / HEADCOUNT) * (365 / 183) * 100
    post_ann = (post_exits / HEADCOUNT) * (365 / 182) * 100

    # Level 1: Original cohort (hire_day=0)
    originals = [s for s in sm if s["hire_day"] == 0]
    orig_exits = sum(1 for s in originals if s["final_persona"] == "exit")

    # Level 2: Replacements and 90-day cliff
    replacements = [s for s in sm if s["hire_day"] > 0]
    repl_under_90_exits = sum(
        1 for s in replacements
        if s["total_days"] < 90 and s["final_persona"] == "exit"
    )
    repl_survived_90 = sum(1 for s in replacements if s["total_days"] >= 90)

    # Level 3: Post-cliff retention
    repl_90plus_exits = sum(
        1 for s in replacements
        if s["total_days"] >= 90 and s["final_persona"] == "exit"
    )

    print(f"\n--- {pkey} ---")
    print(f"  Total records: {total_records}, Total exits: {all_exits}")
    print(f"  Pre-183 exits: {pre_exits} ({pre_ann:.0f}% ann), Post-183 exits: {post_exits} ({post_ann:.0f}% ann)")
    print(f"  L1 Originals: {len(originals)} hired, {orig_exits} exited ({orig_exits*100//len(originals)}%)")
    print(f"  L2 Replacements: {len(replacements)} hired, {repl_under_90_exits} failed <90d", end="")
    if replacements:
        print(f" ({repl_under_90_exits*100//len(replacements)}% cliff failure rate)")
    else:
        print()
    print(f"  L2 Survived 90d: {repl_survived_90}", end="")
    if replacements:
        print(f" ({repl_survived_90*100//len(replacements)}% cliff survival)")
    else:
        print()
    if repl_survived_90 > 0:
        print(f"  L3 Post-cliff exits: {repl_90plus_exits}/{repl_survived_90} ({repl_90plus_exits*100//repl_survived_90}%)")

print(f"\n{'='*70}")
print("Done. Use these numbers to calibrate en_place_effect.py multipliers.")
print(f"{'='*70}")