from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SUPABASE_URL: str = "https://etxdalernqqtwjcrtcuj.supabase.co"
    SUPABASE_KEY: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    APP_ENV: str = "development"
    
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
