from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional
from services.auth_service import verify_jwt_token as get_current_user
from services.escalations_service import EscalationsService
from models.escalations import (
    EscalationCreate, 
    EscalationUpdate, 
    HistoryEntryCreate,
    EscalationResponse, 
    EscalationCreateResponse
)
import os
import logging

logger = logging.getLogger(__name__)
from services.escalation_monitor_service import EscalationMonitorService
from datetime import datetime, timezone


router = APIRouter(prefix="/api/escalations", tags=["escalations"])

@router.post("", response_model=EscalationCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_escalation(
    escalation: EscalationCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new escalation event.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can create escalations"
        )
    
    # Verify restaurant access
    if current_user['restaurant_id'] != escalation.restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    service = EscalationsService()
    
    try:
        result = await service.create_escalation(
            escalation_data=escalation.dict(),
            created_by=current_user['staff_id'],
            auto_created=False
        )
        
        return EscalationCreateResponse(
            success=True,
            escalation_id=str(result['id']),
            message="Escalation created"
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create escalation: {str(e)}"
        )


@router.get("")
async def get_escalations(
    restaurant_id: int,
    status: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user)
):
    """
    Get escalations for a restaurant.
    
    Optional filters:
    - status: 'active', 'escalated', 'monitoring', 'resolved', 'active_all' (both active + escalated)
    - event_type: 'burnout', 'fairness', 'retention', 'alignment'
    - severity: 'mild', 'moderate', 'serious', 'critical'
    """
    # Verify restaurant access
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    service = EscalationsService()
    
    try:
        escalations = await service.get_escalations_by_restaurant(
            restaurant_id=restaurant_id,
            status=status,
            event_type=event_type,
            severity=severity
        )
        
        # Get counts by status
        active_count = len([e for e in escalations if e["status"] in ["active", "escalated"]])
        
        return {
            "success": True,
            "escalations": escalations,
            "count": len(escalations),
            "active_count": active_count
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch escalations: {str(e)}"
        )


@router.get("/{escalation_id}")
async def get_escalation(
    escalation_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a single escalation with full history"""
    service = EscalationsService()
    
    try:
        escalation = await service.get_escalation_with_history(
            escalation_id=escalation_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not escalation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Escalation not found"
            )
        
        return {
            "success": True,
            "escalation": escalation
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch escalation: {str(e)}"
        )


@router.put("/{escalation_id}")
async def update_escalation(
    escalation_id: str,
    escalation: EscalationUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update an escalation event.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can update escalations"
        )
    
    service = EscalationsService()
    
    try:
        # Verify escalation exists
        existing = await service.get_escalation_by_id(
            escalation_id=escalation_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Escalation not found"
            )
        
        result = await service.update_escalation(
            escalation_id=escalation_id,
            restaurant_id=current_user['restaurant_id'],
            update_data=escalation.dict()
        )
        
        return {
            "success": True,
            "escalation": result,
            "message": "Escalation updated"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update escalation: {str(e)}"
        )


@router.post("/{escalation_id}/history")
async def add_history_entry(
    escalation_id: str,
    entry: HistoryEntryCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Add a history entry to an escalation.
    Managers only.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can add history entries"
        )
    
    service = EscalationsService()
    
    try:
        # Verify escalation exists
        existing = await service.get_escalation_by_id(
            escalation_id=escalation_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Escalation not found"
            )
        
        result = await service.add_history_entry(
            event_id=escalation_id,
            step_number=entry.step_number,
            action_taken=entry.action_taken,
            actor_staff_id=current_user['staff_id'],
            actor_name=entry.actor_name
        )
        
        return {
            "success": True,
            "history_entry": result,
            "message": "History entry added"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add history entry: {str(e)}"
        )


@router.post("/{escalation_id}/advance")
async def advance_escalation_step(
    escalation_id: str,
    action_taken: str = Query(..., description="Description of action taken at this step"),
    current_user: dict = Depends(get_current_user)
):
    """
    Advance escalation to the next step.
    Managers only.
    
    Automatically:
    - Increments current_step
    - Changes status to 'escalated' at step 5+
    - Creates history entry
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can advance escalations"
        )
    
    service = EscalationsService()
    
    try:
        result = await service.advance_step(
            escalation_id=escalation_id,
            restaurant_id=current_user['restaurant_id'],
            action_taken=action_taken,
            actor_staff_id=current_user['staff_id']
        )
        
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Escalation not found"
            )
        
        return {
            "success": True,
            "escalation": result,
            "message": f"Advanced to step {result['current_step']}"
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to advance escalation: {str(e)}"
        )
    


@router.post("/monitoring/run")
async def run_monitoring_job(
    api_key: str = Query(None, description="API key for scheduled job authentication")
):
    """
    Run the nightly escalation monitoring job.
    Can be triggered by:
    - Heroku Scheduler
    - Manual API call
    - Cron job
    """
    # Simple API key check (in production, use proper auth)
    expected_key = os.environ.get("MONITORING_JOB_KEY", "enplace-monitor-2025")
    if api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    try:
        monitor = EscalationMonitorService()
        results = await monitor.run_nightly_monitoring()
        return {
            "success": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": results
        }
    except Exception as e:
        logger.error(f"Monitoring job failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@router.get("/{escalation_id}/history")
async def get_escalation_history(escalation_id: str):
    """Get all history entries for an escalation"""
    try:
        supabase = get_supabase()
        
        result = supabase.table("sse_escalation_history") \
            .select("*") \
            .eq("escalation_id", escalation_id) \
            .order("completed_at", desc=False) \
            .execute()
        
        return {
            "success": True,
            "event_id": escalation_id,
            "history": result.data or []
        }
    except Exception as e:
        logger.error(f"Get escalation history error: {e}")
        raise HTTPException(status_code=500, detail=str(e))