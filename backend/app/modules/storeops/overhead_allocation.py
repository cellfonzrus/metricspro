"""storeops OVERHEAD ALLOCATION — salaried staff who belong to no store (proof:
backend/harness_overhead_allocation.py).

OWNER DIRECTIVE 2026-09-09 (verbatim): "payroll needs to be attributed to each store rather than teh
company as that will give the real picture of the store perfromace, the company will only calculate
as a result of all store combined together.  All employees who are salaried and not attached to
stores like the DM and market manager shoudl have thier won seaprat line and thier salary will be
divided amongs he store they handle , if th market manager is 78000 and he handles all markets then
his salary will be 78000/ the number of stores in each market, simialry thier commission will also
be a seaprate line item so at the end of the year we know who got paid how much with clear
distinction , all these will be a seaprate box in the p&l under wages"

WHAT THIS MODULE IS AND IS NOT
------------------------------
It is NOT a second wage derivation and it computes NO new dollar of pay. The platform already has:

  • `account/coa.derive_wage_cells`  — THE per-store wage estimate (hourly hours x rate; salaried
    monthly-equivalent allocated across the stores the person actually worked). Untouched.
  • `account/coa.monthly_salary_equivalent` — THE weekly/monthly/annual -> monthly conversion. This
    module never re-implements it; the caller injects it (`monthly_equivalent=`) so there is exactly
    one conversion table in the codebase.
  • `storeops/router.get_payroll_by_store` — THE per-store salary figure. Untouched.
  • `storeops/salary_expense.build_store_folder` / `fold_store_rows` — THE store-code fold. Reused
    by the IO layer below rather than re-derived.
  • `storeops.org_span_for_manager` (mig 050, index §13) — THE canonical "which stores does this
    manager cover" answer. Consulted FIRST and used AS-IS.

What it ADDS is the one thing none of those can express: a salaried person who is attached to NO
store at all. `derive_wage_cells` gives such a person the key `None` — "company-wide" — and under
`payroll_authority_grain='store'` (live for the reporting tenant) `coa.build_inputs` deliberately
does NOT book a company-wide cell, because it cannot prove the dollars are absent from the entered
per-store figures. Correct, and it means their pay lands NOWHERE. The owner's answer is not to book
them company-wide, it is to SPREAD them over the stores they cover and give them their own line.

THE ALLOCATION BASIS — the ambiguity in the directive, stated rather than silently resolved
--------------------------------------------------------------------------------------------
The directive gives two readings that are NOT the same arithmetic:

  (A) "their salary will be divided among the stores they handle"
  (B) "if the market manager is 78000 and he handles all markets then his salary will be
      78000 / the number of stores in EACH MARKET"

(A) is one division over the whole covered set. (B) reads as a per-market division, which for a
person covering several markets of UNEQUAL size gives each market's stores a different amount.
They coincide only when every covered market has the same store count.

  BASIS_EQUAL_STORES (the DEFAULT, reading A) — monthly salary / |covered stores|. Every store the
      person covers carries the same share of them. This is the defensible reading: it is what "the
      stores they handle" says without adding a grouping the sentence never asks for, it is stable
      when a market gains or loses a store, and it is what the reporting tenant is already doing by
      hand (twenty equal per-store rows).
  BASIS_MARKET_THEN_STORE (reading B) — the salary splits equally across the covered MARKETS first,
      then equally across the stores inside each market. A person covering a 13-store market and a
      7-store market gives the 7-store market's stores nearly twice the per-store load.
  BASIS_WEIGHTED (neither reading; offered because it is the one accountants ask for) — pro-rata on
      a caller-supplied weight per store (revenue, gross profit, labour hours). A big store carries
      more of the overhead that serves it.

The basis is CONFIG (`overhead_config.basis`), so switching readings is a row, never a deploy.
`allocate()` returns the basis it ACTUALLY used, and degrades LOUDLY: a weighted basis with no
usable weights falls back to an equal split and says so in `basis_used`, so a report can never
present an equal split as though it were revenue-weighted.

COMPANY IS THE SUM OF STORES, NEVER ITS OWN ALLOCATION
------------------------------------------------------
The owner is explicit: "the company will only calculate as a result of all store combined together."
Every function here emits PER-STORE cells only. There is no company-wide bucket to book into, and
`company_total()` exists solely to prove the identity company == sum(stores) rather than to compute
a second figure. A dollar this module cannot attribute to a store is REPORTED in `unallocated` — it
is never quietly moved to a company line, and it is never dropped.

CENTS. Each person's per-store shares are cents-rounded with the LAST store (deterministic sort
order) absorbing the residue, so every individual's stores sum EXACTLY to their monthly figure and
the org total is exact by construction. Same convention as `derive_wage_cells`.

RULE TWO. No tenant, store, role, employee or carrier name appears in this file. Which roles count
as overhead beyond the structural test, the line labels, the basis, the coverage fallback and
whether commission is derived at all are per-org config rows over the house defaults below. The
house default is `mode='off'`: with no config row this module books nothing and every org's
statement is byte-identical.
"""
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ── allocation bases ─────────────────────────────────────────────────────────────────────────────
BASIS_EQUAL_STORES = "equal_stores"
BASIS_MARKET_THEN_STORE = "equal_market_then_store"
BASIS_WEIGHTED = "weighted"
BASES = (BASIS_EQUAL_STORES, BASIS_MARKET_THEN_STORE, BASIS_WEIGHTED)

# ── how an overhead person's covered store set was resolved (most authoritative first) ───────────
COVER_ORG_SPAN = "org_span"      # storeops.org_span_for_manager — the canonical manager->stores RPC
COVER_ORG_UNIT = "org_unit"      # the subtree of the unit the employee is PLACED in
COVER_ORG_WIDE = "org_wide"      # no unit at all: every active store in the org
COVER_NONE = "none"              # nothing resolved — REPORTED, never guessed

# ── why an employee is (or is not) overhead ──────────────────────────────────────────────────────
WHY_UNATTACHED = "salaried_unattached"   # structural: salaried, active, no home store
WHY_ROLE = "configured_role"             # the org listed this role as overhead
WHY_INACTIVE = "inactive"
WHY_NOT_SALARIED = "not_salaried"
WHY_STORE_ATTACHED = "store_attached"

# ── config (RULE TWO — house defaults; per-org JSONB `account_config.overhead_config`, mig 997) ───
DEFAULT_CONFIG = {
    # 'off' = HOUSE DEFAULT. Nothing is derived, nothing is booked, every org byte-identical.
    # 'derive' = build the two overhead lines from the roster.
    "mode": "off",
    "basis": BASIS_EQUAL_STORES,
    # How far to look for "the stores they handle" when the canonical manager RPC returns nothing.
    # 'org_span' = the RPC only (strictest); 'org_unit' = also the subtree of the unit they sit in;
    # 'org_wide' = also every active store when they sit at no unit at all.
    "span_fallback": COVER_ORG_UNIT,
    # EXTRA roles to treat as overhead even when they DO carry a home store (a DM pinned to a store
    # for scheduling). Empty default = the structural test alone, so no role string lives in code.
    "roles": [],
    # Include inactive stores in the covered set? A closed store should not carry next month's DM.
    "include_inactive_stores": False,
    "wages_label": "Overhead salaries (allocated to stores)",
    "commission_label": "Overhead commission (allocated to stores)",
    # 'off' = HOUSE DEFAULT: no commission line is derived. 'management_incentive' = read the
    # ALREADY-COMPUTED payout from commcalc.management_incentive_payout. This module never computes
    # a commission amount; it only places one that another engine already decided.
    "commission_source": "off",
    # management_incentive_payout.status values that count as owed. Draft is deliberately excluded.
    "commission_statuses": ["approved", "paid"],
    # store_expenses.expense_name values that carry the SAME overhead salary the org already enters
    # by hand. Empty default = nothing is claimed. Used ONLY to REPORT the overlap (see
    # `reconcile_manual`); this module never deletes an expense row.
    "manual_expense_names": [],
}
_CONFIG_FIELDS = tuple(DEFAULT_CONFIG.keys())


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v) -> str:
    return str(v or "").strip()


def resolve_config(org_row: Optional[dict] = None) -> dict:
    """Merge a (possibly partial / absent / malformed) per-org config over the house defaults.

    Same posture as `salary_expense.resolve_config` and `payroll_expenses.resolve_tax_config`: an org
    with no row behaves EXACTLY as the house default, and an unknown value for an enumerated field
    falls back to the default rather than inventing a fourth basis. NEVER raises."""
    eff = dict(DEFAULT_CONFIG)
    row = org_row if isinstance(org_row, dict) else {}
    for f in _CONFIG_FIELDS:
        v = row.get(f)
        if v is not None:
            eff[f] = v
    mode = _s(eff["mode"]).lower()
    eff["mode"] = mode if mode in ("off", "derive") else "off"
    basis = _s(eff["basis"]).lower()
    eff["basis"] = basis if basis in BASES else BASIS_EQUAL_STORES
    fb = _s(eff["span_fallback"]).lower()
    eff["span_fallback"] = fb if fb in (COVER_ORG_SPAN, COVER_ORG_UNIT, COVER_ORG_WIDE) else COVER_ORG_UNIT
    src = _s(eff["commission_source"]).lower()
    eff["commission_source"] = src if src in ("off", "management_incentive") else "off"
    eff["roles"] = [r for r in (_s(x) for x in (eff["roles"] or [])) if r]
    eff["commission_statuses"] = [r for r in (_s(x).lower() for x in (eff["commission_statuses"] or [])) if r]
    eff["manual_expense_names"] = [r for r in (_s(x) for x in (eff["manual_expense_names"] or [])) if r]
    eff["include_inactive_stores"] = bool(eff["include_inactive_stores"])
    eff["wages_label"] = _s(eff["wages_label"]) or DEFAULT_CONFIG["wages_label"]
    eff["commission_label"] = _s(eff["commission_label"]) or DEFAULT_CONFIG["commission_label"]
    return eff


# ── 1. who is overhead ───────────────────────────────────────────────────────────────────────────
def classify_employee(emp: dict, cfg: dict, monthly_equivalent: Callable[[object, object], Optional[float]]
                      ) -> Tuple[bool, str, float]:
    """(is_overhead, why, monthly_salary) for ONE roster row.

    `monthly_equivalent(pay_basis, pay_amount)` is INJECTED — in production it is
    `account.coa.monthly_salary_equivalent`, the platform's ONE weekly/monthly/annual conversion.
    Passing it in keeps this module pure and keeps the conversion table single-sourced.

    THE STRUCTURAL TEST, which needs no role string and therefore no RULE TWO violation:
      active  AND  salaried (a usable monthly equivalent)  AND  no home_store
    That is exactly the person `derive_wage_cells` can only key as company-wide, i.e. exactly the
    person whose pay currently lands nowhere. An org may WIDEN it with `roles` (a DM who is pinned
    to a store for scheduling but is paid to cover a market); it can never be narrowed, because a
    salaried person attached to no store has no other home in the books.

    An INACTIVE employee is never overhead: they are no longer earning, and booking a leaver's
    salary across stores would overstate every store's cost. Mirrors `derive_wage_cells`'s own
    inactive-and-no-hours rule."""
    if emp.get("is_active") is False:
        return False, WHY_INACTIVE, 0.0
    meq = None
    try:
        meq = monthly_equivalent(emp.get("pay_basis"), emp.get("pay_amount"))
    except Exception:
        meq = None
    if meq is None or _f(meq) <= 0:
        return False, WHY_NOT_SALARIED, 0.0
    role = _s(emp.get("role")).lower()
    if role and role in {r.lower() for r in (cfg.get("roles") or [])}:
        return True, WHY_ROLE, round(_f(meq), 2)
    if not _s(emp.get("home_store")):
        return True, WHY_UNATTACHED, round(_f(meq), 2)
    return False, WHY_STORE_ATTACHED, round(_f(meq), 2)


# ── 2. which stores they cover ───────────────────────────────────────────────────────────────────
def covered_stores(span_codes: Optional[Iterable[str]],
                   unit_codes: Optional[Iterable[str]],
                   org_codes: Optional[Iterable[str]],
                   cfg: dict) -> Tuple[List[str], str]:
    """(sorted covered store codes, how) for ONE overhead person.

    A LADDER, most authoritative first, and it stops at the first rung that answers:

      1. `span_codes`  — the result of `storeops.org_span_for_manager` (index §13, mig 050): the
         org's own statement of which stores this person MANAGES. Used as-is; never second-guessed.
      2. `unit_codes`  — the stores under the org unit the employee is PLACED in
         (`employees.org_unit_id` -> that unit's subtree). This rung exists because the RPC keys on
         `storeops.org_managers`, and an org that has placed its people in the tree but never
         written a manager row gets an EMPTY span from rung 1 — which is a wiring gap, not a person
         who covers nothing. Gated by `span_fallback`.
      3. `org_codes`   — every (active) store, for a person sitting at no unit at all. Gated by
         `span_fallback='org_wide'` ONLY, because "covers everything" is a strong claim to make on
         a person's behalf.

    Returns ([], COVER_NONE) when nothing resolves. AN UNRESOLVED PERSON IS REPORTED, NEVER SPREAD:
    guessing a coverage set moves real salary onto stores that never had that manager, and a store
    wearing another market's overhead is worse than a gap somebody can see and fix."""
    fb = cfg.get("span_fallback", COVER_ORG_UNIT)
    span = sorted({_s(c) for c in (span_codes or []) if _s(c)})
    if span:
        return span, COVER_ORG_SPAN
    if fb in (COVER_ORG_UNIT, COVER_ORG_WIDE):
        unit = sorted({_s(c) for c in (unit_codes or []) if _s(c)})
        if unit:
            return unit, COVER_ORG_UNIT
    if fb == COVER_ORG_WIDE:
        every = sorted({_s(c) for c in (org_codes or []) if _s(c)})
        if every:
            return every, COVER_ORG_WIDE
    return [], COVER_NONE


# ── 3. the allocation itself ─────────────────────────────────────────────────────────────────────
def _spread(amount: float, codes: Sequence[str], weights: Optional[Dict[str, float]] = None
            ) -> Dict[str, float]:
    """Cents-exact spread of `amount` over `codes`. `weights` None/degenerate ⇒ equal shares.

    Deterministic: codes are taken in the order given (callers pass them sorted) and the LAST one
    absorbs the rounding residue, so sum(result.values()) == round(amount, 2) EXACTLY. Same
    convention as `coa.derive_wage_cells`, so the two allocations round alike."""
    n = len(codes)
    if n <= 0:
        return {}
    amt = round(_f(amount), 2)
    if not amt:
        return {}
    tot_w = sum(max(0.0, _f((weights or {}).get(c))) for c in codes) if weights else 0.0
    out: Dict[str, float] = {}
    allocated = 0.0
    for i, c in enumerate(codes):
        if i == n - 1:
            share = round(amt - allocated, 2)
        elif weights and tot_w > 0:
            share = round(amt * max(0.0, _f(weights.get(c))) / tot_w, 2)
        else:
            share = round(amt / n, 2)
        allocated = round(allocated + share, 2)
        out[c] = round(out.get(c, 0.0) + share, 2)
    return out


def allocate(amount: float, codes: Sequence[str], cfg: dict,
             market_of: Optional[Callable[[str], str]] = None,
             weight_of: Optional[Dict[str, float]] = None) -> Tuple[Dict[str, float], str, str]:
    """({store_code: amount}, basis_used, note) — the basis the org configured, or an honest
    degradation to the equal split with `basis_used` naming what actually happened.

    NEVER silently substitutes a basis. A weighted allocation whose weights are all zero/absent is
    NOT a revenue allocation, and a report that showed it as one would be a lie about how the money
    was spread; `basis_used` comes back as the equal split and `note` says why."""
    codes = [c for c in (codes or []) if _s(c)]
    if not codes:
        return {}, cfg.get("basis", BASIS_EQUAL_STORES), "no covered stores"
    basis = cfg.get("basis", BASIS_EQUAL_STORES)
    amt = round(_f(amount), 2)
    if basis == BASIS_MARKET_THEN_STORE and market_of is not None:
        by_market: Dict[str, List[str]] = {}
        for c in codes:
            by_market.setdefault(_s(market_of(c)) or "", []).append(c)
        markets = sorted(by_market)
        if len(markets) > 1:
            per_market = _spread(amt, markets)
            out: Dict[str, float] = {}
            for m in markets:
                for c, v in _spread(per_market.get(m, 0.0), sorted(by_market[m])).items():
                    out[c] = round(out.get(c, 0.0) + v, 2)
            return out, BASIS_MARKET_THEN_STORE, (
                "split equally across %d market(s) first, then equally within each market"
                % len(markets))
        # ONE market (or no market vocabulary): market-then-store and equal-stores are the SAME
        # arithmetic. Reported as the equal split, because that is what it is.
        return _spread(amt, sorted(codes)), BASIS_EQUAL_STORES, (
            "one market covered, so the per-market split is the equal per-store split")
    if basis == BASIS_WEIGHTED:
        tot = sum(max(0.0, _f((weight_of or {}).get(c))) for c in codes)
        if tot > 0:
            return _spread(amt, sorted(codes), weight_of), BASIS_WEIGHTED, "weighted per store"
        return _spread(amt, sorted(codes)), BASIS_EQUAL_STORES, (
            "a weighted basis was configured but no covered store has a usable weight for this "
            "period, so this is an EQUAL split — not a weighted one")
    return _spread(amt, sorted(codes)), BASIS_EQUAL_STORES, "equal across the covered stores"


# ── 4. the two lines ─────────────────────────────────────────────────────────────────────────────
def build_overhead(people: Iterable[dict], cfg: dict,
                   market_of: Optional[Callable[[str], str]] = None,
                   weight_of: Optional[Dict[str, float]] = None) -> dict:
    """The engine's answer for one period.

    `people`: one entry per OVERHEAD employee —
        {'employee_id', 'label', 'role', 'salary_month', 'commission', 'covered': [codes],
         'coverage': COVER_*, 'why': WHY_*}
    `market_of(code) -> market` is only consulted for BASIS_MARKET_THEN_STORE.
    `weight_of` is only consulted for BASIS_WEIGHTED.

    Returns {wages_by_store, commission_by_store, people, unallocated, totals, config}:
      wages_by_store       — what to add to the P&L `overhead_wages` line, per STORE. No company key.
      commission_by_store  — the same for `overhead_comm`.
      people               — the per-person audit row the owner asked for ("at the end of the year we
                             know who got paid how much with clear distinction"): every person, their
                             monthly salary and commission, how many stores they cover, how that
                             coverage was resolved, the basis actually used, and their per-store split.
      unallocated          — every person whose coverage resolved to NOTHING, with their dollars.
                             REPORTED, never spread and never folded into a company line.
      totals               — allocated/unallocated sums + the identity check inputs.
    """
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    wages: Dict[str, float] = {}
    comm: Dict[str, float] = {}
    rows: List[dict] = []
    unallocated: List[dict] = []
    for p in (people or []):
        codes = [c for c in (p.get("covered") or []) if _s(c)]
        sal = round(_f(p.get("salary_month")), 2)
        cm = round(_f(p.get("commission")), 2)
        if not codes:
            unallocated.append({
                "employee_id": p.get("employee_id"), "label": p.get("label"), "role": p.get("role"),
                "salary_month": sal, "commission": cm, "coverage": COVER_NONE,
                "reason": ("no covered stores could be resolved for this person — they manage no unit "
                           "in the org tree, sit at no unit, and have no home store. Their pay is "
                           "REPORTED here and booked NOWHERE: spreading it over a guessed store set "
                           "would move real salary onto stores this person never covered."),
            })
            continue
        w_cells, basis_used, note = allocate(sal, codes, cfg, market_of, weight_of)
        c_cells, c_basis, _c_note = allocate(cm, codes, cfg, market_of, weight_of)
        for k, v in w_cells.items():
            wages[k] = round(wages.get(k, 0.0) + v, 2)
        for k, v in c_cells.items():
            comm[k] = round(comm.get(k, 0.0) + v, 2)
        rows.append({
            "employee_id": p.get("employee_id"), "label": p.get("label"), "role": p.get("role"),
            "why": p.get("why"), "coverage": p.get("coverage") or COVER_NONE,
            "store_count": len(codes), "covered": sorted(codes),
            "salary_month": sal, "commission": cm,
            "basis_used": basis_used if sal else c_basis, "basis_note": note,
            "salary_by_store": w_cells, "commission_by_store": c_cells,
            "per_store_salary": round(sal / len(codes), 2) if sal else 0.0,
        })
    rows.sort(key=lambda r: (str(r.get("employee_id") or ""),))
    alloc_w = round(sum(wages.values()), 2)
    alloc_c = round(sum(comm.values()), 2)
    return {
        "wages_by_store": wages,
        "commission_by_store": comm,
        "people": rows,
        "unallocated": unallocated,
        "config": cfg,
        "totals": {
            "salary_allocated": alloc_w,
            "commission_allocated": alloc_c,
            "salary_unallocated": round(sum(_f(u["salary_month"]) for u in unallocated), 2),
            "commission_unallocated": round(sum(_f(u["commission"]) for u in unallocated), 2),
            "people": len(rows),
            "people_unallocated": len(unallocated),
            "stores_touched": len(set(wages) | set(comm)),
        },
    }


# ── 5. company == sum of stores (the owner's second sentence, as an assertion) ────────────────────
def company_total(by_store: Dict[str, float]) -> float:
    """The company figure. It is DEFINED as the sum of the stores and is computed no other way —
    "the company will only calculate as a result of all store combined together". This function
    exists so that identity can be asserted in a harness, not so a second figure can be derived."""
    return round(sum(_f(v) for v in (by_store or {}).values()), 2)


def rollup_by_market(by_store: Dict[str, float], market_of: Callable[[str], str]) -> Dict[str, float]:
    """Market subtotals, also a pure SUM of stores. A store whose market is unknown folds under ''
    and stays visible in the total — never dropped, so market subtotals always re-sum to company."""
    out: Dict[str, float] = {}
    for code, amt in (by_store or {}).items():
        m = _s(market_of(code))
        out[m] = round(out.get(m, 0.0) + _f(amt), 2)
    return out


# ── 6. what the org ALREADY enters by hand — reported, never deleted ─────────────────────────────
def reconcile_manual(derived_by_store: Dict[str, float], manual_rows: Iterable[dict], cfg: dict,
                     key_of: Optional[Callable[[object], Optional[str]]] = None) -> dict:
    """Compare the DERIVED overhead allocation against the overhead salary the org already types
    into `commcalc.store_expenses` under the names it listed in `manual_expense_names`.

    This function SUPPRESSES NOTHING and DELETES NOTHING. It answers one question — "does the
    roster explain the overhead cost this tenant is already booking?" — and hands back the gap.

    WHY IT IS NOT A SUPPRESSION PLAN (unlike `labour_coverage.suppression_plan`, which it otherwise
    resembles): that plan may suppress because `rep_comm` demonstrably books the SAME dollars from a
    more authoritative source. Here the derived figure comes from the ROSTER, and a roster that is
    missing an overhead employee derives LESS than the truth. Switching the manual rows off on the
    strength of a smaller derived number would delete real cost and book nothing back — precisely
    the failure the house forbids. So: report the gap, name it, and let a human close it by
    completing the roster.

    Returns {manual_total, derived_total, gap, by_store, names, note}. `gap` > 0 means the tenant
    books MORE overhead by hand than the roster explains — i.e. the roster is incomplete."""
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    names = {n.lower() for n in (cfg.get("manual_expense_names") or [])}
    manual: Dict[str, float] = {}
    if names:
        for r in (manual_rows or []):
            if _s(r.get("expense_name")).lower() not in names:
                continue
            raw = r.get("store_code")
            k = (key_of(raw) if key_of else _s(raw)) or ""
            manual[k] = round(manual.get(k, 0.0) + _f(r.get("amount")), 2)
    derived = {k: round(_f(v), 2) for k, v in (derived_by_store or {}).items()}
    by_store = []
    for k in sorted(set(manual) | set(derived)):
        m, d = manual.get(k, 0.0), derived.get(k, 0.0)
        by_store.append({"store": k, "manual": m, "derived": d, "gap": round(m - d, 2)})
    mt, dt = round(sum(manual.values()), 2), round(sum(derived.values()), 2)
    gap = round(mt - dt, 2)
    if not names:
        note = ""
    elif gap > 0:
        note = ("$%s of overhead salary is entered by hand under %s while the roster explains only "
                "$%s of it — a gap of $%s. Both are shown; NEITHER is suppressed, because the "
                "hand-entered rows are the larger and more complete figure. Close the gap by giving "
                "the missing overhead staff a roster row with their pay basis and amount."
                % (f"{mt:,.2f}", ", ".join(sorted(cfg.get('manual_expense_names') or [])),
                   f"{dt:,.2f}", f"{gap:,.2f}"))
    elif gap < 0:
        note = ("the roster derives $%s of overhead salary against $%s entered by hand — $%s MORE "
                "than the manual rows. Both are shown and neither is suppressed; check for an "
                "overhead employee counted twice before switching either off."
                % (f"{dt:,.2f}", f"{mt:,.2f}", f"{abs(gap):,.2f}"))
    else:
        note = ("the roster's overhead allocation and the hand-entered overhead rows agree at "
                "$%s. Only one of them should book; the tenant chooses which." % f"{mt:,.2f}")
    return {"manual_total": mt, "derived_total": dt, "gap": gap, "by_store": by_store,
            "names": sorted(cfg.get("manual_expense_names") or []), "note": note}


def line_note(result: dict, recon: Optional[dict] = None) -> str:
    """The one-sentence honesty note the P&L wage box carries. Same `note` passthrough the wages and
    device-cost lines already speak through (`engine._assemble`) — one mechanism, not a third."""
    t = (result or {}).get("totals") or {}
    people = (result or {}).get("people") or []
    bits: List[str] = []
    if t.get("people"):
        bases = sorted({str(p.get("basis_used")) for p in people if p.get("basis_used")})
        bits.append("%d overhead employee(s) attached to no store are allocated across %d store(s) "
                    "on a %s basis." % (t["people"], t.get("stores_touched", 0),
                                        " / ".join(bases) or BASIS_EQUAL_STORES))
    if t.get("people_unallocated"):
        bits.append("$%s of overhead pay for %d person(s) could NOT be attributed to any store and is "
                    "NOT booked — no org-tree coverage and no home store. Place them in the org tree "
                    "to book it." % (f"{_f(t.get('salary_unallocated')) + _f(t.get('commission_unallocated')):,.2f}",
                                     t["people_unallocated"]))
    if recon and recon.get("note"):
        bits.append(recon["note"])
    return " ".join(bits)


# ── 7. IO layer — org-scoped, thin, and the ONLY part that touches a client ──────────────────────
# NOTE ON ORG SCOPING: the CI org-scope guard scans ONLY commcalc/router.py, so nothing here is
# covered by it. Every read below therefore carries an explicit `.eq("org_id", org_id)` by hand, and
# the org_id is the caller's — never a value read out of a row.
def gather(client, org_id: str, period: str, cfg: dict, code_to_key=None) -> dict:
    """Build the overhead allocation for one period. Returns `build_overhead`'s result plus
    `reconciliation`, or a `{'skipped': reason}` shell when the org has not enabled it.

    `code_to_key(store_code)` maps a canonical store_code onto the caller's own key space (the P&L
    keys by canonical store ADDRESS), so what this returns is directly addable by the caller. It is
    applied LAST, after every allocation, so the cents-exact per-person split is computed in code
    space and can never be disturbed by an address collision.

    Every derivation it needs already exists and is REUSED, never re-derived:
      • `account.coa.monthly_salary_equivalent`  — the salary conversion table
      • `storeops.org_span_for_manager` (RPC)    — the canonical manager -> stores span (index §13)
      • `commcalc.management_incentive_payout`   — the ALREADY-COMPUTED manager commission (§9)
    NEVER raises: any failure degrades to a skipped shell and the statement is byte-identical."""
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    if cfg["mode"] != "derive":
        return {"skipped": "overhead allocation is off for this org (house default)", "config": cfg}
    try:
        from app.modules.account.coa import monthly_salary_equivalent
    except Exception:
        return {"skipped": "salary conversion unavailable", "config": cfg}

    so = client.schema("storeops")
    try:
        stores = (so.table("stores").select("store_code,is_active,org_unit_id")
                  .eq("org_id", org_id).limit(50000).execute().data) or []
    except Exception:
        return {"skipped": "store roster unreadable", "config": cfg}
    if not cfg["include_inactive_stores"]:
        stores = [s for s in stores if s.get("is_active") is not False]
    org_codes = [_s(s.get("store_code")) for s in stores if _s(s.get("store_code"))]
    # STORE -> MARKET is resolved through the ONE canonical union index (§13a, owner directive
    # 2026-09-03), NEVER off `stores.market` directly: these rows already carry a store_code, so
    # `core.scope.market_by_code` is the helper for them. Reading one vocabulary here would make a
    # store that exists only in `store_mapping`/`store_aliases` marketless, and under the
    # market-then-store basis a marketless store silently changes everybody else's share.
    _mkt: Dict[str, str] = {}
    try:
        from app.core.scope import market_by_code as _market_by_code
        _mkt = {_s(k).upper(): _s(v) for k, v in (_market_by_code(client, org_id) or {}).items()}
    except Exception:
        _mkt = {}
    market_by_code = {c: _mkt.get(c.upper(), "") for c in org_codes}
    # unit -> its own stores; the subtree is walked below so a market unit covers its stores.
    try:
        units = (so.table("org_units").select("id,parent_id").eq("org_id", org_id)
                 .limit(50000).execute().data) or []
    except Exception:
        units = []
    children: Dict[str, List[str]] = {}
    for u in units:
        children.setdefault(_s(u.get("parent_id")), []).append(_s(u.get("id")))

    def subtree_codes(unit_id: str) -> List[str]:
        seen, stack = set(), [_s(unit_id)]
        while stack:
            u = stack.pop()
            if not u or u in seen:
                continue
            seen.add(u)
            stack.extend(children.get(u, []))
        return [_s(s.get("store_code")) for s in stores
                if _s(s.get("org_unit_id")) in seen and _s(s.get("store_code"))]

    try:
        emps = (so.table("employees")
                .select("employee_id,name,home_store,role,pay_basis,pay_amount,is_active,org_unit_id")
                .eq("org_id", org_id).limit(50000).execute().data) or []
    except Exception:
        return {"skipped": "employee roster unreadable", "config": cfg}

    comm_by_emp: Dict[str, float] = {}
    if cfg["commission_source"] == "management_incentive":
        try:
            want = set(cfg["commission_statuses"])
            rows = (client.schema("commcalc").table("management_incentive_payout")
                    .select("employee_id,period,total,status").eq("org_id", org_id)
                    .eq("period", period).limit(50000).execute().data) or []
            for r in rows:
                if want and _s(r.get("status")).lower() not in want:
                    continue
                eid = _s(r.get("employee_id"))
                if eid:
                    comm_by_emp[eid] = round(comm_by_emp.get(eid, 0.0) + _f(r.get("total")), 2)
        except Exception:
            comm_by_emp = {}

    people = []
    for e in emps:
        is_oh, why, sal = classify_employee(e, cfg, monthly_salary_equivalent)
        if not is_oh:
            continue
        eid = _s(e.get("employee_id"))
        span: List[str] = []
        try:
            rows = (client.rpc("org_span_for_manager",
                               {"p_org_id": str(org_id), "p_employee_id": eid}).execute().data) or []
            span = [_s(r.get("store_code") if isinstance(r, dict) else r) for r in rows]
        except Exception:
            span = []
        span = [c for c in span if c in set(org_codes)]
        unit = subtree_codes(_s(e.get("org_unit_id"))) if _s(e.get("org_unit_id")) else []
        codes, how = covered_stores(span, unit, org_codes, cfg)
        people.append({"employee_id": eid, "label": _s(e.get("name")) or eid,
                       "role": _s(e.get("role")), "why": why, "salary_month": sal,
                       "commission": comm_by_emp.get(eid, 0.0),
                       "covered": codes, "coverage": how})

    res = build_overhead(people, cfg, market_of=lambda c: market_by_code.get(c, ""))
    # Market subtotals are taken in STORE-CODE space, before any re-keying, because that is the only
    # space the market vocabulary is keyed in. They are a pure sum of stores either way.
    res["market_rollup"] = rollup_by_market(res["wages_by_store"], lambda c: market_by_code.get(c, ""))
    if code_to_key is not None:
        for key in ("wages_by_store", "commission_by_store"):
            folded: Dict[str, float] = {}
            for c, v in (res.get(key) or {}).items():
                k = code_to_key(c) or c
                folded[k] = round(folded.get(k, 0.0) + _f(v), 2)
            res[key] = folded
    return res
