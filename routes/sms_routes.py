"""
EN PLACE SMS ROUTES
Endpoints for SMS notifications and scheduled reminders
"""

from datetime import datetime, date
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
import os
import logging

from services.twilio_service import (
    send_sms, 
    send_checkin_reminder, 
    send_bulk_sms,
    is_twilio_configured
)
from services.auth_service import verify_jwt_token
from supabase import create_client, Client
from config.settings import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sms", tags=["sms"])

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Internal API key for scheduled jobs (set in Heroku config)
SCHEDULER_API_KEY = os.getenv("SCHEDULER_API_KEY")


def verify_scheduler_key(x_scheduler_key: Optional[str] = Header(None)):
    """Verify the request is from Heroku Scheduler"""
    if not SCHEDULER_API_KEY:
        raise HTTPException(status_code=500, detail="Scheduler key not configured")
    if x_scheduler_key != SCHEDULER_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid scheduler key")
    return True


class SendSMSRequest(BaseModel):
    phone: str
    message: str


class TestSMSRequest(BaseModel):
    phone: str


# ═══════════════════════════════════════════════════════════════════
# ADMIN/TESTING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@router.get("/status")
async def get_sms_status(current_staff: Dict[str, Any] = Depends(verify_jwt_token)):
    """Check if Twilio is configured and working"""
    return {
        "configured": is_twilio_configured(),
        "timestamp": datetime.utcnow().isoformat()
    }


@router.post("/test")
async def test_sms(
    request: TestSMSRequest,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """Send a test SMS (manager only)"""
    if current_staff.get("portal_access") != "manager":
        raise HTTPException(status_code=403, detail="Manager access required")
    
    result = send_sms(request.phone, "Test message from En Place. SMS notifications are working!")
    return result


# ═══════════════════════════════════════════════════════════════════
# SCHEDULED JOB ENDPOINT
# ═══════════════════════════════════════════════════════════════════

@router.post("/send-checkin-reminders")
async def send_checkin_reminders(authorized: bool = Depends(verify_scheduler_key)):
    """
    Send check-in reminders to staff who haven't checked in today.
    Called by Heroku Scheduler daily.
    
    Only sends to staff with:
    - sms_notifications_enabled = true
    - valid phone number
    - no check-in record for today
    - status = 'active'
    """
    today = date.today().isoformat()
    
    try:
        # Get staff who need reminders
        # This query finds active staff with SMS enabled who haven't checked in today
        result = supabase.rpc('get_staff_needing_checkin_reminder', {
            'p_date': today
        }).execute()
        
        if not result.data:
            return {
                "success": True,
                "message": "No staff need reminders",
                "sent": 0,
                "failed": 0
            }
        
        staff_to_remind = result.data
        
        # Build recipient list
        recipients = [
            {"phone": s["phone"], "name": s["full_name"]}
            for s in staff_to_remind
            if s.get("phone")
        ]
        
        if not recipients:
            return {
                "success": True,
                "message": "No valid phone numbers",
                "sent": 0,
                "failed": 0
            }
        
        # Send reminders
        from services.twilio_service import CheckInReminder
        results = send_bulk_sms(recipients, CheckInReminder.STANDARD)
        
        # Log results
        logger.info(f"Check-in reminders sent: {results['sent']}, failed: {results['failed']}")
        
        return {
            "success": True,
            "date": today,
            "sent": results["sent"],
            "failed": results["failed"],
            "details": results["details"]
        }
        
    except Exception as e:
        logger.error(f"Send reminders error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════
# MANUAL SEND ENDPOINT (for managers)
# ═══════════════════════════════════════════════════════════════════

@router.post("/send")
async def send_single_sms(
    request: SendSMSRequest,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """Send a custom SMS (manager only)"""
    if current_staff.get("portal_access") != "manager":
        raise HTTPException(status_code=403, detail="Manager access required")
    
    result = send_sms(request.phone, request.message)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "SMS failed"))
    
    return result