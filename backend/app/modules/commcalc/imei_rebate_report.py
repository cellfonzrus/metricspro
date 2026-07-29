"""IMEI ↔ REBATE reconciliation report — PURE, testable aggregation (no DB, no FastAPI).

Owner request 2026-07-28: *"one more exclusive report which shows the IMEI activated and the rebate
received against it."* Asked in a Total/VidaPay thread, built CARRIER- AND TENANT-AGNOSTIC per the
SAP-configurable rule (AGENT_CONTRACT §3): the source is resolved by WHICH DATA EXISTS for the org,
never by a tenant or carrier name — the same posture as the P&L residual fallback.

One row per ACTIVATED IMEI in the selected period, carrying the rebate (and, where the feed states them,
the month-1–6 spiffs) recorded against that IMEI. The reconciliation value is the GAPS: an activation with
NO rebate against it is a first-class, filterable row + tile, not an absent row. The inverse (a rebate row
whose IMEI has no visible activation) is a collapsed data-quality section.

────────────────────────────────────────────────────────────────────────────────────────────────────
TWO SOURCE PATHS (both may coexist in one org → union, every row tagged with its `source`)

  MA path  — `commcalc.raw_ma_commission` (mig 083), the master-agent / VidaPay-fed per-activation feed.
             Activation, rebate and the M1–M6 spiffs are all on the SAME imei-keyed row.
             SIGN: the export writes NEGATIVE = paid TO the dealer. Every payout cell is normalized
             paid-to-dealer via `device_history.ma_paid` (payout → POSITIVE, charge/clawback → NEGATIVE),
             exactly as `/ma-commission/summary` and the device-history MA money table do.
             `mrc_net_discount` is the subscriber's PLAN PRICE, not a payout — it is NOT read here.

  ePay path — activations from the tenant's own B2B sales / residual feed; rebates from
             `commcalc.raw_payment_detail`, classified with the EXISTING classifier
             (`device_history.categorize_comp` over `discrepancy_engine.parse_payment_type`) so the
             rebate-vs-commission split is identical to the Device History widget. No second
             classification is invented here.
             SIGN: payment-detail `amount` is already stored positive = paid to the dealer, so it is
             read AS-IS. The sign asymmetry between the two feeds is deliberate and lives in the two
             adapters (`ma_events` flips, `epay_event` does not) — never in the merge/report layer.

ACTIVATION DEFINITION (stated in the report header so this surface and Device History can never disagree)
  MA   : a `raw_ma_commission` row whose transaction date falls in the period.
  ePay : a device line in the period's B2B sales (`raw_sales.serial_1`) AND/OR a residual line whose MI
         activation date falls in the period (`raw_mi.mi_activation_date`) — the same two sources
         `device_history` reads (sale ← raw_sales, activation ← the residual feed). A residual row whose
         activation date is blank is EXCLUDED and counted, never guessed: `raw_mi` carries every ACTIVE
         subscriber each month, so treating its mere presence as an activation would invent thousands of
         false "no rebate" gaps.

REBATE LAG. A rebate rarely lands in the activation month. Rebate evidence is therefore collected across a
WINDOW of `lag_months` periods AFTER the activation period (default 6, matching the M1–M6 schedule); the
window is stated on the report. Every read stays period-indexed (the caller reads one period at a time
through `_pvariants`, the period-spelling-agnostic path) — no cross-period table scan.

READ-ONLY. Nothing here computes, changes or influences what any human is paid: it displays money the
CARRIER/PROCESSOR already recorded. There is no pay-path, no rate, no tier, no plan rule.

Everything in this module is dependency-free apart from `device_history` (itself pure), so the router
composes it and the proof harness drives it directly.
"""

import re

from app.modules.commcalc import device_history as dh

# The M1–M6 spiff column names — sourced from device_history so a drift there propagates here rather than
# silently diverging (falls back to the literal tuple if that private name is ever renamed).
SPIFF_KEYS = tuple(getattr(dh, "_MA_SPIFF_KEYS",
                           ("spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6")))

# Remaining PAID-TO-DEALER components of a raw_ma_commission row (everything in the /ma-commission/summary
# component set that is neither the rebate nor a spiff). Kept so that, per IMEI,
#     rebate + spiff_total + other_total  ==  that row's contribution to /ma-commission/summary's
#     `total_payable` (which is -Σ of exactly these columns)
# i.e. this report RECONCILES to the existing roll-up instead of quietly disagreeing with it.
MA_OTHER_COMPONENTS = ("device_margin", "consumer_margin", "consumer_financing",
                       "wallet_funding", "fees_margin")

# Rounding epsilon for money comparisons — NOT a business threshold (nothing here is tunable policy).
TOLERANCE = 0.005

STATUS_ORDER = {"partial": 0, "none": 1, "received": 2}
STATUS_LABEL = {"received": "Received", "none": "No rebate",
                "partial": "Partial / mismatch"}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 1. KEYS + DATES + PERIOD WINDOW
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def imei_key(v):
    """The MATCH KEY for a device identifier, so `raw_sales.serial_1`, `raw_payment_detail.imei`,
    `raw_mi.device_serial` and `raw_ma_commission.imei` all collapse onto one value:
      • a value with >= 10 digits → its digits-only form (an IMEI/MEID written with spaces, a trailing
        '.0' from an Excel float, or a leading apostrophe all normalize to the same key);
      • otherwise the trimmed, upper-cased string (an alphanumeric serial).
    Blank/None → ''. Reuses device_history's normalizers rather than re-deriving them."""
    s = dh.norm_key(v)
    if not s:
        return ""
    d = dh.norm_digits(s)
    if len(d) >= 10:
        return d
    return s.strip().upper()


def is_device_identifier(key):
    """True when a match key looks like a real device identifier — an IMEI/MEID/serial rather than a POS
    placeholder ('', 'N/A', '0', a 4-digit SKU tail). Numeric → >= 10 digits (IMEI 14–15, MEID 14 dec);
    alphanumeric → >= 11 chars and alphanumeric (hex MEID / vendor serial). Deliberately strict: a
    placeholder admitted here becomes a permanent phantom 'no rebate' gap on the report."""
    if not key:
        return False
    if key.isdigit():
        return len(key) >= 10
    return len(key) >= 11 and key.isalnum()


_D1 = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_D2 = re.compile(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})")


def parse_loose_date(v):
    """'YYYY-MM-DD' from a date-ish value, or None. Handles the ISO form AND the US 'MM/DD/YYYY' form —
    `raw_mi`'s date columns are TEXT holding `str(value)[:10]` of whatever the carrier report contained
    (mig 021 documents that they are NOT guaranteed ISO). Anything else → None (never a guessed date)."""
    s = str(v or "").strip()
    if not s or s.lower() in ("nan", "none", "null", "-"):
        return None
    m = _D1.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _D2.match(s)
        if not m:
            return None
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31 and 1900 <= y <= 2999):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def period_ym(period):
    """(year, month) of a month-period written ANY way ('June 2026' / '2026-06'), or None. Delegates to
    device_history.period_sort_key so the period-spelling duality is resolved in exactly one place."""
    k = dh.period_sort_key(period)
    if k >= 10 ** 9:
        return None
    return k // 100, k % 100


def canon_period(period):
    """The single canonical 'Month YYYY' spelling (device_history.canon_display_period)."""
    return dh.canon_display_period(period)


def period_window(period, lag_months=6):
    """The activation period PLUS the `lag_months` months after it, canonically spelled — the window a
    rebate for this period's activations may legitimately land in. An unparseable period → just itself
    (the caller still reads something sane); a negative lag clamps to 0."""
    ym = period_ym(period)
    if not ym:
        p = str(period or "").strip()
        return [p] if p else []
    y, m = ym
    n = max(0, int(lag_months or 0))
    out = []
    for i in range(n + 1):
        mm = m + i
        yy, mm = y + (mm - 1) // 12, (mm - 1) % 12 + 1
        out.append(canon_period(f"{yy}-{mm:02d}"))
    return out


def date_in_period(d, period):
    """True when a 'YYYY-MM-DD' date falls inside a month-period (either spelling). Blank/unparseable
    on either side → False (an unknown date NEVER counts as 'in this period')."""
    ymd = parse_loose_date(d)
    ym = period_ym(period)
    if not ymd or not ym:
        return False
    return (int(ymd[:4]), int(ymd[5:7])) == ym


def is_before_period(d, period):
    """True when a date falls in a month STRICTLY EARLIER than `period`. Used to drop a master-agent line
    that belongs to an EARLIER activation: a later-month spiff / adjustment line can land in this window's
    `period` column while its TRANSACTION date stays back where the activation actually happened.
    Attributing that money here would both mis-credit this period and manufacture a phantom "rebate with
    no activation". Blank/unparseable on either side → False (an unknown date is never assumed to be old)."""
    ymd = parse_loose_date(d)
    ym = period_ym(period)
    if not ymd or not ym:
        return False
    return (int(ymd[:4]), int(ymd[5:7])) < ym


def _s(v):
    """Trimmed string or None (never the string 'None')."""
    s = str(v).strip() if v is not None else ""
    return s or None


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 2. SOURCE ADAPTERS — raw table row → the ONE normalized shape the report layer consumes
#    Activation: {key, imei, date, period, source, evidence[], activation_type, activation_type2,
#                 sub_type, sku, device, store, store_label, rep, market, financed, platform,
#                 line_status, rows}
#    Event:      {key, kind: 'rebate'|'spiff'|'other', amount (paid-to-dealer), date, period, label,
#                 source, source_table, month (spiffs only), store, rep}
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def ma_activation(row, market_of=None):
    """One normalized ACTIVATION from a `raw_ma_commission` row. `market_of` (optional) maps the
    processor merchant account to a market — MA data is processor-account-keyed with NO store_mapping
    linkage (the documented `/ma-commission/summary` deviation), so it normally stays None."""
    k = imei_key(row.get("imei"))
    d = parse_loose_date(row.get("tx_date"))
    per = canon_period(row.get("period"))
    if not per:
        try:
            mo, yr = int(row.get("period_month") or 0), int(row.get("period_year") or 0)
            if 1 <= mo <= 12 and yr:
                per = canon_period(f"{yr}-{mo:02d}")
        except (TypeError, ValueError):
            per = ""
    if not per and d:
        per = canon_period(d[:7])
    store = _s(row.get("merchant_account_id"))
    return {
        "key": k, "imei": _s(row.get("imei")) or k, "date": d, "period": per or None,
        "source": "ma", "evidence": ["ma_commission"],
        "activation_type": _s(row.get("activation_type")),
        "activation_type2": _s(row.get("activation_type2")),
        "sub_type": _s(row.get("sub_type")),
        "sku": _s(row.get("sku")), "device": _s(row.get("sku")),
        "store": store, "store_label": store, "rep": _s(row.get("user_name")),
        "market": (market_of(store) if (market_of and store) else None) or None,
        "financed": _s(row.get("is_financed")), "platform": _s(row.get("platform")),
        "line_status": _s(row.get("line_status")), "rows": 1,
    }


def ma_events(row):
    """The paid-to-dealer money events on ONE `raw_ma_commission` row: the rebate, the M1–M6 spiffs, and
    the remaining payable components ('other'). EVERY amount is normalized through
    `device_history.ma_paid` (negative=paid → POSITIVE; a charge/clawback → NEGATIVE) — sign preserved,
    never dropped. Zero/blank cells produce NO event, so a rebate cell of 0 correctly reads as 'no rebate
    line' rather than as a $0 receipt. `mrc_net_discount` is the subscriber plan price and is NOT a
    payout — it is deliberately absent."""
    k = imei_key(row.get("imei"))
    d = parse_loose_date(row.get("tx_date"))
    per = canon_period(row.get("period")) or (canon_period(d[:7]) if d else None)
    store, rep = _s(row.get("merchant_account_id")), _s(row.get("user_name"))
    base = {"key": k, "date": d, "period": per, "source": "ma",
            "source_table": "raw_ma_commission", "store": store, "rep": rep}
    out = []
    amt = dh.ma_paid(row.get("rebate"))
    if amt:
        out.append({**base, "kind": "rebate", "amount": amt, "label": "Rebate", "month": None})
    for i, kk in enumerate(SPIFF_KEYS, start=1):
        a = dh.ma_paid(row.get(kk))
        if a:
            out.append({**base, "kind": "spiff", "amount": a, "label": f"Spiff M{i}", "month": i})
    oth = round(sum(dh.ma_paid(row.get(c)) for c in MA_OTHER_COMPONENTS), 2)
    if oth:
        out.append({**base, "kind": "other", "amount": oth,
                    "label": "Margin / financing / fees", "month": None})
    return out


def epay_activation_from_sale(row, market_of=None):
    """One normalized ACTIVATION from a `raw_sales` device line (a POS line carrying a serial). This is
    the same sale leg device-history reads (IMEI → serial_1)."""
    k = imei_key(row.get("serial_1"))
    d = parse_loose_date(row.get("trans_date"))
    store = _s(row.get("store"))
    return {
        "key": k, "imei": _s(row.get("serial_1")) or k, "date": d,
        "period": canon_period(row.get("period")) or (canon_period(d[:7]) if d else None),
        "source": "epay", "evidence": ["sale"],
        "activation_type": _s(row.get("contract_type")), "activation_type2": None, "sub_type": None,
        "sku": _s(row.get("sku")), "device": _s(row.get("product_desc")),
        "store": store, "store_label": store,
        "rep": _s(row.get("salesperson")) or _s(row.get("user_login")),
        "market": (market_of(store) if (market_of and store) else None) or None,
        "financed": None, "platform": None, "line_status": None, "rows": 1,
    }


def epay_activation_from_mi(row, market_of=None):
    """One normalized ACTIVATION from a `raw_mi` residual line. The caller admits a row ONLY when
    `mi_activation_date` falls in the reported period (see the module docstring) — raw_mi carries every
    ACTIVE subscriber every month, so mere presence is not an activation."""
    k = imei_key(row.get("device_serial"))
    d = parse_loose_date(row.get("mi_activation_date"))
    return {
        "key": k, "imei": _s(row.get("device_serial")) or k, "date": d,
        "period": canon_period(row.get("period")) or (canon_period(d[:7]) if d else None),
        "source": "epay", "evidence": ["residual"],
        "activation_type": None, "activation_type2": None, "sub_type": None,
        "sku": None, "device": _s(row.get("customer_plan")),
        "store": None, "store_label": None, "rep": _s(row.get("rep_username")),
        "market": None, "financed": None, "platform": None,
        "line_status": _s(row.get("subscriber_status")), "rows": 1,
    }


def epay_event(row, comp_of):
    """One money event from a `raw_payment_detail` row. The rebate-vs-other split REUSES the existing
    classifier chain (`comp_of` = discrepancy_engine.parse_payment_type → comp_type, then
    device_history.categorize_comp) — no second classification is invented here. Amount is read AS-IS:
    payment-detail amounts are already stored positive = paid to the dealer (no sign flip, unlike MA)."""
    k = imei_key(row.get("imei"))
    ptype = _s(row.get("payment_type")) or ""
    amt = dh.to_amount(row.get("amount"))
    amt = round(amt, 2) if amt is not None else 0.0
    per = canon_period(row.get("period"))
    if not per:
        try:
            mo, yr = int(row.get("period_month") or 0), int(row.get("period_year") or 0)
            if 1 <= mo <= 12 and yr:
                per = canon_period(f"{yr}-{mo:02d}")
        except (TypeError, ValueError):
            per = ""
    d = parse_loose_date(row.get("payment_date"))
    if not per and d:
        per = canon_period(d[:7])
    comp = ""
    try:
        comp = comp_of(ptype) if comp_of else ""
    except Exception:                                     # a classifier hiccup must not lose the row
        comp = ""
    kind = "rebate" if dh.categorize_comp(comp) == "rebate" else "other"
    return {"key": k, "kind": kind, "amount": amt, "date": d, "period": per or None,
            "label": ptype or ("Reimbursement" if kind == "rebate" else "Other compensation"),
            "source": "epay", "source_table": "raw_payment_detail", "month": None,
            "store": _s(row.get("business_address")), "rep": _s(row.get("rep_username")),
            "comp_type": comp or None}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 3. MERGE + CLASSIFY
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_EV_RANK = {"ma_commission": 0, "sale": 1, "residual": 2}
_FILL_FIELDS = ("imei", "activation_type", "activation_type2", "sub_type", "sku", "device",
                "store", "store_label", "rep", "market", "financed", "platform", "line_status")


def merge_activations(acts):
    """Collapse the activation candidates onto ONE row per device key. The same handset can arrive from
    two evidence legs (a POS sale line AND a residual line) — that is corroboration, not two activations.
      • `evidence` is the UNION, ordered (ma_commission → sale → residual);
      • `date` = the EARLIEST known activation date (None only when no leg carried one);
      • every other field is filled from the highest-ranked leg that actually has a value, so a
        residual-only leg still contributes a rep when the sale leg is missing one;
      • `rows` counts the contributing source rows;
      • `sources` = the distinct feeds ('ma' / 'epay') that saw this device.
    Rows with a non-device key are the caller's problem — they never get here."""
    by = {}
    for a in acts or []:
        k = a.get("key")
        if not k:
            continue
        cur = by.get(k)
        if cur is None:
            by[k] = {**a, "evidence": list(a.get("evidence") or []),
                     "sources": [a.get("source")] if a.get("source") else [],
                     "rows": int(a.get("rows") or 1), "_rank": _EV_RANK.get(
                         (a.get("evidence") or [None])[0], 9)}
            continue
        cur["rows"] += int(a.get("rows") or 1)
        for e in (a.get("evidence") or []):
            if e not in cur["evidence"]:
                cur["evidence"].append(e)
        if a.get("source") and a["source"] not in cur["sources"]:
            cur["sources"].append(a["source"])
        ad, cd = a.get("date"), cur.get("date")
        if ad and (not cd or ad < cd):
            cur["date"] = ad
            cur["period"] = cur.get("period") or a.get("period")
        if not cur.get("period"):
            cur["period"] = a.get("period")
        rank = _EV_RANK.get((a.get("evidence") or [None])[0], 9)
        for f in _FILL_FIELDS:
            if not cur.get(f) and a.get(f):
                cur[f] = a[f]
            elif a.get(f) and rank < cur["_rank"]:
                cur[f] = a[f]
        cur["_rank"] = min(cur["_rank"], rank)
    for v in by.values():
        v.pop("_rank", None)
        v["evidence"].sort(key=lambda e: _EV_RANK.get(e, 9))
        if len(v.get("sources") or []) > 1:
            v["source"] = "both"
    return by


def classify_rebate(rebate_events, expected=None, tolerance=TOLERANCE):
    """The rebate status of ONE activation. Returns `(status, reason)` with status ∈
      'received' — at least one rebate line, netting POSITIVE, with nothing reversing it;
      'none'     — NO rebate line at all against this IMEI (the gap the report exists to surface);
      'partial'  — the spec's "partial / mismatch": rebate lines exist but the money does not stand up —
                   paid then partly or wholly clawed back, a net-negative (reversal), or (when the caller
                   supplies an `expected`) short of it.
    `expected` is an OPTIONAL per-IMEI expectation; there is no expectation feed today (nothing invents
    one), so it is normally None and status is decided by the recorded lines alone. `tolerance` is a
    rounding epsilon, not a business threshold."""
    evs = [e for e in (rebate_events or []) if (e.get("kind") == "rebate")]
    if not evs:
        return "none", "no rebate line recorded against this IMEI in the window"
    net = round(sum(float(e.get("amount") or 0) for e in evs), 2)
    neg = [e for e in evs if float(e.get("amount") or 0) < -tolerance]
    pos = [e for e in evs if float(e.get("amount") or 0) > tolerance]
    if net > tolerance:
        if neg:
            return "partial", ("rebate paid then partly reversed "
                               f"({len(pos)} credit(s), {len(neg)} reversal(s))")
        if expected is not None and net < float(expected) - tolerance:
            return "partial", f"received {net:,.2f} of an expected {float(expected):,.2f}"
        return "received", None
    if net < -tolerance:
        return "partial", "net NEGATIVE — the rebate was reversed / charged back"
    return "partial", "rebate lines net to zero — paid then fully reversed"


def _median(vals):
    v = sorted(vals)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else round((v[n // 2 - 1] + v[n // 2]) / 2, 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. THE REPORT
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def tiles_for(rows, orphans):
    """The headline count + $ tiles over a row set. Called BOTH by `build_report` (unfiltered) and again
    after `apply_filters`, so the tiles always describe exactly what the table — and therefore the export
    — shows (RULE FOUR/FIVE: what you see is what exports). The GAP bucket is first-class: its count is a
    real number and its $ is an explicitly-labelled ESTIMATE (there is no recorded amount for money that
    was never paid), never a silently invented figure."""
    rows = rows or []
    received = [r for r in rows if r.get("rebate_status") == "received"]
    gaps = [r for r in rows if r.get("rebate_status") == "none"]
    partial = [r for r in rows if r.get("rebate_status") == "partial"]
    med = _median([r["rebate"] for r in received]) if received else None
    return {
        "activations": len(rows),
        "with_rebate": {"count": len(received), "amount": round(sum(r["rebate"] for r in received), 2)},
        "no_rebate": {
            "count": len(gaps),
            "estimated_amount": (round(len(gaps) * med, 2) if (med is not None and gaps) else None),
            "estimate_basis": (
                f"{len(gaps)} activation(s) × the median received rebate of ${med:,.2f} — an ESTIMATE of "
                "the exposure, not a recorded amount" if (med is not None and gaps) else
                ("no rebate has been received on any activation in this period, so there is nothing to "
                 "estimate an exposure from" if gaps else None)),
        },
        "partial": {"count": len(partial), "amount": round(sum(r["rebate"] for r in partial), 2)},
        "rebate_total": round(sum(r["rebate"] for r in rows), 2),
        "spiff_total": round(sum(r["spiff_total"] for r in rows), 2),
        "other_total": round(sum(r["other_paid"] for r in rows), 2),
        "total_received": round(sum(r["total_received"] for r in rows), 2),
        "orphan": {"count": len(orphans or []),
                   "amount": round(sum(o["amount"] for o in (orphans or [])), 2)},
    }


def build_report(activations, events, *, expected_of=None, tolerance=TOLERANCE):
    """Merge activations + money events into the report payload. PURE — the caller has already read and
    org-scoped every row.
      activations : normalized Activation dicts (from the adapters above)
      events      : normalized Event dicts (rebate / spiff / other), from ANY window period
      expected_of : optional `key -> expected rebate $` (no expectation feed exists today → None)
    Returns {rows, orphans, tiles} where
      • `rows`    = one per activated IMEI, with rebate / spiff / other / total and a `rebate_status`
                    of 'received' | 'none' | 'partial' (the spec's "partial/mismatch"; a slash-free value
                    so it is safe in a query string — `rebate_status_label` carries the display text);
      • `orphans` = rebate events whose IMEI has NO activation in this period's universe — a data-quality
                    signal, NOT a claim of error (the commonest cause is an activation in an EARLIER
                    period, which the note says out loud);
      • `tiles`   = the count + $ headline numbers, INCLUDING the gap bucket, so "no rebate" is
                    first-class rather than merely absent."""
    merged = merge_activations(activations)
    ev_by = {}
    for e in (events or []):
        k = e.get("key")
        if not k:
            continue
        ev_by.setdefault(k, []).append(e)

    rows = []
    for k, a in merged.items():
        evs = ev_by.get(k) or []
        reb_evs = [e for e in evs if e.get("kind") == "rebate"]
        spf_evs = [e for e in evs if e.get("kind") == "spiff"]
        oth_evs = [e for e in evs if e.get("kind") == "other"]
        rebate = round(sum(float(e.get("amount") or 0) for e in reb_evs), 2)
        spiff = round(sum(float(e.get("amount") or 0) for e in spf_evs), 2)
        other = round(sum(float(e.get("amount") or 0) for e in oth_evs), 2)
        by_month = {f"m{i}": 0.0 for i in range(1, 7)}
        for e in spf_evs:
            m = e.get("month")
            if isinstance(m, int) and 1 <= m <= 6:
                by_month[f"m{m}"] = round(by_month[f"m{m}"] + float(e.get("amount") or 0), 2)
        exp = (expected_of or {}).get(k) if expected_of else None
        status, reason = classify_rebate(reb_evs, expected=exp, tolerance=tolerance)
        # Rebate provenance: WHEN and from WHICH feed the money was recorded (earliest credit).
        credits = sorted([e for e in reb_evs if float(e.get("amount") or 0) > tolerance],
                         key=lambda e: (e.get("date") or "9999-99-99",
                                        dh.period_sort_key(e.get("period"))))
        first = credits[0] if credits else None
        rows.append({
            "imei": a.get("imei"), "key": k,
            "activation_date": a.get("date"), "period": a.get("period"),
            "source": a.get("source"), "sources": a.get("sources") or [],
            "evidence": a.get("evidence") or [],
            "activation_type": a.get("activation_type"),
            "activation_type2": a.get("activation_type2"),
            "sub_type": a.get("sub_type"),
            "device": a.get("device"), "sku": a.get("sku"),
            "store": a.get("store"), "store_label": a.get("store_label") or a.get("store"),
            "rep": a.get("rep"), "market": a.get("market"),
            "financed": a.get("financed"), "platform": a.get("platform"),
            "line_status": a.get("line_status"),
            "rebate": rebate, "rebate_lines": len(reb_evs),
            "rebate_date": (first or {}).get("date"),
            "rebate_period": (first or {}).get("period"),
            "rebate_source": (first or {}).get("source_table"),
            "rebate_label": (first or {}).get("label"),
            "spiff_total": spiff, "spiff_by_month": by_month, "spiff_lines": len(spf_evs),
            "other_paid": other, "other_lines": len(oth_evs),
            "total_received": round(rebate + spiff + other, 2),
            "expected_rebate": (round(float(exp), 2) if exp is not None else None),
            "rebate_status": status, "rebate_status_label": STATUS_LABEL.get(status, status),
            "rebate_status_reason": reason,
            "activation_rows": a.get("rows") or 1,
        })
    rows.sort(key=lambda r: (STATUS_ORDER.get(r["rebate_status"], 9),
                             r.get("activation_date") or "9999-99-99", r.get("imei") or ""))

    # ── the INVERSE gap: a rebate whose IMEI has no activation in this period's universe ───────────
    orph_by = {}
    for k, evs in ev_by.items():
        if k in merged:
            continue
        reb = [e for e in evs if e.get("kind") == "rebate"]
        if not reb:
            continue
        amt = round(sum(float(e.get("amount") or 0) for e in reb), 2)
        first = min(reb, key=lambda e: (e.get("date") or "9999-99-99",
                                        dh.period_sort_key(e.get("period"))))
        orph_by[k] = {"imei": k, "key": k, "amount": amt, "lines": len(reb),
                      "date": first.get("date"), "period": first.get("period"),
                      "label": first.get("label"), "source": first.get("source_table"),
                      "store": first.get("store"), "rep": first.get("rep")}
    orphan_rows = sorted(orph_by.values(), key=lambda o: (-abs(o["amount"]), o["imei"]))

    return {"rows": rows, "orphans": orphan_rows, "tiles": tiles_for(rows, orphan_rows)}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 5. SERVER-SIDE FILTERING + PICK-DON'T-TYPE OPTIONS (RULE THREE / FOUR / FIVE)
#    Options are computed from the UNFILTERED rows so a picker never collapses to the current selection;
#    filtering happens server-side so tiles, table AND exports agree (what-you-see-is-what-exports).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _fold(v):
    return str(v or "").strip().lower()


def filter_options(rows, orphans=None):
    """The pick-don't-type option lists for the standard filter bar + the appended facets, taken from the
    values PRESENT IN THE DATA (never a hard-coded list). Case-variant spellings collapse to one option,
    keeping the first-seen display casing."""
    def _opts(get, extra_rows=()):
        seen = {}
        for r in list(rows or []) + list(extra_rows or []):
            v = get(r)
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            seen.setdefault(s.lower(), s)
        return sorted(seen.values(), key=lambda s: s.lower())

    return {
        "store_options": _opts(lambda r: r.get("store_label") or r.get("store"), orphans or []),
        "rep_options": _opts(lambda r: r.get("rep"), orphans or []),
        "market_options": _opts(lambda r: r.get("market")),
        "activation_type_options": _opts(lambda r: r.get("activation_type")),
        "platform_options": _opts(lambda r: r.get("platform")),
        "financed_options": _opts(lambda r: r.get("financed")),
        "status_options": [{"id": s, "label": STATUS_LABEL[s]}
                           for s in ("none", "partial", "received")],
    }


def _sel(csv):
    return {s.strip().lower() for s in str(csv or "").split(",") if s.strip()}


def apply_filters(rows, *, stores="", reps="", markets="", status="",
                  activation_type="", platform="", financed="", source=""):
    """Narrow the report rows by the standard set (store / rep / market) plus the appended facets
    (rebate status / activation type / platform / financed / source). Every comparison is
    case-insensitive on the value PRESENT IN THE ROW; a blank selection means 'no narrowing'."""
    st, rp, mk = _sel(stores), _sel(reps), _sel(markets)
    stt, at = _sel(status), _sel(activation_type)
    pf, fin, src = _sel(platform), _sel(financed), _sel(source)
    out = []
    for r in rows or []:
        if st and _fold(r.get("store_label") or r.get("store")) not in st:
            continue
        if rp and _fold(r.get("rep")) not in rp:
            continue
        if mk and _fold(r.get("market")) not in mk:
            continue
        if stt and _fold(r.get("rebate_status")) not in stt:
            continue
        if at and _fold(r.get("activation_type")) not in at:
            continue
        if pf and _fold(r.get("platform")) not in pf:
            continue
        if fin and _fold(r.get("financed")) not in fin:
            continue
        if src and not ({_fold(s) for s in (r.get("sources") or [r.get("source")])} & src):
            continue
        out.append(r)
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 6. HONEST HEADER TEXT (the definition the report states out loud)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def definition_note(sources, basis="both"):
    """The activation definition, in words, for the report header + every export — so this surface and
    the Device History widget can never be read as disagreeing. `sources` = which paths actually produced
    data for this org (resolved by data presence, never by tenant/carrier name)."""
    parts = []
    if "ma" in sources:
        parts.append("a master-agent commission line whose transaction date falls in the period "
                     "(raw_ma_commission — the processor's own per-activation feed)")
    if "epay" in sources:
        legs = []
        if basis in ("both", "sales"):
            legs.append("a device line in the period's B2B sales (raw_sales.serial_1)")
        if basis in ("both", "residual"):
            legs.append("a residual line whose MI activation date falls in the period "
                        "(raw_mi.mi_activation_date)")
        parts.append(" and/or ".join(legs))
    if not parts:
        return ("No activation source carries data for this org in this period — neither a master-agent "
                "commission feed nor B2B sales / residual lines.")
    return ("Activation = " + "; or ".join(parts) +
            ". These are the same sources the Device History lookup reads, so the two surfaces cannot "
            "disagree. A residual line with no activation date is EXCLUDED and counted, never guessed.")


def sign_note(sources):
    """The sign convention actually applied, stated per source (they differ, deliberately)."""
    parts = []
    if "ma" in sources:
        parts.append("master-agent amounts are stored NEGATIVE = paid to the dealer and are shown "
                     "sign-flipped (positive = money you received; negative = a charge/clawback)")
    if "epay" in sources:
        parts.append("payment-detail amounts are already stored positive = paid to the dealer and are "
                     "shown as-is")
    return "; ".join(parts) or None


def window_note(window, lag_months):
    """The rebate window, in words."""
    if not window:
        return None
    return (f"Rebate evidence is collected across {len(window)} period(s): {window[0]} → {window[-1]} "
            f"(the activation month plus {lag_months} month(s) of lag). A rebate that lands later than "
            "that window will read as a gap here — widen the lag to see it.")


# ── the PAGE gate (owner directive 2026-07-29: this report has NO default access) ────────────────
GRANT_KEY = "imei_rebates"


def imei_rebates_allowed(caller):
    """Gate the WHOLE REPORT. DEFAULT-CLOSED, grantable via the DATA_GRANTS 'imei_rebates' key — the
    same resolution SHAPE as `device_history.device_commission_allowed`, applied one level up: there it
    hides a money section inside an otherwise-open widget; here it decides whether the report exists at
    all for this caller (owner directive 2026-07-29 — counts, statuses and IMEIs are themselves
    restricted, not just the dollars). Mirrors the frontend `hasDataGrant(perms, 'imei_rebates')`.

    PURE over an already-resolved caller dict (no DB, no HTTP) so it is unit-provable:
      super_admin / perms.scope == 'all' / role == 'admin'          -> allow
      'imei_rebates' in perms.modules, or perms.data.imei_rebates truthy -> allow
      else (including caller=None, i.e. an unresolvable token)      -> DENY

    NOT the money gate. A granted non-admin still passes through the INDEPENDENT
    `_can_view_carrier_residual` check, which nulls every $ when the tenant runs residual visibility
    'permissioned'. Two gates, two questions: "may you open this report" and "may you see its dollars".
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
