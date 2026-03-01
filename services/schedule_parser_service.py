"""
SCHEDULE PARSER SERVICE
=======================
Uses GPT-4o-mini to parse any schedule format and map to staff IDs.

Flow:
1. Receive raw schedule text (CSV, tabs, whatever)
2. Fetch restaurant's staff list
3. GPT parses and fuzzy-matches names to staff_id
4. Return normalized shifts ready for analysis

Historical Upload Mode:
- include_inactive_staff=True fetches ALL staff (active + inactive)
- Unmatched names returned separately for manager reconciliation
- Parser prompt adjusted to flag status of matched staff
"""

import os
import json
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional

from openai import AsyncOpenAI
from supabase import create_client, Client

from config.settings import SUPABASE_URL, SUPABASE_KEY

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Cost tracking
COST_PER_1K_INPUT_TOKENS = 0.00015
COST_PER_1K_OUTPUT_TOKENS = 0.0006


PARSE_SYSTEM_PROMPT = """You are a restaurant schedule parser. Your job is to extract shift assignments from messy schedule data.

CRITICAL RULES:
1. Match employee names using fuzzy matching against the provided staff list
2. Use staff_id from the list, NOT the name
3. Parse ANY format: CSV, tabs, pipes, plain text, whatever
4. Infer dates from context (day names + week_of date)
5. Normalize times to 24-hour format (HH:MM)
6. Recognize position abbreviations: Svr=Server, Bart=Bartender, Cook=Line Cook, Dish=Dishwasher, Host=Host, Expo=Expo, Prep=Prep Cook, Bus=Busser

Respond with ONLY valid JSON (no markdown, no explanation):
{
    "shifts": [
        {
            "staff_id": "SRV001",
            "staff_name": "Maria Rodriguez",
            "date": "2025-12-02",
            "start_time": "16:00",
            "end_time": "22:00",
            "position": "Server"
        }
    ],
    "unmapped": ["Unknown Person Name"],
    "warnings": ["Any parsing issues or ambiguities"],
    "total_shifts": 42,
    "total_staff": 12
}"""

PARSE_HISTORICAL_SYSTEM_PROMPT = """You are a restaurant schedule parser. Your job is to extract shift assignments from HISTORICAL schedule data being imported into the system.

CRITICAL RULES:
1. Match employee names using fuzzy matching against the provided staff list (includes both active AND inactive employees)
2. Use staff_id from the list, NOT the name
3. Parse ANY format: CSV, tabs, pipes, plain text, whatever
4. Infer dates from context (day names + week_of date)
5. Normalize times to 24-hour format (HH:MM)
6. Recognize position abbreviations: Svr=Server, Bart=Bartender, Cook=Line Cook, Dish=Dishwasher, Host=Host, Expo=Expo, Prep=Prep Cook, Bus=Busser
7. For names that do NOT match anyone on the staff list, DO NOT include them in "shifts". Put them ONLY in "unmapped" with the position you inferred from context and their individual shift details.
8. The "shifts" array must ONLY contain entries with a valid staff_id from the staff list. Never use placeholder values like "UNMAPPED" as a staff_id.

Respond with ONLY valid JSON (no markdown, no explanation):
{
    "shifts": [
        {
            "staff_id": "SRV001",
            "staff_name": "Maria Rodriguez",
            "date": "2025-12-02",
            "start_time": "16:00",
            "end_time": "22:00",
            "position": "Server"
        }
    ],
    "unmapped": [
        {
            "name": "Unknown Person",
            "inferred_position": "Server",
            "shift_count": 3,
            "shifts": [
                {"date": "2025-12-02", "start_time": "16:00", "end_time": "22:00"}
            ]
        }
    ],
    "warnings": ["Any parsing issues or ambiguities"],
    "total_shifts": 42,
    "total_staff": 12
}"""


async def parse_schedule(
    raw_schedule: str,
    restaurant_id: int,
    week_of: str,
    include_inactive_staff: bool = False
) -> Dict[str, Any]:
    """
    Parse raw schedule text into normalized shifts.

    Args:
        raw_schedule: Raw schedule data (CSV, tabs, whatever)
        restaurant_id: Restaurant to fetch staff list for
        week_of: Start date of the week (YYYY-MM-DD)
        include_inactive_staff: If True, match against active + inactive staff (for historical imports)

    Returns:
        Parsed schedule with shifts, unmapped names, and warnings
    """
    # Fetch staff list for matching
    staff_list = get_staff_list(restaurant_id, include_inactive=include_inactive_staff)

    if not staff_list:
        return {
            "success": False,
            "error": "No staff found for restaurant",
            "shifts": [],
            "unmapped": [],
            "warnings": []
        }

    # Format staff list for prompt — include status when historical so GPT knows context
    if include_inactive_staff:
        staff_context = json.dumps([
            {
                "staff_id": s["staff_id"],
                "name": s["full_name"],
                "position": s.get("position", ""),
                "status": s.get("status", "active")
            }
            for s in staff_list
        ], indent=2)
    else:
        staff_context = json.dumps([
            {"staff_id": s["staff_id"], "name": s["full_name"], "position": s.get("position", "")}
            for s in staff_list
        ], indent=2)

    # Select appropriate system prompt
    system_prompt = PARSE_HISTORICAL_SYSTEM_PROMPT if include_inactive_staff else PARSE_SYSTEM_PROMPT

    # Build user prompt
    user_prompt = f"""STAFF LIST (use staff_id for matching):
{staff_context}

WEEK OF: {week_of}

RAW SCHEDULE DATA:
{raw_schedule}

Parse this schedule and match names to the staff list above."""

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0,
            max_tokens=4000
        )

        content = response.choices[0].message.content.strip()
        tokens_used = response.usage.total_tokens

        # Parse JSON response
        try:
            result = json.loads(content)
            result["success"] = True
            result["tokens_used"] = tokens_used
            result["estimated_cost"] = (
                (response.usage.prompt_tokens * COST_PER_1K_INPUT_TOKENS / 1000) +
                (response.usage.completion_tokens * COST_PER_1K_OUTPUT_TOKENS / 1000)
            )
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse schedule response: {content[:200]}")
            return {
                "success": False,
                "error": f"JSON parse failed: {str(e)}",
                "raw_response": content[:500],
                "shifts": [],
                "unmapped": [],
                "warnings": ["GPT response was not valid JSON"]
            }

    except Exception as e:
        logger.error(f"OpenAI API error during schedule parse: {e}")
        return {
            "success": False,
            "error": str(e),
            "shifts": [],
            "unmapped": [],
            "warnings": []
        }


def get_staff_list(restaurant_id: int, include_inactive: bool = False) -> List[Dict]:
    """
    Fetch staff for a restaurant.
    
    Args:
        restaurant_id: Restaurant to fetch staff for
        include_inactive: If True, return all staff regardless of status.
                          Used for historical imports where old schedules
                          may contain names of departed employees.
    """
    try:
        query = supabase.table("staff") \
            .select("staff_id, full_name, position, status") \
            .eq("restaurant_id", restaurant_id)

        if not include_inactive:
            query = query.eq("status", "Active")

        result = query.execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error fetching staff: {e}")
        return []


def get_week_dates(week_of: str) -> List[str]:
    """Get all 7 dates for a week starting on the given date."""
    start = datetime.strptime(week_of, "%Y-%m-%d").date()
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]