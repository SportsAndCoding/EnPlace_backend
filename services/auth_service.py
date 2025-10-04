import jwt
from datetime import datetime, timedelta
from typing import Dict, Any
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config.settings import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_HOURS

security = HTTPBearer()

def create_jwt_token(staff_data: Dict[str, Any]) -> str:
    """Create JWT token"""
    payload = {
        "staff_id": staff_data["staff_id"],
        "email": staff_data["email"],
        "full_name": staff_data["full_name"],
        "position": staff_data["position"],
        "portal_access": staff_data["portal_access"],
        "restaurant_id": staff_data["restaurant_id"],
        "restaurant_name": staff_data.get("restaurant_name"),
        "can_edit_staff": staff_data.get("can_edit_staff", False),
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

def require_edit_permission(current_staff: Dict[str, Any] = Depends(verify_jwt_token)) -> Dict[str, Any]:
    """Require can_edit_staff permission"""
    if not current_staff.get("can_edit_staff", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit staff. Contact your system administrator to request the 'Staff Editor' role."
        )
    return current_staff