"""DEFAULT-CLOSED per-report DATA_GRANT gates for the FOUR What-If report surfaces
(`/commcalc/whatif`, owner directive 2026-08-03: "what if also has 4 reports all of which need to be
gated for permissions").

THE FOUR REPORTS (the page's four tabs, one backend endpoint each):

  key                        tab                                    endpoint
  ────────────────────────── ────────────────────────────────────── ─────────────────────────────────
  whatif_employee_payout     🎯 Employee Payout                     GET /whatif/activation-baseline
  whatif_byod_residual       📶 BYOD → Residuals                    GET /whatif/byod-residual
  whatif_accessory_corr      🔗 Accessories ↔ BYOD ↔ Revenue        GET /whatif/accessory-byod
  whatif_carrier_income      💵 Company Payout / Carrier Income     GET /whatif/carrier-income

Before this module the page was gated by NAV ALONE (`rbac.ts` NAV: module 'commissions', scopes
['all','market']) — i.e. hidden from a sidebar, reachable by URL, and its four endpoints answered any
caller who could reach the backend. Two of them (`byod-residual`, `carrier-income`) additionally rode
`_can_view_carrier_residual`, which is effectively default-OPEN (it only bites when a tenant sets
`residual_visibility='permissioned'`). Employee Payout and the Accessory correlation had NOTHING.

SAME SHAPE AS THE EXISTING DEFAULT-CLOSED GRANTS — deliberately not a new pattern. This is
`account/report_gates.py` + `commcalc/router._can_view_device_cost_recon` verbatim:

    super_admin                                    -> allow
    perms.scope == 'all'   OR  role == 'admin'     -> allow
    key in perms.modules   OR  perms.data[key]     -> allow
    else                                           -> DENY

DEFAULT-CLOSED, and every resolution failure (missing/invalid bearer, core unavailable, DB hiccup)
DEGRADES CLOSED: this gate can only ever hide a What-If tab behind the lock note, never leak one.

THE CARRIER-RESIDUAL GATE IS NOT REPLACED. `byod-residual` and `carrier-income` still call
`_require_carrier_residual` AFTER this one. Passing this gate says "you may open this report"; whether
the tenant additionally restricts raw carrier-residual money is still that gate's decision. Two
independent questions, two independent gates — removing either would be a widening.

NOT MONEY-TOUCHING: no rate, tier, plan rule, payout number or calc input is read or written here. It
only decides WHO MAY OPEN four already-read-only projection reports.

Frontend mirror: `hasDataGrant(perms, '<key>')` via
`(platform)/commcalc/whatif/_components/WhatIfGate.tsx`. The four rows for rbac.ts's `DATA_GRANTS`
registry are filed under `## NEEDS CORE` in `docs/handoffs/commission.md` (rbac.ts is SHARED —
AGENT_CONTRACT §1, READ never edited). **The gate works before that registry edit lands** — it reads
the role's own `permissions` JSONB; the registry only makes the keys tickable in the Roles UI. Until
then: super-admins / 'all'-scope / admin roles pass, everyone else gets the 403 + lock note.
"""
from fastapi import HTTPException

# ── grant keys (must stay identical to the rbac.ts DATA_GRANTS keys filed under NEEDS CORE) ────────
EMPLOYEE_PAYOUT = "whatif_employee_payout"
BYOD_RESIDUAL = "whatif_byod_residual"
ACCESSORY_CORR = "whatif_accessory_corr"
CARRIER_INCOME = "whatif_carrier_income"

WHATIF_REPORTS = (EMPLOYEE_PAYOUT, BYOD_RESIDUAL, ACCESSORY_CORR, CARRIER_INCOME)

# Human labels used in the 403 message (the message always NAMES the report AND the permission).
REPORT_LABELS = {
    EMPLOYEE_PAYOUT: "What-If — Employee Payout",
    BYOD_RESIDUAL: "What-If — BYOD to Residuals",
    ACCESSORY_CORR: "What-If — Accessories / BYOD / Revenue correlation",
    CARRIER_INCOME: "What-If — Company Payout / Carrier Income",
}

# What the Roles UI should show (mirrored into rbac.ts DATA_GRANTS by core — see NEEDS CORE). Kept
# HERE as the single source of truth so the registry rows and the 403 text can never drift apart.
GRANT_REGISTRY = [
    {"key": EMPLOYEE_PAYOUT, "label": "What-If — Employee Payout scenario",
     "help": "The 🎯 Employee Payout tab of /commcalc/whatif: the per-carrier payout template "
             "(Boost components or the carrier's Commission-Plan rules/tiers) and the projector. "
             "DEFAULT-CLOSED — admin-only until granted."},
    {"key": BYOD_RESIDUAL, "label": "What-If — BYOD → Residuals",
     "help": "The 📶 BYOD → Residuals tab of /commcalc/whatif. DEFAULT-CLOSED — admin-only until "
             "granted; the raw carrier-residual money additionally rides 'carrier_residual' when the "
             "tenant sets residual visibility to 'permissioned'."},
    {"key": ACCESSORY_CORR, "label": "What-If — Accessories ↔ BYOD ↔ Revenue",
     "help": "The 🔗 correlation tab of /commcalc/whatif (per store/period BYOD activations vs "
             "accessory revenue vs total revenue). DEFAULT-CLOSED — admin-only until granted."},
    {"key": CARRIER_INCOME, "label": "What-If — Company Payout / Carrier Income",
     "help": "The 💵 Company Payout / Carrier Income tab of /commcalc/whatif — what the carrier / "
             "master-agent pays the COMPANY. DEFAULT-CLOSED — admin-only until granted; also rides "
             "'carrier_residual' when the tenant sets residual visibility to 'permissioned'."},
]


def whatif_report_allowed(caller, key):
    """PURE over an already-resolved caller dict (no I/O, no globals) — the unit-testable core of the
    gate, identical in shape to `account.report_gates.grant_allowed` and
    `device_cost_recon.device_cost_recon_allowed`:
        super_admin / perms.scope=='all' / role=='admin'                 -> True
        `key` in perms.modules, or truthy perms.data[key]                -> True
        anything else (including caller=None)                            -> False
    `perms.modules` may be a list (backend seeds) or a dict (Roles UI writes) — `in` handles both."""
    if not caller or not key:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    if (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin"):
        return True
    if key in (perms.get("modules") or []):
        return True
    if bool((perms.get("data") or {}).get(key)):
        return True
    return False


def _resolve_caller_safe(authorization, org_id=None):
    """Resolve the bearer token to core's caller dict FOR THE ACTING ORG; None on ANY failure (→ deny).

    Core is imported LAZILY (core/** is SHARED — imported, never edited) so an absent/renamed core
    symbol degrades closed instead of breaking commcalc at import time. `org_id` is passed through to
    `_resolve_caller` so a user who is admin in org A but a rep in the ACTING org B is gated by B's
    role — the same acting-org resolution `_require_commission_plans_edit` uses."""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller
        from app.core.database import get_supabase
        uid = _uid_from_token(authorization)
        if not uid:
            return None
        return _resolve_caller(get_supabase(), uid, org_id) if org_id else _resolve_caller(get_supabase(), uid)
    except Exception:
        return None


def can_view_whatif(authorization, key, org_id=None):
    """Gate over the raw Authorization header. DEGRADES CLOSED on any error."""
    try:
        return whatif_report_allowed(_resolve_caller_safe(authorization, org_id), key)
    except Exception:
        return False


def allowed_map(authorization, org_id=None):
    """{grant_key -> bool} for all four reports, from ONE caller resolution. Drives GET
    /whatif/access so the page renders only the tabs the caller may actually open (and so an
    ungranted caller never fires four requests just to collect four 403s)."""
    try:
        caller = _resolve_caller_safe(authorization, org_id)
    except Exception:
        caller = None
    return {k: whatif_report_allowed(caller, k) for k in WHATIF_REPORTS}


def require_whatif_report(authorization, key, org_id=None):
    """Raise 403 (the detail NAMES the report AND the literal grant key) unless the caller holds
    `key`. Call it at the TOP of the endpoint, right after `require_org` and BEFORE any read.

    The detail carries the literal key so the page can recognise its own gate in the thrown message
    (client.ts `api()` surfaces only `detail`, not the status code) and render the lock note instead
    of a raw red error — the same contract `_require_device_cost_recon` uses."""
    if can_view_whatif(authorization, key, org_id):
        return
    label = REPORT_LABELS.get(key, "This What-If report")
    raise HTTPException(403, f"{label} is restricted — you need the '{key}' permission to view it. "
                             f"Ask an admin to grant it on your role (admin-only by default).")


def require_any_whatif(authorization, org_id=None):
    """Raise 403 unless the caller holds AT LEAST ONE of the four report grants. Used by the shared
    ⚙️ Sources READ (`GET /whatif/source-config`), which is page chrome for the four reports: a caller
    who may open none of them has no business enumerating the tenant's residual/income source wiring
    either. (The WRITE stays on `_require_commission_admin` — unchanged, not widened.)"""
    amap = allowed_map(authorization, org_id)
    if any(amap.values()):
        return
    raise HTTPException(403, "The What-If source configuration is restricted — you need one of the "
                             "What-If report permissions ('whatif_employee_payout', "
                             "'whatif_byod_residual', 'whatif_accessory_corr', "
                             "'whatif_carrier_income') to view it.")
