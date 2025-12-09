import logging
from datetime import date, timedelta
from typing import Dict, Any, List
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

class AlignmentService:
    def __init__(self):
        self.supabase = get_supabase()
    
    async def get_alignment_data(
        self, 
        restaurant_id: int, 
        days: int = 7
    ) -> Dict[str, Any]:
        """
        Calculate Staff-Manager Alignment scores.
        
        Compares:
        - Staff mood check-ins (how staff felt)
        - Manager daily logs (how manager perceived the day)
        
        Returns alignment scores and perception gaps.
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        # Get staff check-ins
        checkins = await self._get_checkins(restaurant_id, start_date, end_date)
        
        # Get manager logs
        manager_logs = await self._get_manager_logs(restaurant_id, start_date, end_date)
        
        # Get staff by role for role cluster analysis
        staff_by_role = await self._get_staff_by_role(restaurant_id)
        
        # Calculate scores
        emotional_alignment = self._calculate_emotional_alignment(checkins)
        operational_alignment = self._calculate_operational_alignment(checkins, manager_logs)
        perception_gaps = self._calculate_perception_gaps(checkins, manager_logs)
        role_cluster_risk = self._calculate_role_cluster_risk(checkins, staff_by_role)
        
        # Overall SMA score (weighted average)
        sma_score = round(
            (emotional_alignment * 0.4) + 
            (operational_alignment * 0.4) + 
            (100 - self._calculate_gap_penalty(perception_gaps)) * 0.2
        )
        
        return {
            "sma_score": sma_score,
            "emotional_alignment": emotional_alignment,
            "operational_alignment": operational_alignment,
            "perception_gaps": perception_gaps,
            "role_cluster_risk": role_cluster_risk,
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "days": days
            },
            "data_quality": {
                "checkin_count": len(checkins),
                "manager_log_count": len(manager_logs),
                "has_sufficient_data": len(checkins) >= 5 and len(manager_logs) >= 3
            }
        }
    
    async def _get_checkins(
        self, 
        restaurant_id: int, 
        start_date: date, 
        end_date: date
    ) -> List[Dict]:
        """Get all staff check-ins for the period"""
        try:
            result = self.supabase.table("sse_daily_checkins") \
                .select("*, staff:staff_id(full_name, position)") \
                .eq("restaurant_id", restaurant_id) \
                .gte("checkin_date", start_date.isoformat()) \
                .lte("checkin_date", end_date.isoformat()) \
                .execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Get checkins error: {e}")
            return []
    
    async def _get_manager_logs(
        self, 
        restaurant_id: int, 
        start_date: date, 
        end_date: date
    ) -> List[Dict]:
        """Get all manager logs for the period"""
        try:
            result = self.supabase.table("manager_daily_logs") \
                .select("*") \
                .eq("restaurant_id", restaurant_id) \
                .gte("log_date", start_date.isoformat()) \
                .lte("log_date", end_date.isoformat()) \
                .execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Get manager logs error: {e}")
            return []
    
    async def _get_staff_by_role(self, restaurant_id: int) -> Dict[str, List[str]]:
        """Get staff grouped by position"""
        try:
            result = self.supabase.table("staff") \
                .select("staff_id, position") \
                .eq("restaurant_id", restaurant_id) \
                .eq("status", "Active") \
                .execute()
            
            by_role = {}
            for staff in (result.data or []):
                role = staff.get("position", "Other")
                if role not in by_role:
                    by_role[role] = []
                by_role[role].append(staff["staff_id"])
            
            return by_role
        except Exception as e:
            logger.error(f"Get staff by role error: {e}")
            return {}
    
    def _calculate_emotional_alignment(self, checkins: List[Dict]) -> int:
        """
        Calculate emotional alignment score.
        Based on how positive/negative staff felt overall.
        """
        if not checkins:
            return 50  # Neutral if no data
        
        # Average mood (1-5 scale)
        moods = [c["mood_emoji"] for c in checkins if c.get("mood_emoji")]
        if not moods:
            return 50
        
        avg_mood = sum(moods) / len(moods)
        
        # Convert 1-5 to 0-100 scale
        # 1 = 0, 3 = 50, 5 = 100
        score = round((avg_mood - 1) * 25)
        
        # Factor in felt_safe, felt_fair, felt_respected
        safety_scores = [c for c in checkins if c.get("felt_safe") is not None]
        fair_scores = [c for c in checkins if c.get("felt_fair") is not None]
        respect_scores = [c for c in checkins if c.get("felt_respected") is not None]
        
        adjustments = 0
        count = 0
        
        if safety_scores:
            safety_pct = sum(1 for c in safety_scores if c["felt_safe"]) / len(safety_scores)
            adjustments += (safety_pct - 0.5) * 20
            count += 1
        
        if fair_scores:
            fair_pct = sum(1 for c in fair_scores if c["felt_fair"]) / len(fair_scores)
            adjustments += (fair_pct - 0.5) * 20
            count += 1
        
        if respect_scores:
            respect_pct = sum(1 for c in respect_scores if c["felt_respected"]) / len(respect_scores)
            adjustments += (respect_pct - 0.5) * 20
            count += 1
        
        if count > 0:
            score += round(adjustments / count)
        
        return max(0, min(100, score))
    
    def _calculate_operational_alignment(
        self, 
        checkins: List[Dict], 
        manager_logs: List[Dict]
    ) -> int:
        """
        Calculate how closely manager and staff saw the same days.
        Compares manager's overall_rating with staff mood for same dates.
        """
        if not checkins or not manager_logs:
            return 50  # Neutral if insufficient data
        
        # Group checkins by date
        checkins_by_date = {}
        for c in checkins:
            d = c["checkin_date"]
            if d not in checkins_by_date:
                checkins_by_date[d] = []
            checkins_by_date[d].append(c["mood_emoji"])
        
        # Manager logs by date
        manager_by_date = {log["log_date"]: log["overall_rating"] for log in manager_logs}
        
        # Calculate alignment for days with both data
        alignments = []
        for log_date, manager_rating in manager_by_date.items():
            if log_date in checkins_by_date:
                staff_moods = checkins_by_date[log_date]
                avg_staff_mood = sum(staff_moods) / len(staff_moods)
                
                # Difference on 1-5 scale, converted to alignment percentage
                # 0 difference = 100% aligned, 4 difference = 0% aligned
                diff = abs(manager_rating - avg_staff_mood)
                alignment = 100 - (diff * 25)
                alignments.append(alignment)
        
        if not alignments:
            return 50
        
        return round(sum(alignments) / len(alignments))
    
    def _calculate_perception_gaps(
        self, 
        checkins: List[Dict], 
        manager_logs: List[Dict]
    ) -> List[Dict]:
        """
        Identify specific days where staff and manager perceptions differed.
        Returns list of gaps with severity.
        """
        gaps = []
        
        # Group checkins by date
        checkins_by_date = {}
        for c in checkins:
            d = c["checkin_date"]
            if d not in checkins_by_date:
                checkins_by_date[d] = []
            checkins_by_date[d].append(c)
        
        # Manager logs by date
        manager_by_date = {log["log_date"]: log for log in manager_logs}
        
        for log_date, manager_log in manager_by_date.items():
            if log_date in checkins_by_date:
                staff_checkins = checkins_by_date[log_date]
                avg_mood = sum(c["mood_emoji"] for c in staff_checkins) / len(staff_checkins)
                manager_rating = manager_log["overall_rating"]
                
                diff = manager_rating - avg_mood
                
                # Determine perception labels
                staff_saw = self._mood_to_label(avg_mood)
                manager_saw = self._mood_to_label(manager_rating)
                
                # Only flag if there's meaningful difference
                if abs(diff) >= 1:
                    gap_level = "high" if abs(diff) >= 2 else "medium"
                    
                    # Get day of week
                    from datetime import datetime
                    day_name = datetime.fromisoformat(log_date).strftime("%a")
                    
                    gaps.append({
                        "date": log_date,
                        "day": day_name,
                        "staff_saw": staff_saw,
                        "manager_saw": manager_saw,
                        "gap": gap_level,
                        "staff_avg_mood": round(avg_mood, 1),
                        "manager_rating": manager_rating
                    })
        
        return gaps
    
    def _mood_to_label(self, mood: float) -> str:
        """Convert mood score to human label"""
        if mood >= 4.5:
            return "Excellent"
        elif mood >= 3.5:
            return "Smooth"
        elif mood >= 2.5:
            return "Normal"
        elif mood >= 1.5:
            return "Rough"
        else:
            return "Chaotic"
    
    def _calculate_role_cluster_risk(
        self, 
        checkins: List[Dict], 
        staff_by_role: Dict[str, List[str]]
    ) -> List[Dict]:
        """
        Calculate risk scores by role/position.
        Identifies which teams are struggling.
        """
        role_risks = []
        
        for role, staff_ids in staff_by_role.items():
            # Get checkins for this role's staff
            role_checkins = [c for c in checkins if c["staff_id"] in staff_ids]
            
            if not role_checkins:
                continue
            
            # Calculate average mood for this role
            moods = [c["mood_emoji"] for c in role_checkins if c.get("mood_emoji")]
            if not moods:
                continue
            
            avg_mood = sum(moods) / len(moods)
            
            # Convert to risk score (inverse of mood)
            # High mood = low risk, Low mood = high risk
            # 5 mood = 20 risk, 1 mood = 100 risk
            risk_score = round(100 - ((avg_mood - 1) * 20))
            
            # Determine status
            if risk_score >= 70:
                status = "critical"
            elif risk_score >= 50:
                status = "elevated"
            elif risk_score >= 30:
                status = "watch"
            else:
                status = "healthy"
            
            role_risks.append({
                "role": role,
                "score": risk_score,
                "status": status,
                "staff_count": len(staff_ids),
                "checkin_count": len(role_checkins),
                "avg_mood": round(avg_mood, 1)
            })
        
        # Sort by risk score descending
        role_risks.sort(key=lambda x: x["score"], reverse=True)
        
        return role_risks
    
    def _calculate_gap_penalty(self, gaps: List[Dict]) -> int:
        """Calculate penalty score based on perception gaps"""
        if not gaps:
            return 0
        
        penalty = 0
        for gap in gaps:
            if gap["gap"] == "high":
                penalty += 15
            elif gap["gap"] == "medium":
                penalty += 8
        
        return min(50, penalty)  # Cap at 50