"""
PROSPECT SEARCH WORKER
Run by Heroku Scheduler every 30 minutes.
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

# Supabase
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


def process_search(search):
    """Process a single prospect search using Claude with web search."""
    search_id = search["id"]
    zip_code = search["zip_code"]
    radius = search.get("radius_miles", 10)
    cuisine = search.get("cuisine_filter")
    max_results = search.get("max_results", 10)

    logger.info(f"Processing search {search_id}: zip={zip_code}, radius={radius}mi, cuisine={cuisine}")

    # Mark as processing
    supabase.table("prospect_searches").update({
        "status": "processing"
    }).eq("id", search_id).execute()

    cuisine_note = f"Focus on {cuisine} restaurants." if cuisine else "All cuisine types."

    prompt = f"""You are a restaurant industry sales researcher. Find {max_results} independently-owned restaurants near zip code {zip_code} (within ~{radius} miles) that have BAD websites, NO website, or are using only a Facebook page as their web presence.

{cuisine_note}

IMPORTANT RULES:
- Only independently owned restaurants. NO chains, NO franchises.
- Search for restaurants in this area, then check their actual websites.
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

website_score: 1 = no website at all, 2 = Facebook only, 3 = terrible/broken site, 4 = outdated but functional. Only return restaurants scoring 1-4.
estimated_employees: rough guess based on restaurant size, type, and review volume.

Return ONLY valid JSON. No markdown, no backticks, no explanation."""

    try:
        # Retry up to 3 times with increasing delays for rate limits
        message = None
        for attempt in range(3):
            try:
                message = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=4000,
                    tools=[{"type": "web_search_20250305", "name": "web_search"}],
                    messages=[{"role": "user", "content": prompt}]
                )
                break
            except anthropic.RateLimitError:
                wait = 30 * (attempt + 1)
                logger.info(f"Rate limited, waiting {wait}s before retry {attempt + 2}/3")
                time.sleep(wait)

        if not message:
            raise Exception("Rate limited after 3 attempts. Will retry next scheduler run.")

        # Log response info
        logger.info(f"Response blocks: {[(b.type, len(b.text) if hasattr(b, 'text') else 'n/a') for b in message.content]}")
        logger.info(f"Stop reason: {message.stop_reason}")

        # Extract text from response
        response_text = ""
        for block in message.content:
            if block.type == "text":
                response_text += block.text

        logger.info(f"Extracted text length: {len(response_text)}")

        # If no JSON found, send follow-up
        if not response_text.strip() or "[" not in response_text:
            logger.info("No JSON in initial response, sending follow-up")
            followup = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=3000,
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": [b for b in message.content]},
                    {"role": "user", "content": "Now return ONLY the JSON array of restaurants you found. No explanation, just the JSON."}
                ]
            )
            response_text = ""
            for block in followup.content:
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

        # Parse JSON
        try:
            prospects = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("[")
            end = response_text.rfind("]") + 1
            if start >= 0 and end > start:
                prospects = json.loads(response_text[start:end])
            else:
                raise ValueError(f"Could not parse JSON from response: {response_text[:300]}")

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
    # Find all pending searches
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