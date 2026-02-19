"""
calibration_run.py v3

FIXED: Uses the SAME restaurant_id for all modifier comparisons.
The only variable is the exit_modifier itself — same staff, same personas,
same deterministic seed. This isolates the effect of the modifier.

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

HEADCOUNT = 50
DAYS = 365
FIXED_RID = 7777  # Same restaurant for all tests


def make_flat_ep_config(modifier):
    """Flat modifier applied from day 0 through entire sim."""
    return {
        "adoption_day": 0,
        "restaurant_effectiveness": 1.0,
        "industry_variance": 1.0,
        "without_ep": {"exit_modifier": modifier, "emotional_offset": {}},
        "with_ep": {"exit_modifier": modifier, "emotional_offset": {}},
        "stable_hire_weights": None,
    }


def run_with_modifier(modifier, rid=FIXED_RID, profile_key="fast_casual"):
    profile = get_profile(profile_key)
    results = simulate_restaurant(
        restaurant_id=rid,
        number_of_staff=HEADCOUNT,
        simulation_days=DAYS,
        persona_weights=WEIGHTS,
        restaurant_profile=profile,
        enable_contagion=False,
        en_place_config=make_flat_ep_config(modifier),
        enable_replacement_hiring=True,
    )
    sm = results["staff_master"]
    total = len(sm)
    exits = sum(1 for s in sm if s["final_persona"] == "exit")
    annual = (exits / HEADCOUNT) * 100

    originals = [s for s in sm if s["hire_day"] == 0]
    orig_exits = sum(1 for s in originals if s["final_persona"] == "exit")
    l1 = (orig_exits / len(originals) * 100) if originals else 0

    replacements = [s for s in sm if s["hire_day"] > 0]
    eligible = [s for s in replacements if s["hire_day"] + 90 <= DAYS]
    survived = sum(1 for s in eligible if s["total_days"] >= 90)
    l2 = (survived / len(eligible) * 100) if eligible else 0

    cliff_surv = [s for s in eligible if s["total_days"] >= 90]
    cliff_exit = sum(1 for s in cliff_surv if s["final_persona"] == "exit")
    l3 = ((len(cliff_surv) - cliff_exit) / len(cliff_surv) * 100) if cliff_surv else 0

    return {
        "exits": exits, "annual": annual, "records": total,
        "l1_exit_pct": l1, "l2_cliff_survival": l2, "l3_retention": l3,
    }


# =====================================================
# PART 1: Modifier sweep (same rid for all)
# =====================================================
print("=" * 80)
print("PART 1: Modifier → Turnover (same restaurant, same staff, only modifier changes)")
print(f"  rid={FIXED_RID}, profile=fast_casual, headcount={HEADCOUNT}")
print("=" * 80)

MODIFIERS = [1.0, 0.80, 0.65, 0.50, 0.40, 0.35, 0.30, 0.25, 0.20]

print(f"\n  {'Mod':>6} {'Exits':>6} {'Ann%':>6} {'L1 Exit%':>9} {'L2 Cliff%':>10} {'L3 Retain%':>11} {'Records':>8}")
print(f"  {'-'*6} {'-'*6} {'-'*6} {'-'*9} {'-'*10} {'-'*11} {'-'*8}")

for mod in MODIFIERS:
    r = run_with_modifier(mod)
    print(f"  {mod:>6.2f} {r['exits']:>6} {r['annual']:>5.0f}% {r['l1_exit_pct']:>8.0f}% "
          f"{r['l2_cliff_survival']:>9.0f}% {r['l3_retention']:>10.0f}% {r['records']:>8}")


# =====================================================
# PART 2: Type-specific spot checks (same rid per type)
# =====================================================
print(f"\n{'='*80}")
print("PART 2: Without vs With EP by type (same rid per type)")
print("=" * 80)

SPOT_CHECKS = [
    ("fast_casual",        0.78, 0.50),
    ("high_volume_chain",  0.70, 0.45),
    ("college_town_cafe",  0.63, 0.42),
    ("airport_restaurant", 0.55, 0.38),
    ("sports_bar",         0.50, 0.35),
    ("bar_and_grille",     0.49, 0.34),
    ("hotel_restaurant",   0.47, 0.32),
    ("upscale_casual",     0.45, 0.31),
    ("family_diner",       0.44, 0.30),
    ("breakfast_cafe",     0.44, 0.30),
    ("neighborhood_bistro",0.42, 0.28),
    ("steakhouse",         0.36, 0.25),
]

print(f"\n  {'Type':<22} {'W/O':>5} {'Exits':>6} {'Ann%':>6}  {'W/':>5} {'Exits':>6} {'Ann%':>6} {'Delta':>6}")
print(f"  {'-'*22} {'-'*5} {'-'*6} {'-'*6}  {'-'*5} {'-'*6} {'-'*6} {'-'*6}")

for pkey, wo_mod, w_mod in SPOT_CHECKS:
    # SAME rid for both — only modifier changes
    rid = 8000 + hash(pkey) % 1000

    wo = run_with_modifier(wo_mod, rid=rid, profile_key=pkey)
    wi = run_with_modifier(w_mod, rid=rid, profile_key=pkey)

    delta = wo["annual"] - wi["annual"]
    print(f"  {pkey:<22} {wo_mod:>5.2f} {wo['exits']:>6} {wo['annual']:>5.0f}%  "
          f"{w_mod:>5.2f} {wi['exits']:>6} {wi['annual']:>5.0f}% {delta:>+5.0f}")


# =====================================================
# PART 3: L2 Stable Hire effect preview
# =====================================================
print(f"\n{'='*80}")
print("PART 3: Stable Hire effect on cliff survival (same rid, with_mod=0.35)")
print("=" * 80)

from modules.synthetic.en_place_effect import STABLE_HIRE_PERSONA_WEIGHTS

# Without Stable Hire: normal weights at modifier 0.35
r_normal = run_with_modifier(0.35, rid=FIXED_RID)

# With Stable Hire: use stable hire weights for ALL hires to see max effect
profile = get_profile("fast_casual")
results_sh = simulate_restaurant(
    restaurant_id=FIXED_RID,
    number_of_staff=HEADCOUNT,
    simulation_days=DAYS,
    persona_weights=STABLE_HIRE_PERSONA_WEIGHTS,  # Stable Hire weights for ALL
    restaurant_profile=profile,
    enable_contagion=False,
    en_place_config=make_flat_ep_config(0.35),
    enable_replacement_hiring=True,
)
sm_sh = results_sh["staff_master"]
sh_exits = sum(1 for s in sm_sh if s["final_persona"] == "exit")
sh_annual = (sh_exits / HEADCOUNT) * 100
eligible_sh = [s for s in sm_sh if s["hire_day"] > 0 and s["hire_day"] + 90 <= DAYS]
surv_sh = sum(1 for s in eligible_sh if s["total_days"] >= 90)
l2_sh = (surv_sh / len(eligible_sh) * 100) if eligible_sh else 0

print(f"\n  Normal weights @ 0.35:      {r_normal['annual']:.0f}% annual, "
      f"L2 cliff survival: {r_normal['l2_cliff_survival']:.0f}%")
print(f"  Stable Hire weights @ 0.35: {sh_annual:.0f}% annual, "
      f"L2 cliff survival: {l2_sh:.0f}%")
print(f"  Stable Hire impact:         {r_normal['annual'] - sh_annual:+.0f} pts annual, "
      f"{l2_sh - r_normal['l2_cliff_survival']:+.0f} pts cliff survival")

print(f"\n{'='*80}")
print("Done.")
print(f"{'='*80}")