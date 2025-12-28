"""
EN PLACE TWILIO SERVICE
SMS notifications for staff check-in reminders and alerts
"""

import os
import logging
from typing import Optional, List, Dict, Any
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

logger = logging.getLogger(__name__)

# Twilio configuration from environment
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# Initialize Twilio client (lazy - only when needed)
_twilio_client: Optional[Client] = None


def get_twilio_client() -> Optional[Client]:
    """Get or create Twilio client instance"""
    global _twilio_client
    
    if _twilio_client is None:
        if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
            logger.warning("Twilio credentials not configured")
            return None
        
        try:
            _twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        except Exception as e:
            logger.error(f"Failed to initialize Twilio client: {e}")
            return None
    
    return _twilio_client


def is_twilio_configured() -> bool:
    """Check if Twilio is properly configured"""
    return all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER])


def format_phone_number(phone: str) -> Optional[str]:
    """
    Format phone number to E.164 format for Twilio
    Assumes US numbers if no country code provided
    """
    if not phone:
        return None
    
    # Strip all non-numeric characters
    digits = ''.join(filter(str.isdigit, phone))
    
    # Handle US numbers
    if len(digits) == 10:
        return f"+1{digits}"
    elif len(digits) == 11 and digits.startswith('1'):
        return f"+{digits}"
    elif len(digits) > 10 and not digits.startswith('1'):
        # Assume already has country code
        return f"+{digits}"
    else:
        logger.warning(f"Invalid phone format: {phone}")
        return None


def send_sms(to_phone: str, message: str) -> Dict[str, Any]:
    """
    Send a single SMS message
    
    Returns:
        dict with 'success', 'message_sid' or 'error'
    """
    client = get_twilio_client()
    
    if not client:
        return {
            "success": False,
            "error": "Twilio not configured"
        }
    
    formatted_phone = format_phone_number(to_phone)
    if not formatted_phone:
        return {
            "success": False,
            "error": f"Invalid phone number: {to_phone}"
        }
    
    try:
        message_obj = client.messages.create(
            body=message,
            from_=TWILIO_PHONE_NUMBER,
            to=formatted_phone
        )
        
        logger.info(f"SMS sent successfully: {message_obj.sid}")
        
        return {
            "success": True,
            "message_sid": message_obj.sid,
            "to": formatted_phone
        }
        
    except TwilioRestException as e:
        logger.error(f"Twilio API error: {e.msg}")
        return {
            "success": False,
            "error": e.msg,
            "error_code": e.code
        }
    except Exception as e:
        logger.error(f"SMS send error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def send_bulk_sms(recipients: List[Dict[str, str]], message_template: str) -> Dict[str, Any]:
    """
    Send SMS to multiple recipients
    
    Args:
        recipients: List of dicts with 'phone' and optionally 'name' for personalization
        message_template: Message with optional {name} placeholder
    
    Returns:
        dict with 'sent', 'failed', and details
    """
    results = {
        "sent": 0,
        "failed": 0,
        "details": []
    }
    
    for recipient in recipients:
        phone = recipient.get("phone")
        name = recipient.get("name", "")
        
        # Personalize message if template has placeholder
        message = message_template.replace("{name}", name.split()[0] if name else "")
        
        result = send_sms(phone, message)
        
        if result["success"]:
            results["sent"] += 1
        else:
            results["failed"] += 1
        
        results["details"].append({
            "phone": phone,
            **result
        })
    
    return results


# ═══════════════════════════════════════════════════════════════════
# PRE-BUILT MESSAGE TEMPLATES
# ═══════════════════════════════════════════════════════════════════

class CheckInReminder:
    """Check-in reminder message templates"""
    
    STANDARD = "Hey{name}! Quick reminder to check in on En Place today. Just takes 10 seconds 🙂"
    
    GENTLE = "Hi{name}, just a friendly nudge to do your daily check-in when you get a chance."
    
    STREAK = "Hey{name}! You're on a {streak}-day check-in streak. Keep it going! 🔥"


class CoverageAlert:
    """Shift coverage alert templates"""
    
    OPEN_SHIFT = "Open shift available: {date} {time}. Reply YES to claim it."
    
    URGENT = "URGENT: We need coverage for {date} {time}. Can you help? Reply YES/NO"


def send_checkin_reminder(phone: str, name: str = "", streak: int = 0) -> Dict[str, Any]:
    """
    Send check-in reminder with appropriate template
    """
    if streak >= 3:
        message = CheckInReminder.STREAK.replace("{name}", f" {name.split()[0]}" if name else "")
        message = message.replace("{streak}", str(streak))
    else:
        message = CheckInReminder.STANDARD.replace("{name}", f" {name.split()[0]}" if name else "")
    
    return send_sms(phone, message)