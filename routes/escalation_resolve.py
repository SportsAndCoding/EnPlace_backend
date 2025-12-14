# Add these imports at the top of routes/escalations.py
from datetime import datetime, timezone

# Add this new endpoint after the existing ones

@router.post("/{escalation_id}/resolve")
async def resolve_escalation(
    escalation_id: str,
    resolution: str = Query(..., description="Resolution type: retained, resigned, terminated, other"),
    notes: str = Query(None, description="Optional notes about the resolution")
):
    """
    Resolve/close an escalation event.
    
    Resolution types:
    - retained: Staff stayed, situation improved (SUCCESS)
    - resigned: Staff left voluntarily (LOSS)
    - terminated: Staff was let go (LOSS)
    - other: Other outcome
    """
    try:
        supabase = get_supabase()
        
        # Validate resolution type
        valid_resolutions = ['retained', 'resigned', 'terminated', 'other']
        if resolution not in valid_resolutions:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid resolution. Must be one of: {valid_resolutions}"
            )
        
        # Get current escalation
        existing = supabase.table("sse_escalation_events") \
            .select("*") \
            .eq("id", escalation_id) \
            .single() \
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Escalation not found")
        
        escalation = existing.data
        
        if escalation["status"] == "resolved":
            raise HTTPException(status_code=400, detail="Escalation is already resolved")
        
        # Update escalation to resolved
        now = datetime.now(timezone.utc).isoformat()
        
        update_result = supabase.table("sse_escalation_events") \
            .update({
                "status": "resolved",
                "resolution": resolution,
                "resolved_at": now,
                "updated_at": now
            }) \
            .eq("id", escalation_id) \
            .execute()
        
        # Add history entry
        history_note = f"Event resolved as '{resolution}'"
        if notes:
            history_note += f": {notes}"
        
        supabase.table("sse_escalation_history").insert({
            "escalation_id": escalation_id,
            "step_number": escalation["current_step"],
            "action_taken": history_note,
            "actor_type": "manager",
            "actor_id": escalation["created_by"],
            "completed_at": now
        }).execute()
        
        # Fetch updated escalation
        updated = supabase.table("sse_escalation_events") \
            .select("*, primary_staff:primary_staff_id(full_name, position)") \
            .eq("id", escalation_id) \
            .single() \
            .execute()
        
        return {
            "success": True,
            "message": f"Escalation resolved as '{resolution}'",
            "escalation": updated.data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resolve escalation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))