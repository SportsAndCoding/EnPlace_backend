import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, date
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)


class ShiftSwapsService:
    def __init__(self):
        self.supabase = get_supabase()
    
    async def get_swaps(
        self,
        restaurant_id: int,
        status_filter: Optional[str] = None,
        include_past: bool = False
    ) -> List[Dict[str, Any]]:
        """Get shift swaps with staff and shift details"""
        try:
            # Get swaps
            query = self.supabase.table("shift_swaps") \
                .select("*") \
                .eq("restaurant_id", restaurant_id) \
                .order("created_at", desc=True)
            
            if status_filter:
                query = query.eq("status", status_filter)
            
            result = query.execute()
            swaps = result.data or []
            
            if not swaps:
                return []
            
            # Get shift details
            shift_ids = list(set(s['shift_id'] for s in swaps))
            shifts_result = self.supabase.table("sse_shifts") \
                .select("id, shift_date, scheduled_start, scheduled_end, position, shift_type") \
                .in_("id", shift_ids) \
                .execute()
            shifts_map = {s['id']: s for s in (shifts_result.data or [])}
            
            # Filter out past shifts if needed
            today = date.today().isoformat()
            if not include_past:
                swaps = [s for s in swaps if shifts_map.get(s['shift_id'], {}).get('shift_date', '9999') >= today]
            
            # Get staff details
            staff_ids = list(set(
                [s['requesting_staff_id'] for s in swaps] +
                [s['target_staff_id'] for s in swaps if s.get('target_staff_id')] +
                [s['decided_by'] for s in swaps if s.get('decided_by')]
            ))
            staff_result = self.supabase.table("staff") \
                .select("staff_id, full_name, position") \
                .in_("staff_id", staff_ids) \
                .execute()
            staff_map = {s['staff_id']: s for s in (staff_result.data or [])}
            
            # Build enriched response
            enriched = []
            for swap in swaps:
                shift = shifts_map.get(swap['shift_id'], {})
                requester = staff_map.get(swap['requesting_staff_id'], {})
                target = staff_map.get(swap['target_staff_id']) if swap.get('target_staff_id') else None
                decider = staff_map.get(swap['decided_by']) if swap.get('decided_by') else None
                
                # Calculate time ago
                created = swap.get('created_at')
                time_ago = self._time_ago(created) if created else "Recently"
                
                enriched.append({
                    "id": swap['id'],
                    "status": swap['status'],
                    "reason": swap.get('reason'),
                    "manager_notes": swap.get('manager_notes'),
                    "created_at": swap['created_at'],
                    "decided_at": swap.get('decided_at'),
                    "time_ago": time_ago,
                    "shift": {
                        "id": shift.get('id'),
                        "date": shift.get('shift_date'),
                        "start": shift.get('scheduled_start'),
                        "end": shift.get('scheduled_end'),
                        "position": shift.get('position') or shift.get('shift_type')
                    },
                    "requester": {
                        "staff_id": swap['requesting_staff_id'],
                        "name": requester.get('full_name', 'Unknown'),
                        "position": requester.get('position', '')
                    },
                    "target": {
                        "staff_id": swap['target_staff_id'],
                        "name": target.get('full_name', 'Unknown') if target else None,
                        "position": target.get('position', '') if target else None
                    } if swap.get('target_staff_id') else None,
                    "decided_by": {
                        "staff_id": swap['decided_by'],
                        "name": decider.get('full_name', 'Unknown') if decider else None
                    } if swap.get('decided_by') else None
                })
            
            return enriched
            
        except Exception as e:
            logger.error(f"Get swaps error: {e}")
            raise e
    
    async def get_swap_by_id(self, swap_id: int, restaurant_id: int) -> Optional[Dict[str, Any]]:
        """Get single swap by ID"""
        try:
            result = self.supabase.table("shift_swaps") \
                .select("*") \
                .eq("id", swap_id) \
                .eq("restaurant_id", restaurant_id) \
                .single() \
                .execute()
            
            return result.data
        except:
            return None
    
    async def approve_swap(
        self,
        swap_id: int,
        restaurant_id: int,
        decided_by: str
    ) -> Optional[Dict[str, Any]]:
        """Approve a swap and update the shift assignment"""
        try:
            # Get the swap
            swap = await self.get_swap_by_id(swap_id, restaurant_id)
            if not swap or swap['status'] != 'pending':
                return None
            
            # Update swap status
            result = self.supabase.table("shift_swaps") \
                .update({
                    "status": "approved",
                    "decided_by": decided_by,
                    "decided_at": datetime.now(timezone.utc).isoformat()
                }) \
                .eq("id", swap_id) \
                .execute()
            
            # If there's a target, reassign the shift
            if swap.get('target_staff_id'):
                self.supabase.table("sse_shifts") \
                    .update({"staff_id": swap['target_staff_id']}) \
                    .eq("id", swap['shift_id']) \
                    .execute()
            else:
                # No target = giving away shift, make it open
                self.supabase.table("sse_shifts") \
                    .update({
                        "staff_id": None,
                        "status": "open"
                    }) \
                    .eq("id", swap['shift_id']) \
                    .execute()
            
            return result.data[0] if result.data else None
            
        except Exception as e:
            logger.error(f"Approve swap error: {e}")
            raise e
    
    async def reject_swap(
        self,
        swap_id: int,
        restaurant_id: int,
        decided_by: str,
        notes: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Reject a swap request"""
        try:
            swap = await self.get_swap_by_id(swap_id, restaurant_id)
            if not swap or swap['status'] != 'pending':
                return None
            
            result = self.supabase.table("shift_swaps") \
                .update({
                    "status": "rejected",
                    "decided_by": decided_by,
                    "decided_at": datetime.now(timezone.utc).isoformat(),
                    "manager_notes": notes
                }) \
                .eq("id", swap_id) \
                .execute()
            
            return result.data[0] if result.data else None
            
        except Exception as e:
            logger.error(f"Reject swap error: {e}")
            raise e
    
    def _time_ago(self, timestamp_str: str) -> str:
        """Convert timestamp to human-readable time ago"""
        try:
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            delta = datetime.now(timezone.utc) - dt
            minutes = int(delta.total_seconds() / 60)
            
            if minutes < 60:
                return f"{minutes}m ago"
            elif minutes < 1440:
                return f"{minutes // 60}h ago"
            else:
                return f"{minutes // 1440}d ago"
        except:
            return "Recently"