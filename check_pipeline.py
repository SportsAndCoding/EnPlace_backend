"""
Check nightly pipeline status and recent runs
"""
from database.supabase_client import supabase
from datetime import date, timedelta

print("=" * 60)
print("PIPELINE RUN LOG (last 5)")
print("=" * 60)

result = supabase.table('pipeline_run_log').select('*').order('run_date', desc=True).limit(5).execute()
for r in result.data:
    print(f"{r['run_date']}: {r['status']}")
    print(f"  checkins: {r.get('checkins_generated', 0)}, risks: {r.get('risks_calculated', 0)}")

print("\n" + "=" * 60)
print("DEMO BISTRO SHIFTS (next 7 days)")
print("=" * 60)

today = date.today()
week_out = today + timedelta(days=7)

shifts = supabase.table('sse_shifts') \
    .select('shift_date, staff_id, shift_type') \
    .eq('restaurant_id', 1) \
    .gte('shift_date', today.isoformat()) \
    .lte('shift_date', week_out.isoformat()) \
    .order('shift_date') \
    .execute()

from collections import defaultdict
by_date = defaultdict(lambda: {'total': 0, 'open': 0})

for s in shifts.data:
    d = s['shift_date']
    by_date[d]['total'] += 1
    if not s.get('staff_id'):
        by_date[d]['open'] += 1

for d in sorted(by_date.keys()):
    data = by_date[d]
    status = "⚠️ GAPS" if data['open'] > 0 else "✓"
    print(f"{d}: {data['total']} shifts, {data['open']} open {status}")

print("\n" + "=" * 60)
print("CRITICAL GAPS (today/tomorrow)")
print("=" * 60)

tomorrow = today + timedelta(days=1)
critical = supabase.table('sse_shifts') \
    .select('shift_date, shift_type, scheduled_start') \
    .eq('restaurant_id', 1) \
    .is_('staff_id', 'null') \
    .gte('shift_date', today.isoformat()) \
    .lte('shift_date', tomorrow.isoformat()) \
    .execute()

if critical.data:
    for s in critical.data:
        print(f"  {s['shift_date']} - {s['shift_type']} shift")
else:
    print("  No critical gaps found!")