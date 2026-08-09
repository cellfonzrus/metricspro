"""Custom Report builder — the config-driven, universal report over EVERY commcalc dataset.

This module is PURE (no DB, no I/O): the dataset REGISTRY metadata + column catalogs, the RULE FIVE
server-side filter (applied BEFORE aggregation), the optional group-by aggregation, the totals, the
pick-don't-type option extraction, and the saved-definition validation. The actual reads live in
router.py's resolvers (they REUSE the existing read functions — `_sales_rows_union`, the rep_commissions
snapshot, raw_ma_commission / raw_ma_daily_tx, `_compute_feed_actuals_py`, raw_dlar_store, store_expenses,
chargeback_items, flags) and are passed in as callables. Keeping the math here makes it unit-testable
against a FakeClient with no live DB (see backend/scratchpad/custom_report_proof.py).

Design notes:
  • Registry resolution (`resolve_registry`) merges the code-default DATASETS below with the mig-211 config
    rows: code default -> HOUSE row -> the org's own row (enabled / sort_order / display_name override). So
    every tenant inherits the seeded house registry and an admin can rename/reorder/disable per org, and it
    ALL degrades to the code defaults when mig 211 hasn't run.
  • RULE FIVE (`filter_rows`) is applied SERVER-SIDE, before any aggregation, over each dataset's
    field_map (store / rep / market / day). A dataset that lacks a dimension simply isn't filtered by it.
  • Per-column permission GATE (`visible_columns`): a money column carrying a gate key (e.g. MA carrier
    income → 'carrier_residual') is DROPPED from the metadata AND the rows when the caller fails that gate,
    so it can never leak through the export (RULE FOUR).
  • Multi-dataset is honest: one section per dataset, same filter bar drives each; NO fabricated
    cross-dataset joins in v1 (sections side-by-side). Join ambitions are a v2 note.
"""

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

# Column catalog entry: {"field", "label", "type", "gate"(opt), "group"(opt bool)}.
#   type ∈ text | date | money | count | pct   (numeric = money | count | pct)
#   gate: a permission key the caller must hold to see the column (else the column is dropped). None = open.
_NUMERIC = {"money", "count", "pct"}
_SUM_TYPES = {"money", "count"}


def _col(field, label, type="text", gate=None, group=False):
    return {"field": field, "label": label, "type": type, "gate": gate, "group": group}


# ── THE CODE-DEFAULT DATASET REGISTRY ───────────────────────────────────────────────────────────────
# Each dataset: key, name, resolver (a key into router._CUSTOM_REPORT_RESOLVERS), sort_order, the RULE FIVE
# field_map (universal store/rep/market/day dims -> the resolver's row field, None when absent),
# backing_tables (for the "dataset unavailable" degradation), an optional dataset-level gate, and the
# column catalog. Money columns that must honor a permission gate carry gate=<key>.
DATASETS = [
    {
        "key": "sales_line", "name": "Sales — line items", "resolver": "sales_line", "sort_order": 10,
        "field_map": {"store": "store", "rep": "salesperson", "market": "market", "day": "trans_date"},
        "backing_tables": ["daily_sales_feed", "raw_sales"], "gate": None,
        "columns": [
            _col("store", "Store", "text", group=True),
            _col("market", "Market", "text", group=True),
            _col("salesperson", "Rep", "text", group=True),
            _col("trans_date", "Date", "date", group=True),
            _col("trans_id", "Trans ID", "text"),
            _col("department", "Department", "text", group=True),
            _col("category", "Category", "text", group=True),
            _col("contract_type", "Contract Type", "text", group=True),
            _col("product_desc", "Product", "text"),
            _col("ext_price", "Ext Price", "money"),
            _col("gp", "GP", "money"),
        ],
    },
    {
        "key": "rep_commissions", "name": "Commissions — rep payout", "resolver": "rep_commissions",
        "sort_order": 20,
        "field_map": {"store": "store", "rep": "epay_salesperson", "market": "market", "day": None},
        "backing_tables": ["rep_commissions"], "gate": None,
        "columns": [
            _col("store", "Store", "text", group=True),
            _col("market", "Market", "text", group=True),
            _col("epay_salesperson", "Rep", "text", group=True),
            _col("storeops_name", "StoreOps Name", "text"),
            _col("period", "Period", "text", group=True),
            _col("premium_acts", "Premium Acts", "count"),
            _col("byod_acts", "BYOD Acts", "count"),
            _col("upgrade_acts", "Upgrade Acts", "count"),
            _col("premium_comm", "Premium $", "money"),
            _col("byod_comm", "BYOD $", "money"),
            _col("upgrade_comm", "Upgrade $", "money"),
            _col("acc_comm", "Accessory $", "money"),
            _col("setup_fee_comm", "Setup Fee $", "money"),
            _col("trade_in_comm", "Trade-in $", "money"),
            _col("acima_comm", "ACIMA $", "money"),
            _col("subtotal", "Subtotal $", "money"),
            _col("tier", "Tier", "pct"),
            _col("total_payout", "Total Payout $", "money"),
            _col("chargeback_deduction", "Chargeback $", "money"),
            _col("final_payout", "Final Payout $", "money"),
        ],
    },
    {
        "key": "targets_actuals", "name": "Targets — achieved actuals", "resolver": "targets_actuals",
        "sort_order": 30,
        "field_map": {"store": "store_code", "rep": "rep_name", "market": "market", "day": "trans_date"},
        "backing_tables": ["daily_sales_feed", "raw_sales"], "gate": None,
        "columns": [
            _col("store_code", "Store", "text", group=True),
            _col("store", "Address", "text"),
            _col("market", "Market", "text", group=True),
            _col("rep_name", "Rep", "text", group=True),
            _col("trans_date", "Date", "date", group=True),
            _col("prem_count", "Premium Acts", "count"),
            _col("byod_count", "BYOD Acts", "count"),
            _col("upg_count", "Upgrades", "count"),
            _col("acc_gp", "Accessory $", "money"),
            _col("box_count", "Boxes", "count"),
            _col("billpay_count", "Bill Pay", "count"),
        ],
    },
    {
        "key": "kpi_metrics", "name": "KPI — store metrics (DLAR)", "resolver": "kpi_metrics",
        "sort_order": 40,
        "field_map": {"store": "location", "rep": None, "market": "market", "day": None},
        "backing_tables": ["raw_dlar_store"], "gate": None,
        "columns": [
            _col("location", "Store", "text", group=True),
            _col("address", "Address", "text"),
            _col("store_code", "Store Code", "text"),
            _col("market", "Market", "text", group=True),
            _col("total_acts", "Total Acts", "count"),
            _col("gross_adds", "Gross Adds", "count"),
            _col("total_upgrades", "Upgrades", "count"),
            _col("atu", "ATU %", "pct"),
            _col("protect_pct", "Protect %", "pct"),
            _col("byod_pct", "BYOD %", "pct"),
            _col("family_plan_pct", "Family Plan %", "pct"),
            _col("tmr3", "TMR3 %", "pct"),
            _col("aal_conversion", "AAL Conv %", "pct"),
            _col("conversion_rate", "Conversion Rate %", "pct"),
        ],
    },
    {
        "key": "store_expenses", "name": "Store expenses", "resolver": "store_expenses", "sort_order": 50,
        "field_map": {"store": "store_code", "rep": None, "market": "market", "day": None},
        "backing_tables": ["store_expenses"], "gate": None,
        "columns": [
            _col("store_code", "Store", "text", group=True),
            _col("market", "Market", "text", group=True),
            _col("period", "Period", "text", group=True),
            _col("expense_name", "Expense", "text", group=True),
            _col("expense_type", "Type", "text", group=True),
            _col("amount", "Amount $", "money"),
        ],
    },
    {
        "key": "chargebacks", "name": "Chargebacks", "resolver": "chargebacks", "sort_order": 60,
        "field_map": {"store": "store", "rep": "epay_salesperson", "market": "market", "day": None},
        "backing_tables": ["chargeback_items"], "gate": None,
        "columns": [
            _col("epay_salesperson", "Rep", "text", group=True),
            _col("store", "Store", "text", group=True),
            _col("market", "Market", "text", group=True),
            _col("period", "Period", "text", group=True),
            _col("source", "Source", "text", group=True),
            _col("description", "Description", "text"),
            _col("amount", "Amount $", "money"),
            _col("deduct", "Deducted", "text", group=True),
        ],
    },
    {
        "key": "flags", "name": "Flags", "resolver": "flags", "sort_order": 70,
        "field_map": {"store": "store_address", "rep": "epay_salesperson", "market": "market", "day": None},
        # The span filter matches the RESOLVED store too (mig 285). `field_map.store` stays
        # `store_address` — that is the human-facing store column RULE FIVE filters and groups on —
        # while `span_extra` adds the key the manager's keyset is actually built from. Strict superset:
        # a row that matched before still matches.
        "span_extra": ["store_code"],
        "backing_tables": ["flags"], "gate": None,
        "columns": [
            _col("period", "Period", "text", group=True),
            _col("severity", "Severity", "text", group=True),
            _col("flag_type", "Type", "text", group=True),
            _col("source", "Source", "text", group=True),
            _col("store_address", "Store", "text", group=True),
            _col("store_code", "Store Code", "text", group=True),
            _col("market", "Market", "text", group=True),
            _col("epay_salesperson", "Rep", "text", group=True),
            _col("description", "Description", "text"),
            _col("amount", "Amount $", "money"),
        ],
    },
    {
        # MA carrier commission = carrier-income money → gated behind 'carrier_residual' (same gate the
        # What-If carrier-income / BYOD-residual + comp/residual-trend use). The spiff/rebate money columns
        # carry gate='carrier_residual' so they are dropped (metadata + rows) for a caller without the grant.
        "key": "ma_commission", "name": "MA — carrier commission", "resolver": "ma_commission",
        "sort_order": 80,
        "field_map": {"store": None, "rep": None, "market": None, "day": None},
        "backing_tables": ["raw_ma_commission"], "gate": None,
        "columns": [
            _col("period", "Period", "text", group=True),
            _col("activation_type2", "Activation Type", "text", group=True),
            _col("imei", "IMEI", "text"),
            _col("ban", "BAN", "text"),
            _col("spiff_m1", "M1 $", "money", gate="carrier_residual"),
            _col("spiff_m2", "M2 $", "money", gate="carrier_residual"),
            _col("spiff_m3", "M3 $", "money", gate="carrier_residual"),
            _col("spiff_m4", "M4 $", "money", gate="carrier_residual"),
            _col("spiff_m5", "M5 $", "money", gate="carrier_residual"),
            _col("spiff_m6", "M6 $", "money", gate="carrier_residual"),
            _col("rebate", "Rebate $", "money", gate="carrier_residual"),
        ],
    },
    {
        "key": "ma_daily_tx", "name": "MA — daily transactions", "resolver": "ma_daily_tx",
        "sort_order": 90,
        "field_map": {"store": None, "rep": None, "market": None, "day": None},
        "backing_tables": ["raw_ma_daily_tx"], "gate": None,
        "columns": [
            _col("period", "Period", "text", group=True),
            _col("order_type", "Order Type", "text", group=True),
            _col("account_id", "Account", "text"),
            _col("order_number", "Order #", "text"),
            # NOT money: `merchant_invoice` is the Merchant Invoice NUMBER (ma_upload.FIELD_LABELS role
            # "key"), stored NUMERIC by mig 083. As a "money" column the builder SUMMED it — the same
            # defect that reported -$492,946,277,716 of "residual" on the What-If page (2026-07-30).
            # Typed "text" it is still selectable/groupable and no longer aggregates.
            _col("merchant_invoice", "Merchant invoice # (ID, not money)", "text", gate="carrier_residual"),
            _col("merchant_discount", "Merchant Discount $", "money", gate="carrier_residual"),
            _col("retail_cost", "Retail Cost $", "money", gate="carrier_residual"),
        ],
    },
]

_BY_KEY = {d["key"]: d for d in DATASETS}

# mig-210 (installment-edit-m1gate) categories interface — LOOSE COUPLING. When the dual-category mapping
# lands, sales rows may carry these fields; they are exposed as groupable/filterable columns WHEN present
# and hidden silently when absent. Keyed by dataset -> [(field, label)]. Extend as the mig-210 note lands.
DYNAMIC_COLUMNS = {
    # mig 210 stores the dual category on commcalc.item_mapping as sales_category (master/sales dim) +
    # kpi_category (KPI dim), joined by item_key. They light up here once the sales resolver joins them.
    "sales_line": [("sales_category", "Sales Category"), ("kpi_category", "KPI Category")],
}


def augment_columns(dataset, rows):
    """Return a per-request copy of `dataset` with any DYNAMIC columns (mig-210 categories) that ≥1 row
    actually carries APPENDED to its catalog as groupable text columns — so they light up automatically
    when the mapping exists and stay hidden when it doesn't. No-op (returns the same dataset) when there
    are none. Pure."""
    extra = DYNAMIC_COLUMNS.get(dataset["key"]) or []
    if not extra:
        return dataset
    present = [(f, lbl) for f, lbl in extra
               if any(r.get(f) not in (None, "") for r in rows)
               and not any(c["field"] == f for c in dataset["columns"])]
    if not present:
        return dataset
    d = dict(dataset, columns=[dict(c) for c in dataset["columns"]]
             + [_col(f, lbl, "text", group=True) for f, lbl in present])
    return d


def dataset_by_key(key):
    return _BY_KEY.get(key)


def code_registry():
    """A deep-ish copy of the code-default registry, sorted, ready to merge config over."""
    return [dict(d, columns=[dict(c) for c in d["columns"]]) for d in
            sorted(DATASETS, key=lambda d: d["sort_order"])]


def resolve_registry(config_rows):
    """Merge the code-default DATASETS with the mig-211 registry rows. `config_rows` is the list of
    commcalc.custom_report_dataset rows already read for (HOUSE ∪ this org) — [] when the table is absent
    (pre-mig-211) → the code defaults are returned verbatim.

    Precedence per dataset_key: code default -> HOUSE row -> the org's own row. HOUSE rows apply to every
    tenant; an org row (org_id != HOUSE) overrides only its own tenant. Only `enabled`, `sort_order`,
    `display_name` and an optional `column_catalog` are overridable; the resolver + field_map + gates stay
    code-defined (they are behavior, not display). Unknown dataset_keys in config are ignored (a stale row
    can't invent a dataset with no resolver). Returns the merged registry sorted by effective sort_order,
    ENABLED datasets only.
    """
    reg = {d["key"]: dict(d, columns=[dict(c) for c in d["columns"]]) for d in DATASETS}
    # Apply HOUSE rows first, then org rows, so an org override wins over the house default.
    ordered = [r for r in (config_rows or []) if str(r.get("org_id")) == HOUSE_ORG]
    ordered += [r for r in (config_rows or []) if str(r.get("org_id")) != HOUSE_ORG]
    for r in ordered:
        key = r.get("dataset_key")
        d = reg.get(key)
        if not d:
            continue  # stale/unknown key with no code resolver — ignore
        if r.get("display_name"):
            d["name"] = r["display_name"]
        if r.get("sort_order") is not None:
            d["sort_order"] = r["sort_order"]
        if r.get("enabled") is not None:
            d["enabled"] = bool(r["enabled"])
        if isinstance(r.get("column_catalog"), list) and r["column_catalog"]:
            d["columns"] = [dict(c) for c in r["column_catalog"]]
    out = [d for d in reg.values() if d.get("enabled", True)]
    out.sort(key=lambda d: (d.get("sort_order", 999), d["key"]))
    return out


def is_numeric(col):
    return col.get("type") in _NUMERIC


def col_agg(col):
    """How a column aggregates in totals / group-by: 'sum' (money+count), 'avg' (pct), else 'none'."""
    t = col.get("type")
    if t in _SUM_TYPES:
        return "sum"
    if t == "pct":
        return "avg"
    return "none"


def visible_columns(dataset, grants):
    """The dataset's columns MINUS any whose `gate` the caller lacks. `grants` is a set/dict of held gate
    keys (e.g. {'carrier_residual'}). A gated column the caller can't hold is dropped ENTIRELY so it can
    never leak through the row payload or the export (RULE FOUR)."""
    held = set(grants or ())
    return [c for c in dataset["columns"] if not c.get("gate") or c["gate"] in held]


def _norm(v):
    return "" if v is None else str(v).strip()


def filter_rows(rows, field_map, stores=None, markets=None, reps=None, day_from=None, day_to=None):
    """RULE FIVE server-side filter, applied BEFORE any aggregation. A selection list (stores / markets /
    reps) filters on the dataset's mapped field; an EMPTY/None list means 'all'. A dimension the dataset
    lacks (field_map value None) is simply not filtered by (the same bar drives every dataset; a dataset
    with no rep dimension isn't zeroed by a rep filter). day_from/day_to bound the mapped day field
    (YYYY-MM-DD compare) when present. Pure — returns a new list."""
    st = set(_norm(s) for s in (stores or []) if _norm(s))
    mk = set(_norm(m) for m in (markets or []) if _norm(m))
    rp = set(_norm(r) for r in (reps or []) if _norm(r))
    f_store, f_market, f_rep = field_map.get("store"), field_map.get("market"), field_map.get("rep")
    f_day = field_map.get("day")
    out = []
    for r in rows:
        if st and f_store and _norm(r.get(f_store)) not in st:
            continue
        if mk and f_market and _norm(r.get(f_market)) not in mk:
            continue
        if rp and f_rep and _norm(r.get(f_rep)) not in rp:
            continue
        if f_day and (day_from or day_to):
            d = _norm(r.get(f_day))[:10]
            if day_from and d < day_from:
                continue
            if day_to and d > day_to:
                continue
        out.append(r)
    return out


def resolve_group_field(dataset, group_by):
    """Map a universal group-by choice to the dataset's actual row field. group_by may be one of the
    universal dims (store/rep/market/day → field_map) OR any groupable column field. Returns the row
    field to group on, or None when the dataset can't group by that choice (then it stays ungrouped)."""
    if not group_by:
        return None
    fm = dataset.get("field_map", {})
    if group_by in ("store", "rep", "market", "day"):
        return fm.get(group_by)
    for c in dataset["columns"]:
        if c["field"] == group_by and c.get("group"):
            return group_by
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        s = _norm(v).replace("$", "").replace(",", "").replace("%", "")
        try:
            return float(s)
        except (TypeError, ValueError):
            return None


def group_and_aggregate(rows, columns, group_field):
    """Group `rows` by `group_field` and aggregate. For each group: the group column shows its value,
    numeric columns are summed (money/count) or averaged (pct), other columns blank, plus a synthetic
    'Rows' count. Returns (out_rows, out_columns). When group_field is falsy, returns the rows + columns
    unchanged. Pure — no I/O."""
    if not group_field:
        return rows, columns
    grp_col = next((c for c in columns if c["field"] == group_field), None)
    # Numeric columns survive a group; a text/date column other than the group column is dropped (its
    # value isn't defined for a group). This keeps grouped output honest.
    num_cols = [c for c in columns if is_numeric(c)]
    out_cols = ([grp_col] if grp_col else []) + num_cols + [_col("_count", "Rows", "count")]
    buckets = {}
    order = []
    for r in rows:
        g = _norm(r.get(group_field))
        b = buckets.get(g)
        if b is None:
            b = buckets[g] = {"_g": g, "_count": 0, **{c["field"]: [] for c in num_cols}}
            order.append(g)
        b["_count"] += 1
        for c in num_cols:
            n = _num(r.get(c["field"]))
            if n is not None:
                b[c["field"]].append(n)
    out = []
    for g in order:
        b = buckets[g]
        row = {"_count": b["_count"]}
        if grp_col:
            row[grp_col["field"]] = b["_g"]
        for c in num_cols:
            vals = b[c["field"]]
            if col_agg(c) == "avg":
                row[c["field"]] = round(sum(vals) / len(vals), 2) if vals else None
            else:
                row[c["field"]] = round(sum(vals), 2) if vals else 0
        out.append(row)
    return out, out_cols


def compute_totals(rows, columns):
    """Totals row over `rows`: money/count columns SUM, pct columns MEAN, text/date blank. Returns a dict
    field -> number (only for aggregating columns). Pure."""
    totals = {}
    for c in columns:
        agg = col_agg(c)
        if agg == "none":
            continue
        vals = [n for n in (_num(r.get(c["field"])) for r in rows) if n is not None]
        if not vals:
            totals[c["field"]] = 0
        elif agg == "avg":
            totals[c["field"]] = round(sum(vals) / len(vals), 2)
        else:
            totals[c["field"]] = round(sum(vals), 2)
    return totals


def option_values(rows_by_dataset):
    """Pick-don't-type filter options, unioned across the selected datasets' PRE-FILTER rows, from the
    org's REAL data (never a hard-coded list). `rows_by_dataset` is [(dataset, rows)]. Returns
    {stores, markets, reps} sorted-distinct. Pure."""
    stores, markets, reps = set(), set(), set()
    for dataset, rows in rows_by_dataset:
        fm = dataset.get("field_map", {})
        fs, fm_, fr = fm.get("store"), fm.get("market"), fm.get("rep")
        for r in rows:
            if fs and _norm(r.get(fs)):
                stores.add(_norm(r.get(fs)))
            if fm_ and _norm(r.get(fm_)):
                markets.add(_norm(r.get(fm_)))
            if fr and _norm(r.get(fr)):
                reps.add(_norm(r.get(fr)))
    return {"stores": sorted(stores), "markets": sorted(markets), "reps": sorted(reps)}


def select_columns(columns, wanted):
    """Restrict `columns` (already gate-filtered) to `wanted` (a list of field names), preserving catalog
    order. Empty/None `wanted` = keep all. When `wanted` has NO field belonging to this dataset's catalog
    (e.g. a multi-dataset request whose column picks are for a different section), keep ALL — so one
    section's column pick can't blank a co-selected section. Pure."""
    if not wanted:
        return columns
    keep = set(wanted)
    if not any(c["field"] in keep for c in columns):
        return columns
    return [c for c in columns if c["field"] in keep]


def project_rows(rows, columns):
    """Reduce each row to just the visible columns' fields (so a gated column's value never ships even if
    the resolver put it on the row). Pure."""
    fields = [c["field"] for c in columns]
    return [{f: r.get(f) for f in fields} for r in rows]


# ── Saved-definition validation ─────────────────────────────────────────────────────────────────────
def validate_definition(body, known_keys):
    """Validate a saved-report POST body. Returns (ok, cleaned_or_error). `known_keys` = the set of
    registry dataset keys valid for this org. A definition must have a non-empty name and at least one
    known dataset. config carries {datasets, columns, group_by, filters}. Pure — no I/O."""
    if not isinstance(body, dict):
        return False, "body must be an object"
    name = _norm(body.get("name"))
    if not name:
        return False, "name is required"
    cfg = body.get("config") if isinstance(body.get("config"), dict) else body
    ds = [k for k in (cfg.get("datasets") or []) if k in known_keys]
    if not ds:
        return False, "select at least one available dataset"
    cleaned = {
        "datasets": ds,
        "columns": cfg.get("columns") if isinstance(cfg.get("columns"), dict) else {},
        "group_by": cfg.get("group_by") if isinstance(cfg.get("group_by"), (str, dict)) else "",
        "filters": cfg.get("filters") if isinstance(cfg.get("filters"), dict) else {},
    }
    return True, {"name": name[:120], "config": cleaned}
