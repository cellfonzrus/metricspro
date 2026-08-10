"""Residual per subscriber (MI+ATU), per store, month over month — with a commission overlay.

Purpose: see the effect of lower commissions on the residual payout over time.

- Residual   = actual_mi_payout + actual_atu_payout per raw_mi row.
- Subscriber = a distinct phone number we are PAID residual on that month (MI+ATU nonzero).
- Residual/sub = Σ residual ÷ distinct paid phones, per store per month.
- Store       = raw_mi.salesforce_id → store_mapping.salesforce_id (the clean join gp_report uses);
                rows whose salesforce_id doesn't resolve go to an "(Unassigned)" bucket so the company
                total stays complete (residual for ALL companies).
- Commission  = Σ rep_commissions.total_payout per month; per-store it's matched by street number.

Aggregation runs in Postgres via commcalc.residual_per_sub_by_store (raw_mi is ~38k rows/month); if
that RPC isn't present yet it falls back to a bounded Python aggregation (last `months` only) so the
page always works — running migration 101 just makes it fast over full history.
"""
import re
from datetime import datetime, timezone

from app.modules.commcalc.calculator import safe_float
from app.modules.account._period import parse_period, recent_period_keys


def _pkey(period):
    # (year, month) sort key. parse_period is now the shared finance helper (returns (month, year),
    # robust across both spellings). Byte-identical to the prior month-name-only parse for the
    # month-name period labels raw_mi actually stores; numeric 'YYYY-MM' now sorts correctly too.
    mo, yr = parse_period(period or "")
    return (yr, mo)


def _street_num(addr):
    m = re.match(r"\s*(\d+)", str(addr or ""))
    return m.group(1) if m else ""


def _recent_labels(latest_y, latest_m, n):
    """The last `n` months ending at (latest_y, latest_m), as both 'Month YYYY' and 'YYYY-MM'
    spellings — delegated to the shared finance helper (single source of truth)."""
    return recent_period_keys(latest_y, latest_m, n)


def _latest_period(client, org_id):
    """(year, month) of the most recent raw_mi period; falls back to today if the columns are empty."""
    try:
        rows = (client.schema("commcalc").table("raw_mi")
                .select("period_year,period_month")
                .eq("org_id", org_id)
                .order("period_year", desc=True).order("period_month", desc=True)
                .limit(1).execute().data) or []
        if rows and rows[0].get("period_year") and rows[0].get("period_month"):
            return int(rows[0]["period_year"]), int(rows[0]["period_month"])
    except Exception:
        pass
    n = datetime.now(timezone.utc)
    return n.year, n.month


# The MA/VidaPay residual components — SAME definitions the shipped /ma-commission/summary uses
# (mig 083): NEGATIVE on the Commission Details export = paid TO the dealer, so payable is sign-FLIPPED
# (positive = money the dealer receives). Reused here verbatim so the residual page and the commission
# roll-up never diverge on what a Total/VidaPay dealer is paid.
_MA_COMPONENTS = ["device_margin", "consumer_margin", "consumer_financing", "rebate",
                  "wallet_funding", "fees_margin",
                  "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6"]

# ── MONEY vs IDENTIFIER on the raw_ma_* tables (mig 083) ─────────────────────────────────────
# Several raw_ma_* columns are declared NUMERIC but hold IDENTIFIERS, not dollars. Summing one of
# them produces a 10–13 digit "amount" that looks like a catastrophic loss. This has already
# happened in production once (2026-07-30: the What-If MA residual read `merchant_invoice` — the
# Merchant Invoice NUMBER, catalogued as role "key" in commcalc/ma_upload.py — and reported
# −$492,946,277,716 of May-2026 residual). The finance tree never read those columns; this list
# makes that a checked invariant instead of an accident, so no future edit can quietly sum an id.
#
# The dealer's money columns on raw_ma_daily_tx are `retail_cost` (signed line amount; negative =
# paid to the dealer — the column the canonical Commission Ledger books from) and
# `merchant_discount` (airtime margin — what this module's ATU-equivalent reads).
_MA_IDENTIFIER_COLUMNS = frozenset({
    "merchant_invoice",       # Merchant Invoice # — an invoice identifier, NEVER an amount
    "merchant_account_id", "account_id", "order_number", "activation_order",
    "ban", "bin", "imei", "sim", "sku", "pos_invoice",
    "user_id", "platform_tx_id", "external_ref",
    "direct_ma_id", "top_ma_id", "id", "org_id", "carrier_id", "source_id",
})


def assert_money_columns(cols, where=""):
    """Fail loudly if an identifier column is about to be summed as dollars. Returns `cols`."""
    bad = sorted(c for c in cols if c in _MA_IDENTIFIER_COLUMNS)
    if bad:
        raise ValueError(
            "refusing to sum identifier column(s) as money%s: %s — these raw_ma_* columns are "
            "NUMERIC but hold identifiers (see _MA_IDENTIFIER_COLUMNS)."
            % ((" in " + where) if where else "", ", ".join(bad)))
    return cols


# The feed's own label for the recurring residual line (owner ruling 2026-08-05). A LABEL, not a
# column — the taxonomy lives in product_name, which is why this is matched, not computed.
_MA_RESIDUAL_LABEL = "Residual"

_MA_ATU_COLUMN = assert_money_columns(["merchant_discount"], "raw_ma_daily_tx ATU-equivalent")[0]
assert_money_columns(_MA_COMPONENTS, "raw_ma_commission MI-equivalent")


def _latest_ma_period(client, org_id):
    """(year, month) of the most recent raw_ma_commission period; today's month if unknown/empty."""
    try:
        rows = (client.schema("commcalc").table("raw_ma_commission")
                .select("period_year,period_month")
                .eq("org_id", org_id)
                .order("period_year", desc=True).order("period_month", desc=True)
                .limit(1).execute().data) or []
        if rows and rows[0].get("period_year") and rows[0].get("period_month"):
            return int(rows[0]["period_year"]), int(rows[0]["period_month"])
    except Exception:
        pass
    n = datetime.now(timezone.utc)
    return n.year, n.month


def _aggregate_ma(client, org_id, months, meta=None):
    """Carrier-agnostic residual source for MA/VidaPay tenants (Total, luxelink), used when a tenant has
    NO Boost raw_mi. MI-equivalent = MA Commission Details payable (raw_ma_commission, sign-flipped);
    ATU-equivalent = airtime margin (raw_ma_daily_tx.merchant_discount) — the SAME two figures the
    shipped /ma-commission/summary reports (mig 083). Store = the processor merchant/account id (MA rows
    carry NO salesforce_id), so each row carries an explicit `store_label`. Subscribers = distinct
    activation lines (each Commission Details row = one activated line); airtime top-ups are recurring
    margin on existing lines, so they add to residual $ but not to the subscriber count.

    Returns the same per-(period, store) aggregate shape as the Boost path, or [] when the MA tables are
    empty (a data-gap until the VidaPay report ingest runs — the code path is correct, the data just
    hasn't landed). NEVER raises.

    `meta` (optional dict) is filled with per-period SOURCE COVERAGE — which of the two MA reports
    actually had rows for each period. It changes no figure; it lets the report say out loud "this
    month's residual is airtime-only because MA Commission Details was never pulled for it" instead
    of showing a silent $0 the owner has to guess at."""
    ly, lm = _latest_ma_period(client, org_id)
    want = set(_recent_labels(ly, lm, months))
    agg = {}  # (period, store_label) -> aggregate
    cov = {}  # period -> {"commission_rows": int, "daily_tx_rows": int}

    def _cov(period, key):
        c = cov.setdefault(period, {"commission_rows": 0, "daily_tx_rows": 0})
        c[key] += 1

    def _bucket(period, store_label, name=None):
        k = (period, store_label)
        a = agg.get(k)
        if a is None:
            a = agg[k] = {"period": period, "store_label": store_label, "store_name": name,
                          "salesforce_id": "", "market": "(VidaPay/MA)",
                          "sum_mi": 0.0, "sum_atu": 0.0, "subs": 0, "lines": 0}
        elif name and not a.get("store_name"):
            a["store_name"] = name
        return a

    # ── RESIDUAL — the labelled `Residual` line on the daily-tx feed ─────────────────────────────
    # OWNER RULING 2026-08-05 (raw_ma_daily_tx is the ONLY total-residual source) + explicit GO
    # 2026-08-10. This REPLACES the previous MI-equivalent, which summed `_MA_COMPONENTS` from MA
    # Commission Details — that is TOTAL COMPENSATION, not residual, and it was wrong by ~18x:
    # luxelink July 2026 reported $124,043.34 of "MI", of which the device `rebate` alone was
    # $126,636.77 (the components reconcile to the reported figure exactly, so there was no ambiguity
    # about what it was summing). A device rebate is an equipment subsidy; it is not recurring
    # residual, and mixing them made "residual per subscriber" read $174.84/month on $30-65 plans.
    #
    # The feed labels the real thing in `product_name`, and NEGATIVE retail_cost = paid TO the dealer,
    # so residual is the sign-flipped sum of the rows labelled 'Residual'. Summed across EVERY
    # account_name in the org (owner 2026-08-10: "the data has to be pulled from 2 sources, novawave
    # residual and luxelink residual") — the entity split is reported as coverage, never as a filter,
    # so a missing entity shows up as a gap instead of silently halving the number.
    try:
        start, page = 0, 1000
        while True:
            chunk = (client.schema("commcalc").table("raw_ma_daily_tx")
                     .select("period,account_id,account_name,product_name,retail_cost")
                     .eq("org_id", org_id).in_("period", list(want))
                     .eq("product_name", _MA_RESIDUAL_LABEL)
                     .range(start, start + page - 1).execute().data) or []
            for r in chunk:
                per = (r.get("period") or "").strip()
                if not per:
                    continue
                store = (r.get("account_id") or "").strip() or "(Unassigned)"
                a = _bucket(per, store, name=(r.get("account_name") or None))
                a["sum_mi"] += -safe_float(r.get("retail_cost"))   # flip: positive = dealer receives
                a["lines"] += 1
                _cov(per, "residual_rows")
                nm = (r.get("account_name") or "").strip()
                if nm:
                    cov.setdefault(per, {}).setdefault("entities", set()).add(nm)
            if len(chunk) < page:
                break
            start += page
    except Exception:
        pass

    # SUBSCRIBER COUNT — still one row per activated line on MA Commission Details. Counting only:
    # no money is read from that report any more (see the block above for why).
    try:
        start, page = 0, 1000
        while True:
            chunk = (client.schema("commcalc").table("raw_ma_commission")
                     .select("period,merchant_account_id")
                     .eq("org_id", org_id).in_("period", list(want))
                     .range(start, start + page - 1).execute().data) or []
            for r in chunk:
                per = (r.get("period") or "").strip()
                if not per:
                    continue
                store = (r.get("merchant_account_id") or "").strip() or "(Unassigned)"
                _bucket(per, store)["subs"] += 1
                _cov(per, "commission_rows")
            if len(chunk) < page:
                break
            start += page
    except Exception:
        pass

    # ATU-equivalent — airtime margin, by processor account (store)
    try:
        start, page = 0, 1000
        while True:
            chunk = (client.schema("commcalc").table("raw_ma_daily_tx")
                     .select("period,account_id,account_name,merchant_discount")
                     .eq("org_id", org_id).in_("period", list(want))
                     .range(start, start + page - 1).execute().data) or []
            for r in chunk:
                per = (r.get("period") or "").strip()
                if not per:
                    continue
                store = (r.get("account_id") or "").strip() or "(Unassigned)"
                a = _bucket(per, store, name=(r.get("account_name") or None))
                a["sum_atu"] += safe_float(r.get(_MA_ATU_COLUMN))
                _cov(per, "daily_tx_rows")
            if len(chunk) < page:
                break
            start += page
    except Exception:
        pass

    if meta is not None:
        for _p, _c in cov.items():
            if isinstance(_c.get("entities"), set):
                _c["entities"] = sorted(_c["entities"])
        meta["ma_coverage"] = cov
    return list(agg.values())


def _aggregate(client, org_id, months, meta=None):
    """Per (period, store): sum_mi, sum_atu, subs, lines — CARRIER-AGNOSTIC (no tenant-name branching).
    Boost (raw_mi) is the primary source; a tenant with no raw_mi falls through to the MA/VidaPay tables
    (raw_ma_commission + raw_ma_daily_tx). Source is chosen by which data EXISTS, per org, at runtime.
    `meta` (optional) records WHICH source answered + the MA per-period coverage; figures are unchanged."""
    boost = _aggregate_boost(client, org_id, months)
    if boost:
        if meta is not None:
            meta["source"] = "boost_mi_atu"
        return boost
    if meta is not None:
        meta["source"] = "vidapay_ma"
    return _aggregate_ma(client, org_id, months, meta=meta)


def _aggregate_boost(client, org_id, months):
    """Per (period, salesforce_id): sum_mi, sum_atu, subs, lines. Postgres RPC, Python fallback."""
    # Fast path: RPC over ALL history (grouped in Postgres), trim to last `months` after.
    try:
        rows = client.schema("commcalc").rpc(
            "residual_per_sub_by_store", {"p_org_id": org_id, "p_periods": None}).execute().data or []
        if rows:
            return rows
    except Exception:
        pass
    # Fallback: bound to the last `months` periods (avoid a full-history Python scan), paginate + aggregate.
    ly, lm = _latest_period(client, org_id)
    want = _recent_labels(ly, lm, months)
    agg, subs = {}, {}
    start, page = 0, 1000
    while True:
        chunk = (client.schema("commcalc").table("raw_mi")
                 .select("period,salesforce_id,phone_number,actual_mi_payout,actual_atu_payout")
                 .eq("org_id", org_id).in_("period", want)
                 .range(start, start + page - 1).execute().data) or []
        for r in chunk:
            per = (r.get("period") or "").strip()
            if not per:
                continue
            sf = (r.get("salesforce_id") or "").strip()
            mi = safe_float(r.get("actual_mi_payout"))
            atu = safe_float(r.get("actual_atu_payout"))
            k = (per, sf)
            a = agg.setdefault(k, {"period": per, "salesforce_id": sf,
                                   "sum_mi": 0.0, "sum_atu": 0.0, "lines": 0})
            a["sum_mi"] += mi
            a["sum_atu"] += atu
            a["lines"] += 1
            ph = (r.get("phone_number") or "").strip()
            if ph and (mi + atu) != 0:
                subs.setdefault(k, set()).add(ph)
        if len(chunk) < page:
            break
        start += page
    out = []
    for k, a in agg.items():
        a["subs"] = len(subs.get(k, ()))
        out.append(a)
    return out


_SOURCE_LABELS = {
    "boost_mi_atu": "Boost / ePay — raw_mi actual MI + ATU payout",
    "vidapay_ma": ("VidaPay / master-agent — MA Daily Tx 'Residual' line (recurring residual, all "
                   "entities) + MA Daily Tx airtime margin. Device rebates and spiffs are NOT residual "
                   "and are excluded."),
}


def _source_diagnostics(source, meta, kept):
    """Read-only provenance for the payload: WHICH residual source answered, and — for MA/VidaPay
    tenants — the per-period coverage of the two MA reports. Moves NO figure. It exists because a
    month with MA Daily Tx rows but no MA Commission Details rows legitimately computes to
    airtime-margin-only residual and ZERO paid subscribers, which reads as "broken data" unless the
    report says so out loud. Ruling out the data cause is the first step, so the report shows it."""
    out = {"source": source or None, "source_label": _SOURCE_LABELS.get(source),
           "ma_coverage": None, "data_note": None}
    if source != "vidapay_ma":
        return out
    cov = meta.get("ma_coverage") or {}
    rows, airtime_only = [], []
    for p in kept:
        c = cov.get(p) or {}
        cr, dr = int(c.get("commission_rows") or 0), int(c.get("daily_tx_rows") or 0)
        rows.append({"period": p, "commission_rows": cr, "daily_tx_rows": dr,
                     "residual_rows": int(c.get("residual_rows") or 0),
                     "entities": list(c.get("entities") or [])})
        if dr and not cr:
            airtime_only.append(p)
    out["ma_coverage"] = rows

    # ENTITY COVERAGE (owner 2026-08-10: "the data has to be pulled from 2 sources, novawave residual
    # and luxelink residual"). A tenant can hold several master-agent entities in ONE org, and each
    # month's daily-tx file is pulled per entity — so a month whose file for one entity was never
    # uploaded silently reports a PARTIAL residual that looks like a real decline. Verified on
    # luxelink: Feb-Jun carry Novawave only, July carries Luxelink only (no Novawave rows at all),
    # August carries both but over DISJOINT date ranges. Name the entities per period so a gap is
    # visible instead of being read as a business result.
    seen = sorted({e for r in rows for e in (r.get("entities") or [])})
    if len(seen) > 1:
        partial = [r["period"] for r in rows
                   if r.get("residual_rows") and len(r.get("entities") or []) < len(seen)]
        if partial:
            out["entity_note"] = (
                "PARTIAL ENTITY COVERAGE (not a decline) — this tenant reports residual for "
                + str(len(seen)) + " entities (" + ", ".join(seen) + "), but "
                + ", ".join(partial) + " "
                + ("carries" if len(partial) == 1 else "carry")
                + " only some of them. Those months' residual is INCOMPLETE until the missing "
                "entity's MA Daily Tx file is uploaded — do not compare them month over month.")
    out["entities"] = seen
    if airtime_only:
        one = len(airtime_only) == 1
        out["data_note"] = (
            "DATA GAP (not a calculation error) — " + ", ".join(airtime_only) + ": "
            + ("this month has" if one else "these months have")
            + " MA Daily Tx rows but NO MA Commission Details rows, so "
            + ("its" if one else "their") + " residual is airtime margin only and "
            + ("its" if one else "their") + " paid-subscriber count is 0 (residual/subscriber "
            "therefore reads $0.00). Pull MA Commission Details for "
            + ("that month" if one else "those months")
            + " (Data Imports \u2192 payment-processor sources) before comparing month over month.")
    return out


def compute(client, org_id, months=6):
    """Return the residual-per-subscriber trend: per-store monthly series + an exact company total.
    Filtering by store/market is done client-side (like the GP report), so this returns every store.

    The payload also carries read-only provenance (`source`, `source_label`, `ma_coverage`,
    `data_note`) so an MA/VidaPay tenant can tell a real $0 from an un-ingested month. No figure in
    the series/company/totals is affected by it."""
    # NOTE: `src_meta`, not `meta` — the store-bucket loop below already binds a local named `meta`
    # (the store_mapping row). Naming this one `meta` silently shadowed it and blanked the provenance.
    src_meta = {}
    agg = _aggregate(client, org_id, months, meta=src_meta)

    # salesforce_id → store metadata
    sm_rows = (client.schema("commcalc").table("store_mapping")
               .select("store_address,market,store_code,salesforce_id,is_active")
               .eq("org_id", org_id).execute().data) or []
    by_sfid = {}
    for s in sm_rows:
        sf = (s.get("salesforce_id") or "").strip()
        if not sf:
            continue
        by_sfid[sf] = {"store": (s.get("store_address") or "").strip(),
                       "market": ((s.get("market") or "Boost").strip() or "Boost"),
                       "store_code": (s.get("store_code") or "").strip(),
                       "num": _street_num(s.get("store_address"))}

    # periods present → keep the last `months` chronologically
    all_periods = sorted({(a.get("period") or "").strip() for a in agg if a.get("period")}, key=_pkey)
    kept = all_periods[-months:] if months and months > 0 else all_periods
    kept_set = set(kept)

    # commission by street-number/period (per-store) + exact company total per period
    comm_by_num, comm_company = {}, {p: 0.0 for p in kept}
    if kept:
        crows = (client.schema("commcalc").table("rep_commissions")
                 .select("store,total_payout,period").eq("org_id", org_id)
                 .in_("period", kept).execute().data) or []
        for r in crows:
            per = (r.get("period") or "").strip()
            if per not in kept_set:
                continue
            pay = safe_float(r.get("total_payout"))
            comm_company[per] = comm_company.get(per, 0.0) + pay
            num = _street_num(r.get("store"))
            if num:
                comm_by_num.setdefault(num, {})
                comm_by_num[num][per] = comm_by_num[num].get(per, 0.0) + pay

    # bucket residual by store
    UNASSIGNED = "(Unassigned)"
    stores = {}
    for a in agg:
        per = (a.get("period") or "").strip()
        if per not in kept_set:
            continue
        if a.get("store_label"):
            # MA/VidaPay row — the store is carried on the row (merchant/account id); no salesforce_id
            # join exists for MA. Prefer the human account name when the processor supplied one.
            label = (a.get("store_name") or a["store_label"])
            market_v = a.get("market") or "(VidaPay/MA)"
            code, num = "", _street_num(label)
        else:
            meta = by_sfid.get((a.get("salesforce_id") or "").strip())
            if meta and meta["store"]:
                label, market_v, code, num = meta["store"], meta["market"], meta["store_code"], meta["num"]
            else:
                label, market_v, code, num = UNASSIGNED, "(Unassigned)", "", ""
        d = stores.setdefault(label, {"store": label, "market": market_v,
                                      "store_code": code, "num": num, "per": {}})
        pp = d["per"].setdefault(per, {"mi": 0.0, "atu": 0.0, "subs": 0})
        pp["mi"] += safe_float(a.get("sum_mi"))
        pp["atu"] += safe_float(a.get("sum_atu"))
        pp["subs"] += int(a.get("subs") or 0)

    # assemble per-store series
    store_rows = []
    company_res = {p: 0.0 for p in kept}
    company_subs = {p: 0 for p in kept}
    for label, d in stores.items():
        series, t_res, t_subs, t_comm = [], 0.0, 0, 0.0
        for p in kept:
            pp = d["per"].get(p, {"mi": 0.0, "atu": 0.0, "subs": 0})
            res = pp["mi"] + pp["atu"]
            subs = int(pp["subs"])
            comm = round((comm_by_num.get(d["num"], {}) or {}).get(p, 0.0), 2) if d["num"] else 0.0
            series.append({"period": p, "mi": round(pp["mi"], 2), "atu": round(pp["atu"], 2),
                           "residual": round(res, 2), "subs": subs,
                           "per_sub": round(res / subs, 2) if subs else 0.0, "commission": comm})
            t_res += res
            t_subs += subs
            t_comm += comm
            company_res[p] += res
            company_subs[p] += subs
        store_rows.append({
            "store": label, "store_code": d["store_code"], "market": d["market"], "series": series,
            "totals": {"residual": round(t_res, 2), "subs": int(t_subs),
                       "per_sub": round(t_res / t_subs, 2) if t_subs else 0.0,
                       "commission": round(t_comm, 2)}})
    store_rows.sort(key=lambda x: -x["totals"]["residual"])

    # exact company line per period (commission is the true Σ, independent of store matching)
    company = []
    for p in kept:
        subs = int(company_subs[p])
        res = company_res[p]
        company.append({"period": p, "residual": round(res, 2), "subs": subs,
                        "per_sub": round(res / subs, 2) if subs else 0.0,
                        "commission": round(comm_company.get(p, 0.0), 2)})

    markets = sorted({d["market"] for d in store_rows if d["market"]})
    out = {
        "months": kept,
        "stores": store_rows,
        "company": company,
        "markets": markets,
        "note": (None if kept else
                 "No residual (MI/ATU) data yet — upload the residual report (Boost: MI/ePay sweep; "
                 "Total/VidaPay: the MA Commission Details + Daily Tx reports) first."),
    }
    out.update(_source_diagnostics(src_meta.get("source"), src_meta, kept))
    return out
