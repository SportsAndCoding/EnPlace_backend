"""
modules/nightly_pipeline/demo_swap_seeder.py

Seeds the Shift Swap feature for Demo Bistro (restaurant_id=1).
Ensures fresh, realistic swap requests are always available for sales demos.

Demo user: SRV001 (server@demobistro.com)

Runs as part of nightly pipeline to:
1. Clear stale/resolved swap requests
2. Seed 3-5 pending swap requests (mix of broadcast + direct to SRV001)
3. Maintain realistic reasons and timing
"""

import random
from datetime import datetime, timedelta
from typing import Dict, Any, List
import pytz


# Realistic swap request reasons
SWAP_REASONS = [
    "Family emergency - need coverage ASAP",
    "Car broke down, stuck at the mechanic",
    "Childcare fell through last minute",
    "Doctor appointment I can't reschedule",
    "Kid's school event I forgot about",
    "Not feeling well, might be coming down with something",
    "Wedding I RSVP'd to months ago",
    "Have to pick up family from airport",
    "Plumber finally available to fix leak",
    "College orientation for my daughter",
    "Pet has a vet appointment",
    "DMV appointment (finally got one!)",
]

# Staff who can request swaps (not SRV001 - that's the demo user)
REQUESTING_STAFF = [
    {"staff_id": "STAFF005", "name": "Emily Parker"},
    {"staff_id": "STAFF028", "name": "Daniel Martinez"},
    {"staff_id": "STAFF049", "name": "Rachel Kim"},
    {"staff_id": "SRV002", "name": "David Kim"},
    {"staff_id": "STAFF053", "name": "Amanda Foster"},
    {"staff_id": "STAFF032", "name": "Kimberly Clark"},
    {"staff_id": "BUS001", "name": "Marcus Johnson"},
    {"staff_id": "DSH002", "name": "Tyler Wong"},
]

# Demo user who logs in to see incoming requests
DEMO_USER_STAFF_ID = "SRV001"


def _get_today_for_restaurant(supabase_client, restaurant_id: int):
    """Get today's date in restaurant timezone."""
    try:
        result = supabase_client.table("restaurants").select("timezone").eq("id", restaurant_id).single().execute()
        tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
    except:
        tz_name = "America/New_York"
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()


def seed_demo_swap_requests(
    supabase_client,
    restaurant_id: int = 1,
    num_broadcast: int = 3,
    num_direct: int = 2,
) -> Dict[str, int]:
    """
    Seed shift swap requests for Demo Bistro.
    
    Creates a mix of:
    - Broadcast swaps (target_staff_id = NULL) - anyone can accept
    - Direct swaps (target_staff_id = SRV001) - specifically asking demo user
    
    Args:
        supabase_client: Initialized Supabase client
        restaurant_id: Target restaurant (default: Demo Bistro = 1)
        num_broadcast: Number of broadcast swap requests
        num_direct: Number of direct requests to demo user
    
    Returns:
        Dict with stats: {"deleted": N, "created": N}
    """
    today = _get_today_for_restaurant(supabase_client, restaurant_id)
    stats = {"deleted": 0, "created": 0}
    
    # Step 1: Delete old pending demo swap requests
    # Keep approved/rejected for history, but clear pending to refresh
    delete_result = supabase_client.table("shift_swaps") \
        .delete() \
        .eq("restaurant_id", restaurant_id) \
        .eq("status", "pending") \
        .execute()
    stats["deleted"] = len(delete_result.data) if delete_result.data else 0
    
    # Step 2: Get valid future shifts to attach swap requests to
    # Need shifts that belong to staff OTHER than SRV001
    shifts_result = supabase_client.table("sse_shifts") \
        .select("id, staff_id, shift_date, scheduled_start") \
        .eq("restaurant_id", restaurant_id) \
        .gte("shift_date", today.isoformat()) \
        .lte("shift_date", (today + timedelta(days=7)).isoformat()) \
        .neq("staff_id", DEMO_USER_STAFF_ID) \
        .not_.is_("staff_id", "null") \
        .order("shift_date") \
        .execute()
    
    available_shifts = shifts_result.data or []
    
    if len(available_shifts) < (num_broadcast + num_direct):
        print(f"Warning: Only {len(available_shifts)} valid shifts found for swap seeding")
        # Adjust numbers if not enough shifts
        total_needed = num_broadcast + num_direct
        if available_shifts:
            num_broadcast = min(num_broadcast, len(available_shifts) - 1)
            num_direct = min(num_direct, len(available_shifts) - num_broadcast)
        else:
            return stats
    
    # Shuffle to get random shifts
    random.shuffle(available_shifts)
    
    # Step 3: Create swap requests
    swaps_to_create = []
    shifts_used = set()
    
    # Get staff IDs that have shifts we can use
    staff_with_shifts = {s["staff_id"] for s in available_shifts}
    eligible_requesters = [r for r in REQUESTING_STAFF if r["staff_id"] in staff_with_shifts]
    
    if not eligible_requesters:
        print("Warning: No eligible requesters found with shifts")
        return stats
    
    # Create broadcast swaps (anyone can accept)
    for i in range(num_broadcast):
        if i >= len(available_shifts):
            break
            
        shift = available_shifts[i]
        if shift["id"] in shifts_used:
            continue
            
        # Find the requester who owns this shift
        requester = next(
            (r for r in eligible_requesters if r["staff_id"] == shift["staff_id"]),
            random.choice(eligible_requesters)
        )
        
        hours_ago = random.randint(1, 12)
        
        swaps_to_create.append({
            "restaurant_id": restaurant_id,
            "shift_id": shift["id"],
            "requesting_staff_id": requester["staff_id"],
            "target_staff_id": None,  # Broadcast - anyone can grab
            "status": "pending",
            "reason": random.choice(SWAP_REASONS),
            "created_at": (datetime.now() - timedelta(hours=hours_ago)).isoformat(),
        })
        shifts_used.add(shift["id"])
    
    # Create direct swaps (specifically to demo user SRV001)
    for i in range(num_broadcast, num_broadcast + num_direct):
        if i >= len(available_shifts):
            break
            
        shift = available_shifts[i]
        if shift["id"] in shifts_used:
            continue
            
        requester = next(
            (r for r in eligible_requesters if r["staff_id"] == shift["staff_id"]),
            random.choice(eligible_requesters)
        )
        
        hours_ago = random.randint(2, 8)
        
        # Personal message reasons for direct requests
        direct_reasons = [
            "Hey! Can you cover for me? I have a wedding",
            "Would you be able to swap? Doctor appointment",
            "Any chance you can take this shift? Family thing came up",
            "Could really use your help - car issues",
            "Would owe you big time if you could cover!",
        ]
        
        swaps_to_create.append({
            "restaurant_id": restaurant_id,
            "shift_id": shift["id"],
            "requesting_staff_id": requester["staff_id"],
            "target_staff_id": DEMO_USER_STAFF_ID,  # Direct to demo user
            "status": "pending",
            "reason": random.choice(direct_reasons),
            "created_at": (datetime.now() - timedelta(hours=hours_ago)).isoformat(),
        })
        shifts_used.add(shift["id"])
    
    # Step 4: Insert all swap requests
    if swaps_to_create:
        result = supabase_client.table("shift_swaps").insert(swaps_to_create).execute()
        stats["created"] = len(result.data) if result.data else 0
    
    return stats


def ensure_minimum_pending_swaps(
    supabase_client,
    restaurant_id: int = 1,
    minimum: int = 3,
) -> Dict[str, int]:
    """
    Quick check to ensure minimum pending swaps exist for demo.
    Call this if you need a lightweight check without full reset.
    
    Returns:
        Dict with stats: {"existing": N, "created": N}
    """
    # Check current pending count
    existing = supabase_client.table("shift_swaps") \
        .select("id", count="exact") \
        .eq("restaurant_id", restaurant_id) \
        .eq("status", "pending") \
        .execute()
    
    current_count = existing.count if existing.count else 0
    
    if current_count >= minimum:
        return {"existing": current_count, "created": 0}
    
    # Need to create more - run full seed
    stats = seed_demo_swap_requests(
        supabase_client,
        restaurant_id=restaurant_id,
        num_broadcast=2,
        num_direct=1,
    )
    
    return {"existing": current_count, "created": stats["created"]}


def run():
    """
    Entry point for standalone execution.
    
    Usage:
        python modules/nightly_pipeline/demo_swap_seeder.py
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
    print("SHIFT SWAP SEEDER")
    print("=" * 50)
    print("Seeding Demo Bistro (restaurant_id=1)...")
    print(f"Demo user: {DEMO_USER_STAFF_ID}")
    
    stats = seed_demo_swap_requests(client, restaurant_id=1)
    
    print(f"Deleted: {stats['deleted']} old pending swaps")
    print(f"Created: {stats['created']} fresh swap requests")
    print("=" * 50)
    print("COMPLETE")


if __name__ == "__main__":
    run()