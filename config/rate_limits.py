from slowapi import Limiter

def get_identifier(request):
    """Get rate limit key: user ID if authenticated, IP if not"""
    # Check for JWT token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            import jwt
            from config.settings import JWT_SECRET, JWT_ALGORITHM
            token = auth_header.split(" ")[1]
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return f"user:{payload.get('staff_id')}"
        except:
            pass
    
    # Get real IP from Heroku's X-Forwarded-For header
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        # X-Forwarded-For can be comma-separated, first one is the real client
        real_ip = forwarded.split(",")[0].strip()
        return f"ip:{real_ip}"
    
    # Fallback to direct client IP
    return f"ip:{request.client.host}"

limiter = Limiter(key_func=get_identifier)

# Limit definitions
LIMITS = {
    "auth": "5/minute",        # Brute force protection
    "write": "30/minute",      # POST/PUT/DELETE
    "read": "100/minute",      # GET endpoints  
    "public": "60/minute",     # Health, root
}