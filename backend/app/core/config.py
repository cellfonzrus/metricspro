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
    # ── Auto-remediation WhatsApp approval (mig 097 Phase 2) ──────────────────────────────
    # A separate approved template with 3 body vars {{1}}=issue {{2}}=fix {{3}}=preview and two
    # QUICK-REPLY buttons ("Approve" idx0, "Reject" idx1). Payloads are injected per-send.
    WHATSAPP_APPROVAL_TEMPLATE: str = "remediation_approval"
    WHATSAPP_APPROVAL_LANG: str = "en"
    # ── WhatsApp OTP delivery (auth 2FA / password reset) ─────────────────────────────────
    # An approved AUTHENTICATION-category template whose single BODY var carries the code (Meta's
    # verification-code template shape). When UNSET, WhatsApp OTP falls back to a plain text message,
    # which Meta only delivers inside the 24h customer-service window → UNCONFIRMED for cold sends.
    # ⚠️ OWNER: WhatsApp OTP delivery has never been live-verified — needs one real-number test.
    WHATSAPP_OTP_TEMPLATE: str = ""
    WHATSAPP_OTP_LANG: str = "en"
    # Webhook (Meta App → WhatsApp → Configuration): the verify token you set on the callback URL,
    # and the app secret to validate X-Hub-Signature-256 on inbound POSTs (optional but recommended).
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""

    # ── Auth hardening (OTP / 2FA / invite delivery) ─────────────────────────────
    # Pepper mixed into the SHA-256 hash of every stored OTP code (core.auth_otp.code_hash), so a DB
    # read alone can't brute-force a 6-digit code offline. Falls back to SUPABASE_SERVICE_KEY when
    # unset (already a high-entropy secret only the backend holds) — so it is never a trivial/blank
    # pepper in prod. Set an independent value to rotate without touching the service key.
    AUTH_OTP_PEPPER: str = ""
    # HMAC secret signing the post-2FA "verified session" marker (x-2fa-token). Falls back to
    # SUPABASE_SERVICE_KEY when unset. Rotating it invalidates every outstanding 2FA marker (users
    # re-verify on their next request) — safe, never a lockout (they just re-run the OTP step).
    AUTH_2FA_SECRET: str = ""

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
