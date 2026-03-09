"""
EN PLACE - ANONYMITY GUARD
==========================
Prevents de-anonymization of staff mood data when position groups
are too small. If a restaurant has 2 bartenders and we say "your
bartenders feel unsafe," we've just named someone.

This module enforces a hard floor: position groups below the threshold
get rolled up into broader categories (FOH/BOH/Management) or All Staff.

Used by:
  - escalation_monitor_service.py  (auto-created escalations)
  - escalations_service.py         (manual escalation creation)
  - Any future endpoint that surfaces mood by position

Industry standard for anonymous surveys: minimum 5 respondents.
En Place enforces the same.
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# THRESHOLD
# ─────────────────────────────────────────────────────────
ANONYMITY_THRESHOLD = 5


# ─────────────────────────────────────────────────────────
# POSITION → CATEGORY MAPPING
# Built from actual En Place staff.position values (verified 2026-03-09)
# ─────────────────────────────────────────────────────────
ROLE_CATEGORIES = {
    "FOH": [
        "Server",
        "Host",
        "Bartender",
        "Barback",
        "Busser",
        "Food Runner",
        "Expo",
        "Cashier",       # Not yet in system but anticipated
    ],
    "BOH": [
        "Line Cook",
        "Prep Cook",
        "Sous Chef",
        "Executive Chef",
        "Chef",
        "Dishwasher",
    ],
    "Management": [
        "General Manager",
        "Assistant Manager",
        "Kitchen Manager",
        "Manager",
        "Shift Lead",    # Not yet in system but anticipated
    ],
}

# Non-operational positions that should never appear in mood-based
# escalations but are handled gracefully if they do
NON_OPERATIONAL = [
    "Owner",
    "Founder/CEO",
    "Head of Sales",
    "Sales Rep",
    "Sales Representative",
    "Recruiter",
]


def get_role_category(position: str) -> str:
    """
    Map a specific position to its broader anonymity category.

    Returns: "FOH", "BOH", "Management", or "All Staff" (fallback)
    """
    if not position:
        return "All Staff"

    for category, positions in ROLE_CATEGORIES.items():
        if position in positions:
            return category

    # Non-operational positions fall to All Staff
    if position in NON_OPERATIONAL:
        return "All Staff"

    # Unknown position — log it so we can add it to the mapping
    logger.warning(f"Anonymity guard: unmapped position '{position}' — falling back to All Staff")
    return "All Staff"


def get_position_counts(supabase, restaurant_id: int) -> dict:
    """
    Get count of active staff per position for a specific restaurant.

    Returns: { "Server": 23, "Bartender": 8, "Sous Chef": 2, ... }
    """
    try:
        result = supabase.table("staff") \
            .select("position") \
            .eq("restaurant_id", restaurant_id) \
            .eq("status", "active") \
            .execute()

        counts = {}
        for row in (result.data or []):
            pos = row.get("position", "Unknown")
            counts[pos] = counts.get(pos, 0) + 1
        return counts

    except Exception as e:
        logger.error(f"Anonymity guard: failed to get position counts for restaurant {restaurant_id}: {e}")
        return {}


def get_category_count(position_counts: dict, category: str) -> int:
    """
    Sum up all staff in a category (FOH/BOH/Management) from position counts.
    """
    category_positions = ROLE_CATEGORIES.get(category, [])
    return sum(position_counts.get(p, 0) for p in category_positions)


def check_anonymity(position_counts: dict, position: str) -> dict:
    """
    Determine whether a position group is large enough to surface
    position-level mood data, or whether it needs to be rolled up.

    Cascade logic:
      1. Position group >= threshold? → Use position directly
      2. Category group (FOH/BOH/Mgmt) >= threshold? → Roll up to category
      3. Fall back to "All Staff"

    Returns:
    {
        "safe": True,                  # Always True (we always find a safe level)
        "anonymity_applied": bool,     # True if we had to roll up
        "original_role": str,          # The position that was requested
        "display_role": str,           # What should be shown to managers
        "group_count": int,            # Size of the group we're reporting on
        "rollup_level": str            # "position" | "category" | "all_staff"
    }
    """
    if not position:
        total = sum(position_counts.values())
        return {
            "safe": True,
            "anonymity_applied": False,
            "original_role": None,
            "display_role": "All Staff",
            "group_count": total,
            "rollup_level": "all_staff"
        }

    # Level 1: Check the specific position
    position_count = position_counts.get(position, 0)
    if position_count >= ANONYMITY_THRESHOLD:
        return {
            "safe": True,
            "anonymity_applied": False,
            "original_role": position,
            "display_role": position,
            "group_count": position_count,
            "rollup_level": "position"
        }

    # Level 2: Roll up to category (FOH/BOH/Management)
    category = get_role_category(position)
    if category != "All Staff":
        category_count = get_category_count(position_counts, category)
        if category_count >= ANONYMITY_THRESHOLD:
            logger.info(
                f"Anonymity guard: '{position}' ({position_count} staff) "
                f"rolled up to '{category}' ({category_count} staff) "
                f"for restaurant data protection"
            )
            return {
                "safe": True,
                "anonymity_applied": True,
                "original_role": position,
                "display_role": category,
                "group_count": category_count,
                "rollup_level": "category"
            }

    # Level 3: Fall back to All Staff
    total = sum(position_counts.values())
    logger.info(
        f"Anonymity guard: '{position}' ({position_count} staff) and "
        f"'{category}' ({get_category_count(position_counts, category) if category != 'All Staff' else 0} staff) "
        f"both below threshold — using All Staff ({total} staff)"
    )
    return {
        "safe": True,
        "anonymity_applied": True,
        "original_role": position,
        "display_role": "All Staff",
        "group_count": total,
        "rollup_level": "all_staff"
    }


def get_positions_for_display_role(display_role: str) -> list:
    """
    Given a display_role (which might be a category like "FOH"),
    return the list of positions that should be included in queries.

    Used by the escalation monitor to know which positions to include
    when computing mood for a rolled-up category.

    Returns:
      - For a specific position: [position]
      - For a category: list of positions in that category
      - For "All Staff": empty list (meaning: don't filter by position)
    """
    if display_role in ROLE_CATEGORIES:
        return ROLE_CATEGORIES[display_role]
    elif display_role == "All Staff":
        return []  # No filter = all positions
    else:
        return [display_role]  # Specific position


def validate_escalation_role(supabase, restaurant_id: int, affected_role: str) -> dict:
    """
    Convenience function for escalation creation (auto or manual).
    Gets position counts, runs anonymity check, returns everything
    needed to create a safe escalation record.

    Returns the check_anonymity result dict.
    """
    position_counts = get_position_counts(supabase, restaurant_id)
    return check_anonymity(position_counts, affected_role)