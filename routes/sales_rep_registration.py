# routes/sales_rep_registration.py
"""
Sales Rep Registration Routes
=============================
Handles sales rep onboarding via invite tokens.
No Stripe involved - just invite → validate → register flow.
"""

import os
import bcrypt
import secrets
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import Optional
from database.supabase_client import get_supabase
from services.auth_service import verify_jwt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sales-rep", tags=["sales-rep-registration"])


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class CreateInviteRequest(BaseModel):
    email: EmailStr
    full_name: str
    role: Optional[str] = "sales_rep"  # sales_rep, sales_captain, sales_director


class CreateInviteResponse(BaseModel):
    success: bool
    invite_token: str
    invite_url: str
    expires_at: str


class ValidateInviteResponse(BaseModel):
    valid: bool
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    error: Optional[str] = None


class RegisterSalesRepRequest(BaseModel):
    invite_token: str
    email: EmailStr
    password: str
    full_name: str
    phone: Optional[str] = None


class RegisterSalesRepResponse(BaseModel):
    success: bool
    staff_id: str
    token: str
    message: str


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def generate_staff_id() -> str:
    """Generate unique SALES### staff ID"""
    supabase = get_supabase()
    
    # Get current max SALES ID
    result = supabase.table("staff") \
        .select("staff_id") \
        .like("staff_id", "SALES%") \
        .execute()
    
    if result.data:
        # Extract numbers and find max
        max_num = 0
        for row in result.data:
            sid = row["staff_id"]
            if sid.startswith("SALES"):
                try:
                    num = int(sid.replace("SALES", ""))
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        next_num = max_num + 1
    else:
        next_num = 2  # Start at SALES002 since SALES001 exists
    
    return f"SALES{next_num:03d}"


def generate_invite_token() -> str:
    """Generate secure 64-char invite token"""
    return secrets.token_hex(32)


def create_jwt_token(staff_data: dict) -> str:
    """Create JWT token for authenticated staff"""
    import jwt

    JWT_SECRET = os.environ.get("JWT_SECRET", "your-secret-key")
    JWT_ALGORITHM = "HS256"

    payload = {
        "staff_id": staff_data["staff_id"],
        "email": staff_data["email"],
        "full_name": staff_data["full_name"],
        "position": staff_data["position"],
        "portal_access": staff_data["portal_access"],
        "organization_id": staff_data.get("organization_id"),  # None for En Place staff
        "can_edit_staff": staff_data.get("can_edit_staff", False),
        "exp": datetime.utcnow() + timedelta(hours=24),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# Roles allowed to create invites
INVITE_CREATORS = ['founder_ceo', 'recruiter', 'sales_director']

# Valid sales roles
VALID_SALES_ROLES = ['sales_rep', 'sales_captain', 'sales_director']


# ═══════════════════════════════════════════════════════════════════════════════
# CREATE INVITE (Admin only)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/create-invite", response_model=CreateInviteResponse)
async def create_invite(
    request: CreateInviteRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """
    Create an invite token for a new sales rep.
    Only founder_ceo, recruiter, or sales_director can create invites.
    """
    # Check permission
    if current_staff.get("portal_access") not in INVITE_CREATORS:
        raise HTTPException(
            status_code=403,
            detail="Only founders, recruiters, or sales directors can create invites"
        )
    
    # Validate role
    if request.role not in VALID_SALES_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role. Must be one of: {VALID_SALES_ROLES}"
        )
    
    supabase = get_supabase()
    
    # Check if email already registered
    existing = supabase.table("staff") \
        .select("staff_id") \
        .eq("email", request.email.lower()) \
        .execute()
    if existing.data:
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )
    # Check for existing unused invite
    existing_invite = supabase.table("sales_rep_invites") \
        .select("id, invite_token") \
        .eq("email", request.email.lower()) \
        .is_("used_at", "null") \
        .execute()
    
    if existing_invite.data:
        # Return existing invite instead of creating duplicate
        token = existing_invite.data[0]["invite_token"]
        # Update expiration
        expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat()
        supabase.table("sales_rep_invites") \
            .update({"expires_at": expires_at}) \
            .eq("id", existing_invite.data[0]["id"]) \
            .execute()
        
        return CreateInviteResponse(
            success=True,
            invite_token=token,
            invite_url=f"https://app.en-place.ai/register-sales-rep/?invite={token}",
            expires_at=expires_at
        )
    
    # Generate new invite
    token = generate_invite_token()
    expires_at = datetime.utcnow() + timedelta(days=7)
    
    try:
        supabase.table("sales_rep_invites").insert({
            "invite_token": token,
            "email": request.email.lower(),
            "full_name": request.full_name,
            "role": request.role,
            "created_by": current_staff.get("staff_id"),
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": expires_at.isoformat()
        }).execute()
        
        logger.info(f"Created sales rep invite for {request.email} by {current_staff.get('staff_id')}")
        
        return CreateInviteResponse(
            success=True,
            invite_token=token,
            invite_url=f"https://app.en-place.ai/register-sales-rep/?invite={token}",
            expires_at=expires_at.isoformat()
        )
        
    except Exception as e:
        logger.error(f"Error creating invite: {e}")
        raise HTTPException(status_code=500, detail="Failed to create invite")


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATE INVITE (Public - called by registration page)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/validate-invite/{token}")
async def validate_invite(token: str):
    """
    Validate an invite token and return pre-filled data.
    Public endpoint - no auth required.
    """
    supabase = get_supabase()
    
    result = supabase.table("sales_rep_invites") \
        .select("*") \
        .eq("invite_token", token) \
        .execute()
    
    if not result.data:
        return ValidateInviteResponse(
            valid=False,
            error="Invalid invite token"
        )
    
    invite = result.data[0]
    
    # Check if already used
    if invite.get("used_at"):
        return ValidateInviteResponse(
            valid=False,
            error="This invite has already been used"
        )
    
    # Check expiration
    expires_at = datetime.fromisoformat(invite["expires_at"].replace("Z", "+00:00"))
    if datetime.now(expires_at.tzinfo) > expires_at:
        return ValidateInviteResponse(
            valid=False,
            error="This invite has expired. Please request a new one."
        )
    
    return ValidateInviteResponse(
        valid=True,
        email=invite.get("email"),
        full_name=invite.get("full_name"),
        role=invite.get("role", "sales_rep")
    )


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTER SALES REP (Public - consumes invite token)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/register", response_model=RegisterSalesRepResponse)
async def register_sales_rep(request: RegisterSalesRepRequest):
    """
    Register a new sales rep using an invite token.
    
    Flow:
    1. Validate invite token
    2. Create staff record with sales_rep portal_access
    3. Mark invite as used
    4. Return JWT token for immediate login
    """
    supabase = get_supabase()
    
    # Step 1: Validate invite token
    invite_result = supabase.table("sales_rep_invites") \
        .select("*") \
        .eq("invite_token", request.invite_token) \
        .execute()
    
    if not invite_result.data:
        raise HTTPException(status_code=400, detail="Invalid invite token")
    
    invite = invite_result.data[0]
    
    if invite.get("used_at"):
        raise HTTPException(status_code=400, detail="This invite has already been used")
    
    expires_at = datetime.fromisoformat(invite["expires_at"].replace("Z", "+00:00"))
    if datetime.now(expires_at.tzinfo) > expires_at:
        raise HTTPException(status_code=400, detail="This invite has expired")
    
    # Verify email matches invite (security check)
    if request.email.lower() != invite["email"].lower():
        raise HTTPException(
            status_code=400,
            detail="Email does not match the invite. Please use the email the invite was sent to."
        )
    
    # Check if email already registered (double-check)
    existing = supabase.table("staff") \
        .select("staff_id") \
        .eq("email", request.email) \
        .execute()
    
    if existing.data:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Step 2: Create staff record
    try:
        staff_id = generate_staff_id()
        password_hash = hash_password(request.password)
        role = invite.get("role", "sales_rep")
        
        staff_data = {
            "staff_id": staff_id,
            "organization_id": None,  # En Place staff, not restaurant staff
            "email": request.email.lower(),
            "password_hash": password_hash,
            "full_name": request.full_name,
            "phone": request.phone,
            "position": "Sales Representative",
            "portal_access": role,
            "can_edit_staff": False,
            "is_owner": False,
            "status": "active",
            "hire_date": datetime.utcnow().date().isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Adjust position based on role
        if role == "sales_captain":
            staff_data["position"] = "Sales Captain"
        elif role == "sales_director":
            staff_data["position"] = "Sales Director"
        
        staff_result = supabase.table("staff").insert(staff_data).execute()
        
        if not staff_result.data:
            raise HTTPException(status_code=500, detail="Failed to create account")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating sales rep: {e}")
        raise HTTPException(status_code=500, detail="Failed to create account")
    
    # Step 3: Mark invite as used
    try:
        supabase.table("sales_rep_invites") \
            .update({
                "used_at": datetime.utcnow().isoformat(),
                "staff_id_created": staff_id
            }) \
            .eq("invite_token", request.invite_token) \
            .execute()
    except Exception as e:
        logger.warning(f"Failed to mark invite as used (non-critical): {e}")
    
    # Step 4: Generate JWT token
    token = create_jwt_token({
        "staff_id": staff_id,
        "email": request.email.lower(),
        "full_name": request.full_name,
        "position": staff_data["position"],
        "portal_access": role,
        "organization_id": None,
        "can_edit_staff": False
    })
    
    logger.info(f"Registered new sales rep: {request.full_name} ({staff_id})")
    
    return RegisterSalesRepResponse(
        success=True,
        staff_id=staff_id,
        token=token,
        message="Registration successful! Welcome to En Place."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LIST INVITES (Admin view)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/invites")
async def list_invites(
    current_staff: dict = Depends(verify_jwt_token),
    status: Optional[str] = None  # pending, used, expired
):
    """
    List all invites. For admin dashboard.
    """
    if current_staff.get("portal_access") not in INVITE_CREATORS:
        raise HTTPException(status_code=403, detail="Access denied")
    
    supabase = get_supabase()
    
    query = supabase.table("sales_rep_invites") \
        .select("*") \
        .order("created_at", desc=True)
    
    result = query.execute()
    
    invites = []
    now = datetime.utcnow()
    
    for invite in result.data:
        expires_at = datetime.fromisoformat(invite["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        
        if invite.get("used_at"):
            invite_status = "used"
        elif now > expires_at:
            invite_status = "expired"
        else:
            invite_status = "pending"
        
        # Filter by status if provided
        if status and invite_status != status:
            continue
        
        invites.append({
            "id": invite["id"],
            "email": invite["email"],
            "full_name": invite["full_name"],
            "role": invite.get("role", "sales_rep"),
            "status": invite_status,
            "created_at": invite["created_at"],
            "expires_at": invite["expires_at"],
            "used_at": invite.get("used_at"),
            "staff_id_created": invite.get("staff_id_created"),
            "invite_url": f"https://app.en-place.ai/register-sales-rep/?invite={invite['invite_token']}"
        })
    
    return {"invites": invites}