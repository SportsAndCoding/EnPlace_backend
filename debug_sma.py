from services.network_benchmark_service import compute_organic_sma_score, compute_network_sma_percentile, get_synthetic_sma_scores
from database.supabase_client import supabase
from datetime import date, timedelta

today = date.today()
week_ago = today - timedelta(days=7)

# Get Demo Bistro data
checkins = supabase.table('sse_daily_checkins').select('*').eq('restaurant_id', 1).gte('checkin_date', week_ago.isoformat()).lte('checkin_date', today.isoformat()).execute()
logs = supabase.table('manager_daily_logs').select('*').eq('restaurant_id', 1).gte('log_date', week_ago.isoformat()).lte('log_date', today.isoformat()).execute()

print(f'Checkins: {len(checkins.data)}')
print(f'Manager logs: {len(logs.data)}')

organic_sma = compute_organic_sma_score(checkins.data, logs.data)
print(f'Organic SMA: {organic_sma}')

network_scores = get_synthetic_sma_scores()
print(f'Network scores count: {len(network_scores)}')
print(f'Network scores sample: {sorted(network_scores)[:10]}')

result = compute_network_sma_percentile(organic_sma)
print(f'Result: {result}')