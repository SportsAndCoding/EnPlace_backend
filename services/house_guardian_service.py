"""
HOUSE GUARDIAN DETECTION SERVICE
=================================
Nightly job that scans check-in notes for danger signals using OpenAI classification.

Only processes restaurants with has_house_guardian = TRUE.

Usage:
    python -m services.house_guardian_service          # Run nightly scan
    python -m services.house_guardian_service --test   # Test classification on sample notes
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List, Any
import pytz
from collections import Counter
from uuid import uuid4
from services.house_guardian_weekly import generate_weekly_report

from openai import AsyncOpenAI
from supabase import create_client, Client

# Load environment
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config.settings import SUPABASE_URL, SUPABASE_KEY

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def _get_today_for_restaurant(organization_id: int) -> date:
    """Get today's date in restaurant timezone."""
    try:
        result = supabase.table("organizations").select("timezone").eq("id", organization_id).single().execute()
        tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
    except:
        tz_name = "America/New_York"
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()
openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Classification thresholds
MIN_CONFIDENCE = 0.6          # Minimum confidence to store signal
MIN_SEVERITY_FOR_ALERT = "medium"  # low signals go to weekly report only

# Corroboration settings
CORROBORATION_WINDOW_DAYS = 30    # Look back window for grouping signals
MIN_SOURCES_FOR_HIGH = 3          # Sources needed for HIGH alert
MIN_SOURCES_FOR_MEDIUM = 2        # Sources needed for MEDIUM alert

# Cost tracking
COST_PER_1K_INPUT_TOKENS = 0.00015   # GPT-4o-mini pricing
COST_PER_1K_OUTPUT_TOKENS = 0.0006


# ═══════════════════════════════════════════════════════════════════════════
# OPENAI CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════

CLASSIFICATION_SYSTEM_PROMPT = """You analyze restaurant worker check-in notes for HR/safety concerns.

Classify the note and respond with ONLY valid JSON (no markdown, no explanation):
{
    "category": "harassment|theft|drugs|threats|bullying|operational|none",
    "confidence": 0.0-1.0,
    "severity": "low|medium|high",
    "is_direct_accusation": true/false,
    "is_hearsay": true/false,
    "target_role": "manager|supervisor|coworker|customer|unspecified|none",
    "shift_context": "closing|opening|weekend|unspecified",
    "reasoning": "one sentence explaining classification"
}

Category definitions:
- harassment: sexual harassment, unwanted touching, quid pro quo, hostile behavior toward specific person
- theft: stealing money, tips, inventory, food
- drugs: drug use, alcohol abuse, dealing, intoxication at work
- threats: violence, intimidation, physical altercation, threats of harm
- bullying: targeting, public humiliation, hostile environment, exclusion
- operational: equipment issues, scheduling complaints, burnout, understaffing (NOT HR issues)
- none: neutral, positive, or unrelated notes

Rules:
- is_hearsay = true if they "heard" about it vs witnessed/experienced it
- is_direct_accusation = true if pointing at a specific person or role
- severity high = explicit, clear, serious (assault, witnessed theft, direct threats)
- severity medium = concerning pattern or direct complaint
- severity low = vague, uncertain, or one-off minor issue
- Venting frustration about a manager being "annoying" is NOT harassment
- "toxic" or "bad vibes" alone is NOT bullying without specific behavior
- Be conservative - ambiguous notes should be "none" or "operational"
"""


async def classify_note(note: str, shift_type: str = None, position: str = None) -> Dict[str, Any]:
    """
    Classify a single check-in note using GPT-4o-mini.
    """
    user_prompt = f'Note: "{note}"'
    
    if shift_type:
        user_prompt += f"\nShift type: {shift_type}"
    if position:
        user_prompt += f"\nEmployee position: {position}"
    
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0,
            max_tokens=200
        )
        
        content = response.choices[0].message.content.strip()
        
        try:
            result = json.loads(content)
            result["raw_response"] = content
            result["tokens_used"] = response.usage.total_tokens
            return result
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse classification response: {content[:100]}")
            return {
                "category": "none",
                "confidence": 0,
                "error": "parse_failed",
                "raw_response": content
            }
            
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return {
            "category": "none",
            "confidence": 0,
            "error": str(e)
        }

async def classify_notes_batch(notes: List[Dict], batch_size: int = 10) -> List[Dict]:
    """
    Classify multiple notes with rate limiting.
    
    Args:
        notes: List of dicts with 'id', 'notes', 'shift_type', 'position'
        batch_size: How many to process concurrently
        
    Returns:
        List of classification results with note IDs attached
    """
    results = []
    
    for i in range(0, len(notes), batch_size):
        batch = notes[i:i + batch_size]
        
        # Process batch concurrently
        tasks = [
            classify_note(
                n.get("notes", ""),
                n.get("shift_type"),
                n.get("position")
            )
            for n in batch
        ]
        
        batch_results = await asyncio.gather(*tasks)
        
        # Attach note metadata to results
        for note, classification in zip(batch, batch_results):
            classification["checkin_id"] = note.get("id")
            classification["staff_id"] = note.get("staff_id")
            classification["organization_id"] = note.get("organization_id")
            classification["checkin_date"] = note.get("checkin_date")
            results.append(classification)
        
        # Brief pause between batches to avoid rate limits
        if i + batch_size < len(notes):
            await asyncio.sleep(0.5)
    
    return results


# ═══════════════════════════════════════════════════════════════════════════
# DATABASE OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_enabled_restaurants() -> List[int]:
    """Get restaurant IDs with House Guardian enabled."""
    result = supabase.table("organizations") \
        .select("id") \
        .eq("has_house_guardian", True) \
        .execute()
    
    return [r["id"] for r in (result.data or [])]


def get_last_scan_time(organization_id: int) -> datetime:
    """Get the last time we scanned this restaurant's notes."""
    result = supabase.table("house_guardian_scan_log") \
        .select("scanned_at") \
        .eq("organization_id", organization_id) \
        .order("scanned_at", desc=True) \
        .limit(1) \
        .execute()
    
    if result.data:
        return datetime.fromisoformat(result.data[0]["scanned_at"].replace("Z", "+00:00"))
    
    # Default: scan last 24 hours on first run
    return datetime.utcnow() - timedelta(hours=24)


def get_new_notes(organization_id: int, since: datetime) -> List[Dict]:
    """Get check-in notes created since last scan."""
    result = supabase.table("sse_daily_checkins") \
        .select("id, staff_id, organization_id, checkin_date, notes, created_at") \
        .eq("organization_id", organization_id) \
        .not_.is_("notes", "null") \
        .neq("notes", "") \
        .gt("created_at", since.isoformat()) \
        .execute()
    
    return result.data or []


def get_staff_context(staff_ids: List[str]) -> Dict[str, Dict]:
    """Get position and other context for staff members."""
    if not staff_ids:
        return {}
    
    result = supabase.table("staff") \
        .select("staff_id, position, full_name") \
        .in_("staff_id", staff_ids) \
        .execute()
    
    return {s["staff_id"]: s for s in (result.data or [])}


def get_staff_events(staff_ids: List[str], organization_id: int, days_back: int = 14) -> Dict[str, List[Dict]]:
    """Get recent events (PTO denied, write-ups, etc.) for credibility timing."""
    if not staff_ids:
        return {}
    
    cutoff = (_get_today_for_restaurant(organization_id) - timedelta(days=days_back)).isoformat()
    
    result = supabase.table("staff_events") \
        .select("*") \
        .in_("staff_id", staff_ids) \
        .gte("event_date", cutoff) \
        .execute()
    
    # Group by staff_id
    events = {}
    for e in (result.data or []):
        staff_id = e["staff_id"]
        if staff_id not in events:
            events[staff_id] = []
        events[staff_id].append(e)
    
    return events


def save_signals(signals: List[Dict]) -> int:
    """Save classified signals to database."""
    if not signals:
        return 0
    
    rows = []
    for s in signals:
        rows.append({
            "id": str(uuid4()),
            "organization_id": s["organization_id"],
            "checkin_id": str(s["checkin_id"]),
            "staff_id": s["staff_id"],
            "category": s["category"],
            "confidence": s["confidence"],
            "severity": s.get("severity", "low"),
            "is_direct_accusation": s.get("is_direct_accusation", False),
            "is_hearsay": s.get("is_hearsay", False),
            "target_role": s.get("target_role", "unspecified"),
            "shift_context": s.get("shift_context", "unspecified"),
            "reasoning": s.get("reasoning", ""),
            "created_at": datetime.utcnow().isoformat()
        })
    
    result = supabase.table("house_guardian_signals").insert(rows).execute()
    return len(result.data) if result.data else 0


def get_signals_for_corroboration(organization_id: int, days_back: int = 30) -> List[Dict]:
    """Get recent signals for corroboration analysis."""
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
    
    result = supabase.table("house_guardian_signals") \
        .select("*") \
        .eq("organization_id", organization_id) \
        .eq("processed", False) \
        .gte("created_at", cutoff) \
        .execute()
    
    return result.data or []


def get_existing_alert(organization_id: int, category: str, location_context: str) -> Optional[Dict]:
    """Check if there's already an active alert for this pattern."""
    result = supabase.table("house_guardian_alerts") \
        .select("*") \
        .eq("organization_id", organization_id) \
        .eq("category", category) \
        .eq("location_context", location_context) \
        .in_("status", ["active", "investigating"]) \
        .limit(1) \
        .execute()
    
    return result.data[0] if result.data else None


def create_or_update_alert(alert_data: Dict) -> str:
    """Create new alert or update existing one."""
    existing = get_existing_alert(
        alert_data["organization_id"],
        alert_data["category"],
        alert_data["location_context"]
    )

    if existing:
        # Update existing alert
        update_data = {
            "source_count": alert_data["source_count"],
            "signal_strength": alert_data["signal_strength"],
            "credibility_factors": alert_data["credibility_factors"],
            "timeframe_end": alert_data["timeframe_end"],
            "signal_ids": list(set(existing.get("signal_ids", []) + alert_data["signal_ids"]))
        }

        supabase.table("house_guardian_alerts") \
            .update(update_data) \
            .eq("id", existing["id"]) \
            .execute()

        return existing["id"]
    else:
        # Create new alert
        alert_id = str(uuid4())
        alert_data["id"] = alert_id
        alert_data["status"] = "active"
        alert_data["created_at"] = datetime.utcnow().isoformat()

        # Create corresponding SSE event for Event Manager
        sse_event_id = create_sse_event_for_alert(alert_data)
        alert_data["sse_event_id"] = sse_event_id

        supabase.table("house_guardian_alerts").insert(alert_data).execute()
        return alert_id


def create_sse_event_for_alert(alert_data: Dict) -> str:
    """Create an SSE escalation event for a House Guardian alert."""
    
    # Map signal strength to severity
    strength_to_severity = {
        "HIGH": "critical",
        "MEDIUM": "high",
        "LOW": "medium"
    }
    severity = strength_to_severity.get(alert_data.get("signal_strength", "MEDIUM"), "medium")
    
    # Map severity to score
    severity_scores = {
        "critical": 100,
        "high": 75,
        "medium": 50,
        "low": 25
    }
    
    # Build trigger reason
    category_labels = {
        "harassment": "Harassment signals detected",
        "theft": "Theft indicators detected", 
        "drugs": "Substance abuse concerns detected",
        "threats": "Safety threat signals detected",
        "bullying": "Hostile behavior patterns detected"
    }
    category = alert_data.get("category", "concern")
    trigger = category_labels.get(category, f"{category.title()} signals detected")
    
    location = alert_data.get("location_context", "")
    if location:
        trigger += f" - {location}"
    
    source_count = alert_data.get("source_count", 1)
    trigger += f" ({source_count} source{'s' if source_count > 1 else ''})"

    event_data = {
        "organization_id": alert_data["organization_id"],
        "event_type": f"house_guardian_{category}",
        "severity": severity,
        "severity_score": severity_scores.get(severity, 50),
        "status": "actionable",
        "current_step": 1,
        "primary_staff_id": None,  # HG is anonymous
        "affected_role": alert_data.get("location_context", "General"),
        "trigger_reason": trigger,
        "triggered_at": datetime.utcnow().isoformat(),
        "auto_created": True,
        "source_type": "house_guardian"
    }
    
    result = supabase.table("sse_escalation_events").insert(event_data).execute()
    
    if result.data and len(result.data) > 0:
        return result.data[0]["id"]
    
    return None


def mark_signals_processed(signal_ids: List[str]):
    """Mark signals as processed."""
    if not signal_ids:
        return
    
    supabase.table("house_guardian_signals") \
        .update({"processed": True}) \
        .in_("id", signal_ids) \
        .execute()


def log_scan(organization_id: int, notes_scanned: int, signals_found: int, alerts_created: int):
    """Log scan completion for tracking."""
    supabase.table("house_guardian_scan_log").insert({
        "organization_id": organization_id,
        "scanned_at": datetime.utcnow().isoformat(),
        "notes_scanned": notes_scanned,
        "signals_found": signals_found,
        "alerts_created": alerts_created
    }).execute()


# ═══════════════════════════════════════════════════════════════════════════
# CORROBORATION & ALERT GENERATION
# ═══════════════════════════════════════════════════════════════════════════

def build_location_context(signals: List[Dict]) -> str:
    """Build anonymized location context from signals."""
    
    # Collect target roles and shift contexts
    target_roles = [s.get("target_role", "unspecified") for s in signals]
    shift_contexts = [s.get("shift_context", "unspecified") for s in signals]
    
    # Most common target role
    role_counts = Counter(target_roles)
    primary_role = role_counts.most_common(1)[0][0] if role_counts else "unspecified"
    
    # Most common shift context
    shift_counts = Counter(shift_contexts)
    primary_shift = shift_counts.most_common(1)[0][0] if shift_counts else "unspecified"
    
    # Build string
    role_map = {
        "manager": "Management",
        "supervisor": "Management",
        "coworker": "Staff",
        "customer": "Customer-facing",
        "unspecified": "General",
        "none": "General"
    }
    
    shift_map = {
        "closing": "Closing Shifts",
        "opening": "Opening Shifts",
        "weekend": "Weekend Shifts",
        "unspecified": ""
    }
    
    area = role_map.get(primary_role, "General")
    shift = shift_map.get(primary_shift, "")
    
    if shift:
        return f"{area} - {shift}"
    return area


def calculate_credibility_factors(signals: List[Dict], staff_events: Dict[str, List]) -> Dict:
    """Calculate credibility factors for a group of signals."""
    
    factors = {
        "multiple_sources": False,
        "escalation_detected": False,
        "corroborating_language": False,
        "hearsay_ratio": 0.0,
        "timing_flags": 0,
        "flags": []
    }
    
    # Count unique sources
    unique_staff = set(s["staff_id"] for s in signals)
    source_count = len(unique_staff)
    
    if source_count >= 2:
        factors["multiple_sources"] = True
        factors["flags"].append(f"{source_count} independent sources")
    
    # Check for escalation (same person, multiple signals over time)
    for staff_id in unique_staff:
        person_signals = [s for s in signals if s["staff_id"] == staff_id]
        if len(person_signals) > 1:
            # Check if signals are on different days
            dates = set(s.get("checkin_date") for s in person_signals if s.get("checkin_date"))
            if len(dates) > 1:
                factors["escalation_detected"] = True
                factors["flags"].append("Escalation pattern detected (repeated concerns over time)")
                break
    
    # Hearsay ratio
    hearsay_count = sum(1 for s in signals if s.get("is_hearsay", False))
    factors["hearsay_ratio"] = hearsay_count / len(signals) if signals else 0
    
    if factors["hearsay_ratio"] > 0.5:
        factors["flags"].append(f"{int(factors['hearsay_ratio'] * 100)}% of reports are hearsay")
    elif factors["hearsay_ratio"] == 0 and source_count >= 2:
        factors["flags"].append("All reports are first-hand accounts")
    
    # Check timing flags (did accusation follow negative event)
    for signal in signals:
        staff_id = signal["staff_id"]
        if staff_id in staff_events:
            for event in staff_events[staff_id]:
                event_date = date.fromisoformat(event["event_date"])
                signal_date = date.fromisoformat(str(signal.get("checkin_date", ""))[:10]) if signal.get("checkin_date") else None
                
                if signal_date and event_date:
                    days_diff = (signal_date - event_date).days
                    if 0 <= days_diff <= 7:
                        factors["timing_flags"] += 1
                        break
    
    if factors["timing_flags"] > 0:
        factors["flags"].append(
            f"{factors['timing_flags']} report(s) submitted within 7 days of negative event"
        )
    
    # Direct accusations increase credibility
    direct_count = sum(1 for s in signals if s.get("is_direct_accusation", False))
    if direct_count > 0 and source_count >= 2:
        factors["flags"].append(f"{direct_count} direct accusation(s)")
    
    return factors


def calculate_signal_strength(source_count: int, factors: Dict) -> str:
    """Determine alert signal strength."""
    
    # HIGH: 3+ sources, or 2+ with low hearsay
    if source_count >= MIN_SOURCES_FOR_HIGH:
        return "HIGH"
    
    if source_count >= MIN_SOURCES_FOR_MEDIUM and factors["hearsay_ratio"] < 0.3:
        return "HIGH"
    
    # MEDIUM: 2 sources, or 1 with escalation
    if source_count >= MIN_SOURCES_FOR_MEDIUM:
        return "MEDIUM"
    
    if factors["escalation_detected"]:
        return "MEDIUM"
    
    # LOW: single source, no escalation
    return "LOW"


def generate_alerts(organization_id: int, signals: List[Dict], staff_events: Dict) -> int:
    """
    Group signals and generate alerts.
    Returns count of alerts created/updated.
    """
    if not signals:
        return 0
    
    # Filter to danger categories only
    danger_categories = ["harassment", "theft", "drugs", "threats", "bullying"]
    danger_signals = [s for s in signals if s["category"] in danger_categories]
    
    if not danger_signals:
        return 0
    
    # Group by category + target_role + shift_context
    groups = {}
    for signal in danger_signals:
        key = (
            signal["category"],
            signal.get("target_role", "unspecified"),
            signal.get("shift_context", "unspecified")
        )
        if key not in groups:
            groups[key] = []
        groups[key].append(signal)
    
    alerts_created = 0
    
    for (category, target_role, shift_context), group_signals in groups.items():
        # Calculate corroboration
        unique_staff = set(s["staff_id"] for s in group_signals)
        source_count = len(unique_staff)
        
        # Get credibility factors
        factors = calculate_credibility_factors(group_signals, staff_events)
        
        # Calculate signal strength
        strength = calculate_signal_strength(source_count, factors)
        
        # Skip LOW signals for immediate alerts (they go to weekly report)
        if strength == "LOW":
            # Still mark as processed
            mark_signals_processed([s["id"] for s in group_signals])
            continue
        
        # Build location context
        location_context = build_location_context(group_signals)
        
        # Get date range
        dates = [s.get("checkin_date") for s in group_signals if s.get("checkin_date")]
        today_str = _get_today_for_restaurant(organization_id).isoformat()
        timeframe_start = min(dates) if dates else today_str
        timeframe_end = max(dates) if dates else today_str
        
        # Create alert
        alert_data = {
            "organization_id": organization_id,
            "category": category,
            "location_context": location_context,
            "timeframe_start": timeframe_start,
            "timeframe_end": timeframe_end,
            "source_count": source_count,
            "signal_strength": strength,
            "credibility_factors": factors,
            "signal_ids": [s["id"] for s in group_signals]
        }
        
        create_or_update_alert(alert_data)
        alerts_created += 1
        
        # Mark signals as processed
        mark_signals_processed([s["id"] for s in group_signals])
    
    return alerts_created


# ═══════════════════════════════════════════════════════════════════════════
# MAIN SCAN FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

async def scan_restaurant(organization_id: int) -> Dict[str, int]:
    """
    Scan a single restaurant's notes and generate alerts.
    
    Returns stats dict.
    """
    stats = {
        "notes_scanned": 0,
        "signals_found": 0,
        "alerts_created": 0
    }
    
    # Get notes since last scan
    last_scan = get_last_scan_time(organization_id)
    notes = get_new_notes(organization_id, last_scan)
    
    if not notes:
        log_scan(organization_id, 0, 0, 0)
        return stats
    
    stats["notes_scanned"] = len(notes)
    logger.info(f"Restaurant {organization_id}: scanning {len(notes)} notes")
    
    # Get staff context for position info
    staff_ids = list(set(n["staff_id"] for n in notes))
    staff_context = get_staff_context(staff_ids)
    
    # Attach position to notes
    for note in notes:
        staff = staff_context.get(note["staff_id"], {})
        note["position"] = staff.get("position")
    
    # Classify all notes
    classifications = await classify_notes_batch(notes)
    
    # Filter to signals worth storing
    signals = [
        c for c in classifications
        if c.get("category") in ["harassment", "theft", "drugs", "threats", "bullying"]
        and c.get("confidence", 0) >= MIN_CONFIDENCE
        and c.get("error") is None
    ]
    
    stats["signals_found"] = len(signals)
    
    if signals:
        # Save signals
        save_signals(signals)
        
        # Get all unprocessed signals for corroboration
        all_signals = get_signals_for_corroboration(organization_id)
        
        # Get staff events for timing analysis
        all_staff_ids = list(set(s["staff_id"] for s in all_signals))
        staff_events = get_staff_events(all_staff_ids, organization_id)
        
        # Generate alerts
        stats["alerts_created"] = generate_alerts(organization_id, all_signals, staff_events)
    
    # Log scan completion
    log_scan(organization_id, stats["notes_scanned"], stats["signals_found"], stats["alerts_created"])
    
    # Generate weekly report
    try:
        from services.house_guardian_weekly import generate_weekly_report
        generate_weekly_report(organization_id)
    except Exception as e:
        logger.error(f"Failed to generate weekly report for restaurant {organization_id}: {e}")
    
    return stats


async def run_nightly_scan():
    """
    Run the full nightly scan across all enabled restaurants.
    """
    logger.info("=" * 60)
    logger.info("HOUSE GUARDIAN NIGHTLY SCAN")
    logger.info(f"Started at: {datetime.utcnow().isoformat()}")
    logger.info("=" * 60)
    
    # Get enabled restaurants
    restaurant_ids = get_enabled_restaurants()
    logger.info(f"Found {len(restaurant_ids)} restaurants with House Guardian enabled")
    
    if not restaurant_ids:
        logger.info("No restaurants to scan. Exiting.")
        return
    
    total_stats = {
        "restaurants_scanned": 0,
        "notes_scanned": 0,
        "signals_found": 0,
        "alerts_created": 0
    }
    
    for organization_id in restaurant_ids:
        try:
            stats = await scan_restaurant(organization_id)
            total_stats["restaurants_scanned"] += 1
            total_stats["notes_scanned"] += stats["notes_scanned"]
            total_stats["signals_found"] += stats["signals_found"]
            total_stats["alerts_created"] += stats["alerts_created"]
            
        except Exception as e:
            logger.error(f"Error scanning restaurant {organization_id}: {e}")
            continue
    
    # Summary
    logger.info("=" * 60)
    logger.info("SCAN COMPLETE")
    logger.info(f"Restaurants scanned: {total_stats['restaurants_scanned']}")
    logger.info(f"Notes scanned: {total_stats['notes_scanned']}")
    logger.info(f"Signals found: {total_stats['signals_found']}")
    logger.info(f"Alerts created: {total_stats['alerts_created']}")
    
    # Estimate cost
    estimated_cost = total_stats["notes_scanned"] * 0.00004  # ~$0.04 per 1k notes
    logger.info(f"Estimated API cost: ${estimated_cost:.4f}")
    logger.info("=" * 60)


# ═══════════════════════════════════════════════════════════════════════════
# TEST FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

async def test_classification():
    """Test classification on sample notes."""
    
    test_notes = [
        "the gm keeps finding excuses to touch my back and it's weird",
        "pretty sure marcus is pocketing tips from the pool",
        "someone showed up to their shift clearly drunk again",
        "ice machine broke again fml",
        "good shift today, solid team",
        "heard someone might be stealing but idk",
        "manager screamed at me in front of customers",
        "closing with jake is fine but he's kinda creepy after",
        "way understaffed tonight, running around like crazy",
        "the sous is definitely on something, eyes are cooked every shift",
    ]
    
    print("\n" + "=" * 70)
    print("HOUSE GUARDIAN CLASSIFICATION TEST")
    print("=" * 70)
    
    for note in test_notes:
        result = await classify_note(note)
        
        print(f"\nNote: \"{note}\"")
        print(f"  Category: {result.get('category', 'ERROR')}")
        print(f"  Confidence: {result.get('confidence', 0):.2f}")
        print(f"  Severity: {result.get('severity', 'N/A')}")
        print(f"  Direct accusation: {result.get('is_direct_accusation', 'N/A')}")
        print(f"  Hearsay: {result.get('is_hearsay', 'N/A')}")
        print(f"  Target: {result.get('target_role', 'N/A')}")
        print(f"  Reasoning: {result.get('reasoning', 'N/A')}")
    
    print("\n" + "=" * 70)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    if "--test" in sys.argv:
        asyncio.run(test_classification())
    else:
        asyncio.run(run_nightly_scan())