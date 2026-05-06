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
                "organization_id": shift_data["organization_id"],
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
        organization_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific shift by ID - NO EMBED"""
        try:
            result = self.supabase.table("sse_shifts") \
                .select("id, organization_id, staff_id, shift_date, scheduled_start, scheduled_end, shift_type, day_type, is_published, created_by, created_at, status, position, reason, original_staff_id") \
                .eq("id", shift_id) \
                .eq("organization_id", organization_id) \
                .execute()
            
            if not result.data or len(result.data) == 0:
                return None
            
            return result.data[0]
            
        except Exception as e:
            logger.error(f"Get shift error: {e}")
            raise e
    
    async def get_shifts_by_restaurant(
        self,
        organization_id: int,
        start_date: date,
        end_date: date,
        staff_id: Optional[str] = None,
        is_published: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """Get shifts for a restaurant - NO EMBED"""
        try:
            query = self.supabase.table("sse_shifts") \
                .select("id, organization_id, staff_id, shift_date, scheduled_start, scheduled_end, shift_type, day_type, is_published, created_by, created_at, status, position, reason, original_staff_id") \
                .eq("organization_id", organization_id) \
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
        organization_id: int,
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
                return await self.get_shift_by_id(shift_id, organization_id)
            
            result = self.supabase.table("sse_shifts") \
                .update(payload) \
                .eq("id", shift_id) \
                .eq("organization_id", organization_id) \
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
        organization_id: int
    ) -> bool:
        """Delete a shift"""
        try:
            result = self.supabase.table("sse_shifts") \
                .delete() \
                .eq("id", shift_id) \
                .eq("organization_id", organization_id) \
                .execute()
            
            return result.data is not None and len(result.data) > 0
            
        except Exception as e:
            logger.error(f"Delete shift error: {e}")
            raise e
    
    async def get_open_shifts(
        self,
        organization_id: int,
        start_date: date,
        end_date: date
    ) -> List[Dict[str, Any]]:
        """Get open shifts from marketplace"""
        try:
            from datetime import datetime, timezone
            
            result = self.supabase.table("open_shifts") \
                .select("id, organization_id, position, date, start_time, end_time, bonus_pay, description, status, created_at, claimed_by") \
                .eq("organization_id", organization_id) \
                .in_("status", ["open", "pending"]) \
                .gte("date", start_date.isoformat()) \
                .lte("date", end_date.isoformat()) \
                .order("date") \
                .order("start_time") \
                .execute()
            
            # Filter out shifts that have already started today
            now = datetime.now(timezone.utc)
            today_str = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M:%S")
            
            shifts = []
            for row in result.data or []:
                # Skip today's shifts where start_time has passed
                if row["date"] == today_str and row["start_time"] <= current_time:
                    continue
                    
                shifts.append({
                    "id": row["id"],
                    "organization_id": row["organization_id"],
                    "position": row["position"],
                    "shift_date": row["date"],
                    "scheduled_start": row["start_time"],
                    "scheduled_end": row["end_time"],
                    "bonus_pay": row.get("bonus_pay", 0),
                    "description": row.get("description"),
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "claimed_by": row.get("claimed_by")
                })
            return shifts
        except Exception as e:
            print(f"Error fetching open shifts: {e}")
            raise e
    async def get_pending_open_shift_claims(
        self,
        organization_id: int
    ) -> List[Dict[str, Any]]:
        """Get open shifts with pending claims (for manager approval)"""
        try:
            result = self.supabase.table("open_shifts") \
                .select("id, organization_id, position, date, start_time, end_time, bonus_pay, description, status, created_at, claimed_by, claimed_at") \
                .eq("organization_id", organization_id) \
                .eq("status", "pending") \
                .order("date") \
                .order("start_time") \
                .execute()

            if not result.data:
                return []

            # Get staff details for claimed_by
            staff_ids = [r['claimed_by'] for r in result.data if r.get('claimed_by')]
            staff_map = {}
            if staff_ids:
                staff_result = self.supabase.table("staff") \
                    .select("staff_id, full_name, position") \
                    .in_("staff_id", staff_ids) \
                    .execute()
                staff_map = {s['staff_id']: s for s in (staff_result.data or [])}

            # Build response
            claims = []
            for row in result.data:
                claimer = staff_map.get(row.get('claimed_by'), {})
                claims.append({
                    "id": row["id"],
                    "organization_id": row["organization_id"],
                    "position": row["position"],
                    "date": row["date"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                    "bonus_pay": row.get("bonus_pay", 0),
                    "description": row.get("description"),
                    "status": row["status"],
                    "created_at": row.get("created_at"),
                    "claimed_by": row.get("claimed_by"),
                    "claimed_at": row.get("claimed_at"),
                    "claimer": {
                        "staff_id": row.get("claimed_by"),
                        "name": claimer.get("full_name", "Unknown"),
                        "position": claimer.get("position", "")
                    } if row.get("claimed_by") else None
                })
            return claims
            
        except Exception as e:
            logger.error(f"Get pending open shift claims error: {e}")
            raise e

    async def get_shift_volunteers(self, shift_id: int, organization_id: int) -> List[Dict[str, Any]]:
        """Get volunteers for a shift with staff details"""
        try:
            # First verify shift belongs to restaurant
            shift_check = self.supabase.table("sse_shifts") \
                .select("id") \
                .eq("id", shift_id) \
                .eq("organization_id", organization_id) \
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
                    .eq("organization_id", organization_id) \
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
    async def update_open_shift(
        self,
        shift_id: str,
        organization_id: int,
        update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update an open_shifts record (UUID-based marketplace shifts)"""
        try:
            # Allow explicit None for clearable fields like claimed_by
            clearable_fields = {'claimed_by', 'staff_id', 'created_by'}
            payload = {k: v for k, v in update_data.items() if v is not None or k in clearable_fields}
            
            result = self.supabase.table("open_shifts") \
                .update(payload) \
                .eq("id", shift_id) \
                .eq("organization_id", organization_id) \
                .execute()
            
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error updating open shift: {e}")
            raise e
        
    async def get_open_shift_volunteers(
        self,
        shift_id: str,
        organization_id: int
    ) -> List[Dict[str, Any]]:
        """Get all volunteers for an open shift"""
        try:
            # Get volunteers
            result = self.supabase.table("open_shift_volunteers") \
                .select("*") \
                .eq("open_shift_id", shift_id) \
                .eq("status", "pending") \
                .order("volunteered_at", desc=False) \
                .execute()
            
            volunteers = result.data or []
            if not volunteers:
                return []
            
            # Get staff details separately
            staff_ids = [v["staff_id"] for v in volunteers]
            staff_result = self.supabase.table("staff") \
                .select("staff_id, full_name, position, profile_photo_url") \
                .in_("staff_id", staff_ids) \
                .execute()
            
            staff_map = {
                s["staff_id"]: {
                    "staff_id": s["staff_id"],
                    "full_name": s["full_name"],
                    "position": s["position"],
                    "photo_url": s.get("profile_photo_url")
                }
                for s in (staff_result.data or [])
            }
            
            # Merge staff into volunteers
            for v in volunteers:
                v["staff"] = staff_map.get(v["staff_id"], {})
            
            return volunteers
        except Exception as e:
            print(f"Error getting volunteers: {e}")
            raise e

    async def select_volunteer(
        self,
        shift_id: str,
        staff_id: str,
        organization_id: int
    ) -> Dict[str, Any]:
        """Manager selects a volunteer for an open shift"""
        try:
            # Get the open shift details first
            shift_result = self.supabase.table("open_shifts") \
                .select("*") \
                .eq("id", shift_id) \
                .eq("organization_id", organization_id) \
                .single() \
                .execute()
            
            if not shift_result.data:
                raise ValueError("Shift not found")
            
            open_shift = shift_result.data
            
            # Update open_shifts - set claimed_by and approved
            update_result = self.supabase.table("open_shifts") \
                .update({
                    "claimed_by": staff_id,
                    "status": "approved"
                }) \
                .eq("id", shift_id) \
                .eq("organization_id", organization_id) \
                .execute()
            
            if not update_result.data:
                raise ValueError("Failed to update shift")
            
            # Mark selected volunteer as 'selected'
            self.supabase.table("open_shift_volunteers") \
                .update({"status": "selected"}) \
                .eq("open_shift_id", shift_id) \
                .eq("staff_id", staff_id) \
                .execute()
            
            # Mark all other volunteers as 'not_selected'
            self.supabase.table("open_shift_volunteers") \
                .update({"status": "not_selected"}) \
                .eq("open_shift_id", shift_id) \
                .neq("staff_id", staff_id) \
                .execute()
            
            # Insert into sse_shifts so it appears on staff schedule
            shift_date = open_shift["date"]
            start_time = open_shift.get("start_time", "09:00:00")
            end_time = open_shift.get("end_time", "17:00:00")
            
            # Combine date and time into timestamp
            scheduled_start = f"{shift_date}T{start_time}+00:00"
            scheduled_end = f"{shift_date}T{end_time}+00:00"
            
            # Determine day_type
            from datetime import datetime
            date_obj = datetime.strptime(shift_date, "%Y-%m-%d")
            day_type = "weekend" if date_obj.weekday() >= 5 else "weekday"
            
            self.supabase.table("sse_shifts") \
                .insert({
                    "organization_id": organization_id,
                    "staff_id": staff_id,
                    "shift_date": shift_date,
                    "scheduled_start": scheduled_start,
                    "scheduled_end": scheduled_end,
                    "shift_type": open_shift.get("position", "General"),
                    "position": open_shift.get("position"),
                    "day_type": day_type,
                    "is_published": True,
                    "status": "scheduled",
                    "reason": f"Picked up open shift"
                }) \
                .execute()
            
            return update_result.data[0]
        except ValueError:
            raise
        except Exception as e:
            print(f"Error selecting volunteer: {e}")
            raise e