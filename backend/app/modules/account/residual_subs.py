"""Residual per subscriber (MI+ATU), per store, month over month — with a commission overlay.

Purpose: see the effect of lower commissions on the residual payout over time.

- Residual   = actual_mi_payout + actual_atu_payout per raw_mi row.
- Subscriber = a distinct phone number we are PAID residual on that month (MI+ATU nonzero).
- Residual/sub = Σ residual ÷ distinct paid phones, per store per month.
- Store       = Boost: raw_mi.salesforce_id → store_mapping.salesforce_id (the clean join gp_report
                uses). MA/VidaPay: the row's PROCESSOR ACCOUNT → the mig-314 account→store index
                (`ma_store_pnl.canonical_store_index`), the same canonical chain
                `payables.engine.resolve_ma_store` uses — never the bare account id, never the
                master-agent entity name. Either way, a key that doesn't resolve goes to an
                "(Unassigned)" bucket so the company total stays complete (residual for ALL
                companies) and the unplaced accounts are NAMED in the payload.
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
#
# It is a label FAMILY, not one literal (2026-08-10). An exact `.eq("Residual")` matched only 346 of
# luxelink's July 2026 rows worth $7,549.05 and silently skipped 5,602 rows labelled
# "Trac Autopay Residual" worth $16,455.06 — 69% of the month's real residual. Both are the recurring
# per-subscriber residual the owner's 08-05 ruling points at; nothing else in the live feed carries
# "residual" in its product_name. Matching the family also survives the carrier adding another
# residual label without a code change, which a literal cannot.
#
# ONE definition, used by BOTH the residual-per-sub report (below) and the P&L's mi_income
# (account/coa.py). They read the same rows through the same filter, so the report and the books
# cannot drift apart again — divergence between those two is precisely what sent this to the owner.
_MA_RESIDUAL_LABEL = "Residual"
_MA_RESIDUAL_LABEL_MATCH = "%residual%"

_MA_ATU_COLUMN = assert_money_columns(["merchant_discount"], "raw_ma_daily_tx ATU-equivalent")[0]
assert_money_columns(_MA_COMPONENTS, "raw_ma_commission MI-equivalent")


# ── MA TX → P&L booking (Phase B, owner spec 2026-09-01, mig 309) ────────────────────────────────
# "Merchant discount for each line item goes into the P&L as merchant discount, residual under
# residual." The row classification is PURE (rows + resolved config in, per-row bookings out) so
# the money rules are provable without a DB (harness_ma_tx_pnl.py); coa.build_inputs is the only
# I/O caller. RULE TWO: the order-type family and the own-line toggle are per-org CONFIG
# (commcalc.commission_org_config, mig 309) — the values below are the code DEFAULTS that apply
# when the migration hasn't run or the org has no row, and the order-type default mirrors the
# migration's column default exactly.
_MA_RESIDUAL_ORDER_TYPES_DEFAULT = ("Postpaid Residual Order",)
# P&L line keys (coa.PL_SPEC). The residual destination stays `mi_income` because its label of
# record — "MI residual income" — already names residual; adding a second residual line would split
# one figure across two heads for no reader benefit (decision recorded in coa.build_inputs).
_MA_PNL_DISCOUNT_LINE = "ma_merchant_discount"
_MA_PNL_LEGACY_DISCOUNT_LINE = "atu_income"
_MA_PNL_RESIDUAL_LINE = "mi_income"
# The ONLY raw_ma_daily_tx columns the P&L reads as money — checked at import so no future edit can
# quietly sum an identifier (merchant_invoice et al., see _MA_IDENTIFIER_COLUMNS above).
_MA_PNL_MONEY_COLUMNS = assert_money_columns(
    ["merchant_discount", "retail_cost"], "raw_ma_daily_tx P&L booking (mig 309)")


def default_ma_pnl_config():
    """The mig-309 defaults — what every org gets before the migration runs / without a config row."""
    return {"merchant_discount_own_line": True,
            "residual_order_types": list(_MA_RESIDUAL_ORDER_TYPES_DEFAULT)}


def load_ma_pnl_config(client, org_id):
    """Per-org MA TX P&L config (commcalc.commission_org_config, mig 309), org-scoped, ADAPTIVE:
    a missing table/column (pre-309) or missing row degrades to `default_ma_pnl_config()`.
    NEVER raises. Values are validated — a non-bool toggle or non-list order-type value keeps the
    default rather than guessing."""
    cfg = default_ma_pnl_config()
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("pl_merchant_discount_own_line,pl_ma_residual_order_types")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            own = rows[0].get("pl_merchant_discount_own_line")
            if isinstance(own, bool):
                cfg["merchant_discount_own_line"] = own
            ots = rows[0].get("pl_ma_residual_order_types")
            if isinstance(ots, list):
                # An explicit EMPTY list is honored: it means "label family only" (the pre-309 filter).
                cfg["residual_order_types"] = [str(t).strip() for t in ots if str(t).strip()]
    except Exception:
        pass
    return cfg


def ma_residual_row_matcher(cfg=None):
    """PURE: resolved config → predicate(product_name, order_type) for "this raw_ma_daily_tx row is
    residual". The match is the UNION of:
      (a) the product_name label FAMILY — `_MA_RESIDUAL_LABEL_MATCH` ('%residual%'), the same
          case-insensitive containment the server-side ILIKE has always applied (owner ruling
          2026-08-05/10, see the docstring above `_MA_RESIDUAL_LABEL`); and
      (b) order_type ∈ cfg['residual_order_types'] (case-insensitive, trimmed) — mig 309's widening
          for rows like order_type 'Postpaid Residual Order' whose product_name lacks the word.
    A row matching both is still ONE match (the predicate is boolean — the caller books it once)."""
    fam = _MA_RESIDUAL_LABEL_MATCH.strip("%").lower()
    if cfg is None or "residual_order_types" not in cfg:
        ots = _MA_RESIDUAL_ORDER_TYPES_DEFAULT          # unresolved config → the mig-309 default
    else:
        ots = cfg.get("residual_order_types") or ()     # explicit [] → label family only (pre-309)
    types = {str(t).strip().lower() for t in ots if str(t).strip()}

    def match(product_name, order_type=None):
        if fam and fam in str(product_name or "").lower():
            return True
        return str(order_type or "").strip().lower() in types

    return match


def ma_tx_pnl_bookings(rows, cfg=None):
    """PURE: raw_ma_daily_tx rows + resolved config → ordered per-row P&L bookings
    [(line_key, amount), ...] for coa's `add()`. Owner spec 2026-09-01 (Phase B):
      • +merchant_discount → its own "Merchant discount" line (`ma_merchant_discount`) when
        cfg['merchant_discount_own_line'] (the mig-309 default), else the legacy `atu_income` fold —
        per row, so the incremental 2-dp rounding in coa's add() is byte-identical to the old sweep.
      • −retail_cost → `mi_income` ("MI residual income") for rows the residual union matches —
        booked ONCE per row no matter how many criteria hit (the matcher is a single boolean).
    A row can legitimately book BOTH columns (they are different money: the airtime margin and the
    residual amount). Zero amounts are emitted and skipped by add(), same as today. Only the
    `_MA_PNL_MONEY_COLUMNS` are ever read as money; merchant_invoice is untouched."""
    cfg = cfg if cfg is not None else default_ma_pnl_config()
    match = ma_residual_row_matcher(cfg)
    disc_line = (_MA_PNL_DISCOUNT_LINE if cfg.get("merchant_discount_own_line", True)
                 else _MA_PNL_LEGACY_DISCOUNT_LINE)
    out = []
    for r in rows or []:
        r = r or {}
        out.append((disc_line, safe_float(r.get("merchant_discount"))))
        if match(r.get("product_name"), r.get("order_type")):
            out.append((_MA_PNL_RESIDUAL_LINE, -safe_float(r.get("retail_cost"))))
    return out


def _latest_ma_period(client, org_id):
    """(year, month) of the tenant's most recent MA period — the LATER of the two MA feeds.

    It used to read raw_ma_commission ALONE, which silently truncated the window for any tenant
    whose residual feed (raw_ma_daily_tx) runs ahead of, or exists without, the Commission Details
    sheet: the last `months` were counted back from the sheet's last month, so real residual months
    past it were filtered out by the `.in_("period", …)` sweep and read as "no data". The residual
    source must decide its own window, so both feeds vote and the later one wins. Falls back to
    today's month when neither feed carries periods."""
    best = None
    for table in ("raw_ma_daily_tx", "raw_ma_commission"):
        try:
            rows = (client.schema("commcalc").table(table)
                    .select("period_year,period_month")
                    .eq("org_id", org_id)
                    .order("period_year", desc=True).order("period_month", desc=True)
                    .limit(1).execute().data) or []
        except Exception:
            rows = []
        if rows and rows[0].get("period_year") and rows[0].get("period_month"):
            cand = (int(rows[0]["period_year"]), int(rows[0]["period_month"]))
            if best is None or cand > best:
                best = cand
    if best:
        return best
    n = datetime.now(timezone.utc)
    return n.year, n.month


def _aggregate_ma(client, org_id, months, meta=None):
    """Carrier-agnostic residual source for MA/VidaPay tenants (Total, luxelink), used when a tenant has
    NO Boost raw_mi. RESIDUAL = the mig-309 residual family on raw_ma_daily_tx (sign-flipped
    `retail_cost`); ATU-equivalent = airtime margin (`merchant_discount`) on the same rows. Store =
    the PROCESSOR ACCOUNT the row is booked against (MA rows carry no salesforce_id) — carried out
    raw as `store_label`, which `compute` resolves to the canonical store through the mig-314
    account→store index. Subscribers = distinct activation lines on MA Commission Details (each row
    = one activated line, keyed by the SAME processor account); airtime top-ups are recurring margin
    on existing lines, so they add to residual $ but not to the subscriber count.

    Returns the same per-(period, store) aggregate shape as the Boost path, or [] when the MA tables are
    empty (a data-gap until the VidaPay report ingest runs — the code path is correct, the data just
    hasn't landed). NEVER raises.

    `meta` (optional dict) is filled with per-period SOURCE COVERAGE — which of the two MA reports
    actually had rows for each period. It changes no figure; it lets the report say out loud "this
    month's residual is airtime-only because MA Commission Details was never pulled for it" instead
    of showing a silent $0 the owner has to guess at."""
    ly, lm = _latest_ma_period(client, org_id)
    want = _recent_labels(ly, lm, months)
    agg = {}  # (period, store_label) -> aggregate
    cov = {}  # period -> {"commission_rows": int, "daily_tx_rows": int, "residual_rows": int, ...}

    def _cov(period, key, n=1):
        # NOTE: counters are created ON DEMAND. This used to seed a fixed
        # {"commission_rows", "daily_tx_rows"} dict and then do `c[key] += 1`, so the very first
        # `_cov(p, "residual_rows")` raised KeyError — INSIDE the residual sweep's blanket
        # `except Exception: pass`. The whole Total-side residual aggregation aborted on its first
        # row, for every MA/VidaPay tenant, and the report showed airtime margin alone as
        # "residual" (measured on luxelink 2026-09-04: 18,070 residual rows / $73,846.71 booked as
        # $0). A counter must never be able to kill the figures it is only describing.
        c = cov.setdefault(period, {"commission_rows": 0, "daily_tx_rows": 0, "residual_rows": 0})
        c[key] = int(c.get(key) or 0) + n

    def _bucket(period, store_label, name=None):
        k = (period, store_label)
        a = agg.get(k)
        if a is None:
            a = agg[k] = {"period": period, "store_label": store_label, "store_name": name,
                          "salesforce_id": "", "market": "",
                          "sum_mi": 0.0, "sum_atu": 0.0, "subs": 0, "lines": 0}
        elif name and not a.get("store_name"):
            a["store_name"] = name
        return a

    # ── RESIDUAL + airtime margin — ONE sweep of raw_ma_daily_tx ─────────────────────────────────
    # OWNER RULING 2026-08-05 (raw_ma_daily_tx is the ONLY total-residual source) + explicit GO
    # 2026-08-10. NEGATIVE retail_cost = paid TO the dealer, so residual is the sign-flipped sum of
    # the residual-family rows; airtime margin is `merchant_discount` on every row. Summed across
    # EVERY account_name in the org (owner 2026-08-10: "the data has to be pulled from 2 sources,
    # novawave residual and luxelink residual") — the entity split is reported as coverage, never as
    # a filter, so a missing entity shows up as a gap instead of silently halving the number.
    #
    # WHICH ROWS ARE RESIDUAL is not this report's own question to answer (duplicate-check
    # 2026-09-04): it is `ma_residual_row_matcher` — the mig-309/314 booking predicate the P&L's
    # `mi_income` uses — resolved from the org's CONFIG (`load_ma_pnl_config`:
    # `pl_ma_residual_order_types`, house default 'Postpaid Residual Order'). The old code ran a
    # server-side `.ilike(product_name, '%residual%')` instead, which is only HALF that union and
    # dropped every row whose residual-ness is carried by `order_type` alone (5 live luxelink rows
    # the books DO book) — the report and the P&L drifting apart is exactly the defect this module's
    # docstring says must never recur. Reading both columns in one pass also means residual and
    # airtime margin can never disagree about which rows exist.
    ma_cfg = load_ma_pnl_config(client, org_id)
    is_residual = ma_residual_row_matcher(ma_cfg)
    try:
        start, page = 0, 1000
        while True:
            chunk = (client.schema("commcalc").table("raw_ma_daily_tx")
                     .select("id,period,account_id,account_name,product_name,order_type,"
                             "retail_cost," + _MA_ATU_COLUMN)
                     .eq("org_id", org_id).in_("period", want)
                     .order("id", desc=False)
                     .range(start, start + page - 1).execute().data) or []
            for r in chunk:
                per = (r.get("period") or "").strip()
                if not per:
                    continue
                store = (r.get("account_id") or "").strip()
                a = _bucket(per, store, name=(r.get("account_name") or None))
                a["sum_atu"] += safe_float(r.get(_MA_ATU_COLUMN))
                _cov(per, "daily_tx_rows")
                if is_residual(r.get("product_name"), r.get("order_type")):
                    a["sum_mi"] += -safe_float(r.get("retail_cost"))  # flip: + = dealer receives
                    a["lines"] += 1
                    _cov(per, "residual_rows")
                    nm = (r.get("account_name") or "").strip()
                    if nm:
                        cov.setdefault(per, {}).setdefault("entities", set()).add(nm)
            if len(chunk) < page:
                break
            start += page
    except Exception as e:                          # pragma: no cover - I/O guard
        print(f"WARN residual_subs MA daily-tx sweep failed: {e}")

    # SUBSCRIBER COUNT — still one row per activated line on MA Commission Details, keyed by the
    # SAME processor account as the residual rows, so both land on the same resolved store.
    # Counting only: no money is read from that report any more (device rebates are not residual).
    try:
        start, page = 0, 1000
        while True:
            chunk = (client.schema("commcalc").table("raw_ma_commission")
                     .select("id,period,merchant_account_id")
                     .eq("org_id", org_id).in_("period", want)
                     .order("id", desc=False)
                     .range(start, start + page - 1).execute().data) or []
            for r in chunk:
                per = (r.get("period") or "").strip()
                if not per:
                    continue
                store = (r.get("merchant_account_id") or "").strip()
                _bucket(per, store)["subs"] += 1
                _cov(per, "commission_rows")
            if len(chunk) < page:
                break
            start += page
    except Exception as e:                          # pragma: no cover - I/O guard
        print(f"WARN residual_subs MA commission sweep failed: {e}")

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


# ── Provenance copy. RULE TWO: the PROCESSOR / DISTRIBUTOR name in this copy is never a literal —
# it is the org's own mig-953 `report_term` vocabulary (`report_labels.carrier_term`; boost →
# "ePay"/"VIP Wireless", total → "VidaPay"/"T-CETRA"), resolved tenant-override > house carrier
# preset > the NEUTRAL noun. A tenant must only ever read its own carrier's words, and a carrier
# with no preset reads "payment processor" — never another carrier's vendor name.
def _source_label(source, terms):
    processor = (terms or {}).get("processor") or "payment processor"
    distributor = (terms or {}).get("distributor") or "distributor"
    if source == "boost_mi_atu":
        return processor + " — raw_mi actual MI + ATU payout"
    if source == "vidapay_ma":
        return (distributor + " / master-agent — the daily-transaction residual family (recurring "
                "per-subscriber residual, all entities) + the same feed's airtime margin. Device "
                "rebates and spiffs are NOT residual and are excluded.")
    return None


def _carrier_terms(client, org_id):
    """The org's resolved vocabulary words used in this report's copy, via the canonical resolver
    (`commcalc.report_labels.carrier_term`). Best-effort — a label-service hiccup degrades to the
    neutral nouns, never to a hardcoded vendor name."""
    out = {}
    try:
        from app.modules.commcalc import report_labels as _rl
        for key in ("processor", "distributor"):
            out[key] = _rl.carrier_term(client, org_id, key)[0]
    except Exception as e:                          # pragma: no cover - I/O guard
        print(f"WARN residual_subs carrier term resolution failed: {e}")
    return out


def _source_diagnostics(source, meta, kept, terms=None):
    """Read-only provenance for the payload: WHICH residual source answered, and — for MA/VidaPay
    tenants — the per-period coverage of the two MA reports. Moves NO figure. It exists because a
    month with MA Daily Tx rows but no MA Commission Details rows legitimately computes to
    airtime-margin-only residual and ZERO paid subscribers, which reads as "broken data" unless the
    report says so out loud. Ruling out the data cause is the first step, so the report shows it."""
    out = {"source": source or None, "source_label": _source_label(source, terms),
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


# ── MA store ATTRIBUTION for this report ─────────────────────────────────────────────────────────
# Owner report 2026-09-04: "it is also not showing the store name just the store codes, need to get
# accurate reporting and use the index to update store names."
#
# An MA/VidaPay row is booked against a PROCESSOR ACCOUNT ('170084'), not a store, and this report
# used to render that id — or, when the feed supplied one, the LEGAL ENTITY name off the row
# ('Luxelink Wireless LLC'), which is the master-agent entity, not a store either. Both are wrong on
# the owner's screen and, worse, they split one store across two labels: the residual/airtime rows
# bucketed under the entity name while the subscriber counts (keyed by the same account on MA
# Commission Details) bucketed under the bare account id, so stores showed dollars with no
# subscribers next to stores with subscribers and no dollars.
#
# The map exists and is canonical — no new derivation (duplicate-check 2026-09-04):
# `ma_store_pnl.canonical_store_index` = the mig-314 account→store index (raw_ma_fulfillment
# tspid×business_address ∪ the `ma_account_store_map` owner override) collapsed onto the org's
# canonical store spelling by `coa.store_resolver`. It is the SAME chain step 3 of
# `payables.engine.resolve_ma_store` uses for Total/MA device attribution (commit 4d5fcb0). Store
# CODE and MARKET then come from the org's own store vocabulary (store_mapping ∪ core.scope), never
# from the feed.
MA_UNASSIGNED = "(Unassigned)"


def resolve_ma_account_store(account_id, store_by_account, meta_by_address, unassigned=MA_UNASSIGNED):
    """PURE: one processor ACCOUNT id → the store row this report renders.

    `store_by_account`  {account id -> canonical store_address} (mig-314 index, already canonical).
    `meta_by_address`   {lower store_address -> {"store_code", "market"}} — the org's own vocabulary.

    Returns {"store", "store_code", "market", "num", "resolved"}. An account the index cannot place
    renders "(Unassigned)" — HONESTLY, never dropped from the report and never guessed onto a
    plausible store (the phantom-store lesson); `resolved` False is what the payload's
    `unresolved_accounts` diagnostic names so the owner can pin it in `ma_account_store_map`.
    A store the index places but the store vocabulary doesn't know still renders under its canonical
    address with a blank code — the money is real and must stay visible."""
    acct = str(account_id or "").strip()
    addr = str((store_by_account or {}).get(acct) or "").strip() if acct else ""
    if not addr:
        return {"store": unassigned, "store_code": "", "market": unassigned, "num": "",
                "resolved": False}
    m = (meta_by_address or {}).get(addr.lower()) or {}
    return {"store": addr,
            "store_code": str(m.get("store_code") or "").strip(),
            "market": str(m.get("market") or "").strip() or unassigned,
            "num": _street_num(addr),
            "resolved": True}


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
    # Blank-market mapping rows inherit from THE canonical union resolver (core.scope;
    # 2026-09-03 "1115 Liberty Ave"/LI class fix) so the market filter/group-by can't drop a store
    # whose market is spelled only in storeops.stores. The salesforce_id ATTRIBUTION join itself
    # stays store_mapping-based (that column only exists there).
    try:
        from app.core import scope as _cscope
        _rs_resolve_market, _ = _cscope.store_market_resolver(client, org_id)
    except Exception:
        _rs_resolve_market = lambda s: ""
    by_sfid, by_addr = {}, {}
    for s in sm_rows:
        addr = (s.get("store_address") or "").strip()
        market = ((s.get("market") or "").strip()
                  or _rs_resolve_market(s.get("store_address") or s.get("store_code")))
        code = (s.get("store_code") or "").strip()
        if addr:
            # store_address (canonical spelling) → the org's own code + market. This is what turns
            # a mig-314-resolved MA account into a NAMED store row; same rows, same resolver, one
            # read — no second store vocabulary.
            by_addr[addr.lower()] = {"store_code": code, "market": market}
        sf = (s.get("salesforce_id") or "").strip()
        if not sf:
            continue
        by_sfid[sf] = {"store": addr, "market": market, "store_code": code,
                       "num": _street_num(s.get("store_address"))}

    # mig-314 account→store index — built ONCE, only for the MA/VidaPay source (the Boost path
    # joins on salesforce_id and never touches it).
    ma_store_by_account, ma_seen_accounts, ma_unresolved = {}, set(), set()
    if src_meta.get("source") == "vidapay_ma":
        try:
            from app.modules.account import ma_store_pnl as _msp
            ma_store_by_account = _msp.canonical_store_index(client, org_id) or {}
        except Exception as e:                      # pragma: no cover - I/O guard
            print(f"WARN residual_subs mig-314 store index failed: {e}")

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
    UNASSIGNED = MA_UNASSIGNED
    ma_cache = {}
    stores = {}
    for a in agg:
        per = (a.get("period") or "").strip()
        if per not in kept_set:
            continue
        if "store_label" in a:
            # MA/VidaPay row — the row carries a PROCESSOR ACCOUNT, not a store. Resolve it through
            # the mig-314 account→store index (canonical spelling) and take the code/market from the
            # org's own store vocabulary; an account the index cannot place renders "(Unassigned)"
            # rather than showing the reader a bare account id or the master-agent entity name.
            acct = str(a.get("store_label") or "").strip()
            r = ma_cache.get(acct)
            if r is None:
                r = ma_cache[acct] = resolve_ma_account_store(acct, ma_store_by_account, by_addr,
                                                              unassigned=UNASSIGNED)
                if acct:
                    ma_seen_accounts.add(acct)
                    if not r["resolved"]:
                        ma_unresolved.add(acct)
            label, market_v, code, num = r["store"], r["market"], r["store_code"], r["num"]
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

    # §13c enumeration doctrine (owner 2026-09-04, B-1115/LI class): options = the org's canonical
    # market vocabulary ∪ the stamps present in this report's rows — never data-present alone.
    # "(Unassigned)" is a placement, not a market, so it is never offered as an option (the MA path
    # used to feed a synthetic "(VidaPay/MA)" stamp in here — a carrier word masquerading as a
    # market, in the enumeration the §13c guard governs).
    present = {d["market"] for d in store_rows if d["market"] and d["market"] != MA_UNASSIGNED}
    try:
        from app.core import scope as _cscope
        markets = _cscope.org_market_options(client, org_id, present)
    except Exception:
        markets = sorted(present)
    out = {
        "months": kept,
        "stores": store_rows,
        "company": company,
        "markets": markets,
        "note": None,
    }
    _terms = _carrier_terms(client, org_id)
    if not kept:
        # RULE TWO: the empty-state instruction named BOTH carriers' vendors ("Boost: MI/ePay …;
        # Total/VidaPay: …"). A tenant reads only its own carrier's word, from mig 953.
        out["note"] = ("No residual data yet \u2014 upload this tenant's residual source (the "
                       + (_terms.get("processor") or "payment processor")
                       + " residual sweep, or the master-agent commission + daily-transaction "
                         "reports) first.")
    out.update(_source_diagnostics(src_meta.get("source"), src_meta, kept, terms=_terms))
    # STORE-ATTRIBUTION provenance (read-only; moves no figure). Names the processor accounts the
    # mig-314 index could not place, so an "(Unassigned)" row is actionable — pin them in
    # commcalc.ma_account_store_map — instead of being a mystery bucket.
    if src_meta.get("source") == "vidapay_ma":
        out["store_attribution"] = {
            "index": "mig-314 account\u2192store (raw_ma_fulfillment \u222a ma_account_store_map)",
            "accounts_seen": len(ma_seen_accounts),
            "accounts_resolved": len(ma_seen_accounts) - len(ma_unresolved),
            "unresolved_accounts": sorted(ma_unresolved),
        }
        if ma_unresolved:
            out["store_note"] = (
                "UNPLACED PROCESSOR ACCOUNTS \u2014 " + str(len(ma_unresolved)) + " account(s) ("
                + ", ".join(sorted(ma_unresolved)) + ") have no store in the account\u2192store "
                "index, so their residual is reported under \"" + MA_UNASSIGNED + "\" rather than "
                "guessed onto a store. Map them (Data Imports \u2192 MA account\u2192store) to see "
                "them by store; the company total already includes them.")
    return out
