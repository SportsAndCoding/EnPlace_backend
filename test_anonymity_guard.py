"""Test anonymity guard logic against Demo Bistro data. Run via: heroku run python test_anonymity_guard.py --app enplace-api-v3"""
from services.anonymity_guard import check_anonymity, get_position_counts, get_positions_for_display_role
from database.supabase_client import get_supabase

s = get_supabase()
counts = get_position_counts(s, 1)

print("=== Demo Bistro Position Counts ===")
for pos, cnt in sorted(counts.items(), key=lambda x: x[1]):
    print(f"  {pos}: {cnt}")

print("\n=== Anonymity Check Tests ===")
for position in ["Sous Chef", "Server", "Prep Cook", "Manager", "Host", "Executive Chef"]:
    r = check_anonymity(counts, position)
    flag = "ROLLED UP" if r["anonymity_applied"] else "OK"
    print(f"  {position} ({counts.get(position, 0)} staff) -> {r['display_role']} [{r['rollup_level']}] {flag}")

print("\n=== Category Expansion Tests ===")
print(f"  FOH -> {get_positions_for_display_role('FOH')}")
print(f"  BOH -> {get_positions_for_display_role('BOH')}")
print(f"  All Staff -> {get_positions_for_display_role('All Staff')}")
print(f"  Server -> {get_positions_for_display_role('Server')}")

print("\nDone.")