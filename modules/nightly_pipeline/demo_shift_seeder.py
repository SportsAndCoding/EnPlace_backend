"""
modules/nightly_pipeline/demo_shift_seeder.py

Ensures Demo Bistro always has realistic shift gaps for demo purposes.
Run nightly to maintain:
- 1-2 critical gaps (today/tomorrow)
- 2-3 warning gaps (next 3-5 days)

This keeps the "critical shift" alert always visible in demos.
"""

from datetime import date, datetime, timedelta
from typing import List, Dict, Any
import hashlib


def _deterministic_random(seed_str: str) -> float:
    """Generate deterministic random [0,1) based on seed string."""
    hash_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    return (hash_val % 1_000_000) / 1_000_000


def seed_demo_shifts(supabase_client, restaurant_id: int = 1) -> Dict[str, int]:
    """
    Ensure Demo Bistro has shifts for the next 7 days with intentional gaps.
    
    Returns stats about what was created/modified.
    """
    
    today = date.today()
    stats = {"created": 0, "gaps_created": 0}
    
    # Define shift templates for each day
    shift_templates = [
        {"shift_type": "AM", "start_hour": 6, "end_hour": 14},
        {"shift_type": "AM", "start_hour": 7, "end_hour": 15},
        {"shift_type": "AM", "start_hour": 8, "end_hour": 16},
        {"shift_type": "MID", "start_hour": 11, "end_hour": 19},
        {"shift_type": "MID", "start_hour": 12, "end_hour": 20},
        {"shift_type": "PM", "start_hour": 14, "end_hour": 22},
        {"shift_type": "PM", "start_hour": 15, "end_hour": 23},
        {"shift_type": "PM", "start_hour": 16, "end_hour": 24},
        {"shift_type": "PM", "start_hour": 17, "end_hour": 1},  # Closes at 1am
        {"shift_type": "FULL", "start_hour": 10, "end_hour": 22},
    ]
    
    # Positions to assign
    positions = ["Server", "Server", "Server", "Bartender", "Host", "Cook", "Cook", "Busser", "Dishwasher", "Manager"]
    
    # Get existing staff for assignment
    staff_result = supabase_client.table("staff") \
        .select("staff_id, position") \
        .eq("restaurant_id", restaurant_id) \
        .eq("status", "Active") \
        .execute()
    
    staff_by_position = {}
    for s in staff_result.data or []:
        pos = s.get("position", "Server")
        if pos not in staff_by_position:
            staff_by_position[pos] = []
        staff_by_position[pos].append(s["staff_id"])
    
    # Define which shifts should be gaps (by day offset and shift index)
    # Format: (day_offset, shift_index) -> True means leave as gap
    intentional_gaps = {
        (0, 2): True,   # Today, 3rd shift - CRITICAL
        (1, 5): True,   # Tomorrow, 6th shift - CRITICAL
        (1, 7): True,   # Tomorrow, 8th shift - CRITICAL
        (3, 1): True,   # 3 days out - WARNING
        (3, 6): True,   # 3 days out - WARNING
        (5, 3): True,   # 5 days out - WARNING
    }
    
    # Generate shifts for next 7 days
    for day_offset in range(7):
        shift_date = today + timedelta(days=day_offset)
        is_weekend = shift_date.weekday() >= 5
        day_type = "weekend" if is_weekend else "weekday"
        
        # Check if shifts already exist for this date
        existing = supabase_client.table("sse_shifts") \
            .select("id") \
            .eq("restaurant_id", restaurant_id) \
            .eq("shift_date", shift_date.isoformat()) \
            .execute()
        
        if existing.data:
            # Shifts exist - just ensure gaps are maintained
            # Find shifts that should be gaps and clear their staff_id
            all_shifts = supabase_client.table("sse_shifts") \
                .select("id, staff_id") \
                .eq("restaurant_id", restaurant_id) \
                .eq("shift_date", shift_date.isoformat()) \
                .order("scheduled_start") \
                .execute()
            
            for idx, shift in enumerate(all_shifts.data or []):
                should_be_gap = (day_offset, idx) in intentional_gaps
                is_gap = shift.get("staff_id") is None
                
                if should_be_gap and not is_gap:
                    # Make this a gap
                    supabase_client.table("sse_shifts") \
                        .update({"staff_id": None}) \
                        .eq("id", shift["id"]) \
                        .execute()
                    stats["gaps_created"] += 1
            
            continue
        
        # No shifts exist - create them
        shifts_to_insert = []
        
        for idx, template in enumerate(shift_templates):
            # Calculate timestamps
            start_hour = template["start_hour"]
            end_hour = template["end_hour"]
            
            if end_hour <= start_hour:
                # Crosses midnight
                end_date = shift_date + timedelta(days=1)
                end_hour = end_hour if end_hour > 0 else 24
            else:
                end_date = shift_date
            
            scheduled_start = datetime(
                shift_date.year, shift_date.month, shift_date.day,
                start_hour, 0, 0
            )
            scheduled_end = datetime(
                end_date.year, end_date.month, end_date.day,
                end_hour % 24, 0, 0
            )
            
            # Determine if this should be a gap
            is_gap = (day_offset, idx) in intentional_gaps
            
            # Assign staff if not a gap
            staff_id = None
            if not is_gap:
                position = positions[idx % len(positions)]
                available = staff_by_position.get(position, []) or staff_by_position.get("Server", [])
                if available:
                    # Rotate through available staff
                    staff_idx = int(_deterministic_random(f"{shift_date}:{idx}") * len(available))
                    staff_id = available[staff_idx % len(available)]
            
            shifts_to_insert.append({
                "restaurant_id": restaurant_id,
                "staff_id": staff_id,
                "shift_date": shift_date.isoformat(),
                "scheduled_start": scheduled_start.isoformat(),
                "scheduled_end": scheduled_end.isoformat(),
                "shift_type": template["shift_type"],
                "day_type": day_type,
                "is_published": True,
                "created_by": "SYSTEM",
            })
            
            if is_gap:
                stats["gaps_created"] += 1
        
        # Insert shifts
        if shifts_to_insert:
            supabase_client.table("sse_shifts").insert(shifts_to_insert).execute()
            stats["created"] += len(shifts_to_insert)
    
    return stats


def ensure_critical_gaps(supabase_client, restaurant_id: int = 1) -> int:
    """
    Quick function to ensure at least 1 critical gap exists for today/tomorrow.
    Call this if you just need to maintain the gap without full shift seeding.
    
    Returns number of gaps created.
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)
    gaps_created = 0
    
    for check_date in [today, tomorrow]:
        # Check if any open shifts exist
        open_shifts = supabase_client.table("sse_shifts") \
            .select("id", count="exact") \
            .eq("restaurant_id", restaurant_id) \
            .eq("shift_date", check_date.isoformat()) \
            .is_("staff_id", "null") \
            .execute()
        
        if open_shifts.count == 0:
            # No open shifts - create one by clearing a staff assignment
            assigned_shifts = supabase_client.table("sse_shifts") \
                .select("id") \
                .eq("restaurant_id", restaurant_id) \
                .eq("shift_date", check_date.isoformat()) \
                .not_.is_("staff_id", "null") \
                .limit(1) \
                .execute()
            
            if assigned_shifts.data:
                supabase_client.table("sse_shifts") \
                    .update({"staff_id": None}) \
                    .eq("id", assigned_shifts.data[0]["id"]) \
                    .execute()
                gaps_created += 1
    
    return gaps_created