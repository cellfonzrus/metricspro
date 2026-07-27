"""Payroll employee-identity reconciliation (mod-people, 2026-07-27 owner directive: "the names are
coming twice for the same sales rep in the report ... need one line with both scheduled and actual").

ROOT CAUSE (confirmed by reading, not guessed): the Schedule page stores a NEW shift's `employee_id`
as the employee's NUMERIC `employees.id` primary key —
    frontend/src/app/(platform)/storeops/schedule/page.tsx:294
        employee_id: emp?.id?.toString() || '',
— while every OTHER source of payroll hours (a kiosk clock punch via `storeops.timelog`, a manual
hours adjustment via `storeops.manual_hours`, and the `employees` roster's own `employee_id` column
used to build `emp_map`/RBAC) uses the BUSINESS `employees.employee_id` (e.g. "E45"). This is the
EXACT SAME mismatch `_emp_id_variants()` (storeops/router.py) already reconciles for the
shift-extension / force-clockout gate — this module is the payroll-AGGREGATION side of that identical
bug.

`GET /payroll`'s `summary` dict is keyed by whatever raw `employee_id` a source row carries, so a
person with BOTH a shift this period (numeric-id-keyed) AND a clock punch (business-id-keyed) lands
in TWO buckets — visually two rows, same name: one shows scheduled hours (pay_rate wrongly $0,
because `emp_map` is keyed by the business id and the shift-derived bucket's key never matches it),
the other shows the real clocked "actual" hours at the correct rate. Exactly the reported symptom.

FIX SCOPE — presentation only, no pay-math change (AGENT_CONTRACT: "a change to hours/rate math is
propose-first"): `reconcile_employee_identity()` relabels each row's `employee_id` to the CANONICAL
business id (when resolvable) and, for rows that resolve to the SAME canonical id, SUMS their
already-computed scheduled_hours / actual_hours / shifts / scheduled_pay / actual_pay. Every summed
number is a value the existing (unmodified) router aggregation already computed per source row before
this function ever runs — it regroups outputs, it never recomputes hours×rate. A row's grand total,
and the SUM across every row, is therefore byte-identical before/after regrouping (associativity of
addition) — proven by harness_payroll_row_merge.py, including a control fixture where NO row's raw
employee_id needs aliasing (the existing employee-consistent fixtures in
harness_payroll_rpc_equivalence.py / harness_payroll_data_flow.py all fall in this "no-op" bucket,
which is exactly why this bug was invisible to 34+80 pre-existing harness checks despite being
essentially universal — every one of those fixtures happens to use the SAME id string for a person's
shift and timelog rows, unlike real Schedule-page-created shifts).

The one NEW thing shown is `pay_rate`: corrected to the employee's real rate (an informational label
read straight from `emp_map`) even for a single, un-merged shift-only row that would otherwise always
display 0 due to the identical key bug — the row's own scheduled_pay/actual_pay DOLLAR FIGURE is never
recomputed from this corrected rate, it stays exactly what the router already computed.

KNOWN, DELIBERATELY NOT FIXED HERE (money-adjacent, propose-first, see docs/handoffs/people.md and the
Gate-1 report): because the pre-merge shift-derived row's own actual_hours already carries the
act==0->scheduled FALLBACK (nothing ever writes storeops.shifts.actual_hours from a real clock punch —
grepped, confirmed empty), summing it together with the SAME day's real timelog hours can still show a
merged row's "Actual Hrs" larger than what the person truly worked that day (the fallback and the real
punch both count). This function does not touch that — it is the artifact investigated in Deliverable 3
of the 2026-07-27 payroll package (VERDICT: real double-count, not real overwork; propose-first fix is
canonicalizing the aggregation KEY server-side inside the shift/timelog merge itself, which would
change /payroll-by-store's pushed dollar amount and is therefore out of scope for a presentation-only
change).
"""


def business_id_alias_map(employees):
    """{str(numeric employees.id): business employees.employee_id} for every employee whose two id
    forms actually differ (the common case, since the auto-generated business id is "E<numeric id>",
    e.g. numeric id 45 -> business id "E45" -- never equal as strings). An employee with no business
    employee_id, or whose business id happens to equal their numeric id (imported data), contributes
    no alias entry, which is exactly correct: nothing to reconcile."""
    alias = {}
    for e in employees or ():
        numeric = e.get("id")
        biz = str(e.get("employee_id") or "").strip()
        if numeric is None or not biz:
            continue
        numeric_s = str(numeric).strip()
        if numeric_s and numeric_s != biz:
            alias[numeric_s] = biz
    return alias


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def reconcile_employee_identity(rows, employees):
    """Collapse duplicate /payroll rows that represent the SAME physical employee (numeric-id-keyed
    shift bucket + business-id-keyed timelog bucket) into ONE row. See module docstring for the full
    root-cause + fix-scope writeup. `rows` = the router's already-built, already-$-computed summary
    rows (list of dicts with employee_id/name/store/pay_rate/scheduled_hours/actual_hours/shifts/
    scheduled_pay/actual_pay); `employees` = the same roster rows the caller already fetched (needs
    `id`, `employee_id`, `pay_rate`). A `None`/blank employee_id row (e.g. a shift with no assigned
    employee) is NEVER merged with anything — kept exactly as-is, in place."""
    if not rows:
        return rows
    alias = business_id_alias_map(employees)
    emp_map = {e.get("employee_id"): e for e in (employees or ()) if e.get("employee_id")}

    order = []
    groups = {}   # canonical key -> [(canon_id, row), ...], first-seen order preserved
    for r in rows:
        raw = r.get("employee_id")
        if raw is None or str(raw).strip() == "":
            canon = None
        else:
            canon = alias.get(str(raw), raw)
        group_key = canon if canon is not None else (id(r), "unmergeable")
        if group_key not in groups:
            groups[group_key] = []
            order.append(group_key)
        groups[group_key].append((canon, r))

    out = []
    for gk in order:
        members = groups[gk]
        canon, first = members[0]
        if len(members) == 1:
            merged = dict(first)
            if canon is not None:
                merged["employee_id"] = canon
                real = emp_map.get(canon)
                if real is not None:
                    merged["pay_rate"] = _num(real.get("pay_rate"))
            out.append(merged)
            continue
        # 2+ rows resolve to the same canonical employee -> ONE merged row, every numeric field the
        # exact SUM of the members' already-computed values (never recomputed from merged hours).
        real = emp_map.get(canon)
        merged = {
            "employee_id": canon,
            "name": next((r.get("name") for _, r in members if r.get("name")), first.get("name")),
            "store": first.get("store"),
            "pay_rate": _num(real.get("pay_rate")) if real is not None
                        else max(_num(r.get("pay_rate")) for _, r in members),
            "scheduled_hours": round(sum(_num(r.get("scheduled_hours")) for _, r in members), 2),
            "actual_hours": round(sum(_num(r.get("actual_hours")) for _, r in members), 2),
            "shifts": sum(int(r.get("shifts") or 0) for _, r in members),
            "scheduled_pay": round(sum(_num(r.get("scheduled_pay")) for _, r in members), 2),
            "actual_pay": round(sum(_num(r.get("actual_pay")) for _, r in members), 2),
        }
        # Dominant-store LABEL only (non-monetary) across the merged members — the member with the
        # larger hours weight wins, first-seen tie-break (mirrors the router's own dominant-store
        # convention for a single employee's multi-store hours).
        best = max(members, key=lambda cr: (_num(cr[1].get("scheduled_hours")) + _num(cr[1].get("actual_hours"))))
        merged["store"] = best[1].get("store") or merged["store"]
        out.append(merged)
    return out
