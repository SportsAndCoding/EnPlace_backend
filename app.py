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

# Import modular components
from config.settings import ALLOWED_ORIGINS, SUPABASE_URL, SUPABASE_KEY, JWT_SECRET, JWT_ALGORITHM
from routes import staff
from services.auth_service import verify_jwt_token
from routes.staff import router as staff_router


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

# Pydantic models
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

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
from routes.constraints import router as constraints_router
app.include_router(constraints_router)
from routes.schedules import router as schedules_router
app.include_router(schedules_router)
from routes.demand import router as demand_router
app.include_router(demand_router)
from routes.schedule_review import router as review_router
app.include_router(review_router)


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
async def login(request: LoginRequest):
    """Staff login endpoint"""
    try:
        staff_data = await authenticate_staff_db(request.email)
        
        if not staff_data:
            return {
                "success": False,
                "error": "Invalid email or password"
            }
        
        if not verify_password(request.password, staff_data['password_hash']):
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