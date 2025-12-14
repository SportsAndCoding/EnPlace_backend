import logging
from datetime import date, timedelta
from typing import Dict, Any, List, Optional, Tuple
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
        Calculate Staff-Manager Alignment scores with trends and drivers.
        
        Returns comprehensive alignment data including:
        - Overall SMA score with trend
        - Emotional alignment with drivers
        - Operational alignment with gaps
        - Role cluster risk analysis
        - Network percentile comparison
        - Fairness index
        """
        # Current period
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        # Previous period (for trend comparison)
        prev_end_date = start_date - timedelta(days=1)
        prev_start_date = prev_end_date - timedelta(days=days)
        
        # Get current period data
        checkins = await self._get_checkins(restaurant_id, start_date, end_date)
        manager_logs = await self._get_manager_logs(restaurant_id, start_date, end_date)
        staff_by_role = await self._get_staff_by_role(restaurant_id)
        
        # Get previous period data (for trends)
        prev_checkins = await self._get_checkins(restaurant_id, prev_start_date, prev_end_date)
        prev_manager_logs = await self._get_manager_logs(restaurant_id, prev_start_date, prev_end_date)
        
        # Calculate current scores
        emotional_score = self._calculate_emotional_alignment(checkins)
        operational_score = self._calculate_operational_alignment(checkins, manager_logs)
        perception_gaps = self._calculate_perception_gaps(checkins, manager_logs)
        role_cluster_risk = self._calculate_role_cluster_risk(checkins, staff_by_role)
        fairness_score = self._calculate_fairness_score(checkins, perception_gaps, role_cluster_risk)
        
        # Calculate previous scores (for trends)
        prev_emotional = self._calculate_emotional_alignment(prev_checkins)
        prev_operational = self._calculate_operational_alignment(prev_checkins, prev_manager_logs)
        prev_gaps = self._calculate_perception_gaps(prev_checkins, prev_manager_logs)
        prev_role_risk = self._calculate_role_cluster_risk(prev_checkins, staff_by_role)
        prev_fairness = self._calculate_fairness_score(prev_checkins, prev_gaps, prev_role_risk)
        
        # Overall SMA score (weighted average)
        gap_penalty = self._calculate_gap_penalty(perception_gaps)
        sma_score = round(
            (emotional_score * 0.4) + 
            (operational_score * 0.4) + 
            (100 - gap_penalty) * 0.2
        )
        
        prev_gap_penalty = self._calculate_gap_penalty(prev_gaps)
        prev_sma_score = round(
            (prev_emotional * 0.4) + 
            (prev_operational * 0.4) + 
            (100 - prev_gap_penalty) * 0.2
        )
        
        # Calculate network percentile
        network_data = await self._calculate_network_percentile(sma_score, emotional_score)
        
        # Generate drivers from check-in patterns
        drivers = self._generate_emotional_drivers(checkins, role_cluster_risk, perception_gaps)
        
        # Find perfect alignment days
        perfect_days = self._find_perfect_alignment_days(checkins, manager_logs)
        
        # Generate fairness complaints from patterns
        fairness_complaints = self._generate_fairness_complaints(checkins, role_cluster_risk, perception_gaps)
        
        return {
            "success": True,
            "sma_score": sma_score,
            "sma_trend": self._calculate_trend(sma_score, prev_sma_score),
            
            "emotional_alignment": {
                "score": emotional_score,
                "trend": self._calculate_trend(emotional_score, prev_emotional),
                "drivers": drivers,
                "network_percentile": network_data["emotional_percentile"],
                "network_comparison": network_data["emotional_comparison"]
            },
            
            "operational_alignment": {
                "score": operational_score,
                "trend": self._calculate_trend(operational_score, prev_operational),
                "perfect_days": perfect_days,
                "gaps": [g for g in perception_gaps if g["gap"] in ["high", "medium"]]
            },
            
            "role_cluster_risk": role_cluster_risk,
            
            "fairness_index": {
                "score": fairness_score,
                "trend": self._calculate_trend(fairness_score, prev_fairness),
                "complaints": fairness_complaints
            },
            
            "perception_gaps": perception_gaps,
            
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "days": days
            },
            
            "data_quality": {
                "checkin_count": len(checkins),
                "manager_log_count": len(manager_logs),
                "prev_checkin_count": len(prev_checkins),
                "has_sufficient_data": len(checkins) >= 5 and len(manager_logs) >= 3,
                "has_trend_data": len(prev_checkins) >= 3
            }
        }
    
    # ═══════════════════════════════════════════════════════════════════
    # DATA FETCHING
    # ═══════════════════════════════════════════════════════════════════
    
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
    
    # ═══════════════════════════════════════════════════════════════════
    # NETWORK PERCENTILE CALCULATION
    # ═══════════════════════════════════════════════════════════════════
    
    async def _calculate_network_percentile(
        self, 
        sma_score: int, 
        emotional_score: int
    ) -> Dict[str, Any]:
        """
        Calculate where this restaurant ranks in the REx network.
        Uses synthetic_restaurants data for comparison.
        """
        try:
            # Get all synthetic restaurant scores
            # We'll use the stability_score as a proxy for SMA (in production, 
            # you'd have actual SMA scores stored)
            result = self.supabase.table("synthetic_restaurants") \
                .select("id, stability_score") \
                .execute()
            
            restaurants = result.data or []
            
            if len(restaurants) < 10:
                # Not enough network data
                return {
                    "sma_percentile": 50,
                    "sma_comparison": "insufficient network data",
                    "emotional_percentile": 50,
                    "emotional_comparison": "insufficient network data"
                }
            
            # Extract scores
            network_scores = [r["stability_score"] for r in restaurants if r.get("stability_score")]
            
            if not network_scores:
                return {
                    "sma_percentile": 50,
                    "sma_comparison": "no comparison data available",
                    "emotional_percentile": 50,
                    "emotional_comparison": "no comparison data available"
                }
            
            # Calculate SMA percentile
            sma_percentile = self._percentile_rank(sma_score, network_scores)
            
            # Calculate emotional percentile (use a slightly different distribution)
            # In production, you'd have actual emotional scores stored
            emotional_percentile = self._percentile_rank(emotional_score, network_scores)
            
            # Generate comparison text
            avg_score = sum(network_scores) / len(network_scores)
            
            sma_diff = sma_score - avg_score
            if sma_diff > 5:
                sma_comparison = f"{abs(round(sma_diff))} points above network average"
            elif sma_diff < -5:
                sma_comparison = f"{abs(round(sma_diff))} points below network average"
            else:
                sma_comparison = "at network average"
            
            emotional_diff = emotional_score - avg_score
            if emotional_diff > 5:
                emotional_comparison = f"{abs(round(emotional_diff))} points above similar concepts"
            elif emotional_diff < -5:
                emotional_comparison = f"{abs(round(emotional_diff))} points below similar concepts"
            else:
                emotional_comparison = "aligned with similar concepts"
            
            return {
                "sma_percentile": sma_percentile,
                "sma_comparison": sma_comparison,
                "emotional_percentile": emotional_percentile,
                "emotional_comparison": emotional_comparison,
                "network_size": len(network_scores),
                "network_average": round(avg_score)
            }
            
        except Exception as e:
            logger.error(f"Network percentile calculation error: {e}")
            return {
                "sma_percentile": 50,
                "sma_comparison": "calculation error",
                "emotional_percentile": 50,
                "emotional_comparison": "calculation error"
            }
    
    def _percentile_rank(self, score: int, all_scores: List[int]) -> int:
        """Calculate percentile rank of a score within a distribution"""
        if not all_scores:
            return 50
        
        below = sum(1 for s in all_scores if s < score)
        equal = sum(1 for s in all_scores if s == score)
        
        percentile = ((below + 0.5 * equal) / len(all_scores)) * 100
        return min(99, max(1, round(percentile)))
    
    # ═══════════════════════════════════════════════════════════════════
    # DRIVER GENERATION
    # ═══════════════════════════════════════════════════════════════════
    
    def _generate_emotional_drivers(
        self,
        checkins: List[Dict],
        role_risks: List[Dict],
        perception_gaps: List[Dict]
    ) -> List[Dict]:
        """
        Generate specific emotional drivers from check-in patterns.
        Analyzes actual data to find what's driving alignment/misalignment.
        """
        drivers = []
        
        if not checkins:
            return [{"icon": "📊", "role": None, "feeling": "No check-in data", "context": "for this period"}]
        
        # 1. Analyze role-based patterns
        critical_roles = [r for r in role_risks if r["status"] == "critical"]
        if critical_roles:
            worst_role = critical_roles[0]
            drivers.append({
                "icon": "🔥",
                "role": worst_role["role"],
                "feeling": "struggling",
                "context": f"averaging {worst_role.get('avg_mood', 2.5)}/5 mood across {worst_role.get('checkin_count', 0)} check-ins"
            })
        
        # 2. Analyze felt_safe, felt_fair, felt_respected patterns
        safety_issues = [c for c in checkins if c.get("felt_safe") == False]
        fair_issues = [c for c in checkins if c.get("felt_fair") == False]
        respect_issues = [c for c in checkins if c.get("felt_respected") == False]
        
        if len(fair_issues) >= 3:
            # Group by role if possible
            fair_roles = {}
            for c in fair_issues:
                role = c.get("staff", {}).get("position", "Staff")
                fair_roles[role] = fair_roles.get(role, 0) + 1
            
            if fair_roles:
                top_role = max(fair_roles.items(), key=lambda x: x[1])
                drivers.append({
                    "icon": "⚖️",
                    "role": top_role[0] if top_role[1] >= 2 else None,
                    "feeling": "unfairly treated",
                    "context": f"reported in {len(fair_issues)} check-ins this period"
                })
        
        if len(safety_issues) >= 2:
            drivers.append({
                "icon": "🛡️",
                "role": None,
                "feeling": "unsafe",
                "context": f"flagged in {len(safety_issues)} check-ins"
            })
        
        if len(respect_issues) >= 2:
            drivers.append({
                "icon": "💬",
                "role": None,
                "feeling": "not respected",
                "context": f"indicated in {len(respect_issues)} check-ins"
            })
        
        # 3. Analyze perception gaps
        high_gaps = [g for g in perception_gaps if g["gap"] == "high"]
        if high_gaps:
            gap_days = [g["day"] for g in high_gaps]
            drivers.append({
                "icon": "📊",
                "role": None,
                "feeling": "perception mismatch",
                "context": f"on {', '.join(gap_days)} — staff and management saw different days"
            })
        
        # 4. Analyze mood trends within period
        mood_by_date = {}
        for c in checkins:
            d = c["checkin_date"]
            if d not in mood_by_date:
                mood_by_date[d] = []
            if c.get("mood_emoji"):
                mood_by_date[d].append(c["mood_emoji"])
        
        if len(mood_by_date) >= 3:
            daily_avgs = [(d, sum(m)/len(m)) for d, m in sorted(mood_by_date.items()) if m]
            if len(daily_avgs) >= 3:
                # Check for declining trend
                first_half_avg = sum(a[1] for a in daily_avgs[:len(daily_avgs)//2]) / (len(daily_avgs)//2)
                second_half_avg = sum(a[1] for a in daily_avgs[len(daily_avgs)//2:]) / (len(daily_avgs) - len(daily_avgs)//2)
                
                if second_half_avg < first_half_avg - 0.5:
                    drivers.append({
                        "icon": "📉",
                        "role": None,
                        "feeling": "declining mood",
                        "context": "trend detected over the past week"
                    })
                elif second_half_avg > first_half_avg + 0.5:
                    drivers.append({
                        "icon": "📈",
                        "role": None,
                        "feeling": "improving mood",
                        "context": "positive trend this week"
                    })
        
        # 5. If no issues found, report positive
        if not drivers:
            drivers.append({
                "icon": "✓",
                "role": None,
                "feeling": "stable and aligned",
                "context": "no major emotional drivers detected"
            })
        
        # Limit to top 4 drivers
        return drivers[:4]
    
    # ═══════════════════════════════════════════════════════════════════
    # TREND CALCULATION
    # ═══════════════════════════════════════════════════════════════════
    
    def _calculate_trend(self, current: int, previous: int) -> Dict[str, Any]:
        """Calculate trend direction and delta from two periods"""
        if previous == 0 or previous is None:
            return {"direction": "neutral", "delta": 0}
        
        delta = current - previous
        
        if delta > 2:
            return {"direction": "up", "delta": abs(delta)}
        elif delta < -2:
            return {"direction": "down", "delta": abs(delta)}
        else:
            return {"direction": "neutral", "delta": 0}
    
    # ═══════════════════════════════════════════════════════════════════
    # PERFECT ALIGNMENT DAYS
    # ═══════════════════════════════════════════════════════════════════
    
    def _find_perfect_alignment_days(
        self,
        checkins: List[Dict],
        manager_logs: List[Dict]
    ) -> List[str]:
        """Find days where staff and manager perceptions matched"""
        from datetime import datetime
        
        if not checkins or not manager_logs:
            return []
        
        # Group checkins by date
        checkins_by_date = {}
        for c in checkins:
            d = c["checkin_date"]
            if d not in checkins_by_date:
                checkins_by_date[d] = []
            if c.get("mood_emoji"):
                checkins_by_date[d].append(c["mood_emoji"])
        
        # Manager logs by date
        manager_by_date = {log["log_date"]: log["overall_rating"] for log in manager_logs}
        
        perfect_days = []
        
        for log_date, manager_rating in manager_by_date.items():
            if log_date in checkins_by_date and checkins_by_date[log_date]:
                staff_moods = checkins_by_date[log_date]
                avg_staff_mood = sum(staff_moods) / len(staff_moods)
                
                # Perfect alignment if difference < 0.5
                diff = abs(manager_rating - avg_staff_mood)
                if diff < 0.75:
                    try:
                        day_name = datetime.fromisoformat(log_date).strftime("%a")
                        perfect_days.append(day_name)
                    except:
                        pass
        
        return perfect_days
    
    # ═══════════════════════════════════════════════════════════════════
    # FAIRNESS CALCULATIONS
    # ═══════════════════════════════════════════════════════════════════
    
    def _calculate_fairness_score(
        self,
        checkins: List[Dict],
        perception_gaps: List[Dict],
        role_risks: List[Dict]
    ) -> int:
        """Calculate fairness index based on check-in patterns"""
        base_score = 75
        
        if not checkins:
            return base_score
        
        # Penalize for felt_fair = False
        fair_responses = [c for c in checkins if c.get("felt_fair") is not None]
        if fair_responses:
            unfair_count = sum(1 for c in fair_responses if not c["felt_fair"])
            unfair_pct = unfair_count / len(fair_responses)
            base_score -= round(unfair_pct * 30)  # Up to -30 for 100% unfair
        
        # Penalize for high perception gaps
        high_gaps = [g for g in perception_gaps if g["gap"] == "high"]
        medium_gaps = [g for g in perception_gaps if g["gap"] == "medium"]
        base_score -= len(high_gaps) * 8
        base_score -= len(medium_gaps) * 4
        
        # Penalize for critical roles
        critical_count = sum(1 for r in role_risks if r["status"] == "critical")
        base_score -= critical_count * 5
        
        return max(30, min(95, base_score))
    
    def _generate_fairness_complaints(
        self,
        checkins: List[Dict],
        role_risks: List[Dict],
        perception_gaps: List[Dict]
    ) -> List[Dict]:
        """Generate fairness complaints from patterns in the data"""
        complaints = []
        
        # 1. Check felt_fair patterns
        unfair_checkins = [c for c in checkins if c.get("felt_fair") == False]
        if unfair_checkins:
            # Group by role
            unfair_by_role = {}
            for c in unfair_checkins:
                role = c.get("staff", {}).get("position", "Staff")
                unfair_by_role[role] = unfair_by_role.get(role, 0) + 1
            
            if unfair_by_role:
                top_role = max(unfair_by_role.items(), key=lambda x: x[1])
                if top_role[1] >= 2:
                    complaints.append({
                        "quote": f"{top_role[0]} team reporting unfair treatment",
                        "category": "role_fairness",
                        "count": top_role[1]
                    })
        
        # 2. Check for perception gaps indicating schedule issues
        high_gaps = [g for g in perception_gaps if g["gap"] == "high"]
        if high_gaps:
            # Look for patterns like "staff saw understaffed"
            understaffed = [g for g in high_gaps if "understaff" in g.get("staff_saw", "").lower() or 
                          g.get("staff_saw", "") in ["Chaotic", "Rough"]]
            if understaffed:
                complaints.append({
                    "quote": "Staffing didn't match the reality on the floor",
                    "category": "staffing",
                    "count": len(understaffed)
                })
        
        # 3. Check for critical roles indicating overwork
        critical_roles = [r for r in role_risks if r["status"] == "critical"]
        for role in critical_roles[:2]:  # Top 2 critical
            if role.get("avg_mood", 5) < 2.5:
                complaints.append({
                    "quote": f"{role['role']} feeling overworked and underappreciated",
                    "category": "workload",
                    "count": role.get("checkin_count", 0)
                })
        
        # 4. Generic complaints if we found felt_fair issues but no specific pattern
        total_checkins = len(checkins)
        unfair_count = len(unfair_checkins)
        if unfair_count >= 3 and unfair_count / max(total_checkins, 1) > 0.2:
            if not any(c["category"] == "role_fairness" for c in complaints):
                complaints.append({
                    "quote": "Schedule changes and shift assignments feel uneven",
                    "category": "scheduling",
                    "count": unfair_count
                })
        
        # 5. If no complaints, add positive message
        if not complaints:
            complaints.append({
                "quote": "No major fairness concerns this period",
                "category": "positive",
                "count": 0
            })
        
        # Limit and format
        return [{"quote": c["quote"]} for c in complaints[:3]]
    
    # ═══════════════════════════════════════════════════════════════════
    # CORE CALCULATIONS (existing, slightly enhanced)
    # ═══════════════════════════════════════════════════════════════════
    
    def _calculate_emotional_alignment(self, checkins: List[Dict]) -> int:
        """Calculate emotional alignment score from check-ins"""
        if not checkins:
            return 50
        
        moods = [c["mood_emoji"] for c in checkins if c.get("mood_emoji")]
        if not moods:
            return 50
        
        avg_mood = sum(moods) / len(moods)
        score = round((avg_mood - 1) * 25)
        
        # Factor in felt_safe, felt_fair, felt_respected
        adjustments = 0
        count = 0
        
        for field in ["felt_safe", "felt_fair", "felt_respected"]:
            responses = [c for c in checkins if c.get(field) is not None]
            if responses:
                positive_pct = sum(1 for c in responses if c[field]) / len(responses)
                adjustments += (positive_pct - 0.5) * 20
                count += 1
        
        if count > 0:
            score += round(adjustments / count)
        
        return max(0, min(100, score))
    
    def _calculate_operational_alignment(
        self, 
        checkins: List[Dict], 
        manager_logs: List[Dict]
    ) -> int:
        """Calculate operational alignment from check-ins vs manager logs"""
        if not checkins or not manager_logs:
            return 50
        
        checkins_by_date = {}
        for c in checkins:
            d = c["checkin_date"]
            if d not in checkins_by_date:
                checkins_by_date[d] = []
            if c.get("mood_emoji"):
                checkins_by_date[d].append(c["mood_emoji"])
        
        manager_by_date = {log["log_date"]: log["overall_rating"] for log in manager_logs}
        
        alignments = []
        for log_date, manager_rating in manager_by_date.items():
            if log_date in checkins_by_date and checkins_by_date[log_date]:
                staff_moods = checkins_by_date[log_date]
                avg_staff_mood = sum(staff_moods) / len(staff_moods)
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
        """Identify specific days with perception gaps"""
        from datetime import datetime
        
        gaps = []
        
        checkins_by_date = {}
        for c in checkins:
            d = c["checkin_date"]
            if d not in checkins_by_date:
                checkins_by_date[d] = []
            if c.get("mood_emoji"):
                checkins_by_date[d].append(c)
        
        manager_by_date = {log["log_date"]: log for log in manager_logs}
        
        for log_date, manager_log in manager_by_date.items():
            if log_date in checkins_by_date and checkins_by_date[log_date]:
                staff_checkins = checkins_by_date[log_date]
                moods = [c["mood_emoji"] for c in staff_checkins if c.get("mood_emoji")]
                if not moods:
                    continue
                    
                avg_mood = sum(moods) / len(moods)
                manager_rating = manager_log["overall_rating"]
                diff = manager_rating - avg_mood
                
                staff_saw = self._mood_to_label(avg_mood)
                manager_saw = self._mood_to_label(manager_rating)
                
                try:
                    day_name = datetime.fromisoformat(log_date).strftime("%a")
                except:
                    day_name = log_date
                
                if abs(diff) >= 1.5:
                    gap_level = "high"
                elif abs(diff) >= 0.75:
                    gap_level = "medium"
                else:
                    gap_level = "none"
                
                gaps.append({
                    "date": log_date,
                    "day": day_name,
                    "staff_saw": staff_saw,
                    "manager_saw": manager_saw,
                    "gap": gap_level,
                    "staff_avg_mood": round(avg_mood, 1),
                    "manager_rating": manager_rating
                })
        
        # Sort by date
        gaps.sort(key=lambda x: x["date"])
        
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
        """Calculate risk scores by role/position"""
        role_risks = []
        
        for role, staff_ids in staff_by_role.items():
            role_checkins = [c for c in checkins if c["staff_id"] in staff_ids]
            
            if not role_checkins:
                continue
            
            moods = [c["mood_emoji"] for c in role_checkins if c.get("mood_emoji")]
            if not moods:
                continue
            
            avg_mood = sum(moods) / len(moods)
            risk_score = round(100 - ((avg_mood - 1) * 20))
            
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
        
        return min(50, penalty)