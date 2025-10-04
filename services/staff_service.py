import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from database.supabase_client import get_supabase
from models.staff import StaffCreate, StaffUpdate
from services.audit_service import log_staff_change

logger = logging.getLogger(__name__)

async def get_staff_list(restaurant_id: int) -> List[Dict[str, Any]]:
    """Get all staff for a restaurant"""
    supabase = get_supabase()
    
    result = supabase.table('staff').select(
        'staff_id, email, full_name, position, hourly_rate, hire_date, status, '
        'portal_access, can_edit_staff, skills, notes'
    ).eq('restaurant_id', restaurant_id).execute()
    
    return result.data

async def create_staff_member(
    staff_data: StaffCreate,
    created_by: str,
    restaurant_id: int,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> Dict[str, Any]:
    """Create new staff member"""
    supabase = get_supabase()
    
    # Generate staff_id (you may have your own logic)
    staff_id = f"STAFF{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    new_staff = {
        "staff_id": staff_id,
        "email": staff_data.email,
        "full_name": staff_data.name,
        "position": staff_data.position,
        "hire_date": staff_data.hireDate.isoformat(),
        "hourly_rate": staff_data.payRate,
        "skills": staff_data.skills,
        "notes": staff_data.notes,
        "portal_access": staff_data.portal_access,
        "can_edit_staff": staff_data.can_edit_staff,
        "status": "Active",
        "restaurant_id": restaurant_id
    }
    
    result = supabase.table('staff').insert(new_staff).execute()
    
    # Log the change
    await log_staff_change(
        staff_id=staff_id,
        restaurant_id=restaurant_id,
        changed_by=created_by,
        action="CREATE",
        changed_fields={"created": new_staff},
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    return result.data[0]

async def update_staff_member(
    staff_id: str,
    staff_data: StaffUpdate,
    changed_by: str,
    restaurant_id: int,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> Dict[str, Any]:
    """Update existing staff member"""
    supabase = get_supabase()
    
    # Get current data for audit trail
    current = supabase.table('staff').select('*').eq('staff_id', staff_id).eq('restaurant_id', restaurant_id).single().execute()
    
    if not current.data:
        raise ValueError(f"Staff member {staff_id} not found")
    
    # Build update dict
    update_data = {
        "full_name": staff_data.name,
        "position": staff_data.position,
        "hire_date": staff_data.hireDate.isoformat(),
        "hourly_rate": staff_data.payRate,
        "skills": staff_data.skills,
        "notes": staff_data.notes,
        "portal_access": staff_data.portal_access,
        "can_edit_staff": staff_data.can_edit_staff
    }
    
    if staff_data.email:
        update_data["email"] = staff_data.email
    
    # Track what changed
    changed_fields = {}
    for key, new_value in update_data.items():
        old_value = current.data.get(key)
        if old_value != new_value:
            changed_fields[key] = {"old": old_value, "new": new_value}
    
    result = supabase.table('staff').update(update_data).eq('staff_id', staff_id).eq('restaurant_id', restaurant_id).execute()
    
    # Log the changes
    await log_staff_change(
        staff_id=staff_id,
        restaurant_id=restaurant_id,
        changed_by=changed_by,
        action="UPDATE",
        changed_fields=changed_fields,
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    return result.data[0]

async def deactivate_staff_member(
    staff_id: str,
    reason: str,
    last_work_date: str,
    notes: Optional[str],
    changed_by: str,
    restaurant_id: int,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> Dict[str, Any]:
    """Deactivate (soft delete) staff member"""
    supabase = get_supabase()
    
    update_data = {
        "status": "Inactive",
        "last_work_date": last_work_date,
        "removal_reason": reason,
        "removal_notes": notes
    }
    
    result = supabase.table('staff').update(update_data).eq('staff_id', staff_id).eq('restaurant_id', restaurant_id).execute()
    
    # Log the deactivation
    await log_staff_change(
        staff_id=staff_id,
        restaurant_id=restaurant_id,
        changed_by=changed_by,
        action="DEACTIVATE",
        changed_fields={"reason": reason, "last_work_date": last_work_date, "notes": notes},
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    return result.data[0]