# routes/relay.py
"""
Relay Intelligence API
======================
Contact monitoring and change detection platform.
Watches a user's book of business for title changes, departures,
promotions, and company moves. Industry-agnostic from day one.

Shares backend infrastructure with Mise/Brigade (same Heroku app, same Supabase)
but has its own auth (relay_users), its own branding, and its own pricing.
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
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from database.supabase_client import get_supabase
from config.settings import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_HOURS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/relay", tags=["relay"])
security = HTTPBearer()

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


# ═══════════════════════════════════════════════════════════════════════════════
# PRICING & PLANS
# ═══════════════════════════════════════════════════════════════════════════════

RELAY_PLANS = {
    "free":    {"price": 0,   "contact_limit": 25,   "scans": "weekly", "deep_scans_included": 0,   "imports": 1},
    "starter": {"price": 39,  "contact_limit": 200,  "scans": "weekly", "deep_scans_included": 10,  "imports": -1},
    "pro":     {"price": 99,  "contact_limit": 1000, "scans": "daily",  "deep_scans_included": 50,  "imports": -1},
}

RELAY_PRICING = {
    "free":    {"deep_scan": 1.00},
    "starter": {"deep_scan": 0.50},
    "pro":     {"deep_scan": 0.25},
}

RELAY_STRIPE_PRICES = {
    "starter": os.environ.get("RELAY_STRIPE_PRICE_STARTER"),
    "pro": os.environ.get("RELAY_STRIPE_PRICE_PRO"),
}

RELAY_CREDIT_PACKS = {
    "10":  {"credits": 10,  "price": 900},
    "25":  {"credits": 25,  "price": 2000},
    "50":  {"credits": 50,  "price": 3500},
}


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_relay_token(user: dict) -> str:
    payload = {
        "relay_user_id": str(user["id"]),
        "email": user["email"],
        "full_name": user.get("full_name"),
        "plan": user.get("plan", "free"),
        "plan_status": user.get("plan_status", "active"),
        "portal_access": "relay",
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_relay_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    try:
        payload = jwt.decode(
            credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
        if payload.get("portal_access") != "relay":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


def get_deep_scan_cost(current_user: dict) -> float:
    plan = current_user.get("plan", "free")
    return RELAY_PRICING.get(plan, RELAY_PRICING["free"])["deep_scan"]

def get_monthly_deep_scan_usage(supabase, user_id: str) -> int:
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = supabase.table("relay_credit_transactions") \
        .select("id", count="exact") \
        .eq("user_id", user_id) \
        .eq("transaction_type", "deep_scan") \
        .gte("created_at", month_start.isoformat()) \
        .execute()
    return result.count or 0


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class RelayRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    company: Optional[str] = None
    industry: Optional[str] = None

class RelayLoginRequest(BaseModel):
    email: EmailStr
    password: str

class RelayContactCreate(BaseModel):
    full_name: str
    company: str
    title: str
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    relationship_notes: Optional[str] = None
    tags: Optional[List[str]] = None

class RelayContactUpdate(BaseModel):
    full_name: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    relationship_notes: Optional[str] = None
    tags: Optional[List[str]] = None
    monitoring_status: Optional[str] = None

class RelayBulkAction(BaseModel):
    contact_ids: List[str]

class RelayScanRequest(BaseModel):
    contact_ids: Optional[List[str]] = None  # None = scan all active

class RelayImportRequest(BaseModel):
    mapping: Dict[str, str]
    records: List[Dict[str, Any]]
    filename: Optional[str] = None

class RelayHeaderMapRequest(BaseModel):
    headers: List[str]

class RelaySnoozeRequest(BaseModel):
    snooze_until: str  # ISO date string


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/register")
async def relay_register(data: RelayRegisterRequest):
    supabase = get_supabase()

    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    existing = supabase.table("relay_users") \
        .select("id") \
        .eq("email", data.email.lower()) \
        .execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = supabase.table("relay_users").insert({
        "email": data.email.lower(),
        "password_hash": hash_password(data.password),
        "full_name": data.full_name,
        "company": data.company,
        "industry": data.industry,
        "credit_balance": 0,
        "plan": "free",
        "plan_status": "active",
        "contact_limit": RELAY_PLANS["free"]["contact_limit"],
        "scan_frequency": RELAY_PLANS["free"]["scans"],
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    user_data = user.data[0]
    token = create_relay_token(user_data)

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user_data["id"],
            "email": user_data["email"],
            "full_name": user_data["full_name"],
            "company": user_data.get("company"),
            "industry": user_data.get("industry"),
            "plan": "free",
            "credit_balance": 0,
            "contact_limit": RELAY_PLANS["free"]["contact_limit"]
        }
    }


@router.post("/login")
async def relay_login(data: RelayLoginRequest):
    supabase = get_supabase()

    result = supabase.table("relay_users") \
        .select("*") \
        .eq("email", data.email.lower()) \
        .execute()
    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = result.data[0]
    if not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    supabase.table("relay_users").update({
        "last_login": datetime.utcnow().isoformat()
    }).eq("id", user["id"]).execute()

    token = create_relay_token(user)
    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user.get("full_name"),
            "company": user.get("company"),
            "industry": user.get("industry"),
            "plan": user.get("plan", "free"),
            "plan_status": user.get("plan_status", "active"),
            "credit_balance": float(user.get("credit_balance", 0)),
            "contact_limit": user.get("contact_limit", 25),
            "scan_frequency": user.get("scan_frequency", "weekly")
        }
    }


@router.get("/me")
async def relay_me(current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    result = supabase.table("relay_users") \
        .select("id, email, full_name, company, industry, plan, plan_status, credit_balance, contact_limit, scan_frequency, created_at") \
        .eq("id", current_user["relay_user_id"]) \
        .single() \
        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    user = result.data
    user["credit_balance"] = float(user.get("credit_balance", 0))

    # Get contact count
    contact_count = supabase.table("relay_contacts") \
        .select("id", count="exact") \
        .eq("user_id", current_user["relay_user_id"]) \
        .in_("monitoring_status", ["active", "paused"]) \
        .execute()
    user["contact_count"] = contact_count.count or 0

    return {"success": True, "user": user}


# ═══════════════════════════════════════════════════════════════════════════════
# CONTACT CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/contacts")
async def create_contact(data: RelayContactCreate, current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]

    # Check contact limit
    count_result = supabase.table("relay_contacts") \
        .select("id", count="exact") \
        .eq("user_id", user_id) \
        .in_("monitoring_status", ["active", "paused"]) \
        .execute()
    current_count = count_result.count or 0

    user_result = supabase.table("relay_users") \
        .select("contact_limit") \
        .eq("id", user_id) \
        .single() \
        .execute()
    limit = user_result.data.get("contact_limit", 25) if user_result.data else 25

    if current_count >= limit:
        raise HTTPException(
            status_code=403,
            detail=f"Contact limit reached ({limit}). Upgrade your plan to monitor more contacts."
        )

    contact = supabase.table("relay_contacts").insert({
        "user_id": user_id,
        "full_name": data.full_name,
        "company": data.company,
        "title": data.title,
        "email": data.email,
        "phone": data.phone,
        "linkedin_url": data.linkedin_url,
        "city": data.city,
        "state": data.state,
        "relationship_notes": data.relationship_notes,
        "tags": data.tags or [],
        "monitoring_status": "active",
        "current_status": "stable",
        "imported_from": "manual",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }).execute()

    return {"success": True, "contact": contact.data[0]}


@router.get("/contacts")
async def list_contacts(
    status: Optional[str] = None,
    monitoring: Optional[str] = None,
    company: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    current_user: dict = Depends(verify_relay_token)
):
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]

    query = supabase.table("relay_contacts") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("updated_at", desc=True)

    if status:
        query = query.eq("current_status", status)
    if monitoring:
        query = query.eq("monitoring_status", monitoring)
    else:
        # Default: don't show archived
        query = query.in_("monitoring_status", ["active", "paused"])
    if company:
        query = query.ilike("company", f"%{company}%")
    if search:
        query = query.or_(f"full_name.ilike.%{search}%,company.ilike.%{search}%,title.ilike.%{search}%")

    result = query.execute()
    contacts = result.data or []

    # Filter by tag in Python (Supabase array contains is finicky)
    if tag:
        contacts = [c for c in contacts if tag in (c.get("tags") or [])]

    return {"success": True, "contacts": contacts, "count": len(contacts)}


@router.get("/contacts/{contact_id}")
async def get_contact(contact_id: str, current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]

    result = supabase.table("relay_contacts") \
        .select("*") \
        .eq("id", contact_id) \
        .eq("user_id", user_id) \
        .single() \
        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Get signal history for this contact
    signals = supabase.table("relay_signals") \
        .select("*") \
        .eq("contact_id", contact_id) \
        .order("detected_at", desc=True) \
        .execute()

    return {
        "success": True,
        "contact": result.data,
        "signals": signals.data or []
    }


@router.patch("/contacts/{contact_id}")
async def update_contact(contact_id: str, data: RelayContactUpdate, current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]

    # Verify ownership
    existing = supabase.table("relay_contacts") \
        .select("id") \
        .eq("id", contact_id) \
        .eq("user_id", user_id) \
        .execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Contact not found")

    update = {k: v for k, v in data.dict(exclude_unset=True).items() if v is not None}
    update["updated_at"] = datetime.utcnow().isoformat()

    result = supabase.table("relay_contacts") \
        .update(update) \
        .eq("id", contact_id) \
        .execute()

    return {"success": True, "contact": result.data[0] if result.data else None}


@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: str, current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]

    existing = supabase.table("relay_contacts") \
        .select("id") \
        .eq("id", contact_id) \
        .eq("user_id", user_id) \
        .execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Contact not found")

    supabase.table("relay_contacts") \
        .delete() \
        .eq("id", contact_id) \
        .execute()

    return {"success": True, "message": "Contact deleted"}


@router.post("/contacts/bulk-pause")
async def bulk_pause(data: RelayBulkAction, current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]
    updated = 0
    for cid in data.contact_ids:
        try:
            supabase.table("relay_contacts") \
                .update({"monitoring_status": "paused", "updated_at": datetime.utcnow().isoformat()}) \
                .eq("id", cid) \
                .eq("user_id", user_id) \
                .execute()
            updated += 1
        except Exception:
            pass
    return {"success": True, "updated": updated}


@router.post("/contacts/bulk-archive")
async def bulk_archive(data: RelayBulkAction, current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]
    updated = 0
    for cid in data.contact_ids:
        try:
            supabase.table("relay_contacts") \
                .update({"monitoring_status": "archived", "updated_at": datetime.utcnow().isoformat()}) \
                .eq("id", cid) \
                .eq("user_id", user_id) \
                .execute()
            updated += 1
        except Exception:
            pass
    return {"success": True, "updated": updated}


# ═══════════════════════════════════════════════════════════════════════════════
# CSV IMPORT
# ═══════════════════════════════════════════════════════════════════════════════

HEADER_MAP_PROMPT = """You are mapping CSV column headers to database fields for a contact monitoring platform.

CSV headers from the uploaded file:
{headers}

Map each CSV header to ONE of these database fields (or null if no match):
full_name, company, title, email, phone, linkedin_url, city, state, relationship_notes, tags

Return ONLY valid JSON, no explanation. Example:
{{"Contact Name": "full_name", "Organization": "company", "Job Title": "title", "Email Address": "email", "Notes": "relationship_notes", "Random Column": null}}

Rules:
- "Name", "Contact", "Person" variations map to full_name
- "Company", "Organization", "Employer", "Firm" variations map to company
- "Title", "Role", "Position", "Job Title" variations map to title
- "LinkedIn", "Profile URL" variations map to linkedin_url
- "Notes", "Comments", "Relationship" variations map to relationship_notes
- "Tags", "Labels", "Categories" variations map to tags
- If a header is ambiguous or unrelated, map it to null"""


@router.post("/import/map-headers")
async def map_import_headers(data: RelayHeaderMapRequest, current_user: dict = Depends(verify_relay_token)):
    """Use Haiku to map CSV headers to database fields."""
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
                    "messages": [{
                        "role": "user",
                        "content": HEADER_MAP_PROMPT.format(headers=json.dumps(data.headers))
                    }]
                },
                timeout=15.0
            )
            resp_data = resp.json()

        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="AI mapping service unavailable")

        text = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        cleaned = text.strip()
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            mapping = json.loads(repair_json(cleaned[first_brace:last_brace + 1]))
        else:
            raise HTTPException(status_code=502, detail="Failed to parse header mapping")

        return {"success": True, "mapping": mapping}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Header mapping error: {e}")
        raise HTTPException(status_code=500, detail="Failed to map headers")


@router.post("/import/records")
async def import_records(data: RelayImportRequest, current_user: dict = Depends(verify_relay_token)):
    """Import mapped CSV records into relay_contacts."""
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]

    if not data.records:
        raise HTTPException(status_code=400, detail="No records to import")
    if len(data.records) > 500:
        raise HTTPException(status_code=400, detail="Maximum 500 records per import")

    # Check import limit for free plan
    plan = current_user.get("plan", "free")
    if plan == "free":
        import_count = supabase.table("relay_imports") \
            .select("id", count="exact") \
            .eq("user_id", user_id) \
            .execute()
        if (import_count.count or 0) >= RELAY_PLANS["free"]["imports"]:
            raise HTTPException(status_code=403, detail="Free plan allows 1 import. Upgrade for unlimited imports.")

    # Check contact limit
    count_result = supabase.table("relay_contacts") \
        .select("id", count="exact") \
        .eq("user_id", user_id) \
        .in_("monitoring_status", ["active", "paused"]) \
        .execute()
    current_count = count_result.count or 0

    user_result = supabase.table("relay_users") \
        .select("contact_limit") \
        .eq("id", user_id) \
        .single() \
        .execute()
    limit = user_result.data.get("contact_limit", 25) if user_result.data else 25

    allowed_fields = {"full_name", "company", "title", "email", "phone", "linkedin_url", "city", "state", "relationship_notes", "tags"}
    imported = 0
    skipped = 0
    duplicates = 0

    for row in data.records:
        if current_count + imported >= limit:
            skipped += len(data.records) - imported - skipped - duplicates
            break

        mapped = {}
        for csv_header, db_field in data.mapping.items():
            if db_field and db_field in allowed_fields and csv_header in row:
                val = row[csv_header]
                if val and str(val).strip():
                    if db_field == "tags":
                        # Split comma-separated tags
                        mapped[db_field] = [t.strip() for t in str(val).split(",") if t.strip()]
                    else:
                        mapped[db_field] = str(val).strip()

        if not mapped.get("full_name") or not mapped.get("company") or not mapped.get("title"):
            skipped += 1
            continue

        # Deduplicate: check if name + company already exists
        existing = supabase.table("relay_contacts") \
            .select("id") \
            .eq("user_id", user_id) \
            .ilike("full_name", mapped["full_name"]) \
            .ilike("company", mapped["company"]) \
            .execute()

        if existing.data:
            # Update existing record instead of creating duplicate
            update_data = {k: v for k, v in mapped.items() if k not in ("full_name", "company")}
            if update_data:
                update_data["updated_at"] = datetime.utcnow().isoformat()
                supabase.table("relay_contacts") \
                    .update(update_data) \
                    .eq("id", existing.data[0]["id"]) \
                    .execute()
            duplicates += 1
            continue

        mapped["user_id"] = user_id
        mapped["monitoring_status"] = "active"
        mapped["current_status"] = "stable"
        mapped["imported_from"] = "csv"
        mapped["created_at"] = datetime.utcnow().isoformat()
        mapped["updated_at"] = datetime.utcnow().isoformat()
        if "tags" not in mapped:
            mapped["tags"] = []

        try:
            supabase.table("relay_contacts").insert(mapped).execute()
            imported += 1
        except Exception as e:
            logger.warning(f"Import row failed: {e}")
            skipped += 1

    # Record the import
    supabase.table("relay_imports").insert({
        "user_id": user_id,
        "filename": data.filename,
        "row_count": len(data.records),
        "imported_count": imported,
        "skipped_count": skipped,
        "status": "complete",
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "duplicates": duplicates,
        "at_limit": (current_count + imported) >= limit
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN ENGINE (core value)
# ═══════════════════════════════════════════════════════════════════════════════

CONTACT_SCAN_PROMPT = """You are analyzing web search results to determine if a professional contact has changed roles or companies.

KNOWN INFORMATION:
- Name: {full_name}
- Company: {company}
- Title: {title}
- Location: {location}
- Last verified: {last_verified}

Search the web for current information about this person's professional status. Look for LinkedIn profiles, company pages, press releases, job postings, and news articles.

Return ONLY valid JSON:
{{
    "status": "stable" | "promoted" | "lateral_move" | "departed" | "moved_company" | "title_change" | "uncertain",
    "confidence": "high" | "medium" | "low",
    "new_title": "new title if changed, else null",
    "new_company": "new company if moved, else null",
    "replacement_name": "name of person who appears to have replaced them, if detectable, else null",
    "replacement_linkedin": "linkedin URL if found, else null",
    "source": "linkedin" | "company_website" | "press_release" | "google" | "news",
    "source_url": "most relevant URL or null",
    "details": "brief explanation of what the search results indicate",
    "recommended_action": "specific sales action to take based on this change, or null if stable"
}}

RULES:
- If results confirm the person still holds the same title at the same company, status is "stable"
- If you find their name with a DIFFERENT title at the SAME company, determine if it is a promotion (bigger scope) or lateral move
- If you find their name at a DIFFERENT company, status is "moved_company"
- If their name no longer appears connected to that company at all, status is "departed"
- If results are ambiguous or insufficient, status is "uncertain" with confidence "low"
- Do not invent information. Only report what the search results support.
- For recommended_action, be specific and actionable. Example: "Congratulate on promotion to VP. This expands their purchasing authority. Pitch the enterprise tier." Not generic advice."""


async def scan_single_contact(contact: dict, supabase) -> Optional[dict]:
    """Scan a single contact using Haiku + web search. Returns signal dict or None."""
    location = ""
    if contact.get("city") and contact.get("state"):
        location = f"{contact['city']}, {contact['state']}"
    elif contact.get("city"):
        location = contact["city"]
    elif contact.get("state"):
        location = contact["state"]

    prompt = CONTACT_SCAN_PROMPT.format(
        full_name=contact["full_name"],
        company=contact["company"],
        title=contact["title"],
        location=location or "unknown",
        last_verified=contact.get("last_verified_at") or "never"
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
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}],
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}]
                },
                timeout=45.0
            )
            resp_data = resp.json()

        if resp.status_code != 200:
            logger.warning(f"Scan API error for {contact['full_name']}: {resp.status_code}")
            return None

        text = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        if not text:
            return None

        cleaned = text.strip()
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace == -1 or last_brace <= first_brace:
            logger.warning(f"No JSON in scan response for {contact['full_name']}")
            return None

        parsed = json.loads(repair_json(cleaned[first_brace:last_brace + 1]))

        # Stable: just update verification timestamp
        if parsed.get("status") == "stable":
            supabase.table("relay_contacts").update({
                "last_verified_at": datetime.utcnow().isoformat(),
                "last_scanned_at": datetime.utcnow().isoformat()
            }).eq("id", contact["id"]).execute()
            return None

        # Low-confidence uncertain: don't create a signal
        if parsed.get("status") == "uncertain" and parsed.get("confidence") == "low":
            supabase.table("relay_contacts").update({
                "last_scanned_at": datetime.utcnow().isoformat()
            }).eq("id", contact["id"]).execute()
            return None

        # Create signal
        signal = {
            "contact_id": contact["id"],
            "user_id": contact["user_id"],
            "signal_type": parsed["status"],
            "old_title": contact["title"],
            "new_title": parsed.get("new_title"),
            "old_company": contact["company"],
            "new_company": parsed.get("new_company"),
            "new_contact_name": parsed.get("replacement_name"),
            "new_contact_linkedin": parsed.get("replacement_linkedin"),
            "confidence": parsed.get("confidence", "medium"),
            "source": parsed.get("source"),
            "source_url": parsed.get("source_url"),
            "details": parsed.get("details"),
            "recommended_action": parsed.get("recommended_action"),
            "detected_at": datetime.utcnow().isoformat()
        }
        supabase.table("relay_signals").insert(signal).execute()

        # Update contact status
        update = {
            "current_status": parsed["status"],
            "last_scanned_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        if parsed.get("new_title"):
            update["title"] = parsed["new_title"]
        if parsed.get("new_company"):
            update["company"] = parsed["new_company"]

        supabase.table("relay_contacts").update(update).eq("id", contact["id"]).execute()

        return signal

    except Exception as e:
        logger.error(f"Scan error for {contact['full_name']}: {e}")
        return None


async def run_scan_batch(scan_id: str, contacts: list, user_id: str):
    """Background task: scan a batch of contacts."""
    supabase = get_supabase()
    scanned = 0
    signals_found = 0

    for contact in contacts:
        signal = await scan_single_contact(contact, supabase)
        scanned += 1
        if signal:
            signals_found += 1

        # Update scan progress
        supabase.table("relay_scans").update({
            "contacts_scanned": scanned,
            "signals_found": signals_found
        }).eq("id", scan_id).execute()

        # Small delay to avoid rate limiting
        await asyncio.sleep(0.5)

    # Mark scan complete
    supabase.table("relay_scans").update({
        "contacts_scanned": scanned,
        "signals_found": signals_found,
        "completed_at": datetime.utcnow().isoformat(),
        "status": "complete"
    }).eq("id", scan_id).execute()


@router.post("/scan")
async def trigger_scan(
    data: RelayScanRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(verify_relay_token)
):
    """Trigger a manual scan of active contacts (or selected contacts)."""
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]

    # Get contacts to scan
    query = supabase.table("relay_contacts") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("monitoring_status", "active")

    if data.contact_ids:
        query = query.in_("id", data.contact_ids)

    result = query.execute()
    contacts = result.data or []

    if not contacts:
        raise HTTPException(status_code=400, detail="No active contacts to scan")

    # Create scan record
    scan = supabase.table("relay_scans").insert({
        "user_id": user_id,
        "scan_type": "manual",
        "contacts_scanned": 0,
        "signals_found": 0,
        "started_at": datetime.utcnow().isoformat(),
        "status": "running"
    }).execute()

    scan_id = scan.data[0]["id"]

    # Run in background
    background_tasks.add_task(run_scan_batch, scan_id, contacts, user_id)

    return {
        "success": True,
        "scan_id": scan_id,
        "contacts_queued": len(contacts)
    }


@router.get("/scan/{scan_id}/status")
async def scan_status(scan_id: str, current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    result = supabase.table("relay_scans") \
        .select("*") \
        .eq("id", scan_id) \
        .eq("user_id", current_user["relay_user_id"]) \
        .single() \
        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Scan not found")
    return {"success": True, "scan": result.data}


@router.get("/scans")
async def list_scans(current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    result = supabase.table("relay_scans") \
        .select("*") \
        .eq("user_id", current_user["relay_user_id"]) \
        .order("started_at", desc=True) \
        .limit(50) \
        .execute()
    return {"success": True, "scans": result.data or []}


# ═══════════════════════════════════════════════════════════════════════════════
# DEEP SCAN (premium per-contact)
# ═══════════════════════════════════════════════════════════════════════════════

DEEP_SCAN_PROMPT = """You are performing a thorough investigation of a professional contact to detect any role changes, company moves, or departures.

KNOWN INFORMATION:
- Name: {full_name}
- Company: {company}
- Title: {title}
- Location: {location}
- LinkedIn: {linkedin}
- Last verified: {last_verified}

Perform a comprehensive web search. Check:
1. Their current LinkedIn profile status
2. Whether their name still appears with this title at this company
3. Any job postings for their role at their company (suggesting departure)
4. Any press releases, news, or announcements mentioning them
5. Whether someone else now holds their title at the same company

Return ONLY valid JSON:
{{
    "status": "stable" | "promoted" | "lateral_move" | "departed" | "moved_company" | "title_change" | "uncertain",
    "confidence": "high" | "medium" | "low",
    "new_title": "new title if changed, else null",
    "new_company": "new company if moved, else null",
    "replacement_name": "name of replacement if detectable, else null",
    "replacement_linkedin": "linkedin URL of replacement if found, else null",
    "source": "linkedin" | "company_website" | "press_release" | "google" | "news",
    "source_url": "most relevant URL or null",
    "details": "thorough explanation of all findings from the search",
    "recommended_action": "specific, actionable sales recommendation based on findings, or null if stable",
    "additional_findings": "any other relevant intelligence discovered during the search"
}}

Be thorough. This is a premium scan, so provide maximum detail and specificity."""


@router.post("/contacts/{contact_id}/deep-scan")
async def deep_scan_contact(contact_id: str, current_user: dict = Depends(verify_relay_token)):
    """Enhanced multi-signal scan on a single contact. Premium feature."""
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]
    plan = current_user.get("plan", "free")

    # Get contact
    contact_result = supabase.table("relay_contacts") \
        .select("*") \
        .eq("id", contact_id) \
        .eq("user_id", user_id) \
        .single() \
        .execute()
    if not contact_result.data:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact = contact_result.data

    # Check deep scan allocation
    plan_config = RELAY_PLANS.get(plan, RELAY_PLANS["free"])
    included = plan_config["deep_scans_included"]
    usage = get_monthly_deep_scan_usage(supabase, user_id)

    cost = 0.0
    if usage >= included:
        # Past allocation, charge credits
        cost = get_deep_scan_cost(current_user)
        user_data = supabase.table("relay_users") \
            .select("credit_balance") \
            .eq("id", user_id) \
            .single() \
            .execute()
        balance = float(user_data.data.get("credit_balance", 0)) if user_data.data else 0

        if balance < cost:
            raise HTTPException(
                status_code=402,
                detail=f"Insufficient credits. Deep scan costs ${cost:.2f}. Balance: ${balance:.2f}"
            )

    # Build prompt
    location = ""
    if contact.get("city") and contact.get("state"):
        location = f"{contact['city']}, {contact['state']}"

    prompt = DEEP_SCAN_PROMPT.format(
        full_name=contact["full_name"],
        company=contact["company"],
        title=contact["title"],
        location=location or "unknown",
        linkedin=contact.get("linkedin_url") or "not provided",
        last_verified=contact.get("last_verified_at") or "never"
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
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 800,
                    "messages": [{"role": "user", "content": prompt}],
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}]
                },
                timeout=60.0
            )
            resp_data = resp.json()

        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Scan service unavailable")

        text = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        if not text:
            raise HTTPException(status_code=502, detail="No scan results returned")

        cleaned = text.strip()
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace == -1 or last_brace <= first_brace:
            raise HTTPException(status_code=502, detail="Failed to parse scan results")

        parsed = json.loads(repair_json(cleaned[first_brace:last_brace + 1]))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Deep scan error for {contact['full_name']}: {e}")
        raise HTTPException(status_code=500, detail="Deep scan failed")

    # Charge credits if past allocation
    if cost > 0:
        new_balance = balance - cost
        supabase.table("relay_users").update({
            "credit_balance": new_balance
        }).eq("id", user_id).execute()

        supabase.table("relay_credit_transactions").insert({
            "user_id": user_id,
            "transaction_type": "deep_scan",
            "amount": -cost,
            "balance_after": new_balance,
            "description": f"Deep scan: {contact['full_name']} at {contact['company']}",
            "created_at": datetime.utcnow().isoformat()
        }).execute()
    else:
        # Record the usage even if covered by allocation
        user_data = supabase.table("relay_users") \
            .select("credit_balance") \
            .eq("id", user_id) \
            .single() \
            .execute()
        current_balance = float(user_data.data.get("credit_balance", 0)) if user_data.data else 0
        supabase.table("relay_credit_transactions").insert({
            "user_id": user_id,
            "transaction_type": "deep_scan",
            "amount": 0,
            "balance_after": current_balance,
            "description": f"Deep scan (included): {contact['full_name']} at {contact['company']}",
            "created_at": datetime.utcnow().isoformat()
        }).execute()

    # Create signal if status is not stable
    signal_data = None
    if parsed.get("status") not in ("stable", "uncertain"):
        signal_data = {
            "contact_id": contact["id"],
            "user_id": user_id,
            "signal_type": parsed["status"],
            "old_title": contact["title"],
            "new_title": parsed.get("new_title"),
            "old_company": contact["company"],
            "new_company": parsed.get("new_company"),
            "new_contact_name": parsed.get("replacement_name"),
            "new_contact_linkedin": parsed.get("replacement_linkedin"),
            "confidence": parsed.get("confidence", "medium"),
            "source": parsed.get("source"),
            "source_url": parsed.get("source_url"),
            "details": parsed.get("details"),
            "recommended_action": parsed.get("recommended_action"),
            "detected_at": datetime.utcnow().isoformat()
        }
        supabase.table("relay_signals").insert(signal_data).execute()

        # Update contact
        update = {
            "current_status": parsed["status"],
            "last_scanned_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        if parsed.get("new_title"):
            update["title"] = parsed["new_title"]
        if parsed.get("new_company"):
            update["company"] = parsed["new_company"]
        supabase.table("relay_contacts").update(update).eq("id", contact["id"]).execute()

    else:
        # Stable or uncertain: just update timestamps
        supabase.table("relay_contacts").update({
            "last_scanned_at": datetime.utcnow().isoformat(),
            "last_verified_at": datetime.utcnow().isoformat() if parsed.get("status") == "stable" else contact.get("last_verified_at"),
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", contact["id"]).execute()

    return {
        "success": True,
        "result": parsed,
        "signal": signal_data,
        "cost": cost,
        "scans_used": usage + 1,
        "scans_included": included
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/signals")
async def list_signals(
    signal_type: Optional[str] = None,
    confidence: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    current_user: dict = Depends(verify_relay_token)
):
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]

    query = supabase.table("relay_signals") \
        .select("*, relay_contacts(full_name, company, title, tags)") \
        .eq("user_id", user_id) \
        .order("detected_at", desc=True)

    if signal_type:
        query = query.eq("signal_type", signal_type)
    if confidence:
        query = query.eq("confidence", confidence)
    if acknowledged is not None:
        query = query.eq("acknowledged", acknowledged)
    else:
        # Default: unacknowledged, not snoozed
        query = query.eq("acknowledged", False)

    result = query.limit(100).execute()
    signals = result.data or []

    # Filter out snoozed signals
    now = datetime.utcnow().isoformat()
    if acknowledged is None:
        signals = [
            s for s in signals
            if not s.get("snoozed_until") or s["snoozed_until"] < now
        ]

    return {"success": True, "signals": signals, "count": len(signals)}


@router.get("/signals/summary")
async def signal_summary(current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]

    # Get all unacknowledged signals
    result = supabase.table("relay_signals") \
        .select("signal_type, confidence") \
        .eq("user_id", user_id) \
        .eq("acknowledged", False) \
        .execute()

    signals = result.data or []
    now = datetime.utcnow().isoformat()

    summary = {
        "total": len(signals),
        "promoted": 0,
        "moved_company": 0,
        "departed": 0,
        "lateral_move": 0,
        "title_change": 0,
        "new_hire_detected": 0,
        "high_confidence": 0,
        "medium_confidence": 0,
        "low_confidence": 0
    }

    for s in signals:
        st = s.get("signal_type", "")
        if st in summary:
            summary[st] += 1
        conf = s.get("confidence", "")
        key = f"{conf}_confidence"
        if key in summary:
            summary[key] += 1

    return {"success": True, "summary": summary}


@router.get("/signals/feed")
async def signal_feed(
    page: int = 1,
    per_page: int = 20,
    current_user: dict = Depends(verify_relay_token)
):
    """Paginated feed of all signals (acknowledged and not)."""
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]
    offset = (page - 1) * per_page

    result = supabase.table("relay_signals") \
        .select("*, relay_contacts(full_name, company, title, tags)") \
        .eq("user_id", user_id) \
        .order("detected_at", desc=True) \
        .range(offset, offset + per_page - 1) \
        .execute()

    total = supabase.table("relay_signals") \
        .select("id", count="exact") \
        .eq("user_id", user_id) \
        .execute()

    return {
        "success": True,
        "signals": result.data or [],
        "page": page,
        "per_page": per_page,
        "total": total.count or 0
    }


@router.patch("/signals/{signal_id}/acknowledge")
async def acknowledge_signal(signal_id: str, current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()

    existing = supabase.table("relay_signals") \
        .select("id") \
        .eq("id", signal_id) \
        .eq("user_id", current_user["relay_user_id"]) \
        .execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Signal not found")

    supabase.table("relay_signals").update({
        "acknowledged": True,
        "acknowledged_at": datetime.utcnow().isoformat()
    }).eq("id", signal_id).execute()

    return {"success": True}


@router.patch("/signals/{signal_id}/snooze")
async def snooze_signal(signal_id: str, data: RelaySnoozeRequest, current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()

    existing = supabase.table("relay_signals") \
        .select("id") \
        .eq("id", signal_id) \
        .eq("user_id", current_user["relay_user_id"]) \
        .execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Signal not found")

    supabase.table("relay_signals").update({
        "snoozed_until": data.snooze_until
    }).eq("id", signal_id).execute()

    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════════
# CREDITS & BILLING
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/credits")
async def get_credits(current_user: dict = Depends(verify_relay_token)):
    supabase = get_supabase()
    user_id = current_user["relay_user_id"]

    user = supabase.table("relay_users") \
        .select("credit_balance, plan, plan_status") \
        .eq("id", user_id) \
        .single() \
        .execute()

    transactions = supabase.table("relay_credit_transactions") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .limit(50) \
        .execute()

    plan = user.data.get("plan", "free") if user.data else "free"
    deep_scan_usage = get_monthly_deep_scan_usage(supabase, user_id)
    plan_config = RELAY_PLANS.get(plan, RELAY_PLANS["free"])

    return {
        "success": True,
        "balance": float(user.data.get("credit_balance", 0)) if user.data else 0,
        "plan": plan,
        "deep_scans_used": deep_scan_usage,
        "deep_scans_included": plan_config["deep_scans_included"],
        "transactions": transactions.data or []
    }


@router.post("/credits/purchase")
async def purchase_credits(
    request: Request,
    pack: str = "10",
    current_user: dict = Depends(verify_relay_token)
):
    """Create Stripe checkout session for credit purchase."""
    if pack not in RELAY_CREDIT_PACKS:
        raise HTTPException(status_code=400, detail="Invalid credit pack")

    pack_config = RELAY_CREDIT_PACKS[pack]
    user_id = current_user["relay_user_id"]

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Relay Intelligence - {pack_config['credits']} Credits",
                        "description": f"{pack_config['credits']} deep scan credits"
                    },
                    "unit_amount": pack_config["price"]
                },
                "quantity": 1
            }],
            mode="payment",
            success_url="https://relay.en-place.ai/credits?purchase=success",
            cancel_url="https://relay.en-place.ai/credits?purchase=cancelled",
            metadata={
                "product": "relay",
                "user_id": user_id,
                "credits": str(pack_config["credits"]),
                "pack": pack
            }
        )
        return {"success": True, "checkout_url": session.url}

    except Exception as e:
        logger.error(f"Stripe checkout error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


@router.post("/subscribe")
async def create_subscription(
    plan: str = "starter",
    current_user: dict = Depends(verify_relay_token)
):
    """Create Stripe checkout for subscription."""
    if plan not in RELAY_STRIPE_PRICES or not RELAY_STRIPE_PRICES[plan]:
        raise HTTPException(status_code=400, detail="Invalid plan or plan not configured")

    user_id = current_user["relay_user_id"]
    supabase = get_supabase()

    # Get or create Stripe customer
    user = supabase.table("relay_users") \
        .select("stripe_customer_id, email, full_name") \
        .eq("id", user_id) \
        .single() \
        .execute()

    if not user.data:
        raise HTTPException(status_code=404, detail="User not found")

    customer_id = user.data.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            email=user.data["email"],
            name=user.data.get("full_name"),
            metadata={"product": "relay", "user_id": user_id}
        )
        customer_id = customer.id
        supabase.table("relay_users").update({
            "stripe_customer_id": customer_id
        }).eq("id", user_id).execute()

    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": RELAY_STRIPE_PRICES[plan], "quantity": 1}],
            mode="subscription",
            success_url="https://relay.en-place.ai/credits?subscribed=true",
            cancel_url="https://relay.en-place.ai/credits?subscribed=cancelled",
            metadata={
                "product": "relay",
                "user_id": user_id,
                "plan": plan
            }
        )
        return {"success": True, "checkout_url": session.url}

    except Exception as e:
        logger.error(f"Subscription checkout error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create subscription checkout")
