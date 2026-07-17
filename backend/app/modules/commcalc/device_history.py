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
                "provenance": "No purchase-price record on file for this device.",
                "candidates_considered": considered}
    return {"found": True, "amount": round(float(chosen["amount"]), 2),
            "source": chosen.get("source"), "label": chosen.get("label"),
            "provenance": f"{chosen.get('label')} · source: {chosen.get('source')}",
            "candidates_considered": considered}


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
