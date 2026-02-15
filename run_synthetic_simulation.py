"""
run_synthetic_simulation.py

Runs the full synthetic staffing simulation across 100 restaurants,
producing 5 output tables: staff_master, daily_emotions, daily_behavior,
graph_snapshots, and exit_cascades.

OUTPUT MODES:
    CSV:      Flat files for staff_master, daily_emotions, daily_behavior,
              exit_cascades. Graph snapshots get JSONL (one JSON object per line)
              because they contain nested visualization payloads.
    SUPABASE: Direct batch insert into all 6 synthetic_* tables.

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


# -------------------------------------------------------------
# 1. CONFIGURATION
# -------------------------------------------------------------

RESTAURANTS_TO_SIMULATE = [
    (101, "steakhouse", 50, 365),
    (102, "sports_bar", 60, 365),
    (103, "fast_casual", 40, 365),
    (104, "neighborhood_bistro", 35, 365),
    (105, "upscale_casual", 55, 365),
    (106, "family_diner", 30, 365),
    (107, "breakfast_cafe", 28, 365),
    (108, "bar_and_grille", 65, 365),
    (109, "high_volume_chain", 75, 365),
    (110, "college_town_cafe", 38, 365),
    (111, "hotel_restaurant", 52, 365),
    (112, "airport_restaurant", 70, 365),
    (113, "steakhouse", 45, 365),
    (114, "sports_bar", 62, 365),
    (115, "fast_casual", 42, 365),
    (116, "neighborhood_bistro", 33, 365),
    (117, "upscale_casual", 58, 365),
    (118, "family_diner", 27, 365),
    (119, "breakfast_cafe", 25, 365),
    (120, "bar_and_grille", 63, 365),
    (121, "high_volume_chain", 78, 365),
    (122, "college_town_cafe", 41, 365),
    (123, "hotel_restaurant", 55, 365),
    (124, "airport_restaurant", 72, 365),
    (125, "steakhouse", 48, 365),
    (126, "sports_bar", 59, 365),
    (127, "fast_casual", 44, 365),
    (128, "neighborhood_bistro", 34, 365),
    (129, "upscale_casual", 53, 365),
    (130, "family_diner", 29, 365),
    (131, "breakfast_cafe", 26, 365),
    (132, "bar_and_grille", 60, 365),
    (133, "high_volume_chain", 80, 365),
    (134, "college_town_cafe", 43, 365),
    (135, "hotel_restaurant", 57, 365),
    (136, "airport_restaurant", 68, 365),
    (137, "steakhouse", 47, 365),
    (138, "sports_bar", 61, 365),
    (139, "fast_casual", 39, 365),
    (140, "neighborhood_bistro", 37, 365),
    (141, "upscale_casual", 54, 365),
    (142, "family_diner", 31, 365),
    (143, "breakfast_cafe", 29, 365),
    (144, "bar_and_grille", 66, 365),
    (145, "high_volume_chain", 76, 365),
    (146, "college_town_cafe", 36, 365),
    (147, "hotel_restaurant", 50, 365),
    (148, "airport_restaurant", 74, 365),
    (149, "steakhouse", 55, 365),
    (150, "sports_bar", 64, 365),
    (151, "fast_casual", 41, 365),
    (152, "neighborhood_bistro", 32, 365),
    (153, "upscale_casual", 56, 365),
    (154, "family_diner", 33, 365),
    (155, "breakfast_cafe", 27, 365),
    (156, "bar_and_grille", 67, 365),
    (157, "high_volume_chain", 73, 365),
    (158, "college_town_cafe", 40, 365),
    (159, "hotel_restaurant", 53, 365),
    (160, "airport_restaurant", 69, 365),
    (161, "steakhouse", 49, 365),
    (162, "sports_bar", 60, 365),
    (163, "fast_casual", 46, 365),
    (164, "neighborhood_bistro", 35, 365),
    (165, "upscale_casual", 59, 365),
    (166, "family_diner", 28, 365),
    (167, "breakfast_cafe", 24, 365),
    (168, "bar_and_grille", 62, 365),
    (169, "high_volume_chain", 77, 365),
    (170, "college_town_cafe", 39, 365),
    (171, "hotel_restaurant", 51, 365),
    (172, "airport_restaurant", 75, 365),
    (173, "steakhouse", 52, 365),
    (174, "sports_bar", 63, 365),
    (175, "fast_casual", 43, 365),
    (176, "neighborhood_bistro", 36, 365),
    (177, "upscale_casual", 57, 365),
    (178, "family_diner", 30, 365),
    (179, "breakfast_cafe", 26, 365),
    (180, "bar_and_grille", 64, 365),
    (181, "high_volume_chain", 79, 365),
    (182, "college_town_cafe", 37, 365),
    (183, "hotel_restaurant", 54, 365),
    (184, "airport_restaurant", 71, 365),
    (185, "steakhouse", 46, 365),
    (186, "sports_bar", 58, 365),
    (187, "fast_casual", 45, 365),
    (188, "neighborhood_bistro", 34, 365),
    (189, "upscale_casual", 52, 365),
    (190, "family_diner", 32, 365),
    (191, "breakfast_cafe", 25, 365),
    (192, "bar_and_grille", 61, 365),
    (193, "high_volume_chain", 74, 365),
    (194, "college_town_cafe", 42, 365),
    (195, "hotel_restaurant", 56, 365),
    (196, "airport_restaurant", 73, 365),
    (197, "steakhouse", 53, 365),
    (198, "sports_bar", 65, 365),
    (199, "fast_casual", 47, 365),
    (200, "neighborhood_bistro", 38, 365),
]


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
        ]:
            truncate_table(table)

    # Accumulators
    combined_staff_master = []
    combined_daily_emotions = []
    combined_daily_behavior = []
    combined_graph_snapshots = []
    combined_exit_cascades = []

    total_restaurants = len(RESTAURANTS_TO_SIMULATE)
    sim_start = time.time()

    for idx, (restaurant_id, profile_key, num_staff, num_days) in enumerate(RESTAURANTS_TO_SIMULATE):
        r_start = time.time()
        print(f"\n=== [{idx + 1}/{total_restaurants}] Restaurant {restaurant_id} "
              f"({profile_key}, {num_staff} staff, {num_days} days) ===")

        profile = get_profile(profile_key)

        results = simulate_restaurant(
            restaurant_id=restaurant_id,
            number_of_staff=num_staff,
            simulation_days=num_days,
            persona_weights=DEFAULT_PERSONA_WEIGHTS,
            restaurant_profile=profile,
            enable_contagion=True,
            graph_snapshot_interval=GRAPH_SNAPSHOT_INTERVAL,
        )

        # Core tables (unchanged)
        combined_staff_master.extend(results["staff_master"])
        combined_daily_emotions.extend(results["daily_emotions"])
        combined_daily_behavior.extend(results["daily_behavior"])

        # Graph tables (new)
        graph_snap_rows = flatten_graph_snapshots(
            results["graph_snapshots"], restaurant_id
        )
        cascade_rows = flatten_exit_cascades(
            results["exit_cascades"], restaurant_id
        )
        combined_graph_snapshots.extend(graph_snap_rows)
        combined_exit_cascades.extend(cascade_rows)

        r_elapsed = time.time() - r_start
        exits = sum(1 for s in results["staff_master"] if s["final_persona"] == "exit")
        print(f"  Done in {r_elapsed:.1f}s — {exits} exits, "
              f"{len(results['graph_snapshots'])} snapshots, "
              f"{len(results['exit_cascades'])} cascades")

        # ---------------------------------------------------------
        # Per-restaurant upload (reduces peak memory for 100 restaurants)
        # ---------------------------------------------------------
        if upload_supabase:
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
        write_jsonl("graph_snapshots.jsonl", combined_graph_snapshots)

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------
    total_elapsed = time.time() - sim_start
    print(f"\n{'='*60}")
    print(f"ALL SIMULATIONS COMPLETE — {total_elapsed:.1f}s")
    print(f"{'='*60}")
    print(f"  Restaurants:     {total_restaurants}")
    print(f"  Staff master:    {len(combined_staff_master):,}")
    print(f"  Emotion rows:    {len(combined_daily_emotions):,}")
    print(f"  Behavior rows:   {len(combined_daily_behavior):,}")
    print(f"  Graph snapshots: {len(combined_graph_snapshots):,}")
    print(f"  Exit cascades:   {len(combined_exit_cascades):,}")
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