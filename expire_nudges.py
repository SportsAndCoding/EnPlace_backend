"""
Expire old pending nudges (30+ days old).
Run daily via Heroku Scheduler: python expire_nudges.py
"""

from datetime import datetime, timedelta
from database.supabase_client import get_supabase

def expire_old_nudges():
    """Delete pending nudges older than 30 days."""
    supabase = get_supabase()
    
    thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
    
    try:
        result = supabase.table("nudges") \
            .delete() \
            .eq("status", "pending") \
            .lt("created_at", thirty_days_ago) \
            .execute()
        
        deleted_count = len(result.data) if result.data else 0
        print(f"[{datetime.utcnow().isoformat()}] Expired {deleted_count} old nudges")
        return deleted_count
        
    except Exception as e:
        print(f"[{datetime.utcnow().isoformat()}] ERROR expiring nudges: {e}")
        raise e

if __name__ == "__main__":
    expire_old_nudges()