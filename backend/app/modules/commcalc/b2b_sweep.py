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


def _bounded_session(_timeout=60):
    """requests.Session with a DEFAULT per-request timeout (2026-08-04 outage class fix).
    requests has NO default timeout — a blackholed portal (e.g. an IP-blocking host that
    drops packets after SYN-ACK) hangs the calling THREAD forever. These sweeps run hourly
    in the threadpool; each eternal thread is gone for good, and once enough accumulate the
    pool exhausts and EVERY sync endpoint starves (app-wide zero-byte hangs). All Session
    verbs funnel through Session.request, so wrapping it covers get/post/head/etc. An
    explicit timeout= at a call site still wins."""
    s = requests.Session()
    _orig = s.request
    def _req(method, url, **kw):
        kw.setdefault("timeout", _timeout)
        return _orig(method, url, **kw)
    s.request = _req
    return s

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
                        "Outlet", "Shop", "Store Address", "Billing Address 1",
                        # SPECULATIVE (2026-07-25): the owner re-scheduled luxelink's b2bsoft
                        # "Inventory Aging" export (every 6h) to CARRY a store-location column, but the
                        # exact header b2bsoft emits is unconfirmed. "Store Location" normalizes to
                        # 'storelocation', which matches NOTHING above (_norm_key strips spaces, so
                        # 'Store Name'->'storename' but 'Store Location'->'storelocation'), so the
                        # store-level roll-up would still parse 0 stores and only the device-only path
                        # would save. Appended LAST so an exact 'Store'/'Location'/'Store Name' column
                        # keeps winning the exact-match pass in _first_field. Purely additive coverage:
                        # no existing export has any of these headers.
                        "Store Location", "StoreLocation", "Store Loc", "Location Desc",
                        "Location Description")
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


# ── per-DEVICE inventory-aging rows (v2, owner directive 2026-07-17) ──────────────────────────
# The store-level normalize_inventory() above rolls the file up to one $ value per store (for the
# Balance-Sheet inventory line). BUT the same Inventory Aging report carries a per-DEVICE cost the
# device-history lookup needs (owner: "the inventory aging should give the correct purchase price").
# When the file has per-device detail rows (an imei/serial + a cost column), extract_inventory_devices
# pulls one row per device: {imei, serial, sku, item, store, unit_cost, received_date, days_in_stock}.
# Same FLAT + GROUPED handling as normalize_inventory(); when the file has no per-device rows it returns
# [] and device_diagnostics() explains honestly (never faked). Column names are matched broadly (a
# b2bsoft/generic-POS export spells them many ways) — calibrate live with a one-line synonym add.
DEFAULT_IMEI_FIELDS = ("imei", "IMEI", "Imei", "ESN", "esn", "ESN/IMEI", "IMEI/ESN", "IMEI/MEID",
                       "MEID", "Device ID", "DeviceId", "Device Serial", "esn_imei")
DEFAULT_SERIAL_FIELDS = ("serial", "Serial", "Serial 1", "Serial Number", "SerialNumber", "Serial #",
                         "serial_1", "SN", "S/N")
DEFAULT_SKU_FIELDS = ("sku", "SKU", "Sku", "Item Number", "ItemNumber", "Item #", "Model Number",
                      "ModelNumber", "Part Number", "Part #", "UPC", "Product ID", "ProductId")
DEFAULT_ITEM_FIELDS = ("item", "Item", "Item Name", "ItemName", "Description", "Product", "Product Name",
                       "ProductName", "Product Desc", "Model", "Device", "Device Model", "device_model",
                       # b2bsoft serialized-device export (luxelink, 2026-07-25) spells the description
                       # 'Product Desc Full' — appended LAST so an exact 'Item'/'Description' column still
                       # wins. Purely additive: these rows stored item=NULL before (blank device model on
                       # the device-history page).
                       "Product Desc Full", "Product Description", "Item Description")
DEFAULT_DEVICE_COST_FIELDS = ("Unit Cost", "UnitCost", "unit_cost", "Cost", "cost", "Item Cost",
                              "ItemCost", "Device Cost", "DeviceCost", "Our Cost", "Dealer Cost",
                              "Purchase Price", "Acquisition Cost", "Ext Cost", "Extended Cost")
DEFAULT_RECEIVED_FIELDS = ("Received Date", "ReceivedDate", "received_date", "Date Received",
                           "Received", "Acquired Date", "Acquired", "Date Added", "DateAdded",
                           "Add Date", "In Date", "Stock Date", "Created Date")
# 'Age in Company' (b2bsoft serialized-device export, luxelink 2026-07-25) = TOTAL age of the unit in the
# company — the aging number a device-history reader means. Deliberately NOT mapping 'Age in Store': that is
# the age at its CURRENT location and resets on a transfer, so it would understate a transferred device's
# age; it stays available verbatim in inventory_aging_device.raw_row. Appended LAST so a file carrying a
# real 'Days In Stock' column still wins.
DEFAULT_DAYS_FIELDS = ("Days In Stock", "DaysInStock", "days_in_stock", "Days on Hand", "Days On Hand",
                       "Aging Days", "AgingDays", "Age", "Days", "Days in Inventory", "Age (days)",
                       "Age in Company", "Age In Company")


def _to_int(v):
    """A bare int (days-in-stock) from a cell, else None."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s.lower() in ("n/a", "na", "-", "--", "none", "nan"):
        return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def _iso_date_cell(v):
    """A 'YYYY-MM-DD' string from a date-ish cell (tolerates a trailing time), else None."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "nat", ""):
        return None
    t = s[:10]
    if len(t) == 10 and t[4] == "-" and t[7] == "-":
        return t
    # US m/d/Y → ISO
    for sep in ("/", "-"):
        parts = s.split(sep)
        if len(parts) == 3 and all(p.strip().isdigit() for p in parts[:3]):
            a, b, c = (p.strip() for p in parts[:3])
            try:
                if len(a) == 4:
                    return f"{int(a):04d}-{int(b):02d}-{int(c):02d}"
                yr = int(c) + (2000 if len(c) == 2 else 0)
                return f"{yr:04d}-{int(a):02d}-{int(b):02d}"
            except ValueError:
                return None
    return None


def extract_inventory_devices(raw_rows, as_of_date=None):
    """Per-device rows from an Inventory Aging file. Returns a list of dicts (device key required — a
    row with neither imei nor serial is SKIPPED, never faked). Handles FLAT (device columns on every
    row) and GROUPED ('Store: <addr>' header rows) layouts, mirroring normalize_inventory()."""
    rows = list(raw_rows or [])
    first_col = next((next(iter(r.keys())) for r in rows if isinstance(r, dict) and r), None)
    out = []
    grouped = False
    cur_store = None
    for r in rows:
        gh = _group_header_store(r)
        if gh is not None:
            grouped = True
            cur_store = gh or cur_store
            continue
        if grouped:
            if not (str(r.get(first_col, "")).strip() if first_col else ""):
                continue  # per-store subtotal / blank row
        imei = _first_field(r, DEFAULT_IMEI_FIELDS)
        serial = _first_field(r, DEFAULT_SERIAL_FIELDS)
        imei = (str(imei).strip() if imei not in (None, "") else None)
        serial = (str(serial).strip() if serial not in (None, "") else None)
        dev_key = imei or serial
        if not dev_key:
            continue  # no device identity → not a per-device row (honest skip)
        store = _first_field(r, DEFAULT_STORE_FIELDS)
        store = (str(store).strip() if store not in (None, "") else None) or (cur_store if grouped else None)
        out.append({
            "imei": dev_key,          # canonical device key (imei, else serial)
            "serial": serial,
            "sku": (lambda s: str(s).strip() if s not in (None, "") else None)(_first_field(r, DEFAULT_SKU_FIELDS)),
            "item": (lambda s: str(s).strip() if s not in (None, "") else None)(_first_field(r, DEFAULT_ITEM_FIELDS)),
            "store": store,
            "unit_cost": _num(_first_field(r, DEFAULT_DEVICE_COST_FIELDS)),
            "received_date": _iso_date_cell(_first_field(r, DEFAULT_RECEIVED_FIELDS)),
            "days_in_stock": _to_int(_first_field(r, DEFAULT_DAYS_FIELDS)),
            "as_of_date": as_of_date,
            "raw_row": {str(k): (None if v is None else str(v)) for k, v in r.items()},
        })
    return out


def device_diagnostics(raw_rows):
    """Explain a 0-device parse honestly: the file's columns, whether an imei/serial + cost column were
    matchable, and whether it was a grouped layout. Never changes what's written."""
    rows = list(raw_rows or [])
    columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    col_norm = {_norm_key(c) for c in columns}

    def _hit(cands):
        return next((c for c in cands if c in columns or _norm_key(c) in col_norm), None)
    return {"columns": columns, "n_rows": len(rows),
            "grouped": any(_group_header_store(r) is not None for r in rows),
            "imei_col": _hit(DEFAULT_IMEI_FIELDS), "serial_col": _hit(DEFAULT_SERIAL_FIELDS),
            "cost_col": _hit(DEFAULT_DEVICE_COST_FIELDS)}


def write_inventory_devices(client, org_id, devices, as_of_date):
    """Write per-device inventory-aging rows into commcalc.inventory_aging_device as a SNAPSHOT.

    OWNER REPORT 2026-08-10: "inventory is really those phones which were not sold ... the device which
    got sold did not pay rebate should not be in inventory as it got sold."  He is right, and this
    function was the reason it was wrong. It used to ONLY upsert (conflict org_id,imei) and never
    delete, so the table was a cumulative pile of every device ever seen in ANY Inventory Aging file —
    a device that sold last month simply kept its old row with a stale as_of_date. Measured before the
    fix: 1,057 of luxelink's 2,097 rows (50%) had already been sold in POS, and the rows carried 20
    distinct as_of dates instead of one.

    An Inventory Aging export IS a point-in-time snapshot of what is ON HAND, so it is now treated as
    one: write the file's devices, then drop the rows this file's own stores no longer list (their
    as_of_date stayed behind). The prune is SCOPED to the stores the file actually covers, so a
    per-store or partial export can never wipe a store it said nothing about — the one failure mode a
    blanket delete-all would have. Rows with no store are pruned only when the file itself carried
    store-less rows (i.e. it is the same device-only export shape).

    Returns the count written. Degrades gracefully if the table doesn't exist yet (mig 216 pending) —
    the caller wraps this in try/except."""
    devices = list(devices or [])
    saved = 0
    stores_seen, had_storeless = set(), False
    for d in devices:
        st = (str(d.get("store") or "").strip() or None)
        if st:
            stores_seen.add(st)
        else:
            had_storeless = True
        rec = {"org_id": org_id, "imei": d.get("imei"), "serial": d.get("serial"),
               "sku": d.get("sku"), "item": d.get("item"), "store": d.get("store"),
               "unit_cost": d.get("unit_cost"), "received_date": d.get("received_date"),
               "days_in_stock": d.get("days_in_stock"),
               "as_of_date": (d.get("as_of_date") or as_of_date), "source": "inventory_aging",
               "raw_row": d.get("raw_row"),
               "updated_at": datetime.now(timezone.utc).isoformat()}
        client.schema("commcalc").table("inventory_aging_device").upsert(
            rec, on_conflict="org_id,imei").execute()
        saved += 1

    # ── prune what this file's stores no longer list (= sold / transferred out) ──────────────────
    # Everything still on hand was just re-stamped with `as_of_date`, so "older than this file" is
    # exactly "absent from this file". Only runs when the file actually wrote something, so an empty
    # or misparsed file can never empty the inventory.
    pruned = 0
    if saved and as_of_date:
        try:
            for st in sorted(stores_seen):
                r = (client.schema("commcalc").table("inventory_aging_device")
                     .delete(count="exact").eq("org_id", org_id).eq("store", st)
                     .lt("as_of_date", as_of_date).execute())
                pruned += (getattr(r, "count", None) or 0)
            if had_storeless:
                r = (client.schema("commcalc").table("inventory_aging_device")
                     .delete(count="exact").eq("org_id", org_id).is_("store", "null")
                     .lt("as_of_date", as_of_date).execute())
                pruned += (getattr(r, "count", None) or 0)
        except Exception as e:                      # a prune failure must never lose the ingest
            print(f"WARN inventory_aging_device prune skipped: {e}")
    if pruned:
        print(f"inventory_aging_device: {saved} on hand, pruned {pruned} no longer listed")
    return saved


def write_b2b_inventory_counts(client, org_id, devices, as_of_date):
    """Per-store x device-type UNIT COUNTS from an Inventory Aging file -> commcalc.b2b_inventory.

    OWNER DIRECTIVE 2026-08-10: "inventory aging file can be used as a basis for the current inventory
    count", now that the b2bsoft portal sweep is parked behind the VidaPay proxy issue. Until this, the
    ONLY writer of b2b_inventory was the manual paste on the On-Inventory recon page, so that table was
    EMPTY for every tenant and the recon rendered nothing no matter how many Inventory Aging files
    imported cleanly — the "it is not updating the relevant tables" report.

    Buckets come from the SHARED classifier (asset.inventory_buckets.inv_bucket) — the same five types
    the recon compares against, never a second copy of the keyword rules. A device that buckets to None
    (SIM kit, accessory) is counted in `skipped`, not silently dropped into a bucket it isn't.

    Replaces this as_of's snapshot (delete-then-insert by date, the same contract the manual upload
    uses), and writes source='inventory_aging' so the recon page can say where the numbers came from.
    Returns {rows, devices, skipped, stores}. NEVER raises — a recon-feed failure must not fail the
    ingest that already wrote the value + per-device rows."""
    from app.modules.asset.inventory_buckets import inv_bucket
    out = {"rows": 0, "devices": 0, "skipped": 0, "stores": 0}
    agg = {}
    for d in (devices or []):
        st = str(d.get("store") or "").strip()
        # The bucket is read from the product description first (what the item IS), falling back to the
        # SKU — mirroring how the manual upload classifies its `category` column.
        bucket = inv_bucket(d.get("item")) or inv_bucket(d.get("sku"))
        if not st or not bucket:
            out["skipped"] += 1
            continue
        agg[(st, bucket)] = agg.get((st, bucket), 0) + 1
        out["devices"] += 1
    if not agg:
        return out
    try:
        client.schema("commcalc").table("b2b_inventory").delete() \
            .eq("org_id", org_id).eq("as_of_date", as_of_date).eq("source", "inventory_aging").execute()
        payload = [{"org_id": org_id, "store": st, "category": b, "qty": q,
                    "as_of_date": as_of_date, "source": "inventory_aging"}
                   for (st, b), q in agg.items()]
        for i in range(0, len(payload), 500):
            client.schema("commcalc").table("b2b_inventory").insert(payload[i:i + 500]).execute()
        out["rows"] = len(payload)
        out["stores"] = len({st for st, _ in agg})
    except Exception as e:
        print(f"WARN b2b_inventory counts not written: {e}")
    return out


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
    session = _bounded_session()
    session.headers.update({"User-Agent": UA})
    login(session, user, pw)
    raw = fetch_inventory_aging(session)
    store_values = normalize_inventory(raw, store_field, value_field)
    as_of = datetime.now(timezone.utc).date().isoformat()
    saved = write_inventory_values(client, org_id, store_values, as_of)
    total = round(sum(store_values.values()), 2)
    return {"stores": saved, "total_value": total, "as_of_date": as_of}
