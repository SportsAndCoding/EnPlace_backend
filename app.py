###just updating
import os
import bcrypt
import jwt
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client
import secrets


# Import modular components
from config.settings import ALLOWED_ORIGINS, SUPABASE_URL, SUPABASE_KEY, JWT_SECRET, JWT_ALGORITHM
from routes import staff
from services.auth_service import verify_jwt_token
from routes.staff import router as staff_router
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from fastapi import Request
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from config.rate_limits import limiter, LIMITS

FRONTEND_URL = os.environ.get('FRONTEND_URL', 'https://app.en-place.ai')


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize Sentry error monitoring
SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.1,  # 10% of requests for performance
        environment=os.getenv("ENVIRONMENT", "production"),
        release=f"enplace-api@3.0.0",
        send_default_pii=False,  # Don't send personal info
    )
    logger.info("Sentry error monitoring initialized")

# Initialize FastAPI
app = FastAPI(
    title="En Place API",
    description="Restaurant staff management platform",
    version="3.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.options("/{rest_of_path:path}")
async def preflight_handler(rest_of_path: str):
    return {}

# Pydantic models
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class ChangePasswordRequest(BaseModel):
    email: EmailStr
    current_password: str
    new_password: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


# ===== UTILITY FUNCTIONS (MUST BE BEFORE ENDPOINTS) =====

def hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    """Verify password against hash"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_jwt_token(staff_data: Dict[str, Any]) -> str:
    """Create JWT token for authenticated staff"""
    payload = {
        "staff_id": staff_data["staff_id"],
        "email": staff_data["email"],
        "full_name": staff_data["full_name"],
        "position": staff_data["position"],
        "portal_access": staff_data["portal_access"],
        "restaurant_id": staff_data["restaurant_id"],
        "can_edit_staff": staff_data.get("can_edit_staff", False),
        "exp": datetime.utcnow() + timedelta(hours=24),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def get_portal_redirect_url(portal_access: str) -> str:
    """Get redirect URL based on portal access level"""
    if portal_access == "manager":
        return "/manager-dashboard"
    elif portal_access == "staff":
        return "/staff-portal"
    else:
        return "/error"

async def authenticate_staff_db(email: str) -> Optional[Dict[str, Any]]:
    """Authenticate staff using Supabase function"""
    try:
        result = supabase.rpc('authenticate_staff', {'p_email': email}).execute()
        
        if result.data and len(result.data) > 0:
            row = result.data[0]
            
            if row.get('success') is True:
                staff_obj = row.get('staff')
                
                if isinstance(staff_obj, str):
                    import json
                    return json.loads(staff_obj)
                else:
                    return staff_obj
        
        return None
    except Exception as e:
        logger.error(f"Database authentication error: {e}")
        return None


async def update_last_login_db(staff_id: str) -> bool:
    """Update staff last login timestamp"""
    try:
        result = supabase.rpc('update_staff_last_login', {'p_staff_id': staff_id}).execute()
        return result.data if result.data else False
    except Exception as e:
        logger.error(f"Update last login error: {e}")
        return False

# ===== ROUTES =====
app.include_router(staff.router, prefix="/api/staff", tags=["staff"])
from routes.restaurants import router as restaurants_router
app.include_router(restaurants_router)
from routes.checkins import router as checkins_router
app.include_router(checkins_router)
from routes.manager_logs import router as manager_logs_router
app.include_router(manager_logs_router)
from routes.alignment import router as alignment_router
app.include_router(alignment_router)
from routes.shifts import router as shifts_router
app.include_router(shifts_router)
from routes.escalations import router as escalations_router
app.include_router(escalations_router)
from routes.candidates import router as candidates_router
app.include_router(candidates_router)
from routes.notifications import router as notifications_router
app.include_router(notifications_router)
from routes.dashboard import router as dashboard_router
app.include_router(dashboard_router)
from routes.house_guardian import router as house_guardian_router
app.include_router(house_guardian_router)
from routes.shift_swaps import router as shift_swaps_router
app.include_router(shift_swaps_router)
from routes.schedule import router as schedule_router
app.include_router(schedule_router)
from routes.staff_portal import router as staff_portal_router
app.include_router(staff_portal_router)
from routes.onboarding import router as onboarding_router
app.include_router(onboarding_router)
from routes.stripe_checkout import router as stripe_router
app.include_router(stripe_router)
from routes.registration import router as registration_router
app.include_router(registration_router)
from routes.recruiting import router as recruiting_router
app.include_router(recruiting_router
)
from routes.sms import router as sms_router
app.include_router(sms_router)
from routes.rewards import router as rewards_router
app.include_router(rewards_router)
from routes.sales import router as sales_router
app.include_router(sales_router)
from routes.sales_rep_registration import router as sales_rep_registration_router
app.include_router(sales_rep_registration_router)


@app.get("/")
async def root():
    return {
        "message": "En Place API v3.0",
        "status": "running"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        result = supabase.table('restaurants').select('id').limit(1).execute()
        db_status = "connected" if result.data is not None else "disconnected"
        
        return {
            "status": "healthy",
            "database": db_status,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy", 
            "database": "disconnected",
            "error": str(e)
        }

@app.post("/auth/login")
@limiter.limit(LIMITS["auth"])
async def login(request: Request, login_data: LoginRequest):
    """Staff login endpoint"""
    try:
        staff_data = await authenticate_staff_db(login_data.email)
        
        if not staff_data:
            return {
                "success": False,
                "error": "Invalid email or password"
            }
        
        if not verify_password(login_data.password, staff_data['password_hash']):
            return {
                "success": False, 
                "error": "Invalid email or password"
            }
        
        await update_last_login_db(staff_data['staff_id'])
        token = create_jwt_token(staff_data)
        redirect_url = get_portal_redirect_url(staff_data['portal_access'])
        safe_staff_data = {k: v for k, v in staff_data.items() if k != 'password_hash'}
        
        return {
            "success": True,
            "token": token,
            "staff": safe_staff_data,
            "portal_access": staff_data['portal_access'],
            "redirect_url": redirect_url
        }
        
    except Exception as e:
        logger.error(f"Login error: {e}")
        return {
            "success": False,
            "error": "An error occurred during login"
        }

@app.post("/auth/create-demo-passwords")
async def create_demo_passwords():
    """Create real password hashes for demo accounts"""
    try:
        manager_hash = hash_password("manager123")
        server_hash = hash_password("server123")
        
        supabase.table('staff').update({
            'password_hash': manager_hash
        }).eq('email', 'manager@demobistro.com').execute()
        
        supabase.table('staff').update({
            'password_hash': server_hash  
        }).eq('email', 'server@demobistro.com').execute()
        
        return {
            "success": True,
            "message": "Demo passwords created"
        }
    except Exception as e:
        logger.error(f"Demo password creation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create demo passwords")

@app.get("/auth/me")
async def get_current_staff(current_staff: Dict[str, Any] = Depends(verify_jwt_token)):
    """Get current authenticated staff info"""
    return {
        "success": True,
        "staff": current_staff
    }

@app.post("/auth/logout")
async def logout():
    """Logout endpoint (client-side token removal)"""
    return {
        "success": True,
        "message": "Logged out successfully"
    }

@app.post("/auth/change-password")
async def change_password(request: ChangePasswordRequest):
    """Change password using email + current password verification (no JWT required)"""
    try:
        # Get staff by email
        result = supabase.table('staff').select('staff_id, password_hash').eq(
            'email', request.email
        ).single().execute()
        
        if not result.data:
            return {
                "success": False,
                "error": "Invalid email or password"
            }
        
        # Verify current password
        if not verify_password(request.current_password, result.data['password_hash']):
            return {
                "success": False,
                "error": "Invalid email or password"
            }
        
        # Validate new password
        if len(request.new_password) < 8:
            return {
                "success": False,
                "error": "New password must be at least 8 characters"
            }
        
        # Hash and update new password
        new_hash = hash_password(request.new_password)
        supabase.table('staff').update({
            'password_hash': new_hash
        }).eq('staff_id', result.data['staff_id']).execute()
        
        return {
            "success": True,
            "message": "Password changed successfully"
        }
        
    except Exception as e:
        logger.error(f"Change password error: {e}")
        return {
            "success": False,
            "error": "An error occurred while changing password"
        }

@app.post("/auth/forgot-password")
async def forgot_password(request: ForgotPasswordRequest):
    """
    Generate password reset token and return reset link.
    In production, this would send an email instead of returning the link.
    """
    try:
        # Check if email exists
        result = supabase.table('staff').select('staff_id, email, full_name').eq(
            'email', request.email
        ).single().execute()
        
        if not result.data:
            # Don't reveal whether email exists - always return success
            return {
                "success": True,
                "message": "If this email exists in our system, you will receive a password reset link."
            }
        
        # Generate secure token
        reset_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=1)  # Token valid for 1 hour
        
        # Store token in database
        supabase.table('staff').update({
            'reset_token': reset_token,
            'reset_token_expires': expires_at.isoformat()
        }).eq('staff_id', result.data['staff_id']).execute()
        
        # Build reset URL (adjust domain for production)
        reset_url = f"https://app.en-place.ai/reset-password.html?token={reset_token}"
        
        # TODO: Send email with reset_url when SendGrid is configured
        # For now, log it for development/testing
        logger.info(f"Password reset requested for {request.email}")
        logger.info(f"Reset URL: {reset_url}")
        
        # In dev mode, include the link in response (REMOVE IN PRODUCTION)
        return {
            "success": True,
            "message": "If this email exists in our system, you will receive a password reset link.",
            # DEV ONLY - Remove this line in production:
            "_dev_reset_url": reset_url
        }
        
    except Exception as e:
        logger.error(f"Forgot password error: {e}")
        return {
            "success": False,
            "error": "An error occurred. Please try again."
        }


@app.post("/auth/reset-password")
async def reset_password(request: ResetPasswordRequest):
    """
    Reset password using valid token.
    """
    try:
        # Find staff with this token
        result = supabase.table('staff').select(
            'staff_id, reset_token, reset_token_expires'
        ).eq('reset_token', request.token).single().execute()
        
        if not result.data:
            return {
                "success": False,
                "error": "Invalid or expired reset link. Please request a new one."
            }
        
        # Check if token is expired
        expires_at = datetime.fromisoformat(result.data['reset_token_expires'].replace('Z', '+00:00'))
        if datetime.now(expires_at.tzinfo) > expires_at:
            # Clear expired token
            supabase.table('staff').update({
                'reset_token': None,
                'reset_token_expires': None
            }).eq('staff_id', result.data['staff_id']).execute()
            
            return {
                "success": False,
                "error": "Reset link has expired. Please request a new one."
            }
        
        # Validate new password
        if len(request.new_password) < 8:
            return {
                "success": False,
                "error": "Password must be at least 8 characters."
            }
        
        # Hash and update password, clear reset token
        new_hash = hash_password(request.new_password)
        supabase.table('staff').update({
            'password_hash': new_hash,
            'reset_token': None,
            'reset_token_expires': None
        }).eq('staff_id', result.data['staff_id']).execute()
        
        logger.info(f"Password reset successful for staff_id: {result.data['staff_id']}")
        
        return {
            "success": True,
            "message": "Password has been reset successfully. You can now log in with your new password."
        }
        
    except Exception as e:
        logger.error(f"Reset password error: {e}")
        return {
            "success": False,
            "error": "An error occurred. Please try again."
        }

@app.get("/api/notifications")
async def get_notifications(current_staff: Dict[str, Any] = Depends(verify_jwt_token)):
    """Get notifications for current staff"""
    return {
        "success": True,
        "notifications": []
    }

@app.get("/api/my-schedule")
async def get_my_schedule(current_staff: Dict[str, Any] = Depends(verify_jwt_token)):
    """Get current staff's schedule"""
    return {
        "success": True,
        "schedule": []
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)