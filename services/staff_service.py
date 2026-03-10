import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from database.supabase_client import get_supabase
from models.staff import StaffCreate, StaffUpdate
from services.audit_service import log_staff_change
from services.cascade_trigger import trigger_exit_cascade

logger = logging.getLogger(__name__)

async def get_staff_list(restaurant_id: int) -> List[Dict[str, Any]]:
    supabase = get_supabase()  # Fresh client
    result = supabase.table('staff').select(
        'staff_id, email, full_name, position, hourly_rate, hire_date, status, '
        'portal_access, can_edit_staff, skills, notes, is_owner, strategic_alerts_only'
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
    
    # Generate staff_id
    staff_id = f"STAFF{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # Generate a default password hash (they'll need to reset it)
    import bcrypt
    default_password = "ChangeMe123!"  # Temporary password
    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(default_password.encode('utf-8'), salt).decode('utf-8')
    
    new_staff = {
        "staff_id": staff_id,
        "email": staff_data.email,
        "full_name": staff_data.name,
        "position": staff_data.position,
        "hire_date": staff_data.hireDate.isoformat(),
        "skills": staff_data.skills,
        "notes": staff_data.notes,
        "portal_access": staff_data.portal_access,
        "can_edit_staff": staff_data.can_edit_staff,
        "status": "Active",
        "restaurant_id": restaurant_id,
        "password_hash": password_hash  # ADD THIS LINE
    }

    # Only set hourly_rate if payRate was provided
    if hasattr(staff_data, "payRate") and staff_data.payRate is not None:
        new_staff["hourly_rate"] = staff_data.payRate

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
        "skills": staff_data.skills,
        "notes": staff_data.notes,
        "portal_access": staff_data.portal_access,
        "can_edit_staff": staff_data.can_edit_staff
    }
    
    # Only update phone if provided
    if hasattr(staff_data, 'phone') and staff_data.phone is not None:
        update_data["phone"] = staff_data.phone
    
    # Only update hourly_rate if payRate was explicitly provided
    if hasattr(staff_data, "payRate") and staff_data.payRate is not None:
        update_data["hourly_rate"] = staff_data.payRate
    
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

    # Trigger cascade analysis — create escalations for at-risk connected staff
    departed_name = result.data[0].get("full_name", "A team member") if result.data else "A team member"
    try:
        cascade_result = trigger_exit_cascade(
            departed_staff_id=staff_id,
            restaurant_id=restaurant_id,
            departed_name=departed_name,
        )
        if cascade_result.get("escalations_created"):
            print(f"[CASCADE] {departed_name} exit triggered {cascade_result['escalations_created']} escalations")
    except Exception as e:
        # Never block deactivation if cascade fails
        print(f"[CASCADE] Trigger failed (non-blocking): {e}")

    return result.data[0]