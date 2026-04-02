# routes/scout.py
"""
Scout Intelligence API
======================
Two-sided recruiting search engine. Type a role, type a location,
see who is hiring and who is available, in seconds.

Shares backend infrastructure with Mise/Brigade (same Heroku app, same Supabase)
but has its own auth (scout_users), its own branding, and its own pricing.
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

router = APIRouter(prefix="/api/scout", tags=["scout"])
security = HTTPBearer()

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ═══════════════════════════════════════════════════════════════════════════════
# PRICING & ALLOCATIONS
# ═══════════════════════════════════════════════════════════════════════════════

SCOUT_PRICING = {
    "free":    {"hiring": 0.50, "available": 0.50, "both": 0.75},
    "starter": {"hiring": 0.25, "available": 0.25, "both": 0.40},
    "pro":     {"hiring": 0.10, "available": 0.10, "both": 0.15},
}

SCOUT_ALLOCATIONS = {
    "free":    {"searches": 5},
    "starter": {"searches": 100},
    "pro":     {"searches": 500},
}

SCOUT_STRIPE_PRICE_IDS = {
    "starter": os.environ.get("STRIPE_PRICE_SCOUT_STARTER"),
    "pro":     os.environ.get("STRIPE_PRICE_SCOUT_PRO"),
    "credits_10": os.environ.get("STRIPE_PRICE_SCOUT_CREDITS_10"),
    "credits_25": os.environ.get("STRIPE_PRICE_SCOUT_CREDITS_25"),
}


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ScoutRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    company: Optional[str] = None

class ScoutLoginRequest(BaseModel):
    email: EmailStr
    password: str

class ScoutSearchRequest(BaseModel):
    role: str
    location: str

class ScoutSaveRequest(BaseModel):
    result_type: str           # 'hiring' or 'available'
    hiring_result_id: Optional[str] = None
    available_result_id: Optional[str] = None
    notes: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_scout_token(user: dict) -> str:
    payload = {
        "scout_user_id": str(user["id"]),
        "email": user["email"],
        "full_name": user.get("full_name"),
        "plan": user.get("plan", "free"),
        "plan_status": user.get("plan_status", "active"),
        "portal_access": "scout",
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_scout_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    try:
        payload = jwt.decode(
            credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
        if payload.get("portal_access") != "scout":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


# ═══════════════════════════════════════════════════════════════════════════════
# BILLING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_search_cost(plan: str, search_type: str) -> float:
    return SCOUT_PRICING.get(plan, SCOUT_PRICING["free"]).get(search_type, 0.50)

def get_monthly_search_count(supabase, user_id: str) -> int:
    """Count searches this calendar month."""
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = supabase.table("scout_searches") \
        .select("id", count="exact") \
        .eq("user_id", user_id) \
        .gte("created_at", month_start.isoformat()) \
        .execute()
    return result.count or 0

def check_and_charge(supabase, user_id: str, plan: str, cost: float, description: str):
    """
    Check if user has free allocation remaining. If not, charge credits.
    Raises HTTPException if insufficient balance.
    """
    allocation = SCOUT_ALLOCATIONS.get(plan, SCOUT_ALLOCATIONS["free"])["searches"]
    used = get_monthly_search_count(supabase, user_id)

    if used < allocation:
        # Still within free allocation, no charge
        return 0.0

    # Past allocation, charge credits
    user = supabase.table("scout_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()

    balance = float(user.data["credit_balance"] or 0)
    if balance < cost:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "insufficient_credits",
                "balance": balance,
                "cost": cost,
                "message": f"Insufficient credits. This search costs ${cost:.2f} but your balance is ${balance:.2f}."
            }
        )

    new_balance = balance - cost
    supabase.table("scout_users") \
        .update({"credit_balance": new_balance}) \
        .eq("id", user_id) \
        .execute()

    supabase.table("scout_credit_transactions").insert({
        "user_id": user_id,
        "transaction_type": description,
        "amount": -cost,
        "balance_after": new_balance,
        "description": f"Search: {description}",
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    return cost


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH ENGINE (Anthropic web search tool + Haiku parsing)
# ═══════════════════════════════════════════════════════════════════════════════

HIRING_PROMPT = """You are a recruiting search engine. Search the web for companies actively hiring for the role of "{role}" in or near {location}.

Search for: "{role}" hiring "{location}" job openings

After searching, analyze every result and return a JSON array of genuine job openings:
[{{
    "company_name": "company or restaurant name",
    "location": "city, state",
    "address": "street address if available, else null",
    "posting_source": "indeed" | "linkedin" | "glassdoor" | "ziprecruiter" | "company_website" | "google_jobs" | "poached" | "culinary_agents" | "other",
    "posting_url": "direct URL to the posting if available",
    "posting_age_days": estimated days since posted (integer or null),
    "job_title_exact": "exact title as listed in the posting",
    "salary_range": "salary or range if mentioned, else null",
    "urgency": "urgent" | "normal" | "passive",
    "signals": ["list of urgency signals"],
    "company_details": "1-2 sentence summary of the company from context",
    "confidence": "high" | "medium" | "low"
}}]

URGENCY RULES:
- "urgent" if: reposted multiple times, listed on 3+ platforms, mentions "immediate start" or sign-on bonus, posting age > 30 days (struggling to fill)
- "normal" if: standard single posting, recent, no urgency language
- "passive" if: general talent pipeline posting, not an active vacancy

RULES:
- Only include genuine job openings. Not staffing agency ads that list hundreds of cities.
- Deduplicate: same company from multiple sources = one entry with combined signals.
- Do not invent companies or details. Only report what search results actually contain.
- If no genuine openings found, return an empty array [].

Return ONLY valid JSON. No explanation text before or after the array."""

AVAILABLE_PROMPT = """You are a recruiting search engine. Search the web for professionals who appear available for or seeking "{role}" positions in or near {location}.

Search for: "{role}" "open to work" OR "seeking" OR "available" OR resume "{location}"

After searching, analyze every result and return a JSON array of candidates showing availability signals:
[{{
    "candidate_name": "full name",
    "current_title": "most recent or current title",
    "current_company": "most recent employer if identifiable, else null",
    "location": "city, state if identifiable",
    "signal_type": "open_to_work" | "resume_posted" | "actively_applying" | "social_signal" | "recently_laid_off",
    "signal_source": "linkedin" | "indeed" | "glassdoor" | "culinary_agents" | "poached" | "ziprecruiter" | "personal_website" | "other",
    "signal_url": "URL where this signal was found, if available",
    "signal_details": "what specifically indicates availability",
    "linkedin_url": "linkedin profile URL if found, else null",
    "years_experience": "estimated years based on context, or null",
    "specialties": ["relevant specialties from context"],
    "confidence": "high" | "medium" | "low"
}}]

SIGNAL TYPES:
- "open_to_work": LinkedIn badge, explicit statement of availability
- "resume_posted": Resume found on job boards or personal sites
- "actively_applying": Evidence of applications or job seeking activity
- "social_signal": Social media posts about career change, leaving a role
- "recently_laid_off": News about layoffs at their company, or explicit mention

RULES:
- Only include people showing genuine signals of seeking new opportunities.
- Do NOT include people merely mentioned in job postings (those are on the hiring side).
- Do NOT include recruiters or staffing agency contacts.
- Do NOT fabricate names or details. Only report what is actually in the search results.
- If no genuine candidates found, return an empty array [].

Return ONLY valid JSON. No explanation text before or after the array."""


async def run_scout_search(search_type: str, role: str, location: str) -> list:
    """
    Run a single search using Anthropic's web search tool + Haiku.
    Returns parsed list of results.
    """
    if search_type == "hiring":
        prompt = HIRING_PROMPT.format(role=role, location=location)
    else:
        prompt = AVAILABLE_PROMPT.format(role=role, location=location)

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
                    "max_tokens": 4096,
                    "tools": [
                        {
                            "type": "web_search_20250305",
                            "name": "web_search",
                            "max_uses": 3
                        }
                    ],
                    "messages": [
                        {"role": "user", "content": prompt}
                    ]
                },
                timeout=60.0
            )

        if resp.status_code != 200:
            logger.error(f"Scout search API error: {resp.status_code} {resp.text[:500]}")
            raise HTTPException(status_code=500, detail="Search failed. Please try again.")

        resp_data = resp.json()

        # Extract text content from response (may contain tool_use blocks too)
        content = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        content = content.strip()

        # Strip markdown fences if present
        if content.startswith("```"):
            first_nl = content.index("\n")
            content = content[first_nl + 1:]
            if content.endswith("```"):
                content = content[:-3].strip()

        # Try json_repair for resilience
        try:
            results = json.loads(content)
        except json.JSONDecodeError:
            repaired = repair_json(content)
            results = json.loads(repaired)

        if not isinstance(results, list):
            results = []

        return results

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Search timed out. Try a more specific role or location.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Scout search error: {e}")
        raise HTTPException(status_code=500, detail="Search failed. Please try again.")


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/register")
async def scout_register(data: ScoutRegisterRequest):
    supabase = get_supabase()

    existing = supabase.table("scout_users") \
        .select("id") \
        .eq("email", data.email.lower()) \
        .execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = supabase.table("scout_users").insert({
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
    token = create_scout_token(user_data)

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
async def scout_login(data: ScoutLoginRequest):
    supabase = get_supabase()

    result = supabase.table("scout_users") \
        .select("*") \
        .eq("email", data.email.lower()) \
        .execute()
    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = result.data[0]
    if not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    supabase.table("scout_users").update({
        "last_login": datetime.utcnow().isoformat()
    }).eq("id", user["id"]).execute()

    token = create_scout_token(user)

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user.get("full_name"),
            "company": user.get("company"),
            "plan": user.get("plan", "free"),
            "credit_balance": float(user.get("credit_balance", 0))
        }
    }


@router.get("/me")
async def scout_me(current_user: dict = Depends(verify_scout_token)):
    supabase = get_supabase()
    result = supabase.table("scout_users") \
        .select("id, email, full_name, company, plan, plan_status, credit_balance, searches_this_month, created_at") \
        .eq("id", current_user["scout_user_id"]) \
        .single() \
        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    user = result.data
    # Get real-time monthly count
    monthly_count = get_monthly_search_count(supabase, user["id"])
    allocation = SCOUT_ALLOCATIONS.get(user.get("plan", "free"), SCOUT_ALLOCATIONS["free"])["searches"]

    return {
        "success": True,
        "user": {
            **user,
            "credit_balance": float(user.get("credit_balance", 0)),
            "searches_this_month": monthly_count,
            "monthly_allocation": allocation,
            "searches_remaining": max(0, allocation - monthly_count)
        }
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CORE SEARCH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/search/hiring")
async def scout_search_hiring(
    data: ScoutSearchRequest,
    current_user: dict = Depends(verify_scout_token)
):
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]
    plan = current_user.get("plan", "free")

    cost = get_search_cost(plan, "hiring")
    actual_cost = check_and_charge(supabase, user_id, plan, cost, "search_hiring")

    hiring_results = await run_scout_search("hiring", data.role, data.location)

    search_record = supabase.table("scout_searches").insert({
        "user_id": user_id,
        "role_query": data.role,
        "location_query": data.location,
        "search_type": "hiring",
        "results_count": len(hiring_results),
        "cost": actual_cost,
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    search_id = search_record.data[0]["id"]

    stored_results = []
    for r in hiring_results:
        row = {
            "search_id": search_id,
            "company_name": r.get("company_name", "Unknown"),
            "location": r.get("location"),
            "address": r.get("address"),
            "posting_source": r.get("posting_source"),
            "posting_url": r.get("posting_url"),
            "posting_age_days": r.get("posting_age_days"),
            "job_title_exact": r.get("job_title_exact"),
            "salary_range": r.get("salary_range"),
            "urgency": r.get("urgency", "normal"),
            "signals": r.get("signals", []),
            "company_details": r.get("company_details"),
            "confidence": r.get("confidence", "medium"),
            "created_at": datetime.utcnow().isoformat()
        }
        inserted = supabase.table("scout_hiring_results").insert(row).execute()
        stored_results.append({**row, "id": inserted.data[0]["id"]})

    return {
        "success": True,
        "search_id": search_id,
        "role": data.role,
        "location": data.location,
        "count": len(stored_results),
        "results": stored_results,
        "cost": actual_cost
    }


@router.post("/search/available")
async def scout_search_available(
    data: ScoutSearchRequest,
    current_user: dict = Depends(verify_scout_token)
):
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]
    plan = current_user.get("plan", "free")

    cost = get_search_cost(plan, "available")
    actual_cost = check_and_charge(supabase, user_id, plan, cost, "search_available")

    available_results = await run_scout_search("available", data.role, data.location)

    search_record = supabase.table("scout_searches").insert({
        "user_id": user_id,
        "role_query": data.role,
        "location_query": data.location,
        "search_type": "available",
        "results_count": len(available_results),
        "cost": actual_cost,
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    search_id = search_record.data[0]["id"]

    stored_results = []
    for r in available_results:
        row = {
            "search_id": search_id,
            "candidate_name": r.get("candidate_name", "Unknown"),
            "current_title": r.get("current_title"),
            "current_company": r.get("current_company"),
            "location": r.get("location"),
            "signal_type": r.get("signal_type", "social_signal"),
            "signal_source": r.get("signal_source"),
            "signal_url": r.get("signal_url"),
            "signal_details": r.get("signal_details"),
            "linkedin_url": r.get("linkedin_url"),
            "years_experience": r.get("years_experience"),
            "specialties": r.get("specialties", []),
            "confidence": r.get("confidence", "medium"),
            "created_at": datetime.utcnow().isoformat()
        }
        inserted = supabase.table("scout_available_results").insert(row).execute()
        stored_results.append({**row, "id": inserted.data[0]["id"]})

    return {
        "success": True,
        "search_id": search_id,
        "role": data.role,
        "location": data.location,
        "count": len(stored_results),
        "results": stored_results,
        "cost": actual_cost
    }


@router.post("/search/both")
async def scout_search_both(
    data: ScoutSearchRequest,
    current_user: dict = Depends(verify_scout_token)
):
    """Run hiring and available searches in parallel. Cheaper than two separate calls."""
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]
    plan = current_user.get("plan", "free")

    cost = get_search_cost(plan, "both")
    actual_cost = check_and_charge(supabase, user_id, plan, cost, "search_both")

    # Run both searches in parallel
    hiring_task = run_scout_search("hiring", data.role, data.location)
    available_task = run_scout_search("available", data.role, data.location)
    hiring_results, available_results = await asyncio.gather(
        hiring_task, available_task, return_exceptions=True
    )

    # Handle partial failures gracefully
    if isinstance(hiring_results, Exception):
        logger.error(f"Scout hiring search failed: {hiring_results}")
        hiring_results = []
    if isinstance(available_results, Exception):
        logger.error(f"Scout available search failed: {available_results}")
        available_results = []

    total_count = len(hiring_results) + len(available_results)

    search_record = supabase.table("scout_searches").insert({
        "user_id": user_id,
        "role_query": data.role,
        "location_query": data.location,
        "search_type": "both",
        "results_count": total_count,
        "cost": actual_cost,
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    search_id = search_record.data[0]["id"]

    stored_hiring = []
    for r in hiring_results:
        row = {
            "search_id": search_id,
            "company_name": r.get("company_name", "Unknown"),
            "location": r.get("location"),
            "address": r.get("address"),
            "posting_source": r.get("posting_source"),
            "posting_url": r.get("posting_url"),
            "posting_age_days": r.get("posting_age_days"),
            "job_title_exact": r.get("job_title_exact"),
            "salary_range": r.get("salary_range"),
            "urgency": r.get("urgency", "normal"),
            "signals": r.get("signals", []),
            "company_details": r.get("company_details"),
            "confidence": r.get("confidence", "medium"),
            "created_at": datetime.utcnow().isoformat()
        }
        inserted = supabase.table("scout_hiring_results").insert(row).execute()
        stored_hiring.append({**row, "id": inserted.data[0]["id"]})

    stored_available = []
    for r in available_results:
        row = {
            "search_id": search_id,
            "candidate_name": r.get("candidate_name", "Unknown"),
            "current_title": r.get("current_title"),
            "current_company": r.get("current_company"),
            "location": r.get("location"),
            "signal_type": r.get("signal_type", "social_signal"),
            "signal_source": r.get("signal_source"),
            "signal_url": r.get("signal_url"),
            "signal_details": r.get("signal_details"),
            "linkedin_url": r.get("linkedin_url"),
            "years_experience": r.get("years_experience"),
            "specialties": r.get("specialties", []),
            "confidence": r.get("confidence", "medium"),
            "created_at": datetime.utcnow().isoformat()
        }
        inserted = supabase.table("scout_available_results").insert(row).execute()
        stored_available.append({**row, "id": inserted.data[0]["id"]})

    return {
        "success": True,
        "search_id": search_id,
        "role": data.role,
        "location": data.location,
        "hiring": {"count": len(stored_hiring), "results": stored_hiring},
        "available": {"count": len(stored_available), "results": stored_available},
        "cost": actual_cost
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH INFO (cost preview, no auth required for display)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/search/cost")
async def scout_search_cost(current_user: dict = Depends(verify_scout_token)):
    """Return cost info for the current user so the UI can display before searching."""
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]
    plan = current_user.get("plan", "free")

    monthly_count = get_monthly_search_count(supabase, user_id)
    allocation = SCOUT_ALLOCATIONS.get(plan, SCOUT_ALLOCATIONS["free"])["searches"]
    remaining = max(0, allocation - monthly_count)

    user = supabase.table("scout_users") \
        .select("credit_balance") \
        .eq("id", user_id) \
        .single() \
        .execute()
    balance = float(user.data["credit_balance"] or 0)

    return {
        "success": True,
        "plan": plan,
        "allocation": allocation,
        "used": monthly_count,
        "remaining": remaining,
        "balance": balance,
        "costs": SCOUT_PRICING.get(plan, SCOUT_PRICING["free"]),
        "within_allocation": remaining > 0
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/history")
async def scout_history(
    page: int = 1,
    limit: int = 20,
    current_user: dict = Depends(verify_scout_token)
):
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]
    offset = (page - 1) * limit

    result = supabase.table("scout_searches") \
        .select("*", count="exact") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .range(offset, offset + limit - 1) \
        .execute()

    return {
        "success": True,
        "searches": result.data,
        "total": result.count or 0,
        "page": page,
        "limit": limit
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SAVED RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/saved")
async def scout_save_result(
    data: ScoutSaveRequest,
    current_user: dict = Depends(verify_scout_token)
):
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]

    row = {
        "user_id": user_id,
        "result_type": data.result_type,
        "notes": data.notes,
        "created_at": datetime.utcnow().isoformat()
    }

    if data.result_type == "hiring" and data.hiring_result_id:
        row["hiring_result_id"] = data.hiring_result_id
    elif data.result_type == "available" and data.available_result_id:
        row["available_result_id"] = data.available_result_id
    else:
        raise HTTPException(status_code=400, detail="Must provide hiring_result_id or available_result_id matching result_type")

    result = supabase.table("scout_saved").insert(row).execute()

    return {"success": True, "saved": result.data[0]}


@router.get("/saved")
async def scout_get_saved(
    result_type: Optional[str] = None,
    current_user: dict = Depends(verify_scout_token)
):
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]

    query = supabase.table("scout_saved") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True)

    if result_type:
        query = query.eq("result_type", result_type)

    saved = query.execute()
    saved_items = saved.data or []

    # Hydrate with actual result data
    for item in saved_items:
        if item.get("hiring_result_id"):
            hr = supabase.table("scout_hiring_results") \
                .select("*") \
                .eq("id", item["hiring_result_id"]) \
                .execute()
            item["hiring_result"] = hr.data[0] if hr.data else None
        if item.get("available_result_id"):
            ar = supabase.table("scout_available_results") \
                .select("*") \
                .eq("id", item["available_result_id"]) \
                .execute()
            item["available_result"] = ar.data[0] if ar.data else None

    return {"success": True, "saved": saved_items}


@router.delete("/saved/{saved_id}")
async def scout_delete_saved(
    saved_id: str,
    current_user: dict = Depends(verify_scout_token)
):
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]

    # Verify ownership
    existing = supabase.table("scout_saved") \
        .select("id") \
        .eq("id", saved_id) \
        .eq("user_id", user_id) \
        .execute()

    if not existing.data:
        raise HTTPException(status_code=404, detail="Saved item not found")

    supabase.table("scout_saved").delete().eq("id", saved_id).execute()
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════════
# CREDITS & BILLING
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/credits")
async def scout_credits(current_user: dict = Depends(verify_scout_token)):
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]

    user = supabase.table("scout_users") \
        .select("credit_balance, plan, plan_status") \
        .eq("id", user_id) \
        .single() \
        .execute()

    transactions = supabase.table("scout_credit_transactions") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .limit(50) \
        .execute()

    return {
        "success": True,
        "balance": float(user.data.get("credit_balance", 0)),
        "plan": user.data.get("plan", "free"),
        "plan_status": user.data.get("plan_status", "active"),
        "transactions": transactions.data or []
    }


@router.post("/credits/purchase")
async def scout_purchase_credits(
    request: Request,
    current_user: dict = Depends(verify_scout_token)
):
    body = await request.json()
    pack = body.get("pack", "credits_10")
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]

    price_id = SCOUT_STRIPE_PRICE_IDS.get(pack)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Unknown credit pack: {pack}")

    user = supabase.table("scout_users") \
        .select("stripe_customer_id, email") \
        .eq("id", user_id) \
        .single() \
        .execute()

    customer_id = user.data.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(email=user.data["email"])
        customer_id = customer.id
        supabase.table("scout_users") \
            .update({"stripe_customer_id": customer_id}) \
            .eq("id", user_id) \
            .execute()

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="payment",
        success_url="https://scout.en-place.ai/credits?purchased=true",
        cancel_url="https://scout.en-place.ai/credits?cancelled=true",
        metadata={"scout_user_id": user_id, "pack": pack, "product": "scout"}
    )

    return {"success": True, "url": session.url}


@router.post("/subscribe")
async def scout_subscribe(
    request: Request,
    current_user: dict = Depends(verify_scout_token)
):
    body = await request.json()
    plan = body.get("plan", "starter")
    supabase = get_supabase()
    user_id = current_user["scout_user_id"]

    price_id = SCOUT_STRIPE_PRICE_IDS.get(plan)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")

    user = supabase.table("scout_users") \
        .select("stripe_customer_id, email") \
        .eq("id", user_id) \
        .single() \
        .execute()

    customer_id = user.data.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(email=user.data["email"])
        customer_id = customer.id
        supabase.table("scout_users") \
            .update({"stripe_customer_id": customer_id}) \
            .eq("id", user_id) \
            .execute()

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url="https://scout.en-place.ai/search?subscribed=true",
        cancel_url="https://scout.en-place.ai/credits?cancelled=true",
        metadata={"scout_user_id": user_id, "plan": plan, "product": "scout"}
    )

    return {"success": True, "url": session.url}
