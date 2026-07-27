"""Retail-ops contributions to the UNIVERSAL admin-attention system (core/import_health.py).

OWNER DIRECTIVE 2026-07-26 (settings + imports audit, every module): "any required-but-missing
configuration or stale/never-delivering feed must SURFACE as an admin notification with plain-
language fix instructions." This module already has ONE self-diagnostic surface
(`GET /closing/readiness`, 2026-07-16) that a tenant has to remember to visit — the providers below
push the SAME class of finding (plus two genuinely new checks) into the login-time popup every other
module's gaps already appear in.

Registered from closing/router.py's own bottom-of-file guarded import (`try: from . import
attention_providers`), NOT from any shared file — no NEEDS CORE, no main.py change. Every provider
function does its own LAZY, call-time-only `from .router import ...` (never at module import time):
router.py imports THIS file at the very end of its own definition, so importing `.router` back at
THIS file's top level would be a real circular import; deferring the import into the function body
sidesteps it entirely (router.py is fully loaded by the time any provider actually runs).

Nothing here writes closing/store-visit data, changes a gate, or touches recon math — read-only,
same as every other provider in core/import_health.py.
"""
from datetime import datetime, timezone, timedelta

try:
    from app.modules.core.import_health import register_provider
except Exception:                      # mig 717 / import_health not present in this deployment yet
    def register_provider(*_a, **_k):
        def _deco(fn):
            return fn
        return _deco


def _now():
    return datetime.now(timezone.utc)


_SEV = {"critical": "error", "warning": "warning", "info": "info"}


@register_provider("closing_readiness", label="Daily Closing readiness", group="other", cost="heavy")
def _p_closing_readiness(client, org_id, ctx):
    """Bridges the EXISTING `/closing/readiness` self-diagnostic (module disabled, no stores, no B2B
    sales source ever, no X-report ever, tender/count-config left at the built-in default) into the
    universal attention feed, so a tenant doesn't have to know that page exists to learn Daily
    Closing isn't fully wired for them. Re-runs the SAME bounded exact-count checks
    `closing_readiness()` already exposes — no new queries invented here.

    cost='heavy' (2026-07-26 audit dispatch: "cost='cheap' ONLY for config-table reads; scans of
    daily_closing history = cost='heavy'"): `closing_readiness()` counts `commcalc.raw_sales` /
    `daily_sales_feed` / `daily_closing` — real transactional tables, not config — so this only runs
    on `deep=1` ("Run full check"), not on every login popup. The two genuinely cheap/real-time gaps
    this audit added (`closing_sweep_credentials`, and `storevisit_checklist_template`) stay
    always-on; this one is a login-time DUPLICATE of a page an admin can already visit, so deferring
    it costs nothing new."""
    try:
        from .router import closing_readiness
        rep = closing_readiness(org_id=org_id)
    except Exception:
        return []
    out = []
    for i in (rep.get("issues") or []):
        out.append({
            "group": "other", "key": f"closing_readiness:{i.get('code')}",
            "severity": _SEV.get(i.get("severity"), "info"),
            "label": "Daily Closing — " + (i.get("code") or "issue").replace("_", " "),
            "detail": i.get("message") or "",
            "count": 1, "deep_link": "/closing/readiness",
            "deep_link_label": "Open Closing Readiness",
        })
    return out


@register_provider("closing_sweep_credentials", label="Daily-closing sheet import health",
                   group="import", cost="cheap")
def _p_closing_sweep_credentials(client, org_id, ctx):
    """The generic 'imports overdue' check (core/import_health.py, feed `sweep:closing`) treats ANY
    completed sweep RUN as fresh evidence even when it FAILED — `core.import_evidence()`'s 'sweep' rows
    report `closing_sweep_config.last_run_at` as the success timestamp regardless of `last_status`.
    A Google-service-account credential that has expired keeps "running" (and refreshing
    `last_run_at`) on schedule while importing zero rows, so that generic check stays silent forever.
    This provider reads the sweep config directly and catches exactly that failure mode, plus the two
    purely-config-level breakages that would ALSO look identical to the generic check: the server has
    no Google credentials at all, and (implicitly, via `last_status`) the sheet not being shared with
    the service account."""
    try:
        from .router import _closing_cfg
        from . import gsheet
    except Exception:
        return []
    try:
        cfg = _closing_cfg(client, org_id) or {}
    except Exception:
        return []
    sheet_id = (cfg.get("sheet_id") or "").strip()
    enabled = bool(cfg.get("enabled"))
    if not sheet_id and not enabled:
        return []                                          # tenant has never touched this connector
    out = []
    try:
        sa_present = gsheet.sa_info() is not None
    except Exception:
        sa_present = False
    if sheet_id and not sa_present:
        out.append({
            "group": "import", "key": "closing_sweep_no_sa", "severity": "error",
            "label": "Daily-closing sheet import has no Google credentials",
            "detail": ("A Google Sheet id is configured for the daily-closing auto-import, but "
                       "GOOGLE_SERVICE_ACCOUNT_JSON is not set on the server — the import can never "
                       "run. Set that environment variable (the service-account JSON), then share "
                       "the sheet with the service account's email on Closing → Auto-Import."),
            "count": 1, "deep_link": "/closing/imports", "deep_link_label": "Open Closing Auto-Import",
        })
    elif sheet_id and (cfg.get("last_status") or "").strip().lower() == "error":
        try:
            sa_email = gsheet.sa_email() or "the service account"
        except Exception:
            sa_email = "the service account"
        out.append({
            "group": "import", "key": "closing_sweep_error", "severity": "error",
            "label": "Daily-closing sheet import is failing",
            "detail": (f"The last run failed: {(cfg.get('last_detail') or 'no detail recorded')[:220]}. "
                       f"Common causes: the sheet isn't shared with {sa_email}, the sheet id/tab was "
                       f"changed or deleted, or the service-account credentials expired. Fix it on "
                       f"Closing → Auto-Import, then use 'Run now' to confirm."),
            "count": 1, "deep_link": "/closing/imports", "deep_link_label": "Open Closing Auto-Import",
        })
    return out


@register_provider("closing_stale_stores", label="Stores selling but not submitting closings",
                   group="other", cost="heavy")
def _p_closing_stale_stores(client, org_id, ctx):
    """HEAVY (scans recent B2B sales + daily_closing history): a store with recent B2B sales activity
    but no daily_closing row in the last N days is either not closing its books at all, or closing
    under a store_code this module can't match to its sales — either way, cash/tender recon has been
    blind for that store the whole time with nothing saying so. N is tenant-configurable
    (storeops.tenants.closing_stale_alert_days, migration 505, default 3, 0 = disabled) via
    /closing/cash-config; falls back to the same default 3 if the migration hasn't run yet."""
    now = ctx.get("now") or _now()
    try:
        rows = (client.schema("storeops").table("tenants").select("closing_stale_alert_days")
                .eq("org_id", org_id).limit(1).execute().data) or []
        n_days = int(rows[0].get("closing_stale_alert_days")) if rows and rows[0].get("closing_stale_alert_days") is not None else 3
    except Exception:
        n_days = 3
    if n_days <= 0:
        return []                                          # tenant explicitly disabled this check
    n_days = min(n_days, 14)                                # bound the scan regardless of a huge config value
    try:
        from .router import _b2b_day
    except Exception:
        return []
    sold_stores = set()
    for i in range(1, n_days + 1):
        day = (now - timedelta(days=i)).date().isoformat()
        try:
            d = _b2b_day(client, org_id, day)
        except Exception:
            continue
        sold_stores |= {c for c, v in (d.get("by_store") or {}).items() if v.get("total")}
    if not sold_stores:
        return []                                          # no B2B data loaded at all → nothing to compare
    since_close = (now - timedelta(days=n_days)).date().isoformat()
    try:
        closed_rows = (client.schema("commcalc").table("daily_closing").select("store_code")
                       .eq("org_id", org_id).gte("close_date", since_close).limit(5000).execute().data) or []
    except Exception:
        return []
    closed_stores = {(r.get("store_code") or "").strip() for r in closed_rows}
    stale = sorted(s for s in sold_stores if s not in closed_stores)
    if not stale:
        return []
    eg = ", ".join(stale[:5]) + (f" +{len(stale) - 5} more" if len(stale) > 5 else "")
    return [{
        "group": "other", "key": "closing_stale_stores", "severity": "warning",
        "label": "Stores selling but not submitting daily closings",
        "detail": (f"{len(stale)} store(s) had B2B sales in the last {n_days} day(s) but no "
                  f"daily_closing submission in that window — cash/tender recon has been blind for "
                  f"them: {eg}. Check that the store is actually able to close (kiosk/app access, an "
                  f"assigned closer) or that its store_code matches what the B2B sales feed uses "
                  f"(commcalc.store_mapping)."),
        "count": len(stale), "deep_link": "/closing/management",
        "deep_link_label": "Open Management Review",
    }]
