"""
WEBSITE PROSPECTING ROUTES
Async prospecting: rep submits a search, worker processes it, rep retrieves results.
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from services.auth_service import verify_jwt_token
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prospecting", tags=["prospecting"])


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ProspectSearchRequest(BaseModel):
    zip_code: str
    radius_miles: Optional[int] = 10
    cuisine_filter: Optional[str] = None
    max_results: Optional[int] = 10


# ═══════════════════════════════════════════════════════════════════════════════
# SUBMIT SEARCH (returns immediately)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/search")
async def submit_search(req: ProspectSearchRequest, user=Depends(verify_jwt_token)):
    """
    Queue a prospecting search. Returns immediately.
    Worker picks it up and processes within 30 minutes.
    """
    if not req.zip_code or len(req.zip_code) != 5:
        raise HTTPException(status_code=400, detail="Valid 5-digit zip code required")

    try:
        supabase = get_supabase()
        result = supabase.table("prospect_searches").insert({
            "staff_id": user.get("staff_id", "unknown"),
            "zip_code": req.zip_code,
            "radius_miles": req.radius_miles,
            "cuisine_filter": req.cuisine_filter,
            "max_results": req.max_results,
            "status": "pending"
        }).execute()

        record = result.data[0] if result.data else None

        return {
            "success": True,
            "search_id": record["id"] if record else None,
            "message": "Search queued. Results will be ready within 30 minutes."
        }

    except Exception as e:
        logger.error(f"Failed to queue search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# GET MY SEARCHES
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/my-searches")
async def get_my_searches(user=Depends(verify_jwt_token)):
    """
    Get all searches for the current rep, newest first.
    """
    try:
        supabase = get_supabase()
        result = supabase.table("prospect_searches") \
            .select("id, zip_code, radius_miles, cuisine_filter, max_results, status, created_at, completed_at") \
            .eq("staff_id", user.get("staff_id", "unknown")) \
            .order("created_at", desc=True) \
            .limit(20) \
            .execute()

        return {"success": True, "searches": result.data or []}

    except Exception as e:
        logger.error(f"Failed to get searches: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# GET SEARCH RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/results/{search_id}")
async def get_search_results(search_id: str, user=Depends(verify_jwt_token)):
    """
    Get results for a specific search.
    """
    try:
        supabase = get_supabase()
        result = supabase.table("prospect_searches") \
            .select("*") \
            .eq("id", search_id) \
            .eq("staff_id", user.get("staff_id", "unknown")) \
            .single() \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Search not found")

        return {"success": True, "search": result.data}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get results: {e}")
        raise HTTPException(status_code=500, detail=str(e))