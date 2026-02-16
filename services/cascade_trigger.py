"""
services/cascade_trigger.py

When a staff member is deactivated (quits/fired), this module:
1. Looks up their pre-computed cascade analysis
2. Identifies at-risk connected staff
3. Creates targeted escalation events for each at-risk person
4. Applies exit shock modifiers to the graph

These escalations appear immediately on the Action Board as
"Cascade Risk" items with specific coaching language for the manager.

The cascade analysis is pre-computed nightly by graph_pipeline.py
and stored in staff_cascade_analysis. This trigger just reads it
and converts it into actionable escalations.

PRIVACY: Escalation trigger_reason references the departed staff
by name (observable event — they quit, everyone knows). The at-risk
staff are identified by graph position, NOT by mood data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from database.supabase_client import supabase

logger = logging.getLogger(__name__)


def trigger_exit_cascade(
    departed_staff_id: str,
    restaurant_id: int,
    departed_name: str = "A team member",
) -> Dict[str, Any]:
    """
    Fire cascade escalations when a staff member leaves.

    Called from deactivate_staff_member() after the status update.
    Non-blocking: failures are logged but don't break deactivation.

    Parameters
    ----------
    departed_staff_id : str
        The staff_id of the person who left.
    restaurant_id : int
        Restaurant ID.
    departed_name : str
        Display name for the escalation messages.

    Returns
    -------
    dict with: escalations_created, shock_modifiers_applied, skipped (reason).
    """
    result = {
        "escalations_created": 0,
        "shock_modifiers_applied": 0,
        "skipped": None,
    }

    try:
        # Step 1: Get cascade analysis for the departed staff
        cascade = _get_cascade_analysis(restaurant_id, departed_staff_id)

        if not cascade:
            result["skipped"] = "no_cascade_data"
            logger.info(
                "No cascade data for %s — likely low-priority (no pre-computed analysis)",
                departed_staff_id,
            )
            return result

        at_risk_staff = cascade.get("at_risk_staff") or []
        severity = cascade.get("cascade_severity", "low")

        if not at_risk_staff:
            result["skipped"] = "no_at_risk_staff"
            return result

        # Step 2: Get names for at-risk staff
        at_risk_ids = [a["staff_id"] for a in at_risk_staff if a.get("staff_id")]
        names = _get_staff_names(restaurant_id, at_risk_ids)

        # Step 3: Create escalations for each at-risk person
        now = datetime.utcnow()
        deadline = (now + timedelta(days=2)).isoformat()  # 48hr window

        for person in at_risk_staff:
            sid = person.get("staff_id")
            if not sid:
                continue

            follow_prob = person.get("follow_probability", 0)

            # Skip very low probability connections
            if follow_prob < 0.01:
                continue

            person_name = names.get(sid, sid)

            # Determine severity from follow probability
            if follow_prob >= 0.3:
                esc_severity = "critical"
                severity_score = 85
            elif follow_prob >= 0.15:
                esc_severity = "high"
                severity_score = 65
            else:
                esc_severity = "moderate"
                severity_score = 45

            trigger_reason = (
                f"{departed_name} recently left. {person_name} has a strong "
                f"working relationship with them and may be affected. "
                f"Have a 1-on-1 check-in — acknowledge the change, "
                f"reinforce their value, ask what support they need."
            )

            # Check for existing active cascade escalation for this person
            existing = _check_existing_escalation(restaurant_id, sid)
            if existing:
                continue

            created = _create_escalation(
                restaurant_id=restaurant_id,
                primary_staff_id=sid,
                event_type="cascade_risk",
                severity=esc_severity,
                severity_score=severity_score,
                trigger_reason=trigger_reason,
                source_type="graph",
                deadline=deadline,
            )

            if created:
                result["escalations_created"] += 1

        # Step 4: Apply shock modifiers
        for person in at_risk_staff:
            sid = person.get("staff_id")
            if not sid:
                continue

            follow_prob = person.get("follow_probability", 0)
            if follow_prob < 0.05:
                continue

            # Shock modifier: 1.0 = no effect, higher = increased exit risk
            # Scale: 10% follow prob → 1.3x, 30% → 1.9x
            modifier = 1.0 + (follow_prob * 3.0)
            modifier = min(modifier, 3.0)  # Cap at 3x

            applied = _apply_shock_modifier(
                restaurant_id=restaurant_id,
                staff_id=sid,
                source_staff_id=departed_staff_id,
                modifier=modifier,
            )

            if applied:
                result["shock_modifiers_applied"] += 1

        logger.info(
            "Cascade trigger for %s (%s): %d escalations, %d shocks",
            departed_name,
            departed_staff_id,
            result["escalations_created"],
            result["shock_modifiers_applied"],
        )

    except Exception as e:
        logger.error(
            "Cascade trigger failed for %s: %s",
            departed_staff_id, e,
            exc_info=True,
        )
        result["skipped"] = f"error: {str(e)}"

    return result


# ------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------

def _get_cascade_analysis(
    restaurant_id: int,
    staff_id: str,
) -> Optional[Dict[str, Any]]:
    """Get the most recent cascade analysis for a staff member."""
    try:
        result = supabase.table("staff_cascade_analysis") \
            .select("cascade_severity, expected_additional_exits, "
                    "worst_case_exits, at_risk_staff, risk_narrative") \
            .eq("restaurant_id", restaurant_id) \
            .eq("target_staff_id", staff_id) \
            .order("analysis_date", desc=True) \
            .limit(1) \
            .execute()

        return result.data[0] if result.data else None
    except Exception as e:
        logger.warning("Failed to fetch cascade analysis: %s", e)
        return None


def _get_staff_names(
    restaurant_id: int,
    staff_ids: List[str],
) -> Dict[str, str]:
    """Get staff_id -> full_name mapping."""
    if not staff_ids:
        return {}

    try:
        result = supabase.table("staff") \
            .select("staff_id, full_name") \
            .eq("restaurant_id", restaurant_id) \
            .in_("staff_id", staff_ids) \
            .execute()

        return {
            row["staff_id"]: row["full_name"]
            for row in (result.data or [])
            if row.get("full_name")
        }
    except Exception:
        return {}


def _check_existing_escalation(
    restaurant_id: int,
    staff_id: str,
) -> bool:
    """Check if there's already an active cascade_risk escalation for this person."""
    try:
        result = supabase.table("sse_escalation_events") \
            .select("id") \
            .eq("restaurant_id", restaurant_id) \
            .eq("primary_staff_id", staff_id) \
            .eq("event_type", "cascade_risk") \
            .in_("status", ["actionable", "monitoring"]) \
            .limit(1) \
            .execute()

        return bool(result.data)
    except Exception:
        return False


def _create_escalation(
    restaurant_id: int,
    primary_staff_id: str,
    event_type: str,
    severity: str,
    severity_score: int,
    trigger_reason: str,
    source_type: str,
    deadline: str,
) -> bool:
    """Insert an escalation into sse_escalation_events."""
    try:
        now = datetime.utcnow().isoformat()

        payload = {
            "restaurant_id": restaurant_id,
            "event_type": event_type,
            "severity": severity,
            "severity_score": severity_score,
            "status": "actionable",
            "current_step": 1,
            "primary_staff_id": primary_staff_id,
            "trigger_reason": trigger_reason,
            "source_type": source_type,
            "triggered_at": now,
            "next_action_deadline": deadline,
            "auto_created": True,
            "created_by": None,
        }

        result = supabase.table("sse_escalation_events") \
            .insert(payload) \
            .execute()

        return bool(result.data)
    except Exception as e:
        logger.error("Failed to create cascade escalation: %s", e)
        return False


def _apply_shock_modifier(
    restaurant_id: int,
    staff_id: str,
    source_staff_id: str,
    modifier: float,
) -> bool:
    """Upsert a shock modifier for an at-risk staff member."""
    try:
        now = datetime.utcnow().isoformat()

        supabase.table("staff_shock_modifiers") \
            .upsert({
                "restaurant_id": restaurant_id,
                "staff_id": staff_id,
                "source_staff_id": source_staff_id,
                "modifier": round(modifier, 3),
                "source_exit_date": now,
                "updated_at": now,
            }) \
            .execute()

        return True
    except Exception as e:
        logger.error("Failed to apply shock modifier: %s", e)
        return False