"""
modules/nightly_pipeline/run_snapshot_pipeline.py

Nightly snapshot job for the Executive Portal.
Computes per-restaurant metrics and writes one row per restaurant
to restaurant_daily_snapshot. Skips restaurants with no active staff.

Usage:
    python -m modules.nightly_pipeline.run_snapshot_pipeline

For Heroku Scheduler:
    python modules/nightly_pipeline/run_snapshot_pipeline.py
"""
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Optional

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_today_for_restaurant(client, organization_id: int) -> date:
    try:
        result = client.table("organizations").select("timezone").eq("id", organization_id).single().execute()
        tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
    except Exception:
        tz_name = "America/New_York"
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()


def _compute_sma(checkins_7d: list, checkins_28d: list) -> float:
    """
    Simplified SMA computation matching dashboard_service logic.
    Returns 0-100 score.
    """
    if not checkins_7d:
        return 0.0

    mood_scores = [c.get("mood_emoji", 3) for c in checkins_7d if c.get("mood_emoji")]
    avg_mood = sum(mood_scores) / len(mood_scores) if mood_scores else 3.0

    felt_safe      = [c for c in checkins_7d if c.get("felt_safe")]
    felt_fair      = [c for c in checkins_7d if c.get("felt_fair")]
    felt_respected = [c for c in checkins_7d if c.get("felt_respected")]

    safe_pct      = len(felt_safe)      / len(checkins_7d) * 100
    fair_pct      = len(felt_fair)      / len(checkins_7d) * 100
    respect_pct   = len(felt_respected) / len(checkins_7d) * 100

    mood_normalized = (avg_mood - 1) / 4 * 100
    emotional = (mood_normalized * 0.4 + safe_pct * 0.2 + fair_pct * 0.2 + respect_pct * 0.2)

    # Trend component
    trend_score = 50.0
    if checkins_28d:
        old_checkins = [c for c in checkins_28d if c not in checkins_7d]
        if old_checkins:
            old_mood = sum(c.get("mood_emoji", 3) for c in old_checkins) / len(old_checkins)
            old_score = int((old_mood - 1) / 4 * 100)
            current_score = int(mood_normalized)
            delta = current_score - old_score
            trend_score = min(100, max(0, 50 + delta * 2))

    sma = emotional * 0.7 + trend_score * 0.3
    return round(min(100, max(0, sma)), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN COMPUTE
# ═══════════════════════════════════════════════════════════════════════════════

def compute_snapshot(client, organization_id: int, today: date) -> Optional[dict]:
    """
    Compute all metrics for one restaurant for today.
    Returns None if restaurant has no active staff (skip).
    """
    week_ago       = today - timedelta(days=7)
    four_weeks_ago = today - timedelta(days=28)

    # Active staff count
    staff_result = client.table("staff").select("staff_id").eq(
        "organization_id", organization_id
    ).eq("status", "active").execute()
    active_staff = staff_result.data or []
    active_count = len(active_staff)

    if active_count == 0:
        return None

    # Check-ins 7d
    checkins_7d_result = client.table("sse_daily_checkins").select("*").eq(
        "organization_id", organization_id
    ).gte("checkin_date", week_ago.isoformat()).lte(
        "checkin_date", today.isoformat()
    ).execute()
    checkins_7d = checkins_7d_result.data or []

    # Check-ins 28d
    checkins_28d_result = client.table("sse_daily_checkins").select("*").eq(
        "organization_id", organization_id
    ).gte("checkin_date", four_weeks_ago.isoformat()).lte(
        "checkin_date", today.isoformat()
    ).execute()
    checkins_28d = checkins_28d_result.data or []

    # Mood metrics
    mood_scores_7d = [c.get("mood_emoji", 3) for c in checkins_7d if c.get("mood_emoji")]
    avg_mood_7d = round(sum(mood_scores_7d) / len(mood_scores_7d), 2) if mood_scores_7d else 0

    mood_scores_28d = [c.get("mood_emoji", 3) for c in checkins_28d if c.get("mood_emoji")]
    avg_mood_28d = round(sum(mood_scores_28d) / len(mood_scores_28d), 2) if mood_scores_28d else 0

    mood_trend = round(avg_mood_7d - avg_mood_28d, 2)

    safe_rate      = round(len([c for c in checkins_7d if c.get("felt_safe")])      / len(checkins_7d) * 100, 2) if checkins_7d else 0
    fair_rate      = round(len([c for c in checkins_7d if c.get("felt_fair")])      / len(checkins_7d) * 100, 2) if checkins_7d else 0
    respected_rate = round(len([c for c in checkins_7d if c.get("felt_respected")]) / len(checkins_7d) * 100, 2) if checkins_7d else 0

    # Check-in completion rate
    # Expected: each active staff submits once per working day (5 of 7 days)
    expected_checkins = active_count * 5
    completion_rate = round(min(100, len(checkins_7d) / expected_checkins * 100), 2) if expected_checkins > 0 else 0

    # Flight risk (most recent score per staff)
    risk_result = client.table("staff_flight_risk").select(
        "staff_id, risk_level, calculated_date"
    ).eq("organization_id", organization_id).gte(
        "calculated_date", week_ago.isoformat()
    ).execute()

    risk_rows = risk_result.data or []
    # Keep only latest per staff
    latest_risk = {}
    for row in risk_rows:
        sid = row["staff_id"]
        if sid not in latest_risk or row["calculated_date"] > latest_risk[sid]["calculated_date"]:
            latest_risk[sid] = row

    risk_counts = {"critical": 0, "high": 0, "elevated": 0, "moderate": 0, "low": 0}
    for row in latest_risk.values():
        level = row.get("risk_level", "low")
        if level in risk_counts:
            risk_counts[level] += 1

    # Active escalations
    esc_result = client.table("sse_escalation_events").select("id").eq(
        "organization_id", organization_id
    ).eq("status", "active").execute()
    active_escalations = len(esc_result.data or [])

    # Resolved escalations last 7d
    resolved_result = client.table("sse_escalation_events").select("id").eq(
        "organization_id", organization_id
    ).eq("status", "resolved").gte(
        "resolved_at", week_ago.isoformat()
    ).execute()
    resolved_7d = len(resolved_result.data or [])

    # SMA
    sma = _compute_sma(checkins_7d, checkins_28d)

    return {
        "organization_id":           organization_id,
        "snapshot_date":           today.isoformat(),
        "checkin_count_7d":        len(checkins_7d),
        "active_staff_count":      active_count,
        "checkin_completion_rate": completion_rate,
        "avg_mood_7d":             avg_mood_7d,
        "avg_mood_28d":            avg_mood_28d,
        "mood_trend":              mood_trend,
        "safe_rate":               safe_rate,
        "fair_rate":               fair_rate,
        "respected_rate":          respected_rate,
        "flight_risk_critical":    risk_counts["critical"],
        "flight_risk_high":        risk_counts["high"],
        "flight_risk_elevated":    risk_counts["elevated"],
        "flight_risk_moderate":    risk_counts["moderate"],
        "flight_risk_low":         risk_counts["low"],
        "flight_risk_total":       sum(risk_counts.values()),
        "active_escalations":      active_escalations,
        "resolved_escalations_7d": resolved_7d,
        "sma_score":               sma,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_snapshot_pipeline():
    start = time.time()
    print(f"[snapshot] Starting at {datetime.utcnow().isoformat()}")

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Get all active restaurants
    restaurants = client.table("organizations").select(
        "id, name, timezone"
    ).eq("status", "active").execute()

    if not restaurants.data:
        print("[snapshot] No active restaurants found. Exiting.")
        return

    success = 0
    skipped = 0
    errors  = 0

    for restaurant in restaurants.data:
        rid  = restaurant["id"]
        name = restaurant["name"]
        try:
            today    = _get_today_for_restaurant(client, rid)
            snapshot = compute_snapshot(client, rid, today)

            if snapshot is None:
                print(f"[snapshot] Skipped {name} (id={rid}) — no active staff")
                skipped += 1
                continue

            # Upsert — if already ran today, update it
            client.table("restaurant_daily_snapshot").upsert(
                snapshot,
                on_conflict="organization_id,snapshot_date"
            ).execute()

            print(f"[snapshot] ✓ {name} (id={rid}) | SMA={snapshot['sma_score']} | "
                  f"completion={snapshot['checkin_completion_rate']}% | "
                  f"critical={snapshot['flight_risk_critical']}")
            success += 1

        except Exception as e:
            print(f"[snapshot] ✗ ERROR {name} (id={rid}): {e}")
            errors += 1

    elapsed = round(time.time() - start, 2)
    print(f"\n[snapshot] Done in {elapsed}s — {success} written, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    run_snapshot_pipeline()