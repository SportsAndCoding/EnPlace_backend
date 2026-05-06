"""
modules/nightly_pipeline/demo_open_shift_seeder.py

Seeds the Open Shift Marketplace for Demo Bistro (organization_id=1).
Ensures fresh, realistic open shifts are always available for sales demos.

Runs as part of nightly pipeline to:
1. Clear stale/past open shifts
2. Seed 8-12 open shifts across the next 7 days
3. Vary positions, times, and bonus amounts for realistic demos
"""

import random
from datetime import date, time, timedelta, datetime
from typing import Dict, Any, List
import pytz


# Realistic restaurant positions (excludes admin roles)
DEMO_POSITIONS = [
    "Server",
    "Bartender", 
    "Line Cook",
    "Host",
    "Busser",
    "Dishwasher",
    "Prep Cook",
    "Sous Chef",
]

# Shift templates by position - realistic times
SHIFT_TEMPLATES = {
    "Server": [
        {"start": time(11, 0), "end": time(16, 0), "desc": "Lunch shift - high volume expected"},
        {"start": time(17, 0), "end": time(22, 0), "desc": "Dinner shift - need experienced server"},
        {"start": time(16, 0), "end": time(23, 0), "desc": "Evening double - great tips night"},
    ],
    "Bartender": [
        {"start": time(16, 0), "end": time(23, 0), "desc": "Bar shift - craft cocktail experience preferred"},
        {"start": time(18, 0), "end": time(2, 0), "desc": "Late night bar - busy weekend"},
        {"start": time(11, 0), "end": time(17, 0), "desc": "Day bar - brunch service"},
    ],
    "Line Cook": [
        {"start": time(6, 0), "end": time(14, 0), "desc": "AM line - breakfast/lunch prep"},
        {"start": time(14, 0), "end": time(22, 0), "desc": "PM line - dinner rush"},
        {"start": time(10, 0), "end": time(18, 0), "desc": "Mid shift - lunch through early dinner"},
    ],
    "Host": [
        {"start": time(11, 0), "end": time(15, 0), "desc": "Lunch host - manage reservations"},
        {"start": time(17, 0), "end": time(22, 0), "desc": "Dinner host - busy Friday night"},
    ],
    "Busser": [
        {"start": time(11, 0), "end": time(16, 0), "desc": "Lunch support - fast table turns"},
        {"start": time(17, 0), "end": time(23, 0), "desc": "Dinner busser - heavy volume"},
    ],
    "Dishwasher": [
        {"start": time(10, 0), "end": time(18, 0), "desc": "Day dish - steady pace"},
        {"start": time(16, 0), "end": time(0, 0), "desc": "Night dish - dinner rush support"},
    ],
    "Prep Cook": [
        {"start": time(6, 0), "end": time(14, 0), "desc": "Morning prep - sauce and stock day"},
        {"start": time(8, 0), "end": time(16, 0), "desc": "Day prep - weekend prep list"},
    ],
    "Sous Chef": [
        {"start": time(14, 0), "end": time(22, 0), "desc": "PM sous - run dinner service"},
        {"start": time(6, 0), "end": time(14, 0), "desc": "AM sous - prep oversight"},
    ],
}

# Bonus pay tiers (makes demos more interesting)
BONUS_TIERS = [
    {"amount": 0.00, "weight": 3},      # No bonus - 30%
    {"amount": 10.00, "weight": 3},     # Small bonus - 30%
    {"amount": 25.00, "weight": 2},     # Medium bonus - 20%
    {"amount": 50.00, "weight": 1},     # Large bonus - 10%
    {"amount": 75.00, "weight": 1},     # Premium bonus - 10%
]


def _get_today_for_restaurant(supabase_client, organization_id: int) -> date:
    """Get today's date in restaurant timezone."""
    try:
        result = supabase_client.table("organizations").select("timezone").eq("id", organization_id).single().execute()
        tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
    except:
        tz_name = "America/New_York"
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()


def _weighted_bonus() -> float:
    """Select bonus amount using weighted distribution."""
    choices = []
    for tier in BONUS_TIERS:
        choices.extend([tier["amount"]] * tier["weight"])
    return random.choice(choices)


def _get_shift_template(position: str) -> Dict[str, Any]:
    """Get a random shift template for a position."""
    templates = SHIFT_TEMPLATES.get(position, SHIFT_TEMPLATES["Server"])
    return random.choice(templates)


def seed_demo_open_shifts(
    supabase_client,
    organization_id: int = 1,
    days_ahead: int = 7,
    shifts_per_day: int = 2,
) -> Dict[str, int]:
    """
    Seed open shifts for Demo Bistro's Open Shift Marketplace.
    
    Args:
        supabase_client: Initialized Supabase client
        organization_id: Target restaurant (default: Demo Bistro = 1)
        days_ahead: How many days into the future to seed
        shifts_per_day: Number of open shifts per day (default: 2)
    
    Returns:
        Dict with stats: {"deleted": N, "created": N}
    """
    today = _get_today_for_restaurant(supabase_client, organization_id)
    stats = {"deleted": 0, "created": 0}
    
    # Step 1: Delete stale open shifts (past dates or old unclaimed)
    # Delete all past-date shifts
    delete_past = supabase_client.table("open_shifts") \
        .delete() \
        .eq("organization_id", organization_id) \
        .lt("date", today.isoformat()) \
        .execute()
    stats["deleted"] += len(delete_past.data) if delete_past.data else 0
    
    # Delete existing future shifts for Demo Bistro to reset fresh
    delete_future = supabase_client.table("open_shifts") \
        .delete() \
        .eq("organization_id", organization_id) \
        .eq("status", "open") \
        .gte("date", today.isoformat()) \
        .execute()
    stats["deleted"] += len(delete_future.data) if delete_future.data else 0
    
    # Step 2: Generate new open shifts
    shifts_to_create = []
    
    for day_offset in range(days_ahead):
        shift_date = today + timedelta(days=day_offset)
        
        # Pick N different positions for this day
        day_positions = random.sample(DEMO_POSITIONS, shifts_per_day)
        
        for position in day_positions:
            template = _get_shift_template(position)
            
            shift_record = {
                "organization_id": organization_id,
                "position": position,
                "date": shift_date.isoformat(),
                "start_time": template["start"].strftime("%H:%M:%S"),
                "end_time": template["end"].strftime("%H:%M:%S"),
                "bonus_pay": _weighted_bonus(),
                "description": template["desc"],
                "created_by": None,
                "status": "open",
            }
            
            shifts_to_create.append(shift_record)
    
    # Step 3: Insert all shifts
    if shifts_to_create:
        result = supabase_client.table("open_shifts").insert(shifts_to_create).execute()
        stats["created"] = len(result.data) if result.data else 0
    
    return stats


def ensure_minimum_open_shifts(
    supabase_client,
    organization_id: int = 1,
    minimum: int = 5,
) -> Dict[str, int]:
    """
    Quick check to ensure minimum open shifts exist.
    Call this if you need a lightweight check without full reset.
    
    Returns:
        Dict with stats: {"existing": N, "created": N}
    """
    today = _get_today_for_restaurant(supabase_client, organization_id)
    
    # Check current count
    existing = supabase_client.table("open_shifts") \
        .select("id", count="exact") \
        .eq("organization_id", organization_id) \
        .eq("status", "open") \
        .gte("date", today.isoformat()) \
        .execute()
    
    current_count = existing.count if existing.count else 0
    
    if current_count >= minimum:
        return {"existing": current_count, "created": 0}
    
    # Need to create more
    needed = minimum - current_count
    shifts_to_create = []
    
    for i in range(needed):
        day_offset = random.randint(0, 6)
        shift_date = today + timedelta(days=day_offset)
        position = random.choice(DEMO_POSITIONS)
        template = _get_shift_template(position)
        
        shifts_to_create.append({
            "organization_id": organization_id,
            "position": position,
            "date": shift_date.isoformat(),
            "start_time": template["start"].strftime("%H:%M:%S"),
            "end_time": template["end"].strftime("%H:%M:%S"),
            "bonus_pay": _weighted_bonus(),
            "description": template["desc"],
            "created_by": None,
            "status": "open",
        })
    
    if shifts_to_create:
        supabase_client.table("open_shifts").insert(shifts_to_create).execute()
    
    return {"existing": current_count, "created": len(shifts_to_create)}


def run():
    """
    Entry point for Heroku Scheduler.
    
    Schedule with:
        python modules/nightly_pipeline/demo_open_shift_seeder.py
    """
    import os
    from dotenv import load_dotenv
    from supabase import create_client
    
    load_dotenv()
    
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    
    if not url or not key:
        print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        return
    
    client = create_client(url, key)
    
    print("=" * 50)
    print("OPEN SHIFT MARKETPLACE SEEDER")
    print("=" * 50)
    print("Seeding Demo Bistro (organization_id=1)...")
    
    stats = seed_demo_open_shifts(client, organization_id=1)
    
    print(f"Deleted: {stats['deleted']} stale shifts")
    print(f"Created: {stats['created']} fresh open shifts")
    print("=" * 50)
    print("COMPLETE")


if __name__ == "__main__":
    run()