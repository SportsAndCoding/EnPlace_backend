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
from json_repair import repair_json
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Request, Header, Depends, BackgroundTasks
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
    "starter":     os.environ.get("STRIPE_PRICE_PROOF_STARTER"),
    "individual":  os.environ.get("STRIPE_PRICE_PROOF_INDIVIDUAL"),  # legacy alias for starter
    "growth":      os.environ.get("STRIPE_PRICE_PROOF_GROWTH"),
    "team":        os.environ.get("STRIPE_PRICE_PROOF_TEAM"),
    "company":     os.environ.get("STRIPE_PRICE_PROOF_COMPANY"),  # legacy
    "credits_10":  os.environ.get("STRIPE_PRICE_PROOF_CREDITS_10"),   # free tier entry
    "credits_25":  os.environ.get("STRIPE_PRICE_PROOF_CREDITS_25"),
    "credits_50":  os.environ.get("STRIPE_PRICE_PROOF_CREDITS_50"),
    "credits_100": os.environ.get("STRIPE_PRICE_PROOF_CREDITS_100"),
}

PLAN_SEAT_LIMITS = {
    "free":       1,
    "starter":    1,
    "individual": 1,
    "growth":     1,
    "team":       10,
    "company":    25,
    "partner":    1,
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
# ── Tiered pricing by plan ──
PLAN_PRICING = {
    'free':       {'enrichment': 0.50, 'dossier': 15.00, 'scan': 0.50, 'route': 0},
    'starter':    {'enrichment': 0.15, 'dossier':  7.00, 'scan': 0.30, 'route': 1.00},
    'individual': {'enrichment': 0.15, 'dossier':  7.00, 'scan': 0.30, 'route': 1.00},
    'growth':     {'enrichment': 0.10, 'dossier':  5.00, 'scan': 0.25, 'route': 0.75},
    'team':       {'enrichment': 0.08, 'dossier':  3.00, 'scan': 0.15, 'route': 0.50},
    'company':    {'enrichment': 0.08, 'dossier':  3.00, 'scan': 0.15, 'route': 0.50},
    'partner':    {'enrichment': 0.10, 'dossier':  5.00, 'scan': 0.25, 'route': 0.75},
}

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
        stale_days: Optional[int] = None
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

class ProofSaveSearchRequest(BaseModel):
    name: str
    filters: dict

class ProofMapHeadersRequest(BaseModel):
    headers: List[str]


class ProofImportRecordsRequest(BaseModel):
    mapping: dict
    records: List[dict]

class ProofChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class ProofScanEstimateRequest(BaseModel):
    states: Optional[List[str]] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    county: Optional[str] = None
    categories: Optional[List[str]] = None

class ProofDocketRequest(BaseModel):
    call_count: int = 10

class RouteStop(BaseModel):
    contact_id: str
    earliest: Optional[str] = None    # "09:00" format
    latest: Optional[str] = None      # "17:00" format
    duration_minutes: int = 15        # time spent at this stop

class PulseRouteRequest(BaseModel):
    stops: List[RouteStop]
    start_address: Optional[str] = None  # rep's starting point
    departure_time: str = "08:00"
    return_to_start: bool = False

class DocketOverrideRequest(BaseModel):
    docket_id: str
    contact_id: str
    method: str  # 'call', 'email', 'visit'

class ContactRuleRequest(BaseModel):
    contact_id: str
    rule_text: str
    day_of_week: Optional[List[str]] = None
    time_earliest: Optional[str] = None
    time_latest: Optional[str] = None

class DealCreateRequest(BaseModel):
    contact_id: str
    title: str
    value: float = 0
    recurring: bool = False
    recurring_frequency: Optional[str] = None  # 'weekly', 'monthly', 'quarterly', 'annual'
    stage: str = "discovery"
    notes: Optional[str] = None

class DealUpdateRequest(BaseModel):
    title: Optional[str] = None
    value: Optional[float] = None
    recurring: Optional[bool] = None
    recurring_frequency: Optional[str] = None
    stage: Optional[str] = None
    notes: Optional[str] = None

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
    """Return (enrichment_cost, dossier_cost) based on user plan tier."""
    plan = current_user.get("plan", "free")
    tier = PLAN_PRICING.get(plan, PLAN_PRICING['free'])
    return (tier['enrichment'], tier['dossier'])

@router.post("/pulse/route")
async def pulse_route_plan(
    data: PulseRouteRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Optimize a route with time window constraints using OR-Tools VRPTW."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]
    plan = current_user.get("plan", "free")

    if plan == "free":
        raise HTTPException(status_code=403, detail="Route Planner requires a Starter plan or above.")

    # Check route allocation
    covered, billable = check_allocation(supabase, user_id, plan, 'routes')
    if billable > 0:
        route_cost = PLAN_PRICING.get(plan, PLAN_PRICING['free']).get('route', 1.00)
        balance = float(current_user.get("credit_balance", 0))
        if balance < route_cost:
            alloc = PLAN_ALLOCATIONS.get(plan, PLAN_ALLOCATIONS['free'])
            used = get_monthly_usage(supabase, user_id, 'routes')
            raise HTTPException(
                status_code=403,
                detail=f"Route limit reached ({used}/{alloc.get('routes', 0)} this month). Add credits or upgrade your plan."
            )
        # Deduct credit
        new_balance = balance - route_cost
        supabase.table("proof_users").update({"credit_balance": new_balance}).eq("id", user_id).execute()
        supabase.table("proof_credit_transactions").insert({
            "user_id": user_id,
            "transaction_type": "route",
            "amount": -route_cost,
            "balance_after": new_balance,
            "description": f"Route optimization ({len(data.stops)} stops)",
            "created_at": datetime.utcnow().isoformat()
        }).execute()

    if len(data.stops) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 stops to plan a route.")
    if len(data.stops) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 stops per route.")

    # Fetch contact addresses
    contact_ids = [s.contact_id for s in data.stops]
    contacts_result = supabase.table("proof_contacts") \
        .select("id, business_name, address, city, state, zip, phone") \
        .eq("user_id", user_id) \
        .in_("id", contact_ids) \
        .execute()

    contacts_map = {c["id"]: c for c in (contacts_result.data or [])}

    # Build address list for Distance Matrix
    addresses = []
    stop_data = []
    has_start = bool(data.start_address and data.start_address.strip())

    if has_start:
        addresses.append(data.start_address.strip())
        stop_data.append({
            "contact_id": None,
            "business_name": "Start",
            "address_raw": data.start_address.strip(),
            "phone": None,
            "earliest": None,
            "latest": None,
            "duration_minutes": 0
        })

    # Load existing rules for selected contacts
    rules_result = supabase.table("proof_contact_rules") \
        .select("contact_id, day_of_week, time_earliest, time_latest, rule_text") \
        .eq("user_id", user_id) \
        .eq("active", True) \
        .in_("contact_id", contact_ids) \
        .execute()
    rules_by_contact = {}
    for rule in (rules_result.data or []):
        rules_by_contact[rule["contact_id"]] = rule

    missing = []
    for stop in data.stops:
        contact = contacts_map.get(stop.contact_id)
        if not contact:
            missing.append(stop.contact_id)
            continue
        addr_parts = [contact.get("address"), contact.get("city"), contact.get("state")]
        addr = ", ".join([p for p in addr_parts if p])
        if contact.get("zip"):
            addr += " " + contact["zip"]
        if not addr.strip():
            missing.append(stop.contact_id)
            continue
        addresses.append(addr)
        # Merge user-provided constraints with saved rules
        earliest = stop.earliest
        latest = stop.latest
        rule = rules_by_contact.get(stop.contact_id)
        if rule and not earliest:
            earliest = rule.get("time_earliest")
        if rule and not latest:
            latest = rule.get("time_latest")

        stop_data.append({
            "contact_id": stop.contact_id,
            "business_name": contact.get("business_name", "Unknown"),
            "address_raw": addr,
            "phone": contact.get("phone"),
            "earliest": earliest,
            "latest": latest,
            "duration_minutes": stop.duration_minutes,
            "rule_text": rule.get("rule_text") if rule else None
        })

    if missing:
        raise HTTPException(status_code=400, detail=f"Missing address for {len(missing)} contact(s). Enrich them first.")

    if len(addresses) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 valid addresses.")

    n = len(addresses)

    # Get distance matrix from Google
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/distancematrix/json",
                params={
                    "origins": "|".join(addresses),
                    "destinations": "|".join(addresses),
                    "key": GOOGLE_PLACES_API_KEY,
                    "units": "imperial"
                },
                timeout=30.0
            )
            matrix_data = resp.json()
    except Exception as e:
        logger.error(f"Distance Matrix API error: {e}")
        raise HTTPException(status_code=500, detail="Failed to calculate distances. Try again.")

    if matrix_data.get("status") != "OK":
        raise HTTPException(status_code=500, detail=f"Google API error: {matrix_data.get('status')}")

    # Parse distance matrix into duration (seconds) and distance (meters)
    duration_matrix = []
    distance_matrix = []
    for i, row in enumerate(matrix_data["rows"]):
        dur_row = []
        dist_row = []
        for j, elem in enumerate(row["elements"]):
            if elem["status"] == "OK":
                dur_row.append(elem["duration"]["value"])      # seconds
                dist_row.append(elem["distance"]["value"])     # meters
            else:
                dur_row.append(999999)
                dist_row.append(999999)
        duration_matrix.append(dur_row)
        distance_matrix.append(dist_row)

    # Parse departure time
    dep_parts = data.departure_time.split(":")
    dep_minutes = int(dep_parts[0]) * 60 + int(dep_parts[1])

    # Build time windows (in seconds from midnight)
    time_windows = []
    for sd in stop_data:
        tw_start = 0
        tw_end = 86400
        if sd["earliest"]:
            e_parts = sd["earliest"].split(":")
            tw_start = int(e_parts[0]) * 3600 + int(e_parts[1]) * 60
        if sd["latest"]:
            l_parts = sd["latest"].split(":")
            tw_end = int(l_parts[0]) * 3600 + int(l_parts[1]) * 60
        time_windows.append((tw_start, tw_end))

    # Service times (duration at each stop, in seconds)
    service_times = [sd["duration_minutes"] * 60 for sd in stop_data]

    # Solve with OR-Tools
    try:
        from ortools.constraint_solver import routing_enums_pb2, pywrapcp

        manager = pywrapcp.RoutingIndexManager(n, 1, 0 if has_start else 0)
        routing = pywrapcp.RoutingModel(manager)

        def time_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            travel = duration_matrix[from_node][to_node]
            service = service_times[from_node]
            return travel + service

        transit_callback_index = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        # Time dimension with time windows
        routing.AddDimension(
            transit_callback_index,
            7200,   # max wait time (2 hours)
            86400,  # max total time (24 hours)
            False,
            "Time"
        )
        time_dimension = routing.GetDimensionOrDie("Time")

        # Apply time windows
        for i in range(n):
            index = manager.NodeToIndex(i)
            time_dimension.CumulVar(index).SetRange(
                time_windows[i][0],
                time_windows[i][1]
            )

        # Set departure time for start node
        dep_seconds = dep_minutes * 60
        start_index = routing.Start(0)
        time_dimension.CumulVar(start_index).SetRange(dep_seconds, dep_seconds)

        # Don't force return to start unless requested
        if not data.return_to_start:
            routing.SetFixedCostOfVehicle(0, 0)

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        search_params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        search_params.time_limit.seconds = 5

        solution = routing.SolveWithParameters(search_params)

    except Exception as e:
        logger.error(f"OR-Tools error: {e}")
        raise HTTPException(status_code=500, detail="Route optimization failed.")

    if not solution:
        raise HTTPException(status_code=400, detail="No feasible route found with these time constraints. Try relaxing your time windows.")

    # Extract solution
    route_order = []
    total_drive_seconds = 0
    total_distance_meters = 0
    index = routing.Start(0)
    prev_node = None

    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        time_var = time_dimension.CumulVar(index)
        arrival_seconds = solution.Min(time_var)
        arrival_h = arrival_seconds // 3600
        arrival_m = (arrival_seconds % 3600) // 60
        arrival_time = f"{arrival_h:02d}:{arrival_m:02d}"

        leg_drive = 0
        leg_distance = 0
        if prev_node is not None:
            leg_drive = duration_matrix[prev_node][node]
            leg_distance = distance_matrix[prev_node][node]
            total_drive_seconds += leg_drive
            total_distance_meters += leg_distance

        stop_info = stop_data[node]
        route_order.append({
            "stop_number": len(route_order) + 1,
            "contact_id": stop_info["contact_id"],
            "business_name": stop_info["business_name"],
            "address": stop_info["address_raw"],
            "phone": stop_info["phone"],
            "arrival_time": arrival_time,
            "departure_time": f"{(arrival_seconds + service_times[node]) // 3600:02d}:{((arrival_seconds + service_times[node]) % 3600) // 60:02d}",
            "duration_minutes": stop_info["duration_minutes"],
            "drive_minutes_from_prev": round(leg_drive / 60, 1) if prev_node is not None else 0,
            "drive_miles_from_prev": round(leg_distance / 1609.34, 1) if prev_node is not None else 0,
            "time_window": {
                "earliest": stop_info["earliest"],
                "latest": stop_info["latest"]
            } if stop_info["earliest"] else None
        })

        prev_node = node
        index = solution.Value(routing.NextVar(index))

    # Build Google Maps URL
    waypoints = [s["address"] for s in route_order if s["contact_id"]]  # skip start
    if len(waypoints) >= 2:
        origin = waypoints[0]
        destination = waypoints[-1]
        mid = waypoints[1:-1]
        maps_url = f"https://www.google.com/maps/dir/{'/'.join([origin] + mid + [destination])}"
    else:
        maps_url = None

    return {
        "success": True,
        "route": route_order,
        "summary": {
            "total_stops": len(route_order),
            "total_drive_minutes": round(total_drive_seconds / 60, 1),
            "total_drive_miles": round(total_distance_meters / 1609.34, 1),
            "total_time_minutes": round((total_drive_seconds + sum(service_times[manager.IndexToNode(i)] for i in range(n))) / 60, 1),
            "departure_time": data.departure_time,
        },
        "maps_url": maps_url,
        "cost_note": f"Distance Matrix: {n*n} elements (~${n*n*0.005:.2f})"
    }


def get_scan_cost(current_user: dict) -> float:
    """Return per-restaurant scan cost based on user plan tier."""
    plan = current_user.get("plan", "free")
    tier = PLAN_PRICING.get(plan, PLAN_PRICING['free'])
    return tier['scan']


# ── Monthly plan allocations ──
PLAN_ALLOCATIONS = {
    'free':       {'enrichments': 5,   'dossiers': 0,  'scan_restaurants': 0,   'docket': 0,  'routes': 0},
    'starter':    {'enrichments': 50,  'dossiers': 5,  'scan_restaurants': 100,  'docket': 10, 'routes': 5},
    'individual': {'enrichments': 50,  'dossiers': 5,  'scan_restaurants': 100,  'docket': 10, 'routes': 5},
    'growth':     {'enrichments': 200, 'dossiers': 15, 'scan_restaurants': 500,  'docket': -1, 'routes': 15},
    'team':       {'enrichments': 500, 'dossiers': 40, 'scan_restaurants': 1500, 'docket': -1, 'routes': 30},
    'company':    {'enrichments': 500, 'dossiers': 40, 'scan_restaurants': 1500, 'docket': -1, 'routes': 30},
    'partner':    {'enrichments': 500, 'dossiers': 50, 'scan_restaurants': 2000, 'docket': -1, 'routes': 15},
}

def get_monthly_usage(supabase, user_id: str, feature: str) -> int:
    """Count how many times a user has used a feature this calendar month."""
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    if feature == 'scan_restaurants':
        result = supabase.table("proof_gm_scans") \
            .select("scanned_count") \
            .eq("user_id", user_id) \
            .gte("created_at", month_start) \
            .execute()
        return sum(r.get("scanned_count", 0) for r in (result.data or []))
    elif feature == 'docket':
        result = supabase.table("proof_dockets") \
            .select("id") \
            .eq("user_id", user_id) \
            .gte("created_at", month_start) \
            .execute()
        return len(result.data or [])
    elif feature == 'routes':
        result = supabase.table("proof_credit_transactions") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("transaction_type", "route") \
            .gte("created_at", month_start) \
            .execute()
        return len(result.data or [])
    else:
        type_map = {'enrichments': 'enrichment', 'dossiers': 'dossier'}
        tx_type = type_map.get(feature, feature)
        result = supabase.table("proof_credit_transactions") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("transaction_type", tx_type) \
            .lt("amount", 0) \
            .gte("created_at", month_start) \
            .execute()
        return len(result.data or [])

def check_allocation(supabase, user_id: str, plan: str, feature: str, units: int = 1) -> tuple:
    """Check if usage is within plan allocation. Returns (units_covered, units_billable)."""
    alloc = PLAN_ALLOCATIONS.get(plan, PLAN_ALLOCATIONS['free'])
    limit = alloc.get(feature, 0)
    if limit == -1:
        return (units, 0)
    used = get_monthly_usage(supabase, user_id, feature)
    remaining = max(0, limit - used)
    covered = min(units, remaining)
    billable = units - covered
    return (covered, billable)

# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVITY LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

def log_activity(supabase, user_id: str, activity_type: str, contact_id: str = None,
                 deal_id: str = None, metadata: dict = None, org_id: str = None):
    """Log a rep activity for admin tracking."""
    try:
        supabase.table("proof_activity_log").insert({
            "user_id": user_id,
            "organization_id": org_id,
            "activity_type": activity_type,
            "contact_id": contact_id,
            "deal_id": deal_id,
            "metadata": metadata,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        logger.warning(f"Activity log failed: {e}")

def check_spending_limit(supabase, user_id: str, monthly_limit, cost: float) -> bool:
    """Check if a credit deduction would exceed the user's monthly spending limit."""
    if monthly_limit is None:
        return True  # No limit set
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    result = supabase.table("proof_credit_transactions") \
        .select("amount") \
        .eq("user_id", user_id) \
        .lt("amount", 0) \
        .gte("created_at", month_start) \
        .execute()
    spent = abs(sum(float(c["amount"]) for c in (result.data or [])))
    return (spent + cost) <= float(monthly_limit)

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


_anthropic_status = {"ok": True, "checked_at": None}

@router.get("/health/ai")
async def proof_ai_health():
    """Check if Anthropic API is reachable. Cached for 60 seconds."""
    now = datetime.utcnow()
    if _anthropic_status["checked_at"] and (now - _anthropic_status["checked_at"]).total_seconds() < 60:
        return {"available": _anthropic_status["ok"]}
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
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}]
                },
                timeout=10.0
            )
            _anthropic_status["ok"] = resp.status_code == 200
    except Exception:
        _anthropic_status["ok"] = False
    _anthropic_status["checked_at"] = now
    return {"available": _anthropic_status["ok"]}


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

_anthropic_status = {"ok": True, "checked_at": None}

@router.get("/health/ai")
async def proof_ai_health():
    """Check if Anthropic API is reachable. Cached for 60 seconds."""
    now = datetime.utcnow()
    if _anthropic_status["checked_at"] and (now - _anthropic_status["checked_at"]).total_seconds() < 60:
        return {"available": _anthropic_status["ok"]}
    
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
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}]
                },
                timeout=10.0
            )
            _anthropic_status["ok"] = resp.status_code == 200
    except Exception:
        _anthropic_status["ok"] = False
    
    _anthropic_status["checked_at"] = now
    return {"available": _anthropic_status["ok"]}


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/search")
async def proof_search(
    data: ProofSearchRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """
    Search prospect_master via grouped RPC.
    Deduplicates by establishment (address + city + state).
    Returns one row per establishment with license_count and license_types.
    """
    supabase = get_supabase()
    plan = current_user.get("plan", "free")
    is_paid = plan != "free"

    if not is_paid and (data.new_since_days or data.expiring_within_days or data.stale_days):
        raise HTTPException(
            status_code=403,
            detail="New issuance, expiry, and freshness filters require a paid plan"
        )

    # Enforce territory restrictions
    territory = current_user.get("territory_states")
    search_states = data.states if data.states else None
    if territory:
        if search_states:
            search_states = [s for s in search_states if s.upper() in territory]
            if not search_states:
                raise HTTPException(status_code=403, detail="Those states are outside your assigned territory.")
        else:
            search_states = territory

    params = {
        "p_states": search_states,
        "p_city": data.city if data.city else None,
        "p_zip": data.zip_code if data.zip_code else None,
        "p_county": data.county if data.county else None,
        "p_address": data.address if data.address else None,
        "p_categories": data.categories if data.categories else None,
        "p_new_since_days": data.new_since_days if is_paid else None,
        "p_expiring_within_days": data.expiring_within_days if is_paid else None,
        "p_stale_days": data.stale_days if is_paid else None,
        "p_page": data.page,
        "p_page_size": data.page_size
    }

    result = supabase.rpc("proof_search_grouped", params).execute()
    results = result.data if isinstance(result.data, list) else (result.data or [])

    # Log search (non-blocking)
    try:
        supabase.table("proof_search_log").insert({
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
            "result_count": len(results)
        }).execute()
    except Exception:
        pass

    return {
        "success": True,
        "results": results,
        "page": data.page,
        "page_size": data.page_size,
        "plan": plan
    }

@router.post("/searches/save")
async def proof_save_search(
    data: ProofSaveSearchRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Save a search configuration."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    count = supabase.table("proof_saved_searches") \
        .select("id") \
        .eq("user_id", user_id) \
        .execute()

    if len(count.data) >= 20:
        raise HTTPException(status_code=400, detail="Maximum 20 saved searches. Delete one to save a new one.")

    result = supabase.table("proof_saved_searches").insert({
        "user_id": user_id,
        "name": data.name[:60],
        "filters": data.filters,
    }).execute()

    return {"success": True, "search": result.data[0]}


@router.get("/searches/saved")
async def proof_get_saved_searches(
    current_user: dict = Depends(verify_proof_token)
):
    """Get all saved searches for the current user."""
    supabase = get_supabase()
    result = supabase.table("proof_saved_searches") \
        .select("*") \
        .eq("user_id", current_user["proof_user_id"]) \
        .order("created_at", desc=True) \
        .execute()
    return {"success": True, "searches": result.data}


@router.delete("/searches/{search_id}")
async def proof_delete_saved_search(
    search_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Delete a saved search."""
    supabase = get_supabase()
    supabase.table("proof_saved_searches") \
        .delete() \
        .eq("id", search_id) \
        .eq("user_id", current_user["proof_user_id"]) \
        .execute()
    return {"success": True}

@router.get("/prospect/{prospect_id}")
async def proof_get_prospect(
    prospect_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Get a single prospect record."""
    supabase = get_supabase()
    result = supabase.table("prospect_master") \
        .select(
            "id, legal_name, dba_name, business_category, raw_license_type, "
            "license_status, premise_address1, premise_city, premise_state, "
            "premise_zip, premise_county, license_issue_date, license_expiry_date, "
            "first_seen_at, latitude, longitude"
        ) \
        .eq("id", prospect_id) \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Prospect not found")

    return {"success": True, "prospect": result.data[0]}


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
    plan = current_user.get("plan", "free")
    covered, billable = check_allocation(supabase, user_id, plan, 'enrichments')
    effective_cost = enrichment_cost if billable > 0 else 0

    # Check balance
    user = supabase.table("proof_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()

    balance = float(user.data.get("credit_balance", 0))
    if effective_cost > 0 and balance < effective_cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. Balance: ${balance:.2f}, cost: ${effective_cost:.2f}"
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
            new_balance = balance - effective_cost
            supabase.table("proof_users").update({
                "credit_balance": new_balance
            }).eq("id", user_id).execute()

            supabase.table("proof_credit_transactions").insert({
                "user_id": user_id,
                "transaction_type": "enrichment",
                "amount": -effective_cost,
                "balance_after": new_balance,
                "description": f"Enrichment: {business_name}",
                "prospect_id": prospect_id,
                "created_at": datetime.utcnow().isoformat()
            }).execute()

            return {
                "success": True,
                "enrichment": enrichment,
                "cached": False,
                "charged": effective_cost,
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

DOSSIER_SYSTEM_PROMPT = """You are a restaurant industry intelligence researcher. When given a restaurant name and location, conduct exhaustive research using web search. Check Google, Yelp, Facebook, Instagram, TripAdvisor, LinkedIn, state business registrations, Indeed, Google Jobs, and local news.
 
Return ONLY a valid JSON object. No markdown, no code fences, no preamble. Just the raw JSON.
 
Reputation scores are your best estimates on a 1-10 scale synthesized from review data, sentiment analysis, and complaint frequency. Be honest. A 4.2 wait time score for a place with "long wait" complaints is correct. Do not inflate.
 
Use this exact schema. Every top-level key is required. Use null for unknown values, empty arrays for no items.
 
{
  "basic_info": {
    "legal_name": "string — full legal business name from state registry",
    "dba": "string — DBA / trade name",
    "cuisine": "string — e.g. American Steakhouse, Mexican, Italian",
    "price_point": "string — $, $$, $$$, or $$$$",
    "rating": "number or null — Google rating",
    "review_count": "number or null — Google review count",
    "avg_check": "string — e.g. $85 to 110/pp",
    "year_established": "number or null",
    "phone": "string or null",
    "website": "string or null — full URL",
    "website_quality": "number 1-5 — 1=none, 2=Facebook only, 3=poor/broken, 4=outdated, 5=solid",
    "hours": "string or null — condensed format",
    "seating_capacity": "number or null — estimate",
    "alcohol_license": "string or null"
  },
  "ownership": {
    "name": "string or null — primary owner/operator name",
    "title": "string or null — e.g. Managing partner, Owner, CEO",
    "background": "string or null — 1-2 sentences: career background, entity info",
    "linkedin_url": "string or null — direct profile URL if found",
    "entity_name": "string or null — LLC/corp name from state registry",
    "structure": "string — owner-operated, absentee, management-company, investor-group",
    "other_locations": [
      {"name": "string", "location": "string — city, state"}
    ]
  },
  "reputation_scores": {
    "food_quality": "number 1-10 — synthesized from review themes",
    "service": "number 1-10",
    "wait_times": "number 1-10 — higher = shorter waits (better)",
    "consistency": "number 1-10"
  },
  "pain_points": [
    "string — each a specific, actionable insight. 3-6 items."
  ],
  "recommended_approach": {
    "narrative": "string — 3-4 sentences: strongest hook, recommended contact method, landmines to avoid",
    "opportunity": "string — exactly one of: HIGH - MULTI-UNIT, HIGH, MEDIUM, LOW",
    "best_contact": "string — phone, email, walk-in, or social",
    "best_time": "string — e.g. Tuesday-Thursday, 2-4pm"
  },
  "leadership": {
    "verdict": "string — exactly one of: GM STABLE, GM VACANCY, GM TRANSITION",
    "detail": "string — one sentence explaining the evidence"
  },
  "hiring": {
    "total_openings": "number — 0 if none found",
    "departments": ["string — FOH, BOH, management"],
    "roles": ["string — specific role titles"],
    "assessment": "string — one sentence: growth, turnover, or no activity"
  },
  "online_presence": {
    "website_url": "string or null",
    "website_quality": "number 1-5",
    "google_claimed": "boolean or null",
    "google_rating": "number or null",
    "google_reviews": "number or null",
    "google_response_rate": "string or null — e.g. responds to 80% of reviews",
    "yelp_rating": "number or null",
    "yelp_reviews": "number or null",
    "facebook_followers": "number or null",
    "facebook_last_post": "string or null — date or relative",
    "instagram_handle": "string or null",
    "instagram_followers": "number or null",
    "ordering_platforms": ["string — DoorDash, UberEats, ChowNow, etc."],
    "reservation_system": "string or null — OpenTable, Resy, etc."
  },
  "competitive_landscape": "string — 2-3 sentences on direct competitors and differentiation",
  "menu_highlights": "string — 2-3 sentences on signature items, menu format, special services",
  "account_intel": {
    "est_revenue": "string — e.g. $2-3M annually",
    "est_employees": "number or null",
    "owner_email": "string or null — only if publicly available"
  }
}"""


@router.post("/dossier/{prospect_id}")
async def proof_dossier(
    prospect_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_proof_token)
):
    """
    Start dossier generation as a background task.
    Returns immediately. Frontend polls GET /dossier/{id}/status.
    """
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]
    plan = current_user.get("plan", "free")
    covered, billable = check_allocation(supabase, user_id, plan, 'dossiers')
    effective_cost = dossier_cost if billable > 0 else 0
    plan = current_user.get("plan", "free")
    covered, billable = check_allocation(supabase, user_id, plan, 'dossiers')
    effective_cost = dossier_cost if billable > 0 else 0

    # Check balance
    user = supabase.table("proof_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()

    balance = float(user.data.get("credit_balance", 0))
    if effective_cost > 0 and balance < effective_cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. Balance: ${balance:.2f}, dossier cost: ${effective_cost:.2f}"
        )

    # Check cache — return immediately if cached
    cached = supabase.table("proof_dossier_cache") \
        .select("*") \
        .eq("prospect_id", prospect_id) \
        .execute()

    if cached.data:
        cached_text = cached.data[0]["dossier_text"]
        try:
            dossier_data = json.loads(cached_text)
        except (json.JSONDecodeError, ValueError):
            dossier_data = cached_text
        return {
            "success": True,
            "dossier": dossier_data,
            "cached": True,
            "charged": 0,
            "balance_remaining": balance
        }

    # Fetch prospect info for the background task
    prospect = supabase.table("prospect_master") \
        .select("dba_name, legal_name, premise_address1, premise_city, premise_state, premise_zip, business_category, raw_license_type") \
        .eq("id", prospect_id) \
        .single() \
        .execute()

    if not prospect.data:
        raise HTTPException(status_code=404, detail="Prospect not found")

    # Deduct credit NOW (before background task) so user can't double-spend
    new_balance = balance - effective_cost
    supabase.table("proof_users").update({
        "credit_balance": new_balance
    }).eq("id", user_id).execute()

    supabase.table("proof_credit_transactions").insert({
        "user_id": user_id,
        "transaction_type": "dossier",
        "amount": -effective_cost,
        "balance_after": new_balance,
        "description": f"Dossier: {prospect.data.get('dba_name') or prospect.data.get('legal_name', 'Unknown')}",
        "prospect_id": prospect_id,
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    # Fire background task and return immediately
    background_tasks.add_task(
        _generate_dossier_background,
        prospect_id, user_id, prospect.data
    )

    return {
        "success": True,
        "status": "generating",
        "cached": False,
        "charged": dossier_cost,
        "balance_remaining": new_balance
    }


async def _generate_dossier_background(prospect_id: str, user_id: str, prospect_data: dict):
    """Background task: call Anthropic, cache result."""
    p = prospect_data
    business_name = p.get("dba_name") or p.get("legal_name", "Unknown")
    city = p.get("premise_city", "")
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
                timeout=180.0
            )
            resp_data = resp.json()

        if resp.status_code != 200:
            logger.error(f"Anthropic API error for dossier {prospect_id}: {resp_data}")
            return

        dossier_text = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                dossier_text += block.get("text", "")

        if not dossier_text:
            logger.error(f"Empty dossier response for {prospect_id}")
            return

        cleaned_text = dossier_text.strip()
        if cleaned_text.startswith("```"):
            first_newline = cleaned_text.index("\n")
            cleaned_text = cleaned_text[first_newline + 1:]
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3].strip()

        # Extract JSON even if Claude prefixed it with conversational text
        first_brace = cleaned_text.find("{")
        last_brace = cleaned_text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            json_candidate = cleaned_text[first_brace:last_brace + 1]
            try:
                json.loads(json_candidate)
                cache_text = json_candidate
            except (json.JSONDecodeError, ValueError):
                try:
                    repaired = repair_json(json_candidate, return_objects=False)
                    json.loads(repaired)
                    cache_text = repaired
                    logger.info(f"Dossier for {prospect_id} repaired from malformed JSON")
                except Exception:
                    logger.warning(f"Dossier for {prospect_id} could not be repaired, storing as text")
                    cache_text = dossier_text
        else:
            logger.warning(f"Dossier for {prospect_id} had no JSON braces, storing as text")
            cache_text = dossier_text

        supabase = get_supabase()
        supabase.table("proof_dossier_cache").insert({
            "prospect_id": prospect_id,
            "dossier_text": cache_text,
            "generated_by": user_id,
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        logger.info(f"Dossier cached for {prospect_id}")

    except Exception as e:
        logger.error(f"Background dossier error for {prospect_id}: {e}")

@router.get("/dossier/{prospect_id}/status")
async def proof_dossier_status(
    prospect_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Poll for dossier completion. Returns cached result if ready."""
    supabase = get_supabase()
    cached = supabase.table("proof_dossier_cache") \
        .select("dossier_text") \
        .eq("prospect_id", prospect_id) \
        .execute()

    if cached.data:
        cached_text = cached.data[0]["dossier_text"]
        try:
            dossier_data = json.loads(cached_text)
        except (json.JSONDecodeError, ValueError):
            dossier_data = cached_text
        return {"status": "complete", "dossier": dossier_data}

    return {"status": "generating"}

@router.get("/dossiers/mine")
async def proof_my_dossiers(
    current_user: dict = Depends(verify_proof_token)
):
    """Get all dossiers generated by this user, with prospect info."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    cached = supabase.table("proof_dossier_cache") \
        .select("id, prospect_id, created_at") \
        .eq("generated_by", user_id) \
        .order("created_at", desc=True) \
        .execute()

    if not cached.data:
        return {"success": True, "dossiers": []}

    prospect_ids = [d["prospect_id"] for d in cached.data]
    prospects = supabase.table("prospect_master") \
        .select("id, dba_name, legal_name, premise_address1, premise_city, premise_state, premise_zip, business_category") \
        .in_("id", prospect_ids) \
        .execute()

    prospect_map = {p["id"]: p for p in (prospects.data or [])}

    results = []
    for d in cached.data:
        p = prospect_map.get(d["prospect_id"], {})
        results.append({
            "cache_id": d["id"],
            "prospect_id": d["prospect_id"],
            "business_name": p.get("dba_name") or p.get("legal_name") or "Unknown",
            "legal_name": p.get("legal_name"),
            "address": p.get("premise_address1"),
            "city": p.get("premise_city"),
            "state": p.get("premise_state"),
            "zip": p.get("premise_zip"),
            "category": p.get("business_category"),
            "generated_at": d["created_at"]
        })

    return {"success": True, "dossiers": results}

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

class ProofSaveContactRequest(BaseModel):
    prospect_id: Optional[str] = None
    business_name: str
    legal_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    county: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    category: Optional[str] = None
    notes: Optional[str] = None
    source: Optional[str] = "search"


@router.post("/contacts/save")
async def proof_save_contact(
    data: ProofSaveContactRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Save a prospect to the user's contacts."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    # Check if already saved
    if data.prospect_id:
        existing = supabase.table("proof_contacts") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("prospect_id", data.prospect_id) \
            .execute()
        if existing.data:
            return {"success": True, "message": "Already in your contacts", "contact_id": existing.data[0]["id"], "already_saved": True}

    # Check for enrichment data to carry over
    enrichment_data = None
    if data.prospect_id:
        enrich = supabase.table("prospect_enrichments") \
            .select("*") \
            .eq("prospect_id", data.prospect_id) \
            .execute()
        if enrich.data:
            enrichment_data = enrich.data[0]

    # Check for dossier
    has_dossier = False
    if data.prospect_id:
        dossier = supabase.table("proof_dossier_cache") \
            .select("id") \
            .eq("prospect_id", data.prospect_id) \
            .execute()
        has_dossier = bool(dossier.data)

    contact = supabase.table("proof_contacts").insert({
        "user_id": user_id,
        "prospect_id": data.prospect_id,
        "business_name": data.business_name,
        "legal_name": data.legal_name,
        "address": data.address,
        "city": data.city,
        "state": data.state,
        "zip": data.zip,
        "county": data.county,
        "phone": data.phone or (enrichment_data.get("phone") if enrichment_data else None),
        "email": data.email,
        "website": data.website or (enrichment_data.get("website") if enrichment_data else None),
        "category": data.category,
        "notes": data.notes,
        "source": data.source or "search",
        "status": "lead",
        "enrichment_data": enrichment_data,
        "has_dossier": has_dossier,
    }).execute()

    return {
        "success": True,
        "message": "Saved to contacts",
        "contact_id": contact.data[0]["id"],
        "already_saved": False
    }


@router.get("/contacts")
async def proof_get_contacts(
    current_user: dict = Depends(verify_proof_token)
):
    """Get all contacts for the current user."""
    supabase = get_supabase()
    result = supabase.table("proof_contacts") \
        .select("*") \
        .eq("user_id", current_user["proof_user_id"]) \
        .order("created_at", desc=True) \
        .execute()
    return {"success": True, "contacts": result.data}

@router.patch("/contacts/{contact_id}")
async def proof_update_contact(
    contact_id: str,
    request: Request,
    current_user: dict = Depends(verify_proof_token)
):
    """Update a contact's status, notes, or other fields."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]
    body = await request.json()

    allowed = {"status", "notes", "tags", "last_contacted_at", "phone", "email", "website", "business_name"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    updates["updated_at"] = datetime.utcnow().isoformat()

    supabase.table("proof_contacts") \
        .update(updates) \
        .eq("id", contact_id) \
        .eq("user_id", user_id) \
        .execute()

    return {"success": True}


@router.delete("/contacts/{contact_id}")
async def proof_delete_contact(
    contact_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Remove a contact."""
    supabase = get_supabase()
    supabase.table("proof_contacts") \
        .delete() \
        .eq("id", contact_id) \
        .eq("user_id", current_user["proof_user_id"]) \
        .execute()
    return {"success": True}

@router.get("/contacts/{contact_id}/notes")
async def proof_get_contact_notes(
    contact_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Get all notes for a contact."""
    supabase = get_supabase()
    result = supabase.table("proof_contact_notes") \
        .select("*") \
        .eq("contact_id", contact_id) \
        .eq("user_id", current_user["proof_user_id"]) \
        .order("created_at", desc=True) \
        .execute()
    return {"success": True, "notes": result.data}


@router.post("/contacts/{contact_id}/notes")
async def proof_add_contact_note(
    contact_id: str,
    request: Request,
    current_user: dict = Depends(verify_proof_token)
):
    """Add a note to a contact."""
    supabase = get_supabase()
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Note cannot be empty")

    result = supabase.table("proof_contact_notes").insert({
        "contact_id": contact_id,
        "user_id": current_user["proof_user_id"],
        "content": content[:5000
        ]
    }).execute()

    return {"success": True, "note": result.data[0]}


@router.delete("/contacts/notes/{note_id}")
async def proof_delete_contact_note(
    note_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Delete a note."""
    supabase = get_supabase()
    supabase.table("proof_contact_notes") \
        .delete() \
        .eq("id", note_id) \
        .eq("user_id", current_user["proof_user_id"]) \
        .execute()
    return {"success": True}


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
    if data.plan not in ("starter", "individual", "growth", "team", "company"):
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

        elif meta.get("partner_certification"):
            import secrets, string

            def _gen_ref_code():
                chars = string.ascii_uppercase + string.digits
                return "EP-" + ''.join(secrets.choice(chars) for _ in range(4))

            user = supabase.table("proof_users") \
                .select("plan, stripe_subscription_id, organization_id") \
                .eq("id", user_id) \
                .single() \
                .execute()

            is_org = bool(user.data.get("organization_id")) if user.data else False

            referral_code = _gen_ref_code()
            for _ in range(10):
                existing = supabase.table("proof_partners") \
                    .select("id") \
                    .eq("referral_code", referral_code) \
                    .execute()
                if not existing.data:
                    break
                referral_code = _gen_ref_code()

            supabase.table("proof_partners").insert({
                "user_id": user_id,
                "status": "pending_cert",
                "referral_code": referral_code,
                "certification_stripe_pi": session.payment_intent,
                "previous_plan": user.data.get("plan", "free") if user.data else "free",
                "previous_stripe_sub_id": user.data.get("stripe_subscription_id") if user.data else None,
                "is_org_member": is_org,
                "created_at": datetime.utcnow().isoformat()
            }).execute()

            partner_result = supabase.table("proof_partners") \
                .select("id") \
                .eq("user_id", user_id) \
                .single() \
                .execute()

            partner_id = partner_result.data["id"]

            cert_modules = [
                "product_deep_dive", "service_profit_chain", "restaurant_pnl",
                "identifying_buyers", "demo_walkthrough", "objection_handling",
                "compliance_boundaries", "final_assessment"
            ]
            for mod in cert_modules:
                supabase.table("proof_partner_certification").insert({
                    "partner_id": partner_id,
                    "module_id": mod,
                    "status": "not_started",
                    "created_at": datetime.utcnow().isoformat()
                }).execute()

            logger.info(f"Partner enrolled: user {user_id}, code {referral_code}")
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

# ═══════════════════════════════════════════════════════════════════════════════
# VOICE LOGGER — AI-parsed call notes
# ═══════════════════════════════════════════════════════════════════════════════

VOICE_PARSE_PROMPT = """You extract structured data from sales call notes for a restaurant industry CRM.

Extract these fields. Use null if not determinable.

Required (reject if missing):
- business_name: The restaurant name

Optional:
- contact_name: Person spoken to
- contact_title: Their role (GM, Owner, Manager, Bar Manager, etc.)
- phone: Phone number if mentioned
- email: Email if mentioned
- city: City
- state: State abbreviation
- address: Street address if mentioned
- category: One of: restaurant, bar, restaurant_bar, other
- note_content: Summary of the conversation (what was discussed, key takeaways)
- outcome: How it went (positive, neutral, negative, no_answer, voicemail)
- status_suggestion: Suggested pipeline status based on outcome (lead, contacted, meeting_set, proposal, won, lost, nurture)
- follow_up_date: When to follow up (parse relative dates like "Tuesday" or "next week")
- follow_up_note: What to do on follow-up

Respond ONLY with valid JSON. No markdown, no explanation, no code fences.

Example input:
"Called Sotto on Vine Street, spoke with the bar manager about their bourbon program. They're unhappy with delivery reliability from their current distributor. Contract expires in 60 days. Follow up Thursday with samples."

Example output:
{
  "business_name": "Sotto",
  "contact_name": null,
  "contact_title": "Bar Manager",
  "phone": null,
  "email": null,
  "city": null,
  "state": null,
  "address": "Vine Street",
  "category": "restaurant_bar",
  "note_content": "Spoke with bar manager about bourbon program. Unhappy with current distributor's delivery reliability. Contract expires in 60 days.",
  "outcome": "positive",
  "status_suggestion": "contacted",
  "follow_up_date": "Thursday",
  "follow_up_note": "Bring bourbon samples",
  "follow_up_draft": null
}

If the notes mention an existing restaurant the user has been working with, still parse everything. The system will match it.
Now parse these notes:
"""

class VoiceLogRequest(BaseModel):
    notes_text: str
    contact_id: Optional[str] = None

@router.post("/voice-log")
async def proof_voice_log(
    data: VoiceLogRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Parse voice/text call notes and create or update contact + add note."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    if not data.notes_text or len(data.notes_text.strip()) < 10:
        raise HTTPException(status_code=400, detail="Notes too short. Include at least a restaurant name and what happened.")

    # Check plan — Starter+ only
    plan = current_user.get("plan", "free")
    if plan == "free":
        raise HTTPException(status_code=403, detail="Voice logger requires a Starter plan or above.")

    # Parse with Haiku
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
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 800,
                    "messages": [
                        {"role": "user", "content": VOICE_PARSE_PROMPT + data.notes_text}
                    ]
                },
                timeout=30.0
            )

        if resp.status_code != 200:
            logger.error(f"Voice parse API error: {resp.json()}")
            raise HTTPException(status_code=500, detail="AI parsing failed")

        resp_data = resp.json()
        content = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        content = content.strip()
        if content.startswith("```"):
            first_nl = content.index("\n")
            content = content[first_nl + 1:]
            if content.endswith("```"):
                content = content[:-3].strip()

        parsed = json.loads(content)

    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI returned unparseable response. Try rephrasing your notes.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AI parsing timed out. Try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voice parse error: {e}")
        raise HTTPException(status_code=500, detail="AI parsing failed")

    # Validate required field
    if not parsed.get("business_name"):
        return {
            "success": False,
            "error": "missing_restaurant",
            "message": "Couldn't identify the restaurant name. Try starting with the restaurant name.",
            "parsed": parsed
        }

    # Match or create contact
    contact_id = data.contact_id
    matched_existing = False

    if contact_id:
        # User specified which contact — verify it exists
        existing = supabase.table("proof_contacts") \
            .select("id, business_name") \
            .eq("id", contact_id) \
            .eq("user_id", user_id) \
            .execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Contact not found")
        matched_existing = True
    else:
        # Try to match by business name (fuzzy)
        bname = parsed["business_name"].strip().lower()
        contacts = supabase.table("proof_contacts") \
            .select("id, business_name") \
            .eq("user_id", user_id) \
            .execute()
        for c in (contacts.data or []):
            if c["business_name"] and c["business_name"].strip().lower() == bname:
                contact_id = c["id"]
                matched_existing = True
                break

    if not contact_id:
        # Create new contact
        new_contact = {
            "user_id": user_id,
            "business_name": parsed["business_name"],
            "city": parsed.get("city"),
            "state": parsed.get("state"),
            "address": parsed.get("address"),
            "phone": parsed.get("phone"),
            "email": parsed.get("email"),
            "category": parsed.get("category", "restaurant"),
            "status": parsed.get("status_suggestion", "contacted"),
            "source": "voice_log",
            "last_contacted_at": datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }
        # Remove None values
        new_contact = {k: v for k, v in new_contact.items() if v is not None}
        result = supabase.table("proof_contacts").insert(new_contact).execute()
        contact_id = result.data[0]["id"]

    # Update existing contact fields if AI found new info
    if matched_existing:
        update_fields = {"last_contacted_at": datetime.utcnow().isoformat()}
        if parsed.get("phone"):
            update_fields["phone"] = parsed["phone"]
        if parsed.get("email"):
            update_fields["email"] = parsed["email"]
        if parsed.get("status_suggestion") and parsed["status_suggestion"] != "lead":
            update_fields["status"] = parsed["status_suggestion"]
        supabase.table("proof_contacts").update(update_fields).eq("id", contact_id).execute()

    # Add the note
    note_content = parsed.get("note_content") or data.notes_text
    if parsed.get("follow_up_note"):
        note_content += f"\n\nFollow-up: {parsed['follow_up_note']}"
    if parsed.get("follow_up_date"):
        note_content += f" ({parsed['follow_up_date']})"

    supabase.table("proof_contact_notes").insert({
        "contact_id": contact_id,
        "user_id": user_id,
        "content": note_content,
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    log_activity(supabase, user_id, "voice_log", contact_id=contact_id,
                 metadata={"matched_existing": matched_existing, "business_name": parsed.get("business_name")},
                 org_id=current_user.get("organization_id"))
    
    return {
        "success": True,
        "contact_id": contact_id,
        "matched_existing": matched_existing,
        "parsed": parsed,
        "message": f"{'Updated' if matched_existing else 'Created'} {parsed['business_name']} and added note."
    }

@router.post("/import/map-headers")
async def proof_map_headers(
    data: ProofMapHeadersRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Use AI to map CSV headers to proof_contacts schema."""
    if not data.headers or len(data.headers) > 50:
        raise HTTPException(status_code=400, detail="Invalid headers")

    target_fields = "business_name, legal_name, address, city, state, zip, county, phone, email, website, category"

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
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": f"Map these CSV headers to these database fields. Return ONLY a JSON object where keys are the CSV headers and values are the matching database field name or null if no match.\n\nCSV headers: {json.dumps(data.headers)}\n\nDatabase fields: {target_fields}\n\nJSON only, no explanation:"}]
                },
                timeout=15.0
            )
            resp_data = resp.json()

        text = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned[cleaned.index("\n") + 1:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()

        mapping = json.loads(cleaned)
        return {"success": True, "mapping": mapping}

    except Exception as e:
        logger.error(f"Header mapping error: {e}")
        raise HTTPException(status_code=500, detail="Failed to map headers")


@router.post("/import/records")
async def proof_import_records(
    data: ProofImportRecordsRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Import mapped CSV records into proof_contacts."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    if not data.records:
        raise HTTPException(status_code=400, detail="No records to import")
    if len(data.records) > 500:
        raise HTTPException(status_code=400, detail="Maximum 500 records per import")

    allowed_fields = {"business_name", "legal_name", "address", "city", "state", "zip", "county", "phone", "email", "website", "category"}
    imported = 0
    skipped = 0

    for row in data.records:
        mapped = {}
        for csv_header, db_field in data.mapping.items():
            if db_field and db_field in allowed_fields and csv_header in row:
                val = row[csv_header]
                if val and str(val).strip():
                    mapped[db_field] = str(val).strip()

        if not mapped.get("business_name"):
            skipped += 1
            continue

        mapped["user_id"] = user_id
        mapped["source"] = "import"
        mapped["status"] = "lead"

        try:
            supabase.table("proof_contacts").insert(mapped).execute()
            imported += 1
        except Exception as e:
            logger.warning(f"Import row error: {e}")
            skipped += 1

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "message": f"Imported {imported} contacts. {skipped} skipped."
    }

@router.post("/change-password")
async def proof_change_password(
    data: ProofChangePasswordRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Change password for logged-in user."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    user = supabase.table("proof_users") \
        .select("password_hash") \
        .eq("id", user_id) \
        .single() \
        .execute()

    if not user.data:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(data.current_password, user.data["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    new_hash = hash_password(data.new_password)
    supabase.table("proof_users") \
        .update({"password_hash": new_hash}) \
        .eq("id", user_id) \
        .execute()

    return {"success": True, "message": "Password changed successfully"}

# ═══════════════════════════════════════════════════════════════════════════════
# PULSE — CLOSED LOCATIONS SCAN
# ═══════════════════════════════════════════════════════════════════════════════

CLOSED_STATUSES = [
    'EXPIRED', 'Expired', 'Expired - Original Required', 'Expired - Non Renewable',
    'CANCELED / DEACTIVATED', 'CANCELED', 'Cancelled', 'Canceled',
    'Surrendered', 'surend', 'Ceased', 'Closed', 'CLOSED',
    'REVOKED', 'Revoked', 'Inactive', 'INACTIVE', 'inactive', 'InActive'
]

@router.post("/pulse/closed/estimate")
async def pulse_closed_estimate(
    data: ProofScanEstimateRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Estimate number of closed/expired restaurant locations in a region."""
    supabase = get_supabase()
    if not data.states and not data.city and not data.zip_code and not data.county:
        raise HTTPException(status_code=400, detail="Select at least one filter")

    query = supabase.table("prospect_master") \
        .select("id", count="exact") \
        .in_("license_status", CLOSED_STATUSES)

    if data.states:
        query = query.in_("premise_state", [s.upper() for s in data.states])
    if data.city:
        query = query.ilike("premise_city", f"%{data.city}%")
    if data.zip_code:
        query = query.eq("premise_zip", data.zip_code)
    if data.county:
        query = query.ilike("premise_county", f"%{data.county}%")
    if data.categories:
        query = query.in_("business_category", data.categories)

    result = query.execute()
    count = result.count or 0

    return {
        "success": True,
        "count": count,
        "filters": {
            "states": data.states,
            "city": data.city,
            "zip_code": data.zip_code,
            "county": data.county,
            "categories": data.categories
        }
    }


@router.post("/pulse/closed/search")
async def pulse_closed_search(
    data: ProofScanEstimateRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Return closed/expired restaurant locations in a region."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]
    plan = current_user.get("plan", "free")

    if plan == "free":
        raise HTTPException(status_code=403, detail="Closed Locations requires a Starter plan or above.")

    if not data.states and not data.city and not data.zip_code and not data.county:
        raise HTTPException(status_code=400, detail="Select at least one filter")

    query = supabase.table("prospect_master") \
        .select("id, dba_name, legal_name, premise_address1, premise_city, premise_state, premise_zip, premise_county, business_category, raw_license_type, license_status, license_expiry_date, license_issue_date") \
        .in_("license_status", CLOSED_STATUSES) \
        .order("license_expiry_date", desc=True) \
        .limit(500)

    if data.states:
        query = query.in_("premise_state", [s.upper() for s in data.states])
    if data.city:
        query = query.ilike("premise_city", f"%{data.city}%")
    if data.zip_code:
        query = query.eq("premise_zip", data.zip_code)
    if data.county:
        query = query.ilike("premise_county", f"%{data.county}%")
    if data.categories:
        query = query.in_("business_category", data.categories)

    result = query.execute()
    records = result.data or []

    # Calculate days since closure
    from datetime import datetime
    now = datetime.utcnow()
    for r in records:
        if r.get("license_expiry_date"):
            try:
                exp = datetime.fromisoformat(r["license_expiry_date"].replace("Z", "+00:00")).replace(tzinfo=None)
                r["days_since_closure"] = (now - exp).days
            except Exception:
                r["days_since_closure"] = None
        else:
            r["days_since_closure"] = None

    return {
        "success": True,
        "count": len(records),
        "locations": records
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PULSE — COMPETITOR FOOTPRINT SCAN
# ═══════════════════════════════════════════════════════════════════════════════

class PulseCompetitorRequest(BaseModel):
    business_name: str
    city: str
    state: str
    category: Optional[str] = None
    radius_miles: Optional[int] = 5

@router.post("/pulse/competitors")
async def pulse_competitor_scan(
    data: PulseCompetitorRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Find active restaurants in the same category near a target location."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]
    plan = current_user.get("plan", "free")

    if plan == "free":
        raise HTTPException(status_code=403, detail="Competitor Footprint requires a Starter plan or above.")

    # First find the target restaurant to get its zip/county
    target_query = supabase.table("prospect_master") \
        .select("id, dba_name, premise_address1, premise_city, premise_state, premise_zip, premise_county, business_category") \
        .ilike("premise_city", f"%{data.city}%")

    if data.state:
        target_query = target_query.eq("premise_state", data.state.upper())

    target_query = target_query.ilike("dba_name", f"%{data.business_name}%") \
        .limit(1)

    target_result = target_query.execute()

    # Determine category and location for competitor search
    target = target_result.data[0] if target_result.data else None
    search_category = data.category
    search_county = None
    search_zip = None
    search_city = data.city

    if target:
        if not search_category:
            search_category = target.get("business_category")
        search_county = target.get("premise_county")
        search_zip = target.get("premise_zip")

    # Find competitors: same area, active licenses
    active_statuses = ['Active', 'ACTIVE', 'active', 'ISSUED', 'APPROVED', 'Active - Renewal Pending', 'Renewed', 'RENEWAL']

    comp_query = supabase.table("prospect_master") \
        .select("id, dba_name, legal_name, premise_address1, premise_city, premise_state, premise_zip, premise_county, business_category, raw_license_type, license_status, license_issue_date") \
        .in_("license_status", active_statuses) \
        .order("license_issue_date", desc=True) \
        .limit(200)

    # Search by county if available (approximates radius), otherwise city
    if search_county:
        comp_query = comp_query.ilike("premise_county", f"%{search_county}%")
    else:
        comp_query = comp_query.ilike("premise_city", f"%{search_city}%")

    if data.state:
        comp_query = comp_query.eq("premise_state", data.state.upper())

    # Filter by category if specified
    if search_category:
        comp_query = comp_query.eq("business_category", search_category)

    comp_result = comp_query.execute()
    competitors = comp_result.data or []

    # Remove the target restaurant from results
    if target:
        competitors = [c for c in competitors if c["id"] != target["id"]]

    # Calculate age (how long they've been licensed)
    for c in competitors:
        if c.get("license_issue_date"):
            try:
                issued = datetime.fromisoformat(c["license_issue_date"].replace("Z", "+00:00")).replace(tzinfo=None)
                c["years_licensed"] = round((datetime.utcnow() - issued).days / 365.25, 1)
            except Exception:
                c["years_licensed"] = None
        else:
            c["years_licensed"] = None

    # Summary stats
    total = len(competitors)
    new_entrants = len([c for c in competitors if c.get("years_licensed") and c["years_licensed"] < 1])

    return {
        "success": True,
        "target": target,
        "category": search_category,
        "area": search_county or search_city,
        "total_competitors": total,
        "new_entrants_last_year": new_entrants,
        "competitors": competitors
    }

@router.post("/scan/estimate")
async def proof_scan_estimate(
    data: ProofScanEstimateRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Estimate cost for a regional GM vacancy scan."""
    supabase = get_supabase()

    if not data.states and not data.city and not data.zip_code and not data.county:
        raise HTTPException(status_code=400, detail="Select at least one filter (state, city, zip, or county)")

    params = {
        "p_states": data.states if data.states else None,
        "p_city": data.city if data.city else None,
        "p_zip": data.zip_code if data.zip_code else None,
        "p_county": data.county if data.county else None,
        "p_categories": data.categories if data.categories else None,
        "p_new_since_days": None,
        "p_expiring_within_days": None,
        "p_page": 1,
        "p_page_size": 1
    }

    result = supabase.rpc("proof_scan_count", {
        "p_states": data.states if data.states else None,
        "p_city": data.city if data.city else None,
        "p_zip": data.zip_code if data.zip_code else None,
        "p_county": data.county if data.county else None,
        "p_categories": data.categories if data.categories else None
    }).execute()
    count = result.data if isinstance(result.data, int) else 0

    cost_per = get_scan_cost(current_user)
    plan = current_user.get("plan", "free")
    covered, billable = check_allocation(supabase, user_id, plan, 'scan_restaurants', units=count)
    total = round(billable * cost_per, 2)

    return {
        "success": True,
        "count": count,
        "cost_per_record": cost_per,
        "total_cost": total
    }


@router.post("/scan/start")
async def proof_start_scan(
    data: ProofScanEstimateRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_proof_token)
):
    """Start a GM vacancy scan. Deducts credits, runs in background."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    if not data.states and not data.city and not data.zip_code and not data.county:
        raise HTTPException(status_code=400, detail="Select at least one filter")

    count_result = supabase.rpc("proof_scan_count", {
        "p_states": data.states if data.states else None,
        "p_city": data.city if data.city else None,
        "p_zip": data.zip_code if data.zip_code else None,
        "p_county": data.county if data.county else None,
        "p_categories": data.categories if data.categories else None
    }).execute()
    count = count_result.data if isinstance(count_result.data, int) else 0

    if count == 0:
        raise HTTPException(status_code=400, detail="No restaurants match these filters")
    if count > 2000:
        raise HTTPException(status_code=400, detail=f"Too many restaurants ({count}). Narrow your filters to under 2,000.")

    cost_per = get_scan_cost(current_user)
    plan = current_user.get("plan", "free")
    covered, billable = check_allocation(supabase, user_id, plan, 'scan_restaurants', units=count)
    total_cost = round(billable * cost_per, 2)

    # Check balance
    user = supabase.table("proof_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()
    balance = float(user.data.get("credit_balance", 0))

    if total_cost > 0 and balance < total_cost:
        raise HTTPException(status_code=402, detail=f"Insufficient credits. Need ${total_cost:.2f}, have ${balance:.2f}")

    # Deduct credits
    new_balance = round(balance - total_cost, 2)
    supabase.table("proof_users").update({"credit_balance": new_balance}).eq("id", user_id).execute()
    supabase.table("proof_credit_transactions").insert({
        "user_id": user_id,
        "transaction_type": "gm_scan",
        "amount": -total_cost,
        "balance_after": new_balance,
        "description": f"GM Scan: {count} restaurants",
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    # Create scan record
    filters = {
        "states": data.states,
        "city": data.city,
        "zip_code": data.zip_code,
        "county": data.county,
        "categories": data.categories
    }
    scan = supabase.table("proof_gm_scans").insert({
        "user_id": user_id,
        "filters": filters,
        "total_count": count,
        "total_cost": total_cost,
        "status": "running",
        "started_at": datetime.utcnow().isoformat()
    }).execute()

    scan_id = scan.data[0]["id"]

    # Fire background task
    background_tasks.add_task(
        _run_gm_scan_background,
        scan_id, user_id, filters, count
    )

    return {
        "success": True,
        "scan_id": scan_id,
        "total_count": count,
        "total_cost": total_cost,
        "balance_remaining": new_balance
    }


@router.get("/scan/{scan_id}/status")
async def proof_scan_status(
    scan_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Poll scan progress."""
    supabase = get_supabase()
    scan = supabase.table("proof_gm_scans") \
        .select("*") \
        .eq("id", scan_id) \
        .eq("user_id", current_user["proof_user_id"]) \
        .single() \
        .execute()

    if not scan.data:
        raise HTTPException(status_code=404, detail="Scan not found")

    return {
        "success": True,
        "status": scan.data["status"],
        "total_count": scan.data["total_count"],
        "scanned_count": scan.data["scanned_count"],
        "vacancy_count": scan.data["vacancy_count"],
        "completed_at": scan.data.get("completed_at")
    }


@router.get("/scan/{scan_id}/results")
async def proof_scan_results(
    scan_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Get scan results, vacancies first."""
    supabase = get_supabase()

    # Verify ownership
    scan = supabase.table("proof_gm_scans") \
        .select("id, user_id") \
        .eq("id", scan_id) \
        .eq("user_id", current_user["proof_user_id"]) \
        .execute()
    if not scan.data:
        raise HTTPException(status_code=404, detail="Scan not found")

    results = supabase.table("proof_gm_signals") \
        .select("*") \
        .eq("scan_id", scan_id) \
        .order("vacancy_detected", desc=True) \
        .order("scanned_at") \
        .execute()

    return {
        "success": True,
        "results": results.data
    }


@router.get("/scans/mine")
async def proof_my_scans(
    current_user: dict = Depends(verify_proof_token)
):
    """List all scans for this user."""
    supabase = get_supabase()
    result = supabase.table("proof_gm_scans") \
        .select("*") \
        .eq("user_id", current_user["proof_user_id"]) \
        .order("created_at", desc=True) \
        .execute()
    return {"success": True, "scans": result.data}


GM_SCAN_PROMPT = """You are a restaurant industry research assistant. Your ONLY job is to determine if this restaurant currently has a General Manager vacancy or leadership transition.

Search job boards (Indeed, LinkedIn, Google Jobs) for current job postings. Search Google for any news about management changes.

Restaurant: {name}
Location: {city}, {state}

Respond with ONLY this JSON, nothing else:
{{
  "vacancy_detected": true/false,
  "confidence": "high"/"medium"/"low",
  "source": "indeed"/"linkedin"/"google_jobs"/"news"/"none",
  "job_title": "exact job title found or null",
  "posted_date": "approximate date or null",
  "signal_detail": "one sentence explanation"
}}"""


async def _run_gm_scan_background(scan_id: str, user_id: str, filters: dict, total_count: int):
    """Background task: scan restaurants for GM vacancies."""
    supabase = get_supabase()

    try:
        # Fetch deduplicated restaurants via grouped RPC
        result = supabase.rpc("proof_scan_prospects", {
            "p_states": filters.get("states"),
            "p_city": filters.get("city"),
            "p_zip": filters.get("zip_code"),
            "p_county": filters.get("county"),
            "p_categories": filters.get("categories"),
            "p_limit": 2000
        }).execute()
        all_prospects = result.data if isinstance(result.data, list) else (result.data or [])

        scanned = 0
        vacancies = 0

        for prospect in all_prospects:
            name = prospect.get("dba_name") or prospect.get("legal_name") or "Unknown"
            city = prospect.get("premise_city", "")
            state = prospect.get("premise_state", "")

            signal = {
                "scan_id": scan_id,
                "prospect_id": prospect["id"],
                "business_name": name,
                "address": prospect.get("premise_address1"),
                "city": city,
                "state": state,
                "zip": prospect.get("premise_zip"),
                "category": prospect.get("business_category"),
                "vacancy_detected": False,
                "confidence": "low",
                "source": "none",
                "signal_detail": "No vacancy signals detected"
            }

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
                            "model": "claude-haiku-4-5-20251001",
                            "max_tokens": 300,
                            "messages": [{"role": "user", "content": GM_SCAN_PROMPT.format(name=name, city=city, state=state)}],
                            "tools": [{"type": "web_search_20250305", "name": "web_search"}]
                        },
                        timeout=30.0
                    )
                    resp_data = resp.json()

                if resp.status_code == 200:
                    text = ""
                    for block in resp_data.get("content", []):
                        if block.get("type") == "text":
                            text += block.get("text", "")

                    if text:
                        cleaned = text.strip()
                        first_brace = cleaned.find("{")
                        last_brace = cleaned.rfind("}")
                        if first_brace != -1 and last_brace > first_brace:
                            try:
                                parsed = json.loads(cleaned[first_brace:last_brace + 1])
                                signal["vacancy_detected"] = bool(parsed.get("vacancy_detected", False))
                                signal["confidence"] = parsed.get("confidence", "low")
                                signal["source"] = parsed.get("source", "none")
                                signal["job_title"] = parsed.get("job_title")
                                signal["posted_date"] = parsed.get("posted_date")
                                signal["signal_detail"] = parsed.get("signal_detail", "")
                            except (json.JSONDecodeError, ValueError):
                                pass

            except Exception as e:
                logger.warning(f"GM scan error for {name}: {e}")
                signal["signal_detail"] = "Scan error, skipped"

            # Insert signal
            supabase.table("proof_gm_signals").insert(signal).execute()

            scanned += 1
            if signal["vacancy_detected"]:
                vacancies += 1

            # Update progress every 10 records
            if scanned % 10 == 0 or scanned == len(all_prospects):
                supabase.table("proof_gm_scans").update({
                    "scanned_count": scanned,
                    "vacancy_count": vacancies
                }).eq("id", scan_id).execute()

        # Mark complete
        supabase.table("proof_gm_scans").update({
            "status": "complete",
            "scanned_count": scanned,
            "vacancy_count": vacancies,
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", scan_id).execute()

        logger.info(f"GM scan {scan_id} complete: {scanned} scanned, {vacancies} vacancies")

    except Exception as e:
        logger.error(f"GM scan {scan_id} failed: {e}")
        supabase.table("proof_gm_scans").update({
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", scan_id).execute()

@router.post("/docket/generate")
async def proof_generate_docket(
    data: ProofDocketRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_proof_token)
):
    """Generate a prioritized daily call list."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    if data.call_count < 1 or data.call_count > 50:
        raise HTTPException(status_code=400, detail="Call count must be between 1 and 50")

    # Cooldown: 1 hour between docket generations
    last_docket = supabase.table("proof_dockets") \
        .select("created_at") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()
    if last_docket.data:
        last_time = datetime.fromisoformat(last_docket.data[0]["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        minutes_since = (datetime.utcnow() - last_time).total_seconds() / 60
        if minutes_since < 60:
            remaining = int(60 - minutes_since)
            raise HTTPException(status_code=429, detail=f"Docket refreshes once per hour. Try again in {remaining} minutes.")

    # Check docket allocation
    plan = current_user.get("plan", "free")
    covered, billable = check_allocation(supabase, user_id, plan, 'docket')
    if billable > 0:
        alloc = PLAN_ALLOCATIONS.get(plan, PLAN_ALLOCATIONS['free'])
        limit = alloc.get('docket', 0)
        if limit == 0:
            raise HTTPException(status_code=403, detail="The Docket requires a Starter plan or above.")
        used = get_monthly_usage(supabase, user_id, 'docket')
        raise HTTPException(status_code=403, detail=f"Docket limit reached ({used}/{limit} this month). Upgrade to Growth for unlimited.")

    # Check they have contacts
    contacts = supabase.table("proof_contacts") \
        .select("id", count="exact") \
        .eq("user_id", user_id) \
        .execute()
    if not contacts.count or contacts.count == 0:
        raise HTTPException(status_code=400, detail="No contacts in your pipeline. Save some from Search or Import first.")

    # Create docket record
    docket = supabase.table("proof_dockets").insert({
        "user_id": user_id,
        "call_count": data.call_count,
        "status": "generating"
    }).execute()

    docket_id = docket.data[0]["id"]

    background_tasks.add_task(
        _run_docket_background,
        docket_id, user_id, data.call_count
    )

    return {"success": True, "docket_id": docket_id}


@router.get("/docket/{docket_id}/status")
async def proof_docket_status(
    docket_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    supabase = get_supabase()
    result = supabase.table("proof_dockets") \
        .select("*") \
        .eq("id", docket_id) \
        .eq("user_id", current_user["proof_user_id"]) \
        .single() \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Docket not found")

    return {
        "success": True,
        "status": result.data["status"],
        "docket": result.data.get("docket"),
        "call_count": result.data["call_count"],
        "created_at": result.data["created_at"],
        "completed_at": result.data.get("completed_at")
    }


@router.get("/docket/latest")
async def proof_docket_latest(
    current_user: dict = Depends(verify_proof_token)
):
    supabase = get_supabase()
    result = supabase.table("proof_dockets") \
        .select("*") \
        .eq("user_id", current_user["proof_user_id"]) \
        .eq("status", "complete") \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()

    if not result.data:
        return {"success": True, "docket": None}

    return {
        "success": True,
        "docket": result.data[0].get("docket"),
        "docket_id": result.data[0]["id"],
        "call_count": result.data[0]["call_count"],
        "created_at": result.data[0]["created_at"]
    }


DOCKET_SYSTEM_PROMPT = """You are a sales intelligence analyst for the restaurant and hospitality industry. Your job is to analyze a sales rep's contact pipeline and prioritize their calls for maximum effectiveness today.

You will receive a JSON array of contacts with their details, notes history, enrichment data, and dossier intel. Analyze everything and return a prioritized call list.

PRIORITIZATION FACTORS (in rough order of weight):
1. HOT SIGNALS: GM vacancy detected, recent hiring surge, ownership change — these are time-sensitive
2. MOMENTUM: Recently contacted with positive notes, meeting set, proposal pending — don't let warm leads go cold
3. RECENCY GAP: Contacted 2-4 weeks ago with no follow-up — danger zone of being forgotten
4. NEVER CONTACTED: Leads saved but never called — especially if they have enrichment/dossier data ready
5. PAIN POINTS: Dossier revealed specific pain points that align with the rep's pitch
6. HIGH VALUE: Multi-unit operators, high revenue, high review counts
7. RE-ENGAGE: Status is "lost" or "nurture" but notes suggest a timing issue, not a hard no

For each prioritized contact, provide:
- A specific reason why TODAY is the right day to call (not generic)
- A suggested opening line tailored to what you know about them
- The call type: "cold_intro", "follow_up", "close_attempt", "re_engage", or "check_in"
- The recommended contact method: "visit", "call", or "email" based on:
  * VISIT if: cold_intro with no prior relationship, high-value target, notes mention "stop by" or in-person meeting, or the contact has a physical address but no phone/email
  * CALL if: has phone number, follow_up or check_in on warm lead, quick re-engage
  * EMAIL if: has email but no phone, proposal or document needs sending, re-engage on cold lead where a call might feel pushy
- A brief reason for the method choice

ALSO scan ALL notes for scheduling constraints. Look for any mention of:
- Specific days someone is available/unavailable ("only there on Tuesdays", "closed Mondays")
- Time preferences ("mornings are best", "don't call before 10", "lunch rush avoid 11-2")
- Location patterns ("only at the west side location on Fridays")
- Upcoming meetings or follow-ups ("said to come back Thursday")

Return these as structured rules in a separate array.

Respond with ONLY valid JSON, no preamble:
{
  "calls": [
    {
      "rank": 1,
      "contact_id": "uuid",
      "business_name": "Name",
      "why_today": "Specific reason this is priority today",
      "opening_line": "Hey [name], I saw that...",
      "call_type": "follow_up",
      "contact_method": "call",
      "method_reason": "Has phone, warm follow-up from last week",
      "phone": "555-1234 or null",
      "city": "Cincinnati",
      "state": "OH"
    }
  ],
  "detected_rules": [
    {
      "contact_id": "uuid",
      "business_name": "Name",
      "rule_text": "Only available weekday mornings",
      "day_of_week": ["monday","tuesday","wednesday","thursday","friday"],
      "time_earliest": "08:00",
      "time_latest": "12:00",
      "source_quote": "betty said mornings work best, she does yoga afternoons"
    }
  ],
  "pipeline_summary": "Brief 2-sentence summary of the overall pipeline health"
}"""

@router.post("/docket/override-method")
async def proof_docket_override_method(
    data: DocketOverrideRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Override AI-recommended contact method for a docket entry."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    if data.method not in ("call", "email", "visit"):
        raise HTTPException(status_code=400, detail="Method must be call, email, or visit")

    # Get current docket
    result = supabase.table("proof_dockets") \
        .select("method_overrides") \
        .eq("id", data.docket_id) \
        .eq("user_id", user_id) \
        .single() \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Docket not found")

    overrides = result.data.get("method_overrides") or {}
    overrides[data.contact_id] = {
        "method": data.method,
        "overridden_at": datetime.utcnow().isoformat()
    }

    supabase.table("proof_dockets").update({
        "method_overrides": overrides
    }).eq("id", data.docket_id).execute()

    # Log the override for admin tracking
    log_activity(supabase, user_id, "method_override", contact_id=data.contact_id,
                 metadata={"method": data.method, "docket_id": data.docket_id},
                 org_id=current_user.get("organization_id"))

    return {"success": True, "method": data.method}


async def _run_docket_background(docket_id: str, user_id: str, call_count: int):
    """Background task: build the daily call docket."""
    supabase = get_supabase()

    try:
        # Gather all contacts
        contacts_result = supabase.table("proof_contacts") \
            .select("*") \
            .eq("user_id", user_id) \
            .execute()
        contacts = contacts_result.data or []

        if not contacts:
            supabase.table("proof_dockets").update({
                "status": "failed",
                "docket": {"error": "No contacts found"},
                "completed_at": datetime.utcnow().isoformat()
            }).eq("id", docket_id).execute()
            return

        # Pre-filter: cap at 200 most actionable contacts for Sonnet
        active = [c for c in contacts if c.get("status") not in ("won", "lost")]
        if not active:
            active = contacts  # If all won/lost, use everything
        if len(active) > 200:
            def _score(c):
                s = 0
                if c.get("has_dossier"): s += 3
                if c.get("enrichment_data"): s += 2
                if c.get("last_contacted_at"): s += 1
                if c.get("status") in ("meeting_set", "proposal"): s += 4
                if c.get("status") == "contacted": s += 2
                if c.get("notes"): s += 1
                return s
            active.sort(key=_score, reverse=True)
            active = active[:200]
        contacts = active

        # Gather notes for all contacts
        contact_ids = [c["id"] for c in contacts]
        notes_result = supabase.table("proof_contact_notes") \
            .select("contact_id, content, created_at") \
            .in_("contact_id", contact_ids) \
            .order("created_at", desc=True) \
            .execute()
        notes_by_contact = {}
        for n in (notes_result.data or []):
            cid = n["contact_id"]
            if cid not in notes_by_contact:
                notes_by_contact[cid] = []
            notes_by_contact[cid].append({
                "date": n["created_at"][:10] if n.get("created_at") else None,
                "content": n["content"][:200]  # Truncate long notes
            })

        # Gather dossier data for contacts with prospect_id
        prospect_ids = [c["prospect_id"] for c in contacts if c.get("prospect_id")]
        dossiers_by_prospect = {}
        if prospect_ids:
            for pid in prospect_ids[:50]:  # Cap at 50 to stay within context
                try:
                    dos_result = supabase.table("proof_dossier_cache") \
                        .select("dossier, prospect_id") \
                        .eq("prospect_id", pid) \
                        .limit(1) \
                        .execute()
                    if dos_result.data:
                        raw = dos_result.data[0].get("dossier")
                        if isinstance(raw, str):
                            try:
                                f = raw.find("{")
                                l = raw.rfind("}")
                                if f != -1 and l > f:
                                    raw = json.loads(raw[f:l+1])
                            except:
                                raw = None
                        if isinstance(raw, dict):
                            # Extract only key fields to save tokens
                            dossiers_by_prospect[pid] = {
                                "opportunity": (raw.get("recommended_approach") or {}).get("opportunity"),
                                "pain_points": raw.get("pain_points", [])[:3],
                                "gm_status": (raw.get("leadership") or {}).get("verdict"),
                                "narrative": ((raw.get("recommended_approach") or {}).get("narrative") or "")[:150]
                            }
                except:
                    pass

        # Gather GM scan signals for these contacts
        gm_signals = {}
        if prospect_ids:
            try:
                sig_result = supabase.table("proof_gm_signals") \
                    .select("prospect_id, vacancy_detected, confidence, signal_detail, job_title") \
                    .in_("prospect_id", prospect_ids) \
                    .eq("vacancy_detected", True) \
                    .execute()
                for s in (sig_result.data or []):
                    gm_signals[s["prospect_id"]] = {
                        "vacancy": True,
                        "confidence": s.get("confidence"),
                        "detail": (s.get("signal_detail") or "")[:100],
                        "job_title": s.get("job_title")
                    }
            except:
                pass

        # Build the context payload for Sonnet
        today = datetime.utcnow().strftime("%Y-%m-%d")
        contact_summaries = []
        for c in contacts:
            pid = c.get("prospect_id")
            enrich = c.get("enrichment_data") or {}
            summary = {
                "contact_id": c["id"],
                "business_name": c.get("business_name", ""),
                "city": c.get("city", ""),
                "state": c.get("state", ""),
                "phone": c.get("phone") or enrich.get("phone"),
                "category": c.get("category", ""),
                "status": c.get("status", "lead"),
                "source": c.get("source", ""),
                "last_contacted": c.get("last_contacted_at", "never"),
                "saved_date": (c.get("created_at") or "")[:10],
                "notes": notes_by_contact.get(c["id"], [])[:5],  # Last 5 notes
                "google_rating": enrich.get("google_rating"),
                "google_reviews": enrich.get("google_review_count"),
            }
            if pid and pid in dossiers_by_prospect:
                summary["dossier"] = dossiers_by_prospect[pid]
            if pid and pid in gm_signals:
                summary["gm_vacancy"] = gm_signals[pid]
            if c.get("notes"):
                summary["initial_note"] = c["notes"][:150]

            contact_summaries.append(summary)

        user_prompt = f"""Today's date: {today}
The rep wants {call_count} calls prioritized for today.
They have {len(contacts)} total contacts in their pipeline.

Here are all their contacts with available intelligence:

{json.dumps(contact_summaries, default=str)}

Return exactly {call_count} prioritized calls (or fewer if they don't have enough actionable contacts). Focus on contacts where action TODAY specifically matters."""

        # Call Sonnet
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
                    "system": DOCKET_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_prompt}]
                },
                timeout=90.0
            )
            resp_data = resp.json()

        if resp.status_code != 200:
            logger.error(f"Docket Sonnet call failed: {resp.status_code} {resp_data}")
            supabase.table("proof_dockets").update({
                "status": "failed",
                "completed_at": datetime.utcnow().isoformat()
            }).eq("id", docket_id).execute()
            return

        # Parse response
        text = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        docket_data = None
        if text:
            cleaned = text.strip()
            first_brace = cleaned.find("{")
            last_brace = cleaned.rfind("}")
            if first_brace != -1 and last_brace > first_brace:
                try:
                    docket_data = json.loads(cleaned[first_brace:last_brace + 1])
                except json.JSONDecodeError:
                    try:
                        import json_repair
                        docket_data = json_repair.loads(cleaned[first_brace:last_brace + 1])
                    except:
                        pass

        if docket_data:
            # Override AI fields with real contact data — never trust LLM for structured fields
            contact_lookup = {}
            for c in contacts:
                enrich = c.get("enrichment_data") or {}
                contact_lookup[c["id"]] = {
                    "phone": c.get("phone") or enrich.get("phone"),
                    "has_enrichment": bool(enrich),
                    "has_dossier": bool(c.get("has_dossier")),
                    "prospect_id": c.get("prospect_id"),
                    "status": c.get("status", "lead"),
                }
            for call in docket_data.get("calls", []):
                cid = call.get("contact_id")
                if cid and cid in contact_lookup:
                    real = contact_lookup[cid]
                    call["phone"] = real["phone"]
                    call["has_enrichment"] = real["has_enrichment"]
                    call["has_dossier"] = real["has_dossier"]
                    call["prospect_id"] = real["prospect_id"]
                    call["status"] = real["status"]
                elif cid:
                    call["phone"] = None
                    call["has_enrichment"] = False
                    call["has_dossier"] = False
                    call["prospect_id"] = None

            # Save any AI-detected scheduling rules
            detected_rules = docket_data.get("detected_rules", [])
            for rule in detected_rules:
                rule_cid = rule.get("contact_id")
                if not rule_cid or rule_cid not in contact_lookup:
                    continue
                # Check if similar rule already exists
                existing = supabase.table("proof_contact_rules") \
                    .select("id") \
                    .eq("contact_id", rule_cid) \
                    .eq("user_id", user_id) \
                    .eq("active", True) \
                    .execute()
                if existing.data:
                    continue  # Don't duplicate rules
                try:
                    supabase.table("proof_contact_rules").insert({
                        "contact_id": rule_cid,
                        "user_id": user_id,
                        "rule_type": "availability",
                        "rule_text": rule.get("rule_text", ""),
                        "day_of_week": rule.get("day_of_week"),
                        "time_earliest": rule.get("time_earliest"),
                        "time_latest": rule.get("time_latest"),
                        "source": "ai_parsed",
                        "created_at": datetime.utcnow().isoformat()
                    }).execute()
                    logger.info(f"Saved detected rule for contact {rule_cid}: {rule.get('rule_text')}")
                except Exception as e:
                    logger.warning(f"Failed to save detected rule: {e}")

            # Enrich call entries with address data and existing rules for route planner
            for call in docket_data.get("calls", []):
                cid = call.get("contact_id")
                if cid:
                    contact = next((c for c in contacts if c["id"] == cid), None)
                    if contact:
                        call["address"] = contact.get("address")
                        call["zip"] = contact.get("zip")

            supabase.table("proof_dockets").update({
                "status": "complete",
                "docket": docket_data,
                "completed_at": datetime.utcnow().isoformat()
            }).eq("id", docket_id).execute()
            logger.info(f"Docket {docket_id} complete: {len(docket_data.get('calls', []))} calls, {len(detected_rules)} rules detected")
        else:
            supabase.table("proof_dockets").update({
                "status": "failed",
                "docket": {"error": "Failed to parse AI response", "raw": text[:500]},
                "completed_at": datetime.utcnow().isoformat()
            }).eq("id", docket_id).execute()

    except Exception as e:
        logger.error(f"Docket {docket_id} failed: {e}")
        supabase.table("proof_dockets").update({
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", docket_id).execute()

@router.post("/contacts/{contact_id}/enrich")
async def proof_enrich_contact(
    contact_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Enrich a contact directly (no prospect_master needed)."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]
    plan = current_user.get("plan", "free")
    covered, billable = check_allocation(supabase, user_id, plan, 'enrichments')
    effective_cost = enrichment_cost if billable > 0 else 0

    # Get contact
    contact = supabase.table("proof_contacts") \
        .select("*") \
        .eq("id", contact_id) \
        .eq("user_id", user_id) \
        .single() \
        .execute()
    if not contact.data:
        raise HTTPException(status_code=404, detail="Contact not found")

    c = contact.data

    # If already enriched, return cached
    if c.get("enrichment_data") and c["enrichment_data"].get("phone"):
        return {
            "success": True,
            "enrichment": c["enrichment_data"],
            "cached": True,
            "charged": 0
        }

    # If contact has a prospect_id, redirect to existing enrich
    if c.get("prospect_id"):
        # Use existing enrichment flow
        return await proof_enrich(c["prospect_id"], current_user)

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

    business_name = c.get("business_name") or c.get("legal_name") or ""
    city = c.get("city", "")
    state = c.get("state", "")
    address = c.get("address", "")

    if not business_name:
        raise HTTPException(status_code=400, detail="Contact has no business name")

    gp_query = f"{business_name} {address} {city} {state}"

    try:
        async with httpx.AsyncClient() as client:
            google_data, yelp_data = await asyncio.gather(
                _fetch_google_places(client, gp_query),
                _fetch_yelp(client, business_name, city, state, address),
                return_exceptions=True
            )

        if isinstance(google_data, Exception):
            logger.warning(f"Google Places failed for contact {contact_id}: {google_data}")
            google_data = {}
        if isinstance(yelp_data, Exception):
            logger.warning(f"Yelp failed for contact {contact_id}: {yelp_data}")
            yelp_data = {}

        enrichment = {
            "enrichment_source": "google_places+yelp",
            "enriched_at": datetime.utcnow().isoformat(),
            "phone": google_data.get("phone"),
            "website": google_data.get("website"),
            "google_rating": google_data.get("rating"),
            "google_review_count": google_data.get("user_ratings_total"),
            "google_place_id": google_data.get("place_id"),
            "google_price_level": google_data.get("price_level"),
            "google_types": google_data.get("types"),
            "yelp_rating": yelp_data.get("rating"),
            "yelp_review_count": yelp_data.get("review_count"),
            "yelp_url": yelp_data.get("url"),
            "yelp_price": yelp_data.get("price"),
        }

        has_data = enrichment.get("phone") or enrichment.get("website") or enrichment.get("google_rating")

        if has_data:
            # Charge credits
            new_balance = round(balance - enrichment_cost, 2)
            supabase.table("proof_users").update({"credit_balance": new_balance}).eq("id", user_id).execute()
            supabase.table("proof_credit_transactions").insert({
                "user_id": user_id,
                "transaction_type": "enrichment",
                "amount": -effective_cost,
                "balance_after": new_balance,
                "description": f"Enrich contact: {business_name}",
                "created_at": datetime.utcnow().isoformat()
            }).execute()

            # Update contact's enrichment_data + phone/website if we found them
            update_fields = {"enrichment_data": enrichment}
            if enrichment.get("phone") and not c.get("phone"):
                update_fields["phone"] = enrichment["phone"]
            if enrichment.get("website") and not c.get("website"):
                update_fields["website"] = enrichment["website"]
            if google_data.get("formatted_address"):
                update_fields["address"] = google_data["formatted_address"]

            supabase.table("proof_contacts").update(update_fields).eq("id", contact_id).execute()

            return {
                "success": True,
                "enrichment": enrichment,
                "cached": False,
                "charged": effective_cost,
                "balance_remaining": new_balance
            }
        else:
            return {
                "success": True,
                "enrichment": enrichment,
                "cached": False,
                "charged": 0,
                "balance_remaining": balance,
                "message": "No useful data found. No charge."
            }

    except Exception as e:
        logger.error(f"Contact enrichment failed for {contact_id}: {e}")
        raise HTTPException(status_code=500, detail="Enrichment failed")


@router.post("/contacts/{contact_id}/dossier")
async def proof_contact_dossier(
    contact_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_proof_token)
):
    """Generate a dossier for a contact (no prospect_master needed)."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]
    _, dossier_cost = get_costs(current_user)

    # Get contact
    contact = supabase.table("proof_contacts") \
        .select("*") \
        .eq("id", contact_id) \
        .eq("user_id", user_id) \
        .single() \
        .execute()
    if not contact.data:
        raise HTTPException(status_code=404, detail="Contact not found")

    c = contact.data

    # If contact has a prospect_id, redirect to existing dossier flow
    if c.get("prospect_id"):
        return await proof_dossier(c["prospect_id"], background_tasks, current_user)

    # Check for cached dossier by contact_id
    cached = supabase.table("proof_dossier_cache") \
        .select("*") \
        .eq("prospect_id", contact_id) \
        .execute()
    if cached.data:
        cached_text = cached.data[0].get("dossier_text") or cached.data[0].get("dossier")
        try:
            dossier_data = json.loads(cached_text) if isinstance(cached_text, str) else cached_text
        except (json.JSONDecodeError, ValueError):
            dossier_data = cached_text
        return {
            "success": True,
            "cached": True,
            "dossier": dossier_data,
            "charged": 0
        }

    # Check balance
    user = supabase.table("proof_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()
    balance = float(user.data.get("credit_balance", 0))
    monthly_limit = user.data.get("monthly_credit_limit")
    if monthly_limit is not None and not check_spending_limit(supabase, user_id, monthly_limit, dossier_cost):
        raise HTTPException(
            status_code=403,
            detail="Monthly spending limit reached. Contact your admin to increase your limit."
        )
    if balance < dossier_cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. Balance: ${balance:.2f}, dossier cost: ${dossier_cost:.2f}"
        )

    # Charge
    new_balance = round(balance - dossier_cost, 2)
    supabase.table("proof_users").update({"credit_balance": new_balance}).eq("id", user_id).execute()
    supabase.table("proof_credit_transactions").insert({
        "user_id": user_id,
        "transaction_type": "dossier",
        "amount": -dossier_cost,
        "balance_after": new_balance,
        "description": f"Dossier: {c.get('business_name', 'Unknown')}",
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    business_name = c.get("business_name") or c.get("legal_name") or "Unknown"
    city = c.get("city", "")
    state = c.get("state", "")

    # Build prospect-like dict for the existing background function
    prospect_data = {
        "dba_name": business_name,
        "legal_name": c.get("legal_name"),
        "premise_address1": c.get("address"),
        "premise_city": city,
        "premise_state": state,
        "premise_zip": c.get("zip"),
    }

    # Use contact_id as the key in dossier cache
    background_tasks.add_task(
        _generate_dossier_background,
        contact_id, user_id, prospect_data
    )

    # Mark contact as having a dossier (will be generated shortly)
    supabase.table("proof_contacts") \
        .update({"has_dossier": True}) \
        .eq("id", contact_id) \
        .execute()

    return {
        "success": True,
        "cached": False,
        "charged": dossier_cost,
        "balance_remaining": new_balance,
        "message": "Dossier generating in background"
    }

@router.post("/deals")
async def proof_create_deal(
    data: DealCreateRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Create a new deal on a contact."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    if data.stage not in ("discovery", "proposal", "negotiation", "won", "lost"):
        raise HTTPException(status_code=400, detail="Invalid stage")

    deal = {
        "contact_id": data.contact_id,
        "user_id": user_id,
        "organization_id": current_user.get("organization_id"),
        "title": data.title,
        "value": data.value,
        "recurring": data.recurring,
        "recurring_frequency": data.recurring_frequency,
        "stage": data.stage,
        "notes": data.notes,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    if data.stage == "won":
        deal["closed_at"] = datetime.utcnow().isoformat()

    result = supabase.table("proof_deals").insert(deal).execute()

    log_activity(supabase, user_id, "deal_created", contact_id=data.contact_id,
                 deal_id=result.data[0]["id"] if result.data else None,
                 metadata={"title": data.title, "value": data.value, "stage": data.stage},
                 org_id=current_user.get("organization_id"))

    return {"success": True, "deal": result.data[0] if result.data else None}


@router.get("/deals")
async def proof_list_deals(
    contact_id: Optional[str] = None,
    current_user: dict = Depends(verify_proof_token)
):
    """List deals for current user, optionally filtered by contact."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    query = supabase.table("proof_deals") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True)

    if contact_id:
        query = query.eq("contact_id", contact_id)

    result = query.execute()
    return {"success": True, "deals": result.data or []}


@router.patch("/deals/{deal_id}")
async def proof_update_deal(
    deal_id: str,
    data: DealUpdateRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Update a deal."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    update = {"updated_at": datetime.utcnow().isoformat()}
    if data.title is not None:
        update["title"] = data.title
    if data.value is not None:
        update["value"] = data.value
    if data.recurring is not None:
        update["recurring"] = data.recurring
    if data.recurring_frequency is not None:
        update["recurring_frequency"] = data.recurring_frequency
    if data.notes is not None:
        update["notes"] = data.notes
    if data.stage is not None:
        if data.stage not in ("discovery", "proposal", "negotiation", "won", "lost"):
            raise HTTPException(status_code=400, detail="Invalid stage")
        update["stage"] = data.stage
        if data.stage == "won":
            update["closed_at"] = datetime.utcnow().isoformat()
        elif data.stage != "lost":
            update["closed_at"] = None

    result = supabase.table("proof_deals") \
        .update(update) \
        .eq("id", deal_id) \
        .eq("user_id", user_id) \
        .execute()

    log_activity(supabase, user_id, "deal_updated", deal_id=deal_id,
                 metadata={"changes": update},
                 org_id=current_user.get("organization_id"))

    return {"success": True, "deal": result.data[0] if result.data else None}


@router.delete("/deals/{deal_id}")
async def proof_delete_deal(
    deal_id: str,
    current_user: dict = Depends(verify_proof_token)
):
    """Delete a deal."""
    supabase = get_supabase()
    supabase.table("proof_deals") \
        .delete() \
        .eq("id", deal_id) \
        .eq("user_id", current_user["proof_user_id"]) \
        .execute()
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN — TEAM DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/dashboard")
async def proof_admin_dashboard(
    period: str = "month",
    current_user: dict = Depends(verify_proof_token)
):
    """Get team activity dashboard. Admin only."""
    if not current_user.get("is_org_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    org_id = current_user.get("organization_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization found")

    supabase = get_supabase()
    now = datetime.utcnow()

    if period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "quarter":
        quarter_month = ((now.month - 1) // 3) * 3 + 1
        start = now.replace(month=quarter_month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    start_iso = start.isoformat()

    # Get all members
    members = supabase.table("proof_users") \
        .select("id, email, full_name, credit_balance, territory_states, monthly_credit_limit, last_login, is_org_admin") \
        .eq("organization_id", org_id) \
        .execute()

    member_ids = [m["id"] for m in (members.data or [])]

    # Get activities for all members in period
    activities = supabase.table("proof_activity_log") \
        .select("user_id, activity_type, metadata, created_at") \
        .eq("organization_id", org_id) \
        .gte("created_at", start_iso) \
        .order("created_at", desc=True) \
        .limit(5000) \
        .execute()

    # Get deals for all members
    deals = supabase.table("proof_deals") \
        .select("user_id, title, value, stage, recurring, recurring_frequency, closed_at, contact_id, created_at") \
        .in_("user_id", member_ids) \
        .execute()

    # Get docket method overrides in period
    dockets = supabase.table("proof_dockets") \
        .select("user_id, method_overrides, created_at") \
        .in_("user_id", member_ids) \
        .gte("created_at", start_iso) \
        .execute()

    # Build per-member summaries
    member_summaries = []
    for m in (members.data or []):
        uid = m["id"]
        user_activities = [a for a in (activities.data or []) if a["user_id"] == uid]

        # Count by activity type
        type_counts = {}
        for a in user_activities:
            t = a["activity_type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        # Count method overrides
        override_count = 0
        for d in (dockets.data or []):
            if d["user_id"] == uid and d.get("method_overrides"):
                override_count += len(d["method_overrides"])

        # Deal metrics
        user_deals = [d for d in (deals.data or []) if d["user_id"] == uid]
        won_deals = [d for d in user_deals if d["stage"] == "won"]
        active_deals = [d for d in user_deals if d["stage"] not in ("won", "lost")]
        pipeline_value = sum(float(d.get("value", 0)) for d in active_deals)
        closed_value = sum(float(d.get("value", 0)) for d in won_deals)
        recurring_value = sum(float(d.get("value", 0)) for d in won_deals if d.get("recurring"))

        # Credit usage in period
        credit_result = supabase.table("proof_credit_transactions") \
            .select("amount") \
            .eq("user_id", uid) \
            .lt("amount", 0) \
            .gte("created_at", start_iso) \
            .execute()
        credits_used = abs(sum(float(c["amount"]) for c in (credit_result.data or [])))

        member_summaries.append({
            "user_id": uid,
            "full_name": m.get("full_name", ""),
            "email": m.get("email", ""),
            "is_admin": m.get("is_org_admin", False),
            "credit_balance": float(m.get("credit_balance", 0)),
            "credits_used_period": credits_used,
            "monthly_credit_limit": float(m["monthly_credit_limit"]) if m.get("monthly_credit_limit") else None,
            "territory_states": m.get("territory_states"),
            "last_login": m.get("last_login"),
            "activity_counts": type_counts,
            "method_overrides": override_count,
            "total_activities": len(user_activities),
            "deals": {
                "active": len(active_deals),
                "won": len(won_deals),
                "lost": len([d for d in user_deals if d["stage"] == "lost"]),
                "pipeline_value": pipeline_value,
                "closed_value": closed_value,
                "recurring_value": recurring_value
            }
        })

    # Org-level totals
    total_activities = len(activities.data or [])
    total_pipeline = sum(m["deals"]["pipeline_value"] for m in member_summaries)
    total_closed = sum(m["deals"]["closed_value"] for m in member_summaries)
    total_recurring = sum(m["deals"]["recurring_value"] for m in member_summaries)

    return {
        "success": True,
        "period": period,
        "period_start": start_iso,
        "team_size": len(members.data or []),
        "totals": {
            "activities": total_activities,
            "pipeline_value": total_pipeline,
            "closed_value": total_closed,
            "recurring_value": total_recurring
        },
        "members": member_summaries
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN — TERRITORY ASSIGNMENT
# ═══════════════════════════════════════════════════════════════════════════════

class TerritoryRequest(BaseModel):
    user_id: str
    states: Optional[List[str]] = None  # None = unrestricted

@router.post("/admin/territory")
async def proof_admin_set_territory(
    data: TerritoryRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Assign territory states to a team member. Admin only."""
    if not current_user.get("is_org_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    org_id = current_user.get("organization_id")
    supabase = get_supabase()

    # Verify user is in the same org
    target = supabase.table("proof_users") \
        .select("id, organization_id") \
        .eq("id", data.user_id) \
        .single() \
        .execute()

    if not target.data or target.data.get("organization_id") != org_id:
        raise HTTPException(status_code=404, detail="User not found in your organization")

    states = [s.upper() for s in data.states] if data.states else None

    supabase.table("proof_users") \
        .update({"territory_states": states}) \
        .eq("id", data.user_id) \
        .execute()

    return {"success": True, "territory_states": states}


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN — SPENDING LIMITS
# ═══════════════════════════════════════════════════════════════════════════════

class SpendingLimitRequest(BaseModel):
    user_id: str
    monthly_limit: Optional[float] = None  # None = unlimited

@router.post("/admin/spending-limit")
async def proof_admin_set_spending_limit(
    data: SpendingLimitRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Set monthly credit spending limit for a team member. Admin only."""
    if not current_user.get("is_org_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    org_id = current_user.get("organization_id")
    supabase = get_supabase()

    target = supabase.table("proof_users") \
        .select("id, organization_id") \
        .eq("id", data.user_id) \
        .single() \
        .execute()

    if not target.data or target.data.get("organization_id") != org_id:
        raise HTTPException(status_code=404, detail="User not found in your organization")

    supabase.table("proof_users") \
        .update({"monthly_credit_limit": data.monthly_limit}) \
        .eq("id", data.user_id) \
        .execute()

    return {"success": True, "monthly_credit_limit": data.monthly_limit}


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-REFILL
# ═══════════════════════════════════════════════════════════════════════════════

class AutoRefillRequest(BaseModel):
    threshold: Optional[float] = None   # trigger when balance drops below this
    amount: Optional[float] = None      # refill to this amount

@router.post("/credits/auto-refill")
async def proof_set_auto_refill(
    data: AutoRefillRequest,
    current_user: dict = Depends(verify_proof_token)
):
    """Set auto-refill preferences. Pass null to disable."""
    supabase = get_supabase()
    user_id = current_user["proof_user_id"]

    if data.threshold is not None and data.amount is not None:
        if data.amount < data.threshold:
            raise HTTPException(status_code=400, detail="Refill amount must be greater than threshold")
        if data.amount not in (25, 50, 100):
            raise HTTPException(status_code=400, detail="Refill amount must be $25, $50, or $100")

    supabase.table("proof_users").update({
        "auto_refill_threshold": data.threshold,
        "auto_refill_amount": data.amount
    }).eq("id", user_id).execute()

    return {
        "success": True,
        "auto_refill_threshold": data.threshold,
        "auto_refill_amount": data.amount
    }


@router.get("/credits/auto-refill")
async def proof_get_auto_refill(
    current_user: dict = Depends(verify_proof_token)
):
    """Get current auto-refill settings."""
    supabase = get_supabase()
    result = supabase.table("proof_users") \
        .select("auto_refill_threshold, auto_refill_amount") \
        .eq("id", current_user["proof_user_id"]) \
        .single() \
        .execute()

    return {
        "success": True,
        "auto_refill_threshold": result.data.get("auto_refill_threshold") if result.data else None,
        "auto_refill_amount": result.data.get("auto_refill_amount") if result.data else None
    }