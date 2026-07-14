"""core.run_for_tenant — the central guarded entrypoint every background/sweep job runs under.

WHY THIS EXISTS
---------------
Background jobs (the `*/run-due` sweeps, the commission recompute, the asset upload wipe) run with the
service key and NO human in the loop. Two failure modes have already bitten production:

  1. **Tenant mis-fire.** A job runs for an org_id that has no tenant row (or a deactivated one) and
     pours writes into a phantom / dead tenant. (See platform-core handoff: the Luxelink-mailbox and
     1,230 mis-imported `ma_daily_tx` incidents.)
  2. **Anomalous org-wide money write.** The 2026-07-13 plan-mode "$0 incident": a recompute
     delete-then-insert replaced an entire tenant's commission rows with all-$0 (or nothing), silently
     zeroing everyone's pay. There was no shared guard for it to inherit — every sweep rolled its own
     (or no) safety.

`run_for_tenant` is that inheritable guard. Every `/run-due`-style job calls it instead of touching a
tenant's data directly. It:

  • **asserts the tenant exists and is active** (fail-closed: an unknown/inactive org_id is REFUSED,
    never written to);
  • gives the job a **money-write guard** (`ctx.guard_money_write(...)`) that REFUSES an anomalous
    org-wide money write (the $0-incident shape) BEFORE the write happens — configurable per tenant,
    "refuse" or "park-and-alert";
  • records a **`core.job_run`** audit row (running → succeeded / failed / refused / skipped);
  • on failure or refusal, writes **`core.failure_log`** so it surfaces on the admin /failures page with
    a how-to-fix note.

DEGRADES GRACEFULLY (contract §5): the tenant assert + money guard are pure Python and work with only
mig 055 present. The `core.job_run` audit + `core.failure_log` writes are best-effort try/except — a
missing mig 701 / mig 112 makes the guard un-audited, it never breaks the caller.

PUBLIC CONTRACT (keep minimal — mod-commission + mod-asset adopt this next; see the platform-core handoff)
---------------------------------------------------------------------------------------------------------
    run_for_tenant(org_id, job_name, job, *, client=None, tenant=None,
                   money_scope="none", allow_missing_tenant=False) -> job's return value
    await run_for_tenant_async(...)   # identical, for an async `job` (e.g. notify's await _dispatch)

    `job` is called as `job(ctx)` where `ctx` is a `TenantJobContext`. Before committing an org-wide
    money write, the job MUST call:

        ctx.guard_money_write(row_count=<n rows to write>, total_amount=<sum $ across them>,
                              scope="org", prior_total=<current $ being replaced, or None>,
                              label="rep_commissions 2026-07")

    which raises `MoneyWriteRefused` (caught + recorded by run_for_tenant) if the write looks like the
    $0-incident. Call it AFTER computing the new rows but BEFORE the delete/insert, so a refusal leaves
    the tenant's existing data untouched.

    Raises `TenantNotRunnable` if the tenant is missing/inactive (fail-closed). Both exceptions carry the
    recorded job_run id in `.run_id`.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.database import get_supabase

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

# money_scope / guard scope values
SCOPE_NONE = "none"        # the job does no money write (e.g. notify sends a report)
SCOPE_PARTIAL = "partial"  # a bounded write (one rep, one store) — not org-wide, lightly guarded
SCOPE_ORG = "org"          # an org-wide replace/wipe (recompute a whole period, wipe the ledger) — GUARDED

# core.job_run.status values
ST_RUNNING = "running"
ST_SUCCEEDED = "succeeded"
ST_FAILED = "failed"
ST_REFUSED = "refused"     # guard blocked the write (tenant inactive OR anomalous money write)
ST_SKIPPED = "skipped"     # nothing to do

# Default money-write anomaly policy. Per-tenant override lives in storeops.tenants.money_guard_config
# (jsonb, mig 701); null → these defaults. Conservative on purpose: it only trips on a total wipe-to-$0,
# so a normal recompute (numbers move up/down) never false-positives.
_DEFAULT_GUARD = {
    "enabled": True,
    "mode": "refuse",            # "refuse" (severity error) | "park" (park-and-alert, severity warning)
    "block_zero_org_write": True,  # an org-wide write of N>0 rows whose $ total is 0 is anomalous
    "max_drop_pct": 100.0,       # refuse an org-wide total that drops >= this % vs prior_total (100 = only a full wipe-to-0)
    "min_rows_to_guard": 1,      # only guard writes of at least this many rows (a 1-row org write is edge)
    "zero_epsilon": 0.005,       # |total| below this counts as $0 (float noise)
}


class TenantNotRunnable(Exception):
    """The job's tenant is missing or inactive — the job was REFUSED before any write."""
    def __init__(self, message, run_id=None):
        super().__init__(message)
        self.run_id = run_id


class MoneyWriteRefused(Exception):
    """The guard blocked an anomalous org-wide money write (the $0-incident shape) before it happened."""
    def __init__(self, message, *, run_id=None, summary=None, mode="refuse"):
        super().__init__(message)
        self.run_id = run_id
        self.summary = summary or {}
        self.mode = mode


def _guard_config(tenant: dict) -> dict:
    cfg = dict(_DEFAULT_GUARD)
    raw = (tenant or {}).get("money_guard_config")
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if v is not None})
    return cfg


def _evaluate_money_write(cfg: dict, *, row_count: int, total_amount: float,
                          scope: str, prior_total) -> tuple[bool, str]:
    """Pure decision fn. Returns (ok, reason). Only org-wide writes are guarded; a partial write is
    the caller's own concern."""
    if scope != SCOPE_ORG:
        return True, "not an org-wide write"
    if not cfg.get("enabled", True):
        return True, "money guard disabled for this tenant"
    row_count = int(row_count or 0)
    total_amount = float(total_amount or 0.0)
    if row_count < int(cfg.get("min_rows_to_guard", 1)):
        return True, "below min_rows_to_guard"
    eps = float(cfg.get("zero_epsilon", 0.005))
    # (1) the $0-incident shape: many rows, $0 total.
    if cfg.get("block_zero_org_write", True) and abs(total_amount) < eps:
        return False, (f"org-wide money write of {row_count} row(s) totals $0 "
                       f"(the 2026-07-13 $0-incident shape)")
    # (2) a large drop vs the known prior total (a near-total wipe).
    if prior_total is not None:
        prior = float(prior_total or 0.0)
        if prior > 0:
            drop_pct = (prior - total_amount) / prior * 100.0
            if drop_pct >= float(cfg.get("max_drop_pct", 100.0)):
                return False, (f"org-wide money total drops {drop_pct:.0f}% "
                               f"(${prior:,.2f} -> ${total_amount:,.2f}); threshold {cfg.get('max_drop_pct')}%")
    return True, "ok"


@dataclass
class TenantJobContext:
    """Passed to `job(ctx)`. The job reports what it is about to write via `guard_money_write`."""
    org_id: str
    job_name: str
    run_id: str
    tenant: dict
    client: object
    guard_cfg: dict = field(default_factory=dict)
    money_writes: list = field(default_factory=list)

    def guard_money_write(self, *, row_count: int, total_amount: float, scope: str = SCOPE_ORG,
                          prior_total=None, label: str = "") -> dict:
        """Call BEFORE committing a money write. Raises MoneyWriteRefused on an anomalous org-wide write.
        Returns the recorded summary on success (so the caller can proceed)."""
        summary = {
            "label": label or self.job_name,
            "scope": scope,
            "row_count": int(row_count or 0),
            "total_amount": round(float(total_amount or 0.0), 4),
            "prior_total": (round(float(prior_total), 4) if prior_total is not None else None),
        }
        ok, reason = _evaluate_money_write(self.guard_cfg, row_count=row_count,
                                           total_amount=total_amount, scope=scope,
                                           prior_total=prior_total)
        summary["decision"] = "allow" if ok else "refuse"
        summary["reason"] = reason
        self.money_writes.append(summary)
        if not ok:
            mode = str(self.guard_cfg.get("mode", "refuse")).lower()
            raise MoneyWriteRefused(reason, run_id=self.run_id, summary=summary, mode=mode)
        return summary


# ── internal recording helpers (all best-effort — never raise) ────────────────────────────────────

def _base_client(client):
    return client if client is not None else get_supabase()


def _fetch_tenant(client, org_id: str):
    try:
        rows = (client.schema("storeops").table("tenants").select("*")
                .eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        # Tenant lookup is the guard's ground truth; if it genuinely errors we fail closed at the caller.
        raise


def _open_job_run(client, org_id, job_name, money_scope) -> str:
    run_id = str(uuid.uuid4())
    try:
        client.schema("core").table("job_run").insert({
            "id": run_id, "org_id": org_id, "job_name": job_name,
            "status": ST_RUNNING, "money_scope": money_scope,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        pass  # mig 701 not run yet — guard still works, just un-audited
    return run_id


def _close_job_run(client, run_id, org_id, status, started, *, detail=None):
    try:
        dur = int((time.monotonic() - started) * 1000) if started else None
        client.schema("core").table("job_run").update({
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": dur,
            "detail": detail,
        }).eq("id", run_id).eq("org_id", org_id).execute()
    except Exception:
        pass


def _log_failure(client, org_id, *, category, severity, message, source, detail, remediation):
    try:
        client.schema("core").table("failure_log").insert({
            "org_id": org_id, "category": category, "severity": severity,
            "source": source[:200] if source else None,
            "message": message[:1000], "detail": detail, "remediation": remediation,
        }).execute()
    except Exception:
        pass  # mig 112 not run / write blocked — never break the caller


_REMEDIATION = {
    "tenant_missing": ("A background job fired for an org_id with no tenant row. Confirm the connector / "
                       "subscription / plan is filed under a REAL tenant (storeops.tenants), or remove the "
                       "stale row. This is the tenant-misfiling guard."),
    "tenant_inactive": ("The tenant is deactivated (storeops.tenants.is_active = false). Reactivate it at "
                        "/admin/tenants to resume its jobs, or leave it off if the deactivation is intended."),
    "money_refused": ("An org-wide money write was blocked because it looked anomalous (all-$0, or a near-total "
                      "wipe of an existing balance) — the 2026-07-13 $0-incident shape. Check the source data / "
                      "plan assignment BEFORE re-running: a $0 result is almost always missing input, not a real "
                      "zero. Override per-tenant via storeops.tenants.money_guard_config if this is legitimate."),
    "job_failed": ("A guarded background job raised. See detail for the trace; the tenant's data was left as-is. "
                   "Fix the underlying cause and the sweep will retry on its next tick."),
}


# ── the guarded entrypoints ───────────────────────────────────────────────────────────────────────

def _preflight(client, org_id, job_name, money_scope, tenant, allow_missing_tenant):
    """Shared: assert tenant, open job_run, build ctx. Returns (ctx, run_id, started).
    Raises TenantNotRunnable (already recorded) if the tenant is missing/inactive."""
    started = time.monotonic()
    if tenant is None:
        tenant = _fetch_tenant(client, org_id)
    run_id = _open_job_run(client, org_id, job_name, money_scope)

    if tenant is None and not allow_missing_tenant:
        _close_job_run(client, run_id, org_id, ST_REFUSED,
                       started, detail={"reason": "tenant_not_found", "org_id": org_id})
        _log_failure(client, org_id, category="tenant_guard", severity="error",
                     message=f"Job '{job_name}' refused: no tenant row for org_id {org_id}",
                     source=f"run_for_tenant/{job_name}",
                     detail={"org_id": org_id, "job_name": job_name},
                     remediation=_REMEDIATION["tenant_missing"])
        raise TenantNotRunnable(f"no tenant for org_id {org_id}", run_id=run_id)

    if tenant is not None and not tenant.get("is_active", True):
        _close_job_run(client, run_id, org_id, ST_REFUSED,
                       started, detail={"reason": "tenant_inactive", "org_id": org_id})
        _log_failure(client, org_id, category="tenant_guard", severity="warning",
                     message=f"Job '{job_name}' skipped: tenant {org_id} is inactive",
                     source=f"run_for_tenant/{job_name}",
                     detail={"org_id": org_id, "job_name": job_name, "name": (tenant or {}).get("name")},
                     remediation=_REMEDIATION["tenant_inactive"])
        raise TenantNotRunnable(f"tenant {org_id} is inactive", run_id=run_id)

    ctx = TenantJobContext(org_id=org_id, job_name=job_name, run_id=run_id,
                           tenant=tenant or {}, client=client,
                           guard_cfg=_guard_config(tenant or {}))
    return ctx, run_id, started


def _finish_ok(client, ctx, run_id, started):
    _close_job_run(client, run_id, ctx.org_id, ST_SUCCEEDED, started,
                   detail={"money_writes": ctx.money_writes} if ctx.money_writes else None)


def _finish_refused(client, ctx, run_id, started, exc: MoneyWriteRefused):
    _close_job_run(client, run_id, ctx.org_id, ST_REFUSED, started,
                   detail={"reason": "money_write_refused", "summary": exc.summary,
                           "money_writes": ctx.money_writes})
    _log_failure(client, ctx.org_id,
                 category="money_write_refused",
                 severity=("error" if exc.mode != "park" else "warning"),
                 message=f"Job '{ctx.job_name}' refused an org-wide money write: {exc}",
                 source=f"run_for_tenant/{ctx.job_name}",
                 detail=exc.summary,
                 remediation=_REMEDIATION["money_refused"])


def _finish_failed(client, ctx, run_id, started, exc: Exception):
    _close_job_run(client, run_id, ctx.org_id, ST_FAILED, started,
                   detail={"error": str(exc), "money_writes": ctx.money_writes})
    _log_failure(client, ctx.org_id, category="sweep_error", severity="error",
                 message=f"Job '{ctx.job_name}' failed: {exc}",
                 source=f"run_for_tenant/{ctx.job_name}",
                 detail={"error": str(exc), "job_name": ctx.job_name},
                 remediation=_REMEDIATION["job_failed"])


def run_for_tenant(org_id, job_name, job, *, client=None, tenant=None,
                   money_scope=SCOPE_NONE, allow_missing_tenant=False):
    """Run `job(ctx)` under the tenant guard (sync). See module docstring for the full contract."""
    client = _base_client(client)
    ctx, run_id, started = _preflight(client, org_id, job_name, money_scope, tenant, allow_missing_tenant)
    try:
        result = job(ctx)
    except MoneyWriteRefused as e:
        _finish_refused(client, ctx, run_id, started, e)
        raise
    except Exception as e:
        _finish_failed(client, ctx, run_id, started, e)
        raise
    _finish_ok(client, ctx, run_id, started)
    return result


async def run_for_tenant_async(org_id, job_name, job, *, client=None, tenant=None,
                               money_scope=SCOPE_NONE, allow_missing_tenant=False):
    """Async twin of run_for_tenant, for an `async def job(ctx)` (e.g. notify's `await _dispatch`)."""
    client = _base_client(client)
    ctx, run_id, started = _preflight(client, org_id, job_name, money_scope, tenant, allow_missing_tenant)
    try:
        result = await job(ctx)
    except MoneyWriteRefused as e:
        _finish_refused(client, ctx, run_id, started, e)
        raise
    except Exception as e:
        _finish_failed(client, ctx, run_id, started, e)
        raise
    _finish_ok(client, ctx, run_id, started)
    return result
