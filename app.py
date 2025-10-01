# En Place FastAPI Backend
# Core staffing focus with authentication

import os
import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # Use service key for server-side
JWT_SECRET = os.getenv("JWT_SECRET", "your-secret-key-change-this")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize FastAPI
app = FastAPI(
    title="En Place API",
    description="Restaurant staff management and scheduling platform",
    version="2.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.en-place.ai",
        "https://enplaceappv2.vercel.app", 
        "http://localhost:3000",
        "http://127.0.0.1:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()

# Pydantic models
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    staff: Optional[Dict[str, Any]] = None
    portal_access: Optional[str] = None
    redirect_url: Optional[str] = None
    error: Optional[str] = None

class StaffCreate(BaseModel):
    staff_id: str
    email: EmailStr
    password: str
    full_name: str
    position: str
    restaurant_id: int
    portal_access: str = "staff"
    hourly_rate: Optional[float] = None

# Utility functions
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
        "restaurant_name": staff_data["restaurant_name"],
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_jwt_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """Verify JWT token and return payload"""
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials"
        )

def get_portal_redirect_url(portal_access: str) -> str:
    """Get redirect URL based on portal access level"""
    if portal_access == "manager":
        return "/manager-dashboard"
    elif portal_access == "staff":
        return "/staff-portal"
    else:
        return "/error"

# Database functions
async def authenticate_staff_db(email: str) -> Optional[Dict[str, Any]]:
    """Authenticate staff using Supabase function"""
    try:
        result = supabase.rpc('authenticate_staff', {'p_email': email}).execute()
        
        if result.data and result.data.get('success'):
            return result.data['staff']
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

# API Endpoints

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "En Place FastAPI Backend",
        "version": "2.0.0",
        "status": "running",
        "authentication": "JWT",
        "focus": "Core staffing components"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test Supabase connection
        result = supabase.table('restaurants').select('id').limit(1).execute()
        db_status = "connected" if result.data is not None else "disconnected"
        
        return {
            "status": "healthy",
            "database": db_status,
            "timestamp": datetime.utcnow().isoformat(),
            "services": ["authentication", "staff_management", "scheduling"]
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy", 
            "database": "disconnected",
            "error": str(e)
        }

@app.post("/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Staff login endpoint"""
    try:
        # Get staff data from database
        staff_data = await authenticate_staff_db(request.email)
        
        if not staff_data:
            return LoginResponse(
                success=False,
                error="Invalid email or password"
            )
        
        # Verify password
        if not verify_password(request.password, staff_data['password_hash']):
            return LoginResponse(
                success=False, 
                error="Invalid email or password"
            )
        
        # Update last login
        await update_last_login_db(staff_data['staff_id'])
        
        # Create JWT token
        token = create_jwt_token(staff_data)
        
        # Get redirect URL
        redirect_url = get_portal_redirect_url(staff_data['portal_access'])
        
        # Remove sensitive data from response
        safe_staff_data = {k: v for k, v in staff_data.items() if k != 'password_hash'}
        
        return LoginResponse(
            success=True,
            token=token,
            staff=safe_staff_data,
            portal_access=staff_data['portal_access'],
            redirect_url=redirect_url
        )
        
    except Exception as e:
        logger.error(f"Login error: {e}")
        return LoginResponse(
            success=False,
            error="An error occurred during login"
        )

@app.post("/auth/create-demo-passwords")
async def create_demo_passwords():
    """Create real password hashes for demo accounts"""
    try:
        # Hash demo passwords
        manager_hash = hash_password("manager123")
        server_hash = hash_password("server123")
        
        # Update demo accounts with real hashes
        supabase.table('staff').update({
            'password_hash': manager_hash
        }).eq('email', 'manager@demobistro.com').execute()
        
        supabase.table('staff').update({
            'password_hash': server_hash  
        }).eq('email', 'server@demobistro.com').execute()
        
        return {
            "success": True,
            "message": "Demo passwords created",
            "accounts": {
                "manager@demobistro.com": "manager123",
                "server@demobistro.com": "server123"
            }
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

# Staff management endpoints
@app.get("/api/staff")
async def get_staff_list(current_staff: Dict[str, Any] = Depends(verify_jwt_token)):
    """Get staff list for restaurant"""
    try:
        restaurant_id = current_staff.get('restaurant_id')
        
        result = supabase.table('staff').select(
            'staff_id, email, full_name, position, hourly_rate, hire_date, status, portal_access'
        ).eq('restaurant_id', restaurant_id).execute()
        
        return {
            "success": True,
            "staff": result.data
        }
    except Exception as e:
        logger.error(f"Get staff list error: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve staff list")

@app.post("/api/staff")
async def create_staff(
    staff_data: StaffCreate,
    current_staff: Dict[str, Any] = Depends(verify_jwt_token)
):
    """Create new staff member"""
    try:
        # Only managers can create staff
        if current_staff.get('portal_access') != 'manager':
            raise HTTPException(status_code=403, detail="Only managers can create staff")
        
        # Hash password
        password_hash = hash_password(staff_data.password)
        
        # Use Supabase function to create staff
        result = supabase.rpc('create_staff_member', {
            'p_staff_id': staff_data.staff_id,
            'p_email': staff_data.email,
            'p_password_hash': password_hash,
            'p_full_name': staff_data.full_name,
            'p_position': staff_data.position,
            'p_restaurant_id': staff_data.restaurant_id,
            'p_portal_access': staff_data.portal_access,
            'p_hourly_rate': staff_data.hourly_rate
        }).execute()
        
        if result.data and result.data.get('success'):
            return {
                "success": True,
                "message": "Staff member created successfully",
                "staff_id": result.data['staff_id']
            }
        else:
            raise HTTPException(status_code=400, detail=result.data.get('error', 'Failed to create staff'))
            
    except Exception as e:
        logger.error(f"Create staff error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create staff member")

# Placeholder endpoints for frontend compatibility
@app.get("/api/notifications")
async def get_notifications(current_staff: Dict[str, Any] = Depends(verify_jwt_token)):
    """Get notifications for current staff"""
    return {
        "success": True,
        "notifications": [],
        "emergencies": [],
        "pending_approvals": []
    }

@app.get("/api/my-schedule")
async def get_my_schedule(current_staff: Dict[str, Any] = Depends(verify_jwt_token)):
    """Get current staff's schedule"""
    return {
        "success": True,
        "schedule": [],
        "next_shift": None
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)