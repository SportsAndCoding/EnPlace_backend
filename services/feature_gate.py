"""
Feature Gate - Reusable dependency for premium feature enforcement
"""
from fastapi import HTTPException, status, Depends
from services.auth_service import verify_jwt_token
from database.supabase_client import supabase

# Map feature keys to database columns
FEATURE_COLUMNS = {
    "stable_hire": "has_stable_hire",
    "stable_schedule": "has_schedule_optimizer",
    "house_guardian": "has_house_guardian",
    "open_shift": "has_open_shift_marketplace",
    "shift_swap": "has_shift_swap"
}


def require_feature(feature_key: str):
    """
    Dependency factory that checks if restaurant has a premium feature enabled.
    
    Usage:
        @router.post("/candidates")
        async def create_candidate(
            current_user: dict = Depends(require_feature("stable_hire"))
        ):
    """
    async def check_feature(current_user: dict = Depends(verify_jwt_token)) -> dict:
        restaurant_id = current_user.get("restaurant_id")
        if not restaurant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No restaurant_id in token"
            )
        
        column_name = FEATURE_COLUMNS.get(feature_key)
        if not column_name:
            # Unknown feature - allow (fail open for safety)
            return current_user
        
        # Check feature flag
        result = supabase.table("restaurants") \
            .select(column_name, "subscription_status") \
            .eq("id", restaurant_id) \
            .single() \
            .execute()
        
        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Restaurant not found"
            )
        
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