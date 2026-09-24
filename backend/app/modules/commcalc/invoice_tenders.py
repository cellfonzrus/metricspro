"""SALES BY INVOICE — the tender split and the tax columns of an invoice-level sales export. PURE.

OWNER (2026-09-21, verbatim): *"sales by invoice report also has the tender types on the report, need to
capture that as well"* — *"tender types is in columns"*.

THE FILE, MEASURED: one row per INVOICE (10,823 rows, 41 columns): the invoice header (who / where /
when, subtotal, adjustments, net sales, cost, gross profit, extra charges, donations, invoice total,
coupons, gift-card sales, non-revenue sales, channel / region / district), then ONE AMOUNT COLUMN PER
TENDER TYPE (twelve of them — the card brands, their non-integrated twins, cash, debit PIN, a vendor
rebate applied as payment), then two tax columns (the total, and one named jurisdiction). The invoice
number is the same `Invoice #` the line-level export carries as `trans_id`.

THE CLASS. The tender columns are NOT fixed: a POS names them per tenant and per register set-up
(a second brand, a second jurisdiction, a non-integrated twin). So which columns are tenders, and what
kind of payment each is, is DECLARED at intake (step 2.5b) — proposed from the header words, confirmed
by the person, saved as config — and the landing writes ONE ROW PER (invoice, tender column) with an
amount ≠ 0, carrying the column's canonical tender CLASS. A tax component column rides the same grain
with role 'tax'.

ONE VOCABULARY, DEREFERENCED — NEVER COPIED. The tender classes are `closing.router.TENDER_VOCAB` (the
3-way recon's axis + the finer invoice classes, one home); the header → class ladder is
`closing.router.tender_class`; the per-org overrides are `commcalc.closing_tender_map` rows (mig 111)
with report='invoice', resolved through `closing.tender_config.make_resolver` exactly as the X-report
and sales legs resolve theirs. THIS MODULE SPELLS NO TENDER WORD: every classifier arrives injected
(`resolve`, `keyed_of`, `recon_class_of`), so the proof harness drives the real ones and the lock
(harness_tender_vocab_lock.py) can fail the build on a second list.

RULE TWO: no carrier, POS vendor, tenant, card brand or jurisdiction is named here. stdlib only.
"""
import re

ROLE_TENDER = "tender"
ROLE_TAX = "tax"
ROLE_IGNORE = "ignore"
ROLES = (ROLE_TENDER, ROLE_TAX, ROLE_IGNORE)
# the config-map pseudo tender key under which the "keyed manually" column flags are remembered
# (closing_tender_map report='invoice'): a per-column fact, not a class — never on any recon axis
KEYED_FLAG_KEY = "keyed_manually"
MAP_REPORT = "invoice"
# a column header that names TAX (a total or a jurisdiction component) — generic word, not a jurisdiction
_TAX_RE = re.compile(r"\btax", re.I)
_NUM_RE = re.compile(r"^\(?[-+]?[$£€]?\s*[\d,]*\.?\d+\s*\)?$")


def _s(v):
    return "" if v is None else str(v).strip()


def _sf(v):
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    t = _s(v).replace("$", "").replace(",", "")
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        return float(t)
    except Exception:
        return 0.0


def _is_num(v):
    if v is None or _s(v) == "":
        return False
    if isinstance(v, (int, float)):
        return True
    return bool(_NUM_RE.match(_s(v)))


def money(x):
    return round(float(x or 0.0), 2)


# ── 2.5b — WHICH COLUMNS ARE TENDER TYPES, AND WHAT KIND OF PAYMENT IS EACH ──────────────────────
def column_stats(headers, records):
    """Per header over the DATA rows: numeric cells, blank cells, text cells, Σ and the count of
    non-zero amounts. A column that is money looks like numbers-or-blank on every row."""
    out = {}
    for h in headers or []:
        if not _s(h):
            continue
        num = blank = text = nz = 0
        total = 0.0
        for r in records or []:
            v = r.get(h)
            if v is None or _s(v) == "":
                blank += 1
            elif _is_num(v):
                num += 1
                a = _sf(v)
                total += a
                if abs(a) >= 0.005:
                    nz += 1
            else:
                text += 1
        out[_s(h)] = {"numeric": num, "blank": blank, "text": text, "sum": money(total), "nonzero": nz}
    return out


def is_money_column(st):
    return st["numeric"] > 0 and st["text"] == 0


def suggest_columns(headers, records, mapped_headers, resolve, keyed_of, tax_header=None, earlier=None):
    """The 2.5b proposal: for every column NOT mapped to a header field of the layout, a role
    (tender / tax component / not proposed) with its Σ and cell counts.

      resolve(header) → canonical tender class or None — the org's closing_tender_map (report='invoice')
                        through tender_config.make_resolver, falling back to closing.router.tender_class
      keyed_of(header) → bool — closing.router.keyed_manually (a non-integrated twin: same class, flagged)
      tax_header       → the header mapped as the invoice's TOTAL tax (never a component)
      earlier          → {header lower: {"tender_class", "keyed_manually", "role"}} the org's saved map
                         (the "your earlier choice" provenance)

    Nothing is guessed: a money column no rule places is listed as `unplaced` (the person may declare
    it a tender by hand); a text column is never a tender."""
    stats = column_stats(headers, records)
    mapped = {_s(h).lower() for h in (mapped_headers or []) if _s(h)}
    earlier = {str(k).lower(): v for k, v in (earlier or {}).items()}
    columns, unplaced = [], []
    for h, st in stats.items():
        hl = h.lower()
        if hl in mapped and hl != _s(tax_header).lower():
            continue                      # a header field of the invoice row (net sales, invoice total …)
        if not is_money_column(st):
            continue
        prev = earlier.get(hl)
        if hl == _s(tax_header).lower():
            columns.append({"header": h, "role": ROLE_TAX, "tender_class": None, "keyed_manually": False,
                            "tax_total": True, "provenance": "mapped as the invoice's total tax",
                            "sum": st["sum"], "cells": st["numeric"], "nonzero": st["nonzero"]})
            continue
        if prev:
            columns.append({"header": h, "role": prev.get("role") or (ROLE_TENDER if prev.get("tender_class") else ROLE_IGNORE),
                            "tender_class": prev.get("tender_class"), "keyed_manually": bool(prev.get("keyed_manually")),
                            "tax_total": False, "provenance": "your earlier choice",
                            "sum": st["sum"], "cells": st["numeric"], "nonzero": st["nonzero"]})
            continue
        cls = resolve(h)
        if cls:
            columns.append({"header": h, "role": ROLE_TENDER, "tender_class": cls, "keyed_manually": bool(keyed_of(h)),
                            "tax_total": False, "provenance": "from the words in the header",
                            "sum": st["sum"], "cells": st["numeric"], "nonzero": st["nonzero"]})
        elif _TAX_RE.search(h):
            columns.append({"header": h, "role": ROLE_TAX, "tender_class": None, "keyed_manually": False,
                            "tax_total": False, "provenance": "from the words in the header (a tax component)",
                            "sum": st["sum"], "cells": st["numeric"], "nonzero": st["nonzero"]})
        else:
            unplaced.append({"header": h, "sum": st["sum"], "cells": st["numeric"], "nonzero": st["nonzero"]})
    return {"columns": columns, "unplaced": unplaced, "scanned": len(records or []), "stats": stats}


def apply_decisions(proposal, decisions, known_classes):
    """The CONFIRMED columns: the proposal with the person's decisions laid over ({header: {role,
    tender_class, keyed_manually}}); an unplaced money column the person declares becomes a column.
    An unknown class or role is refused (returned under `errors`), never silently kept."""
    dec = {str(k).strip().lower(): (v or {}) for k, v in (decisions or {}).items() if isinstance(v, dict)}
    out, errors, seen = [], [], set()
    pool = list(proposal.get("columns") or []) + [
        {"header": u["header"], "role": ROLE_IGNORE, "tender_class": None, "keyed_manually": False, "tax_total": False,
         "provenance": "not proposed — no rule placed it", "sum": u["sum"], "cells": u["cells"], "nonzero": u["nonzero"]}
        for u in proposal.get("unplaced") or []]
    for c in pool:
        hl = c["header"].lower()
        seen.add(hl)
        d = dec.get(hl)
        row = dict(c)
        if d:
            role = _s(d.get("role")).lower() or (ROLE_TENDER if _s(d.get("tender_class")) else c["role"])
            if role not in ROLES:
                errors.append(f"'{c['header']}': unknown role '{role}'")
                continue
            cls = _s(d.get("tender_class")).lower() or None
            if role == ROLE_TENDER and cls not in known_classes:
                errors.append(f"'{c['header']}': unknown tender class '{cls}' (allowed: {', '.join(known_classes)})")
                continue
            row.update({"role": role, "tender_class": cls if role == ROLE_TENDER else None,
                        "keyed_manually": bool(d.get("keyed_manually")) if role == ROLE_TENDER else False,
                        "provenance": "confirmed by you"})
        if row["role"] == ROLE_TENDER and not row.get("tender_class"):
            errors.append(f"'{c['header']}': a tender column needs a tender class")
            continue
        out.append(row)
    for hl, d in dec.items():
        if hl not in seen:
            errors.append(f"'{hl}': not a money column of this file")
    return {"columns": out, "errors": errors,
            "tenders": [c for c in out if c["role"] == ROLE_TENDER],
            "taxes": [c for c in out if c["role"] == ROLE_TAX and not c.get("tax_total")]}


def tender_rows(pairs, confirmed, fields, exclude=None):
    """ONE ROW PER (invoice, declared column) with an amount ≠ 0 — the landing of the tender split.
    `pairs` = [(raw record, mapped invoice row)] as the intake maps them; `fields` = the invoice kind's
    field names (store / date / txn / rep). A tax component rides the same grain with role 'tax'.
    Zero amounts are not rows: the column exists on every invoice, the tender does not."""
    ex = set(exclude or ())
    cols = [c for c in (confirmed or []) if c["role"] in (ROLE_TENDER, ROLE_TAX) and not c.get("tax_total")]
    out = []
    for raw, m in pairs or []:
        if _s(m.get(fields["store"])) in ex:
            continue
        idx = {_s(k).lower(): v for k, v in (raw or {}).items()}
        for c in cols:
            amt = _sf(idx.get(c["header"].lower()))
            if abs(amt) < 0.005:
                continue
            out.append({"store": _s(m.get(fields["store"])), "trans_date": _s(m.get(fields["date"]))[:10],
                        "trans_id": _s(m.get(fields["txn"])), "salesperson": _s(m.get(fields.get("rep") or "")) or None,
                        "role": c["role"], "tender_label": c["header"], "tender_class": c.get("tender_class"),
                        "keyed_manually": bool(c.get("keyed_manually")), "amount": money(amt)})
    return out


# ── 2.5 / 2.6 — OUR NUMBERS BESIDE THE FILE'S ────────────────────────────────────────────────────
def invoice_verify(kept, fields, trows=None, *, pays):
    """The verify numbers for an invoice-level export over mapped rows (built OR re-read — the same
    shape, so the commit compares like with like): rows = invoices, Σ of the tie field, Σ invoice
    total, Σ net sales, Σ tax, the date span, per store / per rep; the tender split per class and the
    Σ CUSTOMER tenders vs Σ invoice total difference, in words; the tax components.

    `pays(tender_class)` → is it money the CUSTOMER paid (closing.router.is_customer_payment — injected,
    REQUIRED, never defaulted here). The invoice total is what the customer paid, so only those tenders
    tie to it; a tender the vendor or a promotion covered (a rebate applied as payment) lands with the
    rest but is reported BESIDE the tie (`not_customer`), never inside it — the same answer the
    receipts built from these rows and Cash Collected give (owner 2026-09-24: the vendor-rebate column
    held the invoice card at 2.5b for a file whose customer tenders tie on every invoice)."""
    f = fields
    kept = list(kept or [])
    trows = list(trows or [])
    amt, tot, dt, st, rep, txn, tax = (f["amount"], f.get("total"), f["date"], f["store"], f.get("rep"), f.get("txn"), f.get("tax"))
    inv_ids = {_s(m.get(txn)) for m in kept if txn and _s(m.get(txn))}
    tenders = [t for t in trows if t.get("role") == ROLE_TENDER]
    taxes = [t for t in trows if t.get("role") == ROLE_TAX]
    by_class, by_label, by_inv = {}, {}, {}
    nc_label, nc_inv = {}, set()
    for t in tenders:
        by_class[t["tender_class"]] = money(by_class.get(t["tender_class"], 0.0) + _sf(t["amount"]))
        by_label[t["tender_label"]] = money(by_label.get(t["tender_label"], 0.0) + _sf(t["amount"]))
        if pays(t.get("tender_class")):
            by_inv[t["trans_id"]] = by_inv.get(t["trans_id"], 0.0) + _sf(t["amount"])
        else:
            nc_label[t["tender_label"]] = money(nc_label.get(t["tender_label"], 0.0) + _sf(t["amount"]))
            nc_inv.add(t["trans_id"])
    sum_tenders = money(sum(_sf(t["amount"]) for t in tenders))
    sum_customer = money(sum(by_inv.values()))
    sum_nc = money(sum(nc_label.values()))
    sum_total = money(sum(_sf(m.get(tot)) for m in kept)) if tot else None
    off = []
    if tot:
        for m in kept:
            tid = _s(m.get(txn))
            d = money(by_inv.get(tid, 0.0) - _sf(m.get(tot)))
            if abs(d) >= 0.005:
                off.append({"trans_id": tid, "invoice_total": money(_sf(m.get(tot))), "tenders": money(by_inv.get(tid, 0.0)), "difference": d})
    diff = money(sum_customer - sum_total) if sum_total is not None else None
    tax_components = {}
    for t in taxes:
        tax_components[t["tender_label"]] = money(tax_components.get(t["tender_label"], 0.0) + _sf(t["amount"]))
    beside = ""
    if nc_label:
        beside = ("; not counted as the customer's payment (paid by the vendor or a promotion — the invoice total does not include it): "
                  + ", ".join(f"{k} {v:,.2f}" for k, v in sorted(nc_label.items())) + f" on {len(nc_inv):,} invoice(s) — it lands beside them")
    if tenders:
        if diff == 0.0:
            words = f"the customer's tenders add up to the invoice totals to the cent ({sum_customer:,.2f} over {len(inv_ids):,} invoices)" + beside
        else:
            words = (f"the customer's tenders add up to {sum_customer:,.2f} and the invoice totals to {sum_total:,.2f} — "
                     f"a difference of {diff:,.2f} on {len(off):,} invoice(s)" + beside)
    else:
        words = "no tender column declared — the tender split is not captured"
    return {
        "rows": len(kept), "distinct_txns": len(inv_ids),
        "sum_amount": money(sum(_sf(m.get(amt)) for m in kept)),
        "sum_invoice_total": sum_total,
        "sum_net_sales": money(sum(_sf(m.get("net_sales")) for m in kept)),
        "sum_gp": money(sum(_sf(m.get("gp")) for m in kept)),
        "sum_tax": money(sum(_sf(m.get(tax)) for m in kept)) if tax else None,
        "date_span": _date_span(kept, dt),
        "per_store": _per(kept, st, amt), "per_rep": _per(kept, rep, amt) if rep else [],
        "storeless_rows": sum(1 for m in kept if not _s(m.get(st))),
        "tenders": {"rows": len(tenders), "sum": sum_tenders, "customer_sum": sum_customer, "by_class": by_class, "by_label": by_label,
                    "not_customer": {"sum": sum_nc, "by_label": nc_label, "invoices": len(nc_inv)},
                    "invoices_with_tenders": len(by_inv), "difference": diff,
                    "invoices_off": len(off), "invoices_off_sample": off[:25], "words": words,
                    "match": (diff == 0.0) if diff is not None else None},
        "tax": {"sum": money(sum(_sf(m.get(tax)) for m in kept)) if tax else None,
                "components": tax_components, "component_rows": len(taxes),
                "components_sum": money(sum(tax_components.values())),
                "words": (f"Σ tax {money(sum(_sf(m.get(tax)) for m in kept)):,.2f}" + (
                    f"; components {', '.join(f'{k} {v:,.2f}' for k, v in sorted(tax_components.items()))}" if tax_components else "")
                          if tax else "no tax column mapped")},
    }


def _date_span(kept, dt):
    lo = hi = None
    undated = 0
    for m in kept or []:
        d = _s(m.get(dt))[:10]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            undated += 1
            continue
        lo = d if lo is None or d < lo else lo
        hi = d if hi is None or d > hi else hi
    return {"from": lo, "to": hi, "undated_rows": undated}


def _per(kept, field, amt, cap=300):
    agg = {}
    for m in kept or []:
        k = _s(m.get(field)) or "(blank)"
        a = agg.setdefault(k, {"value": k, "count": 0, "sum": 0.0})
        a["count"] += 1
        a["sum"] += _sf(m.get(amt))
    return sorted(({**a, "sum": money(a["sum"])} for a in agg.values()), key=lambda a: -a["count"])[:cap]


def tender_tie(vn):
    """The Σ CUSTOMER tenders vs Σ invoice total tie — the shape `simple_tie` has, so the refusal rule reads it."""
    t = (vn or {}).get("tenders") or {}
    if not t.get("rows"):
        return None
    return {"our_total": t.get("customer_sum"), "file_total": (vn or {}).get("sum_invoice_total"), "file_total_source": "invoice totals",
            "difference": t.get("difference"), "match": t.get("match"), "words": t.get("words")}


# ── STAGE 4 — THE TENDER SPLIT PER STORE-DAY BESIDE THE X-REPORT (not a sibling basis) ───────────
# The invoice tender split per store-day is NOT derived here: closing.router._invoice_tenders_by_store is
# the ONE reader (the same one the closing pages read under the 'invoice' basis), so the Stage-4 tie-out
# shows exactly what Cash Collected will show. `recon_vs_xreport` compares its output with the X-report leg.
def recon_vs_xreport(store_days, xreport_by_date):
    """Per store-day: the invoice tender split ({(code, date): agg with by_class} — closing.router.
    _invoice_tenders_by_store per date) beside the X-report's (closing.router._tender_split_by_store
    forced to the X-report leg, {date: {code: {cash, card, other, total}}}), with the difference per gate
    when an X-report exists for the day — else 'no X-report for this day'. A tie-out shown at Stage 4;
    which of the two the closing recon READS is the org's tender basis (closing.router.tender_basis)."""
    rows = []
    days_with = set()
    for (code, date), inv in sorted(store_days.items()):
        xr = (xreport_by_date.get(date) or {}).get(code)
        row = {"store": code, "date": date, "invoice": {k: inv[k] for k in ("cash", "card", "other", "total")},
               "invoice_by_class": inv.get("by_class") or {}, "xreport": None, "difference": None, "verdict": "no X-report for this day"}
        if xr:
            days_with.add(date)
            row["xreport"] = {k: money(xr.get(k, 0.0)) for k in ("cash", "card", "other", "total")}
            row["difference"] = {k: money(inv[k] - money(xr.get(k, 0.0))) for k in ("cash", "card", "other", "total")}
            row["verdict"] = "matches the X-report" if all(abs(v) < 0.005 for v in row["difference"].values()) else \
                "differs from the X-report by " + ", ".join(f"{k} {v:,.2f}" for k, v in row["difference"].items() if abs(v) >= 0.005)
        rows.append(row)
    compared = [r for r in rows if r["xreport"]]
    matched = sum(1 for r in compared if r["verdict"] == "matches the X-report")
    return {"rows": rows, "store_days": len(rows), "days_with_xreport": len(days_with),
            "compared": len(compared), "matched": matched,
            "sum_invoice": {k: money(sum(r["invoice"][k] for r in rows)) for k in ("cash", "card", "other", "total")},
            "sum_xreport": {k: money(sum(r["xreport"][k] for r in compared)) for k in ("cash", "card", "other", "total")},
            "words": (f"{len(compared)} of {len(rows)} store-day(s) have an X-report; {matched} match to the cent"
                      if rows else "no tender rows to compare")}


# ── THE CONFIG ROWS — closing_tender_map (mig 111), report='invoice' ─────────────────────────────
def tender_map_rows(org_id, confirmed):
    """The org's closing_tender_map rows for report='invoice' that REMEMBER the confirmed columns:
    one row per tender class (source_labels = the exact headers, match_mode 'exact', priority 10 —
    before any substring rule) + the keyed-manually flag row (KEYED_FLAG_KEY) naming the flagged
    headers. Written through the intake, read by tender_config.make_resolver next month; the
    closing legs never read report='invoice' rows (make_resolver filters report ∈ {leg, 'both'})."""
    by_class, keyed = {}, []
    for c in confirmed or []:
        if c.get("role") != ROLE_TENDER or not c.get("tender_class"):
            continue
        by_class.setdefault(c["tender_class"], []).append(c["header"])
        if c.get("keyed_manually"):
            keyed.append(c["header"])
    rows = [{"org_id": org_id, "tender_key": k, "report": MAP_REPORT, "source_labels": sorted(set(v)),
             "match_mode": "exact", "priority": 10} for k, v in sorted(by_class.items())]
    if keyed:
        rows.append({"org_id": org_id, "tender_key": KEYED_FLAG_KEY, "report": MAP_REPORT,
                     "source_labels": sorted(set(keyed)), "match_mode": "exact", "priority": 10})
    return rows


def earlier_from_map(map_rows):
    """{header lower: {tender_class, keyed_manually, role}} from the org's report='invoice' rows — the
    'your earlier choice' provenance the next analyze shows."""
    out, keyed = {}, set()
    for r in map_rows or []:
        if (r.get("report") or "") != MAP_REPORT:
            continue
        labels = [_s(x).lower() for x in (r.get("source_labels") or []) if _s(x)]
        if r.get("tender_key") == KEYED_FLAG_KEY:
            keyed.update(labels)
            continue
        for l in labels:
            out[l] = {"tender_class": r.get("tender_key"), "keyed_manually": False, "role": ROLE_TENDER}
    for l in keyed:
        if l in out:
            out[l]["keyed_manually"] = True
    return out


def stage4_tender_note(vn):
    """The one-line Stage-4 note for a landed invoice export: the tender split beside the X-report,
    and Σ tax. None when the commit recorded no tender check."""
    vn = vn or {}
    tr = vn.get("tender_recon") or {}
    tx = (vn.get("numbers") or {}).get("tax") or {}
    parts = []
    if tr.get("basis"):
        parts.append(f"tenders: {tr.get('words')}" if tr.get("store_days") else "tenders: none captured — declare the tender columns at step 2.5b")
    if tx.get("sum") is not None:
        parts.append(f"Σ tax {money(tx['sum']):,.2f} (invoice-level; the Tax Collected report does not read it yet)")
    return "; ".join(parts) or None
