"""
run_synthetic_simulation.py

Runs the full synthetic staffing simulation across 100 restaurants.
Every restaurant adopts En Place at day 183. Replacement hiring ON.

THREE-LEVEL ANALYSIS:
  L1: Original staff retention (pre vs post adoption)
  L2: Replacement hire 90-day cliff survival rate (pre vs post)
  L3: Post-cliff retention of replacement hires (pre vs post)

OUTPUT:
  - restaurant_meta.csv: all three levels per restaurant (drill-down ready)
  - Summary rolled up by restaurant type
  - Aggregate across all 100 restaurants

USAGE:
    python run_synthetic_simulation.py                  # CSV only
    python run_synthetic_simulation.py --upload          # CSV + Supabase
"""

import sys
import os
import csv
import json
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Dict, Any, List

from modules.synthetic.restaurant_profiles import get_profile, list_profile_keys
from modules.synthetic.restaurant_simulation_runner import simulate_restaurant
from modules.synthetic.en_place_effect import get_en_place_config, DEFAULT_PERSONA_WEIGHTS


# -------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------

ADOPTION_DAY = 183  # 6-month mark
SIM_DAYS = 365

_PROFILE_ROTATION = [
    "steakhouse", "sports_bar", "fast_casual", "neighborhood_bistro",
    "upscale_casual", "family_diner", "breakfast_cafe", "bar_and_grille",
    "high_volume_chain", "college_town_cafe", "hotel_restaurant",
    "airport_restaurant",
]

_STAFF_COUNTS = {
    "steakhouse":         (45, 55),
    "sports_bar":         (55, 65),
    "fast_casual":        (38, 48),
    "neighborhood_bistro":(30, 40),
    "upscale_casual":     (50, 60),
    "family_diner":       (25, 35),
    "breakfast_cafe":     (22, 30),
    "bar_and_grille":     (58, 68),
    "high_volume_chain":  (70, 82),
    "college_town_cafe":  (34, 44),
    "hotel_restaurant":   (48, 58),
    "airport_restaurant": (65, 76),
}

OUTPUT_DIR = "synthetic_output"
GRAPH_SNAPSHOT_INTERVAL = 7
SUPABASE_BATCH_SIZE = 500


def _deterministic_staff_count(restaurant_id: int, profile_key: str) -> int:
    import hashlib
    low, high = _STAFF_COUNTS.get(profile_key, (40, 60))
    seed = int(hashlib.sha256(f"{restaurant_id}:staff_count".encode()).hexdigest()[:8], 16)
    return low + (seed % (high - low + 1))


def build_restaurant_configs() -> List[Dict[str, Any]]:
    configs = []
    for i in range(100):
        rid = 101 + i
        profile_key = _PROFILE_ROTATION[i % len(_PROFILE_ROTATION)]
        configs.append({
            "restaurant_id": rid,
            "profile_key": profile_key,
            "num_staff": _deterministic_staff_count(rid, profile_key),
            "num_days": SIM_DAYS,
            "adoption_day": ADOPTION_DAY,
        })
    return configs


RESTAURANTS_TO_SIMULATE = build_restaurant_configs()


# -------------------------------------------------------------
# THREE-LEVEL ANALYSIS
# -------------------------------------------------------------

def compute_three_level_analysis(
    staff_master: List[Dict[str, Any]],
    target_headcount: int,
    adoption_day: int = ADOPTION_DAY,
) -> Dict[str, Any]:
    """
    Compute the three-level before/after analysis.

    Level 1: Original Staff Retention
      Pre:  Of originals, how many exited before adoption day?
      Post: Of originals who survived to adoption, how many exited after?

    Level 2: 90-Day Cliff Survival (Replacement Hires)
      Pre:  Replacements hired early enough to complete 90d before adoption
      Post: Replacements hired after adoption who complete 90d before end of sim
      Metric: % who survived 90 days

    Level 3: Post-Cliff Retention
      Pre:  Replacements who survived 90d in pre-period, did they exit?
      Post: Replacements who survived 90d in post-period, did they exit?
    """
    # Separate staff by cohort
    originals = [s for s in staff_master if s["hire_day"] == 0]
    replacements = [s for s in staff_master if s["hire_day"] > 0]

    # ---- LEVEL 1: Original staff ----
    orig_count = len(originals)

    # Pre: originals who exited before adoption
    l1_pre_exits = sum(
        1 for s in originals
        if s["exit_day"] is not None and s["exit_day"] <= adoption_day
    )
    l1_pre_survived = orig_count - l1_pre_exits
    l1_pre_retention = ((orig_count - l1_pre_exits) / orig_count * 100) if orig_count > 0 else 0

    # Post: of those who survived to adoption, how many exited after?
    l1_post_exits = sum(
        1 for s in originals
        if s["exit_day"] is not None and s["exit_day"] > adoption_day
    )
    l1_post_retention = (
        ((l1_pre_survived - l1_post_exits) / l1_pre_survived * 100)
        if l1_pre_survived > 0 else 100
    )

    # ---- LEVEL 2: 90-Day Cliff Survival ----
    # Pre-period replacements: hired before adoption_day
    pre_replacements = [s for s in replacements if s["hire_day"] < adoption_day]

    # Only count those who had a chance to reach 90 days before end of sim
    # (hired early enough that hire_day + 90 <= sim end)
    pre_repl_eligible = [
        s for s in pre_replacements
        if s["hire_day"] + 90 <= SIM_DAYS
    ]
    pre_repl_survived_90 = sum(1 for s in pre_repl_eligible if s["total_days"] >= 90)
    l2_pre_count = len(pre_repl_eligible)
    l2_pre_cliff_survival = (pre_repl_survived_90 / l2_pre_count * 100) if l2_pre_count > 0 else 0

    # Post-period replacements: hired on or after adoption_day
    post_replacements = [s for s in replacements if s["hire_day"] >= adoption_day]

    # Only count those who had a chance to reach 90 days
    post_repl_eligible = [
        s for s in post_replacements
        if s["hire_day"] + 90 <= SIM_DAYS
    ]
    post_repl_survived_90 = sum(1 for s in post_repl_eligible if s["total_days"] >= 90)
    l2_post_count = len(post_repl_eligible)
    l2_post_cliff_survival = (post_repl_survived_90 / l2_post_count * 100) if l2_post_count > 0 else 0

    # Stable Hire tracking
    stable_hire_count = sum(1 for s in post_replacements if s.get("hired_with_stable_hire"))

    # ---- LEVEL 3: Post-Cliff Retention ----
    # Pre: replacements who survived 90d in pre-period, how many then exited?
    pre_cliff_survivors = [s for s in pre_repl_eligible if s["total_days"] >= 90]
    pre_cliff_then_exited = sum(
        1 for s in pre_cliff_survivors
        if s["final_persona"] == "exit"
    )
    l3_pre_count = len(pre_cliff_survivors)
    l3_pre_retention = (
        ((l3_pre_count - pre_cliff_then_exited) / l3_pre_count * 100)
        if l3_pre_count > 0 else 100
    )

    # Post: replacements who survived 90d in post-period, how many then exited?
    post_cliff_survivors = [s for s in post_repl_eligible if s["total_days"] >= 90]
    post_cliff_then_exited = sum(
        1 for s in post_cliff_survivors
        if s["final_persona"] == "exit"
    )
    l3_post_count = len(post_cliff_survivors)
    l3_post_retention = (
        ((l3_post_count - post_cliff_then_exited) / l3_post_count * 100)
        if l3_post_count > 0 else 100
    )

    # ---- AGGREGATE: Total exits per period ----
    total_pre_exits = sum(
        1 for s in staff_master
        if s["exit_day"] is not None and s["exit_day"] <= adoption_day
    )
    total_post_exits = sum(
        1 for s in staff_master
        if s["exit_day"] is not None and s["exit_day"] > adoption_day
    )
    pre_annualized = (total_pre_exits / target_headcount) * (365 / ADOPTION_DAY) * 100
    post_annualized = (total_post_exits / target_headcount) * (365 / (SIM_DAYS - ADOPTION_DAY)) * 100

    return {
        # Aggregate
        "total_staff_records": len(staff_master),
        "total_pre_exits": total_pre_exits,
        "total_post_exits": total_post_exits,
        "pre_annualized_turnover": round(pre_annualized, 1),
        "post_annualized_turnover": round(post_annualized, 1),

        # L1: Original staff
        "l1_original_count": orig_count,
        "l1_pre_exits": l1_pre_exits,
        "l1_pre_retention_pct": round(l1_pre_retention, 1),
        "l1_post_at_risk": l1_pre_survived,
        "l1_post_exits": l1_post_exits,
        "l1_post_retention_pct": round(l1_post_retention, 1),

        # L2: 90-day cliff
        "l2_pre_eligible": l2_pre_count,
        "l2_pre_survived_90d": pre_repl_survived_90,
        "l2_pre_cliff_survival_pct": round(l2_pre_cliff_survival, 1),
        "l2_post_eligible": l2_post_count,
        "l2_post_survived_90d": post_repl_survived_90,
        "l2_post_cliff_survival_pct": round(l2_post_cliff_survival, 1),
        "l2_stable_hire_count": stable_hire_count,

        # L3: Post-cliff retention
        "l3_pre_count": l3_pre_count,
        "l3_pre_exits": pre_cliff_then_exited,
        "l3_pre_retention_pct": round(l3_pre_retention, 1),
        "l3_post_count": l3_post_count,
        "l3_post_exits": post_cliff_then_exited,
        "l3_post_retention_pct": round(l3_post_retention, 1),
    }


# -------------------------------------------------------------
# CSV EXPORT
# -------------------------------------------------------------

def ensure_output_dir():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)


def write_csv(filename: str, rows: List[Dict[str, Any]]):
    path = os.path.join(OUTPUT_DIR, filename)
    if not rows:
        print(f"  [WARN] No rows for {filename}")
        return
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    print(f"  [CSV] {len(rows):,} rows -> {filename}")


def write_jsonl(filename: str, rows: List[Dict[str, Any]]):
    path = os.path.join(OUTPUT_DIR, filename)
    if not rows:
        return
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")
    print(f"  [JSONL] {len(rows):,} rows -> {filename}")


# -------------------------------------------------------------
# SUPABASE HELPERS
# -------------------------------------------------------------

def get_supabase():
    from database.supabase_client import supabase
    return supabase


def batch_insert(table_name: str, rows: List[Dict[str, Any]]):
    if not rows:
        return 0
    sb = get_supabase()
    total = 0
    for i in range(0, len(rows), SUPABASE_BATCH_SIZE):
        batch = rows[i:i + SUPABASE_BATCH_SIZE]
        try:
            sb.table(table_name).insert(batch).execute()
            total += len(batch)
        except Exception as e:
            print(f"  [ERROR] {table_name} batch at {i}: {e}")
    return total


def truncate_table(table_name: str):
    sb = get_supabase()
    try:
        sb.table(table_name).delete().gte("id", 0).execute()
        print(f"  [TRUNCATE] {table_name}")
    except Exception:
        try:
            sb.table(table_name).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
            print(f"  [TRUNCATE] {table_name}")
        except Exception as e2:
            print(f"  [WARN] Could not truncate {table_name}: {e2}")


# -------------------------------------------------------------
# DATA FLATTENING
# -------------------------------------------------------------

def flatten_graph_snapshots(snapshots, restaurant_id):
    rows = []
    for snap in snapshots:
        meta = snap.get("metadata", {})
        rows.append({
            "restaurant_id": restaurant_id,
            "day_index": meta.get("day_index", 0),
            "active_staff": meta.get("active_staff_count", 0),
            "edge_count": meta.get("edge_count", 0),
            "graph_density": meta.get("graph_density", 0),
            "avg_criticality": meta.get("avg_criticality", 0),
            "avg_mood": meta.get("avg_mood", 0),
            "snapshot_data": json.dumps(snap, default=str),
        })
    return rows


def flatten_exit_cascades(cascades, restaurant_id):
    rows = []
    for cas in cascades:
        rows.append({
            "staff_id": cas["staff_id"],
            "restaurant_id": restaurant_id,
            "day_index": cas["day_index"],
            "exit_reason": cas.get("exit_reason"),
            "cascade_severity": cas.get("cascade_severity"),
            "expected_additional_exits": cas.get("expected_additional_exits", 0),
            "worst_case_exits": cas.get("worst_case_exits", 0),
            "at_risk_staff": json.dumps(cas.get("at_risk_staff", []), default=str),
            "cascade_viz_data": "{}",
        })
    return rows


# -------------------------------------------------------------
# MAIN PIPELINE
# -------------------------------------------------------------

def run_full_simulation(write_csv_flag: bool = True, upload_supabase: bool = False):
    ensure_output_dir()

    if write_csv_flag:
        for fn in ["staff_master.csv", "daily_emotions.csv", "daily_behavior.csv",
                    "exit_cascades.csv", "graph_snapshots.jsonl", "restaurant_meta.csv"]:
            open(os.path.join(OUTPUT_DIR, fn), "w").close()

    if upload_supabase:
        print("\n--- Truncating Supabase tables ---")
        for t in ["synthetic_staff_master", "synthetic_daily_emotions",
                   "synthetic_daily_behavior", "synthetic_graph_snapshots",
                   "synthetic_exit_cascades", "synthetic_restaurants"]:
            truncate_table(t)

    # Accumulators
    all_staff_master = []
    all_daily_emotions = []
    all_daily_behavior = []
    all_graph_snapshots = []
    all_exit_cascades = []
    all_restaurant_meta = []

    # Per-type aggregation for summary
    type_agg: Dict[str, Dict[str, Any]] = {}

    total = len(RESTAURANTS_TO_SIMULATE)
    sim_start = time.time()

    for idx, config in enumerate(RESTAURANTS_TO_SIMULATE):
        rid = config["restaurant_id"]
        pkey = config["profile_key"]
        num_staff = config["num_staff"]
        adoption_day = config["adoption_day"]

        r_start = time.time()
        print(f"\n=== [{idx+1}/{total}] Restaurant {rid} ({pkey}, {num_staff} staff) ===")

        profile = get_profile(pkey)
        ep_config = get_en_place_config(rid, pkey, adoption_day)

        print(f"  EP effectiveness: {ep_config['restaurant_effectiveness']:.2f}, "
              f"variance: {ep_config['industry_variance']:.2f}")
        print(f"  Exit mod: without={ep_config['without_ep']['exit_modifier']:.3f}, "
              f"with={ep_config['with_ep']['exit_modifier']:.3f}")

        results = simulate_restaurant(
            restaurant_id=rid,
            number_of_staff=num_staff,
            simulation_days=SIM_DAYS,
            persona_weights=DEFAULT_PERSONA_WEIGHTS,
            restaurant_profile=profile,
            enable_contagion=False,  # OFF for speed — enable with flag if needed
            graph_snapshot_interval=GRAPH_SNAPSHOT_INTERVAL,
            en_place_config=ep_config,
            enable_replacement_hiring=True,
        )

        # Three-level analysis
        analysis = compute_three_level_analysis(results["staff_master"], num_staff, adoption_day)

        r_elapsed = time.time() - r_start

        # Print per-restaurant summary
        print(f"  Done in {r_elapsed:.1f}s — {analysis['total_staff_records']} staff records")
        print(f"  AGGREGATE: Pre={analysis['pre_annualized_turnover']:.0f}% → "
              f"Post={analysis['post_annualized_turnover']:.0f}% annualized")
        print(f"  L1 Original Staff:  Pre {analysis['l1_pre_retention_pct']:.0f}% retained → "
              f"Post {analysis['l1_post_retention_pct']:.0f}% retained "
              f"({analysis['l1_pre_exits']}/{analysis['l1_original_count']} pre exits, "
              f"{analysis['l1_post_exits']}/{analysis['l1_post_at_risk']} post exits)")
        print(f"  L2 90-Day Cliff:    Pre {analysis['l2_pre_cliff_survival_pct']:.0f}% survive → "
              f"Post {analysis['l2_post_cliff_survival_pct']:.0f}% survive "
              f"({analysis['l2_pre_eligible']} pre eligible, "
              f"{analysis['l2_post_eligible']} post eligible, "
              f"{analysis['l2_stable_hire_count']} via Stable Hire)")
        print(f"  L3 Post-Cliff:      Pre {analysis['l3_pre_retention_pct']:.0f}% retained → "
              f"Post {analysis['l3_post_retention_pct']:.0f}% retained")

        # Build metadata row
        meta = {
            "restaurant_id": rid,
            "profile_key": pkey,
            "num_staff": num_staff,
            "adoption_day": adoption_day,
            "ep_effectiveness": ep_config["restaurant_effectiveness"],
            "industry_variance": ep_config["industry_variance"],
            "without_ep_exit_mod": ep_config["without_ep"]["exit_modifier"],
            "with_ep_exit_mod": ep_config["with_ep"]["exit_modifier"],
            **analysis,
        }
        all_restaurant_meta.append(meta)

        # Accumulate for type rollup
        if pkey not in type_agg:
            type_agg[pkey] = {
                "count": 0, "headcount": 0,
                "l1_orig": 0, "l1_pre_exits": 0, "l1_post_at_risk": 0, "l1_post_exits": 0,
                "l2_pre_elig": 0, "l2_pre_surv": 0, "l2_post_elig": 0, "l2_post_surv": 0,
                "l3_pre_count": 0, "l3_pre_exits": 0, "l3_post_count": 0, "l3_post_exits": 0,
                "total_pre_exits": 0, "total_post_exits": 0,
            }
        ta = type_agg[pkey]
        ta["count"] += 1
        ta["headcount"] += num_staff
        ta["l1_orig"] += analysis["l1_original_count"]
        ta["l1_pre_exits"] += analysis["l1_pre_exits"]
        ta["l1_post_at_risk"] += analysis["l1_post_at_risk"]
        ta["l1_post_exits"] += analysis["l1_post_exits"]
        ta["l2_pre_elig"] += analysis["l2_pre_eligible"]
        ta["l2_pre_surv"] += analysis["l2_pre_survived_90d"]
        ta["l2_post_elig"] += analysis["l2_post_eligible"]
        ta["l2_post_surv"] += analysis["l2_post_survived_90d"]
        ta["l3_pre_count"] += analysis["l3_pre_count"]
        ta["l3_pre_exits"] += analysis["l3_pre_exits"]
        ta["l3_post_count"] += analysis["l3_post_count"]
        ta["l3_post_exits"] += analysis["l3_post_exits"]
        ta["total_pre_exits"] += analysis["total_pre_exits"]
        ta["total_post_exits"] += analysis["total_post_exits"]

        # Accumulate raw data
        all_staff_master.extend(results["staff_master"])
        all_daily_emotions.extend(results["daily_emotions"])
        all_daily_behavior.extend(results["daily_behavior"])
        all_graph_snapshots.extend(
            flatten_graph_snapshots(results["graph_snapshots"], rid)
        )
        all_exit_cascades.extend(
            flatten_exit_cascades(results["exit_cascades"], rid)
        )

        # Per-restaurant upload
        if upload_supabase:
            batch_insert("synthetic_restaurants", [{
                "restaurant_id": rid, "profile_key": pkey,
                "num_staff": num_staff, "num_days": SIM_DAYS, "sma_score": None,
            }])
            batch_insert("synthetic_staff_master", results["staff_master"])
            batch_insert("synthetic_daily_emotions", results["daily_emotions"])
            batch_insert("synthetic_daily_behavior", results["daily_behavior"])

    # ---------------------------------------------------------
    # CSV EXPORT
    # ---------------------------------------------------------
    if write_csv_flag:
        print("\n--- Writing CSV/JSONL files ---")
        write_csv("staff_master.csv", all_staff_master)
        write_csv("daily_emotions.csv", all_daily_emotions)
        write_csv("daily_behavior.csv", all_daily_behavior)
        write_csv("exit_cascades.csv", all_exit_cascades)
        write_csv("restaurant_meta.csv", all_restaurant_meta)
        write_jsonl("graph_snapshots.jsonl", all_graph_snapshots)

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------
    total_elapsed = time.time() - sim_start

    print(f"\n{'='*80}")
    print(f"ALL SIMULATIONS COMPLETE — {total_elapsed:.1f}s")
    print(f"{'='*80}")
    print(f"  Restaurants: {total}")
    print(f"  Staff records: {len(all_staff_master):,}")
    print(f"  Emotion rows: {len(all_daily_emotions):,}")

    # Grand totals
    grand = {
        "l1_orig": 0, "l1_pre_exits": 0, "l1_post_at_risk": 0, "l1_post_exits": 0,
        "l2_pre_elig": 0, "l2_pre_surv": 0, "l2_post_elig": 0, "l2_post_surv": 0,
        "l3_pre_count": 0, "l3_pre_exits": 0, "l3_post_count": 0, "l3_post_exits": 0,
        "headcount": 0, "total_pre": 0, "total_post": 0,
    }
    for ta in type_agg.values():
        for k in grand:
            if k in ta:
                grand[k] += ta[k]
            elif k == "total_pre":
                grand[k] += ta["total_pre_exits"]
            elif k == "total_post":
                grand[k] += ta["total_post_exits"]

    pre_ann = (grand["total_pre"] / grand["headcount"]) * (365 / ADOPTION_DAY) * 100 if grand["headcount"] > 0 else 0
    post_ann = (grand["total_post"] / grand["headcount"]) * (365 / (SIM_DAYS - ADOPTION_DAY)) * 100 if grand["headcount"] > 0 else 0

    print(f"\n{'='*80}")
    print(f"AGGREGATE RESULTS (all {total} restaurants)")
    print(f"{'='*80}")
    print(f"  Annualized Turnover:  Pre={pre_ann:.0f}% → Post={post_ann:.0f}%")

    l1_pre_ret = ((grand["l1_orig"] - grand["l1_pre_exits"]) / grand["l1_orig"] * 100) if grand["l1_orig"] > 0 else 0
    l1_post_ret = ((grand["l1_post_at_risk"] - grand["l1_post_exits"]) / grand["l1_post_at_risk"] * 100) if grand["l1_post_at_risk"] > 0 else 0
    l2_pre_surv = (grand["l2_pre_surv"] / grand["l2_pre_elig"] * 100) if grand["l2_pre_elig"] > 0 else 0
    l2_post_surv = (grand["l2_post_surv"] / grand["l2_post_elig"] * 100) if grand["l2_post_elig"] > 0 else 0
    l3_pre_ret = ((grand["l3_pre_count"] - grand["l3_pre_exits"]) / grand["l3_pre_count"] * 100) if grand["l3_pre_count"] > 0 else 0
    l3_post_ret = ((grand["l3_post_count"] - grand["l3_post_exits"]) / grand["l3_post_count"] * 100) if grand["l3_post_count"] > 0 else 0

    print(f"\n  L1 Original Staff Retention:")
    print(f"     Pre-EP:  {l1_pre_ret:.1f}% retained ({grand['l1_pre_exits']}/{grand['l1_orig']} exited)")
    print(f"     Post-EP: {l1_post_ret:.1f}% retained ({grand['l1_post_exits']}/{grand['l1_post_at_risk']} exited)")

    print(f"\n  L2 90-Day Cliff Survival (Stable Hire):")
    print(f"     Pre-EP:  {l2_pre_surv:.1f}% survived ({grand['l2_pre_surv']}/{grand['l2_pre_elig']} eligible)")
    print(f"     Post-EP: {l2_post_surv:.1f}% survived ({grand['l2_post_surv']}/{grand['l2_post_elig']} eligible)")

    print(f"\n  L3 Post-Cliff Retention:")
    print(f"     Pre-EP:  {l3_pre_ret:.1f}% retained ({grand['l3_pre_exits']}/{grand['l3_pre_count']} exited)")
    print(f"     Post-EP: {l3_post_ret:.1f}% retained ({grand['l3_post_exits']}/{grand['l3_post_count']} exited)")

    # Per-type breakdown
    print(f"\n{'='*80}")
    print(f"BY RESTAURANT TYPE")
    print(f"{'='*80}")
    print(f"  {'Type':<22} {'L1 Pre':>8} {'L1 Post':>8} {'L2 Pre':>8} {'L2 Post':>8} {'L3 Pre':>8} {'L3 Post':>8}")
    print(f"  {'':<22} {'retain':>8} {'retain':>8} {'cliff%':>8} {'cliff%':>8} {'retain':>8} {'retain':>8}")
    print(f"  {'-'*22} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")

    for pkey in _PROFILE_ROTATION:
        ta = type_agg.get(pkey)
        if not ta:
            continue

        tl1_pre = ((ta["l1_orig"] - ta["l1_pre_exits"]) / ta["l1_orig"] * 100) if ta["l1_orig"] > 0 else 0
        tl1_post = ((ta["l1_post_at_risk"] - ta["l1_post_exits"]) / ta["l1_post_at_risk"] * 100) if ta["l1_post_at_risk"] > 0 else 0
        tl2_pre = (ta["l2_pre_surv"] / ta["l2_pre_elig"] * 100) if ta["l2_pre_elig"] > 0 else 0
        tl2_post = (ta["l2_post_surv"] / ta["l2_post_elig"] * 100) if ta["l2_post_elig"] > 0 else 0
        tl3_pre = ((ta["l3_pre_count"] - ta["l3_pre_exits"]) / ta["l3_pre_count"] * 100) if ta["l3_pre_count"] > 0 else 0
        tl3_post = ((ta["l3_post_count"] - ta["l3_post_exits"]) / ta["l3_post_count"] * 100) if ta["l3_post_count"] > 0 else 0

        print(f"  {pkey:<22} {tl1_pre:>7.1f}% {tl1_post:>7.1f}% {tl2_pre:>7.1f}% {tl2_post:>7.1f}% {tl3_pre:>7.1f}% {tl3_post:>7.1f}%")

    print()


# -------------------------------------------------------------
# ENTRY POINT
# -------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run En Place synthetic simulation")
    parser.add_argument("--upload", action="store_true", help="Upload to Supabase")
    parser.add_argument("--upload-only", action="store_true", help="Upload only, no CSV")
    args = parser.parse_args()

    run_full_simulation(
        write_csv_flag=not args.upload_only,
        upload_supabase=args.upload or args.upload_only,
    )