"""
Upload turnover simulation data to Supabase.

Tables (must exist before running):
  - turnover_benchmarks   (100 rows from restaurant_meta.csv)
  - turnover_timeseries   (73,000 rows from rolling_turnover_timeseries.csv)

Usage:
  python upload_turnover_data.py

Requires .env (loaded via python-dotenv):
  SUPABASE_URL              e.g. https://xxxxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY (service_role key — needed for bulk insert)

Install if needed:
  pip install supabase python-dotenv
"""

import csv
import os
import sys
import time

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
META_CSV = os.path.join("synthetic_output", "restaurant_meta.csv")
TS_CSV = os.path.join("synthetic_output", "rolling_turnover_timeseries.csv")
BATCH_SIZE = 1000  # rows per insert for timeseries

# Columns that should be integers (everything else numeric stays as float)
INT_COLS = {
    "restaurant_id", "headcount", "post_exits", "post_days", "sim_day",
    "days_from_adoption", "l1_original_count", "l1_pre_exits",
    "l1_post_at_risk", "l1_post_exits", "l2_pre_eligible",
    "l2_pre_survived", "l2_post_eligible", "l2_post_survived",
    "l3_pre_count", "l3_pre_exits", "l3_post_count", "l3_post_exits",
}

# Columns that are text (don't coerce to number)
TEXT_COLS = {
    "profile_key", "label", "national_range", "source",
}


def coerce(key, val):
    """Convert CSV string to proper Python type."""
    if val == "" or val is None:
        return None
    if key in TEXT_COLS:
        return val
    if key in INT_COLS:
        return int(val)
    # Everything else: try float
    try:
        return float(val)
    except ValueError:
        return val


def load_csv(path):
    """Read CSV and return list of dicts with proper types."""
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            rows.append({k: coerce(k, v) for k, v in row.items()})
    return rows


def upload_batch(table, rows, sb):
    """Insert a batch of rows. Returns count inserted."""
    resp = sb.table(table).insert(rows).execute()
    return len(resp.data) if resp.data else 0


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        print("ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")
        sys.exit(1)

    sb = create_client(url, key)

    # ── Verify CSV files exist ──
    for path in [META_CSV, TS_CSV]:
        if not os.path.exists(path):
            print(f"ERROR: {path} not found. Run turnover_analysis.py first.")
            sys.exit(1)

    # ── 1. Upload benchmarks (100 rows) ──
    print(f"\n{'='*60}")
    print("STEP 1: turnover_benchmarks (restaurant_meta.csv)")
    print(f"{'='*60}")

    meta_rows = load_csv(META_CSV)
    print(f"  Loaded {len(meta_rows)} rows from {META_CSV}")

    # Clear existing data
    print("  Clearing existing turnover_benchmarks...")
    sb.table("turnover_benchmarks").delete().gte("restaurant_id", 0).execute()

    count = upload_batch("turnover_benchmarks", meta_rows, sb)
    print(f"  Inserted {count} rows into turnover_benchmarks ✓")

    # ── 2. Upload timeseries (73K rows in batches) ──
    print(f"\n{'='*60}")
    print("STEP 2: turnover_timeseries (rolling_turnover_timeseries.csv)")
    print(f"{'='*60}")

    ts_rows = load_csv(TS_CSV)
    print(f"  Loaded {len(ts_rows)} rows from {TS_CSV}")

    # Clear existing data
    print("  Clearing existing turnover_timeseries...")
    sb.table("turnover_timeseries").delete().gte("restaurant_id", 0).execute()

    total = 0
    start = time.time()
    for i in range(0, len(ts_rows), BATCH_SIZE):
        batch = ts_rows[i:i + BATCH_SIZE]
        count = upload_batch("turnover_timeseries", batch, sb)
        total += count
        elapsed = time.time() - start
        pct = total / len(ts_rows) * 100
        print(f"  {total:>6,} / {len(ts_rows):,}  ({pct:5.1f}%)  [{elapsed:.1f}s]")

    elapsed = time.time() - start
    print(f"\n  Inserted {total:,} rows into turnover_timeseries in {elapsed:.1f}s ✓")

    # ── Summary ──
    print(f"\n{'='*60}")
    print("COMPLETE")
    print(f"{'='*60}")
    print(f"  turnover_benchmarks:  {len(meta_rows)} rows")
    print(f"  turnover_timeseries:  {total:,} rows")
    print(f"  Total time: {elapsed:.1f}s")


if __name__ == "__main__":
    main()