"""
Features Discovery API
======================
Exposes one endpoint that tells the frontend which feature concepts apply to
the authenticated user's industry, plus which of those are actually enabled
for their organization.

The frontend uses this on app boot / login to decide which UI sections to
render. It separates two concerns the frontend needs to handle differently:
  - 'available':  feature applies to this industry. UI section may render.
  - 'enabled':    org has access. Render functional version.
                  (If false, render upgrade-prompt version.)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from services.auth_service import verify_jwt_token
from services.feature_gate import (
    FEATURE_COLUMNS,
    INDUSTRY_FEATURES,
    get_features_for_industry,
)
from database.supabase_client import supabase

router = APIRouter(prefix="/api/features", tags=["features"])


@router.get("/available")
async def get_available_features(current_user: dict = Depends(verify_jwt_token)):
    """
    Return the feature catalog for the current user's industry.

    Response shape:
    {
        "industry": "service_care",
        "features": [
            {
                "key": "stable_hire",
                "available": true,
                "enabled": true
            },
            {
                "key": "open_shift",
                "available": false,
                "enabled": false
            },
            ...
        ]
    }

    'available' = applies to this industry (concept fit)
    'enabled'   = paid for / granted to this org (subscription gate)
    """
    organization_id = current_user.get("organization_id")
    if not organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization_id in token"
        )

    # Pull industry + every feature column in a single query
    select_columns = "industry, " + ", ".join(FEATURE_COLUMNS.values())
    result = supabase.table("organizations") \
        .select(select_columns) \
        .eq("id", organization_id) \
        .single() \
        .execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found"
        )

    industry = result.data.get("industry") or "restaurant"
    available_set = get_features_for_industry(industry)

    features = []
    for feature_key, column_name in FEATURE_COLUMNS.items():
        is_available = feature_key in available_set
        is_enabled = bool(result.data.get(column_name, False))

        features.append({
            "key": feature_key,
            "available": is_available,
            # Force enabled=False if not available, even if column says True.
            # Avoids confusing the frontend (an unavailable feature can never
            # be enabled, regardless of stale column state).
            "enabled": is_enabled and is_available,
        })

    return {
        "industry": industry,
        "features": features,
    }


@router.get("/industry-catalog")
async def get_industry_catalog(current_user: dict = Depends(verify_jwt_token)):
    """
    Return the full industry -> feature mapping. Useful for admin UI that
    needs to display what each industry supports (e.g., onboarding flows,
    org creation forms). Doesn't require the user's org to look it up.
    """
    return {
        "industries": {
            industry: sorted(features)
            for industry, features in INDUSTRY_FEATURES.items()
        }
    }
