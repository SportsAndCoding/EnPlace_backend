import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config.settings import ALLOWED_ORIGINS
from routes import staff
# Import your existing auth route or keep it in app.py for now

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

# Include routers
app.include_router(staff.router, prefix="/api/staff", tags=["staff"])

@app.get("/")
async def root():
    return {
        "message": "En Place API v3.0",
        "status": "running"
    }

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

@app.post("/auth/login")
async def login(request: LoginRequest):
    """Staff login endpoint"""
    try:
        # Get staff data from database
        staff_data = await authenticate_staff_db(request.email)
        
        if not staff_data:
            return {
                "success": False,
                "error": "Invalid email or password"
            }
        
        # Verify password
        if not verify_password(request.password, staff_data['password_hash']):
            return {
                "success": False, 
                "error": "Invalid email or password"
            }
        
        # Update last login
        await update_last_login_db(staff_data['staff_id'])
        
        # Create JWT token
        token = create_jwt_token(staff_data)
        
        # Get redirect URL
        redirect_url = get_portal_redirect_url(staff_data['portal_access'])
        
        # Remove sensitive data from response
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