import logging
import random
from datetime import datetime
from typing import Optional, Dict, Any, List
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

class CandidatesService:
    def __init__(self):
        self.supabase = get_supabase()
    
    def _generate_candidate_code(self) -> str:
        """Generate unique candidate code like CND-2025-0147"""
        year = datetime.utcnow().year
        random_num = random.randint(1000, 9999)
        return f"CND-{year}-{random_num}"
    
    async def create_candidate(
        self, 
        candidate_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new candidate"""
        try:
            candidate_code = self._generate_candidate_code()
            
            payload = {
                "restaurant_id": candidate_data["restaurant_id"],
                "candidate_code": candidate_code,
                "name": candidate_data["name"],
                "email": candidate_data.get("email"),
                "phone": candidate_data.get("phone"),
                "role": candidate_data["role"],
                "status": "open",
                "gm_notes": candidate_data.get("gm_notes"),
                "applied_at": datetime.utcnow().isoformat()
            }
            
            result = self.supabase.table("hiring_candidates").insert(payload).execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            else:
                raise Exception("Insert returned no data")
                
        except Exception as e:
            logger.error(f"Create candidate error: {e}")
            raise e
    
    async def get_candidate_by_id(
        self, 
        candidate_id: str, 
        restaurant_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a specific candidate"""
        try:
            result = self.supabase.table("hiring_candidates") \
                .select("*") \
                .eq("id", candidate_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Get candidate error: {e}")
            raise e
    
    async def get_candidates_by_restaurant(
        self,
        restaurant_id: int,
        status: Optional[str] = None,
        role: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get candidates for a restaurant with optional filters"""
        try:
            query = self.supabase.table("hiring_candidates") \
                .select("*") \
                .eq("restaurant_id", restaurant_id)
            
            if status:
                query = query.eq("status", status)
            
            if role:
                query = query.eq("role", role)
            
            result = query.order("created_at", desc=True).execute()
            
            return result.data or []
            
        except Exception as e:
            logger.error(f"Get candidates error: {e}")
            raise e
    
    async def update_candidate(
        self, 
        candidate_id: str, 
        restaurant_id: int,
        update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a candidate"""
        try:
            # Filter out None values
            payload = {k: v for k, v in update_data.items() if v is not None}
            
            # Convert datetime objects
            for key in ['interviewed_at', 'decision_at', 'hired_at']:
                if key in payload and hasattr(payload[key], 'isoformat'):
                    payload[key] = payload[key].isoformat()
            
            # Always update timestamp
            payload["updated_at"] = datetime.utcnow().isoformat()
            
            if not payload:
                return await self.get_candidate_by_id(candidate_id, restaurant_id)
            
            result = self.supabase.table("hiring_candidates") \
                .update(payload) \
                .eq("id", candidate_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Update candidate error: {e}")
            raise e
    
    async def score_candidate(
        self,
        candidate_id: str,
        restaurant_id: int,
        scenario_rankings: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Calculate stability score from scenario rankings.
        
        The 8 scenarios map to 6 behavioral dimensions:
        - autonomy
        - adaptability
        - conflict_tolerance
        - authority_response
        - team_orientation
        - feedback_reception
        
        Each character choice (alex, jordan, taylor) has different weights.
        """
        try:
            # Character weights for each dimension (0-100)
            # alex = generally stable, team player
            # jordan = moderate, adaptable
            # taylor = higher risk, more independent/avoidant
            character_scores = {
                "alex": {
                    "autonomy": 75,
                    "adaptability": 85,
                    "conflict_tolerance": 80,
                    "authority_response": 85,
                    "team_orientation": 90,
                    "feedback_reception": 80
                },
                "jordan": {
                    "autonomy": 70,
                    "adaptability": 75,
                    "conflict_tolerance": 70,
                    "authority_response": 70,
                    "team_orientation": 75,
                    "feedback_reception": 75
                },
                "taylor": {
                    "autonomy": 85,
                    "adaptability": 55,
                    "conflict_tolerance": 50,
                    "authority_response": 55,
                    "team_orientation": 45,
                    "feedback_reception": 50
                }
            }
            
            # Initialize fingerprint
            fingerprint = {
                "autonomy": 0,
                "adaptability": 0,
                "conflict_tolerance": 0,
                "authority_response": 0,
                "team_orientation": 0,
                "feedback_reception": 0
            }
            
            # Calculate average across all scenarios
            count = 0
            for scenario, choice in scenario_rankings.items():
                choice_lower = choice.lower()
                if choice_lower in character_scores:
                    for dimension, score in character_scores[choice_lower].items():
                        fingerprint[dimension] += score
                    count += 1
            
            if count > 0:
                for dimension in fingerprint:
                    fingerprint[dimension] = round(fingerprint[dimension] / count)
            
            # Calculate overall stability score (weighted average of dimensions)
            weights = {
                "autonomy": 0.10,
                "adaptability": 0.20,
                "conflict_tolerance": 0.15,
                "authority_response": 0.15,
                "team_orientation": 0.25,
                "feedback_reception": 0.15
            }
            
            stability_score = round(sum(
                fingerprint[dim] * weight 
                for dim, weight in weights.items()
            ))
            
            # Calculate cliff risk (inverse relationship with stability)
            # High stability = low cliff risk
            cliff_risk_percent = max(5, min(85, 100 - stability_score + random.randint(-5, 5)))
            
            # Determine recommendation
            if stability_score >= 75:
                recommendation = "strong_hire"
            elif stability_score >= 65:
                recommendation = "hire"
            elif stability_score >= 50:
                recommendation = "hire_with_caution"
            else:
                recommendation = "not_recommended"
            
            # Update candidate with scores
            update_payload = {
                "scenario_rankings": scenario_rankings,
                "stability_score": stability_score,
                "cliff_risk_percent": cliff_risk_percent,
                "recommendation": recommendation,
                "fingerprint": fingerprint,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            result = self.supabase.table("hiring_candidates") \
                .update(update_payload) \
                .eq("id", candidate_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            
            raise Exception("Update returned no data")
            
        except Exception as e:
            logger.error(f"Score candidate error: {e}")
            raise e
    
    async def hire_candidate(
        self,
        candidate_id: str,
        restaurant_id: int,
        staff_id: str
    ) -> Optional[Dict[str, Any]]:
        """Mark candidate as hired and link to staff record"""
        try:
            payload = {
                "status": "hired",
                "hired_at": datetime.utcnow().isoformat(),
                "decision_at": datetime.utcnow().isoformat(),
                "hired_staff_id": staff_id,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            result = self.supabase.table("hiring_candidates") \
                .update(payload) \
                .eq("id", candidate_id) \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Hire candidate error: {e}")
            raise e
    
    async def get_stats(self, restaurant_id: int) -> Dict[str, int]:
        """Get candidate pipeline stats"""
        try:
            result = self.supabase.table("hiring_candidates") \
                .select("status, recommendation") \
                .eq("restaurant_id", restaurant_id) \
                .execute()
            
            candidates = result.data or []
            
            return {
                "total": len(candidates),
                "open": len([c for c in candidates if c["status"] == "open"]),
                "interviewed": len([c for c in candidates if c["status"] == "interviewed"]),
                "hired": len([c for c in candidates if c["status"] == "hired"]),
                "rejected": len([c for c in candidates if c["status"] == "rejected"]),
                "recommended": len([c for c in candidates if c.get("recommendation") in ["strong_hire", "hire"]])
            }
            
        except Exception as e:
            logger.error(f"Get stats error: {e}")
            return {"total": 0, "open": 0, "interviewed": 0, "hired": 0, "rejected": 0, "recommended": 0}