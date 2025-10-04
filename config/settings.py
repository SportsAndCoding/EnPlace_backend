import os

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# JWT
JWT_SECRET = os.getenv("JWT_SECRET", "your-secret-key-change-this")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

# CORS
ALLOWED_ORIGINS = [
    "https://app.en-place.ai",
    "https://enplaceappv2.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000"
]