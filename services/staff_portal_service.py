"""
Staff Portal Service
Handles staff-facing functionality: profile, preferences, stability points, callouts
"""
import logging
from datetime import date, datetime, timezone
from typing import Optional, Dict, Any, List
import pytz
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)


class StaffPortalService:
    def __init__(self):
        self.supabase = get_supabase()

    def _get_now_for_restaurant(self, restaurant_id: int) -> datetime:
        """Get current datetime in restaurant timezone."""
        try:
            result = self.supabase.table("restaurants").select("timezone").eq("id", restaurant_id).single().execute()
            tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
        except:
            tz_name = "America/New_York"
        tz = pytz.timezone(tz_name)
        return datetime.now(tz)

    # ═══════════════════════════════════════════════════════════════════
    # STAFF PROFILE
    # ═══════════════════════════════════════════════════════════════════

    async def get_staff_profile(self, staff_id: str) -> Optional[Dict[str, Any]]:
        """Get full staff profile for portal display"""
        try:
            result = self.supabase.table("staff") \
                .select("staff_id, restaurant_id, full_name, email, position, phone, hire_date, status, skills, stability_points_balance, aime_score, burnout_risk_score") \
                .eq("staff_id", staff_id) \
                .single() \
                .execute()

            if not result.data:
                return None

            profile = result.data

            # Parse name into first/last
            full_name = profile.get("full_name", "")
            name_parts = full_name.split(" ", 1)
            profile["first_name"] = name_parts[0] if name_parts else ""
            profile["last_name"] = name_parts[1] if len(name_parts) > 1 else ""

            # Generate initials
            profile["initials"] = "".join([p[0].upper() for p in name_parts if p])[:2]

            # Get current tier
            balance = profile.get("stability_points_balance", 0) or 0
            profile["current_tier"] = self._get_tier(balance)
            profile["tier_progress"] = self._get_tier_progress(balance)
            
            # Fetch restaurant feature flags for paywall
            restaurant_id = profile.get("restaurant_id")
            if restaurant_id:
                restaurant_result = self.supabase.table("restaurants") \
                    .select("has_open_shift_marketplace, has_shift_swap, has_schedule_optimizer, has_stable_hire, has_house_guardian, name") \
                    .eq("id", restaurant_id) \
                    .single() \
                    .execute()
                
                if restaurant_result.data:
                    r = restaurant_result.data
                    profile["restaurant_name"] = r.get("name", "")
                    profile["modules"] = {
                        "openShifts": {"owned": r.get("has_open_shift_marketplace", False)},
                        "shiftSwap": {"owned": r.get("has_shift_swap", False)},
                        "schedule": {"owned": r.get("has_schedule_optimizer", False)},
                        "stableHire": {"owned": r.get("has_stable_hire", False)},
                        "houseGuardian": {"owned": r.get("has_house_guardian", False)},
                        "aime": {"owned": True}  # Always included
                    }
                else:
                    profile["modules"] = {}
            else:
                profile["modules"] = {}
            
            return profile

        except Exception as e:
            logger.error(f"Get staff profile error: {e}")
            raise e

    def _get_tier(self, points: int) -> Dict[str, Any]:
        """Get tier info based on points"""
        if points >= 1000:
            return {"key": "platinum", "name": "Platinum", "icon": "💎", "color": "#E5E4E2"}
        elif points >= 500:
            return {"key": "gold", "name": "Gold", "icon": "🥇", "color": "#FFD700"}
        elif points >= 200:
            return {"key": "silver", "name": "Silver", "icon": "🥈", "color": "#C0C0C0"}
        else:
            return {"key": "bronze", "name": "Bronze", "icon": "🥉", "color": "#CD7F32"}

    def _get_tier_progress(self, points: int) -> Dict[str, Any]:
        """Get progress to next tier"""
        tiers = [
            {"key": "bronze", "min": 0, "max": 199},
            {"key": "silver", "min": 200, "max": 499},
            {"key": "gold", "min": 500, "max": 999},
            {"key": "platinum", "min": 1000, "max": float('inf')}
        ]

        current_tier = None
        next_tier = None

        for i, tier in enumerate(tiers):
            if tier["min"] <= points <= tier["max"]:
                current_tier = tier
                if i < len(tiers) - 1:
                    next_tier = tiers[i + 1]
                break

        if not next_tier:
            return {"progress": 100, "next_tier": None, "points_needed": 0}

        points_into_tier = points - current_tier["min"]
        tier_range = next_tier["min"] - current_tier["min"]
        progress = int((points_into_tier / tier_range) * 100)

        return {
            "progress": min(progress, 100),
            "next_tier": next_tier["key"],
            "points_needed": next_tier["min"] - points
        }

    # ═══════════════════════════════════════════════════════════════════
    # STAFF PREFERENCES
    # ═══════════════════════════════════════════════════════════════════

    async def get_preferences(self, staff_id: str) -> Optional[Dict[str, Any]]:
        """Get staff preferences"""
        try:
            result = self.supabase.table("staff_preferences") \
                .select("*") \
                .eq("staff_id", staff_id) \
                .execute()

            if result.data and len(result.data) > 0:
                return result.data[0]

            # Return empty defaults if no preferences set
            return {
                "staff_id": staff_id,
                "preferred_shift_types": [],
                "preferred_days_of_week": [],
                "trained_roles": [],
                "max_consecutive_days": None,
                "notes": None
            }

        except Exception as e:
            logger.error(f"Get preferences error: {e}")
            raise e

    async def update_preferences(
        self,
        staff_id: str,
        preferences: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update or create staff preferences"""
        try:
            # Convert day strings to integers for database
            day_str_to_int = {
                'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3,
                'thu': 4, 'fri': 5, 'sat': 6
            }
            days_str = preferences.get("preferred_days_of_week", [])
            days_int = [day_str_to_int[d.lower()] for d in days_str if d.lower() in day_str_to_int]

            payload = {
                "staff_id": staff_id,
                "preferred_shift_types": preferences.get("preferred_shift_types", []),
                "preferred_days_of_week": days_int,
                "trained_roles": preferences.get("trained_roles", []),
                "max_consecutive_days": preferences.get("max_consecutive_days"),
                "notes": preferences.get("notes"),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

            # Upsert (insert or update)
            result = self.supabase.table("staff_preferences") \
                .upsert(payload, on_conflict="staff_id") \
                .execute()

            if result.data and len(result.data) > 0:
                return result.data[0]

            raise Exception("Upsert returned no data")

        except Exception as e:
            logger.error(f"Update preferences error: {e}")
            raise e
        
    async def update_profile_photo(
        self,
        staff_id: str,
        photo_url: str
    ) -> Dict[str, Any]:
        """Update staff profile photo URL"""
        try:
            result = self.supabase.table("staff") \
                .update({"profile_photo_url": photo_url}) \
                .eq("staff_id", staff_id) \
                .execute()

            if result.data and len(result.data) > 0:
                return result.data[0]
            raise Exception("Update returned no data")

        except Exception as e:
            logger.error(f"Update profile photo error: {e}")
            raise e

    # ═══════════════════════════════════════════════════════════════════
    # STABILITY POINTS
    # ═══════════════════════════════════════════════════════════════════

    async def get_stability_points(
        self,
        staff_id: str,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Get SP balance and transaction history"""
        try:
            # Get current balance from staff table
            staff_result = self.supabase.table("staff") \
                .select("stability_points_balance") \
                .eq("staff_id", staff_id) \
                .single() \
                .execute()

            balance = staff_result.data.get("stability_points_balance", 0) if staff_result.data else 0

            # Get transaction history
            history_result = self.supabase.table("stability_points") \
                .select("*") \
                .eq("staff_id", staff_id) \
                .order("created_at", desc=True) \
                .limit(limit) \
                .execute()

            history = history_result.data or []

            return {
                "balance": balance or 0,
                "tier": self._get_tier(balance or 0),
                "tier_progress": self._get_tier_progress(balance or 0),
                "history": history
            }

        except Exception as e:
            logger.error(f"Get stability points error: {e}")
            raise e

    async def award_points(
        self,
        staff_id: str,
        restaurant_id: int,
        points: int,
        transaction_type: str,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """Award stability points to staff"""
        try:
            # Get current balance
            staff_result = self.supabase.table("staff") \
                .select("stability_points_balance") \
                .eq("staff_id", staff_id) \
                .single() \
                .execute()

            current_balance = staff_result.data.get("stability_points_balance", 0) if staff_result.data else 0
            new_balance = (current_balance or 0) + points

            # Insert transaction
            tx_payload = {
                "staff_id": staff_id,
                "restaurant_id": restaurant_id,
                "points": points,
                "balance_after": new_balance,
                "transaction_type": transaction_type,
                "description": description
            }

            tx_result = self.supabase.table("stability_points") \
                .insert(tx_payload) \
                .execute()

            # Update staff balance
            self.supabase.table("staff") \
                .update({"stability_points_balance": new_balance}) \
                .eq("staff_id", staff_id) \
                .execute()

            return {
                "points_awarded": points,
                "new_balance": new_balance,
                "transaction": tx_result.data[0] if tx_result.data else None
            }

        except Exception as e:
            logger.error(f"Award points error: {e}")
            raise e

    async def redeem_points(
        self,
        staff_id: str,
        restaurant_id: int,
        item_id: str,
        item_name: str,
        cost: int
    ) -> Dict[str, Any]:
        """Redeem stability points for a reward"""
        try:
            # Get current balance
            staff_result = self.supabase.table("staff") \
                .select("stability_points_balance") \
                .eq("staff_id", staff_id) \
                .single() \
                .execute()

            current_balance = staff_result.data.get("stability_points_balance", 0) if staff_result.data else 0

            if (current_balance or 0) < cost:
                raise ValueError("Insufficient points")

            new_balance = (current_balance or 0) - cost

            # Insert redemption transaction
            tx_payload = {
                "staff_id": staff_id,
                "restaurant_id": restaurant_id,
                "points": -cost,
                "balance_after": new_balance,
                "transaction_type": "redemption",
                "description": f"Redeemed: {item_name}",
                "redemption_item_id": item_id,
                "redemption_item_name": item_name
            }

            tx_result = self.supabase.table("stability_points") \
                .insert(tx_payload) \
                .execute()

            # Update staff balance
            self.supabase.table("staff") \
                .update({"stability_points_balance": new_balance}) \
                .eq("staff_id", staff_id) \
                .execute()

            return {
                "points_spent": cost,
                "new_balance": new_balance,
                "item_redeemed": item_name,
                "transaction": tx_result.data[0] if tx_result.data else None
            }

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Redeem points error: {e}")
            raise e

    # ═══════════════════════════════════════════════════════════════════
    # CALLOUTS (Call in Sick)
    # ═══════════════════════════════════════════════════════════════════

    async def create_callout(
        self,
        staff_id: str,
        restaurant_id: int,
        callout_date: date,
        reason: str,
        shift_id: Optional[int] = None,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a callout (call in sick) record"""
        try:
            payload = {
                "staff_id": staff_id,
                "restaurant_id": restaurant_id,
                "callout_date": callout_date.isoformat(),
                "reason": reason,
                "shift_id": shift_id,
                "notes": notes,
                "status": "pending"
            }

            result = self.supabase.table("callouts") \
                .insert(payload) \
                .execute()

            if result.data and len(result.data) > 0:
                return result.data[0]

            raise Exception("Insert returned no data")

        except Exception as e:
            logger.error(f"Create callout error: {e}")
            raise e

    async def get_callouts(
        self,
        restaurant_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        staff_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get callouts for a restaurant"""
        try:
            query = self.supabase.table("callouts") \
                .select("*") \
                .eq("restaurant_id", restaurant_id)

            if start_date:
                query = query.gte("callout_date", start_date.isoformat())
            if end_date:
                query = query.lte("callout_date", end_date.isoformat())
            if staff_id:
                query = query.eq("staff_id", staff_id)

            result = query.order("callout_date", desc=True).execute()
            return result.data or []

        except Exception as e:
            logger.error(f"Get callouts error: {e}")
            raise e

    # ═══════════════════════════════════════════════════════════════════
    # MY SCHEDULE (Staff's own shifts)
    # ═══════════════════════════════════════════════════════════════════

    async def get_my_schedule(
        self,
        staff_id: str,
        restaurant_id: int,
        start_date: date,
        end_date: date
    ) -> List[Dict[str, Any]]:
        """Get staff member's own shifts"""
        try:
            result = self.supabase.table("sse_shifts") \
                .select("id, shift_date, scheduled_start, scheduled_end, shift_type, position, status, is_published") \
                .eq("staff_id", staff_id) \
                .eq("restaurant_id", restaurant_id) \
                .gte("shift_date", start_date.isoformat()) \
                .lte("shift_date", end_date.isoformat()) \
                .eq("is_published", True) \
                .order("shift_date") \
                .order("scheduled_start") \
                .execute()

            return result.data or []

        except Exception as e:
            logger.error(f"Get my schedule error: {e}")
            raise e

    # ═══════════════════════════════════════════════════════════════════
    # SHIFT VOLUNTEER
    # ═══════════════════════════════════════════════════════════════════

    async def volunteer_for_shift(
        self,
        staff_id: str,
        shift_id: str,  # Changed to str to accept UUID
        restaurant_id: int
    ) -> Dict[str, Any]:
        """Staff volunteers for an open shift from the open_shifts marketplace"""
        try:
            # Query open_shifts table (UUID-based)
            shift_result = self.supabase.table("open_shifts") \
                .select("id, restaurant_id, status, claimed_by, position, date, start_time, end_time") \
                .eq("id", shift_id) \
                .eq("restaurant_id", restaurant_id) \
                .single() \
                .execute()

            if not shift_result.data:
                raise ValueError("Shift not found")

            shift = shift_result.data

            if shift.get("status") != "open":
                raise ValueError("Shift is no longer available")

            if shift.get("claimed_by"):
                raise ValueError("Shift has already been claimed")

            # Update the open shift with the volunteer
            result = self.supabase.table("open_shifts") \
                .update({
                    "claimed_by": staff_id,
                    "claimed_at": datetime.now(timezone.utc).isoformat(),
                    "status": "pending"  # Awaiting manager approval
                }) \
                .eq("id", shift_id) \
                .eq("status", "open") \
                .execute()

            if result.data and len(result.data) > 0:
                return result.data[0]

            raise Exception("Failed to claim shift - it may have been claimed by someone else")

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Volunteer for shift error: {e}")
            raise e

    # ═══════════════════════════════════════════════════════════════════
    # SHIFT SWAP REQUEST
    # ═══════════════════════════════════════════════════════════════════

    async def create_swap_request(
        self,
        staff_id: str,
        restaurant_id: int,
        shift_id: int,
        reason: Optional[str] = None,
        target_staff_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a shift swap request"""
        try:
            # Verify shift exists and belongs to requesting staff
            shift_result = self.supabase.table("sse_shifts") \
                .select("id, staff_id, restaurant_id, shift_date") \
                .eq("id", shift_id) \
                .eq("restaurant_id", restaurant_id) \
                .single() \
                .execute()

            if not shift_result.data:
                raise ValueError("Shift not found")

            if shift_result.data.get("staff_id") != staff_id:
                raise ValueError("Can only request swap for your own shifts")

            # Check shift is at least 72 hours away
            shift_date = datetime.strptime(shift_result.data["shift_date"], "%Y-%m-%d")
            now = self._get_now_for_restaurant(restaurant_id).replace(tzinfo=None)  # Make naive for comparison
            hours_until = (shift_date - now).total_seconds() / 3600
            if hours_until < 72:
                raise ValueError("Shifts must be at least 72 hours away to swap")

            # Check for existing pending swap
            existing = self.supabase.table("shift_swaps") \
                .select("id") \
                .eq("shift_id", shift_id) \
                .in_("status", ["posted", "accepted"]) \
                .execute()

            if existing.data and len(existing.data) > 0:
                raise ValueError("Swap request already exists for this shift")

            # Create swap request
            payload = {
                "restaurant_id": restaurant_id,
                "shift_id": shift_id,
                "requesting_staff_id": staff_id,
                "target_staff_id": target_staff_id,
                "status": "posted",
                "reason": reason
            }

            result = self.supabase.table("shift_swaps") \
                .insert(payload) \
                .execute()

            if result.data and len(result.data) > 0:
                return result.data[0]

            raise Exception("Insert returned no data")

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Create swap request error: {e}")
            raise e

    async def get_my_swap_requests(
        self,
        staff_id: str,
        restaurant_id: int
    ) -> List[Dict[str, Any]]:
        """Get swap requests created by this staff member"""
        try:
            result = self.supabase.table("shift_swaps") \
                .select("*") \
                .eq("requesting_staff_id", staff_id) \
                .eq("restaurant_id", restaurant_id) \
                .order("created_at", desc=True) \
                .execute()

            return result.data or []

        except Exception as e:
            logger.error(f"Get my swap requests error: {e}")
            raise e

    async def get_available_swap_requests(
        self,
        staff_id: str,
        restaurant_id: int
    ) -> List[Dict[str, Any]]:
        """Get swap requests from other staff that this person could accept"""
        try:
            # Get pending swaps where target is null or matches this staff
            result = self.supabase.table("shift_swaps") \
                .select("*") \
                .eq("restaurant_id", restaurant_id) \
                .eq("status", "posted") \
                .neq("requesting_staff_id", staff_id) \
                .execute()

            # Filter to only include swaps where target_staff_id is null or matches
            swaps = []
            for swap in (result.data or []):
                target = swap.get("target_staff_id")
                if target is None or target == staff_id:
                    swaps.append(swap)

            return swaps

        except Exception as e:
            logger.error(f"Get available swap requests error: {e}")
            raise e

    async def accept_swap(
        self,
        swap_id: int,
        staff_id: str,
        restaurant_id: int
    ) -> Dict[str, Any]:
        """Accept a swap request (as the receiving staff)"""
        try:
            # Verify swap exists and is pending
            swap_result = self.supabase.table("shift_swaps") \
                .select("*") \
                .eq("id", swap_id) \
                .eq("restaurant_id", restaurant_id) \
                .eq("status", "posted") \
                .single() \
                .execute()

            if not swap_result.data:
                raise ValueError("Swap request not found or already processed")

            # Can't accept your own swap request
            if swap_result.data.get("requesting_staff_id") == staff_id:
                raise ValueError("Cannot accept your own swap request")

            # Update swap with accepting staff
            result = self.supabase.table("shift_swaps") \
                .update({
                    "target_staff_id": staff_id,
                    "status": "accepted"
                }) \
                .eq("id", swap_id) \
                .execute()

            if result.data and len(result.data) > 0:
                return result.data[0]

            raise Exception("Update returned no data")

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Accept swap error: {e}")
            raise e

    async def cancel_swap_request(
        self,
        swap_id: int,
        staff_id: str,
        restaurant_id: int
    ) -> bool:
        """Cancel a swap request (only by the requester)"""
        try:
            # Verify ownership
            swap_result = self.supabase.table("shift_swaps") \
                .select("requesting_staff_id") \
                .eq("id", swap_id) \
                .eq("restaurant_id", restaurant_id) \
                .single() \
                .execute()

            if not swap_result.data:
                raise ValueError("Swap request not found")

            if swap_result.data.get("requesting_staff_id") != staff_id:
                raise ValueError("Can only cancel your own swap requests")

            # Update status to cancelled
            self.supabase.table("shift_swaps") \
                .update({"status": "cancelled"}) \
                .eq("id", swap_id) \
                .execute()

            return True

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Cancel swap request error: {e}")
            raise e