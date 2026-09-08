"""THE ONE per-(store, day) SALES-TAX aggregation — read by the Tax Collected report AND by the
Balance Sheet's `sales_tax_payable` liability (owner directive 2026-09-08: "now the sales tax in
p&l").

WHY THIS MODULE EXISTS (duplicate-check gate, CLAUDE.md)
--------------------------------------------------------
Before this, THREE readers of `raw_sales.tax` already disagreed with each other, and none of them
reached the books:

  A. `commcalc/router.py tax_collected`  drops voided AND `trans_type == 'Return'`, and keys each
     row on the RAW store string the export happened to spell.
  B. `closing/router.py _b2b_money`      drops voided only, with its own address resolver.
  C. `account/coa.py _sales_union_rows`  drops voided only, canonical `coa.store_resolver` — but
     never SELECTS the `tax` column at all, so the P&L/Balance Sheet could not see a cent of it.

Adding a fourth loop for the books would have guaranteed a fourth answer. Instead the report's own
per-(store, day) pass is factored out HERE, pure, and the balance sheet calls the SAME function.
The endpoint is its first caller and keeps its existing reader (`_sales_rows_union_txn_range`).

WHAT THE FACTORING CHANGED, DELIBERATELY
----------------------------------------
1. THE STORE KEY IS CANONICALIZED (§13a). The report used to group on the raw export label while
   the P&L books under `coa.store_resolver`'s canonical address, so the two vocabularies could not
   be joined. Measured live (house org, August 2026): 6 of 28 tax-report store labels did not
   match the key the P&L books under — $3,346.02, **24.4% of the month's tax**. The canonical
   resolver is now applied inside this function, so the report and the books share ONE vocabulary.
   Nothing is merged that the resolver would not already merge for every other money line.
   A rename is never silent: `meta['renamed']` lists every raw label that moved, with its dollars,
   and flags as `suspect` any rename where the LEADING STREET NUMBER changes — a generic rule (no
   address literal anywhere) that catches an alias pointing at a genuinely different address rather
   than a spelling variant.
2. RETURNS ARE CARRIED, NOT DROPPED. The report's headline excludes `trans_type == 'Return'` and
   must keep doing so — but a refunded sale's tax is money the tenant does NOT owe the state, so
   the LIABILITY must be net of it. Both figures are produced side by side at every grain
   (`tax` = gross, today's headline; `tax_net` = gross + the return rows' own tax), and the
   headline is not changed. Live August 2026: 109 return rows, tax -$53.74, ext_price -$1,807.25 —
   gross $13,733.70 vs net $13,679.96.

SIGN IS PRESERVED, NEVER CLAMPED. A return line's tax is summed EXACTLY as the export stores it
(live, it is negative). If a tenant's export writes it positive, netting would move the liability
the wrong way — so that is REPORTED (`meta['returns_tax_positive']`), not silently corrected.

RULE TWO: no jurisdiction, tenant, carrier, department or product literal appears here. Taxability
is the line's own `tax > 0` (the §23f ruling), the store vocabulary arrives as an injected
resolver, and the tender bucket arrives as an injected function.

PURE — rows in, aggregates out. No I/O, no client, never raises. Proof:
backend/harness_tax_collected.py.
"""
from app.modules.commcalc.gp_report import VOID_TOKENS, is_voided, safe_float

# The ONE column projection both callers read, so the report and the books can never be looking at
# a different set of fields. `tax` is the column `account/coa._sales_union_rows` never selected.
SALES_COLUMNS = "trans_id,trans_date,store,ext_price,tax,voided,trans_type,tender_type"

# The row-level exclusion vocabulary, named rather than inlined. `VOID_TOKENS` is imported (never
# re-declared) from gp_report, which holds the house's single source of truth for it.
RETURN_TRANS_TYPE = "Return"

__all__ = ["SALES_COLUMNS", "RETURN_TRANS_TYPE", "VOID_TOKENS", "aggregate", "store_tax_rows"]


def _default_bucket(_tender):
    """Shape-stable fallback when a caller has no tender mapper (the books do not need one). Every
    line lands in one bucket so the split still ties to the totals."""
    return "other"


def _lead_num(store):
    """Leading street number of a store label, digits only ('1234 Main St Ste A' -> '1234';
    'B-1234 …' -> None because the lead token is not numeric). Used ONLY to FLAG a rename that
    changed the street number — never to decide a merge, and never against a literal address."""
    tok = str(store or "").strip().split(" ")[0]
    if not tok[:1].isdigit():
        return None
    return "".join(ch for ch in tok if ch.isdigit()) or None


def _blank_store(store, market):
    return {"store": store, "market": market,
            "tax": 0.0, "revenue": 0.0, "taxable_revenue": 0.0, "untaxed_revenue": 0.0,
            "returns_tax": 0.0, "returns_revenue": 0.0, "return_rows": 0,
            "_raw": set(), "_days": {}, "_tender": {}}


def _blank_day(date):
    return {"date": date, "tax": 0.0, "revenue": 0.0, "taxable_revenue": 0.0,
            "untaxed_revenue": 0.0, "returns_tax": 0.0, "returns_revenue": 0.0,
            "return_rows": 0}


def _blank_bucket():
    return {"sales": 0.0, "taxable_revenue": 0.0, "untaxed_revenue": 0.0, "tax": 0.0}


def _rate(tax, taxable):
    return round(100 * tax / taxable, 2) if taxable else 0.0


def aggregate(rows, resolve_store=None, resolve_market=None, bucket=None,
              start="", end="", tender_buckets=()):
    """PURE: unified sales rows -> per-(store, day) sales tax, gross AND net of returns.

    `rows`           dicts carrying at least SALES_COLUMNS' fields.
    `resolve_store`  canonical store key (coa.store_resolver / §13a). Identity when None — a caller
                     that passes nothing gets the raw export vocabulary, exactly as before.
    `resolve_market` raw/canonical store -> market (core.scope). '' when None.
    `bucket`         tender_type -> bucket name. Everything to 'other' when None.
    `start`/`end`    inclusive 'YYYY-MM-DD' day bounds; blank means unbounded on that side.
    `tender_buckets` the bucket vocabulary to materialise (so the split always shows every bucket,
                     including the empty ones the report renders).

    EXCLUSIONS, stated once and applied once:
      * VOIDED rows are excluded from EVERYTHING. A void is not a sale, gross or net.
      * RETURN rows are excluded from the gross figures (`tax`, `revenue`, `taxable_revenue`,
        `untaxed_revenue`, the tender split, `rows_in_window`) — the report's headline is
        unchanged — and accumulated SEPARATELY into `returns_*`, from which `tax_net` is derived.
      * a row outside the day bounds is excluded from both.

    Returns {'stores': [...], 'totals': {...}, 'rows_in_window': n, 'meta': {...}}.
    `rows_in_window` keeps its original meaning (non-void, non-return rows the window kept), so the
    report's operator-facing note reads exactly as it did.
    """
    rz = resolve_store or (lambda s: s)
    mz = resolve_market or (lambda _s: "")
    bz = bucket or _default_bucket
    buckets = tuple(tender_buckets) or ("other",)
    s0 = str(start or "")[:10]
    s1 = str(end or "")[:10]

    by_store = {}
    in_window = 0
    return_rows_in_window = 0
    renamed = {}          # raw label -> {canonical, tax}  (tax = gross, the report's own figure)

    for r in rows or []:
        r = r or {}
        if is_voided(r.get("voided")):
            continue
        day = str(r.get("trans_date") or "")[:10]
        if s0 and day and day < s0:
            continue
        if s1 and day and day > s1:
            continue
        raw = (str(r.get("store") or "?").strip()) or "?"
        # ONE vocabulary for the report and the books (§13a). `resolve` returns the cleaned raw
        # string for a store it does not know, so an unmapped store is never dropped.
        store = rz(raw) or raw
        s = by_store.get(store)
        if not s:
            # Market resolves on the CANONICAL key, falling back to the raw label when the
            # canonical one binds nothing — coverage never regresses (the mig-954 posture).
            mkt = mz(store) or mz(raw) or ""
            s = by_store[store] = _blank_store(store, mkt)
        s["_raw"].add(raw)
        if raw != store:
            e = renamed.setdefault(raw, {"raw": raw, "canonical": store, "tax": 0.0})
            e["canonical"] = store

        tx = safe_float(r.get("tax"))
        ext = safe_float(r.get("ext_price"))
        is_return = str(r.get("trans_type") or "").strip() == RETURN_TRANS_TYPE
        d = s["_days"].get(day) if day else None
        if day and d is None:
            d = s["_days"][day] = _blank_day(day)

        if is_return:
            # REFUNDED TAX IS NOT OWED. Kept out of the headline, carried for the liability.
            return_rows_in_window += 1
            s["returns_tax"] += tx
            s["returns_revenue"] += ext
            s["return_rows"] += 1
            if d is not None:
                d["returns_tax"] += tx
                d["returns_revenue"] += ext
                d["return_rows"] += 1
            continue

        in_window += 1
        if raw != store:
            renamed[raw]["tax"] = round(renamed[raw]["tax"] + tx, 2)
        s["tax"] += tx
        s["revenue"] += ext
        b = s["_tender"].get(bname := bz(r.get("tender_type")))
        if b is None:
            b = s["_tender"][bname] = _blank_bucket()
        b["sales"] += ext
        b["tax"] += tx
        # Taxability is the LINE's own tax, never a department name in code (RULE TWO, §23f).
        if tx:
            s["taxable_revenue"] += ext
            b["taxable_revenue"] += ext
        else:
            s["untaxed_revenue"] += ext
            b["untaxed_revenue"] += ext
        if d is not None:
            d["tax"] += tx
            d["revenue"] += ext
            if tx:
                d["taxable_revenue"] += ext
            else:
                d["untaxed_revenue"] += ext

    out = []
    for s in by_store.values():
        days = []
        for d in sorted(s["_days"].values(), key=lambda x: x["date"]):
            for k in ("tax", "revenue", "taxable_revenue", "untaxed_revenue",
                      "returns_tax", "returns_revenue"):
                d[k] = round(d[k], 2)
            d["tax_net"] = round(d["tax"] + d["returns_tax"], 2)
            d["effective_rate"] = _rate(d["tax"], d["taxable_revenue"])
            days.append(d)
        tender = {b: {k: round(v, 2) for k, v in (s["_tender"].get(b) or _blank_bucket()).items()}
                  for b in buckets}
        for b, v in s["_tender"].items():           # a bucket outside the declared vocabulary is
            if b not in tender:                     # surfaced, never silently discarded
                tender[b] = {k: round(x, 2) for k, x in v.items()}
        row = {"store": s["store"], "market": s["market"],
               "tax": round(s["tax"], 2), "revenue": round(s["revenue"], 2),
               "taxable_revenue": round(s["taxable_revenue"], 2),
               "untaxed_revenue": round(s["untaxed_revenue"], 2),
               "effective_rate": _rate(s["tax"], s["taxable_revenue"]),
               "returns_tax": round(s["returns_tax"], 2),
               "returns_revenue": round(s["returns_revenue"], 2),
               "return_rows": s["return_rows"],
               "tender": tender, "days": days}
        row["tax_net"] = round(row["tax"] + row["returns_tax"], 2)
        row["revenue_net"] = round(row["revenue"] + row["returns_revenue"], 2)
        raws = sorted(s["_raw"])
        if raws != [s["store"]]:
            row["raw_labels"] = raws
        out.append(row)
    out.sort(key=lambda x: -x["tax"])

    tot_tax = round(sum(x["tax"] for x in out), 2)
    tot_taxable = round(sum(x["taxable_revenue"] for x in out), 2)
    tot_rev = round(sum(x["revenue"] for x in out), 2)
    tot_ret_tax = round(sum(x["returns_tax"] for x in out), 2)
    tot_ret_rev = round(sum(x["returns_revenue"] for x in out), 2)
    tender_tot = {b: _blank_bucket() for b in buckets}
    for x in out:
        for b, v in x["tender"].items():
            t = tender_tot.setdefault(b, _blank_bucket())
            for kk in ("sales", "taxable_revenue", "untaxed_revenue", "tax"):
                t[kk] += v[kk]
    totals = {"tax": tot_tax, "revenue": tot_rev, "taxable_revenue": tot_taxable,
              "untaxed_revenue": round(tot_rev - tot_taxable, 2),
              "effective_rate": _rate(tot_tax, tot_taxable),
              "returns_tax": tot_ret_tax, "returns_revenue": tot_ret_rev,
              "return_rows": sum(x["return_rows"] for x in out),
              "tax_net": round(tot_tax + tot_ret_tax, 2),
              "revenue_net": round(tot_rev + tot_ret_rev, 2),
              "tender": {b: {k: round(v, 2) for k, v in tender_tot[b].items()} for b in tender_tot}}

    ren = sorted(renamed.values(), key=lambda e: -e["tax"])
    for e in ren:
        # SUSPECT = the street number itself changed, so this is not a spelling variant of the same
        # address. Generic rule, no address literal — it is a signal for a human, not a merge veto:
        # the canonical resolver still decides, and the dollars are reported either way.
        e["suspect"] = _lead_num(e["raw"]) != _lead_num(e["canonical"])
    meta = {"stores": len(out), "renamed": ren,
            "renamed_tax": round(sum(e["tax"] for e in ren), 2),
            "suspect_renames": [e for e in ren if e["suspect"]],
            "return_rows_in_window": return_rows_in_window,
            "returns_tax_positive": tot_ret_tax > 0,
            "window": {"start": s0, "end": s1}}
    return {"stores": out, "totals": totals, "rows_in_window": in_window, "meta": meta}


def store_tax_rows(agg):
    """PURE: the aggregate's per-store rows reduced to what a BOOKING needs — the canonical store
    key and its gross / net tax. Kept here (beside the aggregation) so the balance sheet never has
    to know the report's row shape."""
    return [{"store": s["store"], "tax": s["tax"], "tax_net": s["tax_net"],
             "returns_tax": s["returns_tax"]} for s in (agg or {}).get("stores", [])]
