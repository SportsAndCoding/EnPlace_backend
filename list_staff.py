"""
List Demo Bistro active staff
Run: heroku run "python list_staff.py" --app enplace-api-v3
"""
from database.supabase_client import get_supabase

s = get_supabase()
r = s.table("staff").select("staff_id, full_name, position, restaurant_id").eq("restaurant_id", 1).eq("status", "active").execute()

print(f"Demo Bistro (restaurant_id=1) — {len(r.data)} active staff\n")
for row in r.data:
    print(f"  {row['staff_id']:20s}  {row['full_name']:25s}  {row['position']}")