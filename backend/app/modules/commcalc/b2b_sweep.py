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
DEFAULT_STORE_FIELDS = ("store", "Store", "location", "Location", "store_name", "StoreName", "Site")
DEFAULT_VALUE_FIELDS = ("inventory_value", "InventoryValue", "value", "Value", "cost", "Cost",
                        "ext_cost", "ExtCost", "total_cost", "TotalCost", "amount", "Amount")


def _first_field(row, candidates):
    for c in candidates:
        if c in row and row[c] not in (None, ""):
            return row[c]
    return None


def normalize_inventory(raw_rows, store_field=None, value_field=None):
    """Aggregate raw Inventory Aging rows → {store: total_value}. Sums value across all rows
    (devices / categories / aging buckets) for each store."""
    store_keys = (store_field,) if store_field else DEFAULT_STORE_FIELDS
    value_keys = (value_field,) if value_field else DEFAULT_VALUE_FIELDS
    out = {}
    for r in raw_rows or []:
        store = _first_field(r, store_keys)
        if store is None:
            continue
        store = str(store).strip()
        if not store:
            continue
        out[store] = round(out.get(store, 0.0) + _num(_first_field(r, value_keys)), 2)
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
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    login(session, user, pw)
    raw = fetch_inventory_aging(session)
    store_values = normalize_inventory(raw, store_field, value_field)
    as_of = datetime.now(timezone.utc).date().isoformat()
    saved = write_inventory_values(client, org_id, store_values, as_of)
    total = round(sum(store_values.values()), 2)
    return {"stores": saved, "total_value": total, "as_of_date": as_of}
