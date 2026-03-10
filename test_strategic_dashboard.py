"""Test that action board filters correctly when strategic_alerts_only is on. Run via: heroku run python test_strategic_dashboard.py --app enplace-api-v3"""
from database.supabase_client import get_supabase
from services.dashboard_service import get_dashboard_data

s = get_supabase()

# Find an owner at a restaurant with data (Demo Bistro = restaurant 1)
owner = s.table("staff").select("staff_id, full_name").eq("restaurant_id", 1).eq("is_owner", True).limit(1).execute()
if not owner.data:
    print("FAIL - No owner found at restaurant 1")
    exit()

staff_id = owner.data[0]["staff_id"]
name = owner.data[0]["full_name"]
print(f"Testing with owner: {name} ({staff_id})")

print("\n=== Test A: Dashboard with strategic_alerts_only OFF ===")
s.table("staff").update({"strategic_alerts_only": False}).eq("staff_id", staff_id).execute()
data_off = get_dashboard_data(1, staff_id=staff_id)
items_off = data_off["action_board"]["items"]
types_off = [item["type"] for item in items_off]
print(f"  Total items: {len(items_off)}")
print(f"  Types: {types_off}")

print("\n=== Test B: Dashboard with strategic_alerts_only ON ===")
s.table("staff").update({"strategic_alerts_only": True}).eq("staff_id", staff_id).execute()
data_on = get_dashboard_data(1, staff_id=staff_id)
items_on = data_on["action_board"]["items"]
types_on = [item["type"] for item in items_on]
print(f"  Total items: {len(items_on)}")
print(f"  Types: {types_on}")

# Verify no operational types leaked through
operational_types = {"swap_request", "coverage_gap", "pto_request", "schedule_issue"}
leaked = [t for t in types_on if t in operational_types]
if leaked:
    print(f"\n  FAIL - Operational types leaked through: {leaked}")
else:
    print(f"\n  PASS - No operational types in strategic view")

print("\n=== Cleanup: Reset flag ===")
s.table("staff").update({"strategic_alerts_only": False}).eq("staff_id", staff_id).execute()
print("  Flag reset to false")
print("\nDone.")