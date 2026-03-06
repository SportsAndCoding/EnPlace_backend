# routes/webinars.py
#
# Handles public webinar registration and protected admin registration views.
# Auth dependency: verify_jwt_token (from services.auth_service) — matches all other routes.
# No SendGrid calls yet — stubs are marked TODO for when SendGrid is configured.

import re
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any

from config.settings import SUPABASE_URL, SUPABASE_KEY
from supabase import create_client, Client
from services.auth_service import verify_jwt_token

logger = logging.getLogger(__name__)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

router = APIRouter()


# ── Pydantic Models ──────────────────────────────────────────────────────────

class WebinarRegistrationRequest(BaseModel):
    webinar_id: str
    first_name: str
    last_name: str
    email: str
    restaurant_name: Optional[str] = None
    role: str  # GM | Owner | Operator | Multi-Unit | Other


# ── GET /api/webinars ────────────────────────────────────────────────────────
# Public — no auth required.
# Returns the next upcoming active webinar + last 5 past webinars.
# Called by the marketing site webinars page on load.

@router.get("/api/webinars")
async def get_webinars():
    try:
        now = datetime.now(timezone.utc).isoformat()

        # Next upcoming webinar
        upcoming_result = supabase.table("webinars") \
            .select("*") \
            .eq("is_active", True) \
            .gte("scheduled_at", now) \
            .order("scheduled_at", desc=False) \
            .limit(1) \
            .execute()

        upcoming = upcoming_result.data[0] if upcoming_result.data else None

        # Past webinars (most recent 5, only expose public fields)
        past_result = supabase.table("webinars") \
            .select("id, title, scheduled_at, zoom_join_url, host_name") \
            .eq("is_active", True) \
            .lt("scheduled_at", now) \
            .order("scheduled_at", desc=True) \
            .limit(5) \
            .execute()

        return {
            "upcoming": upcoming,
            "past": past_result.data or []
        }

    except Exception as e:
        logger.error(f"Get webinars error: {e}")
        raise HTTPException(status_code=500, detail="Failed to load webinars.")


# ── POST /api/webinars/register ──────────────────────────────────────────────
# Public — no auth required.
# Registers a prospect. Validates webinar is active and in the future.
# Unique constraint on (webinar_id, email) returns 409 on duplicate.

@router.post("/api/webinars/register")
async def register_for_webinar(payload: WebinarRegistrationRequest):
    try:
        # Normalize email
        email = payload.email.strip().lower()
        if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            raise HTTPException(status_code=400, detail="Invalid email address.")

        # Validate role
        valid_roles = {'GM', 'Owner', 'Operator', 'Multi-Unit', 'Other'}
        if payload.role not in valid_roles:
            raise HTTPException(status_code=400, detail="Invalid role.")

        # Validate required name fields
        first_name = payload.first_name.strip()
        last_name = payload.last_name.strip()
        if not first_name or not last_name:
            raise HTTPException(status_code=400, detail="First and last name are required.")

        # Confirm webinar exists, is active, and is upcoming
        webinar_result = supabase.table("webinars") \
            .select("*") \
            .eq("id", payload.webinar_id) \
            .eq("is_active", True) \
            .limit(1) \
            .execute()

        if not webinar_result.data:
            raise HTTPException(status_code=404, detail="Webinar not found.")

        webinar = webinar_result.data[0]
        scheduled_at = datetime.fromisoformat(
            webinar["scheduled_at"].replace("Z", "+00:00")
        )
        if scheduled_at < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=400,
                detail="This webinar has already taken place."
            )

        # Insert registration
        reg_data = {
            "webinar_id": payload.webinar_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "restaurant_name": payload.restaurant_name.strip() if payload.restaurant_name else None,
            "role": payload.role,
        }

        result = supabase.table("webinar_registrations").insert(reg_data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

        logger.info(f"Webinar registration: {email} -> {webinar['title']}")

        # ── TODO: SendGrid email sequence ─────────────────────────────────────
        # Wire in once SendGrid is configured in Heroku env vars.
        # Trigger order:
        #   1. Immediate confirmation — webinar title, date/time, Zoom join link, .ics attachment
        #   2. 24-hour reminder
        #   3. 1-hour reminder
        #   4. Post-webinar follow-up (attended=True vs attended=False segments)
        #
        # await send_webinar_confirmation(
        #     to_email=email,
        #     first_name=first_name,
        #     webinar_title=webinar["title"],
        #     scheduled_at=webinar["scheduled_at"],
        #     zoom_join_url=webinar.get("zoom_join_url"),
        # )
        # ─────────────────────────────────────────────────────────────────────

        return {
            "success": True,
            "message": f"Registration confirmed for {webinar['title']}",
            "webinar": {
                "title": webinar["title"],
                "scheduled_at": webinar["scheduled_at"],
                "host_name": webinar.get("host_name"),
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        error_str = str(e)
        # Unique constraint violation = duplicate email for this webinar
        if "duplicate" in error_str.lower() or "unique" in error_str.lower() or "23505" in error_str:
            raise HTTPException(
                status_code=409,
                detail="You're already registered for this webinar. Check your email for the confirmation."
            )
        logger.error(f"Webinar registration error: {e}")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")


# ── GET /api/webinars/registrations ─────────────────────────────────────────
# Protected — requires valid JWT.
# Restricted to admin / sales_director portal_access roles.
# Returns all registrations + attended/no-show summary for a given webinar.
# Query param: webinar_id (required)

@router.get("/api/webinars/registrations")
async def get_webinar_registrations(
    webinar_id: str,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    try:
        allowed_roles = {"admin", "sales_director", "sales_manager"}
        portal_access = current_staff.get("portal_access", "")
        if portal_access not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions.")

        result = supabase.table("webinar_registrations") \
            .select("*") \
            .eq("webinar_id", webinar_id) \
            .order("registered_at", desc=False) \
            .execute()

        registrations = result.data or []
        total = len(registrations)
        attended = sum(1 for r in registrations if r.get("attended"))

        return {
            "webinar_id": webinar_id,
            "total_registrations": total,
            "attended": attended,
            "no_show": total - attended,
            "registrations": registrations
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get webinar registrations error: {e}")
        raise HTTPException(status_code=500, detail=str(e))