"""Create a test escalation for Sous Chef at Demo Bistro. Run via: heroku run python test_anonymity_escalation.py --app enplace-api-v3"""
import asyncio
from services.escalations_service import EscalationsService

async def test():
    svc = EscalationsService()
    result = await svc.create_escalation(
        escalation_data={
            "restaurant_id": 1,
            "event_type": "mood_drop",
            "severity": "moderate",
            "affected_role": "Sous Chef",
            "trigger_reason": "ANONYMITY TEST - safe to delete",
            "source_type": "mood"
        },
        created_by="system",
        auto_created=True
    )
    print("=== Escalation Created ===")
    print(f"  id: {result['id']}")
    print(f"  affected_role: {result['affected_role']}")
    print(f"  anonymity_applied: {result['anonymity_applied']}")
    print(f"  original_affected_role: {result['original_affected_role']}")
    print(f"  rollup_level: {result['rollup_level']}")
    print()
    if result["anonymity_applied"] and result["affected_role"] != "Sous Chef":
        print("PASS - Sous Chef was rolled up correctly")
    else:
        print("FAIL - Sous Chef should have been rolled up")
    print()
    print(f"Cleanup SQL:")
    print(f"  DELETE FROM sse_escalation_history WHERE event_id = '{result['id']}';")
    print(f"  DELETE FROM sse_escalation_events WHERE id = '{result['id']}';")

asyncio.run(test())