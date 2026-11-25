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
                     from_name: str | None = None, org_id=None, *, purpose: str,
                     label: str = "", subscription_id=None, filters=None,
                     triggered_by: str = "") -> str:
    """Send one email. `attachments` = [(filename, bytes, mime), ...]. Returns provider id.

    Raises RuntimeError with the provider message on failure (caught by the caller
    so one bad recipient doesn't abort the batch).

    EVERY ATTEMPT IS RECORDED HERE, in `notify.send_log`, through the one home for it
    (`notify/send_record`, index §NOTIFY-02) — accepted or refused, before the exception reaches the
    caller. It is done in the TRANSPORT because thirty-one call sites each decided for themselves
    whether to record a send and every one of them decided not to, which is why "did the employee
    get the onboarding invite" was unanswerable on 2026-11-25 (owner report). `purpose` is therefore
    a REQUIRED keyword argument and has deliberately NO default: a send that cannot say what it is
    cannot be made, and a new caller that forgets fails loudly instead of sending invisibly.
    `harness_email_send_record.py` fails the build if this function stops recording.
    """
    from app.modules.notify import send_record

    def _record(status, mid="", error=""):
        send_record.record_attempt(org_id=org_id, purpose=purpose, channel="email", target=to,
                                   status=status, provider_message_id=mid, error=error,
                                   label=label, subscription_id=subscription_id,
                                   filters=filters, triggered_by=triggered_by)

    if not is_configured():
        note = "Email sending isn't set up yet — contact support to switch it on."
        _record("failed", error=note)
        raise RuntimeError(note)

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

    try:
        async with httpx.AsyncClient(timeout=60) as cx:
            r = await cx.post(RESEND_URL, json=body,
                              headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"})
    except Exception as e:
        # A transport failure (DNS, TLS, timeout) never reached the provider. Before this it was
        # indistinguishable from a send nobody recorded — which is the whole defect.
        _record("failed", error=f"{type(e).__name__}: {e}"[:300])
        raise
    if r.status_code >= 300:
        note = f"Resend {r.status_code}: {r.text[:300]}"
        _record("failed", error=note)
        raise RuntimeError(note)
    try:
        mid = r.json().get("id", "")
    except Exception:
        mid = ""
    _record("sent", mid=mid)
    return mid
