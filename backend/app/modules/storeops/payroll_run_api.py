"""THE ENDPOINTS for running one pay period as a guided, resumable flow (index §STOREOPS-16).

Owner 2026-11-25: *"...and simlar workflow for runnign the payroll with paying attention to
details"*, said of the daily-closing workflow a new person can start and come back to.

EVERY DECISION ALREADY LIVES IN A PURE MODULE AND IS DEREFERENCED HERE, never re-derived:

    the rail, lamps, resume, progress      `core/guided_workflow`              (index §CORE-51)
    what each of the seven steps means     `storeops/payroll_run_flow`         (index §STOREOPS-16)
    this period's hours, per person,        `storeops/router.get_payroll` — the ONE pay derivation
    with the basis each stands on           (§STOREOPS-07), called in-process, not reimplemented
    the punches, the variances, the         `storeops/clock_variance_api._assemble` — the SAME
    manual rows nothing counted             bundle the clock-variance board reads
    may the run compute, and why not        `clock_variance.payroll_run_gate`   (§14z)
    a salaried person's period pay          `storeops/payroll_salary.derive_salary_pay`
    which dates a pay period covers         `storeops/payroll_approval._resolve_period` — the
                                            tenant's DECLARED cycle, never a house fortnight
    who may see pay figures                  `storeops/pay_visibility`           (mig 434)
    which stores reach this login            `storeops/router.scope_keyset`      (index §14x)

So this file is I/O and HTTP shape, and NOTHING here writes — it cannot run payroll, only say
whether a run may happen and what is in the way. The screen it feeds sends people to the doors that
already exist.

MONEY IS GATED, NOT ASSUMED. Step 7 reports what the run pushed, which is pay. The figures ride
`pay_visibility.can_see_pay` and a caller without it is told WHETHER the month was run, with the
amounts withheld rather than quietly shown — the same posture `GET /storeops/payroll` already takes
(RULE FOUR: the export cannot carry what the screen hides).

DEGRADES BY NAME, NEVER 500s. Each read reports itself in `blockers` when it fails, because "we
could not look" and "there is nothing left to do" must never print the same.
"""
from datetime import date

from fastapi import APIRouter, Header, HTTPException, Response

from app.core.database import get_supabase
from app.modules.core import guided_workflow as gw
from app.modules.core import roster_visibility as _rv
from app.modules.storeops import clock_variance as _cv
from app.modules.storeops import payroll_hours_basis as _hb
from app.modules.storeops import payroll_run_flow as _flow
from app.modules.storeops import payroll_salary as _salary

router = APIRouter()

ORG_ID = "00000000-0000-0000-0000-000000000001"

#: How much of the paid time should stand on a punch before the basis step stops asking. A POLICY
#: (a threshold), not a tenant's vocabulary — so a house default is legitimate here, and an org can
#: raise it with `clock_coverage_floor` on its own config row.
CLOCK_COVERAGE_FLOOR = 0.5


def sb():
    """SCHEMA-SCOPED to `storeops`, like `storeops/router.sb()`."""
    return get_supabase().schema("storeops")


def _s(v) -> str:
    return str(v or "").strip()


def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _day(v):
    return _flow._day(v)


def _rows(table, select, org_id, blockers, *, schema=None, **eqs):
    """A failed read is REPORTED, never silently empty."""
    try:
        client = get_supabase().schema(schema) if schema else sb()
        q = client.table(table).select(select).eq("org_id", org_id)
        for k, v in eqs.items():
            q = q.eq(k, v)
        return q.execute().data or []
    except Exception as e:
        blockers.append({"table": table, "reason": type(e).__name__})
        return []


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE ORGANISATION'S DECLARATIONS — its own pay period, and the policy knobs beside it
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _period(org_id, start, end, blockers):
    """The pay period, from the TENANT'S declared cycle through the home that already resolves it.

    `payroll_approval._resolve_period` returns the explicit range when one is given and otherwise
    the last COMPLETE pay period — weekly for a weekly tenant, a fortnight for a fortnightly one.
    A copy of that arithmetic here is how two payroll surfaces come to disagree about which period
    is being paid (the defect that module's §② records).
    """
    from app.modules.storeops.payroll_approval import _resolve_period
    try:
        return _resolve_period(org_id, start, end)
    except HTTPException:
        raise
    except Exception as e:
        blockers.append({"table": "tenants", "reason": type(e).__name__})
        today = date.today()
        raise HTTPException(409, "This organisation's pay cycle could not be read, so the period "
                                 f"cannot be resolved (as at {today.isoformat()}).")


def _declarations(org_id, lo, hi, var_cfg) -> dict:
    cfg = _cv.resolve_config(var_cfg if isinstance(var_cfg, dict) else None)
    return {"period_start": lo, "period_end": hi,
            "gate_enabled": bool(cfg.get("payroll_run_gate_enabled")),
            "clock_coverage_floor": CLOCK_COVERAGE_FLOOR}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FACTS, read once for the whole board
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _variance_bundle(org_id, lo, hi, blockers):
    """The punches, the shifts, the manual rows and the uncorrected variances — ASKED of the
    clock-variance board's own assembler, so this board and that one can never disagree."""
    try:
        from app.modules.storeops import clock_variance_api as _cva
        res = _cva._assemble(org_id, lo.isoformat(), hi.isoformat())
    except Exception as e:
        blockers.append({"table": "timelog", "reason": type(e).__name__})
        return {"rows": [], "bundle": {}, "variance_config": None}
    for b in (res.get("blockers") or ()):
        if isinstance(b, dict):
            blockers.append(b)
    return {"rows": res.get("rows") or [], "bundle": res.get("bundle") or {},
            "variance_config": res.get("variance_config")}


def _paid_rows(org_id, lo, hi, authorization, blockers):
    """This period's hours per person, with the basis each stands on — the ONE pay derivation,
    called in-process. A second derivation of paid hours is the defect §STOREOPS-07 records."""
    try:
        from app.modules.storeops.router import get_payroll
        return get_payroll(start=lo.isoformat(), end=hi.isoformat(),
                           authorization=authorization or "", org_id=org_id) or []
    except Exception as e:
        blockers.append({"table": "shifts", "reason": type(e).__name__})
        return []


def _salary_figures(org_id, employees, lo, hi, blockers):
    """A salaried person's pay for THIS period, through the home that converts it. Nothing is read
    from a salary table because there is none — the figure is derived from the employee's own
    `pay_amount` against the tenant's declared cycle."""
    try:
        rows = (sb().table("tenants").select(
            "work_week_start_dow,pay_period_type,payday_dow,payday_weeks_after,biweekly_anchor")
            .eq("org_id", org_id).limit(1).execute().data) or []
        settings = _salary.tenant_pay_period_settings(rows[0]) if rows else None
    except Exception as e:
        blockers.append({"table": "tenants", "reason": type(e).__name__})
        settings = None
    out = []
    for e in (employees or ()):
        eid = _s((e or {}).get("employee_id"))
        if not eid:
            continue
        basis = _s((e or {}).get("pay_basis")).lower()
        if not basis or basis == "hourly":
            continue
        try:
            got = _salary.derive_salary_pay(basis, (e or {}).get("pay_amount"), settings, lo, hi,
                                            hire_date=_day((e or {}).get("hire_date")))
        except Exception:
            got = None
        amount = (got or {}).get("amount") if isinstance(got, dict) else got
        if amount is not None and _f(amount) > 0:
            out.append({"employee_id": eid, "amount": _f(amount)})
    return out


def _approvals(org_id, lo, hi, blockers):
    try:
        return (sb().table("payroll_approval")
                .select("employee_id,store_code,hours_source,hours_approved,dm_status,hr_status,"
                        "override_by,override_reason,period_start,period_end")
                .eq("org_id", org_id).eq("period_start", lo.isoformat())
                .eq("period_end", hi.isoformat()).limit(5000).execute().data) or []
    except Exception as e:
        blockers.append({"table": "payroll_approval", "reason": type(e).__name__})
        return []


def _run_records(org_id, months, blockers):
    runs, taxes, expenses = [], [], []
    for m in months:
        runs += _rows("payroll_gross_ledger", "period,store,wages,headcount,run_at,run_by",
                      org_id, blockers, period=m)
        taxes += _rows("payroll_tax_ledger", "period,employee_id,wages,employer_total",
                       org_id, blockers, period=m)
        expenses += _rows("payroll_expense_item", "period,store_code,amount,category",
                          org_id, blockers, period=m)
    return runs, taxes, expenses


def _stores(org_id, blockers, *, include_inactive=False, store_code=None):
    """The roster, read once. `market` is carried for DISPLAY beside a store the caller already
    chose — nothing here filters or buckets by it, and no money figure on this board is bucketed by
    market either; the market filter is `router.scope_keyset` (§14x), the canonical resolution.
    Pinned ROSTER in `harness_market_resolution_guard.py`."""
    eqs = {"store_code": store_code} if store_code else {}
    rows = _rows("stores", "store_code,address,market,is_active", org_id, blockers, **eqs)
    return _rv.visible(rows, include_inactive=include_inactive), (_rv.hidden_count(rows) if rows
                                                                  else 0)


def _assemble(org_id, start, end, authorization, *, include_inactive=False, store_code=None):
    blockers = []
    lo, hi = _period(org_id, start, end, blockers)
    var = _variance_bundle(org_id, lo, hi, blockers)
    declared = _declarations(org_id, lo, hi, var["variance_config"])
    months = _flow.months_of(declared)

    employees = (var["bundle"] or {}).get("employees") or []
    paid = _paid_rows(org_id, lo, hi, authorization, blockers)
    runs, taxes, expenses = _run_records(org_id, months, blockers)
    stores, hidden = _stores(org_id, blockers, include_inactive=include_inactive,
                             store_code=store_code)
    return {"declared": declared, "months": months, "stores": stores, "inactive_hidden": hidden,
            "employees": employees,
            "punches": (var["bundle"] or {}).get("punches") or [],
            "shifts": (var["bundle"] or {}).get("shifts") or [],
            "manual": (var["bundle"] or {}).get("manual") or [],
            "variances": var["rows"], "paid": paid,
            "approvals": _approvals(org_id, lo, hi, blockers),
            "salary": _salary_figures(org_id, employees, lo, hi, blockers),
            "runs": runs, "taxes": taxes, "expenses": expenses,
            "blockers": blockers}


def _facts_for(store, a) -> dict:
    """One store's pay period, cut out of the period that was read once."""
    code = _s(store.get("store_code"))
    key = code.upper()

    def _at(rows, *fields):
        out = []
        for r in (rows or ()):
            for fld in fields:
                if _s((r or {}).get(fld)).upper() == key:
                    out.append(r)
                    break
        return out

    punches = _at(a["punches"], "store_code")
    paid = _at(a["paid"], "store", "store_code")
    paid_ids = [_s(r.get("employee_id")) for r in paid if _s(r.get("employee_id"))]
    scheduled_ids = {_s(s.get("employee_id")) for s in _at(a["shifts"], "store_code")
                     if _s(s.get("employee_id"))}
    punched_ids = {_s(p.get("employee_id")) for p in punches if _s(p.get("employee_id"))}
    emps_here = [e for e in a["employees"] if _s((e or {}).get("home_store")).upper() == key
                 or _s((e or {}).get("employee_id")) in set(paid_ids)]
    return {"store_code": code,
            "punches": punches,
            "worked_without_punch": sorted(scheduled_ids - punched_ids),
            "paid_rows": paid,
            "paid_employee_ids": paid_ids,
            "blocking": [r for r in _at(a["variances"], "store_code") if r.get("blocks")],
            "manual_uncounted": [m for m in _at(a["manual"], "store_code")
                                 if (m or {}).get("counted") is False],
            "approvals": _at(a["approvals"], "store_code"),
            "employees": emps_here,
            "salary_rows": [r for r in a["salary"]
                            if _s(r.get("employee_id")) in {_s(e.get("employee_id"))
                                                            for e in emps_here}],
            "run_rows": _at(a["runs"], "store", "store_code"),
            "tax_rows": [t for t in a["taxes"]
                         if _s((t or {}).get("employee_id")) in set(paid_ids)],
            "expense_rows": _at(a["expenses"], "store_code")}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE MONEY GATE — step 7 reports pay, so its figures ride the platform's own pay visibility
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MONEY_KEYS = ("wages", "employer_tax", "expenses")


def _withhold_money(row):
    """Strip step 7's amounts for a caller who may not see pay, and SAY they were withheld — a blank
    is indistinguishable from $0.00, and this flow's whole posture is that absence is never zero."""
    for s in row.get("steps") or ():
        if s.get("key") != _flow.STEP_RUN:
            continue
        detail = dict(s.get("detail") or {})
        withheld = [k for k in MONEY_KEYS if k in detail]
        for k in withheld:
            detail.pop(k, None)
        if withheld:
            detail["money_withheld"] = ("The amounts this run pushed are not shown to your role; "
                                        "whether it ran is.")
        s["detail"] = detail
    return row


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE BOARD — one row per store for this pay period
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _apply_filters(stores, *, markets=None, stores_filter=None):
    def _set(v):
        return {x.strip().upper() for x in str(v or "").split(",") if x.strip()}

    want_stores, want_markets = _set(stores_filter), _set(markets)
    if want_stores:
        stores = [s for s in stores if _s(s.get("store_code")).upper() in want_stores]
    if want_markets:
        stores = [s for s in stores if _s(s.get("market")).upper() in want_markets]
    return stores


def _gate(stores, authorization, org_id, lo, hi):
    """Which of these stores reach this login at all, through the ONE home (§14x). FAILS CLOSED."""
    from app.modules.storeops.router import scope_keyset
    try:
        keyset = scope_keyset(authorization, org_id, since=lo.isoformat(), until=hi.isoformat())
    except Exception:
        return []
    if keyset is None:
        return stores
    return [s for s in stores if _s(s.get("store_code")).upper() in keyset]


def _can_see_pay(authorization, org_id) -> bool:
    try:
        from app.modules.storeops import pay_visibility as _payvis
        return bool(_payvis.can_see_pay(authorization or "", org_id, client=get_supabase()))
    except Exception:
        # FAIL CLOSED on money: an unreadable gate withholds the figures rather than showing them.
        return False


@router.get("/payroll-workflow")
def payroll_workflow_board(start: str = None, end: str = None, include_inactive: bool = False,
                           markets: str = None, stores: str = None, status: str = None,
                           authorization: str = Header(default=""), org_id: str = ORG_ID,
                           response: Response = None):
    """Every store's pay period at a glance, with where each one picks up.

    With no `start`/`end` this answers for the last COMPLETE pay period of this tenant's own cycle.
    OPEN STORES ONLY BY DEFAULT, `include_inactive` being the one spelling (§CORE-45).
    """
    a = _assemble(org_id, start, end, authorization, include_inactive=include_inactive)
    declared = a["declared"]
    visible = _apply_filters(a["stores"], markets=markets, stores_filter=stores)
    visible = _gate(visible, authorization, org_id, declared["period_start"],
                    declared["period_end"])
    see_pay = _can_see_pay(authorization, org_id)

    rows = []
    for store in visible:
        try:
            row = _flow.describe(declared, _facts_for(store, a))
        except ValueError as e:
            rows.append({"store_code": _s(store.get("store_code")),
                         "status": gw.NEEDS_INPUT, "label": gw.LABELS[gw.NEEDS_INPUT],
                         "resume": None, "resume_title": "", "blocking": (), "steps": [],
                         "refused": str(e)})
            continue
        row["address"] = _s(store.get("address"))
        row["market"] = _s(store.get("market"))
        row["is_active"] = store.get("is_active") is not False
        rows.append(row if see_pay else _withhold_money(row))

    if status:
        want = {x.strip() for x in str(status).split(",") if x.strip()}
        rows = [r for r in rows if r.get("status") in want]
    rows.sort(key=lambda r: (r.get("status") == gw.DONE, _s(r.get("store_code")).casefold()))
    described = [r for r in rows if r.get("steps")]

    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Can-See-Pay-Rates"] = "true" if see_pay else "false"
    return {"ready": not a["blockers"],
            "period_start": declared["period_start"].isoformat(),
            "period_end": declared["period_end"].isoformat(),
            "months": list(a["months"]),
            "declared": {"gate_enabled": declared["gate_enabled"],
                         "clock_coverage_floor": declared["clock_coverage_floor"]},
            "can_see_pay": see_pay,
            "steps": [{"key": s["key"], "title": s["title"], "why": s["why"],
                       "required": s["required"], "screen": s["screen"]}
                      for s in _flow.steps(declared)],
            "statuses": [{"key": s, "label": gw.LABELS[s]} for s in gw.STATUSES],
            "bases": {b: _hb.basis_reason(b) for b in _hb.ALL_BASES},
            "summary": _flow.summarize(described),
            "rows": rows,
            "inactive_hidden": a["inactive_hidden"],
            "blockers": a["blockers"]}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ONE STORE'S PAY PERIOD — the rail, and the door for each step
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/payroll-workflow/{store_code}")
def payroll_workflow_one(store_code: str, start: str = None, end: str = None,
                         authorization: str = Header(default=""), org_id: str = ORG_ID,
                         response: Response = None):
    """One store's seven steps for one pay period, what each is waiting on, and where to go."""
    code = _s(store_code)
    a = _assemble(org_id, start, end, authorization, include_inactive=True, store_code=code)
    if not a["stores"]:
        raise HTTPException(404, "No store with that code in this organisation.")
    declared = a["declared"]
    if not _gate(a["stores"], authorization, org_id, declared["period_start"],
                 declared["period_end"]):
        raise HTTPException(403, "You cannot read this store's payroll.")

    store = a["stores"][0]
    try:
        out = _flow.describe(declared, _facts_for(store, a))
    except ValueError as e:
        raise HTTPException(409, str(e))
    see_pay = _can_see_pay(authorization, org_id)
    if not see_pay:
        out = _withhold_money(out)
    out["address"] = _s(store.get("address"))
    out["market"] = _s(store.get("market"))
    out["is_active"] = store.get("is_active") is not False
    out["can_see_pay"] = see_pay
    out["ready"] = not a["blockers"]
    out["blockers"] = a["blockers"]
    out["declared"] = {"gate_enabled": declared["gate_enabled"],
                       "clock_coverage_floor": declared["clock_coverage_floor"]}
    out["statuses"] = [{"key": s, "label": gw.LABELS[s]} for s in gw.STATUSES]
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Can-See-Pay-Rates"] = "true" if see_pay else "false"
    return out
