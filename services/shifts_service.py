import logging
from datetime import date
from typing import Optional, Dict, Any, List
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

class ShiftsService:
    def __init__(self):
        self.supabase = get_supabase()
    
    async def create_shift(
        self, 
        shift_data: Dict[str, Any], 
        created_by: str
    ) -> Dict[str, Any]:
        """Create a new shift"""
        try:
            payload = {
                "restaurant_id": shift_data["restaurant_id"],
                "staff_id": shift_data.get("staff_id"),
                "shift_date": shift_data["shift_date"].isoformat() if isinstance(shift_data["shift_date"], date) else shift_data["shift_date"],
                "scheduled_start": shift_data["scheduled_start"].isoformat() if hasattr(shift_data["scheduled_start"], 'isoformat') else shift_data["scheduled_start"],
                "scheduled_end": shift_data["scheduled_end"].isoformat() if hasattr(shift_data["scheduled_end"], 'isoformat') else shift_data["scheduled_end"],
                "shift_type": shift_data["shift_type"],
                "day_type": shift_data["day_type"],
                "is_published": shift_data.get("is_published", False),
                "created_by": created_by
            }
            
            result = self.supabase.table("sse_shifts").insert(payload).execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            else:
                raise Exception("Insert returned no data")
                
        except Exception as e:
            logger.error(f"Create shift error: {e}")
            raise e
    
    async def get_shift_by_id(
        self, 
        shift_id: int, 
        restaurant_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific shift by ID"""
        try:
            result = self.supabase.table("sse_shifts") \
                .select("*, staff:staff_id(full_name, position)") \
                .eq("id", shift_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Get shift error: {e}")
            raise e
    
    async def get_shifts_by_restaurant(
        self,
        restaurant_id: int,
        start_date: date,
        end_date: date,
        staff_id: Optional[str] = None,
        is_published: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """Get shifts for a restaurant within a date range"""
        try:
            query = self.supabase.table("sse_shifts") \
                .select("*, staff:staff_id(full_name, position)") \
                .eq("restaurant_id", restaurant_id) \
                .gte("shift_date", start_date.isoformat()) \
                .lte("shift_date", end_date.isoformat())
            
            if staff_id:
                query = query.eq("staff_id", staff_id)
            
            if is_published is not None:
                query = query.eq("is_published", is_published)
            
            result = query.order("shift_date").order("scheduled_start").execute()
            
            return result.data or []
            
        except Exception as e:
            logger.error(f"Get shifts error: {e}")
            raise e
    
    async def update_shift(
        self, 
        shift_id: int, 
        restaurant_id: int,
        update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update an existing shift"""
        try:
            # Filter out None values
            payload = {k: v for k, v in update_data.items() if v is not None}
            
            # Convert date/datetime objects to ISO strings
            if "shift_date" in payload and hasattr(payload["shift_date"], 'isoformat'):
                payload["shift_date"] = payload["shift_date"].isoformat()
            if "scheduled_start" in payload and hasattr(payload["scheduled_start"], 'isoformat'):
                payload["scheduled_start"] = payload["scheduled_start"].isoformat()
            if "scheduled_end" in payload and hasattr(payload["scheduled_end"], 'isoformat'):
                payload["scheduled_end"] = payload["scheduled_end"].isoformat()
            
            if not payload:
                return await self.get_shift_by_id(shift_id, restaurant_id)
            
            result = self.supabase.table("sse_shifts") \
                .update(payload) \
                .eq("id", shift_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Update shift error: {e}")
            raise e
    
    async def delete_shift(
        self, 
        shift_id: int, 
        restaurant_id: int
    ) -> bool:
        """Delete a shift"""
        try:
            result = self.supabase.table("sse_shifts") \
                .delete() \
                .eq("id", shift_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            return result.data is not None and len(result.data) > 0
            
        except Exception as e:
            logger.error(f"Delete shift error: {e}")
            raise e
    
    async def get_open_shifts(
        self,
        restaurant_id: int,
        start_date: date,
        end_date: date
    ) -> List[Dict[str, Any]]:
        """Get unassigned (open) shifts"""
        try:
            result = self.supabase.table("sse_shifts") \
                .select("*") \
                .eq("restaurant_id", restaurant_id) \
                .is_("staff_id", "null") \
                .gte("shift_date", start_date.isoformat()) \
                .lte("shift_date", end_date.isoformat()) \
                .order("shift_date") \
                .order("scheduled_start") \
                .execute()
            
            return result.data or []
            
        except Exception as e:
            logger.error(f"Get open shifts error: {e}")
            raise e