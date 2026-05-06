# check_schema.py
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(
    os.getenv('SUPABASE_URL'),
    os.getenv('SUPABASE_KEY')
)

# From the errors, tables are: sse_shifts, checkins, staff

print("=== SSE_SHIFTS ===")
r = sb.table('sse_shifts').select('*').limit(1).execute()
print(list(r.data[0].keys()) if r.data else 'No data')

print("\n=== CHECKINS ===")
r2 = sb.table('checkins').select('*').limit(1).execute()
print(list(r2.data[0].keys()) if r2.data else 'No data')

print("\n=== STAFF (SMS fields) ===")
r3 = sb.table('staff').select('staff_id, full_name, phone, sms_notifications_enabled, organization_id').limit(1).execute()
print(r3.data[0] if r3.data else 'No data')