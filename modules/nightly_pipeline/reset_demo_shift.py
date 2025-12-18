"""
Reset demo shift for today - runs via Heroku Scheduler
Creates a 6 PM - 10 PM Server shift for demo restaurant
"""
import os
from datetime import datetime, time
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
DEMO_RESTAURANT_ID = 1


def run():
    """Main entry point for nightly pipeline"""
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    today = datetime.now().date()
    
    print(f"🔄 Resetting demo shift for {today}...")
    
    # Clean up old demo shifts (delete in correct order for FK constraints)
    supabase.table("sse_open_shifts").delete().eq("reason", "DEMO_BILLY_MOMENT").execute()
    supabase.table("sse_shifts").delete().eq("reason", "DEMO_BILLY_MOMENT").execute()
    
    # Create today's 6 PM shift
    start_time = datetime.combine(today, time(18, 0))  # 6 PM
    end_time = datetime.combine(today, time(22, 0))    # 10 PM
    
    result = supabase.table("sse_shifts").insert({
        "restaurant_id": DEMO_RESTAURANT_ID,
        "staff_id": None,
        "shift_date": today.isoformat(),
        "scheduled_start": start_time.isoformat(),
        "scheduled_end": end_time.isoformat(),
        "shift_type": "Server",
        "position": "Server",
        "status": "posted",
        "is_published": True,
        "created_by": "SYSTEM",
        "reason": "DEMO_BILLY_MOMENT"
    }).execute()
    
    if result.data:
        shift_id = result.data[0]['id']
        supabase.table("sse_open_shifts").insert({
            "restaurant_id": DEMO_RESTAURANT_ID,
            "original_shift_id": shift_id,
            "shift_date": today.isoformat(),
            "scheduled_start": start_time.isoformat(),
            "scheduled_end": end_time.isoformat(),
            "shift_type": "Server",
            "reason": "DEMO_BILLY_MOMENT",
            "created_by": "SYSTEM"
        }).execute()
        
        print(f"✅ Created demo shift ID {shift_id} for {today} at 6 PM")
    else:
        print("❌ Failed to create demo shift")


if __name__ == "__main__":
    run()