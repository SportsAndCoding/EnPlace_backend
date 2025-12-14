"""
modules/nightly_pipeline/run_nightly_pipeline.py

Main orchestrator for the nightly data pipeline.

Runs after midnight to:
1. Seed Demo Bistro check-ins for today
2. Load quitter signatures (cached)
3. Score all staff for flight risk
4. Calculate network benchmarks
5. Write results to database
6. Log the run

Usage:
    python -m modules.nightly_pipeline.run_nightly_pipeline

For Heroku Scheduler:
    python modules/nightly_pipeline/run_nightly_pipeline.py
"""

import os
import sys
import json
import time
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Optional
from modules.nightly_pipeline.demo_shift_seeder import seed_demo_shifts, ensure_critical_gaps
from modules.nightly_pipeline.demo_hire_reset import reset_stable_hire_demo

# Add project root for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client


def get_supabase_client():
    """Initialize Supabase client."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    
    if not url or not key:
        raise EnvironmentError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
    
    return create_client(url, key)


def log_pipeline_start(client, run_date: date) -> int:
    """Log pipeline start, return run ID."""
    result = client.table("pipeline_run_log").insert({
        "run_date": run_date.isoformat(),
        "started_at": datetime.now().isoformat(),
        "status": "running",
    }).execute()
    
    return result.data[0]["id"]


def log_pipeline_complete(client, run_id: int, stats: Dict[str, int], duration: float):
    """Log pipeline completion."""
    client.table("pipeline_run_log").update({
        "completed_at": datetime.now().isoformat(),
        "status": "completed",
        "restaurants_processed": stats.get("restaurants", 0),
        "staff_scored": stats.get("staff_scored", 0),
        "checkins_generated": stats.get("checkins", 0),
        "duration_seconds": round(duration, 2),
    }).eq("id", run_id).execute()


def log_pipeline_failed(client, run_id: int, error: str):
    """Log pipeline failure."""
    client.table("pipeline_run_log").update({
        "completed_at": datetime.now().isoformat(),
        "status": "failed",
        "error_message": error[:1000],  # Truncate long errors
    }).eq("id", run_id).execute()


def load_signatures(signatures_path: str = "quitter_signatures.json") -> Dict[str, Any]:
    """Load cached quitter signatures from JSON file."""
    if not os.path.exists(signatures_path):
        raise FileNotFoundError(
            f"Signatures file not found: {signatures_path}\n"
            f"Run test_pattern_matcher.py first to generate signatures."
        )
    
    with open(signatures_path, "r") as f:
        return json.load(f)


def get_active_restaurants(client) -> List[Dict[str, Any]]:
    """Get list of restaurants that need processing."""
    result = client.table("restaurants") \
        .select("id, name, status") \
        .eq("status", "active") \
        .execute()
    
    return result.data


def seed_demo_bistro(client, run_date: date) -> int:
    """Seed check-ins for Demo Bistro."""
    from modules.nightly_pipeline.demo_bistro_seeder import generate_demo_bistro_checkins
    
    # Delete existing check-ins for today
    client.table("sse_daily_checkins") \
        .delete() \
        .eq("restaurant_id", 1) \
        .eq("checkin_date", run_date.isoformat()) \
        .execute()
    
    # Generate and insert
    checkins = generate_demo_bistro_checkins(run_date, restaurant_id=1)
    
    if checkins:
        client.table("sse_daily_checkins").insert(checkins).execute()
    
    return len(checkins)


def score_restaurant_staff(
    client,
    restaurant_id: int,
    signatures: Dict[str, Any],
    run_date: date,
    lookback_days: int = 14,
) -> List[Dict[str, Any]]:
    """
    Score all active staff at a restaurant for flight risk.
    
    Returns list of score records ready for insertion.
    """
    from modules.network_intelligence.pattern_matcher import (
        score_staff_flight_risk,
        QuitterSignature,
        EmotionalSignature,
    )
    
    # Reconstruct signature objects from cached dict
    sig_objects = {}
    for bucket_name, sig_data in signatures.items():
        q = sig_data["quitter"]
        s = sig_data["stayer"]
        
        quitter_sig = EmotionalSignature(
            avg_mood=q["avg_mood"],
            safe_rate=q["safe_rate"],
            fair_rate=q["fair_rate"],
            respected_rate=q["respected_rate"],
            mood_trend=q["mood_trend"],
            safe_trend=0,  # Not stored in cache
            fair_trend=0,
            respected_trend=0,
            n_staff=q["n_staff"],
            n_observations=q["n_observations"],
        )
        
        stayer_sig = EmotionalSignature(
            avg_mood=s["avg_mood"],
            safe_rate=s["safe_rate"],
            fair_rate=s["fair_rate"],
            respected_rate=s["respected_rate"],
            mood_trend=s["mood_trend"],
            safe_trend=0,
            fair_trend=0,
            respected_trend=0,
            n_staff=s["n_staff"],
            n_observations=s["n_observations"],
        )
        
        sig_objects[bucket_name] = QuitterSignature(
            bucket_name=bucket_name,
            bucket_label=sig_data["bucket_label"],
            quitter=quitter_sig,
            stayer=stayer_sig,
            mood_gap=sig_data["gaps"]["mood"],
            safe_gap=sig_data["gaps"]["safe"],
            fair_gap=sig_data["gaps"]["fair"],
            respected_gap=sig_data["gaps"]["respected"],
            primary_signal=sig_data["primary_signal"],
            signal_strength=sig_data["signal_strength"],
        )
    
    # Score staff
    flight_scores = score_staff_flight_risk(
        client,
        restaurant_id=restaurant_id,
        signatures=sig_objects,
        lookback_days=lookback_days,
    )
    
    # Convert to database records
    records = []
    for score in flight_scores:
        records.append({
            "staff_id": score.staff_id,
            "restaurant_id": restaurant_id,
            "calculated_date": run_date.isoformat(),
            "score": score.score,
            "risk_level": score.risk_level,
            "tenure_days": score.tenure_days,
            "tenure_bucket": score.tenure_bucket,
            "primary_concern": score.primary_concern,
            "contributing_factors": score.contributing_factors,
            "current_mood": score.current_mood,
            "safe_rate": score.safe_rate,
            "fair_rate": score.fair_rate,
            "respected_rate": score.respected_rate,
            "mood_trend": score.mood_trend,
        })
    
    return records


def write_flight_risk_scores(client, records: List[Dict[str, Any]], run_date: date):
    """Write flight risk scores to database."""
    if not records:
        return
    
    # Delete existing scores for this date
    restaurant_ids = list(set(r["restaurant_id"] for r in records))
    for rid in restaurant_ids:
        client.table("staff_flight_risk") \
            .delete() \
            .eq("restaurant_id", rid) \
            .eq("calculated_date", run_date.isoformat()) \
            .execute()
    
    # Insert new scores
    client.table("staff_flight_risk").insert(records).execute()


def calculate_restaurant_metrics(
    client,
    restaurant_id: int,
    flight_risk_records: List[Dict[str, Any]],
    run_date: date,
) -> Dict[str, Any]:
    """Calculate daily metrics and network percentiles for a restaurant."""
    from modules.network_intelligence.pattern_matcher import calculate_network_percentile
    
    # Get network percentiles
    mood_result = calculate_network_percentile(client, restaurant_id, "mood")
    safety_result = calculate_network_percentile(client, restaurant_id, "safety")
    fairness_result = calculate_network_percentile(client, restaurant_id, "fairness")
    respect_result = calculate_network_percentile(client, restaurant_id, "respect")
    
    # Count risk levels
    risk_counts = {"low": 0, "moderate": 0, "elevated": 0, "high": 0, "critical": 0}
    for record in flight_risk_records:
        if record["restaurant_id"] == restaurant_id:
            risk_counts[record["risk_level"]] += 1
    
    # Calculate overall health (average of percentiles)
    percentiles = [
        p for p in [
            mood_result.get("percentile"),
            safety_result.get("percentile"),
            fairness_result.get("percentile"),
            respect_result.get("percentile"),
        ] if p is not None
    ]
    overall = int(sum(percentiles) / len(percentiles)) if percentiles else None
    
    return {
        "restaurant_id": restaurant_id,
        "calculated_date": run_date.isoformat(),
        "mood_percentile": mood_result.get("percentile"),
        "safety_percentile": safety_result.get("percentile"),
        "fairness_percentile": fairness_result.get("percentile"),
        "respect_percentile": respect_result.get("percentile"),
        "overall_health_percentile": overall,
        "avg_mood": mood_result.get("restaurant_value"),
        "avg_safe_rate": safety_result.get("restaurant_value"),
        "avg_fair_rate": fairness_result.get("restaurant_value"),
        "avg_respected_rate": respect_result.get("restaurant_value"),
        "total_active_staff": len([r for r in flight_risk_records if r["restaurant_id"] == restaurant_id]),
        "staff_with_checkins": len([r for r in flight_risk_records if r["restaurant_id"] == restaurant_id]),
        "checkin_rate": 0.85,  # Placeholder
        "low_risk_count": risk_counts["low"],
        "moderate_risk_count": risk_counts["moderate"],
        "elevated_risk_count": risk_counts["elevated"],
        "high_risk_count": risk_counts["high"],
        "critical_risk_count": risk_counts["critical"],
    }


def write_restaurant_metrics(client, metrics: Dict[str, Any], run_date: date):
    """Write restaurant daily metrics to database."""
    # Delete existing metrics for this date
    client.table("restaurant_daily_metrics") \
        .delete() \
        .eq("restaurant_id", metrics["restaurant_id"]) \
        .eq("calculated_date", run_date.isoformat()) \
        .execute()
    
    # Insert new metrics
    client.table("restaurant_daily_metrics").insert(metrics).execute()


def run_pipeline(run_date: Optional[date] = None):
    """
    Run the complete nightly pipeline.
    
    Args:
        run_date: Date to process (default: today)
    """
    if run_date is None:
        run_date = date.today()
    
    print(f"\n{'='*60}")
    print(f"NIGHTLY PIPELINE - {run_date.isoformat()}")
    print(f"{'='*60}")
    
    start_time = time.time()
    client = get_supabase_client()
    
    # Log start
    try:
        run_id = log_pipeline_start(client, run_date)
        print(f"Pipeline run logged: ID {run_id}")
    except Exception as e:
        print(f"Warning: Could not log pipeline start: {e}")
        run_id = None
    
    stats = {
        "restaurants": 0,
        "staff_scored": 0,
        "checkins": 0,
    }
    
    try:
        # Step 1: Seed Demo Bistro check-ins
        print(f"\n[1/5] Seeding Demo Bistro check-ins...")
        checkins_count = seed_demo_bistro(client, run_date)
        stats["checkins"] = checkins_count
        print(f"      Generated {checkins_count} check-ins")

        # Step 1b: Seed Demo Bistro shifts with intentional gaps
        print(f"\n[1b/5] Seeding Demo Bistro shifts...")
        shift_stats = seed_demo_shifts(client, restaurant_id=1)
        print(f"      Created {shift_stats['created']} shifts, {shift_stats['gaps_created']} intentional gaps")
        
        # Step 1c: Reset Stable Hire demo data
        print(f"\n[1c/5] Resetting Stable Hire demo data...")
        hire_stats = reset_stable_hire_demo(client, restaurant_id=1)
        print(f"      Deleted {hire_stats['deleted']} demo candidates")
        print(f"      Reset {hire_stats['reset_to_open']} to open, {hire_stats['set_hired']} hired, {hire_stats['set_rejected']} rejected")
        
        # Step 2: Load signatures
        print(f"\n[2/5] Loading quitter signatures...")
        signatures = load_signatures()
        print(f"      Loaded {len(signatures)} tenure bucket signatures")
        
        # Step 3: Get restaurants to process
        print(f"\n[3/5] Getting active restaurants...")
        restaurants = get_active_restaurants(client)
        print(f"      Found {len(restaurants)} restaurants")
        
        all_flight_risk_records = []
        
        # Step 4: Score each restaurant
        print(f"\n[4/5] Scoring staff flight risk...")
        for restaurant in restaurants:
            rid = restaurant["id"]
            rname = restaurant["name"]
            print(f"      Processing {rname} (ID: {rid})...")
            
            records = score_restaurant_staff(client, rid, signatures, run_date)
            all_flight_risk_records.extend(records)
            
            risk_summary = {}
            for r in records:
                level = r["risk_level"]
                risk_summary[level] = risk_summary.get(level, 0) + 1
            
            print(f"        Scored {len(records)} staff: {risk_summary}")
            stats["staff_scored"] += len(records)
            stats["restaurants"] += 1
        
        # Write flight risk scores
        write_flight_risk_scores(client, all_flight_risk_records, run_date)
        print(f"      Wrote {len(all_flight_risk_records)} flight risk records")
        
        # Step 5: Calculate and write restaurant metrics
        print(f"\n[5/5] Calculating restaurant metrics...")
        for restaurant in restaurants:
            rid = restaurant["id"]
            metrics = calculate_restaurant_metrics(
                client, rid, all_flight_risk_records, run_date
            )
            write_restaurant_metrics(client, metrics, run_date)
            
            print(f"      {restaurant['name']}: {metrics['overall_health_percentile']}th percentile overall")
        
        # Log success
        duration = time.time() - start_time
        if run_id:
            log_pipeline_complete(client, run_id, stats, duration)
        
        print(f"\n{'='*60}")
        print(f"PIPELINE COMPLETE")
        print(f"{'='*60}")
        print(f"Duration: {duration:.1f} seconds")
        print(f"Restaurants: {stats['restaurants']}")
        print(f"Staff scored: {stats['staff_scored']}")
        print(f"Check-ins generated: {stats['checkins']}")
        
    except Exception as e:
        duration = time.time() - start_time
        print(f"\n{'='*60}")
        print(f"PIPELINE FAILED")
        print(f"{'='*60}")
        print(f"Error: {e}")
        
        if run_id:
            log_pipeline_failed(client, run_id, str(e))
        
        raise


if __name__ == "__main__":
    run_pipeline()