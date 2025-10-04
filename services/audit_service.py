import logging
from typing import Dict, Any, Optional
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

async def log_staff_change(
    staff_id: str,
    restaurant_id: int,
    changed_by: str,
    action: str,
    changed_fields: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
):
    """Log staff changes to audit table"""
    try:
        supabase = get_supabase()
        
        audit_entry = {
            "staff_id": staff_id,
            "restaurant_id": restaurant_id,
            "changed_by": changed_by,
            "action": action,
            "changed_fields": changed_fields,
            "ip_address": ip_address,
            "user_agent": user_agent
        }
        
        result = supabase.table('staff_audit_log').insert(audit_entry).execute()
        logger.info(f"Audit log created: {action} for staff {staff_id} by {changed_by}")
        
        return result.data
        
    except Exception as e:
        logger.error(f"Failed to create audit log: {e}")
        # Don't fail the operation if audit logging fails
        return None