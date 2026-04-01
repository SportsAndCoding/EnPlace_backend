# routes/brigade.py
"""
Brigade Intelligence API
========================
Recruiting intelligence for hospitality recruiters and staffing agencies.
Detects leadership vacancies, surfaces passive candidate signals,
and manages recruiter placement pipelines.

Shares backend infrastructure with Mise (same Heroku app, same Supabase)
but has its own auth (brigade_users), its own branding, and its own pricing.
"""
import os
import jwt
import stripe
import httpx
import asyncio
import logging
import bcrypt
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
router = APIRouter(prefix="/api/brigade", tags=["brigade"])
security = HTTPBearer()

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


# ═══════════════════════════════════════════════════════════════════════════════
# PRICING & ALLOCATIONS
# ═══════════════════════════════════════════════════════════════════════════════

BRIGADE_PRICING = {
    "free":   {"vacancy_scan": 0.50, "candidate_search": 1.00, "dossier": 15.00},
    "scout":  {"vacancy_scan": 0.25, "candidate_search": 0.50, "dossier":  7.00},
    "agency": {"vacancy_scan": 0.15, "candidate_search": 0.30, "dossier":  5.00},
}

BRIGADE_ALLOCATIONS = {
    "free":   {"vacancy_scans": 5,   "candidate_searches": 0,   "dossiers": 0},
    "scout":  {"vacancy_scans": 50,  "candidate_searches": 20,  "dossiers": 10},
    "agency": {"vacancy_scans": 200, "candidate_searches": 100, "dossiers": 30},
}

SCAN_ROLES = {
    "gm": {
        "title": "General Manager",
        "search_terms": ['"General Manager"', '"GM"'],
        "job_titles": ["general manager", "gm", "restaurant manager", "store manager"]
    },
    "chef": {
        "title": "Executive Chef / Head Chef",
        "search_terms": ['"Executive Chef"', '"Head Chef"', '"Chef de Cuisine"'],
        "job_titles": ["executive chef", "head chef", "chef de cuisine", "kitchen director"]
    },
    "bar": {
        "title": "Bar Manager / Beverage Director",
        "search_terms": ['"Bar Manager"', '"Beverage Director"', '"Beverage Manager"'],
        "job_titles": ["bar manager", "beverage director", "beverage manager"]
    },
    "kitchen": {
        "title": "Kitchen Manager",
        "search_terms": ['"Kitchen Manager"', '"Back of House Manager"', '"BOH Manager"'],
        "job_titles": ["kitchen manager", "back of house manager", "boh manager"]
    },
    "foh_manager": {
        "title": "Front of House Manager",
        "search_terms": ['"FOH Manager"', '"Front of House Manager"', '"Dining Room Manager"'],
        "job_titles": ["foh manager", "front of house manager", "dining room manager"]
    }
}


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_brigade_token(user: dict) -> str:
    payload = {
        "brigade_user_id": str(user["id"]),
        "email": user["email"],
        "full_name": user.get("full_name"),
        "plan": user.get("plan", "free"),
        "plan_status": user.get("plan_status", "active"),
        "portal_access": "brigade",
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_brigade_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    try:
        payload = jwt.decode(
            credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
        if payload.get("portal_access") != "brigade":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


def get_scan_cost(current_user: dict) -> float:
    plan = current_user.get("plan", "free")
    return BRIGADE_PRICING.get(plan, BRIGADE_PRICING["free"])["vacancy_scan"]

def get_candidate_search_cost(current_user: dict) -> float:
    plan = current_user.get("plan", "free")
    return BRIGADE_PRICING.get(plan, BRIGADE_PRICING["free"])["candidate_search"]

def get_monthly_usage(supabase, user_id: str, transaction_type: str) -> int:
    """Count how many times this user has used a feature this month."""
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = supabase.table("brigade_credit_transactions") \
        .select("id", count="exact") \
        .eq("user_id", user_id) \
        .eq("transaction_type", transaction_type) \
        .gte("created_at", month_start.isoformat()) \
        .execute()
    return result.count or 0


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class BrigadeRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    company: Optional[str] = None

class BrigadeLoginRequest(BaseModel):
    email: EmailStr
    password: str

class BrigadeVacancyScanRequest(BaseModel):
    states: Optional[List[str]] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    county: Optional[str] = None
    categories: Optional[List[str]] = None
    role_types: Optional[List[str]] = None  # ['gm', 'chef', 'bar']

class BrigadeCandidateSearchRequest(BaseModel):
    role: str = "gm"
    city: str
    state: str

class BrigadeCandidateAddRequest(BaseModel):
    full_name: str
    current_role: Optional[str] = None
    current_employer: Optional[str] = None
    current_city: Optional[str] = None
    current_state: Optional[str] = None
    linkedin_url: Optional[str] = None
    signal_type: str = "manual"
    signal_details: Optional[str] = None
    years_experience: Optional[int] = None
    specialties: Optional[List[str]] = None
    estimated_salary_range: Optional[str] = None

class BrigadePlacementCreateRequest(BaseModel):
    vacancy_id: Optional[str] = None
    candidate_id: Optional[str] = None
    business_name: str
    role_title: str
    candidate_name: Optional[str] = None
    estimated_fee: Optional[float] = 0
    notes: Optional[str] = None

class BrigadePlacementUpdateRequest(BaseModel):
    stage: Optional[str] = None
    candidate_name: Optional[str] = None
    estimated_fee: Optional[float] = None
    actual_fee: Optional[float] = None
    notes: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/register")
async def brigade_register(data: BrigadeRegisterRequest):
    supabase = get_supabase()

    existing = supabase.table("brigade_users") \
        .select("id") \
        .eq("email", data.email.lower()) \
        .execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = supabase.table("brigade_users").insert({
        "email": data.email.lower(),
        "password_hash": hash_password(data.password),
        "full_name": data.full_name,
        "company": data.company,
        "credit_balance": 0,
        "plan": "free",
        "plan_status": "active",
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    user_data = user.data[0]
    token = create_brigade_token(user_data)

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user_data["id"],
            "email": user_data["email"],
            "full_name": user_data["full_name"],
            "company": user_data.get("company"),
            "plan": "free",
            "credit_balance": 0
        }
    }


@router.post("/login")
async def brigade_login(data: BrigadeLoginRequest):
    supabase = get_supabase()

    result = supabase.table("brigade_users") \
        .select("*") \
        .eq("email", data.email.lower()) \
        .execute()
    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = result.data[0]
    if not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    supabase.table("brigade_users").update({
        "last_login": datetime.utcnow().isoformat()
    }).eq("id", user["id"]).execute()

    token = create_brigade_token(user)
    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user.get("full_name"),
            "company": user.get("company"),
            "plan": user.get("plan", "free"),
            "plan_status": user.get("plan_status", "active"),
            "credit_balance": float(user.get("credit_balance", 0)),
            "specialties": user.get("specialties"),
            "territories": user.get("territories")
        }
    }


@router.get("/me")
async def brigade_me(current_user: dict = Depends(verify_brigade_token)):
    supabase = get_supabase()
    result = supabase.table("brigade_users") \
        .select("id, email, full_name, company, plan, plan_status, credit_balance, specialties, territories, created_at") \
        .eq("id", current_user["brigade_user_id"]) \
        .single() \
        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")
    user = result.data
    user["credit_balance"] = float(user.get("credit_balance", 0))
    return {"success": True, "user": user}


# ═══════════════════════════════════════════════════════════════════════════════
# VACANCY SCAN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

VACANCY_SCAN_PROMPT = """You are a restaurant industry research assistant. Your ONLY job is to determine if this restaurant currently has a {role_title} vacancy or leadership transition in that role.

Search job boards (Indeed, LinkedIn, Google Jobs) for current job postings matching these titles: {job_titles}. Search Google for any news about management changes.

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


@router.post("/vacancies/scan")
async def brigade_start_scan(
    data: BrigadeVacancyScanRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_brigade_token)
):
    """Start a leadership vacancy scan. Deducts credits, runs in background."""
    supabase = get_supabase()
    user_id = current_user["brigade_user_id"]

    if not data.states and not data.city and not data.zip_code and not data.county:
        raise HTTPException(status_code=400, detail="Select at least one geographic filter")

    role_types = data.role_types or ["gm"]
    for rt in role_types:
        if rt not in SCAN_ROLES:
            raise HTTPException(status_code=400, detail=f"Invalid role type: {rt}")

    # Count matching restaurants via Mise's existing RPC
    count_result = supabase.rpc("proof_scan_count", {
        "p_states": data.states if data.states else None,
        "p_city": data.city if data.city else None,
        "p_zip": data.zip_code if data.zip_code else None,
        "p_county": data.county if data.county else None,
        "p_categories": data.categories if data.categories else None,
        "p_stale_days": None
    }).execute()
    count = count_result.data if isinstance(count_result.data, int) else 0
    if count == 0:
        raise HTTPException(status_code=400, detail="No restaurants match these filters")
    if count > 2000:
        raise HTTPException(status_code=400, detail=f"Too many restaurants ({count}). Narrow your filters to under 2,000.")

    # Calculate cost (scans per role type per restaurant)
    total_scans = count * len(role_types)
    cost_per = get_scan_cost(current_user)
    total_cost = round(total_scans * cost_per, 2)

    # Check balance
    user = supabase.table("brigade_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()
    balance = float(user.data.get("credit_balance", 0))

    # Check free tier allocation
    plan = current_user.get("plan", "free")
    alloc = BRIGADE_ALLOCATIONS.get(plan, BRIGADE_ALLOCATIONS["free"])
    monthly_scan_usage = get_monthly_usage(supabase, user_id, "vacancy_scan")

    if plan == "free":
        if monthly_scan_usage + total_scans > alloc["vacancy_scans"]:
            remaining = max(0, alloc["vacancy_scans"] - monthly_scan_usage)
            raise HTTPException(
                status_code=403,
                detail=f"Free tier allows {alloc['vacancy_scans']} scans/month. You have {remaining} remaining. Upgrade to Scout for more."
            )
        total_cost = 0  # Free tier scans don't cost credits
    elif total_cost > 0 and balance < total_cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. Need ${total_cost:.2f}, have ${balance:.2f}"
        )

    # Deduct credits (if paid tier)
    new_balance = balance
    if total_cost > 0:
        new_balance = round(balance - total_cost, 2)
        supabase.table("brigade_users").update({"credit_balance": new_balance}).eq("id", user_id).execute()
        supabase.table("brigade_credit_transactions").insert({
            "user_id": user_id,
            "transaction_type": "vacancy_scan",
            "amount": -total_cost,
            "balance_after": new_balance,
            "description": f"Vacancy Scan: {count} restaurants x {len(role_types)} roles",
            "created_at": datetime.utcnow().isoformat()
        }).execute()

    # Create scan record
    filters = {
        "states": data.states,
        "city": data.city,
        "zip_code": data.zip_code,
        "county": data.county,
        "categories": data.categories,
        "role_types": role_types
    }
    scan = supabase.table("brigade_scans").insert({
        "user_id": user_id,
        "scan_type": "vacancy",
        "filters": filters,
        "role_type": role_types[0] if len(role_types) == 1 else "multi",
        "total_count": total_scans,
        "total_cost": total_cost,
        "cost_per_record": cost_per,
        "status": "running",
        "started_at": datetime.utcnow().isoformat()
    }).execute()
    scan_id = scan.data[0]["id"]

    background_tasks.add_task(
        _run_vacancy_scan_background,
        scan_id, user_id, filters, count, role_types
    )

    return {
        "success": True,
        "scan_id": scan_id,
        "total_count": total_scans,
        "total_cost": total_cost,
        "balance_remaining": new_balance
    }


@router.get("/vacancies/scan/{scan_id}/status")
async def brigade_scan_status(
    scan_id: str,
    current_user: dict = Depends(verify_brigade_token)
):
    """Poll scan progress."""
    supabase = get_supabase()
    scan = supabase.table("brigade_scans") \
        .select("*") \
        .eq("id", scan_id) \
        .eq("user_id", current_user["brigade_user_id"]) \
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


@router.get("/vacancies/scan/{scan_id}/results")
async def brigade_scan_results(
    scan_id: str,
    current_user: dict = Depends(verify_brigade_token)
):
    """Get scan results, vacancies first."""
    supabase = get_supabase()
    scan = supabase.table("brigade_scans") \
        .select("id, user_id") \
        .eq("id", scan_id) \
        .eq("user_id", current_user["brigade_user_id"]) \
        .execute()
    if not scan.data:
        raise HTTPException(status_code=404, detail="Scan not found")

    results = supabase.table("brigade_vacancies") \
        .select("*") \
        .eq("scan_id", scan_id) \
        .order("vacancy_detected", desc=True) \
        .order("scanned_at") \
        .execute()

    return {"success": True, "results": results.data}


@router.get("/vacancies")
async def brigade_list_vacancies(
    role_type: Optional[str] = None,
    state: Optional[str] = None,
    confidence: Optional[str] = None,
    max_age_days: Optional[int] = None,
    page: int = 1,
    per_page: int = 25,
    current_user: dict = Depends(verify_brigade_token)
):
    """List detected vacancies with filters. Only shows vacancy_detected=true."""
    supabase = get_supabase()
    query = supabase.table("brigade_vacancies") \
        .select("*", count="exact") \
        .eq("vacancy_detected", True)

    if role_type:
        query = query.eq("role_type", role_type)
    if state:
        query = query.eq("state", state)
    if confidence:
        query = query.eq("confidence", confidence)
    if max_age_days:
        cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
        query = query.gte("scanned_at", cutoff)

    offset = (page - 1) * per_page
    result = query.order("scanned_at", desc=True) \
        .range(offset, offset + per_page - 1) \
        .execute()

    return {
        "success": True,
        "vacancies": result.data,
        "total": result.count or 0,
        "page": page,
        "per_page": per_page
    }


@router.get("/vacancies/{vacancy_id}")
async def brigade_vacancy_detail(
    vacancy_id: str,
    current_user: dict = Depends(verify_brigade_token)
):
    """Get full vacancy detail."""
    supabase = get_supabase()
    result = supabase.table("brigade_vacancies") \
        .select("*") \
        .eq("id", vacancy_id) \
        .single() \
        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    return {"success": True, "vacancy": result.data}


# ═══════════════════════════════════════════════════════════════════════════════
# VACANCY SCAN BACKGROUND TASK
# ═══════════════════════════════════════════════════════════════════════════════

async def _run_vacancy_scan_background(
    scan_id: str, user_id: str, filters: dict, total_count: int, role_types: list
):
    """Background task: scan restaurants for leadership vacancies."""
    supabase = get_supabase()

    try:
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

            for role_key in role_types:
                role_config = SCAN_ROLES.get(role_key)
                if not role_config:
                    continue

                signal = {
                    "scan_id": scan_id,
                    "prospect_id": prospect["id"],
                    "business_name": name,
                    "address": prospect.get("premise_address1"),
                    "city": city,
                    "state": state,
                    "zip": prospect.get("premise_zip"),
                    "category": prospect.get("business_category"),
                    "role_type": role_key,
                    "role_title": role_config["title"],
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
                                "messages": [{
                                    "role": "user",
                                    "content": VACANCY_SCAN_PROMPT.format(
                                        role_title=role_config["title"],
                                        job_titles=", ".join(role_config["job_titles"]),
                                        name=name,
                                        city=city,
                                        state=state
                                    )
                                }],
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
                    logger.warning(f"Brigade vacancy scan error for {name} ({role_key}): {e}")
                    signal["signal_detail"] = "Scan error, skipped"

                supabase.table("brigade_vacancies").insert(signal).execute()

                scanned += 1
                if signal["vacancy_detected"]:
                    vacancies += 1

                if scanned % 10 == 0 or scanned == total_count:
                    supabase.table("brigade_scans").update({
                        "scanned_count": scanned,
                        "vacancy_count": vacancies
                    }).eq("id", scan_id).execute()

        supabase.table("brigade_scans").update({
            "status": "complete",
            "scanned_count": scanned,
            "vacancy_count": vacancies,
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", scan_id).execute()

        logger.info(f"Brigade scan {scan_id} complete: {scanned} scanned, {vacancies} vacancies")

    except Exception as e:
        logger.error(f"Brigade scan {scan_id} failed: {e}")
        supabase.table("brigade_scans").update({
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", scan_id).execute()


# ═══════════════════════════════════════════════════════════════════════════════
# CANDIDATE SEARCH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

CANDIDATE_SEARCH_PROMPT = """Analyze these search results for restaurant professionals in or near {city}, {state} who appear to be seeking new {role_title} opportunities.

For each person found, return a JSON array:
[{{
    "full_name": "name",
    "current_role": "their current title",
    "current_employer": "restaurant name or unknown",
    "signal_type": "open_to_work" or "resume_posted" or "social_signal",
    "signal_source": "linkedin" or "indeed" or "culinary_agents" or "other",
    "signal_details": "what specifically suggests they are looking",
    "confidence": "high"/"medium"/"low",
    "linkedin_url": "url if found, else null"
}}]

Only include people who show genuine signals of seeking new opportunities.
Do not include people who are simply mentioned in job postings.
If no candidates are found, return an empty array: []

Search results:
{results}"""


@router.post("/candidates/search")
async def brigade_search_candidates(
    data: BrigadeCandidateSearchRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_brigade_token)
):
    """Search for passive candidate signals via web search."""
    supabase = get_supabase()
    user_id = current_user["brigade_user_id"]
    plan = current_user.get("plan", "free")

    # Free tier can't search candidates
    alloc = BRIGADE_ALLOCATIONS.get(plan, BRIGADE_ALLOCATIONS["free"])
    if alloc["candidate_searches"] == 0:
        raise HTTPException(
            status_code=403,
            detail="Candidate search requires a Scout or Agency plan."
        )

    # Check monthly allocation
    monthly_usage = get_monthly_usage(supabase, user_id, "candidate_search")
    if monthly_usage >= alloc["candidate_searches"]:
        raise HTTPException(
            status_code=403,
            detail=f"Candidate search limit reached ({monthly_usage}/{alloc['candidate_searches']} this month)."
        )

    # Deduct cost
    cost = get_candidate_search_cost(current_user)
    user = supabase.table("brigade_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()
    balance = float(user.data.get("credit_balance", 0))

    if cost > 0 and balance < cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. Need ${cost:.2f}, have ${balance:.2f}"
        )

    new_balance = round(balance - cost, 2)
    if cost > 0:
        supabase.table("brigade_users").update({"credit_balance": new_balance}).eq("id", user_id).execute()

    role_config = SCAN_ROLES.get(data.role, SCAN_ROLES["gm"])

    # Create search tracker
    search_record = supabase.table("brigade_candidate_searches").insert({
        "user_id": user_id,
        "filters": {"role": data.role, "city": data.city, "state": data.state},
        "status": "running",
        "total_cost": cost,
        "started_at": datetime.utcnow().isoformat()
    }).execute()
    search_id = search_record.data[0]["id"]

    # Log credit transaction
    if cost > 0:
        supabase.table("brigade_credit_transactions").insert({
            "user_id": user_id,
            "transaction_type": "candidate_search",
            "amount": -cost,
            "balance_after": new_balance,
            "description": f"Candidate Search: {role_config['title']} in {data.city}, {data.state}",
            "created_at": datetime.utcnow().isoformat()
        }).execute()

    background_tasks.add_task(
        _search_candidates_background,
        search_id, user_id, data.role, data.city, data.state
    )

    return {
        "success": True,
        "search_id": search_id,
        "cost": cost,
        "balance_remaining": new_balance
    }


@router.get("/candidates/search/{search_id}/status")
async def brigade_candidate_search_status(
    search_id: str,
    current_user: dict = Depends(verify_brigade_token)
):
    supabase = get_supabase()
    search = supabase.table("brigade_candidate_searches") \
        .select("*") \
        .eq("id", search_id) \
        .eq("user_id", current_user["brigade_user_id"]) \
        .single() \
        .execute()
    if not search.data:
        raise HTTPException(status_code=404, detail="Search not found")

    return {
        "success": True,
        "status": search.data["status"],
        "result_count": search.data["result_count"],
        "completed_at": search.data.get("completed_at")
    }


@router.get("/candidates/search/{search_id}/results")
async def brigade_candidate_search_results(
    search_id: str,
    current_user: dict = Depends(verify_brigade_token)
):
    supabase = get_supabase()
    search = supabase.table("brigade_candidate_searches") \
        .select("id, user_id") \
        .eq("id", search_id) \
        .eq("user_id", current_user["brigade_user_id"]) \
        .execute()
    if not search.data:
        raise HTTPException(status_code=404, detail="Search not found")

    results = supabase.table("brigade_candidates") \
        .select("*") \
        .eq("search_id", search_id) \
        .order("created_at", desc=True) \
        .execute()

    return {"success": True, "candidates": results.data}


@router.get("/candidates")
async def brigade_list_candidates(
    signal_type: Optional[str] = None,
    state: Optional[str] = None,
    page: int = 1,
    per_page: int = 25,
    current_user: dict = Depends(verify_brigade_token)
):
    """List all discovered candidates with filters."""
    supabase = get_supabase()
    query = supabase.table("brigade_candidates") \
        .select("*", count="exact")

    if signal_type:
        query = query.eq("signal_type", signal_type)
    if state:
        query = query.eq("current_state", state)

    offset = (page - 1) * per_page
    result = query.order("created_at", desc=True) \
        .range(offset, offset + per_page - 1) \
        .execute()

    return {
        "success": True,
        "candidates": result.data,
        "total": result.count or 0,
        "page": page,
        "per_page": per_page
    }


@router.post("/candidates")
async def brigade_add_candidate(
    data: BrigadeCandidateAddRequest,
    current_user: dict = Depends(verify_brigade_token)
):
    """Manually add a candidate the recruiter already knows."""
    supabase = get_supabase()
    candidate = supabase.table("brigade_candidates").insert({
        "full_name": data.full_name,
        "candidate_role": data.current_role,
        "current_employer": data.current_employer,
        "current_city": data.current_city,
        "current_state": data.current_state,
        "linkedin_url": data.linkedin_url,
        "signal_type": data.signal_type or "manual",
        "signal_source": "manual",
        "signal_details": data.signal_details,
        "confidence": "high",
        "years_experience": data.years_experience,
        "specialties": data.specialties,
        "estimated_salary_range": data.estimated_salary_range,
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    return {"success": True, "candidate": candidate.data[0]}


# ═══════════════════════════════════════════════════════════════════════════════
# CANDIDATE SEARCH BACKGROUND TASK
# ═══════════════════════════════════════════════════════════════════════════════

async def _search_candidates_background(
    search_id: str, user_id: str, role: str, city: str, state: str
):
    """Background task: search for passive candidate signals via Anthropic web search."""
    supabase = get_supabase()
    role_config = SCAN_ROLES.get(role, SCAN_ROLES["gm"])

    try:
        search_query = (
            f'restaurant {role_config["title"]} '
            f'("open to work" OR "seeking new opportunity" OR "available" OR "looking for") '
            f'"{city}" "{state}"'
        )

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
                    "max_tokens": 1000,
                    "messages": [{
                        "role": "user",
                        "content": f"""Search for restaurant professionals who are {role_config['title']}s in or near {city}, {state} who appear to be seeking new opportunities. Look on LinkedIn, Indeed, Culinary Agents, and Poached Jobs.

For each person found, return a JSON array:
[{{
    "full_name": "name",
    "current_role": "their current title",
    "current_employer": "restaurant name or unknown",
    "signal_type": "open_to_work" or "resume_posted" or "social_signal",
    "signal_source": "linkedin" or "indeed" or "culinary_agents" or "other",
    "signal_details": "what specifically suggests they are looking",
    "confidence": "high"/"medium"/"low",
    "linkedin_url": "url if found, else null"
}}]

Only include people who show genuine signals of seeking new opportunities.
Do not include people who are simply mentioned in job postings as employers.
If no candidates are found, return an empty array: []"""
                    }],
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}]
                },
                timeout=60.0
            )
            resp_data = resp.json()

        candidates_found = 0
        if resp.status_code == 200:
            text = ""
            for block in resp_data.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")

            if text:
                cleaned = text.strip()
                first_bracket = cleaned.find("[")
                last_bracket = cleaned.rfind("]")
                if first_bracket != -1 and last_bracket > first_bracket:
                    try:
                        parsed = json.loads(cleaned[first_bracket:last_bracket + 1])
                        if isinstance(parsed, list):
                            for candidate in parsed:
                                supabase.table("brigade_candidates").insert({
                                    "search_id": search_id,
                                    "full_name": candidate.get("full_name", "Unknown"),
                                    "candidate_role": candidate.get("current_role"),
                                    "current_employer": candidate.get("current_employer"),
                                    "current_city": city,
                                    "current_state": state,
                                    "linkedin_url": candidate.get("linkedin_url"),
                                    "signal_type": candidate.get("signal_type", "unknown"),
                                    "signal_source": candidate.get("signal_source", "other"),
                                    "signal_details": candidate.get("signal_details"),
                                    "confidence": candidate.get("confidence", "medium"),
                                    "created_at": datetime.utcnow().isoformat()
                                }).execute()
                                candidates_found += 1
                    except (json.JSONDecodeError, ValueError):
                        pass

        supabase.table("brigade_candidate_searches").update({
            "status": "complete",
            "result_count": candidates_found,
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", search_id).execute()

        logger.info(f"Brigade candidate search {search_id} complete: {candidates_found} candidates found")

    except Exception as e:
        logger.error(f"Brigade candidate search {search_id} failed: {e}")
        supabase.table("brigade_candidate_searches").update({
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", search_id).execute()


# ═══════════════════════════════════════════════════════════════════════════════
# PLACEMENT PIPELINE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

VALID_STAGES = ["identified", "outreach", "submitted", "interviewing", "offered", "placed", "lost"]

@router.post("/placements")
async def brigade_create_placement(
    data: BrigadePlacementCreateRequest,
    current_user: dict = Depends(verify_brigade_token)
):
    supabase = get_supabase()
    placement = supabase.table("brigade_placements").insert({
        "user_id": current_user["brigade_user_id"],
        "vacancy_id": data.vacancy_id,
        "candidate_id": data.candidate_id,
        "business_name": data.business_name,
        "role_title": data.role_title,
        "candidate_name": data.candidate_name,
        "stage": "identified",
        "estimated_fee": data.estimated_fee or 0,
        "notes": data.notes,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }).execute()
    return {"success": True, "placement": placement.data[0]}


@router.get("/placements")
async def brigade_list_placements(
    stage: Optional[str] = None,
    current_user: dict = Depends(verify_brigade_token)
):
    supabase = get_supabase()
    query = supabase.table("brigade_placements") \
        .select("*") \
        .eq("user_id", current_user["brigade_user_id"])

    if stage and stage in VALID_STAGES:
        query = query.eq("stage", stage)

    result = query.order("updated_at", desc=True).execute()
    return {"success": True, "placements": result.data}


@router.patch("/placements/{placement_id}")
async def brigade_update_placement(
    placement_id: str,
    data: BrigadePlacementUpdateRequest,
    current_user: dict = Depends(verify_brigade_token)
):
    supabase = get_supabase()

    existing = supabase.table("brigade_placements") \
        .select("id") \
        .eq("id", placement_id) \
        .eq("user_id", current_user["brigade_user_id"]) \
        .execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Placement not found")

    updates = {"updated_at": datetime.utcnow().isoformat()}
    if data.stage is not None:
        if data.stage not in VALID_STAGES:
            raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {', '.join(VALID_STAGES)}")
        updates["stage"] = data.stage
    if data.candidate_name is not None:
        updates["candidate_name"] = data.candidate_name
    if data.estimated_fee is not None:
        updates["estimated_fee"] = data.estimated_fee
    if data.actual_fee is not None:
        updates["actual_fee"] = data.actual_fee
    if data.notes is not None:
        updates["notes"] = data.notes

    result = supabase.table("brigade_placements").update(updates).eq("id", placement_id).execute()
    return {"success": True, "placement": result.data[0]}


@router.delete("/placements/{placement_id}")
async def brigade_delete_placement(
    placement_id: str,
    current_user: dict = Depends(verify_brigade_token)
):
    supabase = get_supabase()
    existing = supabase.table("brigade_placements") \
        .select("id") \
        .eq("id", placement_id) \
        .eq("user_id", current_user["brigade_user_id"]) \
        .execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Placement not found")

    supabase.table("brigade_placements").delete().eq("id", placement_id).execute()
    return {"success": True, "deleted": True}


# ═══════════════════════════════════════════════════════════════════════════════
# CREDITS & BILLING
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/credits")
async def brigade_credits(current_user: dict = Depends(verify_brigade_token)):
    """Get credit balance and transaction history."""
    supabase = get_supabase()
    user_id = current_user["brigade_user_id"]

    user = supabase.table("brigade_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()

    transactions = supabase.table("brigade_credit_transactions") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .limit(50) \
        .execute()

    return {
        "success": True,
        "balance": float(user.data.get("credit_balance", 0)),
        "transactions": transactions.data
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SCANS HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/scans/mine")
async def brigade_my_scans(current_user: dict = Depends(verify_brigade_token)):
    """List all scans for this user."""
    supabase = get_supabase()
    result = supabase.table("brigade_scans") \
        .select("*") \
        .eq("user_id", current_user["brigade_user_id"]) \
        .order("created_at", desc=True) \
        .execute()
    return {"success": True, "scans": result.data}


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD STATS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def brigade_dashboard(current_user: dict = Depends(verify_brigade_token)):
    """Dashboard summary: vacancy counts, placement stats, pipeline value."""
    supabase = get_supabase()
    user_id = current_user["brigade_user_id"]

    # Active vacancies (detected in last 90 days)
    cutoff_90 = (datetime.utcnow() - timedelta(days=90)).isoformat()
    vacancies = supabase.table("brigade_vacancies") \
        .select("id", count="exact") \
        .eq("vacancy_detected", True) \
        .gte("scanned_at", cutoff_90) \
        .execute()
    total_vacancies = vacancies.count or 0

    # New this week
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    new_this_week = supabase.table("brigade_vacancies") \
        .select("id", count="exact") \
        .eq("vacancy_detected", True) \
        .gte("scanned_at", week_ago) \
        .execute()
    new_count = new_this_week.count or 0

    # Open placements
    open_placements = supabase.table("brigade_placements") \
        .select("id, estimated_fee", count="exact") \
        .eq("user_id", user_id) \
        .not_.in_("stage", ["placed", "lost"]) \
        .execute()
    open_count = open_placements.count or 0
    pipeline_value = sum(float(p.get("estimated_fee", 0)) for p in (open_placements.data or []))

    # Placed this month
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    placed_this_month = supabase.table("brigade_placements") \
        .select("id, actual_fee", count="exact") \
        .eq("user_id", user_id) \
        .eq("stage", "placed") \
        .gte("updated_at", month_start.isoformat()) \
        .execute()
    placed_count = placed_this_month.count or 0
    revenue_this_month = sum(float(p.get("actual_fee", 0) or 0) for p in (placed_this_month.data or []))

    # Recent vacancies (10 most recent)
    recent = supabase.table("brigade_vacancies") \
        .select("id, business_name, city, state, role_type, role_title, confidence, source, signal_detail, scanned_at") \
        .eq("vacancy_detected", True) \
        .order("scanned_at", desc=True) \
        .limit(10) \
        .execute()

    return {
        "success": True,
        "stats": {
            "active_vacancies": total_vacancies,
            "new_this_week": new_count,
            "open_placements": open_count,
            "pipeline_value": pipeline_value,
            "placed_this_month": placed_count,
            "revenue_this_month": revenue_this_month
        },
        "recent_vacancies": recent.data
    }

STRIPE_WEBHOOK_SECRET_BRIGADE = os.environ.get("STRIPE_WEBHOOK_SECRET_BRIGADE")
 
BRIGADE_PRICE_IDS = {
    "scout":      os.environ.get("STRIPE_PRICE_BRIGADE_SCOUT"),
    "agency":     os.environ.get("STRIPE_PRICE_BRIGADE_AGENCY"),
    "credits_10": os.environ.get("STRIPE_PRICE_BRIGADE_CREDITS_10"),
    "credits_25": os.environ.get("STRIPE_PRICE_BRIGADE_CREDITS_25"),
    "credits_50": os.environ.get("STRIPE_PRICE_BRIGADE_CREDITS_50"),
}
 
BRIGADE_CREDIT_PACKS = {
    "credits_10":  10.00,
    "credits_25":  25.00,
    "credits_50":  50.00,
}
 
BRIGADE_SUCCESS_URL = "https://brigade.en-place.ai/credits?session_id={CHECKOUT_SESSION_ID}"
BRIGADE_CANCEL_URL  = "https://brigade.en-place.ai/credits"
 
 
# ── Pydantic models for Stripe requests ──
 
class BrigadeCreditsRequest(BaseModel):
    pack: str  # "credits_10", "credits_25", "credits_50"
 
class BrigadeSubscriptionRequest(BaseModel):
    plan: str  # "scout", "agency"
 
 
# ═══════════════════════════════════════════════════════════════════════════════
# STRIPE CHECKOUT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════
 
@router.post("/checkout/credits")
async def brigade_buy_credits(
    data: BrigadeCreditsRequest,
    current_user: dict = Depends(verify_brigade_token)
):
    """Create a Stripe checkout session for credit top-up."""
    if data.pack not in BRIGADE_CREDIT_PACKS:
        raise HTTPException(status_code=400, detail="Invalid credit pack")
 
    price_id = BRIGADE_PRICE_IDS.get(data.pack)
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
            success_url=BRIGADE_SUCCESS_URL,
            cancel_url=BRIGADE_CANCEL_URL,
            metadata={
                "brigade_user_id": current_user["brigade_user_id"],
                "credit_pack": data.pack,
                "credit_amount": str(BRIGADE_CREDIT_PACKS[data.pack])
            }
        )
        return {"success": True, "session_id": session.id, "url": session.url}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))
 
 
@router.post("/checkout/subscription")
async def brigade_subscribe(
    data: BrigadeSubscriptionRequest,
    current_user: dict = Depends(verify_brigade_token)
):
    """Create a Stripe checkout session for a subscription plan."""
    if data.plan not in ("scout", "agency"):
        raise HTTPException(status_code=400, detail="Invalid plan")
 
    price_id = BRIGADE_PRICE_IDS.get(data.plan)
    if not price_id:
        raise HTTPException(status_code=500, detail="Plan price not configured")
 
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=BRIGADE_SUCCESS_URL,
            cancel_url=BRIGADE_CANCEL_URL,
            allow_promotion_codes=True,
            billing_address_collection="required",
            metadata={
                "brigade_user_id": current_user["brigade_user_id"],
                "brigade_plan": data.plan
            },
            subscription_data={
                "metadata": {
                    "brigade_user_id": current_user["brigade_user_id"],
                    "brigade_plan": data.plan
                }
            }
        )
        return {"success": True, "session_id": session.id, "url": session.url}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))
 
 
# ═══════════════════════════════════════════════════════════════════════════════
# STRIPE WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════════
 
@router.post("/stripe/webhook")
async def brigade_stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature")
):
    """Handle Stripe webhook events for Brigade Intelligence."""
    payload = await request.body()
 
    if STRIPE_WEBHOOK_SECRET_BRIGADE and stripe_signature:
        try:
            event = stripe.Webhook.construct_event(
                payload, stripe_signature, STRIPE_WEBHOOK_SECRET_BRIGADE
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
 
    event_type = event.type
    data = event.data.object
    logger.info(f"Brigade Stripe webhook: {event_type}")
 
    if event_type == "checkout.session.completed":
        await _handle_brigade_checkout(data)
    elif event_type == "customer.subscription.deleted":
        await _handle_brigade_subscription_cancelled(data)
    elif event_type == "invoice.payment_failed":
        await _handle_brigade_payment_failed(data)
 
    return {"received": True}
 
 
async def _handle_brigade_checkout(session):
    """Handle completed checkout: credit purchase or subscription activation."""
    supabase = get_supabase()
    meta = session.metadata or {}
    user_id = meta.get("brigade_user_id")
 
    if not user_id:
        logger.error("Brigade checkout with no brigade_user_id in metadata")
        return
 
    try:
        # Credit pack purchase
        if meta.get("credit_pack"):
            credit_amount = float(meta.get("credit_amount", 0))
            if credit_amount > 0:
                user = supabase.table("brigade_users") \
                    .select("credit_balance") \
                    .eq("id", user_id) \
                    .single() \
                    .execute()
 
                current_balance = float(user.data.get("credit_balance", 0))
                new_balance = current_balance + credit_amount
 
                supabase.table("brigade_users").update({
                    "credit_balance": new_balance
                }).eq("id", user_id).execute()
 
                supabase.table("brigade_credit_transactions").insert({
                    "user_id": user_id,
                    "transaction_type": "purchase",
                    "amount": credit_amount,
                    "balance_after": new_balance,
                    "description": f"Credit purchase: ${credit_amount:.0f} pack",
                    "created_at": datetime.utcnow().isoformat()
                }).execute()
 
                logger.info(f"Brigade: Added ${credit_amount} credits to user {user_id}")
 
        # Subscription activation
        elif meta.get("brigade_plan"):
            plan = meta["brigade_plan"]
            supabase.table("brigade_users").update({
                "plan": plan,
                "plan_status": "active",
                "stripe_customer_id": session.customer,
                "stripe_subscription_id": session.subscription,
            }).eq("id", user_id).execute()
 
            logger.info(f"Brigade: Activated {plan} plan for user {user_id}")
 
    except Exception as e:
        logger.error(f"Error handling Brigade checkout: {e}")
 
 
async def _handle_brigade_subscription_cancelled(subscription):
    """Downgrade user to free when subscription is cancelled."""
    supabase = get_supabase()
    try:
        supabase.table("brigade_users").update({
            "plan": "free",
            "plan_status": "cancelled"
        }).eq("stripe_subscription_id", subscription.id).execute()
        logger.info(f"Brigade subscription cancelled: {subscription.id}")
    except Exception as e:
        logger.error(f"Error handling Brigade cancellation: {e}")
 
 
async def _handle_brigade_payment_failed(invoice):
    """Mark user as past_due when payment fails."""
    supabase = get_supabase()
    try:
        supabase.table("brigade_users").update({
            "plan_status": "past_due"
        }).eq("stripe_subscription_id", invoice.subscription).execute()
        logger.warning(f"Brigade payment failed: {invoice.subscription}")
    except Exception as e:
        logger.error(f"Error handling Brigade payment failure: {e}")
 
 
# ═══════════════════════════════════════════════════════════════════════════════
# DOSSIER — Recruiter-Framed Restaurant Intelligence
# ═══════════════════════════════════════════════════════════════════════════════
 
BRIGADE_DOSSIER_PROMPT = """You are a recruiting intelligence analyst for the hospitality industry.
Generate a detailed intelligence brief about this restaurant to help a recruiter place a leadership candidate.
 
Restaurant: {business_name}
Address: {address}
City: {city}, {state} {zip}
Category: {category}
License Status: {license_status}
 
Research this restaurant thoroughly using web search. Focus on information valuable to a recruiter:
 
Return ONLY this JSON structure:
{{
    "overview": "2-3 sentences about the restaurant concept, cuisine, price point, and reputation",
    "ownership": {{
        "owner_name": "name or null",
        "owner_linkedin": "url or null",
        "ownership_type": "independent / franchise / group / corporate",
        "parent_company": "name or null",
        "unit_count": "number of locations or null"
    }},
    "hiring_authority": {{
        "decision_maker": "who likely makes the hiring decision for leadership roles",
        "contact_approach": "recommended outreach strategy (email, LinkedIn, walk-in, referral)",
        "best_time": "suggested timing for outreach based on restaurant type"
    }},
    "vacancy_context": {{
        "urgency_level": "high / medium / low",
        "urgency_reasoning": "why this level based on available signals",
        "turnover_indicators": "any visible signs of instability (multiple postings, review complaints, etc.)",
        "recent_changes": "any leadership transitions, renovations, openings, closings"
    }},
    "compensation_context": {{
        "market_position": "upscale / mid-range / casual / fast-casual / QSR",
        "estimated_gm_salary": "range based on concept and market",
        "estimated_chef_salary": "range based on concept and market",
        "benefits_likely": "what benefits this type of establishment typically offers"
    }},
    "competitive_landscape": {{
        "nearby_competitors": ["list of 3-5 similar restaurants in the area"],
        "market_saturation": "high / moderate / low for this concept in this area",
        "staffing_difficulty": "how hard is it to recruit leadership in this market"
    }},
    "red_flags": ["any concerns a recruiter should know: bad reviews about management, legal issues, financial trouble"],
    "talking_points": ["3-5 specific things a recruiter can reference when pitching a candidate to this restaurant"]
}}"""
 
 
class BrigadeDossierRequest(BaseModel):
    prospect_id: str
 
 
@router.post("/dossier")
async def brigade_dossier(
    data: BrigadeDossierRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_brigade_token)
):
    """Generate a recruiter-framed dossier on a restaurant."""
    supabase = get_supabase()
    user_id = current_user["brigade_user_id"]
    plan = current_user.get("plan", "free")
 
    # Free tier can't generate dossiers
    alloc = BRIGADE_ALLOCATIONS.get(plan, BRIGADE_ALLOCATIONS["free"])
    if alloc["dossiers"] == 0:
        raise HTTPException(status_code=403, detail="Dossiers require a Scout or Agency plan.")
 
    # Check monthly allocation
    monthly_usage = get_monthly_usage(supabase, user_id, "dossier")
    if monthly_usage >= alloc["dossiers"]:
        raise HTTPException(
            status_code=403,
            detail=f"Dossier limit reached ({monthly_usage}/{alloc['dossiers']} this month)."
        )
 
    # Get cost
    pricing = BRIGADE_PRICING.get(plan, BRIGADE_PRICING["free"])
    cost = pricing["dossier"]
 
    # Check balance
    user = supabase.table("brigade_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()
    balance = float(user.data.get("credit_balance", 0))
 
    if cost > 0 and balance < cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. Need ${cost:.2f}, have ${balance:.2f}"
        )
 
    # Fetch prospect data from prospect_master (read-only, shared with Mise)
    prospect = supabase.table("prospect_master") \
        .select("*") \
        .eq("id", data.prospect_id) \
        .single() \
        .execute()
    if not prospect.data:
        raise HTTPException(status_code=404, detail="Restaurant not found")
 
    prospect_data = prospect.data
    business_name = prospect_data.get("dba_name") or prospect_data.get("legal_name") or "Unknown"
 
    # Deduct credits
    new_balance = round(balance - cost, 2)
    if cost > 0:
        supabase.table("brigade_users").update({"credit_balance": new_balance}).eq("id", user_id).execute()
        supabase.table("brigade_credit_transactions").insert({
            "user_id": user_id,
            "transaction_type": "dossier",
            "amount": -cost,
            "balance_after": new_balance,
            "description": f"Dossier: {business_name}",
            "created_at": datetime.utcnow().isoformat()
        }).execute()
 
    # Create dossier record
    dossier = supabase.table("brigade_dossiers").insert({
        "user_id": user_id,
        "prospect_id": data.prospect_id,
        "business_name": business_name,
        "status": "generating",
        "cost": cost,
        "created_at": datetime.utcnow().isoformat()
    }).execute()
    dossier_id = dossier.data[0]["id"]
 
    # Fire background task
    background_tasks.add_task(
        _generate_brigade_dossier_background,
        dossier_id, user_id, prospect_data
    )
 
    return {
        "success": True,
        "dossier_id": dossier_id,
        "cost": cost,
        "balance_remaining": new_balance
    }
 
 
@router.get("/dossier/{dossier_id}/status")
async def brigade_dossier_status(
    dossier_id: str,
    current_user: dict = Depends(verify_brigade_token)
):
    """Poll dossier generation status."""
    supabase = get_supabase()
    result = supabase.table("brigade_dossiers") \
        .select("id, status, business_name, content, created_at, completed_at") \
        .eq("id", dossier_id) \
        .eq("user_id", current_user["brigade_user_id"]) \
        .single() \
        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Dossier not found")
    return {"success": True, "dossier": result.data}
 
 
@router.get("/dossiers/mine")
async def brigade_my_dossiers(
    current_user: dict = Depends(verify_brigade_token)
):
    """List all dossiers for this user."""
    supabase = get_supabase()
    result = supabase.table("brigade_dossiers") \
        .select("id, business_name, status, cost, created_at, completed_at") \
        .eq("user_id", current_user["brigade_user_id"]) \
        .order("created_at", desc=True) \
        .limit(50) \
        .execute()
    return {"success": True, "dossiers": result.data}
 
 
async def _generate_brigade_dossier_background(
    dossier_id: str, user_id: str, prospect_data: dict
):
    """Background task: generate recruiter-framed dossier via Anthropic Sonnet + web search."""
    supabase = get_supabase()
 
    business_name = prospect_data.get("dba_name") or prospect_data.get("legal_name") or "Unknown"
 
    try:
        prompt = BRIGADE_DOSSIER_PROMPT.format(
            business_name=business_name,
            address=prospect_data.get("premise_address1", ""),
            city=prospect_data.get("premise_city", ""),
            state=prospect_data.get("premise_state", ""),
            zip=prospect_data.get("premise_zip", ""),
            category=prospect_data.get("business_category", ""),
            license_status=prospect_data.get("license_status", "")
        )
 
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
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}],
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}]
                },
                timeout=90.0
            )
            resp_data = resp.json()
 
        content = None
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
                        content = json.loads(cleaned[first_brace:last_brace + 1])
                    except json.JSONDecodeError:
                        try:
                            repaired = repair_json(cleaned[first_brace:last_brace + 1])
                            content = json.loads(repaired)
                        except Exception:
                            content = {"raw_text": text, "parse_error": True}
 
        if content:
            supabase.table("brigade_dossiers").update({
                "status": "complete",
                "content": content,
                "completed_at": datetime.utcnow().isoformat()
            }).eq("id", dossier_id).execute()
            logger.info(f"Brigade dossier {dossier_id} complete for {business_name}")
        else:
            supabase.table("brigade_dossiers").update({
                "status": "failed",
                "completed_at": datetime.utcnow().isoformat()
            }).eq("id", dossier_id).execute()
            logger.error(f"Brigade dossier {dossier_id} failed: no content parsed")
 
    except Exception as e:
        logger.error(f"Brigade dossier {dossier_id} failed: {e}")
        supabase.table("brigade_dossiers").update({
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", dossier_id).execute()