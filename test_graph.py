from database.supabase_client import supabase
from datetime import date, timedelta

yesterday = (date.today() - timedelta(days=1)).isoformat()

# Active staff count
staff = supabase.table("staff").select("staff_id").eq("restaurant_id", 1).in_("status", ["active", "Active"]).execute()
print(f"Active staff: {len(staff.data or [])}")

# Recent checkins from sse_daily_checkins
sse = supabase.table("sse_daily_checkins").select("checkin_date, staff_id, mood_emoji").eq("restaurant_id", 1).order("checkin_date", desc=True).limit(5).execute()
print(f"\nRecent sse_daily_checkins: {len(sse.data or [])}")
for c in (sse.data or []):
    print(f"  {c['checkin_date']} - {c['staff_id']} - mood: {c.get('mood_emoji')}")

# Recent shifts
shifts = supabase.table("sse_shifts").select("shift_date, staff_id").eq("restaurant_id", 1).not_.is_("staff_id", "null").eq("status", "assigned").order("shift_date", desc=True).limit(5).execute()
print(f"\nRecent assigned shifts: {len(shifts.data or [])}")
for s in (shifts.data or []):
    print(f"  {s['shift_date']} - {s['staff_id']}")