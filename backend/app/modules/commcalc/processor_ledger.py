"""Processor Money-Movement Ledger — daily debits vs credits per transaction type (owner directive
2026-09-04: "report on the asset ledger for the debits and credits being done by [the processors]
for their carriers on the same day, in two separate columns and then the net on the third column
with separated by the transaction type, all filters needed store transaction type dates etc").

WHAT THIS REPORTS
─────────────────
Each org's carrier PROCESSOR feed — the money the processor moves on the dealer's account — grouped
DAY × TRANSACTION TYPE (× store for filtering), split into a DEBITS column (money the processor
takes from / charges the dealer), a CREDITS column (money the processor pays / credits the dealer),
and NET = CREDITS − DEBITS.

SOURCES (duplicate-check per the build gate — REUSED, never re-derived):
  • `commcalc.raw_payment_detail` — the ePay-processor payment-detail feed (epay sweep, mig 020/025;
    index §2/§16: "GP payment categorization, commission reimbursement"). One row per payment event:
    `payment_date`, `payment_type` (the transaction type), signed `amount`, `business_address`.
    SIGN RULE (verified live 2026-09-04, house org: 42 'Commission Withholding' rows on 2026-07-27
    all negative; every bounty/incentive/spiff row positive): amount > 0 = CREDIT to the dealer,
    amount < 0 = DEBIT (withheld/charged).
  • `commcalc.raw_ma_daily_tx` — the VidaPay/Total daily-TX feed (mig 083; VidaPay sweep / upload /
    report_pull). One row per order line: `tx_date`, `order_type` (the transaction type), signed
    `retail_cost`, `account_id` (store account on the processor). SIGN RULE (verified live
    2026-09-04, org 854f…: Sales Order / Branded MarketPlace / Fee rows positive; Residual / Spiff /
    Promo / Void rows negative): retail_cost > 0 = DEBIT to the dealer (a charge), retail_cost < 0 =
    CREDIT (money paid to the dealer).
  The sign direction is a property of the FEED SHAPE (which table the processor's report lands in),
  never a carrier/tenant branch — the same posture as `_billpay_processor_by_store`'s per-feed reads.

  NOT re-derived here (each already has its own owner surface; a second daily derivation would
  drift): the Boost device asset ledger (`asset_ledger.owed_to_vip`/reimbursements — the verified
  Friday-billing computation, /commcalc/asset + invoice-due), VIP invoices / PayGo payments
  (Distributor Invoices report), and `raw_epay_daily_tx` (customer bill-payment terminal detail —
  the §12 bill-pay recon leg, not the dealer's account ledger; empty in live data today).

RESOLUTIONS REUSED (never a second path):
  • Processor identity (WHICH feed is the org's primary): `commcalc.router._metric_source`
    (mig 923) + `_billpay_processor_name` (mig 939) — config first, data_source auto-detect
    fallback. When resolution yields '' (e.g. the house org's portal login is b2bsoft-only), the
    feeds still speak for themselves: a feed with rows in range is included, tagged with its own
    processor code — honest data over a silent empty report.
  • Processor NAME IN COPY: the mig-953 carrier vocabulary — `report_labels.load_report_labels`
    term key 'processor' for the org's default carrier (tenant override > house carrier preset >
    the NEUTRAL noun "payment processor"). This is the ONE naming path: the Boost side renders its
    processor's name, the Total side renders its own, and neither vendor string exists in this
    module or in page copy (owner directive 2026-09-04; harness_carrier_vocab_guard).
  • VidaPay account → store: `commcalc.router._vidapay_account_resolver` (storeops merchant-id map
    ∪ the mig-314 account→store index — the SAME resolution the P&L / BS / billpay recon use).
  • Raw store string → canonical address: `account.coa.store_resolver` (the app's one
    spelling-collapse chain: store_mapping / aliases / store_code / unambiguous leading number).
  • Canonical address → store_code: `commcalc.flag_store_resolver.store_index`/`resolve_code`
    (mirrors the span keyset's vocabulary exactly, so scope filtering matches what it resolves).
  • Store_code → MARKET, and the market OPTION LIST: `core.scope.market_by_code` +
    `core.scope.org_market_options` (§13a/§13c). Resolving market here rather than joining it on
    the page from the store roster is deliberate: a cell whose store is missing from (or spelled
    differently on) the roster would otherwise carry a blank market and be silently dropped the
    moment a market filter is picked — the exact B-1115/LI bug class the §13 doctrine exists to
    end. No store-vocabulary table is read directly (the cached union index does it), so this adds
    no resolution site for harness_market_resolution_guard.

PURE CORE (harness_processor_ledger.py proves it DB-free): `classify_amount`, `fold_cells`,
`filter_cells`, `day_type_rollup`. IO lives only in `assemble` (client passed in, lazy app imports).
READ-ONLY: this module writes nothing.
"""
from collections import OrderedDict
from datetime import date as _date, timedelta as _timedelta

# Per-feed shape: where the rows live and which way the feed's sign points. `credit_positive` is a
# property of the source report's own convention (see module docstring; verified against live rows),
# not tenant/carrier behavior — behavior config (which processor an org uses) stays in
# metric_source_of_truth / data_source per RULE TWO.
FEED_SHAPES = {
    "epay": {
        "source": "raw_payment_detail",
        "date_col": "payment_date", "type_col": "payment_type",
        "amount_col": "amount", "store_col": "business_address",
        "credit_positive": True,
        "rule": "amount > 0 = credit to the dealer; amount < 0 = debit (withheld/charged)",
    },
    "vidapay": {
        "source": "raw_ma_daily_tx",
        "date_col": "tx_date", "type_col": "order_type",
        "amount_col": "retail_cost", "store_col": "account_id",
        "credit_positive": False,
        "rule": "retail_cost > 0 = debit to the dealer (a charge); retail_cost < 0 = credit",
    },
}

# Hard cap on rows folded per feed (the house feed has 100k+ row days); the payload flags
# truncation honestly instead of silently under-counting.
MAX_ROWS_PER_FEED = 500_000
PAGE = 10_000


def classify_amount(amount, credit_positive):
    """(debit, credit) for one signed feed amount — both non-negative, at most one non-zero.
    `credit_positive` = the feed's sign convention (see FEED_SHAPES). Zero → (0, 0)."""
    try:
        a = float(amount or 0.0)
    except (TypeError, ValueError):
        a = 0.0
    if a == 0.0:
        return 0.0, 0.0
    if credit_positive:
        return (0.0, a) if a > 0 else (-a, 0.0)
    return (a, 0.0) if a > 0 else (0.0, -a)


def fold_cells(events):
    """Fold classified events into cells keyed (processor, date, tx_type, store_code) — the finest
    grain the report serves; every coarser view (day × type) sums these, so on-screen totals always
    tie out to the exported cells. Each event: {processor, date, tx_type, store_code, store, debit,
    credit}. Returns a list of cell dicts with debits/credits/net (net = credits − debits) and the
    folded row count, insertion-ordered by (date, tx_type, store). `market` rides along on the
    cell (it is a pure function of store_code, canonically resolved by the caller) so the market
    filter never re-derives it from a roster join."""
    cells = OrderedDict()
    for e in events:
        key = (e.get("processor") or "", str(e.get("date") or ""), e.get("tx_type") or "(blank)",
               e.get("store_code") or "", e.get("store") or "")
        c = cells.get(key)
        if c is None:
            c = cells[key] = {"processor": key[0], "date": key[1], "tx_type": key[2],
                              "store_code": key[3], "store": key[4],
                              "market": str(e.get("market") or ""),
                              "debits": 0.0, "credits": 0.0, "rows": 0}
        c["debits"] += float(e.get("debit") or 0.0)
        c["credits"] += float(e.get("credit") or 0.0)
        c["rows"] += 1
    out = sorted(cells.values(), key=lambda c: (c["date"], c["tx_type"].lower(), c["store"].lower()))
    for c in out:
        c["debits"] = round(c["debits"], 2)
        c["credits"] = round(c["credits"], 2)
        c["net"] = round(c["credits"] - c["debits"], 2)
    return out


def filter_cells(cells, stores=None, types=None, markets=None):
    """Filter semantics shared by the endpoint, the page and the W3 builder: empty/None = no
    filter; a store filter matches store_code OR the store display string (case/whitespace-
    insensitive) so an unmapped feed key ('' store_code, raw string store) is still addressable;
    the type and market filters match tx_type / the cell's canonically-resolved market the same
    way. Filters AND-compose."""
    def _fold(vals):
        return {str(v or "").strip().lower() for v in (vals or []) if str(v or "").strip()}
    sf, tf, mf = _fold(stores), _fold(types), _fold(markets)
    out = []
    for c in cells:
        if sf and not ({str(c.get("store_code") or "").strip().lower(),
                        str(c.get("store") or "").strip().lower()} & sf):
            continue
        if tf and str(c.get("tx_type") or "").strip().lower() not in tf:
            continue
        if mf and str(c.get("market") or "").strip().lower() not in mf:
            continue
        out.append(c)
    return out


def day_type_rollup(cells):
    """The owner's view: rows DAY × TRANSACTION TYPE (across whatever stores survived the filter),
    debits / credits / net columns, plus per-day subtotals and a grand total. Net = credits − debits
    at every grain; Σ(day nets) == grand net by construction."""
    rows, days = OrderedDict(), OrderedDict()
    total = {"debits": 0.0, "credits": 0.0, "rows": 0}
    for c in cells:
        k = (c["date"], c["processor"], c["tx_type"])
        r = rows.get(k)
        if r is None:
            r = rows[k] = {"date": c["date"], "processor": c["processor"], "tx_type": c["tx_type"],
                           "debits": 0.0, "credits": 0.0, "rows": 0}
        d = days.get(c["date"])
        if d is None:
            d = days[c["date"]] = {"date": c["date"], "debits": 0.0, "credits": 0.0, "rows": 0}
        for slot in (r, d, total):
            slot["debits"] += c["debits"]
            slot["credits"] += c["credits"]
            slot["rows"] += c["rows"]
    for slot in list(rows.values()) + list(days.values()) + [total]:
        slot["debits"] = round(slot["debits"], 2)
        slot["credits"] = round(slot["credits"], 2)
        slot["net"] = round(slot["credits"] - slot["debits"], 2)
    return {"rows": sorted(rows.values(), key=lambda r: (r["date"], r["tx_type"].lower())),
            "days": sorted(days.values(), key=lambda d: d["date"]),
            "total": total}


def _valid_ymd(s):
    try:
        _date.fromisoformat(str(s or ""))
        return True
    except ValueError:
        return False


def _fetch_feed(client, org_id, shape, date_from, date_to):
    """Org-scoped, date-ranged, paged read of one feed. Returns (rows, truncated)."""
    cols = ",".join({shape["date_col"], shape["type_col"], shape["amount_col"], shape["store_col"]})
    rows, start = [], 0
    while start < MAX_ROWS_PER_FEED:
        page = (client.schema("commcalc").table(shape["source"]).select(cols)
                .eq("org_id", org_id)
                .gte(shape["date_col"], date_from).lte(shape["date_col"], date_to)
                .order(shape["date_col"]).order(shape["type_col"])
                .range(start, start + PAGE - 1).execute().data) or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows, False
        start += PAGE
    return rows, True


def _processor_term(client, org_id):
    """The org's processor NAME for copy, from the mig-953 carrier vocabulary (report_term preset
    scope, key 'processor'): tenant override > house carrier preset > the neutral noun. Returns
    (label, source). NEVER a hardcoded vendor string — see the module docstring. Best-effort: a
    label-service hiccup degrades to the neutral noun, never to another carrier's word."""
    neutral = "payment processor"
    try:
        from app.modules.commcalc import report_labels as _rl
        payload = _rl.load_report_labels(client, org_id)
        terms = payload.get("terms") or {}
        for key in (payload.get("default_carrier") or "", *(payload.get("carriers") or []), "_"):
            got = str(((terms.get(key) or {}) if key else {}).get("processor") or "").strip()
            if got:
                return got, ("report_term:" + key if key != "_" else "report_term:override")
    except Exception as e:                       # pragma: no cover - I/O guard
        print(f"WARN processor_ledger term resolution failed: {e}")
    return neutral, "neutral_default"


def assemble(client, org_id, date_from, date_to):
    """Build the full ledger payload for [date_from, date_to] (inclusive). Raises ValueError on a
    bad window (the endpoint maps it to 400). Scope filtering is the ENDPOINT's job (it holds the
    caller's keyset); this stays caller-agnostic for the W3 builder."""
    if not (_valid_ymd(date_from) and _valid_ymd(date_to)):
        raise ValueError("date_from/date_to must be YYYY-MM-DD")
    if date_to < date_from:
        raise ValueError("date_to is before date_from")
    if _date.fromisoformat(date_to) - _date.fromisoformat(date_from) > _timedelta(days=366):
        raise ValueError("window too large — max 366 days")

    # Processor resolution (mig 923/939) — REUSED via lazy import, never a second path.
    from app.modules.commcalc.router import (_metric_source, _billpay_processor_name,
                                             _vidapay_account_resolver)
    from app.modules.account.coa import store_resolver as _coa_store_resolver
    from app.modules.commcalc import flag_store_resolver as _fsr

    from app.core import scope as _cscope

    msrc = _metric_source(client, org_id, "bill_payments")
    primary = _billpay_processor_name(client, org_id, msrc)
    proc_label, label_source = _processor_term(client, org_id)

    resolve_addr = _coa_store_resolver(client, org_id)
    code_index = _fsr.store_index(client, org_id)
    resolve_acct = _vidapay_account_resolver(client, org_id)
    # §13a: store_code → market off the ONE cached canonical union index (both vocabularies), so a
    # store that carries its market on only one side still filters. Degrades to no stamps.
    try:
        market_of_code = _cscope.market_by_code(client, org_id) or {}
    except Exception as e:                       # pragma: no cover - I/O guard
        print(f"WARN processor_ledger market_by_code failed: {e}")
        market_of_code = {}

    def _store_of(processor, raw):
        """(store_code, display) for a feed row's raw store key — canonical chain, honest fallback:
        an unresolvable key keeps the raw string as display with '' code (never a guessed store)."""
        raw = str(raw or "").strip()
        s = (resolve_acct(raw) or raw) if processor == "vidapay" else raw
        addr = None
        try:
            addr = resolve_addr(s)
        except Exception:
            addr = None
        code = _fsr.resolve_code(code_index, s, addr, raw)
        display = addr or s or "(no store)"
        code = code or ""
        return code, display, str(market_of_code.get(code.upper()) or "")

    events, feeds_meta = [], []
    for proc, shape in FEED_SHAPES.items():
        try:
            rows, truncated = _fetch_feed(client, org_id, shape, date_from, date_to)
        except Exception as e:  # a missing table / transient read degrades honestly, never 500s
            feeds_meta.append({"processor": proc, "source": f"commcalc.{shape['source']}",
                               "rows": 0, "truncated": False, "error": str(e)[:200],
                               "classification": shape["rule"]})
            continue
        store_cache = {}
        for r in rows:
            raw_store = r.get(shape["store_col"])
            ck = str(raw_store or "").strip()
            if ck not in store_cache:
                store_cache[ck] = _store_of(proc, raw_store)
            code, display, mkt = store_cache[ck]
            debit, credit = classify_amount(r.get(shape["amount_col"]), shape["credit_positive"])
            events.append({"processor": proc, "date": str(r.get(shape["date_col"]) or "")[:10],
                           "tx_type": str(r.get(shape["type_col"]) or "").strip() or "(blank)",
                           "store_code": code, "store": display, "market": mkt,
                           "debit": debit, "credit": credit})
        feeds_meta.append({"processor": proc, "source": f"commcalc.{shape['source']}",
                           "rows": len(rows), "truncated": truncated,
                           "classification": shape["rule"]})

    cells = fold_cells(events)
    active = [f["processor"] for f in feeds_meta if f["rows"]]
    code = primary or (active[0] if len(active) == 1 else "")
    # §13c ENUMERATION doctrine (harness_market_enumeration_guard pins this function CANONICAL):
    # the market dropdown is the org's canonical vocabulary ∪ the stamps this report's own rows
    # carry — never "whatever markets happened to load" — so a market recorded on one vocabulary
    # only (B-1115/LI) can never go missing from this filter. The "(no market)" sentinel is
    # appended by the PAGE after composing, per the doctrine.
    present = {c["market"] for c in cells if c.get("market")}
    try:
        market_options = _cscope.org_market_options(client, org_id, present)
    except Exception as e:                       # pragma: no cover - I/O guard
        print(f"WARN processor_ledger org_market_options failed: {e}")
        market_options = sorted(present)
    return {
        "processor": {"code": code, "label": proc_label, "label_source": label_source,
                      "resolved_from": "config" if primary else ("feed_presence" if code else "")},
        "market_options": market_options,
        "feeds": feeds_meta,
        "cells": cells,
        "types": sorted({c["tx_type"] for c in cells}, key=str.lower),
        "meta": {"date_from": date_from, "date_to": date_to,
                 "net_rule": "net = credits − debits (positive = paid to the dealer)"},
    }
