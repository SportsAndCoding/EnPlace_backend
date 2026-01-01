#!/usr/bin/env python3
"""
Demo Bistro Nightly Seeder
--------------------------
Heroku Scheduler job that runs nightly to ensure Demo Bistro (restaurant_id=1)
always has fresh shifts and pending swap requests for sales demos.

Run manually: heroku run "python seed_demo_bistro.py" --app enplace-api-v3
"""

import os
import random
from datetime import datetime, timedelta, timezone, date
from supabase import create_client, Client

# ============================================================
# CONFIGURATION
# ============================================================

DEMO_RESTAURANT_ID = 1
DEMO_MANAGER_ID = "MGR001"  # Test User - creates shifts/decides swaps

# Staff pools by position (active Demo Bistro staff only)
SERVERS = [
    "SRV001", "SRV002", "SRV003", "SRV004", "STAFF003", "STAFF005",
    "STAFF027", "STAFF028", "STAFF029", "STAFF030", "STAFF031",
    "STAFF032", "STAFF033", "STAFF034", "STAFF035", "STAFF036",
    "STAFF038", "STAFF040", "STAFF041", "STAFF042"
]

BARTENDERS = [
    "BAR001", "BAR002", "STAFF004", "STAFF043", "STAFF044", "STAFF045"
]

HOSTS = [
    "HST001", "STAFF047", "STAFF048", "STAFF049"
]

BUSSERS = [
    "BUS001", "STAFF051", "STAFF052", "STAFF053", "STAFF054", 
    "STAFF055", "STAFF061", "STAFF062"
]

LINE_COOKS = [
    "COK001", "COK002", "STAFF002", "STAFF016", "STAFF017", 
    "STAFF018", "STAFF019", "STAFF020", "STAFF021", "STAFF022", "STAFF039"
]

DISHWASHERS = [
    "DSH001", "DSH002", "STAFF056", "STAFF057", "STAFF058", "STAFF059"
]

# Shift templates: (start_hour, end_hour, shift_type)
SHIFT_TEMPLATES = {
    "morning": (10, 16, "morning"),
    "evening": (16, 22, "evening"),
    "mid": (12, 20, "mid"),
    "close": (18, 24, "close"),
}

# Realistic swap request reasons
SWAP_REASONS = [
    "Doctor's appointment I forgot about",
    "My kid's school play is that evening",
    "Family visiting from out of town",
    "Car issues, need to take it to the shop",
    "Have a final exam that morning",
    "Feeling under the weather, hoping to rest",
    "Dentist appointment I can't reschedule",
    "Need to help a friend move",
    "Wedding I RSVP'd to months ago",
    "Childcare fell through for that day",
    "Anniversary dinner with my partner",
    "Landlord needs access for repairs",
    "Jury duty summons",
    "Pet has a vet appointment",
    "College orientation for my daughter",
]


# ============================================================
# SUPABASE CONNECTION
# ============================================================

def get_supabase() -> Client:
    """Initialize Supabase client using environment variables"""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
    
    return create_client(url, key)


# ============================================================
# CLEANUP FUNCTIONS
# ============================================================

def cleanup_old_swaps(supabase: Client) -> int:
    """Delete pending swaps older than 7 days or for past shifts"""
    try:
        # Get all pending swaps for Demo Bistro
        result = supabase.table("shift_swaps") \
            .select("id, shift_id, created_at") \
            .eq("restaurant_id", DEMO_RESTAURANT_ID) \
            .eq("status", "pending") \
            .execute()
        
        if not result.data:
            return 0
        
        today = date.today().isoformat()
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        
        # Get shift dates for these swaps
        shift_ids = [s['shift_id'] for s in result.data]
        shifts_result = supabase.table("sse_shifts") \
            .select("id, shift_date") \
            .in_("id", shift_ids) \
            .execute()
        
        shift_dates = {s['id']: s['shift_date'] for s in (shifts_result.data or [])}
        
        # Find swaps to delete (past shifts or old pending)
        to_delete = []
        for swap in result.data:
            shift_date = shift_dates.get(swap['shift_id'], '9999-99-99')
            created_at = swap.get('created_at', '')
            
            if shift_date < today or created_at < seven_days_ago:
                to_delete.append(swap['id'])
        
        # Delete old swaps
        if to_delete:
            supabase.table("shift_swaps") \
                .delete() \
                .in_("id", to_delete) \
                .execute()
        
        return len(to_delete)
    
    except Exception as e:
        print(f"Error cleaning up swaps: {e}")
        return 0


def cleanup_past_shifts(supabase: Client) -> int:
    """Delete Demo Bistro shifts older than 14 days"""
    try:
        cutoff = (date.today() - timedelta(days=14)).isoformat()
        
        result = supabase.table("sse_shifts") \
            .delete() \
            .eq("restaurant_id", DEMO_RESTAURANT_ID) \
            .lt("shift_date", cutoff) \
            .execute()
        
        return len(result.data) if result.data else 0
    
    except Exception as e:
        print(f"Error cleaning up shifts: {e}")
        return 0


# ============================================================
# SEEDING FUNCTIONS
# ============================================================

def get_existing_shifts(supabase: Client, start_date: date, end_date: date) -> set:
    """Get existing shift (staff_id, date) combos to avoid duplicates"""
    try:
        result = supabase.table("sse_shifts") \
            .select("staff_id, shift_date") \
            .eq("restaurant_id", DEMO_RESTAURANT_ID) \
            .gte("shift_date", start_date.isoformat()) \
            .lte("shift_date", end_date.isoformat()) \
            .execute()
        
        return {(s['staff_id'], s['shift_date']) for s in (result.data or [])}
    
    except Exception as e:
        print(f"Error getting existing shifts: {e}")
        return set()


def seed_shifts(supabase: Client) -> int:
    """Seed shifts for the next 14 days"""
    today = date.today()
    end_date = today + timedelta(days=14)
    
    existing = get_existing_shifts(supabase, today, end_date)
    shifts_to_create = []
    
    # Define staffing per day
    daily_staffing = {
        "Server": (SERVERS, 4, 6),       # 4-6 servers per day
        "Bartender": (BARTENDERS, 2, 3), # 2-3 bartenders
        "Host": (HOSTS, 1, 2),           # 1-2 hosts
        "Busser": (BUSSERS, 2, 3),       # 2-3 bussers
        "Line Cook": (LINE_COOKS, 3, 4), # 3-4 line cooks
        "Dishwasher": (DISHWASHERS, 1, 2) # 1-2 dishwashers
    }
    
    for day_offset in range(15):
        shift_date = today + timedelta(days=day_offset)
        day_of_week = shift_date.weekday()
        is_weekend = day_of_week >= 5
        day_type = "weekend" if is_weekend else "weekday"
        
        for position, (staff_pool, min_count, max_count) in daily_staffing.items():
            # More staff on weekends
            count = random.randint(min_count, max_count)
            if is_weekend:
                count = min(count + 1, len(staff_pool))
            
            # Pick random staff for this position
            selected_staff = random.sample(staff_pool, min(count, len(staff_pool)))
            
            for staff_id in selected_staff:
                # Skip if shift already exists
                if (staff_id, shift_date.isoformat()) in existing:
                    continue
                
                # Pick shift type (more evening on weekends)
                if is_weekend:
                    shift_type = random.choice(["morning", "evening", "evening", "mid"])
                else:
                    shift_type = random.choice(["morning", "evening", "mid"])
                
                start_hour, end_hour, _ = SHIFT_TEMPLATES[shift_type]
                
                # Build timestamps
                scheduled_start = datetime(
                    shift_date.year, shift_date.month, shift_date.day,
                    start_hour, 0, 0, tzinfo=timezone.utc
                )
                scheduled_end = datetime(
                    shift_date.year, shift_date.month, shift_date.day,
                    end_hour if end_hour < 24 else 23, 
                    0 if end_hour < 24 else 59, 
                    0, tzinfo=timezone.utc
                )
                
                shifts_to_create.append({
                    "restaurant_id": DEMO_RESTAURANT_ID,
                    "staff_id": staff_id,
                    "shift_date": shift_date.isoformat(),
                    "scheduled_start": scheduled_start.isoformat(),
                    "scheduled_end": scheduled_end.isoformat(),
                    "shift_type": shift_type,
                    "day_type": day_type,
                    "is_published": True,
                    "created_by": DEMO_MANAGER_ID,
                    "status": "assigned"
                })
    
    # Batch insert
    if shifts_to_create:
        try:
            # Insert in batches of 50
            for i in range(0, len(shifts_to_create), 50):
                batch = shifts_to_create[i:i+50]
                supabase.table("sse_shifts").insert(batch).execute()
        except Exception as e:
            print(f"Error inserting shifts: {e}")
            return 0
    
    return len(shifts_to_create)


def seed_swap_requests(supabase: Client) -> int:
    """Create 5-8 pending swap requests for upcoming shifts"""
    try:
        # First, clear existing pending swaps for Demo Bistro (fresh slate each night)
        supabase.table("shift_swaps") \
            .delete() \
            .eq("restaurant_id", DEMO_RESTAURANT_ID) \
            .eq("status", "pending") \
            .execute()
        
        # Get upcoming shifts (next 7 days, excluding today)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        week_out = (date.today() + timedelta(days=7)).isoformat()
        
        result = supabase.table("sse_shifts") \
            .select("id, staff_id, shift_date, shift_type, scheduled_start") \
            .eq("restaurant_id", DEMO_RESTAURANT_ID) \
            .eq("status", "assigned") \
            .gte("shift_date", tomorrow) \
            .lte("shift_date", week_out) \
            .execute()
        
        shifts = result.data or []
        if not shifts:
            print("No upcoming shifts found to create swap requests")
            return 0
        
        # Pick 5-8 random shifts for swap requests
        num_swaps = random.randint(5, 8)
        selected_shifts = random.sample(shifts, min(num_swaps, len(shifts)))
        
        swaps_to_create = []
        used_staff = set()
        
        for shift in selected_shifts:
            requesting_staff = shift['staff_id']
            
            # Avoid same person requesting multiple swaps
            if requesting_staff in used_staff:
                continue
            used_staff.add(requesting_staff)
            
            # 60% chance of having a target (swap with someone)
            # 40% chance of just giving up shift (open request)
            has_target = random.random() < 0.6
            
            target_staff_id = None
            if has_target:
                # Find a coworker in the same role
                if requesting_staff in SERVERS:
                    pool = [s for s in SERVERS if s != requesting_staff]
                elif requesting_staff in BARTENDERS:
                    pool = [s for s in BARTENDERS if s != requesting_staff]
                elif requesting_staff in HOSTS:
                    pool = [s for s in HOSTS if s != requesting_staff]
                elif requesting_staff in BUSSERS:
                    pool = [s for s in BUSSERS if s != requesting_staff]
                elif requesting_staff in LINE_COOKS:
                    pool = [s for s in LINE_COOKS if s != requesting_staff]
                elif requesting_staff in DISHWASHERS:
                    pool = [s for s in DISHWASHERS if s != requesting_staff]
                else:
                    pool = SERVERS  # fallback
                
                if pool:
                    target_staff_id = random.choice(pool)
            
            # Vary created_at to look natural (1-48 hours ago)
            hours_ago = random.randint(1, 48)
            created_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
            
            swaps_to_create.append({
                "restaurant_id": DEMO_RESTAURANT_ID,
                "shift_id": shift['id'],
                "requesting_staff_id": requesting_staff,
                "target_staff_id": target_staff_id,
                "status": "pending",
                "reason": random.choice(SWAP_REASONS),
                "created_at": created_at
            })
        
        # Insert swaps
        if swaps_to_create:
            supabase.table("shift_swaps").insert(swaps_to_create).execute()
        
        return len(swaps_to_create)
    
    except Exception as e:
        print(f"Error seeding swap requests: {e}")
        return 0


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    print("=" * 60)
    print("Demo Bistro Nightly Seeder")
    print(f"Run time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    supabase = get_supabase()
    
    # Step 1: Cleanup
    print("\n[1/4] Cleaning up old swap requests...")
    deleted_swaps = cleanup_old_swaps(supabase)
    print(f"      Deleted {deleted_swaps} old pending swaps")
    
    print("\n[2/4] Cleaning up past shifts...")
    deleted_shifts = cleanup_past_shifts(supabase)
    print(f"      Deleted {deleted_shifts} shifts older than 14 days")
    
    # Step 2: Seed fresh data
    print("\n[3/4] Seeding upcoming shifts...")
    new_shifts = seed_shifts(supabase)
    print(f"      Created {new_shifts} new shifts")
    
    print("\n[4/4] Seeding swap requests...")
    new_swaps = seed_swap_requests(supabase)
    print(f"      Created {new_swaps} pending swap requests")
    
    print("\n" + "=" * 60)
    print("Demo Bistro seeding complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()