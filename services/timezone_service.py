"""
Timezone Service - Restaurant-aware date/time utilities
All user-facing date comparisons should use these functions.
"""
from datetime import datetime, date, timedelta
from typing import Optional
import pytz
from database.supabase_client import supabase

# Cache to avoid repeated DB calls
_timezone_cache = {}


def get_restaurant_timezone(restaurant_id: int) -> str:
    """Get timezone string for a restaurant (cached)."""
    if restaurant_id in _timezone_cache:
        return _timezone_cache[restaurant_id]
    
    try:
        result = supabase.table("restaurants").select("timezone").eq("id", restaurant_id).single().execute()
        tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
    except:
        tz_name = "America/New_York"
    
    _timezone_cache[restaurant_id] = tz_name
    return tz_name


def get_today(restaurant_id: int) -> date:
    """Get today's date in the restaurant's timezone."""
    tz_name = get_restaurant_timezone(restaurant_id)
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()


def get_now(restaurant_id: int) -> datetime:
    """Get current datetime in the restaurant's timezone."""
    tz_name = get_restaurant_timezone(restaurant_id)
    tz = pytz.timezone(tz_name)
    return datetime.now(tz)


def get_date_range(restaurant_id: int, days_back: int = 7) -> tuple[date, date]:
    """Get (start_date, end_date) for a range ending today in restaurant timezone."""
    today = get_today(restaurant_id)
    start = today - timedelta(days=days_back)
    return (start, today)


def clear_timezone_cache():
    """Clear the cache (useful if restaurant timezone is updated)."""
    global _timezone_cache
    _timezone_cache = {}