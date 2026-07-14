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
