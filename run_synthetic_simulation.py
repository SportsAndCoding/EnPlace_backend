"""
run_synthetic_simulation.py

Runs the full synthetic staffing simulation across 100 restaurants.
Every restaurant runs 365 days with En Place activating at day 183
(the 6-month mark). Replacement hiring maintains headcount.

OUTPUT: Two-column comparison per restaurant:
  Pre-EP  (days 0-182):   annualized turnover WITHOUT En Place
  Post-EP (days 183-365): annualized turnover WITH En Place

Same restaurant, same environment, same manager. Only variable: En Place.

OUTPUT MODES:
    CSV:      Flat files for all tables + restaurant_meta with per-period stats.
    SUPABASE: Direct batch insert into synthetic_* tables.

USAGE:
    python run_synthetic_simulation.py                  # CSV only
    python run_synthetic_simulation.py --upload          # CSV + Supabase upload
    python run_synthetic_simulation.py --upload-only     # Supabase only, no CSV
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
from modules.synthetic.en_place_effect import get_en_place_config


# -------------------------------------------------------------
# 1. CONFIGURATION
# -------------------------------------------------------------

ADOPTION_DAY = 183  # 6-month mark — EP activates here for all restaurants

# Pre-EP period: day_index 0 to 182 → exit_day 1 to 183 (183 days)
# Post-EP period: day_index 183 to 364 → exit_day 184 to 365 (182 days)
PRE_EP_DAYS = 183
POST_EP_DAYS = 182

# Restaurant type rotation for even distribution
_PROFILE_ROTATION = [
    "steakhouse", "sports_bar", "fast_casual", "neighborhood_bistro",
    "upscale_casual", "family_diner", "breakfast_cafe", "bar_and_grille",
    "high_volume_chain", "college_town_cafe", "hotel_restaurant",
    "airport_restaurant",
]

# Staff counts vary by type (realistic sizing)
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


def _deterministic_staff_count(restaurant_id: int, profile_key: str) -> int:
    """Pick a deterministic staff count within range for this restaurant."""
    import hashlib
    low, high = _STAFF_COUNTS.get(profile_key, (40, 60))
    seed = int(hashlib.sha256(f"{restaurant_id}:staff_count".encode()).hexdigest()[:8], 16)
    return low + (seed % (high - low + 1))


def build_restaurant_configs() -> List[Dict[str, Any]]:
    """
    Build the full list of restaurant simulation configurations.
    100 restaurants, all adopters with EP activating at ADOPTION_DAY.
    ~8-9 of each restaurant type for balanced representation.
    """
    configs = []

    for i in range(100):
        rid = 101 + i
        profile_key = _PROFILE_ROTATION[i % len(_PROFILE_ROTATION)]
        configs.append({
            "restaurant_id": rid,
            "profile_key": profile_key,
            "num_staff": _deterministic_staff_count(rid, profile_key),
            "num_days": 365,
            "adoption_day": ADOPTION_DAY,
        })

    return configs


RESTAURANTS_TO_SIMULATE = build_restaurant_configs()

DEFAULT_PERSONA_WEIGHTS = {
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

OUTPUT_DIR = "synthetic_output"
GRAPH_SNAPSHOT_INTERVAL = 7
SUPABASE_BATCH_SIZE = 500


# -------------------------------------------------------------
# 2. PERIOD TURNOVER CALCULATIONS
# -------------------------------------------------------------

def compute_period_turnover(
    staff_master: List[Dict[str, Any]],
    target_headcount: int,
) -> Dict[str, Any]:
    """
    Compute annualized turnover for pre-EP and post-EP periods.

    Pre-EP:  exits where exit_day <= ADOPTION_DAY (days 0-182)
    Post-EP: exits where exit_day > ADOPTION_DAY (days 183-365)

    Annualized = (exits / headcount) * (365 / period_days)

    With replacement hiring, headcount stays at target_headcount.
    """
    pre_exits = 0
    post_exits = 0

    for staff in staff_master:
        ed = staff.get("exit_day")
        if ed is not None:
            if ed <= ADOPTION_DAY:
                pre_exits += 1
            else:
                post_exits += 1

    pre_annualized = (pre_exits / target_headcount) * (365 / PRE_EP_DAYS) * 100
    post_annualized = (post_exits / target_headcount) * (365 / POST_EP_DAYS) * 100

    return {
        "pre_ep_exits": pre_exits,
        "post_ep_exits": post_exits,
        "pre_ep_annualized_turnover": round(pre_annualized, 1),
        "post_ep_annualized_turnover": round(post_annualized, 1),
        "delta": round(pre_annualized - post_annualized, 1),
        "pct_improvement": round(
            ((pre_annualized - post_annualized) / pre_annualized * 100)
            if pre_annualized > 0 else 0, 1
        ),
    }


# -------------------------------------------------------------
# 3. CSV EXPORT HELPERS
# -------------------------------------------------------------

def ensure_output_dir():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)


def write_csv(filename: str, rows: List[Dict[str, Any]]):
    """Write flat rows to CSV (append mode)."""
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
    """Write rows as JSON Lines (for nested data that doesn't fit CSV)."""
    path = os.path.join(OUTPUT_DIR, filename)
    if not rows:
        print(f"  [WARN] No rows for {filename}")
        return

    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")

    print(f"  [JSONL] {len(rows):,} rows -> {filename}")


# -------------------------------------------------------------
# 4. SUPABASE UPLOAD HELPERS
# -------------------------------------------------------------

def get_supabase():
    """Lazy import — only load Supabase client when needed."""
    from database.supabase_client import supabase
    return supabase


def batch_insert(table_name: str, rows: List[Dict[str, Any]]):
    """Insert rows into Supabase in batches."""
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
            print(f"  [ERROR] Batch insert to {table_name} failed at row {i}: {e}")
            continue

    return total


def truncate_table(table_name: str):
    """Delete all rows from a synthetic table before re-populating."""
    sb = get_supabase()
    try:
        sb.table(table_name).delete().gte("id", 0).execute()
        print(f"  [TRUNCATE] {table_name}")
    except Exception as e:
        try:
            sb.table(table_name).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
            print(f"  [TRUNCATE] {table_name}")
        except Exception as e2:
            print(f"  [WARN] Could not truncate {table_name}: {e2}")


# -------------------------------------------------------------
# 5. DATA FLATTENING
# -------------------------------------------------------------

def flatten_graph_snapshots(
    snapshots: List[Dict[str, Any]],
    restaurant_id: int,
) -> List[Dict[str, Any]]:
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


def flatten_exit_cascades(
    cascades: List[Dict[str, Any]],
    restaurant_id: int,
) -> List[Dict[str, Any]]:
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
            "cascade_viz_data": json.dumps({
                "node_states_before": cas.get("node_states_before", {}),
                "node_states_after": cas.get("node_states_after", {}),
                "removed_edges": cas.get("removed_edges", []),
            }, default=str),
        })
    return rows


# -------------------------------------------------------------
# 6. MAIN PIPELINE
# -------------------------------------------------------------

def run_full_simulation(write_csv_flag: bool = True, upload_supabase: bool = False):
    ensure_output_dir()

    # Truncate CSV files for fresh run
    if write_csv_flag:
        for filename in [
            "staff_master.csv",
            "daily_emotions.csv",
            "daily_behavior.csv",
            "exit_cascades.csv",
            "graph_snapshots.jsonl",
            "restaurant_meta.csv",
        ]:
            path = os.path.join(OUTPUT_DIR, filename)
            open(path, "w").close()

    # Truncate Supabase tables if uploading
    if upload_supabase:
        print("\n--- Truncating Supabase tables ---")
        for table in [
            "synthetic_staff_master",
            "synthetic_daily_emotions",
            "synthetic_daily_behavior",
            "synthetic_graph_snapshots",
            "synthetic_exit_cascades",
            "synthetic_restaurants",
        ]:
            truncate_table(table)

    # Accumulators
    combined_staff_master = []
    combined_daily_emotions = []
    combined_daily_behavior = []
    combined_graph_snapshots = []
    combined_exit_cascades = []
    combined_restaurant_meta = []

    # Aggregate tracking
    agg_pre_exits = 0
    agg_post_exits = 0
    agg_headcount = 0

    # Per-type tracking
    type_stats: Dict[str, Dict[str, float]] = {}

    total_restaurants = len(RESTAURANTS_TO_SIMULATE)
    sim_start = time.time()

    for idx, config in enumerate(RESTAURANTS_TO_SIMULATE):
        restaurant_id = config["restaurant_id"]
        profile_key = config["profile_key"]
        num_staff = config["num_staff"]
        num_days = config["num_days"]
        adoption_day = config["adoption_day"]

        r_start = time.time()
        print(f"\n=== [{idx + 1}/{total_restaurants}] Restaurant {restaurant_id} "
              f"({profile_key}, {num_staff} staff, adoption day {adoption_day}) ===")

        profile = get_profile(profile_key)

        # Generate En Place effect config
        ep_config = get_en_place_config(
            restaurant_id=restaurant_id,
            profile_key=profile_key,
            adoption_day=adoption_day,
        )

        print(f"  EP effectiveness: {ep_config['restaurant_effectiveness']:.2f}, "
              f"industry variance: {ep_config['industry_variance']:.2f}")
        print(f"  Without EP exit mod: {ep_config['without_ep']['exit_modifier']:.3f}, "
              f"With EP exit mod: {ep_config['with_ep']['exit_modifier']:.3f}")

        results = simulate_restaurant(
            restaurant_id=restaurant_id,
            number_of_staff=num_staff,
            simulation_days=num_days,
            persona_weights=DEFAULT_PERSONA_WEIGHTS,
            restaurant_profile=profile,
            enable_contagion=True,
            graph_snapshot_interval=GRAPH_SNAPSHOT_INTERVAL,
            en_place_config=ep_config,
            enable_replacement_hiring=True,
        )

        # Compute per-period turnover
        period_stats = compute_period_turnover(results["staff_master"], num_staff)

        # Core tables
        combined_staff_master.extend(results["staff_master"])
        combined_daily_emotions.extend(results["daily_emotions"])
        combined_daily_behavior.extend(results["daily_behavior"])

        # Graph tables
        graph_snap_rows = flatten_graph_snapshots(results["graph_snapshots"], restaurant_id)
        cascade_rows = flatten_exit_cascades(results["exit_cascades"], restaurant_id)
        combined_graph_snapshots.extend(graph_snap_rows)
        combined_exit_cascades.extend(cascade_rows)

        # Restaurant metadata
        restaurant_meta = {
            "restaurant_id": restaurant_id,
            "profile_key": profile_key,
            "num_staff": num_staff,
            "num_days": num_days,
            "adoption_day": adoption_day,
            "ep_effectiveness": ep_config["restaurant_effectiveness"],
            "industry_variance": ep_config["industry_variance"],
            "without_ep_exit_mod": ep_config["without_ep"]["exit_modifier"],
            "with_ep_exit_mod": ep_config["with_ep"]["exit_modifier"],
            "total_staff_records": len(results["staff_master"]),
            "pre_ep_exits": period_stats["pre_ep_exits"],
            "post_ep_exits": period_stats["post_ep_exits"],
            "pre_ep_annualized_turnover": period_stats["pre_ep_annualized_turnover"],
            "post_ep_annualized_turnover": period_stats["post_ep_annualized_turnover"],
            "delta": period_stats["delta"],
            "pct_improvement": period_stats["pct_improvement"],
        }
        combined_restaurant_meta.append(restaurant_meta)

        # Aggregate tracking
        agg_pre_exits += period_stats["pre_ep_exits"]
        agg_post_exits += period_stats["post_ep_exits"]
        agg_headcount += num_staff

        # Per-type tracking
        if profile_key not in type_stats:
            type_stats[profile_key] = {"headcount": 0, "pre_exits": 0, "post_exits": 0}
        type_stats[profile_key]["headcount"] += num_staff
        type_stats[profile_key]["pre_exits"] += period_stats["pre_ep_exits"]
        type_stats[profile_key]["post_exits"] += period_stats["post_ep_exits"]

        r_elapsed = time.time() - r_start
        print(f"  Done in {r_elapsed:.1f}s — "
              f"{len(results['staff_master'])} total staff records, "
              f"{period_stats['pre_ep_exits']} pre-EP exits, "
              f"{period_stats['post_ep_exits']} post-EP exits")
        print(f"  Pre-EP:  {period_stats['pre_ep_annualized_turnover']:.1f}% annualized")
        print(f"  Post-EP: {period_stats['post_ep_annualized_turnover']:.1f}% annualized")
        print(f"  Delta:   -{period_stats['delta']:.1f} pts "
              f"({period_stats['pct_improvement']:.1f}% improvement)")

        # Per-restaurant upload
        if upload_supabase:
            sb_restaurant = {
                "restaurant_id": restaurant_id,
                "profile_key": profile_key,
                "num_staff": num_staff,
                "num_days": num_days,
                "sma_score": None,
            }
            batch_insert("synthetic_restaurants", [sb_restaurant])

            inserted = batch_insert("synthetic_staff_master", results["staff_master"])
            print(f"  [DB] staff_master: {inserted}")

            inserted = batch_insert("synthetic_daily_emotions", results["daily_emotions"])
            print(f"  [DB] daily_emotions: {inserted}")

            inserted = batch_insert("synthetic_daily_behavior", results["daily_behavior"])
            print(f"  [DB] daily_behavior: {inserted}")

            if graph_snap_rows:
                inserted = batch_insert("synthetic_graph_snapshots", graph_snap_rows)
                print(f"  [DB] graph_snapshots: {inserted}")

            if cascade_rows:
                inserted = batch_insert("synthetic_exit_cascades", cascade_rows)
                print(f"  [DB] exit_cascades: {inserted}")

    # ---------------------------------------------------------
    # CSV EXPORT
    # ---------------------------------------------------------
    if write_csv_flag:
        print("\n--- Writing CSV/JSONL files ---")
        write_csv("staff_master.csv", combined_staff_master)
        write_csv("daily_emotions.csv", combined_daily_emotions)
        write_csv("daily_behavior.csv", combined_daily_behavior)
        write_csv("exit_cascades.csv", combined_exit_cascades)
        write_csv("restaurant_meta.csv", combined_restaurant_meta)
        write_jsonl("graph_snapshots.jsonl", combined_graph_snapshots)

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------
    total_elapsed = time.time() - sim_start

    agg_pre_ann = (agg_pre_exits / agg_headcount) * (365 / PRE_EP_DAYS) * 100
    agg_post_ann = (agg_post_exits / agg_headcount) * (365 / POST_EP_DAYS) * 100

    print(f"\n{'='*70}")
    print(f"ALL SIMULATIONS COMPLETE — {total_elapsed:.1f}s")
    print(f"{'='*70}")
    print(f"  Total restaurants:  {total_restaurants}")
    print(f"  Staff records:      {len(combined_staff_master):,}")
    print(f"  Emotion rows:       {len(combined_daily_emotions):,}")
    print(f"  Behavior rows:      {len(combined_daily_behavior):,}")
    print(f"  Graph snapshots:    {len(combined_graph_snapshots):,}")
    print(f"  Exit cascades:      {len(combined_exit_cascades):,}")

    print(f"\n{'='*70}")
    print(f"AGGREGATE RESULTS (all {total_restaurants} restaurants)")
    print(f"{'='*70}")
    print(f"  Total headcount:     {agg_headcount:,}")
    print(f"  Pre-EP exits:        {agg_pre_exits:,}")
    print(f"  Post-EP exits:       {agg_post_exits:,}")
    print(f"  Pre-EP turnover:     {agg_pre_ann:.1f}% (annualized)")
    print(f"  Post-EP turnover:    {agg_post_ann:.1f}% (annualized)")
    print(f"  Delta:               -{agg_pre_ann - agg_post_ann:.1f} pts")
    print(f"  Improvement:         {((agg_pre_ann - agg_post_ann) / agg_pre_ann * 100):.1f}%")

    print(f"\n{'='*70}")
    print(f"TURNOVER BY RESTAURANT TYPE")
    print(f"{'='*70}")
    print(f"  {'Type':<22} {'Pre-EP':>10} {'Post-EP':>10} {'Delta':>10} {'Improv':>10}")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

    for pkey in _PROFILE_ROTATION:
        data = type_stats.get(pkey)
        if data and data["headcount"] > 0:
            pre = (data["pre_exits"] / data["headcount"]) * (365 / PRE_EP_DAYS) * 100
            post = (data["post_exits"] / data["headcount"]) * (365 / POST_EP_DAYS) * 100
            delta = pre - post
            improv = (delta / pre * 100) if pre > 0 else 0
            print(f"  {pkey:<22} {pre:>8.1f}% {post:>8.1f}% {delta:>+8.1f} {improv:>8.1f}%")

    print()


# -------------------------------------------------------------
# 7. ENTRY POINT
# -------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run En Place synthetic simulation")
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload results to Supabase after CSV export",
    )
    parser.add_argument(
        "--upload-only",
        action="store_true",
        help="Upload to Supabase without writing CSV files",
    )
    args = parser.parse_args()

    write_csv_flag = not args.upload_only
    upload_flag = args.upload or args.upload_only

    run_full_simulation(
        write_csv_flag=write_csv_flag,
        upload_supabase=upload_flag,
    )