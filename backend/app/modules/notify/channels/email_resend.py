"""Email delivery via Resend (https://resend.com) — plain HTTPS, no SDK.

Needs env (see app/core/config.py): RESEND_API_KEY, NOTIFY_FROM_EMAIL, NOTIFY_FROM_NAME.
The sending domain in NOTIFY_FROM_EMAIL must be verified in the Resend dashboard.
"""
import base64

import httpx

from app.core.config import settings

RESEND_URL = "https://api.resend.com/emails"


def is_configured() -> bool:
    return bool(settings.RESEND_API_KEY and settings.NOTIFY_FROM_EMAIL)


async def send_email(to: str, subject: str, html: str, attachments: list | None = None) -> str:
    """Send one email. `attachments` = [(filename, bytes, mime), ...]. Returns provider id.

    Raises RuntimeError with the provider message on failure (caught by the caller
    so one bad recipient doesn't abort the batch)."""
    if not is_configured():
        raise RuntimeError("Resend not configured (set RESEND_API_KEY + NOTIFY_FROM_EMAIL)")

    frm = settings.NOTIFY_FROM_EMAIL
    if settings.NOTIFY_FROM_NAME:
        frm = f"{settings.NOTIFY_FROM_NAME} <{settings.NOTIFY_FROM_EMAIL}>"

    body = {"from": frm, "to": [to], "subject": subject, "html": html}
    if attachments:
        body["attachments"] = [
            {"filename": fn, "content": base64.b64encode(data).decode("ascii")}
            for (fn, data, _mime) in attachments
        ]

    async with httpx.AsyncClient(timeout=60) as cx:
        r = await cx.post(RESEND_URL, json=body,
                          headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"})
    if r.status_code >= 300:
        raise RuntimeError(f"Resend {r.status_code}: {r.text[:300]}")
    try:
        return r.json().get("id", "")
    except Exception:
        return ""
