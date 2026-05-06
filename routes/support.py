# routes/support.py
"""
Support Portal Routes
=====================
Internal support agent tools for safe, audited account management.
Restricted to support_agent portal_access (founder_ceo also allowed).
All write actions are logged to staff_audit_log with before/after state.
"""
import logging
import secrets
import bcrypt
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional
from database.supabase_client import get_supabase
from services.auth_service import verify_jwt_token
from services.twilio_service import send_sms

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/support", tags=["support"])

# ═══════════════════════════════════════════════════════════════════════════════
# AUTH GUARD
# ═══════════════════════════════════════════════════════════════════════════════

async def verify_support_agent(current_staff: dict = Depends(verify_jwt_token)):
    """Verify user has support_agent access. founder_ceo also permitted."""
    allowed = {"support_agent", "founder_ceo"}
    if current_staff.get("portal_access") not in allowed:
        raise HTTPException(status_code=403, detail="Support agent access required")
    return current_staff

# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def _write_audit(
    supabase,
    staff_id: str,
    organization_id: int,
    changed_by: str,
    action: str,
    changed_fields: dict,
    request: Request = None,
):
    """
    Write a row to staff_audit_log.
    organization_id = target staff member's organization_id (NOT the agent's).
    Falls back to 0 for internal/null restaurant staff.
    """
    entry = {
        "staff_id": staff_id,
        "organization_id": organization_id if organization_id is not None else 0,
        "changed_by": changed_by,
        "action": action,
        "changed_fields": changed_fields,
    }
    if request:
        entry["ip_address"] = request.client.host if request.client else None
        entry["user_agent"] = request.headers.get("user-agent", "")
    try:
        supabase.table("staff_audit_log").insert(entry).execute()
    except Exception as e:
        # Audit failure should never block the primary action, but must be logged
        logger.error(f"Audit log write failed: {e} | entry={entry}")


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class LookupRequest(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None

class ResetPasswordRequest(BaseModel):
    staff_id: str

class UpdateEmailRequest(BaseModel):
    staff_id: str
    new_email: str

class UpdatePhoneRequest(BaseModel):
    staff_id: str
    new_phone: str

class ToggleAccountRequest(BaseModel):
    staff_id: str
    action: str   # "enable" or "disable"
    reason: str

class ResendOnboardingRequest(BaseModel):
    staff_id: str

# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/lookup-staff")
async def lookup_staff(
    payload: LookupRequest,
    current_staff: dict = Depends(verify_support_agent),
):
    """
    Search for a staff member by email or phone.
    Returns a read-only snapshot including their restaurant context.
    """
    if not payload.email and not payload.phone:
        raise HTTPException(status_code=400, detail="Provide at least one of: email, phone")

    supabase = get_supabase()

    if payload.email:
        result = supabase.table("staff").select(
            "staff_id, full_name, email, phone, organization_id, status, "
            "is_portal_enabled, portal_access, last_login, created_at, position"
        ).eq("email", payload.email.strip().lower()).execute()
    else:
        result = supabase.table("staff").select(
            "staff_id, full_name, email, phone, organization_id, status, "
            "is_portal_enabled, portal_access, last_login, created_at, position"
        ).eq("phone", payload.phone.strip()).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="No staff member found with those credentials")

    staff = result.data[0]

    # Attach restaurant context if applicable
    if staff.get("organization_id"):
        r = supabase.table("organizations").select(
            "id, name, status, subscription_status, modules_enabled"
        ).eq("id", staff["organization_id"]).execute()
        staff["restaurant"] = r.data[0] if r.data else None

    return {"success": True, "staff": staff}


@router.post("/reset-password")
async def support_reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    current_staff: dict = Depends(verify_support_agent),
):
    """
    Trigger a password reset SMS for a staff member.
    Reuses the existing reset_token / reset_token_expires flow.
    Does NOT set a password directly — sends the staff member a 24hr link.
    """
    supabase = get_supabase()

    result = supabase.table("staff").select(
        "staff_id, full_name, phone, organization_id"
    ).eq("staff_id", payload.staff_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Staff not found")

    target = result.data[0]

    if not target.get("phone"):
        raise HTTPException(
            status_code=400,
            detail="This staff member has no phone number on file. Update their phone first, then retry."
        )

    reset_token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)

    supabase.table("staff").update({
        "reset_token": reset_token,
        "reset_token_expires": expires.isoformat(),
    }).eq("staff_id", payload.staff_id).execute()

    reset_url = f"https://app.en-place.ai/reset-password.html?token={reset_token}"
    first_name = target["full_name"].split()[0]

    sms_result = send_sms(
        target["phone"],
        f"Hi {first_name}! En Place support has sent you a password reset link:\n\n{reset_url}\n\nExpires in 24 hours. If you didn't request this, contact your manager."
    )

    if not sms_result.get("success"):
        logger.warning(f"Support reset SMS failed for {payload.staff_id}: {sms_result.get('error')}")

    _write_audit(
        supabase,
        staff_id=payload.staff_id,
        organization_id=target.get("organization_id"),
        changed_by=current_staff["staff_id"],
        action="support_password_reset",
        changed_fields={"note": "Support agent triggered password reset SMS", "sms_success": sms_result.get("success", False)},
        request=request,
    )

    return {
        "success": True,
        "message": f"Password reset SMS sent to {target['phone']}",
        "sms_delivered": sms_result.get("success", False),
    }


@router.post("/update-email")
async def support_update_email(
    payload: UpdateEmailRequest,
    request: Request,
    current_staff: dict = Depends(verify_support_agent),
):
    """Change a staff member's login email. Checks for conflicts before writing."""
    supabase = get_supabase()

    result = supabase.table("staff").select(
        "staff_id, full_name, email, organization_id"
    ).eq("staff_id", payload.staff_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Staff not found")

    target = result.data[0]
    old_email = target["email"]
    new_email = payload.new_email.strip().lower()

    if old_email == new_email:
        raise HTTPException(status_code=400, detail="New email is the same as current email")

    conflict = supabase.table("staff").select("staff_id").eq("email", new_email).execute()
    if conflict.data:
        raise HTTPException(status_code=409, detail="That email is already in use by another account")

    supabase.table("staff").update({"email": new_email}).eq("staff_id", payload.staff_id).execute()

    _write_audit(
        supabase,
        staff_id=payload.staff_id,
        organization_id=target.get("organization_id"),
        changed_by=current_staff["staff_id"],
        action="support_email_update",
        changed_fields={"before": {"email": old_email}, "after": {"email": new_email}},
        request=request,
    )

    return {"success": True, "message": f"Email updated for {target['full_name']}"}


@router.post("/update-phone")
async def support_update_phone(
    payload: UpdatePhoneRequest,
    request: Request,
    current_staff: dict = Depends(verify_support_agent),
):
    """Change a staff member's phone number."""
    supabase = get_supabase()

    result = supabase.table("staff").select(
        "staff_id, full_name, phone, organization_id"
    ).eq("staff_id", payload.staff_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Staff not found")

    target = result.data[0]
    old_phone = target.get("phone")

    supabase.table("staff").update({"phone": payload.new_phone.strip()}).eq(
        "staff_id", payload.staff_id
    ).execute()

    _write_audit(
        supabase,
        staff_id=payload.staff_id,
        organization_id=target.get("organization_id"),
        changed_by=current_staff["staff_id"],
        action="support_phone_update",
        changed_fields={"before": {"phone": old_phone}, "after": {"phone": payload.new_phone.strip()}},
        request=request,
    )

    return {"success": True, "message": f"Phone updated for {target['full_name']}"}


@router.post("/toggle-account")
async def support_toggle_account(
    payload: ToggleAccountRequest,
    request: Request,
    current_staff: dict = Depends(verify_support_agent),
):
    """
    Enable or disable a staff member's portal access.
    Requires a reason string — written to audit log.
    """
    if payload.action not in ("enable", "disable"):
        raise HTTPException(status_code=400, detail="action must be 'enable' or 'disable'")

    supabase = get_supabase()

    result = supabase.table("staff").select(
        "staff_id, full_name, is_portal_enabled, status, organization_id"
    ).eq("staff_id", payload.staff_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Staff not found")

    target = result.data[0]
    new_enabled = payload.action == "enable"
    new_status = "active" if new_enabled else "inactive"

    supabase.table("staff").update({
        "is_portal_enabled": new_enabled,
        "status": new_status,
    }).eq("staff_id", payload.staff_id).execute()

    _write_audit(
        supabase,
        staff_id=payload.staff_id,
        organization_id=target.get("organization_id"),
        changed_by=current_staff["staff_id"],
        action=f"support_account_{payload.action}d",
        changed_fields={
            "before": {"is_portal_enabled": target["is_portal_enabled"], "status": target["status"]},
            "after": {"is_portal_enabled": new_enabled, "status": new_status},
            "reason": payload.reason,
        },
        request=request,
    )

    return {
        "success": True,
        "message": f"Account {'enabled' if new_enabled else 'disabled'} for {target['full_name']}",
    }


@router.get("/restaurant/{organization_id}")
async def get_restaurant_snapshot(
    organization_id: int,
    current_staff: dict = Depends(verify_support_agent),
):
    """
    Read-only restaurant profile. Gives support agents context
    without exposing billing details beyond subscription status.
    """
    supabase = get_supabase()

    result = supabase.table("organizations").select(
        "id, name, address, phone, status, subscription_status, modules_enabled, "
        "has_stable_hire, has_house_guardian, has_open_shift_marketplace, "
        "has_shift_swap, has_schedule_optimizer, organization_subtype, created_at"
    ).eq("id", organization_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    restaurant = result.data[0]

    staff_result = supabase.table("staff").select("staff_id, status, portal_access").eq(
        "organization_id", organization_id
    ).execute()

    all_staff = staff_result.data or []
    active = [s for s in all_staff if s["status"] == "active"]
    managers = [s for s in all_staff if s.get("portal_access") == "manager"]

    restaurant["staff_summary"] = {
        "total": len(all_staff),
        "active": len(active),
        "managers": len(managers),
    }

    return {"success": True, "restaurant": restaurant}


@router.post("/resend-onboarding")
async def resend_onboarding_sms(
    payload: ResendOnboardingRequest,
    request: Request,
    current_staff: dict = Depends(verify_support_agent),
):
    """
    Re-send the onboarding SMS to a staff member.
    Generates a fresh reset_token (72hr expiry) so they land on the
    set-password page cleanly.
    """
    supabase = get_supabase()

    result = supabase.table("staff").select(
        "staff_id, full_name, phone, organization_id"
    ).eq("staff_id", payload.staff_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Staff not found")

    target = result.data[0]

    if not target.get("phone"):
        raise HTTPException(
            status_code=400,
            detail="Staff has no phone number. Add a phone number first, then resend."
        )

    reset_token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=72)

    supabase.table("staff").update({
        "reset_token": reset_token,
        "reset_token_expires": expires.isoformat(),
    }).eq("staff_id", payload.staff_id).execute()

    setup_url = f"https://app.en-place.ai/reset-password.html?token={reset_token}"
    first_name = target["full_name"].split()[0]

    sms_result = send_sms(
        target["phone"],
        f"Hi {first_name}! You've been invited to En Place, your restaurant's staff wellness platform. Set up your account here:\n\n{setup_url}\n\nLink expires in 72 hours."
    )

    _write_audit(
        supabase,
        staff_id=payload.staff_id,
        organization_id=target.get("organization_id"),
        changed_by=current_staff["staff_id"],
        action="support_resend_onboarding",
        changed_fields={
            "note": "Support agent re-sent onboarding SMS",
            "sms_success": sms_result.get("success", False),
        },
        request=request,
    )

    return {
        "success": True,
        "message": f"Onboarding SMS sent to {target['phone']}",
        "sms_delivered": sms_result.get("success", False),
    }


@router.get("/audit-log/{staff_id}")
async def get_staff_audit_log(
    staff_id: str,
    current_staff: dict = Depends(verify_support_agent),
):
    """Full audit history for a staff member. Most recent first, capped at 100 rows."""
    supabase = get_supabase()

    result = supabase.table("staff_audit_log").select(
        "id, action, changed_fields, changed_by, changed_at, ip_address"
    ).eq("staff_id", staff_id).order("changed_at", desc=True).limit(100).execute()

    return {"success": True, "staff_id": staff_id, "log": result.data or []}


@router.get("/my-actions")
async def get_my_recent_actions(
    current_staff: dict = Depends(verify_support_agent),
):
    """Agent's own recent 50 actions — personal accountability view."""
    supabase = get_supabase()

    result = supabase.table("staff_audit_log").select(
        "id, staff_id, action, changed_fields, changed_at, ip_address"
    ).eq("changed_by", current_staff["staff_id"]).order(
        "changed_at", desc=True
    ).limit(50).execute()

    return {"success": True, "agent_id": current_staff["staff_id"], "actions": result.data or []}