"""
WEBSITE PROSPECTING ROUTES
AI-powered tool to find independently-owned restaurants with bad/no websites in a given area.
Uses Anthropic Claude API with web search to research and score targets.
"""
import os
import json
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from services.auth_service import verify_jwt_token
import anthropic

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prospecting", tags=["prospecting"])

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ProspectSearchRequest(BaseModel):
    zip_code: str
    radius_miles: Optional[int] = 10
    cuisine_filter: Optional[str] = None
    max_results: Optional[int] = 15


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/search")
async def search_prospects(req: ProspectSearchRequest, user=Depends(verify_jwt_token)):
    """
    AI-powered restaurant prospecting. Finds independently-owned restaurants
    with bad or no websites in a given zip code area.
    Returns structured JSON ready for CSV export.
    """
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Anthropic API key not configured")

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        cuisine_note = f"Focus on {req.cuisine_filter} restaurants." if req.cuisine_filter else "All cuisine types."

        prompt = f"""You are a restaurant industry sales researcher. Find {req.max_results} independently-owned restaurants near zip code {req.zip_code} (within ~{req.radius_miles} miles) that have BAD websites, NO website, or are using only a Facebook page as their web presence.

{cuisine_note}

IMPORTANT RULES:
- Only independently owned restaurants. NO chains, NO franchises.
- Search for restaurants in this area, then check their websites.
- A "bad website" means: outdated design, not mobile-friendly, broken links, uses a free site builder (Wix free tier, GoDaddy basic), no online menu, or just a Facebook page.
- A restaurant with NO website at all is the best prospect.
- Skip restaurants that already have professional, modern websites.

For each restaurant found, return this EXACT JSON structure. Return ONLY the JSON array, no other text:

[
  {{
    "restaurant_name": "Name of restaurant",
    "address": "Full street address",
    "city": "City",
    "state": "State abbreviation",
    "zip": "ZIP code",
    "phone": "Phone number or empty string",
    "cuisine_type": "Italian, Mexican, BBQ, etc.",
    "current_website": "URL or 'None' or 'Facebook only'",
    "website_score": 2,
    "google_rating": 4.2,
    "review_count": 156,
    "estimated_employees": 15,
    "owner_name": "Name if findable, otherwise empty string",
    "facebook_url": "URL or empty string",
    "instagram_url": "URL or empty string",
    "notes": "Brief explanation of why they are a good prospect"
  }}
]

website_score: 1 = no website at all, 2 = Facebook only, 3 = terrible/broken site, 4 = outdated but functional, 5 = decent but not great. Only return restaurants scoring 1-4.

estimated_employees: rough guess based on restaurant size, type, and review volume.

Return ONLY valid JSON. No markdown, no backticks, no explanation."""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )

        # Extract text from response (may have multiple content blocks due to tool use)
        response_text = ""
        for block in message.content:
            if block.type == "text":
                response_text += block.text

        # Parse JSON from response
        response_text = response_text.strip()
        # Strip markdown fences if present
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1] if "\n" in response_text else response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        if response_text.startswith("json"):
            response_text = response_text[4:].strip()

        try:
            prospects = json.loads(response_text)
        except json.JSONDecodeError:
            # Try to find JSON array in the response
            start = response_text.find("[")
            end = response_text.rfind("]") + 1
            if start >= 0 and end > start:
                prospects = json.loads(response_text[start:end])
            else:
                logger.error(f"Failed to parse prospects JSON: {response_text[:500]}")
                raise HTTPException(status_code=500, detail="AI returned invalid format. Try again.")

        return {
            "success": True,
            "zip_code": req.zip_code,
            "count": len(prospects),
            "prospects": prospects
        }

    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}")
        raise HTTPException(status_code=500, detail=f"AI service error: {str(e)}")
    except Exception as e:
        logger.error(f"Prospecting error: {e}")
        raise HTTPException(status_code=500, detail=str(e))