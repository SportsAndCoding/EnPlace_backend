from database.supabase_client import supabase
from datetime import date, timedelta

yesterday = date.today() - timedelta(days=1)
yesterday_str = yesterday.isoformat()
print(f"Target date: {yesterday_str}")

# Check 1: Active staff
staff = supabase.table("staff").select("staff_id, status").eq("restaurant_id", 1).eq("status", "active").execute()
print(f"\n1. Active staff: {len(staff.data or [])}")
for s in (staff.data or [])[:5]:
    print(f"   {s['staff_id']} - {s['status']}")

# Check 2: Shifts on that date
shifts = supabase.table("sse_shifts").select("id, staff_id, shift_date, status").eq("restaurant_id", 1).eq("shift_date", yesterday_str).execute()
print(f"\n2. Shifts on {yesterday_str}: {len(shifts.data or [])}")
for s in (shifts.data or [])[:5]:
    print(f"   {s['staff_id']} - {s['status']}")

# Check 3: Checkins on that date
checkins = supabase.table("checkins").select("staff_id, mood_rating, checkin_date").eq("restaurant_id", 1).eq("checkin_date", yesterday_str).execute()
print(f"\n3. Checkins on {yesterday_str}: {len(checkins.data or [])}")

# Check 4: What dates DO have data?
recent_shifts = supabase.table("sse_shifts").select("shift_date").eq("restaurant_id", 1).order("shift_date", desc=True).limit(5).execute()
print(f"\n4. Most recent shift dates:")
for s in (recent_shifts.data or []):
    print(f"   {s['shift_date']}")

recent_checkins = supabase.table("checkins").select("checkin_date").eq("restaurant_id", 1).order("checkin_date", desc=True).limit(5).execute()
print(f"\n5. Most recent checkin dates:")
for c in (recent_checkins.data or []):
    print(f"   {c['checkin_date']}")