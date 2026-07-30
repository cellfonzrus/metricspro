"""Marketplace Handset COGS report — PURE, testable aggregation (no DB, no FastAPI, no HTTP).

WHAT IT ANSWERS. "What did the handsets we ordered from the marketplace COST us — by product, by month,
by ship-to — and which orders are still open?" The source is `commcalc.raw_ma_fulfillment` (mig 083, the
"MA - Marketplace Handset Fulfillment Orders" report), one row per ORDER LINE, carrying `number_ordered`
(qty) and `price` (the per-unit handset price). Extended cost = **qty × unit price**.

WHY `price` IS READ AS A UNIT PRICE. Device History already reads this exact column as the per-DEVICE
purchase price for one IMEI (`device_history.pick_ma_marketplace_price`: imei → activation_order →
raw_ma_fulfillment.order_number → .price), so unit-pricing is the interpretation the codebase already
depends on. Because a tenant's export could conceivably publish a LINE total instead, the basis is a
REPORT PARAMETER (`price_basis` = 'unit' | 'line') rather than a buried constant, it is stated on the page
and in every export subtitle, and the count of lines with qty > 1 (the only lines where the two bases
differ) is reported out loud. No guessing, no silent double-count.

CARRIER- AND TENANT-AGNOSTIC (AGENT_CONTRACT §3 / RULE TWO). Nothing here branches on a tenant, carrier or
store name: an org either has marketplace fulfillment rows or it does not. Markets come from the org's own
/store-match chain (the resolver is injected by the router — `_ir_store_resolver`), never from a hard-coded
map, and everything the resolver cannot place lands in the EXPLICIT, SELECTABLE `(no market)` bucket.

READ-ONLY / NOT MONEY-TOUCHING. This is a COST report over what the distributor already invoiced. It reads
no rate, tier, plan rule or payout, writes nothing at all (in particular it never writes `asset_ledger`),
and no recompute is reachable from it. Wiring these costs INTO a unified device-cost ledger or the P&L
would be money-touching and is deliberately left as a design note for the owner (docs/designs/).

ORDER STATE. The feed's `order_status` vocabulary is small but tenant-visible, so state is derived from
BOTH the dates and the status text, and the raw statuses are always exposed as a facet + counted per
state, so a mis-bucketed status is visible on the page instead of hidden in a sum:
  fulfilled  — a fill or ship date exists, or the status says filled/shipped/complete/delivered
  cancelled  — the status says cancelled/void/rejected/returned  (kept, shown, and EXCLUDED from committed
               COGS: a cancelled order is not a cost)
  open       — everything else with no fill/ship date (the "still owed to us" bucket)
The keyword sets are function arguments with sane defaults (an operator override reaches them through the
endpoint), never constants baked into the math.
"""

from app.modules.commcalc import imei_rebate_report as _irr   # pure period/date helpers — reused, not re-written

# Explicit, SELECTABLE buckets. A filter must never make rows vanish into a hole nobody can see
# (the retail-ops B1 lesson) — so "no market" / "no product" / "no ship-to" are named answers, and the
# underlying row keeps its blank value so an export still exports blank.
NO_MARKET = "(no market)"
NO_PRODUCT = "(no product name)"
NO_SHIP_TO = "(no ship-to)"

# Order-state vocabularies. Substring match on a casefolded status; overridable per call.
FULFILLED_WORDS = ("filled", "fulfilled", "shipped", "complete", "completed", "delivered", "received")
CANCELLED_WORDS = ("cancel", "canceled", "cancelled", "void", "reject", "denied", "returned", "refund")

STATE_LABEL = {"open": "Open / unfulfilled", "fulfilled": "Fulfilled", "cancelled": "Cancelled"}
PRICE_BASIS = ("unit", "line")


def _s(v):
    """Trimmed string or None (never the string 'None')."""
    s = str(v).strip() if v is not None else ""
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    return s


def _fold(v):
    return str(v or "").strip().lower()


def to_num(v):
    """A float from a spreadsheet-ish value ('$1,234.50', '2', 2.0, '', None) or None. Never raises;
    an unparseable value is None (NOT 0) so 'missing' and 'zero' can be told apart in the notes."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


# Re-exported period helpers so callers (the router) never reach into another module's internals for the
# 'June 2026' vs '2026-06' duality — one spelling-resolution path, shared.
period_ym = _irr.period_ym
canon_period = _irr.canon_period
parse_date = _irr.parse_loose_date


def month_of(d):
    """'YYYY-MM' for a date-ish value, or None (an unparseable date is never guessed into a month)."""
    d10 = _irr.parse_loose_date(d)
    return d10[:7] if d10 else None


def month_label(ym):
    """'2026-06' → 'June 2026' (the canonical display spelling), or the input when unparseable."""
    return _irr.canon_period(ym) or ym


def order_state(status, date_filled=None, date_shipped=None,
                fulfilled_words=FULFILLED_WORDS, cancelled_words=CANCELLED_WORDS):
    """(state, reason) for one fulfillment line. Dates WIN over status text — a line that shipped is
    fulfilled whatever the status column says — except that an explicit cancellation is honoured even
    with dates present (a shipped-then-returned line is not a committed cost).

    Precedence: cancelled → fulfilled(date) → fulfilled(status) → open. A blank status with no dates is
    OPEN and says so ('no fill or ship date'), which is exactly the bucket this report exists to surface.
    """
    st = _fold(status)
    filled, shipped = _irr.parse_loose_date(date_filled), _irr.parse_loose_date(date_shipped)
    if st and any(w in st for w in cancelled_words):
        return "cancelled", f"status “{str(status).strip()}”"
    if filled or shipped:
        return "fulfilled", ("filled " + filled if filled else "shipped " + shipped)
    if st and any(w in st for w in fulfilled_words):
        return "fulfilled", f"status “{str(status).strip()}”"
    return "open", (f"status “{str(status).strip()}” · no fill or ship date" if st
                    else "no fill or ship date")


def is_opaque_id(v):
    """True for a key that is ONLY digits (e.g. TSPID '1800'). Such a value is an account/dealer id, not
    a store name or address, and it is NOT safe to push through the /store-match address chain: the
    chain's last resort matches a LEADING STREET NUMBER, so a numeric dealer id '1800' silently resolves
    to '1800 Great Neck Rd' and inherits that store's market. Proven in the harness."""
    s = str(v or "").strip()
    return bool(s) and s.isdigit()


def line_from_row(row, store_of=None, price_basis="unit", resolve_opaque_tspid=False,
                  fulfilled_words=FULFILLED_WORDS, cancelled_words=CANCELLED_WORDS):
    """One `raw_ma_fulfillment` row → the normalized report line.

    qty × unit price is computed HERE and nowhere else:
      price_basis 'unit' (default) → ext_cost = qty × price
      price_basis 'line'           → ext_cost = price          (unit_price = price / qty, when qty > 0)
    A missing qty is treated as 1 for the EXTENSION (an order line always ordered something) but is
    reported as `qty_assumed` so the assumption is visible; a missing price yields ext_cost None, which
    is counted as a priceless line rather than silently summed as $0.

    SHIP-TO resolution (RULE TWO — reuses the org's existing /store-match chain via `store_of`):
    business_address → business_name → tspid, first key that resolves wins. `ship_to` is the raw label
    the feed gave (traceable back to the source cell), `ship_to_label` is the canonical store name when
    the org's mapping knows it, and `market` is None when nothing resolves (→ the `(no market)` bucket).

    A PURELY NUMERIC TSPID is deliberately NOT resolved (`resolve_opaque_tspid=False`): the address chain
    would match it against a store's leading STREET NUMBER and hand the line a plausible-but-wrong market
    (see `is_opaque_id`). Those lines land in the visible `(no market)` bucket instead — and in the normal
    case the feed's own business address/name resolves them anyway, since TSPID is only the third key
    tried. The keyword argument exists so an org that genuinely keys its stores by that id can turn it on
    without a code change.
    """
    qty_raw = to_num(row.get("number_ordered"))
    price = to_num(row.get("price"))
    qty_assumed = qty_raw is None
    qty = 1.0 if qty_assumed else float(qty_raw)
    basis = price_basis if price_basis in PRICE_BASIS else "unit"
    if price is None:
        unit_price, ext_cost = None, None
    elif basis == "line":
        ext_cost = float(price)
        unit_price = (float(price) / qty) if qty else None
    else:
        unit_price = float(price)
        ext_cost = float(price) * qty

    addr, biz, tspid = _s(row.get("business_address")), _s(row.get("business_name")), _s(row.get("tspid"))
    label, market, matched_on = None, None, None
    if store_of:
        for key, kind in ((addr, "business_address"), (biz, "business_name"), (tspid, "tspid")):
            if not key:
                continue
            if kind == "tspid" and is_opaque_id(key) and not resolve_opaque_tspid:
                continue
            try:
                lbl, mkt = store_of(key)
            except Exception:                        # a resolver hiccup must never lose the line
                lbl, mkt = None, None
            if lbl or mkt:
                label, market, matched_on = (lbl or label), (mkt or market), kind
                if market:
                    break
    raw_ship_to = addr or biz or tspid
    state, state_reason = order_state(row.get("order_status"), row.get("date_filled"),
                                      row.get("date_shipped"), fulfilled_words, cancelled_words)
    d_ordered = _irr.parse_loose_date(row.get("date_ordered"))
    return {
        "id": row.get("id"),
        "order_number": _s(row.get("order_number")),
        "order_status": _s(row.get("order_status")),
        "order_type": _s(row.get("order_type")),
        "product": _s(row.get("product_name")),
        "product_label": _s(row.get("product_name")) or NO_PRODUCT,
        "qty": qty, "qty_assumed": qty_assumed,
        "unit_price": unit_price, "price_raw": price, "ext_cost": ext_cost,
        "price_basis": basis,
        "date_ordered": d_ordered,
        "date_filled": _irr.parse_loose_date(row.get("date_filled")),
        "date_shipped": _irr.parse_loose_date(row.get("date_shipped")),
        "month": month_of(row.get("date_ordered")),
        "month_label": month_label(month_of(row.get("date_ordered"))) if d_ordered else None,
        "ship_to": raw_ship_to,
        "ship_to_label": label or raw_ship_to or NO_SHIP_TO,
        "ship_to_matched_on": matched_on,
        "business_name": biz, "business_address": addr, "tspid": tspid,
        "city": _s(row.get("city")), "state_code": _s(row.get("state")), "zip": _s(row.get("zip")),
        "market": market,
        "tracking_number": _s(row.get("tracking_number")),
        "state": state, "state_label": STATE_LABEL.get(state, state), "state_reason": state_reason,
        "is_open": state == "open",
    }


def build_rows(rows, store_of=None, price_basis="unit", today=None, resolve_opaque_tspid=False,
               fulfilled_words=FULFILLED_WORDS, cancelled_words=CANCELLED_WORDS):
    """Normalize every fulfillment row and attach `days_open` (age of an OPEN line as of `today`).
    Sorted OPEN-first (the actionable bucket), then newest order date, then biggest cost — the same
    "gaps are the product" ordering the IMEI-rebate report uses."""
    out = []
    for r in rows or []:
        ln = line_from_row(r, store_of=store_of, price_basis=price_basis,
                           resolve_opaque_tspid=resolve_opaque_tspid,
                           fulfilled_words=fulfilled_words, cancelled_words=cancelled_words)
        ln["days_open"] = days_between(ln["date_ordered"], today) if ln["is_open"] else None
        out.append(ln)
    out.sort(key=lambda r: (0 if r["is_open"] else 1,
                            _neg_date(r["date_ordered"]),
                            -(r["ext_cost"] or 0)))
    return out


def _neg_date(d10):
    """Sort key that puts the NEWEST date first while keeping blanks last."""
    return "" if not d10 else "".join(chr(255 - ord(c)) if c.isdigit() else c for c in d10)


def days_between(d10, today=None):
    """Whole days from `d10` to `today` ('YYYY-MM-DD' or a date/datetime), or None when either is
    unusable. Never negative-clamped — a future order date honestly reads negative."""
    from datetime import date as _date
    a = _irr.parse_loose_date(d10)
    if not a:
        return None
    if today is None:
        b = _date.today()
    elif isinstance(today, str):
        t = _irr.parse_loose_date(today)
        if not t:
            return None
        b = _date(int(t[:4]), int(t[5:7]), int(t[8:10]))
    else:
        b = today
    return (b - _date(int(a[:4]), int(a[5:7]), int(a[8:10]))).days


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# FILTERS + OPTIONS (pick-don't-type, from the values PRESENT IN THE DATA — RULE THREE/FIVE).
# Every filter is applied here, server-side, so tiles ≡ table ≡ export (RULE FOUR's WYSIWYG).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _sel(csv):
    if isinstance(csv, (list, tuple, set)):
        return {str(x).strip().lower() for x in csv if str(x).strip()}
    return {s.strip().lower() for s in str(csv or "").split(",") if s.strip()}


def market_match(row, selection):
    """Does `row` satisfy a market selection? The `(no market)` sentinel matches rows whose market did
    not resolve — that bucket is a real, selectable answer, never a silent drop."""
    if not selection:
        return True
    mk = _fold(row.get("market"))
    if mk and mk in selection:
        return True
    return (not mk) and (NO_MARKET.lower() in selection)


def _label_match(row, field, label_field, sentinel, selection):
    """Match on the display label, with the sentinel bucket ('(no product name)' / '(no ship-to)')
    matching rows whose underlying value is blank."""
    if not selection:
        return True
    v = _fold(row.get(field))
    if v and v in selection:
        return True
    lbl = _fold(row.get(label_field))
    if lbl and lbl in selection:
        return True
    return (not v) and (sentinel.lower() in selection)


def unresolved_market_count(rows):
    """Size of the `(no market)` bucket — drives both the filter option and the 'map these at
    /store-match' prompt on the page."""
    return sum(1 for r in rows or [] if not _s(r.get("market")))


def filter_options(rows):
    """Option lists for the standard bar + the appended facets, taken from the values PRESENT IN THE
    DATA (never a hard-coded list). Case-variant spellings collapse to one option, first casing wins."""
    def _opts(get):
        seen = {}
        for r in rows or []:
            v = get(r)
            if v is None:
                continue
            s = str(v).strip()
            if s:
                seen.setdefault(s.lower(), s)
        return sorted(seen.values(), key=lambda s: s.lower())

    markets = _opts(lambda r: r.get("market"))
    if unresolved_market_count(rows) > 0:
        markets = markets + [NO_MARKET]
    products = _opts(lambda r: r.get("product"))
    if any(not _s(r.get("product")) for r in rows or []):
        products = products + [NO_PRODUCT]
    ship_tos = _opts(lambda r: r.get("ship_to_label") or r.get("ship_to"))
    if any(not _s(r.get("ship_to")) for r in rows or []):
        ship_tos = ship_tos + [NO_SHIP_TO]
    months = sorted({r["month"] for r in rows or [] if r.get("month")})
    states = [{"id": s, "label": STATE_LABEL[s]} for s in ("open", "fulfilled", "cancelled")
              if any(r.get("state") == s for r in rows or [])]
    return {
        "product_options": products,
        "ship_to_options": ship_tos,
        "market_options": markets,
        "status_options": _opts(lambda r: r.get("order_status")),
        "order_type_options": _opts(lambda r: r.get("order_type")),
        "state_options": states,
        "month_options": [{"id": m, "label": month_label(m)} for m in months],
    }


def apply_filters(rows, *, products="", ship_to="", markets="", statuses="", order_types="",
                  states="", months="", open_only=False, min_days_open=0):
    """Narrow the lines by the standard set (ship-to / market — the fulfillment feed's store dimension)
    plus the appended facets (product · order status · order type · state · month) and the two
    open-order controls. Comparisons are case-insensitive on the value PRESENT IN THE ROW; a blank
    selection means 'no narrowing'; `open_only` is the same thing as states=['open'] but survives as its
    own toggle because it is the report's headline question."""
    pr, sh, mk = _sel(products), _sel(ship_to), _sel(markets)
    st, ot, stt, mo = _sel(statuses), _sel(order_types), _sel(states), _sel(months)
    try:
        min_days = max(0, int(min_days_open or 0))
    except (TypeError, ValueError):
        min_days = 0
    out = []
    for r in rows or []:
        if not _label_match(r, "product", "product_label", NO_PRODUCT, pr):
            continue
        if not _label_match(r, "ship_to", "ship_to_label", NO_SHIP_TO, sh):
            continue
        if not market_match(r, mk):
            continue
        if st and _fold(r.get("order_status")) not in st:
            continue
        if ot and _fold(r.get("order_type")) not in ot:
            continue
        if stt and _fold(r.get("state")) not in stt:
            continue
        if mo and _fold(r.get("month")) not in mo:
            continue
        if open_only and not r.get("is_open"):
            continue
        if min_days and not (r.get("is_open") and (r.get("days_open") or 0) >= min_days):
            continue
        out.append(r)
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# TILES + GROUPING
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _agg(rows):
    """(lines, orders, units, cogs, priceless_lines) over a row list. COGS sums ext_cost only where a
    price exists; a priceless line is COUNTED, never summed as $0."""
    units = 0.0
    cogs = 0.0
    priceless = 0
    orders = set()
    for r in rows:
        units += float(r.get("qty") or 0)
        if r.get("ext_cost") is None:
            priceless += 1
        else:
            cogs += float(r["ext_cost"])
        if r.get("order_number"):
            orders.add(_fold(r["order_number"]))
    return len(rows), len(orders), round(units, 4), round(cogs, 2), priceless


def tiles_for(rows):
    """Headline numbers over the FILTERED set (so the tiles can never disagree with the table).

    COMMITTED COGS deliberately EXCLUDES cancelled lines — a cancelled order is not a cost — and the
    cancelled bucket is reported separately so the exclusion is visible rather than a silent deduction.
    """
    rows = list(rows or [])
    live = [r for r in rows if r.get("state") != "cancelled"]
    op = [r for r in rows if r.get("state") == "open"]
    fu = [r for r in rows if r.get("state") == "fulfilled"]
    ca = [r for r in rows if r.get("state") == "cancelled"]
    l_all, o_all, u_all, c_all, pl = _agg(live)
    _, o_op, u_op, c_op, _ = _agg(op)
    _, o_fu, u_fu, c_fu, _ = _agg(fu)
    _, o_ca, u_ca, c_ca, _ = _agg(ca)
    avg = round(c_all / u_all, 2) if u_all else None
    return {
        "lines": l_all, "orders": o_all, "units": u_all, "cogs": c_all,
        "avg_unit_cost": avg,
        "open": {"lines": len(op), "orders": o_op, "units": u_op, "amount": c_op},
        "fulfilled": {"lines": len(fu), "orders": o_fu, "units": u_fu, "amount": c_fu},
        "cancelled": {"lines": len(ca), "orders": o_ca, "units": u_ca, "amount": c_ca},
        "products": len({_fold(r.get("product")) for r in live if r.get("product")}),
        "ship_tos": len({_fold(r.get("ship_to_label")) for r in live if r.get("ship_to_label")}),
        "priceless_lines": pl,
        "multi_unit_lines": sum(1 for r in live if float(r.get("qty") or 0) > 1),
        "oldest_open_days": max([r.get("days_open") for r in op if r.get("days_open") is not None],
                                default=None),
    }


GROUP_BY = ("product", "month", "ship_to", "market", "order_type", "status", "state", "order")
GROUP_LABEL = {"product": "Product", "month": "Month", "ship_to": "Ship-to",
               "market": "Market", "order_type": "Order type", "status": "Order status",
               "state": "State", "order": "Order number"}


def _group_key(row, group_by):
    if group_by == "product":
        return (_fold(row.get("product")), row.get("product_label") or NO_PRODUCT)
    if group_by == "month":
        return (row.get("month") or "", row.get("month_label") or "(no order date)")
    if group_by == "ship_to":
        return (_fold(row.get("ship_to_label")), row.get("ship_to_label") or NO_SHIP_TO)
    if group_by == "market":
        return (_fold(row.get("market")), row.get("market") or NO_MARKET)
    if group_by == "order_type":
        return (_fold(row.get("order_type")), row.get("order_type") or "(no order type)")
    if group_by == "status":
        return (_fold(row.get("order_status")), row.get("order_status") or "(no status)")
    if group_by == "state":
        return (row.get("state") or "", row.get("state_label") or "")
    return (_fold(row.get("order_number")), row.get("order_number") or "(no order number)")


def group_rows(rows, group_by="product"):
    """Aggregate the FILTERED lines by one dimension → [{key, label, lines, orders, units, cogs,
    avg_unit_cost, open_units, open_cogs, cancelled_cogs, first_order, last_order}], biggest COGS first.
    Cancelled lines are excluded from a group's `cogs` for the same reason as the tiles, and surfaced in
    `cancelled_cogs`, so a group total + the cancelled column always reconcile to the raw rows."""
    gb = group_by if group_by in GROUP_BY else "product"
    buckets = {}
    for r in rows or []:
        k, label = _group_key(r, gb)
        b = buckets.setdefault(k, {"key": k, "label": label, "rows": []})
        b["rows"].append(r)
    out = []
    for b in buckets.values():
        rs = b["rows"]
        live = [r for r in rs if r.get("state") != "cancelled"]
        op = [r for r in rs if r.get("state") == "open"]
        ca = [r for r in rs if r.get("state") == "cancelled"]
        lines, orders, units, cogs, priceless = _agg(live)
        _, _, u_op, c_op, _ = _agg(op)
        _, _, _, c_ca, _ = _agg(ca)
        dates = sorted([r["date_ordered"] for r in rs if r.get("date_ordered")])
        out.append({
            "key": b["key"], "label": b["label"],
            "lines": len(rs), "orders": orders, "units": units, "cogs": cogs,
            "avg_unit_cost": (round(cogs / units, 2) if units else None),
            "open_lines": len(op), "open_units": u_op, "open_cogs": c_op,
            "cancelled_lines": len(ca), "cancelled_cogs": c_ca,
            "priceless_lines": priceless,
            "first_order": (dates[0] if dates else None), "last_order": (dates[-1] if dates else None),
        })
    out.sort(key=lambda g: (-(g["cogs"] or 0), g["label"].lower()))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# HONEST HEADER TEXT — the definition the report states out loud, carried into every export subtitle
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def basis_note(price_basis, tiles=None):
    """How extended cost was computed, and whether the choice can even matter for this data."""
    multi = (tiles or {}).get("multi_unit_lines")
    if price_basis == "line":
        base = ("Extended cost = the feed's Price column taken as a LINE TOTAL; unit cost is derived "
                "(price ÷ qty).")
    else:
        base = ("Extended cost = Number Ordered × Price, reading Price as the PER-UNIT handset price — "
                "the same column Device History reads as a device's purchase price.")
    if multi == 0:
        return base + " Every line here has qty 1, so the unit/line basis makes no difference to these totals."
    if multi:
        return base + f" {multi} line(s) have qty > 1 — those are the only lines the basis changes."
    return base


def open_note():
    return ("An order line is OPEN when no fill or ship date has arrived and its status is not a "
            "fulfilled/cancelled one. Open lines are handsets paid for (or committed) but not yet in "
            "hand — the actionable bucket, listed first.")


def definition_note(price_basis="unit"):
    return ("One row per marketplace handset ORDER LINE from the master-agent fulfillment feed "
            "(raw_ma_fulfillment). " + basis_note(price_basis) + " Cancelled lines are shown but "
            "excluded from committed COGS. This is a COST report: nothing here changes anyone's pay.")


# ── the PAGE gate (owner-approved 2026-07-29: this report has NO default access) ──────────────────
GRANT_KEY = "ma_handset_cogs"


def ma_handset_cogs_allowed(caller):
    """Gate the WHOLE REPORT. DEFAULT-CLOSED, grantable via the DATA_GRANTS 'ma_handset_cogs' key — the
    same resolution SHAPE as `imei_rebate_report.imei_rebates_allowed` / `device_history.
    device_commission_allowed`: what the dealer PAYS for inventory is commercially sensitive, so the
    lines, quantities and costs are all restricted, not merely the totals.

    PURE over an already-resolved caller dict (no DB, no HTTP) so it is unit-provable:
      super_admin / perms.scope == 'all' / role == 'admin'                    -> allow
      'ma_handset_cogs' in perms.modules, or perms.data.ma_handset_cogs truthy -> allow
      else (including caller=None, i.e. an unresolvable token)                -> DENY

    Frontend mirror: `hasDataGrant(perms, 'ma_handset_cogs')`.
    """
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    if (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin"):
        return True
    if GRANT_KEY in (perms.get("modules") or []):
        return True
    if bool((perms.get("data") or {}).get(GRANT_KEY)):
        return True
    return False
