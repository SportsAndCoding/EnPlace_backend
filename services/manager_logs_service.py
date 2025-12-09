import logging
from datetime import date
from typing import Optional, Dict, Any, List
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

class ManagerLogsService:
    def __init__(self):
        self.supabase = get_supabase()
    
    async def create_log(self, log_data: Dict[str, Any], manager_staff_id: str) -> Dict[str, Any]:
        """
        Create a daily log entry for a manager.
        One log per restaurant per day.
        """
        try:
            log_date = log_data.get("log_date") or date.today().isoformat()
            
            payload = {
                "restaurant_id": log_data["restaurant_id"],
                "manager_staff_id": manager_staff_id,
                "log_date": log_date if isinstance(log_date, str) else log_date.isoformat(),
                "overall_rating": log_data["overall_rating"],
                "felt_smooth": log_data.get("felt_smooth"),
                "felt_understaffed": log_data.get("felt_understaffed"),
                "felt_chaotic": log_data.get("felt_chaotic"),
                "felt_overstaffed": log_data.get("felt_overstaffed"),
                "notes": log_data.get("notes")
            }
            
            result = self.supabase.table("manager_daily_logs").insert(payload).execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            else:
                raise Exception("Insert returned no data")
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Create manager log error: {error_msg}")
            
            # Check for unique constraint violation (already logged today)
            if "unique_manager_date" in error_msg or "duplicate key" in error_msg.lower():
                raise ValueError("Already logged for this date")
            
            raise e
    
    async def get_log_by_restaurant_and_date(
        self, 
        restaurant_id: int, 
        log_date: date
    ) -> Optional[Dict[str, Any]]:
        """Get a specific log by restaurant and date"""
        try:
            result = self.supabase.table("manager_daily_logs") \
                .select("*") \
                .eq("restaurant_id", restaurant_id) \
                .eq("log_date", log_date.isoformat()) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Get manager log error: {e}")
            raise e
    
    async def get_logs_by_restaurant(
        self,
        restaurant_id: int,
        start_date: date,
        end_date: date
    ) -> List[Dict[str, Any]]:
        """Get all logs for a restaurant within a date range"""
        try:
            result = self.supabase.table("manager_daily_logs") \
                .select("*, manager:manager_staff_id(full_name)") \
                .eq("restaurant_id", restaurant_id) \
                .gte("log_date", start_date.isoformat()) \
                .lte("log_date", end_date.isoformat()) \
                .order("log_date", desc=True) \
                .execute()
            
            return result.data or []
            
        except Exception as e:
            logger.error(f"Get manager logs error: {e}")
            raise e
    
    async def get_today_log(self, restaurant_id: int) -> Optional[Dict[str, Any]]:
        """Check if restaurant already has a log for today"""
        return await self.get_log_by_restaurant_and_date(restaurant_id, date.today())