"""
run_synthetic_simulation.py

Runs the full synthetic staffing simulation across 120 restaurants in 3 cohorts,
producing 5 output tables: staff_master, daily_emotions, daily_behavior,
graph_snapshots, and exit_cascades.

COHORTS:
    Control (20 restaurants, IDs 301-320):
        Never adopt En Place. Run with industry-baseline exit penalties.
        These produce 75-80% average turnover with natural variance.

    Adopters (80 restaurants, IDs 101-200):
        Start without En Place, adopt on a staggered schedule (day 45-210).
        Shows the turnover curve bending after adoption. This is the story.

    Day-1 Network (20 restaurants, IDs 201-220):
        On En Place from day 0. Full benefit. Best-case reference.
        Target: 52-58% average turnover.

OUTPUT MODES:
    CSV:      Flat files for staff_master, daily_emotions, daily_behavior,
              exit_cascades. Graph snapshots get JSONL (one JSON object per line)
              because they contain nested visualization payloads.
    SUPABASE: Direct batch insert into all synthetic_* tables.

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

# Restaurant type rotation for even distribution across cohorts
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


def _deterministic_adoption_day(restaurant_id: int) -> int:
    """
    Pick a deterministic adoption day for adopter restaurants.
    Range: day 45 to day 210 (weeks 6-30).
    Weighted toward earlier adoption (most join in first 4 months).
    """
    import hashlib
    seed = int(hashlib.sha256(f"{restaurant_id}:adoption_day".encode()).hexdigest()[:8], 16)
    normalized = (seed % 1000) / 1000.0
    # Skew toward earlier: use square root to bias toward lower values
    skewed = normalized ** 0.7  # mild early-bias
    return int(45 + skewed * (210 - 45))


def build_restaurant_configs() -> List[Dict[str, Any]]:
    """
    Build the full list of restaurant simulation configurations.

    Returns list of dicts with:
        restaurant_id, profile_key, num_staff, num_days, cohort, adoption_day
    """
    configs = []

    # ------------------------------------------------------------------
    # COHORT 1: Control Group (IDs 301-320) — Never adopt EP
    # 20 restaurants, ~2 per type, evenly distributed
    # ------------------------------------------------------------------
    for i in range(20):
        rid = 301 + i
        profile_key = _PROFILE_ROTATION[i % len(_PROFILE_ROTATION)]
        configs.append({
            "restaurant_id": rid,
            "profile_key": profile_key,
            "num_staff": _deterministic_staff_count(rid, profile_key),
            "num_days": 365,
            "cohort": "control",
            "adoption_day": 9999,  # Never adopts within simulation window
        })

    # ------------------------------------------------------------------
    # COHORT 2: Adopters (IDs 101-180) — Staggered adoption
    # 80 restaurants, various adoption days between day 45-210
    # ------------------------------------------------------------------
    for i in range(80):
        rid = 101 + i
        profile_key = _PROFILE_ROTATION[i % len(_PROFILE_ROTATION)]
        adoption_day = _deterministic_adoption_day(rid)
        configs.append({
            "restaurant_id": rid,
            "profile_key": profile_key,
            "num_staff": _deterministic_staff_count(rid, profile_key),
            "num_days": 365,
            "cohort": "adopter",
            "adoption_day": adoption_day,
        })

    # ------------------------------------------------------------------
    # COHORT 3: Day-1 Network (IDs 201-220) — EP from day 0
    # 20 restaurants, ~2 per type
    # ------------------------------------------------------------------
    for i in range(20):
        rid = 201 + i
        profile_key = _PROFILE_ROTATION[i % len(_PROFILE_ROTATION)]
        configs.append({
            "restaurant_id": rid,
            "profile_key": profile_key,
            "num_staff": _deterministic_staff_count(rid, profile_key),
            "num_days": 365,
            "cohort": "day1",
            "adoption_day": 0,
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
GRAPH_SNAPSHOT_INTERVAL = 7   # days between graph snapshots
SUPABASE_BATCH_SIZE = 500     # rows per insert call


# -------------------------------------------------------------
# 2. CSV EXPORT HELPERS
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
# 3. SUPABASE UPLOAD HELPERS
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
            # Continue with next batch instead of dying
            continue

    return total


def truncate_table(table_name: str):
    """Delete all rows from a synthetic table before re-populating."""
    sb = get_supabase()
    try:
        # Supabase doesn't have TRUNCATE — use delete with always-true filter
        sb.table(table_name).delete().gte("id", 0).execute()
        print(f"  [TRUNCATE] {table_name}")
    except Exception as e:
        # Fallback for UUID PKs
        try:
            sb.table(table_name).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
            print(f"  [TRUNCATE] {table_name}")
        except Exception as e2:
            print(f"  [WARN] Could not truncate {table_name}: {e2}")


# -------------------------------------------------------------
# 4. DATA FLATTENING
# -------------------------------------------------------------

def flatten_graph_snapshots(
    snapshots: List[Dict[str, Any]],
    restaurant_id: int,
) -> List[Dict[str, Any]]:
    """
    Convert graph snapshot dicts from the simulation runner into
    rows matching the synthetic_graph_snapshots table schema.
    """
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
    """
    Convert exit cascade dicts from the simulation runner into
    rows matching the synthetic_exit_cascades table schema.
    """
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
# 5. MAIN PIPELINE
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

    # Cohort-level tracking for summary
    cohort_stats = {
        "control": {"restaurants": 0, "staff": 0, "exits": 0},
        "adopter": {"restaurants": 0, "staff": 0, "exits": 0},
        "day1":    {"restaurants": 0, "staff": 0, "exits": 0},
    }

    total_restaurants = len(RESTAURANTS_TO_SIMULATE)
    sim_start = time.time()

    for idx, config in enumerate(RESTAURANTS_TO_SIMULATE):
        restaurant_id = config["restaurant_id"]
        profile_key = config["profile_key"]
        num_staff = config["num_staff"]
        num_days = config["num_days"]
        cohort = config["cohort"]
        adoption_day = config["adoption_day"]

        r_start = time.time()
        print(f"\n=== [{idx + 1}/{total_restaurants}] Restaurant {restaurant_id} "
              f"({profile_key}, {num_staff} staff, {num_days} days, "
              f"cohort={cohort}, adoption_day={adoption_day}) ===")

        profile = get_profile(profile_key)

        # Generate En Place effect config for this restaurant
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
        )

        # Core tables
        combined_staff_master.extend(results["staff_master"])
        combined_daily_emotions.extend(results["daily_emotions"])
        combined_daily_behavior.extend(results["daily_behavior"])

        # Graph tables
        graph_snap_rows = flatten_graph_snapshots(
            results["graph_snapshots"], restaurant_id
        )
        cascade_rows = flatten_exit_cascades(
            results["exit_cascades"], restaurant_id
        )
        combined_graph_snapshots.extend(graph_snap_rows)
        combined_exit_cascades.extend(cascade_rows)

        # Restaurant metadata
        exits = sum(1 for s in results["staff_master"] if s["final_persona"] == "exit")
        turnover_rate = (exits / num_staff * 100) if num_staff > 0 else 0

        restaurant_meta = {
            "restaurant_id": restaurant_id,
            "profile_key": profile_key,
            "num_staff": num_staff,
            "num_days": num_days,
            "cohort": cohort,
            "adoption_day": adoption_day if adoption_day < 9999 else None,
            "ep_effectiveness": ep_config["restaurant_effectiveness"],
            "industry_variance": ep_config["industry_variance"],
            "without_ep_exit_mod": ep_config["without_ep"]["exit_modifier"],
            "with_ep_exit_mod": ep_config["with_ep"]["exit_modifier"],
            "total_exits": exits,
            "turnover_rate": round(turnover_rate, 1),
        }
        combined_restaurant_meta.append(restaurant_meta)

        # Cohort tracking
        cohort_stats[cohort]["restaurants"] += 1
        cohort_stats[cohort]["staff"] += num_staff
        cohort_stats[cohort]["exits"] += exits

        r_elapsed = time.time() - r_start
        print(f"  Done in {r_elapsed:.1f}s — {exits}/{num_staff} exits "
              f"({turnover_rate:.1f}% turnover), "
              f"{len(results['graph_snapshots'])} snapshots, "
              f"{len(results['exit_cascades'])} cascades")

        # ---------------------------------------------------------
        # Per-restaurant upload (reduces peak memory)
        # ---------------------------------------------------------
        if upload_supabase:
            # Upload restaurant metadata
            sb_restaurant = {
                "restaurant_id": restaurant_id,
                "profile_key": profile_key,
                "num_staff": num_staff,
                "num_days": num_days,
                "sma_score": None,  # Existing column, compute later
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
    print(f"\n{'='*70}")
    print(f"ALL SIMULATIONS COMPLETE — {total_elapsed:.1f}s")
    print(f"{'='*70}")
    print(f"  Total restaurants:  {total_restaurants}")
    print(f"  Staff master:       {len(combined_staff_master):,}")
    print(f"  Emotion rows:       {len(combined_daily_emotions):,}")
    print(f"  Behavior rows:      {len(combined_daily_behavior):,}")
    print(f"  Graph snapshots:    {len(combined_graph_snapshots):,}")
    print(f"  Exit cascades:      {len(combined_exit_cascades):,}")

    print(f"\n{'='*70}")
    print(f"COHORT RESULTS")
    print(f"{'='*70}")
    for cohort_name, stats in cohort_stats.items():
        if stats["staff"] > 0:
            turnover = stats["exits"] / stats["staff"] * 100
            print(f"  {cohort_name:>10}: {stats['restaurants']} restaurants, "
                  f"{stats['staff']:,} staff, {stats['exits']:,} exits "
                  f"({turnover:.1f}% turnover)")

    # Per-type breakdown
    print(f"\n{'='*70}")
    print(f"TURNOVER BY TYPE AND COHORT")
    print(f"{'='*70}")
    type_cohort_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
    for meta in combined_restaurant_meta:
        pkey = meta["profile_key"]
        coh = meta["cohort"]
        if pkey not in type_cohort_stats:
            type_cohort_stats[pkey] = {}
        if coh not in type_cohort_stats[pkey]:
            type_cohort_stats[pkey][coh] = {"staff": 0, "exits": 0}
        type_cohort_stats[pkey][coh]["staff"] += meta["num_staff"]
        type_cohort_stats[pkey][coh]["exits"] += meta["total_exits"]

    print(f"  {'Type':<22} {'Control':>10} {'Adopter':>10} {'Day-1':>10}")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10}")
    for pkey in _PROFILE_ROTATION:
        parts = []
        for coh in ["control", "adopter", "day1"]:
            data = type_cohort_stats.get(pkey, {}).get(coh, {"staff": 0, "exits": 0})
            if data["staff"] > 0:
                rate = data["exits"] / data["staff"] * 100
                parts.append(f"{rate:>8.1f}%")
            else:
                parts.append(f"{'N/A':>9}")
        print(f"  {pkey:<22} {parts[0]} {parts[1]} {parts[2]}")

    print()


# -------------------------------------------------------------
# 6. ENTRY POINT
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