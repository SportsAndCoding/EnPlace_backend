"""
Industry-aware seeding helpers for new organization onboarding.

When a new organization is created, the rewards system needs default
earning rules and catalog items, calibrated to the org's industry. This
module provides a single entry point - seed_default_rewards(org_id, industry) -
that dispatches to the correct industry-specific seeder.

The actual seeding logic lives in PostgreSQL functions (created via the
004_seed_service_care_rewards_template.sql migration) for performance and
transactional safety. This Python module is just a thin wrapper around the
RPC calls, plus the industry dispatch table.

Currently supported industries:
  - 'restaurant'   : uses existing seed (already in DB for organization_id=1)
  - 'service_care' : calls seed_service_care_rewards(org_id) RPC

Adding a new industry: write a SQL seed function following the same pattern,
add an entry to SEED_FUNCTIONS below.
"""
import logging
from typing import Dict, Optional, Any
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# INDUSTRY -> SEED FUNCTION MAPPING
# ════════════════════════════════════════════════════════════════════════════
# Each entry maps an industry value to the name of a PostgreSQL function
# that takes (p_org_id INTEGER) and seeds default rewards for that org.
# None means "no seed function exists yet for this industry."

SEED_FUNCTIONS: Dict[str, Optional[str]] = {
    "service_care": "seed_service_care_rewards",
    # 'restaurant' intentionally omitted - the existing organization_id=1
    # was seeded manually long ago. Future restaurant orgs would need a
    # similar seed_restaurant_rewards SQL function written, then mapped here.
    "restaurant": None,
}


def seed_default_rewards(organization_id: int, industry: str) -> Dict[str, Any]:
    """Seed the default reward catalog and earning rules for a new org.

    Looks up the appropriate SQL seed function based on the org's industry
    and invokes it. Safe to call multiple times - the seed functions are
    idempotent via ON CONFLICT clauses.

    Args:
        organization_id: The integer ID of the org to seed.
        industry: The industry value (e.g. 'service_care', 'restaurant').

    Returns:
        A dict with keys:
          - success (bool): whether the seed ran
          - industry (str): the industry that was processed
          - result (dict | None): the RPC's return value, if it ran
          - reason (str | None): explanation if it didn't run

    Does not raise on industries without a seed function - it returns
    success=False with a reason. This lets the caller decide whether
    that's an error or just a no-op.
    """
    seed_function = SEED_FUNCTIONS.get(industry)

    if seed_function is None:
        logger.info(
            "No reward seed function defined for industry '%s' (org_id=%s). "
            "Skipping default seed.", industry, organization_id
        )
        return {
            "success": False,
            "industry": industry,
            "result": None,
            "reason": f"No seed function defined for industry '{industry}'."
        }

    try:
        supabase = get_supabase()
        result = supabase.rpc(seed_function, {"p_org_id": organization_id}).execute()

        logger.info(
            "Seeded default rewards for org_id=%s industry='%s': %s",
            organization_id, industry, result.data
        )

        return {
            "success": True,
            "industry": industry,
            "result": result.data,
            "reason": None
        }

    except Exception as e:
        logger.error(
            "Failed to seed default rewards for org_id=%s industry='%s': %s",
            organization_id, industry, e
        )
        return {
            "success": False,
            "industry": industry,
            "result": None,
            "reason": f"RPC call failed: {str(e)}"
        }


def get_supported_industries() -> Dict[str, bool]:
    """Return a map of industry -> whether a seed function exists.

    Useful for admin UI that wants to show which industries have full
    onboarding support vs. which require manual catalog setup.
    """
    return {
        industry: (seed_fn is not None)
        for industry, seed_fn in SEED_FUNCTIONS.items()
    }
