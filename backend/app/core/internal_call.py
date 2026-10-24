"""THE ONE HOME for the headers an INTERNAL SELF-CALL must carry (index §CORE-16).

A handler that needs a sibling module's figure sometimes calls the platform's OWN HTTP surface at
`INTERNAL_API_BASE_URL` (default `http://127.0.0.1:8000`) rather than importing across package
boundaries — mod-retail-ops' envelope payout reads mod-commission's accrual and mod-people's
salary-owed this way, and mod-people pushes its P&L system lines back the same route.

Such a call re-enters `app.core.tenant_middleware` from the outside, so it must prove the SAME two
facts the browser's request proved:

  1. WHO is calling        → `Authorization: Bearer <the caller's own token>`
  2. WHICH TENANT they are → `x-active-org: <the tenant this request already acts as>`
     acting as

Dropping either does not degrade — it REFUSES, and the refusal arrives as a figure:

  • no token            → 401. Fixed 2026-08-04; the comment above `closing/router._sib_headers`
                          records that it read as **$0.00 owed** on every envelope.
  • no acting tenant    → **409 `tenant_choice_required`**, for any login that belongs to more than
                          one company and has no `is_default_org` row (4 such logins; measured
                          2026-10-24, all 4 have `is_default_org IS NULL`). The browser's request
                          names the company in `x-active-org`; the self-call did not forward it, so
                          `tenant_middleware._tenant_is_ambiguous` refused to guess — correctly —
                          BEFORE the handler ran. Reported by the owner 2026-10-24 on the envelope
                          payout screen; `core.access_log` shows the pair of 409s with
                          `actor_auth_id IS NULL` sandwiched between the outer
                          `GET /closing/payout-due` 200s of login `ab08bb15…` acting as Luxelink
                          (`854f6d7b…`), which is admin in three companies.

THE CLASS, not the instance (CLAUDE.md "a fix is a DESIGN fix"): the general fact that was wrong is
not "the envelope screen loses the tenant" but **"a self-call re-enters the gate with less identity
than the request that caused it"**. So the fact lives here and every self-call site DEREFERENCES it;
`harness_internal_self_call.py` fails the build if a new site builds its headers by hand or if a
wired site stops dereferencing this module.

NOT A WIDENING. `x-active-org` is untrusted by design: `tenant_middleware` honours it only when it
names one of the caller's own memberships (a super-admin's cross-tenant administration is already
intentional and already reads the same header). Stamping the tenant the OUTER request was validated
for therefore cannot reach a company the caller could not already reach — it removes a guess, it
does not grant anything.
"""
from __future__ import annotations

# The ONE place the self-call base URL is read. `0.0.0.0:8000` is what the Dockerfile binds, so
# 127.0.0.1:8000 is the correct same-process default; an operator can point it elsewhere.
import os

ACTIVE_ORG_HEADER = "x-active-org"


def internal_api_base() -> str:
    """The base URL a self-call targets."""
    return os.environ.get("INTERNAL_API_BASE_URL") or "http://127.0.0.1:8000"


def acting_tenant(org_id=None) -> str:
    """The tenant a self-call must declare: the one `tenant_middleware` ALREADY validated for this
    request, falling back to the `org_id` the handler is working on.

    The contextvar is the better answer because it is the middleware's own resolution (and for a
    super-admin it is the company they chose in the switcher). It is None outside a request the
    middleware saw — a sweep, a cron job, a harness — and there the handler's `org_id` is the only
    tenant in play, so declaring it is honest rather than a guess. Never raises.
    """
    acting = None
    try:
        from app.core.tenant_middleware import acting_org
        acting = acting_org()
    except Exception:
        acting = None
    return str(acting or org_id or "").strip()


def self_call_headers(authorization="", org_id=None) -> dict:
    """The headers EVERY internal self-call carries. Omits a header rather than sending it empty,
    so a tokenless sweep behaves exactly as it did before this module existed."""
    out = {}
    auth = (authorization or "").strip() if isinstance(authorization, str) else ""
    if auth:
        out["Authorization"] = auth
    org = acting_tenant(org_id)
    if org:
        out[ACTIVE_ORG_HEADER] = org
    return out


# ── THE REFUSAL, NAMED ───────────────────────────────────────────────────────────────────────────
# A degrade-safe caller turns a refusal into a note beside a figure. Naming the status is the
# difference between a ten-minute fix and another blind hunt (the 2026-08-04 401 and the 2026-10-24
# 409 were both read as "the number is zero" for exactly as long as the note said nothing).
def self_call_note(status_code, what, sibling=""):
    """A plain-language note for a self-call the gate refused, or None when `status_code` is not a
    refusal this function speaks for (the caller then raises for status as it always did)."""
    try:
        code = int(status_code)
    except Exception:
        return None
    of = f" ({sibling} package pending)" if sibling else ""
    if code == 404:
        return f"{what} endpoint not deployed yet{of}"
    if code in (401, 403):
        return (f"{what} refused the internal call ({code}) — the caller's Authorization header was "
                f"missing or out of scope, so this figure is NOT $0, it is UNKNOWN")
    if code == 409:
        return (f"{what} refused the internal call (409) — it could not tell which company this "
                f"request is for, so this figure is NOT $0, it is UNKNOWN")
    return None
