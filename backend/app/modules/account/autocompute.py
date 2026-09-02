"""Statement auto-recompute — the scheduled `/account/run-due` sweep + the staleness signal.

WHY THIS EXISTS (BACKLOG finance-7)
-----------------------------------
`account_statements` only ever built on a manual "Compute" click. Two failure modes:
  1. A NEW tenant's books read `{"computed": false}` forever — nobody ever clicks Compute, so
     the P&L / Balance Sheet stay empty even though the tenant has uploaded data.
  2. An EXISTING tenant's statements go STALE the moment a fresh upload lands (new sales, a
     re-swept MI file, an edited journal) — the snapshot no longer matches the tenant's own data.

This module adds:
  • `recompute_due(client, ...)` — the sweep the secret-gated POST /account/run-due calls. For
    EVERY tenant it recomputes the current + prior period statements, but ONLY when they are
    STALE (never computed, or the newest relevant ingest is newer than the snapshot). Idempotent
    and cheap on a quiet tick, so it is safe to schedule frequently. Every tenant runs under
    `core.run_for_tenant` (money_scope="none" — statements are DERIVED books, not rep pay), so a
    missing/inactive tenant is refused fail-closed and every run is audited in core.job_run.
  • `newest_ingest_at(client, org_id, period)` — the newest ingest timestamp across the source
    tables that feed a period's statements. Drives BOTH the stale/skip decision here AND the P&L /
    Balance Sheet staleness banner ("statements N hours older than your newest data — Recompute").

DETERMINISM (finance §7-7, §6-3): this changes WHEN compute runs, never WHAT it computes. The
numbers still come straight from `engine.compute_and_store` → `coa.build_inputs`, byte-identical.

DEGRADES GRACEFULLY (contract §5): every source-table probe is wrapped so a table/column that a
tenant's schema lacks (or a not-yet-run migration) is skipped, never raised. The staleness signal
simply ignores a source it cannot read.
"""
from datetime import datetime, timezone

from app.core.config import settings
from app.modules.account import statement_engine as engine   # 2026-09-02: the sweep computes via
# statement_engine.compute_and_store — same deterministic assembly + the balance-sheet truths
# (handset payables, inventory basis, fixed journal company scoping) + the stored Cash Flow.
# Aliased as `engine` so every call site below is unchanged; defaults byte-identical
# (harness_statement_engine.py).
from app.modules.account._period import period_keys, _MONTHS
from app.modules.core.run_for_tenant import run_for_tenant, TenantNotRunnable, SCOPE_NONE

# ── source tables whose ingest timestamp makes a period's statements stale ─────────────────────
# (table, [candidate ts columns]). Candidates are tried in order; the first that resolves wins,
# and a table whose columns don't exist for this tenant is skipped (graceful — see _table_newest_ts).
# PERIOD-scoped sources filter on the period spellings; POINT-IN-TIME sources (asset_ledger,
# inventory value, inter-store borrowings) always feed the CURRENT Balance Sheet, so they carry no
# period filter and count toward every open period.
_PERIOD_SOURCES = [
    ("raw_sales",           ["created_at"]),
    # daily_sales_feed is a NON-Boost tenant's PRIMARY sales source (the daily B2B feed, which
    # coa._sales_union_rows reads for the open month BEFORE/without promotion into raw_sales). Its
    # ingest column is `uploaded_at`, NOT created_at. Missing here, a feed-only tenant (luxelink)
    # yields newest_ingest_at=None → recompute_due treats it as "no account data" → the sweep SKIPS
    # it → its P&L / Balance Sheet snapshots NEVER compute (permanently empty), and the P&L/BS
    # staleness banner never prompts a recompute. This is the "same data, not wired" root cause: the
    # coa READ-path was universalized (dcb0807) but this data-DETECTION list was never mirrored.
    # (created_at kept as a defensive 2nd candidate; a table without uploaded_at falls through.)
    ("daily_sales_feed",    ["uploaded_at", "created_at"]),
    ("raw_mi",              ["created_at"]),
    # raw_ma_commission / raw_ma_daily_tx are the VidaPay/MA (Total, luxelink) residual sources that
    # coa.build_inputs falls through to when a tenant has NO raw_mi — the carrier-agnostic MI/ATU
    # income lines (dcb0807 / 87c182d). They must count as "this tenant has data for the period" for
    # the same reason as the feed above, else an MA-only tenant's books never auto-compute / go stale.
    ("raw_ma_commission",   ["created_at"]),
    ("raw_ma_daily_tx",     ["created_at"]),
    ("raw_comp_report",     ["created_at"]),
    ("rep_commissions",     ["created_at"]),
    # store_expenses carries BOTH hand-entered expenses and every AUTO "system line"
    # (source_key non-null): PTO accrual, gross payroll, payroll burden, and — EEP 2026-08-04 —
    # 'additional_payroll' (mod-people) + 'closing_expense:<category-id>' (mod-retail-ops).
    # A system-line POST is a DELETE-by-(org,period,source_key) followed by an INSERT of the fresh
    # cells (commcalc.upsert_expense_system_line), and store_expenses.created_at defaults to now()
    # (mig 002) — so every re-post writes a NEWER created_at than the last statement snapshot and
    # correctly marks the period stale. No extra wiring is needed for the new producers; they ride
    # the same probe. (A hypothetical in-place UPDATE producer would NOT bump created_at — if one is
    # ever added, this entry needs an updated_at candidate.)
    ("store_expenses",      ["created_at"]),
    ("chargeback_items",    ["created_at", "decided_at"]),
    ("journal_entries",     ["created_at"]),
    ("vip_invoices",        ["created_at"]),
    ("vip_paygo_payments",  ["swept_at", "created_at"]),
    ("vip_credit_memos",    ["swept_at", "created_at"]),
]
# ⚠️ NOT SOURCES — the envelope cash ledgers (commcalc.envelope_withdrawal,
# commcalc.commission_payout_ledger, storeops.salary_advance_ledger, EEP 2026-08-04) are cash
# MOVEMENTS against costs already booked from clock-in payroll / rep_commissions. They must never
# appear in either list: the P&L does not read them (coa.build_inputs has no such query), so making
# them a staleness trigger would only churn recomputes — and adding them as a coa source would
# double-count. Their P&L-visible consequence reaches the books ONLY as the derived
# 'additional_payroll' / 'closing_expense:*' store_expenses system lines above.
_POINT_IN_TIME_SOURCES = [
    ("asset_ledger",     ["updated_at", "created_at"]),
    ("inventory_value",  ["updated_at"]),
    # The unsold-phone device ledger (owner defect #1, 2026-09-02): under the 'devices' inventory
    # basis the Balance Sheet reads inventory_aging_device directly, so a fresh emailed Inventory
    # Aging file must mark the open periods stale. For 'report'-basis orgs this at most co-triggers
    # with the inventory_value upsert the same ingest already writes — no spurious churn.
    ("inventory_aging_device", ["updated_at", "created_at"]),
    ("store_borrowings", ["updated_at", "created_at"]),
]


# ── timestamp helpers ──────────────────────────────────────────────────────────────────────────
def _parse_iso(s):
    if not s:
        return None
    try:
        t = str(s).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        # normalize naïve → UTC so aware/naïve never collide on compare
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _later(a, b):
    """True if ISO string `a` is strictly newer than ISO string `b` (either may be None)."""
    da, db = _parse_iso(a), _parse_iso(b)
    if da is None:
        return False
    if db is None:
        return True
    return da > db


def _max_ts(a, b):
    """The later of two ISO timestamp strings (either may be None)."""
    return a if _later(a, b) else (b if b is not None else a)


def _table_newest_ts(client, table, org_id, keys, candidates):
    """Newest ingest timestamp in one org-scoped (optionally period-scoped) source table.
    Tries each candidate ts column; the first that the schema resolves wins (even if it yields no
    rows). A table/column the tenant's schema lacks raises → we try the next candidate, then skip."""
    for col in candidates:
        try:
            q = client.schema("commcalc").table(table).select(col).eq("org_id", org_id)
            if keys is not None:
                q = q.in_("period", list(keys))
            rows = q.order(col, desc=True).limit(1).execute().data or []
            return rows[0].get(col) if rows else None
        except Exception:
            continue
    return None


def newest_ingest_at(client, org_id, period):
    """The newest ingest timestamp (ISO string, or None) across every source table that feeds
    `period`'s P&L / Balance Sheet — the "your newest data" side of the staleness banner and the
    stale/skip decision in recompute_due. None ⇒ this tenant has no account data for the period."""
    keys = period_keys(period)
    newest = None
    for table, cands in _PERIOD_SOURCES:
        newest = _max_ts(newest, _table_newest_ts(client, table, org_id, keys, cands))
    for table, cands in _POINT_IN_TIME_SOURCES:
        newest = _max_ts(newest, _table_newest_ts(client, table, org_id, None, cands))
    return newest


def statements_computed_at(client, org_id, period):
    """The newest computed_at across a period's stored statement snapshots (any scope), or None if
    the period was never computed. All scopes are written together, so the max is the build time."""
    try:
        rows = (client.schema("commcalc").table("account_statements").select("computed_at")
                .eq("org_id", org_id).eq("period", period)
                .order("computed_at", desc=True).limit(1).execute().data) or []
        return rows[0].get("computed_at") if rows else None
    except Exception:
        return None


def staleness(client, org_id, period, computed_at=None):
    """The staleness block the P&L / Balance Sheet endpoints return so the page can show
    "statements N hours older than your newest data — Recompute".

    `computed_at` may be passed from the statement row already read (avoids a re-query); when None
    it is looked up. `stale` is True when the tenant HAS data for the period AND the snapshot is
    either missing (never computed) or older than the newest ingest. A period with no data is never
    "stale" (there is nothing to compute)."""
    if computed_at is None:
        computed_at = statements_computed_at(client, org_id, period)
    newest = newest_ingest_at(client, org_id, period)
    return {
        "computed_at": computed_at,
        "newest_ingest_at": newest,
        "stale": needs_recompute(computed_at, newest, force=False),
    }


def needs_recompute(computed_at, newest_ingest, force=False):
    """The single stale/skip rule, shared by the sweep and the banner.
      • force                     → always recompute
      • no ingest for the period  → nothing to (re)compute (never build empty statements)
      • has data, never computed  → recompute (the new-tenant fix)
      • has data, ingest newer    → recompute (stale vs the tenant's own upload)
      • otherwise                 → up to date, skip"""
    if force:
        return True
    if newest_ingest is None:
        return False
    if computed_at is None:
        return True
    return _later(newest_ingest, computed_at)


# ── period labelling (month-name form — the spelling the pages read) ───────────────────────────
def _period_label(y, m):
    return f"{_MONTHS[m]} {y}"


def current_and_prior_periods(now=None):
    """(prior, current) period labels in the month-name spelling the frontend uses ("June 2026").
    CHRONOLOGICAL ORDER (prior first) so recompute builds the prior month before the current one —
    the current month's retained-earnings roll-up (engine._prior_accum_ni) then sees it."""
    if now is None:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(settings.BUSINESS_TZ))
        except Exception:
            now = datetime.now(timezone.utc)
    y, m = now.year, now.month
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    return _period_label(py, pm), _period_label(y, m)


# ── the sweep ──────────────────────────────────────────────────────────────────────────────────
def recompute_due(client, *, only_org=None, force=False, now=None):
    """Recompute the current + prior period statements for every tenant with account data, but only
    where stale (or force=True). Each tenant runs under core.run_for_tenant (money_scope="none"):
      • a missing / inactive tenant is REFUSED fail-closed (never written to);
      • an active tenant with no account data for either period is a no-op (skipped);
      • an active tenant with stale/absent statements is recomputed via engine.compute_and_store
        (deterministic — this changes WHEN, not WHAT).
    `only_org` limits the sweep to one tenant (walkthrough / targeted refresh)."""
    prior, current = current_and_prior_periods(now)
    periods = [prior, current]  # prior first (retained-earnings roll-up)

    tenants = (client.schema("storeops").table("tenants")
               .select("org_id,name,is_active").execute().data) or []
    if only_org:
        tenants = [t for t in tenants if t.get("org_id") == only_org]

    results = []
    for t in tenants:
        oid = t.get("org_id")
        if not oid:
            continue

        def _job(ctx, _periods=periods):
            out = {"skipped": True, "recomputed": [], "up_to_date": [], "reason": None}
            saw_data = False
            for p in _periods:
                ingest = newest_ingest_at(ctx.client, ctx.org_id, p)
                computed_at = statements_computed_at(ctx.client, ctx.org_id, p)
                if ingest is not None:
                    saw_data = True
                if needs_recompute(computed_at, ingest, force=force):
                    r = engine.compute_and_store(ctx.client, ctx.org_id, p)
                    out["recomputed"].append({"period": p, "snapshots": r.get("snapshots")})
                    out["skipped"] = False
                elif computed_at is not None:
                    out["up_to_date"].append(p)
            if not saw_data and not out["recomputed"] and not out["up_to_date"]:
                out["reason"] = "no account data"
            return out

        row = {"org_id": oid, "name": t.get("name")}
        try:
            job_out = run_for_tenant(oid, "account.recompute", _job,
                                     client=client, money_scope=SCOPE_NONE)
            row.update(status="ok", **job_out)
        except TenantNotRunnable as e:
            row.update(status="refused", reason=str(e))
        except Exception as e:
            row.update(status="error", reason=f"{type(e).__name__}: {e}")
        results.append(row)

    return {"periods": periods, "force": force, "tenants": len(tenants), "results": results}
