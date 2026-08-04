"""MA "Overview of Accounts" RECONCILIATION — the master-agent portal's stated tiles vs. ours.

WHY (owner directive, in chat 2026-08-04): the Total Wireless / VidaPay master-agent portal publishes an
"Overview of Accounts" report that STATES, for one period, a fixed tile set — Activation Count, TWP Count,
Residual, Rebates Paid, Fees Margin Paid, Commissions Paid, Commissions Not Eligible, Edge (Device
Finance), Appeal Count. The owner wants the SAME tiles computed from OUR ingested data next to the stated
ones, with a per-tile delta, a per-merchant-account table sorted by |delta|, and — where the delta is
non-zero — which rows plausibly explain it. "so all activations and commission paid can be cross checked
with this report to check the validity of the data in our system".

MONEY RULE — THIS FILE IS READ-ONLY WITH RESPECT TO PAY. It reads commcalc.raw_ma_commission /
raw_ma_daily_tx (mig 083) and the uploaded report (mig 268) and it COMPARES them. It never writes
rep_commissions, commission_plans, payout schedules, the commission ledger or any pay table, and it never
triggers a recalculation. The only rows it writes are the uploaded report's own stated numbers and the
tenant's tile mapping.

RULE ONE (multi-tenant): every read and write here takes `org_id` as an argument — supplied by the
router from the QUERY PARAM the tenant middleware rewrites. There is no module-level org constant and no
house fallback in any query. Works for any Total-Wireless tenant (house + luxelink at minimum).

RULE TWO (SAP-configurable): a tile is DATA. `DEFAULT_TILES` below is the seed vocabulary; a tenant's
commcalc.ma_overview_tile_config rows override it field-by-field and may add tiles. NOTHING in the compute
path branches on a carrier name, a tenant name or a store.

AGGREGATE IN POSTGRES: the system side is computed from two small CUBES (mig 268 RPCs) — one row per
(merchant account x dimension combination) with the row count and every money column summed — so the whole
page is a handful of round trips instead of paging tens of thousands of raw rows into Python. If the
migration has not been run, `load_cube` falls back to the paged scan and the page still works (slower).

PERIOD SPELLING: every period filter goes through the caller-supplied `_pvariants` list so 'June 2026' and
'2026-06' both match. A `.eq('period', period)` here would silently return zero rows.
"""
from datetime import date as _date
import calendar as _calendar
import re as _re

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

# ── the tile vocabulary (RULE TWO seed; identical to migration 268's house seed) ──────────────────
# A tile with agg='none' is DELIBERATELY UNMAPPED: the source report's definition is not known, and the
# directive is explicit — render an honest "no source mapped" state, never a fake 0.
DEFAULT_TILES = [
    {"tile_key": "activation_count", "label": "Activation Count", "sort_order": 10,
     "value_format": "count", "source_table": "raw_ma_commission", "agg": "count",
     "value_fields": None, "sign": "as_is",
     "filter_field": "activation_type", "filter_op": "nonblank", "filter_value": None,
     "uploaded_field": "activation_count",
     "uploaded_aliases": "Activation Count,Activations,ActivationCount,Total Activations",
     "tolerance_abs": 0, "tolerance_pct": 0,
     "note": "Rows of the MA Commission Details export carrying an Activation Type (New/Add). The page "
             "also reports DISTINCT activation orders next to it — an order with several lines counts "
             "once there."},
    {"tile_key": "twp_count", "label": "TWP Count", "sort_order": 20,
     "value_format": "count", "source_table": "raw_ma_commission", "agg": "count",
     "value_fields": None, "sign": "as_is",
     "filter_field": "sub_type", "filter_op": "eq", "filter_value": "TWP",
     "uploaded_field": "twp_count", "uploaded_aliases": "TWP Count,TWP,TWPCount",
     "tolerance_abs": 0, "tolerance_pct": 0, "note": "Sub Type = TWP."},
    {"tile_key": "residual", "label": "Residual", "sort_order": 30,
     "value_format": "money", "source_table": "raw_ma_daily_tx", "agg": "sum",
     "value_fields": "retail_cost", "sign": "negate",
     "filter_field": "order_type", "filter_op": "contains", "filter_value": "Postpaid Residual Order",
     "uploaded_field": "residual", "uploaded_aliases": "Residual,Residuals,Residual Paid",
     "tolerance_abs": 0, "tolerance_pct": 0,
     "note": "The SAME residual definition the What-If / finance residual-per-sub path uses: "
             "raw_ma_daily_tx rows whose Order Type contains 'Postpaid Residual Order', summing "
             "retail_cost, sign-negated to income (whatif._CFG_DEFAULTS['plan'], mig 209 + the mig 252 "
             "amount-field correction). ASSUMPTION — if the portal's Residual tile is a different basis "
             "(MI+ATU, or every residual order type), change it in the tile mapping."},
    {"tile_key": "rebates_paid", "label": "Rebates Paid", "sort_order": 40,
     "value_format": "money", "source_table": "raw_ma_commission", "agg": "sum",
     "value_fields": "rebate", "sign": "negate",
     "filter_field": None, "filter_op": None, "filter_value": None,
     "uploaded_field": "rebates_paid", "uploaded_aliases": "Rebates Paid,Rebate,Rebates",
     "tolerance_abs": 0, "tolerance_pct": 0,
     "note": "Sum of the rebate column, sign-flipped (the export posts money paid TO the dealer as "
             "negative — the same convention /ma-commission/summary and account.residual_subs use)."},
    {"tile_key": "fees_margin_paid", "label": "Fees Margin Paid", "sort_order": 50,
     "value_format": "money", "source_table": "raw_ma_commission", "agg": "sum",
     "value_fields": "fees_margin", "sign": "negate",
     "filter_field": None, "filter_op": None, "filter_value": None,
     "uploaded_field": "fees_margin_paid", "uploaded_aliases": "Fees Margin Paid,Fees Margin,Fee Margin",
     "tolerance_abs": 0, "tolerance_pct": 0,
     "note": "Sum of fees_margin, sign-flipped to money received."},
    {"tile_key": "commissions_paid", "label": "Commissions Paid", "sort_order": 60,
     "value_format": "money", "source_table": "raw_ma_commission", "agg": "sum",
     "value_fields": "consumer_margin,device_margin", "sign": "negate",
     "filter_field": None, "filter_op": None, "filter_value": None,
     "uploaded_field": "commissions_paid",
     "uploaded_aliases": "Commissions Paid,Commission Paid,Commissions",
     "tolerance_abs": 0, "tolerance_pct": 0,
     "note": "consumer_margin + device_margin, sign-flipped. ASSUMPTION: the portal's 'Commissions Paid' "
             "is the margin pair and does NOT include the M1-M6 spiffs or the rebate (stated separately). "
             "The spiff total is shown beside it so an alternative basis is one config edit away."},
    {"tile_key": "commissions_not_eligible", "label": "Commissions Not Eligible", "sort_order": 70,
     "value_format": "count", "source_table": "raw_ma_commission", "agg": "none",
     "value_fields": None, "sign": "as_is",
     "filter_field": None, "filter_op": None, "filter_value": None,
     "uploaded_field": "commissions_not_eligible",
     "uploaded_aliases": "Commissions Not Eligible,Not Eligible,Ineligible Commissions",
     "tolerance_abs": 0, "tolerance_pct": 0,
     "note": "NO SOURCE MAPPED ON PURPOSE — the source report's definition is not known. Candidates on "
             "raw_ma_commission are line_status and suspension_reason; their real value distributions are "
             "listed under 'Unmapped tile candidates' so the owner can choose, then set agg=count + the "
             "filter in the tile mapping."},
    {"tile_key": "edge_count", "label": "Edge (Device Finance)", "sort_order": 80,
     "value_format": "count", "source_table": "raw_ma_commission", "agg": "count",
     "value_fields": None, "sign": "as_is",
     "filter_field": "is_financed", "filter_op": "truthy", "filter_value": None,
     "uploaded_field": "edge_count", "uploaded_aliases": "Edge,Edge Count,Device Finance,Device Financing",
     "tolerance_abs": 0, "tolerance_pct": 0,
     "note": "Rows whose Is Financed flag is truthy (Y/Yes/True/1). 'Edge' here is the TW FINANCING "
             "TENDER, not a Motorola Edge handset — never match this on a device model name."},
    {"tile_key": "appeal_count", "label": "Appeal Count", "sort_order": 90,
     "value_format": "count", "source_table": "", "agg": "none",
     "value_fields": None, "sign": "as_is",
     "filter_field": None, "filter_op": None, "filter_value": None,
     "uploaded_field": "appeal_count", "uploaded_aliases": "Appeal Count,Appeals",
     "tolerance_abs": 0, "tolerance_pct": 0,
     "note": "NO SOURCE MAPPED — the MA feed we ingest carries no appeal/dispute column. The tile renders "
             "the stated value with an honest 'no source mapped' system side rather than a fake 0."},
]

TILE_FIELDS = ("tile_key", "label", "sort_order", "value_format", "source_table", "agg", "value_fields",
               "sign", "filter_field", "filter_op", "filter_value", "uploaded_field",
               "uploaded_aliases", "tolerance_abs", "tolerance_pct", "note")

# Metric columns on commcalc.ma_overview_upload — the STATED side of every tile.
UPLOAD_METRICS = ("activation_count", "twp_count", "residual", "rebates_paid", "fees_margin_paid",
                  "commissions_paid", "commissions_not_eligible", "edge_count", "appeal_count")
UPLOAD_COUNT_METRICS = {"activation_count", "twp_count", "commissions_not_eligible", "edge_count",
                        "appeal_count"}

# What a tile may filter on / sum, per source table. A tile naming anything else is reported as a
# CONFIG ERROR on the page (and contributes nothing) rather than silently returning 0 — an unmapped
# column that quietly reads as "no rows matched" is exactly how a recon lies.
SOURCES = {
    "raw_ma_commission": {
        "cube_rpc": "ma_overview_commission_cube",
        "dates_rpc": "ma_overview_commission_dates",
        "account_key": "merchant_account_id",
        "dims": ("activation_type", "activation_type2", "sub_type", "line_status",
                 "suspension_reason", "is_financed", "port_status", "perfect_sale"),
        "money": ("device_margin", "consumer_margin", "consumer_financing", "rebate", "wallet_funding",
                  "fees", "fees_margin", "mrc_net_discount", "consumer_value",
                  "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6"),
        "counts": ("rows_n", "orders_n", "imei_n", "imei_blank_n"),
    },
    "raw_ma_daily_tx": {
        "cube_rpc": "ma_overview_dailytx_cube",
        "dates_rpc": "ma_overview_dailytx_dates",
        "account_key": "account_id",
        "dims": ("order_type",),
        "money": ("retail_cost", "merchant_discount", "merchant_invoice"),
        "counts": ("rows_n", "orders_n"),
    },
}

FILTER_OPS = ("eq", "neq", "in", "not_in", "contains", "nonblank", "blank", "truthy")
TRUTHY = {"y", "yes", "true", "t", "1", "financed", "edge"}


# ── small pure helpers ───────────────────────────────────────────────────────────────────────────
def safe_float(v) -> float:
    """Money/number coercion that never raises: '', None, 'nan', '$1,234.50', '(12.00)' all behave."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if f != f else f          # NaN
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "nat", "-", "n/a"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = _re.sub(r"[^0-9.\-]", "", s.strip("()"))
    if not s or s in ("-", ".", "-."):
        return 0.0
    try:
        f = float(s)
    except ValueError:
        return 0.0
    return -f if neg else f


def sign_apply(x, sign) -> float:
    """Normalize a money figure to 'money the dealer RECEIVES'. The MA exports post amounts paid TO the
    dealer as NEGATIVE, so the default for those columns is 'negate' — the same convention
    /ma-commission/summary, whatif._ma_commission_amount and account.residual_subs already use."""
    v = safe_float(x)
    s = (sign or "as_is").strip().lower()
    if s == "negate":
        return -v
    if s == "abs":
        return abs(v)
    return v


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def match_filter(group: dict, field, op, value) -> bool:
    """Does one cube group satisfy a tile's row filter? No filter field => every group matches."""
    f = _s(field)
    if not f:
        return True
    o = (_s(op) or "eq").lower()
    cell = _s(group.get(f))
    raw = _s(value)
    if o == "nonblank":
        return cell != ""
    if o == "blank":
        return cell == ""
    if o == "truthy":
        return cell.lower() in TRUTHY
    if o == "contains":
        return raw.lower() in cell.lower() if raw else True
    vals = [x.strip() for x in raw.split(",") if x.strip()]
    low = {x.lower() for x in vals}
    if o == "eq":
        return cell.lower() == (vals[0].lower() if vals else "")
    if o == "neq":
        return cell.lower() != (vals[0].lower() if vals else "")
    if o == "in":
        return cell.lower() in low
    if o == "not_in":
        return cell.lower() not in low
    return True


def tile_problems(tile) -> list:
    """Config validation for ONE tile. Returns a list of human-readable problems; empty = usable. A tile
    with problems is rendered with its system side blank and the reason shown — never a silent 0."""
    out = []
    agg = (_s(tile.get("agg")) or "none").lower()
    src = _s(tile.get("source_table"))
    if agg == "none":
        return out
    spec = SOURCES.get(src)
    if not spec:
        out.append(f"source table '{src or '(blank)'}' is not one of {', '.join(sorted(SOURCES))}")
        return out
    if agg not in ("count", "sum"):
        out.append(f"aggregate '{agg}' is not one of count | sum | none")
    if agg == "sum":
        fields = [x.strip() for x in _s(tile.get("value_fields")).split(",") if x.strip()]
        if not fields:
            out.append("aggregate is 'sum' but no value column is named")
        for f in fields:
            if f not in spec["money"]:
                out.append(f"'{f}' is not a money column of {src}")
    ff = _s(tile.get("filter_field"))
    if ff and ff not in spec["dims"]:
        out.append(f"filter column '{ff}' is not a filterable dimension of {src} "
                   f"({', '.join(spec['dims'])})")
    fo = _s(tile.get("filter_op"))
    if fo and fo.lower() not in FILTER_OPS:
        out.append(f"filter operator '{fo}' is not one of {', '.join(FILTER_OPS)}")
    return out


def tile_value(tile, groups) -> float:
    """The SYSTEM value of one tile over a list of cube groups (already narrowed to the accounts in play).
    count => Σ rows_n of matching groups; sum => Σ (Σ value_fields) sign-normalized."""
    agg = (_s(tile.get("agg")) or "none").lower()
    if agg == "none":
        return 0.0
    ff, fo, fv = tile.get("filter_field"), tile.get("filter_op"), tile.get("filter_value")
    if agg == "count":
        return float(sum(int(g.get("rows_n") or 0) for g in groups if match_filter(g, ff, fo, fv)))
    fields = [x.strip() for x in _s(tile.get("value_fields")).split(",") if x.strip()]
    total = 0.0
    for g in groups:
        if not match_filter(g, ff, fo, fv):
            continue
        total += sign_apply(sum(safe_float(g.get(f)) for f in fields), tile.get("sign"))
    return total


def delta_status(tile, uploaded, system) -> str:
    """'ok' | 'off' | 'unmapped' | 'no_report'. Tolerances are per-tile config (both default 0 = exact)."""
    if (_s(tile.get("agg")) or "none").lower() == "none":
        return "unmapped"
    if uploaded is None:
        return "no_report"
    d = abs(safe_float(system) - safe_float(uploaded))
    if d <= safe_float(tile.get("tolerance_abs")):
        return "ok"
    base = abs(safe_float(uploaded))
    pct = safe_float(tile.get("tolerance_pct"))
    if base and pct and (d / base) * 100.0 <= pct:
        return "ok"
    return "ok" if d == 0 else "off"


# ── uploaded-report parsing (pure) ───────────────────────────────────────────────────────────────
ACCOUNT_ALIASES = ("merchantaccountid", "merchant account id", "merchant account", "account id",
                   "accountid", "account", "account #", "account number", "tspid", "dealer code",
                   "location id")
NAME_ALIASES = ("account name", "accountname", "merchant name", "business name", "dealer name",
                "location name", "store", "store name")
CARRIER_ALIASES = ("carrier", "carrier name", "carriername")
PERIOD_ALIASES = ("period", "month", "report month", "reporting period", "date")


def _key(h) -> str:
    return _re.sub(r"[^a-z0-9]+", " ", _s(h).lower()).strip()


def build_header_index(tiles, headers):
    """Map each source-file header -> the ma_overview_upload column it feeds, using the tiles'
    `uploaded_aliases` (config!) plus the canonical metric name itself. Matching is case/punctuation
    insensitive. Returns (metric_by_header, account_header, name_header, carrier_header, period_header)."""
    alias_to_field = {}
    for t in tiles:
        fld = _s(t.get("uploaded_field"))
        if fld not in UPLOAD_METRICS:
            continue
        alias_to_field[_key(fld)] = fld
        alias_to_field[_key(t.get("label"))] = fld
        for a in _s(t.get("uploaded_aliases")).split(","):
            if a.strip():
                alias_to_field[_key(a)] = fld
    metric_by_header, acct_h, name_h, carr_h, per_h = {}, None, None, None, None
    for h in headers:
        k = _key(h)
        if not k:
            continue
        if k in alias_to_field:
            metric_by_header[h] = alias_to_field[k]
            continue
        if acct_h is None and k in {_key(a) for a in ACCOUNT_ALIASES}:
            acct_h = h
        elif name_h is None and k in {_key(a) for a in NAME_ALIASES}:
            name_h = h
        elif carr_h is None and k in {_key(a) for a in CARRIER_ALIASES}:
            carr_h = h
        elif per_h is None and k in {_key(a) for a in PERIOD_ALIASES}:
            per_h = h
    return metric_by_header, acct_h, name_h, carr_h, per_h


def parse_overview_rows(tiles, records, default_period, canon_period):
    """Turn the source file's records into ma_overview_upload rows (WITHOUT org/period stamping, which the
    caller does). Handles BOTH shapes the portal exports:
      • one row per merchant account carrying the metric columns (preferred), and
      • a two-column tile list (label, value) — 'Activation Count | 1.1K' — which collapses to ONE
        report-level TOTAL row (merchant_account_id='*').
    '1.1K'/'$28.3K'/'$173.7K' are expanded (K=thousand, M=million) because the portal's tiles are
    ABBREVIATED — an abbreviated tile is recorded with `stated_abbreviated` so the page can say the
    stated side is rounded and a small delta is expected.
    Returns (rows, warnings)."""
    warnings = []
    if not records:
        return [], ["the file had no data rows"]
    headers = list(records[0].keys())
    metric_by_header, acct_h, name_h, carr_h, per_h = build_header_index(tiles, headers)

    # ── shape B: a tile list (two columns: something like Metric | Value) ──
    if not metric_by_header and len(headers) >= 2:
        label_h, value_h = headers[0], headers[1]
        alias_index, _a, _b, _c, _d = build_header_index(tiles, [])
        acc, abbrev = {}, False
        for rec in records:
            fld = None
            k = _key(rec.get(label_h))
            for t in tiles:
                cands = {_key(t.get("uploaded_field")), _key(t.get("label"))}
                cands |= {_key(a) for a in _s(t.get("uploaded_aliases")).split(",") if a.strip()}
                if k in cands and _s(t.get("uploaded_field")) in UPLOAD_METRICS:
                    fld = _s(t.get("uploaded_field"))
                    break
            if not fld:
                continue
            val, ab = _expand_abbrev(rec.get(value_h))
            abbrev = abbrev or ab
            acc[fld] = val
        if not acc:
            return [], ["no tile labels in this file matched any tile's uploaded_aliases — check the "
                        "⚙ Tile mapping, or upload the per-account export"]
        row = {"merchant_account_id": "*", "account_name": "(report total)",
               "period": canon_period(default_period), "extra": {"stated_abbreviated": abbrev}}
        row.update(acc)
        if abbrev:
            warnings.append("the file states ABBREVIATED tiles (1.1K / $28.3K); they were expanded, so "
                            "small deltas against our exact figures are expected")
        return [row], warnings

    if not metric_by_header:
        return [], ["none of this file's columns matched a tile — expected headers like "
                    "'Activation Count', 'Rebates Paid', 'Commissions Paid' (edit ⚙ Tile mapping to add "
                    "your export's spellings)"]

    rows, abbrev_any = [], False
    for rec in records:
        acct = _s(rec.get(acct_h)) if acct_h else ""
        row = {"merchant_account_id": acct or "*",
               "account_name": _s(rec.get(name_h)) if name_h else None,
               "carrier_name": _s(rec.get(carr_h)) if carr_h else None,
               "period": canon_period(_s(rec.get(per_h)) or default_period) if per_h
                         else canon_period(default_period)}
        seen_any = False
        for h, fld in metric_by_header.items():
            val, ab = _expand_abbrev(rec.get(h))
            abbrev_any = abbrev_any or ab
            if val is not None:
                seen_any = True
            row[fld] = val
        extra = {k: _s(v) for k, v in rec.items()
                 if k not in metric_by_header and k not in (acct_h, name_h, carr_h, per_h) and _s(v)}
        if abbrev_any:
            extra["stated_abbreviated"] = True
        row["extra"] = extra or None
        if seen_any or acct:
            rows.append(row)
    if not acct_h:
        warnings.append("no account column was found — every row was recorded as the report TOTAL ('*')")
    if abbrev_any:
        warnings.append("the file states ABBREVIATED values (1.1K / $28.3K); they were expanded, so small "
                        "deltas against our exact figures are expected")
    # Collapse duplicates within the file (last wins) so the DB upsert key can never conflict with itself.
    merged = {}
    for r in rows:
        merged[(r["period"], r["merchant_account_id"])] = r
    return list(merged.values()), warnings


_ABBREV = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}


def _expand_abbrev(v):
    """('1.1K' -> 1100.0, True) · ('$28.3K' -> 28300.0, True) · ('279' -> 279.0, False) · ('' -> None,
    False). The bool says the source value was ABBREVIATED, i.e. the stated side is rounded."""
    s = _s(v)
    if not s or s.lower() in ("nan", "none", "-", "n/a"):
        return None, False
    m = _re.match(r"^\(?\s*[-+]?\s*[$]?\s*([0-9,]*\.?[0-9]+)\s*([kKmMbB])\s*\)?$", s)
    if m:
        base = float(m.group(1).replace(",", "")) * _ABBREV[m.group(2).lower()]
        if s.strip().startswith("(") or "-" in s.split(m.group(1))[0]:
            base = -base
        return base, True
    return safe_float(s), False


# ── config resolution (DB) ───────────────────────────────────────────────────────────────────────
def resolve_tiles(client, org_id):
    """The tenant's tiles. Order: the org's own ma_overview_tile_config rows override DEFAULT_TILES
    field-by-field (a NULL config field keeps the default); config rows with a new tile_key are appended;
    is_active=false hides a tile. Absent table / zero rows => the code defaults, unchanged, so a tenant
    that has never run migration 268 still gets a working page. Returns (tiles, source)."""
    by_key = {t["tile_key"]: dict(t) for t in DEFAULT_TILES}
    src = "code_default"
    try:
        rows = (client.schema("commcalc").table("ma_overview_tile_config").select("*")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        rows = []
    if rows:
        src = "org_config"
        for r in rows:
            k = _s(r.get("tile_key"))
            if not k:
                continue
            base = by_key.get(k, {"tile_key": k, "label": k, "sort_order": 500, "value_format": "count",
                                  "source_table": "", "agg": "none", "sign": "as_is"})
            for f in TILE_FIELDS:
                if f in r and r.get(f) is not None:
                    base[f] = r.get(f)
            base["is_active"] = r.get("is_active", True)
            by_key[k] = base
    tiles = [t for t in by_key.values() if t.get("is_active", True) is not False]
    tiles.sort(key=lambda t: (int(t.get("sort_order") or 500), _s(t.get("label"))))
    return tiles, src


# ── cube loading (Postgres first, paged scan as the graceful fallback) ───────────────────────────
def load_cube(client, org_id, source_table, periods, accounts=None):
    """One row per (account x dimensions) with rows_n + every money column summed. Returns (groups, via)
    where via is 'rpc' or 'python_fallback'. NEVER raises: a missing migration degrades to the scan."""
    spec = SOURCES.get(source_table)
    if not spec:
        return [], "unknown_source"
    args = {"p_org": org_id, "p_periods": list(periods or []),
            "p_accounts": list(accounts) if accounts else None}
    try:
        data = client.schema("commcalc").rpc(spec["cube_rpc"], args).execute().data
        if data is not None:
            return list(data), "rpc"
    except Exception as e:
        print(f"WARN ma_overview {spec['cube_rpc']} unavailable, falling back to scan: {e}")
    return _cube_fallback(client, org_id, source_table, periods, accounts), "python_fallback"


def _cube_fallback(client, org_id, source_table, periods, accounts=None):
    """The same cube, grouped in Python off a paged scan. Only used when migration 268's RPCs are absent."""
    spec = SOURCES[source_table]
    akey = spec["account_key"]
    cols = ",".join((akey, "tx_date", "period") + spec["dims"] + spec["money"] +
                    (("activation_order", "imei") if source_table == "raw_ma_commission"
                     else ("order_number",)))
    out, start, page = {}, 0, 1000
    acc_set = {str(a) for a in accounts} if accounts else None
    while True:
        try:
            q = (client.schema("commcalc").table(source_table).select(cols).eq("org_id", org_id))
            if periods:
                q = q.in_("period", list(periods))
            chunk = q.range(start, start + page - 1).execute().data or []
        except Exception as e:
            print(f"WARN ma_overview fallback scan of {source_table} failed: {e}")
            break
        for r in chunk:
            acct = _s(r.get(akey)) or "?"
            if acc_set and acct not in acc_set:
                continue
            key = (acct,) + tuple(_s(r.get(d)) for d in spec["dims"])
            g = out.get(key)
            if g is None:
                g = {akey: acct, "rows_n": 0, "orders_n": 0, "imei_n": 0, "imei_blank_n": 0,
                     "min_tx_date": None, "max_tx_date": None,
                     "_orders": set(), "_imeis": set()}
                for i, d in enumerate(spec["dims"]):
                    g[d] = key[i + 1]
                for m in spec["money"]:
                    g[m] = 0.0
                if source_table == "raw_ma_daily_tx":
                    g["account_name"] = ""
                out[key] = g
            g["rows_n"] += 1
            for m in spec["money"]:
                g[m] += safe_float(r.get(m))
            if source_table == "raw_ma_commission":
                o, im = _s(r.get("activation_order")), _s(r.get("imei"))
                if o:
                    g["_orders"].add(o)
                if im:
                    g["_imeis"].add(im)
                else:
                    g["imei_blank_n"] += 1
            else:
                o = _s(r.get("order_number"))
                if o:
                    g["_orders"].add(o)
                if not g.get("account_name"):
                    g["account_name"] = _s(r.get("account_name"))
            d = _s(r.get("tx_date"))[:10]
            if d:
                g["min_tx_date"] = d if not g["min_tx_date"] else min(g["min_tx_date"], d)
                g["max_tx_date"] = d if not g["max_tx_date"] else max(g["max_tx_date"], d)
        if len(chunk) < page:
            break
        start += page
    groups = []
    for g in out.values():
        g["orders_n"] = len(g.pop("_orders"))
        g["imei_n"] = len(g.pop("_imeis"))
        groups.append(g)
    return groups


def load_account_profile(client, org_id, periods, accounts=None):
    """Per merchant account: rows, DISTINCT activation orders, distinct IMEIs, blank-IMEI rows.

    Why this is not read off the cube: count(DISTINCT ...) inside a grouped cube is distinct WITHIN each
    dimension combination, so an activation whose order has a plain line AND a TWP line is counted once
    per group — exactly the case the "the portal counts orders, we count rows" explainer exists to
    surface. It gets its own account-level aggregate (mig 268 RPC), with a light paged fallback."""
    args = {"p_org": org_id, "p_periods": list(periods or []),
            "p_accounts": list(accounts) if accounts else None}
    out = {}
    try:
        data = client.schema("commcalc").rpc("ma_overview_commission_accounts", args).execute().data
        for r in (data or []):
            out[_s(r.get("merchant_account_id")) or "?"] = {
                "rows": int(r.get("rows_n") or 0), "orders": int(r.get("orders_n") or 0),
                "imei": int(r.get("imei_n") or 0), "imei_blank": int(r.get("imei_blank_n") or 0)}
        return out
    except Exception as e:
        print(f"WARN ma_overview_commission_accounts unavailable, falling back to scan: {e}")
    acc_set = {str(a) for a in accounts} if accounts else None
    seen, start, page = {}, 0, 1000
    while True:
        try:
            q = (client.schema("commcalc").table("raw_ma_commission")
                 .select("merchant_account_id,activation_order,imei").eq("org_id", org_id))
            if periods:
                q = q.in_("period", list(periods))
            chunk = q.range(start, start + page - 1).execute().data or []
        except Exception as e:
            print(f"WARN ma_overview account-profile scan failed: {e}")
            break
        for r in chunk:
            a = _s(r.get("merchant_account_id")) or "?"
            if acc_set and a not in acc_set:
                continue
            d = seen.setdefault(a, {"rows": 0, "orders": set(), "imei": set(), "imei_blank": 0})
            d["rows"] += 1
            o, im = _s(r.get("activation_order")), _s(r.get("imei"))
            if o:
                d["orders"].add(o)
            if im:
                d["imei"].add(im)
            else:
                d["imei_blank"] += 1
        if len(chunk) < page:
            break
        start += page
    for a, d in seen.items():
        out[a] = {"rows": d["rows"], "orders": len(d["orders"]), "imei": len(d["imei"]),
                  "imei_blank": d["imei_blank"]}
    return out


def load_dates(client, org_id, source_table, periods, accounts=None):
    """(tx_date, stored period, rows) triples for the boundary explainer. [] when unavailable."""
    spec = SOURCES.get(source_table)
    if not spec:
        return []
    args = {"p_org": org_id, "p_periods": list(periods or []),
            "p_accounts": list(accounts) if accounts else None}
    try:
        data = client.schema("commcalc").rpc(spec["dates_rpc"], args).execute().data
        return list(data or [])
    except Exception as e:
        print(f"WARN ma_overview {spec['dates_rpc']} unavailable: {e}")
        return []


# ── the delta explainers ─────────────────────────────────────────────────────────────────────────
def month_bounds(period, month_year):
    """(first_day, last_day) ISO strings for a month-period, or (None, None) when it isn't one."""
    mo, yr = month_year(period)
    if not (1 <= int(mo or 0) <= 12 and yr):
        return None, None
    last = _calendar.monthrange(int(yr), int(mo))[1]
    return _date(int(yr), int(mo), 1).isoformat(), _date(int(yr), int(mo), last).isoformat()


def date_boundary_explain(date_rows, period, month_year):
    """Rows that plausibly belong to a NEIGHBOURING month: those on the first or last calendar day of the
    period, and — the sharper signal — those whose tx_date month does not match the period they are filed
    under (the month-boundary / period-spelling bug class). Also counts rows with no date at all."""
    first, last = month_bounds(period, month_year)
    mo, yr = month_year(period)
    on_first = on_last = mismatched = undated = 0
    mismatch_dates = {}
    for r in date_rows or []:
        n = int(r.get("rows_n") or 0)
        d = _s(r.get("tx_date"))[:10]
        if not d:
            undated += n
            continue
        if first and d == first:
            on_first += n
        if last and d == last:
            on_last += n
        try:
            if int(d[5:7]) != int(mo) or int(d[:4]) != int(yr):
                mismatched += n
                mismatch_dates[d] = mismatch_dates.get(d, 0) + n
        except (ValueError, TypeError):
            pass
    return {"period_first_day": first, "period_last_day": last,
            "rows_on_first_day": on_first, "rows_on_last_day": on_last,
            "rows_dated_outside_period": mismatched, "rows_with_no_date": undated,
            "outside_dates": sorted(({"tx_date": k, "rows": v} for k, v in mismatch_dates.items()),
                                    key=lambda x: -x["rows"])[:20],
            "note": ("Rows sitting on the first/last calendar day of the month are the usual explanation "
                     "for a small activation-count delta — the portal and our feed can disagree by a few "
                     "hours. Rows DATED OUTSIDE the period they are filed under are a real data defect, "
                     "not a boundary effect.")}


def candidate_distribution(groups, field, limit=25):
    """Value -> row count for one dimension, biggest first. Powers the honest 'no source mapped' tiles:
    the owner picks the right line_status / suspension_reason values from real data instead of guessing."""
    acc = {}
    for g in groups:
        acc[_s(g.get(field)) or "(blank)"] = acc.get(_s(g.get(field)) or "(blank)", 0) + int(g.get("rows_n") or 0)
    out = sorted(({"value": k, "rows": v} for k, v in acc.items()), key=lambda x: -x["rows"])
    return out[:limit]


# ── the recon payload ────────────────────────────────────────────────────────────────────────────
def load_uploaded(client, org_id, period_variants):
    """The stored "Overview of Accounts" rows for a period (any spelling). [] when mig 268 is unrun."""
    try:
        return (client.schema("commcalc").table("ma_overview_upload").select("*")
                .eq("org_id", org_id).in_("period", list(period_variants)).execute().data) or []
    except Exception as e:
        print(f"WARN ma_overview_upload unavailable (run migration 268?): {e}")
        return []


def uploaded_periods(client, org_id):
    """Every period that has a stored overview report, newest-looking first. Pick-don't-type source."""
    try:
        rows = (client.schema("commcalc").table("ma_overview_upload").select("period")
                .eq("org_id", org_id).limit(20000).execute().data) or []
    except Exception:
        return []
    return sorted({_s(r.get("period")) for r in rows if _s(r.get("period"))}, reverse=True)


def _uploaded_totals(rows):
    """Tile totals from the stored report. Per-account rows are SUMMED; a report-level '*' row is used
    only for the metrics no per-account row supplies (so the two shapes can coexist without double
    counting). Returns (totals_by_metric, by_account, used_total_row_for)."""
    per_acct = [r for r in rows if _s(r.get("merchant_account_id")) not in ("", "*")]
    total_row = next((r for r in rows if _s(r.get("merchant_account_id")) == "*"), None)
    totals, used_star = {}, []
    for m in UPLOAD_METRICS:
        vals = [r.get(m) for r in per_acct if r.get(m) is not None]
        if vals:
            totals[m] = float(sum(safe_float(v) for v in vals))
        elif total_row is not None and total_row.get(m) is not None:
            totals[m] = safe_float(total_row.get(m))
            used_star.append(m)
        else:
            totals[m] = None
    by_account = {}
    for r in per_acct:
        k = _s(r.get("merchant_account_id"))
        d = by_account.setdefault(k, {"account_id": k, "account_name": _s(r.get("account_name")) or None})
        for m in UPLOAD_METRICS:
            if r.get(m) is not None:
                d[m] = safe_float(d.get(m)) + safe_float(r.get(m))
    return totals, by_account, used_star


def _groups_by_account(groups, account_key):
    out = {}
    for g in groups:
        out.setdefault(_s(g.get(account_key)) or "?", []).append(g)
    return out


def compute(client, org_id, period, pvariants, canon_period, month_year,
            accounts=None, include_explain=True):
    """The whole recon for ONE period: tiles (stated vs computed vs delta), the per-account cross-check
    sorted by |delta|, the delta explainers, and the honest 'unmapped tile' candidates.

    READ-ONLY. `accounts` (a list of merchant-account ids) narrows BOTH sides — tiles, table, explainers
    and therefore the exports (WYSIWYG). Every DB call is org-scoped to the caller's org_id."""
    tiles, cfg_source = resolve_tiles(client, org_id)
    variants = list(pvariants(period)) if period else []
    canon = canon_period(period) if period else ""

    needed = {_s(t.get("source_table")) for t in tiles
              if (_s(t.get("agg")) or "none").lower() != "none" and _s(t.get("source_table")) in SOURCES}
    cubes, vias = {}, {}
    for src in sorted(needed) or []:
        cubes[src], vias[src] = load_cube(client, org_id, src, variants, accounts)
    # Always load the commission cube: the per-account table, the candidate distributions and the
    # activation/order diagnostics all read it, even when every commission tile is unmapped.
    if "raw_ma_commission" not in cubes:
        cubes["raw_ma_commission"], vias["raw_ma_commission"] = load_cube(
            client, org_id, "raw_ma_commission", variants, accounts)

    up_rows = load_uploaded(client, org_id, variants) if variants else []
    # RULE FIVE / WYSIWYG: an account filter narrows BOTH sides. Narrowing only our cube would compare a
    # filtered system total against the report's FULL total and invent a delta out of nothing. The
    # report-level '*' row is dropped under a filter for the same reason — it states every account.
    if accounts:
        _sel = {str(a) for a in accounts}
        up_rows = [r for r in up_rows if _s(r.get("merchant_account_id")) in _sel]
    up_totals, up_by_account, used_star = _uploaded_totals(up_rows)

    out_tiles = []
    for t in tiles:
        probs = tile_problems(t)
        src = _s(t.get("source_table"))
        groups = cubes.get(src, [])
        mapped = (_s(t.get("agg")) or "none").lower() != "none" and not probs
        system = tile_value(t, groups) if mapped else None
        stated = up_totals.get(_s(t.get("uploaded_field"))) if _s(t.get("uploaded_field")) else None
        delta = (system - stated) if (mapped and stated is not None) else None
        out_tiles.append({
            "tile_key": t.get("tile_key"), "label": t.get("label"),
            "value_format": t.get("value_format") or "count",
            "uploaded": stated, "system": system, "delta": delta,
            "delta_pct": (round((delta / stated) * 100.0, 2) if (delta is not None and stated) else None),
            "status": ("config_error" if probs else delta_status(t, stated, system)),
            "mapped": mapped, "config_problems": probs,
            "source": ({"table": src, "agg": t.get("agg"), "fields": t.get("value_fields"),
                        "sign": t.get("sign"), "filter": (
                            f"{t.get('filter_field')} {t.get('filter_op')} {t.get('filter_value') or ''}".strip()
                            if _s(t.get("filter_field")) else None)}),
            "uploaded_field": t.get("uploaded_field"),
            "stated_from_total_row": _s(t.get("uploaded_field")) in used_star,
            "note": t.get("note"),
        })

    # ── per-account cross-check (this is how a bad account is actually found) ──
    comm_groups = cubes.get("raw_ma_commission", [])
    tx_groups = cubes.get("raw_ma_daily_tx", [])
    comm_by_acct = _groups_by_account(comm_groups, "merchant_account_id")
    tx_by_acct = _groups_by_account(tx_groups, "account_id")
    acct_names = {}
    for g in tx_groups:
        if _s(g.get("account_name")):
            acct_names.setdefault(_s(g.get("account_id")), _s(g.get("account_name")))
    for k, v in up_by_account.items():
        if v.get("account_name"):
            acct_names.setdefault(k, v["account_name"])

    profile = load_account_profile(client, org_id, variants, accounts)
    all_accts = sorted(set(comm_by_acct) | set(tx_by_acct) | set(up_by_account) | set(profile))
    per_account = []
    for a in all_accts:
        gl = comm_by_acct.get(a, []) + tx_by_acct.get(a, [])
        row = {"account_id": a, "account_name": acct_names.get(a) or None,
               "in_system": a in comm_by_acct or a in tx_by_acct,
               "in_report": a in up_by_account,
               "rows": (profile.get(a) or {}).get(
                   "rows", sum(int(g.get("rows_n") or 0) for g in comm_by_acct.get(a, []))),
               "distinct_orders": (profile.get(a) or {}).get("orders", 0),
               "missing_imei_rows": (profile.get(a) or {}).get("imei_blank", 0)}
        worst = 0.0
        for t in tiles:
            if (_s(t.get("agg")) or "none").lower() == "none" or tile_problems(t):
                continue
            k = t.get("tile_key")
            src = _s(t.get("source_table"))
            g = comm_by_acct.get(a, []) if src == "raw_ma_commission" else tx_by_acct.get(a, [])
            sysv = tile_value(t, g)
            upv = (up_by_account.get(a) or {}).get(_s(t.get("uploaded_field")))
            row[f"sys_{k}"] = sysv
            row[f"up_{k}"] = upv
            row[f"d_{k}"] = (sysv - safe_float(upv)) if upv is not None else None
            if upv is not None:
                base = abs(safe_float(upv)) or 1.0
                worst = max(worst, abs(sysv - safe_float(upv)) / base)
        row["_rank"] = worst
        per_account.append(row)
    per_account.sort(key=lambda r: (-(r.get("_rank") or 0), r["account_id"]))
    for r in per_account:
        r.pop("_rank", None)

    payload = {
        "ok": True, "org_id": org_id, "period": canon or period,
        "period_variants": variants,
        "tiles": out_tiles,
        "per_account": per_account,
        "config_source": cfg_source,
        "cube_source": vias,
        "report": {"present": bool(up_rows), "rows": len(up_rows),
                   "accounts": len(up_by_account),
                   "has_total_row": any(_s(r.get("merchant_account_id")) == "*" for r in up_rows),
                   "source_file": next((_s(r.get("source_file")) for r in up_rows if _s(r.get("source_file"))), None),
                   "uploaded_at": max([_s(r.get("updated_at") or r.get("created_at")) for r in up_rows] or [""]) or None,
                   "stated_abbreviated": any(bool((r.get("extra") or {}).get("stated_abbreviated"))
                                             for r in up_rows if isinstance(r.get("extra"), dict))},
        "account_options": [{"id": a, "label": (acct_names.get(a) or a)} for a in all_accts],
        "assumptions": _assumptions(tiles, up_rows),
    }
    if include_explain:
        payload["explain"] = _explain(client, org_id, variants, period, month_year, accounts,
                                      comm_groups, tx_groups, comm_by_acct, tx_by_acct, up_by_account,
                                      profile)
        payload["unmapped_candidates"] = {
            "line_status": candidate_distribution(comm_groups, "line_status"),
            "suspension_reason": candidate_distribution(comm_groups, "suspension_reason"),
            "sub_type": candidate_distribution(comm_groups, "sub_type"),
            "activation_type": candidate_distribution(comm_groups, "activation_type"),
            "order_type": candidate_distribution(tx_groups, "order_type"),
        }
    return payload


def _assumptions(tiles, up_rows):
    """What this page is ASSUMING, stated on the page itself rather than guessed silently."""
    out = []
    for t in tiles:
        if (_s(t.get("agg")) or "none").lower() == "none":
            out.append({"tile": t.get("label"), "kind": "unmapped",
                        "text": f"“{t.get('label')}” has NO system source mapped — the stated value is "
                                f"shown alone. {_s(t.get('note'))}"})
    res = next((t for t in tiles if t.get("tile_key") == "residual"), None)
    if res and (_s(res.get("agg")) or "none").lower() != "none":
        out.append({"tile": "Residual", "kind": "basis",
                    "text": "Residual is computed on the SAME basis the What-If / finance residual-per-sub "
                            "path uses — raw_ma_daily_tx rows whose Order Type contains "
                            f"“{_s(res.get('filter_value'))}”, summing {_s(res.get('value_fields'))}, "
                            "sign-normalized to income. Whether the portal's Residual tile means the same "
                            "thing (vs. MI+ATU, or every residual order type) is an OPEN QUESTION."})
    comm = next((t for t in tiles if t.get("tile_key") == "commissions_paid"), None)
    if comm and (_s(comm.get("agg")) or "none").lower() != "none":
        out.append({"tile": "Commissions Paid", "kind": "basis",
                    "text": f"Commissions Paid = {_s(comm.get('value_fields'))} only — the M1–M6 spiffs and "
                            "the rebate are NOT included (the portal states the rebate separately). The "
                            "spiff total is shown under “Basis alternatives” if the portal's figure is "
                            "closer to that."})
    if any(bool((r.get("extra") or {}).get("stated_abbreviated")) for r in up_rows
           if isinstance(r.get("extra"), dict)):
        out.append({"tile": "(all)", "kind": "precision",
                    "text": "The stored report's values were ABBREVIATED in the source (1.1K / $28.3K) and "
                            "were expanded on ingest — the stated side is rounded, so a delta smaller than "
                            "the rounding step is not a data problem. Upload the un-abbreviated export for "
                            "an exact cross-check."})
    return out


def _explain(client, org_id, variants, period, month_year, accounts,
             comm_groups, tx_groups, comm_by_acct, tx_by_acct, up_by_account, profile=None):
    """Which rows plausibly explain a non-zero delta."""
    prof = profile or {}
    rows_n = (sum(int(p.get("rows") or 0) for p in prof.values()) if prof
              else sum(int(g.get("rows_n") or 0) for g in comm_groups))
    # DISTINCT orders across the whole period — from the account profile, never Sigma of the cube's
    # per-group distincts (which double-counts an order that spans two dimension combinations).
    orders_n = sum(int(p.get("orders") or 0) for p in prof.values()) if prof else 0
    imei_blank = (sum(int(p.get("imei_blank") or 0) for p in prof.values()) if prof
                  else sum(int(g.get("imei_blank_n") or 0) for g in comm_groups))
    spiffs = sum(sign_apply(sum(safe_float(g.get(f"spiff_m{i}")) for i in range(1, 7)), "negate")
                 for g in comm_groups)
    only_report = sorted(set(up_by_account) - (set(comm_by_acct) | set(tx_by_acct)))
    only_system = sorted((set(comm_by_acct) | set(tx_by_acct)) - set(up_by_account)) if up_by_account else []
    return {
        "accounts_only_in_report": [{"account_id": a,
                                     "account_name": (up_by_account.get(a) or {}).get("account_name")}
                                    for a in only_report],
        "accounts_only_in_system": [{"account_id": a,
                                     "rows": sum(int(g.get("rows_n") or 0) for g in comm_by_acct.get(a, []))}
                                    for a in only_system],
        "missing_imei": {"rows": imei_blank, "of_rows": rows_n,
                         "note": "A commission row with no IMEI cannot be joined to a device, an asset "
                                 "charge or a fulfillment order — it still counts as an activation here, "
                                 "but it is the usual reason a per-device cross-check comes up short."},
        "multi_line_activations": {"rows": rows_n, "distinct_activation_orders": orders_n,
                                   "extra_lines": max(0, rows_n - orders_n),
                                   "note": "Activation Count counts ROWS. If the portal counts ORDERS, "
                                           "the difference is exactly these extra lines (an activation "
                                           "with a TWP add-on, a second SIM, an accessory SKU…)."},
        "date_boundary": date_boundary_explain(
            load_dates(client, org_id, "raw_ma_commission", variants, accounts), period, month_year),
        "residual_date_boundary": date_boundary_explain(
            load_dates(client, org_id, "raw_ma_daily_tx", variants, accounts), period, month_year),
        "basis_alternatives": {
            "spiff_m1_m6_total": round(spiffs, 2),
            "consumer_margin": round(sign_apply(sum(safe_float(g.get("consumer_margin")) for g in comm_groups), "negate"), 2),
            "device_margin": round(sign_apply(sum(safe_float(g.get("device_margin")) for g in comm_groups), "negate"), 2),
            "consumer_financing": round(sign_apply(sum(safe_float(g.get("consumer_financing")) for g in comm_groups), "negate"), 2),
            "wallet_funding": round(sign_apply(sum(safe_float(g.get("wallet_funding")) for g in comm_groups), "negate"), 2),
            "fees": round(sign_apply(sum(safe_float(g.get("fees")) for g in comm_groups), "negate"), 2),
            "daily_tx_merchant_discount": round(sign_apply(sum(safe_float(g.get("merchant_discount")) for g in tx_groups), "negate"), 2),
            "note": "If a money tile is off, compare its stated value against these — a mismatch that "
                    "lands exactly on one of them means the portal's tile uses a different basis, which "
                    "is a TILE MAPPING edit, not a data defect.",
        },
    }


# ── persistence of the uploaded report (the ONLY write path here) ────────────────────────────────
def upload_rows_to_records(rows, org_id, source_file, uploaded_by, month_year):
    """Stamp org/period parts onto parsed rows. RULE ONE: org_id comes from the caller's query param."""
    out = []
    for r in rows:
        rec = {k: v for k, v in r.items() if k in
               ("period", "merchant_account_id", "account_name", "carrier_name", "extra") + UPLOAD_METRICS}
        mo, yr = month_year(rec.get("period") or "")
        rec["org_id"] = org_id
        rec["period_month"] = int(mo) if 1 <= int(mo or 0) <= 12 else None
        rec["period_year"] = int(yr) if yr else None
        rec["merchant_account_id"] = _s(rec.get("merchant_account_id")) or "*"
        rec["source_file"] = source_file or None
        rec["uploaded_by"] = uploaded_by or None
        out.append(rec)
    return out


def persist_upload(client, org_id, records):
    """Idempotent REPLACE by (org, period, account) — a re-upload of one period can never touch another.
    Deletes only the (period, account) keys present in THIS file, then inserts. Returns {saved, periods}."""
    if not records:
        return {"saved": 0, "periods": {}}
    periods = {}
    for r in records:
        periods[_s(r.get("period"))] = periods.get(_s(r.get("period")), 0) + 1
    for p, _n in periods.items():
        accts = sorted({_s(r.get("merchant_account_id")) for r in records if _s(r.get("period")) == p})
        for i in range(0, len(accts), 200):
            (client.schema("commcalc").table("ma_overview_upload").delete()
             .eq("org_id", org_id).eq("period", p).in_("merchant_account_id", accts[i:i + 200]).execute())
    saved = 0
    for i in range(0, len(records), 500):
        res = (client.schema("commcalc").table("ma_overview_upload")
               .insert(records[i:i + 500]).execute())
        saved += len(res.data or [])
    return {"saved": saved or len(records), "periods": periods}
