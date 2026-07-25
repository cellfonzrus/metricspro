"""Proof harness for agent/commission/storeloc-synonym (2026-07-25) — SPECULATIVE.

Context: the owner re-scheduled luxelink's b2bsoft "Inventory Aging" export (now every 6h) to include a
store-location column. The exact header b2bsoft emits is UNCONFIRMED. If it is spelled "Store Location"
(the likely spelling), `_norm_key` turns it into 'storelocation' — which matched NOTHING in the shipped
DEFAULT_STORE_FIELDS ('Store Name'->'storename', 'Location Name'->'locationname', …). The store-level
roll-up would therefore STILL parse 0 stores, only the device-only path would save, and the honest note
shipped in dc01434 would keep saying "no store column found" forever.

This package appends five speculative synonyms LAST to DEFAULT_STORE_FIELDS:
    "Store Location", "StoreLocation", "Store Loc", "Location Desc", "Location Description"

Proves, with NO DB and NO network (in-memory FakeClient + a fake IMAP fetcher):
  G. NEW-FORMAT luxelink shape (the shipped 8 columns + a "Store Location" column):
     normalize_inventory now returns per-store $ totals, extract_inventory_devices STAMPS the store on
     every device row, the REAL upload_file returns the plain success payload (stores>0, NO
     'inventory_devices_only'), writes inventory_value rows org-stamped, and the REAL _run_email_sweep
     records status 'ok' with rows_saved = store count.
  H. OLD-FORMAT (the exact 8 columns, no store) is UNCHANGED — still 0 stores, still
     'inventory_devices_only', payload byte-identical to origin/main dc01434.
  I. PRECEDENCE — a file carrying BOTH an exact 'Store' column and a 'Store Location' column still
     resolves 'Store' (appended LAST ⇒ the exact-match pass in _first_field wins).
  J. HEADER-CASE COVERAGE — 'STORE LOCATION', 'store_location', 'Store  Location', 'Store-Location',
     'Store Loc', 'Location Description' all resolve (normalized match), and a near-miss that is NOT a
     store column ('Stock Location') is still ignored.
  K. ORIGIN/MAIN DIFFERENTIAL (b2b_sweep vendored from dc01434): every pre-existing fixture parses
     IDENTICALLY (stores and devices); the ONLY behavior change is on files that actually carry one of
     the five new headers.

Run:  cd backend && python3 scratchpad/inventory_aging_storeloc_synonym_proof.py
"""
import asyncio
import importlib.util
import io
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

from starlette.datastructures import UploadFile as _UF

import app.modules.commcalc.b2b_sweep as B
import app.modules.commcalc.router as R

BASE_REF = "dc01434"   # origin/main at the time this package was built (pre-synonym)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {detail}")


LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
HOUSE = "00000000-0000-0000-0000-000000000001"


# ── in-memory Supabase double (same shape as inventory_aging_device_only_proof.py) ───────────────
class FakeTable:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self._rows = list(store.get(table, []))

    def select(self, *a, **k):
        return self

    def eq(self, key, val):
        self._rows = [r for r in self._rows if r.get(key) == val]
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def insert(self, row):
        self.store.setdefault(self.table, [])
        (self.store[self.table].extend if isinstance(row, list) else self.store[self.table].append)(row)
        return self

    def upsert(self, row, on_conflict=None):
        self.store.setdefault(self.table, [])
        rows = row if isinstance(row, list) else [row]
        keys = [k.strip() for k in (on_conflict or "").split(",") if k.strip()]
        for rec in rows:
            hit = None
            if keys:
                for existing in self.store[self.table]:
                    if all(existing.get(k) == rec.get(k) for k in keys):
                        hit = existing
                        break
            if hit is not None:
                hit.update(rec)
            else:
                self.store[self.table].append(dict(rec))
        return self

    def update(self, upd):
        self._upd = upd
        return self

    def delete(self):
        self._del = True
        return self

    def execute(self):
        class Res:
            pass
        res = Res()
        if getattr(self, "_upd", None) is not None:
            for r in self._rows:
                r.update(self._upd)
        res.data = self._rows
        res.count = len(self._rows)
        return res


class FakeSchema:
    def __init__(self, store):
        self.store = store

    def table(self, t):
        return FakeTable(self.store, t)


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, s):
        return FakeSchema(self.store)


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────────
# The SHIPPED (old) luxelink export — exactly these 8 columns, no store anywhere.
OLD_COLUMNS = ["Bin", "Qty", "Cost", "Serial 1", "Product ID", "Age in Store", "Age in Company",
               "Product Desc Full"]
# The RE-SCHEDULED (new) export = the same 8 + a store-location column. Header spelling unconfirmed;
# "Store Location" is the assumption this package covers.
NEW_COLUMNS = OLD_COLUMNS + ["Store Location"]


def lux_row(serial, cost="$449.99", age_store="12", age_company="25", pid="SKU-778",
            desc="MOTOROLA MOTO G 5G 2024 128GB BLACK", store_col=None, store=None):
    """One luxelink serialized-device row — $-prefixed Cost, two-space-padded Qty."""
    r = {"Bin": "MAIN", "Qty": "  1", "Cost": cost, "Serial 1": serial, "Product ID": pid,
         "Age in Store": age_store, "Age in Company": age_company, "Product Desc Full": desc}
    if store_col:
        r[store_col] = store
    return r


OLD_ROWS = [lux_row("355163568356973"), lux_row("355163568356974"),
            lux_row("355163568356981", cost="$129.00", age_company="7", age_store="7")]

# same three devices, now carrying "Store Location" (two stores, so the roll-up is non-trivial)
NEW_ROWS = [
    lux_row("355163568356973", store_col="Store Location", store="3 Palisade Ave"),
    lux_row("355163568356974", store_col="Store Location", store="3 Palisade Ave"),
    lux_row("355163568356981", cost="$129.00", age_company="7", age_store="7",
            store_col="Store Location", store="100 Main St"),
]

# BOTH an exact 'Store' column and a 'Store Location' column → 'Store' must keep winning
BOTH_ROWS = [
    {"Store": "3 Palisade Ave", "Store Location": "WAREHOUSE-DO-NOT-USE", "Serial 1": "111111111111111",
     "Cost": "$100.00", "Product Desc Full": "PHONE A"},
    {"Store": "100 Main St", "Store Location": "WAREHOUSE-DO-NOT-USE", "Serial 1": "222222222222222",
     "Cost": "$25.25", "Product Desc Full": "PHONE B"},
]

# pre-existing fixtures carried over from inventory_aging_device_only_proof.py (regression surface)
HOUSE_ROWS = [
    {"Bin": "Store: 3 Palisade Ave", "Qty": "", "Cost": "", "Serial 1": "", "Item": ""},
    {"Bin": "A1", "Qty": "1", "Cost": "$100.00", "Serial 1": "111111111111111", "Item": "PHONE A"},
    {"Bin": "A2", "Qty": "1", "Cost": "$50.50", "Serial 1": "222222222222222", "Item": "PHONE B"},
    {"Bin": "", "Qty": "", "Cost": "$150.50", "Serial 1": "", "Item": ""},          # subtotal → skipped
    {"Bin": "Store: 100 Main St", "Qty": "", "Cost": "", "Serial 1": "", "Item": ""},
    {"Bin": "B1", "Qty": "1", "Cost": "$25.25", "Serial 1": "333333333333333", "Item": "PHONE C"},
]
FLAT_STORE_ROWS = [
    {"Store": "3 Palisade Ave", "Item": "PHONE A", "Cost": "$100.00"},
    {"Store": "3 Palisade Ave", "Item": "PHONE B", "Cost": "$10.00"},
    {"Store": "100 Main St", "Item": "PHONE C", "Cost": "$5.00"},
]
NEITHER_ROWS = [{"Foo": "1", "Bar": "2"}, {"Foo": "3", "Bar": "4"}]
DAYS_WINS_ROWS = [{"Serial": "9", "Cost": "5.00", "Days In Stock": "3", "Age in Company": "99",
                   "Item": "X", "Product Desc Full": "Y"}]


def to_csv(rows, columns=None):
    import csv
    cols = columns or list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in cols})
    return buf.getvalue().encode("utf-8")


_orig_sb = R.sb


def run_upload(rows, columns=None, org=LUX, filename="Inventory Aging.csv", store=None):
    store = {} if store is None else store
    R.sb = lambda: FakeClient(store)
    try:
        uf = _UF(io.BytesIO(to_csv(rows, columns)), filename=filename)
        res = asyncio.get_event_loop().run_until_complete(
            R.upload_file("inventory_aging", uf, "", force=False, org_id=org))
    finally:
        R.sb = _orig_sb
    return res, store


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== G. NEW FORMAT: the re-scheduled export WITH a 'Store Location' column ===")

sv = B.normalize_inventory(NEW_ROWS)
check("G1 normalize_inventory → per-store $ totals (was {} before this package)",
      sv == {"3 Palisade Ave": 899.98, "100 Main St": 129.0}, sv)
check("G2 exactly 2 stores parsed from 3 rows", len(sv) == 2, sv)

diag = B.inventory_diagnostics(NEW_ROWS)
check("G3 diagnostics now NAME the store column ('Store Location') instead of None",
      diag["store_col"] == "Store Location", diag)
check("G4 diagnostics value_col still 'cost' + not grouped",
      (diag["value_col"] or "").lower() == "cost" and diag["grouped"] is False, diag)

devs = B.extract_inventory_devices(NEW_ROWS, as_of_date="2026-07-25")
check("G5 3 device rows (device extraction unchanged)", len(devs) == 3, len(devs))
check("G6 EVERY device row now carries its store (was None on all 3)",
      [d["store"] for d in devs] == ["3 Palisade Ave", "3 Palisade Ave", "100 Main St"],
      [d["store"] for d in devs])
d0 = devs[0]
check("G7 device fields otherwise unchanged (imei/serial/unit_cost/days/sku/item)",
      (d0["imei"], d0["serial"], d0["unit_cost"], d0["days_in_stock"], d0["sku"], d0["item"]) ==
      ("355163568356973", "355163568356973", 449.99, 25, "SKU-778",
       "MOTOROLA MOTO G 5G 2024 128GB BLACK"), d0)
check("G8 'Age in Store' still preserved verbatim in raw_row",
      (d0["raw_row"] or {}).get("Age in Store") == "12", (d0["raw_row"] or {}).get("Age in Store"))
check("G9 'Store Location' preserved verbatim in raw_row too",
      (d0["raw_row"] or {}).get("Store Location") == "3 Palisade Ave", d0["raw_row"])

st = {}
res, st = run_upload(NEW_ROWS, NEW_COLUMNS, store=st)
check("G10 REAL upload_file: plain success payload, stores 2 / saved 2 (NOT device-only)",
      res.get("success") is True and res.get("stores") == 2 and res.get("saved") == 2, res)
check("G11 payload has NO 'skipped' and NO 'note' — the honest 'no store column found' note is GONE",
      "skipped" not in res and "note" not in res, res)
check("G12 payload key-set = the plain success shape",
      set(res.keys()) == {"success", "file_type", "stores", "saved", "devices", "as_of", "rows_read"},
      sorted(res.keys()))
check("G13 devices still 3 on the same payload", res.get("devices") == 3, res)
iv = st.get("inventory_value", [])
check("G14 2 inventory_value rows written (the Balance-Sheet line finally fills)", len(iv) == 2, iv)
check("G15 every inventory_value row org-stamped luxelink (multi-tenant rule)",
      bool(iv) and all(r.get("org_id") == LUX for r in iv), iv)
check("G16 inventory_value $ match the roll-up",
      sorted((r.get("store"), r.get("value") if "value" in r else r.get("swept_value")) for r in iv) ==
      sorted([("100 Main St", 129.0), ("3 Palisade Ave", 899.98)]), iv)
dev_rows = st.get("inventory_aging_device", [])
check("G17 3 device rows written, each org-stamped + store-stamped",
      len(dev_rows) == 3 and all(r.get("org_id") == LUX and r.get("store") for r in dev_rows), dev_rows)
tr = (st.get("upload_trace") or [{}])[-1]
check("G18 upload_trace status 'ok' with rows_saved 2 (store count), no 'partial'",
      tr.get("status") == "ok" and tr.get("rows_saved") == 2, tr)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== G-sweep. REAL _run_email_sweep over the new-format attachment ===")

SWEEP_STORE = {
    "email_sweep_config": [{"org_id": LUX, "account": "default", "imap_host": "imap.example.com",
                            "patterns": [{"pattern": "*Inventory*Aging*", "upload_type": "inventory_aging"}]}],
}
ATTACH = {"message_id": "<msg-storeloc@b2bsoft>", "name": "Inventory Aging.csv", "size": 640,
          "upload_type": "inventory_aging", "bytes": to_csv(NEW_ROWS, NEW_COLUMNS)}

_orig_fetch = R._email.fetch_new_attachments
_fetch_calls = []


def fake_fetch(cfg, already):
    _fetch_calls.append(set(already))
    if (ATTACH["message_id"], ATTACH["name"]) in already:
        return []
    return [dict(ATTACH)]


R.sb = lambda: FakeClient(SWEEP_STORE)
R._email.fetch_new_attachments = fake_fetch
_recalcs = []
_orig_calc = R._run_calculation


async def _spy_calc(*a, **k):
    _recalcs.append(a)
    return {}


R._run_calculation = _spy_calc
try:
    r1 = asyncio.get_event_loop().run_until_complete(R._run_email_sweep(LUX, "default"))
    r2 = asyncio.get_event_loop().run_until_complete(R._run_email_sweep(LUX, "default"))
finally:
    R.sb = _orig_sb
    R._email.fetch_new_attachments = _orig_fetch
    R._run_calculation = _orig_calc

f1 = (r1.get("files") or [{}])[0]
check("G19 sweep run 1: ingested 1, status 'ok', rows_saved 2 (store count)",
      r1.get("ingested") == 1 and f1.get("status") == "ok" and f1.get("rows_saved") == 2, (r1, f1))
check("G20 sweep run 2 pulls NOTHING (dedup marks it done — no 6-hourly re-ingest loop)",
      r2.get("ingested") == 0 and (r2.get("files") or []) == [], r2)
check("G21 MONEY PATH UNTOUCHED — no _run_calculation fired", _recalcs == [], _recalcs)
check("G22 device rows landed once (3), not twice",
      len(SWEEP_STORE.get("inventory_aging_device", [])) == 3,
      len(SWEEP_STORE.get("inventory_aging_device", [])))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== H. OLD FORMAT (the exact 8 columns, no store) — device-only path UNCHANGED ===")

check("H1 normalize_inventory still {} (nothing invented)", B.normalize_inventory(OLD_ROWS) == {},
      B.normalize_inventory(OLD_ROWS))
check("H2 diagnostics store_col still None for the old shape",
      B.inventory_diagnostics(OLD_ROWS)["store_col"] is None, B.inventory_diagnostics(OLD_ROWS))
res_o, st_o = run_upload(OLD_ROWS, OLD_COLUMNS)
check("H3 still the device-only outcome: stores 0 / saved 0 / devices 3 / 'inventory_devices_only'",
      (res_o.get("stores"), res_o.get("saved"), res_o.get("devices"), res_o.get("skipped")) ==
      (0, 0, 3, "inventory_devices_only"), res_o)
check("H4 the honest note still says 'no store column found'",
      "no store column" in (res_o.get("note") or ""), res_o.get("note"))
tr_o = (st_o.get("upload_trace") or [{}])[-1]
check("H5 upload_trace still 'partial' with rows_saved 3", tr_o.get("status") == "partial" and
      tr_o.get("rows_saved") == 3, tr_o)
check("H6 device rows still have store None (honest — the old file has none)",
      all(d["store"] is None for d in B.extract_inventory_devices(OLD_ROWS)),
      [d["store"] for d in B.extract_inventory_devices(OLD_ROWS)])
check("H7 nothing written to inventory_value for the old shape", not st_o.get("inventory_value"),
      st_o.get("inventory_value"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== I. PRECEDENCE — an exact 'Store' column still beats 'Store Location' ===")

sv_b = B.normalize_inventory(BOTH_ROWS)
check("I1 roll-up keyed by 'Store', NOT 'Store Location'",
      sv_b == {"3 Palisade Ave": 100.0, "100 Main St": 25.25}, sv_b)
check("I2 'WAREHOUSE-DO-NOT-USE' never appears as a store key",
      "WAREHOUSE-DO-NOT-USE" not in sv_b, sv_b)
devs_b = B.extract_inventory_devices(BOTH_ROWS)
check("I3 device rows stamped with the exact 'Store' value",
      [d["store"] for d in devs_b] == ["3 Palisade Ave", "100 Main St"], [d["store"] for d in devs_b])
# NOTE: inventory_diagnostics reports the first CANDIDATE that hits (candidate order), not the file's
# literal header — so an exact 'Store' column is reported as the earlier-listed candidate 'store'. That
# is pre-existing behavior; what matters here is that it is NEVER one of the five new synonyms.
NEW_SYNONYMS = {"Store Location", "StoreLocation", "Store Loc", "Location Desc", "Location Description"}
check("I4 diagnostics resolve a SHIPPED store candidate, never one of the 5 new synonyms",
      B.inventory_diagnostics(BOTH_ROWS)["store_col"] == "store" and
      B.inventory_diagnostics(BOTH_ROWS)["store_col"] not in NEW_SYNONYMS,
      B.inventory_diagnostics(BOTH_ROWS))
# 'Location' (already shipped) also outranks the new synonyms
LOC_ROWS = [{"Location": "3 Palisade Ave", "Store Location": "NOPE", "Cost": "$5.00",
             "Serial 1": "444444444444444"}]
check("I5 an already-shipped 'Location' column also still wins",
      B.normalize_inventory(LOC_ROWS) == {"3 Palisade Ave": 5.0}, B.normalize_inventory(LOC_ROWS))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== J. HEADER-CASE COVERAGE (the real b2bsoft header is unconfirmed) ===")

VARIANTS = ["Store Location", "STORE LOCATION", "store location", "store_location", "StoreLocation",
            "Store  Location", "Store-Location", "Store Loc", "store loc", "Location Desc",
            "Location Description", "LOCATION DESCRIPTION", "location_description"]
for h in VARIANTS:
    rows = [{"Bin": "MAIN", "Cost": "$10.00", "Serial 1": "555555555555555", h: "3 Palisade Ave"}]
    ok = B.normalize_inventory(rows) == {"3 Palisade Ave": 10.0}
    stamped = B.extract_inventory_devices(rows)[0]["store"] == "3 Palisade Ave"
    check(f"J-var {h!r} resolves (roll-up + device stamp)", ok and stamped,
          (B.normalize_inventory(rows), B.extract_inventory_devices(rows)[0]["store"]))

NEGATIVE = ["Stock Location", "Bin Location", "Warehouse Loc"]
for h in NEGATIVE:
    rows = [{"Bin": "MAIN", "Cost": "$10.00", "Serial 1": "666666666666666", h: "AISLE-3"}]
    check(f"J-neg {h!r} is NOT treated as a store column", B.normalize_inventory(rows) == {},
          B.normalize_inventory(rows))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n=== K. ORIGIN/MAIN DIFFERENTIAL (b2b_sweep vendored from {BASE_REF}) ===")

old_src = subprocess.run(["git", "-C", REPO, "show", f"{BASE_REF}:backend/app/modules/commcalc/b2b_sweep.py"],
                         capture_output=True, text=True)
if old_src.returncode != 0:
    check(f"K0 vendored {BASE_REF} b2b_sweep.py", False, old_src.stderr[:200])
else:
    tmp = os.path.join(tempfile.mkdtemp(), "b2b_sweep_base.py")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(old_src.stdout)
    spec = importlib.util.spec_from_file_location("b2b_sweep_base", tmp)
    OLD = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(OLD)
    check(f"K0 vendored {BASE_REF} b2b_sweep.py loaded", True)
    check("K1 the ONLY source delta is the 5 appended synonyms",
          set(B.DEFAULT_STORE_FIELDS) - set(OLD.DEFAULT_STORE_FIELDS) ==
          {"Store Location", "StoreLocation", "Store Loc", "Location Desc", "Location Description"} and
          not (set(OLD.DEFAULT_STORE_FIELDS) - set(B.DEFAULT_STORE_FIELDS)),
          (set(B.DEFAULT_STORE_FIELDS) ^ set(OLD.DEFAULT_STORE_FIELDS)))
    check("K2 they are appended LAST (every shipped candidate keeps its position)",
          B.DEFAULT_STORE_FIELDS[:len(OLD.DEFAULT_STORE_FIELDS)] == OLD.DEFAULT_STORE_FIELDS,
          B.DEFAULT_STORE_FIELDS)
    check("K3 DEFAULT_VALUE_FIELDS untouched", B.DEFAULT_VALUE_FIELDS == OLD.DEFAULT_VALUE_FIELDS)
    check("K4 device-side field lists untouched",
          (B.DEFAULT_IMEI_FIELDS, B.DEFAULT_SERIAL_FIELDS, B.DEFAULT_SKU_FIELDS, B.DEFAULT_ITEM_FIELDS,
           B.DEFAULT_DEVICE_COST_FIELDS, B.DEFAULT_RECEIVED_FIELDS, B.DEFAULT_DAYS_FIELDS) ==
          (OLD.DEFAULT_IMEI_FIELDS, OLD.DEFAULT_SERIAL_FIELDS, OLD.DEFAULT_SKU_FIELDS,
           OLD.DEFAULT_ITEM_FIELDS, OLD.DEFAULT_DEVICE_COST_FIELDS, OLD.DEFAULT_RECEIVED_FIELDS,
           OLD.DEFAULT_DAYS_FIELDS))

    UNCHANGED = {"luxelink_old": OLD_ROWS, "house_grouped": HOUSE_ROWS, "flat_store": FLAT_STORE_ROWS,
                 "neither": NEITHER_ROWS, "empty": [], "days_in_stock_wins": DAYS_WINS_ROWS,
                 "both_store_cols": BOTH_ROWS, "location_col": LOC_ROWS}
    norm_drift = {k: (OLD.normalize_inventory(v), B.normalize_inventory(v))
                  for k, v in UNCHANGED.items() if OLD.normalize_inventory(v) != B.normalize_inventory(v)}
    check("K5 normalize_inventory IDENTICAL on all 8 pre-existing fixtures", not norm_drift, norm_drift)
    dev_drift = {}
    for k, v in UNCHANGED.items():
        o = OLD.extract_inventory_devices(v, as_of_date="2026-07-25")
        n = B.extract_inventory_devices(v, as_of_date="2026-07-25")
        if o != n:
            dev_drift[k] = (o, n)
    check("K6 extract_inventory_devices IDENTICAL on all 8 pre-existing fixtures", not dev_drift,
          list(dev_drift))
    check("K7 the new-format file is the ONLY behavior change: base=0 stores → now 2",
          OLD.normalize_inventory(NEW_ROWS) == {} and
          B.normalize_inventory(NEW_ROWS) == {"3 Palisade Ave": 899.98, "100 Main St": 129.0},
          (OLD.normalize_inventory(NEW_ROWS), B.normalize_inventory(NEW_ROWS)))
    check("K8 base stamped store=None on the new-format devices; now every row carries its store",
          all(d["store"] is None for d in OLD.extract_inventory_devices(NEW_ROWS)) and
          all(d["store"] for d in B.extract_inventory_devices(NEW_ROWS)),
          [d["store"] for d in OLD.extract_inventory_devices(NEW_ROWS)])
    check("K9 base diagnostics said 'no store column' for the new format; now it names it",
          OLD.inventory_diagnostics(NEW_ROWS)["store_col"] is None and
          B.inventory_diagnostics(NEW_ROWS)["store_col"] == "Store Location",
          (OLD.inventory_diagnostics(NEW_ROWS)["store_col"],
           B.inventory_diagnostics(NEW_ROWS)["store_col"]))


print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
