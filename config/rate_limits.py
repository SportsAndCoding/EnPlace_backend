import os
from slowapi import Limiter

def get_identifier(request):
    """Get rate limit key: user ID if authenticated, IP if not"""
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
    
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        real_ip = forwarded.split(",")[0].strip()
        return f"ip:{real_ip}"
    
    return f"ip:{request.client.host}"

# Use Redis for shared state across workers, fallback to memory for local dev
REDIS_URL = os.getenv("REDIS_URL", "memory://")
limiter = Limiter(key_func=get_identifier, storage_uri=REDIS_URL)

LIMITS = {
    "auth": "5/minute",
    "write": "30/minute",
    "read": "100/minute",
    "public": "60/minute",
}
