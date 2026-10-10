"""DAILY INCENTIVE — what a day's declared categories pay each employee. PURE (mig 1072).

Owner 2026-10-10, NY Logistics / UPS Store: *"the commission current is being paid is as per the
screenshot, so all these needs to be declared daily and calculated accordingly, but i dont wnat them
to be hard coded it should be a design effect"*.

So NO rule in the screenshot is spelled in this file. "Daily Cash Pool", "Overnight Shipments",
"Mailbox 1-Year Rental" are CONFIG ROWS (`commcalc.incentive_rule`); this module knows only the three
SHAPES they take, and every metric a rule points at is dereferenced from
`closing/metric_catalog.py` — the one home for what the business declares. A tenant adds a category
on the Count Fields screen and pays on it the same afternoon, with no code change (RULE TWO).

THE THREE SHAPES (the `TYPE` column of the owner's screenshot):

  per_unit            rate $ x the employee's own units of the payout metrics.
  percent_of_amount   rate (a fraction) x the employee's own declared $ of the payout metrics.
  threshold_pool      a pool = rate x the STORE's declared $, split among the employees who qualify.

A rule's QUALIFIER is a gate, evaluated at store or employee scope, over any metric on the catalog —
including several metrics combined, which is how "store overnight + express COMBINED must reach 5"
is expressed without either carrier or product ever being named in code.

MONEY POSTURE, inherited from `management_incentive.py` and not weakened here:
  · computing is not paying — the router persists and runs draft -> approved -> paid.
  · a gate that cannot be VERIFIED fails CLOSED. A missing metric is not a passed qualifier.
  · an unresolved input counts as zero production, never as an average or a guess.
  · a pool that cannot be SPLIT is reported unallocated with a reason. Silently equal-splitting a
    by-hours pool because nobody clocked in would invent a payment nobody earned.

SCOPE PRECEDENCE IS NOT REDEFINED HERE. Which rules apply to whom is
`management_incentive.resolve_plan` — the same resolver the employee commission plan and the
management incentive already use. A second precedence ladder is the duplicate defect.
"""
from app.modules.closing import metric_catalog as mc

RULE_KINDS = ("per_unit", "percent_of_amount", "threshold_pool")

# How a threshold_pool divides. Declared per rule — the owner's own screenshot carries a pool whose
# description says "proportional to their individual sales" and whose payout column says "by hours",
# which is exactly why this is a config choice and not a constant.
SPLIT_BASES = ("hours", "individual_sales", "equal")

QUALIFIER_SCOPES = ("store", "employee", "none")
MEASURE_BASES = ("revenue", "units", "amount")
OPS = {
    "gte": lambda a, b: a >= b,
    "gt": lambda a, b: a > b,
    "lte": lambda a, b: a <= b,
    "lt": lambda a, b: a < b,
    "eq": lambda a, b: a == b,
}


def _num(v, d=0.0):
    try:
        return float(d) if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return float(d)


def _key(v):
    return str(v or "").strip()


def _round(v):
    return round(_num(v) + 0.0, 2)


# ── Measuring ─────────────────────────────────────────────────────────────────────────────────────

def measure(catalog, declared_map, basis, metric_keys):
    """One number off a declared store-day (or one employee's slice of it).

    basis 'revenue' with no metric_keys is the day's gross revenue — `metric_catalog.gross_revenue`,
    never a local sum, so a qualifier and a target can never disagree about what "sales" means.
    'units' and 'amount' sum the named metrics.

    Returns (value, missing) where `missing` lists metric keys the catalog does not carry. A caller
    that is gating MUST treat a non-empty `missing` as a failed gate — see `_gate`.
    """
    b = _key(basis).lower() or "revenue"
    want = [_key(k) for k in (metric_keys or []) if _key(k)]
    known = set(mc.keys(catalog))
    missing = [k for k in want if k not in known]
    if b == "revenue" and not want:
        return mc.gross_revenue(catalog, declared_map), missing
    total = 0.0
    for k in want:
        if k not in known:
            continue
        if b == "units":
            total += mc.units_of(declared_map, k)
        else:
            total += mc.amount_of(declared_map, k)
    return (_round(total) if b != "units" else int(total)), missing


def _gate(catalog, qualifier, store_declared, employee_declared):
    """Evaluate a rule's qualifier. Returns (passed, detail) — detail always says WHY, so a rep can be
    shown what they missed rather than an unexplained zero."""
    q = qualifier or {}
    scope = _key(q.get("scope")).lower() or "none"
    if scope not in QUALIFIER_SCOPES:
        scope = "none"
    if scope == "none":
        return True, {"scope": "none", "passed": True, "reason": "no qualifier"}
    op_key = _key(q.get("op")).lower() or "gte"
    op = OPS.get(op_key)
    threshold = _num(q.get("threshold"))
    basis = _key(q.get("basis")).lower() or "revenue"
    if basis not in MEASURE_BASES:
        basis = "revenue"
    source = store_declared if scope == "store" else employee_declared
    if source is None:
        # employee scope with nothing declared for that person: fails closed.
        return False, {"scope": scope, "passed": False, "op": op_key, "threshold": threshold,
                       "value": 0, "reason": "nothing declared for this employee"}
    value, missing = measure(catalog, source, basis, q.get("metric_keys"))
    if missing:
        return False, {"scope": scope, "passed": False, "op": op_key, "threshold": threshold,
                       "value": value, "missing": missing,
                       "reason": "qualifier names a category this business does not declare"}
    if op is None:
        return False, {"scope": scope, "passed": False, "op": op_key, "threshold": threshold,
                       "value": value, "reason": f"unknown comparison '{op_key}'"}
    passed = op(value, threshold)
    return passed, {"scope": scope, "passed": passed, "op": op_key, "threshold": threshold,
                    "value": value, "basis": basis,
                    "metric_keys": [_key(k) for k in (q.get("metric_keys") or [])],
                    "reason": "" if passed else "below the qualifying threshold"}


# ── Splitting a pool ──────────────────────────────────────────────────────────────────────────────

def split_pool(pool, employees, *, basis, hours_by_employee=None, revenue_by_employee=None):
    """Divide `pool` among `employees` on the declared basis.

    Returns (shares, unallocated, note). `shares` is {employee: $}. When the basis cannot be measured
    — a by-hours pool on a day nobody's hours are known, or every weight zero — the WHOLE pool comes
    back unallocated with a note. It is never quietly turned into an equal split: that would pay
    people a share of a basis nobody recorded.

    Rounding: shares are rounded to the cent and the remainder goes to the LARGEST weight, so the
    shares always sum to the pool exactly.
    """
    b = _key(basis).lower() or "equal"
    names = [n for n in (employees or []) if _key(n)]
    if not names or pool <= 0:
        return {}, _round(pool if pool > 0 else 0), ("nobody qualified" if pool > 0 else "")
    if b == "hours":
        weights = {n: _num((hours_by_employee or {}).get(n)) for n in names}
        note_empty = "no hours recorded for the qualifying employees, so the pool cannot be split by hours"
    elif b == "individual_sales":
        # "Who ran the sale." The caller supplies the weights, because WHICH sales count depends on
        # what the pool is built from — see compute_day. Nobody declared any, nobody gets a share:
        # dividing it evenly would pay people for sales nobody recorded them making.
        weights = {n: _num((revenue_by_employee or {}).get(n)) for n in names}
        note_empty = "nobody is recorded as having run these sales, so the pool cannot be split by who ran them"
    else:
        weights = {n: 1.0 for n in names}
        note_empty = ""
    total = sum(max(0.0, w) for w in weights.values())
    if total <= 0:
        return {}, _round(pool), note_empty or "no measurable basis for the split"
    shares, running = {}, 0.0
    for n in names:
        w = max(0.0, weights[n])
        amt = _round(pool * (w / total))
        shares[n] = amt
        running += amt
    drift = _round(pool - running)
    if drift:
        top = max(names, key=lambda n: (max(0.0, weights[n]), n))
        shares[top] = _round(shares[top] + drift)
    return {n: a for n, a in shares.items() if a}, 0.0, ""


# ── The engine ────────────────────────────────────────────────────────────────────────────────────

def compute_day(catalog, rules, *, declared_map, per_employee, hours_by_employee=None,
                employees=None):
    """One store-day's incentive.

    catalog           `metric_catalog.build(...)`
    rules             the tenant's ACTIVE `incentive_rule` rows, already resolved to this store /
                      these people by `management_incentive.resolve_plan` — this module does not
                      re-decide who a rule applies to.
    declared_map      `metric_catalog.declared(...)` — the store-day
    per_employee      `metric_catalog.by_employee(...)` — {name: {metric_key: {units, amount}}}
    hours_by_employee {name: hours} for a pool split by hours; absent is honestly absent.
    employees         the roster for the day. Defaults to whoever declared something. Passing it
                      explicitly is what lets an equal / by-hours pool include someone who worked and
                      sold nothing.

    Returns {store_revenue, employee_revenue, lines[], by_employee{}, unallocated, rules[]} — every
    line carries the rule, the qualifier verdict and the arithmetic, so a payout is always explainable
    without re-running it.
    """
    store_rev = mc.gross_revenue(catalog, declared_map)
    emp_rev = mc.employee_revenue(catalog, per_employee)
    roster = [n for n in (employees or sorted(per_employee or {})) if _key(n)]
    lines, totals, rule_notes = [], {}, []
    unallocated = 0.0

    for rule in (rules or []):
        if rule.get("is_active") is False:
            continue
        kind = _key(rule.get("rule_kind")).lower()
        label = _key(rule.get("label")) or _key(rule.get("key")) or kind
        if kind not in RULE_KINDS:
            rule_notes.append({"rule": label, "skipped": True,
                               "reason": f"unknown rule type '{kind}'"})
            continue
        payout = rule.get("payout") or {}
        rate = _num(payout.get("rate"))
        pbasis = _key(payout.get("basis")).lower() or ("units" if kind == "per_unit" else "amount")
        if pbasis not in MEASURE_BASES:
            pbasis = "amount"
        pkeys = payout.get("metric_keys") or []
        cap = payout.get("cap")

        store_pass, store_detail = _gate(catalog, rule.get("qualifier"), declared_map, None) \
            if _key((rule.get("qualifier") or {}).get("scope")).lower() == "store" \
            else (True, {"scope": "none", "passed": True, "reason": "no store qualifier"})

        if kind == "threshold_pool":
            if not store_pass:
                rule_notes.append({"rule": label, "paid": 0.0, "qualifier": store_detail})
                continue
            pool_value, missing = measure(catalog, declared_map, pbasis, pkeys)
            if missing:
                rule_notes.append({"rule": label, "paid": 0.0,
                                   "reason": "pays on a category this business does not declare",
                                   "missing": missing})
                continue
            pool = _round(pool_value * rate)
            if cap not in (None, ""):
                pool = min(pool, _num(cap))
            # Who shares it: the roster, filtered by an EMPLOYEE-scope qualifier when the rule has one.
            qual_scope = _key((rule.get("qualifier") or {}).get("scope")).lower()
            eligible, per_person_detail = [], {}
            for who in roster:
                if qual_scope == "employee":
                    ok, det = _gate(catalog, rule.get("qualifier"), declared_map,
                                    per_employee.get(who))
                    per_person_detail[who] = det
                    if not ok:
                        continue
                eligible.append(who)
            # "Who ran the sale" (owner 2026-10-10) weights by each person's own figure in the
            # POOL'S OWN categories, not by their whole day. A pool built on printing should be
            # divided by printing; weighting it by total revenue would hand a share to somebody who
            # sold none of what made the pool. A pool on the whole day falls back to the whole day,
            # which is the same number — so there is no second setting to get wrong.
            if _key(payout.get("split")).lower() == "individual_sales" and pkeys:
                weights = {}
                for who in roster:
                    v, _m = measure(catalog, (per_employee or {}).get(who) or {}, pbasis, pkeys)
                    weights[who] = v
            else:
                weights = emp_rev
            shares, un, note = split_pool(
                pool, eligible, basis=payout.get("split"),
                hours_by_employee=hours_by_employee, revenue_by_employee=weights)
            unallocated = _round(unallocated + un)
            for who, amt in sorted(shares.items()):
                lines.append({"employee": who, "rule": label, "rule_kind": kind, "amount": amt,
                              "basis": f"{pbasis} {pool_value} x {rate} = pool {pool}, split by "
                                       f"{_key(payout.get('split')).lower() or 'equal'}",
                              "qualifier": store_detail})
                totals[who] = _round(totals.get(who, 0.0) + amt)
            rule_notes.append({"rule": label, "pool": pool, "paid": _round(pool - un),
                               "unallocated": un, "note": note, "qualifier": store_detail,
                               "eligible": eligible, "per_employee_qualifier": per_person_detail})
            continue

        # per_unit / percent_of_amount — paid on each employee's OWN production.
        if not store_pass:
            rule_notes.append({"rule": label, "paid": 0.0, "qualifier": store_detail})
            continue
        paid_total, detail = 0.0, {}
        for who in roster:
            mine = (per_employee or {}).get(who)
            if mine is None:
                continue
            if _key((rule.get("qualifier") or {}).get("scope")).lower() == "employee":
                ok, det = _gate(catalog, rule.get("qualifier"), declared_map, mine)
                detail[who] = det
                if not ok:
                    continue
            value, missing = measure(catalog, mine, pbasis, pkeys)
            if missing:
                detail[who] = {"passed": False, "missing": missing,
                               "reason": "pays on a category this business does not declare"}
                continue
            amt = _round(value * rate)
            if cap not in (None, ""):
                amt = min(amt, _num(cap))
            if not amt:
                continue
            lines.append({"employee": who, "rule": label, "rule_kind": kind, "amount": amt,
                          "basis": f"{value} {pbasis} x {rate}",
                          "qualifier": detail.get(who, store_detail)})
            totals[who] = _round(totals.get(who, 0.0) + amt)
            paid_total = _round(paid_total + amt)
        rule_notes.append({"rule": label, "paid": paid_total, "qualifier": store_detail,
                           "per_employee_qualifier": detail})

    top = max(emp_rev.items(), key=lambda kv: (kv[1], kv[0]))[0] if emp_rev else None
    return {
        "store_revenue": store_rev,
        "employee_revenue": emp_rev,
        "top_earner": top,
        "lines": sorted(lines, key=lambda l: (l["employee"], l["rule"])),
        "by_employee": totals,
        "total": _round(sum(totals.values())),
        "unallocated": _round(unallocated),
        "rules": rule_notes,
    }
