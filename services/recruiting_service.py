"""
RECRUITING SERVICE
Scoring engine for organic applicants (careers_submissions table)

WEIGHT CONFIGURATION - Adjust these values to tune scoring behavior
All weights should sum to 1.0 (100%)
"""
import logging
import re
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from database.supabase_client import get_supabase
from openai import OpenAI

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# SCORING CONFIGURATION - EASY TO REFACTOR
# ═══════════════════════════════════════════════════════════════════════════════

SCORING_WEIGHTS = {
    "experience": 0.30,      # Restaurant experience depth
    "motivation": 0.25,      # Quality of "why" answer
    "availability": 0.20,    # Schedule flexibility
    "completeness": 0.15,    # Application effort
    "recency": 0.10,         # How recently they applied
}

# Experience scoring keywords and values
EXPERIENCE_KEYWORDS = {
    # High-value experience (fine dining, management)
    "fine dining": 15,
    "manager": 12,
    "supervisor": 10,
    "lead": 8,
    "head server": 10,
    "sommelier": 12,
    "executive chef": 15,
    "sous chef": 12,
    
    # Standard restaurant roles
    "server": 6,
    "bartender": 7,
    "host": 5,
    "hostess": 5,
    "cook": 6,
    "line cook": 6,
    "prep cook": 5,
    "dishwasher": 4,
    "busser": 4,
    "barback": 5,
    "food runner": 4,
    "expo": 6,
    "expeditor": 6,
    
    # Years of experience
    "5+ years": 10,
    "5 years": 10,
    "4 years": 8,
    "3 years": 6,
    "2 years": 4,
    "1 year": 2,
    
    # Establishment types
    "michelin": 10,
    "upscale": 6,
    "casual dining": 4,
    "fast casual": 3,
    "fast food": 2,
    "catering": 5,
    "hotel": 5,
    "country club": 6,
    "private club": 6,
}

# Availability scoring
AVAILABILITY_SCORES = {
    "open": 20,
    "open availability": 20,
    "flexible": 15,
    "full time": 15,
    "full-time": 15,
    "part time": 10,
    "part-time": 10,
    "weekends": 12,
    "nights": 10,
    "evenings": 10,
    "mornings": 8,
}

# Motivation quality indicators
MOTIVATION_POSITIVE = [
    "passion", "passionate", "love", "career", "grow", "growth",
    "learn", "develop", "team", "hospitality", "service",
    "guest experience", "culinary", "craft", "dedication",
    "long-term", "professional", "opportunity"
]

MOTIVATION_NEGATIVE = [
    "need money", "need a job", "bills", "temporary", "for now",
    "until", "whatever", "anything"
]


class RecruitingService:
    def __init__(self):
        self.supabase = get_supabase()
        self.openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # ═══════════════════════════════════════════════════════════════════════════
    # MAIN SCORING FUNCTION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def calculate_app_score(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate application score for a candidate.
        Returns score breakdown for transparency.
        """
        breakdown = {}
        
        # 1. Experience Score (0-30 points based on weight)
        exp_raw = self._score_experience(candidate)
        breakdown["experience"] = {
            "raw": exp_raw,
            "weighted": round(exp_raw * SCORING_WEIGHTS["experience"]),
            "max": 30
        }
        
        # 2. Motivation Score (0-25 points based on weight)
        mot_raw = self._score_motivation(candidate)
        breakdown["motivation"] = {
            "raw": mot_raw,
            "weighted": round(mot_raw * SCORING_WEIGHTS["motivation"]),
            "max": 25
        }
        
        # 3. Availability Score (0-20 points based on weight)
        avail_raw = self._score_availability(candidate)
        breakdown["availability"] = {
            "raw": avail_raw,
            "weighted": round(avail_raw * SCORING_WEIGHTS["availability"]),
            "max": 20
        }
        
        # 4. Completeness Score (0-15 points based on weight)
        comp_raw = self._score_completeness(candidate)
        breakdown["completeness"] = {
            "raw": comp_raw,
            "weighted": round(comp_raw * SCORING_WEIGHTS["completeness"]),
            "max": 15
        }
        
        # 5. Recency Score (0-10 points based on weight)
        rec_raw = self._score_recency(candidate)
        breakdown["recency"] = {
            "raw": rec_raw,
            "weighted": round(rec_raw * SCORING_WEIGHTS["recency"]),
            "max": 10
        }
        
        # Calculate total
        total_score = sum(b["weighted"] for b in breakdown.values())
        total_score = min(100, max(0, total_score))  # Clamp to 0-100
        
        return {
            "app_score": total_score,
            "breakdown": breakdown,
            "weights_used": SCORING_WEIGHTS
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # INDIVIDUAL SCORING FUNCTIONS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _score_experience(self, candidate: Dict[str, Any]) -> int:
        """Score based on restaurant experience (0-100 raw)"""
        score = 0
        
        # Combine all text fields that might contain experience info
        text_fields = [
            candidate.get("background", []),
            candidate.get("extra", ""),
            candidate.get("role", ""),
        ]
        
        # Handle background as array
        if isinstance(text_fields[0], list):
            text_fields[0] = " ".join(text_fields[0])
        
        combined_text = " ".join(str(f) for f in text_fields if f).lower()
        
        # Check for experience keywords
        matched_keywords = []
        for keyword, points in EXPERIENCE_KEYWORDS.items():
            if keyword.lower() in combined_text:
                score += points
                matched_keywords.append(keyword)
        
        # Cap at 100
        return min(100, score)

    def _score_motivation(self, candidate: Dict[str, Any]) -> int:
        """Score motivation quality (0-100 raw)"""
        motivation = candidate.get("motivation", "") or ""
        interest = candidate.get("interest", "") or ""
        combined = f"{motivation} {interest}".lower()
        
        if not combined.strip():
            return 20  # Base score for no answer
        
        score = 40  # Base score for providing an answer
        
        # Length bonus (thoughtful answers tend to be longer)
        word_count = len(combined.split())
        if word_count > 50:
            score += 20
        elif word_count > 25:
            score += 10
        elif word_count > 10:
            score += 5
        
        # Positive indicators
        for keyword in MOTIVATION_POSITIVE:
            if keyword in combined:
                score += 5
        
        # Negative indicators
        for keyword in MOTIVATION_NEGATIVE:
            if keyword in combined:
                score -= 10
        
        return min(100, max(0, score))

    def _score_availability(self, candidate: Dict[str, Any]) -> int:
        """Score availability flexibility (0-100 raw)"""
        availability = (candidate.get("availability", "") or "").lower()
        
        if not availability:
            return 25  # Unknown availability
        
        score = 0
        for keyword, points in AVAILABILITY_SCORES.items():
            if keyword in availability:
                score = max(score, points * 5)  # Scale to 0-100
        
        return min(100, score) if score > 0 else 50  # Default to 50 if has text but no keywords

    def _score_completeness(self, candidate: Dict[str, Any]) -> int:
        """Score application completeness (0-100 raw)"""
        optional_fields = [
            "phone",
            "linkedin", 
            "background",
            "motivation",
            "interest",
            "extra",
            "availability",
            "city_state"
        ]
        
        filled = 0
        for field in optional_fields:
            value = candidate.get(field)
            if value:
                if isinstance(value, list) and len(value) > 0:
                    filled += 1
                elif isinstance(value, str) and value.strip():
                    filled += 1
        
        return round((filled / len(optional_fields)) * 100)

    def _score_recency(self, candidate: Dict[str, Any]) -> int:
        """Score based on how recently they applied (0-100 raw)"""
        created_at = candidate.get("created_at")
        
        if not created_at:
            return 50  # Unknown, give middle score
        
        try:
            if isinstance(created_at, str):
                # Handle ISO format
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            else:
                created = created_at
            
            now = datetime.now(created.tzinfo) if created.tzinfo else datetime.utcnow()
            days_ago = (now - created).days
            
            if days_ago < 1:
                return 100
            elif days_ago < 3:
                return 80
            elif days_ago < 7:
                return 50
            elif days_ago < 14:
                return 30
            else:
                return 20
                
        except Exception as e:
            logger.warning(f"Could not parse created_at: {e}")
            return 50

    # ═══════════════════════════════════════════════════════════════════════════
    # DATABASE OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def score_candidate(self, candidate_id: str) -> Dict[str, Any]:
        """Score a specific candidate and update their record"""
        try:
            # Fetch candidate
            result = self.supabase.table("careers_submissions") \
                .select("*") \
                .eq("id", candidate_id) \
                .execute()
            
            if not result.data or len(result.data) == 0:
                raise Exception(f"Candidate {candidate_id} not found")
            
            candidate = result.data[0]
            
            # Calculate score
            score_result = self.calculate_app_score(candidate)
            
            # Update candidate
            update_result = self.supabase.table("careers_submissions") \
                .update({"app_score": score_result["app_score"]}) \
                .eq("id", candidate_id) \
                .execute()
            
            return {
                "candidate_id": candidate_id,
                "name": candidate.get("name"),
                **score_result
            }
            
        except Exception as e:
            logger.error(f"Score candidate error: {e}")
            raise e

    async def score_all_unscored(self) -> Dict[str, Any]:
        """Score all candidates that don't have a score yet"""
        try:
            # Fetch unscored candidates
            result = self.supabase.table("careers_submissions") \
                .select("*") \
                .is_("app_score", "null") \
                .execute()
            
            candidates = result.data or []
            scored_count = 0
            errors = []
            
            for candidate in candidates:
                try:
                    score_result = self.calculate_app_score(candidate)
                    
                    self.supabase.table("careers_submissions") \
                        .update({"app_score": score_result["app_score"]}) \
                        .eq("id", candidate["id"]) \
                        .execute()
                    
                    scored_count += 1
                except Exception as e:
                    errors.append({"id": candidate["id"], "error": str(e)})
            
            return {
                "total_unscored": len(candidates),
                "scored": scored_count,
                "errors": errors
            }
            
        except Exception as e:
            logger.error(f"Score all unscored error: {e}")
            raise e

    # ═══════════════════════════════════════════════════════════════════════════
    # RESUME PARSING (Indeed paste-ins)
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def parse_resume(self, resume_text: str) -> Dict[str, Any]:
        """
        Parse resume text using OpenAI and extract structured data.
        Returns extracted fields ready for scoring or saving.
        """
        try:
            prompt = """Extract the following information from this resume/application text. 
Return ONLY valid JSON with these fields (use null for missing data):

{
    "name": "Full name",
    "email": "Email address",
    "phone": "Phone number",
    "city_state": "City, State",
    "role": "Most recent or desired restaurant position",
    "background": ["Array of relevant experience items"],
    "availability": "Availability if mentioned",
    "linkedin": "LinkedIn URL if present",
    "years_experience": "Total years in restaurant industry (number or null)",
    "motivation": "Any stated interest/motivation for the role",
    "extra": "Any other relevant details"
}

Resume text:
"""
            response = self.openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system", 
                        "content": "You extract structured data from resumes. Return only valid JSON, no markdown."
                    },
                    {
                        "role": "user",
                        "content": prompt + resume_text
                    }
                ],
                temperature=0.1,
                max_tokens=1000
            )
            
            # Parse response
            content = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            
            import json
            extracted = json.loads(content)
            
            # Calculate preview score
            score_result = self.calculate_app_score(extracted)
            
            return {
                "extracted": extracted,
                "score_preview": score_result,
                "raw_text_length": len(resume_text)
            }
            
        except Exception as e:
            logger.error(f"Resume parse error: {e}")
            raise e

    async def parse_and_save_resume(
        self, 
        resume_text: str,
        source: str = "Indeed Paste"
    ) -> Dict[str, Any]:
        """
        Parse resume and save as new candidate.
        Also creates an auto-note with extraction summary.
        """
        try:
            # Parse first
            parse_result = await self.parse_resume(resume_text)
            extracted = parse_result["extracted"]
            
            # Prepare candidate record
            candidate_data = {
                "name": extracted.get("name") or "Unknown",
                "email": extracted.get("email") or f"unknown-{datetime.utcnow().timestamp()}@placeholder.com",
                "phone": extracted.get("phone"),
                "city_state": extracted.get("city_state"),
                "role": extracted.get("role"),
                "background": extracted.get("background"),
                "availability": extracted.get("availability"),
                "linkedin": extracted.get("linkedin"),
                "motivation": extracted.get("motivation"),
                "extra": extracted.get("extra"),
                "source": source,
                "stage": "new",
                "app_score": parse_result["score_preview"]["app_score"]
            }
            
            # Insert candidate
            result = self.supabase.table("careers_submissions") \
                .insert(candidate_data) \
                .execute()
            
            if not result.data or len(result.data) == 0:
                raise Exception("Insert returned no data")
            
            new_candidate = result.data[0]
            
            # Create auto-note with extraction summary
            note_text = f"Auto-parsed from {source}.\n\nExtraction summary:\n"
            note_text += f"- Experience items: {len(extracted.get('background', []))}\n"
            if extracted.get("years_experience"):
                note_text += f"- Years experience: {extracted['years_experience']}\n"
            note_text += f"- App score: {parse_result['score_preview']['app_score']}/100"
            
            self.supabase.table("candidate_notes").insert({
                "candidate_id": new_candidate["id"],
                "author": "System",
                "note_text": note_text
            }).execute()
            
            return {
                "success": True,
                "candidate": new_candidate,
                "score": parse_result["score_preview"],
                "extracted_fields": list(k for k, v in extracted.items() if v)
            }
            
        except Exception as e:
            logger.error(f"Parse and save error: {e}")
            raise e

    # ═══════════════════════════════════════════════════════════════════════════
    # ANALYTICS
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def get_score_distribution(self) -> Dict[str, Any]:
        """Get score distribution for analytics"""
        try:
            result = self.supabase.table("careers_submissions") \
                .select("app_score, stage, source, created_at") \
                .execute()
            
            candidates = result.data or []
            
            # Score buckets
            buckets = {
                "excellent": 0,   # 80-100
                "good": 0,        # 60-79
                "average": 0,     # 40-59
                "below_average": 0,  # 20-39
                "poor": 0,        # 0-19
                "unscored": 0
            }
            
            for c in candidates:
                score = c.get("app_score")
                if score is None:
                    buckets["unscored"] += 1
                elif score >= 80:
                    buckets["excellent"] += 1
                elif score >= 60:
                    buckets["good"] += 1
                elif score >= 40:
                    buckets["average"] += 1
                elif score >= 20:
                    buckets["below_average"] += 1
                else:
                    buckets["poor"] += 1
            
            # Source breakdown
            by_source = {}
            for c in candidates:
                source = c.get("source") or "Direct"
                if source not in by_source:
                    by_source[source] = {"count": 0, "avg_score": 0, "scores": []}
                by_source[source]["count"] += 1
                if c.get("app_score") is not None:
                    by_source[source]["scores"].append(c["app_score"])
            
            for source in by_source:
                scores = by_source[source]["scores"]
                by_source[source]["avg_score"] = round(sum(scores) / len(scores)) if scores else None
                del by_source[source]["scores"]
            
            return {
                "total": len(candidates),
                "score_distribution": buckets,
                "by_source": by_source
            }
            
        except Exception as e:
            logger.error(f"Get score distribution error: {e}")
            raise e