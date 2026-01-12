"""
EN PLACE STAFF SELF-REGISTRATION
Public endpoints for staff joining via QR code / join link
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
import bcrypt
import json
from openai import OpenAI
from database.supabase_client import get_supabase

client = OpenAI()  # Uses OPENAI_API_KEY env var

router = APIRouter(prefix="/api/public/join", tags=["staff-join"])


# ═══════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════

class ValidateCodeResponse(BaseModel):
    valid: bool
    restaurant_name: Optional[str] = None
    error: Optional[str] = None


class FindMatchRequest(BaseModel):
    join_code: str
    first_name: str
    last_name: str


class FindMatchResponse(BaseModel):
    success: bool
    match_found: bool = False
    staff_id: Optional[str] = None
    full_name: Optional[str] = None
    position: Optional[str] = None
    confidence: Optional[str] = None
    error: Optional[str] = None


class StaffJoinRequest(BaseModel):
    join_code: str
    staff_id: str  # Confirmed staff_id from roster
    email: EmailStr
    phone: str
    password: str
    sms_consent: bool


class StaffJoinResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    error: Optional[str] = None
    staff_id: Optional[str] = None
    token: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


async def match_staff_with_openai(entered_name: str, roster: list) -> dict:
    """
    Use OpenAI to find the best match from the roster.
    """
    if not roster:
        return {"match_found": False}
    
    roster_text = "\n".join([
        f"- ID: {s['staff_id']}, Name: {s['full_name']}, Position: {s['position'] or 'Unknown'}"
        for s in roster
    ])
    
    prompt = f"""You are matching a staff member who is trying to register to an existing roster.

The person entered their name as: "{entered_name}"

Here is the unclaimed staff roster:
{roster_text}

Find the best match considering:
- Nicknames (Billy = William, Bob = Robert, Mike = Michael, etc.)
- Common misspellings
- Name order variations

Respond with JSON only:
{{
    "match_found": true/false,
    "staff_id": "matched staff_id or null",
    "full_name": "matched full name or null", 
    "position": "matched position or null",
    "confidence": "high/medium/low",
    "reasoning": "brief explanation"
}}

If no reasonable match exists, set match_found to false."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        
        return json.loads(response.choices[0].message.content)
        
    except Exception as e:
        print(f"OpenAI matching error: {str(e)}")
        return {"match_found": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@router.get("/{code}", response_model=ValidateCodeResponse)
async def validate_join_code(code: str):
    """
    Validate a staff join code.
    Public endpoint - no auth required.
    """
    supabase = get_supabase()

    result = supabase.table("restaurant_onboarding_status") \
        .select("restaurant_id, join_code") \
        .eq("join_code", code.upper()) \
        .execute()

    if not result.data:
        return ValidateCodeResponse(
            valid=False,
            error="Invalid join code"
        )

    restaurant_id = result.data[0]["restaurant_id"]

    # Get restaurant name
    restaurant = supabase.table("restaurants") \
        .select("name") \
        .eq("id", restaurant_id) \
        .single() \
        .execute()

    if not restaurant.data:
        return ValidateCodeResponse(
            valid=False,
            error="Restaurant not found"
        )

    return ValidateCodeResponse(
        valid=True,
        restaurant_name=restaurant.data["name"]
    )


@router.post("/find-match", response_model=FindMatchResponse)
async def find_roster_match(data: FindMatchRequest):
    """
    Find matching unclaimed staff member using AI.
    Public endpoint - no auth required.
    """
    supabase = get_supabase()

    # Validate join code and get restaurant_id
    code_result = supabase.table("restaurant_onboarding_status") \
        .select("restaurant_id") \
        .eq("join_code", data.join_code.upper()) \
        .execute()

    if not code_result.data:
        return FindMatchResponse(
            success=False,
            error="Invalid join code"
        )

    restaurant_id = code_result.data[0]["restaurant_id"]

    # Get unclaimed staff (no password_hash)
    roster_result = supabase.table("staff") \
        .select("staff_id, full_name, position") \
        .eq("restaurant_id", restaurant_id) \
        .is_("password_hash", "null") \
        .eq("status", "active") \
        .execute()

    if not roster_result.data:
        return FindMatchResponse(
            success=True,
            match_found=False,
            error="No unclaimed staff found. Please contact your manager."
        )

    # Use OpenAI to find match
    entered_name = f"{data.first_name} {data.last_name}"
    match_result = await match_staff_with_openai(entered_name, roster_result.data)

    if match_result.get("match_found"):
        return FindMatchResponse(
            success=True,
            match_found=True,
            staff_id=match_result.get("staff_id"),
            full_name=match_result.get("full_name"),
            position=match_result.get("position"),
            confidence=match_result.get("confidence")
        )
    else:
        return FindMatchResponse(
            success=True,
            match_found=False,
            error="We couldn't find you on the roster. Please contact your manager."
        )


@router.post("", response_model=StaffJoinResponse)
async def staff_join(data: StaffJoinRequest):
    """
    Complete staff registration by updating existing roster entry.
    Public endpoint - no auth required.
    """
    supabase = get_supabase()

    # Validate SMS consent
    if not data.sms_consent:
        return StaffJoinResponse(
            success=False,
            error="SMS consent is required to receive shift notifications"
        )

    # Validate join code
    code_result = supabase.table("restaurant_onboarding_status") \
        .select("restaurant_id") \
        .eq("join_code", data.join_code.upper()) \
        .execute()

    if not code_result.data:
        return StaffJoinResponse(
            success=False,
            error="Invalid join code"
        )

    restaurant_id = code_result.data[0]["restaurant_id"]

    # Verify staff_id exists and is unclaimed
    staff_result = supabase.table("staff") \
        .select("staff_id, full_name, password_hash") \
        .eq("staff_id", data.staff_id) \
        .eq("restaurant_id", restaurant_id) \
        .single() \
        .execute()

    if not staff_result.data:
        return StaffJoinResponse(
            success=False,
            error="Staff member not found"
        )

    if staff_result.data.get("password_hash"):
        return StaffJoinResponse(
            success=False,
            error="This account has already been claimed"
        )

    # Check email isn't already used
    email_check = supabase.table("staff") \
        .select("staff_id") \
        .eq("restaurant_id", restaurant_id) \
        .eq("email", data.email.lower()) \
        .execute()

    if email_check.data:
        return StaffJoinResponse(
            success=False,
            error="This email is already in use"
        )

    # Update the staff record with credentials
    try:
        supabase.table("staff").update({
            "email": data.email.lower(),
            "phone": data.phone,
            "password_hash": hash_password(data.password),
            "is_portal_enabled": True,
            "sms_notifications_enabled": True,
            "sms_opt_in_date": datetime.utcnow().isoformat()
        }).eq("staff_id", data.staff_id).execute()

        # Generate JWT token
        from services.auth_service import create_jwt_token

        token = create_jwt_token(
            staff_id=data.staff_id,
            restaurant_id=restaurant_id,
            portal_access="staff"
        )

        full_name = staff_result.data["full_name"]
        first_name = full_name.split()[0] if full_name else "there"

        return StaffJoinResponse(
            success=True,
            message=f"Welcome to the team, {first_name}!",
            token=token
        )

    except Exception as e:
        print(f"Staff join error: {str(e)}")
        return StaffJoinResponse(
            success=False,
            error="Something went wrong. Please try again."
        )