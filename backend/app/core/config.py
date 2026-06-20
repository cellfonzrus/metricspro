from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SUPABASE_URL: str = "https://etxdalernqqtwjcrtcuj.supabase.co"
    SUPABASE_KEY: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    APP_ENV: str = "development"

    # ── Notify / subscribe (report delivery) ──────────────────────────────────
    # Public base URL of the frontend, used to build "view live report" links.
    APP_PUBLIC_URL: str = "https://metricspro-five.vercel.app"
    # Shared secret guarding POST /notify/run-due (pg_cron sends it as x-notify-secret).
    NOTIFY_RUN_SECRET: str = ""
    # Resend email — sending domain is metricspro.tech (verify it in Resend; override via env).
    RESEND_API_KEY: str = ""
    NOTIFY_FROM_EMAIL: str = "reports@metricspro.tech"
    NOTIFY_FROM_NAME: str = "MetricsPro"
    # Meta WhatsApp Cloud API
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_TEMPLATE_NAME: str = ""
    WHATSAPP_TEMPLATE_LANG: str = "en"  # approved metricspro_report template is 'en' (not en_US)
    WHATSAPP_GRAPH_VERSION: str = "v21.0"
    # Set true ONLY when the approved template has a DOCUMENT header (to attach the file).
    # The current approved metricspro_report template has NO document header → keep false
    # so we send the report link in the body (Meta rejects header params otherwise: #132018).
    WHATSAPP_TEMPLATE_DOC_HEADER: bool = False

    # ── Account Module — Claude-powered accounting engine (#8/#9/#10) ─────────────
    # Drives statement assembly + narrative + the #10 missed-days recon. The engine
    # degrades to deterministic-only (no narrative) when this is unset.
    ANTHROPIC_API_KEY: str = ""
    ACCOUNT_ENGINE_MODEL: str = "claude-opus-4-8"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
