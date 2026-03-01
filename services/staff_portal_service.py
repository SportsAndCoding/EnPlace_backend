"""
Staff Portal Service
Handles staff-facing functionality: profile, preferences, stability points, callouts
"""
import logging
from datetime import date, datetime, timezone
from typing import Optional, Dict, Any, List
import pytz
from database.supabase_client import get_supabase
from services.team_composition_model import analyze_team_composition

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

            if not result.data or len(result.data) == 0:
                raise Exception("Insert returned no data")

            callout = result.data[0]

            # Update shift status to 'callout' if shift_id provided
            if shift_id:
                try:
                    self.supabase.table("sse_shifts") \
                        .update({
                            "status": "callout",
                            "reason": reason
                        }) \
                        .eq("id", shift_id) \
                        .eq("restaurant_id", restaurant_id) \
                        .execute()
                    logger.info(f"Shift {shift_id} marked as callout")
                except Exception as shift_err:
                    logger.warning(f"Failed to update shift status: {shift_err}")
                    # Don't fail the callout if shift update fails

            return callout

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
        shift_id: str,
        restaurant_id: int
    ) -> Dict[str, Any]:
        """Staff volunteers for an open shift - adds to volunteers list"""
        try:
            # Verify shift exists and is open
            shift_result = self.supabase.table("open_shifts") \
                .select("id, restaurant_id, status, position, date, start_time, end_time") \
                .eq("id", shift_id) \
                .eq("restaurant_id", restaurant_id) \
                .single() \
                .execute()
            
            if not shift_result.data:
                raise ValueError("Shift not found")
            
            shift = shift_result.data
            if shift.get("status") not in ("open", "pending"):
                raise ValueError("Shift is no longer available")
            
            # Check if already volunteered
            existing = self.supabase.table("open_shift_volunteers") \
                .select("id") \
                .eq("open_shift_id", shift_id) \
                .eq("staff_id", staff_id) \
                .execute()
            
            if existing.data and len(existing.data) > 0:
                raise ValueError("You have already volunteered for this shift")
            
            # Insert volunteer record
            result = self.supabase.table("open_shift_volunteers") \
                .insert({
                    "open_shift_id": shift_id,
                    "staff_id": staff_id,
                    "restaurant_id": shift.get("restaurant_id"),
                    "status": "pending"
                }) \
                .execute()
            
            # Update shift status to pending if first volunteer
            self.supabase.table("open_shifts") \
                .update({"status": "pending"}) \
                .eq("id", shift_id) \
                .eq("status", "open") \
                .execute()
            
            if result.data and len(result.data) > 0:
                return {**result.data[0], "shift": shift}
            
            raise Exception("Failed to volunteer")
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Volunteer for shift error: {e}")
            raise e

    async def get_my_open_shift_claims(
        self,
        staff_id: str,
        restaurant_id: int
    ) -> List[Dict[str, Any]]:
        """Get open shifts this staff member has volunteered for"""
        try:
            result = self.supabase.table("open_shift_volunteers") \
                .select("*, open_shifts(*)") \
                .eq("staff_id", staff_id) \
                .execute()
            
            # Flatten and filter by restaurant
            claims = []
            for vol in (result.data or []):
                shift = vol.get("open_shifts")
                if shift and shift.get("restaurant_id") == restaurant_id:
                    claims.append({
                        "volunteer_id": vol["id"],
                        "volunteer_status": vol["status"],
                        "volunteered_at": vol["volunteered_at"],
                        **shift
                    })
            
            return sorted(claims, key=lambda x: x.get("date", ""))
        except Exception as e:
            logger.error(f"Get my open shift claims error: {e}")
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

            swaps = result.data or []
            return await self._enrich_swaps(swaps)

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

            return await self._enrich_swaps(swaps)

        except Exception as e:
            logger.error(f"Get available swap requests error: {e}")
            raise e

    async def _enrich_swaps(self, swaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Enrich swap records with shift and staff details"""
        if not swaps:
            return []

        try:
            # Get all shift IDs
            shift_ids = list(set(s['shift_id'] for s in swaps))
            shifts_result = self.supabase.table("sse_shifts") \
                .select("id, shift_date, scheduled_start, scheduled_end, position, shift_type") \
                .in_("id", shift_ids) \
                .execute()
            shifts_map = {s['id']: s for s in (shifts_result.data or [])}

            # Get all staff IDs (requesters and targets)
            staff_ids = list(set(
                [s['requesting_staff_id'] for s in swaps] +
                [s['target_staff_id'] for s in swaps if s.get('target_staff_id')]
            ))
            staff_result = self.supabase.table("staff") \
                .select("staff_id, full_name, position") \
                .in_("staff_id", staff_ids) \
                .execute()
            staff_map = {s['staff_id']: s for s in (staff_result.data or [])}

            # Enrich each swap
            enriched = []
            for swap in swaps:
                shift = shifts_map.get(swap['shift_id'], {})
                requester = staff_map.get(swap['requesting_staff_id'], {})

                swap['shift'] = {
                    'date': shift.get('shift_date'),
                    'start': shift.get('scheduled_start'),
                    'end': shift.get('scheduled_end'),
                    'position': shift.get('position') or shift.get('shift_type')
                }
                swap['staff'] = {
                    'full_name': requester.get('full_name'),
                    'position': requester.get('position')
                }
                enriched.append(swap)

            return enriched

        except Exception as e:
            logger.error(f"Enrich swaps error: {e}")
            # Return original swaps if enrichment fails
            return swaps

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
        
    async def get_personality_profile(self, staff_id: str) -> Optional[Dict[str, Any]]:
        """Get staff member's personality profile"""
        try:
            result = self.supabase.table("staff_personality_profiles") \
                .select("*") \
                .eq("staff_id", staff_id) \
                .execute()

            if result.data and len(result.data) > 0:
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"Get personality profile error: {e}")
            raise e

    async def save_personality_profile(
        self,
        staff_id: str,
        restaurant_id: int,
        scenario_rankings: Dict[str, str],
        source: str = "self_assessment"
    ) -> Dict[str, Any]:
        """
        Compute and save personality profile from scenario rankings.
        Awards 10 SP on first completion, 2 SP on retake (6-month cooldown).
        """
        from services.personality_scoring import compute_full_profile

        try:
            # Check if this is first completion or retake
            existing = await self.get_personality_profile(staff_id)
            is_first = existing is None

            # Enforce 6-month cooldown on retakes
            if existing and source == "self_assessment":
                completed_at = existing.get("completed_at")
                if completed_at:
                    if isinstance(completed_at, str):
                        last_completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                    else:
                        last_completed = completed_at

                    from datetime import timedelta
                    cooldown = timedelta(days=180)
                    now = datetime.now(timezone.utc)
                    if now - last_completed < cooldown:
                        days_remaining = (cooldown - (now - last_completed)).days
                        raise ValueError(f"Personality assessment can be retaken in {days_remaining} days")

            # Compute full profile from scenario rankings
            profile = compute_full_profile(scenario_rankings)

            # Build upsert payload
            points_to_award = 10 if is_first else 2
            payload = {
                "staff_id": staff_id,
                "restaurant_id": restaurant_id,
                "scenario_rankings": scenario_rankings,
                "fingerprint": profile["fingerprint"],
                "persona_primary": profile["persona_primary"],
                "persona_scores": profile["persona_scores"],
                "stability_score": profile["stability_score"],
                "source": source,
                "points_awarded": points_to_award,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

            result = self.supabase.table("staff_personality_profiles") \
                .upsert(payload, on_conflict="staff_id") \
                .execute()

            if not result.data or len(result.data) == 0:
                raise Exception("Upsert returned no data")

            saved_profile = result.data[0]

            # Award stability points (skip for stable_hire source — those are free)
            if source != "stable_hire":
                try:
                    await self.award_points(
                        staff_id=staff_id,
                        restaurant_id=restaurant_id,
                        points=points_to_award,
                        transaction_type="personalityAssessment",
                        description="Personality Assessment" if is_first else "Personality Assessment (retake)"
                    )
                    saved_profile["points_awarded"] = points_to_award
                except Exception as sp_err:
                    logger.warning(f"Failed to award personality SP: {sp_err}")
                    saved_profile["points_awarded"] = 0

            return saved_profile

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Save personality profile error: {e}")
            raise e

    async def get_team_composition(self, restaurant_id: int) -> Dict[str, Any]:
        """
        Aggregate personality profiles for all active staff at a restaurant.
        Returns team fingerprint average, persona distribution, completion rate, gap analysis.
        """
        try:
            # Get all personality profiles for this restaurant
            profiles_result = self.supabase.table("staff_personality_profiles") \
                .select("staff_id, fingerprint, persona_primary, persona_scores, stability_score, source, completed_at") \
                .eq("restaurant_id", restaurant_id) \
                .execute()

            profiles = profiles_result.data or []

            # Get total active staff count
            staff_result = self.supabase.table("staff") \
                .select("staff_id, full_name, position") \
                .eq("restaurant_id", restaurant_id) \
                .ilike("status", "active") \
                .execute()

            active_staff = staff_result.data or []
            total_active = len(active_staff)
            staff_lookup = {s["staff_id"]: s for s in active_staff}

            # Filter to only active staff profiles
            active_profiles = [p for p in profiles if p["staff_id"] in staff_lookup]
            completed = len(active_profiles)

            # Empty state
            if completed == 0:
                return {
                    "team_fingerprint_avg": None,
                    "persona_distribution": {"steadyOperator": 0, "quietContributor": 0, "socialNavigator": 0, "flightRisk": 0},
                    "completion_rate": {"completed": 0, "total_active": total_active, "percent": 0},
                    "gap_analysis": None,
                    "profiles": []
                }

            # Compute averaged fingerprint
            dimensions = ["autonomy", "adaptability", "conflict_tolerance",
                          "authority_response", "team_orientation", "feedback_reception"]

            fingerprint_avg = {}
            for dim in dimensions:
                values = [p["fingerprint"][dim] for p in active_profiles
                          if isinstance(p.get("fingerprint"), dict) and dim in p["fingerprint"]]
                fingerprint_avg[dim] = round(sum(values) / len(values)) if values else 50

            # Persona distribution counts
            persona_counts = {"steadyOperator": 0, "quietContributor": 0, "socialNavigator": 0, "flightRisk": 0}
            for p in active_profiles:
                primary = p.get("persona_primary", "")
                if primary in persona_counts:
                    persona_counts[primary] += 1

            # ── Research-backed composition analysis ──
            # Get restaurant type for format-aware scoring
            restaurant_type = "casual_dining"
            try:
                rest_result = self.supabase.table("restaurants") \
                    .select("restaurant_type") \
                    .eq("id", restaurant_id) \
                    .limit(1) \
                    .execute()
                if rest_result.data and rest_result.data[0].get("restaurant_type"):
                    restaurant_type = rest_result.data[0]["restaurant_type"]
            except Exception:
                pass  # Fall back to casual_dining

            # Build position-persona map for position-level insights
            position_persona_map = {}
            for p in active_profiles:
                s = staff_lookup.get(p["staff_id"], {})
                pos = s.get("position", "Unknown")
                persona = p.get("persona_primary", "")
                if pos not in position_persona_map:
                    position_persona_map[pos] = {"steadyOperator": 0, "quietContributor": 0, "socialNavigator": 0, "flightRisk": 0}
                if persona in position_persona_map[pos]:
                    position_persona_map[pos][persona] += 1

            # Run composition model
            composition = analyze_team_composition(
                persona_counts=persona_counts,
                total_assessed=completed,
                restaurant_type=restaurant_type,
                position_persona_map=position_persona_map,
            )

            # Build gap_analysis in the format the frontend expects
            # (backward compatible — underrepresented, overrepresented, recommendation)
            model_gap = composition.get("gap_analysis") or {}
            gap_analysis = None
            if model_gap.get("recommendation"):
                gap_analysis = {
                    "underrepresented": model_gap.get("underrepresented"),
                    "overrepresented": model_gap.get("overrepresented"),
                    "recommendation": model_gap["recommendation"],
                    "hiring_action": model_gap.get("hiring_action"),
                    "priority": model_gap.get("priority"),
                }

            # Per-staff profile list for manager view
            profile_list = []
            for p in active_profiles:
                s = staff_lookup.get(p["staff_id"], {})
                profile_list.append({
                    "staff_id": p["staff_id"],
                    "full_name": s.get("full_name", "Unknown"),
                    "position": s.get("position", "Unknown"),
                    "persona_primary": p.get("persona_primary"),
                    "fingerprint": p.get("fingerprint"),
                    "stability_score": p.get("stability_score"),
                    "source": p.get("source"),
                    "completed_at": p.get("completed_at")
                })

            return {
                "success": True,
                "team_fingerprint_avg": fingerprint_avg,
                "persona_distribution": persona_counts,
                "completion_rate": {
                    "completed": completed,
                    "total_active": total_active,
                    "percent": round((completed / total_active) * 100) if total_active > 0 else 0
                },
                "gap_analysis": gap_analysis,
                "composition_analysis": {
                    "format_profile": composition.get("format_profile"),
                    "actual_ratios": composition.get("actual_ratios"),
                    "deviations": composition.get("deviations"),
                    "alerts": composition.get("alerts"),
                    "position_insights": composition.get("position_insights"),
                    "overall_health_score": composition.get("overall_health_score"),
                },
                "profiles": profile_list
            }

        except Exception as e:
            logger.error(f"Get team composition error: {e}")
            raise e

    async def migrate_candidate_personality(
        self,
        candidate_id: str,
        staff_id: str,
        restaurant_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        When a candidate is hired via Stable Hire, copy their personality data
        to staff_personality_profiles with source='stable_hire'.
        Called during the hire-to-staff conversion flow.
        """
        try:
            candidate_result = self.supabase.table("hiring_candidates") \
                .select("scenario_rankings, fingerprint, stability_score") \
                .eq("id", candidate_id) \
                .eq("restaurant_id", restaurant_id) \
                .single() \
                .execute()

            if not candidate_result.data:
                logger.warning(f"No candidate found for personality migration: {candidate_id}")
                return None

            candidate = candidate_result.data
            rankings = candidate.get("scenario_rankings")
            fingerprint = candidate.get("fingerprint")

            if not rankings or not fingerprint:
                logger.info(f"Candidate {candidate_id} has no personality data to migrate")
                return None

            # Compute persona scores from the existing fingerprint
            from services.personality_scoring import compute_personas, get_primary_persona
            persona_scores = compute_personas(fingerprint)
            persona_primary = get_primary_persona(persona_scores)

            payload = {
                "staff_id": staff_id,
                "restaurant_id": restaurant_id,
                "scenario_rankings": rankings,
                "fingerprint": fingerprint,
                "persona_primary": persona_primary,
                "persona_scores": persona_scores,
                "stability_score": candidate.get("stability_score", 0),
                "source": "stable_hire",
                "points_awarded": 0,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

            result = self.supabase.table("staff_personality_profiles") \
                .upsert(payload, on_conflict="staff_id") \
                .execute()

            if result.data and len(result.data) > 0:
                logger.info(f"Migrated personality: candidate {candidate_id} → staff {staff_id}")
                return result.data[0]

            return None

        except Exception as e:
            logger.error(f"Migrate candidate personality error: {e}")
            return None