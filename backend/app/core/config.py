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
    # Public base URL of the BACKEND itself (Railway) — used to build no-login signed download links
    # (GET /api/v1/notify/dl/{token}) that stream a sent report file directly (owner directive: "send the
    # PDF as is without logging in"). Override via env if the backend host changes.
    API_PUBLIC_URL: str = "https://metricspro-production.up.railway.app"
    # Shared secret guarding POST /notify/run-due (pg_cron sends it as x-notify-secret).
    NOTIFY_RUN_SECRET: str = ""

    # ── Auto-Fix Pipeline (mig 718) — the AGENT door into /api/v1/core/fix-pipeline/* ──────────
    # Least-privilege service secret presented as `x-fix-pipeline-secret` by the scheduled triage
    # routine (which has no JWT), same precedent as NOTIFY_RUN_SECRET. It is scoped IN CODE to
    # feed-read + fix-request registry read/write (fix_pipeline.SECRET_CAPS): it can never approve,
    # never mark anything pushed, never edit the token-rate table, and — because no other endpoint in
    # the app reads this header — it unlocks nothing else. UNSET (the default) = the agent door is
    # CLOSED (an empty secret can never match), so the browser/super-admin board is unaffected until
    # the operator sets it on Railway. Never log it, never commit a value.
    FIX_PIPELINE_SECRET: str = ""
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
    # POST /api/v1/remediation/whatsapp-webhook is on the PUBLIC allowlist (Meta carries no JWT), so its
    # ONLY authentication is Meta's X-Hub-Signature-256. Default TRUE = an UNSET WHATSAPP_APP_SECRET makes
    # the POST reject (403) instead of accepting anonymous payloads — a spoofed inbound could otherwise
    # drive the remediation free-text YES/NO approval path or write fake delivery statuses. Break-glass:
    # WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE=0 restores the pre-2026-08-05 verify-only-if-secret-set behaviour
    # with one Railway env change and no code rollback. The GET verification handshake is unaffected (it
    # self-gates on WHATSAPP_VERIFY_TOKEN, which is already fail-closed when unset).
    WHATSAPP_WEBHOOK_REQUIRE_SIGNATURE: bool = True
    # ── WhatsApp 24h customer-service window (delivery-truth ladder) ───────────────────────
    # Meta only delivers FREE-FORM (non-template) messages — including the `type:document` rung that
    # attaches the real report file — inside 24h of the recipient's last inbound message. Outside it the
    # Graph API frequently still answers 200 + a wamid and then drops the message asynchronously (owner
    # incident 2026-08-05: luxelink sends 'sent' with real wamids, nothing delivered, zero Meta
    # conversations in 30 days). We therefore attempt the free-form rung ONLY with positive evidence of an
    # open window (an inbound recorded by the Meta webhook, notify.whatsapp_window). Hours is tunable and
    # deliberately a little under Meta's 24 so a send racing the boundary falls back to the template.
    WHATSAPP_WINDOW_HOURS: float = 23.0
    # Break-glass: TRUE restores the pre-2026-08-05 ladder EXACTLY (free-form document attempted even with
    # no window evidence). Leave FALSE — TRUE reintroduces the silent-drop failure mode.
    WHATSAPP_FREEFORM_WHEN_UNKNOWN: bool = False

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
    # HMAC secret signing no-login report-download tokens (notify.download_token). Falls back to
    # AUTH_2FA_SECRET → SUPABASE_SERVICE_KEY when unset (never a trivial constant in prod). Rotating it
    # invalidates outstanding download links (they 404) — safe, they are short-lived (default 7-day expiry).
    NOTIFY_DOWNLOAD_SECRET: str = ""

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
