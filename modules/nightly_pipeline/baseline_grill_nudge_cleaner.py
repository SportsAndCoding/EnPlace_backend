"""
modules/nightly_pipeline/baseline_grill_nudge_cleaner.py

Clears nudges for Baseline Grill (restaurant_id=11) nightly.
This ensures the paywall demo always starts fresh - staff can nudge manager,
manager can see nudge in action board, rinse and repeat for each demo.
"""

from typing import Dict


BASELINE_GRILL_ID = 11


def clear_baseline_grill_nudges(supabase_client) -> Dict[str, int]:
    """
    Delete all nudges for Baseline Grill to reset demo state.
    
    Returns:
        Dict with stats: {"deleted": N}
    """
    result = supabase_client.table("nudges") \
        .delete() \
        .eq("restaurant_id", BASELINE_GRILL_ID) \
        .execute()
    
    deleted_count = len(result.data) if result.data else 0
    
    return {"deleted": deleted_count}


def run():
    """
    Entry point for standalone execution.
    
    Usage:
        python modules/nightly_pipeline/baseline_grill_nudge_cleaner.py
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
    print("BASELINE GRILL NUDGE CLEANER")
    print("=" * 50)
    print(f"Clearing nudges for Baseline Grill (restaurant_id={BASELINE_GRILL_ID})...")
    
    stats = clear_baseline_grill_nudges(client)
    
    print(f"Deleted: {stats['deleted']} nudges")
    print("=" * 50)
    print("COMPLETE")


if __name__ == "__main__":
    run()