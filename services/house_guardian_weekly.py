"""
HOUSE GUARDIAN WEEKLY REPORT GENERATOR
======================================
Generates weekly summary reports for House Guardian.
Called at end of nightly scan.
"""

from datetime import datetime, timedelta, date
from typing import Dict, List, Any
import pytz
from uuid import uuid4
from collections import Counter

from supabase import create_client
from config.settings import SUPABASE_URL, SUPABASE_KEY

import logging
logger = logging.getLogger(__name__)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def _get_today_for_restaurant(restaurant_id: int) -> date:
    """Get today's date in restaurant timezone."""
    try:
        result = supabase.table("restaurants").select("timezone").eq("id", restaurant_id).single().execute()
        tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
    except:
        tz_name = "America/New_York"
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()


DANGER_CATEGORIES = ["harassment", "theft", "drugs", "threats", "bullying"]


def generate_weekly_report(restaurant_id: int) -> Dict[str, Any]:
    """
    Generate weekly House Guardian report for a restaurant.
    
    Returns the report data that was saved.
    """
    today = _get_today_for_restaurant(restaurant_id)
    week_start = today - timedelta(days=7)
    week_end = today - timedelta(days=1)
    
    # ═══════════════════════════════════════════════════════════════════
    # 1. COUNT TOTAL NOTES SCANNED THIS WEEK
    # ═══════════════════════════════════════════════════════════════════
    
    checkins_result = supabase.table("sse_daily_checkins") \
        .select("id, notes, mood_emoji, staff_id") \
        .eq("restaurant_id", restaurant_id) \
        .gte("checkin_date", week_start.isoformat()) \
        .lte("checkin_date", week_end.isoformat()) \
        .execute()
    
    all_checkins = checkins_result.data or []
    notes_with_text = [c for c in all_checkins if c.get("notes") and c["notes"].strip()]
    
    notes_scanned = len(all_checkins)
    notes_with_content = len(notes_with_text)
    
    # ═══════════════════════════════════════════════════════════════════
    # 2. GET SIGNALS DETECTED THIS WEEK
    # ═══════════════════════════════════════════════════════════════════
    
    signals_result = supabase.table("house_guardian_signals") \
        .select("category, severity") \
        .eq("restaurant_id", restaurant_id) \
        .gte("created_at", week_start.isoformat()) \
        .execute()
    
    signals = signals_result.data or []
    signals_detected = len(signals)
    
    # Count by category
    category_counts = Counter(s["category"] for s in signals)
    
    # ═══════════════════════════════════════════════════════════════════
    # 3. GET ALERTS GENERATED THIS WEEK
    # ═══════════════════════════════════════════════════════════════════
    
    alerts_result = supabase.table("house_guardian_alerts") \
        .select("category, signal_strength, status") \
        .eq("restaurant_id", restaurant_id) \
        .gte("created_at", week_start.isoformat()) \
        .in_("status", ["active", "investigating"]) \
        .execute()
    
    alerts = alerts_result.data or []
    alerts_generated = len(alerts)
    
    # Determine which categories are clear vs flagged
    flagged_categories = list(set(a["category"] for a in alerts))
    clear_categories = [c for c in DANGER_CATEGORIES if c not in flagged_categories]
    
    # ═══════════════════════════════════════════════════════════════════
    # 4. EXTRACT OPERATIONAL THEMES
    # ═══════════════════════════════════════════════════════════════════
    
    operational_themes = extract_operational_themes(notes_with_text)
    
    # ═══════════════════════════════════════════════════════════════════
    # 5. GET POSITIVE SENTIMENT SAMPLES
    # ═══════════════════════════════════════════════════════════════════
    
    # Get staff info for sentiment samples
    staff_ids = list(set(c["staff_id"] for c in notes_with_text if c.get("mood_emoji", 0) >= 4))
    staff_map = {}
    if staff_ids:
        staff_result = supabase.table("staff") \
            .select("staff_id, position") \
            .in_("staff_id", staff_ids) \
            .execute()
        staff_map = {s["staff_id"]: s["position"] for s in (staff_result.data or [])}
    
    sentiment_samples = []
    positive_notes = [c for c in notes_with_text if c.get("mood_emoji", 0) >= 4]
    for note in positive_notes[:3]:  # Max 3 samples
        if len(note["notes"]) < 100:  # Short positive notes only
            sentiment_samples.append({
                "text": note["notes"],
                "role": staff_map.get(note["staff_id"], "Staff")
            })
    
    # ═══════════════════════════════════════════════════════════════════
    # 6. BUILD REPORT CONTENT
    # ═══════════════════════════════════════════════════════════════════
    
    report_content = {
        "categories_clear": clear_categories,
        "categories_flagged": flagged_categories,
        "operational_themes": operational_themes,
        "sentiment_samples": sentiment_samples,
        "all_clear": len(flagged_categories) == 0
    }
    
    # ═══════════════════════════════════════════════════════════════════
    # 7. UPSERT REPORT
    # ═══════════════════════════════════════════════════════════════════
    
    report_data = {
        "restaurant_id": restaurant_id,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "notes_scanned": notes_scanned,
        "signals_detected": signals_detected,
        "alerts_generated": alerts_generated,
        "report_content": report_content,
        "generated_at": datetime.utcnow().isoformat()
    }
    
    # Check if report exists for this week
    existing = supabase.table("house_guardian_weekly_reports") \
        .select("id") \
        .eq("restaurant_id", restaurant_id) \
        .eq("week_start", week_start.isoformat()) \
        .execute()
    
    if existing.data:
        # Update existing
        supabase.table("house_guardian_weekly_reports") \
            .update(report_data) \
            .eq("id", existing.data[0]["id"]) \
            .execute()
        logger.info(f"Restaurant {restaurant_id}: Updated weekly report")
    else:
        # Insert new
        report_data["id"] = str(uuid4())
        supabase.table("house_guardian_weekly_reports") \
            .insert(report_data) \
            .execute()
        logger.info(f"Restaurant {restaurant_id}: Created weekly report")
    
    return report_data


def extract_operational_themes(notes: List[Dict]) -> List[Dict]:
    """
    Extract operational themes from notes.
    Looks for equipment issues, process complaints, etc.
    """
    themes = []
    
    # Keywords to look for
    equipment_keywords = [
        "ice machine", "pos", "printer", "oven", "fryer", "dishwasher", 
        "freezer", "cooler", "ac", "air conditioning", "grill"
    ]
    
    process_keywords = [
        "checklist", "sidework", "prep", "inventory", "schedule"
    ]
    
    # Count mentions
    equipment_mentions = Counter()
    process_mentions = Counter()
    
    for note in notes:
        text = note.get("notes", "").lower()
        
        for keyword in equipment_keywords:
            if keyword in text:
                equipment_mentions[keyword] += 1
        
        for keyword in process_keywords:
            if keyword in text:
                process_mentions[keyword] += 1
    
    # Add themes with 2+ mentions
    for item, count in equipment_mentions.most_common(5):
        if count >= 2:
            themes.append({
                "issue": item.title(),
                "mentions": count,
                "type": "equipment"
            })
    
    for item, count in process_mentions.most_common(3):
        if count >= 2:
            themes.append({
                "issue": item.title(),
                "mentions": count,
                "type": "process"
            })
    
    return themes