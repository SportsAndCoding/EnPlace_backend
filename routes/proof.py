# routes/proof.py
"""
Proof Intelligence API
======================
National liquor license database with enrichment, dossiers, and sales intelligence.

Session refinements applied:
- Conditional pricing: free vs paid tier rates
- No-charge enrichment when Google returns no useful data
- Yelp Fusion parallel enrichment
- Expanded Google Places fields
- Deterministic search sort
- Removed duplicate license_status filter
- Leadership Signal (GM vacancy) in dossier prompt
- Outreach drafting endpoint
- $10 credit pack for free tier users
"""

import os
import jwt
import stripe
import httpx
import asyncio
import logging
import bcrypt
import secrets
import json
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
GOOGLE_PLACES_API_KEY       = os.environ.get("GOOGLE_PLACES_API_KEY")
YELP_API_KEY                = os.environ.get("YELP_API_KEY")
ANTHROPIC_API_KEY           = os.environ.get("ANTHROPIC_API_KEY")

PROOF_PRICE_IDS = {
    "individual":  os.environ.get("STRIPE_PRICE_PROOF_INDIVIDUAL"),
    "team":        os.environ.get("STRIPE_PRICE_PROOF_TEAM"),
    "company":     os.environ.get("STRIPE_PRICE_PROOF_COMPANY"),
    "credits_10":  os.environ.get("STRIPE_PRICE_PROOF_CREDITS_10"),   # free tier entry
    "credits_25":  os.environ.get("STRIPE_PRICE_PROOF_CREDITS_25"),
    "credits_50":  os.environ.get("STRIPE_PRICE_PROOF_CREDITS_50"),
    "credits_100": os.environ.get("STRIPE_PRICE_PROOF_CREDITS_100"),
}

PLAN_SEAT_LIMITS = {
    "individual": 1,
    "team":       10,
    "company":    25,
    "enterprise": None,
}

CREDIT_PACK_AMOUNTS = {
    "credits_10":  10.00,
    "credits_25":  25.00,
    "credits_50":  50.00,
    "credits_100": 100.00,
}

# ── Pricing by plan ──
# Free tier pays premium rates. Paid tier pays standard rates.
ENRICHMENT_COST_PAID = 0.01
ENRICHMENT_COST_FREE = 0.25
DOSSIER_COST_PAID    = 1.00
DOSSIER_COST_FREE    = 10.00

# Dead license statuses — excluded from all search results
DEAD_STATUSES = [
    "CANCELED / DEACTIVATED", "EXPIRED", "CANCELLED",
    "REVOKED", "INACTIVE", "DENIED", "VOID"
]

PROOF_SUCCESS_URL = "https://proof.en-place.ai/credits?session_id={CHECKOUT_SESSION_ID}"
PROOF_CANCEL_URL  = "https://proof.en-place.ai/credits"


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
    address: Optional[str] = None
    categories: Optional[List[str]] = None
    new_since_days: Optional[int] = None
    expiring_within_days: Optional[int] = None
    page: int = 1
    page_size: int = 25

class ProofSubscriptionRequest(BaseModel):
    plan: str

class ProofCreditsRequest(BaseModel):
    pack: str

class ProofDigestRequest(BaseModel):
    states: List[str]
    cities: Optional[List[str]] = []
    categories: Optional[List[str]] = ["restaurant", "bar", "restaurant_bar"]

class OrgInviteRequest(BaseModel):
    email: EmailStr
    full_name: str

class ProofOutreachRequest(BaseModel):
    prospect_id: str
    dossier_text: str
    outreach_type: Optional[str] = "email"  # email, call_script, both


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
    """Require any active plan (free users can still use paid endpoints at premium rates)."""
    if current_user.get("plan_status") not in ("active", "trialing", None):
        raise HTTPException(
            status_code=403,
            detail="Your subscription is inactive. Please update your billing."
        )
    return current_user


def get_costs(current_user: dict) -> tuple:
    """Return (enrichment_cost, dossier_cost) based on user plan."""
    is_paid = current_user.get("plan", "free") != "free"
    return (
        ENRICHMENT_COST_PAID if is_paid else ENRICHMENT_COST_FREE,
        DOSSIER_COST_PAID    if is_paid else DOSSIER_COST_FREE
    )


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
    Free: browse only, no time-based filters.
    Paid: full filters, export enabled.
    Results are deterministically sorted by city + DBA name.
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

    # Base query — exclude dead licenses, sort deterministically
    query = supabase.table("prospect_master") \
        .select(
            "id, legal_name, dba_name, business_category, raw_license_type, "
            "license_status, premise_address1, premise_city, premise_state, "
            "premise_zip, premise_county, license_issue_date, license_expiry_date, "
            "first_seen_at, is_current, latitude, longitude"
        ) \
        .eq("is_current", True) \
        .not_.in_("license_status", DEAD_STATUSES) \
        .order("premise_city") \
        .order("dba_name")

    # Filters
    if data.states:
        query = query.in_("premise_state", data.states)

    if data.city:
        query = query.ilike("premise_city", f"%{data.city}%")

    if data.zip_code:
        query = query.eq("premise_zip", data.zip_code)

    if data.county:
        query = query.ilike("premise_county", f"%{data.county}%")

    if data.address:
        query = query.ilike("premise_address1", f"%{data.address}%")

    if data.categories:
        query = query.in_("business_category", data.categories)

    # Premium: new issuances (via license_issue_date where available)
    if is_paid and data.new_since_days:
        cutoff = (datetime.utcnow() - timedelta(days=data.new_since_days)).date().isoformat()
        query = query \
            .gte("license_issue_date", cutoff) \
            .lte("license_issue_date", datetime.utcnow().date().isoformat()) \
            .gt("license_issue_date", "2000-01-01")

    # Premium: expiring soon
    if is_paid and data.expiring_within_days:
        today  = datetime.utcnow().date().isoformat()
        future = (datetime.utcnow() + timedelta(days=data.expiring_within_days)).date().isoformat()
        query  = query \
            .gte("license_expiry_date", today) \
            .lte("license_expiry_date", future)

    # Pagination
    offset = (data.page - 1) * data.page_size
    query  = query.range(offset, offset + data.page_size - 1)

    result = query.execute()

    # Log search (non-blocking)
    try:
        supabase.table("prospect_searches").insert({
            "proof_user_id": current_user["proof_user_id"],
            "filters": {
                "states": data.states,
                "city": data.city,
                "zip": data.zip_code,
                "address": data.address,
                "categories": data.categories,
                "new_since_days": data.new_since_days,
                "expiring_within_days": data.expiring_within_days
            },
            "result_count": len(result.data),
            "created_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception:
        pass

    return {
        "success": True,
        "results": result.data,
        "page": data.page,
        "page_size": data.page_size,
        "plan": plan
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ENRICHMENT — Google Places + Yelp Fusion (parallel)
# ═══════════════════════════════════════════════════════════════════════════════

async def _fetch_google_places(client: httpx.AsyncClient, query: str) -> dict:
    """Fetch Google Places data for a business."""
    try:
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

        if not find_data.get("candidates"):
            return {}

        place_id = find_data["candidates"][0]["place_id"]

        detail_resp = await client.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                "place_id": place_id,
                "fields": "name,formatted_phone_number,website,rating,user_ratings_total,opening_hours,price_level,business_status",
                "key": GOOGLE_PLACES_API_KEY
            },
            timeout=10.0
        )
        detail_data = detail_resp.json()
        place = detail_data.get("result", {})

        return {
            "phone": place.get("formatted_phone_number"),
            "website": place.get("website"),
            "google_rating": place.get("rating"),
            "google_review_count": place.get("user_ratings_total"),
            "price_level": place.get("price_level"),
            "business_status": place.get("business_status"),
            "opening_hours": place.get("opening_hours", {}).get("weekday_text") or None,
        }
    except Exception as e:
        logger.warning(f"Google Places error: {e}")
        return {}


async def _fetch_yelp(client: httpx.AsyncClient, business_name: str, city: str, state: str, address: str) -> dict:
    """Fetch Yelp Fusion data for a business."""
    if not YELP_API_KEY:
        return {}
    try:
        resp = await client.get(
            "https://api.yelp.com/v3/businesses/search",
            headers={"Authorization": f"Bearer {YELP_API_KEY}"},
            params={
                "term": business_name,
                "location": f"{address} {city} {state}",
                "limit": 1
            },
            timeout=10.0
        )
        data = resp.json()
        businesses = data.get("businesses", [])
        if not businesses:
            return {}

        biz = businesses[0]
        return {
            "yelp_rating": biz.get("rating"),
            "yelp_review_count": biz.get("review_count"),
        }
    except Exception as e:
        logger.warning(f"Yelp API error: {e}")
        return {}


@router.post("/enrich/{prospect_id}")
async def proof_enrich(
    prospect_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """
    Enrich a prospect with Google Places + Yelp data in parallel.

    Pricing:
      - Pro plan: $0.01 — only charged if useful data is returned
      - Free tier: $0.25 — only charged if useful data is returned
      - Cached results: always free
    """
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]
    enrichment_cost, _ = get_costs(current_user)

    # Check balance
    user = supabase.table("proof_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()

    balance = float(user.data.get("credit_balance", 0))
    if balance < enrichment_cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. Balance: ${balance:.2f}, cost: ${enrichment_cost:.2f}"
        )

    # Check cache — cached results are always free
    cached = supabase.table("prospect_enrichments") \
        .select("*") \
        .eq("prospect_id", prospect_id) \
        .execute()

    if cached.data:
        return {
            "success": True,
            "enrichment": cached.data[0],
            "cached": True,
            "charged": 0,
            "balance_remaining": balance
        }

    # Fetch prospect
    prospect = supabase.table("prospect_master") \
        .select("dba_name, legal_name, premise_address1, premise_city, premise_state, premise_zip") \
        .eq("id", prospect_id) \
        .single() \
        .execute()

    if not prospect.data:
        raise HTTPException(status_code=404, detail="Prospect not found")

    p = prospect.data
    business_name = p.get("dba_name") or p.get("legal_name", "")
    city     = p.get("premise_city", "")
    state    = p.get("premise_state", "")
    address  = p.get("premise_address1", "")
    gp_query = f"{business_name} {address} {city} {state}"

    try:
        async with httpx.AsyncClient() as client:
            # Run Google Places and Yelp in parallel
            google_data, yelp_data = await asyncio.gather(
                _fetch_google_places(client, gp_query),
                _fetch_yelp(client, business_name, city, state, address),
                return_exceptions=True
            )

        # Handle exceptions from gather
        if isinstance(google_data, Exception):
            logger.warning(f"Google Places failed: {google_data}")
            google_data = {}
        if isinstance(yelp_data, Exception):
            logger.warning(f"Yelp failed: {yelp_data}")
            yelp_data = {}

        # Merge results
        enrichment = {
            "prospect_id": prospect_id,
            "enrichment_source": "google_places+yelp",
            "enriched_at": datetime.utcnow().isoformat(),
            "confidence_score": 0.85 if google_data.get("phone") or google_data.get("website") else 0.3,
            **google_data,
            **yelp_data,
        }

        # Only charge if we got useful data
        useful_fields = ["phone", "website", "google_rating", "yelp_rating"]
        has_useful_data = any(enrichment.get(f) for f in useful_fields)

        # Save to cache regardless (prevents repeat API calls on empty results)
        supabase.table("prospect_enrichments").insert(enrichment).execute()

        if has_useful_data:
            new_balance = balance - enrichment_cost
            supabase.table("proof_users").update({
                "credit_balance": new_balance
            }).eq("id", user_id).execute()

            supabase.table("proof_credit_transactions").insert({
                "user_id": user_id,
                "transaction_type": "enrichment",
                "amount": -enrichment_cost,
                "balance_after": new_balance,
                "description": f"Enrichment: {business_name}",
                "prospect_id": prospect_id,
                "created_at": datetime.utcnow().isoformat()
            }).execute()

            return {
                "success": True,
                "enrichment": enrichment,
                "cached": False,
                "charged": enrichment_cost,
                "balance_remaining": new_balance
            }
        else:
            # No useful data found — no charge
            return {
                "success": True,
                "enrichment": enrichment,
                "cached": False,
                "charged": 0,
                "balance_remaining": balance,
                "message": "No public data found for this location — no charge applied"
            }

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Enrichment API timed out")
    except Exception as e:
        logger.error(f"Enrichment error for {prospect_id}: {e}")
        raise HTTPException(status_code=500, detail="Enrichment failed")


# ═══════════════════════════════════════════════════════════════════════════════
# DOSSIER — Claude AI with Leadership Signal
# ═══════════════════════════════════════════════════════════════════════════════

DOSSIER_SYSTEM_PROMPT = """You are a restaurant industry intelligence researcher. When given a restaurant name and location, conduct exhaustive research and return a complete dossier that a sales rep, vendor, or recruiter can use to understand the account before walking in the door.

Use web search aggressively. Check Google, Yelp, Facebook, Instagram, TripAdvisor, LinkedIn, state business registrations, Indeed, Google Jobs, and local news.

Return the report using these exact section headers. Plain text, no markdown tables. Direct and factual. No filler. If a section has nothing useful, write "Not found" and move on.

### 1. Basic Information
Full legal business name, DBA, all addresses, phone, website URL (note quality: professional, outdated, broken, none, Facebook-only), hours, cuisine type, price range ($-$$$$), estimated seating capacity, year established, alcohol license type.

### 2. Ownership & Management
Owner name(s), background (immigrant family, hospitality veteran, investor group, etc.), management structure (owner-operated, absentee, management company), other businesses owned by same entity, LLC/entity name from state registry, multi-unit group check.

### 3. LinkedIn Intelligence
Search owner name + restaurant name + LinkedIn. Profile URL if found, current title, previous experience, hospitality background, tenure at this restaurant, other business ventures, industry associations or groups.

### 4. Online Presence Audit
Website platform and quality score (1-5: 1=none, 2=Facebook only, 3=poor, 4=outdated, 5=solid), Google Business Profile (claimed yes/no, rating, review count, response rate to reviews), Yelp (rating, count, claimed), Facebook (followers, posting frequency, last post date), Instagram (handle, followers, content quality), TripAdvisor ranking, third-party ordering platforms, reservation systems.

### 5. Reputation & Reviews
Overall sentiment (positive/mixed/negative), common praise themes, common complaint themes, notable food blogger or media coverage, any health inspection issues or public complaints, awards or recognition.

### 6. Menu & Operations
Menu highlights and signature dishes, menu format (printed only/PDF/interactive online), estimated average check per person, dine-in/takeout/delivery/catering availability, special services (private dining, events, happy hour), POS system if identifiable.

### 7. Hiring Activity
Search Indeed, LinkedIn, and Google Jobs for active job postings at this establishment. Report:
- Total number of open positions
- Departments hiring (FOH, BOH, management)
- Specific roles listed
- How long postings have been active
- Whether volume suggests growth or turnover problems
If no postings found, note that explicitly.

### 8. Competitive Landscape
Direct competitors within 5 miles in the same cuisine category. How does this establishment differentiate? Competitor website quality comparison.

### 9. Account Intelligence
Estimated annual revenue range (based on seating, price point, location), estimated employee count, identifiable pain points based on reviews and online presence, best contact method (phone/email/walk-in/social), owner email if publicly available, best time to reach (avoid lunch and dinner rush), website quality score (1-5).

### 10. Multi-Unit Intelligence
If the owner has multiple restaurants, list ALL of them with addresses. Check business registrations under same owner name and LLC. Note: landing one location in a group often opens the door to all locations.

### 11. Leadership Signal
Search Indeed, LinkedIn, Google Jobs, and general web search for evidence of GM or management changes at this location.

Investigate:
- Is there an active General Manager or AGM job posting?
- Are there recent LinkedIn profiles listing this restaurant as current employer in a GM role?
- Do recent Google or Yelp reviews mention "new management", "new owner", or "under new ownership"?
- Any local news about leadership change?

Return exactly one of these three verdicts on its own line:
LEADERSHIP: GM STABLE
LEADERSHIP: GM VACANCY
LEADERSHIP: GM TRANSITION

Then in one sentence explain what you found. If GM VACANCY or GM TRANSITION, this is high-priority intelligence.

### 12. Recommended Approach
Write 3-4 sentences covering: the single strongest hook for outreach, the recommended first contact method, any landmines to avoid (bad reviews they're sensitive about, competitor they hate), and assign one of these opportunity ratings:

OPPORTUNITY: HIGH
OPPORTUNITY: HIGH - MULTI-UNIT
OPPORTUNITY: MEDIUM
OPPORTUNITY: LOW"""


@router.post("/dossier/{prospect_id}")
async def proof_dossier(
    prospect_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """
    Generate a full AI dossier including Leadership Signal (GM vacancy detection).

    Pricing:
      - Pro plan: $1.00
      - Free tier: $10.00
      - Cached: always free
    """
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]
    _, dossier_cost = get_costs(current_user)

    # Check balance
    user = supabase.table("proof_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()

    balance = float(user.data.get("credit_balance", 0))
    if balance < dossier_cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. Balance: ${balance:.2f}, dossier cost: ${dossier_cost:.2f}"
        )

    # Check cache
    cached = supabase.table("proof_dossier_cache") \
        .select("*") \
        .eq("prospect_id", prospect_id) \
        .execute()

    if cached.data:
        return {
            "success": True,
            "dossier": cached.data[0]["dossier_text"],
            "cached": True,
            "charged": 0,
            "balance_remaining": balance
        }

    # Fetch prospect
    prospect = supabase.table("prospect_master") \
        .select("dba_name, legal_name, premise_address1, premise_city, premise_state, premise_zip, business_category, raw_license_type") \
        .eq("id", prospect_id) \
        .single() \
        .execute()

    if not prospect.data:
        raise HTTPException(status_code=404, detail="Prospect not found")

    p = prospect.data
    business_name = p.get("dba_name") or p.get("legal_name", "Unknown")
    city  = p.get("premise_city", "")
    state = p.get("premise_state", "")

    user_prompt = (
        f"Generate a complete dossier for: {business_name}, "
        f"located at {p.get('premise_address1', '')}, {city}, {state} {p.get('premise_zip', '')}. "
        f"Category: {p.get('business_category', '')}. "
        f"License type: {p.get('raw_license_type', '')}."
    )

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
                    "messages": [{"role": "user", "content": user_prompt}],
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}]
                },
                timeout=120.0
            )
            resp_data = resp.json()

        if resp.status_code != 200:
            logger.error(f"Anthropic API error: {resp_data}")
            raise HTTPException(status_code=500, detail="Dossier generation failed")

        # Extract text from response (may contain tool_use blocks)
        dossier_text = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                dossier_text += block.get("text", "")

        if not dossier_text:
            raise HTTPException(status_code=500, detail="Dossier generation returned empty response")

        # Cache dossier
        supabase.table("proof_dossier_cache").insert({
            "prospect_id": prospect_id,
            "dossier_text": dossier_text,
            "generated_by": user_id,
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        # Deduct credit
        new_balance = balance - dossier_cost
        supabase.table("proof_users").update({
            "credit_balance": new_balance
        }).eq("id", user_id).execute()

        supabase.table("proof_credit_transactions").insert({
            "user_id": user_id,
            "transaction_type": "dossier",
            "amount": -dossier_cost,
            "balance_after": new_balance,
            "description": f"Dossier: {business_name}",
            "prospect_id": prospect_id,
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        return {
            "success": True,
            "dossier": dossier_text,
            "cached": False,
            "charged": dossier_cost,
            "balance_remaining": new_balance
        }

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Dossier generation timed out. Please try again.")
    except Exception as e:
        logger.error(f"Dossier error for {prospect_id}: {e}")
        raise HTTPException(status_code=500, detail="Dossier generation failed")


# ═══════════════════════════════════════════════════════════════════════════════
# OUTREACH DRAFTING
# ═══════════════════════════════════════════════════════════════════════════════

OUTREACH_SYSTEM_PROMPT = """You are an expert B2B sales copywriter specializing in food and beverage industry outreach. 
Given a restaurant intelligence dossier, write personalized outreach that a sales rep can send or use as a call script.

The outreach should:
- Open with something specific from the dossier — not generic
- Reference a real signal (new license, GM vacancy, pain point, multi-unit opportunity)
- Be concise — emails under 150 words, call scripts under 90 seconds
- Sound human, not AI-generated
- Never mention that you used a database or AI tool
- End with a single clear call to action

Return in this exact format with no preamble:

SUBJECT: [email subject line]

EMAIL:
[email body — under 150 words]

CALL SCRIPT:
[what to say on the phone — under 90 seconds when read aloud, conversational tone]"""


@router.post("/outreach")
async def proof_outreach(
    data: ProofOutreachRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """
    Generate personalized outreach from a dossier.
    Free with any dossier — no additional credit charge.
    """
    if not data.dossier_text or len(data.dossier_text) < 100:
        raise HTTPException(status_code=400, detail="Dossier text too short to generate outreach")

    # Get prospect name for context
    supabase = get_supabase()
    prospect = supabase.table("prospect_master") \
        .select("dba_name, legal_name, premise_city, premise_state") \
        .eq("id", data.prospect_id) \
        .single() \
        .execute()

    business_name = "this restaurant"
    if prospect.data:
        business_name = prospect.data.get("dba_name") or prospect.data.get("legal_name", "this restaurant")

    user_prompt = (
        f"Write outreach for a sales rep approaching {business_name}. "
        f"Use this intelligence dossier to personalize:\n\n{data.dossier_text[:3000]}"
    )

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
                    "max_tokens": 1000,
                    "system": OUTREACH_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_prompt}]
                },
                timeout=30.0
            )
            resp_data = resp.json()

        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail="Outreach generation failed")

        outreach_text = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                outreach_text += block.get("text", "")

        # Parse into structured response
        subject, email_body, call_script = "", "", ""
        if "SUBJECT:" in outreach_text:
            lines = outreach_text.split("\n")
            section = None
            for line in lines:
                if line.startswith("SUBJECT:"):
                    subject = line.replace("SUBJECT:", "").strip()
                elif line.strip() == "EMAIL:":
                    section = "email"
                elif line.strip() == "CALL SCRIPT:":
                    section = "call"
                elif section == "email":
                    email_body += line + "\n"
                elif section == "call":
                    call_script += line + "\n"

        return {
            "success": True,
            "subject": subject.strip(),
            "email": email_body.strip(),
            "call_script": call_script.strip(),
            "raw": outreach_text
        }

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Outreach generation timed out")
    except Exception as e:
        logger.error(f"Outreach error: {e}")
        raise HTTPException(status_code=500, detail="Outreach generation failed")


# ═══════════════════════════════════════════════════════════════════════════════
# CREDITS & CHECKOUT
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/checkout/credits")
async def proof_buy_credits(
    data: ProofCreditsRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Create a Stripe checkout session for credit top-up. Available to all plans."""
    if data.pack not in CREDIT_PACK_AMOUNTS:
        raise HTTPException(status_code=400, detail="Invalid credit pack")

    price_id = PROOF_PRICE_IDS.get(data.pack)
    if not price_id:
        raise HTTPException(
            status_code=500,
            detail=f"Credit pack '{data.pack}' price not configured. Contact support."
        )

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
        .limit(100) \
        .execute()

    return {"success": True, "transactions": result.data}


# ═══════════════════════════════════════════════════════════════════════════════
# ORGANIZATION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/org/members")
async def proof_org_members(
    current_user: dict = Depends(verify_proof_token)
):
    """Get all members of the current user's organization. Admin only."""
    if not current_user.get("is_org_admin"):
        raise HTTPException(status_code=403, detail="Organization admin access required")

    org_id = current_user.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="You are not part of an organization")

    supabase = get_supabase()

    members = supabase.table("proof_users") \
        .select("id, email, full_name, plan, plan_status, credit_balance, last_login, created_at, is_org_admin") \
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
    current_user: dict = Depends(verify_proof_token)
):
    """Invite a user to the organization. Admin only."""
    if not current_user.get("is_org_admin"):
        raise HTTPException(status_code=403, detail="Organization admin access required")

    org_id = current_user.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="You are not part of an organization")

    supabase = get_supabase()

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

    existing = supabase.table("proof_users") \
        .select("id, organization_id") \
        .eq("email", data.email.lower()) \
        .execute()

    if existing.data:
        user = existing.data[0]
        if user.get("organization_id"):
            raise HTTPException(status_code=409, detail="This user is already part of an organization")

        supabase.table("proof_users").update({
            "organization_id": org_id,
            "plan": org.data["plan"],
            "plan_status": "active"
        }).eq("id", user["id"]).execute()

        return {"success": True, "message": f"{data.email} added to your organization", "new_user": False}

    temp_password = secrets.token_urlsafe(16)

    supabase.table("proof_users").insert({
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

    # TODO: Send welcome email via SendGrid

    return {
        "success": True,
        "message": f"Account created for {data.email}.",
        "new_user": True,
        "temp_password": temp_password
    }


# ═══════════════════════════════════════════════════════════════════════════════
# WEEKLY DIGEST
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/digest/subscribe")
async def proof_digest_subscribe(
    data: ProofDigestRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Subscribe to weekly new license digest. Paid plans only."""
    if current_user.get("plan", "free") == "free":
        raise HTTPException(status_code=403, detail="Weekly digest requires a paid plan")

    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

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
        "message": f"Weekly alerts activated for: {', '.join(data.states)}"
    }


@router.get("/digest/subscription")
async def proof_digest_get(
    current_user: dict = Depends(verify_proof_token)
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
# STRIPE WEBHOOK
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
        event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)

    event_type = event.type
    data       = event.data.object
    logger.info(f"Proof Stripe webhook: {event_type}")

    if event_type == "checkout.session.completed":
        await handle_proof_checkout(data)
    elif event_type == "customer.subscription.deleted":
        await handle_proof_subscription_cancelled(data)
    elif event_type == "invoice.payment_failed":
        await handle_proof_payment_failed(data)

    return {"received": True}


async def handle_proof_checkout(session):
    supabase = get_supabase()
    meta = session.metadata or {}
    user_id = meta.get("proof_user_id")

    if not user_id:
        logger.error("Proof checkout with no proof_user_id in metadata")
        return

    try:
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

        elif meta.get("proof_plan"):
            plan = meta["proof_plan"]
            seat_limit = PLAN_SEAT_LIMITS.get(plan)

            if plan in ("team", "company"):
                user = supabase.table("proof_users") \
                    .select("full_name, company, organization_id") \
                    .eq("id", user_id) \
                    .single() \
                    .execute()

                if not user.data.get("organization_id"):
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
    supabase = get_supabase()
    try:
        supabase.table("proof_users").update({
            "plan_status": "past_due"
        }).eq("stripe_subscription_id", invoice.subscription).execute()
        logger.warning(f"Proof payment failed: {invoice.subscription}")
    except Exception as e:
        logger.error(f"Error handling proof payment failure: {e}")