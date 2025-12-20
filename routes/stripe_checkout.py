# routes/stripe_checkout.py
"""
Stripe Checkout & Subscription Management
=========================================
Handles checkout session creation and webhook events.
"""

import os
import stripe
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["stripe"])

# Initialize Stripe
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

# Price IDs (Test Mode)
PRICE_IDS = {
    "sse": "price_1SgTnqKPFo8zGGmKMxYuuqN6",           # $1,500/mo - Required
    "stable_hire": "price_1SgToIKPFo8zGGmKwXvssUrs",   # $500/mo
    "stable_schedule": "price_1SgTohKPFo8zGGmKyq3g9bSr", # $500/mo
    "house_guardian": "price_1SgTrfKPFo8zGGmKzqWSDKkw",  # $500/mo
    "open_shift": "price_1SgTsrKPFo8zGGmKtL2SjMOF",    # $200/mo
    "shift_swap": "price_1SgTt7KPFo8zGGmKFQoXJYgI",    # $200/mo
}

# Frontend URLs
SUCCESS_URL = "https://en-place.ai/register?session_id={CHECKOUT_SESSION_ID}"
CANCEL_URL = "https://en-place.ai/pricing"


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class CreateCheckoutRequest(BaseModel):
    modules: List[str]  # ["sse", "stable_hire", "shift_swap", etc.]
    

class CheckoutSessionResponse(BaseModel):
    session_id: str
    url: str


# ═══════════════════════════════════════════════════════════════════════════════
# CHECKOUT SESSION
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/checkout/create-session", response_model=CheckoutSessionResponse)
async def create_checkout_session(request: CreateCheckoutRequest):
    """
    Create a Stripe Checkout session for subscription.
    
    SSE is always required. Additional modules are optional.
    """
    try:
        # Validate modules
        if "sse" not in request.modules:
            request.modules.insert(0, "sse")  # SSE is always required
        
        # Build line items
        line_items = []
        for module in request.modules:
            if module in PRICE_IDS:
                line_items.append({
                    "price": PRICE_IDS[module],
                    "quantity": 1
                })
            else:
                logger.warning(f"Unknown module requested: {module}")
        
        if not line_items:
            raise HTTPException(status_code=400, detail="No valid modules selected")
        
        # Create Checkout Session
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=line_items,
            success_url=SUCCESS_URL,
            cancel_url=CANCEL_URL,
            # Collect billing address for invoices
            billing_address_collection="required",
            # Allow promo codes
            allow_promotion_codes=True,
            # Store module selection in metadata
            metadata={
                "modules": ",".join(request.modules)
            },
            subscription_data={
                "metadata": {
                    "modules": ",".join(request.modules)
                }
            }
        )
        
        return CheckoutSessionResponse(
            session_id=session.id,
            url=session.url
        )
    
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Checkout session error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


@router.get("/checkout/session/{session_id}")
async def get_checkout_session(session_id: str):
    """
    Retrieve checkout session details.
    Used by registration page to get customer info.
    """
    try:
        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["subscription", "customer"]
        )
        
        return {
            "success": True,
            "session_id": session.id,
            "customer_email": session.customer_details.email if session.customer_details else None,
            "customer_name": session.customer_details.name if session.customer_details else None,
            "customer_id": session.customer if isinstance(session.customer, str) else session.customer.id,
            "subscription_id": session.subscription if isinstance(session.subscription, str) else session.subscription.id if session.subscription else None,
            "payment_status": session.payment_status,
            "modules": session.metadata.get("modules", "sse").split(","),
            "amount_total": session.amount_total,
        }
    
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error retrieving session: {e}")
        raise HTTPException(status_code=400, detail="Invalid session")


# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature")
):
    """
    Handle Stripe webhook events.
    
    Events we care about:
    - checkout.session.completed: Payment successful, ready for registration
    - customer.subscription.updated: Plan changes
    - customer.subscription.deleted: Cancellation
    - invoice.payment_failed: Payment issue
    """
    payload = await request.body()
    
    # Verify webhook signature (skip if no secret configured)
    if STRIPE_WEBHOOK_SECRET and stripe_signature:
        try:
            event = stripe.Webhook.construct_event(
                payload, stripe_signature, STRIPE_WEBHOOK_SECRET
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        # Development mode - parse without verification
        import json
        event = stripe.Event.construct_from(
            json.loads(payload), stripe.api_key
        )
    
    event_type = event.type
    data = event.data.object
    
    logger.info(f"Stripe webhook received: {event_type}")
    
    # Handle events
    if event_type == "checkout.session.completed":
        await handle_checkout_completed(data)
    
    elif event_type == "customer.subscription.updated":
        await handle_subscription_updated(data)
    
    elif event_type == "customer.subscription.deleted":
        await handle_subscription_deleted(data)
    
    elif event_type == "invoice.payment_failed":
        await handle_payment_failed(data)
    
    return {"received": True}


async def handle_checkout_completed(session):
    """
    Checkout completed - store pending registration.
    
    We don't create the restaurant yet - that happens when they
    complete the registration form.
    """
    supabase = get_supabase()
    
    try:
        # Store checkout completion for registration
        supabase.table("pending_registrations").upsert({
            "checkout_session_id": session.id,
            "customer_id": session.customer,
            "subscription_id": session.subscription,
            "customer_email": session.customer_details.email if session.customer_details else None,
            "customer_name": session.customer_details.name if session.customer_details else None,
            "modules": session.metadata.get("modules", "sse"),
            "amount_total": session.amount_total,
            "status": "pending_registration",
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        
        logger.info(f"Stored pending registration for session {session.id}")
    
    except Exception as e:
        logger.error(f"Error storing checkout completion: {e}")


async def handle_subscription_updated(subscription):
    """Subscription was updated (plan change, etc.)"""
    supabase = get_supabase()
    
    try:
        # Find restaurant by subscription ID
        result = supabase.table("restaurants") \
            .select("id") \
            .eq("stripe_subscription_id", subscription.id) \
            .single() \
            .execute()
        
        if result.data:
            # Update subscription status
            supabase.table("restaurants").update({
                "subscription_status": subscription.status,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", result.data["id"]).execute()
            
            logger.info(f"Updated subscription status for restaurant {result.data['id']}")
    
    except Exception as e:
        logger.error(f"Error handling subscription update: {e}")


async def handle_subscription_deleted(subscription):
    """Subscription was cancelled"""
    supabase = get_supabase()
    
    try:
        result = supabase.table("restaurants") \
            .select("id") \
            .eq("stripe_subscription_id", subscription.id) \
            .single() \
            .execute()
        
        if result.data:
            supabase.table("restaurants").update({
                "subscription_status": "cancelled",
                "status": "churned",
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", result.data["id"]).execute()
            
            logger.info(f"Marked restaurant {result.data['id']} as churned")
    
    except Exception as e:
        logger.error(f"Error handling subscription deletion: {e}")


async def handle_payment_failed(invoice):
    """Payment failed - may need to notify or restrict access"""
    supabase = get_supabase()
    
    try:
        subscription_id = invoice.subscription
        
        result = supabase.table("restaurants") \
            .select("id, name") \
            .eq("stripe_subscription_id", subscription_id) \
            .single() \
            .execute()
        
        if result.data:
            # Update status
            supabase.table("restaurants").update({
                "subscription_status": "past_due",
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", result.data["id"]).execute()
            
            logger.warning(f"Payment failed for restaurant {result.data['name']}")
            # TODO: Send notification email
    
    except Exception as e:
        logger.error(f"Error handling payment failure: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# SUBSCRIPTION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/subscription/status")
async def get_subscription_status(restaurant_id: int):
    """Get current subscription status for a restaurant"""
    supabase = get_supabase()
    
    try:
        result = supabase.table("restaurants") \
            .select("stripe_subscription_id, subscription_status, modules_enabled") \
            .eq("id", restaurant_id) \
            .single() \
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Restaurant not found")
        
        # Get details from Stripe if we have a subscription
        subscription_details = None
        if result.data.get("stripe_subscription_id"):
            try:
                subscription = stripe.Subscription.retrieve(
                    result.data["stripe_subscription_id"]
                )
                subscription_details = {
                    "status": subscription.status,
                    "current_period_end": subscription.current_period_end,
                    "cancel_at_period_end": subscription.cancel_at_period_end
                }
            except:
                pass
        
        return {
            "success": True,
            "subscription_status": result.data.get("subscription_status"),
            "modules_enabled": result.data.get("modules_enabled", ["sse"]),
            "stripe_details": subscription_details
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting subscription status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get subscription status")