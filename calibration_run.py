"""
calibration_run.py v5

Now includes LIFE_EVENT_DAILY_PROB in all tests.
Shows the combined effect of modifier + life events.

Run: python calibration_run.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.synthetic.restaurant_profiles import get_profile
from modules.synthetic.restaurant_simulation_runner import simulate_restaurant
from modules.synthetic.en_place_effect import (
    LIFE_EVENT_DAILY_PROB, STABLE_HIRE_PERSONA_WEIGHTS
)

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

HC = 50
DAYS = 365
RID = 7777


def make_ep(modifier, life_events=True):
    return {
        "adoption_day": 0,
        "restaurant_effectiveness": 1.0,
        "industry_variance": 1.0,
        "without_ep": {"exit_modifier": modifier, "emotional_offset": {}},
        "with_ep": {"exit_modifier": modifier, "emotional_offset": {}},
        "stable_hire_weights": None,
        "life_event_daily_prob": LIFE_EVENT_DAILY_PROB if life_events else 0.0,
    }


def run(modifier, rid=RID, weights=WEIGHTS, life_events=True):
    profile = get_profile("fast_casual")
    results = simulate_restaurant(
        organization_id=rid, number_of_staff=HC, simulation_days=DAYS,
        persona_weights=weights, restaurant_profile=profile,
        enable_contagion=False,
        en_place_config=make_ep(modifier, life_events),
        enable_replacement_hiring=True,
    )
    sm = results["staff_master"]
    exits = sum(1 for s in sm if s["final_persona"] == "exit")
    annual = (exits / HC) * 100

    originals = [s for s in sm if s["hire_day"] == 0]
    orig_exits = sum(1 for s in originals if s["final_persona"] == "exit")

    replacements = [s for s in sm if s["hire_day"] > 0]
    elig = [s for s in replacements if s["hire_day"] + 90 <= DAYS]
    surv = sum(1 for s in elig if s["total_days"] >= 90)
    l2 = (surv / len(elig) * 100) if elig else 0

    return {"exits": exits, "annual": annual, "records": len(sm),
            "l1_exit": (orig_exits / HC * 100), "l2_surv": l2}


# =====================================================
# PART 1: Modifier sweep WITH life events
# =====================================================
print("=" * 70)
print(f"PART 1: Modifier sweep + life events (prob={LIFE_EVENT_DAILY_PROB})")
print(f"  rid={RID}, headcount={HC}")
print("=" * 70)

MODS = [0.08, 0.11, 0.14, 0.18, 0.20, 0.25, 0.28, 0.35]

print(f"\n  {'Mod':>5} {'Exits':>6} {'Ann%':>6} {'L1 Exit%':>9} {'L2 Surv%':>9} {'Recs':>5}")
print(f"  {'-'*5} {'-'*6} {'-'*6} {'-'*9} {'-'*9} {'-'*5}")

for m in MODS:
    r = run(m)
    print(f"  {m:>5.2f} {r['exits']:>6} {r['annual']:>5.0f}% "
          f"{r['l1_exit']:>8.0f}% {r['l2_surv']:>8.0f}% {r['records']:>5}")


# =====================================================
# PART 2: Life events only (modifier=0) — the floor
# =====================================================
print(f"\n{'='*70}")
print("PART 2: Life events ONLY (modifier=0.0) — unavoidable turnover floor")
print("=" * 70)

# Use a tiny modifier instead of 0 to avoid division issues
r_floor = run(0.01)
print(f"  Life-event-only: {r_floor['annual']:.0f}% annual, "
      f"{r_floor['exits']} exits, {r_floor['records']} records")


# =====================================================
# PART 3: Type spot checks with life events
# =====================================================
print(f"\n{'='*70}")
print("PART 3: Without vs With EP (life events included)")
print("=" * 70)

PAIRS = [
    ("fast_casual",        0.35, 0.27),
    ("high_volume_chain",  0.28, 0.22),
    ("sports_bar",         0.18, 0.14),
    ("steakhouse",         0.11, 0.08),
    ("family_diner",       0.15, 0.11),
    ("neighborhood_bistro",0.14, 0.11),
]

print(f"\n  {'Type':<22} {'W/O':>5} {'Ann%':>6}  {'W/':>5} {'Ann%':>6} {'Delta':>6}")
print(f"  {'-'*22} {'-'*5} {'-'*6}  {'-'*5} {'-'*6} {'-'*6}")

for pkey, wo, wi in PAIRS:
    r_wo = run(wo)
    r_wi = run(wi)
    delta = r_wo["annual"] - r_wi["annual"]
    print(f"  {pkey:<22} {wo:>5.2f} {r_wo['annual']:>5.0f}%  "
          f"{wi:>5.2f} {r_wi['annual']:>5.0f}% {delta:>+5.0f}")


# =====================================================
# PART 4: Stable Hire compound effect with life events
# =====================================================
print(f"\n{'='*70}")
print("PART 4: Stable Hire + life events")
print("=" * 70)

SH_MODS = [0.27, 0.18, 0.11, 0.08]

print(f"\n  {'Mod':>5} {'Normal':>8} {'StabHire':>9} {'Delta':>6} {'L2 Norm':>8} {'L2 SH':>7}")
print(f"  {'-'*5} {'-'*8} {'-'*9} {'-'*6} {'-'*8} {'-'*7}")

for m in SH_MODS:
    r_n = run(m, weights=WEIGHTS)
    r_sh = run(m, weights=STABLE_HIRE_PERSONA_WEIGHTS)
    delta = r_n["annual"] - r_sh["annual"]
    print(f"  {m:>5.2f} {r_n['annual']:>7.0f}% {r_sh['annual']:>8.0f}% {delta:>+5.0f} "
          f"{r_n['l2_surv']:>7.0f}% {r_sh['l2_surv']:>6.0f}%")

print(f"\n{'='*70}")
print("Done.")
print(f"{'='*70}")