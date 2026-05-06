"""
modules/nightly_pipeline/demo_bistro_seeder.py

Generates daily check-ins for Demo Bistro staff with intentional patterns.
Creates a compelling demo with a mix of:
- Happy, stable staff
- Neutral staff  
- Flight risk staff (declining mood, fairness issues)
- Critical risk staff

This runs nightly to keep the demo fresh and realistic.
"""

import random
import hashlib
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import pytz


def _get_today_for_restaurant(supabase_client, organization_id: int) -> date:
    """Get today's date in restaurant timezone."""
    try:
        result = supabase_client.table("organizations").select("timezone").eq("id", organization_id).single().execute()
        tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
    except:
        tz_name = "America/New_York"
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()


@dataclass
class StaffPattern:
    """Defines the emotional pattern for a staff member."""
    staff_id: str
    name: str
    position: str
    pattern_type: str  # "stable", "neutral", "flight_risk", "critical_risk"
    
    # Baseline probabilities
    mood_base: float      # Center of mood distribution (1-5)
    mood_volatility: float  # How much it varies day to day
    safe_prob: float      # Probability of feeling safe
    fair_prob: float      # Probability of feeling fairly treated
    respected_prob: float # Probability of feeling respected
    
    # Trend (change per week, negative = declining)
    mood_trend: float
    fair_trend: float
    respected_trend: float


# Demo Bistro staff patterns - creates a realistic mixed environment
DEMO_BISTRO_PATTERNS = [
    # === STABLE STAFF (happy, engaged) ===
    StaffPattern(
        staff_id="STAFF013", name="Jennifer Martinez", position="Executive Chef",
        pattern_type="stable",
        mood_base=4.5, mood_volatility=0.3,
        safe_prob=0.98, fair_prob=0.95, respected_prob=0.96,
        mood_trend=0.0, fair_trend=0.0, respected_trend=0.0,
    ),
    StaffPattern(
        staff_id="STAFF010", name="James Smith", position="General Manager",
        pattern_type="stable",
        mood_base=4.3, mood_volatility=0.4,
        safe_prob=0.97, fair_prob=0.92, respected_prob=0.94,
        mood_trend=0.0, fair_trend=0.0, respected_trend=0.0,
    ),
    StaffPattern(
        staff_id="STAFF011", name="Maria Garcia", position="Assistant Manager",
        pattern_type="stable",
        mood_base=4.4, mood_volatility=0.3,
        safe_prob=0.96, fair_prob=0.93, respected_prob=0.95,
        mood_trend=0.0, fair_trend=0.0, respected_trend=0.0,
    ),
    StaffPattern(
        staff_id="STAFF014", name="David Wilson", position="Sous Chef",
        pattern_type="stable",
        mood_base=4.2, mood_volatility=0.4,
        safe_prob=0.95, fair_prob=0.90, respected_prob=0.92,
        mood_trend=0.0, fair_trend=0.0, respected_trend=0.0,
    ),
    
    # === NEUTRAL STAFF (fine, nothing concerning) ===
    StaffPattern(
        staff_id="STAFF031", name="Ashley Robinson", position="Server",
        pattern_type="neutral",
        mood_base=3.8, mood_volatility=0.5,
        safe_prob=0.92, fair_prob=0.85, respected_prob=0.87,
        mood_trend=0.0, fair_trend=0.0, respected_trend=0.0,
    ),
    StaffPattern(
        staff_id="STAFF038", name="Matthew Allen", position="Server",
        pattern_type="neutral",
        mood_base=3.7, mood_volatility=0.5,
        safe_prob=0.90, fair_prob=0.82, respected_prob=0.85,
        mood_trend=0.0, fair_trend=0.0, respected_trend=0.0,
    ),
    StaffPattern(
        staff_id="STAFF018", name="John Miller", position="Line Cook",
        pattern_type="neutral",
        mood_base=3.6, mood_volatility=0.6,
        safe_prob=0.88, fair_prob=0.80, respected_prob=0.82,
        mood_trend=0.0, fair_trend=0.0, respected_trend=0.0,
    ),
    StaffPattern(
        staff_id="STAFF046", name="Carlos Green", position="Bartender",
        pattern_type="neutral",
        mood_base=3.9, mood_volatility=0.4,
        safe_prob=0.91, fair_prob=0.84, respected_prob=0.86,
        mood_trend=0.0, fair_trend=0.0, respected_trend=0.0,
    ),
    StaffPattern(
        staff_id="HST001", name="Emily Davis", position="Host",
        pattern_type="neutral",
        mood_base=3.8, mood_volatility=0.5,
        safe_prob=0.93, fair_prob=0.86, respected_prob=0.88,
        mood_trend=0.0, fair_trend=0.0, respected_trend=0.0,
    ),
    
    # === FLIGHT RISK STAFF (showing early warning signs, needs attention) ===
    StaffPattern(
        staff_id="SRV002", name="David Kim", position="Server",
        pattern_type="flight_risk",
        mood_base=3.5, mood_volatility=0.5,
        safe_prob=0.88, fair_prob=0.72, respected_prob=0.74,
        mood_trend=-0.05, fair_trend=-0.02, respected_trend=-0.02,
    ),
    StaffPattern(
        staff_id="DSH001", name="Tony Nguyen", position="Dishwasher",
        pattern_type="flight_risk",
        mood_base=3.3, mood_volatility=0.6,
        safe_prob=0.85, fair_prob=0.68, respected_prob=0.70,
        mood_trend=-0.06, fair_trend=-0.02, respected_trend=-0.02,
    ),
    StaffPattern(
        staff_id="COK002", name="James Wilson", position="Line Cook",
        pattern_type="flight_risk",
        mood_base=3.5, mood_volatility=0.5,
        safe_prob=0.86, fair_prob=0.70, respected_prob=0.72,
        mood_trend=-0.04, fair_trend=-0.02, respected_trend=-0.02,
    ),
    
    # === WATCH LIST STAFF (notable concerns, proactive outreach recommended) ===
    StaffPattern(
        staff_id="SRV004", name="Marcus Thompson", position="Server",
        pattern_type="watch_list",
        mood_base=3.2, mood_volatility=0.6,
        safe_prob=0.84, fair_prob=0.62, respected_prob=0.64,
        mood_trend=-0.08, fair_trend=-0.03, respected_trend=-0.03,
    ),
]


def _deterministic_random(staff_id: str, date_str: str, salt: str = "") -> float:
    """Generate deterministic random [0,1) based on staff_id and date."""
    seed_str = f"{staff_id}:{date_str}:{salt}"
    hash_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    return (hash_val % 1_000_000) / 1_000_000


def _calculate_weeks_active(pattern: StaffPattern, check_date: date) -> int:
    """Calculate how many weeks the trend has been applied."""
    # Use a reference start date for trend calculation
    # Trends started 4 weeks ago
    trend_start = check_date - timedelta(days=28)
    days_since_start = (check_date - trend_start).days
    return max(0, days_since_start // 7)


def generate_daily_checkin(
    pattern: StaffPattern,
    check_date: date,
    organization_id: int = 1,
) -> Dict[str, Any]:
    """
    Generate a single day's check-in for a staff member based on their pattern.
    
    Returns dict ready for insertion into sse_daily_checkins.
    """
    date_str = check_date.isoformat()
    
    # Apply trend based on weeks active
    weeks = _calculate_weeks_active(pattern, check_date)
    
    adjusted_mood = pattern.mood_base + (pattern.mood_trend * weeks)
    adjusted_fair = max(0.1, min(0.99, pattern.fair_prob + (pattern.fair_trend * weeks)))
    adjusted_respected = max(0.1, min(0.99, pattern.respected_prob + (pattern.respected_trend * weeks)))
    
    # Generate deterministic values for this day
    mood_noise = (_deterministic_random(pattern.staff_id, date_str, "mood") - 0.5) * 2
    raw_mood = adjusted_mood + (mood_noise * pattern.mood_volatility)
    mood_emoji = max(1, min(5, round(raw_mood)))
    
    felt_safe = _deterministic_random(pattern.staff_id, date_str, "safe") < pattern.safe_prob
    felt_fair = _deterministic_random(pattern.staff_id, date_str, "fair") < adjusted_fair
    felt_respected = _deterministic_random(pattern.staff_id, date_str, "respected") < adjusted_respected
    
    return {
        "staff_id": pattern.staff_id,
        "organization_id": organization_id,
        "checkin_date": date_str,
        "mood_emoji": mood_emoji,
        "felt_safe": felt_safe,
        "felt_fair": felt_fair,
        "felt_respected": felt_respected,
        "notes": None,
        "created_at": datetime.now().isoformat(),
    }


def generate_demo_bistro_checkins(
    check_date: date,
    organization_id: int = 1,
    patterns: List[StaffPattern] = None,
) -> List[Dict[str, Any]]:
    """
    Generate all check-ins for Demo Bistro for a given date.
    
    Args:
        check_date: Date to generate check-ins for
        organization_id: Demo Bistro restaurant ID (default 1)
        patterns: List of staff patterns (default DEMO_BISTRO_PATTERNS)
        
    Returns:
        List of check-in dicts ready for insertion
    """
    if patterns is None:
        patterns = DEMO_BISTRO_PATTERNS
    
    checkins = []
    for pattern in patterns:
        # 85% chance of checking in each day (realistic)
        if _deterministic_random(pattern.staff_id, check_date.isoformat(), "checkin") < 0.85:
            checkin = generate_daily_checkin(pattern, check_date, organization_id)
            checkins.append(checkin)
    
    return checkins


def seed_demo_bistro_history(
    supabase_client,
    days_back: int = 30,
    organization_id: int = 1,
) -> int:
    """
    Seed historical check-ins for Demo Bistro.
    
    Useful for initial setup or resetting demo data.
    
    Args:
        supabase_client: Initialized Supabase client
        days_back: How many days of history to generate
        organization_id: Demo Bistro restaurant ID
        
    Returns:
        Number of check-ins inserted
    """
    from datetime import date, timedelta
    
    today = _get_today_for_restaurant(supabase_client, organization_id)
    all_checkins = []
    
    for days_ago in range(days_back, 0, -1):
        check_date = today - timedelta(days=days_ago)
        daily_checkins = generate_demo_bistro_checkins(check_date, organization_id)
        all_checkins.extend(daily_checkins)
    
    if not all_checkins:
        return 0
    
    # Delete existing check-ins for this date range and restaurant
    start_date = (today - timedelta(days=days_back)).isoformat()
    end_date = today.isoformat()
    
    supabase_client.table("sse_daily_checkins") \
        .delete() \
        .eq("organization_id", organization_id) \
        .gte("checkin_date", start_date) \
        .lt("checkin_date", end_date) \
        .execute()
    
    # Insert new check-ins in batches
    batch_size = 100
    for i in range(0, len(all_checkins), batch_size):
        batch = all_checkins[i:i+batch_size]
        supabase_client.table("sse_daily_checkins").insert(batch).execute()
    
    return len(all_checkins)


def seed_today(
    supabase_client,
    organization_id: int = 1,
) -> int:
    """
    Seed today's check-ins for Demo Bistro.
    
    Called by nightly pipeline.
    
    Returns:
        Number of check-ins inserted
    """
    today = _get_today_for_restaurant(supabase_client, organization_id)
    
    # Delete any existing check-ins for today
    supabase_client.table("sse_daily_checkins") \
        .delete() \
        .eq("organization_id", organization_id) \
        .eq("checkin_date", today.isoformat()) \
        .execute()
    
    # Generate and insert today's check-ins
    checkins = generate_demo_bistro_checkins(today, organization_id)
    
    if checkins:
        supabase_client.table("sse_daily_checkins").insert(checkins).execute()
    
    return len(checkins)