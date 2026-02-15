"""
routes/social_graph.py

Social graph API endpoints for the manager portal.

Endpoints:
    GET /api/social-graph/snapshot       — Current graph visualization data
    GET /api/social-graph/ranking        — Staff retention priority ranking
    GET /api/social-graph/cascade/{sid}  — What-if cascade analysis for a specific staff member
    GET /api/social-graph/history        — Graph metric trends over time
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from services.social_graph_service import (
    get_graph_snapshot,
    get_retention_ranking,
    get_cascade_analysis,
    get_graph_history,
)
from services.auth_service import verify_jwt_token as get_current_user


router = APIRouter(prefix="/api/social-graph", tags=["Social Graph"])


@router.get("/snapshot")
async def graph_snapshot(current_user: dict = Depends(get_current_user)):
    """
    Get the current social graph visualization data.

    Returns nodes (staff) and edges (relationships) with all
    rendering directives: colors, sizes, positions, icons.
    The frontend renders this directly with zero business logic.
    """
    try:
        restaurant_id = current_user.get("restaurant_id")
        if not restaurant_id:
            raise HTTPException(status_code=400, detail="No restaurant_id in token")

        data = get_graph_snapshot(restaurant_id)
        return data

    except HTTPException:
        raise
    except Exception as e:
        print(f"Graph snapshot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ranking")
async def retention_ranking(current_user: dict = Depends(get_current_user)):
    """
    Get staff retention priority ranking.

    Returns staff sorted by retention score (highest priority first),
    with tier, role, cascade risk, and plain-English explanation.
    This is the main SSE dashboard table.
    """
    try:
        restaurant_id = current_user.get("restaurant_id")
        if not restaurant_id:
            raise HTTPException(status_code=400, detail="No restaurant_id in token")

        data = get_retention_ranking(restaurant_id)
        return data

    except HTTPException:
        raise
    except Exception as e:
        print(f"Retention ranking error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cascade/{staff_id}")
async def cascade_analysis(
    staff_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get what-if cascade analysis for a specific staff member.

    Returns:
    - Cascade severity and expected additional exits
    - Before/after visualization states (for animation)
    - Cost framing with plain-English narrative
    - At-risk staff list with follow probabilities

    This powers the "what happens if Billy leaves?" feature.
    """
    try:
        restaurant_id = current_user.get("restaurant_id")
        if not restaurant_id:
            raise HTTPException(status_code=400, detail="No restaurant_id in token")

        data = get_cascade_analysis(restaurant_id, staff_id)
        if data is None:
            raise HTTPException(
                status_code=404,
                detail=f"No cascade analysis found for staff {staff_id}"
            )
        return data

    except HTTPException:
        raise
    except Exception as e:
        print(f"Cascade analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def graph_history(
    days: int = Query(default=30, ge=7, le=90),
    current_user: dict = Depends(get_current_user),
):
    """
    Get graph metric trends over time.

    Returns daily aggregates: staff count, edge count, density,
    avg mood, tier distribution. Used for trend charts on the
    manager dashboard.
    """
    try:
        restaurant_id = current_user.get("restaurant_id")
        if not restaurant_id:
            raise HTTPException(status_code=400, detail="No restaurant_id in token")

        data = get_graph_history(restaurant_id, days)
        return data

    except HTTPException:
        raise
    except Exception as e:
        print(f"Graph history error: {e}")
        raise HTTPException(status_code=500, detail=str(e))