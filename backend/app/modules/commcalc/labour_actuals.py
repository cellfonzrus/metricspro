"""THE ONE HOME for "what did this month's platform-derived labour actually cost, per store".

OWNER DIRECTIVE 2026-10-20, verbatim: *"sepatember and october expeses wll be carried forward but
salary and commisson shoudl be derived from the actuals"*.

WHY THIS MODULE EXISTS, and why it derives NO dollars of its own
────────────────────────────────────────────────────────────────
`expenses_effective.carryable_rows` stops a carried month inheriting the two rows the platform fills
in itself (`Employee Salaries`, `Employee Commission`, or whatever a tenant named instead). That
alone would leave a hole: a surface that showed one total for the month would simply show less money,
and a smaller wrong number is still a wrong number. This module fills the hole from the SAME sources
the platform already derives those figures from, and it is the only place that assembles them:

  · SALARY     → `account/coa.wages_by_store` (hourly paid hours × pay_rate, salaried monthly
                 equivalent allocated across the stores worked — index §14y decides what "paid
                 hours" means, and this module does not second-guess it).
  · COMMISSION → `commcalc.rep_commissions.total_payout` for the period, the figure the P&L books on
                 its `rep_comm` line and the GP report books as `rep_pay`.

So every surface that needs "this month's labour" gets the number the P&L already books, rather than
a fourth derivation of the same dollars. Nothing here multiplies an hour by a rate or reads a feed.

ONE STORE VOCABULARY. `wages_by_store` keys by store ADDRESS, `rep_commissions.store` is an address
or a label, and the expense sheet keys by store CODE. All three are resolved through
`account/store_identity.store_key` — THE store-identity home since 2026-10-09 (§64) — injected by the
caller exactly as `expenses_effective.totals_by_store` takes it. The pre-existing
`GET /commcalc/commission-by-store` still bridges those vocabularies by LEADING STREET NUMBER, which
is the join §64 replaced because it dropped money whose token no store row led with; that endpoint is
an auto-fill for a sheet a human then edits, so it is left alone here and named in the index as the
remaining caller of the old bridge rather than quietly rewritten under a different owner's feature.

AN ABSENCE IS REPORTED WITH ITS REASON (§19.49). A month whose roster or shifts cannot be read does
not come back as $0.00 of salary: `measured` says which halves were measured, and a caller that gets
`measured['salary'] is False` must SAY the salary is unknown rather than print a zero. A zero labour
cost makes every store look profitable, which is the dangerous direction.

ONLY WHAT THE VOCABULARY CLAIMS. The halves returned are exactly the halves the org's resolved
vocabulary names, so a tenant with `mode='off'` (nothing excluded from the carry) gets nothing added
back, and the two decisions cannot disagree.

Pure core (`fold`) is DB-free; proof + lock: `backend/harness_labour_actuals.py`.
Registered in `docs/SYSTEM_DATA_FLOW_INDEX.md` §COMMCALC-22.
"""
from app.modules.commcalc import labour_vocabulary as _lv

SALARY = "salary"
COMMISSION = "commission"


def wanted_halves(resolved):
    """PURE: which halves this org's vocabulary claims the platform derives.

    A half with no row names is not derived for this tenant, so nothing is excluded from its carried
    sheet and nothing may be added back for it — the carry rule and this module read one vocabulary
    so they cannot take different views of the same month."""
    r = resolved if isinstance(resolved, dict) else {}
    out = []
    if _lv._clean(r.get("payroll_names")):
        out.append(SALARY)
    if _lv._clean(r.get("commission_names")):
        out.append(COMMISSION)
    return out


def fold(wage_cells=None, commission_rows=None, store_key_of=None, *, halves=None):
    """PURE: the derived labour of one month → ({half: {store key: $}}, {half: $}).

    Kept PER HALF on purpose. A reader that must not double-count commission (the GP report already
    subtracts `rep_pay`) needs the SALARY of each store, and a blended figure split by a ratio would
    be an invented per-store number — the kind of plausible arithmetic this house treats as a defect.

    `wage_cells` is `coa.derive_wage_cells`'s output ({store address or None: $}); its None key is
    labour with no attributable store and is kept under `''`, visible on a row of its own rather than
    dropped — the same rule `expenses_effective.totals_by_store` applies.
    `commission_rows` are `rep_commissions` rows ({'store', 'total_payout'}).
    `store_key_of` is `account.store_identity.store_key(resolve, …)` partially applied; None keeps the
    raw stripped spelling. `halves` limits which halves are folded (see `wanted_halves`).
    """
    use = [h for h in (SALARY, COMMISSION)
           if h in set(halves if halves is not None else (SALARY, COMMISSION))]
    key = store_key_of or (lambda v: str(v or "").strip())
    by_half_store = {h: {} for h in use}
    by_half = {h: 0.0 for h in use}

    def add(half, raw_store, amount):
        try:
            amt = float(str(amount or 0).replace(",", "").replace("$", "").strip() or 0)
        except (TypeError, ValueError):
            amt = 0.0
        k = key(raw_store) if raw_store else ""
        d = by_half_store[half]
        d[k] = round(d.get(k, 0.0) + amt, 2)
        by_half[half] = round(by_half[half] + amt, 2)

    # TOTAL over the shape as well as the values: a caller handed a string or a list where a dict
    # was meant must get an empty month, not a traceback that takes the whole report down.
    cells = wage_cells if isinstance(wage_cells, dict) else {}
    comms = commission_rows if isinstance(commission_rows, (list, tuple)) else []
    if SALARY in use:
        for store, amt in cells.items():
            add(SALARY, store, amt)
    if COMMISSION in use:
        for r in comms:
            d = r if isinstance(r, dict) else {}
            add(COMMISSION, d.get("store"), d.get("total_payout"))
    return by_half_store, by_half


def totals_by_store(by_half_store, halves=None):
    """PURE: {half: {store: $}} → {store: $} over the chosen halves, summed verbatim."""
    use = [h for h in (by_half_store or {}) if halves is None or h in set(halves)]
    out = {}
    for h in use:
        for k, v in (by_half_store.get(h) or {}).items():
            out[k] = round(out.get(k, 0.0) + float(v or 0.0), 2)
    return out


def by_store(client, org_id, period, resolved, store_key_of=None):
    """I/O: one month's derived labour per store, from the homes that derive it.

    Returns `{'by_store': {key: $}, 'by_half': {half: $}, 'measured': {half: bool},
               'halves': [...], 'period': period}`.

    Never raises: a half whose read fails comes back `measured[half] = False` with no dollars, so the
    caller reports it as unknown instead of as nothing. Org-scoped throughout."""
    halves = wanted_halves(resolved)
    measured = {h: False for h in halves}
    cells, comms = {}, []
    if SALARY in halves:
        try:
            from app.modules.account import coa as _coa
            cells = _coa.wages_by_store(client, org_id, period) or {}
            measured[SALARY] = True
        except Exception as e:                                     # pragma: no cover - I/O guard
            print(f"WARN labour_actuals salary unavailable: {type(e).__name__}: {e}")
    if COMMISSION in halves:
        # BOTH SPELLINGS OF A MONTH, through the one parser every money read here uses
        # (`account/_period.period_keys` — the same list the P&L hands the carry rule).
        try:
            from app.modules.account._period import period_keys as _pk
            variants = list(_pk(period)) or [period]
        except Exception:
            variants = [period]
        try:
            comms = (client.schema("commcalc").table("rep_commissions")
                     .select("store,total_payout").eq("org_id", org_id)
                     .in_("period", list(variants)).limit(100000).execute().data) or []
            measured[COMMISSION] = True
        except Exception as e:                                     # pragma: no cover - I/O guard
            print(f"WARN labour_actuals commission unavailable: {type(e).__name__}: {e}")
    got = [h for h in halves if measured.get(h)]
    by_half_store, by_half = fold(cells, comms, store_key_of, halves=got)
    return {"period": period, "by_half_store": by_half_store, "by_half": by_half,
            "by_store": totals_by_store(by_half_store), "measured": measured, "halves": halves}


def series(client, org_id, periods, resolved, store_key_of=None):
    """I/O: `by_store` for SEVERAL months — {period: the same dict `by_store` returns}.

    A LOOP over the one-month home, deliberately, not a second multi-month derivation: the same
    reason `expenses_effective.effective_series` dereferences `pick_carry_period` and `manual_rows`
    rather than re-deciding the carry (§19.50). It costs one roster read per month, which is a
    performance cost and never a correctness one; a cheaper reader belongs in the home that derives
    wages, where its result can be proved equal to this one, not here as a rival answer.

    A month whose read fails is still present, carrying `measured[half] = False` — a missing month is
    reported, never silently absent from the series."""
    return {p: by_store(client, org_id, p, resolved, store_key_of) for p in (periods or [])}


def as_expense_rows(actual, resolved, *, halves=None, store_col="store_code",
                    period=None, amount_col="amount", key_as=None):
    """PURE: a month's derived labour → rows shaped like `store_expenses` rows, so a reader that
    already sums an expense sheet picks the month's OWN labour up with no arithmetic change.

    `actual` is a `by_store` result. `halves` chooses which halves to materialise, and the CALLER
    must choose, because the right answer differs per surface and the difference is money:

      · a surface whose only labour figure is the expense sheet (the expenses trend, the Sales
        Comparison's expense line, the takeover evaluation) takes BOTH halves;
      · the GP report takes SALARY ONLY — it already subtracts `rep_pay` from `rep_commissions`
        inside `net_profit`, so a commission row here would subtract the same dollars twice;
      · the P&L takes NEITHER — it derives `wages` and `rep_comm` itself from these very sources, so
        it needs the carried rows gone and nothing put back.

    One row per store per half, each named with that half's OWN row name from the vocabulary (the
    tenant's spelling where it named one), so a reader grouping by `expense_name` shows the lines the
    Expenses sheet would have shown. `source_key` is stamped `labour_actual`: these rows are a
    measurement of THIS month, so the carry rule can never take them into another one. A half that
    was asked for but not measured contributes nothing — the caller reports it as unknown.
    """
    asked = set(halves if halves is not None else (SALARY, COMMISSION))
    act = actual if isinstance(actual, dict) else {}            # total over the shape too (see `fold`)
    use = [h for h in (act.get("halves") or ())
           if h in asked and (act.get("measured") or {}).get(h)]
    if not use:
        return []
    # THE ROW NAMES ARE NEVER SPELLED HERE. The resolved vocabulary carries the tenant's own names,
    # and `labour_vocabulary.DEFAULT_*_ROWS` carries the house ones — a literal in this file would be
    # the second copy `harness_labour_vocabulary.py` §G4 exists to fail the build over.
    names = {SALARY: (_lv._clean((resolved or {}).get("payroll_names"))
                      or list(_lv.DEFAULT_PAYROLL_ROWS))[0],
             COMMISSION: (_lv._clean((resolved or {}).get("commission_names"))
                          or list(_lv.DEFAULT_COMMISSION_ROWS))[0]}
    out = []
    for h in use:
        for store, amt in sorted((act.get("by_half_store") or {}).get(h, {}).items()):
            val = round(float(amt or 0.0), 2)
            if not val:
                continue
            label_store = store
            if key_as is not None and store:
                try:
                    label_store = str(key_as(store) or store)
                except Exception:
                    label_store = store
            row = {store_col: label_store, "expense_name": names[h], amount_col: val,
                   "source_key": "labour_actual"}
            if period is not None:
                row["period"] = period
            out.append(row)
    return out
