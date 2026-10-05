"""MANAGEMENT WATCHDOG API — /api/v1/commcalc/watchdog/* (owner ask 2026-10-05).

Owner, verbatim: *"keep these reports in management dashboard under different reports so it is easy
for the management to review each area and take appropriate action, name it Management Watch dog"*.

WHAT THIS IS, AND WHAT IT IS NOT (the duplicate-check build gate, CLAUDE.md)
───────────────────────────────────────────────────────────────────────────
`/compliance` already exists and already answers "how many items are open in each QUEUE" across ten
different tables (`commcalc/compliance_summary.py`, owner directive 2026-09-03). A second dashboard
counting the same queues would be exactly the sibling derivation the index rules forbid.

So the Management Watchdog is a different cut of ONE table, not a second board over many:

    /compliance   one row per QUEUE — ten different tables, ten different owners. "What is open?"
    /watchdog     one page per AREA of `commcalc.flags` — one table, grouped by what a manager
                  reviews together. "Which part of the business do I go and act on?"

And the two are wired to the same registry rather than kept in step by hand: `compliance_summary`'s
commission-flags row and this board's area counts both resolve through
`commcalc/flag_registry.py`, so a new detector appears on both by registering once.

EVERY READ GOES THROUGH THE EXISTING ANSWER
───────────────────────────────────────────
This file **does not query `commcalc.flags`**. It calls `commcalc/router.get_flags`, which is the one
home for "which findings may this caller see" — the active-queue filter (a retired finding drops
out), the period-spelling variants, and the district-manager span filter (`scope_keyset` /
`in_keyset`, so a market manager sees their own stores and a store-scoped user sees theirs). A private
query here would be a second answer to "may this manager see this finding", which is a permission
defect waiting to happen, not a convenience.

The area, label, severity scale and grain all come from `flag_registry`. This file decides nothing.

Mounted by app/main.py. Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §53.

RULE TWO: no carrier, tenant, store or product name appears in this file.
"""
from __future__ import annotations

import calendar as _calendar
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header, Query

from app.core.database import get_supabase
from app.core.run_secret import verify_notify_secret

from app.modules.commcalc import flag_registry as _reg
from app.modules.commcalc import void_watchdog as _void
from app.modules.closing import cash_watchdog as _cash

router = APIRouter(prefix="/commcalc", tags=["Management Watchdog"])

ORG_ID = "00000000-0000-0000-0000-000000000001"

#: How far back the cash sweep looks on a tick. A drawer variance that nobody has ruled on is still
#: worth raising weeks later (the B-117 case was four days old and climbing when it was found), but an
#: unbounded scan would re-read years of attempts every hour. TENANT-TUNABLE via the rule row's
#: `lookback_days`, read below — the house default is deliberately generous.
LOOKBACK_DAYS_DEFAULT = 45


def _s(v) -> str:
    return str(v or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _period_now() -> str:
    """This month, in the '%B %Y' spelling every writer of `commcalc.flags` already uses — which is
    therefore the spelling `commcalc/router._pvariants` can expand. A canonical '202610' would be a
    third spelling in the same column that `_pvariants` cannot parse, so it would match nothing."""
    d = datetime.now(timezone.utc)
    return d.strftime("%B %Y")


def _month_year(period: str):
    """(month, year) from a '%B %Y' or 'YYYY-MM' period label, or (None, None) when neither.

    (None, None) is kept and written as NULL rather than guessed: `flag_persist` derives a finding's
    identity from these columns, so a guessed month would re-key every finding of that period.
    """
    q = _s(period)
    if len(q) >= 7 and q[:4].isdigit() and q[4] == "-" and q[5:7].isdigit():
        return int(q[5:7]), int(q[:4])
    parts = q.split()
    if len(parts) == 2 and parts[1].isdigit():
        names = {m.lower(): i for i, m in enumerate(_calendar.month_name) if m}
        if parts[0].lower() in names:
            return names[parts[0].lower()], int(parts[1])
    return None, None


def _visible_flags(period: str, authorization: str, org_id: str):
    """The findings this caller may see for a period — THE existing answer, not a new one.

    Dereferences `commcalc/router.get_flags`: active queue only, every period spelling, and the
    district-manager span filter. Imported lazily because commcalc/router is a large module and this
    one is mounted beside it (the same lazy-import idiom the closing↔commcalc pair already uses to
    avoid an import cycle).
    """
    from app.modules.commcalc.router import get_flags as _get_flags
    return _get_flags(period, authorization=authorization, org_id=org_id) or []


# ── The board: one row per area. ─────────────────────────────────────────────────────────────────
@router.get("/watchdog/board")
# PLAIN `def`, not `async def`: `_visible_flags` does a BLOCKING supabase round trip. As `async def`
# it would run that on the event loop and stall every other in-flight request — verbatim the SEV-1 of
# 2026-07-30 that `get_flags` itself carries a comment about. Same shape here on purpose.
def watchdog_board(period: str = Query(default=""), authorization: str = Header(default=""),
                   org_id: str = ORG_ID):
    """One row per review area with its open findings counted, in board order.

    EVERY area is returned even at zero, so the board is a statement of what is watched rather than a
    list of today's bad news — a manager must be able to tell "nothing open in cash" from "cash is
    not watched". An `unassigned` row appears ONLY when a finding landed whose kind is not registered,
    and `unregistered_types` names those kinds, so the board is never read as complete when it is not.
    """
    p = _s(period) or _period_now()
    rows = _visible_flags(p, authorization, org_id)
    return {
        "period": p,
        "as_of": _now(),
        "total_open": len(rows),
        "areas": _reg.area_summary(rows),
        "unregistered_types": _reg.unregistered_types(rows),
    }


# ── One area's findings: the page a manager acts on. ─────────────────────────────────────────────
@router.get("/watchdog/area/{area}")
def watchdog_area(area: str, period: str = Query(default=""),
                  authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The findings for one review area, worst first.

    `grain` on each finding is the honest answer to "which transaction was this": a `rep_period`
    finding counted transactions it did not keep, so there is nothing to click through to, and the
    page says so rather than showing an empty drill-down.
    """
    a = _s(area)
    if a != _reg.UNASSIGNED and a not in _reg.AREA_KEYS:
        raise HTTPException(404, "unknown watchdog area")
    p = _s(period) or _period_now()
    rows = _visible_flags(p, authorization, org_id)
    mine = [r for r in rows if _reg.area_of(r.get("flag_type")) == a]
    mine.sort(key=lambda r: (_reg.sev_rank(r.get("severity")),
                             _s(r.get("flag_type")), _s(r.get("store_code")),
                             _s(r.get("transaction_date"))))
    out = []
    for r in mine:
        d = dict(r)
        d["type_label"] = _reg.label_of(r.get("flag_type"))
        d["grain"] = _reg.grain_of(r.get("flag_type"))
        d["severity"] = _reg.canon_sev(r.get("severity"))
        out.append(d)
    return {
        "area": a,
        "label": _reg.AREA_LABELS.get(a, _reg.UNASSIGNED_LABEL),
        "blurb": _reg.AREA_BLURBS.get(a, ""),
        "period": p, "as_of": _now(),
        "open_count": len(out),
        "types": list(_reg.types_in_area(a)),
        "findings": out,
    }


# ── The void register: the list that did not exist. ──────────────────────────────────────────────
@router.get("/watchdog/voids")
def watchdog_voids(period: str = Query(default=""), authorization: str = Header(default=""),
                   org_id: str = ORG_ID):
    """Every voided, returned and unattributed sales line for a period, with the rates.

    THIS IS THE THING THAT DID NOT EXIST. Before it, `is_voided` was dereferenced in about a dozen
    places and every one of them only EXCLUDED the line from pay, so a void left no trace but an
    absence. Nothing here changes what pays: the classification comes from
    `gp_report.countable_sale_skip_reason`, the pay path's own rule, read a second way.

    A share is `null`, never 0, where there is no denominator — a rep with no lines has no void rate,
    and 0% would read as clean.
    """
    p = _s(period) or _period_now()
    client = get_supabase()
    from app.modules.commcalc.router import _pvariants
    try:
        rows = (client.schema("commcalc").table("raw_sales")
                .select("store,salesperson,ext_price,trans_id,trans_date,product_desc,"
                        "serial_1,mdn,tender_type,contract_type,voided,trans_type")
                .eq("org_id", org_id).in_("period", _pvariants(p)).limit(100000)
                .execute().data) or []
    except Exception as e:
        raise HTTPException(503, f"sales feed unavailable: {str(e)[:200]}")

    # The SAME district-manager span filter the findings use, so this register cannot show a
    # market manager a store their flag queue hides. `in_keyset` is keyed on the resolved store code
    # and the raw address, exactly as `get_flags` applies it.
    from app.modules.storeops.router import scope_keyset, in_keyset
    ks = scope_keyset(authorization, org_id)
    if ks is not None:
        rows = [r for r in rows if in_keyset(ks, None, r.get("store"))]

    s = _void.summarise(rows)
    s["period"] = p
    s["as_of"] = _now()
    # The feed genuinely carrying nothing for this period is NOT "no voids" — it is "not reported".
    s["has_feed"] = bool(rows)
    return s


# ── The config surface: thresholds are rows, never code (RULE TWO). ─────────────────────────────
@router.get("/watchdog/rules")
def watchdog_rules(org_id: str = ORG_ID):
    """Every watchdog type with the thresholds IN FORCE for this org — house default plus any row.

    `source` says which it is, so an operator can tell a deliberate setting from an inherited
    default. That distinction is the whole reason this endpoint exists: a threshold that silently
    came from code looks identical to one somebody chose.
    """
    rows = _read_rules(get_supabase(), org_id)
    by_type = {_s(r.get("flag_type")) for r in rows}
    out = []
    for t in sorted(_reg.DEFAULT_PARAMS):
        out.append({
            "flag_type": t,
            "label": _reg.label_of(t),
            "area": _reg.area_of(t),
            "severity": _reg.severity_for(t),
            "enabled": _reg.is_enabled(t, rows),
            "params": _reg.rule_params(t, rows),
            "defaults": _reg.default_params(t),
            "source": "org row" if t in by_type else "house default",
        })
    return {"org_id": org_id, "as_of": _now(), "rules": out}


def _read_rules(client, org_id):
    """This org's `commcalc.watchdog_rule` rows, or [] before migration 1056 has been applied.

    Degrades to the house defaults rather than erroring (contract §5): a watchdog must run on the
    day its code ships, not on the day somebody remembers to paste the migration.
    """
    try:
        return (client.schema("commcalc").table("watchdog_rule")
                .select("flag_type,enabled,params").eq("org_id", org_id).execute().data) or []
    except Exception as e:
        print(f"INFO watchdog_rule unavailable, using house defaults (run migration 1056?): {e}")
        return []


# ── The tick: one sweep, both watchdogs, one cron line to paste. ────────────────────────────────
def _run_for_org(client, org_id: str, *, period: str = "", lookback_days: int = 0) -> dict:
    """Both detectors for one org, written through `flag_persist` so a manager's ruling survives.

    Deliberately ONE function for both: a second `run-due` endpoint would mean a second cron line to
    register, and the 2026-10-04 notes already record alert sweeps sitting switched off because their
    `cron.schedule` line was never pasted. One line is one thing to forget.
    """
    from app.modules.commcalc import flag_persist
    from app.modules.commcalc.router import _pvariants

    rules = _read_rules(client, org_id)
    p = _s(period) or _period_now()
    out = {"org_id": org_id, "period": p}

    # ── Cash. Reads the submit trail and the closings, hands them to the pure detector. ─────────
    try:
        days = int(lookback_days or 0) or LOOKBACK_DAYS_DEFAULT
        from datetime import date, timedelta
        since = (date.today() - timedelta(days=days)).isoformat()
        attempts = (client.schema("commcalc").table("closing_attempt")
                    .select("store_code,store_address,close_date,employee_name,attempt_no,"
                            "entered_cash,entered_credit,b2b_cash,b2b_credit,cash_dir,credit_dir,"
                            "blocked,accepted,auto_accepted,created_at")
                    .eq("org_id", org_id).gte("close_date", since).limit(50000).execute().data) or []
        closings = (client.schema("commcalc").table("daily_closing")
                    .select("store_code,store_address,close_date")
                    .eq("org_id", org_id).gte("close_date", since).limit(50000).execute().data) or []
        # The §13 store resolver, so the two tables join on the same store identity. Without it the
        # measured 2026-10-04 case recurs: seven banked days read as unfinished because the attempt
        # trail and the closing row spelled the same store differently.
        resolve = None
        try:
            from app.modules.account.coa import store_resolver
            resolve = store_resolver(client, org_id)
        except Exception as e:
            print(f"WARN cash watchdog store resolver unavailable, raw codes used: {e}")
        cash_flags = _cash.calc_cash_flags(attempts, closings, resolve=resolve, rules=rules)
        periods = sorted({f["period"] for f in cash_flags} | {p})
        pv = []
        for one in periods:
            pv.extend(_pvariants(one))
        out["cash"] = flag_persist.sync(
            client, org_id, cash_flags, periods=sorted(set(pv)), sources=[_cash.SOURCE],
            reason="the drawer variance was corrected or the day was ruled on")
        out["cash"].pop("run_id", None)
    except flag_persist.FlagPersistUnavailable as e:
        out["cash"] = {"skipped": "findings cannot be recorded yet",
                      "detail": f"run migration 287 first: {str(e)[:200]}"}
    except Exception as e:
        out["cash"] = {"error": str(e)[:300]}

    # ── Voids. One period at a time; the sales feed is the biggest table in the schema. ─────────
    try:
        rows = (client.schema("commcalc").table("raw_sales")
                .select("store,salesperson,ext_price,trans_id,trans_date,product_desc,serial_1,"
                        "mdn,voided,trans_type")
                .eq("org_id", org_id).in_("period", _pvariants(p)).limit(100000)
                .execute().data) or []
        pm, py = _month_year(p)
        void_flags = _void.calc_void_flags(rows, period=p, period_month=pm, period_year=py,
                                           rules=rules)
        out["voids"] = flag_persist.sync(
            client, org_id, void_flags, periods=_pvariants(p), sources=[_void.SOURCE],
            reason="the void or return rate is back inside the allowed share")
        out["voids"].pop("run_id", None)
        out["voids"]["sales_lines_read"] = len(rows)
    except flag_persist.FlagPersistUnavailable as e:
        out["voids"] = {"skipped": "findings cannot be recorded yet",
                      "detail": f"run migration 287 first: {str(e)[:200]}"}
    except Exception as e:
        out["voids"] = {"error": str(e)[:300]}

    return out


@router.post("/watchdog/run-now")
def watchdog_run_now(period: str = Query(default=""), lookback_days: int = Query(default=0),
                     org_id: str = ORG_ID):
    """Run both watchdogs for ONE org, now. The operator's button and the test entrypoint."""
    return _run_for_org(get_supabase(), org_id, period=period, lookback_days=lookback_days)


@router.post("/watchdog/run-due")
def watchdog_run_due(x_notify_secret: str = Header(default=""), period: str = Query(default="")):
    """pg_cron entrypoint, guarded by NOTIFY_RUN_SECRET — every tenant on one tick (the mig-433
    convention). One org failing is reported and never stops the others.

    NOTHING RUNS UNTIL ONE `cron.schedule` LINE IS PASTED. That is stated here because the 2026-10-04
    notes record two alert sweeps shipped, correct and silent for exactly this reason. The line is in
    migration 1056's comment header.
    """
    if not verify_notify_secret(x_notify_secret):
        raise HTTPException(403, "forbidden")
    client = get_supabase()
    try:
        tenants = (client.schema("storeops").table("tenants").select("org_id")
                   .execute().data) or []
    except Exception as e:
        raise HTTPException(503, f"tenant list unavailable: {str(e)[:200]}")
    results = []
    for t in tenants:
        oid = _s(t.get("org_id"))
        if not oid:
            continue
        try:
            results.append(_run_for_org(client, oid, period=period))
        except Exception as e:
            results.append({"org_id": oid, "error": str(e)[:300]})
    return {"tenants": len(results), "as_of": _now(), "results": results}
