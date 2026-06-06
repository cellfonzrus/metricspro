from supabase import create_client, Client
from app.core.config import settings

def get_supabase() -> Client:
    key = settings.SUPABASE_SERVICE_KEY or settings.SUPABASE_KEY
    return create_client(settings.SUPABASE_URL, key)

def get_supabase_admin() -> Client:
    key = settings.SUPABASE_SERVICE_KEY or settings.SUPABASE_KEY
    return create_client(settings.SUPABASE_URL, key)
