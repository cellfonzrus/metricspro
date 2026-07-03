from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SUPABASE_URL: str = "https://etxdalernqqtwjcrtcuj.supabase.co"
    SUPABASE_KEY: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    APP_ENV: str = "development"

    # Business timezone — time-clock punches are stored in UTC but DISPLAYED (kiosk + reports) and
    # date-stamped (work_date) in this zone so an evening ET shift doesn't roll to the next UTC day
    # and the kiosk time matches the report. (Per-store tz can override this later.)
    BUSINESS_TZ: str = "America/New_York"

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

    # ── Sensitive-field encryption (employee PII: SSN/bank/A-Number) ──────────────
    # Fernet key(s) for app-level encryption of sensitive onboarding fields (app/core/crypto.py).
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Unset = graceful passthrough (values stay plaintext; the UI warns). ROTATION: set
    # FIELD_ENCRYPTION_KEYS = "newkey,oldkey" (newest first). ⚠️ Losing the key makes ciphertext
    # unrecoverable — store it in a secrets manager and back it up.
    FIELD_ENCRYPTION_KEY: str = ""
    FIELD_ENCRYPTION_KEYS: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
