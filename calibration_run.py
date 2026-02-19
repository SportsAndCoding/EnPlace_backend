"""
calibration_run.py v4

Tests the recalibrated low-range multipliers. All tests use SAME rid
to isolate modifier as only variable.

Part 1: Fine-grained modifier sweep in the 0.08-0.35 range
Part 2: WITHOUT vs WITH pairs at proposed type-specific values
Part 3: Stable Hire compound effect

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
RID = 7777


def make_ep(modifier):
    return {
        "adoption_day": 0,
        "restaurant_effectiveness": 1.0,
        "industry_variance": 1.0,
        "without_ep": {"exit_modifier": modifier, "emotional_offset": {}},
        "with_ep": {"exit_modifier": modifier, "emotional_offset": {}},
        "stable_hire_weights": None,
    }


def run(modifier, rid=RID, weights=WEIGHTS):
    profile = get_profile("fast_casual")
    results = simulate_restaurant(
        restaurant_id=rid,
        number_of_staff=HEADCOUNT,
        simulation_days=DAYS,
        persona_weights=weights,
        restaurant_profile=profile,
        enable_contagion=False,
        en_place_config=make_ep(modifier),
        enable_replacement_hiring=True,
    )
    sm = results["staff_master"]
    exits = sum(1 for s in sm if s["final_persona"] == "exit")
    annual = (exits / HEADCOUNT) * 100

    originals = [s for s in sm if s["hire_day"] == 0]
    orig_exits = sum(1 for s in originals if s["final_persona"] == "exit")

    replacements = [s for s in sm if s["hire_day"] > 0]
    elig = [s for s in replacements if s["hire_day"] + 90 <= DAYS]
    surv = sum(1 for s in elig if s["total_days"] >= 90)
    l2 = (surv / len(elig) * 100) if elig else 0

    return {"exits": exits, "annual": annual, "records": len(sm),
            "l1_exit": (orig_exits / 50 * 100), "l2_surv": l2}


# =====================================================
# PART 1: Fine-grained sweep
# =====================================================
print("=" * 70)
print(f"PART 1: Modifier sweep (rid={RID}, headcount={HEADCOUNT})")
print("=" * 70)

MODS = [0.08, 0.10, 0.11, 0.12, 0.14, 0.15, 0.17, 0.18,
        0.20, 0.23, 0.25, 0.28, 0.30, 0.35]

print(f"\n  {'Mod':>5} {'Exits':>6} {'Ann%':>6} {'L1 Exit%':>9} {'L2 Surv%':>9}")
print(f"  {'-'*5} {'-'*6} {'-'*6} {'-'*9} {'-'*9}")

for m in MODS:
    r = run(m)
    print(f"  {m:>5.2f} {r['exits']:>6} {r['annual']:>5.0f}% {r['l1_exit']:>8.0f}% {r['l2_surv']:>8.0f}%")


# =====================================================
# PART 2: Proposed WITHOUT/WITH pairs (same rid)
# =====================================================
print(f"\n{'='*70}")
print("PART 2: Proposed type multiplier pairs (same rid for each pair)")
print("=" * 70)

PAIRS = [
    ("fast_casual",        0.35, 0.27),
    ("high_volume_chain",  0.28, 0.22),
    ("college_town_cafe",  0.23, 0.18),
    ("airport_restaurant", 0.20, 0.16),
    ("sports_bar",         0.18, 0.14),
    ("bar_and_grille",     0.17, 0.13),
    ("hotel_restaurant",   0.16, 0.12),
    ("upscale_casual",     0.15, 0.12),
    ("family_diner",       0.15, 0.11),
    ("breakfast_cafe",     0.15, 0.11),
    ("neighborhood_bistro",0.14, 0.11),
    ("steakhouse",         0.11, 0.08),
]

print(f"\n  {'Type':<22} {'W/O':>5} {'Ann%':>6}  {'W/':>5} {'Ann%':>6} {'Delta':>6}")
print(f"  {'-'*22} {'-'*5} {'-'*6}  {'-'*5} {'-'*6} {'-'*6}")

for pkey, wo, wi in PAIRS:
    r_wo = run(wo)
    r_wi = run(wi)
    delta = r_wo["annual"] - r_wi["annual"]
    print(f"  {pkey:<22} {wo:>5.2f} {r_wo['annual']:>5.0f}%  {wi:>5.2f} {r_wi['annual']:>5.0f}% {delta:>+5.0f}")


# =====================================================
# PART 3: Stable Hire compound effect at different mods
# =====================================================
print(f"\n{'='*70}")
print("PART 3: Stable Hire impact at different modifier levels")
print("=" * 70)

from modules.synthetic.en_place_effect import STABLE_HIRE_PERSONA_WEIGHTS

SH_MODS = [0.27, 0.18, 0.12, 0.08]

print(f"\n  {'Mod':>5} {'Normal':>8} {'StabHire':>9} {'Delta':>6} {'L2 Norm':>8} {'L2 SH':>7}")
print(f"  {'-'*5} {'-'*8} {'-'*9} {'-'*6} {'-'*8} {'-'*7}")

for m in SH_MODS:
    r_norm = run(m, weights=WEIGHTS)
    r_sh = run(m, weights=STABLE_HIRE_PERSONA_WEIGHTS)
    delta = r_norm["annual"] - r_sh["annual"]
    print(f"  {m:>5.2f} {r_norm['annual']:>7.0f}% {r_sh['annual']:>8.0f}% {delta:>+5.0f} "
          f"{r_norm['l2_surv']:>7.0f}% {r_sh['l2_surv']:>6.0f}%")

print(f"\n{'='*70}")
print("Done.")
print(f"{'='*70}")