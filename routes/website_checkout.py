"""
WEBSITE PLATFORM STRIPE CHECKOUT
Handles checkout for restaurant website sales:
- $250 one-time setup fee
- $50/month hosting subscription
- Tracks rep commission ($125 = 50% of setup fee)
"""
import os
import stripe
import logging
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from typing import Optional
from database.supabase_client import get_supabase
from services.auth_service import verify_jwt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/website-checkout", tags=["website-checkout"])

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBSITE_WEBHOOK_SECRET")

PRICE_SETUP = os.environ.get("STRIPE_PRICE_WEBSITE_SETUP", "price_1TBgEL3LJ5OuNoyoKD4dEO7b")
PRICE_MONTHLY = os.environ.get("STRIPE_PRICE_WEBSITE_HOSTING", "price_1TBgEz3LJ5OuNoyorC5MzQno")

SUCCESS_URL = "https://app.en-place.ai/sales-portal/site-builder.html?payment=success&session_id={CHECKOUT_SESSION_ID}"
CANCEL_URL = "https://app.en-place.ai/sales-portal/site-builder.html?payment=cancelled"


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class WebsiteCheckoutRequest(BaseModel):
    restaurant_id: int
    restaurant_name: str
    owner_email: Optional[str] = None
    owner_name: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# CREATE CHECKOUT SESSION
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/create-session")
async def create_checkout_session(req: WebsiteCheckoutRequest, user=Depends(verify_jwt_token)):
    """
    Creates a Stripe Checkout session with:
    - $250 one-time setup fee
    - $50/month recurring subscription
    Metadata tracks restaurant_id and rep staff_id for commission.
    """
    try:
        staff_id = user.get("staff_id", "unknown")

        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[
                {
                    "price": PRICE_SETUP,
                    "quantity": 1,
                },
                {
                    "price": PRICE_MONTHLY,
                    "quantity": 1,
                },
            ],
            metadata={
                "type": "website_platform",
                "restaurant_id": str(req.restaurant_id),
                "restaurant_name": req.restaurant_name,
                "rep_staff_id": staff_id,
                "setup_fee": "250",
                "commission_amount": "125",
            },
            subscription_data={
                "metadata": {
                    "type": "website_platform",
                    "restaurant_id": str(req.restaurant_id),
                    "restaurant_name": req.restaurant_name,
                    "rep_staff_id": staff_id,
                },
            },
            customer_email=req.owner_email,
            success_url=SUCCESS_URL,
            cancel_url=CANCEL_URL,
        )

        return {
            "success": True,
            "session_id": session.id,
            "url": session.url,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating website checkout: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Website checkout error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/webhook")
async def website_stripe_webhook(request: Request):
    """
    Handles Stripe webhook events for website platform payments.
    On successful payment:
    1. Flips restaurant_config status to 'active'
    2. Records commission for the rep
    3. Stores Stripe customer/subscription IDs
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})

        # Only handle website platform checkouts
        if metadata.get("type") != "website_platform":
            return {"status": "ignored"}

        restaurant_id = metadata.get("restaurant_id")
        rep_staff_id = metadata.get("rep_staff_id")
        commission_amount = metadata.get("commission_amount", "125")

        if not restaurant_id:
            logger.error("Website webhook: no restaurant_id in metadata")
            return {"status": "error"}

        supabase = get_supabase()

        try:
            # 1. Activate the site
            supabase.table("restaurant_config") \
                .update({
                    "status": "active",
                }) \
                .eq("restaurant_id", int(restaurant_id)) \
                .execute()

            logger.info(f"Website activated for restaurant {restaurant_id}")

            # 2. Record commission
            supabase.table("website_commissions").insert({
                "restaurant_id": int(restaurant_id),
                "rep_staff_id": rep_staff_id,
                "amount_cents": int(float(commission_amount) * 100),
                "stripe_session_id": session.get("id"),
                "stripe_customer_id": session.get("customer"),
                "stripe_subscription_id": session.get("subscription"),
                "status": "pending",
            }).execute()

            logger.info(f"Commission recorded: ${commission_amount} for rep {rep_staff_id}")

        except Exception as e:
            logger.error(f"Website webhook processing error: {e}")

    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════════════
# GET COMMISSIONS (for rep dashboard)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/commissions")
async def get_my_commissions(user=Depends(verify_jwt_token)):
    """Get commission history for the logged-in rep."""
    try:
        supabase = get_supabase()
        staff_id = user.get("staff_id", "unknown")

        result = supabase.table("website_commissions") \
            .select("*, restaurants(name)") \
            .eq("rep_staff_id", staff_id) \
            .order("created_at", desc=True) \
            .execute()

        return {"success": True, "commissions": result.data or []}

    except Exception as e:
        logger.error(f"Commission fetch error: {e}")
        raise HTTPException(status_code=500, detail=str(e))