"""THE ONE HOME for "fetch the evidence a pay path needs for a period, and say whether it may pay".

WHY THIS MODULE EXISTS (owner directive 2026-10-12; the defect, measured, not hypothesised)
-------------------------------------------------------------------------------------------
`storeops/payroll_hours_basis.py` (index §14y) is the one home for the DECISION — *which hours is
this employee paid for on this day, and on what basis*. It is PURE: it takes evidence and returns
`(hours, basis, detail)`. The defect was never in that decision. It was that **four pay paths called
it with no evidence at all**:

    account/coa.py                  the P&L wage line           resolve_shift_row(s, cfg, emp)
    commcalc/labour_coverage.py     the labour-coverage basis    resolve_shift_row(s, cfg, emp)
    storeops/pto_accrual.py         hours PTO accrues on         resolve_shift_row(s, cfg, emp)
    hr/router.py                    HR total comp                resolve_shift_row(s, cfg, emp)

With no `cell`, `resolve_shift_row` can only answer from the SHIFT ROW — i.e. from
`shifts.actual_hours`, the dead column the whole §14y package exists because of (**7 of 7,740** live
rows carry it). Under `hours_basis_mode = 'clocked_only'` with `pay_from_schedule_default = false`,
which is what the house org is live on, every such row therefore resolves `not_measured_unpaid` =
**ZERO**. Replayed over live house shifts on 2026-10-12, the P&L wage path booked:

    Jun 555 shifts $75,837.00 -> $0.00   Jul 640 $88,128.00 -> $0.00   Aug 613 $85,389.00 -> $0.00
    Sep 603 $81,132.02 -> $0.00          Oct 341 $44,247.00 -> $0.00      total $374,733.02 -> $0

while the punches that measured those days sat unread in `storeops.timelog` (Aug 3,781.6 h, Sep
4,724.0 h). And because none of the four consulted `payroll_hours_basis.interlock`, nothing stopped
the zero from being published as a figure.

THE CLASS, NOT THE INSTANCE
---------------------------
The instance is "the P&L wage line reads a dead column". The class is:

    **a pay-path caller could reach the one home WITHOUT the evidence the one home needs, and
      publish the answer anyway — there was no shared way to GET that evidence and no gate that
      noticed its absence.**

So the fix is not a fifth punch query in each of the four. It is:

  1. **ONE read.** `read_pay_evidence` is the only place in the pay path that fetches punches and
     manual-hours rows for a period. `storeops/clock_variance_api._load` — which had the only such
     read before this — now dereferences it instead of carrying its own, so there is one path and
     not two. Same tables, same column lists, same half-open range, same per-table degradation.
  2. **ONE assembly.** `period_evidence` hands those rows to `payroll_hours_basis.build_evidence`,
     resolves the org's config through `load_config`, and computes `coverage` + `interlock` in the
     same breath — so a caller cannot get the evidence without also getting the verdict on whether
     it is safe to pay from it.
  3. **A REFUSAL IS REPORTED, NEVER A ZERO.** `refusal(res)` is the one sentence a caller surfaces.
     CLAUDE.md: a defect found in live data is REPORTED, never "fixed" by writing code that hides
     it — and publishing $0.00 because the punches did not arrive is exactly that code.
  4. **LOCKED.** `backend/harness_payroll_hours_basis_lock.py` §H fails the build if any pay-path
     caller stops passing evidence, stops consulting the interlock, or grows a second punch read.

WHAT THIS MODULE IS NOT
-----------------------
It makes NO decision. Every verdict in its output comes from `payroll_hours_basis`; this module only
does I/O and hands the rows over. It holds no tenant, store, employee or carrier name (RULE TWO) and
no date literal — the effective-from floor is a config row (mig 1076), read by the one home.

ORG-SCOPED ALWAYS. Every query carries `.eq("org_id", org_id)`; CI enforces it. `client` must already
be SCHEMA-SCOPED to `storeops` by the caller (`storeops.router.sb()` / `clock_variance_api.sb()` /
`client.schema("storeops")`) — an unscoped client reads `public`, finds nothing, and the pay path
renders zeros from an empty read, which is this module's own defect wearing a different hat.

NEVER RAISES. A failed read is REPORTED in `blockers` and the period is treated as UNMEASURED, which
makes the interlock refuse — the safe direction. "We could not look" and "nobody clocked" are
different answers, and they must never print the same.

Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §14y. Proof: `backend/harness_payroll_hours_basis.py`
(section E, the regression). Lock: `backend/harness_payroll_hours_basis_lock.py` §H.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.modules.storeops import payroll_hours_basis as _hb

#: THE COLUMN LISTS, spelled ONCE. They are the superset of what `clock_variance_api._load` read and
#: what `build_evidence` documents it takes, so neither caller narrows the other's evidence.
PUNCH_SELECT = "employee_id,work_date,hours,clock_in,clock_out,store_code,auto_clocked_out"
MANUAL_SELECT = "employee_id,work_date,hours,reason"

#: The tables this module is the one reader of, on the pay path. Named so the lock can assert that
#: no other pay-path module reads them for a period.
PUNCH_TABLE = "timelog"
MANUAL_TABLE = "manual_hours"


class EvidenceRefused(RuntimeError):
    """The evidence for this period may not be paid from, and the caller asked for a FIGURE.

    Raised by a producer whose whole contract is to return money (`coa.wages_by_store` returns
    `{store: dollars}` and has two call sites that book every cell it yields). Such a producer
    cannot return "refused" in its own shape, and returning `{}` would be indistinguishable from a
    tenant with no shifts — so it raises, the caller's existing degraded-source channel reports the
    sentence, and NOTHING is booked. A refusal must be louder than an empty dict, never quieter.
    """


def day_after(d) -> str:
    """'YYYY-MM-DD' + 1 day, for a caller that holds an INCLUSIVE period end.

    THE RANGE CONVENTION IS HALF-OPEN `[lo, hi)` everywhere here, because that is what
    `clock_variance_api._range_rows` has always used and what `payroll_hours_basis`'s own period
    resolver returns. A caller holding `period_end` (inclusive — `pto_accrual.pto_month_bounds`
    returns one) converts through THIS function rather than writing `lte` on one surface and `lt` on
    another, which is how a month boundary comes to be read one day differently by two reports.
    """
    s = str(d or "")[:10]
    try:
        return (date.fromisoformat(s) + timedelta(days=1)).isoformat()
    except ValueError:
        return s


def _range_rows(client, table, select, org_id, col, lo, hi):
    """Half-open `[lo, hi)` on `col`, org-scoped. A failure is REPORTED, never raised and never
    silently empty — `{table, reason}`, the same shape `clock_variance_api._rows` reports."""
    try:
        q = client.table(table).select(select).eq("org_id", org_id)
        if lo:
            q = q.gte(col, str(lo)[:10])
        if hi:
            q = q.lt(col, str(hi)[:10])
        return (q.range(0, 49999).execute().data or []), None
    except Exception as e:                                        # noqa: BLE001 — reported, not raised
        return [], {"table": table, "reason": str(e)}


def read_pay_evidence(client, org_id, lo, hi):
    """THE ONE READ of the period's punches and manual-hours corrections. `(rows, blockers)`.

    `rows` is `{"punches": [...], "manual": [...]}`; `blockers` is a list of `{table, reason}`.
    `[lo, hi)` is half-open — see `day_after`. `client` is already schema-scoped to `storeops`.

    This is deliberately NOT a shifts read. Every one of the four callers already holds the shifts it
    needs, each with its own column list and its own soft-delete and store filters, and those reads
    are each the one home for their own report's scope. What they all LACKED was the punches, so
    that — and only that — is what this centralises. Factoring their four different shift reads into
    one would be a second, larger change hiding inside a fix.
    """
    blockers = []

    def take(pair):
        rows, err = pair
        if err:
            blockers.append(err)
        return rows

    punches = take(_range_rows(client, PUNCH_TABLE, PUNCH_SELECT, org_id, "work_date", lo, hi))
    manual = take(_range_rows(client, MANUAL_TABLE, MANUAL_SELECT, org_id, "work_date", lo, hi))
    return {"punches": punches, "manual": manual}, blockers


def period_evidence(client, org_id, lo, hi, shifts, *, employee_rows=None, cfg=None,
                    punch_rows=None):
    """THE ONE ASSEMBLY a pay path uses. Evidence AND the verdict on whether it may pay from it.

    Returns:
        `evidence`   the `build_evidence` index, keyed `(raw employee_id, 'YYYY-MM-DD')`. Pass the
                     matching cell to `resolve_shift_row(shift, cfg, emp, cell, day)`.
        `cfg`        this org's resolved hours-basis config (`payroll_hours_basis.load_config`).
        `coverage`   `payroll_hours_basis.coverage` over the period — floor-aware.
        `interlock`  `payroll_hours_basis.interlock`'s verdict. **CONSULT IT.**
        `blockers`   failed reads, as `{table, reason}`. Each one is why a figure may be short.
        `refused`    True iff the interlock refuses OR a read failed. A caller that publishes a
                     figure anyway is publishing a number it was told not to.
        `reason`     the one sentence to show a reader when `refused`.

    `cfg` may be passed when the caller already read it (so the row is read once per request);
    `punch_rows` likewise, for a caller that already holds this period's rows — which is how
    `clock_variance_api` reuses its own bundle without a second query.

    A FAILED READ REFUSES **where the punches decide the money** — i.e. in clocked-only mode, where
    an unreadable punch table and an empty one produce the same arithmetic and only one of them is a
    fact about the business. In schedule-fallback mode it only REPORTS (the blocker stays in
    `blockers`), because in that mode an unmeasured day is paid from the schedule by design, so a
    failed punch read cannot shorten the figure — and refusing on it withheld whole wage lines from
    pre-migration databases. See the note at the test itself.
    """
    if punch_rows is None:
        rows, blockers = read_pay_evidence(client, org_id, lo, hi)
    else:
        rows, blockers = dict(punch_rows), []
    if cfg is None:
        cfg = _hb.load_config(client, org_id)
    evidence = _hb.build_evidence(shifts=shifts or (), punches=rows.get("punches") or (),
                                  manual_rows=rows.get("manual") or ())
    cov = _hb.coverage(evidence, cfg, employee_rows=employee_rows)
    lock = _hb.interlock(cov, cfg)
    # ── WHEN A FAILED READ REFUSES, AND WHEN IT ONLY REPORTS ────────────────────────────────────
    # A blocker refuses ONLY where the punches actually decide the money — i.e. where the org's
    # basis depends on them. That test is not invented here: it is the one `interlock` already
    # states ("this org still pays an unmeasured day from the schedule, so there is no clocked-only
    # basis to protect"), so it is DEREFERENCED off `hours_basis_mode` rather than spelled a second
    # time with its own opinion.
    #
    # MEASURED, and it is why this is not `or bool(blockers)`: a pre-migration database (or any
    # tenant whose `storeops.timelog` / `manual_hours` tables are absent) reports two blockers on
    # every read. Refusing on that alone WITHHELD THE WHOLE WAGE LINE for a schedule-fallback org
    # whose figure the punches could not have changed — caught by `harness_eep_pl_wiring.py` (p13,
    # p20, p21, w6: `100h x $20 = 2000` vanished). Failing in that direction turns a missing table
    # into a missing P&L line, which is the opposite of reporting a defect.
    #
    # THE BLOCKER IS NEVER DROPPED either way: it stays in `blockers` for the caller to surface, so
    # "we could not look" and "nobody clocked" still do not print the same.
    _mode_needs_punches = cfg.get("hours_basis_mode") == _hb.MODE_CLOCKED_ONLY
    refused = bool(lock.get("refused")) or (bool(blockers) and _mode_needs_punches)
    reason = None
    if lock.get("refused"):
        reason = lock.get("reason")
    elif blockers and _mode_needs_punches:
        reason = ("This period's clock evidence could not be read in full, so these hours are not a "
                  "measurement of what was worked: "
                  + "; ".join(sorted({str(b.get("table") or "?") for b in blockers}))
                  + ". The figure is withheld rather than published short.")
    # THE DAYS `paid_hours_rows` WILL NOT EMIT, named rather than dropped: a punch on a day no
    # shift covers. `GET /storeops/payroll` counts such a punch; these four shift-row surfaces never
    # have, and a wiring fix is not where that changes. Absence is never silent here.
    _sched_keys = {((s or {}).get("employee_id"), str((s or {}).get("shift_date") or "")[:10])
                   for s in (shifts or ())}
    unscheduled = sorted({(str(k[0]), k[1]) for k, c in evidence.items()
                          if k not in _sched_keys and isinstance(c, dict)
                          and (c.get("_raw_punches") or c.get("manual_rows"))})
    return {"evidence": evidence, "cfg": cfg, "coverage": cov, "interlock": lock,
            "blockers": blockers, "refused": refused, "reason": reason,
            "unscheduled_punch_days": [{"employee_id": e, "date": d} for e, d in unscheduled],
            "period": {"from": lo, "to_exclusive": hi}}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PAID HOURS PER SHIFT ROW — why a shift-row loop cannot just pass a cell and be done
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# `payroll_hours_basis.resolve_shift_row(shift, cfg, emp, cell)` returns **0.0** for a day a punch
# measured, on purpose: the punch's hours are counted FROM THE PUNCH ROW, at the store the person
# actually stood in (`router._shift_actual_contribution`'s long-standing no-double-count rule). The
# Payroll screen can do that because it iterates `shifts` AND `timelog` and sums both.
#
# ★ THE FOUR CALLERS THIS MODULE EXISTS FOR ITERATE SHIFTS ONLY. ★ So handing them a cell and
# nothing else swaps one zero for another: `not_measured_unpaid` 0.0 becomes `clocked` 0.0, and the
# P&L wage line still books $0.00 over a fully-clocked month. (Caught by
# `harness_payroll_hours_basis.py` §P4 while this fix was being written — the first cut of it did
# exactly that.)
#
# So the DAY is resolved ONCE, by the one home, and then ALLOCATED back across that day's shift rows.
# The allocation weights are each row's OWN pre-evidence contribution, which makes the split
# byte-identical to today wherever the day's total is unchanged (one shift row: exact; several rows
# on one day: the same proportions those rows always had), while the TOTAL is the one home's answer.
# No caller re-decides anything, and nothing is counted twice: the punch is counted once, as the
# day's total, and placed at the store its own row names.


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _store_for_day(cell, shifts_of_day):
    """The store a day's paid hours belong to when the shift rows disagree with the punch.

    THE PUNCH WINS, because the punch is where the person actually was and the schedule is only
    where they were expected — on a borrowed day those differ and the labour cost belongs where the
    work happened. This is `clock_variance_api._store_of`'s rule, stated here once so the money path
    and the variance board place a borrowed day at the same store.
    """
    for t in (cell or {}).get("_raw_punches") or ():
        code = str((t or {}).get("store_code") or "").strip()
        if code:
            return code
    for s in shifts_of_day or ():
        code = str((s or {}).get("store_code") or "").strip()
        if code:
            return code
    return None


def paid_hours_rows(res, shifts, *, emp_by_id=None, cfg=None):
    """PURE: one row PER SHIFT ROW, carrying that row's share of its DAY's paid hours.

    `[{shift, employee_id, date, hours, basis, store_code, detail, day_hours}]`, **the same length
    and the same order as `shifts`**, so a caller replaces `resolve_shift_row(...)[0]` with
    `row["hours"]` and changes nothing else about its own loop, its own store attribution or its own
    rate maths. Zip it by POSITION, never by `id(row["shift"])` — two shift rows can be the same
    dict object, and an identity map collapses them into one share paid twice.

    `res` is a `period_evidence` result (or `None` / `{}`, which degrades to the evidence-free
    answer — byte-identical to the pre-2026-10-12 call and therefore safe for a caller that could
    not read). `store_code` is the day's attributed store (`_store_for_day`); a caller that
    attributes its own way can ignore it, and `coa` does because it has a home-store fallback.

    DAYS WITH NO SHIFT ROW ARE NOT EMITTED, deliberately. These four callers' day universe is the
    shifts they themselves read, and widening it here — to a punch on an unscheduled day — would add
    hours to four reports as a side effect of a wiring fix. That is a separate question with its own
    answer (`GET /storeops/payroll` already counts such a punch) and it is REPORTED rather than
    folded in: `unscheduled_punch_days` on `period_evidence`'s result names them.
    """
    res = res if isinstance(res, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else (res.get("cfg") or _hb.resolve_config(None))
    ev = res.get("evidence") or {}
    emp_by_id = emp_by_id or {}

    # GROUPED BY POSITION, not by object identity: two shift rows that happen to be the SAME dict
    # object (an aliased row, a caller reusing a template) must still each get their own share, and
    # keying on `id()` would silently collapse them into one and then emit it twice.
    by_day = {}
    rows_all = list(shifts or ())
    for _i, s in enumerate(rows_all):
        key = ((s or {}).get("employee_id"), str((s or {}).get("shift_date") or "")[:10])
        by_day.setdefault(key, []).append((_i, s))

    out_by_pos = {}
    for (eid, day), rows in by_day.items():
        idxs = [i for i, _s in rows]
        rows = [_s for _i, _s in rows]
        cell = ev.get((eid, day))
        emp = emp_by_id.get(eid)
        # THE DAY, RESOLVED ONCE, BY THE ONE HOME. `cell=None` keeps the evidence-free arithmetic.
        if cell is None:
            day_h, basis, detail = _hb.resolve_shift_row(rows[0] if len(rows) == 1 else
                                                         {"shift_date": day,
                                                          "scheduled_hours": sum(_f(r.get("scheduled_hours")) for r in rows),
                                                          "actual_hours": sum(_f(r.get("actual_hours")) for r in rows)},
                                                         cfg, emp, None, day)
        else:
            day_h, basis, detail = _hb.resolve_day(cell, cfg, emp, day)
        # THE WEIGHTS: each row's own pre-evidence contribution, so the split across stores keeps
        # the proportions it has always had and a single-row day is exact.
        weights = [max(0.0, _hb.resolve_shift_row(r, cfg, emp, None, day)[0]) for r in rows]
        total_w = sum(weights)
        if total_w <= 0:
            weights = [1.0] * len(rows)
            total_w = float(len(rows))
        store = _store_for_day(cell, rows)
        allocated = 0.0
        for i, r in enumerate(rows):
            share = (round(day_h - allocated, 2) if i == len(rows) - 1
                     else round(day_h * weights[i] / total_w, 2))
            allocated = round(allocated + share, 2)
            out_by_pos[idxs[i]] = {"shift": r, "employee_id": eid, "date": day, "hours": share,
                                   "basis": basis, "store_code": store, "detail": detail,
                                   "day_hours": day_h}
    # ★ ONE ROW PER INPUT ROW, SAME LENGTH, SAME ORDER ★ — the contract a caller zips against. Every
    # input row reaches `by_day` (a row with no employee or no date keys on `(None, "")` and is still
    # grouped), so nothing is dropped; the guard below makes that a GUARANTEE rather than a
    # reasoned-about property, because a caller that zips a short list silently misattributes money.
    if len(out_by_pos) != len(rows_all):
        # NOT an `assert`: `python -O` strips those, and a silently SHORT list is a caller zipping
        # one employee's hours onto another employee's rate. It holds by construction (every row
        # reaches `by_day`), so this never fires — and if the construction ever changes, the pay
        # path REFUSES rather than publishing a misattributed figure.
        raise EvidenceRefused(
            f"The paid-hours allocation accounted for {len(out_by_pos)} of {len(rows_all)} shift "
            f"rows. Every row must be accounted for before any of them can be priced, so no figure "
            f"is produced for this period.")
    return [out_by_pos[i] for i in range(len(rows_all))]


def refusal(res):
    """The one sentence a caller surfaces when the evidence may not be paid from, else None.

    Having this as a function rather than a key is the point: a caller that reads `res["refused"]`
    and ignores it reads as a caller that never asked, and the lock cannot tell the difference. A
    call to `refusal(...)` is visible in the source, so §H can require it.
    """
    res = res if isinstance(res, dict) else {}
    return res.get("reason") if res.get("refused") else None
