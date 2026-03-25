# routes/partner.py
"""
En Place Partner Program API
=============================
Enrollment, certification, referral pipeline, and commission tracking
for Proof Intelligence partners who sell En Place.

Prefix: /api/proof/partner
Auth: verify_proof_token (partner-facing), verify_enplace_admin (internal)
"""

import os
import jwt
import stripe
import secrets
import string
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from database.supabase_client import get_supabase
from config.settings import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_HOURS
from routes.proof import verify_proof_token, create_proof_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/proof/partner", tags=["partner"])
security = HTTPBearer()

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

STRIPE_PRICE_PARTNER_CERT = os.environ.get("STRIPE_PRICE_PARTNER_CERT")

PARTNER_CERT_SUCCESS_URL = "https://proof.en-place.ai/partner-portal?session_id={CHECKOUT_SESSION_ID}"
PARTNER_CONNECT_RETURN_URL = "https://proof.en-place.ai/partner-portal"
PARTNER_CONNECT_REFRESH_URL = "https://proof.en-place.ai/partner-portal?connect_refresh=true"

CERTIFICATION_MODULES = [
    "product_deep_dive",
    "service_profit_chain",
    "restaurant_pnl",
    "identifying_buyers",
    "demo_walkthrough",
    "objection_handling",
    "compliance_boundaries",
    "final_assessment",
]

ASSESSMENT_PASS_SCORE = 80.0
ASSESSMENT_MAX_ATTEMPTS = 2
DEFAULT_MONTHLY_CREDIT_AMOUNT = 155.00


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class CertificationCompleteRequest(BaseModel):
    score: Optional[float] = None   # Required for final_assessment

class CertificationProgressRequest(BaseModel):
    time_spent_seconds: int = 0

class ReferralSubmitRequest(BaseModel):
    restaurant_name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    city: str
    state: str
    notes: Optional[str] = None
    proof_contact_id: Optional[str] = None

class AdminReferralUpdateRequest(BaseModel):
    status: Optional[str] = None
    sales_lead_id: Optional[str] = None
    en_place_restaurant_id: Optional[int] = None
    monthly_subscription_value: Optional[float] = None

class AdminPartnerUpdateRequest(BaseModel):
    status: str   # suspended, active, certified


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def verify_enplace_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Verify the caller is an En Place admin (staff JWT with admin portal_access)."""
    try:
        payload = jwt.decode(
            credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
        if payload.get("portal_access") not in ("founder_ceo", "sales_director"):
            raise HTTPException(status_code=403, detail="Admin access required")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


def generate_referral_code() -> str:
    """Generate a code like EP-7K2M. 4 alphanumeric chars = 1.6M combinations."""
    chars = string.ascii_uppercase + string.digits
    code = ''.join(secrets.choice(chars) for _ in range(4))
    return f"EP-{code}"


# ═══════════════════════════════════════════════════════════════════════════════
# ENROLLMENT
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/enroll")
async def partner_enroll(current_user: dict = Depends(verify_proof_token)):
    """
    Start the partner enrollment process.
    Creates a Stripe checkout session for the $500 certification fee.
    """
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    # Check if already enrolled
    existing = supabase.table("proof_partners") \
        .select("id, status") \
        .eq("user_id", user_id) \
        .execute()

    if existing.data:
        partner = existing.data[0]
        if partner["status"] == "failed":
            # Re-enrollment: delete old record so they can start fresh
            supabase.table("proof_partner_certification") \
                .delete() \
                .eq("partner_id", partner["id"]) \
                .execute()
            supabase.table("proof_partners") \
                .delete() \
                .eq("id", partner["id"]) \
                .execute()
        else:
            raise HTTPException(
                status_code=409,
                detail=f"You are already enrolled in the partner program (status: {partner['status']})"
            )

    if not STRIPE_PRICE_PARTNER_CERT:
        raise HTTPException(
            status_code=500,
            detail="Partner certification pricing not configured. Contact support."
        )

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_PARTNER_CERT, "quantity": 1}],
            success_url=PARTNER_CERT_SUCCESS_URL,
            cancel_url=PARTNER_CERT_CANCEL_URL,
            metadata={
                "proof_user_id": user_id,
                "partner_certification": "true"
            }
        )
        return {"success": True, "session_id": session.id, "url": session.url}

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
async def partner_status(current_user: dict = Depends(verify_proof_token)):
    """Get partner status, tier info, and commission summary."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    partner = supabase.table("proof_partners") \
        .select("*") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data:
        return {"success": True, "enrolled": False}

    p = partner.data[0]
    return {
        "success": True,
        "enrolled": True,
        "partner": {
            "id": p["id"],
            "status": p["status"],
            "referral_code": p["referral_code"],
            "certified_at": p["certified_at"],
            "partner_tier_expires_at": p["partner_tier_expires_at"],
            "total_referrals": p["total_referrals"],
            "active_referrals": p["active_referrals"],
            "total_commission_earned": float(p["total_commission_earned"] or 0),
            "total_commission_paid": float(p["total_commission_paid"] or 0),
            "pending_commission": float(p["pending_commission"] or 0),
            "monthly_credit_amount": float(p["monthly_credit_amount"] or 0),
            "last_credit_grant_at": p["last_credit_grant_at"],
            "stripe_connect_onboarded": p["stripe_connect_onboarded"],
            "created_at": p["created_at"],
        }
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CERTIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/certification")
async def get_certification(current_user: dict = Depends(verify_proof_token)):
    """Get all certification module statuses for the current partner."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    partner = supabase.table("proof_partners") \
        .select("id, status") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data:
        raise HTTPException(status_code=404, detail="Not enrolled in partner program")

    if partner.data[0]["status"] not in ("pending_cert", "certified", "active"):
        raise HTTPException(
            status_code=403,
            detail=f"Partner status is '{partner.data[0]['status']}'. Certification not accessible."
        )

    modules = supabase.table("proof_partner_certification") \
        .select("*") \
        .eq("partner_id", partner.data[0]["id"]) \
        .execute()

    return {
        "success": True,
        "partner_status": partner.data[0]["status"],
        "modules": modules.data
    }


@router.post("/certification/{module_id}/progress")
async def update_certification_progress(
    module_id: str,
    data: CertificationProgressRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Update time spent on a module and mark as in_progress."""
    if module_id not in CERTIFICATION_MODULES:
        raise HTTPException(status_code=400, detail=f"Invalid module: {module_id}")

    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    partner = supabase.table("proof_partners") \
        .select("id, status") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data or partner.data[0]["status"] != "pending_cert":
        raise HTTPException(status_code=403, detail="Certification not in progress")

    partner_id = partner.data[0]["id"]

    # Get current module state
    module = supabase.table("proof_partner_certification") \
        .select("id, status, time_spent_seconds") \
        .eq("partner_id", partner_id) \
        .eq("module_id", module_id) \
        .single() \
        .execute()

    if not module.data:
        raise HTTPException(status_code=404, detail="Module not found")

    if module.data["status"] == "completed":
        return {"success": True, "message": "Module already completed"}

    new_time = (module.data["time_spent_seconds"] or 0) + data.time_spent_seconds

    supabase.table("proof_partner_certification") \
        .update({
            "status": "in_progress",
            "time_spent_seconds": new_time
        }) \
        .eq("id", module.data["id"]) \
        .execute()

    return {"success": True, "time_spent_seconds": new_time}


@router.post("/certification/{module_id}/complete")
async def complete_certification_module(
    module_id: str,
    data: CertificationCompleteRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """
    Mark a certification module as complete.

    For final_assessment:
    - Requires score in request body
    - Pass threshold: 80%
    - Max 2 attempts. Second failure = must re-enroll ($500)
    - On pass: triggers pending_cert -> certified transition, returns fresh JWT
    """
    if module_id not in CERTIFICATION_MODULES:
        raise HTTPException(status_code=400, detail=f"Invalid module: {module_id}")

    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    partner = supabase.table("proof_partners") \
        .select("id, status, monthly_credit_amount") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data or partner.data[0]["status"] != "pending_cert":
        raise HTTPException(status_code=403, detail="Certification not in progress")

    partner_id = partner.data[0]["id"]

    module = supabase.table("proof_partner_certification") \
        .select("*") \
        .eq("partner_id", partner_id) \
        .eq("module_id", module_id) \
        .single() \
        .execute()

    if not module.data:
        raise HTTPException(status_code=404, detail="Module not found")

    if module.data["status"] == "completed":
        return {"success": True, "message": "Module already completed", "already_complete": True}

    # ── Final assessment has special logic ──
    if module_id == "final_assessment":
        if data.score is None:
            raise HTTPException(status_code=400, detail="Score is required for the final assessment")

        current_attempts = (module.data["attempts"] or 0) + 1

        if data.score >= ASSESSMENT_PASS_SCORE:
            # PASS — mark module complete
            supabase.table("proof_partner_certification") \
                .update({
                    "status": "completed",
                    "score": data.score,
                    "attempts": current_attempts,
                    "completed_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", module.data["id"]) \
                .execute()

            # Trigger certification completion
            result = await _complete_certification(partner_id, user_id, data.score, supabase)
            return result

        else:
            # FAIL
            if current_attempts >= ASSESSMENT_MAX_ATTEMPTS:
                # Second fail — partner must re-enroll
                supabase.table("proof_partner_certification") \
                    .update({
                        "status": "failed",
                        "score": data.score,
                        "attempts": current_attempts
                    }) \
                    .eq("id", module.data["id"]) \
                    .execute()

                supabase.table("proof_partners") \
                    .update({
                        "status": "failed",
                        "updated_at": datetime.utcnow().isoformat()
                    }) \
                    .eq("id", partner_id) \
                    .execute()

                return {
                    "success": False,
                    "passed": False,
                    "score": data.score,
                    "attempts_used": current_attempts,
                    "max_attempts": ASSESSMENT_MAX_ATTEMPTS,
                    "message": "Assessment failed. Maximum attempts reached. You must re-enroll to try again."
                }
            else:
                # First fail — retake available
                supabase.table("proof_partner_certification") \
                    .update({
                        "status": "not_started",
                        "score": data.score,
                        "attempts": current_attempts
                    }) \
                    .eq("id", module.data["id"]) \
                    .execute()

                return {
                    "success": False,
                    "passed": False,
                    "score": data.score,
                    "attempts_used": current_attempts,
                    "max_attempts": ASSESSMENT_MAX_ATTEMPTS,
                    "message": f"Score {data.score}% did not meet the {ASSESSMENT_PASS_SCORE}% threshold. You have 1 retake remaining."
                }

    # ── Regular module (not assessment) ──
    supabase.table("proof_partner_certification") \
        .update({
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat()
        }) \
        .eq("id", module.data["id"]) \
        .execute()

    return {"success": True, "module_id": module_id, "status": "completed"}


async def _complete_certification(partner_id: str, user_id: str, score: float, supabase) -> dict:
    """
    Handle the pending_cert -> certified transition.
    - Verify all modules are complete
    - Flip plan to 'partner'
    - Grant immediate credits
    - Handle Stripe sub cancellation (safe)
    - Return fresh JWT
    """
    # Verify ALL modules are complete
    modules = supabase.table("proof_partner_certification") \
        .select("module_id, status") \
        .eq("partner_id", partner_id) \
        .execute()

    incomplete = [m for m in modules.data if m["status"] != "completed"]
    if incomplete:
        incomplete_ids = [m["module_id"] for m in incomplete]
        return {
            "success": True,
            "passed": True,
            "score": score,
            "certified": False,
            "message": f"Assessment passed! Complete remaining modules to certify: {', '.join(incomplete_ids)}"
        }

    # All modules complete — certify
    now = datetime.utcnow()
    monthly_credit_amount = DEFAULT_MONTHLY_CREDIT_AMOUNT

    # Get partner record for Stripe sub context
    partner = supabase.table("proof_partners") \
        .select("is_org_member, previous_stripe_sub_id, monthly_credit_amount") \
        .eq("id", partner_id) \
        .single() \
        .execute()

    if partner.data.get("monthly_credit_amount"):
        monthly_credit_amount = float(partner.data["monthly_credit_amount"])

    # 1. Update partner status
    supabase.table("proof_partners") \
        .update({
            "status": "certified",
            "certified_at": now.isoformat(),
            "assessment_score": score,
            "partner_tier_started_at": now.isoformat(),
            "partner_tier_expires_at": (now + timedelta(days=180)).isoformat(),
            "last_credit_grant_at": now.isoformat(),
            "updated_at": now.isoformat()
        }) \
        .eq("id", partner_id) \
        .execute()

    # 2. Flip user plan to 'partner'
    supabase.table("proof_users") \
        .update({"plan": "partner", "plan_status": "active"}) \
        .eq("id", user_id) \
        .execute()

    # 3. Grant immediate credits
    user = supabase.table("proof_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()

    current_balance = float(user.data.get("credit_balance", 0))
    new_balance = round(current_balance + monthly_credit_amount, 2)

    supabase.table("proof_users") \
        .update({"credit_balance": new_balance}) \
        .eq("id", user_id) \
        .execute()

    supabase.table("proof_credit_transactions").insert({
        "user_id": user_id,
        "transaction_type": "partner_grant",
        "amount": monthly_credit_amount,
        "balance_after": new_balance,
        "description": "Partner certification — initial credit grant",
        "created_at": now.isoformat()
    }).execute()

    # 4. Safe Stripe sub cancellation (only solo individual plans)
    if not partner.data.get("is_org_member") and partner.data.get("previous_stripe_sub_id"):
        try:
            stripe.Subscription.cancel(partner.data["previous_stripe_sub_id"])
            logger.info(f"Cancelled Stripe sub {partner.data['previous_stripe_sub_id']} for new partner {partner_id}")
        except stripe.error.StripeError as e:
            logger.warning(f"Failed to cancel Stripe sub for partner {partner_id}: {e}")

    # 5. Generate fresh JWT with plan='partner'
    updated_user = supabase.table("proof_users") \
        .select("*") \
        .eq("id", user_id) \
        .single() \
        .execute()

    new_token = create_proof_token(updated_user.data)

    return {
        "success": True,
        "passed": True,
        "score": score,
        "certified": True,
        "token": new_token,
        "credits_granted": monthly_credit_amount,
        "balance": new_balance,
        "partner_tier_expires_at": (now + timedelta(days=180)).isoformat(),
        "message": "Congratulations! You are now a certified En Place Partner."
    }


# ═══════════════════════════════════════════════════════════════════════════════
# REFERRALS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/referrals")
async def submit_referral(
    data: ReferralSubmitRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """
    Submit a restaurant referral.
    Three-layer dedup: cross-partner, sales pipeline, active subscribers.
    """
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    # Must be certified or active
    partner = supabase.table("proof_partners") \
        .select("id, status") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data:
        raise HTTPException(status_code=403, detail="Not enrolled in partner program")

    if partner.data[0]["status"] not in ("certified", "active"):
        raise HTTPException(
            status_code=403,
            detail=f"Referrals require certified or active status. Current: {partner.data[0]['status']}"
        )

    partner_id = partner.data[0]["id"]
    name_clean = data.restaurant_name.strip()
    city_clean = data.city.strip()
    state_clean = data.state.strip().upper()

    if not name_clean or not city_clean or not state_clean:
        raise HTTPException(status_code=400, detail="Restaurant name, city, and state are required")

    # ── Layer 1: Cross-partner dedup ──
    # The unique index will catch this on insert, but we check first for a clear error message
    existing_referral = supabase.table("proof_partner_referrals") \
        .select("id, partner_id") \
        .ilike("restaurant_name", name_clean) \
        .ilike("city", city_clean) \
        .ilike("state", state_clean) \
        .not_.in_("status", ["rejected", "churned"]) \
        .execute()

    if existing_referral.data:
        if existing_referral.data[0]["partner_id"] == partner_id:
            raise HTTPException(status_code=409, detail="You have already referred this restaurant.")
        else:
            raise HTTPException(status_code=409, detail="This restaurant has already been referred by another partner.")

    # ── Layer 2: Sales pipeline check ──
    pipeline_check = supabase.table("sales_leads") \
        .select("id, stage") \
        .ilike("restaurant_name", f"%{name_clean}%") \
        .ilike("city_state", f"%{city_clean}%") \
        .execute()

    if pipeline_check.data:
        raise HTTPException(
            status_code=409,
            detail="This restaurant is already in the En Place sales pipeline."
        )

    # ── Layer 3: Active subscriber check ──
    # restaurants table uses 'name' column — need to verify
    # Using ilike for fuzzy match since restaurant names may differ slightly
    subscriber_check = supabase.table("restaurants") \
        .select("id, name") \
        .ilike("name", f"%{name_clean}%") \
        .execute()

    if subscriber_check.data:
        raise HTTPException(
            status_code=409,
            detail="This restaurant is already an En Place subscriber."
        )

    # ── Insert referral ──
    try:
        referral = supabase.table("proof_partner_referrals").insert({
            "partner_id": partner_id,
            "restaurant_name": name_clean,
            "contact_name": data.contact_name,
            "contact_email": data.contact_email,
            "contact_phone": data.contact_phone,
            "city": city_clean,
            "state": state_clean,
            "notes": data.notes,
            "proof_contact_id": data.proof_contact_id,
            "status": "submitted",
            "submitted_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        # Unique index violation (race condition on dedup)
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(status_code=409, detail="This restaurant has already been referred.")
        raise

    # Create corresponding sales_leads entry
    partner_info = supabase.table("proof_users") \
        .select("full_name, company") \
        .eq("id", user_id) \
        .single() \
        .execute()

    partner_name = partner_info.data.get("full_name", "Unknown") if partner_info.data else "Unknown"
    partner_company = partner_info.data.get("company", "") if partner_info.data else ""

    lead_notes = f"Partner referral from {partner_name}"
    if partner_company:
        lead_notes += f" ({partner_company})"
    if data.notes:
        lead_notes += f". Partner notes: {data.notes}"

    lead = supabase.table("sales_leads").insert({
        "restaurant_name": name_clean,
        "contact_name": data.contact_name,
        "contact_email": data.contact_email,
        "contact_phone": data.contact_phone,
        "city_state": f"{city_clean}, {state_clean}",
        "lead_source": "partner_referral",
        "stage": "new",
        "notes": lead_notes,
    }).execute()

    # Link the sales lead back to the referral
    if lead.data:
        supabase.table("proof_partner_referrals") \
            .update({"sales_lead_id": lead.data[0]["id"]}) \
            .eq("id", referral.data[0]["id"]) \
            .execute()

    # Update partner aggregates
    supabase.table("proof_partners") \
        .update({
            "total_referrals": partner.data[0].get("total_referrals", 0) + 1
            if "total_referrals" in partner.data[0] else 1,
            "updated_at": datetime.utcnow().isoformat()
        }) \
        .eq("id", partner_id) \
        .execute()

    # Re-fetch for accurate count
    updated_partner = supabase.table("proof_partners") \
        .select("total_referrals") \
        .eq("id", partner_id) \
        .single() \
        .execute()

    # Actually count from source of truth
    referral_count = supabase.table("proof_partner_referrals") \
        .select("id", count="exact") \
        .eq("partner_id", partner_id) \
        .execute()

    supabase.table("proof_partners") \
        .update({"total_referrals": referral_count.count or 0}) \
        .eq("id", partner_id) \
        .execute()

    return {
        "success": True,
        "referral_id": referral.data[0]["id"],
        "sales_lead_id": lead.data[0]["id"] if lead.data else None,
        "message": f"Referral submitted for {name_clean}. The En Place team will review it."
    }


@router.get("/referrals")
async def list_referrals(current_user: dict = Depends(verify_proof_token)):
    """List all referrals for the current partner."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    partner = supabase.table("proof_partners") \
        .select("id") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data:
        raise HTTPException(status_code=404, detail="Not enrolled in partner program")

    referrals = supabase.table("proof_partner_referrals") \
        .select("*") \
        .eq("partner_id", partner.data[0]["id"]) \
        .order("created_at", desc=True) \
        .execute()

    return {"success": True, "referrals": referrals.data}


@router.get("/referrals/{referral_id}")
async def get_referral(
    referral_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Get detail for a specific referral."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    partner = supabase.table("proof_partners") \
        .select("id") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data:
        raise HTTPException(status_code=404, detail="Not enrolled in partner program")

    referral = supabase.table("proof_partner_referrals") \
        .select("*") \
        .eq("id", referral_id) \
        .eq("partner_id", partner.data[0]["id"]) \
        .single() \
        .execute()

    if not referral.data:
        raise HTTPException(status_code=404, detail="Referral not found")

    # Get commissions for this referral
    commissions = supabase.table("proof_partner_commissions") \
        .select("*") \
        .eq("referral_id", referral_id) \
        .order("created_at", desc=True) \
        .execute()

    return {
        "success": True,
        "referral": referral.data,
        "commissions": commissions.data
    }


# ═══════════════════════════════════════════════════════════════════════════════
# REFERRAL CODE RESOLUTION (PUBLIC)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/resolve-code/{code}")
async def resolve_referral_code(code: str):
    """
    Public endpoint. Resolve a partner referral code to partner info.
    Called by en-place.ai signup flow when ?ref= param is present.
    """
    supabase = get_supabase()

    partner = supabase.table("proof_partners") \
        .select("id, user_id, status") \
        .eq("referral_code", code.upper().strip()) \
        .execute()

    if not partner.data:
        return {"valid": False}

    p = partner.data[0]
    if p["status"] not in ("certified", "active"):
        return {"valid": False, "reason": "Partner is not currently active"}

    # Get partner name and company
    user = supabase.table("proof_users") \
        .select("full_name, company") \
        .eq("id", p["user_id"]) \
        .single() \
        .execute()

    return {
        "valid": True,
        "partner_id": p["id"],
        "partner_name": user.data.get("full_name") if user.data else None,
        "company": user.data.get("company") if user.data else None
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMMISSIONS (READ-ONLY FOR PARTNERS)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/commissions")
async def list_commissions(current_user: dict = Depends(verify_proof_token)):
    """List commission history for the current partner."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    partner = supabase.table("proof_partners") \
        .select("id") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data:
        raise HTTPException(status_code=404, detail="Not enrolled in partner program")

    commissions = supabase.table("proof_partner_commissions") \
        .select("*") \
        .eq("partner_id", partner.data[0]["id"]) \
        .order("created_at", desc=True) \
        .limit(100) \
        .execute()

    return {"success": True, "commissions": commissions.data}


@router.get("/commissions/summary")
async def commission_summary(current_user: dict = Depends(verify_proof_token)):
    """Aggregated commission summary."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    partner = supabase.table("proof_partners") \
        .select("id, total_commission_earned, total_commission_paid, pending_commission") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data:
        raise HTTPException(status_code=404, detail="Not enrolled in partner program")

    p = partner.data[0]
    return {
        "success": True,
        "total_earned": float(p["total_commission_earned"] or 0),
        "total_paid": float(p["total_commission_paid"] or 0),
        "pending": float(p["pending_commission"] or 0),
    }

# ═══════════════════════════════════════════════════════════════════════════════
# STRIPE CONNECT — Partner Commission Payouts
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/connect/onboard")
async def partner_connect_onboard(current_user: dict = Depends(verify_proof_token)):
    """
    Create or retrieve Stripe Connect Express account for partner.
    Returns onboarding link for partner to enter bank details.
    """
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    partner = supabase.table("proof_partners") \
        .select("id, status, stripe_connect_account_id") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data:
        raise HTTPException(status_code=404, detail="Not enrolled in partner program")

    p = partner.data[0]
    if p["status"] not in ("certified", "active"):
        raise HTTPException(status_code=403, detail="Stripe Connect is available after certification")

    try:
        existing_account_id = p.get("stripe_connect_account_id")

        if existing_account_id:
            account = stripe.Account.retrieve(existing_account_id)

            if account.details_submitted and account.payouts_enabled:
                # Already fully onboarded — update flag if needed
                if not partner.data[0].get("stripe_connect_onboarded"):
                    supabase.table("proof_partners") \
                        .update({"stripe_connect_onboarded": True, "updated_at": datetime.utcnow().isoformat()}) \
                        .eq("id", p["id"]) \
                        .execute()

                return {
                    "success": True,
                    "message": "Your payout account is already set up and ready to receive commissions."
                }

            # Onboarding incomplete — generate new link
            account_link = stripe.AccountLink.create(
                account=existing_account_id,
                refresh_url=PARTNER_CONNECT_REFRESH_URL,
                return_url=PARTNER_CONNECT_RETURN_URL,
                type="account_onboarding"
            )

            return {
                "success": True,
                "onboarding_url": account_link.url,
                "message": "Please complete your payout setup."
            }

        # Create new Express Connect account
        user = supabase.table("proof_users") \
            .select("email, full_name") \
            .eq("id", user_id) \
            .single() \
            .execute()

        account = stripe.Account.create(
            type="express",
            country="US",
            email=user.data.get("email") if user.data else None,
            capabilities={"transfers": {"requested": True}},
            business_type="individual",
            metadata={
                "proof_user_id": user_id,
                "partner_id": p["id"],
                "source": "enplace_partner"
            }
        )

        # Store account ID
        supabase.table("proof_partners") \
            .update({
                "stripe_connect_account_id": account.id,
                "updated_at": datetime.utcnow().isoformat()
            }) \
            .eq("id", p["id"]) \
            .execute()

        logger.info(f"Created Stripe Connect account {account.id} for partner {p['id']}")

        # Generate onboarding link
        account_link = stripe.AccountLink.create(
            account=account.id,
            refresh_url=PARTNER_CONNECT_REFRESH_URL,
            return_url=PARTNER_CONNECT_RETURN_URL,
            type="account_onboarding"
        )

        return {
            "success": True,
            "onboarding_url": account_link.url,
            "message": "Click the link to set up your direct deposit."
        }

    except stripe.error.StripeError as e:
        logger.error(f"Partner Connect error for {p['id']}: {e}")
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
    except Exception as e:
        logger.error(f"Partner Connect onboarding error for {p['id']}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create payout account")


@router.get("/connect/status")
async def partner_connect_status(current_user: dict = Depends(verify_proof_token)):
    """Check if partner has completed Stripe Connect onboarding."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    partner = supabase.table("proof_partners") \
        .select("id, stripe_connect_account_id, stripe_connect_onboarded") \
        .eq("user_id", user_id) \
        .execute()

    if not partner.data:
        raise HTTPException(status_code=404, detail="Not enrolled in partner program")

    p = partner.data[0]
    account_id = p.get("stripe_connect_account_id")

    if not account_id:
        return {
            "success": True,
            "is_onboarded": False,
            "payouts_enabled": False,
            "details_submitted": False
        }

    try:
        account = stripe.Account.retrieve(account_id)
        is_ready = account.details_submitted and account.payouts_enabled

        # Sync the flag if Stripe says they're good but our DB doesn't know yet
        if is_ready and not p.get("stripe_connect_onboarded"):
            supabase.table("proof_partners") \
                .update({"stripe_connect_onboarded": True, "updated_at": datetime.utcnow().isoformat()}) \
                .eq("id", p["id"]) \
                .execute()

        return {
            "success": True,
            "is_onboarded": is_ready,
            "payouts_enabled": account.payouts_enabled,
            "details_submitted": account.details_submitted,
            "account_id": account_id
        }

    except stripe.error.StripeError as e:
        logger.error(f"Partner Connect status error: {e}")
        return {
            "success": True,
            "is_onboarded": False,
            "payouts_enabled": False,
            "details_submitted": False
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.patch("/admin/referrals/{referral_id}")
async def admin_update_referral(
    referral_id: str,
    data: AdminReferralUpdateRequest,
    admin: dict = Depends(verify_enplace_admin)
):
    """Admin: Update referral status, link to sales pipeline or restaurant."""
    supabase = get_supabase()

    referral = supabase.table("proof_partner_referrals") \
        .select("id, partner_id, status") \
        .eq("id", referral_id) \
        .single() \
        .execute()

    if not referral.data:
        raise HTTPException(status_code=404, detail="Referral not found")

    updates = {"updated_at": datetime.utcnow().isoformat()}

    if data.status:
        valid_statuses = ["submitted", "accepted", "demo_scheduled", "trial", "active", "churned", "rejected"]
        if data.status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")
        updates["status"] = data.status

        if data.status == "accepted" and not referral.data.get("accepted_at"):
            updates["accepted_at"] = datetime.utcnow().isoformat()

        if data.status == "active" and not referral.data.get("first_active_at"):
            updates["first_active_at"] = datetime.utcnow().isoformat()

        if data.status == "churned":
            updates["churned_at"] = datetime.utcnow().isoformat()

    if data.sales_lead_id:
        updates["sales_lead_id"] = data.sales_lead_id
    if data.en_place_restaurant_id:
        updates["en_place_restaurant_id"] = data.en_place_restaurant_id
    if data.monthly_subscription_value:
        updates["monthly_subscription_value"] = data.monthly_subscription_value

    supabase.table("proof_partner_referrals") \
        .update(updates) \
        .eq("id", referral_id) \
        .execute()

    # If status changed to 'active', update partner aggregates and check for first close bonus
    if data.status == "active":
        partner_id = referral.data["partner_id"]
        await _update_partner_aggregates(partner_id, supabase)

    # If status changed to 'churned', update partner aggregates and check tier status
    if data.status == "churned":
        partner_id = referral.data["partner_id"]
        await _update_partner_aggregates(partner_id, supabase)

    return {"success": True, "message": f"Referral updated to '{data.status or 'unchanged'}'"}


async def _update_partner_aggregates(partner_id: str, supabase):
    """Recalculate partner aggregate counts from source of truth."""
    active_count = supabase.table("proof_partner_referrals") \
        .select("id", count="exact") \
        .eq("partner_id", partner_id) \
        .eq("status", "active") \
        .execute()

    total_count = supabase.table("proof_partner_referrals") \
        .select("id", count="exact") \
        .eq("partner_id", partner_id) \
        .execute()

    active_referrals = active_count.count or 0
    total_referrals = total_count.count or 0

    partner_updates = {
        "active_referrals": active_referrals,
        "total_referrals": total_referrals,
        "updated_at": datetime.utcnow().isoformat()
    }

    # Get current partner status
    partner = supabase.table("proof_partners") \
        .select("status, partner_tier_expires_at") \
        .eq("id", partner_id) \
        .single() \
        .execute()

    current_status = partner.data["status"]

    # State machine transitions based on active referral count
    if active_referrals > 0 and current_status in ("certified", "lapsed"):
        partner_updates["status"] = "active"
        partner_updates["partner_tier_expires_at"] = None  # No expiry while active

        if current_status == "lapsed":
            # Reactivate plan
            p = supabase.table("proof_partners") \
                .select("user_id") \
                .eq("id", partner_id) \
                .single() \
                .execute()
            supabase.table("proof_users") \
                .update({"plan": "partner", "plan_status": "active"}) \
                .eq("id", p.data["user_id"]) \
                .execute()

    elif active_referrals == 0 and current_status == "active":
        # All referrals churned — restart 6-month clock
        partner_updates["status"] = "certified"
        partner_updates["partner_tier_expires_at"] = (datetime.utcnow() + timedelta(days=180)).isoformat()

    supabase.table("proof_partners") \
        .update(partner_updates) \
        .eq("id", partner_id) \
        .execute()

    # Check for first close bonus
    if active_referrals > 0:
        first_close_check = supabase.table("proof_partner_referrals") \
            .select("id") \
            .eq("partner_id", partner_id) \
            .eq("is_first_close", True) \
            .execute()

        if not first_close_check.data:
            # This is the first close — find the first active referral and mark it
            first_active = supabase.table("proof_partner_referrals") \
                .select("id") \
                .eq("partner_id", partner_id) \
                .eq("status", "active") \
                .order("first_active_at") \
                .limit(1) \
                .execute()

            if first_active.data:
                supabase.table("proof_partner_referrals") \
                    .update({"is_first_close": True}) \
                    .eq("id", first_active.data[0]["id"]) \
                    .execute()

                # Insert first close bonus commission
                supabase.table("proof_partner_commissions").insert({
                    "partner_id": partner_id,
                    "referral_id": first_active.data[0]["id"],
                    "commission_type": "first_close_bonus",
                    "gross_amount": 500,
                    "commission_rate": 1.0,
                    "commission_amount": 500,
                    "status": "pending",
                    "created_at": datetime.utcnow().isoformat()
                }).execute()

                # Update aggregates
                supabase.table("proof_partners") \
                    .update({
                        "pending_commission": (
                            supabase.table("proof_partner_commissions")
                            .select("commission_amount")
                            .eq("partner_id", partner_id)
                            .eq("status", "pending")
                            .execute()
                        ) and 500  # Will be recalculated properly in commission endpoints
                    }) \
                    .eq("id", partner_id) \
                    .execute()

                logger.info(f"First close bonus created for partner {partner_id}")


@router.patch("/admin/partners/{partner_id}")
async def admin_update_partner(
    partner_id: str,
    data: AdminPartnerUpdateRequest,
    admin: dict = Depends(verify_enplace_admin)
):
    """Admin: Suspend or reactivate a partner."""
    supabase = get_supabase()

    partner = supabase.table("proof_partners") \
        .select("id, user_id, status") \
        .eq("id", partner_id) \
        .single() \
        .execute()

    if not partner.data:
        raise HTTPException(status_code=404, detail="Partner not found")

    valid_transitions = {
        "suspended": ["certified", "active", "lapsed"],
        "active": ["suspended"],
        "certified": ["suspended"],
    }

    if data.status not in valid_transitions:
        raise HTTPException(status_code=400, detail=f"Invalid target status: {data.status}")

    current = partner.data["status"]
    if current not in valid_transitions.get(data.status, []):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition from '{current}' to '{data.status}'"
        )

    updates = {
        "status": data.status,
        "updated_at": datetime.utcnow().isoformat()
    }

    if data.status == "suspended":
        supabase.table("proof_users") \
            .update({"plan": "free"}) \
            .eq("id", partner.data["user_id"]) \
            .execute()

    elif data.status in ("active", "certified"):
        supabase.table("proof_users") \
            .update({"plan": "partner", "plan_status": "active"}) \
            .eq("id", partner.data["user_id"]) \
            .execute()

    supabase.table("proof_partners") \
        .update(updates) \
        .eq("id", partner_id) \
        .execute()

    return {"success": True, "message": f"Partner status changed to '{data.status}'"}
