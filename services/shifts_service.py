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
        """Get a specific shift by ID - NO EMBED"""
        try:
            result = self.supabase.table("sse_shifts") \
                .select("id, restaurant_id, staff_id, shift_date, scheduled_start, scheduled_end, shift_type, day_type, is_published, created_by, created_at, status, position, reason, original_staff_id") \
                .eq("id", shift_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if not result.data or len(result.data) == 0:
                return None
            
            return result.data[0]
            
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
        """Get shifts for a restaurant - NO EMBED"""
        try:
            query = self.supabase.table("sse_shifts") \
                .select("id, restaurant_id, staff_id, shift_date, scheduled_start, scheduled_end, shift_type, day_type, is_published, created_by, created_at, status, position, reason, original_staff_id") \
                .eq("restaurant_id", restaurant_id) \
                .gte("shift_date", start_date.isoformat()) \
                .lte("shift_date", end_date.isoformat())
            
            if staff_id:
                query = query.eq("staff_id", staff_id)
            
            if is_published is not None:
                query = query.eq("is_published", is_published)
            
            result = query.order("shift_date").order("scheduled_start").execute()
            shifts = result.data or []
            
            # Add volunteer counts via sse_open_shifts junction
            for shift in shifts:
                vol_count = 0
                # Find corresponding open_shift record
                open_shift = self.supabase.table("sse_open_shifts") \
                    .select("id") \
                    .eq("original_shift_id", shift['id']) \
                    .limit(1) \
                    .execute()
                
                if open_shift.data:
                    # Count pending volunteers
                    count_result = self.supabase.table("sse_shift_offers") \
                        .select("id", count="exact") \
                        .eq("open_shift_id", open_shift.data[0]['id']) \
                        .eq("offer_status", "pending") \
                        .execute()
                    vol_count = count_result.count if count_result.count else 0
                
                shift['volunteer_count'] = vol_count
            
            return shifts
            
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
            payload = {k: v for k, v in update_data.items() if v is not None}
            
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
        """Get open shifts from marketplace"""
        try:
            result = self.supabase.table("open_shifts") \
                .select("id, restaurant_id, position, date, start_time, end_time, bonus_pay, description, status, created_at, claimed_by") \
                .eq("restaurant_id", restaurant_id) \
                .eq("status", "open") \
                .gte("date", start_date.isoformat()) \
                .lte("date", end_date.isoformat()) \
                .order("date") \
                .order("start_time") \
                .execute()

            # Map to frontend expected field names
            shifts = []
            for row in result.data or []:
                shifts.append({
                    "id": row["id"],
                    "restaurant_id": row["restaurant_id"],
                    "position": row["position"],
                    "shift_date": row["date"],
                    "scheduled_start": row["start_time"],
                    "scheduled_end": row["end_time"],
                    "bonus_pay": row.get("bonus_pay", 0),
                    "description": row.get("description"),
                    "status": row["status"],
                    "created_at": row.get("created_at"),
                    "staff_id": row.get("claimed_by"),
                })
            return shifts
            
        except Exception as e:
            logger.error(f"Get open shifts error: {e}")
            raise e
    
    async def get_shift_volunteers(self, shift_id: int, restaurant_id: int) -> List[Dict[str, Any]]:
        """Get volunteers for a shift with staff details"""
        try:
            # First verify shift belongs to restaurant
            shift_check = self.supabase.table("sse_shifts") \
                .select("id") \
                .eq("id", shift_id) \
                .eq("restaurant_id", restaurant_id) \
                .single() \
                .execute()
            
            if not shift_check.data:
                return []
            
            # Get the open_shift record
            open_shift = self.supabase.table("sse_open_shifts") \
                .select("id") \
                .eq("original_shift_id", shift_id) \
                .limit(1) \
                .execute()
            
            if not open_shift.data:
                return []
            
            open_shift_id = open_shift.data[0]['id']
            
            # Get volunteers
            result = self.supabase.table("sse_shift_offers") \
                .select("id, staff_id, offer_status, created_at, responded_at") \
                .eq("open_shift_id", open_shift_id) \
                .eq("offer_status", "pending") \
                .order("created_at", desc=False) \
                .execute()
            
            if not result.data:
                return []
            
            # Get staff details
            staff_ids = [v['staff_id'] for v in result.data]
            staff_result = self.supabase.table("staff") \
                .select("staff_id, full_name, position") \
                .in_("staff_id", staff_ids) \
                .execute()
            
            staff_map = {s['staff_id']: s for s in staff_result.data}
            
            # Build response
            volunteers = []
            for v in result.data:
                staff = staff_map.get(v['staff_id'], {})
                
                # Calculate time ago
                responded = v.get('responded_at')
                time_ago = "just now"
                if responded:
                    from datetime import datetime, timezone
                    responded_dt = datetime.fromisoformat(responded.replace('Z', '+00:00'))
                    delta = datetime.now(timezone.utc) - responded_dt
                    minutes = int(delta.total_seconds() / 60)
                    if minutes < 60:
                        time_ago = f"{minutes}m ago"
                    else:
                        hours = minutes // 60
                        time_ago = f"{hours}h ago"
                
                volunteers.append({
                    "id": v['id'],
                    "staff_id": v['staff_id'],
                    "name": staff.get('full_name', 'Unknown'),
                    "position": staff.get('position', ''),
                    "hours_this_week": 0,
                    "time_ago": time_ago,
                    "offer_status": v['offer_status']
                })
            
            return volunteers
            
        except Exception as e:
            print(f"Error getting shift volunteers: {e}")
            return []
            """Get volunteers for a shift with staff details"""
            try:
                # First verify shift belongs to restaurant
                shift_check = self.supabase.table("sse_shifts") \
                    .select("id") \
                    .eq("id", shift_id) \
                    .eq("restaurant_id", restaurant_id) \
                    .single() \
                    .execute()
                
                if not shift_check.data:
                    return []
                
                # Get volunteers
                result = self.supabase.table("sse_shift_offers") \
                    .select("id, staff_id, offer_status, created_at, responded_at") \
                    .eq("open_shift_id", shift_id) \
                    .eq("offer_status", "pending") \
                    .order("created_at", desc=False) \
                    .execute()
                
                if not result.data:
                    return []
                
                # Get staff details
                staff_ids = [v['staff_id'] for v in result.data]
                staff_result = self.supabase.table("staff") \
                    .select("staff_id, full_name, position") \
                    .in_("staff_id", staff_ids) \
                    .execute()
                
                staff_map = {s['staff_id']: s for s in staff_result.data}
                
                # Build response
                volunteers = []
                for v in result.data:
                    staff = staff_map.get(v['staff_id'], {})
                    
                    # Calculate time ago
                    responded = v.get('responded_at')
                    time_ago = "just now"
                    if responded:
                        from datetime import datetime, timezone
                        responded_dt = datetime.fromisoformat(responded.replace('Z', '+00:00'))
                        delta = datetime.now(timezone.utc) - responded_dt
                        minutes = int(delta.total_seconds() / 60)
                        if minutes < 60:
                            time_ago = f"{minutes}m ago"
                        else:
                            hours = minutes // 60
                            time_ago = f"{hours}h ago"
                    
                    volunteers.append({
                        "id": v['id'],
                        "staff_id": v['staff_id'],
                        "name": staff.get('full_name', 'Unknown'),
                        "position": staff.get('position', ''),
                        "hours_this_week": 0,  # TODO: Calculate from sse_shifts
                        "time_ago": time_ago,
                        "offer_status": v['offer_status']
                    })
                
                return volunteers
                
            except Exception as e:
                print(f"Error getting shift volunteers: {e}")
                return []