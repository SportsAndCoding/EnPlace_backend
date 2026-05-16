"""
Feature Gate - Reusable dependency for premium feature enforcement.

Two-stage gating:
  Stage 1: Industry compatibility - is this feature even applicable to
           the org's industry? (404 if not - hide UI entirely)
  Stage 2: Org subscription - is this feature enabled for this org?
           (403 if not - show upgrade modal)

Adding a feature for a new industry: extend INDUSTRY_FEATURES below.
Adding a new feature entirely: extend FEATURE_COLUMNS and INDUSTRY_FEATURES.
"""
from fastapi import HTTPException, status, Depends
from services.auth_service import verify_jwt_token
from database.supabase_client import supabase

# ════════════════════════════════════════════════════════════════════════════
# FEATURE -> COLUMN MAPPING
# ════════════════════════════════════════════════════════════════════════════
# Every feature key the application uses must map to a boolean column on
# the organizations table. The column controls whether the org has paid
# for / been granted access to the feature.

FEATURE_COLUMNS = {
    "stable_hire": "has_stable_hire",
    "stable_schedule": "has_schedule_optimizer",
    "house_guardian": "has_house_guardian",
    "open_shift": "has_open_shift_marketplace",
    "shift_swap": "has_shift_swap",
}


# ════════════════════════════════════════════════════════════════════════════
# INDUSTRY -> AVAILABLE FEATURE SET
# ════════════════════════════════════════════════════════════════════════════
# Which feature concepts apply to which industry. An org with industry='X'
# can only use features in INDUSTRY_FEATURES['X'], regardless of has_*
# column values. This is the "concept fit" gate, not the "paid for" gate.
#
# Restaurant gets the full set (existing behavior preserved verbatim).
# Service_care excludes:
#   - open_shift: marketplace shift-fill is a restaurant concept; service
#                 care assigns shifts based on certifications/training match
#                 rather than self-service pickup.
#   - stable_schedule: schedule optimizer is built around restaurant shift
#                      patterns (FOH/BOH, prep/service/cleanup). Service
#                      care has compliance-driven scheduling (HCBS ratios,
#                      certification requirements) that needs separate
#                      constraint logic. Re-enable when the schedule parser
#                      gets the service_care content pack.

INDUSTRY_FEATURES = {
    "restaurant": {
        "stable_hire",
        "stable_schedule",
        "house_guardian",
        "open_shift",
        "shift_swap",
    },
    "service_care": {
        "stable_hire",
        "house_guardian",
        "shift_swap",
    },
}

# Default for any industry not explicitly listed: same set as restaurant
# (the legacy default). Better to fail open than silently disable features
# for a brand-new industry someone added.
DEFAULT_INDUSTRY_FEATURES = INDUSTRY_FEATURES["restaurant"]


# ════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS (importable, no FastAPI dependency)
# ════════════════════════════════════════════════════════════════════════════

def get_features_for_industry(industry: str) -> set:
    """Return the set of feature_keys applicable to the given industry.

    Falls back to DEFAULT_INDUSTRY_FEATURES for unknown industries so a new
    vertical doesn't break working behavior before it's explicitly mapped.
    """
    return INDUSTRY_FEATURES.get(industry, DEFAULT_INDUSTRY_FEATURES)


def is_feature_available_for_industry(feature_key: str, industry: str) -> bool:
    """True if the feature concept applies to this industry at all.

    Independent of whether the org has paid for it. Use this when deciding
    whether to render UI for the feature.
    """
    return feature_key in get_features_for_industry(industry)


def get_org_industry(organization_id: int) -> str:
    """Look up an org's industry. Defaults to 'restaurant' if missing."""
    result = supabase.table("organizations") \
        .select("industry") \
        .eq("id", organization_id) \
        .single() \
        .execute()
    return (result.data or {}).get("industry") or "restaurant"


# ════════════════════════════════════════════════════════════════════════════
# DEPENDENCY FACTORY
# ════════════════════════════════════════════════════════════════════════════

def require_feature(feature_key: str):
    """
    Dependency factory that gates an endpoint behind a feature.

    Usage:
        @router.post("/candidates")
        async def create_candidate(
            current_user: dict = Depends(require_feature("stable_hire"))
        ):
            ...

    Raises:
        404: feature is not applicable to the org's industry (hide UI)
        403: feature is applicable but not enabled for this org (upgrade)
        400: token is missing organization_id
        404: organization does not exist
    """
    async def check_feature(current_user: dict = Depends(verify_jwt_token)) -> dict:
        organization_id = current_user.get("organization_id")
        if not organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No organization_id in token"
            )

        column_name = FEATURE_COLUMNS.get(feature_key)
        if not column_name:
            # Unknown feature - allow (fail open for safety)
            return current_user

        # Single round trip: pull industry + feature flag + subscription status
        result = supabase.table("organizations") \
            .select(f"{column_name}, subscription_status, industry") \
            .eq("id", organization_id) \
            .single() \
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found"
            )

        industry = result.data.get("industry") or "restaurant"

        # Stage 1: industry compatibility check
        if not is_feature_available_for_industry(feature_key, industry):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "feature_not_available_for_industry",
                    "feature": feature_key,
                    "industry": industry,
                    "message": f"This feature is not available for {industry} organizations."
                }
            )

        # Stage 2: subscription / enablement check
        has_feature = result.data.get(column_name, False)
        subscription_status = result.data.get("subscription_status", "none")

        if not has_feature:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "upgrade_required",
                    "feature": feature_key,
                    "message": f"This feature requires an upgrade. Add {feature_key.replace('_', ' ').title()} to your subscription.",
                    "subscription_status": subscription_status
                }
            )

        return current_user

    return check_feature
