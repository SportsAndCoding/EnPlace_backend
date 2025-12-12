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
print(f'Network scores min/max: {min(network_scores):.1f} / {max(network_scores):.1f}')
print(f'Network scores sample: {sorted(network_scores)[:10]}')

result = compute_network_sma_percentile(organic_sma)
print(f'Result: {result}')


from services.network_benchmark_service import compute_organic_sma_score, compute_network_sma_percentile, get_synthetic_sma_scores, get_synthetic_fairness_scores, compute_network_fairness_percentile, compute_organic_fairness_score
from database.supabase_client import supabase
from datetime import date, timedelta

today = date.today()
week_ago = today - timedelta(days=7)

# Get Demo Bistro data
checkins = supabase.table('sse_daily_checkins').select('*').eq('restaurant_id', 1).gte('checkin_date', week_ago.isoformat()).lte('checkin_date', today.isoformat()).execute()

print(f'Checkins: {len(checkins.data)}')

# Test Fairness
organic_fairness = compute_organic_fairness_score(checkins.data)
print(f'Organic Fairness: {organic_fairness}')

fairness_scores = get_synthetic_fairness_scores()
print(f'Fairness scores count: {len(fairness_scores)}')
print(f'Fairness scores min/max: {min(fairness_scores):.1f} / {max(fairness_scores):.1f}')

fairness_result = compute_network_fairness_percentile(organic_fairness)
print(f'Fairness Result: {fairness_result}')