from database.supabase_client import supabase

# What staff exist for Demo Bistro?
staff = supabase.table("staff").select("staff_id, full_name, status, restaurant_id").eq("restaurant_id", 1).execute()
print(f"All staff for restaurant 1: {len(staff.data or [])}")
for s in (staff.data or []):
    print(f"  {s['staff_id']} - {s['full_name']} - status: {s['status']}")

# What about checkins - any at all?
checkins = supabase.table("checkins").select("checkin_date, staff_id").eq("restaurant_id", 1).order("checkin_date", desc=True).limit(5).execute()
print(f"\nRecent checkins: {len(checkins.data or [])}")
for c in (checkins.data or []):
    print(f"  {c['checkin_date']} - {c['staff_id']}")

# SSE daily checkins (the nightly pipeline seeds these)
sse = supabase.table("sse_daily_checkins").select("checkin_date, staff_id, mood_score").eq("restaurant_id", 1).order("checkin_date", desc=True).limit(5).execute()
print(f"\nSSE daily checkins: {len(sse.data or [])}")
for c in (sse.data or []):
    print(f"  {c['checkin_date']} - {c['staff_id']} - mood: {c.get('mood_score')}")