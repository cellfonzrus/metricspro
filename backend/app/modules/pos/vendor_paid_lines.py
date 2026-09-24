"""WHICH SALE LINES THE VENDOR PAID — they print at $0.00 on a receipt rebuilt from the reports, as the
register prints them. PURE (stdlib).

OWNER (2026-09-24, verbatim): *"the same receipt shown above shows the commission earned for each line but
not the cost of phone charged to the customer"* — and, asked how those lines should print: *"$0.00 like
register"*.

THE FACT, MEASURED (live tenant, 10,823 invoices, index §30.14a). A line-level sales export carries, beside
the goods the customer bought, the lines a VENDOR pays the store for (rate-plan rebates, kickers, feature
perks, installment rebate amounts, promotions). The register prints those at $0.00; the export carries
their amount. On every invoice the vendor-paid lines add up to the invoice's non-customer tenders (the
vendor rebate applied as payment — `closing.router.is_customer_payment` false), which is how the rule is
LEARNED and PROVEN here without a word of it in code: 3,816 of 3,882 invoices tie with the seven learned
category words; 0 phone lines are caught.

THE RULE IS CONFIG, NEVER CODE (RULE TWO). One per-org row — `pos.pos_settings` key `vendor_paid_lines`
(CONFIG_KEY; the POS module's per-org kv, written only through `pos.router.upsert_pos_setting`) — holding
the category-path SEGMENTS whose lines the vendor pays. House default: no rule → no line prints at $0.00
(byte-identical to before). The segments are PROPOSED from the tenant's own lines (`propose`), shown with
their proof (`proof`) and saved only when the person confirms them.

TWO GUARDS, both generic:
  · a vendor-paid line is not merchandise — a segment that appears on ANY line with a cost of goods is
    never proposed and is refused on save (`costed_segments`): the phone, the accessory, the device;
  · a rule must tie MORE invoices than no rule at all, or it is refused on save.

Readers: pos/sales_from_reports.build_document (injected `vendor_paid` predicate, built here by `matcher`),
the POS receipts page card (GET/PUT /pos/sales-from-reports/vendor-paid-lines). Locked by
backend/harness_pos_sales_from_reports_lock.py.
"""
from __future__ import annotations

CONFIG_KEY = "vendor_paid_lines"
FIELD = "category"                 # the landed line column the segments are read from (raw_sales.category)
AMOUNT = "ext_price"               # the landed line's total
COST = "total_cost"                # the landed line's cost of goods
TXN = "trans_id"                   # the invoice number both landings carry
SEP = ">>"                         # the category path separator the exports use
MAX_TOKENS = 25                    # the proposal never grows past this many segments


def _s(v):
    return "" if v is None else str(v).strip()


def _f(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _m(x):
    return round(float(x) + 0.0, 2)


def segments(text):
    """A category path → its lower-cased, stripped segments ('>> A >> B' → ['a', 'b'])."""
    return [p.strip().lower() for p in _s(text).split(SEP) if p.strip()]


def resolve_config(raw):
    """The org's saved row value → {'tokens': [...], 'confirmed_by', 'confirmed_at'}; anything else → the
    house default (no tokens: no line is vendor-paid)."""
    v = raw if isinstance(raw, dict) else {}
    toks = []
    for t in v.get("tokens") or []:
        t = _s(t).lower()
        if t and t not in toks:
            toks.append(t)
    return {"tokens": toks, "confirmed_by": v.get("confirmed_by"), "confirmed_at": v.get("confirmed_at")}


def matcher(rule):
    """The predicate the receipt builder is handed: line → is it vendor-paid under `rule`. No tokens →
    always False."""
    toks = frozenset((rule or {}).get("tokens") or ())
    if not toks:
        return lambda line: False
    return lambda line: bool(toks.intersection(segments((line or {}).get(FIELD))))


def costed_segments(lines):
    """Every segment that appears on a line with a cost of goods — never a vendor-paid segment."""
    out = set()
    for ln in lines or []:
        if abs(_f(ln.get(COST))) > 0.004:
            out.update(segments(ln.get(FIELD)))
    return out


def non_customer_by_invoice(tenders, pays):
    """Σ of the invoice tender rows that are NOT the customer's payment, per invoice number (`pays` =
    closing.router.is_customer_payment, injected)."""
    out = {}
    for t in tenders or []:
        if _s(t.get("role")) not in ("", "tender"):
            continue
        if pays(_s(t.get("tender_class")).lower()):
            continue
        k = _s(t.get(TXN))
        out[k] = out.get(k, 0.0) + _f(t.get("amount"))
    return out


def _index(lines, invoices):
    """invoice → [(frozenset(segments), amount)] over the invoices that have a header."""
    by = {}
    for ln in lines or []:
        k = _s(ln.get(TXN))
        if k in invoices:
            by.setdefault(k, []).append((frozenset(segments(ln.get(FIELD))), _f(ln.get(AMOUNT))))
    return by


def _ties(by, target, toks):
    n = 0
    for k, ls in by.items():
        if abs(sum(a for s, a in ls if s & toks) - target.get(k, 0.0)) < 0.005:
            n += 1
    return n


def _score(by, target, toks):
    """(invoices that tie, −Σ |vendor-paid lines − vendor-paid tenders|): more ties first, then closer. The
    second key lets a segment in that brings every invoice NEARER (an invoice needs several segments before
    it ties) and keeps out one that pushes them away (a financed offset, a customer-paid fee)."""
    ties, resid = 0, 0.0
    for k, ls in by.items():
        d = abs(sum(a for s, a in ls if s & toks) - target.get(k, 0.0))
        if d < 0.005:
            ties += 1
        else:
            resid += d
    return (ties, -round(resid, 2))


def proof(lines, invoice_numbers, nc_by_inv, rule):
    """How many invoices (with lines and a header) have their vendor-paid lines add up to their
    non-customer tenders under `rule` — beside the same count with no rule. The words say it."""
    inv = {_s(i) for i in invoice_numbers or []}
    by = _index(lines, inv)
    toks = frozenset((rule or {}).get("tokens") or ())
    tied = _ties(by, nc_by_inv, toks)
    base = _ties(by, nc_by_inv, frozenset())
    matched = [ln for ln in lines or [] if _s(ln.get(TXN)) in inv and toks.intersection(segments(ln.get(FIELD)))]
    off = []
    for k, ls in by.items():
        d = _m(sum(a for s, a in ls if s & toks) - nc_by_inv.get(k, 0.0))
        if abs(d) >= 0.005:
            off.append({"invoice": k, "vendor_paid_lines": _m(sum(a for s, a in ls if s & toks)),
                        "non_customer_tenders": _m(nc_by_inv.get(k, 0.0)), "difference": d})
    n = len(by)
    words = (f"the lines this rule marks as vendor-paid add up to the invoice's vendor-paid tenders on {tied:,} of {n:,} invoice(s)"
             f" ({(tied / n if n else 0):.1%}); with no rule {base:,}") if n else "no invoice has both a header and lines in this period"
    return {"invoices": n, "tied": tied, "tied_without_rule": base, "lines_matched": len(matched),
            "amount_matched": _m(sum(_f(ln.get(AMOUNT)) for ln in matched)), "off": len(off),
            "off_sample": sorted(off, key=lambda o: -abs(o["difference"]))[:15], "words": words}


def propose(lines, invoice_numbers, nc_by_inv):
    """LEARN the segments from the tenant's own lines: greedily add the segment (never one that appears on
    a costed line) that makes the most invoices tie their vendor-paid lines to their non-customer tenders;
    stop when no segment adds a tie; then drop any segment the rule no longer needs. Returns the tokens in
    the order they were learned with the running count (the proposal's own evidence) and the proof."""
    inv = {_s(i) for i in invoice_numbers or []}
    by = _index(lines, inv)
    costed = costed_segments(lines)
    cands = sorted({s for ls in by.values() for seg, a in ls if abs(a) > 0.004 for s in seg} - costed)
    toks, steps = set(), []
    best = _score(by, nc_by_inv, frozenset())
    while len(toks) < MAX_TOKENS:
        score, pick = max(((_score(by, nc_by_inv, frozenset(toks | {c})), c) for c in cands if c not in toks),
                          default=(best, None))
        if pick is None or score <= best:
            break
        toks.add(pick)
        best = score
        steps.append({"token": pick, "tied": score[0]})
    for c in sorted(toks):
        s = _score(by, nc_by_inv, frozenset(toks - {c}))
        if s >= best:
            toks.discard(c)
            best = s
            steps = [x for x in steps if x["token"] != c]
    ordered = [x["token"] for x in steps]
    return {"tokens": ordered, "steps": steps, "costed_excluded": len(costed & {s for ls in by.values() for seg, _a in ls for s in seg}),
            "proof": proof(lines, inv, nc_by_inv, {"tokens": ordered})}


def check_save(tokens, lines, invoice_numbers, nc_by_inv):
    """The refusals for saving `tokens` (plain sentences; [] = may be saved): a segment that appears on a
    line with a cost of goods; a rule that ties no more invoices than no rule at all."""
    toks = [t for t in (_s(t).lower() for t in tokens or []) if t]
    out = []
    bad = sorted(set(toks) & costed_segments(lines))
    if bad:
        out.append("These words also name goods with a cost (a phone, an accessory) — a vendor-paid line never has one, so they would "
                   "print merchandise at $0.00: " + ", ".join(f"'{b}'" for b in bad) + ".")
    if toks:
        p = proof(lines, invoice_numbers, nc_by_inv, {"tokens": toks})
        if p["invoices"] and p["tied"] <= p["tied_without_rule"]:
            out.append(f"This rule ties {p['tied']:,} invoice(s) — no more than with no rule ({p['tied_without_rule']:,}); it does not pick out "
                       "the lines the vendor paid.")
    return out
