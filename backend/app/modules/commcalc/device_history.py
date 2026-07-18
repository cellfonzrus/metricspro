"""Device history lookup (commission-16) — PURE, testable helpers for the employee-portal widget.

Given an IMEI or phone number, the widget shows:
  1. activation + tenure from the tenant's RESIDUAL data (raw_mi): first period the line appears =
     activation; months active = COUNT of DISTINCT residual periods (owner doctrine: residual-months,
     NOT calendar months) — the assumption is surfaced visibly.
  2. device + sale from the B2B sales data (raw_sales, fallback daily_sales_feed): phone model, sold
     date, sale price. Match IMEI -> serial_1, phone -> mdn.
  3. a salesperson-facing prompt: NOT sold by us -> sell a NEW phone; sold by us -> offer an UPGRADE.
  4. a per-period MONEY table with COMMISSION and REBATE as SEPARATE categories (never blended), each
     with its own subtotal + a grand total. Commission = raw_mi residual (MI+ATU); rebate =
     raw_payment_detail reimbursement classes. Non-rebate payment-detail rows are EXCLUDED (noted) so
     the two feeds never double-count.

DISPLAY of already-recorded data only — there is no pay-path here. These functions are dependency-free
(no DB, no dateutil) so the router composes them and the proof harness drives them directly. The router
supplies the DB rows and the payment_type->comp_type classifier (reused from
discrepancy_engine.parse_payment_type)."""

import re
from datetime import date

_MONTH_NAME = ["", "January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]
_MONTHS = {m.lower(): i for i, m in enumerate(_MONTH_NAME) if m}


# ── input-token normalization + shape detection ─────────────────────────────────────────────────────
def norm_digits(v):
    """Digits-only form of a token (IMEI / MSISDN)."""
    return re.sub(r"\D", "", "" if v is None else str(v))


def norm_key(v):
    """Loose key normalization for matching a stored serial/mdn: strip a trailing '.0' + whitespace
    (mirrors the importer's mdn cleanup) while preserving any non-digit serial characters."""
    s = ("" if v is None else str(v)).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def detect_shape(q):
    """Classify a lookup token by SHAPE — a hint for labeling only; the search matches BOTH the
    serial/imei key AND the mdn/phone key regardless. 14-16 digits -> 'imei'; 10-11 -> 'phone'."""
    d = norm_digits(q)
    if len(d) >= 14:
        return "imei"
    if 10 <= len(d) <= 11:
        return "phone"
    return "unknown"


def query_candidates(q):
    """The stored-value spellings a query could match on (exact-match candidates for an .in_() filter):
    the raw token, the '.0'-stripped form, the digits-only form, and the US 10/11-digit phone variants
    (with/without a leading country '1'). De-duped, empties dropped."""
    q0 = ("" if q is None else str(q)).strip()
    cands = set()
    if q0:
        cands.add(q0)
    nk = norm_key(q0)
    if nk:
        cands.add(nk)
    d = norm_digits(q0)
    if d:
        cands.add(d)
        if len(d) == 11 and d[0] == "1":
            cands.add(d[1:])
        elif len(d) == 10:
            cands.add("1" + d)
    return sorted(c for c in cands if c)


def keys_match(query_cands, *stored_values):
    """True if any stored value matches any query candidate under '.0'/digit normalization. Used to
    confirm a DB row (fetched by an .in_() over the candidates) is a real match for THIS query."""
    qset = set(query_cands) | {norm_digits(c) for c in query_cands}
    qset.discard("")
    for sv in stored_values:
        if sv is None:
            continue
        for form in (norm_key(sv), norm_digits(sv)):
            if form and form in qset:
                return True
    return False


# ── period canonicalization (a month written two ways must collapse to ONE) ─────────────────────────
def canon_display_period(period):
    """A single canonical 'Month YYYY' spelling so 'July 2026' and '2026-07' collapse to ONE period —
    distinct-period counting (residual months) must not double-count a month written two ways.
    Unparseable input passes through unchanged."""
    s = ("" if period is None else str(period)).strip()
    if len(s) >= 7 and s[:4].isdigit() and s[4] == "-" and s[5:7].isdigit():
        mo, yr = int(s[5:7]), int(s[:4])
    else:
        parts = s.split()
        if len(parts) == 2 and parts[0].lower() in _MONTHS and parts[1].isdigit():
            mo, yr = _MONTHS[parts[0].lower()], int(parts[1])
        else:
            return s
    if 1 <= mo <= 12 and yr:
        return f"{_MONTH_NAME[mo]} {yr}"
    return s


def period_sort_key(period):
    """yyyymm int for chronological sort; unparseable -> a large sentinel so a real month always wins
    'earliest' (activation)."""
    disp = canon_display_period(period)
    parts = disp.split()
    if len(parts) == 2 and parts[0].lower() in _MONTHS and parts[1].isdigit():
        return int(parts[1]) * 100 + _MONTHS[parts[0].lower()]
    return 10 ** 9


def tenure_from_periods(periods):
    """Activation + tenure from the DISTINCT residual periods a line appears in (owner doctrine:
    residual-months, NOT calendar). `periods` = the raw period strings from the tenant's raw_mi rows.
    Returns activation (earliest), last_seen (latest), months_active (count of DISTINCT canonical
    periods) and a VISIBLE assumption note. Empty history -> None / 0."""
    seen = []
    for p in periods:
        c = canon_display_period(p)
        if c and c not in seen:
            seen.append(c)
    if not seen:
        return {"activation_period": None, "last_seen_period": None, "months_active": 0,
                "basis": "residual months",
                "note": "No residual (MI) history on file for this line."}
    ordered = sorted(seen, key=period_sort_key)
    n = len(ordered)
    return {"activation_period": ordered[0], "last_seen_period": ordered[-1],
            "months_active": n, "basis": "residual months",
            "note": (f"Active {n} mo (residual months — the count of DISTINCT periods this line appears "
                     f"in the residual/MI report, not calendar months).")}


def ma_tenure_from_periods(periods):
    """Activation + tenure for a NON-Boost / MA-fed (Total / VidaPay) line — same residual-months
    doctrine + output SHAPE as tenure_from_periods, but sourced from the master-agent RECURRING data
    (raw_ma_daily_tx residual/recurring rows + raw_ma_commission's 1st–6th-month schedule) instead of
    raw_mi. Only the wording differs so the tenure section stops falsely saying 'No residual history on
    file' for an MA tenant that HAS MA periods. Empty history -> None / 0 with an MA-specific note."""
    seen = []
    for p in periods:
        c = canon_display_period(p)
        if c and c not in seen:
            seen.append(c)
    if not seen:
        return {"activation_period": None, "last_seen_period": None, "months_active": 0,
                "basis": "residual months (master-agent)",
                "note": "No master-agent recurring/residual history on file for this line yet."}
    ordered = sorted(seen, key=period_sort_key)
    n = len(ordered)
    return {"activation_period": ordered[0], "last_seen_period": ordered[-1],
            "months_active": n, "basis": "residual months (master-agent)",
            "note": (f"Active {n} mo (recurring months — the count of DISTINCT periods this line appears "
                     f"in the master-agent recurring/residual data, not calendar months).")}


# ── the salesperson-facing prompt (ALWAYS shown; never gated) ───────────────────────────────────────
def prompt_for(sold_by_us, sold_date=None):
    """Sold by us -> offer an UPGRADE; not sold by us -> sell a NEW phone. This is the core
    salesperson-facing signal and is returned regardless of commission-visibility gating."""
    if sold_by_us:
        when = f" on {str(sold_date)[:10]}" if sold_date else ""
        return {"kind": "upgrade", "icon": "⬆️",
                "text": f"Sold here{when} — offer this customer an UPGRADE"}
    return {"kind": "new_phone", "icon": "\U0001f4f1",
            "text": "Not sold by us — sell this customer a NEW phone"}


# ── aging history + our purchase price (DISPLAY-only; ungated — reps may see the cost) ───────────────
# Aging mirrors the asset-module Inventory Aging report EXACTLY (backend/app/modules/asset/router.py
# get_aging): buckets by days from acquired_date, edges  <45 / 45-60 (inclusive) / >60. A device that
# sold ages acquired->sold (days-on-inventory at sale); an unsold device ages acquired->today (current
# age). Everything here is PURE over dicts/strings so the router supplies the DB rows and the proof
# harness drives it. `today` may be a 'YYYY-MM-DD' string OR a datetime.date.
def _parse_date(v):
    """A 'YYYY-MM-DD...' string (or a date) → datetime.date, else None. Tolerates a trailing time."""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    t = str(v).strip()[:10]
    if len(t) != 10 or t[4] != "-" or t[7] != "-":
        return None
    try:
        return date(int(t[:4]), int(t[5:7]), int(t[8:10]))
    except (ValueError, TypeError):
        return None


def iso_date(v):
    """Normalize a date-ish value to a 'YYYY-MM-DD' string, or None if unparseable."""
    d = _parse_date(v)
    return d.isoformat() if d else None


def days_between(start, end):
    """Whole days from `start` to `end` (either a 'YYYY-MM-DD' string or a date). None if either is
    unparseable — never a fake 0."""
    a, b = _parse_date(start), _parse_date(end)
    if a is None or b is None:
        return None
    return (b - a).days


# The SAME three buckets the asset Inventory Aging report uses. Keep the edges in lock-step.
AGING_BUCKETS = (
    ("under45", "Fresh", "< 45 days"),
    ("warn", "Aging", "45–60 days"),
    ("missed", "Overaged", "> 60 days"),
)


def aging_bucket(days):
    """days -> {'key','label','range'} using the asset report's edges: <45 under45; 45..60 warn; >60
    missed. None days -> None (unknown, not a fake bucket)."""
    if days is None:
        return None
    if days < 45:
        key = "under45"
    elif days <= 60:
        key = "warn"
    else:
        key = "missed"
    for k, label, rng in AGING_BUCKETS:
        if k == key:
            return {"key": k, "label": label, "range": rng}
    return None


def build_aging(asset_row, sale_sold_date, today):
    """Compose the aging section from a matched asset_ledger row (dict) + the sold_date from the B2B
    sale match (fallback) + `today`. Pure. `asset_row` None/empty -> an HONEST 'no inventory record'
    line (never a fabricated zero-age). Sold date prefers the ledger's own date_sold, then the sale
    match; unsold -> age acquired->today (current age)."""
    asof = iso_date(today) or (today if isinstance(today, str) else None)
    if not asset_row:
        return {
            "found": False, "source": None, "asof": asof,
            "note": ("No inventory (asset-ledger) record on file for this device — aging is tracked "
                     "for VIP / asset-lending devices; this line has none."),
        }
    acquired = iso_date(asset_row.get("acquired_date"))
    ledger_sold = iso_date(asset_row.get("date_sold"))
    match_sold = iso_date(sale_sold_date)
    sold = ledger_sold or match_sold
    sold_source = "asset_ledger" if ledger_sold else ("raw_sales_match" if match_sold else None)
    is_sold = bool(sold)
    end = sold if is_sold else asof
    days = days_between(acquired, end)
    basis = None
    if days is not None:
        basis = ("days on inventory (acquired → sold)" if is_sold
                 else "current age (acquired → today, unsold)")
    billing = {k: iso_date(asset_row.get(k)) for k in
               ("payg_date", "due_date", "reimbursement_date", "trigger_date", "billing_friday")}
    billing["bill_path"] = (asset_row.get("bill_path") or None)
    billing = {k: v for k, v in billing.items() if v}  # drop empties — show only what's present
    note = None
    if acquired is None:
        note = "Inventory record found but no acquired date — cannot compute age."
    return {
        "found": True, "source": "asset_ledger", "asof": asof,
        "acquired_date": acquired,
        "store": ((asset_row.get("store") or "").strip() or None),
        "market": ((asset_row.get("market") or "").strip() or None),
        "device_model": ((asset_row.get("device_model") or "").strip() or None),
        "category": ((asset_row.get("category") or "").strip() or None),
        "status": ((asset_row.get("status") or "").strip() or None),
        "is_sold": is_sold,
        "sold_date": sold,
        "sold_source": sold_source,
        "days_on_inventory": days,
        "age_basis": basis,
        "bucket": aging_bucket(days),
        "billing": (billing or None),
        "note": note,
    }


# ── our purchase price (source-priority pick + provenance; UNGATED per owner directive) ──────────────
# For VIP / asset-lending devices `owed_to_vip` is the device COST basis (the asset module's own
# undercharge flag labels it "cost": owed_to_vip > reimbursement + selling_price = a loss). If the
# source VIP file ALSO carries an explicit device-cost column it is captured verbatim in raw_row and
# wins (more authoritative than the derived owed_to_vip). SAP-configurable spirit: the header match is
# an allowlist, EXCLUDING sale/selling/reimbursement/owed so the SALE price is never mistaken for cost.
PURCHASE_PRICE_KEYS = ("purchase price", "device price", "device cost", "unit cost", "our cost",
                       "cost basis", "acquisition cost", "dealer cost", "buy price", "item cost")
PURCHASE_PRICE_EXCLUDE = ("selling", "sale", "sold", "reimburse", "retail", "owed", "commission")


def to_amount(v):
    """Parse a money-ish value to float, or None (DISTINCT from 0). '$1,234.50' -> 1234.5; blank /
    non-numeric / 'nan' / None -> None (never a fake 0)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    s = str(v).strip().replace("$", "").replace(",", "")
    if s == "" or s.lower() in ("nan", "none", "null", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def scan_raw_row_price(raw_row, keys=PURCHASE_PRICE_KEYS, exclude=PURCHASE_PRICE_EXCLUDE):
    """Scan an asset_ledger raw_row (dict of original header -> string) for an explicit device
    purchase/cost column. Returns (amount, matched_header) or (None, None). Priority = the order of
    `keys`; a header CONTAINING any `exclude` term is skipped so the SALE price is never picked."""
    if not isinstance(raw_row, dict):
        return (None, None)
    lowered = []
    for hdr, val in raw_row.items():
        lowered.append((str(hdr).strip().lower(), hdr, val))
    for key in keys:
        for h, hdr, val in lowered:
            if key in h and not any(x in h for x in exclude):
                amt = to_amount(val)
                if amt is not None:
                    return (amt, hdr)
    return (None, None)


def pick_purchase_price(candidates):
    """Source-priority pick over an ORDERED candidate list. Each candidate:
       {'amount': float|None, 'source': str, 'label': str}.
    Returns the FIRST candidate whose amount is a real number (not None) with its provenance. Honest
    empty when none present — never a fabricated 0. The full considered list rides along so the UI can
    show WHERE the price came from (and what was skipped)."""
    considered, chosen = [], None
    for c in candidates or []:
        amt = c.get("amount")
        considered.append({"source": c.get("source"), "label": c.get("label"), "amount": amt})
        if chosen is None and amt is not None:
            chosen = c
    if chosen is None:
        return {"found": False, "amount": None, "source": None, "label": None,
                "provenance": ("No purchase-price record on file yet for this device — no POS/SKU "
                               "inventory cost, at-sale cost, marketplace order, or VIP basis. "
                               "Inventory/POS cost feed pending."),
                "candidates_considered": considered}
    return {"found": True, "amount": round(float(chosen["amount"]), 2),
            "source": chosen.get("source"), "label": chosen.get("label"),
            "provenance": f"{chosen.get('label')} · source: {chosen.get('source')}",
            "candidates_considered": considered}


# ── v2: UNIVERSAL POS/SKU purchase-price sources (owner directive 2026-07-17) ─────────────────────────
# owed_to_vip is NOT universal (VIP/house only). The universal cost signal is POS/SKU-based:
#   ① per-IMEI inventory-aging cost (POS on-hand cost keyed by device/SKU)   [inv_device_cost]
#   ② at-sale POS cost = the B2B sale line's ext_price − GP                    [pos_cost_from_sale]
#   ③ MA marketplace order price (Total/MA: imei → activation_order → order)  [pick_ma_marketplace_price]
#   ④ asset_ledger.raw_row explicit device-cost column                        [scan_raw_row_price]
#   ⑤ asset_ledger.owed_to_vip — LAST RESORT, VIP billing basis (house only)
# All pure over dicts/strings; the router supplies the org-scoped DB rows.

def pos_cost_from_sale(ext_price, gp):
    """At-sale POS cost derived from the B2B sale LINE: cost = ext_price − GP (the 78-col Sales
    Transaction Details export carries GP + Ext Price; there is NO explicit cost column on raw_sales /
    daily_sales_feed). Returns the derived cost as a float, or None (honest — never a fake 0) when:
      • ext_price is unknown/blank, or
      • GP is unknown/blank (GP=None ≠ GP=0 — a real 0 GP is a valid signal → cost = ext_price), or
      • the derived cost is ≤ 0 (gp ≥ ext_price: a $0-line or sold-below-cost anomaly is NOT a cost).
    A negative GP (sold below cost) legitimately yields cost = ext + |gp| > ext (kept)."""
    ext = to_amount(ext_price)
    g = to_amount(gp)
    if ext is None or g is None:
        return None
    cost = round(ext - g, 2)
    return cost if cost > 0 else None


def inv_device_cost(inv_row):
    """Per-IMEI inventory-aging device COST from an inventory_aging_device row (dict). Returns
    (amount, sku) or (None, None). The unit_cost is the POS on-hand cost captured by the b2bsoft/POS
    Inventory Aging report; a 0/blank cost is treated as no-signal (honest empty)."""
    if not isinstance(inv_row, dict):
        return (None, None)
    amt = to_amount(inv_row.get("unit_cost"))
    if amt is None or amt <= 0:
        return (None, None)
    sku = (str(inv_row.get("sku") or "").strip() or None)
    return (round(amt, 2), sku)


def norm_order(v):
    """Comparable form of an order key (activation_order / order_number): strip, drop a trailing '.0',
    lowercase. Empty → ''."""
    s = ("" if v is None else str(v)).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.lower()


def order_candidates(orders):
    """Exact-match spellings an order key could be stored as (for an .in_() over raw_ma_fulfillment):
    the raw stripped value + its '.0'-stripped form. De-duped, empties dropped. (The final match is
    still confirmed in Python via norm_order, so light case/format drift can't silently miss.)"""
    out = set()
    for o in orders or []:
        s = ("" if o is None else str(o)).strip()
        if not s:
            continue
        out.add(s)
        if s.endswith(".0"):
            out.add(s[:-2])
    return sorted(out)


def pick_ma_marketplace_price(commission_rows, fulfillment_rows):
    """MA (Total/VidaPay) per-IMEI purchase price. VERIFIED join (mig 083):
        raw_ma_commission.imei  →  raw_ma_commission.activation_order  (the order key on the
        commission side; there is NO order_number column on raw_ma_commission)  →
        raw_ma_fulfillment.order_number  →  raw_ma_fulfillment.price  (the handset purchase price).
    `commission_rows` are ALREADY imei-matched + org-scoped by the router; `fulfillment_rows` are the
    candidate marketplace-order rows. Pure. Returns a dict:
      • hit           → {found True, amount, order_number, product_name}
      • order but no price row → {found False, note '…no marketplace fulfillment price…', linked_order}
      • no commission order    → {found False, note '…no MA commission/order row…'}"""
    orders = [norm_order(r.get("activation_order")) for r in (commission_rows or [])]
    orders = [o for o in orders if o]
    if not orders:
        return {"found": False, "amount": None, "order_number": None, "product_name": None,
                "note": "No MA commission (activation-order) row links this IMEI to a marketplace order."}
    price_by_order = {}
    for f in (fulfillment_rows or []):
        o = norm_order(f.get("order_number"))
        amt = to_amount(f.get("price"))
        if o and amt is not None and amt > 0 and o not in price_by_order:
            price_by_order[o] = (round(amt, 2), (str(f.get("order_number") or "").strip() or None),
                                 (str(f.get("product_name") or "").strip() or None))
    for o in orders:
        if o in price_by_order:
            amt, ordnum, prod = price_by_order[o]
            return {"found": True, "amount": amt, "order_number": ordnum, "product_name": prod, "note": None}
    linked = next((r.get("activation_order") for r in commission_rows
                   if norm_order(r.get("activation_order"))), None)
    return {"found": False, "amount": None, "order_number": (str(linked).strip() if linked else None),
            "product_name": None,
            "note": ("MA commission order linked to this IMEI, but no marketplace fulfillment price "
                     "row matched its order number.")}


def build_aging_inventory(inv_row, sale_sold_date, today):
    """Aging section for a NON-VIP tenant, sourced from an inventory_aging_device row (the b2bsoft/POS
    Inventory Aging report's per-device line) instead of asset_ledger. Same OUTPUT SHAPE as build_aging
    so the UI/exports are unchanged. Aging basis:
      • received_date present → received → sold (if a B2B sale matched) else received → today (unsold);
      • else days_in_stock from the report → used as-is (as of the file's as_of_date);
      • else honest 'no aging date' note.
    inv_row None/empty → honest 'no inventory record' (delegates to build_aging(None, …))."""
    asof = iso_date(today) or (today if isinstance(today, str) else None)
    if not inv_row:
        return build_aging(None, sale_sold_date, today)
    acquired = iso_date(inv_row.get("received_date"))
    match_sold = iso_date(sale_sold_date)
    sold = match_sold  # the aging report is an ON-HAND snapshot; only a B2B sale supplies a sold date
    is_sold = bool(sold)
    days = None
    basis = None
    if acquired is not None:
        end = sold if is_sold else asof
        days = days_between(acquired, end)
        if days is not None:
            basis = ("days on inventory (received → sold)" if is_sold
                     else "current age (received → today, unsold)")
    note = None
    dis = inv_row.get("days_in_stock")
    if days is None and dis is not None:
        try:
            days = int(dis)
            basis = "days in stock (from the inventory-aging report" + (
                f", as of {iso_date(inv_row.get('as_of_date'))}" if inv_row.get("as_of_date") else "") + ")"
        except (TypeError, ValueError):
            days = None
    if acquired is None and days is None:
        note = "Inventory record found but no received/aging date — cannot compute age."
    model = ((str(inv_row.get("item") or "").strip()) or (str(inv_row.get("sku") or "").strip()) or None)
    return {
        "found": True, "source": "inventory_aging_device", "asof": asof,
        "acquired_date": acquired,
        "store": ((str(inv_row.get("store") or "").strip()) or None),
        "market": None,
        "device_model": model,
        "category": "On Inventory",
        "status": None,
        "is_sold": is_sold,
        "sold_date": sold,
        "sold_source": ("raw_sales_match" if is_sold else None),
        "days_on_inventory": days,
        "age_basis": basis,
        "bucket": aging_bucket(days),
        "billing": None,
        "note": note,
    }


# ── money-table categorization (COMMISSION vs REBATE — never blended) ────────────────────────────────
# raw_payment_detail rows are classed via discrepancy_engine.parse_payment_type -> comp_type. Only the
# reimbursement classes are REBATES here; recurring residual (MI/ATU) is shown from raw_mi (the residual
# report), so the two feeds never double-count. Everything else in payment_detail (one-time
# bounties/spiffs, or payment-level MI/ATU) is EXCLUDED from the v1 table and reported honestly.
REBATE_COMP_TYPES = {"SIMCR", "DEVICE_REIMB"}


def categorize_comp(comp_type):
    """'rebate' for the reimbursement classes; 'other' for everything else (NOT folded into commission —
    commission comes from raw_mi residual, not payment_detail, to avoid double-counting)."""
    return "rebate" if (comp_type or "") in REBATE_COMP_TYPES else "other"


def build_money_table(mi_matches, payment_matches, comp_of):
    """Assemble the per-period money table. COMMISSION and REBATE are SEPARATE sections, each with its
    own subtotal, plus a grand total. Pure:
      mi_matches      = [{'period': str, 'amount': float}]   (raw_mi residual MI+ATU per period)
      payment_matches = [{'period': str, 'amount': float, 'payment_type': str}]  (raw_payment_detail)
      comp_of(payment_type) -> comp_type string (discrepancy_engine.parse_payment_type[0])
    NO invented numbers: every row is labeled with exactly what it is + its source table; non-rebate
    payment-detail rows are surfaced as an honest `excluded` summary, never blended into a subtotal."""
    # COMMISSION — one row per DISTINCT residual period (sum within a period).
    by_period = {}
    for r in mi_matches:
        c = canon_display_period(r.get("period"))
        by_period[c] = round(by_period.get(c, 0.0) + float(r.get("amount") or 0), 2)
    comm_rows = [{"period": p, "amount": a, "label": "Residual (MI+ATU)", "source": "raw_mi"}
                 for p, a in by_period.items()]
    comm_rows.sort(key=lambda x: period_sort_key(x["period"]))
    comm_sub = round(sum(r["amount"] for r in comm_rows), 2)

    # REBATE — one row per reimbursement-class payment; everything else EXCLUDED (noted).
    reb_rows, excl_count, excl_total = [], 0, 0.0
    for r in payment_matches:
        ptype = r.get("payment_type") or ""
        amt = round(float(r.get("amount") or 0), 2)
        if categorize_comp(comp_of(ptype)) == "rebate":
            reb_rows.append({"period": canon_display_period(r.get("period")), "amount": amt,
                             "label": ptype or "Reimbursement", "source": "raw_payment_detail"})
        else:
            excl_count += 1
            excl_total = round(excl_total + amt, 2)
    reb_rows.sort(key=lambda x: period_sort_key(x["period"]))
    reb_sub = round(sum(r["amount"] for r in reb_rows), 2)

    excluded = None
    if excl_count:
        excluded = {"payment_detail_other": {
            "count": excl_count, "total": excl_total,
            "note": (f"{excl_count} payment-detail line(s) (${excl_total:,.2f}) are one-time "
                     "bounties/spiffs or payment-level MI/ATU — NOT itemized in v1 to avoid "
                     "double-counting the MI-report residual shown under Commission.")}}
    return {
        "commission": {"label": "Residual (MI+ATU)", "source": "raw_mi",
                       "rows": comm_rows, "subtotal": comm_sub},
        "rebate": {"label": "Equipment / SIM reimbursement", "source": "raw_payment_detail",
                   "rows": reb_rows, "subtotal": reb_sub},
        "grand_total": round(comm_sub + reb_sub, 2),
        "excluded": excluded,
    }


# ── MA (master-agent / VidaPay-fed) money table — carrier-aware money section ────────────────────────
# For a non-Boost / MA-fed tenant the "money received on this line" comes from the MA Commission Details
# feed (commcalc.raw_ma_commission, imei-keyed), NOT raw_mi/raw_payment_detail. Per period the file
# carries: 1st–6th-month spiffs (spiff_m1..spiff_m6), rebate, equipment margins (device_margin +
# consumer_margin) and the subscriber plan MRC (mrc_net_discount — INFORMATIONAL, the plan's MRC, not a
# dealer payout). SIGN: VidaPay/Total follow the canonical negative=payout convention already used across
# the codebase (commission_ledger.classify/build_row book abs of a negative as the payout; whatif's MA
# source config normalizes with residual_sign='negate'). We normalize each amount paid-to-dealer = −raw
# so a payout shows POSITIVE and a charge/clawback shows NEGATIVE — sign info is preserved, never dropped.
# DISPLAY of already-recorded data only; no pay-path here.
_MA_SPIFF_KEYS = ("spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6")


def ma_paid(v):
    """Normalize one raw MA Commission Details cell to a PAID-TO-DEALER value: negate (paid = −raw), so a
    NEGATIVE cell (money paid to the dealer) shows POSITIVE and a POSITIVE cell (a charge/clawback) shows
    NEGATIVE. Sign is preserved, never dropped. Blank/None/non-numeric → 0.0 (a blank cell contributes
    nothing to a sum). Mirrors the canonical negative=payout convention (commission_ledger.py)."""
    a = to_amount(v)
    return round(-a, 2) if a is not None else 0.0


def _ma_detail(r):
    """One per-row MA money detail (a base line OR a later adjustment line), all amounts paid-to-dealer.
    Kept so multiple rows per IMEI/period can be aggregated for totals AND shown expandably. line_status
    may be None — it is metadata only and NEVER gates the paid display."""
    sp = {k: ma_paid(r.get(k)) for k in _MA_SPIFF_KEYS}
    dm, cm = ma_paid(r.get("device_margin")), ma_paid(r.get("consumer_margin"))
    d = {"period": canon_display_period(r.get("period")),
         "spiff_total": round(sum(sp.values()), 2),
         "rebate": ma_paid(r.get("rebate")),
         "device_margin": dm, "consumer_margin": cm, "margin_total": round(dm + cm, 2),
         "mrc_net_discount": ma_paid(r.get("mrc_net_discount")),
         "line_status": (str(r.get("line_status") or "").strip() or None),
         "activation_type": (str(r.get("activation_type") or "").strip() or None),
         "activation_type2": (str(r.get("activation_type2") or "").strip() or None),
         "ban": (str(r.get("ban") or "").strip() or None)}
    d.update(sp)
    return d


def build_ma_money_table(commission_rows):
    """Assemble the MA money table for a non-Boost / MA-fed tenant from raw_ma_commission rows ALREADY
    imei-matched + org-scoped by the router. Pure.
      • MULTIPLE rows per IMEI per period (a base line + later adjustment lines) are AGGREGATED into one
        per-period total, with every contributing row kept in that period's `detail` list (expandable).
      • Amounts are paid-to-dealer normalized (see ma_paid: negative=payout → positive; charge → negative).
      • Period spelling collapses via canon_display_period so 'June 2026' and '2026-06' are ONE period.
      • line_status may be NULL — it NEVER gates the paid/active display; nonzero spiff/rebate is the real
        payment evidence.
    Sections (all per DISTINCT canonical period):
      spiff  Σ M1–M6 spiffs      [payout] · rebate Σ rebate [payout] ·
      margin Σ device+consumer   [payout] · mrc Σ mrc_net_discount [INFORMATIONAL, subscriber plan MRC,
                                            not a dealer payout — excluded from the grand total].
    grand_total = spiff + rebate + margin subtotals (the dealer-payable money).
    Empty input → empty sections + an explicit note (never a silent $0, never a crash)."""
    per, order = {}, []
    _SUM_KEYS = _MA_SPIFF_KEYS + ("spiff_total", "rebate", "device_margin", "consumer_margin",
                                  "margin_total", "mrc_net_discount")
    for r in (commission_rows or []):
        det = _ma_detail(r)
        cp = det["period"]
        if not cp:
            try:
                mo, yr = int(r.get("period_month") or 0), int(r.get("period_year") or 0)
                if 1 <= mo <= 12 and yr:
                    cp = canon_display_period(f"{yr}-{mo:02d}")
            except Exception:
                cp = ""
        if not cp:
            cp = "(no period)"
        det["period"] = cp
        d = per.get(cp)
        if d is None:
            d = {"period": cp, "line_status": None, "_sk": "", "rows": 0, "detail": []}
            for k in _SUM_KEYS:
                d[k] = 0.0
            per[cp] = d
            order.append(cp)
        d["detail"].append(det)
        d["rows"] += 1
        for k in _SUM_KEYS:
            d[k] = round(d[k] + det[k], 2)
        ls, sk = det["line_status"], str(r.get("status_change_date") or "")
        if ls and sk >= (d["_sk"] or ""):
            d["line_status"], d["_sk"] = ls, sk
        elif ls and not d["line_status"]:
            d["line_status"] = ls

    periods = sorted(per.values(), key=lambda x: period_sort_key(x["period"]))
    for d in periods:
        d.pop("_sk", None)

    def _section(label, field):
        return {"label": label, "source": "raw_ma_commission",
                "rows": [{"period": d["period"], "amount": d[field], "label": label,
                          "source": "raw_ma_commission"} for d in periods],
                "subtotal": round(sum(d[field] for d in periods), 2)}

    spiff = _section("First-6-month spiffs (M1–M6)", "spiff_total")
    rebate = _section("Rebate", "rebate")
    margin = _section("Equipment margin (device + consumer)", "margin_total")
    mrc = _section("Plan MRC (net discount) — informational", "mrc_net_discount")
    grand = round(spiff["subtotal"] + rebate["subtotal"] + margin["subtotal"], 2)
    note = None
    if not periods:
        note = ("No master-agent commission (MA Commission Details) rows ingested for this device yet — "
                "pull/upload the MA Commission report (Data Imports → payment-processor sources).")
    return {
        "kind": "ma", "source": "raw_ma_commission",
        "sign_convention": "negative=payout (shown paid-to-dealer; charge shown negative)",
        "periods": periods, "spiff": spiff, "rebate": rebate, "margin": margin, "mrc": mrc,
        "line_status": (periods[-1]["line_status"] if periods else None),
        "grand_total": grand, "note": note,
    }


# ── the money gate (admin-only by default; grantable via the 'device_commission' DATA_GRANT) ────────
def device_commission_allowed(caller):
    """Gate the MONEY table. ADMIN-ONLY BY DEFAULT, grantable via the DATA_GRANTS 'device_commission'
    key — same resolution SHAPE as commcalc `_can_view_carrier_residual` but DEFAULT-CLOSED (commission
    $ is not open-by-default). Mirrors the frontend `hasDataGrant('device_commission')`. Pure over a
    resolved caller dict:
      super_admin / perms.scope=='all' / role=='admin'  -> allow
      'device_commission' in perms.modules, or perms.data.device_commission truthy -> allow
      else -> deny (the widget shows history/device/prompt, money section shows the lock note)."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    if (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin"):
        return True
    if "device_commission" in (perms.get("modules") or []):
        return True
    if bool((perms.get("data") or {}).get("device_commission")):
        return True
    return False
