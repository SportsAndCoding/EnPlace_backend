"""
EN PLACE ONBOARDING WIZARD API
All endpoints for restaurant onboarding flow
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import date, datetime
import secrets
import string
from services.auth_service import verify_jwt_token as get_current_user
from database.supabase_client import get_supabase

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


# ═══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════

class BasicsUpdate(BaseModel):
    name: str
    address: str
    timezone: str


class HoursUpdate(BaseModel):
    operating_hours: dict  # jsonb: {"monday": {"open": "11:00", "close": "22:00"}, ...}


class PayrollUpdate(BaseModel):
    pay_frequency: str  # weekly, biweekly, semi_monthly, monthly
    next_pay_date: Optional[date] = None
    allow_overtime: bool = False


class StaffMember(BaseModel):
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    position: str
    hourly_rate: Optional[float] = None
    is_manager: bool = False


class BulkStaffUpload(BaseModel):
    staff: List[StaffMember]


class PermissionsUpdate(BaseModel):
    owner_staff_id: str
    manager_staff_ids: List[str] = []
    billing_admin_staff_id: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def generate_join_code(length: int = 6) -> str:
    """Generate a random alphanumeric join code"""
    chars = string.ascii_uppercase + string.digits
    # Exclude ambiguous characters
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '').replace('L', '')
    return ''.join(secrets.choice(chars) for _ in range(length))


def generate_staff_id(position: str, restaurant_id: int) -> str:
    """Generate a unique staff ID"""
    prefix = position[:3].upper() if position else "STF"
    timestamp = datetime.now().strftime("%H%M%S")
    random_suffix = ''.join(secrets.choice(string.digits) for _ in range(3))
    return f"{prefix}{restaurant_id}{timestamp}{random_suffix}"


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@router.get("/status")
async def get_onboarding_status(current_user: dict = Depends(get_current_user)):
    """Get current onboarding progress for restaurant"""
    
    restaurant_id = current_user['restaurant_id']
    supabase = get_supabase()
    
    try:
        # Get restaurant status
        restaurant = supabase.table('restaurants') \
            .select('id, name, address, timezone, status, operating_hours, pay_frequency, next_pay_date, allow_overtime') \
            .eq('id', restaurant_id) \
            .single() \
            .execute()
        
        if not restaurant.data:
            raise HTTPException(status_code=404, detail="Restaurant not found")
        
        # Get onboarding progress
        progress = supabase.table('restaurant_onboarding_status') \
            .select('*') \
            .eq('restaurant_id', restaurant_id) \
            .single() \
            .execute()
        
        # If no progress record, create one
        if not progress.data:
            supabase.table('restaurant_onboarding_status') \
                .insert({'restaurant_id': restaurant_id, 'setup_step': 'basics'}) \
                .execute()
            
            progress = supabase.table('restaurant_onboarding_status') \
                .select('*') \
                .eq('restaurant_id', restaurant_id) \
                .single() \
                .execute()
        
        # Get staff count
        staff_count = supabase.table('staff') \
            .select('staff_id', count='exact') \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        return {
            "success": True,
            "restaurant": restaurant.data,
            "progress": progress.data,
            "staff_count": staff_count.count or 0
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get onboarding status: {str(e)}")


@router.put("/basics")
async def update_basics(
    data: BasicsUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Step 1: Update restaurant basics (name, address, timezone)"""
    
    restaurant_id = current_user['restaurant_id']
    supabase = get_supabase()
    
    try:
        # Update restaurant
        supabase.table('restaurants') \
            .update({
                'name': data.name,
                'address': data.address,
                'timezone': data.timezone
            }) \
            .eq('id', restaurant_id) \
            .execute()
        
        # Update progress
        supabase.table('restaurant_onboarding_status') \
            .update({
                'basics_completed': True,
                'setup_step': 'hours',
                'current_step_started_at': datetime.utcnow().isoformat()
            }) \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        return {"success": True, "next_step": "hours"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update basics: {str(e)}")


@router.put("/hours")
async def update_hours(
    data: HoursUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Step 2: Update operating hours"""
    
    restaurant_id = current_user['restaurant_id']
    supabase = get_supabase()
    
    try:
        # Update restaurant
        supabase.table('restaurants') \
            .update({'operating_hours': data.operating_hours}) \
            .eq('id', restaurant_id) \
            .execute()
        
        # Update progress
        supabase.table('restaurant_onboarding_status') \
            .update({
                'operating_hours_completed': True,
                'setup_step': 'payroll',
                'current_step_started_at': datetime.utcnow().isoformat()
            }) \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        return {"success": True, "next_step": "payroll"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update hours: {str(e)}")


@router.put("/payroll")
async def update_payroll(
    data: PayrollUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Step 3: Update payroll settings"""
    
    restaurant_id = current_user['restaurant_id']
    supabase = get_supabase()
    
    # Validate pay_frequency
    valid_frequencies = ['weekly', 'biweekly', 'semi_monthly', 'monthly']
    if data.pay_frequency not in valid_frequencies:
        raise HTTPException(status_code=400, detail=f"Invalid pay_frequency. Must be one of: {valid_frequencies}")
    
    try:
        update_data = {
            'pay_frequency': data.pay_frequency,
            'allow_overtime': data.allow_overtime
        }
        
        if data.next_pay_date:
            update_data['next_pay_date'] = data.next_pay_date.isoformat()
        
        # Update restaurant
        supabase.table('restaurants') \
            .update(update_data) \
            .eq('id', restaurant_id) \
            .execute()
        
        # Update progress
        supabase.table('restaurant_onboarding_status') \
            .update({
                'payroll_completed': True,
                'setup_step': 'roster',
                'current_step_started_at': datetime.utcnow().isoformat()
            }) \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        return {"success": True, "next_step": "roster"}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update payroll: {str(e)}")


@router.post("/staff")
async def add_staff_member(
    data: StaffMember,
    current_user: dict = Depends(get_current_user)
):
    """Step 4: Add a single staff member"""
    
    restaurant_id = current_user['restaurant_id']
    supabase = get_supabase()
    
    try:
        staff_id = generate_staff_id(data.position, restaurant_id)
        full_name = f"{data.first_name} {data.last_name}"
        
        staff_data = {
            'staff_id': staff_id,
            'restaurant_id': restaurant_id,
            'full_name': full_name,
            'email': data.email,
            'phone': data.phone,
            'position': data.position,
            'hourly_rate': data.hourly_rate,
            'portal_access': 'manager' if data.is_manager else 'staff',
            'status': 'active',
            'hire_date': date.today().isoformat()
        }
        
        result = supabase.table('staff') \
            .insert(staff_data) \
            .execute()
        
        # Update staff count in onboarding status
        staff_count = supabase.table('staff') \
            .select('staff_id', count='exact') \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        supabase.table('restaurant_onboarding_status') \
            .update({'staff_count': staff_count.count or 0}) \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        return {
            "success": True,
            "staff_id": staff_id,
            "staff": result.data[0] if result.data else None
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add staff: {str(e)}")


@router.post("/staff/bulk")
async def add_staff_bulk(
    data: BulkStaffUpload,
    current_user: dict = Depends(get_current_user)
):
    """Step 4: Bulk add staff members"""
    
    restaurant_id = current_user['restaurant_id']
    supabase = get_supabase()
    
    try:
        added = []
        errors = []
        
        for member in data.staff:
            try:
                staff_id = generate_staff_id(member.position, restaurant_id)
                full_name = f"{member.first_name} {member.last_name}"
                
                staff_data = {
                    'staff_id': staff_id,
                    'restaurant_id': restaurant_id,
                    'full_name': full_name,
                    'email': member.email,
                    'phone': member.phone,
                    'position': member.position,
                    'hourly_rate': member.hourly_rate,
                    'portal_access': 'manager' if member.is_manager else 'staff',
                    'status': 'active',
                    'hire_date': date.today().isoformat()
                }
                
                supabase.table('staff').insert(staff_data).execute()
                added.append({'staff_id': staff_id, 'name': full_name})
                
            except Exception as e:
                errors.append({'name': f"{member.first_name} {member.last_name}", 'error': str(e)})
        
        # Update staff count
        staff_count = supabase.table('staff') \
            .select('staff_id', count='exact') \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        supabase.table('restaurant_onboarding_status') \
            .update({
                'staff_count': staff_count.count or 0,
                'staff_upload_completed': True
            }) \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        return {
            "success": True,
            "added_count": len(added),
            "added": added,
            "errors": errors
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to bulk add staff: {str(e)}")


@router.put("/permissions")
async def update_permissions(
    data: PermissionsUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Step 5: Set owner, managers, and billing admin"""
    
    restaurant_id = current_user['restaurant_id']
    supabase = get_supabase()
    
    try:
        # Set owner (full access + can_edit_staff)
        supabase.table('staff') \
            .update({
                'portal_access': 'manager',
                'can_edit_staff': True
            }) \
            .eq('staff_id', data.owner_staff_id) \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        # Set managers
        if data.manager_staff_ids:
            for staff_id in data.manager_staff_ids:
                supabase.table('staff') \
                    .update({'portal_access': 'manager'}) \
                    .eq('staff_id', staff_id) \
                    .eq('restaurant_id', restaurant_id) \
                    .execute()
        
        # Set billing admin on restaurant
        if data.billing_admin_staff_id:
            supabase.table('restaurants') \
                .update({'billing_admin_staff_id': data.billing_admin_staff_id}) \
                .eq('id', restaurant_id) \
                .execute()
        
        # Update progress
        supabase.table('restaurant_onboarding_status') \
            .update({
                'permissions_completed': True,
                'setup_step': 'qr',
                'current_step_started_at': datetime.utcnow().isoformat()
            }) \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        return {"success": True, "next_step": "qr"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update permissions: {str(e)}")


@router.post("/generate-join-code")
async def generate_join_code_endpoint(current_user: dict = Depends(get_current_user)):
    """Step 6: Generate staff join code and URL"""
    
    restaurant_id = current_user['restaurant_id']
    supabase = get_supabase()
    
    try:
        # Check if code already exists
        existing = supabase.table('restaurant_onboarding_status') \
            .select('join_code, join_url') \
            .eq('restaurant_id', restaurant_id) \
            .single() \
            .execute()
        
        if existing.data and existing.data.get('join_code'):
            # Return existing code
            return {
                "success": True,
                "join_code": existing.data['join_code'],
                "join_url": existing.data['join_url'],
                "already_generated": True
            }
        
        # Generate new code (ensure uniqueness)
        max_attempts = 10
        join_code = None
        
        for _ in range(max_attempts):
            candidate = generate_join_code(6)
            check = supabase.table('restaurant_onboarding_status') \
                .select('restaurant_id') \
                .eq('join_code', candidate) \
                .execute()
            
            if not check.data:
                join_code = candidate
                break
        
        if not join_code:
            raise HTTPException(status_code=500, detail="Failed to generate unique join code")
        
        # Build join URL
        join_url = f"https://enplace.app/join/{join_code}"
        
        # Save code
        supabase.table('restaurant_onboarding_status') \
            .update({
                'join_code': join_code,
                'join_url': join_url,
                'qr_generated': True,
                'setup_step': 'review',
                'current_step_started_at': datetime.utcnow().isoformat()
            }) \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        return {
            "success": True,
            "join_code": join_code,
            "join_url": join_url,
            "already_generated": False
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate join code: {str(e)}")


@router.post("/complete")
async def complete_onboarding(current_user: dict = Depends(get_current_user)):
    """Step 7: Complete onboarding and activate restaurant"""
    
    restaurant_id = current_user['restaurant_id']
    supabase = get_supabase()
    
    try:
        # Verify all steps completed
        progress = supabase.table('restaurant_onboarding_status') \
            .select('*') \
            .eq('restaurant_id', restaurant_id) \
            .single() \
            .execute()
        
        if not progress.data:
            raise HTTPException(status_code=400, detail="Onboarding not started")
        
        p = progress.data
        incomplete = []
        
        if not p.get('basics_completed'):
            incomplete.append('basics')
        if not p.get('operating_hours_completed'):
            incomplete.append('hours')
        if not p.get('payroll_completed'):
            incomplete.append('payroll')
        if not p.get('staff_upload_completed') and (p.get('staff_count', 0) == 0):
            incomplete.append('roster')
        if not p.get('permissions_completed'):
            incomplete.append('permissions')
        if not p.get('qr_generated'):
            incomplete.append('qr')
        
        if incomplete:
            raise HTTPException(
                status_code=400, 
                detail=f"Incomplete steps: {', '.join(incomplete)}"
            )
        
        # Activate restaurant
        supabase.table('restaurants') \
            .update({'status': 'active'}) \
            .eq('id', restaurant_id) \
            .execute()
        
        # Mark onboarding complete
        supabase.table('restaurant_onboarding_status') \
            .update({
                'setup_step': 'complete',
                'onboarding_completed_at': datetime.utcnow().isoformat()
            }) \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        return {
            "success": True,
            "status": "active",
            "message": "Restaurant activated! Redirecting to dashboard..."
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to complete onboarding: {str(e)}")