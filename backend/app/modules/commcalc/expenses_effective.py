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
  2. Otherwise carry forward the latest STRICTLY-PRIOR period's CARRYABLE rows — the MANUAL rows
     (source_key IS NULL) minus the rows the PLATFORM DERIVES for each month (owner directive
     2026-10-20; see `carryable_rows`). SYSTEM rows (non-null source_key — e.g. a payroll run's PTO
     accrual, `payroll_gross`) are period-specific products of their own month and are NEVER carried
     into another month (carrying July's payroll into August would double-book it the moment
     August's own run lands); `Employee Salaries` / `Employee Commission` and whatever a tenant named
     instead are the same thing wearing a manual row's clothes — the platform fills them in and
     re-derives them for every month, so they come from the MONTH'S OWN actuals, never from the sheet
     it inherited.
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


def carryable_rows(rows, derived_names=None):
    """PURE: of a prior month's rows, the ones a LATER month may inherit — THE carry selection.

    OWNER DIRECTIVE 2026-10-20, verbatim: *"sepatember and october expeses wll be carried forward but
    salary and commisson shoudl be derived from the actuals"*.

    Two kinds of row are a measurement of THEIR OWN month and may never speak for another:

      1. a SYSTEM row (`source_key` set) — a payroll run's accrual, already excluded since mig 206;
      2. a row the PLATFORM DERIVES — `Employee Salaries`, `Employee Commission`, and whatever a
         tenant named instead (`labour_vocabulary.derived_row_names`). The platform auto-fills these
         from StoreOps hours and from `rep_commissions`, and it derives the SAME figures again for
         every month on the `wages` and `rep_comm` P&L lines. Carrying one states August's payroll as
         September's: measured live 2026-10-20, 58% of the $276,162.69 that September and October were
         each showing for one tenant was August's salary and commission, against an actual September
         of $110,302.73 of wages and $12,703.71 of commission.

    `labour_coverage`'s docstring has named this defect since 2026-09-08 (point 3, MONTH GRAIN) and
    only ever REPORTED it on the coverage report. This is the fix, in the one place the decision lives.

    `derived_names` is INJECTED (resolved per org by `labour_vocabulary.resolve`, house default where
    the tenant named nothing) — RULE TWO: no row name is written into this module. None means the
    caller resolved no vocabulary and only rule 1 applies, which is the pre-2026-10-20 behaviour;
    `harness_expense_one_path.py` §H fails the build when a caller stops passing it, because a
    registry nothing dereferences is this house's most repeated defect (§19.18).

    A dropped row is never silently dropped: `carried_excluded` beside each result says which names
    and how many dollars did not come across, so the surface can SAY that this month's labour comes
    from payroll rather than from the sheet it inherited.
    """
    # The membership test is DEREFERENCED from the labour-row vocabulary (index §4e), never
    # re-spelled here. Imported INSIDE the function on purpose: this module imports nothing at
    # module scope so the carry rule stays loadable with no app weight, and
    # `harness_expense_one_path.py` §F4 fails the build if that changes.
    from app.modules.commcalc import labour_vocabulary as _lv
    names = derived_names or ()
    return [r for r in manual_rows(rows)
            if not _lv.is_derived_row(r.get("expense_name"), names)]


def excluded_summary(rows, derived_names=None, *, amount_col="amount"):
    """PURE: {row name: dollars} for the rows `carryable_rows` refused to carry — the absence, with
    its reason, so no consumer has to re-derive what it is missing (§19.49)."""
    from app.modules.commcalc import labour_vocabulary as _lv      # see `carryable_rows` on why here
    out = {}
    for r in manual_rows(rows):
        nm = " ".join(str(r.get("expense_name") or "").strip().split())
        if not _lv.is_derived_row(nm, derived_names or ()):
            continue
        try:
            amt = float(str(r.get(amount_col) or 0).replace(",", "").replace("$", "").strip() or 0)
        except (TypeError, ValueError):
            amt = 0.0
        out[nm] = round(out.get(nm, 0.0) + amt, 2)
    return out


def effective_series(rows, periods, derived_names=None):
    """PURE: THE carry-forward rule applied to a SERIES of months at once (index §19.50).

    `rows` = every `store_expenses` row the org has (each needs `period`, plus `source_key` for the
    carry test and whatever money columns the caller reads). `periods` = the month labels the caller
    wants, in any spelling. Returns {period_label: {'rows': [...], 'carried_from': str|None}}.

    WHY THIS EXISTS. `effective_expense_rows` above answers the question for ONE period over I/O;
    the trend endpoint asked the SAME question for twelve months and answered it by reading the raw
    table — no carry-forward, no store resolution. Two paths, one question, different answers: the
    duplicate defect the house rules forbid (owner report 2026-10-06). Live evidence: the P&L booked
    $379,108.81 of carried October store opex while the expenses trend had no October row at all,
    and both of LuxeLink's September and October months were likewise absent from the trend while
    the P&L booked $275,610.15 for each.

    Both spellings of a month collapse into one slot (via `period_sort_key`, the ONE parser here),
    and the carry decision dereferences `pick_carry_period` and `manual_rows` — the same two pure
    facts the I/O path uses — so the series and the single-period read cannot drift. That agreement
    is PROVED, not asserted: `harness_expense_one_path.py` §C runs both over one fixture and
    requires them to return the same rows and the same `carried_from` for every month.
    """
    all_periods = [r.get("period") for r in (rows or [])]
    by_month = {}
    for r in (rows or []):
        k = period_sort_key(r.get("period"))
        if k == (0, 0):
            continue
        by_month.setdefault(k, []).append(r)
    out = {}
    for p in (periods or []):
        k = period_sort_key(p)
        own = list(by_month.get(k) or [])
        if own:
            out[p] = {"rows": own, "carried_from": None, "carried_excluded": {}}
            continue
        carried_from = pick_carry_period(all_periods, p) if k != (0, 0) else None
        prior = by_month.get(period_sort_key(carried_from)) or [] if carried_from else []
        # THE carry selection (above): system rows and platform-DERIVED rows stay in their own month,
        # and what did not come across is named rather than left for the reader to notice.
        out[p] = {"rows": carryable_rows(prior, derived_names), "carried_from": carried_from,
                  "carried_excluded": excluded_summary(prior, derived_names)}
    return out


def totals_by_store(rows, store_key_of=None, *, store_col="store_code", amount_col="amount"):
    """PURE: effective `store_expenses` rows -> {store key: total $}. THE one home for
    "what did this store spend that month", given the rows the carry-forward rule above already chose.

    WHY IT IS HERE AND NOT AT EACH CALL SITE (owner directive 2026-09-20, the design-fix rule).
    `effective_series` / `effective_expense_rows` answer WHICH rows a month books; summing them per
    store was then re-spelled by every reader — the expenses trend, the GP report, the P&L — and a
    reader that spells the fold differently gets a different store. That already bit this platform
    twice: `coa._norm_store` only STRIPS, so `'b-1115'` and `'B-1115'` became two stores holding one
    store's money (2026-10-09), and the leading-street-number joins dropped money whose token no
    store row led with (§64). So the fold is injected, once, and it is the SAME one every other money
    report uses: `store_key_of` is `account.store_identity.store_key(resolve, …)` partially applied —
    THE store-identity home since 2026-10-09.

    `store_key_of` None keeps the raw stripped spelling (what a reader keyed on a store CODE already
    does, byte-identical). A row with no store at all is kept under `''` rather than dropped — the
    money is visible on a row of its own instead of vanishing, exactly as `store_key` does.

    Amounts are summed verbatim; this never invents, scales or signs a dollar."""
    key = store_key_of or (lambda v: str(v or "").strip())
    out = {}
    for r in (rows or []):
        k = key(r.get(store_col))
        try:
            amt = float(str(r.get(amount_col) or 0).replace(",", "").replace("$", "").strip() or 0)
        except (TypeError, ValueError):
            amt = 0.0
        out[k] = out.get(k, 0.0) + amt
    return out


def effective_expense_rows(client, org_id, period, pvariants, select_cols, derived_names=None):
    """I/O: the period's effective store_expenses rows under the carry-forward rule above.

    Returns (rows, carried_from). `pvariants` = the caller's period-spelling list (both spellings);
    `select_cols` = the columns the caller needs (must include source_key when the table has it —
    a select that fails on source_key degrades to the raw column set, treating every row as manual,
    which is exactly the pre-mig-206 world where every row WAS manual).
    Org-scoped throughout; never raises — any read failure degrades to (whatever was read, None)."""
    sc = client.schema("commcalc")
    # ANY SUBSET OF COLUMNS (index §4b.1): `source_key` (mig 206) is PROBED on its own before the one
    # real select — pre-206 every row is manual, exactly as before — instead of a block whose failure
    # on that column re-read the caller's columns and could not tell a schema gap from a read error.
    from app.core import column_tolerant as _ct
    _present = _ct.present_columns(lambda: sc.table("store_expenses"),
                                   lambda q: q.eq("org_id", org_id), ("source_key",))
    cols = select_cols if ("source_key" in select_cols or "source_key" not in _present) \
        else (select_cols + ",source_key")

    def _read(q_period_list):
        try:
            return (sc.table("store_expenses").select(cols)
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
    # THE carry selection, the same one `effective_series` applies (owner directive 2026-10-20): a
    # month inherits what the tenant typed, never a figure the platform derives for its own month.
    # `derived_names` None keeps rule 1 only — what every caller did before the vocabulary was wired.
    return carryable_rows(prior, derived_names), carried_from
