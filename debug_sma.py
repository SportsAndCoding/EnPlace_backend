from services.network_benchmark_service import compute_organic_sma_score, compute_network_sma_percentile, get_synthetic_sma_scores
from database.supabase_client import supabase
from datetime import date, timedelta

# Check what get_synthetic_sma_scores is actually querying
max_day_result = supabase.table("synthetic_daily_emotions").select("day_index").order("day_index", desc=True).limit(1).execute()
max_day = max_day_result.data[0]["day_index"]
recent_start = max_day - 7

print(f"Max day: {max_day}, Recent start: {recent_start}")

# How many emotion records in that range?
emotions_count = supabase.table("synthetic_daily_emotions").select("id", count="exact").gte("day_index", recent_start).execute()
print(f"Emotion records in range: {emotions_count.count}")

# How many manager logs in that range?
manager_count = supabase.table("synthetic_manager_logs").select("id", count="exact").gte("day_index", recent_start).execute()
print(f"Manager logs in range: {manager_count.count}")

# What does the actual query return?
emotions_result = supabase.table("synthetic_daily_emotions").select("restaurant_id, day_index, mood_emoji").gte("day_index", recent_start).execute()
print(f"Emotions returned: {len(emotions_result.data)}")

manager_result = supabase.table("synthetic_manager_logs").select("restaurant_id, day_index, overall_rating").gte("day_index", recent_start).execute()
print(f"Manager logs returned: {len(manager_result.data)}")