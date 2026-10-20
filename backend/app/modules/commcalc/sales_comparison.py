"""Sales Comparison — period-over-period % change per product category, across all stores.

OWNER DIRECTIVE: "need a month over month / year over year, week-1 over week-1, … report as of a
specific day for the sales in all stores … show the percentage increase or decrease for each item
sold — phones, byod, accessories, tablet, financing like acima / tw."

This module is PURE math over the two windows of sale lines the router hands it (base = the period the
owner is looking at, compare = whatever the scenario points at). The router is the ONLY thing that
knows a scenario — it picks the two periods and, for the "as of a specific day" / "week N" scenarios,
cuts BOTH windows to the same day-of-month slice so a mid-month base is never compared against a full
comparison month. Everything below is derived from `base_rows`/`cmp_rows` alone, so the harness drives
it with fixtures.

THE CATEGORIES (owner-specified order + metric; documented so the number is never a mystery):
  Phones / Tablets       — UNITS. The transaction's STRONGEST device signal (installment_category chain),
                           so a phone sold with a case + a SIM is ONE phone and a tablet is ONE tablet.
                           New and upgrade device sales both land here (an upgrade is still a phone sold).
  BYOD                   — UNITS. A transaction carrying a BYOD activation line (customer brought the
                           device; there is no device unit, so it is tallied on its own exactly like the
                           Sales Report's BYOD column) via the shared classify_contract_type.
  Activation             — UNITS. A transaction carrying a PREMIUM (new-line) activation line
                           (classify_contract_type == 'premium') — the SAME classifier the Sales Report /
                           Exec MTD / commission engine use. Independent tally, exactly like BYOD: a new
                           phone activation counts in BOTH Phones (a device sold) and Activation (a new
                           line opened), which is how the Sales Report already shows those two columns.
  Accessories            — DOLLARS. Accessory LINES sold (the ONE shared accessory classifier), summed on
                           ext_price. Units are still carried for context but $ is the headline metric.
  Financing              — UNITS **and** DOLLARS. ONE carrier-scoped line: a transaction carrying the
                           ACTIVE CARRIER'S financing tender, detected with the SAME matchers the
                           Financing Report and the payout use. CARRIER-SCOPED / COMPLIANCE-CRITICAL — the
                           router hands `build` ONLY the vendors that serve the active carrier (see
                           `vendors_for_carrier`), and all of them collapse into this single "Financing"
                           row so no screen ever shows both Boost's and Total's financing vendors together
                           (the dual-affiliation leak this report was flagged for). The vendor BRAND
                           (ACIMA / TW / Edge) is never emitted — only the neutral label "Financing".

UNITS are DISTINCT transactions per bucket — a multi-line receipt is one sale, not N. The two live-line
filters (not voided, not a Return) are the same two every commcalc sales report applies.
"""
import calendar as _cal

from app.modules.commcalc.calculator import safe_float
from app.modules.commcalc import line_class as _lc   # THE activation-event count unit (2026-09-25)
from app.modules.commcalc import installment_category as _icat
from app.modules.commcalc import financing_registry as _finreg
# THE period parser (§19.50's home), so 'August 2026' and '2026-08' are one month to this report
# exactly as they are to the carry-forward rule itself — dereferenced, never re-spelled.
from app.modules.commcalc.expenses_effective import period_sort_key as _period_key

VOID_TOKENS = ("true", "yes", "1", "voided", "void")


# ── scenario helpers (pure; the router feeds these two windows into build) ──────────────────────────
def period_ym(period):
    """('YYYY-MM' | 'Month YYYY') → (year, month), or None if it can't be parsed as a month. PURE."""
    p = str(period or "").strip()
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
        return int(p[:4]), int(p[5:7])
    parts = p.split()
    names = {m.lower(): i for i, m in enumerate(_cal.month_name) if m}
    if len(parts) == 2 and parts[0].lower() in names and parts[1].isdigit():
        return int(parts[1]), names[parts[0].lower()]
    return None


def shift_month(y, m, months_back):
    """(y, m) shifted back `months_back` calendar months. PURE."""
    idx = y * 12 + (m - 1) - months_back
    return idx // 12, idx % 12 + 1


def week_bounds(week):
    """Day-of-month bounds for 'week N of the month' (1→1-7 … 5→29-31). PURE."""
    lo = (week - 1) * 7 + 1
    hi = 31 if week >= 5 else week * 7
    return lo, hi


def in_day_window(trans_date, as_of_day, week):
    """Does a line's date fall in the selected day-slice? `week` (1-5) wins if set, else `as_of_day`
    keeps day ≤ N (month-to-date). A line with no parseable day is only kept in a FULL-month window —
    it can't be fairly placed inside a slice. PURE."""
    d = str(trans_date or "")
    if not (len(d) >= 10 and d[8:10].isdigit()):
        return not week and not as_of_day
    day = int(d[8:10])
    if week:
        lo, hi = week_bounds(week)
        return lo <= day <= hi
    if as_of_day:
        return day <= as_of_day
    return True

# The device chain-categories that get their own headline row. home_internet / sim / unknown are folded
# into the totals but not shown as one of the owner's named items (kept in `other_units` for honesty).
_DEVICE_HEADLINE = ("phone", "tablet")


def is_live_line(r):
    """A sale line that counts: not voided, not a Return. Same two filters every other commcalc sales
    report applies before counting anything. PURE."""
    if str(r.get("voided") or "").strip().lower() in VOID_TOKENS:
        return False
    if str(r.get("trans_type") or "").strip().lower() == "return":
        return False
    return True


def _txn_groups(rows):
    """Live sale lines grouped into transactions: {trans_id: [line, …]}. A line with no trans_id is its
    own singleton (keyed by a synthetic id) so it is still counted once. PURE."""
    groups = {}
    for i, r in enumerate(rows or []):
        if not is_live_line(r):
            continue
        tid = str(r.get("trans_id") or "").strip() or f"_row{i}"
        groups.setdefault(tid, []).append(r)
    return groups


def _txn_store(lines):
    """The transaction's store = the first non-blank store on any of its lines. PURE."""
    for ln in lines:
        st = str(ln.get("store") or "").strip()
        if st:
            return st
    return ""


# ── carrier scoping (COMPLIANCE-CRITICAL; PURE — the backend mirror of carrier-scope.ts) ────────────
def vendor_serves_carrier(vendor, active_carrier):
    """Does a financing vendor serve the active carrier? Mirrors the frontend `vendorServesCarrier`:
    a vendor with NO carrier assignment is carrier-neutral ("any carrier") and always matches; otherwise
    it matches when any of its carrier rows names the active carrier (by carrier_name or carrier_id).
    An empty active_carrier means "no lens applied" → everything matches. PURE.

    This is the server-side guard that keeps the other carrier's financing vendor out of the payload
    entirely, so the dual Boost+Total affiliation can never leak even if the frontend lens is bypassed."""
    a = str(active_carrier or "").strip().lower()
    if not a:
        return True
    cs = vendor.get("carriers") or []
    if not cs:
        return True
    for c in cs:
        t = str((c.get("carrier_name") or c.get("carrier_id") or "")).strip().lower()
        if not t:                       # a carrier row with no name is itself neutral
            return True
        if a in t or t in a:
            return True
    return False


def vendors_for_carrier(vendors, active_carrier):
    """The subset of resolved vendors that serve the active carrier — the ONLY vendors the report may
    count, so a single collapsed "Financing" line can never mix two carriers' vendors. PURE."""
    return [v for v in (vendors or []) if vendor_serves_carrier(v, active_carrier)]


def _financing_status(vendors):
    """Aggregate detection status for the single Financing line, given the carrier-scoped vendors that
    actually feed it. 'configured' if any usable vendor is configured; else the most-informative of the
    remaining statuses; None when there are no carrier-scoped vendors at all. PURE."""
    usable = [v for v in (vendors or []) if v.get("enabled") and (v.get("matchers") or [])]
    if not usable:
        return "not_configured" if vendors else None
    order = {"configured": 0, "inherited_default": 1, "not_configured": 2, "unusable": 3}
    return sorted((v.get("detection_status") or "not_configured" for v in usable),
                  key=lambda s: order.get(s, 9))[0]


# ── STORE EXPENSES (owner ask 2026-10-17) ─────────────────────────────────────────────────────────
# Expenses are NOT an item sold, and they are deliberately a line of their own: they never enter the
# revenue totals, the transaction count or any per-item tile. The figure is the month's EFFECTIVE
# store expenses — `expenses_effective`'s carry-forward rule, the same rule the P&L and the GP report
# book — handed in already folded per store, so this module states no expense rule of its own.
EXPENSE_KEY = "expenses"
EXPENSE_LABEL = "Store expenses"


def category_defs(vendors, with_expenses=False):
    """The ordered list of category keys + labels + primary metric this report tracks. OWNER ORDER +
    METRIC (exact): Phones (units), BYOD (units), Activation (units), Tablets (units), Accessories ($),
    Financing (units AND $). `vendors` is the ALREADY carrier-scoped vendor list — the single Financing
    row is present whenever the tenant has any financing vendor for the active carrier.

    `with_expenses` appends the Store expenses line (owner ask 2026-10-17) — flagged `expense: True`
    so no caller can mistake it for revenue. PURE."""
    defs = [
        {"key": "phone", "label": "Phones", "metric": "units"},
        {"key": "byod", "label": "BYOD", "metric": "units"},
        {"key": "activation", "label": "Activation", "metric": "units"},
        {"key": "tablet", "label": "Tablets", "metric": "units"},
        {"key": "accessory", "label": "Accessories", "metric": "dollars"},
    ]
    if vendors:
        defs.append({"key": "financing", "label": "Financing", "metric": "both", "financing": True,
                     "detection_status": _financing_status(vendors)})
    if with_expenses:
        defs.append({"key": EXPENSE_KEY, "label": EXPENSE_LABEL, "metric": "dollars",
                     "expense": True})
    return defs


def _blank_metric():
    return {"units": 0, "revenue": 0.0, "gp": 0.0}


def tally(rows, rules, is_accessory, vendors, line_rules=None):
    """One window → {store: {cat_key: {units, revenue, gp}}}, plus a `txns` count and `other_units`
    (home-internet / SIM / unclassified device sales, surfaced for honesty). PURE — `is_accessory(row)`
    is injected. `rules` = installment_category.load_category_rules(...); `vendors` =
    financing_registry.resolve_vendors(...); `line_rules` = the org's activation-type rules
    (line_class.resolve_rules; None = house defaults) for THE one predicate."""
    fin_vendors = [v for v in (vendors or []) if v.get("enabled") and (v.get("matchers") or [])]
    per_store = {}
    for tid, lines in _txn_groups(rows).items():
        store = _txn_store(lines)
        st = per_store.setdefault(store, {"cats": {}, "txns": 0, "other_units": 0})
        st["txns"] += 1
        cats = st["cats"]

        # ── device unit: the transaction's strongest device signal (one unit for the whole receipt) ──
        chain_cat, _ev = _icat.resolve_chain_category(lines, rules, is_accessory=is_accessory)
        if chain_cat in _DEVICE_HEADLINE:
            m = cats.setdefault(chain_cat, _blank_metric())
            m["units"] += 1
            # revenue/gp of a device sale = its NON-accessory lines (device + plan + fees); the accessory
            # lines are attributed to Accessories below so the two never double-count.
            for ln in lines:
                if is_accessory and is_accessory(ln):
                    continue
                m["revenue"] += safe_float(ln.get("ext_price"))
                m["gp"] += safe_float(ln.get("gp"))
        elif chain_cat not in ("accessory",):
            # home_internet / sim / unknown — real sales, just not one of the owner's headline items.
            st["other_units"] += 1

        # THE COUNT UNIT (2026-09-25): line_class.activation_units — the org's `event.count_unit`. House
        # 'transaction' = one unit per receipt that carries such a line (exactly the retired `any(...)`,
        # a blank-id receipt included); 'event' = one per activation / upgrade on the receipt.
        _units = [u for u in _lc.activation_units(lines, line_rules, require_txn=False) if u]
        # ── BYOD (independent tally, like the Sales Report's BYOD column) ──
        _nb = len({u[1] for u in _units if u[0] == "byod"})
        if _nb:
            cats.setdefault("byod", _blank_metric())["units"] += _nb

        # ── Activation (independent tally): a PREMIUM new-line activation on the receipt. THE one
        #    predicate (line_class) the commission engine / Sales Report use, over the whole row with the
        #    org's rules. ──
        _np = len({u[1] for u in _units if u[0] == "premium"})
        if _np:
            cats.setdefault("activation", _blank_metric())["units"] += _np

        # ── Accessories: every accessory LINE on the receipt, with its revenue ($ is the headline) ──
        for ln in lines:
            if is_accessory and is_accessory(ln):
                m = cats.setdefault("accessory", _blank_metric())
                m["units"] += 1
                m["revenue"] += safe_float(ln.get("ext_price"))
                m["gp"] += safe_float(ln.get("gp"))

        # ── Financing (ONE carrier-scoped line): a transaction carrying ANY active-carrier vendor's
        #    tender = one financed unit. `fin_vendors` is already carrier-scoped by the router, so this
        #    can never mix two carriers. Counted ONCE per receipt even if several vendors would hit. ──
        financed = any(
            _finreg.matcher_hits(ln, mch)
            for v in fin_vendors for ln in lines for mch in v["matchers"]
        )
        if financed:
            m = cats.setdefault("financing", _blank_metric())
            m["units"] += 1
            # financed $ = the device (highest-value) line of the transaction — the same "unit line"
            # basis the Financing Report defaults to; labelled, never guessed from a POS column.
            m["revenue"] += max((safe_float(ln.get("ext_price")) for ln in lines), default=0.0)
    return per_store


def _pct(cur, prev):
    """Period-over-period % change, or None when there is no base to divide by (a brand-new item —
    the frontend renders that as 'new' rather than a meaningless ∞%). PURE."""
    if prev in (None, 0) or prev == 0.0:
        return None
    return round((cur - prev) / abs(prev) * 100.0, 1)


def _combine(cur_m, prev_m):
    cur_m = cur_m or _blank_metric()
    prev_m = prev_m or _blank_metric()
    return {
        "current": cur_m["units"], "previous": prev_m["units"],
        "delta": cur_m["units"] - prev_m["units"], "pct": _pct(cur_m["units"], prev_m["units"]),
        "current_rev": round(cur_m["revenue"], 2), "previous_rev": round(prev_m["revenue"], 2),
        "rev_delta": round(cur_m["revenue"] - prev_m["revenue"], 2),
        "rev_pct": _pct(cur_m["revenue"], prev_m["revenue"]),
        "current_gp": round(cur_m["gp"], 2), "previous_gp": round(prev_m["gp"], 2),
    }


def expense_change_measured(base_carried_from, compare_carried_from,
                            base_month_labour=False, compare_month_labour=False):
    """PURE: can a month-over-month EXPENSE change be measured at all?

    THE DEFECT THIS NAMES (owner report 2026-10-19, looking at the live screen). An expense month a
    tenant has not entered inherits the latest earlier month — the P&L's own carry-forward rule
    (§19.50), and the right answer for what that month BOOKS. But when BOTH windows inherit the SAME
    month, the two figures are one sheet compared with itself, and the report printed `± 0%`. A
    reader acts on that: it says "expenses held steady month over month", when what actually happened
    is that nobody has entered either month. Measured live: one tenant's `store_expenses` ends at
    **August 2026**, so October vs September showed $276,162.69 vs $276,162.69 and ± 0% — August
    against August.

    THE CLASS, the one this house keeps re-learning: *an absence is reported with its reason, never
    as a result* (§19.49). A zero change is a claim about two months; it may only be made when two
    months were actually measured.

    False when both windows carried from the same sheet AND neither of them contributed a figure of
    its OWN; True otherwise — including when only ONE side carried, because then the two figures
    really are different months and the change is real.

    `base_month_labour` / `compare_month_labour` say whether that month's SALARY OR COMMISSION was
    measured from its own payroll rather than inherited (owner directive 2026-10-20, §COMMCALC-23).
    Once they are, two carried months are no longer one sheet compared with itself: the labour half
    of each figure is that month's own measurement, and a change between them is a real claim. The
    non-labour half may still be one sheet, which the surface says — a partly-measured change is
    reported WITH what is measured in it, not withheld as if nothing were (§19.49 cuts both ways:
    a measurement must not be suppressed any more than an absence may be asserted)."""
    b = str(base_carried_from or "").strip()
    c = str(compare_carried_from or "").strip()
    if not b or not c:
        return True
    if base_month_labour or compare_month_labour:
        return True
    kb, kc = _period_key(b), _period_key(c)
    if kb == (0, 0) or kc == (0, 0):
        return b.lower() != c.lower()
    return kb != kc


def _expense_cell(cur, prev):
    """PURE: one store's base-vs-compare EXPENSE dollars in the same row shape every other row uses,
    with the unit fields pinned at 0 — an expense has no unit, and a 0 there is the honest answer, not
    a missing one. `pct` mirrors `rev_pct` so a caller sorting the table on 'Change %' sorts this row
    on the only change it has."""
    cur = round(float(cur or 0.0), 2)
    prev = round(float(prev or 0.0), 2)
    return {
        "current": 0, "previous": 0, "delta": 0, "pct": _pct(cur, prev),
        "current_rev": cur, "previous_rev": prev,
        "rev_delta": round(cur - prev, 2), "rev_pct": _pct(cur, prev),
        "current_gp": 0.0, "previous_gp": 0.0,
    }


def build(base_rows, cmp_rows, rules, is_accessory, vendors, *,
          base_period, compare_period, mode, window_label, resolve_market=None, params=None,
          line_rules=None, expenses=None, store_key_of=None):
    """The whole report payload. PURE apart from the injected `resolve_market(store)` /
    `store_key_of(store)`.

    Returns per-(store × category) rows (each with current / previous / Δ / Δ%), a totals-by-category
    roll-up for the summary tiles, and an overall transactions/revenue/gp comparison.

    STORE EXPENSES (owner ask 2026-10-17). `expenses`, when given, is
    `{'base': {store key: $}, 'compare': {store key: $}, 'base_carried_from': …,
    'compare_carried_from': …}` — ALREADY folded per store by `expenses_effective.totals_by_store`
    under the carry-forward rule the P&L and the GP report book, so no expense rule lives here. It is
    reported as its own `expenses` line and NEVER enters revenue, GP or the transaction count: an
    expense added into a revenue total would be a lie in both directions.

    `store_key_of(store)` is the store-identity fold (`account.store_identity.store_key` partially
    applied) — REQUIRED for the expense join to be right, because the sale feed spells a store by its
    ADDRESS and `store_expenses` spells it by its CODE. Without it the join is identity and the two
    vocabularies cannot meet. When several sale spellings fold to ONE store the expense is reported
    ONCE, on that store's canonical row, and the spellings it covers are named in
    `expense_store_merges` — reporting it per spelling would multiply one month's rent.
    """
    cats = category_defs(vendors, with_expenses=expenses is not None)
    sale_keys = [c["key"] for c in cats if not c.get("expense")]
    cat_keys = [c["key"] for c in cats]
    cat_label = {c["key"]: c["label"] for c in cats}
    cat_metric = {c["key"]: c.get("metric", "units") for c in cats}

    base = tally(base_rows, rules, is_accessory, vendors, line_rules)
    comp = tally(cmp_rows, rules, is_accessory, vendors, line_rules)
    stores = sorted(set(base) | set(comp))

    rows = []
    cat_tot = {k: {"cur": _blank_metric(), "prev": _blank_metric()} for k in sale_keys}
    store_summ = []
    over_cur_txn = over_prev_txn = 0
    over_cur_rev = over_prev_rev = 0.0
    for store in stores:
        b = base.get(store, {"cats": {}, "txns": 0, "other_units": 0})
        c = comp.get(store, {"cats": {}, "txns": 0, "other_units": 0})
        mkt = resolve_market(store) if resolve_market else ""
        over_cur_txn += b["txns"]
        over_prev_txn += c["txns"]
        s_cur_rev = s_prev_rev = 0.0
        for k in sale_keys:
            cm = _combine(b["cats"].get(k), c["cats"].get(k))
            # drop a store×category with zero activity in BOTH windows (keeps the table honest + small)
            if cm["current"] == 0 and cm["previous"] == 0:
                continue
            row = dict(cm)
            row.update({"store": store or "—", "market": mkt,
                        "category_key": k, "category": cat_label[k], "metric": cat_metric[k]})
            rows.append(row)
            tb = cat_tot[k]
            tb["cur"]["units"] += b["cats"].get(k, _blank_metric())["units"]
            tb["cur"]["revenue"] += b["cats"].get(k, _blank_metric())["revenue"]
            tb["cur"]["gp"] += b["cats"].get(k, _blank_metric())["gp"]
            tb["prev"]["units"] += c["cats"].get(k, _blank_metric())["units"]
            tb["prev"]["revenue"] += c["cats"].get(k, _blank_metric())["revenue"]
            tb["prev"]["gp"] += c["cats"].get(k, _blank_metric())["gp"]
            s_cur_rev += b["cats"].get(k, _blank_metric())["revenue"]
            s_prev_rev += c["cats"].get(k, _blank_metric())["revenue"]
        over_cur_rev += s_cur_rev
        over_prev_rev += s_prev_rev
        store_summ.append({
            "store": store or "—", "market": mkt,
            "current_txns": b["txns"], "previous_txns": c["txns"],
            "txns_delta": b["txns"] - c["txns"], "txns_pct": _pct(b["txns"], c["txns"]),
            "current_rev": round(s_cur_rev, 2), "previous_rev": round(s_prev_rev, 2),
            "rev_pct": _pct(s_cur_rev, s_prev_rev),
        })

    # ── STORE EXPENSES, folded on the store-identity key, reported ONCE per store ─────────────────
    exp_rows, exp_merges = [], {}
    exp_cur_tot = exp_prev_tot = 0.0
    # None = no expenses were handed in at all; True/False = the verdict below, computed ONCE and
    # read by the rows, the category total and the payload meta so the three cannot disagree.
    exp_measured = None
    if expenses is not None:
        fold = store_key_of or (lambda v: str(v or "").strip())
        exp_base = {str(k): float(v or 0.0) for k, v in (expenses.get("base") or {}).items()}
        exp_cmp = {str(k): float(v or 0.0) for k, v in (expenses.get("compare") or {}).items()}
        groups = {}
        for store in stores:
            groups.setdefault(fold(store), []).append(store)
        # A month "contributed its own labour" when at least one half of it was measured from that
        # month's own payroll — read from the SAME `labour` map the meta reports, so the predicate
        # and the note on screen can never disagree about which months were measured.
        _own_labour = {p: any((((expenses.get("labour") or {}).get(p) or {}).get("measured")
                               or {}).values())
                       for p in (base_period, compare_period)}
        exp_measured = expense_change_measured(expenses.get("base_carried_from"),
                                               expenses.get("compare_carried_from"),
                                               _own_labour.get(base_period, False),
                                               _own_labour.get(compare_period, False))
        label_of, market_of = {}, {}
        for ckey, spellings in groups.items():
            uniq = sorted(set(spellings))
            # One sale spelling → label the row with it, so the expense lands in that store's group in
            # the table. Several → the canonical key, and the spellings are NAMED rather than summed
            # over (one month of rent must not be reported once per spelling).
            label_of[ckey] = uniq[0] if len(uniq) == 1 else (ckey or "—")
            market_of[ckey] = (resolve_market(uniq[0]) if resolve_market else "") or ""
            if len(uniq) > 1:
                exp_merges[ckey] = uniq
        for ckey in sorted(set(exp_base) | set(exp_cmp)):
            cur, prev = exp_base.get(ckey, 0.0), exp_cmp.get(ckey, 0.0)
            cell = _expense_cell(cur, prev)
            if not exp_measured:
                # The two figures are ONE carried sheet compared with itself. They stay — they are
                # what each month books — but the change is NOT MEASURED, never a ± 0% that reads as
                # "steady". `rev_delta` stays 0.00 because that IS the arithmetic; the percent is the
                # claim, so the percent is the thing withheld.
                cell["rev_pct"] = None
                cell["pct"] = None
                cell["change_measured"] = False
            cell.update({
                "store": label_of.get(ckey) or ckey or "—",
                "market": market_of.get(ckey) or (resolve_market(ckey) if resolve_market else "") or "",
                "category_key": EXPENSE_KEY, "category": EXPENSE_LABEL, "metric": "dollars",
                "expense": True,
            })
            exp_rows.append(cell)
            exp_cur_tot += cell["current_rev"]
            exp_prev_tot += cell["previous_rev"]
        rows.extend(exp_rows)
        # the per-store roll-up carries the expense beside the revenue it was spent to earn
        by_label = {r["store"]: r for r in exp_rows}
        for ss in store_summ:
            r = by_label.pop(ss["store"], None)
            ss["current_exp"] = r["current_rev"] if r else None
            ss["previous_exp"] = r["previous_rev"] if r else None
            ss["exp_pct"] = r["rev_pct"] if r else None
        # a store that spent money and sold nothing in BOTH windows is still a store that spent money
        for lbl, r in sorted(by_label.items()):
            store_summ.append({
                "store": lbl, "market": r["market"],
                "current_txns": 0, "previous_txns": 0, "txns_delta": 0, "txns_pct": None,
                "current_rev": 0.0, "previous_rev": 0.0, "rev_pct": None,
                "current_exp": r["current_rev"], "previous_exp": r["previous_rev"],
                "exp_pct": r["rev_pct"],
            })

    totals_by_category = []
    for c in cats:
        k = c["key"]
        if c.get("expense"):
            m = _expense_cell(exp_cur_tot, exp_prev_tot)
            if expenses is not None and not exp_measured:
                m["rev_pct"] = None
                m["pct"] = None
                m["change_measured"] = False
        else:
            m = _combine(cat_tot[k]["cur"], cat_tot[k]["prev"])
        m.update({"key": k, "label": c["label"], "metric": c.get("metric", "units"),
                  "financing": bool(c.get("financing")), "expense": bool(c.get("expense")),
                  "detection_status": c.get("detection_status")})
        totals_by_category.append(m)

    rows.sort(key=lambda r: (r["store"], cat_keys.index(r["category_key"])))
    store_summ.sort(key=lambda r: -(r["current_txns"] + r["previous_txns"]))

    out = {
        "base_period": base_period,
        "compare_period": compare_period,
        "mode": mode,
        "window_label": window_label,
        "categories": cats,
        "totals_by_category": totals_by_category,
        "rows": rows,
        "store_summary": store_summ,
        "overall": {
            "current_txns": over_cur_txn, "previous_txns": over_prev_txn,
            "txns_delta": over_cur_txn - over_prev_txn, "txns_pct": _pct(over_cur_txn, over_prev_txn),
            "current_rev": round(over_cur_rev, 2), "previous_rev": round(over_prev_rev, 2),
            "rev_pct": _pct(over_cur_rev, over_prev_rev),
        },
        "params": params or {},
        "note": (None if rows else
                 "No sales in either window. Sales come from the imported Sales Transaction Details — "
                 "check the month(s), or that the daily feed / monthly upload has loaded."),
    }
    # The expense side says out loud what it measured and whether either month was CARRIED FORWARD —
    # a carried month is the P&L's own rule, not an estimate, but the reader is told which month it
    # came from rather than left to assume the sheet was re-entered.
    _exp_change_measured = (None if expenses is None else exp_measured)
    out["expenses_meta"] = (None if expenses is None else {
        "measured": True,
        # measured = the expense FIGURES were read. change_measured = the two figures are two
        # different months, so a change between them is a claim worth printing. The second can be
        # False while the first is True, and that is exactly the case this report used to misreport.
        "change_measured": bool(_exp_change_measured),
        "current_total": round(exp_cur_tot, 2),
        "previous_total": round(exp_prev_tot, 2),
        "pct": (_pct(round(exp_cur_tot, 2), round(exp_prev_tot, 2)) if _exp_change_measured else None),
        "base_carried_from": expenses.get("base_carried_from"),
        "compare_carried_from": expenses.get("compare_carried_from"),
        "stores": len(exp_rows),
        "store_merges": exp_merges,
        # WHAT THE EXPENSE FIGURE IS MADE OF (owner directive 2026-10-20). `labour` is each month's
        # OWN salary and commission, from the homes that derive them, with `measured` per half; the
        # `*_excluded` maps name what the carried sheet was not allowed to hand over. A half that was
        # not measured must be SAID on the surface — a month missing its payroll is an incomplete
        # month, never a cheaper one (§19.49).
        "labour": expenses.get("labour") or {},
        "base_excluded": expenses.get("base_excluded") or {},
        "compare_excluded": expenses.get("compare_excluded") or {},
        "labour_measured": all(bool(v) for m in (expenses.get("labour") or {}).values()
                               for v in ((m or {}).get("measured") or {"_": True}).values()),
    })
    out["overall"]["current_exp"] = round(exp_cur_tot, 2) if expenses is not None else None
    out["overall"]["previous_exp"] = round(exp_prev_tot, 2) if expenses is not None else None
    out["overall"]["exp_pct"] = (_pct(round(exp_cur_tot, 2), round(exp_prev_tot, 2))
                                 if (expenses is not None and _exp_change_measured) else None)
    return out
