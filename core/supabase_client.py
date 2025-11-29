import os
from typing import Final

from supabase import create_client, Client

# Environment variable names
_SUPABASE_URL_ENV: Final[str] = "SUPABASE_URL"
_SUPABASE_KEY_ENV: Final[str] = "SUPABASE_SERVICE_ROLE_KEY"


def _get_required_env(var_name: str) -> str:
    """Retrieve an environment variable or raise a clear error if missing."""
    value = os.environ.get(var_name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable '{var_name}'. "
            "Please set it before starting the application."
        )
    return value


# Client is created only once at import time in the main process.
# In multiprocessing scenarios (e.g. uvicorn --workers), each worker gets its own module-level instance.
_supabase_client: Client = create_client(
    url=_get_required_env(_SUPABASE_URL_ENV),
    key=_get_required_env(_SUPABASE_KEY_ENV),
)

# Primary export – the client instance
supabase: Client = _supabase_client


def get_supabase() -> Client:
    """
    Helper function to retrieve the Supabase client.
    Useful for dependency injection, testing (where the module can be patched),
    and to make the client explicitly available without relying on module-level imports.
    """
    return supabase