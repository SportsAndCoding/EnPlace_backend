import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

class EscalationsService:
    def __init__(self):
        self.supabase = get_supabase()
    
    async def create_escalation(
        self, 
        escalation_data: Dict[str, Any], 
        created_by: str,
        auto_created: bool = False
    ) -> Dict[str, Any]:
        """Create a new escalation event"""
        try:
            payload = {
                "restaurant_id": escalation_data["restaurant_id"],
                "event_type": escalation_data["event_type"],
                "severity": escalation_data.get("severity", "moderate"),
                "severity_score": escalation_data.get("severity_score"),
                "status": "actionable",
                "current_step": 1,
                "primary_staff_id": escalation_data.get("primary_staff_id"),
                "affected_role": escalation_data.get("affected_role"),
                "trigger_reason": escalation_data["trigger_reason"],
                "source_type": escalation_data.get("source_type", "mood"),
                "triggered_at": datetime.utcnow().isoformat(),
                "next_action_deadline": escalation_data.get("next_action_deadline"),
                "created_by": created_by,
                "auto_created": auto_created
            }
            
            result = self.supabase.table("sse_escalation_events").insert(payload).execute()
            
            if result.data and len(result.data) > 0:
                event = result.data[0]
                
                # Auto-create Step 1 history entry
                await self.add_history_entry(
                    event_id=event["id"],
                    step_number=1,
                    action_taken="Event detected and created",
                    actor_staff_id=created_by if not auto_created else None,
                    actor_name="System" if auto_created else None
                )
                
                return event
            else:
                raise Exception("Insert returned no data")
                
        except Exception as e:
            logger.error(f"Create escalation error: {e}")
            raise e
    
    async def get_escalation_by_id(
        self, 
        escalation_id: str, 
        restaurant_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific escalation with staff details"""
        try:
            result = self.supabase.table("sse_escalation_events") \
                .select("*, primary_staff:primary_staff_id(full_name, position, email)") \
                .eq("id", escalation_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Get escalation error: {e}")
            raise e
    
    async def get_escalation_with_history(
        self, 
        escalation_id: str, 
        restaurant_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get escalation with full history"""
        try:
            # Get the event
            event = await self.get_escalation_by_id(escalation_id, restaurant_id)
            if not event:
                return None
            
            # Get history
            history_result = self.supabase.table("sse_escalation_history") \
                .select("*") \
                .eq("event_id", escalation_id) \
                .order("completed_at") \
                .execute()
            
            event["history"] = history_result.data or []
            
            return event
            
        except Exception as e:
            logger.error(f"Get escalation with history error: {e}")
            raise e
    
    async def get_escalations_by_restaurant(
        self,
        restaurant_id: int,
        status: Optional[str] = None,
        event_type: Optional[str] = None,
        severity: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get escalations for a restaurant with optional filters"""
        try:
            query = self.supabase.table("sse_escalation_events") \
                .select("*, primary_staff:primary_staff_id(full_name, position)") \
                .eq("restaurant_id", restaurant_id)
            
            if status:
                if status == "active_all":
                    # Get all actionable
                    query = query.eq("status", "actionable")
                else:
                    query = query.eq("status", status)
            
            if event_type:
                query = query.eq("event_type", event_type)
            
            if severity:
                query = query.eq("severity", severity)
            
            result = query.order("triggered_at", desc=True).execute()
            
            return result.data or []
            
        except Exception as e:
            logger.error(f"Get escalations error: {e}")
            raise e
    
    async def update_escalation(
        self, 
        escalation_id: str, 
        restaurant_id: int,
        update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update an escalation event"""
        try:
            # Filter out None values
            payload = {k: v for k, v in update_data.items() if v is not None}
            
            # Handle resolution timestamp
            if "resolution" in payload and payload["resolution"]:
                payload["resolved_at"] = datetime.utcnow().isoformat()
                payload["status"] = "resolved"
            
            # Always update the updated_at timestamp
            payload["updated_at"] = datetime.utcnow().isoformat()
            
            if not payload:
                return await self.get_escalation_by_id(escalation_id, restaurant_id)
            
            result = self.supabase.table("sse_escalation_events") \
                .update(payload) \
                .eq("id", escalation_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Update escalation error: {e}")
            raise e
    
    async def add_history_entry(
        self,
        event_id: str,
        step_number: int,
        action_taken: str,
        actor_staff_id: Optional[str] = None,
        actor_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Add a history entry to an escalation"""
        try:
            payload = {
                "event_id": event_id,
                "step_number": step_number,
                "action_taken": action_taken,
                "actor_staff_id": actor_staff_id,
                "actor_name": actor_name,
                "completed_at": datetime.utcnow().isoformat()
            }
            
            result = self.supabase.table("sse_escalation_history").insert(payload).execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            else:
                raise Exception("Insert returned no data")
                
        except Exception as e:
            logger.error(f"Add history entry error: {e}")
            raise e
    
    async def advance_step(
        self,
        escalation_id: str,
        restaurant_id: int,
        action_taken: str,
        actor_staff_id: str
    ) -> Optional[Dict[str, Any]]:
        """Advance escalation to next step with history entry"""
        try:
            # Get current event
            event = await self.get_escalation_by_id(escalation_id, restaurant_id)
            if not event:
                return None
            
            current_step = event["current_step"]
            if current_step >= 7:
                raise ValueError("Already at maximum step")
            
            new_step = current_step + 1
            
            # Update status if reaching escalation threshold
            new_status = event["status"]
            if new_step >= 5 and new_status == "actionable":
                new_status = "escalated"
            
            # Update the event
            await self.update_escalation(
                escalation_id=escalation_id,
                restaurant_id=restaurant_id,
                update_data={
                    "current_step": new_step,
                    "status": new_status
                }
            )
            
            # Add history entry
            await self.add_history_entry(
                event_id=escalation_id,
                step_number=new_step,
                action_taken=action_taken,
                actor_staff_id=actor_staff_id
            )
            
            return await self.get_escalation_with_history(escalation_id, restaurant_id)
            
        except Exception as e:
            logger.error(f"Advance step error: {e}")
            raise e
    
    async def get_active_count(self, restaurant_id: int) -> int:
        """Get count of active escalations"""
        try:
            result = self.supabase.table("sse_escalation_events") \
                .select("id", count="exact") \
                .eq("restaurant_id", restaurant_id) \
                .eq("status", "actionable") \
                .execute()
            
            return result.count or 0
            
        except Exception as e:
            logger.error(f"Get active count error: {e}")
            return 0