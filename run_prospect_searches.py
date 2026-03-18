"""
PROSPECT SEARCH WORKER
Run by Heroku Scheduler every 10 minutes.
Picks up pending prospect searches, runs Claude with web search, saves results.

Usage: heroku run python run_prospect_searches.py --app enplace-api-v3
Scheduler: python run_prospect_searches.py
"""
import os
import json
import logging
import sys
import time
import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

from supabase import create_client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY]):
    logger.error("Missing env vars: SUPABASE_URL, SUPABASE_SERVICE_KEY, or ANTHROPIC_API_KEY")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

import anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def extract_search_results(message):
    """Extract readable text from web search result blocks."""
    texts = []
    for block in message.content:
        if block.type == "text" and block.text.strip():
            texts.append(block.text)
        elif block.type == "web_search_tool_result":
            for item in getattr(block, 'content', []):
                if hasattr(item, 'text') and item.text:
                    texts.append(item.text[:500])
                elif hasattr(item, 'title') and hasattr(item, 'url'):
                    texts.append(f"{item.title}: {item.url}")
    return "\n\n".join(texts)


def process_search(search):
    """Process a single prospect search using Claude with web search."""
    search_id = search["id"]
    zip_code = search["zip_code"]
    radius = search.get("radius_miles", 10)
    cuisine = search.get("cuisine_filter")
    max_results = search.get("max_results", 10)

    logger.info(f"Processing search {search_id}: zip={zip_code}, radius={radius}mi, cuisine={cuisine}")

    supabase.table("prospect_searches").update({
        "status": "processing"
    }).eq("id", search_id).execute()

    cuisine_note = f"Focus on {cuisine} restaurants." if cuisine else "All cuisine types."

    search_prompt = f"""You are a restaurant industry sales researcher finding prospects near zip code {zip_code} (within ~{radius} miles). {cuisine_note}

Your goal: find {max_results} independently-owned restaurants that need a better website.

SEARCH STRATEGY (do ALL of these):
1. Search "restaurants near {zip_code}" and check the top results
2. Search "best independent restaurants {zip_code}"
3. Search "locally owned restaurants near {zip_code}"
4. Search Yelp for restaurants in this zip code
5. Search Google Maps for restaurants in this area
6. Try different cuisine searches: "Mexican restaurant {zip_code}", "Italian restaurant {zip_code}", "BBQ restaurant {zip_code}", "pizza {zip_code}", "diner {zip_code}"

For EACH restaurant you find:
- Search for their website directly (e.g. "Restaurant Name website")
- Check if the website exists, is modern, or is terrible
- A restaurant with NO website, only a Facebook page, or a clearly outdated/broken site is a GOOD prospect

IMPORTANT RULES:
- Only independently owned. NO chains, NO franchises (no Applebees, Chilis, McDonalds, Olive Garden, etc.)
- Get a DIVERSE mix of cuisine types. Do NOT return all the same cuisine.
- Prioritize restaurants with high Google/Yelp ratings but bad web presence (these are the best prospects - great food, bad marketing)
- Find at LEAST {max_results} restaurants. Search more if needed.

For each restaurant found, include: name, address, city, state, zip, phone, cuisine type, current website URL (or "None" or "Facebook only"), website quality score 1-4, Google rating, review count, estimated employees, owner name if findable, Facebook URL, Instagram URL, and a note about why they need a website."""

    try:
        # STEP 1: Let Claude do web searches
        message = None
        for attempt in range(3):
            try:
                message = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=4000,
                    tools=[{"type": "web_search_20250305", "name": "web_search"}],
                    messages=[{"role": "user", "content": search_prompt}]
                )
                break
            except anthropic.RateLimitError:
                wait = 30 * (attempt + 1)
                logger.info(f"Rate limited, waiting {wait}s before retry {attempt + 2}/3")
                time.sleep(wait)

        if not message:
            raise Exception("Rate limited after 3 attempts")

        logger.info(f"Step 1 done. Stop reason: {message.stop_reason}, blocks: {len(message.content)}")

        # Extract whatever text and search results we got
        raw_findings = extract_search_results(message)

        # Check if Claude already gave us JSON
        if "[" in raw_findings and "]" in raw_findings:
            start = raw_findings.find("[")
            end = raw_findings.rfind("]") + 1
            try:
                prospects = json.loads(raw_findings[start:end])
                logger.info(f"Got JSON directly from step 1: {len(prospects)} prospects")
                supabase.table("prospect_searches").update({
                    "status": "completed",
                    "results": prospects,
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }).eq("id", search_id).execute()
                return
            except json.JSONDecodeError:
                pass

        logger.info(f"Extracted {len(raw_findings)} chars of search findings. Sending to step 2.")

        # STEP 2: Format the findings as JSON (no web search, fast)
        format_prompt = f"""Based on the following research about restaurants near zip code {zip_code}, create a JSON array of prospects.

RESEARCH FINDINGS:
{raw_findings[:6000]}

Return ONLY a valid JSON array with this structure for each restaurant found:
[
  {{
    "restaurant_name": "Name",
    "address": "Street address",
    "city": "City",
    "state": "ST",
    "zip": "{zip_code}",
    "phone": "Phone or empty string",
    "cuisine_type": "Type",
    "current_website": "URL or None or Facebook only",
    "website_score": 2,
    "google_rating": 4.2,
    "review_count": 150,
    "estimated_employees": 12,
    "owner_name": "Name if found, else empty string",
    "facebook_url": "URL or empty string",
    "instagram_url": "URL or empty string",
    "notes": "Why they need a website (1 sentence)"
  }}
]

website_score: 1=no website, 2=Facebook only, 3=terrible site, 4=outdated. Only 1-4.
Return ONLY the JSON array. No markdown, no backticks, no explanation."""

        time.sleep(5)  # Brief pause between steps

        format_message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            messages=[{"role": "user", "content": format_prompt}]
        )

        response_text = ""
        for block in format_message.content:
            if block.type == "text":
                response_text += block.text

        response_text = response_text.strip()
        logger.info(f"Step 2 response length: {len(response_text)}")

        # Strip markdown fences
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:])
        if response_text.endswith("```"):
            response_text = response_text[:-3].strip()
        if response_text.startswith("json"):
            response_text = response_text[4:].strip()

        # Parse JSON
        try:
            prospects = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("[")
            end = response_text.rfind("]") + 1
            if start >= 0 and end > start:
                prospects = json.loads(response_text[start:end])
            else:
                raise ValueError(f"Could not parse JSON: {response_text[:300]}")

        # Save results
        supabase.table("prospect_searches").update({
            "status": "completed",
            "results": prospects,
            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }).eq("id", search_id).execute()

        logger.info(f"Search {search_id} completed: {len(prospects)} prospects found")

    except Exception as e:
        logger.error(f"Search {search_id} failed: {e}")
        supabase.table("prospect_searches").update({
            "status": "failed",
            "error_message": str(e)[:500],
            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }).eq("id", search_id).execute()


def main():
    result = supabase.table("prospect_searches") \
        .select("*") \
        .eq("status", "pending") \
        .order("created_at") \
        .limit(5) \
        .execute()

    pending = result.data or []

    if not pending:
        logger.info("No pending prospect searches. Exiting.")
        return

    logger.info(f"Found {len(pending)} pending searches to process.")

    for search in pending:
        process_search(search)

    logger.info("All pending searches processed.")


if __name__ == "__main__":
    main()