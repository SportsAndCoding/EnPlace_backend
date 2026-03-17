"""
MENU PARSER ROUTES
AI-powered menu extraction. Takes a restaurant menu URL or raw text,
returns structured JSON matching site-builder's menu_highlights format.
"""
import os
import json
import logging
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from services.auth_service import verify_jwt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/menu-parser", tags=["menu-parser"])

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


class MenuParseRequest(BaseModel):
    url: Optional[str] = None
    raw_text: Optional[str] = None


@router.post("/parse")
async def parse_menu(req: MenuParseRequest, user=Depends(verify_jwt_token)):
    """
    Parse a restaurant menu from URL or raw text.
    Returns structured JSON matching site-builder menu_highlights format.
    """
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Anthropic API key not configured")

    if not req.url and not req.raw_text:
        raise HTTPException(status_code=400, detail="Provide a menu URL or raw text")

    menu_text = req.raw_text or ""

    # If URL provided, fetch the page and extract text
    if req.url and not menu_text:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(req.url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                resp.raise_for_status()
                html = resp.text

                # Strip HTML tags to get raw text
                import re
                text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()

                # Limit to ~8000 chars to keep token count reasonable
                menu_text = text[:8000]

        except Exception as e:
            logger.error(f"Failed to fetch menu URL: {e}")
            raise HTTPException(status_code=400, detail=f"Could not fetch URL: {str(e)}")

    if not menu_text.strip():
        raise HTTPException(status_code=400, detail="No menu content found at that URL")

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        prompt = f"""You are a restaurant menu parser. Extract EVERY menu item from the following menu text and organize it into categories.

MENU TEXT:
{menu_text}

Return ONLY a valid JSON array. No markdown, no backticks, no explanation.

Structure:
[
  {{
    "icon": "emoji that fits this category",
    "category": "Category Name",
    "items": [
      {{
        "name": "Item Name",
        "note": "Brief description if available, otherwise empty string",
        "price": "$XX.XX or Market Price or empty string if not listed"
      }}
    ]
  }}
]

RULES:
- Extract EVERY item. Do not summarize or skip items.
- Group items into logical categories (Appetizers, Sushi Rolls, Hibachi Entrees, etc.)
- Use the restaurant's own category names when they exist in the text.
- If an item has a description, put it in "note". Keep it under 60 characters.
- If an item has multiple sizes/prices, use the most common price or the dinner price.
- Use appropriate emoji for each category (🥢 sushi, 🔥 hibachi, 🥗 salads, 🍶 drinks, etc.)
- Prices should include the $ sign.
- Return ONLY the JSON array."""

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = ""
        for block in message.content:
            if block.type == "text":
                response_text += block.text

        response_text = response_text.strip()

        # Strip markdown fences
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:])
        if response_text.endswith("```"):
            response_text = response_text[:-3].strip()
        if response_text.startswith("json"):
            response_text = response_text[4:].strip()

        try:
            menu = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("[")
            end = response_text.rfind("]") + 1
            if start >= 0 and end > start:
                menu = json.loads(response_text[start:end])
            else:
                raise ValueError("Could not parse menu JSON")

        # Count total items
        total_items = sum(len(cat.get("items", [])) for cat in menu)

        return {
            "success": True,
            "categories": len(menu),
            "total_items": total_items,
            "menu_highlights": menu
        }

    except anthropic.APIError as e:
        logger.error(f"Anthropic error parsing menu: {e}")
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")
    except Exception as e:
        logger.error(f"Menu parse error: {e}")
        raise HTTPException(status_code=500, detail=str(e))