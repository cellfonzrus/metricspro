"""Admin-attention providers for the asset module (mod-asset · settings-audit package, 2026-07-26).

WHAT THIS IS
  Contributes THREE checks to the platform-core "login attention" system
  (backend/app/modules/core/import_health.py — `register_provider` / `GET /core/attention`) that are
  specific to this module and are NOT already covered by the centrally-derived checks (import feed
  staleness from sweep/email/ftp/data_source configs, unmapped stores, duplicate uploads):

    1. asset_ledger_stale    — the asset ledger (Asset_Lending.xlsx, uploaded at /commcalc/asset) has
       never been uploaded, or its FileDate is older than a few days. Uses the SAME freshness signal
       GET /asset/aging's own stale-data banner already uses (raw_row.FileDate off any one row — the
       upload wipes-and-replaces the whole ledger every time, so every row shares one vintage), so
       this can never disagree with what an admin already sees on that page; it just makes the fact
       visible to an admin who never opens it. Only fires for a Boost-carrier tenant — an empty
       ledger is EXPECTED, not an error, for a Total/luxelink-only tenant that doesn't run the
       VIP asset-financing program at all (see docs/handoffs/asset.md, asset-10 luxelink-parity).

    2. asset_market_gap      — asset_ledger rows whose `store` text (VIP's own "Billing Address 1")
       never matched commcalc.store_mapping OR the router's MARKET_OVERRIDES dict during the
       upload-time backfill (router.py's `_backfill_market`), so they carry NO market and silently
       drop out of every market-filtered asset report (Charges Dashboard, RMA, Aging, Owed-Weekly all
       accept a `market` query param). This is DIFFERENT from the centrally-covered "stores not in
       the market map" check (core/import_health.py's own `unmapped_stores` provider, which compares
       storeops.stores against store_mapping) — that one never looks at asset_ledger at all. Reads
       the org-scoped Postgres aggregate `commcalc.asset_market_gap` (migration 302) rather than
       pulling ledger rows into Python (CLAUDE.md: aggregate in Postgres, not Python, for 40k+ rows).
       IMPORTANT (confirmed by reading commcalc/router.py's own comment at its store-aliases route):
       `commcalc.store_aliases` — what the Store-Matching page (/commcalc/store-match) actually
       writes — deliberately does NOT touch `store_mapping`, "which the asset market join depends
       on". So this provider's fix instructions point at Settings → Stores (`/commcalc/settings`,
       `PUT /commcalc/stores/{id}`), the one surface that edits `store_mapping.market` directly —
       resolving an alias in Store Matching would NOT fix this gap.

    3. asset_pipeline_issues — the last upload logged a real pipeline failure (market backfill,
       selling-price backfill, or a flag sync raised) into core.failure_log (category `asset_%`,
       written by router.py's `_log_asset_pipeline_issue` / `_log_degraded_upload_mode`). Those
       writes existed before this package but were invisible to anyone who didn't proactively open
       /failures — this surfaces the SAME rows in the login popup with a plain-language next step.

MULTI-TENANT (RULE ONE): every function takes `org_id` from the caller (the attention aggregator in
import_health.py has already resolved/clamped it from the JWT before calling any provider) and every
read below is `.eq("org_id", org_id)`. No house-org constant is ever used as a data scope.

COST: all three are registered `cost="cheap"` — provider 1 reads exactly ONE row (bounded, matches
the existing /aging endpoint's own already-shipped pattern, not a scan); provider 2 is a single
Postgres-side aggregate RPC (the recommended alternative to a Python-side scan); provider 3 reads a
small, indexed, LIMIT-20 slice of core.failure_log. None pulls the 43k+-row ledger into Python.

DEGRADES GRACEFULLY (contract §5): importing `register_provider` is guarded — if core/import_health.py
is ever refactored to remove/rename it, these checks silently stop contributing and NOTHING else
breaks (not this module's pages, not the login popup for every OTHER module's providers). Every DB
read below is its own try/except returning `[]` on any error (migration 302 not run yet, missing
core.failure_log, missing commcalc.carrier, etc. all degrade to "nothing to report" rather than a 500
or a bad guess).
"""
from datetime import datetime, timezone, timedelta, date

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Mirrors /asset/aging's own stale-data banner threshold
# (frontend/.../commcalc/asset/aging/page.tsx: `staleDays > 3`). Hardcoded on BOTH sides today —
# flagged in docs/handoffs/asset.md as a RULE TWO candidate (a tenant-configurable "expected refresh
# cadence") rather than built here (small, obvious package scope only per the dispatch).
_STALE_DAYS = 3

# How far back to look for pipeline-issue failure_log rows, and how many recent rows to read.
_PIPELINE_LOOKBACK_DAYS = 14
_PIPELINE_ROW_LIMIT = 20


def _is_boost_mode(client, org_id: str) -> bool:
    """True unless the org's configured DEFAULT/only carrier is explicitly non-Boost. Reuses the
    router's single-source carrier-mode resolver (lazy import) with the same inline fallback already
    used twice elsewhere in this codebase (commcalc/sale_installment_engine.py, commcalc/whatif.py) —
    never a second implementation of what counts as 'Boost'."""
    try:
        carriers = (client.schema("commcalc").table("carrier").select("id,name,code,is_default")
                    .eq("org_id", org_id).limit(50).execute().data) or []
    except Exception:
        carriers = []
    try:
        from app.modules.commcalc.router import _resolve_carrier_mode
    except Exception:
        def _resolve_carrier_mode(cs):
            def _is_boost(c):
                return "boost" in ((c.get("code") or "") + " " + (c.get("name") or "")).lower()
            cs = cs or []
            if not cs:
                return "boost"
            d = next((c for c in cs if c.get("is_default")), None)
            if d is not None:
                return "boost" if _is_boost(d) else "plan"
            return "boost" if any(_is_boost(c) for c in cs) else "plan"
    return _resolve_carrier_mode(carriers) == "boost"


def _p_asset_ledger_stale(client, org_id, ctx):
    """Never-uploaded / stale asset ledger — cheap (one-row read, matches /asset/aging exactly)."""
    if not _is_boost_mode(client, org_id):
        return []   # VIP asset-financing is Boost-program-specific; empty is expected elsewhere
    try:
        rows = (client.schema("commcalc").table("asset_ledger").select("raw_row")
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return []   # table itself missing/unreachable — nothing this provider can responsibly say
    if not rows:
        return [{
            "group": "import", "key": "asset_ledger_never_uploaded", "severity": "warning",
            "label": "Asset ledger has never been uploaded",
            "detail": "No Asset_Lending.xlsx has ever been uploaded for this account. Weekly "
                      "Owed-to-VIP billing and Inventory Aging both read from this file and will "
                      "show nothing until it's uploaded. Go to Asset Ledger and click "
                      "“Upload Asset_Lending.xlsx”.",
            "count": 1, "deep_link": "/commcalc/asset",
            "deep_link_label": "Open Asset Ledger / Upload",
        }]
    fd = ((rows[0].get("raw_row") or {}).get("FileDate") or "")[:10]
    if not fd:
        return []   # can't determine an age from this file — say nothing rather than guess
    try:
        y, m, d = (int(x) for x in fd.split("-")[:3])
        age = (date.today() - date(y, m, d)).days
    except Exception:
        return []
    if age <= _STALE_DAYS:
        return []
    return [{
        "group": "import", "key": "asset_ledger_stale", "severity": "error",
        "label": "Asset ledger is stale",
        "detail": f"The Asset Ledger was last refreshed {age} day(s) ago (file dated {fd}). Weekly "
                  f"Owed-to-VIP billing and Inventory Aging are both computed AS OF TODAY from this "
                  f"file, so their numbers drift further from reality every day it isn't refreshed "
                  f"— re-upload the current Asset_Lending.xlsx.",
        "count": age, "deep_link": "/commcalc/asset",
        "deep_link_label": "Open Asset Ledger / Upload",
    }]


def _p_asset_market_gap(client, org_id, ctx):
    """Ledger rows with no market (store text didn't match store_mapping/MARKET_OVERRIDES at
    upload time) — a Postgres aggregate (migration 302), never a Python scan of the ledger."""
    try:
        resp = client.schema("commcalc").rpc("asset_market_gap", {"p_org_id": org_id}).execute()
        data = resp.data
        row = (data[0] if isinstance(data, list) and data else data) or {}
    except Exception:
        return []   # migration 302 not run yet, or the RPC errored — say nothing, never guess
    total = int(row.get("total_rows") or 0)
    gap = int(row.get("unmapped_rows") or 0)
    if not total or not gap:
        return []
    examples = [s for s in (row.get("unmapped_stores") or []) if s]
    eg = (" e.g. " + ", ".join(examples[:3])) if examples else ""
    severity = "error" if gap >= total * 0.5 else "warning"
    return [{
        "group": "mapping", "key": "asset_market_gap", "severity": severity,
        "label": "Asset ledger rows with no market",
        "detail": f"{gap} of {total} asset ledger row(s) have no market because the store text VIP "
                  f"sent for them doesn't match a store address in Store Mapping.{eg} These rows "
                  f"silently drop out of every market-filtered asset report (Charges Dashboard, RMA, "
                  f"Aging, Owed-Weekly). Fix: open Settings → Stores and set the market for that "
                  f"store (the dropdown there edits commcalc.store_mapping directly — the ONLY thing "
                  f"this backfill checks; a Store-Matching alias does NOT fix this, see note below). "
                  f"If the store isn't listed there at all, it needs to be created in StoreOps first "
                  f"with its address spelled EXACTLY like VIP's file, then re-upload the Asset Ledger "
                  f"(or wait for the next scheduled upload) to backfill.",
        "count": gap, "deep_link": "/commcalc/settings",
        "deep_link_label": "Open Settings → Stores",
    }]


# category (core.failure_log, written by router.py) -> a plain-language next step. Anything logged
# under a category not listed here still surfaces (using its own stored message as the detail) —
# this dict only upgrades the KNOWN categories with a friendlier, non-engineer instruction.
_PIPELINE_CATEGORY_HINTS = {
    "asset_upload_degraded_mode": (
        "The last upload used the older, non-atomic save path because a required database change "
        "hasn't been applied yet. Ask an engineer to run "
        "database/migrations/300_asset_ledger_staging_swap.sql."),
    "asset_market_backfill_failed": (
        "The store→market backfill did not finish on the last upload, so some rows may have "
        "the wrong (or no) market. Re-run the upload; if it keeps failing, ask an engineer to check "
        "Store Mapping for this account."),
    "asset_selling_price_backfill_failed": (
        "Selling prices weren't refreshed from Sales on the last upload. Ask an engineer to confirm "
        "database/migrations/009_asset_selling_price.sql has been applied."),
    "asset_appeal_flag_sync_failed": (
        "Appeals & Denied Payments flags weren't refreshed on the last upload. Try the "
        "“Re-sync appeal flags” action on the Asset Ledger page; if it keeps failing, ask "
        "an engineer to check."),
    "asset_rma_flag_sync_failed": (
        "RMA flags weren't refreshed on the last upload. Try the “Re-sync RMA flags” "
        "action on the Asset Ledger page; if it keeps failing, ask an engineer to check."),
    "asset_undercharge_flag_sync_failed": (
        "Undercharge flags weren't refreshed on the last upload. Try the re-price / re-sync action "
        "on the Asset Ledger page."),
}


def _p_asset_pipeline_issues(client, org_id, ctx):
    """Recent core.failure_log rows from this module's own upload pipeline. Those rows are already
    written by router.py's _log_asset_pipeline_issue()/_log_degraded_upload_mode(), but were
    invisible before this package unless an admin happened to open /failures. Bounded read (last 14
    days, 20 rows, org+category filtered) — a small log-table read, not a ledger scan."""
    now = (ctx or {}).get("now") or datetime.now(timezone.utc)
    since = (now.replace(microsecond=0) - timedelta(days=_PIPELINE_LOOKBACK_DAYS)).isoformat()
    try:
        rows = (client.schema("core").table("failure_log")
                .select("id,category,message,created_at")
                .eq("org_id", org_id).ilike("category", "asset_%")
                .gte("created_at", since).order("created_at", desc=True)
                .limit(_PIPELINE_ROW_LIMIT).execute().data) or []
    except Exception:
        return []   # core.failure_log absent (mig 112 not run) or unreachable — nothing to report
    if not rows:
        return []
    latest_by_cat, count_by_cat = {}, {}
    for r in rows:
        c = r.get("category") or ""
        count_by_cat[c] = count_by_cat.get(c, 0) + 1
        if c not in latest_by_cat:
            latest_by_cat[c] = r   # rows are DESC by created_at, so the first hit is the newest
    out = []
    for cat, r in latest_by_cat.items():
        hint = _PIPELINE_CATEGORY_HINTS.get(cat) or (r.get("message") or "See /failures for detail.")
        out.append({
            "group": "import", "key": f"asset_pipeline:{cat}", "severity": "warning",
            "label": f"Asset upload issue: {cat.replace('asset_', '').replace('_', ' ')}",
            "detail": hint, "count": count_by_cat[cat],
            "deep_link": "/commcalc/asset", "deep_link_label": "Open Asset Ledger",
        })
    return out


# ── Registration (import-time; mounted by a one-line import at the bottom of router.py) ────────────
try:
    from app.modules.core.import_health import register_provider
except Exception:
    register_provider = None

if register_provider:
    register_provider("asset_ledger_stale", label="Asset ledger stale / never uploaded",
                      group="import", cost="cheap")(_p_asset_ledger_stale)
    register_provider("asset_market_gap", label="Asset ledger rows with no market",
                      group="mapping", cost="cheap")(_p_asset_market_gap)
    register_provider("asset_pipeline_issues", label="Asset upload pipeline issues",
                      group="import", cost="cheap")(_p_asset_pipeline_issues)
