"""Test strategic alerts flag. Run via: heroku run python test_strategic_alerts.py --app enplace-api-v3"""
from database.supabase_client import get_supabase

s = get_supabase()

print("=== Step 1: Verify migration ===")
owners = s.table("staff").select("staff_id, full_name, organization_id, is_owner, strategic_alerts_only").eq("is_owner", True).execute()
if not owners.data:
    print("FAIL - No owners found")
    exit()
for o in owners.data:
    print(f"  {o['full_name']} (restaurant {o['organization_id']}): strategic_alerts_only = {o.get('strategic_alerts_only')}")

print("\n=== Step 2: Enable strategic_alerts_only on first owner ===")
owner = owners.data[0]
s.table("staff").update({"strategic_alerts_only": True}).eq("staff_id", owner["staff_id"]).execute()
check = s.table("staff").select("staff_id, full_name, strategic_alerts_only").eq("staff_id", owner["staff_id"]).single().execute()
print(f"  {check.data['full_name']}: strategic_alerts_only = {check.data['strategic_alerts_only']}")
if check.data["strategic_alerts_only"] == True:
    print("  PASS - Flag set correctly")
else:
    print("  FAIL - Flag not set")

print("\n=== Step 3: Reset flag back to false ===")
s.table("staff").update({"strategic_alerts_only": False}).eq("staff_id", owner["staff_id"]).execute()
check2 = s.table("staff").select("staff_id, full_name, strategic_alerts_only").eq("staff_id", owner["staff_id"]).single().execute()
print(f"  {check2.data['full_name']}: strategic_alerts_only = {check2.data['strategic_alerts_only']}")
if check2.data["strategic_alerts_only"] == False:
    print("  PASS - Flag reset correctly")
else:
    print("  FAIL - Flag not reset")

print("\nDone. No cleanup needed - flag was reset.")