import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

class NotificationsService:
    def __init__(self):
        self.supabase = get_supabase()
    
    async def create_notification(
        self, 
        notification_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new notification"""
        try:
            payload = {
                "recipient_id": notification_data.get("recipient_id"),
                "restaurant_id": notification_data["restaurant_id"],
                "title": notification_data["title"],
                "message": notification_data["message"],
                "type": notification_data["type"],
                "related_id": notification_data.get("related_id"),
                "is_read": False,
                "created_at": datetime.utcnow().isoformat()
            }
            
            result = self.supabase.table("notifications").insert(payload).execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            else:
                raise Exception("Insert returned no data")
                
        except Exception as e:
            logger.error(f"Create notification error: {e}")
            raise e
    
    async def get_notifications_for_user(
        self,
        staff_id: str,
        restaurant_id: int,
        unread_only: bool = False,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get notifications for a user.
        Includes both direct notifications (recipient_id = staff_id)
        and broadcast notifications (recipient_id = null) for their restaurant.
        """
        try:
            # Get direct notifications
            query = self.supabase.table("notifications") \
                .select("*") \
                .eq("restaurant_id", restaurant_id) \
                .or_(f"recipient_id.eq.{staff_id},recipient_id.is.null")
            
            if unread_only:
                query = query.eq("is_read", False)
            
            result = query \
                .order("created_at", desc=True) \
                .limit(limit) \
                .execute()
            
            return result.data or []
            
        except Exception as e:
            logger.error(f"Get notifications error: {e}")
            raise e
    
    async def get_notification_by_id(
        self, 
        notification_id: str, 
        restaurant_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific notification"""
        try:
            result = self.supabase.table("notifications") \
                .select("*") \
                .eq("id", notification_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Get notification error: {e}")
            raise e
    
    async def mark_as_read(
        self, 
        notification_id: str, 
        restaurant_id: int
    ) -> Optional[Dict[str, Any]]:
        """Mark a notification as read"""
        try:
            result = self.supabase.table("notifications") \
                .update({"is_read": True}) \
                .eq("id", notification_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Mark as read error: {e}")
            raise e
    
    async def mark_all_as_read(
        self, 
        staff_id: str, 
        restaurant_id: int
    ) -> int:
        """Mark all notifications as read for a user"""
        try:
            # Mark direct notifications
            result = self.supabase.table("notifications") \
                .update({"is_read": True}) \
                .eq("restaurant_id", restaurant_id) \
                .eq("is_read", False) \
                .or_(f"recipient_id.eq.{staff_id},recipient_id.is.null") \
                .execute()
            
            return len(result.data) if result.data else 0
            
        except Exception as e:
            logger.error(f"Mark all as read error: {e}")
            raise e
    
    async def get_unread_count(
        self, 
        staff_id: str, 
        restaurant_id: int
    ) -> int:
        """Get count of unread notifications"""
        try:
            result = self.supabase.table("notifications") \
                .select("id", count="exact") \
                .eq("restaurant_id", restaurant_id) \
                .eq("is_read", False) \
                .or_(f"recipient_id.eq.{staff_id},recipient_id.is.null") \
                .execute()
            
            return result.count or 0
            
        except Exception as e:
            logger.error(f"Get unread count error: {e}")
            return 0
    
    async def delete_notification(
        self, 
        notification_id: str, 
        restaurant_id: int
    ) -> bool:
        """Delete a notification"""
        try:
            result = self.supabase.table("notifications") \
                .delete() \
                .eq("id", notification_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            return result.data is not None and len(result.data) > 0
            
        except Exception as e:
            logger.error(f"Delete notification error: {e}")
            raise e