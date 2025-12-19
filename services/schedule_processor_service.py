"""
SCHEDULE PROCESSOR SERVICE
==========================
Nightly job that processes queued schedule uploads.

For each pending upload:
1. Parse raw schedule with GPT-4o-mini
2. Run full analysis (fairness, fatigue, preferences)
3. Create Action Board items for critical issues
4. Store results and mark complete

Usage:
    python -m services.schedule_processor_service          # Process all pending
    python -m services.schedule_processor_service --test   # Dry run, no DB writes
"""

import os
import sys
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from supabase import create_client, Client

# Load environment
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config.settings import SUPABASE_URL, SUPABASE_KEY
from services.schedule_parser_service import parse_schedule
from services.schedule_analysis_service import analyze_schedule

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════

async def process_pending_schedules(dry_run: bool = False) -> Dict[str, Any]:
    """
    Process all pending schedule uploads.
    
    Args:
        dry_run: If True, parse and analyze but don't write to DB
    
    Returns:
        Summary of processing results
    """
    logger.info("=" * 60)
    logger.info("SCHEDULE PROCESSOR - Starting nightly run")
    logger.info("=" * 60)
    
    # Fetch pending uploads
    pending = fetch_pending_uploads()
    
    if not pending:
        logger.info("No pending schedule uploads found")
        return {"processed": 0, "success": 0, "failed": 0, "action_items_created": 0}
    
    logger.info(f"Found {len(pending)} pending upload(s)")
    
    results = {
        "processed": 0,
        "success": 0,
        "failed": 0,
        "action_items_created": 0,
        "details": []
    }
    
    for upload in pending:
        upload_id = upload["id"]
        restaurant_id = upload["restaurant_id"]
        week_of = upload["week_of"]
        
        logger.info(f"\n--- Processing upload {upload_id}: restaurant={restaurant_id}, week={week_of} ---")
        
        # Mark as processing
        if not dry_run:
            mark_processing(upload_id)
        
        try:
            # Step 1: Parse the raw schedule
            logger.info("Step 1: Parsing schedule with GPT...")
            parse_result = await parse_schedule(
                raw_schedule=upload["raw_schedule"],
                restaurant_id=restaurant_id,
                week_of=week_of
            )
            
            if not parse_result.get("success"):
                raise Exception(f"Parse failed: {parse_result.get('error', 'Unknown error')}")
            
            shifts = parse_result.get("shifts", [])
            unmapped = parse_result.get("unmapped", [])
            
            logger.info(f"   Parsed {len(shifts)} shifts, {len(unmapped)} unmapped names")
            
            if not shifts:
                raise Exception("No shifts could be extracted from schedule")
            
            # Step 2: Run analysis
            logger.info("Step 2: Running analysis...")
            analysis_result = await analyze_schedule(
                shifts=shifts,
                restaurant_id=restaurant_id,
                week_of=week_of,
                manager_notes=upload.get("manager_notes", "")
            )
            
            stability_score = analysis_result["analysis"]["scores"]["stabilityScore"]
            priority_fixes = analysis_result.get("priorityFixes", [])
            
            issues_found = len(priority_fixes)
            critical_issues = len([f for f in priority_fixes if f.get("severity") == "high"])
            
            logger.info(f"   Stability score: {stability_score}")
            logger.info(f"   Issues found: {issues_found} ({critical_issues} critical)")
            
            # Step 3: Create Action Board items for critical issues
            action_items = 0
            if not dry_run:
                action_items = create_action_board_items(
                    restaurant_id=restaurant_id,
                    upload_id=upload_id,
                    week_of=week_of,
                    priority_fixes=priority_fixes,
                    sse_events=analysis_result.get("sseEvents", [])
                )
                logger.info(f"   Created {action_items} Action Board items")
            
            # Step 4: Store results
            if not dry_run:
                mark_completed(
                    upload_id=upload_id,
                    analysis_result=analysis_result,
                    stability_score=stability_score,
                    issues_found=issues_found,
                    critical_issues=critical_issues
                )
            
            results["success"] += 1
            results["action_items_created"] += action_items
            results["details"].append({
                "upload_id": upload_id,
                "restaurant_id": restaurant_id,
                "week_of": week_of,
                "status": "success",
                "stability_score": stability_score,
                "issues_found": issues_found,
                "action_items": action_items
            })
            
            logger.info(f"   ✓ Upload {upload_id} completed successfully")
            
        except Exception as e:
            logger.error(f"   ✗ Upload {upload_id} failed: {e}")
            
            if not dry_run:
                mark_failed(upload_id, str(e))
            
            results["failed"] += 1
            results["details"].append({
                "upload_id": upload_id,
                "restaurant_id": restaurant_id,
                "week_of": week_of,
                "status": "failed",
                "error": str(e)
            })
        
        results["processed"] += 1
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("SCHEDULE PROCESSOR - Run complete")
    logger.info(f"   Processed: {results['processed']}")
    logger.info(f"   Success: {results['success']}")
    logger.info(f"   Failed: {results['failed']}")
    logger.info(f"   Action items created: {results['action_items_created']}")
    logger.info("=" * 60)
    
    return results


# ═══════════════════════════════════════════════════════════════════════════
# DATABASE OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

def fetch_pending_uploads() -> List[Dict]:
    """Fetch all pending schedule uploads."""
    try:
        result = supabase.table("schedule_uploads") \
            .select("*") \
            .eq("status", "pending") \
            .order("created_at") \
            .execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error fetching pending uploads: {e}")
        return []


def mark_processing(upload_id: int):
    """Mark upload as processing."""
    try:
        supabase.table("schedule_uploads") \
            .update({"status": "processing"}) \
            .eq("id", upload_id) \
            .execute()
    except Exception as e:
        logger.error(f"Error marking upload {upload_id} as processing: {e}")


def mark_completed(
    upload_id: int,
    analysis_result: Dict,
    stability_score: int,
    issues_found: int,
    critical_issues: int
):
    """Mark upload as completed with results."""
    try:
        supabase.table("schedule_uploads") \
            .update({
                "status": "completed",
                "analysis_result": analysis_result,
                "stability_score": stability_score,
                "issues_found": issues_found,
                "critical_issues": critical_issues,
                "processed_at": datetime.utcnow().isoformat(),
                "error_message": None
            }) \
            .eq("id", upload_id) \
            .execute()
    except Exception as e:
        logger.error(f"Error marking upload {upload_id} as completed: {e}")


def mark_failed(upload_id: int, error_message: str):
    """Mark upload as failed."""
    try:
        supabase.table("schedule_uploads") \
            .update({
                "status": "failed",
                "error_message": error_message[:500],
                "processed_at": datetime.utcnow().isoformat()
            }) \
            .eq("id", upload_id) \
            .execute()
    except Exception as e:
        logger.error(f"Error marking upload {upload_id} as failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# ACTION BOARD INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

def create_action_board_items(
    restaurant_id: int,
    upload_id: int,
    week_of: str,
    priority_fixes: List[Dict],
    sse_events: List[Dict]
) -> int:
    """
    Create Action Board notifications for critical schedule issues.
    
    Returns number of items created.
    """
    items_created = 0
    
    # Create notifications for high-severity fixes
    for fix in priority_fixes:
        if fix.get("severity") != "high":
            continue
        
        try:
            # Check if similar notification already exists (prevent duplicates)
            existing = supabase.table("notifications") \
                .select("id") \
                .eq("restaurant_id", restaurant_id) \
                .eq("type", "schedule_issue") \
                .ilike("title", f"%{fix.get('title', '')[:50]}%") \
                .execute()
            
            if existing.data:
                continue
            
            # Create notification - matches actual schema
            notification_data = {
                "restaurant_id": restaurant_id,
                "recipient_id": None,  # Restaurant-wide notification
                "type": "schedule_issue",
                "title": fix.get("title", "Schedule Issue")[:255],
                "message": f"{fix.get('description', '')} Suggested: {fix.get('suggestedAction', '')}",
                "is_read": False
            }
            
            supabase.table("notifications").insert(notification_data).execute()
            items_created += 1
            
        except Exception as e:
            logger.error(f"Error creating notification for fix {fix.get('id')}: {e}")
    
    # Create SSE escalation events
    for event in sse_events:
        if not event.get("autoCreated"):
            continue
        
        try:
            # Get staff_id from event
            staff_id = event.get("staff_id")
            staff_name = event.get("staff", "Unknown")
            
            # Create event - matches actual schema
            event_data = {
                "restaurant_id": restaurant_id,
                "event_type": map_sse_event_type(event.get("type")),
                "severity": event.get("severity", "medium"),
                "severity_score": severity_to_score(event.get("severity", "medium")),
                "status": "active",
                "current_step": 1,
                "primary_staff_id": staff_id,
                "trigger_reason": f"Schedule analysis: {event.get('trigger', 'No details')}",
                "triggered_at": datetime.utcnow().isoformat(),
                "auto_created": True,
                "source_type": "schedule_analysis"
            }
            
            supabase.table("sse_escalation_events").insert(event_data).execute()
            items_created += 1
            
        except Exception as e:
            logger.error(f"Error creating SSE event: {e}")
    
    return items_created


def severity_to_score(severity: str) -> int:
    """Convert severity string to numeric score."""
    mapping = {
        "low": 25,
        "medium": 50,
        "high": 75,
        "critical": 100
    }
    return mapping.get(severity, 50)

def map_sse_event_type(analysis_type: str) -> str:
    """Map analysis event types to SSE escalation event types."""
    mapping = {
        "burnout": "burnout_risk",
        "fatigue": "burnout_risk",
        "fairness": "fairness_issue",
        "preference_drift": "preference_drift",
        "availability": "scheduling_conflict"
    }
    return mapping.get(analysis_type, "schedule_issue")


# ═══════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """CLI entry point."""
    dry_run = "--test" in sys.argv or "--dry-run" in sys.argv
    
    if dry_run:
        logger.info("*** DRY RUN MODE - No database writes ***")
    
    results = asyncio.run(process_pending_schedules(dry_run=dry_run))
    
    # Exit with error code if any failures
    if results["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()