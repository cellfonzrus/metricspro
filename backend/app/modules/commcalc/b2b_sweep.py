"""b2bsoft "Inventory Aging" auto-sweep — pulls the real-time on-hand inventory VALUE per
store from wsreports.b2bsoft.com and writes it to commcalc.inventory_value, which the Account
Module Balance Sheet reads (COALESCE(manual_value, swept_value)) for its Inventory line.

Runs INSIDE the backend (Railway) on a schedule (pg_cron → POST /commcalc/b2b/sweep/run-due),
the same pattern as the DLAR / VIP / epay sweeps. Credentials + schedule live in the
BACKEND-ONLY table commcalc.b2b_sweep_config; the password is never returned to the browser.

╔══════════════════════════════════════════════════════════════════════════════════════════╗
║  PORTAL NOT YET REVERSE-ENGINEERED.                                                        ║
║  login() and fetch_inventory_aging() are stubs: wsreports.b2bsoft.com needs live probing  ║
║  with real credentials (as was done for boostelevatego.com / vipwireless.com). Once creds  ║
║  are supplied: build tools/b2b_scraper to capture the login flow + the Inventory Aging     ║
║  report's data endpoint + its $-value column, then fill the two stubs below. Everything    ║
║  downstream (normalize → upsert, status, scheduling, the editable Balance-Sheet line) is   ║
║  already wired, so only these two functions remain.                                        ║
╚══════════════════════════════════════════════════════════════════════════════════════════╝
"""
from datetime import datetime, timezone

import requests

BASE = "https://wsreports.b2bsoft.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


class B2BLoginError(Exception):
    """Login failed — surfaced to the admin UI without ever echoing the password."""


class B2BNotConfigured(Exception):
    """The portal client isn't reverse-engineered yet (no creds were ever supplied)."""


def _num(v):
    """Coerce a money cell ('$1,234.56', '1234.56', '', '-') to float."""
    if v is None:
        return 0.0
    s = str(v).strip().replace("$", "").replace(",", "")
    if not s or s.lower() in ("n/a", "na", "-", "--", "none"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        n = float(s)
        return -n if neg else n
    except ValueError:
        return 0.0


# ── portal client (STUBS — fill once creds are supplied) ─────────────────────────────────────
def login(session, user, pw):
    """Authenticate to wsreports.b2bsoft.com. STUB — to be implemented from a live probe."""
    raise B2BNotConfigured(
        "The b2bsoft (wsreports.b2bsoft.com) portal client is not reverse-engineered yet. "
        "Provide the b2bsoft login so the Inventory Aging report flow can be captured.")


def fetch_inventory_aging(session, **kwargs):
    """Return the Inventory Aging report as a list of raw row dicts. STUB.

    When implemented this returns rows carrying at least a store identifier and a $ value
    column (the report has a cost/value column per the user) — normalize_inventory() below
    turns them into one (store, value) pair per store."""
    raise B2BNotConfigured("fetch_inventory_aging() not implemented — provide b2bsoft credentials.")


# ── normalize (REAL — store-agnostic; the only portal-specific bit is the column names) ───────
# Override these by passing store_field / value_field once the real report columns are known.
# The candidate lists are matched EXACTLY first, then case/space/punctuation-insensitively (see
# _first_field), so 'Store Name' / 'STORE NAME' / 'store_name' all resolve without enumerating every
# spelling. Kept broad on purpose — a b2bsoft / generic-POS Inventory Aging export uses many spellings.
DEFAULT_STORE_FIELDS = ("store", "Store", "Store Name", "StoreName", "Store #", "Store No",
                        "Store Number", "Store Code", "location", "Location", "Location Name",
                        "store_name", "Site", "Site Name", "Branch", "Branch Name", "Dealer",
                        "Outlet", "Shop", "Store Address", "Billing Address 1")
DEFAULT_VALUE_FIELDS = ("inventory_value", "InventoryValue", "Inventory Value", "value", "Value",
                        "cost", "Cost", "Unit Cost", "UnitCost", "ext_cost", "ExtCost", "Ext Cost",
                        "Extended Cost", "total_cost", "TotalCost", "Total Cost", "Total Value",
                        "Inventory Cost", "On Hand Value", "Stock Value", "Item Cost", "amount",
                        "Amount", "Ext Price", "Extended Price", "Retail", "Retail Value")

# Grouped-layout header prefixes. b2bsoft can only SCHEDULE its GROUPED exports, where the store is a
# HEADER ROW ("Store: <addr>" in the first cell), not a per-line column — the exact same convention as
# the grouped "Sales Transaction Details" export (_flatten_grouped_sales in router.py). We fill that
# store DOWN onto the item/detail rows and skip the per-store SUBTOTAL rows (empty first cell) so a
# grouped Inventory Aging file parses instead of yielding 0 stores.
_GROUP_STORE_PREFIXES = ("store:", "store name:", "location:", "location name:", "site:", "branch:")


def _norm_key(s):
    """Case/space/punctuation-insensitive column-name key: 'Store Name'→'storename'."""
    return "".join(ch for ch in str(s).strip().lower() if ch.isalnum())


def _first_field(row, candidates):
    # Exact header match first (fast, unambiguous), then a normalized match so renamed-case/spacing
    # variants of a known column still resolve without listing every spelling.
    for c in candidates:
        if c in row and row[c] not in (None, ""):
            return row[c]
    cand_norm = {_norm_key(c) for c in candidates}
    for k, v in row.items():
        if v in (None, "") or _norm_key(k) not in cand_norm:
            continue
        return v
    return None


def _group_header_store(row):
    """If this row is a grouped 'Store: <addr>' header row, return the store name; else None.
    Only the FIRST non-empty cell is inspected (mirrors the grouped-sales flatten)."""
    for v in (row or {}).values():
        s = str(v).strip()
        if not s:
            continue
        low = s.lower()
        for pfx in _GROUP_STORE_PREFIXES:
            if low.startswith(pfx):
                return s[len(pfx):].strip(" :")
        return None  # first non-empty cell isn't a 'Store:' header → this is a normal data row
    return None


def normalize_inventory(raw_rows, store_field=None, value_field=None):
    """Aggregate raw Inventory Aging rows → {store: total_value}. Sums value across all rows
    (devices / categories / aging buckets) for each store.

    Handles BOTH layouts b2bsoft/POS emit: (a) FLAT — every row carries a store column; and
    (b) GROUPED — the store is a 'Store: <addr>' header row and the following item rows have no store
    column (subtotal rows, with an empty first cell, are skipped so the value isn't double-counted)."""
    store_keys = (store_field,) if store_field else DEFAULT_STORE_FIELDS
    value_keys = (value_field,) if value_field else DEFAULT_VALUE_FIELDS
    rows = list(raw_rows or [])
    first_col = next((next(iter(r.keys())) for r in rows if isinstance(r, dict) and r), None)
    out = {}
    grouped = False
    cur_store = None
    for r in rows:
        gh = _group_header_store(r)
        if gh is not None:
            grouped = True
            cur_store = gh or cur_store
            continue  # a header row carries no inventory value
        if grouped:
            # In a grouped file, a row with an empty FIRST cell is a per-store SUBTOTAL / blank —
            # skip it so its value isn't added on top of the detail rows it summarises.
            if not (str(r.get(first_col, "")).strip() if first_col else ""):
                continue
            store = _first_field(r, store_keys) or cur_store
        else:
            store = _first_field(r, store_keys)
        if store is None:
            continue
        store = str(store).strip()
        if not store:
            continue
        out[store] = round(out.get(store, 0.0) + _num(_first_field(r, value_keys)), 2)
    return out


def inventory_diagnostics(raw_rows, store_field=None, value_field=None):
    """Explain a 0-store parse honestly: the columns the file actually has, which store/value column
    (if any) we could match, and whether a grouped 'Store:' header was present. Used ONLY to build an
    actionable message for the ingest history — it never changes what is written."""
    rows = list(raw_rows or [])
    columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    store_keys = (store_field,) if store_field else DEFAULT_STORE_FIELDS
    value_keys = (value_field,) if value_field else DEFAULT_VALUE_FIELDS
    col_norm = {_norm_key(c) for c in columns}
    store_hit = next((c for c in store_keys if c in columns or _norm_key(c) in col_norm), None)
    value_hit = next((c for c in value_keys if c in columns or _norm_key(c) in col_norm), None)
    grouped = any(_group_header_store(r) is not None for r in rows)
    return {"columns": columns, "n_rows": len(rows), "grouped": grouped,
            "store_col": store_hit, "value_col": value_hit}


# ── orchestration (REAL — writes commcalc.inventory_value, preserving manual overrides) ───────
def write_inventory_values(client, org_id, store_values, as_of_date):
    """Upsert {store: value} into commcalc.inventory_value as the SWEPT value (source 'b2bsoft').
    manual_value is never touched here — a hand-entered override always wins on the Balance Sheet."""
    saved = 0
    for store, value in (store_values or {}).items():
        rec = {"org_id": org_id, "store": store, "swept_value": round(float(value), 2),
               "as_of_date": as_of_date, "source": "b2bsoft",
               "updated_at": datetime.now(timezone.utc).isoformat()}
        client.schema("commcalc").table("inventory_value").upsert(rec, on_conflict="org_id,store").execute()
        saved += 1
    return saved


def run_inventory_sweep(client, org_id, user, pw, store_field=None, value_field=None):
    """Log in → fetch Inventory Aging → normalize → upsert swept values. Returns a summary dict.
    Raises B2BNotConfigured until the portal client stubs above are implemented."""
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    login(session, user, pw)
    raw = fetch_inventory_aging(session)
    store_values = normalize_inventory(raw, store_field, value_field)
    as_of = datetime.now(timezone.utc).date().isoformat()
    saved = write_inventory_values(client, org_id, store_values, as_of)
    total = round(sum(store_values.values()), 2)
    return {"stores": saved, "total_value": total, "as_of_date": as_of}
