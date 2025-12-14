"""
modules/nightly_pipeline/demo_hire_reset.py

Resets Stable Hire demo data for Demo Bistro.

Runs nightly to:
1. Delete any candidates created during demos (Bob Boosho, etc.)
2. Reset seed candidates to 'open' status
3. Keep a few as 'hired'/'rejected' for history demo
4. Refresh interview dates to look recent
"""

from datetime import datetime, timedelta
import random
from typing import Dict, Any


# Original seed candidate codes - these are preserved
SEED_CANDIDATES = [
    'CND-2025-0147',  # Marcus T.
    'CND-2025-0148',  # Jade P.
    'CND-2025-0149',  # Derek S.
    'CND-2025-0150',  # Priya M.
    'CND-2025-0151',  # Tyler K.
    'CND-2025-0152',  # Sam W.
    'CND-2025-0190',  # Lisa Chang
    'CND-2025-0195',  # Kevin Hart
    'CND-2025-0188',  # Ryan Foster
    'CND-2025-0201',  # Marcus Thompson Jr.
    'CND-2025-0202',  # Jade Park
    'CND-2025-0203',  # DeShawn Williams
    'CND-2025-0204',  # Aisha Mohammed
    'CND-2025-0205',  # Tommy Chen
    'CND-2025-6818',  # Sarah Martinez
]

# Candidates to keep as "open" (available for demo)
OPEN_CANDIDATES = [
    'CND-2025-0147',  # Marcus T.
    'CND-2025-0148',  # Jade P.
    'CND-2025-0149',  # Derek S.
    'CND-2025-0150',  # Priya M.
    'CND-2025-0151',  # Tyler K.
    'CND-2025-0152',  # Sam W.
    'CND-2025-0201',  # Marcus Thompson Jr.
    'CND-2025-0202',  # Jade Park
    'CND-2025-0203',  # DeShawn Williams
    'CND-2025-0204',  # Aisha Mohammed
    'CND-2025-0205',  # Tommy Chen
    'CND-2025-6818',  # Sarah Martinez
]

# Candidates to show as "hired" (demonstrates successful hires)
HIRED_CANDIDATES = [
    'CND-2025-0190',  # Lisa Chang
    'CND-2025-0188',  # Ryan Foster
]

# Candidates to show as "rejected" (demonstrates archive)
REJECTED_CANDIDATES = [
    'CND-2025-0195',  # Kevin Hart
]


def reset_stable_hire_demo(client, restaurant_id: int = 1) -> Dict[str, Any]:
    """
    Reset Stable Hire candidates to demo-ready state.
    
    Args:
        client: Supabase client
        restaurant_id: Restaurant to reset (default: Demo Bistro = 1)
    
    Returns:
        Dict with counts of actions taken
    """
    stats = {
        'deleted': 0,
        'reset_to_open': 0,
        'set_hired': 0,
        'set_rejected': 0,
        'interview_dates_refreshed': 0,
    }
    
    now = datetime.utcnow()
    
    # Step 1: Delete non-seed candidates (Bob Boosho and friends)
    try:
        # Get all candidate codes for this restaurant
        result = client.table("hiring_candidates") \
            .select("id, candidate_code, name") \
            .eq("restaurant_id", restaurant_id) \
            .execute()
        
        candidates_to_delete = [
            c for c in result.data 
            if c['candidate_code'] not in SEED_CANDIDATES
        ]
        
        for candidate in candidates_to_delete:
            client.table("hiring_candidates") \
                .delete() \
                .eq("id", candidate['id']) \
                .execute()
            print(f"        Deleted demo candidate: {candidate['name']} ({candidate['candidate_code']})")
            stats['deleted'] += 1
            
    except Exception as e:
        print(f"        Warning: Error deleting demo candidates: {e}")
    
    # Step 2: Reset "open" candidates
    try:
        for code in OPEN_CANDIDATES:
            # Random interview date in last 7 days
            days_ago = random.randint(1, 7)
            interview_date = (now - timedelta(days=days_ago)).isoformat()
            
            client.table("hiring_candidates") \
                .update({
                    "status": "open",
                    "hired_at": None,
                    "decision_at": None,
                    "hired_staff_id": None,
                    "interviewed_at": interview_date,
                    "updated_at": now.isoformat(),
                }) \
                .eq("candidate_code", code) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            stats['reset_to_open'] += 1
            stats['interview_dates_refreshed'] += 1
            
    except Exception as e:
        print(f"        Warning: Error resetting open candidates: {e}")
    
    # Step 3: Set "hired" candidates
    try:
        for code in HIRED_CANDIDATES:
            days_ago = random.randint(14, 28)
            hire_date = (now - timedelta(days=days_ago)).isoformat()
            
            client.table("hiring_candidates") \
                .update({
                    "status": "hired",
                    "hired_at": hire_date,
                    "decision_at": hire_date,
                    "updated_at": now.isoformat(),
                }) \
                .eq("candidate_code", code) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            stats['set_hired'] += 1
            
    except Exception as e:
        print(f"        Warning: Error setting hired candidates: {e}")
    
    # Step 4: Set "rejected" candidates
    try:
        for code in REJECTED_CANDIDATES:
            days_ago = random.randint(10, 21)
            reject_date = (now - timedelta(days=days_ago)).isoformat()
            
            client.table("hiring_candidates") \
                .update({
                    "status": "rejected",
                    "hired_at": None,
                    "decision_at": reject_date,
                    "updated_at": now.isoformat(),
                }) \
                .eq("candidate_code", code) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            stats['set_rejected'] += 1
            
    except Exception as e:
        print(f"        Warning: Error setting rejected candidates: {e}")
    
    return stats