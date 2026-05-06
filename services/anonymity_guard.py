"""
ANONYMITY GUARD
===============
Prevents de-anonymization of staff mood data when position groups
are too small to protect identity.

If an organization has 2 bartenders and we surface "your bartenders feel
unsafe," we've named someone. The guard enforces a minimum group size
(industry standard: 5) and rolls smaller groups up through a cascade:
position -> category (FOH/BOH/Direct Care/Clinical/etc) -> all staff.

Industry-agnostic: the role taxonomy is loaded per-organization from the
industry_role_taxonomy table, with optional per-org overrides in
organization_role_overrides. Restaurants get FOH/BOH/Management; service
care gets Direct Care/Clinical/Administrative/Management; etc.

Used by:
  - escalation_monitor_service.py  (auto-created escalations)
  - escalations_service.py         (manual escalation creation)
  - dashboard_service.py           (mood data display)
  - Any future code that surfaces mood by position
"""

from typing import Optional, Dict, List
import logging
import time

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────
ANONYMITY_THRESHOLD = 5

# Cache TTL for role maps. Taxonomies change rarely; 1 hour is conservative.
ROLE_MAP_CACHE_TTL_SECONDS = 3600

# In-process cache, keyed by organization_id. Each entry is (loaded_at, role_map).
_role_map_cache: Dict[int, tuple] = {}


# ─────────────────────────────────────────────────────────
# ROLE MAP LOADING
# ─────────────────────────────────────────────────────────

def _load_role_map(supabase, organization_id: int) -> Dict[str, dict]:
    """
    Load the effective role map for an organization, combining the industry
    default taxonomy with any per-organization overrides.

    Returns a dict keyed by position name with values:
        { "category": str, "is_non_operational": bool }

    Result is cached per organization for ROLE_MAP_CACHE_TTL_SECONDS.
    Call invalidate_role_map_cache(organization_id) after taxonomy changes.
    """
    now = time.time()
    cached = _role_map_cache.get(organization_id)
    if cached and (now - cached[0]) < ROLE_MAP_CACHE_TTL_SECONDS:
        return cached[1]

    # Determine the organization's industry
    try:
        org_result = supabase.table("organizations") \
            .select("industry") \
            .eq("id", organization_id) \
            .single() \
            .execute()
    except Exception as e:
        logger.error(
            f"Anonymity guard: failed to look up industry for org {organization_id}: {e}"
        )
        return {}

    if not org_result.data:
        logger.error(f"Anonymity guard: organization {organization_id} not found")
        return {}

    industry = org_result.data.get("industry", "restaurant")

    # Load the industry default taxonomy
    try:
        industry_result = supabase.table("industry_role_taxonomy") \
            .select("position_name, category_name, is_non_operational") \
            .eq("industry", industry) \
            .execute()
    except Exception as e:
        logger.error(
            f"Anonymity guard: failed to load taxonomy for industry '{industry}': {e}"
        )
        return {}

    role_map: Dict[str, dict] = {}
    for row in (industry_result.data or []):
        role_map[row["position_name"]] = {
            "category": row["category_name"],
            "is_non_operational": row["is_non_operational"]
        }

    # Apply per-organization overrides (these win over industry defaults)
    try:
        override_result = supabase.table("organization_role_overrides") \
            .select("position_name, category_name, is_non_operational") \
            .eq("organization_id", organization_id) \
            .execute()
    except Exception as e:
        logger.warning(
            f"Anonymity guard: failed to load overrides for org {organization_id}: {e}"
        )
        override_result = type("obj", (), {"data": []})()

    for row in (override_result.data or []):
        existing = role_map.get(row["position_name"], {})
        role_map[row["position_name"]] = {
            "category": (
                row["category_name"]
                if row["category_name"] is not None
                else existing.get("category")
            ),
            "is_non_operational": (
                row["is_non_operational"]
                if row["is_non_operational"] is not None
                else existing.get("is_non_operational", False)
            )
        }

    _role_map_cache[organization_id] = (now, role_map)
    return role_map


def invalidate_role_map_cache(organization_id: Optional[int] = None) -> None:
    """
    Invalidate cached role maps. Call after an organization's taxonomy or
    overrides change.

    Pass an organization_id to invalidate that org only, or None to clear
    the entire cache (useful when industry_role_taxonomy itself changes).
    """
    if organization_id is None:
        _role_map_cache.clear()
    else:
        _role_map_cache.pop(organization_id, None)


# ─────────────────────────────────────────────────────────
# CATEGORY RESOLUTION
# ─────────────────────────────────────────────────────────

def get_role_category(supabase, organization_id: int, position: str) -> str:
    """
    Map a position to its anonymity category for the given organization.

    Returns the category name (e.g., "FOH", "Direct Care", "Management"),
    or "All Staff" if the position is unknown or non-operational.
    """
    if not position:
        return "All Staff"

    role_map = _load_role_map(supabase, organization_id)
    role = role_map.get(position)

    if role is None:
        logger.warning(
            f"Anonymity guard: unmapped position '{position}' for org "
            f"{organization_id} (falling back to All Staff)"
        )
        return "All Staff"

    if role.get("is_non_operational"):
        return "All Staff"

    return role.get("category") or "All Staff"


def get_positions_in_category(
    supabase,
    organization_id: int,
    category: str
) -> List[str]:
    """
    Return all positions belonging to a category for this organization.

    Used by the escalation monitor when computing aggregate mood for a
    rolled-up category, and by anyone querying staff by category.
    """
    role_map = _load_role_map(supabase, organization_id)
    return [
        position for position, role in role_map.items()
        if role.get("category") == category
        and not role.get("is_non_operational", False)
    ]


# ─────────────────────────────────────────────────────────
# POSITION COUNTS
# ─────────────────────────────────────────────────────────

def get_position_counts(supabase, organization_id: int) -> Dict[str, int]:
    """
    Get count of active staff per position for an organization.

    Returns: { "Server": 23, "Bartender": 8, "Sous Chef": 2, ... }
    """
    try:
        result = supabase.table("staff") \
            .select("position") \
            .eq("organization_id", organization_id) \
            .eq("status", "active") \
            .execute()

        counts: Dict[str, int] = {}
        for row in (result.data or []):
            pos = row.get("position", "Unknown")
            counts[pos] = counts.get(pos, 0) + 1
        return counts
    except Exception as e:
        logger.error(
            f"Anonymity guard: failed to get position counts for org "
            f"{organization_id}: {e}"
        )
        return {}


def get_category_count(
    supabase,
    organization_id: int,
    position_counts: Dict[str, int],
    category: str
) -> int:
    """
    Sum staff in a category from the given position counts.
    """
    category_positions = get_positions_in_category(
        supabase, organization_id, category
    )
    return sum(position_counts.get(p, 0) for p in category_positions)


# ─────────────────────────────────────────────────────────
# ANONYMITY CASCADE
# ─────────────────────────────────────────────────────────

def check_anonymity(
    supabase,
    organization_id: int,
    position_counts: Dict[str, int],
    position: str
) -> dict:
    """
    Determine whether a position group is large enough to surface
    position-level mood data, or whether it needs to be rolled up.

    Cascade:
      1. Position group >= threshold -> use position directly
      2. Category group >= threshold -> roll up to category
      3. Fall back to "All Staff"

    Returns:
        {
            "safe": True,
            "anonymity_applied": bool,     # True if rolled up
            "original_role": str,           # what was requested
            "display_role": str,            # what to show managers
            "group_count": int,             # size of the group reported on
            "rollup_level": str             # "position" | "category" | "all_staff"
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

    # Level 1: position group
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

    # Level 2: category group
    category = get_role_category(supabase, organization_id, position)
    if category != "All Staff":
        category_count = get_category_count(
            supabase, organization_id, position_counts, category
        )
        if category_count >= ANONYMITY_THRESHOLD:
            logger.info(
                f"Anonymity guard: '{position}' ({position_count} staff) "
                f"rolled up to '{category}' ({category_count} staff) "
                f"for org {organization_id}"
            )
            return {
                "safe": True,
                "anonymity_applied": True,
                "original_role": position,
                "display_role": category,
                "group_count": category_count,
                "rollup_level": "category"
            }

    # Level 3: All Staff fallback
    total = sum(position_counts.values())
    logger.info(
        f"Anonymity guard: '{position}' below thresholds for org "
        f"{organization_id}, using All Staff ({total} staff)"
    )
    return {
        "safe": True,
        "anonymity_applied": True,
        "original_role": position,
        "display_role": "All Staff",
        "group_count": total,
        "rollup_level": "all_staff"
    }


# ─────────────────────────────────────────────────────────
# ESCALATION HELPERS
# ─────────────────────────────────────────────────────────

def get_positions_for_display_role(
    supabase,
    organization_id: int,
    display_role: str
) -> List[str]:
    """
    Given a display_role (a category, a specific position, or "All Staff"),
    return the positions to filter on when querying.

    Returns:
      - Specific position: [position]
      - Category: list of positions in that category
      - "All Staff": empty list (meaning: don't filter by position)
    """
    if display_role == "All Staff":
        return []

    # Check if display_role is a category for this organization
    role_map = _load_role_map(supabase, organization_id)
    categories = {
        role["category"] for role in role_map.values()
        if role.get("category") and not role.get("is_non_operational", False)
    }

    if display_role in categories:
        return get_positions_in_category(supabase, organization_id, display_role)

    # Otherwise treat as a specific position name
    return [display_role]


def validate_escalation_role(
    supabase,
    organization_id: int,
    affected_role: str
) -> dict:
    """
    Convenience function for escalation creation. Gets position counts,
    runs the anonymity cascade, and returns everything needed to create
    a safe escalation record.
    """
    position_counts = get_position_counts(supabase, organization_id)
    return check_anonymity(
        supabase, organization_id, position_counts, affected_role
    )
