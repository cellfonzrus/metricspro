"""THE ONE HOME for "what did this period's labour cost, per store" (index §STOREOPS-10).

OWNER REPORT 2026-11-16, verbatim: *"The payroll for luxelink is being calculated properly but it is
not reflecting per store expenses in dashboard, gross profit or p&l, they should all be updated from
here and it is quick fix but hasn't been happening — if the agent cannot do it assign somebody
senior to fix the platform wide issue not for just this tenant"*.

THE DEFECT HE IS DESCRIBING, MEASURED LIVE (October 2026, all four tenants)
──────────────────────────────────────────────────────────────────────────
The platform held TWO derivations of one question, and the expense surfaces read the wrong one:

  · `GET /storeops/payroll` + `/payroll-by-store` — the Payroll Report. LuxeLink October
    **$121,632.66** (the screen's own $121,632.67, to the cent).
  · `account/coa.derive_wage_cells` — a SECOND shifts x rate derivation, which is what the
    dashboard, the Gross Profit report and the P&L actually booked. LuxeLink **$116,826.26**.

Every store disagreed, in every tenant: Cellfonz R Us $72,905.30 vs **$0.00**, LuxeLink
$121,632.66 vs $116,826.26, Vzone $20,449.79 vs $20,010.00, NY LOGISTICS $20,160.28 vs $18,328.79.

AND EACH COPY KNEW SOMETHING THE OTHER DID NOT, which is how two paths drift:

  the Payroll Report's path        the accounting copy
  ────────────────────────        ───────────────────
  salaried pay-basis override     the per-EMPLOYEE `pay_from_schedule` tick (mig 1073)
  lunch auto-deduction            the clock-evidence verdict (`payroll_evidence`)
  inactive-employee real activity

So neither was the answer. "What did this store's labour cost" now has ONE producer — the one the
Payroll Report publishes — and ONE door onto it, this module. Every non-payroll surface asks HERE.

WHAT THIS MODULE DOES NOT DO
────────────────────────────
It derives no dollar, states no hours rule, no rate, no tax rate, no store name and no tenant name
(RULE TWO). It calls the producer and SHAPES its answer into the key spaces its readers hold:

  · `cells_by_code`     {store_code: $}    — the Gross Profit report's space.
  · `cells_by_address`  {address or None: $} — the P&L's space; an unkeyable store keeps its own
                         spelling rather than vanishing, and labour attributable to NO store is
                         carried under `None` (company-wide) exactly as the statement expects.

THE REFUSAL IS NOT THIS MODULE'S EITHER. `storeops/payroll_evidence` decides whether a period's
clock evidence may be paid from, and the caller that books money consults it. A figure this module
returns is the figure the Payroll Report shows; whether a tenant's books may carry it is a separate,
owner-ruled question, and the two must not be confused (§19.50 tri-state).

Pure shaping (`fold_rows`, `cells_by_code`, `cells_by_address`) is DB-free.
Proof + lock: `backend/harness_labour_cost_one_home.py`.
"""
from typing import Any, Callable, Dict, Optional

from app.modules.storeops.payroll_salary import UNASSIGNED_STORE

#: The producer this door dereferences, named once so the lock harness can find it and so a reader
#: of the index can see which surface the books now agree with.
PRODUCER = "GET /storeops/payroll-by-store"


def _producer_fn() -> Callable:
    """The live producer. Imported lazily: `account/coa` is a reader of this module and the producer
    lives in the StoreOps router, so a module-level import would tie finance's import graph to a
    FastAPI router for no gain. There is no cycle — the StoreOps router imports no finance module."""
    from app.modules.storeops import router as _r
    return _r.get_payroll_by_store


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def is_unattributed(store_code) -> bool:
    """PURE: does this producer key mean "this pay belongs to no store"?

    TWO spellings mean it, and both are the producer's, not this module's: an EMPTY code, and
    `payroll_salary.UNASSIGNED_STORE` — the bucket a salaried employee with no worked hours and no
    `home_store` lands in. DEREFERENCED, never re-spelled here: booking that bucket as if it were a
    store would put a store called "Unassigned" on the P&L, holding real money (live, October 2026:
    NY LOGISTICS $8,857.15). The books carry it company-wide, which is where it has always been."""
    c = str(store_code or "").strip()
    return (not c) or c.casefold() == UNASSIGNED_STORE.casefold()


def fold_rows(rows) -> Dict[str, Dict[str, float]]:
    """PURE: the producer's `stores` rows -> {store_code: {'hours', 'amount'}}.

    A row that belongs to no store folds under `''` — labour with no store is a FACT the readers
    must be able to see and place, never a row quietly dropped. Two rows carrying the same code are
    summed rather than one overwriting the other."""
    out: Dict[str, Dict[str, float]] = {}
    for r in rows or []:
        code = str((r or {}).get("store_code") or "").strip()
        if is_unattributed(code):
            code = ""
        cell = out.setdefault(code, {"hours": 0.0, "amount": 0.0})
        cell["hours"] = round(cell["hours"] + _num(r.get("hours")), 4)
        cell["amount"] = round(cell["amount"] + _num(r.get("amount")), 2)
    return out


def cells_by_code(cells) -> Dict[str, float]:
    """PURE: {store_code: cell} -> {store_code: $}, the Gross Profit report's key space."""
    return {code: round(_num(c.get("amount")), 2) for code, c in (cells or {}).items()}


def cells_by_address(cells, code2addr=None, norm: Optional[Callable] = None) -> Dict[Any, float]:
    """PURE: {store_code: cell} -> {store address or None: $}, the P&L's key space.

    `code2addr` is the caller's canonical code -> address map and `norm` its key-folding rule; both
    are handed over rather than restated, so this module owns no store-identity rule of its own
    (§64). A code the map does not know keeps its own (folded) spelling — the money stays visible on
    a row of its own instead of disappearing into a key nobody renders. The empty code becomes
    `None`: "attributable to no store", which is what the statement books company-wide."""
    fold = norm or (lambda s: str(s or "").strip())
    m = code2addr or {}
    out: Dict[Any, float] = {}
    for code, c in (cells or {}).items():
        amt = round(_num(c.get("amount")), 2)
        key = None if is_unattributed(code) else m.get(fold(code), fold(code))
        out[key] = round(out.get(key, 0.0) + amt, 2)
    return out


def per_store(org_id, *, month=None, start=None, end=None, so=None,
              producer: Optional[Callable] = None) -> Dict[str, Dict[str, float]]:
    """THE DOOR: {store_code: {'hours', 'amount'}} for a month or an inclusive start/end range.

    `so` is a StoreOps-schema client handle for a caller that already holds one (and for a DB-free
    test); omitted, the producer reads through its own. `producer` is injectable for the same
    reason and defaults to the live one — it is never a second implementation."""
    fn = producer or _producer_fn()
    out = fn(month=month, start=start, end=end, org_id=org_id, so=so) or {}
    return fold_rows(out.get("stores") if isinstance(out, dict) else out)
