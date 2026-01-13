# routes/registration.py
"""
Registration Routes
===================
Handles new customer registration after Stripe checkout.
"""

import os
import bcrypt
import secrets
import string
import stripe
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["registration"])

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    checkout_session_id: str
    restaurant_name: str
    owner_email: EmailStr
    owner_password: str
    owner_first_name: str
    owner_last_name: str
    owner_phone: Optional[str] = None


class RegisterResponse(BaseModel):
    success: bool
    restaurant_id: int
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


def generate_staff_id(prefix: str = "OWN") -> str:
    """Generate unique staff ID"""
    timestamp = datetime.now().strftime("%H%M%S")
    random_suffix = ''.join(secrets.choice(string.digits) for _ in range(4))
    return f"{prefix}{timestamp}{random_suffix}"


def create_jwt_token(staff_data: dict) -> str:
    """Create JWT token for authenticated staff"""
    import jwt
    from datetime import timedelta
    
    JWT_SECRET = os.environ.get("JWT_SECRET", "your-secret-key")
    JWT_ALGORITHM = "HS256"
    
    payload = {
        "staff_id": staff_data["staff_id"],
        "email": staff_data["email"],
        "full_name": staff_data["full_name"],
        "position": staff_data["position"],
        "portal_access": staff_data["portal_access"],
        "restaurant_id": staff_data["restaurant_id"],
        "can_edit_staff": staff_data.get("can_edit_staff", True),
        "exp": datetime.utcnow() + timedelta(hours=24),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRATION ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/register", response_model=RegisterResponse)
async def register(request: RegisterRequest):
    """
    Register a new restaurant and owner account after Stripe checkout.
    
    Flow:
    1. Validate checkout session
    2. Create restaurant record
    3. Create owner staff record
    4. Create onboarding status
    5. Return JWT token for immediate login
    """
    supabase = get_supabase()
    
    # Step 1: Validate checkout session with Stripe
    try:
        session = stripe.checkout.Session.retrieve(
            request.checkout_session_id,
            expand=["subscription"]
        )
        
        if session.payment_status != "paid":
            raise HTTPException(status_code=400, detail="Payment not completed")
        
    except stripe.error.StripeError as e:
        logger.error(f"Invalid checkout session: {e}")
        raise HTTPException(status_code=400, detail="Invalid checkout session")
    
    # Check if this session was already used
    existing = supabase.table("restaurants") \
        .select("id") \
        .eq("stripe_checkout_session_id", request.checkout_session_id) \
        .execute()
    
    if existing.data:
        raise HTTPException(status_code=400, detail="This checkout has already been registered")
    
    # Check if email is already registered
    existing_email = supabase.table("staff") \
        .select("staff_id") \
        .eq("email", request.owner_email) \
        .execute()
    
    if existing_email.data:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Step 2: Create restaurant
    modules = session.metadata.get("modules", "sse").split(",")
    
    try:
        restaurant_result = supabase.table("restaurants").insert({
            "name": request.restaurant_name,
            "status": "onboarding",
            "stripe_customer_id": session.customer,
            "stripe_subscription_id": session.subscription.id if session.subscription else None,
            "stripe_checkout_session_id": session.id,
            "subscription_status": "active",
            "modules_enabled": modules,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }).execute()
        
        if not restaurant_result.data:
            raise HTTPException(status_code=500, detail="Failed to create restaurant")
        
        restaurant_id = restaurant_result.data[0]["id"]
        
    except Exception as e:
        logger.error(f"Error creating restaurant: {e}")
        raise HTTPException(status_code=500, detail="Failed to create restaurant")
    
    # Step 3: Create owner staff record
    try:
        staff_id = generate_staff_id("OWN")
        password_hash = hash_password(request.owner_password)
        full_name = f"{request.owner_first_name} {request.owner_last_name}"
        
        staff_result = supabase.table("staff").insert({
            "staff_id": staff_id,
            "restaurant_id": restaurant_id,
            "email": request.owner_email,
            "password_hash": password_hash,
            "full_name": full_name,
            "phone": request.owner_phone,
            "position": "Owner",
            "portal_access": "manager",
            "can_edit_staff": True,
            "is_owner": True,
            "status": "active",
            "hire_date": datetime.utcnow().date().isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        
        if not staff_result.data:
            # Rollback restaurant creation
            supabase.table("restaurants").delete().eq("id", restaurant_id).execute()
            raise HTTPException(status_code=500, detail="Failed to create owner account")
        
    except Exception as e:
        logger.error(f"Error creating owner: {e}")
        # Rollback
        supabase.table("restaurants").delete().eq("id", restaurant_id).execute()
        raise HTTPException(status_code=500, detail="Failed to create owner account")
    
    # Step 4: Create onboarding status
    try:
        supabase.table("restaurant_onboarding_status").insert({
            "restaurant_id": restaurant_id,
            "setup_step": "basics",
            "onboarding_started_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to create onboarding status (non-critical): {e}")
    
    # Step 5: Update pending registration if exists
    try:
        supabase.table("pending_registrations") \
            .update({"status": "completed", "restaurant_id": restaurant_id}) \
            .eq("checkout_session_id", request.checkout_session_id) \
            .execute()
    except:
        pass  # Not critical
    
    # Step 6: Generate JWT token for immediate login
    token = create_jwt_token({
        "staff_id": staff_id,
        "email": request.owner_email,
        "full_name": full_name,
        "position": "Owner",
        "portal_access": "manager",
        "restaurant_id": restaurant_id,
        "can_edit_staff": True
    })
    
    logger.info(f"Registered new restaurant: {request.restaurant_name} (ID: {restaurant_id})")
    
    return RegisterResponse(
        success=True,
        restaurant_id=restaurant_id,
        staff_id=staff_id,
        token=token,
        message="Registration successful! Redirecting to setup..."
    )


@router.get("/register/validate-session/{session_id}")
async def validate_session(session_id: str):
    """
    Validate a checkout session before showing registration form.
    Returns customer info from Stripe.
    """
    try:
        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["customer"]
        )
        
        if session.payment_status != "paid":
            return {
                "valid": False,
                "error": "Payment not completed"
            }
        
        # Check if already registered
        supabase = get_supabase()
        existing = supabase.table("restaurants") \
            .select("id, name") \
            .eq("stripe_checkout_session_id", session_id) \
            .execute()
        
        if existing.data:
            return {
                "valid": False,
                "error": "Already registered",
                "restaurant_name": existing.data[0]["name"]
            }
        
        return {
            "valid": True,
            "customer_email": session.customer_details.email if session.customer_details else None,
            "customer_name": session.customer_details.name if session.customer_details else None,
            "modules": session.metadata.get("modules", "sse").split(","),
            "amount_total": session.amount_total
        }
    
    except stripe.error.StripeError as e:
        logger.error(f"Session validation error: {e}")
        return {
            "valid": False,
            "error": "Invalid session"
        }

# ═══════════════════════════════════════════════════════════════════════════════
# DEMO REGISTRATION (For testing onboarding without Stripe)
# ═══════════════════════════════════════════════════════════════════════════════

class DemoRegisterRequest(BaseModel):
    owner_email: EmailStr = "demo@enplace.io"
    owner_first_name: str = "Demo"
    owner_last_name: str = "Owner"
    restaurant_name: str = "Demo Restaurant"


@router.post("/register/demo", response_model=RegisterResponse)
async def register_demo(request: DemoRegisterRequest = DemoRegisterRequest()):
    """
    Create a demo restaurant for testing onboarding flow.
    Bypasses Stripe - for development/demo purposes only.
    
    Gated by ALLOW_DEMO_REGISTRATION env var.
    """
    # Safety gate - only allow in dev/demo environments
    if os.environ.get("ALLOW_DEMO_REGISTRATION", "false").lower() != "true":
        raise HTTPException(status_code=403, detail="Demo registration disabled")
    
    supabase = get_supabase()
    
    # Generate unique identifiers
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    demo_restaurant_name = f"{request.restaurant_name} {timestamp}"
    demo_email = f"demo_{timestamp}@enplace.io" if request.owner_email == "demo@enplace.io" else request.owner_email
    
    # Step 1: Create restaurant (no Stripe IDs)
    try:
        restaurant_result = supabase.table("restaurants").insert({
            "name": demo_restaurant_name,
            "status": "onboarding",
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "stripe_checkout_session_id": f"demo_{timestamp}",
            "subscription_status": "demo",
            "modules_enabled": ["sse"],
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }).execute()

        if not restaurant_result.data:
            raise HTTPException(status_code=500, detail="Failed to create demo restaurant")

        restaurant_id = restaurant_result.data[0]["id"]

    except Exception as e:
        logger.error(f"Error creating demo restaurant: {e}")
        raise HTTPException(status_code=500, detail="Failed to create demo restaurant")

    # Step 2: Create owner staff record
    try:
        staff_id = generate_staff_id("DMO")
        password_hash = hash_password("demo123")  # Simple demo password
        full_name = f"{request.owner_first_name} {request.owner_last_name}"

        staff_result = supabase.table("staff").insert({
            "staff_id": staff_id,
            "restaurant_id": restaurant_id,
            "email": demo_email,
            "password_hash": password_hash,
            "full_name": full_name,
            "phone": None,
            "position": "Owner",
            "portal_access": "manager",
            "can_edit_staff": True,
            "is_owner": True,
            "status": "active",
            "hire_date": datetime.utcnow().date().isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        if not staff_result.data:
            supabase.table("restaurants").delete().eq("id", restaurant_id).execute()
            raise HTTPException(status_code=500, detail="Failed to create demo owner")

    except Exception as e:
        logger.error(f"Error creating demo owner: {e}")
        supabase.table("restaurants").delete().eq("id", restaurant_id).execute()
        raise HTTPException(status_code=500, detail="Failed to create demo owner")

    # Step 3: Create onboarding status
    try:
        supabase.table("restaurant_onboarding_status").insert({
            "restaurant_id": restaurant_id,
            "setup_step": "basics",
            "onboarding_started_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to create onboarding status: {e}")

    # Step 4: Generate JWT token
    token = create_jwt_token({
        "staff_id": staff_id,
        "email": demo_email,
        "full_name": full_name,
        "position": "Owner",
        "portal_access": "manager",
        "restaurant_id": restaurant_id,
        "can_edit_staff": True
    })

    logger.info(f"Created demo restaurant: {demo_restaurant_name} (ID: {restaurant_id})")

    return RegisterResponse(
        success=True,
        restaurant_id=restaurant_id,
        staff_id=staff_id,
        token=token,
        message="Demo restaurant created! Use this token for onboarding."
    )