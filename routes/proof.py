# routes/proof.py
"""
Proof Intelligence API
======================
Restaurant data platform with freemium access to liquor license records,
Google Places enrichment, and AI-powered dossiers.
"""

import os
import jwt
import stripe
import httpx
import logging
import bcrypt
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Request, Header, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from database.supabase_client import get_supabase
from config.settings import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_HOURS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/proof", tags=["proof"])
security = HTTPBearer()

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET_PROOF = os.environ.get("STRIPE_WEBHOOK_SECRET_PROOF")
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

PROOF_PRICE_IDS = {
    "individual": os.environ.get("STRIPE_PRICE_PROOF_INDIVIDUAL"),
    "team":       os.environ.get("STRIPE_PRICE_PROOF_TEAM"),
    "company":    os.environ.get("STRIPE_PRICE_PROOF_COMPANY"),
    "credits_25":  os.environ.get("STRIPE_PRICE_PROOF_CREDITS_25"),
    "credits_50":  os.environ.get("STRIPE_PRICE_PROOF_CREDITS_50"),
    "credits_100": os.environ.get("STRIPE_PRICE_PROOF_CREDITS_100"),
}

PLAN_SEAT_LIMITS = {
    "individual": 1,
    "team": 10,
    "company": 25,
    "enterprise": None,
}

CREDIT_PACK_AMOUNTS = {
    "credits_25":  25.00,
    "credits_50":  50.00,
    "credits_100": 100.00,
}

ENRICHMENT_COST  = 0.01   # Google Places
DOSSIER_COST     = 1.00   # Claude AI

PROOF_SUCCESS_URL = "https://proof.en-place.ai/register?session_id={CHECKOUT_SESSION_ID}"
PROOF_CANCEL_URL  = "https://proof.en-place.ai/pricing"


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ProofRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    company: Optional[str] = None
    phone: Optional[str] = None

class ProofLoginRequest(BaseModel):
    email: EmailStr
    password: str

class ProofSearchRequest(BaseModel):
    states: Optional[List[str]] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    county: Optional[str] = None
    categories: Optional[List[str]] = None
    license_status: Optional[str] = "active"
    new_since_days: Optional[int] = None      # premium: new issuances
    expiring_within_days: Optional[int] = None # premium: expiring soon
    page: int = 1
    page_size: int = 25

class ProofSubscriptionRequest(BaseModel):
    plan: str  # individual, team, company

class ProofCreditsRequest(BaseModel):
    pack: str  # credits_25, credits_50, credits_100

class ProofDigestRequest(BaseModel):
    states: List[str]
    cities: Optional[List[str]] = []
    categories: Optional[List[str]] = ["restaurant", "bar", "restaurant_bar"]

class OrgInviteRequest(BaseModel):
    email: EmailStr
    full_name: str


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def create_proof_token(user: dict) -> str:
    payload = {
        "proof_user_id": str(user["id"]),
        "email": user["email"],
        "full_name": user.get("full_name"),
        "plan": user.get("plan", "free"),
        "plan_status": user.get("plan_status", "active"),
        "organization_id": str(user["organization_id"]) if user.get("organization_id") else None,
        "is_org_admin": user.get("is_org_admin", False),
        "portal_access": "proof",
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_proof_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    try:
        payload = jwt.decode(
            credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
        if payload.get("portal_access") != "proof":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


def require_paid(current_user: dict = Depends(verify_proof_token)) -> dict:
    """Gate endpoints to paid plans only."""
    if current_user.get("plan", "free") == "free":
        raise HTTPException(
            status_code=403,
            detail="This feature requires a paid plan. Upgrade at proof.en-place.ai/pricing"
        )
    if current_user.get("plan_status") not in ("active", "trialing"):
        raise HTTPException(
            status_code=403,
            detail="Your subscription is inactive. Please update your billing."
        )
    return current_user


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTER & LOGIN
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/register")
async def proof_register(data: ProofRegisterRequest):
    """Create a free Proof Intelligence account."""
    supabase = get_supabase()

    # Check duplicate email
    existing = supabase.table("proof_users") \
        .select("id") \
        .eq("email", data.email.lower()) \
        .execute()

    if existing.data:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = supabase.table("proof_users").insert({
        "email": data.email.lower(),
        "password_hash": hash_password(data.password),
        "full_name": data.full_name,
        "company": data.company,
        "phone": data.phone,
        "plan": "free",
        "plan_status": "active",
        "credit_balance": 0,
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    if not user.data:
        raise HTTPException(status_code=500, detail="Failed to create account")

    token = create_proof_token(user.data[0])

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user.data[0]["id"],
            "email": user.data[0]["email"],
            "full_name": user.data[0]["full_name"],
            "plan": "free",
            "credit_balance": 0
        }
    }


@router.post("/login")
async def proof_login(data: ProofLoginRequest):
    """Authenticate a Proof Intelligence user."""
    supabase = get_supabase()

    result = supabase.table("proof_users") \
        .select("*") \
        .eq("email", data.email.lower()) \
        .execute()

    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = result.data[0]

    if not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.get("plan_status") == "cancelled":
        raise HTTPException(status_code=403, detail="Your account has been cancelled")

    # Update last login
    supabase.table("proof_users").update({
        "last_login": datetime.utcnow().isoformat()
    }).eq("id", user["id"]).execute()

    token = create_proof_token(user)

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "plan": user["plan"],
            "plan_status": user["plan_status"],
            "credit_balance": float(user.get("credit_balance", 0)),
            "organization_id": user.get("organization_id"),
            "is_org_admin": user.get("is_org_admin", False)
        }
    }


@router.get("/me")
async def proof_me(current_user: dict = Depends(verify_proof_token)):
    """Get current user profile and credit balance."""
    supabase = get_supabase()

    result = supabase.table("proof_users") \
        .select("id, email, full_name, company, plan, plan_status, credit_balance, organization_id, is_org_admin, created_at") \
        .eq("id", current_user["proof_user_id"]) \
        .single() \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    return {"success": True, "user": result.data}


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/search")
async def proof_search(
    data: ProofSearchRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """
    Search prospect_master records.
    Free tier: basic fields only, no export, no time-based filters.
    Paid tier: full fields, time-based filters, export.
    """
    supabase = get_supabase()
    plan = current_user.get("plan", "free")
    is_paid = plan != "free"

    # Block premium filters for free users
    if not is_paid and (data.new_since_days or data.expiring_within_days):
        raise HTTPException(
            status_code=403,
            detail="New issuance and expiry filters require a paid plan"
        )

    # Build base query
    query = supabase.table("prospect_master") \
        .select(
            "id, legal_name, dba_name, business_category, raw_license_type, "
            "license_status, premise_address1, premise_city, premise_state, "
            "premise_zip, premise_county, license_issue_date, license_expiry_date, "
            "first_seen_at, is_current, latitude, longitude"
        ) \
        .eq("is_current", True) \
        .not_.in_("license_status", ["CANCELED / DEACTIVATED", "EXPIRED", "CANCELLED", "REVOKED", "INACTIVE"])

    # Filters
    if data.states:
        query = query.in_("premise_state", data.states)

    if data.city:
        query = query.ilike("premise_city", f"%{data.city}%")

    if data.zip_code:
        query = query.eq("premise_zip", data.zip_code)

    if data.county:
        query = query.ilike("premise_county", f"%{data.county}%")

    if data.categories:
        query = query.in_("business_category", data.categories)

    if data.license_status:
        query = query.not_.in_("license_status", ["CANCELED / DEACTIVATED", "EXPIRED", "CANCELLED", "REVOKED", "INACTIVE", "DENIED"])

    # Premium time-based filters
    if is_paid and data.new_since_days:
        cutoff = (datetime.utcnow() - timedelta(days=data.new_since_days)).date().isoformat()
        query = query.gte("license_issue_date", cutoff) \
                     .lte("license_issue_date", datetime.utcnow().date().isoformat())

    if is_paid and data.expiring_within_days:
        today = datetime.utcnow().date().isoformat()
        future = (datetime.utcnow() + timedelta(days=data.expiring_within_days)).date().isoformat()
        query = query.gte("license_expiry_date", today) \
                     .lte("license_expiry_date", future)

    # Pagination
    offset = (data.page - 1) * data.page_size
    query = query.range(offset, offset + data.page_size - 1)

    result = query.execute()

    # Log search for analytics
    try:
        supabase.table("prospect_searches").insert({
            "user_id": current_user["proof_user_id"],
            "filters": {
                "states": data.states,
                "city": data.city,
                "zip": data.zip_code,
                "categories": data.categories,
                "new_since_days": data.new_since_days,
                "expiring_within_days": data.expiring_within_days
            },
            "result_count": len(result.data),
            "created_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception:
        pass  # Don't fail search if logging fails

    return {
        "success": True,
        "results": result.data,
        "page": data.page,
        "page_size": data.page_size,
        "plan": plan
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ENRICHMENT (Google Places)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/enrich/{prospect_id}")
async def proof_enrich(
    prospect_id: str,
    current_user: dict = Depends(require_paid)
):
    """
    Enrich a prospect record with Google Places data.
    Costs $0.01 per record. Cached permanently after first lookup.
    """
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    # Check credit balance
    user = supabase.table("proof_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()

    balance = float(user.data.get("credit_balance", 0))
    if balance < ENRICHMENT_COST:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. You have ${balance:.2f}, enrichment costs ${ENRICHMENT_COST:.2f}"
        )

    # Check enrichment cache first
    cached = supabase.table("prospect_enrichments") \
        .select("*") \
        .eq("prospect_id", prospect_id) \
        .execute()

    if cached.data:
        # Return cached — no charge
        return {
            "success": True,
            "enrichment": cached.data[0],
            "cached": True,
            "charged": 0
        }

    # Get prospect record
    prospect = supabase.table("prospect_master") \
        .select("dba_name, legal_name, premise_address1, premise_city, premise_state") \
        .eq("id", prospect_id) \
        .single() \
        .execute()

    if not prospect.data:
        raise HTTPException(status_code=404, detail="Prospect not found")

    p = prospect.data
    business_name = p.get("dba_name") or p.get("legal_name", "")
    location = f"{p.get('premise_city', '')}, {p.get('premise_state', '')}"
    query = f"{business_name} {p.get('premise_address1', '')} {location}"

    # Call Google Places API
    try:
        async with httpx.AsyncClient() as client:
            # Find Place
            find_resp = await client.get(
                "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
                params={
                    "input": query,
                    "inputtype": "textquery",
                    "fields": "place_id,name,formatted_address",
                    "key": GOOGLE_PLACES_API_KEY
                },
                timeout=10.0
            )
            find_data = find_resp.json()

            enrichment = {
                "prospect_id": prospect_id,
                "enrichment_source": "google_places",
                "enriched_at": datetime.utcnow().isoformat(),
                "confidence_score": 0
            }

            if find_data.get("candidates"):
                place_id = find_data["candidates"][0]["place_id"]

                # Get Place Details
                detail_resp = await client.get(
                    "https://maps.googleapis.com/maps/api/place/details/json",
                    params={
                        "place_id": place_id,
                        "fields": "name,formatted_phone_number,website,rating,user_ratings_total,opening_hours",
                        "key": GOOGLE_PLACES_API_KEY
                    },
                    timeout=10.0
                )
                detail_data = detail_resp.json()
                place = detail_data.get("result", {})

                enrichment.update({
                    "phone": place.get("formatted_phone_number"),
                    "website": place.get("website"),
                    "google_rating": place.get("rating"),
                    "google_review_count": place.get("user_ratings_total"),
                    "confidence_score": 0.85
                })

            # Save to cache
            supabase.table("prospect_enrichments").insert(enrichment).execute()

            # Deduct credit
            new_balance = balance - ENRICHMENT_COST
            supabase.table("proof_users").update({
                "credit_balance": new_balance
            }).eq("id", user_id).execute()

            # Log transaction
            supabase.table("proof_credit_transactions").insert({
                "user_id": user_id,
                "transaction_type": "enrichment",
                "amount": -ENRICHMENT_COST,
                "balance_after": new_balance,
                "description": f"Enrichment: {business_name}",
                "prospect_id": prospect_id,
                "created_at": datetime.utcnow().isoformat()
            }).execute()

            return {
                "success": True,
                "enrichment": enrichment,
                "cached": False,
                "charged": ENRICHMENT_COST,
                "balance_remaining": new_balance
            }

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Google Places API timed out")
    except Exception as e:
        logger.error(f"Enrichment error for {prospect_id}: {e}")
        raise HTTPException(status_code=500, detail="Enrichment failed")


# ═══════════════════════════════════════════════════════════════════════════════
# DOSSIER (Claude AI)
# ═══════════════════════════════════════════════════════════════════════════════

DOSSIER_SYSTEM_PROMPT = """You are a restaurant industry intelligence researcher. When given a restaurant name and location, conduct exhaustive research and return a complete dossier that a sales rep or vendor can use to personalize their outreach and understand the account before walking in the door.

Use web search aggressively. Check Google, Yelp, Facebook, Instagram, TripAdvisor, LinkedIn, state business registrations, local news, and any other available public sources.

Return the report using these exact section headers. Plain text, no markdown tables. Direct and factual. No filler. If a section has nothing useful, write "Not found" and move on.

### 1. Basic Information
Full legal business name, DBA, all addresses, phone, website URL (note quality: professional, outdated, broken, none, Facebook-only), hours, cuisine type, price range, estimated seating, year established, alcohol license type.

### 2. Ownership & Management
Owner name(s), background, management structure, other businesses owned, LLC/entity name from state registry, multi-unit group check.

### 3. LinkedIn Intelligence
Search owner name + restaurant + LinkedIn. Profile URL, title, experience, hospitality background, tenure, other ventures, associations.

### 4. Online Presence Audit
Website platform and quality, Google Business Profile (claimed, rating, review count, response rate), Yelp (rating, count, claimed), Facebook (followers, posting frequency), Instagram (handle, followers, quality), TripAdvisor, third-party ordering platforms, reservation systems.

### 5. Reputation & Reviews
Overall sentiment, praise themes, complaint themes, notable media coverage, health inspection issues, awards.

### 6. Menu & Operations
Menu highlights, format, average check, service types, special services, POS system if identifiable, active job postings.

### 7. Competitive Landscape
Direct competitors within 5 miles, differentiation, competitor digital presence comparison.

### 8. Account Intelligence
Estimated annual revenue range, estimated employee count, identifiable pain points, website quality score (1-5), best contact method, owner email if public, best time to reach, hiring activity signal.

### 9. Multi-Unit Intelligence
All locations under same ownership, business registrations check. Note: one location often opens door to all.

### 10. Recommended Approach
3-4 sentences: strongest hook, recommended first contact method, landmines to avoid, high/medium/low opportunity rating."""


@router.post("/dossier/{prospect_id}")
async def proof_dossier(
    prospect_id: str,
    current_user: dict = Depends(require_paid)
):
    """
    Generate a full AI dossier for a prospect.
    Costs $1.00 per record. Cached permanently after first generation.
    """
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    # Check credit balance
    user = supabase.table("proof_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()

    balance = float(user.data.get("credit_balance", 0))
    if balance < DOSSIER_COST:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. You have ${balance:.2f}, dossier costs ${DOSSIER_COST:.2f}"
        )

    # Check dossier cache
    cached = supabase.table("proof_dossier_cache") \
        .select("*") \
        .eq("prospect_id", prospect_id) \
        .execute()

    if cached.data:
        return {
            "success": True,
            "dossier": cached.data[0]["dossier_text"],
            "cached": True,
            "charged": 0
        }

    # Get prospect
    prospect = supabase.table("prospect_master") \
        .select("dba_name, legal_name, premise_address1, premise_city, premise_state, premise_zip, business_category, raw_license_type") \
        .eq("id", prospect_id) \
        .single() \
        .execute()

    if not prospect.data:
        raise HTTPException(status_code=404, detail="Prospect not found")

    p = prospect.data
    business_name = p.get("dba_name") or p.get("legal_name", "Unknown")
    city = p.get("premise_city", "")
    state = p.get("premise_state", "")

    user_prompt = f"Generate a complete dossier for: {business_name}, located at {p.get('premise_address1', '')}, {city}, {state} {p.get('premise_zip', '')}. License type: {p.get('raw_license_type', '')}."

    # Call Anthropic API
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4000,
                    "system": DOSSIER_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_prompt}]
                },
                timeout=120.0
            )
            resp_data = resp.json()

        if resp.status_code != 200:
            logger.error(f"Anthropic API error: {resp_data}")
            raise HTTPException(status_code=500, detail="Dossier generation failed")

        dossier_text = resp_data["content"][0]["text"]

        # Cache dossier
        supabase.table("proof_dossier_cache").insert({
            "prospect_id": prospect_id,
            "dossier_text": dossier_text,
            "generated_by": user_id,
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        # Deduct credit
        new_balance = balance - DOSSIER_COST
        supabase.table("proof_users").update({
            "credit_balance": new_balance
        }).eq("id", user_id).execute()

        # Log transaction
        supabase.table("proof_credit_transactions").insert({
            "user_id": user_id,
            "transaction_type": "dossier",
            "amount": -DOSSIER_COST,
            "balance_after": new_balance,
            "description": f"Dossier: {business_name}",
            "prospect_id": prospect_id,
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        return {
            "success": True,
            "dossier": dossier_text,
            "cached": False,
            "charged": DOSSIER_COST,
            "balance_remaining": new_balance
        }

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Dossier generation timed out. Please try again.")
    except Exception as e:
        logger.error(f"Dossier error for {prospect_id}: {e}")
        raise HTTPException(status_code=500, detail="Dossier generation failed")


# ═══════════════════════════════════════════════════════════════════════════════
# CREDITS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/checkout/credits")
async def proof_buy_credits(
    data: ProofCreditsRequest,
    current_user: dict = Depends(require_paid)
):
    """Create a Stripe checkout session for credit top-up."""
    if data.pack not in PROOF_PRICE_IDS:
        raise HTTPException(status_code=400, detail="Invalid credit pack")

    price_id = PROOF_PRICE_IDS.get(data.pack)
    if not price_id:
        raise HTTPException(status_code=500, detail="Credit pack price not configured")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=PROOF_SUCCESS_URL,
            cancel_url=PROOF_CANCEL_URL,
            metadata={
                "proof_user_id": current_user["proof_user_id"],
                "credit_pack": data.pack,
                "credit_amount": str(CREDIT_PACK_AMOUNTS[data.pack])
            }
        )
        return {"success": True, "session_id": session.id, "url": session.url}

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/checkout/subscription")
async def proof_subscribe(
    data: ProofSubscriptionRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Create a Stripe checkout session for a subscription plan."""
    if data.plan not in ("individual", "team", "company"):
        raise HTTPException(status_code=400, detail="Invalid plan")

    price_id = PROOF_PRICE_IDS.get(data.plan)
    if not price_id:
        raise HTTPException(status_code=500, detail="Plan price not configured")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=PROOF_SUCCESS_URL,
            cancel_url=PROOF_CANCEL_URL,
            allow_promotion_codes=True,
            billing_address_collection="required",
            metadata={
                "proof_user_id": current_user["proof_user_id"],
                "proof_plan": data.plan
            },
            subscription_data={
                "metadata": {
                    "proof_user_id": current_user["proof_user_id"],
                    "proof_plan": data.plan
                }
            }
        )
        return {"success": True, "session_id": session.id, "url": session.url}

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/credits/history")
async def proof_credit_history(
    current_user: dict = Depends(verify_proof_token)
):
    """Get credit transaction history for current user."""
    supabase = get_supabase()

    result = supabase.table("proof_credit_transactions") \
        .select("*") \
        .eq("user_id", current_user["proof_user_id"]) \
        .order("created_at", desc=True) \
        .limit(50) \
        .execute()

    return {"success": True, "transactions": result.data}


# ═══════════════════════════════════════════════════════════════════════════════
# ORGANIZATION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/org/members")
async def proof_org_members(
    current_user: dict = Depends(require_paid)
):
    """Get all members of the current user's organization. Admin only."""
    if not current_user.get("is_org_admin"):
        raise HTTPException(status_code=403, detail="Organization admin access required")

    org_id = current_user.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="You are not part of an organization")

    supabase = get_supabase()

    members = supabase.table("proof_users") \
        .select("id, email, full_name, plan, plan_status, credit_balance, last_login, created_at") \
        .eq("organization_id", org_id) \
        .execute()

    org = supabase.table("proof_organizations") \
        .select("name, plan, seat_limit") \
        .eq("id", org_id) \
        .single() \
        .execute()

    return {
        "success": True,
        "organization": org.data,
        "members": members.data,
        "seat_count": len(members.data),
        "seat_limit": org.data.get("seat_limit") if org.data else None
    }


@router.post("/org/invite")
async def proof_org_invite(
    data: OrgInviteRequest,
    current_user: dict = Depends(require_paid)
):
    """Invite a user to the organization. Admin only."""
    if not current_user.get("is_org_admin"):
        raise HTTPException(status_code=403, detail="Organization admin access required")

    org_id = current_user.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="You are not part of an organization")

    supabase = get_supabase()

    # Check seat limit
    org = supabase.table("proof_organizations") \
        .select("seat_limit, plan") \
        .eq("id", org_id) \
        .single() \
        .execute()

    seat_limit = org.data.get("seat_limit") if org.data else None

    if seat_limit:
        current_members = supabase.table("proof_users") \
            .select("id") \
            .eq("organization_id", org_id) \
            .execute()

        if len(current_members.data) >= seat_limit:
            raise HTTPException(
                status_code=403,
                detail=f"Seat limit reached ({seat_limit} seats). Upgrade your plan to add more members."
            )

    # Check if user already exists
    existing = supabase.table("proof_users") \
        .select("id, organization_id") \
        .eq("email", data.email.lower()) \
        .execute()

    if existing.data:
        user = existing.data[0]
        if user.get("organization_id"):
            raise HTTPException(status_code=409, detail="This user is already part of an organization")

        # Add existing user to org
        supabase.table("proof_users").update({
            "organization_id": org_id,
            "plan": org.data["plan"],
            "plan_status": "active"
        }).eq("id", user["id"]).execute()

        return {"success": True, "message": f"{data.email} added to your organization", "new_user": False}

    # Create new user with temp password — they'll reset on first login
    import secrets
    temp_password = secrets.token_urlsafe(16)

    new_user = supabase.table("proof_users").insert({
        "email": data.email.lower(),
        "password_hash": hash_password(temp_password),
        "full_name": data.full_name,
        "plan": org.data["plan"],
        "plan_status": "active",
        "organization_id": org_id,
        "is_org_admin": False,
        "credit_balance": 0,
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    # TODO: Send welcome email with temp password via SendGrid

    return {
        "success": True,
        "message": f"Account created for {data.email}. They will receive a welcome email.",
        "new_user": True,
        "temp_password": temp_password  # Remove once SendGrid email is wired up
    }


# ═══════════════════════════════════════════════════════════════════════════════
# WEEKLY DIGEST
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/digest/subscribe")
async def proof_digest_subscribe(
    data: ProofDigestRequest,
    current_user: dict = Depends(require_paid)
):
    """Subscribe to weekly new license digest."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    # Upsert digest subscription
    existing = supabase.table("proof_digest_subscriptions") \
        .select("id") \
        .eq("user_id", user_id) \
        .execute()

    payload = {
        "user_id": user_id,
        "states": data.states,
        "cities": data.cities,
        "categories": data.categories,
        "is_active": True
    }

    if existing.data:
        supabase.table("proof_digest_subscriptions") \
            .update(payload) \
            .eq("user_id", user_id) \
            .execute()
    else:
        payload["created_at"] = datetime.utcnow().isoformat()
        supabase.table("proof_digest_subscriptions").insert(payload).execute()

    return {
        "success": True,
        "message": f"You'll receive weekly new license alerts for: {', '.join(data.states)}"
    }


@router.get("/digest/subscription")
async def proof_digest_get(
    current_user: dict = Depends(require_paid)
):
    """Get current digest subscription settings."""
    supabase = get_supabase()

    result = supabase.table("proof_digest_subscriptions") \
        .select("*") \
        .eq("user_id", current_user["proof_user_id"]) \
        .execute()

    return {
        "success": True,
        "subscription": result.data[0] if result.data else None
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STRIPE WEBHOOK (Proof-specific)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/stripe/webhook")
async def proof_stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature")
):
    """Handle Stripe webhook events for Proof Intelligence."""
    payload = await request.body()

    if STRIPE_WEBHOOK_SECRET_PROOF and stripe_signature:
        try:
            event = stripe.Webhook.construct_event(
                payload, stripe_signature, STRIPE_WEBHOOK_SECRET_PROOF
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        import json
        event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)

    event_type = event.type
    data = event.data.object
    logger.info(f"Proof Stripe webhook: {event_type}")

    if event_type == "checkout.session.completed":
        await handle_proof_checkout(data)

    elif event_type == "customer.subscription.deleted":
        await handle_proof_subscription_cancelled(data)

    elif event_type == "invoice.payment_failed":
        await handle_proof_payment_failed(data)

    return {"received": True}


async def handle_proof_checkout(session):
    """Handle completed checkout — activate subscription or add credits."""
    supabase = get_supabase()
    meta = session.metadata or {}
    user_id = meta.get("proof_user_id")

    if not user_id:
        logger.error("Proof checkout completed with no proof_user_id in metadata")
        return

    try:
        # Credit purchase
        if meta.get("credit_pack"):
            credit_amount = float(meta.get("credit_amount", 0))
            if credit_amount > 0:
                user = supabase.table("proof_users") \
                    .select("credit_balance") \
                    .eq("id", user_id) \
                    .single() \
                    .execute()

                current_balance = float(user.data.get("credit_balance", 0))
                new_balance = current_balance + credit_amount

                supabase.table("proof_users").update({
                    "credit_balance": new_balance
                }).eq("id", user_id).execute()

                supabase.table("proof_credit_transactions").insert({
                    "user_id": user_id,
                    "transaction_type": "purchase",
                    "amount": credit_amount,
                    "balance_after": new_balance,
                    "description": f"Credit purchase: ${credit_amount:.0f} pack",
                    "stripe_payment_intent_id": session.payment_intent,
                    "created_at": datetime.utcnow().isoformat()
                }).execute()

                logger.info(f"Added ${credit_amount} credits to user {user_id}")

        # Subscription purchase
        elif meta.get("proof_plan"):
            plan = meta["proof_plan"]
            seat_limit = PLAN_SEAT_LIMITS.get(plan)

            # Check if user is creating an org (team/company plans)
            if plan in ("team", "company"):
                user = supabase.table("proof_users") \
                    .select("full_name, company, organization_id") \
                    .eq("id", user_id) \
                    .single() \
                    .execute()

                if not user.data.get("organization_id"):
                    # Create org
                    org = supabase.table("proof_organizations").insert({
                        "name": user.data.get("company") or f"{user.data.get('full_name')}'s Team",
                        "plan": plan,
                        "plan_status": "active",
                        "stripe_customer_id": session.customer,
                        "stripe_subscription_id": session.subscription,
                        "seat_limit": seat_limit,
                        "admin_user_id": user_id,
                        "created_at": datetime.utcnow().isoformat()
                    }).execute()

                    org_id = org.data[0]["id"]

                    supabase.table("proof_users").update({
                        "plan": plan,
                        "plan_status": "active",
                        "stripe_customer_id": session.customer,
                        "stripe_subscription_id": session.subscription,
                        "organization_id": org_id,
                        "is_org_admin": True
                    }).eq("id", user_id).execute()
                else:
                    supabase.table("proof_users").update({
                        "plan": plan,
                        "plan_status": "active",
                        "stripe_customer_id": session.customer,
                        "stripe_subscription_id": session.subscription,
                    }).eq("id", user_id).execute()

            else:
                # Individual plan
                supabase.table("proof_users").update({
                    "plan": plan,
                    "plan_status": "active",
                    "stripe_customer_id": session.customer,
                    "stripe_subscription_id": session.subscription,
                }).eq("id", user_id).execute()

            logger.info(f"Activated {plan} plan for user {user_id}")

    except Exception as e:
        logger.error(f"Error handling proof checkout: {e}")


async def handle_proof_subscription_cancelled(subscription):
    """Mark user as cancelled when subscription ends."""
    supabase = get_supabase()
    try:
        supabase.table("proof_users").update({
            "plan": "free",
            "plan_status": "cancelled"
        }).eq("stripe_subscription_id", subscription.id).execute()

        logger.info(f"Proof subscription cancelled: {subscription.id}")
    except Exception as e:
        logger.error(f"Error handling proof cancellation: {e}")


async def handle_proof_payment_failed(invoice):
    """Mark user as past_due on payment failure."""
    supabase = get_supabase()
    try:
        supabase.table("proof_users").update({
            "plan_status": "past_due"
        }).eq("stripe_subscription_id", invoice.subscription).execute()

        logger.warning(f"Proof payment failed for subscription: {invoice.subscription}")
    except Exception as e:
        logger.error(f"Error handling proof payment failure: {e}")