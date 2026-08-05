"""Cash Deposit Reconciliation — migration 509, OWNER DIRECTIVE 2026-08-05.

Cross-checks cash actually COLLECTED (Daily Closing declared cash + the POS X-Report, per the domain's
usual "X-Report is authoritative for tenders" rule) against cash actually DEPOSITED
(commcalc.bank_deposit, mig 107/502), net of tenant-configurable ADJUSTMENTS (cash expenses / bill-
payment cash / any other tenant-defined item), split across tenant-defined deposit CATEGORIES.

Deliberately reuses existing infra rather than building a parallel cash-position calculator:
  - the RAW (t_cash, epay_on_cash) sums read here are the SAME per-(store,day) figures
    `router._bank_deposit_declared` already reads off `daily_closing` — see `closing_cash_raw_by_store_day`,
    which `_bank_deposit_declared` is refactored (byte-identically) to call instead of its own inline loop.
  - the "cash expenses" adjustment reuses `envelope.approved_expense_totals` (EEP, mig 506/507) verbatim
    — the SAME approved-or-paid closing_expense sum every other netting surface in this module uses.
  - the "bill payments made in cash" adjustment is the SAME `epay_on_cash` figure the `bill_payment_cash`
    match target already represents — not re-derived.
  - the X-Report cross-check reuses `router._xreport_tenders_by_store` (pos_tender_summary), passed in
    by the caller (never re-queried here).

Everything DB-reading is try/except-guarded (missing table/migration -> empty/degrade, never raise) —
same "empty config == today's behaviour" doctrine as tender_config.py / envelope.py. The PURE functions
(cash_for_basis / expected_deposit / status_for / build_deposit_group / remaining_short) have NO DB
access and are unit-proven with no fake client at all — see backend/harness_deposit_recon.py.
"""


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ── Deposit categories (lazy-seeded, mirrors expense_config.py's pattern exactly) ──────────────────
CAT_TABLE = "closing_deposit_category"
BASIS_VALUES = ("bill_payment_cash", "store_cash", "total_cash", "manual")

# The owner's exact named defaults. "manual" categories a tenant later adds have no auto-computed
# expected figure (see cash_for_basis) until a future generalized per-category cash-split exists on the
# closing entry form — flagged as a follow-on in the retail-ops handoff, not silently pretended away.
PRESET_CATEGORY_DEFS = [
    ("Bill Payment Cash Deposit", "bill_payment_cash", 10),
    ("Store Cash Deposit", "store_cash", 20),
]


def _normalize_basis(b) -> str:
    b = str(b or "").strip().lower()
    return b if b in BASIS_VALUES else "manual"


def load_categories(client, org_id, active_only: bool = True, seed_if_empty: bool = True):
    """The org's deposit categories, sorted. Lazy-seeds the 2 presets when the org has none yet —
    same upsert-on-first-GET pattern as expense_config.load_categories (NOT the read-time-only fallback
    tender_config.py/count_config.py use — a category is a real row every bank_deposit/adjustment
    references by id). Degrades to the coded presets (unsaved) if the table isn't migrated yet or the
    seed write itself fails — never raises."""
    try:
        q = client.schema("commcalc").table(CAT_TABLE).select("*").eq("org_id", org_id)
        if active_only:
            q = q.eq("is_active", True)
        rows = q.execute().data or []
    except Exception:
        return [{"id": None, "name": n, "basis": b, "is_preset": True, "is_active": True,
                  "sort_order": so, "source": "default"} for (n, b, so) in PRESET_CATEGORY_DEFS]
    if not rows:
        if seed_if_empty:
            try:
                seed = [{"org_id": org_id, "name": n, "basis": b, "is_preset": True, "is_active": True,
                         "sort_order": so} for (n, b, so) in PRESET_CATEGORY_DEFS]
                ins = client.schema("commcalc").table(CAT_TABLE).insert(seed).execute()
                rows = ins.data or []
            except Exception:
                pass
        if not rows:
            return [{"id": None, "name": n, "basis": b, "is_preset": True, "is_active": True,
                      "sort_order": so, "source": "default"} for (n, b, so) in PRESET_CATEGORY_DEFS]
    rows.sort(key=lambda r: (r.get("sort_order") if r.get("sort_order") is not None else 100,
                             r.get("name") or ""))
    return rows


def category_by_id(client, org_id, category_id):
    """One category row (or None). Ensures the org's categories exist first (lazy-seed) so a first-ever
    deposit against a never-configured tenant still resolves the presets."""
    if not category_id:
        return None
    cats = load_categories(client, org_id, active_only=False)
    for c in cats:
        if str(c.get("id")) == str(category_id):
            return c
    return None


# ── Adjustment types (open list, NO forced presets — "any other adjustment item the tenant configures")
ADJ_TYPE_TABLE = "closing_deposit_adjustment_type"


def load_adjustment_types(client, org_id, active_only: bool = True):
    try:
        q = client.schema("commcalc").table(ADJ_TYPE_TABLE).select("*").eq("org_id", org_id)
        if active_only:
            q = q.eq("is_active", True)
        rows = q.execute().data or []
    except Exception:
        return []
    rows.sort(key=lambda r: (r.get("sort_order") if r.get("sort_order") is not None else 100,
                             r.get("name") or ""))
    return rows


def adjustment_type_by_id(client, org_id, type_id):
    if not type_id:
        return None
    for t in load_adjustment_types(client, org_id, active_only=False):
        if str(t.get("id")) == str(type_id):
            return t
    return None


ADJ_TABLE = "closing_deposit_adjustment"


def load_other_adjustments(client, org_id, date_from, date_to, store_codes=None):
    """Manual 'other' adjustment ledger rows in range — the tenant-configured 3rd adjustment bucket.
    Returns (rows, by_key) where by_key sums amount keyed on (store_code, close_date_str,
    category_id_or_None) — category_id=None means 'applies to the general/total_cash basis', mirroring
    how approved_expense_totals' by_store_day is the superset used at store-day grain."""
    try:
        q = (client.schema("commcalc").table(ADJ_TABLE).select("*").eq("org_id", org_id)
             .gte("close_date", date_from).lte("close_date", date_to))
        if store_codes:
            q = q.in_("store_code", list(store_codes))
        rows = q.limit(200000).execute().data or []
    except Exception:
        return [], {}
    by_key = {}
    for r in rows:
        amt = _f(r.get("amount"))
        if not amt:
            continue
        key = (r.get("store_code") or "", str(r.get("close_date") or ""), r.get("category_id") or None)
        by_key[key] = round(by_key.get(key, 0.0) + amt, 2)
    return rows, by_key


# ── Raw closing-cash reader (single source of truth — _bank_deposit_declared refactors to call this) ─
def closing_cash_raw_by_store_day(client, org_id, date_from, date_to, store_codes=None):
    """{(store_code, close_date_str): {"t_cash": sum, "epay_cash": sum, "rows": n}} over daily_closing
    for the range — the RAW, un-netted figures (no EEP expense/withdrawal deduction here; callers that
    want that apply envelope.net_store_day themselves, exactly as _bank_deposit_declared already does).
    Falls back t_cash -> store_cash for pre-mig-103 tenants that never populated t_cash, same fallback
    _bank_deposit_declared's inline loop already applied."""
    try:
        q = (client.schema("commcalc").table("daily_closing")
             .select("store_code,close_date,t_cash,store_cash,epay_on_cash").eq("org_id", org_id)
             .gte("close_date", date_from).lte("close_date", date_to))
        if store_codes:
            q = q.in_("store_code", list(store_codes))
        rows = q.limit(200000).execute().data or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        cash = _f(r.get("t_cash"))
        if not cash:
            cash = _f(r.get("store_cash"))
        epay = _f(r.get("epay_on_cash"))
        key = (r.get("store_code") or "", str(r.get("close_date") or ""))
        agg = out.setdefault(key, {"t_cash": 0.0, "epay_cash": 0.0, "rows": 0})
        agg["t_cash"] += cash
        agg["epay_cash"] += epay
        agg["rows"] += 1
    for a in out.values():
        a["t_cash"] = round(a["t_cash"], 2)
        a["epay_cash"] = round(a["epay_cash"], 2)
    return out


def bank_deposits_by_store_day(client, org_id, date_from, date_to, store_codes=None):
    """Raw commcalc.bank_deposit rows in range (every column) — grouping into
    (store, day, category)/original-vs-supplemental is the caller's job (assemble_day below).
    Degrades to [] pre-mig-107 (should not happen in practice — bank_deposit is mig 107, long-lived —
    but kept consistent with this module's blanket degrade doctrine)."""
    try:
        q = (client.schema("commcalc").table("bank_deposit").select("*").eq("org_id", org_id)
             .gte("close_date", date_from).lte("close_date", date_to))
        if store_codes:
            q = q.in_("store_code", list(store_codes))
        rows = q.limit(200000).execute().data or []
    except Exception:
        return []
    return rows


# ── Pure math (no DB — unit-proven by harness_deposit_recon.py) ────────────────────────────────────
def cash_for_basis(t_cash: float, epay_cash: float, basis: str) -> float:
    """The gross cash figure a deposit CATEGORY reconciles against, from the SAME 3 formulas
    _bank_deposit_declared already supports (never a 4th/different computation):
      bill_payment_cash = epay_cash            store_cash = max(t_cash - epay_cash, 0)
      total_cash = t_cash                      manual = 0 (a tenant-added bucket with no formula yet)."""
    basis = _normalize_basis(basis)
    if basis == "bill_payment_cash":
        return round(_f(epay_cash), 2)
    if basis == "store_cash":
        return round(max(_f(t_cash) - _f(epay_cash), 0.0), 2)
    if basis == "total_cash":
        return round(_f(t_cash), 2)
    return 0.0


def expected_deposit(t_cash: float, epay_cash: float, basis: str,
                      expenses_amt: float = 0.0, bill_amt: float = 0.0, other_amt: float = 0.0,
                      include_expenses: bool = False, include_bill_payments: bool = False,
                      include_other: bool = False):
    """(expected, adjustments_applied, gross_cash_collected) for one category/day.

    Adjustment-application rule (deliberate, to never double-subtract the same dollar):
      - 'cash expenses' (approved-or-paid closing_expense) only applies to store_cash/total_cash bases
        — the physical envelope those dollars actually left. Applying it to bill_payment_cash would
        double-count against money that basis never included in the first place.
      - 'bill payments made in cash' (epay_cash) ONLY applies to the total_cash basis. A tenant running
        the bill_payment_cash/store_cash SPLIT already structurally excludes epay_cash from store_cash
        (store_cash = t_cash - epay_cash BY DEFINITION) — subtracting it again there would double-count
        the same dollar a second time. A tenant using a single combined total_cash category, by
        contrast, has never excluded it, so the checkbox has real work to do there.
      - 'other' (tenant-configured, manual ledger) applies to store_cash/total_cash bases, same class
        as expenses (a genuine cash-out-of-the-envelope reduction), never bill_payment_cash.
    Expected is floored at 0 (a category can never be expected to owe a NEGATIVE deposit).
    ALL THREE excluded (False) by default — expected == cash_for_basis, i.e. byte-identical to "no
    adjustment logic at all" until an operator/report caller explicitly opts in — the owner's explicit
    "excluded by default" requirement."""
    basis = _normalize_basis(basis)
    gross = cash_for_basis(t_cash, epay_cash, basis)
    adj = 0.0
    if basis in ("store_cash", "total_cash"):
        if include_expenses:
            adj += _f(expenses_amt)
        if include_other:
            adj += _f(other_amt)
    if basis == "total_cash" and include_bill_payments:
        adj += _f(bill_amt)
    expected = round(max(gross - adj, 0.0), 2)
    return expected, round(adj, 2), gross


def status_for(variance: float, tolerance: float = 1.0) -> str:
    """'short' | 'over' | 'ok' — same +/- tolerance convention as every other closing recon surface
    (epay-recon/tender-recon default to 1.0)."""
    if variance < -abs(tolerance):
        return "short"
    if variance > abs(tolerance):
        return "over"
    return "ok"


def build_deposit_group(deposit_rows: list) -> dict:
    """One (store, day, category) group's deposit rows -> {deposits (chronological), total_deposited}.
    Purely additive/order-preserving — proves the append-only guarantee downstream: an original + N
    supplemental rows always SUM, never overwrite (there is nothing here that could collapse two rows
    into one; the caller never mutates a prior row when a new one is appended)."""
    ordered = sorted(deposit_rows, key=lambda r: (r.get("created_at") or "", r.get("id") or ""))
    total = round(sum(_f(r.get("amount")) for r in ordered), 2)
    return {"deposits": ordered, "total_deposited": total}


def remaining_short(expected: float, total_deposited: float) -> float:
    return round(max(_f(expected) - _f(total_deposited), 0.0), 2)


# ── Orchestration (thin glue — every heavy computation above is independently pure/testable) ───────
def assemble_category_block(cat: dict, t_cash: float, epay_cash: float,
                             expenses_amt: float, bill_amt: float, other_amt: float,
                             include_expenses: bool, include_bill_payments: bool, include_other: bool,
                             deposit_rows: list, tolerance: float = 1.0) -> dict:
    """One category's full recon block for one (store, day) — the unit both the report endpoint and
    the harness build up from. `deposit_rows` are this category's own bank_deposit rows for that
    (store, day) only (already filtered/grouped by the caller)."""
    expected, adj_applied, gross = expected_deposit(
        t_cash, epay_cash, cat.get("basis"), expenses_amt, bill_amt, other_amt,
        include_expenses, include_bill_payments, include_other)
    grp = build_deposit_group(deposit_rows)
    variance = round(grp["total_deposited"] - expected, 2)
    return {
        "category_id": cat.get("id"), "category_name": cat.get("name"), "basis": _normalize_basis(cat.get("basis")),
        "cash_collected": gross, "adjustments_applied": adj_applied,
        "expenses_amount": round(_f(expenses_amt), 2), "bill_payments_amount": round(_f(bill_amt), 2),
        "other_amount": round(_f(other_amt), 2),
        "expected_deposit": expected, "deposits": grp["deposits"], "total_deposited": grp["total_deposited"],
        "variance": variance, "status": status_for(variance, tolerance),
        "remaining_short": remaining_short(expected, grp["total_deposited"]),
    }
