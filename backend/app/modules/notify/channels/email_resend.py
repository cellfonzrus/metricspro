"""Email delivery via Resend (https://resend.com) — plain HTTPS, no SDK.

Needs env (see app/core/config.py): RESEND_API_KEY, NOTIFY_FROM_EMAIL, NOTIFY_FROM_NAME.
The sending domain in NOTIFY_FROM_EMAIL must be verified in the Resend dashboard.

WHO THE MAIL IS FROM (owner ask 2026-10-17, index §POS-02). The address is the platform's verified
sending address and cannot be per-tenant — the domain is what Resend verified. The DISPLAY NAME can
be, and must be: a customer receiving a receipt should see their own store's name, not the platform's
or another tenant's. So `from_name` is a parameter, resolved by the caller through the ONE home for a
tenant's name (`core/tenant_identity.display_name`), and `NOTIFY_FROM_NAME` is only the fallback for
a send that belongs to no tenant. The header is assembled in ONE place (`from_header`) so no caller
hand-builds `Name <addr>` and gets the quoting wrong.
"""
import base64

import httpx

from app.core.config import settings

RESEND_URL = "https://api.resend.com/emails"


def is_configured() -> bool:
    return bool(settings.RESEND_API_KEY and settings.NOTIFY_FROM_EMAIL)


def from_header(from_name: str | None = None) -> str:
    """THE `From:` header — `Display Name <address@verified-domain>`, or the bare address when there
    is no name to show. A name containing a quote, comma, angle bracket or newline is quoted (and the
    characters that could forge a second header are dropped), because the name comes from a tenant's
    own onboarding field and a header is not a safe place for arbitrary text."""
    addr = settings.NOTIFY_FROM_EMAIL
    name = " ".join(str(from_name or settings.NOTIFY_FROM_NAME or "").split())
    name = name.replace("\\", "").replace('"', "").replace("<", "").replace(">", "")
    if not name:
        return addr
    if any(ch in name for ch in ',;:@()[]'):
        return f'"{name}" <{addr}>'
    return f"{name} <{addr}>"


def tenant_from_name(org_id) -> str:
    """The display name a TENANT's mail carries, resolved here so a sender only has to say WHICH
    tenant the mail belongs to. The name itself has exactly one home
    (`core/tenant_identity.display_name`, reading `storeops.tenants.name` — what onboarding set), and
    a failure to read it is blank, never a guess: mail still goes out, just without a display name."""
    if not org_id:
        return ""
    try:
        from app.core.database import get_supabase
        from app.modules.core import tenant_identity as _ti
        return _ti.display_name(get_supabase(), org_id)
    except Exception:
        return ""


async def send_email(to: str, subject: str, html: str, attachments: list | None = None,
                     from_name: str | None = None, org_id=None) -> str:
    """Send one email. `attachments` = [(filename, bytes, mime), ...]. Returns provider id.

    Raises RuntimeError with the provider message on failure (caught by the caller
    so one bad recipient doesn't abort the batch)."""
    if not is_configured():
        raise RuntimeError("Email sending isn't set up yet — contact support to switch it on.")

    # `org_id` is the usual way a sender says "this is that tenant's mail"; `from_name` is for a
    # caller that already holds the name. Neither → the platform's own NOTIFY_FROM_NAME, which is
    # correct for mail that IS from the platform (a login code, an account-ready notice).
    frm = from_header(from_name or tenant_from_name(org_id))

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
