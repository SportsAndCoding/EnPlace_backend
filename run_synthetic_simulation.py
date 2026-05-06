"""
run_synthetic_simulation.py v4

Quieter output — one line per restaurant instead of 6.
Full summary at end. Avoids Heroku buffer cutoff.
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


ADOPTION_DAY = 183
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


def _deterministic_staff_count(organization_id, profile_key):
    import hashlib
    low, high = _STAFF_COUNTS.get(profile_key, (40, 60))
    seed = int(hashlib.sha256(f"{organization_id}:staff_count".encode()).hexdigest()[:8], 16)
    return low + (seed % (high - low + 1))


def build_restaurant_configs():
    configs = []
    for i in range(100):
        rid = 101 + i
        pkey = _PROFILE_ROTATION[i % len(_PROFILE_ROTATION)]
        configs.append({
            "organization_id": rid,
            "profile_key": pkey,
            "num_staff": _deterministic_staff_count(rid, pkey),
            "num_days": SIM_DAYS,
            "adoption_day": ADOPTION_DAY,
        })
    return configs


RESTAURANTS_TO_SIMULATE = build_restaurant_configs()


def compute_three_level_analysis(staff_master, target_headcount, adoption_day=ADOPTION_DAY):
    originals = [s for s in staff_master if s["hire_day"] == 0]
    replacements = [s for s in staff_master if s["hire_day"] > 0]

    orig_count = len(originals)

    l1_pre_exits = sum(1 for s in originals if s["exit_day"] is not None and s["exit_day"] <= adoption_day)
    l1_pre_survived = orig_count - l1_pre_exits
    l1_pre_retention = ((orig_count - l1_pre_exits) / orig_count * 100) if orig_count > 0 else 0

    l1_post_exits = sum(1 for s in originals if s["exit_day"] is not None and s["exit_day"] > adoption_day)
    l1_post_retention = ((l1_pre_survived - l1_post_exits) / l1_pre_survived * 100) if l1_pre_survived > 0 else 100

    pre_replacements = [s for s in replacements if s["hire_day"] < adoption_day]
    pre_repl_eligible = [s for s in pre_replacements if s["hire_day"] + 90 <= SIM_DAYS]
    pre_repl_survived_90 = sum(1 for s in pre_repl_eligible if s["total_days"] >= 90)
    l2_pre_count = len(pre_repl_eligible)
    l2_pre_cliff_survival = (pre_repl_survived_90 / l2_pre_count * 100) if l2_pre_count > 0 else 0

    post_replacements = [s for s in replacements if s["hire_day"] >= adoption_day]
    post_repl_eligible = [s for s in post_replacements if s["hire_day"] + 90 <= SIM_DAYS]
    post_repl_survived_90 = sum(1 for s in post_repl_eligible if s["total_days"] >= 90)
    l2_post_count = len(post_repl_eligible)
    l2_post_cliff_survival = (post_repl_survived_90 / l2_post_count * 100) if l2_post_count > 0 else 0

    stable_hire_count = sum(1 for s in post_replacements if s.get("hired_with_stable_hire"))

    pre_cliff_survivors = [s for s in pre_repl_eligible if s["total_days"] >= 90]
    pre_cliff_then_exited = sum(1 for s in pre_cliff_survivors if s["final_persona"] == "exit")
    l3_pre_count = len(pre_cliff_survivors)
    l3_pre_retention = ((l3_pre_count - pre_cliff_then_exited) / l3_pre_count * 100) if l3_pre_count > 0 else 100

    post_cliff_survivors = [s for s in post_repl_eligible if s["total_days"] >= 90]
    post_cliff_then_exited = sum(1 for s in post_cliff_survivors if s["final_persona"] == "exit")
    l3_post_count = len(post_cliff_survivors)
    l3_post_retention = ((l3_post_count - post_cliff_then_exited) / l3_post_count * 100) if l3_post_count > 0 else 100

    total_pre_exits = sum(1 for s in staff_master if s["exit_day"] is not None and s["exit_day"] <= adoption_day)
    total_post_exits = sum(1 for s in staff_master if s["exit_day"] is not None and s["exit_day"] > adoption_day)
    pre_annualized = (total_pre_exits / target_headcount) * (365 / ADOPTION_DAY) * 100
    post_annualized = (total_post_exits / target_headcount) * (365 / (SIM_DAYS - ADOPTION_DAY)) * 100

    return {
        "total_staff_records": len(staff_master),
        "total_pre_exits": total_pre_exits,
        "total_post_exits": total_post_exits,
        "pre_annualized_turnover": round(pre_annualized, 1),
        "post_annualized_turnover": round(post_annualized, 1),
        "l1_original_count": orig_count,
        "l1_pre_exits": l1_pre_exits,
        "l1_pre_retention_pct": round(l1_pre_retention, 1),
        "l1_post_at_risk": l1_pre_survived,
        "l1_post_exits": l1_post_exits,
        "l1_post_retention_pct": round(l1_post_retention, 1),
        "l2_pre_eligible": l2_pre_count,
        "l2_pre_survived_90d": pre_repl_survived_90,
        "l2_pre_cliff_survival_pct": round(l2_pre_cliff_survival, 1),
        "l2_post_eligible": l2_post_count,
        "l2_post_survived_90d": post_repl_survived_90,
        "l2_post_cliff_survival_pct": round(l2_post_cliff_survival, 1),
        "l2_stable_hire_count": stable_hire_count,
        "l3_pre_count": l3_pre_count,
        "l3_pre_exits": pre_cliff_then_exited,
        "l3_pre_retention_pct": round(l3_pre_retention, 1),
        "l3_post_count": l3_post_count,
        "l3_post_exits": post_cliff_then_exited,
        "l3_post_retention_pct": round(l3_post_retention, 1),
    }


def ensure_output_dir():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)


def write_csv(filename, rows):
    path = os.path.join(OUTPUT_DIR, filename)
    if not rows:
        return
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def write_jsonl(filename, rows):
    path = os.path.join(OUTPUT_DIR, filename)
    if not rows:
        return
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def get_supabase():
    from database.supabase_client import supabase
    return supabase


def batch_insert(table_name, rows):
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


def truncate_table(table_name):
    sb = get_supabase()
    try:
        sb.table(table_name).delete().gte("id", 0).execute()
    except Exception:
        try:
            sb.table(table_name).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        except Exception:
            pass


def flatten_graph_snapshots(snapshots, organization_id):
    rows = []
    for snap in snapshots:
        meta = snap.get("metadata", {})
        rows.append({
            "organization_id": organization_id,
            "day_index": meta.get("day_index", 0),
            "active_staff": meta.get("active_staff_count", 0),
            "edge_count": meta.get("edge_count", 0),
            "graph_density": meta.get("graph_density", 0),
            "avg_criticality": meta.get("avg_criticality", 0),
            "avg_mood": meta.get("avg_mood", 0),
            "snapshot_data": json.dumps(snap, default=str),
        })
    return rows


def flatten_exit_cascades(cascades, organization_id):
    rows = []
    for cas in cascades:
        rows.append({
            "staff_id": cas["staff_id"],
            "organization_id": organization_id,
            "day_index": cas["day_index"],
            "exit_reason": cas.get("exit_reason"),
            "cascade_severity": cas.get("cascade_severity"),
            "expected_additional_exits": cas.get("expected_additional_exits", 0),
            "worst_case_exits": cas.get("worst_case_exits", 0),
            "at_risk_staff": json.dumps(cas.get("at_risk_staff", []), default=str),
            "cascade_viz_data": "{}",
        })
    return rows


def run_full_simulation(write_csv_flag=True, upload_supabase=False):
    ensure_output_dir()

    if write_csv_flag:
        for fn in ["staff_master.csv", "daily_emotions.csv", "daily_behavior.csv",
                    "exit_cascades.csv", "graph_snapshots.jsonl", "restaurant_meta.csv"]:
            open(os.path.join(OUTPUT_DIR, fn), "w").close()

    if upload_supabase:
        print("Truncating tables...")
        for t in ["synthetic_staff_master", "synthetic_daily_emotions",
                   "synthetic_daily_behavior", "synthetic_graph_snapshots",
                   "synthetic_exit_cascades", "synthetic_restaurants"]:
            truncate_table(t)

    all_staff_master = []
    all_daily_emotions = []
    all_daily_behavior = []
    all_graph_snapshots = []
    all_exit_cascades = []
    all_restaurant_meta = []

    type_agg: Dict[str, Dict[str, Any]] = {}

    total = len(RESTAURANTS_TO_SIMULATE)
    sim_start = time.time()

    # Compact header
    print(f"{'#':>3} {'RID':>4} {'Type':<18} {'Staff':>5} {'Recs':>5} "
          f"{'Pre%':>5} {'Post%':>5} {'L1pre':>5} {'L1pst':>5} "
          f"{'L2pre':>5} {'L2pst':>5} {'L2n':>4} {'Time':>5}")
    print("-" * 95)

    for idx, config in enumerate(RESTAURANTS_TO_SIMULATE):
        rid = config["organization_id"]
        pkey = config["profile_key"]
        num_staff = config["num_staff"]
        adoption_day = config["adoption_day"]

        r_start = time.time()

        profile = get_profile(pkey)
        ep_config = get_en_place_config(rid, pkey, adoption_day)

        results = simulate_restaurant(
            organization_id=rid,
            number_of_staff=num_staff,
            simulation_days=SIM_DAYS,
            persona_weights=DEFAULT_PERSONA_WEIGHTS,
            restaurant_profile=profile,
            enable_contagion=False,
            graph_snapshot_interval=GRAPH_SNAPSHOT_INTERVAL,
            en_place_config=ep_config,
            enable_replacement_hiring=True,
        )

        analysis = compute_three_level_analysis(results["staff_master"], num_staff, adoption_day)
        r_elapsed = time.time() - r_start

        # One compact line per restaurant
        print(f"{idx+1:>3} {rid:>4} {pkey:<18} {num_staff:>5} {analysis['total_staff_records']:>5} "
              f"{analysis['pre_annualized_turnover']:>4.0f}% {analysis['post_annualized_turnover']:>4.0f}% "
              f"{analysis['l1_pre_retention_pct']:>4.0f}% {analysis['l1_post_retention_pct']:>4.0f}% "
              f"{analysis['l2_pre_cliff_survival_pct']:>4.0f}% {analysis['l2_post_cliff_survival_pct']:>4.0f}% "
              f"{analysis['l2_post_eligible']:>4} {r_elapsed:>4.1f}s")

        meta = {
            "organization_id": rid,
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

        all_staff_master.extend(results["staff_master"])
        all_daily_emotions.extend(results["daily_emotions"])
        all_daily_behavior.extend(results["daily_behavior"])
        all_graph_snapshots.extend(flatten_graph_snapshots(results["graph_snapshots"], rid))
        all_exit_cascades.extend(flatten_exit_cascades(results["exit_cascades"], rid))

        if upload_supabase:
            batch_insert("synthetic_restaurants", [{
                "organization_id": rid, "profile_key": pkey,
                "num_staff": num_staff, "num_days": SIM_DAYS, "sma_score": None,
            }])
            batch_insert("synthetic_staff_master", results["staff_master"])
            batch_insert("synthetic_daily_emotions", results["daily_emotions"])
            batch_insert("synthetic_daily_behavior", results["daily_behavior"])

    if write_csv_flag:
        write_csv("staff_master.csv", all_staff_master)
        write_csv("daily_emotions.csv", all_daily_emotions)
        write_csv("daily_behavior.csv", all_daily_behavior)
        write_csv("exit_cascades.csv", all_exit_cascades)
        write_csv("restaurant_meta.csv", all_restaurant_meta)
        write_jsonl("graph_snapshots.jsonl", all_graph_snapshots)

    total_elapsed = time.time() - sim_start

    # =================================================================
    # SUMMARY
    # =================================================================
    grand = {
        "l1_orig": 0, "l1_pre_exits": 0, "l1_post_at_risk": 0, "l1_post_exits": 0,
        "l2_pre_elig": 0, "l2_pre_surv": 0, "l2_post_elig": 0, "l2_post_surv": 0,
        "l3_pre_count": 0, "l3_pre_exits": 0, "l3_post_count": 0, "l3_post_exits": 0,
        "headcount": 0, "total_pre": 0, "total_post": 0,
    }
    for ta in type_agg.values():
        grand["headcount"] += ta["headcount"]
        grand["total_pre"] += ta["total_pre_exits"]
        grand["total_post"] += ta["total_post_exits"]
        for k in ["l1_orig", "l1_pre_exits", "l1_post_at_risk", "l1_post_exits",
                   "l2_pre_elig", "l2_pre_surv", "l2_post_elig", "l2_post_surv",
                   "l3_pre_count", "l3_pre_exits", "l3_post_count", "l3_post_exits"]:
            grand[k] += ta[k]

    hc = grand["headcount"]
    pre_ann = (grand["total_pre"] / hc) * (365 / ADOPTION_DAY) * 100 if hc else 0
    post_ann = (grand["total_post"] / hc) * (365 / (SIM_DAYS - ADOPTION_DAY)) * 100 if hc else 0

    l1r_pre = ((grand["l1_orig"] - grand["l1_pre_exits"]) / grand["l1_orig"] * 100) if grand["l1_orig"] else 0
    l1r_post = ((grand["l1_post_at_risk"] - grand["l1_post_exits"]) / grand["l1_post_at_risk"] * 100) if grand["l1_post_at_risk"] else 0
    l2_pre = (grand["l2_pre_surv"] / grand["l2_pre_elig"] * 100) if grand["l2_pre_elig"] else 0
    l2_post = (grand["l2_post_surv"] / grand["l2_post_elig"] * 100) if grand["l2_post_elig"] else 0
    l3_pre = ((grand["l3_pre_count"] - grand["l3_pre_exits"]) / grand["l3_pre_count"] * 100) if grand["l3_pre_count"] else 0
    l3_post = ((grand["l3_post_count"] - grand["l3_post_exits"]) / grand["l3_post_count"] * 100) if grand["l3_post_count"] else 0

    print(f"\n{'='*80}")
    print(f"COMPLETE — {total} restaurants, {len(all_staff_master):,} staff, "
          f"{len(all_daily_emotions):,} emotion rows, {total_elapsed:.0f}s")
    print(f"{'='*80}")

    print(f"\n  AGGREGATE TURNOVER:  Pre-EP {pre_ann:.0f}% → Post-EP {post_ann:.0f}%")

    print(f"\n  L1 Original Staff Retention:")
    print(f"     Pre:  {l1r_pre:.1f}% ({grand['l1_pre_exits']}/{grand['l1_orig']} exited)")
    print(f"     Post: {l1r_post:.1f}% ({grand['l1_post_exits']}/{grand['l1_post_at_risk']} exited)")

    print(f"\n  L2 90-Day Cliff Survival:")
    print(f"     Pre:  {l2_pre:.1f}% ({grand['l2_pre_surv']}/{grand['l2_pre_elig']})")
    print(f"     Post: {l2_post:.1f}% ({grand['l2_post_surv']}/{grand['l2_post_elig']})")

    print(f"\n  L3 Post-Cliff Retention:")
    print(f"     Pre:  {l3_pre:.1f}% ({grand['l3_pre_exits']}/{grand['l3_pre_count']} exited)")
    print(f"     Post: {l3_post:.1f}% ({grand['l3_post_exits']}/{grand['l3_post_count']} exited)")

    print(f"\n{'='*80}")
    print("BY TYPE")
    print(f"{'='*80}")
    print(f"  {'Type':<22} {'Pre%':>5} {'Post%':>5} {'L1pre':>6} {'L1pst':>6} "
          f"{'L2pre':>6} {'L2pst':>6} {'L3pre':>6} {'L3pst':>6}")
    print(f"  {'-'*22} {'-'*5} {'-'*5} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")

    for pkey in _PROFILE_ROTATION:
        ta = type_agg.get(pkey)
        if not ta:
            continue
        thc = ta["headcount"]
        t_pre = (ta["total_pre_exits"] / thc) * (365 / ADOPTION_DAY) * 100 if thc else 0
        t_post = (ta["total_post_exits"] / thc) * (365 / (SIM_DAYS - ADOPTION_DAY)) * 100 if thc else 0
        tl1_pre = ((ta["l1_orig"] - ta["l1_pre_exits"]) / ta["l1_orig"] * 100) if ta["l1_orig"] else 0
        tl1_post = ((ta["l1_post_at_risk"] - ta["l1_post_exits"]) / ta["l1_post_at_risk"] * 100) if ta["l1_post_at_risk"] else 0
        tl2_pre = (ta["l2_pre_surv"] / ta["l2_pre_elig"] * 100) if ta["l2_pre_elig"] else 0
        tl2_post = (ta["l2_post_surv"] / ta["l2_post_elig"] * 100) if ta["l2_post_elig"] else 0
        tl3_pre = ((ta["l3_pre_count"] - ta["l3_pre_exits"]) / ta["l3_pre_count"] * 100) if ta["l3_pre_count"] else 0
        tl3_post = ((ta["l3_post_count"] - ta["l3_post_exits"]) / ta["l3_post_count"] * 100) if ta["l3_post_count"] else 0

        print(f"  {pkey:<22} {t_pre:>4.0f}% {t_post:>4.0f}% "
              f"{tl1_pre:>5.0f}% {tl1_post:>5.0f}% "
              f"{tl2_pre:>5.0f}% {tl2_post:>5.0f}% "
              f"{tl3_pre:>5.0f}% {tl3_post:>5.0f}%")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--upload-only", action="store_true")
    args = parser.parse_args()

    run_full_simulation(
        write_csv_flag=not args.upload_only,
        upload_supabase=args.upload or args.upload_only,
    )