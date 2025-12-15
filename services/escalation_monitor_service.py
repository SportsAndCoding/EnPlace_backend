import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

class EscalationMonitorService:
    """
    Monitors active escalations and automatically:
    - Calculates mood trends for affected staff
    - Resolves escalations if situation improved
    - Advances escalations if situation worsened
    """
    
    # Configuration
    IMPROVEMENT_THRESHOLD_DAYS = 7  # Days of improvement before auto-resolve
    MOOD_IMPROVEMENT_DELTA = 0.5    # Mood must improve by this much (on 1-5 scale)
    MOOD_DECLINE_DELTA = -0.3       # Mood decline that triggers advancement
    
    def __init__(self):
        self.supabase = get_supabase()
    
    async def run_nightly_monitoring(self) -> Dict[str, Any]:
        """
        Main entry point for nightly job.
        Returns summary of actions taken.
        """
        logger.info("Starting nightly escalation monitoring...")
        
        results = {
            "processed": 0,
            "auto_resolved": 0,
            "auto_advanced": 0,
            "unchanged": 0,
            "errors": 0,
            "details": []
        }
        
        try:
            # Get all active escalations (not resolved)
            escalations = await self._get_active_escalations()
            results["processed"] = len(escalations)
            
            for escalation in escalations:
                try:
                    action = await self._process_escalation(escalation)
                    results["details"].append(action)
                    
                    if action["action"] == "resolved":
                        results["auto_resolved"] += 1
                    elif action["action"] == "advanced":
                        results["auto_advanced"] += 1
                    else:
                        results["unchanged"] += 1
                        
                except Exception as e:
                    logger.error(f"Error processing escalation {escalation['id']}: {e}")
                    results["errors"] += 1
                    results["details"].append({
                        "escalation_id": escalation["id"],
                        "action": "error",
                        "reason": str(e)
                    })
            
            logger.info(f"Nightly monitoring complete: {results}")
            return results
            
        except Exception as e:
            logger.error(f"Nightly monitoring failed: {e}")
            raise
    
    async def _get_active_escalations(self) -> List[Dict]:
        """Get all non-resolved escalations"""
        result = self.supabase.table("sse_escalation_events") \
            .select("*, primary_staff:primary_staff_id(full_name, position, staff_id)") \
            .neq("status", "resolved") \
            .execute()
        return result.data or []
    
    
    
    async def _process_escalation(self, escalation: Dict) -> Dict[str, Any]:
        """
        Process a single escalation:
        1. Calculate current mood for affected staff/role
        2. Compare to baseline
        3. Decide: resolve, advance, or continue monitoring
        4. Handle pending verification events
        """
        escalation_id = escalation["id"]
        restaurant_id = escalation["restaurant_id"]
        affected_role = escalation["affected_role"]
        primary_staff_id = escalation.get("primary_staff_id")
        source_type = escalation.get("source_type", "mood")
        
        # Calculate baseline if not set
        if escalation.get("baseline_mood") is None:
            baseline = await self._calculate_baseline_mood(
                restaurant_id, 
                affected_role, 
                primary_staff_id,
                escalation["triggered_at"]
            )
            await self._update_escalation_mood_data(escalation_id, baseline_mood=baseline)
            escalation["baseline_mood"] = baseline
        
        # Calculate current mood (last 7 days)
        current_mood = await self._calculate_current_mood(
            restaurant_id,
            affected_role,
            primary_staff_id
        )
        
        # Determine trend
        baseline = float(escalation.get("baseline_mood") or 3.0)
        delta = current_mood - baseline
        
        if delta >= self.MOOD_IMPROVEMENT_DELTA:
            trend = "improving"
        elif delta <= self.MOOD_DECLINE_DELTA:
            trend = "declining"
        else:
            trend = "stable"
        
        # Update escalation with current mood data
        await self._update_escalation_mood_data(
            escalation_id,
            current_mood=current_mood,
            mood_trend=trend
        )
        
        # Decision logic
        action_result = {
            "event_id": escalation_id,
            "affected_role": affected_role,
            "baseline_mood": baseline,
            "current_mood": current_mood,
            "delta": round(delta, 2),
            "trend": trend,
            "action": "unchanged",
            "reason": ""
        }
        
        # ═══════════════════════════════════════════════════════════════
        # CHECK FOR PENDING VERIFICATION (Manager requested close)
        # ═══════════════════════════════════════════════════════════════
        if escalation.get("resolution") == "pending_verification":
            monitoring_end = escalation.get("monitoring_end_date")
            if monitoring_end:
                try:
                    end_date = datetime.fromisoformat(monitoring_end.replace('Z', '+00:00'))
                    if datetime.now(timezone.utc) >= end_date:
                        # Monitoring period complete - evaluate
                        if trend == "improving" or (trend == "stable" and delta >= 0):
                            # Confirm resolution - mood improved or stable-positive
                            await self._confirm_resolution(escalation_id, escalation["current_step"])
                            action_result["action"] = "resolved"
                            action_result["reason"] = "Verification complete: improvement confirmed"
                        else:
                            # Reopen - mood didn't improve
                            await self._reopen_escalation(escalation_id, escalation["current_step"])
                            action_result["action"] = "reopened"
                            action_result["reason"] = "Verification failed: mood did not improve"
                        return action_result
                    else:
                        # Still in verification period
                        days_left = (end_date - datetime.now(timezone.utc)).days
                        action_result["reason"] = f"Pending verification ({days_left} days remaining)"
                        return action_result
                except Exception as e:
                    logger.error(f"Error parsing monitoring_end_date: {e}")
        
        # ═══════════════════════════════════════════════════════════════
        # CHECK FOR MONITORING PERIOD EXPIRY
        # ═══════════════════════════════════════════════════════════════
        if escalation["status"] == "monitoring" and escalation.get("monitoring_end_date"):
            try:
                end_date = datetime.fromisoformat(escalation["monitoring_end_date"].replace('Z', '+00:00'))
                if datetime.now(timezone.utc) >= end_date:
                    # Monitoring period ended - evaluate outcome
                    if trend == "improving":
                        await self._auto_resolve(escalation_id, escalation["current_step"])
                        action_result["action"] = "resolved"
                        action_result["reason"] = "Monitoring complete: situation improved"
                    elif trend == "declining":
                        await self._auto_advance(escalation_id, escalation["current_step"])
                        action_result["action"] = "advanced"
                        action_result["reason"] = "Monitoring complete: situation worsened, escalating"
                    else:
                        # Stable - extend monitoring or return to active
                        action_result["reason"] = "Monitoring complete: stable, continuing observation"
                    return action_result
            except Exception as e:
                logger.error(f"Error parsing monitoring_end_date: {e}")
        
        # ═══════════════════════════════════════════════════════════════
        # STANDARD PROCESSING (Not in verification/monitoring period)
        # ═══════════════════════════════════════════════════════════════
        
        # Check for improvement-based resolution
        if trend == "improving":
            days_improving = await self._count_improvement_days(
                restaurant_id, affected_role, primary_staff_id, baseline
            )
            
            if days_improving >= self.IMPROVEMENT_THRESHOLD_DAYS:
                await self._auto_resolve(escalation_id, escalation["current_step"])
                action_result["action"] = "resolved"
                action_result["reason"] = f"Mood improved for {days_improving} consecutive days"
            else:
                action_result["reason"] = f"Improving ({days_improving}/{self.IMPROVEMENT_THRESHOLD_DAYS} days)"
        
        # Check for decline-based advancement
        elif trend == "declining" and escalation["status"] != "monitoring":
            current_step = escalation["current_step"]
            if current_step < 7:  # Don't advance past step 7
                await self._auto_advance(escalation_id, current_step)
                action_result["action"] = "advanced"
                action_result["reason"] = f"Mood declining, advanced from step {current_step} to {current_step + 1}"
            else:
                action_result["reason"] = "Declining but already at step 7"
        
        else:
            action_result["reason"] = f"Stable (delta: {round(delta, 2)})"
        
        return action_result
    async def _calculate_baseline_mood(
        self,
        restaurant_id: int,
        affected_role: str,
        primary_staff_id: Optional[str],
        triggered_at: str
    ) -> float:
        """
        Calculate baseline mood at time of escalation trigger.
        Uses 7 days before trigger date.
        """
        trigger_date = datetime.fromisoformat(triggered_at.replace('Z', '+00:00'))
        start_date = (trigger_date - timedelta(days=14)).date()
        end_date = (trigger_date - timedelta(days=7)).date()
        
        return await self._get_average_mood(
            restaurant_id, affected_role, primary_staff_id, start_date, end_date
        )
    
    async def _calculate_current_mood(
        self,
        restaurant_id: int,
        affected_role: str,
        primary_staff_id: Optional[str]
    ) -> float:
        """Calculate average mood for last 7 days"""
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=7)
        
        return await self._get_average_mood(
            restaurant_id, affected_role, primary_staff_id, start_date, end_date
        )
    
    async def _get_average_mood(
        self,
        restaurant_id: int,
        affected_role: str,
        primary_staff_id: Optional[str],
        start_date,
        end_date
    ) -> float:
        """Get average mood from check-ins"""
        try:
            # Build query
            query = self.supabase.table("sse_daily_checkins") \
                .select("mood_emoji, staff:staff_id(position)") \
                .eq("restaurant_id", restaurant_id) \
                .gte("checkin_date", start_date.isoformat()) \
                .lte("checkin_date", end_date.isoformat())
            
            result = query.execute()
            checkins = result.data or []
            
            # Filter by role or specific staff
            if primary_staff_id:
                # For specific staff, would need to filter by staff_id
                # For now, filter by role
                pass
            
            if affected_role:
                checkins = [c for c in checkins if c.get("staff", {}).get("position") == affected_role]
            
            if not checkins:
                return 3.0  # Neutral if no data
            
            moods = [c["mood_emoji"] for c in checkins if c.get("mood_emoji")]
            if not moods:
                return 3.0
            
            return sum(moods) / len(moods)
            
        except Exception as e:
            logger.error(f"Get average mood error: {e}")
            return 3.0
    
    async def _count_improvement_days(
        self,
        restaurant_id: int,
        affected_role: str,
        primary_staff_id: Optional[str],
        baseline: float
    ) -> int:
        """Count consecutive days where mood was above baseline"""
        try:
            end_date = datetime.now(timezone.utc).date()
            start_date = end_date - timedelta(days=14)
            
            query = self.supabase.table("sse_daily_checkins") \
                .select("checkin_date, mood_emoji, staff:staff_id(position)") \
                .eq("restaurant_id", restaurant_id) \
                .gte("checkin_date", start_date.isoformat()) \
                .lte("checkin_date", end_date.isoformat()) \
                .order("checkin_date", desc=True)
            
            result = query.execute()
            checkins = result.data or []
            
            # Filter by role
            if affected_role:
                checkins = [c for c in checkins if c.get("staff", {}).get("position") == affected_role]
            
            # Group by date
            by_date = {}
            for c in checkins:
                d = c["checkin_date"]
                if d not in by_date:
                    by_date[d] = []
                if c.get("mood_emoji"):
                    by_date[d].append(c["mood_emoji"])
            
            # Count consecutive improvement days (from most recent)
            consecutive_days = 0
            for date in sorted(by_date.keys(), reverse=True):
                moods = by_date[date]
                if not moods:
                    continue
                avg = sum(moods) / len(moods)
                if avg > baseline:
                    consecutive_days += 1
                else:
                    break  # Streak broken
            
            return consecutive_days
            
        except Exception as e:
            logger.error(f"Count improvement days error: {e}")
            return 0
    
    async def _update_escalation_mood_data(
        self,
        escalation_id: str,
        baseline_mood: float = None,
        current_mood: float = None,
        mood_trend: str = None
    ):
        """Update mood tracking fields on escalation"""
        update_data = {"updated_at": datetime.now(timezone.utc).isoformat()}
        
        if baseline_mood is not None:
            update_data["baseline_mood"] = round(baseline_mood, 2)
        if current_mood is not None:
            update_data["current_mood"] = round(current_mood, 2)
        if mood_trend is not None:
            update_data["mood_trend"] = mood_trend
        
        self.supabase.table("sse_escalation_events") \
            .update(update_data) \
            .eq("id", escalation_id) \
            .execute()
    
    async def _auto_resolve(self, escalation_id: str, current_step: int):
        """Auto-resolve an escalation due to improvement"""
        now = datetime.now(timezone.utc).isoformat()
        
        # Update status
        self.supabase.table("sse_escalation_events") \
            .update({
                "status": "resolved",
                "resolution": "retained",
                "resolved_at": now,
                "updated_at": now
            }) \
            .eq("id", escalation_id) \
            .execute()
        
        # Add history entry
        self.supabase.table("sse_escalation_history").insert({
            "event_id": escalation_id,
            "step_number": current_step,
            "action_taken": "Auto-resolved: Mood improved consistently for 7+ days",
            "actor_type": "system",
            "actor_id": "SYSTEM",
            "completed_at": now
        }).execute()
        
        logger.info(f"Auto-resolved escalation {escalation_id}")
    
    async def _auto_advance(self, escalation_id: str, current_step: int):
        """Auto-advance escalation due to decline"""
        now = datetime.now(timezone.utc).isoformat()
        new_step = min(current_step + 1, 7)
        new_status = "actionable"
        
        # Update step
        self.supabase.table("sse_escalation_events") \
            .update({
                "current_step": new_step,
                "status": new_status,
                "updated_at": now
            }) \
            .eq("id", escalation_id) \
            .execute()
        
        # Add history entry
        self.supabase.table("sse_escalation_history").insert({
            "event_id": escalation_id,
            "step_number": new_step,
            "action_taken": f"Auto-advanced: Mood declining despite intervention at step {current_step}",
            "actor_type": "system",
            "actor_id": "SYSTEM",
            "completed_at": now
        }).execute()
        
        logger.info(f"Auto-advanced escalation {escalation_id} to step {new_step}")
    async def _confirm_resolution(self, escalation_id: str, current_step: int):
        """Confirm a pending verification resolution - manager's close was validated"""
        now = datetime.now(timezone.utc).isoformat()
        
        self.supabase.table("sse_escalation_events") \
            .update({
                "status": "resolved",
                "resolution": "retained",
                "resolved_at": now,
                "monitoring_end_date": None,
                "updated_at": now
            }) \
            .eq("id", escalation_id) \
            .execute()
        
        self.supabase.table("sse_escalation_history").insert({
            "event_id": escalation_id,
            "step_number": current_step,
            "action_taken": "Resolution verified: Mood improvement confirmed by system after 7-day monitoring period",
            "actor_type": "system",
            "actor_id": "SYSTEM",
            "completed_at": now
        }).execute()
        
        logger.info(f"Confirmed resolution for escalation {escalation_id}")

    async def _reopen_escalation(self, escalation_id: str, current_step: int):
        """Reopen an escalation that failed verification - manager's close was not validated"""
        now = datetime.now(timezone.utc).isoformat()
        
        self.supabase.table("sse_escalation_events") \
            .update({
                "status": "actionable",
                "resolution": None,
                "monitoring_end_date": None,
                "updated_at": now
            }) \
            .eq("id", escalation_id) \
            .execute()
        
        self.supabase.table("sse_escalation_history").insert({
            "event_id": escalation_id,
            "step_number": current_step,
            "action_taken": "Event reopened: Verification failed - mood data did not support resolution during 7-day monitoring period",
            "actor_type": "system",
            "actor_id": "SYSTEM",
            "completed_at": now
        }).execute()
        
        logger.info(f"Reopened escalation {escalation_id} - verification failed")

