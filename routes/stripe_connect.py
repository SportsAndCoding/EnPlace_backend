# routes/stripe_connect.py
"""
Stripe Connect - Sales Rep Commission Payouts
==============================================
Handles rep onboarding to Stripe Connect and commission transfers.
"""

import os
import stripe
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional
from database.supabase_client import get_supabase
from services.auth_service import verify_jwt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connect", tags=["stripe-connect"])

# Initialize Stripe
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

# Redirect URLs after Connect onboarding
CONNECT_RETURN_URL = os.environ.get("CONNECT_RETURN_URL", "https://app.en-place.ai/sales/payments")
CONNECT_REFRESH_URL = os.environ.get("CONNECT_REFRESH_URL", "https://app.en-place.ai/sales/payments?refresh=true")


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class OnboardingResponse(BaseModel):
    success: bool
    onboarding_url: Optional[str] = None
    message: Optional[str] = None


class ConnectStatusResponse(BaseModel):
    success: bool
    is_onboarded: bool
    payouts_enabled: bool
    details_submitted: bool
    account_id: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# ONBOARDING - Rep connects their bank account
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/onboard", response_model=OnboardingResponse)
async def create_connect_onboarding(current_staff: dict = Depends(verify_jwt_token)):
    """
    Create or retrieve Stripe Connect account for sales rep.
    Returns onboarding link for rep to enter bank details.
    """
    supabase = get_supabase()
    staff_id = current_staff.get("staff_id")
    email = current_staff.get("email")
    full_name = current_staff.get("full_name", "")
    portal_access = current_staff.get("portal_access")
    
    # Verify this is a sales rep
    allowed_roles = ["sales_rep", "sales_captain", "sales_director", "sales_manager", "founder_ceo"]
    if portal_access not in allowed_roles:
        raise HTTPException(status_code=403, detail="Only sales team members can set up payouts")
    
    try:
        # Check if rep already has a Connect account
        result = supabase.table("staff") \
            .select("stripe_connect_account_id") \
            .eq("staff_id", staff_id) \
            .single() \
            .execute()
        
        existing_account_id = result.data.get("stripe_connect_account_id") if result.data else None
        
        if existing_account_id:
            # Check if onboarding is complete
            account = stripe.Account.retrieve(existing_account_id)
            
            if account.details_submitted and account.payouts_enabled:
                return OnboardingResponse(
                    success=True,
                    message="Your payout account is already set up and ready to receive commissions."
                )
            
            # Onboarding incomplete - generate new link
            account_link = stripe.AccountLink.create(
                account=existing_account_id,
                refresh_url=CONNECT_REFRESH_URL,
                return_url=CONNECT_RETURN_URL,
                type="account_onboarding"
            )
            
            return OnboardingResponse(
                success=True,
                onboarding_url=account_link.url,
                message="Please complete your payout setup."
            )
        
        # Create new Express Connect account
        account = stripe.Account.create(
            type="express",
            country="US",
            email=email,
            capabilities={
                "transfers": {"requested": True}
            },
            business_type="individual",
            metadata={
                "staff_id": staff_id,
                "source": "enplace"
            }
        )
        
        # Store account ID
        supabase.table("staff") \
            .update({"stripe_connect_account_id": account.id}) \
            .eq("staff_id", staff_id) \
            .execute()
        
        logger.info(f"Created Stripe Connect account {account.id} for staff {staff_id}")
        
        # Generate onboarding link
        account_link = stripe.AccountLink.create(
            account=account.id,
            refresh_url=CONNECT_REFRESH_URL,
            return_url=CONNECT_RETURN_URL,
            type="account_onboarding"
        )
        
        return OnboardingResponse(
            success=True,
            onboarding_url=account_link.url,
            message="Click the link to set up your direct deposit."
        )
    
    except stripe.error.StripeError as e:
        logger.error(f"Stripe Connect error for {staff_id}: {e}")
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
    except Exception as e:
        logger.error(f"Connect onboarding error for {staff_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create payout account")


@router.get("/status", response_model=ConnectStatusResponse)
async def get_connect_status(current_staff: dict = Depends(verify_jwt_token)):
    """
    Check if rep has completed Stripe Connect onboarding.
    """
    supabase = get_supabase()
    staff_id = current_staff.get("staff_id")
    
    try:
        result = supabase.table("staff") \
            .select("stripe_connect_account_id") \
            .eq("staff_id", staff_id) \
            .single() \
            .execute()
        
        account_id = result.data.get("stripe_connect_account_id") if result.data else None
        
        if not account_id:
            return ConnectStatusResponse(
                success=True,
                is_onboarded=False,
                payouts_enabled=False,
                details_submitted=False
            )
        
        # Check account status with Stripe
        account = stripe.Account.retrieve(account_id)
        
        return ConnectStatusResponse(
            success=True,
            is_onboarded=account.details_submitted and account.payouts_enabled,
            payouts_enabled=account.payouts_enabled,
            details_submitted=account.details_submitted,
            account_id=account_id
        )
    
    except stripe.error.StripeError as e:
        logger.error(f"Stripe status check error: {e}")
        return ConnectStatusResponse(
            success=False,
            is_onboarded=False,
            payouts_enabled=False,
            details_submitted=False
        )
    except Exception as e:
        logger.error(f"Connect status error: {e}")
        raise HTTPException(status_code=500, detail="Failed to check payout status")


@router.get("/dashboard-link")
async def get_connect_dashboard_link(current_staff: dict = Depends(verify_jwt_token)):
    """
    Generate a link to Stripe Express dashboard where rep can view payouts.
    """
    supabase = get_supabase()
    staff_id = current_staff.get("staff_id")
    
    try:
        result = supabase.table("staff") \
            .select("stripe_connect_account_id") \
            .eq("staff_id", staff_id) \
            .single() \
            .execute()
        
        account_id = result.data.get("stripe_connect_account_id") if result.data else None
        
        if not account_id:
            raise HTTPException(status_code=400, detail="Payout account not set up yet")
        
        # Create login link to Stripe Express dashboard
        login_link = stripe.Account.create_login_link(account_id)
        
        return {
            "success": True,
            "dashboard_url": login_link.url
        }
    
    except stripe.error.StripeError as e:
        logger.error(f"Stripe dashboard link error: {e}")
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dashboard link error: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate dashboard link")


# ═══════════════════════════════════════════════════════════════════════════════
# COMMISSION QUERIES - What the rep sees
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/commissions")
async def get_my_commissions(current_staff: dict = Depends(verify_jwt_token)):
    """
    Get all commissions for the current sales rep.
    """
    supabase = get_supabase()
    staff_id = current_staff.get("staff_id")
    
    try:
        result = supabase.table("sales_commissions") \
            .select("*, sales_deals(monthly_value, organization_id, closed_at)") \
            .eq("rep_id", staff_id) \
            .order("created_at", desc=True) \
            .execute()
        
        commissions = result.data or []
        
        # Calculate totals
        pending = sum(c["amount"] for c in commissions if c["status"] == "pending")
        held = sum(c["amount"] for c in commissions if c["status"] == "held")
        released = sum(c["amount"] for c in commissions if c["status"] == "released")
        paid = sum(c["amount"] for c in commissions if c["status"] == "paid")
        
        return {
            "success": True,
            "commissions": commissions,
            "totals": {
                "pending": float(pending),
                "held": float(held),
                "released": float(released),
                "paid": float(paid)
            }
        }
    
    except Exception as e:
        logger.error(f"Get commissions error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch commissions")