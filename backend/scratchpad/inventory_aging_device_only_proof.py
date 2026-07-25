"""Proof harness for agent/commission/invaging-device-only (2026-07-25).

Context: luxelink's daily "Inventory Aging.csv" is a SERIALIZED-DEVICE export whose columns are exactly
    Bin · Qty · Cost ("$449.99") · Serial 1 · Product ID · Age in Store · Age in Company · Product Desc Full
— there is NO store column. So normalize_inventory() correctly yields {} (0 stores) while
extract_inventory_devices() DOES save per-device rows. Before this package that combination returned the
plain success payload with saved=0, which the email sweep recorded as a silent "ok · 0 rows · (no detail)"
AND — because the sweep dedup only marks a message done when rows_saved > 0 — re-pulled + re-ingested the
same attachment on every hourly sweep (15x on 2026-07-24).

Proves, with NO DB and NO network (in-memory FakeClient + a fake IMAP fetcher):
  A. LUXELINK SHAPE  — the exact 8 columns, $-prefixed Cost, two-space-padded Qty:
     normalize_inventory → {}, extract_inventory_devices → imei from 'Serial 1', unit_cost 449.99,
     days_in_stock 25 (Age in Company, NOT Age in Store), sku from Product ID, item from Product Desc Full,
     store None, and 'Age in Store' still preserved verbatim in raw_row.
  B. NEW RETURN SHAPE — the REAL upload_file(file_type='inventory_aging') over a FakeClient returns
     success/stores 0/saved 0/devices N/skipped 'inventory_devices_only'/note, writes N device rows, and
     records an upload_trace row with status 'partial' + rows_saved N (not 'skipped · 0').
  C. SWEEP HONESTY + DEDUP — the REAL _run_email_sweep records status 'ok', rows_saved N, detail=note; a
     SECOND sweep of the same message now pulls nothing (the loop is broken). Money path untouched
     (no daily_sales ⇒ no promote/recalc).
  D. HOUSE GROUPED FILE — a grouped 'Store: <addr>' file still parses stores unchanged and returns the
     OLD payload byte-for-byte (no 'skipped'/'note' keys added).
  E. BOTH ZERO — a file with neither stores nor devices still returns 'inventory_no_stores' unchanged.
  F. ORIGIN/MAIN DIFFERENTIAL — the pre-change b2b_sweep (git show a11b2ff) vs this one over 6 fixtures:
     normalize_inventory identical everywhere; extract_inventory_devices identical except the two
     intended additive fields (days_in_stock from 'Age in Company', item from 'Product Desc Full') on the
     files that actually carry those columns.

Run:  cd backend && python3 scratchpad/inventory_aging_device_only_proof.py
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


# ── in-memory Supabase double ────────────────────────────────────────────────────────────────────
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
LUX_COLUMNS = ["Bin", "Qty", "Cost", "Serial 1", "Product ID", "Age in Store", "Age in Company",
               "Product Desc Full"]


def lux_row(serial, cost="$449.99", age_store="12", age_company="25", pid="SKU-778",
            desc="MOTOROLA MOTO G 5G 2024 128GB BLACK"):
    """One luxelink serialized-device row — $-prefixed Cost, two-space-padded Qty, NO store column."""
    return {"Bin": "MAIN", "Qty": "  1", "Cost": cost, "Serial 1": serial, "Product ID": pid,
            "Age in Store": age_store, "Age in Company": age_company, "Product Desc Full": desc}


LUX_ROWS = [lux_row("355163568356973"), lux_row("355163568356974"), lux_row("355163568356981",
                                                                           cost="$129.00",
                                                                           age_company="7",
                                                                           age_store="7")]

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


def to_csv(rows, columns=None):
    import csv
    cols = columns or list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in cols})
    return buf.getvalue().encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== A. LUXELINK SHAPE (the exact 8-column serialized-device export) ===")

sv = B.normalize_inventory(LUX_ROWS)
check("A1 normalize_inventory → {} (no store column, nothing invented)", sv == {}, sv)

diag = B.inventory_diagnostics(LUX_ROWS)
check("A2 diagnostics: store_col None, value_col matched ('cost'), not grouped",
      diag["store_col"] is None and (diag["value_col"] or "").lower() == "cost"
      and diag["grouped"] is False, diag)

devs = B.extract_inventory_devices(LUX_ROWS, as_of_date="2026-07-25")
check("A3 extract_inventory_devices → 3 device rows", len(devs) == 3, len(devs))
d0 = devs[0] if devs else {}
check("A4 imei = Serial 1 ('355163568356973')", d0.get("imei") == "355163568356973", d0.get("imei"))
check("A5 serial preserved", d0.get("serial") == "355163568356973", d0.get("serial"))
check("A6 unit_cost = 449.99 from '$449.99'", d0.get("unit_cost") == 449.99, d0.get("unit_cost"))
check("A7 days_in_stock = 25 (Age in Company, the TOTAL company age)", d0.get("days_in_stock") == 25,
      d0.get("days_in_stock"))
check("A8 days_in_stock is NOT 12 (Age in Store deliberately unmapped)", d0.get("days_in_stock") != 12,
      d0.get("days_in_stock"))
check("A9 sku = Product ID", d0.get("sku") == "SKU-778", d0.get("sku"))
check("A10 item = Product Desc Full", d0.get("item") == "MOTOROLA MOTO G 5G 2024 128GB BLACK",
      d0.get("item"))
check("A11 store None (honest — the file has none)", d0.get("store") is None, d0.get("store"))
check("A12 'Age in Store' still preserved verbatim in raw_row",
      (d0.get("raw_row") or {}).get("Age in Store") == "12", (d0.get("raw_row") or {}).get("Age in Store"))
check("A13 as_of_date carried", d0.get("as_of_date") == "2026-07-25", d0.get("as_of_date"))
check("A14 second/third rows parse too (cost 129.0, days 7)",
      devs[2]["unit_cost"] == 129.0 and devs[2]["days_in_stock"] == 7, devs[2] if len(devs) > 2 else None)
ddiag = B.device_diagnostics(LUX_ROWS)
check("A15 device diagnostics name the matched columns (serial + cost)",
      ddiag["serial_col"] == "Serial 1" and ddiag["cost_col"] == "Cost", ddiag)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== B. NEW DEVICE-ONLY RETURN SHAPE (real upload_file over a FakeClient) ===")

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


st = {}
res, st = run_upload(LUX_ROWS, LUX_COLUMNS, store=st)
check("B1 success True (it IS a real ingest)", res.get("success") is True, res)
check("B2 stores 0 / saved 0 (store-count semantics UNCHANGED)",
      res.get("stores") == 0 and res.get("saved") == 0, res)
check("B3 devices = 3", res.get("devices") == 3, res)
check("B4 skipped marker = 'inventory_devices_only'", res.get("skipped") == "inventory_devices_only", res)
check("B5 note names both halves (0 stores + N device rows)",
      "0 stores" in (res.get("note") or "") and "3 device row(s) saved" in (res.get("note") or ""),
      res.get("note"))
check("B6 note explains WHY (no store column / per-device export)",
      "no store column" in (res.get("note") or ""), res.get("note"))
check("B7 3 rows really written to inventory_aging_device",
      len(st.get("inventory_aging_device", [])) == 3, len(st.get("inventory_aging_device", [])))
check("B8 every device row org-stamped luxelink (multi-tenant rule)",
      all(r.get("org_id") == LUX for r in st.get("inventory_aging_device", [])), st.get("inventory_aging_device"))
check("B9 NOTHING written to inventory_value (no store → no Balance-Sheet value invented)",
      not st.get("inventory_value"), st.get("inventory_value"))
tr = (st.get("upload_trace") or [{}])[-1]
check("B10 upload_trace status 'partial' (a real ingest with a caveat, not 'skipped')",
      tr.get("status") == "partial", tr)
check("B11 upload_trace rows_saved = 3 (device count, not 0)", tr.get("rows_saved") == 3, tr)
check("B12 upload_trace skipped marker carried + org = luxelink",
      tr.get("skipped") == "inventory_devices_only" and tr.get("org_id") == LUX, tr)
check("B13 upload_trace note carried for the operator", bool(tr.get("note")), tr)

# re-ingest of the SAME file is idempotent (upsert on org_id,imei) — no duplicate device rows
res2, st = run_upload(LUX_ROWS, LUX_COLUMNS, store=st)
check("B14 re-ingest is idempotent (still 3 device rows, upsert on org_id,imei)",
      len(st.get("inventory_aging_device", [])) == 3, len(st.get("inventory_aging_device", [])))

# tenant isolation: the SAME serials ingested by the house org create SEPARATE rows
res3, st = run_upload(LUX_ROWS, LUX_COLUMNS, org=HOUSE, store=st)
check("B15 same serials under another org → separate rows (org isolation)",
      len(st.get("inventory_aging_device", [])) == 6 and
      len({r["org_id"] for r in st["inventory_aging_device"]}) == 2,
      len(st.get("inventory_aging_device", [])))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== C. EMAIL SWEEP: honest history row + the dedup loop is broken ===")

SWEEP_STORE = {
    "email_sweep_config": [{"org_id": LUX, "account": "default", "imap_host": "imap.example.com",
                            "patterns": [{"pattern": "*Inventory*Aging*", "upload_type": "inventory_aging"}]}],
}
ATTACH = {"message_id": "<msg-1@b2bsoft>", "name": "Inventory Aging.csv", "size": 512,
          "upload_type": "inventory_aging", "bytes": to_csv(LUX_ROWS, LUX_COLUMNS)}

_orig_fetch = R._email.fetch_new_attachments
_fetch_calls = []


def fake_fetch(cfg, already):
    # mirrors the real fetcher's contract: skip (message_id, filename) pairs already marked done
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
    status_after_run1 = SWEEP_STORE["email_sweep_config"][0].get("last_status")
    r2 = asyncio.get_event_loop().run_until_complete(R._run_email_sweep(LUX, "default"))
    status_after_run2 = SWEEP_STORE["email_sweep_config"][0].get("last_status")
finally:
    R.sb = _orig_sb
    R._email.fetch_new_attachments = _orig_fetch
    R._run_calculation = _orig_calc

f1 = (r1.get("files") or [{}])[0]
check("C1 run 1 ingested = 1", r1.get("ingested") == 1, r1)
check("C2 run 1 status 'ok' (a real ingest)", f1.get("status") == "ok", f1)
check("C3 run 1 rows_saved = 3 (device count, was 0)", f1.get("rows_saved") == 3, f1)
check("C4 run 1 detail carries the honest note (was NULL)",
      "device row(s) saved" in (f1.get("detail") or ""), f1.get("detail"))
proc = SWEEP_STORE.get("email_processed") or []
check("C5 exactly ONE email_processed row, org-stamped luxelink",
      len(proc) == 1 and proc[0].get("org_id") == LUX, proc)
check("C6 the recorded row is ok/3/detail (the dedup key the next sweep reads)",
      proc[0].get("status") == "ok" and proc[0].get("rows_saved") == 3 and bool(proc[0].get("detail")),
      proc[0])
check("C7 run 2 pulls NOTHING — the hourly re-ingest loop is broken",
      r2.get("ingested") == 0 and (r2.get("files") or []) == [], r2)
check("C8 run 2's dedup set contained our (message_id, filename)",
      (ATTACH["message_id"], ATTACH["name"]) in (_fetch_calls[1] if len(_fetch_calls) > 1 else set()),
      _fetch_calls)
check("C9 run 1's dedup set was EMPTY (proving C7 comes from the new row, not a stale set)",
      _fetch_calls and _fetch_calls[0] == set(), _fetch_calls[:1])
check("C10 device rows landed once (3), not twice",
      len(SWEEP_STORE.get("inventory_aging_device", [])) == 3,
      len(SWEEP_STORE.get("inventory_aging_device", [])))
check("C11 MONEY PATH UNTOUCHED — no _run_calculation fired (inventory_aging is not daily_sales)",
      _recalcs == [], _recalcs)
check("C12 mailbox status after run 1 = '1/1 attachments ingested'",
      "1/1" in (status_after_run1 or ""), status_after_run1)
check("C13 mailbox status after run 2 = 'no new attachments' (the loop really stopped)",
      "no new attachments" in (status_after_run2 or ""), status_after_run2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== D. HOUSE GROUPED FILE — stores parse unchanged, OLD payload byte-identical ===")

sv_h = B.normalize_inventory(HOUSE_ROWS)
check("D1 grouped file → 2 stores with the right totals",
      sv_h == {"3 Palisade Ave": 150.5, "100 Main St": 25.25}, sv_h)
res_h, st_h = run_upload(HOUSE_ROWS, list(HOUSE_ROWS[0].keys()), org=HOUSE)
check("D2 house payload: success/stores 2/saved 2", res_h.get("success") is True and
      res_h.get("stores") == 2 and res_h.get("saved") == 2, res_h)
check("D3 house payload has NO 'skipped' and NO 'note' (byte-identical shape)",
      "skipped" not in res_h and "note" not in res_h, res_h)
check("D4 house payload keys unchanged",
      set(res_h.keys()) == {"success", "file_type", "stores", "saved", "devices", "as_of", "rows_read"},
      sorted(res_h.keys()))
check("D5 inventory_value written for both stores",
      len(st_h.get("inventory_value", [])) == 2, st_h.get("inventory_value"))
check("D6 grouped device rows still carry their store (filled down from the header)",
      sorted((d.get("store") or "") for d in B.extract_inventory_devices(HOUSE_ROWS)) ==
      ["100 Main St", "3 Palisade Ave", "3 Palisade Ave"],
      [d.get("store") for d in B.extract_inventory_devices(HOUSE_ROWS)])
tr_h = (st_h.get("upload_trace") or [{}])[-1]
check("D7 house upload_trace still status 'ok' with rows_saved 2",
      tr_h.get("status") == "ok" and tr_h.get("rows_saved") == 2, tr_h)

# FLAT per-store file (no devices at all) — the other stores>0 shape
res_f, st_f = run_upload(FLAT_STORE_ROWS, org=HOUSE)
check("D8 flat store file: 2 stores, 0 devices, no skipped/note",
      res_f.get("stores") == 2 and res_f.get("devices") == 0 and "skipped" not in res_f, res_f)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== E. BOTH ZERO — 'inventory_no_stores' unchanged ===")

res_z, st_z = run_upload(NEITHER_ROWS, org=LUX)
check("E1 success False + skipped 'inventory_no_stores'",
      res_z.get("success") is False and res_z.get("skipped") == "inventory_no_stores", res_z)
check("E2 stores 0, saved 0, devices 0", (res_z.get("stores"), res_z.get("saved"), res_z.get("devices")) == (0, 0, 0),
      res_z)
check("E3 note still lists the columns actually found", "Foo" in (res_z.get("note") or ""), res_z.get("note"))
tr_z = (st_z.get("upload_trace") or [{}])[-1]
check("E4 upload_trace still status 'skipped' for the honest-zero case", tr_z.get("status") == "skipped", tr_z)
check("E5 nothing written for the zero case",
      not st_z.get("inventory_value") and not st_z.get("inventory_aging_device"), st_z)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== F. ORIGIN/MAIN DIFFERENTIAL (pre-change b2b_sweep vendored from a11b2ff) ===")

old_src = subprocess.run(["git", "-C", REPO, "show", "a11b2ff:backend/app/modules/commcalc/b2b_sweep.py"],
                         capture_output=True, text=True)
if old_src.returncode != 0:
    check("F0 vendored origin/main b2b_sweep.py", False, old_src.stderr[:200])
else:
    tmp = os.path.join(tempfile.mkdtemp(), "b2b_sweep_main.py")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(old_src.stdout)
    spec = importlib.util.spec_from_file_location("b2b_sweep_main", tmp)
    OLD = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(OLD)
    check("F0 vendored origin/main b2b_sweep.py loaded", True)

    FIXTURES = {"luxelink": LUX_ROWS, "house_grouped": HOUSE_ROWS, "flat_store": FLAT_STORE_ROWS,
                "neither": NEITHER_ROWS, "empty": [],
                "days_in_stock_wins": [{"Serial": "9", "Cost": "5.00", "Days In Stock": "3",
                                        "Age in Company": "99", "Item": "X", "Product Desc Full": "Y"}]}
    norm_same = all(OLD.normalize_inventory(v) == B.normalize_inventory(v) for v in FIXTURES.values())
    check("F1 normalize_inventory IDENTICAL on all 6 fixtures (store roll-up untouched)", norm_same)

    drift = {}
    for k, v in FIXTURES.items():
        o = OLD.extract_inventory_devices(v, as_of_date="2026-07-25")
        n = B.extract_inventory_devices(v, as_of_date="2026-07-25")
        if len(o) != len(n):
            drift[k] = f"row count {len(o)}->{len(n)}"
            continue
        for a, b in zip(o, n):
            for fld in set(a) | set(b):
                if a.get(fld) != b.get(fld):
                    drift.setdefault(k, set()).add(fld)
    check("F2 same device ROW COUNT everywhere (no row appears/disappears)",
          all(not isinstance(d, str) for d in drift.values()), drift)
    check("F3 only 'days_in_stock' + 'item' ever differ, and only on the luxelink fixture",
          set(drift) == {"luxelink"} and drift.get("luxelink") == {"days_in_stock", "item"}, drift)
    o_lux = OLD.extract_inventory_devices(LUX_ROWS)[0]
    check("F4 before: days_in_stock None + item None (the data this package recovers)",
          o_lux.get("days_in_stock") is None and o_lux.get("item") is None, o_lux)
    dw_o = OLD.extract_inventory_devices(FIXTURES["days_in_stock_wins"])[0]
    dw_n = B.extract_inventory_devices(FIXTURES["days_in_stock_wins"])[0]
    check("F5 a real 'Days In Stock' column still wins over 'Age in Company' (3, not 99)",
          dw_o["days_in_stock"] == dw_n["days_in_stock"] == 3, (dw_o["days_in_stock"], dw_n["days_in_stock"]))
    check("F6 a real 'Item' column still wins over 'Product Desc Full' ('X', not 'Y')",
          dw_o["item"] == dw_n["item"] == "X", (dw_o["item"], dw_n["item"]))


print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
