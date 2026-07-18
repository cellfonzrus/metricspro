"""Agency module — pure billing / resolution math (Phase 1).

No DB, no network: every function takes already-fetched rows (lists of dicts) and returns computed values.
This is the money-touching core of the agency invoice, so it is deliberately isolated + fully unit-testable
(backend/scratchpad/agency_phase1_proof.py). NOTHING here reads rep_commissions / calculator /
commission_engine — the agency module never changes a rep's pay.

Algorithms mirror docs/designs/agency-module-schema.md (REV C):
  C1 resolve_holdback_rule     — specificity → carrier-specificity → priority → created_at  (Phase-2 consumer;
                                 built + proven now as a pure config helper — no settlement is written in P1)
  C2 resolve_equipment_margin  — most-specific class, carrier-match, priority
  C3 compute_invoice_lines     — equipment_margin + store_fee (prorated, C6) + other_charge (flat then %) + tax
  C6 proration_factor          — period ∩ store-active ∩ charge-active window
"""
import calendar as _cal
from datetime import date as _date
from decimal import Decimal, ROUND_HALF_UP


def _f(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def r2(x):
    """Round to cents, round-half-up on the third decimal (deterministic; avoids banker's-rounding surprises
    on an exact .xx5 so the invoice math is reproducible)."""
    try:
        return float(Decimal(str(_f(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:
        return round(_f(x), 2)


def _as_date(v):
    if not v:
        return None
    if isinstance(v, _date):
        return v
    try:
        return _date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def parse_period_bounds(period):
    """(start_date, end_date, period_days) for a month-period ('June 2026' or '2026-06'); ('','',30) fallback."""
    p = str(period or "").strip()
    mo = yr = None
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
        yr, mo = int(p[:4]), int(p[5:7])
    else:
        parts = p.split()
        names = {m.lower(): i for i, m in enumerate(_cal.month_name) if m}
        if len(parts) == 2 and parts[0].lower() in names and parts[1].isdigit():
            mo, yr = names[parts[0].lower()], int(parts[1])
    if not mo or not yr or not (1 <= mo <= 12):
        return (None, None, 30)
    days = _cal.monthrange(yr, mo)[1]
    return (_date(yr, mo, 1), _date(yr, mo, days), days)


def is_effective(row, period_start, period_end):
    """A config row (is_active + effective_start/effective_end) overlaps the period window.
    NULL start = open-before, NULL end = open-after."""
    if not row.get("is_active", True):
        return False
    es = _as_date(row.get("effective_start"))
    ee = _as_date(row.get("effective_end"))
    if period_end is not None and es is not None and es > period_end:
        return False
    if period_start is not None and ee is not None and ee < period_start:
        return False
    return True


# ── C1: holdback rule resolution (Phase-2 consumer; pure helper, proven in Phase 1) ─────────────────
# Rank blends specificity with the statement_line_type > commission_component preference at level 4.
_HB_RANK = {"product_class": 50, "statement_line_type": 42, "commission_component": 41,
            "ledger_bucket": 30, "carrier": 20, "all": 10}


def _carrier_ok(rule_carrier, ctx_carrier):
    """A rule with NULL carrier matches any; a carrier-specific rule matches only its carrier (and only when
    the line carries a carrier)."""
    if not rule_carrier:
        return True
    if not ctx_carrier:
        return False
    return rule_carrier == ctx_carrier


def resolve_holdback_rule(rules, ctx, period_start=None, period_end=None):
    """C1: pick exactly one active/effective/carrier-matching holdback rule for a carrier-receipt line.
    ctx keys: carrier_id, ledger_bucket, commission_component, statement_line_type, product_class.
    Returns the winning rule dict, or None."""
    cand = []
    for r in rules or []:
        if not is_effective(r, period_start, period_end):
            continue
        if not _carrier_ok(r.get("carrier_id"), ctx.get("carrier_id")):
            continue
        sk = r.get("scope_kind") or "all"
        sv = r.get("scope_value") or ""
        if sk in ("all", "carrier"):
            matched = True
        elif sk == "ledger_bucket":
            matched = sv == (ctx.get("ledger_bucket") or "")
        elif sk == "commission_component":
            matched = sv == (ctx.get("commission_component") or "")
        elif sk == "statement_line_type":
            matched = sv == (ctx.get("statement_line_type") or "")
        elif sk == "product_class":
            matched = sv == (ctx.get("product_class") or "")
        else:
            matched = False
        if matched:
            cand.append(r)
    if not cand:
        return None
    cand.sort(key=lambda r: (
        -_HB_RANK.get(r.get("scope_kind") or "all", 0),
        -(1 if r.get("carrier_id") else 0),
        int(r.get("priority") or 100),
        str(r.get("created_at") or ""),
    ))
    return cand[0]


def holdback_amount(rule, gross, qty=1, activations=1):
    """C1 apply: percent → value × gross; flat → value × unit-count per flat_per."""
    val = _f(rule.get("value"))
    if (rule.get("method") or "percent") == "percent":
        return r2(val * _f(gross))
    fp = rule.get("flat_per") or "activation"
    units = _f(activations) if fp == "activation" else (1 if fp == "invoice" else _f(qty, 1))
    return r2(val * units)


# ── C2: equipment margin resolution + amount ────────────────────────────────────────────────────────
def resolve_equipment_margin(margins, transfer, period_start=None, period_end=None):
    """C2: the active/effective/carrier-matching agency_equipment_margin for a transfer's equip class."""
    ev = transfer.get("equip_class_value") or ""
    tc = transfer.get("carrier_id")
    cand = []
    for m in margins or []:
        if not is_effective(m, period_start, period_end):
            continue
        if not _carrier_ok(m.get("carrier_id"), tc):
            continue
        if (m.get("equip_class_value") or "") != ev:
            continue
        cand.append(m)
    if not cand:
        return None
    cand.sort(key=lambda m: (0 if m.get("carrier_id") else 1, int(m.get("priority") or 100),
                             str(m.get("created_at") or "")))
    return cand[0]


def equipment_margin_amount(margin, transfer):
    """percent → value × basis × qty (basis ∈ cost/ext_price/gp, falling back to unit_cost when a transfer
    lacks that field); flat → value × qty."""
    qty = _f(transfer.get("qty"))
    val = _f(margin.get("value"))
    if (margin.get("method") or "percent") == "flat":
        return r2(val * qty)
    bk = margin.get("markup_basis") or "cost"
    basis = _f(transfer.get("unit_cost"))
    if bk == "ext_price":
        basis = _f(transfer.get("ext_price"), _f(transfer.get("unit_cost")))
    elif bk == "gp":
        basis = _f(transfer.get("gp"), 0.0)
    return r2(val * basis * qty)


# ── C6: per-store fee proration ───────────────────────────────────────────────────────────────────
def proration_factor(mode, link_default, store_row, charge_row, period_start, period_end, period_days):
    """(factor, active_days). Effective mode = charge.proration_mode unless 'default' → link default.
    'prorated' → active window = period ∩ store-active ∩ charge-active."""
    eff = mode if mode and mode != "default" else (link_default or "full")
    if eff != "prorated" or period_start is None:
        return (1.0, period_days)
    lo, hi = period_start, period_end
    for row in (store_row or {}, charge_row or {}):
        es = _as_date(row.get("effective_start"))
        ee = _as_date(row.get("effective_end"))
        if es and es > lo:
            lo = es
        if ee and ee < hi:
            hi = ee
    if hi < lo:
        return (0.0, 0)
    active = max(0, min((hi - lo).days + 1, period_days))
    return ((active / period_days) if period_days else 1.0, active)


# ── C3: invoice line computation (pure) ─────────────────────────────────────────────────────────────
def _line(sort, source_type, amount, **kw):
    row = {"source_type": source_type, "source_id": None, "transfer_id": None, "link_store_id": None,
           "carrier_id": None, "description": "", "qty": 1, "unit_amount": r2(amount), "method": None,
           "value": None, "proration_factor": 1.0, "amount": r2(amount), "sort": sort}
    row.update(kw)
    return row


def compute_invoice_lines(link, stores, charges, margins, transfers, period):
    """C3: pure computation of invoice lines + totals from already-fetched config + the CONFIRMED, UNCONSUMED
    transfers (caller pre-filters confirm_status='confirmed' AND billed_invoice_id IS NULL — N3 roll-forward).
    A `one_time` charge bills only when the caller sets charge['_bill_one_time']=True. Percent-of-
    invoice_subtotal computes on the base subtotal (equipment + store-fee + flat other) to avoid a
    self-referential basis. Returns the header/line payload dict."""
    ps, pe, pdays = parse_period_bounds(period)
    active = [c for c in (charges or []) if is_effective(c, ps, pe)]
    store_by_id = {s.get("id"): s for s in (stores or [])}
    lines = []
    sort = 0

    # (1) equipment_margin — one line per confirmed unconsumed transfer that resolves a margin rule
    eq_total = 0.0
    for t in sorted(transfers or [], key=lambda x: (str(x.get("equip_class_value") or ""),
                                                    str(x.get("created_at") or ""), str(x.get("id") or ""))):
        m = resolve_equipment_margin(margins, t, ps, pe)
        if not m:
            continue
        amt = equipment_margin_amount(m, t)
        qty = _f(t.get("qty"))
        unit = r2(amt / qty) if qty else amt
        ec = t.get("equip_class_value") or "equipment"
        desc = f"Equipment margin — {ec}" + (f" · {t.get('product_desc')}" if t.get("product_desc") else "")
        lines.append(_line(sort, "equipment_margin", amt, source_id=m.get("id"), transfer_id=t.get("id"),
                           link_store_id=t.get("link_store_id"), carrier_id=t.get("carrier_id"),
                           description=desc, qty=qty, unit_amount=unit, method=m.get("method"),
                           value=_f(m.get("value"))))
        eq_total = r2(eq_total + amt)
        sort += 1

    # (2) store_fee — monthly FLAT charge scoped to a store (prorated per C6)
    store_fee_total = 0.0
    for ch in active:
        if ch.get("cadence") != "monthly" or not ch.get("link_store_id") or (ch.get("method") or "flat") != "flat":
            continue
        s = store_by_id.get(ch.get("link_store_id")) or {}
        factor, adays = proration_factor(ch.get("proration_mode") or "default", link.get("default_proration_mode"),
                                         s, ch, ps, pe, pdays)
        amt = r2(_f(ch.get("value")) * factor)
        sname = s.get("store_label") or s.get("store_address") or s.get("store_code") or ""
        desc = f"{ch.get('label') or 'Monthly store fee'} — {sname}"
        if factor < 1:
            desc += f" (prorated {adays}/{pdays} days)"
        lines.append(_line(sort, "store_fee", amt, source_id=ch.get("id"), link_store_id=ch.get("link_store_id"),
                           description=desc, qty=1, unit_amount=_f(ch.get("value")), method="flat",
                           value=_f(ch.get("value")), proration_factor=round(factor, 6)))
        store_fee_total = r2(store_fee_total + amt)
        sort += 1

    # (3) other charges — FLAT first (feed the base subtotal), then PERCENT on their basis
    other_total = 0.0
    flats, pcts = [], []
    for ch in active:
        if ch.get("cadence") == "monthly" and ch.get("link_store_id") and (ch.get("method") or "flat") == "flat":
            continue  # already billed as store_fee
        if (ch.get("cadence") or "monthly") == "one_time" and not ch.get("_bill_one_time"):
            continue
        (pcts if (ch.get("method") == "percent") else flats).append(ch)
    for ch in flats:
        amt = r2(_f(ch.get("value")))
        lines.append(_line(sort, "other_charge", amt, source_id=ch.get("id"),
                           description=ch.get("label") or "Charge", method="flat", value=_f(ch.get("value"))))
        other_total = r2(other_total + amt)
        sort += 1
    base_subtotal = r2(eq_total + store_fee_total + other_total)
    for ch in pcts:
        bk = ch.get("percent_basis") or "invoice_subtotal"
        basis = eq_total if bk == "equipment_margin_total" else (0.0 if bk == "holdback_total" else base_subtotal)
        amt = r2(_f(ch.get("value")) * basis)
        lbl = f"{ch.get('label') or 'Charge'} ({_f(ch.get('value')) * 100:g}% of {bk})"
        lines.append(_line(sort, "other_charge", amt, source_id=ch.get("id"), description=lbl,
                           method="percent", value=_f(ch.get("value"))))
        other_total = r2(other_total + amt)
        sort += 1

    subtotal = r2(eq_total + store_fee_total + other_total)
    taxable = bool(link.get("taxable"))
    rate = _f(link.get("tax_rate"))
    tax_total = r2(rate * subtotal) if taxable else 0.0
    total = r2(subtotal + tax_total)
    return {
        "lines": lines,
        "equipment_margin_total": r2(eq_total), "store_fee_total": r2(store_fee_total),
        "other_charge_total": r2(other_total), "holdback_total_memo": 0.0,
        "subtotal": subtotal, "taxable_snapshot": taxable, "tax_rate_snapshot": rate,
        "tax_total": tax_total, "total": total,
        "period_start": ps.isoformat() if ps else None, "period_end": pe.isoformat() if pe else None,
    }
