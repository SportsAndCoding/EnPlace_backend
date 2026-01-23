"""
EN PLACE SMS ROUTES
Endpoints for SMS notifications and scheduled reminders
"""

from datetime import datetime, date
from typing import Dict, Any, Optional
import pytz
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
    - no check-in record for today (in restaurant's timezone)
    - status = 'active'
    """
    try:
        # Get staff who need reminders (timezone-aware per restaurant)
        result = supabase.rpc('get_staff_needing_checkin_reminder_v2').execute()
        
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
            "sent": results["sent"],
            "failed": results["failed"],
            "details": results["details"]
        }
        
    except Exception as e:
        logger.error(f"Send reminders error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send-shift-reminders")
async def send_shift_checkin_reminders(authorized: bool = Depends(verify_scheduler_key)):
    """
    Send check-in reminders to staff whose shifts ended today without a check-in.
    Called by Heroku Scheduler (e.g., 10pm and 4pm daily).
    """
    try:
        # Get staff who need reminders
        result = supabase.rpc('get_staff_needing_checkin_reminder').execute()
        
        if not result.data:
            return {
                "success": True,
                "message": "No staff need reminders",
                "sent": 0,
                "failed": 0
            }
        
        staff_to_remind = result.data
        sent = 0
        failed = 0
        details = []
        
        for staff in staff_to_remind:
            first_name = staff["full_name"].split()[0] if staff.get("full_name") else ""
            message = f"Hey {first_name}! How was your shift? Quick 10-sec check-in: https://app.en-place.ai/staff-portal"
            
            sms_result = send_sms(staff["phone"], message)
            
            if sms_result["success"]:
                sent += 1
            else:
                failed += 1
            
            details.append({
                "staff_id": staff["staff_id"],
                "phone": staff["phone"],
                "success": sms_result["success"],
                "error": sms_result.get("error")
            })
        
        logger.info(f"Shift check-in reminders: sent={sent}, failed={failed}")
        
        return {
            "success": True,
            "sent": sent,
            "failed": failed,
            "details": details
        }
        
    except Exception as e:
        logger.error(f"Send shift reminders error: {e}")
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