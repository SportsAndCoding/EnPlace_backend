# routes/stripe_checkout.py
"""
Stripe Checkout & Subscription Management
=========================================
Handles checkout session creation and webhook events.
"""

import os
import stripe
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Request, Header, Depends
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from database.supabase_client import get_supabase
from services.auth_service import verify_jwt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["stripe"])

# Initialize Stripe
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

# Price IDs from environment (supports test/live switching)
PRICE_IDS = {
    "sse": os.environ.get("STRIPE_PRICE_SSE"),
    "stable_hire": os.environ.get("STRIPE_PRICE_STABLE_HIRE"),
    "stable_schedule": os.environ.get("STRIPE_PRICE_STABLE_SCHEDULE"),
    "house_guardian": os.environ.get("STRIPE_PRICE_HOUSE_GUARDIAN"),
    "open_shift": os.environ.get("STRIPE_PRICE_OPEN_SHIFT"),
    "shift_swap": os.environ.get("STRIPE_PRICE_SHIFT_SWAP"),
}

# Frontend URLs
SUCCESS_URL = "https://en-place.ai/register?session_id={CHECKOUT_SESSION_ID}"
CANCEL_URL = "https://en-place.ai/pricing"


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class CreateCheckoutRequest(BaseModel):
    modules: List[str]  # ["sse", "stable_hire", "shift_swap", etc.]


class AddModulesRequest(BaseModel):
    modules: List[str]  # ["stable_hire", "house_guardian", etc.]
    

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
# ADD MODULES TO EXISTING SUBSCRIPTION
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/subscription/add-modules")
async def add_modules_to_subscription(
    request: AddModulesRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """
    Add modules to an existing subscription.
    Charges prorated amount immediately.
    """
    supabase = get_supabase()
    restaurant_id = current_staff.get("restaurant_id")
    
    if not restaurant_id:
        raise HTTPException(status_code=401, detail="No restaurant associated with this account")
    
    result = supabase.table("restaurants") \
        .select("stripe_subscription_id, modules_enabled") \
        .eq("id", restaurant_id) \
        .single() \
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    
    subscription_id = result.data.get("stripe_subscription_id")
    if not subscription_id:
        raise HTTPException(status_code=400, detail="No active subscription. Please purchase a plan first.")
    
    current_modules = result.data.get("modules_enabled") or ["sse"]
    modules_to_add = [m for m in request.modules if m not in current_modules and m in PRICE_IDS]
    
    if not modules_to_add:
        raise HTTPException(status_code=400, detail="No new modules to add")
    
    try:
        subscription = stripe.Subscription.retrieve(subscription_id)
        current_items = [{"id": item.id} for item in subscription["items"]["data"]]
        
        for module in modules_to_add:
            current_items.append({"price": PRICE_IDS[module]})
        
        stripe.Subscription.modify(
            subscription_id,
            items=current_items,
            proration_behavior="always_invoice",
            payment_behavior="error_if_incomplete",
        )
        
        new_modules = current_modules + modules_to_add
        supabase.table("restaurants").update({
            "modules_enabled": new_modules,
            "has_stable_hire": "stable_hire" in new_modules,
            "has_schedule_optimizer": "stable_schedule" in new_modules,
            "has_house_guardian": "house_guardian" in new_modules,
            "has_open_shift_marketplace": "open_shift" in new_modules,
            "has_shift_swap": "shift_swap" in new_modules,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", restaurant_id).execute()
        
        logger.info(f"Restaurant {restaurant_id} added modules: {modules_to_add}")
        
        return {
            "success": True,
            "modules_added": modules_to_add,
            "modules_enabled": new_modules
        }
        
    except stripe.error.CardError as e:
        logger.error(f"Card error adding modules: {e}")
        raise HTTPException(status_code=402, detail=f"Payment failed: {e.user_message}")
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error adding modules: {e}")
        raise HTTPException(status_code=500, detail="Payment processing error")


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
    
    elif event_type == "invoice.paid":
        await handle_invoice_paid(data)
    
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
    """Subscription was updated (renewal, plan change, etc.)"""
    supabase = get_supabase()

    try:
        result = supabase.table("restaurants") \
            .select("id, modules_pending_cancel") \
            .eq("stripe_subscription_id", subscription.id) \
            .single() \
            .execute()

        if result.data:
            restaurant_id = result.data["id"]
            pending = result.data.get("modules_pending_cancel") or {}
            
            # Check for any pending cancellations that should now be processed
            now = datetime.utcnow()
            update_data = {"updated_at": now.isoformat()}
            processed = []
            
            for module, cancel_date_str in list(pending.items()):
                cancel_date = datetime.fromisoformat(cancel_date_str.replace('Z', ''))
                if now >= cancel_date:
                    # Time to actually cancel this module
                    processed.append(module)
                    del pending[module]
                    
                    # Set boolean flag to false
                    if module == "stable_hire":
                        update_data["has_stable_hire"] = False
                    elif module == "stable_schedule":
                        update_data["has_schedule_optimizer"] = False
                    elif module == "house_guardian":
                        update_data["has_house_guardian"] = False
                    elif module == "open_shift":
                        update_data["has_open_shift_marketplace"] = False
                    elif module == "shift_swap":
                        update_data["has_shift_swap"] = False
            
            if processed:
                update_data["modules_pending_cancel"] = pending
                
                # Also remove from Stripe subscription
                try:
                    sub = stripe.Subscription.retrieve(subscription.id)
                    items_to_keep = []
                    for item in sub["items"]["data"]:
                        price_id = item["price"]["id"]
                        module_key = None
                        for key, pid in PRICE_IDS.items():
                            if pid == price_id:
                                module_key = key
                                break
                        if module_key not in processed:
                            items_to_keep.append({"id": item.id})
                        else:
                            items_to_keep.append({"id": item.id, "deleted": True})
                    
                    stripe.Subscription.modify(subscription.id, items=items_to_keep, proration_behavior="none")
                except Exception as e:
                    logger.error(f"Error removing items from Stripe: {e}")
                
                supabase.table("restaurants").update(update_data).eq("id", restaurant_id).execute()
                logger.info(f"Processed pending cancellations for restaurant {restaurant_id}: {processed}")
            
            # Also sync subscription status
            supabase.table("restaurants").update({
                "subscription_status": subscription.status
            }).eq("id", restaurant_id).execute()

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

            # Check for partner referral
            await _check_partner_referral_churn(result.data["id"], supabase)
    
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

async def handle_invoice_paid(invoice):
    """Record successful payment in invoices table and create sales commission"""
    supabase = get_supabase()
    
    try:
        subscription_id = invoice.subscription
        restaurant_id = None
        
        # Find the restaurant
        if subscription_id:
            result = supabase.table("restaurants") \
                .select("id") \
                .eq("stripe_subscription_id", subscription_id) \
                .single() \
                .execute()
            
            if result.data:
                restaurant_id = result.data["id"]
        
        # Insert invoice record
        invoice_result = supabase.table("invoices").insert({
            "restaurant_id": restaurant_id,
            "stripe_invoice_id": invoice.id,
            "amount_cents": invoice.amount_paid,
            "currency": invoice.currency,
            "description": invoice.lines.data[0].description if invoice.lines.data else "Subscription",
            "status": "paid",
            "period_start": datetime.utcfromtimestamp(invoice.period_start).isoformat() if invoice.period_start else None,
            "period_end": datetime.utcfromtimestamp(invoice.period_end).isoformat() if invoice.period_end else None,
            "paid_at": datetime.utcnow().isoformat()
        }).execute()
        
        logger.info(f"Recorded invoice {invoice.id} for restaurant {restaurant_id}: ${invoice.amount_paid / 100:.2f}")
        
        # === COMMISSION CREATION ===
        if restaurant_id:
            await create_commission_for_invoice(supabase, restaurant_id, invoice)
    
    except Exception as e:
        logger.error(f"Error recording invoice: {e}")


async def create_commission_for_invoice(supabase, restaurant_id: int, invoice):
    """
    Create commission record for the sales rep who closed this deal.
    
    - First invoice: 75% initial commission
    - Subsequent invoices: 5% residual commission
    """
    try:
        # Find the deal for this restaurant
        deal_result = supabase.table("sales_deals") \
            .select("id, rep_id, monthly_value") \
            .eq("restaurant_id", restaurant_id) \
            .eq("status", "active") \
            .single() \
            .execute()
        
        if not deal_result.data:
            logger.info(f"No active sales deal found for restaurant {restaurant_id} - no rep commission created")
            # Still check for partner referral even without a sales deal
            await _check_partner_referral_invoice(supabase, restaurant_id, invoice)
            return
        
        deal = deal_result.data
        deal_id = deal["id"]
        rep_id = deal["rep_id"]
        
        if not rep_id:
            logger.info(f"Deal {deal_id} has no rep_id - no commission created")
            return
        
        # Check if initial commission already exists for this deal
        existing_initial = supabase.table("sales_commissions") \
            .select("id") \
            .eq("deal_id", deal_id) \
            .eq("commission_type", "initial") \
            .execute()
        
        amount_dollars = invoice.amount_paid / 100  # Convert cents to dollars
        
        if not existing_initial.data:
            # First payment - 75% initial commission
            commission_amount = amount_dollars * 0.75
            commission_type = "initial"
            logger.info(f"Creating initial commission for deal {deal_id}: ${commission_amount:.2f}")
        else:
            # Subsequent payment - 5% residual
            commission_amount = amount_dollars * 0.05
            commission_type = "residual"
            logger.info(f"Creating residual commission for deal {deal_id}: ${commission_amount:.2f}")
        
        # Create commission with 7-day hold
        release_at = datetime.utcnow() + timedelta(days=7)
        
        supabase.table("sales_commissions").insert({
            "deal_id": deal_id,
            "rep_id": rep_id,
            "commission_type": commission_type,
            "amount": commission_amount,
            "status": "held",
            "release_at": release_at.isoformat(),
            "expected_pay_date": release_at.date().isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        
        logger.info(f"Commission created: ${commission_amount:.2f} ({commission_type}) for rep {rep_id}, releases {release_at.date()}")

        # Partner commission (separate from rep commission)
        await _check_partner_referral_invoice(supabase, restaurant_id, invoice)
    
    except Exception as e:
        logger.error(f"Error creating commission for restaurant {restaurant_id}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# SUBSCRIPTION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/subscription/status")
async def get_subscription_status(current_staff: dict = Depends(verify_jwt_token)):
    """Get current subscription status for a restaurant"""
    supabase = get_supabase()
    restaurant_id = current_staff.get("restaurant_id")
    
    if not restaurant_id:
        raise HTTPException(status_code=401, detail="No restaurant associated with this account")

    try:
        result = supabase.table("restaurants") \
            .select("stripe_subscription_id, subscription_status, modules_enabled, modules_pending_cancel, has_stable_hire, has_schedule_optimizer, has_house_guardian, has_open_shift_marketplace, has_shift_swap") \
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
        
        # Derive modules from boolean flags (source of truth)
        modules = ["sse"]  # Always included
        if result.data.get("has_stable_hire"):
            modules.append("stable_hire")
        if result.data.get("has_schedule_optimizer"):
            modules.append("stable_schedule")
        if result.data.get("has_house_guardian"):
            modules.append("house_guardian")
        if result.data.get("has_open_shift_marketplace"):
            modules.append("open_shift")
        if result.data.get("has_shift_swap"):
            modules.append("shift_swap")
        
        return {
    "success": True,
    "subscription_status": result.data.get("subscription_status"),
    "modules_enabled": modules,
    "modules_pending_cancel": result.data.get("modules_pending_cancel") or {},
    "stripe_details": subscription_details
}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting subscription status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get subscription status")


class UpdateModulesRequest(BaseModel):
    modules: List[str]


@router.post("/subscription/update-modules")
async def update_subscription_modules(
    request: UpdateModulesRequest,
    current_staff: dict = Depends(verify_jwt_token)
):
    """Update subscription modules - handles both additions and removals."""
    supabase = get_supabase()
    restaurant_id = current_staff.get("restaurant_id")
    
    if not restaurant_id:
        raise HTTPException(status_code=401, detail="No restaurant associated with this account")
    
    # SSE is always required
    desired_modules = list(set(request.modules))
    if "sse" not in desired_modules:
        desired_modules.insert(0, "sse")
    
    # Validate all modules
    invalid = [m for m in desired_modules if m not in PRICE_IDS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid modules: {invalid}")
    
    result = supabase.table("restaurants") \
        .select("stripe_subscription_id, modules_enabled, modules_pending_cancel, has_stable_hire, has_schedule_optimizer, has_house_guardian, has_open_shift_marketplace, has_shift_swap") \
        .eq("id", restaurant_id) \
        .single() \
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    
    # Derive current modules from boolean flags
    current_modules = ["sse"]
    if result.data.get("has_stable_hire"):
        current_modules.append("stable_hire")
    if result.data.get("has_schedule_optimizer"):
        current_modules.append("stable_schedule")
    if result.data.get("has_house_guardian"):
        current_modules.append("house_guardian")
    if result.data.get("has_open_shift_marketplace"):
        current_modules.append("open_shift")
    if result.data.get("has_shift_swap"):
        current_modules.append("shift_swap")
    
    # Calculate changes
    modules_to_add = [m for m in desired_modules if m not in current_modules]
    modules_to_remove = [m for m in current_modules if m not in desired_modules and m != "sse"]
    
    if not modules_to_add and not modules_to_remove:
        return {
            "success": True,
            "message": "No changes to make",
            "modules_enabled": current_modules
        }
    
    # Get subscription period end from Stripe (for removals)
    period_end = None
    subscription_id = result.data.get("stripe_subscription_id")
    if modules_to_remove:
        if subscription_id:
            try:
                subscription = stripe.Subscription.retrieve(subscription_id)
                period_end = datetime.utcfromtimestamp(subscription.current_period_end).isoformat()
            except Exception as e:
                logger.error(f"Could not get subscription period end: {e}")
                period_end = (datetime.utcnow() + timedelta(days=30)).isoformat()
        else:
            # No Stripe subscription (demo mode) - use 30 days from now
            period_end = (datetime.utcnow() + timedelta(days=30)).isoformat()
    
    # Build update payload
    update_data = {"updated_at": datetime.utcnow().isoformat()}
    
    # Handle ADDITIONS - immediate activation
    if modules_to_add:
        # Update Stripe subscription (add items with proration)
        if subscription_id:
            try:
                subscription = stripe.Subscription.retrieve(subscription_id)
                new_items = [{"id": item.id} for item in subscription["items"]["data"]]
                for module in modules_to_add:
                    new_items.append({"price": PRICE_IDS[module]})
                
                stripe.Subscription.modify(
                    subscription_id,
                    items=new_items,
                    proration_behavior="always_invoice",
                    payment_behavior="error_if_incomplete",
                )
            except stripe.error.StripeError as e:
                logger.error(f"Stripe error adding modules: {e}")
                raise HTTPException(status_code=402, detail=f"Payment failed: {str(e)}")
        
        # Update boolean flags for additions immediately
        if "stable_hire" in modules_to_add:
            update_data["has_stable_hire"] = True
        if "stable_schedule" in modules_to_add:
            update_data["has_schedule_optimizer"] = True
        if "house_guardian" in modules_to_add:
            update_data["has_house_guardian"] = True
        if "open_shift" in modules_to_add:
            update_data["has_open_shift_marketplace"] = True
        if "shift_swap" in modules_to_add:
            update_data["has_shift_swap"] = True
    
    # Handle REMOVALS - schedule for period end (don't change boolean yet)
    pending_cancel = result.data.get("modules_pending_cancel") or {}
    if modules_to_remove and period_end:
        for module in modules_to_remove:
            pending_cancel[module] = period_end
        update_data["modules_pending_cancel"] = pending_cancel
    
    # Update database
    supabase.table("restaurants").update(update_data).eq("id", restaurant_id).execute()
    
    logger.info(f"Restaurant {restaurant_id} - Added: {modules_to_add}, Scheduled removal: {modules_to_remove}")
    
    return {
        "success": True,
        "modules_added": modules_to_add,
        "modules_scheduled_removal": modules_to_remove,
        "removal_date": period_end if modules_to_remove else None,
        "modules_enabled": current_modules + modules_to_add  # Still have access to removed ones until period end
    }


@router.post("/subscription/cancel")
async def cancel_subscription(
        current_staff: dict = Depends(verify_jwt_token)
    ):
        """Cancel subscription at end of billing period."""
        supabase = get_supabase()
        restaurant_id = current_staff.get("restaurant_id")
        
        if not restaurant_id:
            raise HTTPException(status_code=401, detail="No restaurant associated with this account")
        
        supabase.table("restaurants").update({
            "subscription_status": "canceling",
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", restaurant_id).execute()
        
        return {
            "success": True,
            "message": "Subscription will cancel at end of billing period"
        }

# ═══════════════════════════════════════════════════════════════════════════════
# PARTNER REFERRAL BRIDGE
# ═══════════════════════════════════════════════════════════════════════════════

async def _check_partner_referral_invoice(supabase, restaurant_id: int, invoice):
    """
    Called when an En Place invoice is paid.
    If the restaurant was referred by a partner:
    - First invoice: activate the referral, trigger first-close bonus check
    - Every invoice: create a 10% recurring commission row
    """
    try:
        referral = supabase.table("proof_partner_referrals") \
            .select("id, partner_id, status, commission_rate, is_first_close") \
            .eq("en_place_restaurant_id", restaurant_id) \
            .not_.in_("status", ["rejected", "churned"]) \
            .execute()

        if not referral.data:
            return

        ref = referral.data[0]
        partner_id = ref["partner_id"]
        commission_rate = float(ref.get("commission_rate", 0.10))
        amount_dollars = invoice.amount_paid / 100
        commission_amount = round(amount_dollars * commission_rate, 2)

        # If this is the first invoice, activate the referral
        if ref["status"] != "active":
            supabase.table("proof_partner_referrals") \
                .update({
                    "status": "active",
                    "first_active_at": datetime.utcnow().isoformat(),
                    "monthly_subscription_value": amount_dollars,
                    "updated_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", ref["id"]) \
                .execute()

            logger.info(f"Partner referral {ref['id']} activated for restaurant {restaurant_id}")

            # Update partner aggregates + state machine (certified->active, first close bonus)
            await _update_partner_from_webhook(partner_id, ref["id"], supabase)

        # Create recurring partner commission
        period_start = None
        period_end = None
        if invoice.period_start:
            period_start = datetime.utcfromtimestamp(invoice.period_start).date().isoformat()
        if invoice.period_end:
            period_end = datetime.utcfromtimestamp(invoice.period_end).date().isoformat()

        supabase.table("proof_partner_commissions").insert({
            "partner_id": partner_id,
            "referral_id": ref["id"],
            "commission_type": "recurring",
            "period_start": period_start,
            "period_end": period_end,
            "gross_amount": amount_dollars,
            "commission_rate": commission_rate,
            "commission_amount": commission_amount,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        # Update partner pending commission aggregate
        pending = supabase.table("proof_partner_commissions") \
            .select("commission_amount") \
            .eq("partner_id", partner_id) \
            .eq("status", "pending") \
            .execute()

        total_pending = sum(float(c["commission_amount"]) for c in (pending.data or []))
        total_earned_result = supabase.table("proof_partner_commissions") \
            .select("commission_amount") \
            .eq("partner_id", partner_id) \
            .not_.eq("status", "voided") \
            .execute()
        total_earned = sum(float(c["commission_amount"]) for c in (total_earned_result.data or []))

        supabase.table("proof_partners") \
            .update({
                "pending_commission": total_pending,
                "total_commission_earned": total_earned,
                "updated_at": datetime.utcnow().isoformat()
            }) \
            .eq("id", partner_id) \
            .execute()

        logger.info(f"Partner commission ${commission_amount:.2f} created for referral {ref['id']}")

    except Exception as e:
        logger.error(f"Partner referral invoice check failed for restaurant {restaurant_id}: {e}")


async def _check_partner_referral_churn(restaurant_id: int, supabase):
    """
    Called when an En Place subscription is deleted.
    If the restaurant was referred by a partner, mark the referral as churned
    and recalculate partner state (active->certified if no more active referrals).
    """
    try:
        referral = supabase.table("proof_partner_referrals") \
            .select("id, partner_id") \
            .eq("en_place_restaurant_id", restaurant_id) \
            .eq("status", "active") \
            .execute()

        if not referral.data:
            return

        ref = referral.data[0]
        partner_id = ref["partner_id"]

        supabase.table("proof_partner_referrals") \
            .update({
                "status": "churned",
                "churned_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }) \
            .eq("id", ref["id"]) \
            .execute()

        logger.info(f"Partner referral {ref['id']} churned for restaurant {restaurant_id}")

        # Recalculate active referral count
        active_count = supabase.table("proof_partner_referrals") \
            .select("id", count="exact") \
            .eq("partner_id", partner_id) \
            .eq("status", "active") \
            .execute()

        active_referrals = active_count.count or 0

        partner_updates = {
            "active_referrals": active_referrals,
            "updated_at": datetime.utcnow().isoformat()
        }

        if active_referrals == 0:
            # All referrals churned — restart 6-month clock
            partner_updates["status"] = "certified"
            partner_updates["partner_tier_expires_at"] = (datetime.utcnow() + timedelta(days=180)).isoformat()
            logger.info(f"Partner {partner_id} reverted to certified — 6-month clock restarted")

        supabase.table("proof_partners") \
            .update(partner_updates) \
            .eq("id", partner_id) \
            .execute()

    except Exception as e:
        logger.error(f"Partner referral churn check failed for restaurant {restaurant_id}: {e}")


async def _update_partner_from_webhook(partner_id: str, referral_id: str, supabase):
    """
    Called when a partner referral first activates.
    Updates partner status (certified->active) and handles first close bonus.
    """
    try:
        # Recount active referrals from source of truth
        active_count = supabase.table("proof_partner_referrals") \
            .select("id", count="exact") \
            .eq("partner_id", partner_id) \
            .eq("status", "active") \
            .execute()

        active_referrals = active_count.count or 0

        partner = supabase.table("proof_partners") \
            .select("status, user_id") \
            .eq("id", partner_id) \
            .single() \
            .execute()

        partner_updates = {
            "active_referrals": active_referrals,
            "updated_at": datetime.utcnow().isoformat()
        }

        # State machine: certified/lapsed -> active
        if partner.data["status"] in ("certified", "lapsed"):
            partner_updates["status"] = "active"
            partner_updates["partner_tier_expires_at"] = None

            if partner.data["status"] == "lapsed":
                supabase.table("proof_users") \
                    .update({"plan": "partner", "plan_status": "active"}) \
                    .eq("id", partner.data["user_id"]) \
                    .execute()
                logger.info(f"Lapsed partner {partner_id} reactivated")

        supabase.table("proof_partners") \
            .update(partner_updates) \
            .eq("id", partner_id) \
            .execute()

        # First close bonus check
        first_close_check = supabase.table("proof_partner_referrals") \
            .select("id") \
            .eq("partner_id", partner_id) \
            .eq("is_first_close", True) \
            .execute()

        if not first_close_check.data:
            # This IS the first close
            supabase.table("proof_partner_referrals") \
                .update({"is_first_close": True}) \
                .eq("id", referral_id) \
                .execute()

            supabase.table("proof_partner_commissions").insert({
                "partner_id": partner_id,
                "referral_id": referral_id,
                "commission_type": "first_close_bonus",
                "gross_amount": 500,
                "commission_rate": 1.0,
                "commission_amount": 500,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat()
            }).execute()

            logger.info(f"First close bonus ($500) created for partner {partner_id}")

    except Exception as e:
        logger.error(f"Partner webhook update failed for partner {partner_id}: {e}")