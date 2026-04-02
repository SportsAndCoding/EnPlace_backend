"""
Run scheduled Relay scans for all users.
Deploy to backend root. Add to Heroku Scheduler: python run_relay_scans.py (hourly)

Free + Starter = weekly scans (last scan > 7 days ago)
Pro = daily scans (last scan > 1 day ago)
"""
import os
import sys
import json
import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("relay_scheduler")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY]):
    logger.error("Missing required environment variables")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


CONTACT_SCAN_PROMPT = """You are analyzing web search results to determine if a professional contact has changed roles or companies.

KNOWN INFORMATION:
- Name: {full_name}
- Company: {company}
- Title: {title}
- Location: {location}
- Last verified: {last_verified}

Search the web for current information about this person's professional status. Look for LinkedIn profiles, company pages, press releases, job postings, and news articles.

Return ONLY valid JSON:
{{
    "status": "stable" | "promoted" | "lateral_move" | "departed" | "moved_company" | "title_change" | "uncertain",
    "confidence": "high" | "medium" | "low",
    "new_title": "new title if changed, else null",
    "new_company": "new company if moved, else null",
    "replacement_name": "name of person who appears to have replaced them, if detectable, else null",
    "replacement_linkedin": "linkedin URL if found, else null",
    "source": "linkedin" | "company_website" | "press_release" | "google" | "news",
    "source_url": "most relevant URL or null",
    "details": "brief explanation of what the search results indicate",
    "recommended_action": "specific sales action to take based on this change, or null if stable"
}}

RULES:
- If results confirm the person still holds the same title at the same company, status is "stable"
- If you find their name with a DIFFERENT title at the SAME company, determine if it is a promotion or lateral move
- If you find their name at a DIFFERENT company, status is "moved_company"
- If their name no longer appears connected to that company at all, status is "departed"
- If results are ambiguous or insufficient, status is "uncertain" with confidence "low"
- Do not invent information. Only report what the search results support.
- For recommended_action, be specific and actionable."""


async def scan_contact(contact: dict) -> dict | None:
    """Scan a single contact. Returns signal dict or None."""
    location = ""
    if contact.get("city") and contact.get("state"):
        location = f"{contact['city']}, {contact['state']}"

    prompt = CONTACT_SCAN_PROMPT.format(
        full_name=contact["full_name"],
        company=contact["company"],
        title=contact["title"],
        location=location or "unknown",
        last_verified=contact.get("last_verified_at") or "never"
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}],
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}]
                },
                timeout=45.0
            )
            resp_data = resp.json()

        if resp.status_code != 200:
            logger.warning(f"API error for {contact['full_name']}: {resp.status_code}")
            return None

        text = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        if not text:
            return None

        cleaned = text.strip()
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace == -1 or last_brace <= first_brace:
            return None

        try:
            from json_repair import repair_json
            parsed = json.loads(repair_json(cleaned[first_brace:last_brace + 1]))
        except Exception:
            parsed = json.loads(cleaned[first_brace:last_brace + 1])

        # Stable: update verification timestamp
        if parsed.get("status") == "stable":
            supabase.table("relay_contacts").update({
                "last_verified_at": datetime.utcnow().isoformat(),
                "last_scanned_at": datetime.utcnow().isoformat()
            }).eq("id", contact["id"]).execute()
            return None

        # Low-confidence uncertain: skip
        if parsed.get("status") == "uncertain" and parsed.get("confidence") == "low":
            supabase.table("relay_contacts").update({
                "last_scanned_at": datetime.utcnow().isoformat()
            }).eq("id", contact["id"]).execute()
            return None

        # Create signal
        signal = {
            "contact_id": contact["id"],
            "user_id": contact["user_id"],
            "signal_type": parsed["status"],
            "old_title": contact["title"],
            "new_title": parsed.get("new_title"),
            "old_company": contact["company"],
            "new_company": parsed.get("new_company"),
            "new_contact_name": parsed.get("replacement_name"),
            "new_contact_linkedin": parsed.get("replacement_linkedin"),
            "confidence": parsed.get("confidence", "medium"),
            "source": parsed.get("source"),
            "source_url": parsed.get("source_url"),
            "details": parsed.get("details"),
            "recommended_action": parsed.get("recommended_action"),
            "detected_at": datetime.utcnow().isoformat()
        }
        supabase.table("relay_signals").insert(signal).execute()

        # Update contact
        update = {
            "current_status": parsed["status"],
            "last_scanned_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        if parsed.get("new_title"):
            update["title"] = parsed["new_title"]
        if parsed.get("new_company"):
            update["company"] = parsed["new_company"]
        supabase.table("relay_contacts").update(update).eq("id", contact["id"]).execute()

        return signal

    except Exception as e:
        logger.error(f"Scan error for {contact['full_name']}: {e}")
        return None


async def run_scheduled_scans():
    """Main scheduler loop: find users due for scans, process their contacts."""
    now = datetime.utcnow()
    logger.info(f"Relay scheduled scan starting at {now.isoformat()}")

    # Get all active users
    users = supabase.table("relay_users") \
        .select("id, plan, scan_frequency") \
        .eq("plan_status", "active") \
        .execute()

    if not users.data:
        logger.info("No active users found")
        return

    total_scanned = 0
    total_signals = 0

    for user in users.data:
        is_pro = user.get("plan") == "pro"
        threshold = timedelta(days=1) if is_pro else timedelta(days=7)

        # Get active contacts for this user
        contacts = supabase.table("relay_contacts") \
            .select("*") \
            .eq("user_id", user["id"]) \
            .eq("monitoring_status", "active") \
            .execute()

        if not contacts.data:
            continue

        # Filter to contacts due for a scan
        needs_scan = []
        for c in contacts.data:
            last = c.get("last_scanned_at")
            if not last:
                needs_scan.append(c)
            else:
                try:
                    last_dt = datetime.fromisoformat(last.replace("Z", "+00:00")).replace(tzinfo=None)
                    if (now - last_dt) > threshold:
                        needs_scan.append(c)
                except (ValueError, TypeError):
                    needs_scan.append(c)

        if not needs_scan:
            continue

        logger.info(f"User {user['id']}: {len(needs_scan)} contacts due for scan")

        # Create scan record
        scan = supabase.table("relay_scans").insert({
            "user_id": user["id"],
            "scan_type": "scheduled",
            "contacts_scanned": 0,
            "signals_found": 0,
            "started_at": now.isoformat(),
            "status": "running"
        }).execute()

        scan_id = scan.data[0]["id"]
        scanned = 0
        signals_found = 0

        for contact in needs_scan:
            signal = await scan_contact(contact)
            scanned += 1
            if signal:
                signals_found += 1

            # Delay between scans
            await asyncio.sleep(1.0)

        # Complete scan
        supabase.table("relay_scans").update({
            "contacts_scanned": scanned,
            "signals_found": signals_found,
            "completed_at": datetime.utcnow().isoformat(),
            "status": "complete"
        }).eq("id", scan_id).execute()

        total_scanned += scanned
        total_signals += signals_found
        logger.info(f"User {user['id']}: scanned {scanned}, found {signals_found} signals")

    logger.info(f"Relay scheduled scan complete: {total_scanned} contacts, {total_signals} signals")


if __name__ == "__main__":
    asyncio.run(run_scheduled_scans())
