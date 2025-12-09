import logging
from datetime import date
from typing import Optional, Dict, Any, List
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

class CheckinsService:
    def __init__(self):
        self.supabase = get_supabase()
    
    async def create_checkin(self, checkin_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a daily check-in for a staff member.
        Returns the created check-in or raises an exception if duplicate.
        """
        try:
            # Build insert payload
            payload = {
                "staff_id": checkin_data["staff_id"],
                "restaurant_id": checkin_data["restaurant_id"],
                "checkin_date": date.today().isoformat(),
                "mood_emoji": checkin_data["mood_emoji"],
                "felt_safe": checkin_data.get("felt_safe"),
                "felt_fair": checkin_data.get("felt_fair"),
                "felt_respected": checkin_data.get("felt_respected"),
                "notes": checkin_data.get("notes")
            }
            
            result = self.supabase.table("sse_daily_checkins").insert(payload).execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            else:
                raise Exception("Insert returned no data")
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Create checkin error: {error_msg}")
            
            # Check for unique constraint violation (already checked in today)
            if "unique_staff_date" in error_msg or "duplicate key" in error_msg.lower():
                raise ValueError("Already checked in today")
            
            raise e
    
    async def get_checkin_by_staff_and_date(
        self, 
        staff_id: str, 
        checkin_date: date
    ) -> Optional[Dict[str, Any]]:
        """Get a specific check-in by staff and date"""
        try:
            result = self.supabase.table("sse_daily_checkins") \
                .select("*") \
                .eq("staff_id", staff_id) \
                .eq("checkin_date", checkin_date.isoformat()) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Get checkin error: {e}")
            raise e
    
    async def get_checkins_by_restaurant(
        self,
        restaurant_id: int,
        start_date: date,
        end_date: date
    ) -> List[Dict[str, Any]]:
        """Get all check-ins for a restaurant within a date range"""
        try:
            result = self.supabase.table("sse_daily_checkins") \
                .select("*, staff:staff_id(full_name, position)") \
                .eq("restaurant_id", restaurant_id) \
                .gte("checkin_date", start_date.isoformat()) \
                .lte("checkin_date", end_date.isoformat()) \
                .order("checkin_date", desc=True) \
                .execute()
            
            return result.data or []
            
        except Exception as e:
            logger.error(f"Get checkins error: {e}")
            raise e
    
    async def get_today_checkin(self, staff_id: str) -> Optional[Dict[str, Any]]:
        """Check if staff already checked in today"""
        return await self.get_checkin_by_staff_and_date(staff_id, date.today())