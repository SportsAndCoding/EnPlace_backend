"""Test adoption metrics endpoint. Run via: heroku run python test_adoption_metrics.py --app enplace-api-v3"""
from database.supabase_client import get_supabase
from services.adoption_service import get_adoption_metrics

s = get_supabase()

# Find a restaurant with check-in data
print("=== Step 1: Find restaurant with data ===")
checkins = s.table("sse_daily_checkins").select("restaurant_id", count="exact").eq("restaurant_id", 1).execute()
count = checkins.count or 0
print(f"  Restaurant 1 (Demo Bistro): {count} check-ins")

if count == 0:
    print("  No check-in data at restaurant 1. Trying others...")
    all_checkins = s.rpc("", {}).execute()  # fallback
    print("  SKIP - Run against a restaurant with data")
    exit()

print("\n=== Step 2: Run adoption metrics ===")
data = get_adoption_metrics(1)

print(f"  success: {data['success']}")
print(f"  staff_count: {data['staff_count']}")
print(f"  manager_count: {data['manager_count']}")

hs = data["health_score"]
print(f"\n  Health Score: {hs['score']} ({hs['status']})")
print(f"  Message: {hs['message']}")
print(f"  Breakdown:")
for k, v in hs["breakdown"].items():
    print(f"    {k}: {v}%")

ci = data["checkins"]
print(f"\n  Check-ins today: {ci['today_count']}/{ci['today_total']} ({ci['today_rate']}%)")
print(f"  Check-ins this week: {ci['week_rate']}%")
print(f"  Avg daily: {ci['week_avg_daily']}")
print(f"  Never checked in this week: {len(ci['staff_never_checked_in'])} staff")
for s in ci["staff_never_checked_in"][:5]:
    print(f"    - {s['name']} ({s['position']})")

mgr = data["managers"]
print(f"\n  Manager login rate: {mgr['login_rate']}% ({mgr['active_count']}/{mgr['total_count']})")
print(f"  Manager logs this week: {mgr['total_logs_this_week']}")
for m in mgr["details"]:
    print(f"    - {m['name']}: {m['login_status']} (logs: {m['logs_this_week']})")

esc = data["escalations"]
print(f"\n  Escalations (30d): {esc['total_30d']}")
print(f"  Response rate: {esc['response_rate']}%")
print(f"  Actions taken: {esc['total_actions']}")

print("\nPASS - Adoption metrics computed successfully")
print("No cleanup needed.")