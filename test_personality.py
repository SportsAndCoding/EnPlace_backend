"""
Personality Feature Diagnostic Script
Run: heroku run "python test_personality.py" --app enplace-api-v3
"""
import asyncio
from services.staff_portal_service import StaffPortalService


async def run_tests():
    service = StaffPortalService()
    
    print("=" * 60)
    print("PERSONALITY FEATURE DIAGNOSTICS")
    print("=" * 60)
    
    # ── Test 1: Check if profile exists ──
    print("\n[TEST 1] Get personality profile for S001-001...")
    try:
        profile = await service.get_personality_profile("STAFF001")
        if profile:
            print(f"  ✓ Profile EXISTS — persona: {profile.get('persona_primary')}, score: {profile.get('stability_score')}, source: {profile.get('source')}")
            print(f"  → Skipping Test 2 (profile already saved)")
            skip_save = True
        else:
            print(f"  ✓ Profile is None (not yet completed)")
            skip_save = False
    except Exception as e:
        print(f"  ✗ ERROR: {e}")
        skip_save = False

    # ── Test 2: Save a personality profile ──
    if not skip_save:
        print("\n[TEST 2] Save personality profile for S001-001...")
        try:
            result = await service.save_personality_profile(
                staff_id="STAFF001",
                restaurant_id=1,
                scenario_rankings={
                    "break_room": "alex",
                    "expo_backup": "alex",
                    "schedule_surprise": "jordan",
                    "guest_complaint": "alex",
                    "coworker_tension": "jordan",
                    "new_hire_shadow": "alex",
                    "bar_rush": "jordan",
                    "manager_feedback": "alex"
                },
                source="self_assessment"
            )
            print(f"  ✓ Saved!")
            print(f"    Persona:     {result.get('persona_primary')}")
            print(f"    Score:       {result.get('stability_score')}")
            print(f"    Fingerprint: {result.get('fingerprint')}")
            print(f"    Points:      {result.get('points_awarded')}")
        except Exception as e:
            print(f"  ✗ ERROR: {e}")

    # ── Test 3: Team composition ──
    print("\n[TEST 3] Team composition for restaurant_id=1...")
    try:
        comp = await service.get_team_composition(restaurant_id=1)
        rate = comp.get("completion_rate", {})
        print(f"  ✓ Completion: {rate.get('completed')}/{rate.get('total_active')} ({rate.get('percent')}%)")
        print(f"    Personas:   {comp.get('persona_distribution')}")
        print(f"    Team Avg:   {comp.get('team_fingerprint_avg')}")
        gap = comp.get("gap_analysis")
        if gap:
            print(f"    Gap:        {gap.get('recommendation', 'N/A')[:80]}...")
        else:
            print(f"    Gap:        None (need more profiles)")
    except Exception as e:
        print(f"  ✗ ERROR: {e}")

    # ── Test 4: Verify scoring module import ──
    print("\n[TEST 4] Verify personality_scoring.py module...")
    try:
        from services.personality_scoring import compute_full_profile
        test = compute_full_profile({
            "break_room": "taylor",
            "expo_backup": "taylor",
            "schedule_surprise": "taylor",
            "guest_complaint": "taylor",
            "coworker_tension": "taylor",
            "new_hire_shadow": "taylor",
            "bar_rush": "taylor",
            "manager_feedback": "taylor"
        })
        print(f"  ✓ All-Taylor profile: persona={test['persona_primary']}, score={test['stability_score']}")
        print(f"    Fingerprint: {test['fingerprint']}")
    except Exception as e:
        print(f"  ✗ ERROR: {e}")

    # ── Test 5: Route registration check ──
    print("\n[TEST 5] Verify routes are registered...")
    try:
        from app import app
        personality_routes = [r.path for r in app.routes if "personality" in getattr(r, 'path', '')]
        composition_routes = [r.path for r in app.routes if "composition" in getattr(r, 'path', '')]
        all_found = personality_routes + composition_routes
        if all_found:
            for route in all_found:
                print(f"  ✓ {route}")
        else:
            print(f"  ⚠ No personality/composition routes found in app.routes")
            print(f"    Checking staff-portal routes...")
            sp_routes = [r.path for r in app.routes if "staff-portal" in getattr(r, 'path', '')]
            for route in sp_routes:
                print(f"    {route}")
    except Exception as e:
        print(f"  ✗ ERROR: {e}")

    print("\n" + "=" * 60)
    print("DIAGNOSTICS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_tests())