"""Effective store expenses for a period — the ONE carry-forward rule (owner directive 2026-09-02).

WHY THIS MODULE EXISTS (the systematic fix, not the band-aid): the Expenses sheet
(GET /commcalc/expenses/{period}) has been STICKY since its carry-forward patch — a month with no
saved rows displays the latest prior month's expenses pre-filled ("they persist month-to-month until
changed"). But every MONEY consumer of `commcalc.store_expenses` kept reading the raw table:

  • the GP report (`router._compute_gp` → `calc_gp_report(expenses=…)`) read
    `.in_('period', pv)` directly, so the "−Expenses" column read $0 for any month the tenant had
    not re-saved — while the Expenses sheet the owner was looking at showed the carried numbers
    (house org evidence 2026-09-02: `store_expenses` rows end at "July 2026"; the August GP report
    showed exp_total $0 on every store).
  • the P&L (`account/coa.build_inputs`) read the same raw table, so August `store_opex` read $0.00
    (July: $225,080.58) and the hand-entered salary rows that suppress the wages estimate vanished
    with it.

That display-only stickiness WAS the prior "fix" — it fixed the sheet, not the reports. This module
is the single shared implementation both readers (and any future reader) go through, so the sheet
and the money reports can never disagree again.

THE RULE (mirrors GET /expenses/{period} exactly, money-hardened):
  1. If the period has ANY store_expenses rows (either period spelling), use them as-is — no carry.
  2. Otherwise carry forward the latest STRICTLY-PRIOR period's MANUAL rows (source_key IS NULL).
     SYSTEM rows (non-null source_key — e.g. a payroll run's PTO accrual, `payroll_gross`) are
     period-specific products of their own month and are NEVER carried into another month (carrying
     July's payroll into August would double-book it the moment August's own run lands).
  3. Nothing prior → empty (a brand-new tenant books nothing, exactly as before).

Carried rows are returned with `carried_from` set (and unchanged `period`) so a consumer can label
them; amounts are returned verbatim — this module never invents or scales a dollar.

Pure selection logic is DB-free (proof: backend/harness_expenses_carry_forward.py).
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md (§4 GP report + P&L expense flow).
"""

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}


def period_sort_key(p):
    """PURE: '(year, month)' for either period spelling — 'August 2026' or '2026-08'.
    (0, 0) when unparseable, so junk periods sort before everything and never win a carry."""
    s = str(p or "").strip()
    if not s:
        return (0, 0)
    try:
        if "-" in s and s[:4].isdigit():
            y, m = int(s[:4]), int(s.split("-")[1])
            return (y, m) if 1 <= m <= 12 else (0, 0)
        parts = s.split()
        if len(parts) == 2 and parts[0].lower() in _MONTHS and parts[1].isdigit():
            return (int(parts[1]), _MONTHS[parts[0].lower()])
    except Exception:
        pass
    return (0, 0)


def pick_carry_period(all_periods, current):
    """PURE: the latest period STRICTLY BEFORE `current` from `all_periods`, or None.
    Same-month alternate spellings of `current` never qualify (strict <), so a month can never
    carry from itself; unparseable periods never qualify."""
    cur = period_sort_key(current)
    if cur == (0, 0):
        return None
    best, best_key = None, (0, 0)
    for p in set(all_periods or ()):
        k = period_sort_key(p)
        if k != (0, 0) and k < cur and k > best_key:
            best, best_key = p, k
    return best


def manual_rows(rows):
    """PURE: only the MANUAL rows (source_key NULL/blank) — the ones that persist month-to-month.
    Rows without the column at all (pre-mig-206 schema) are manual by definition."""
    return [r for r in (rows or []) if not (r.get("source_key") or None)]


def effective_expense_rows(client, org_id, period, pvariants, select_cols):
    """I/O: the period's effective store_expenses rows under the carry-forward rule above.

    Returns (rows, carried_from). `pvariants` = the caller's period-spelling list (both spellings);
    `select_cols` = the columns the caller needs (must include source_key when the table has it —
    a select that fails on source_key degrades to the raw column set, treating every row as manual,
    which is exactly the pre-mig-206 world where every row WAS manual).
    Org-scoped throughout; never raises — any read failure degrades to (whatever was read, None)."""
    sc = client.schema("commcalc")
    cols = select_cols if "source_key" in select_cols else (select_cols + ",source_key")

    def _read(q_period_list):
        try:
            return (sc.table("store_expenses").select(cols)
                    .eq("org_id", org_id).in_("period", list(q_period_list))
                    .limit(100000).execute().data) or []
        except Exception:
            try:  # pre-mig-206: no source_key column — every row is manual
                return (sc.table("store_expenses").select(select_cols)
                        .eq("org_id", org_id).in_("period", list(q_period_list))
                        .limit(100000).execute().data) or []
            except Exception:
                return []

    rows = _read(pvariants)
    if rows:
        return rows, None
    try:
        allp = (sc.table("store_expenses").select("period")
                .eq("org_id", org_id).limit(100000).execute().data) or []
    except Exception:
        return [], None
    carried_from = pick_carry_period([r.get("period") for r in allp], period)
    if not carried_from:
        return [], None
    prior = _read([carried_from])
    return manual_rows(prior), carried_from
