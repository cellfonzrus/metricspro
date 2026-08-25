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

from app.modules.commcalc.calculator import safe_float, classify_contract_type
from app.modules.commcalc import installment_category as _icat
from app.modules.commcalc import financing_registry as _finreg

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


def category_defs(vendors):
    """The ordered list of category keys + labels + primary metric this report tracks. OWNER ORDER +
    METRIC (exact): Phones (units), BYOD (units), Activation (units), Tablets (units), Accessories ($),
    Financing (units AND $). `vendors` is the ALREADY carrier-scoped vendor list — the single Financing
    row is present whenever the tenant has any financing vendor for the active carrier. PURE."""
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
    return defs


def _blank_metric():
    return {"units": 0, "revenue": 0.0, "gp": 0.0}


def tally(rows, rules, is_accessory, vendors):
    """One window → {store: {cat_key: {units, revenue, gp}}}, plus a `txns` count and `other_units`
    (home-internet / SIM / unclassified device sales, surfaced for honesty). PURE — `is_accessory(row)`
    is injected. `rules` = installment_category.load_category_rules(...); `vendors` =
    financing_registry.resolve_vendors(...)."""
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

        # ── BYOD (independent tally, like the Sales Report's BYOD column) ──
        if any(classify_contract_type(ln.get("contract_type")) == "byod" for ln in lines):
            cats.setdefault("byod", _blank_metric())["units"] += 1

        # ── Activation (independent tally): a PREMIUM new-line activation on the receipt. Same shared
        #    classify_contract_type the commission engine / Sales Report use; distinct-txn count. ──
        if any(classify_contract_type(ln.get("contract_type")) == "premium" for ln in lines):
            cats.setdefault("activation", _blank_metric())["units"] += 1

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


def build(base_rows, cmp_rows, rules, is_accessory, vendors, *,
          base_period, compare_period, mode, window_label, resolve_market=None, params=None):
    """The whole report payload. PURE apart from the injected `resolve_market(store)`.

    Returns per-(store × category) rows (each with current / previous / Δ / Δ%), a totals-by-category
    roll-up for the summary tiles, and an overall transactions/revenue/gp comparison.
    """
    cats = category_defs(vendors)
    cat_keys = [c["key"] for c in cats]
    cat_label = {c["key"]: c["label"] for c in cats}
    cat_metric = {c["key"]: c.get("metric", "units") for c in cats}

    base = tally(base_rows, rules, is_accessory, vendors)
    comp = tally(cmp_rows, rules, is_accessory, vendors)
    stores = sorted(set(base) | set(comp))

    rows = []
    cat_tot = {k: {"cur": _blank_metric(), "prev": _blank_metric()} for k in cat_keys}
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
        for k in cat_keys:
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

    totals_by_category = []
    for c in cats:
        k = c["key"]
        m = _combine(cat_tot[k]["cur"], cat_tot[k]["prev"])
        m.update({"key": k, "label": c["label"], "metric": c.get("metric", "units"),
                  "financing": bool(c.get("financing")),
                  "detection_status": c.get("detection_status")})
        totals_by_category.append(m)

    rows.sort(key=lambda r: (r["store"], cat_keys.index(r["category_key"])))
    store_summ.sort(key=lambda r: -(r["current_txns"] + r["previous_txns"]))

    return {
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
