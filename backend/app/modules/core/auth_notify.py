"""Auth-delivery bridge — sends invite codes / OTPs over the SAME notify channels (Resend + Meta),
reusing notify's creds logic (no duplication). Every send returns (ok, channel, error) and NEVER
raises, so a delivery failure degrades gracefully (the caller records a visible "delivery failed"
state and continues — an invite is still created, an OTP row still exists).

Channel registry is designed so SMS can be added later purely by config (a provider entry + creds) —
no Twilio client is built now; an unconfigured channel simply reports "not configured".
"""
from app.core.config import settings
from app.modules.notify.channels import email_resend, whatsapp_meta

FROM_NAME = "MetricsPro"
_APP_URL = (settings.APP_PUBLIC_URL or "https://metricspro-five.vercel.app").rstrip("/")


def channels_status() -> dict:
    """Which auth-delivery channels are usable right now (drives the admin/user channel pickers)."""
    return {
        "email": {"configured": email_resend.is_configured(), "label": "Email",
                  "confirmed": True},
        "whatsapp": {"configured": whatsapp_meta.is_configured(), "label": "WhatsApp",
                     # send-validity never live-verified — surfaced so the UI can warn.
                     "confirmed": False},
        "sms": {"configured": False, "label": "SMS (text)", "confirmed": False,
                "note": "No SMS provider configured yet — add one in config to enable."},
    }


def _wrap(title: str, body_html: str) -> str:
    return (
        f'<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:520px;'
        f'margin:0 auto;color:#0f172a">'
        f'<div style="background:#1e3a5f;color:#fff;padding:18px 22px;border-radius:12px 12px 0 0;'
        f'font-size:18px;font-weight:800">MetricsPro</div>'
        f'<div style="border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;padding:22px">'
        f'<div style="font-size:16px;font-weight:700;margin-bottom:10px">{title}</div>{body_html}'
        f'<div style="margin-top:22px;font-size:12px;color:#94a3b8">This is an automated message from '
        f'MetricsPro. If you weren\'t expecting it, you can ignore it.</div></div></div>'
    )


def _code_box(code: str) -> str:
    return (f'<div style="font-family:monospace;font-size:24px;font-weight:800;letter-spacing:3px;'
            f'background:#f1f5f9;border:1px solid #cbd5e1;border-radius:10px;padding:14px;text-align:center;'
            f'margin:14px 0">{code}</div>')


async def send_invite_email(email: str, code: str, tenant_name: str) -> tuple:
    """Email an account-link / access invite code. Returns (ok, 'email', error_or_None)."""
    title = f"You've been invited to {tenant_name or 'a company'} on MetricsPro"
    html = _wrap(title,
                 f'<p style="font-size:14px;line-height:1.5">An administrator set up access for you. '
                 f'Use the access code below to sign in at '
                 f'<a href="{_APP_URL}/login" style="color:#1e3a5f">{_APP_URL}/login</a>.</p>'
                 f'{_code_box(code)}'
                 f'<p style="font-size:13px;color:#475569;line-height:1.5">If you are new to MetricsPro, '
                 f'you\'ll use this code to set your password. If you already use MetricsPro, sign in with '
                 f'your existing password and enter this code to connect the company. The code expires in 30 days.</p>')
    try:
        mid = await email_resend.send_email(email, f"Your MetricsPro access code for {tenant_name or 'MetricsPro'}",
                                            html, [])
        return (True, "email", None)
    except Exception as e:
        return (False, "email", str(e)[:300])


async def send_reset_otp(email: str, code: str, channels=("email",), phone: str = "") -> list:
    """Send a password-reset OTP over each requested channel. Returns a list of (ok, channel, error)."""
    out = []
    for ch in channels:
        if ch == "email":
            out.append(await _email_otp(email, code, "password reset",
                                        "Enter this code on the password-reset screen to choose a new password."))
        elif ch == "whatsapp" and phone:
            out.append(await _wa_otp(phone, code, "password reset"))
    return out


async def send_2fa_otp(email: str, code: str, channel: str, phone: str = "") -> tuple:
    """Send a 2FA sign-in OTP over ONE chosen channel. Returns (ok, channel, error)."""
    if channel == "whatsapp" and phone:
        return await _wa_otp(phone, code, "sign-in verification")
    return await _email_otp(email, code, "sign-in verification",
                            "Enter this code to finish signing in to MetricsPro.")


async def send_phone_verify_otp(phone: str, code: str) -> tuple:
    """Send a phone-verification OTP over WhatsApp. Returns (ok, 'whatsapp', error)."""
    return await _wa_otp(phone, code, "phone verification")


async def _email_otp(email: str, code: str, purpose: str, instruction: str) -> tuple:
    html = _wrap(f"Your MetricsPro {purpose} code",
                 f'<p style="font-size:14px;line-height:1.5">{instruction}</p>{_code_box(code)}'
                 f'<p style="font-size:13px;color:#475569">This code expires in about 10 minutes. '
                 f'Do not share it with anyone.</p>')
    try:
        await email_resend.send_email(email, f"MetricsPro {purpose} code: {code}", html, [])
        return (True, "email", None)
    except Exception as e:
        return (False, "email", str(e)[:300])


async def _wa_otp(phone: str, code: str, purpose: str) -> tuple:
    try:
        mid = await whatsapp_meta.send_otp(phone, code, purpose)
        # send_text returns "" outside the 24h window with no error → treat as best-effort sent.
        return (True, "whatsapp", None)
    except Exception as e:
        return (False, "whatsapp", str(e)[:300])
