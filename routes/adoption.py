"""
Adoption Metrics Route - Owner/GM visibility into platform usage
"""
from fastapi import APIRouter, Depends, HTTPException
from services.adoption_service import get_adoption_metrics
from services.auth_service import verify_jwt_token as get_current_user

router = APIRouter(prefix="/api/adoption", tags=["Adoption Metrics"])


@router.get("")
async def get_adoption(current_user: dict = Depends(get_current_user)):
    """
    Get adoption metrics for the current restaurant.
    Available to all manager-level users.
    """
    try:
        organization_id = current_user.get("organization_id")
        if not organization_id:
            raise HTTPException(status_code=400, detail="No organization_id in token")

        data = get_adoption_metrics(organization_id)
        return data

    except HTTPException:
        raise
    except Exception as e:
        print(f"Adoption metrics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))