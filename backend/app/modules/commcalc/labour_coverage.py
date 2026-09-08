"""LABOUR-COST COVERAGE — three states for a store's salary line, never a silent $0.00.

OWNER DIRECTIVE 2026-09-08: "pull the exact salaries paid as per the schedule and update the same in
the expenses as those are not getting updated for a lot of stores; if we have the actual hours then
the salary is based on actual hours, if not then the salary is based on scheduled hours FOR THAT
MONTH to go in the gross profit FOR THAT MONTH."

This module owns the READ side of that sentence: whether the salary a store shows in the GP report
and the P&L was MEASURED for the month on screen, and if so from what. It computes NO dollars — the
dollar derivations already exist and are deliberately NOT duplicated here:

  • `account/coa.derive_wage_cells` already implements "actual hours else scheduled hours"
    (`hrs = safe_float(s['actual_hours']) or safe_float(s['scheduled_hours'])`) and turns it into
    money, hourly × pay_rate and salaried × monthly equivalent. That IS the owner's rule; this
    module reports the BASIS it resolved to, it never re-derives the amount.
  • `commcalc/expenses_effective.effective_expense_rows` already owns the sticky carry-forward that
    decides WHICH month's rows a period displays. This module reads its `carried_from` answer.

WHY IT EXISTS (the defect class, measured on LUXELINK 854f6d7b-…-646f560d4f4c, 2026-09-08):

  1. SILENT ZERO. July 2026: three stores (`3352 26th`, `3735 26th`, `Chicago heights`) have NO
     'Employee Salaries' expense row at all, and their July shifts carry 0.0 hours on BOTH
     `actual_hours` and `scheduled_hours` (6 / 17 / 15 shifts, zero hours each). Their salary line
     therefore renders $0.00 — indistinguishable on screen from a store that genuinely paid nobody.
     It is not zero, it is UNKNOWN, and the fix for it is an upload, not a code change.
  2. ORG-WIDE SUPPRESSION. `coa.build_inputs` keeps ONE boolean (`has_payroll_gross`) for the whole
     org: the moment ANY store has an authoritative payroll figure, the hours estimate is suppressed
     for EVERY store — so a store with no entry books $0.00 rather than its own hours. Reported
     here per store; the booking behaviour itself is config-gated (`payroll_authority_grain`).
  3. MONTH GRAIN. A period with no `store_expenses` rows of its own carries the previous month's
     MANUAL rows verbatim. Live at the time of writing: September 2026 shows August's
     $295,610.15 — including $158,960.01 of salary — while September's own shifts derive
     $53,059.82. Those dollars belong to August. `carried_from` makes that visible on the report
     instead of leaving a stale figure looking like a measurement.
  4. DOUBLE BOOKING. August 2026 carries twenty $500.00 'Employee Commission' expense rows
     ($10,000.00) while `commcalc.rep_commissions` for the same month totals $11,118.78 and is
     ALREADY deducted separately — by the GP report as `rep_pay` and by the P&L as the `rep_comm`
     line. Same labour dollars, two routes. Detected here and REPORTED; nothing is netted
     automatically, because which of the two routes is authoritative is the owner's call.

SHAPE. Mirrors the house's existing silent-zero detector `exec_metric_defs.bucket_coverage`
(`{'scanned', 'gaps', …, 'note'}`) so the two banners read alike: DISPLAY-ONLY and
TOTAL-PRESERVING — every function here is pure, moves no figure, and a caller that ignores the
result gets byte-identical numbers.

RULE TWO: no tenant, carrier or account name appears in this file. The payroll/commission expense
vocabularies are handed in by the caller from per-org config
(`commcalc.account_config.payroll_expense_names`, `.labour_commission_expense_names`).

Proof: backend/harness_labour_coverage.py (DB-free).
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §4.
"""

# The producer tokens that carry an EXACT gross payroll and therefore make a period authoritative.
# Kept in lockstep with `account/coa._WAGES_AUTHORITATIVE_KEYS` — 'additional_payroll' is an excess
# ON TOP of the clock-in gross and must never appear here (it would delete the wages line).
AUTHORITATIVE_SOURCE_KEYS = frozenset({"payroll_gross"})

# ── the three states, plus the two that say WHICH measurement ────────────────────────────────────
ENTERED = "entered"                    # measured: an authoritative payroll figure for THIS month
DERIVED_ACTUAL = "derived_actual"      # measured: no entry, but the month has actual clocked hours
DERIVED_SCHEDULED = "derived_scheduled"  # measured: no entry, no actual hours, scheduled hours exist
CARRIED = "carried"                    # NOT measured for this month — another month's dollars
NOT_MEASURED = "not_measured"          # NOT measured: no entry, and no hours on either column
NO_STAFF = "no_staff"                  # genuinely zero: the store is inactive and had no shifts

MEASURED_STATES = frozenset({ENTERED, DERIVED_ACTUAL, DERIVED_SCHEDULED})
UNMEASURED_STATES = frozenset({CARRIED, NOT_MEASURED})


def _norm(s):
    """The house store-key normalisation (`coa._norm_store`): strip, blank -> None. Deliberately
    CASE-SENSITIVE, because every existing store join in the finance path is."""
    return (str(s or "").strip()) or None


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _names(vocab):
    """PURE: a config name list -> lowercased set. None/empty -> empty set (nothing is configured,
    so nothing is claimed — the byte-identical default for every org)."""
    return {str(n).strip().lower() for n in (vocab or ()) if str(n or "").strip()}


def authoritative_codes(exp_rows, payroll_names):
    """PURE: the store_codes whose salary for this period is an AUTHORITATIVE entered figure.

    Mirrors `coa.build_inputs`'s ruling-K2 predicate EXACTLY, so the coverage banner can never
    disagree with the booking it describes:
      • a producer row whose `source_key` is in AUTHORITATIVE_SOURCE_KEYS (the exact gross), or
      • a MANUAL row (`source_key` NULL/blank) whose `expense_name` is in the org's configured
        `payroll_expense_names`, with a NON-ZERO amount — a $0.00 placeholder is not a measurement.
    A negative amount (a correction) still counts: money was keyed by hand either way.
    """
    want = _names(payroll_names)
    out = set()
    for r in (exp_rows or []):
        code = _norm(r.get("store_code"))
        if not code:
            continue
        sk = str(r.get("source_key") or "").strip()
        if sk in AUTHORITATIVE_SOURCE_KEYS:
            out.add(code)
            continue
        name = str(r.get("expense_name") or "").strip().lower()
        if want and not sk and name in want and _f(r.get("amount")):
            out.add(code)
    return out


def allocated_names(exp_rows, payroll_names, min_stores=3, dominance=0.8):
    """PURE: the payroll names that are a FLAT ALLOCATION rather than a per-store measurement.

    WHY THIS EXISTS (measured, LuxeLink July 2026): the three stores whose employee wage is missing
    entirely still carry a 'DM Salaries' row — $1,275.00, the SAME figure at all twenty stores, and
    'Owner / Mgmt Salaries' $500.00 likewise. Because 'DM Salaries' is one of the org's configured
    `payroll_expense_names`, those stores register as "payroll entered" and their missing employee
    wage never surfaces. A district-manager cost spread evenly over the estate is a real cost, but
    it is not a measurement of THAT store's staff, and treating it as one is what let "a lot of
    stores" go unnoticed.

    The test is evidence, not vocabulary (RULE TWO — no name is hardcoded): a name qualifies when
    ONE non-zero amount is shared by at least `min_stores` stores AND by at least `dominance` of
    the stores carrying that name. One store paying the same as another proves nothing; nineteen
    paying $1,275.00 to the cent is an allocation. Names whose amounts genuinely vary per store (a
    real payroll figure) never reach the threshold and are never flagged.

    `dominance` is a MAJORITY, not unanimity, on purpose: LuxeLink's live July rows carry
    'DM Salaries' at $1,275.00 on nineteen stores and $1,275.01 on the twentieth, and a unanimity
    test let that single cent mask all three under-reported stores. A one-cent rounding residue must
    not be able to turn an allocation back into a measurement.
    """
    want = _names(payroll_names)
    per_name = {}
    for r in (exp_rows or []):
        code = _norm(r.get("store_code"))
        name = str(r.get("expense_name") or "").strip().lower()
        if not code or name not in want or str(r.get("source_key") or "").strip():
            continue
        cells = per_name.setdefault(name, {})
        cells[code] = round(cells.get(code, 0.0) + _f(r.get("amount")), 2)
    out = set()
    for name, cells in per_name.items():
        if len(cells) < min_stores:
            continue
        tally = {}
        for v in cells.values():
            tally[v] = tally.get(v, 0) + 1
        amount, held_by = max(tally.items(), key=lambda kv: (kv[1], -abs(kv[0])))
        if amount and held_by >= min_stores and held_by >= dominance * len(cells):
            out.add(name)
    return out


def entered_amounts(exp_rows, payroll_names):
    """PURE: {store_code: Σ authoritative payroll dollars entered for this period}. Reporting only —
    the P&L books these rows itself through its own routing table."""
    want = _names(payroll_names)
    out = {}
    for r in (exp_rows or []):
        code = _norm(r.get("store_code"))
        if not code:
            continue
        sk = str(r.get("source_key") or "").strip()
        name = str(r.get("expense_name") or "").strip().lower()
        if sk in AUTHORITATIVE_SOURCE_KEYS or (want and not sk and name in want):
            out[code] = round(out.get(code, 0.0) + _f(r.get("amount")), 2)
    return out


def hours_basis_by_code(shifts):
    """PURE: {store_code: {'shifts', 'actual_hours', 'scheduled_hours', 'basis'}}.

    `basis` states which side of the owner's rule this store's month actually resolves to:
      'actual'    — at least one shift carries actual hours (the preferred basis)
      'scheduled' — no actual hours anywhere, but scheduled hours exist (the documented fallback)
      None        — shifts exist but carry NO hours on either column: nothing to compute a salary
                    from. This is the LIVE state of every LuxeLink shift row inspected on
                    2026-09-08 (0 of 472 July shifts and 0 of 748 August shifts had actual hours),
                    and for three July stores neither column had a number.

    Per-shift precedence is `actual or scheduled`, matching `coa.derive_wage_cells` exactly, so the
    store's basis is the basis its dollars were actually computed on. Deleted shifts must be
    filtered by the caller's query (`is_deleted = false`), as the wage path does.
    """
    out = {}
    for s in (shifts or []):
        code = _norm(s.get("store_code"))
        if not code:
            continue
        cell = out.setdefault(code, {"shifts": 0, "actual_hours": 0.0, "scheduled_hours": 0.0,
                                     "basis": None})
        cell["shifts"] += 1
        cell["actual_hours"] += _f(s.get("actual_hours"))
        cell["scheduled_hours"] += _f(s.get("scheduled_hours"))
    for cell in out.values():
        cell["actual_hours"] = round(cell["actual_hours"], 2)
        cell["scheduled_hours"] = round(cell["scheduled_hours"], 2)
        cell["basis"] = ("actual" if cell["actual_hours"] else
                         ("scheduled" if cell["scheduled_hours"] else None))
    return out


def store_state(code, auth, hours, carried_from, is_active=True):
    """PURE: the coverage state of ONE store for the period. See the state constants above.

    Ordering matters and encodes the owner's rule:
      carried  > entered > derived_actual > derived_scheduled > not_measured / no_staff
    A CARRIED period wins over everything because no figure on it was measured for this month — an
    entered row that came from the previous month is still the previous month's dollars.
    """
    if carried_from:
        return CARRIED
    if code in (auth or ()):
        return ENTERED
    cell = (hours or {}).get(code) or {}
    if cell.get("basis") == "actual":
        return DERIVED_ACTUAL
    if cell.get("basis") == "scheduled":
        return DERIVED_SCHEDULED
    if not cell.get("shifts") and not is_active:
        return NO_STAFF
    return NOT_MEASURED


def labour_coverage(store_codes, exp_rows, shifts, payroll_names, period,
                    carried_from=None, active_codes=None):
    """PURE: the whole report. DISPLAY-ONLY — moves no figure.

    Returns {'period', 'carried_from', 'scanned', 'states': {code: state},
             'detail': {code: {...}}, 'counts': {state: n}, 'gaps': [code…], 'note': str|None}

    `gaps` are the stores whose salary line is NOT a measurement of this month — the ones whose
    $0.00 (or carried figure) must not be read as "this store paid this". `scanned == 0` returns no
    gaps: an org with no stores is not a broken feed, and a banner that is always on is a banner
    nobody reads (the `bucket_coverage` discipline).
    """
    codes = [c for c in (_norm(x) for x in (store_codes or ())) if c]
    # COVERAGE authority deliberately EXCLUDES flat allocations (see `allocated_names`): a store
    # whose only payroll row is an estate-wide $1,275.00 has no measurement of its own staff. The
    # BOOKING authority in `coa.build_inputs` is unchanged and still uses the full vocabulary — this
    # narrower set only decides what the banner says, never what is booked.
    allocated = allocated_names(exp_rows, payroll_names)
    measuring = [n for n in _names(payroll_names) if n not in allocated]
    auth = authoritative_codes(exp_rows, measuring)
    entered = entered_amounts(exp_rows, measuring)
    hours = hours_basis_by_code(shifts)
    active = None if active_codes is None else {c for c in (_norm(x) for x in active_codes) if c}

    states, detail, counts = {}, {}, {}
    for c in codes:
        st = store_state(c, auth, hours, carried_from,
                         is_active=(True if active is None else c in active))
        states[c] = st
        counts[st] = counts.get(st, 0) + 1
        cell = hours.get(c) or {}
        detail[c] = {
            "state": st,
            "measured": st in MEASURED_STATES,
            "entered_amount": entered.get(c),
            "hours_basis": cell.get("basis"),
            "actual_hours": cell.get("actual_hours", 0.0),
            "scheduled_hours": cell.get("scheduled_hours", 0.0),
            "shifts": cell.get("shifts", 0),
            "carried_from": carried_from or None,
        }
    gaps = sorted(c for c in codes if states[c] in UNMEASURED_STATES)
    return {"period": period, "carried_from": carried_from or None,
            "scanned": len(codes), "states": states, "detail": detail,
            "counts": counts, "gaps": gaps, "allocated_names": sorted(allocated),
            "note": coverage_note(len(codes), counts, carried_from, period, sorted(allocated))}


def coverage_note(scanned, counts, carried_from, period, allocated=()):
    """PURE: the one sentence a reader needs, or None when every store was measured.

    Never claims a dollar figure and never blames the reader — it names the feed to fix, because
    a missing upload is REPORTED, not papered over."""
    if not scanned:
        return None
    if carried_from:
        return (f"Salary for {period} is NOT measured: this month has no expense rows of its own, "
                f"so every store is showing {carried_from}'s figures carried forward. Those dollars "
                f"belong to {carried_from}. Save {period}'s expenses, or post its payroll, before "
                f"reading gross profit for this month.")
    n_missing = counts.get(NOT_MEASURED, 0)
    if not n_missing:
        return None
    tail = ""
    if allocated:
        tail = (f" ({', '.join(allocated)} is charged at one identical amount to every store, so it "
                f"is an allocation and does not measure a store's own staff.)")
    return (f"{n_missing} of {scanned} store(s) have NO salary measurement for {period}: no payroll "
            f"figure was entered and their shifts carry hours on neither the actual nor the "
            f"scheduled column, so their salary is UNKNOWN — it is not $0.00. Enter the hours (or "
            f"the payroll) for those stores; the number on screen understates their labour "
            f"cost.{tail}")


def commission_collisions(exp_rows, rep_pay_by_code, commission_names, tolerance=0.005):
    """PURE: labour dollars that reach the report by TWO routes at once.

    The GP report deducts `rep_pay` (Σ `rep_commissions.total_payout`) SEPARATELY from `exp_total`,
    and the P&L books the same figures on the `rep_comm` line separately from `store_opex`. So an
    expense row whose name is in the org's `labour_commission_expense_names` is the SAME labour
    cost a second time.

    Returns {'names': [...], 'stores': [{'store_code','expense','rep_pay','double_booked'}…],
             'total_double_booked': float, 'note': str|None}. Reports only — nothing is netted here,
    because which route is authoritative is a money decision and money decisions are the owner's.
    """
    want = _names(commission_names)
    if not want:
        return {"names": [], "stores": [], "total_double_booked": 0.0, "note": None}
    by_code = {}
    for r in (exp_rows or []):
        name = str(r.get("expense_name") or "").strip().lower()
        code = _norm(r.get("store_code"))
        if code and name in want:
            by_code[code] = round(by_code.get(code, 0.0) + _f(r.get("amount")), 2)
    stores, total = [], 0.0
    for code in sorted(by_code):
        exp = by_code[code]
        rep = round(_f((rep_pay_by_code or {}).get(code)), 2)
        # The overlap is what BOTH routes carry: neither route can double-book more than it holds.
        dup = min(abs(exp), abs(rep))
        if dup <= tolerance:
            continue
        stores.append({"store_code": code, "expense": exp, "rep_pay": rep,
                       "double_booked": round(dup, 2)})
        total = round(total + dup, 2)
    note = None
    if stores:
        note = (f"${total:,.2f} of commission is booked TWICE across {len(stores)} store(s): once as "
                f"an expense row and again from commcalc.rep_commissions, which the GP report and "
                f"the P&L already deduct on their own line. Remove one of the two routes.")
    return {"names": sorted(want), "stores": stores,
            "total_double_booked": total, "note": note}


# ── the ONE hours reader both surfaces go through (I/O; pure logic above is DB-free) ──────────────
def load_shift_hours(client, org_id, period):
    """I/O: the period's non-deleted shifts, hours columns only, for `hours_basis_by_code`.

    `period` is either house spelling ('August 2026' or '2026-08') — parsed by the ONE shared
    parser `expenses_effective.period_sort_key`, never a second copy of it. Org-scoped
    (multi-tenant contract §2). Never raises — a missing table or column degrades to [], which
    yields NOT_MEASURED rather than a fabricated zero, the honest answer when a feed cannot be read.

    Deliberately its own narrow select rather than a second copy of `coa.wages_by_store`'s
    employee-joined read: that one exists to compute MONEY and must keep its exact shape."""
    from app.modules.commcalc.expenses_effective import period_sort_key
    y, m = period_sort_key(period)
    if not y or not m:
        return []
    nxt = f"{y + 1:04d}-01-01" if m == 12 else f"{y:04d}-{m + 1:02d}-01"
    try:
        return (client.schema("storeops").table("shifts")
                .select("store_code,scheduled_hours,actual_hours,shift_date,is_deleted")
                .eq("org_id", org_id).eq("is_deleted", False)
                .gte("shift_date", f"{y:04d}-{m:02d}-01").lt("shift_date", nxt)
                .range(0, 19999).execute().data) or []
    except Exception:
        return []
