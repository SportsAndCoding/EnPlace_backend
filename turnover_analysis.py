"""
turnover_analysis.py

En Place Network Turnover Analysis — Webinar Content Generator

SIMPLE MECHANICS:
  - Each staff member has a daily quit probability
  - Two buckets: cliff (<90 days tenure) and established (90+ days)
  - Two regimes: without EP and with EP (switches at adoption_day)
  - Replacement hiring: quit → new hire next day at tenure 0

RICH OUTPUT:
  - 100 restaurants across 12 types
  - Per-restaurant variance (no two steakhouses identical)
  - Rolling 365-day turnover timeseries for every restaurant
  - Three-level analysis (originals, cliff survival, post-cliff)
  - Rolled up by type and network-wide aggregate
  - CSV exports for charting and webinar drill-down

SIMULATION TIMELINE:
  912 days total. Adoption at day 547.
  Chart window: day 365 (adopt-182) through day 729 (adopt+182)
  Each chart point = exits in trailing 365 days / headcount × 100

Run: python turnover_analysis.py
"""

import hashlib
import csv
import os
import time
from typing import Dict, List, Tuple


# =====================================================================
# RESTAURANT TYPE DEFINITIONS
#
# Four numbers per type:
#   cliff_without:  daily quit probability for <90d staff, no EP
#   estab_without:  daily quit probability for 90+d staff, no EP
#   cliff_with:     daily quit probability for <90d staff, with EP
#   estab_with:     daily quit probability for 90+d staff, with EP
#
# Targets (rolling 365-day):
#   Without EP: 70-90% depending on type
#   With EP: 55-65% depending on type
#   Floor: ~33% (established staff unavoidable life events)
# =====================================================================

RESTAURANT_TYPES: Dict[str, Dict] = {
    # -------------------------------------------------------------------
    # CALIBRATED VIA BINARY SEARCH against industry research targets.
    # cliff:established ratio = 5:1
    # WITH rates = WITHOUT rates for now. Step 2 after pre-EP validated.
    #
    # national_avg: published industry benchmark (NRA, Cornell, Black Box, BLS)
    # national_range: low-high for context
    # -------------------------------------------------------------------
    "fast_casual": {
        "cliff_without":  0.009902,   # Target: 130%, Calibrated: 126.4%
        "estab_without":  0.001980,
        "cliff_with":     0.004562,   # Target: 45%, Calibrated: 47.0%
        "estab_with":     0.000912,
        "headcount_range": (38, 48),
        "label": "Fast Casual / QSR",
        "national_avg": 130,
        "national_range": "100-150%",
        "source": "NRA, QSR Magazine",
    },
    "high_volume_chain": {
        "cliff_without":  0.009016,   # Target: 100%, Calibrated: 100.2%
        "estab_without":  0.001803,
        "cliff_with":     0.005156,   # Target: 48%, Calibrated: 49.6%
        "estab_with":     0.001031,
        "headcount_range": (70, 82),
        "label": "High Volume Chain",
        "national_avg": 100,
        "national_range": "100%+",
        "source": "BLS, Black Box Intelligence",
    },
    "college_town_cafe": {
        "cliff_without":  0.008570,   # Target: 90%, Calibrated: 88.0%
        "estab_without":  0.001714,
        "cliff_with":     0.005750,   # Target: 47%, Calibrated: 47.0%
        "estab_with":     0.001150,
        "headcount_range": (34, 44),
        "label": "College Town Cafe",
        "national_avg": 90,
        "national_range": "90%+",
        "source": "BLS (seasonal/student workforce)",
    },
    "airport_restaurant": {
        "cliff_without":  0.006938,   # Target: 82%, Calibrated: 81.9%
        "estab_without":  0.001388,
        "cliff_with":     0.004562,   # Target: 48%, Calibrated: 46.3%
        "estab_with":     0.000912,
        "headcount_range": (65, 76),
        "label": "Airport Restaurant",
        "national_avg": 82,
        "national_range": "80%+",
        "source": "BLS (transient labor market)",
    },
    "sports_bar": {
        "cliff_without":  0.006678,   # Target: 78%, Calibrated: 78.3%
        "estab_without":  0.001336,
        "cliff_with":     0.004377,   # Target: 50%, Calibrated: 48.0%
        "estab_with":     0.000875,
        "headcount_range": (55, 65),
        "label": "Sports Bar",
        "national_avg": 78,
        "national_range": "75-80%",
        "source": "NRA, 7shifts",
    },
    "bar_and_grille": {
        "cliff_without":  0.006789,   # Target: 78%, Calibrated: 77.4%
        "estab_without":  0.001358,
        "cliff_with":     0.004562,   # Target: 50%, Calibrated: 51.2%
        "estab_with":     0.000912,
        "headcount_range": (58, 68),
        "label": "Bar & Grille",
        "national_avg": 78,
        "national_range": "75-80%",
        "source": "NRA, Black Box Intelligence",
    },
    "hotel_restaurant": {
        "cliff_without":  0.006919,   # Target: 78%, Calibrated: 80.4%
        "estab_without":  0.001384,
        "cliff_with":     0.004562,   # Target: 48%, Calibrated: 46.5%
        "estab_with":     0.000912,
        "headcount_range": (48, 58),
        "label": "Hotel Restaurant",
        "national_avg": 78,
        "national_range": "75-80%",
        "source": "Cornell Hotel School, BLS",
    },
    "family_diner": {
        "cliff_without":  0.006789,   # Target: 78%, Calibrated: 79.1%
        "estab_without":  0.001358,
        "cliff_with":     0.005750,   # Target: 45%, Calibrated: 45.8%
        "estab_with":     0.001150,
        "headcount_range": (25, 35),
        "label": "Family Diner",
        "national_avg": 78,
        "national_range": "75-80%",
        "source": "NRA",
    },
    "breakfast_cafe": {
        "cliff_without":  0.008570,   # Target: 78%, Calibrated: 78.4%
        "estab_without":  0.001714,
        "cliff_with":     0.006047,   # Target: 45%, Calibrated: 43.3%
        "estab_with":     0.001209,
        "headcount_range": (22, 30),
        "label": "Breakfast Cafe",
        "national_avg": 78,
        "national_range": "75-80%",
        "source": "NRA, Homebase",
    },
    "upscale_casual": {
        "cliff_without":  0.006641,   # Target: 75%, Calibrated: 76.9%
        "estab_without":  0.001328,
        "cliff_with":     0.003969,   # Target: 48%, Calibrated: 48.3%
        "estab_with":     0.000794,
        "headcount_range": (50, 60),
        "label": "Upscale Casual",
        "national_avg": 75,
        "national_range": "70-80%",
        "source": "Black Box Intelligence",
    },
    "neighborhood_bistro": {
        "cliff_without":  0.006789,   # Target: 75%, Calibrated: 76.4%
        "estab_without":  0.001358,
        "cliff_with":     0.005750,   # Target: 46%, Calibrated: 47.5%
        "estab_with":     0.001150,
        "headcount_range": (30, 40),
        "label": "Neighborhood Bistro",
        "national_avg": 75,
        "national_range": "70-80%",
        "source": "NRA",
    },
    "steakhouse": {
        "cliff_without":  0.006344,   # Target: 60%, Calibrated: 58.8%
        "estab_without":  0.001269,
        "cliff_with":     0.004414,   # Target: 45%, Calibrated: 46.5%
        "estab_with":     0.000883,
        "headcount_range": (45, 55),
        "label": "Steakhouse / Fine Dining",
        "national_avg": 60,
        "national_range": "50-70%",
        "source": "Cornell Hotel School",
    },
}

PROFILE_ROTATION = list(RESTAURANT_TYPES.keys())

# =====================================================================
# SIMULATION CONFIG
# =====================================================================

TOTAL_DAYS = 1460          # 4 years — room for full post-EP window
ADOPTION_DAY = 730         # 2 years in — pure pre-EP warmup
WINDOW = 365
CHART_START = ADOPTION_DAY - 182   # day 548
CHART_END = ADOPTION_DAY + 547     # day 1277 — rightmost 182 days are pure post-EP
NUM_RESTAURANTS = 100

OUTPUT_DIR = "synthetic_output"


# =====================================================================
# DETERMINISTIC HELPERS
# =====================================================================

def _det_float(seed_str: str) -> float:
    """Deterministic float [0, 1) from a string seed."""
    h = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    return (h % 1_000_000) / 1_000_000


def _det_int(seed_str: str, low: int, high: int) -> int:
    """Deterministic int in [low, high]."""
    h = int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16)
    return low + (h % (high - low + 1))


def _restaurant_variance(organization_id: int) -> float:
    """Per-restaurant multiplier on quit rates. Range [0.88, 1.12]."""
    return 0.88 + _det_float(f"{organization_id}:variance") * 0.24


# =====================================================================
# CORE SIMULATION
# =====================================================================

def simulate_restaurant(
    organization_id: int,
    headcount: int,
    type_config: Dict,
    adoption_day: int = ADOPTION_DAY,
    total_days: int = TOTAL_DAYS,
) -> Dict:
    """
    Simulate one restaurant. Returns dict with:
      - exits_per_day: list[int]
      - staff_records: list of dicts for three-level analysis
      - headcount: int
    """
    # Variance removed — calibrated rates are exact. Natural variance
    # comes from different headcounts and random seeds across restaurants.
    cw = type_config["cliff_without"]
    ew = type_config["estab_without"]
    ce = type_config["cliff_with"]
    ee = type_config["estab_with"]

    # Staff tracking: hire_day per slot, generation counter
    hire_day = [0] * headcount
    gen = [0] * headcount
    exits_per_day = [0] * total_days

    # Staff records for three-level analysis
    # Each entry: (hire_day, exit_day_or_None, exit_tenure, ep_active_at_exit)
    records = []
    # Track active staff initial records (will be updated on exit)
    active_records = {}
    for slot in range(headcount):
        rec = {"hire_day": 0, "exit_day": None, "exit_tenure": None,
               "ep_active": None, "slot": slot, "gen": 0}
        active_records[slot] = rec

    for day in range(total_days):
        ep_active = day >= adoption_day
        c_prob = ce if ep_active else cw
        e_prob = ee if ep_active else ew

        for slot in range(headcount):
            tenure = day - hire_day[slot]
            prob = c_prob if tenure < 90 else e_prob

            uid_seed = f"{organization_id}:{slot}:{gen[slot]}:{day}"
            roll = _det_float(uid_seed)

            if roll < prob:
                exits_per_day[day] += 1

                # Finalize this staff record
                rec = active_records[slot]
                rec["exit_day"] = day
                rec["exit_tenure"] = tenure
                rec["ep_active"] = ep_active
                records.append(rec)

                # Replace
                hire_day[slot] = day + 1
                gen[slot] += 1
                active_records[slot] = {
                    "hire_day": day + 1, "exit_day": None,
                    "exit_tenure": None, "ep_active": None,
                    "slot": slot, "gen": gen[slot],
                }

    # Add staff still active at end of sim
    for slot in range(headcount):
        rec = active_records[slot]
        rec["exit_tenure"] = total_days - rec["hire_day"]
        records.append(rec)

    return {
        "exits_per_day": exits_per_day,
        "staff_records": records,
        "headcount": headcount,
        "variance": 1.0,
        "rates": {"cw": cw, "ew": ew, "ce": ce, "ee": ee},
    }


def rolling_turnover(exits_per_day: List[int], headcount: int, window: int = WINDOW) -> List[Tuple[int, float]]:
    """Rolling window turnover. Returns list of (day, pct) for day >= window-1."""
    results = []
    running = sum(exits_per_day[:window])
    results.append((window - 1, (running / headcount) * 100))

    for day in range(window, len(exits_per_day)):
        running += exits_per_day[day]
        running -= exits_per_day[day - window]
        results.append((day, (running / headcount) * 100))

    return results


def three_level_analysis(records: List[Dict], headcount: int, adoption_day: int = ADOPTION_DAY) -> Dict:
    """
    L1: Original staff (hire_day=0) retention pre vs post adoption
    L2: Replacement cliff survival (<90d) pre vs post
    L3: Post-cliff (90+d) retention of replacements pre vs post
    """
    originals = [r for r in records if r["hire_day"] == 0]
    replacements = [r for r in records if r["hire_day"] > 0]

    # L1: Originals
    orig_count = len(originals)
    l1_pre_exits = sum(1 for r in originals if r["exit_day"] is not None and r["exit_day"] < adoption_day)
    l1_pre_survived = orig_count - l1_pre_exits
    l1_post_exits = sum(1 for r in originals if r["exit_day"] is not None and r["exit_day"] >= adoption_day)
    l1_pre_retention = ((orig_count - l1_pre_exits) / orig_count * 100) if orig_count else 0
    l1_post_retention = ((l1_pre_survived - l1_post_exits) / l1_pre_survived * 100) if l1_pre_survived else 100

    # L2: Replacement cliff survival
    pre_repls = [r for r in replacements if r["hire_day"] < adoption_day]
    post_repls = [r for r in replacements if r["hire_day"] >= adoption_day]

    # Eligible: had chance to reach 90 days
    pre_elig = [r for r in pre_repls if r["hire_day"] + 90 <= TOTAL_DAYS]
    post_elig = [r for r in post_repls if r["hire_day"] + 90 <= TOTAL_DAYS]

    pre_survived_90 = sum(1 for r in pre_elig if r["exit_tenure"] is None or r["exit_tenure"] >= 90)
    post_survived_90 = sum(1 for r in post_elig if r["exit_tenure"] is None or r["exit_tenure"] >= 90)

    l2_pre_survival = (pre_survived_90 / len(pre_elig) * 100) if pre_elig else 0
    l2_post_survival = (post_survived_90 / len(post_elig) * 100) if post_elig else 0

    # L3: Post-cliff retention (of those who made it past 90d)
    pre_cliff_surv = [r for r in pre_elig if r["exit_tenure"] is None or r["exit_tenure"] >= 90]
    post_cliff_surv = [r for r in post_elig if r["exit_tenure"] is None or r["exit_tenure"] >= 90]

    pre_cliff_then_exit = sum(1 for r in pre_cliff_surv if r["exit_day"] is not None)
    post_cliff_then_exit = sum(1 for r in post_cliff_surv if r["exit_day"] is not None)

    l3_pre_retention = ((len(pre_cliff_surv) - pre_cliff_then_exit) / len(pre_cliff_surv) * 100) if pre_cliff_surv else 100
    l3_post_retention = ((len(post_cliff_surv) - post_cliff_then_exit) / len(post_cliff_surv) * 100) if post_cliff_surv else 100

    return {
        "l1_original_count": orig_count,
        "l1_pre_exits": l1_pre_exits,
        "l1_pre_retention": round(l1_pre_retention, 1),
        "l1_post_at_risk": l1_pre_survived,
        "l1_post_exits": l1_post_exits,
        "l1_post_retention": round(l1_post_retention, 1),
        "l2_pre_eligible": len(pre_elig),
        "l2_pre_survived": pre_survived_90,
        "l2_pre_survival": round(l2_pre_survival, 1),
        "l2_post_eligible": len(post_elig),
        "l2_post_survived": post_survived_90,
        "l2_post_survival": round(l2_post_survival, 1),
        "l3_pre_count": len(pre_cliff_surv),
        "l3_pre_exits": pre_cliff_then_exit,
        "l3_pre_retention": round(l3_pre_retention, 1),
        "l3_post_count": len(post_cliff_surv),
        "l3_post_exits": post_cliff_then_exit,
        "l3_post_retention": round(l3_post_retention, 1),
    }


# =====================================================================
# RESTAURANT CONFIG BUILDER
# =====================================================================

def build_configs() -> List[Dict]:
    configs = []
    for i in range(NUM_RESTAURANTS):
        rid = 101 + i
        pkey = PROFILE_ROTATION[i % len(PROFILE_ROTATION)]
        type_cfg = RESTAURANT_TYPES[pkey]
        hc = _det_int(f"{rid}:headcount", *type_cfg["headcount_range"])
        configs.append({
            "organization_id": rid,
            "profile_key": pkey,
            "headcount": hc,
        })
    return configs


# =====================================================================
# MAIN
# =====================================================================

if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    sim_start = time.time()

    configs = build_configs()

    # Per-restaurant results
    all_meta = []
    all_timeseries = []  # (rid, profile_key, day, days_from_adoption, pct)

    # Type-level aggregation
    type_agg = {}

    print(f"{'#':>3} {'RID':>4} {'Type':<20} {'HC':>3} "
          f"{'NatlAvg':>8} {'PreAvg':>7} {'AnnPost':>8} "
          f"{'(R@182)':>8} {'L2pre':>6} {'L2pst':>6}")
    print("-" * 88)

    for idx, cfg in enumerate(configs):
        rid = cfg["organization_id"]
        pkey = cfg["profile_key"]
        hc = cfg["headcount"]
        type_cfg = RESTAURANT_TYPES[pkey]

        result = simulate_restaurant(rid, hc, type_cfg)
        rolling = rolling_turnover(result["exits_per_day"], hc)
        levels = three_level_analysis(result["staff_records"], hc)

        # Extract chart-range values
        # Pre = rolling 365-day average, pure pre-EP windows
        pre_vals = [pct for day, pct in rolling if CHART_START <= day < ADOPTION_DAY]
        pre_avg = sum(pre_vals) / len(pre_vals) if pre_vals else 0

        # Post = ANNUALIZED from raw quit count since adoption
        # Count exits from adoption through end of sim
        post_days = TOTAL_DAYS - ADOPTION_DAY
        post_exits = sum(result["exits_per_day"][ADOPTION_DAY:])
        post_annualized = (post_exits / post_days) * 365 / hc * 100

        # Rolling@182 = what the rolling line shows 6 months post-adoption
        # (still contaminated with pre-EP data — the webinar "aha" moment)
        rolling_dict = {day: pct for day, pct in rolling}
        rolling_at_182 = rolling_dict.get(ADOPTION_DAY + 182, 0)

        natl = type_cfg["national_avg"]
        print(f"{idx+1:>3} {rid:>4} {pkey:<20} {hc:>3} "
              f"{natl:>7.0f}% {pre_avg:>6.1f}% {post_annualized:>7.1f}% "
              f"({rolling_at_182:>5.1f}%) "
              f"{levels['l2_pre_survival']:>5.0f}% {levels['l2_post_survival']:>5.0f}%")

        # Store timeseries
        for day, pct in rolling:
            if CHART_START <= day <= CHART_END:
                all_timeseries.append({
                    "organization_id": rid,
                    "profile_key": pkey,
                    "label": type_cfg["label"],
                    "national_avg": type_cfg["national_avg"],
                    "annualized_post_ep": round(post_annualized, 1),
                    "sim_day": day,
                    "days_from_adoption": day - ADOPTION_DAY,
                    "rolling_365_turnover_pct": round(pct, 1),
                })

        # Store meta
        meta = {
            "organization_id": rid,
            "profile_key": pkey,
            "label": type_cfg["label"],
            "headcount": hc,
            "national_avg": type_cfg["national_avg"],
            "national_range": type_cfg["national_range"],
            "source": type_cfg["source"],
            "pre_avg_turnover": round(pre_avg, 1),
            "post_annualized": round(post_annualized, 1),
            "rolling_at_182": round(rolling_at_182, 1),
            "post_exits": post_exits,
            "post_days": post_days,
            "improvement_pct": round(pre_avg - post_annualized, 1),
            **{f"rates_{k}": round(v, 6) for k, v in result["rates"].items()},
            **levels,
        }
        all_meta.append(meta)

        # Aggregate by type
        if pkey not in type_agg:
            type_agg[pkey] = {"pre_sum": 0, "post_sum": 0, "r182_sum": 0, "count": 0,
                              "l2_pre_elig": 0, "l2_pre_surv": 0,
                              "l2_post_elig": 0, "l2_post_surv": 0}
        ta = type_agg[pkey]
        ta["count"] += 1
        ta["pre_sum"] += pre_avg
        ta["post_sum"] += post_annualized
        ta["r182_sum"] += rolling_at_182
        ta["l2_pre_elig"] += levels["l2_pre_eligible"]
        ta["l2_pre_surv"] += levels["l2_pre_survived"]
        ta["l2_post_elig"] += levels["l2_post_eligible"]
        ta["l2_post_surv"] += levels["l2_post_survived"]

    # Write CSVs
    ts_path = os.path.join(OUTPUT_DIR, "rolling_turnover_timeseries.csv")
    with open(ts_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_timeseries[0].keys())
        w.writeheader()
        w.writerows(all_timeseries)

    meta_path = os.path.join(OUTPUT_DIR, "restaurant_meta.csv")
    with open(meta_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_meta[0].keys())
        w.writeheader()
        w.writerows(all_meta)

    elapsed = time.time() - sim_start

    # =====================================================================
    # SUMMARY
    # =====================================================================
    print(f"\n{'='*75}")
    print(f"COMPLETE — {NUM_RESTAURANTS} restaurants in {elapsed:.1f}s")
    print(f"  Timeseries: {len(all_timeseries):,} rows → {ts_path}")
    print(f"  Meta: {len(all_meta)} rows → {meta_path}")
    print(f"{'='*75}")

    # Grand totals
    grand_pre = sum(m["pre_avg_turnover"] for m in all_meta) / len(all_meta)
    grand_post = sum(m["post_annualized"] for m in all_meta) / len(all_meta)
    grand_r182 = sum(m["rolling_at_182"] for m in all_meta) / len(all_meta)

    print(f"\n  NETWORK AGGREGATE (Industry hourly avg: 75-80%, NRA/BLS):")
    print(f"    Pre-EP avg:           {grand_pre:.1f}%")
    print(f"    Post-EP annualized:   {grand_post:.1f}%")
    print(f"    Rolling@182 (blended):{grand_r182:.1f}%")
    print(f"    True improvement:     {grand_pre - grand_post:.1f} pts")

    print(f"\n  {'Type':<22} {'NatlAvg':>8} {'SimPre':>7} {'AnnPost':>8} {'R@182':>6} {'Delta':>6} {'L2pre':>6} {'L2pst':>6}")
    print(f"  {'-'*22} {'-'*8} {'-'*7} {'-'*8} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")

    for pkey in PROFILE_ROTATION:
        ta = type_agg.get(pkey)
        if not ta:
            continue
        t_pre = ta["pre_sum"] / ta["count"]
        t_post = ta["post_sum"] / ta["count"]
        t_r182 = ta["r182_sum"] / ta["count"]
        l2_pre = (ta["l2_pre_surv"] / ta["l2_pre_elig"] * 100) if ta["l2_pre_elig"] else 0
        l2_post = (ta["l2_post_surv"] / ta["l2_post_elig"] * 100) if ta["l2_post_elig"] else 0
        natl = RESTAURANT_TYPES[pkey]["national_avg"]
        print(f"  {pkey:<22} {natl:>7.0f}% {t_pre:>6.1f}% {t_post:>7.1f}% {t_r182:>5.1f}% {t_pre-t_post:>+5.1f} "
              f"{l2_pre:>5.0f}% {l2_post:>5.0f}%")

    print()