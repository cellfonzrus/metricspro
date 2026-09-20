"""DB-FREE PROOF — the tenant-onboarding intake, STAGE B: sales / POS / inventory / other reports on
the SAME spine as the commission statement, plus the shared store resolver (design §2, 2.0–2.6).

    python3 harness_onboarding_intake_b.py        (from backend/; no network, no DB)

THE SCENARIO (owner 2026-09-20: "they only can upload their existing data: Sales reports, Commission
reports, POS reports, Inventory reports — ask the user if we have uploaded any other report like bill
payments or credit card payments or x reports … Whatever is being uploaded should be able to save is
most important"). A tenant with two stores on its roster drops:

    · a POS product-sales export as the POS writes it: a two-line TITLE block, the header, invoice
      lines for THREE store strings — one spelled like the roster address, one already a store code,
      one the platform has never heard of — a grand-total FOOTER row, and a CONTINUATION sheet that
      repeats the header (the first sheet overflowed);
    · an on-hand inventory listing (csv) with a footer, 3 of whose units have no IMEI yet;
    · a "bill payments" report nobody has a table for.

    2.0 the checklist becomes instance rows; "no inventory export" is a recorded choice, never a blank
    2.2 both sheets are read, the header is found under the title block, the footer's value is shown
    2.3 every column proposed with provenance + samples
    2.4 the SHARED resolver (aliases → store_mapping → roster) knows two strings and not the third;
        the commit is REFUSED until the third is assigned / created / marked not-ours with a reason
    2.5 rows, distinct invoices, Σ amount, Σ GP, date span, refunds, per-store, per-rep — beside the footer
    2.6 commit → mapping saved + read back, the alias saved + read back through the resolver, rows
        landed through the EXISTING mapped importer (slice-scoped replace), RE-READ from the table:
        count and Σ equal what was shown. Re-commit replaces the slice, never doubles it.

NEGATIVE CONTROLS (§F): an unresolved store, a not-ours without a reason, a footer that disagrees, a
missing date column, a landing that drops rows (ok:false, never 'success'), a store-alias write that
does not stick. The 'other' report is RECORDED — headers, rows, Σ of its money columns, the kept file —
and reported as having no destination; it is never written into a table and never dropped.

Runs the REAL router functions over an in-memory fake of the supabase client (+ its storage), so the
endpoint's own save path — upsert_column_mapping, add_store_alias, _ingest_mapped_df,
b2b_sweep.write_inventory_devices, the re-reads — is exercised, not re-stated.
"""
import asyncio
import inspect
import io
import json
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import onboarding_intake as OI
from app.modules.commcalc import column_mapping as CM
from app.modules.commcalc import ingest_slice as IS
from app.modules.commcalc import router as R
from app.modules.storeops import router as SO

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MIG_DIR = os.path.join(ROOT, "database", "migrations")
FE_PAGE = os.path.join(ROOT, "frontend", "src", "app", "(platform)", "onboarding", "intake", "page.tsx")
FE_STAGE2 = os.path.join(ROOT, "frontend", "src", "app", "(platform)", "onboarding", "intake", "stage2.tsx")
FE_SHARED = os.path.join(ROOT, "frontend", "src", "app", "(platform)", "onboarding", "intake", "intake-shared.tsx")
ORG = "11111111-2222-3333-4444-555555555555"

_pass = _fail = 0
_failures = []


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        _failures.append(name)
        print(f"  FAIL  {name}{(' — ' + str(extra)) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


def money(x):
    return round(float(x or 0.0), 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURES — synthetic, in the real SHAPES (no customer data)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
STORE_ADDR = "100 Main St"          # spelled like the roster address of B-100
STORE_CODE = "B-200"                # already a store code
STORE_NEW = "Riverside Kiosk"       # unknown to the platform
SALES_HEADERS = ["Invoice #", "Invoiced At", "Sold By", "Sold On", "Product SKU", "Tracking #", "Product Name",
                 "Category", "Total Price", "Gross Profit", "Refund", "Quantity"]


def sales_lines():
    """(store, rep, day, sku, price, gp, refund) × 60 lines over 24 invoices, three stores, two reps;
    4 refund lines; every price has cents so nothing is an integer id."""
    out = []
    reps = ["Alice Rep", "bob.s"]
    stores = [STORE_ADDR, STORE_CODE, STORE_NEW]
    n = 0
    for inv in range(1, 25):
        st = stores[inv % 3]
        for line in range(1, (inv % 3) + 2):
            n += 1
            price = round(19.99 + n * 3.37, 2)
            refund = (n % 15 == 0)
            if refund:
                price = -price
            out.append({"inv": f"INV-{1000 + inv}", "store": st, "rep": reps[inv % 2],
                        "day": f"08/{1 + inv % 28:02d}/2026 14:{n % 60:02d}:05",
                        "sku": f"SKU-{n % 7}", "imei": f"35{n:013d}", "name": f"Item {n % 9}",
                        "cat": "Devices >> Phones >> Prepaid" if n % 2 else "Accessories >> Cases",
                        "price": price, "gp": round(price * 0.31, 2), "refund": "Yes" if refund else "No", "qty": 1})
    return out


LINES = sales_lines()
SALES_TOTAL = money(sum(l["price"] for l in LINES))
SALES_GP = money(sum(l["gp"] for l in LINES))
NEW_STORE_LINES = [l for l in LINES if l["store"] == STORE_NEW]
REFUNDS = sum(1 for l in LINES if l["refund"] == "Yes")
_days = sorted(f"2026-08-{int(l['day'][3:5]):02d}" for l in LINES)
SPAN_LO, SPAN_HI = _days[0], _days[-1]


def sales_xlsx(footer=True, continuation=True, footer_value=None, undated_rows=0, headers=None):
    """The POS export as a workbook: sheet 1 = title block + header + the first 40 lines (+ footer at
    its end when there is no continuation), sheet 2 = the same header + the rest + the footer."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Product Sales"
    ws.append(["Product Sales Report"])
    ws.append(["Date range", "08/01/2026 - 08/31/2026"])
    hdr = list(headers or SALES_HEADERS)
    ws.append(hdr)

    def row(l, i):
        day = "" if i < undated_rows else l["day"]
        return [l["inv"], l["store"], l["rep"], day, l["sku"], l["imei"], l["name"], l["cat"],
                f"{l['price']:.2f}", f"{l['gp']:.2f}", l["refund"], l["qty"]]
    split = 40 if continuation else len(LINES)
    for i, l in enumerate(LINES[:split]):
        ws.append(row(l, i))
    foot = ["", "", "", "", "", "", "", "", f"{(footer_value if footer_value is not None else SALES_TOTAL):.2f}", f"{SALES_GP:.2f}", "", ""]
    if continuation:
        ws2 = wb.create_sheet("Product Sales (2)")
        ws2.append(hdr)
        for i, l in enumerate(LINES[split:]):
            ws2.append(row(l, i + split))
        if footer:
            ws2.append(foot)
    elif footer:
        ws.append(foot)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


INV_HEADERS = ["Product SKU", "Tracking #", "Product Name", "Location", "Unit Cost", "Total Cost", "Quantity", "Status"]


def inventory_rows():
    out = []
    for i in range(1, 31):
        st = STORE_ADDR if i % 2 else STORE_CODE
        imei = "" if i % 10 == 0 else f"86{i:013d}"        # 3 ordered units without an IMEI yet
        cost = round(120.0 + i * 5.5, 2)
        out.append({"sku": f"SKU-{i % 7}", "imei": imei, "name": f"Item {i % 9}", "store": st,
                    "unit_cost": cost, "total_cost": cost, "qty": 1, "status": "Ordered" if not imei else "In stock"})
    return out


INV = inventory_rows()
INV_COST = money(sum(r["total_cost"] for r in INV))
INV_KEYED = [r for r in INV if r["imei"]]


def inventory_csv(footer=True):
    lines = ["Inventory Listing", ",".join(INV_HEADERS)]
    for r in INV:
        lines.append(f"{r['sku']},{r['imei']},{r['name']},{r['store']},{r['unit_cost']:.2f},{r['total_cost']:.2f},{r['qty']},{r['status']}")
    if footer:
        lines.append(f",,,,,{INV_COST:.2f},{len(INV)},")
    return ("\n".join(lines) + "\n").encode("utf-8")


def other_csv():
    lines = ["Bill Payments", "Date,Store,Account,Amount,Fee"]
    for i in range(1, 13):
        lines.append(f"2026-08-{i:02d},{STORE_ADDR},ACCT{i},{10.5 * i:.2f},{0.25 * i:.2f}")
    return ("\n".join(lines) + "\n").encode("utf-8")


OTHER_AMOUNT = money(sum(10.5 * i for i in range(1, 13)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# AN IN-MEMORY SUPABASE CLIENT (+ storage) — just enough of the query builder the intake path uses
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _Q:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.rows, self.filters, self.conflict = "select", None, [], None
        self._order, self._limit, self._range, self.cols = None, None, None, "*"
        self._count = None

    def select(self, cols="*", **kw):
        self.op, self.cols = "select", cols
        for c in [c.strip() for c in str(cols).split(",") if c.strip() and c.strip() != "*"]:
            if c not in self.db.columns(self.table):
                raise RuntimeError(f'42703 column "{c}" of {self.table} does not exist')
        return self

    def insert(self, rows):
        self.op, self.rows = "insert", (rows if isinstance(rows, list) else [rows])
        return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.rows, self.conflict = "upsert", (rows if isinstance(rows, list) else [rows]), on_conflict
        return self

    def update(self, row, count=None):
        self.op, self.rows, self._count = "update", [row], count
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, k, v):    self.filters.append(("eq", k, v)); return self
    def neq(self, k, v):   self.filters.append(("neq", k, v)); return self
    def in_(self, k, v):   self.filters.append(("in", k, list(v))); return self
    def is_(self, k, v):   self.filters.append(("is", k, v)); return self
    def gte(self, k, v):   self.filters.append(("gte", k, v)); return self
    def lte(self, k, v):   self.filters.append(("lte", k, v)); return self
    def lt(self, k, v):    self.filters.append(("lt", k, v)); return self
    def ilike(self, k, v): self.filters.append(("ilike", k, v)); return self
    def order(self, k, desc=False): self._order = (k, desc); return self
    def limit(self, n):    self._limit = n; return self
    def range(self, lo, hi): self._range = (lo, hi); return self

    def _match(self, r):
        for op, k, v in self.filters:
            x = r.get(k)
            if op == "eq" and x != v: return False
            if op == "neq" and x == v: return False
            if op == "in" and x not in v: return False
            if op == "is" and not ((v == "null" and x is None) or (v is not None and v != "null" and x == v)): return False
            if op == "gte" and not (x is not None and str(x) >= str(v)): return False
            if op == "lte" and not (x is not None and str(x) <= str(v)): return False
            if op == "lt" and not (x is not None and str(x) < str(v)): return False
            if op == "ilike" and not (x is not None and str(v).strip("%").lower() in str(x).lower()): return False
        return True

    def execute(self):
        t = self.db.tables.setdefault(self.table, [])
        if t is None:
            raise RuntimeError(f'42P01 relation "{self.table}" does not exist')
        if self.op == "select":
            out = [dict(r) for r in t if self._match(r)]
            if self._order:
                k, desc = self._order
                out.sort(key=lambda r: (r.get(k) is None, str(r.get(k))), reverse=desc)
            if self._range:
                lo, hi = self._range
                out = out[lo:hi + 1]
            if self._limit is not None:
                out = out[:self._limit]
            return _Res(out)
        if self.op == "insert":
            self.db.check_rows(self.table, self.rows)
            written = []
            for r in self.rows:
                row = dict(r)
                row.setdefault("id", str(uuid.uuid4()))
                if self.db.drop_inserts.get(self.table):
                    continue
                if self.db.drop_every_other.get(self.table) and len(written) % 2:
                    written.append(dict(row))
                    continue
                t.append(row)
                written.append(dict(row))
            return _Res(written)
        if self.op == "upsert":
            self.db.check_rows(self.table, self.rows, upsert=True)
            keys = [k.strip() for k in (self.conflict or "").split(",") if k.strip()]
            written = []
            for r in self.rows:
                row = dict(r)
                hit = next((x for x in t if keys and all(x.get(k) == row.get(k) for k in keys)
                            and all(row.get(k) is not None for k in keys)), None)
                if hit:
                    hit.update(row)
                    written.append(dict(hit))
                else:
                    row.setdefault("id", str(uuid.uuid4()))
                    if not self.db.drop_inserts.get(self.table):
                        t.append(row)
                    written.append(dict(row))
            return _Res(written)
        if self.op == "update":
            self.db.check_rows(self.table, self.rows)
            out = []
            for r in t:
                if self._match(r):
                    r.update(self.rows[0])
                    out.append(dict(r))
            return _Res(out, count=len(out) if self._count else None)
        if self.op == "delete":
            keep = [r for r in t if not self._match(r)]
            gone = len(t) - len(keep)
            t[:] = keep
            return _Res([{}] * gone)
        raise RuntimeError(self.op)


class _Res:
    def __init__(self, data, count=None): self.data, self.count = data, count


class FakeStorage:
    """The supabase storage surface the intake uses: buckets + upload/download by path."""
    def __init__(self, broken=False):
        self.buckets, self.files, self.broken = set(), {}, broken

    def get_bucket(self, name):
        if name not in self.buckets:
            raise RuntimeError("bucket not found")
        return {"name": name}

    def create_bucket(self, name):
        if self.broken:
            raise RuntimeError("storage unavailable")
        self.buckets.add(name)

    def from_(self, bucket):
        st = self

        class _B:
            def upload(self_, path, data, opts=None):
                if st.broken:
                    raise RuntimeError("storage unavailable")
                st.files[(bucket, path)] = bytes(data)
                return {"Key": path}

            def download(self_, path):
                if (bucket, path) not in st.files:
                    raise RuntimeError("object not found")
                return st.files[(bucket, path)]
        return _B()


class FakeDB:
    """Declared columns per table (a select of an undeclared column raises, like 42703); 'drop inserts'
    switches for the save-guarantee negative controls; an in-memory storage."""
    def __init__(self, storage_broken=False):
        self.tables = {}
        self.drop_inserts = {}
        self.drop_every_other = {}
        self.storage = FakeStorage(broken=storage_broken)
        self.declared = {
            "carrier": ["id", "org_id", "name", "code", "is_default"],
            "column_mapping": ["id", "org_id", "report_key", "carrier_id", "target_field", "source_header",
                               "transform", "is_active", "priority", "updated_at", "sign_convention"],
            "raw_sales": ["id", "org_id", "period", "period_month", "period_year", "store", "salesperson", "user_login",
                          "department", "category", "product_desc", "product_id", "gp", "ext_price", "trans_id",
                          "trans_date", "contract_type", "mdn", "serial_1", "register", "tender_type", "voided",
                          "trans_type", "sku", "customer", "email", "customer_no", "quantity", "total_cost",
                          "pricing_discounts", "contract_no", "created_at"],
            "inventory_aging_device": ["id", "org_id", "imei", "serial", "sku", "item", "store", "unit_cost",
                                       "received_date", "days_in_stock", "as_of_date", "source", "raw_row",
                                       "on_hand", "off_hand_as_of", "status", "quantity", "total_cost", "category",
                                       "updated_at", "created_at"],
            "store_mapping": ["id", "org_id", "store_code", "store_address", "market", "is_active"],
            "stores": ["id", "org_id", "store_code", "address", "market", "is_active", "entity_id", "timezone"],
            "store_aliases": ["id", "org_id", "alias", "store_code", "note", "source", "confidence"],
            "rep_aliases": ["id", "org_id", "alias", "canonical"],
            "employees": ["id", "org_id", "employee_id", "name", "home_store", "epay_salesperson", "is_active"],
            "onboarding_run": ["id", "org_id", "run_kind", "period", "status", "current_step", "started_by",
                               "started_at", "signed_off_by", "signed_off_on_behalf", "signed_off_at", "updated_at"],
            "onboarding_stage_state": ["id", "run_id", "org_id", "stage", "step", "instance_key", "status", "payload",
                                       "verified_numbers", "verified_by", "verified_at", "blocking_reason", "updated_at"],
        }

    def columns(self, table):
        if table in self.declared:
            return self.declared[table]
        return ["*"] + [k for r in (self.tables.get(table) or []) for k in r]  # undeclared: permissive

    def check_rows(self, table, rows, upsert=False):
        if table in self.declared:
            for r in rows:
                bad = [k for k in r if k not in self.declared[table]]
                if bad:
                    raise RuntimeError(f'42703 column "{bad[0]}" of {table} does not exist')
        if table == "raw_sales":
            for r in rows:
                if not r.get("period"):
                    raise RuntimeError('23502 null value in column "period" of raw_sales violates not-null constraint')
        if table == "inventory_aging_device" and not upsert:
            for r in rows:
                if r.get("imei") is not None and any(x.get("imei") == r.get("imei") and x.get("org_id") == r.get("org_id")
                                                     and x is not r for x in self.tables.get(table) or []):
                    raise RuntimeError('23505 duplicate key value violates unique constraint "inventory_aging_device_org_imei_uq"')

    def schema(self, _name): return self
    def table(self, name): return _Q(self, name)

    def seed(self, table, rows):
        for r in rows:
            row = dict(r); row.setdefault("id", str(uuid.uuid4()))
            self.tables.setdefault(table, []).append(row)


class FakeUpload:
    def __init__(self, data, filename="export.xlsx"):
        self.data, self.filename = data, filename

    async def read(self): return self.data


def fresh_db(with_state=True, storage_broken=False):
    db = FakeDB(storage_broken=storage_broken)
    db.seed("stores", [{"org_id": ORG, "store_code": "B-100", "address": STORE_ADDR, "market": "NY", "is_active": True},
                       {"org_id": ORG, "store_code": "B-200", "address": "200 Oak Ave", "market": "NY", "is_active": True}])
    db.seed("store_mapping", [{"org_id": ORG, "store_code": "B-100", "store_address": STORE_ADDR, "market": "NY"},
                              {"org_id": ORG, "store_code": "B-200", "store_address": "200 Oak Ave", "market": "NY"}])
    db.seed("employees", [{"org_id": ORG, "employee_id": "E1", "name": "Alice Rep", "epay_salesperson": "arep", "is_active": True},
                          {"org_id": ORG, "employee_id": "E2", "name": "Bob Smith", "epay_salesperson": "bob.s", "is_active": True}])
    db.seed("carrier", [{"id": "aaaaaaaa-0000-0000-0000-00000000c001", "org_id": ORG, "name": "Northwind Cellular", "code": "northwind"}])
    if not with_state:
        db.declared.pop("onboarding_stage_state"); db.declared.pop("onboarding_run")
        db.tables["onboarding_stage_state"] = None
        db.tables["onboarding_run"] = None
    return db


def use(db):
    R.sb = lambda: db
    SO.sb = lambda: db
    SO.get_supabase = lambda: db
    R._TABLE_COL_PRESENT.clear()
    return db


def run(coro):
    return asyncio.run(coro)


def analyze(db, data=None, filename="sales.xlsx", file=True, **form):
    use(db)
    kw = dict(source_kind="pos", pos_source="rq", layout="pos_product_sales", org_id=ORG)
    kw.update(form)
    up = FakeUpload(data if data is not None else sales_xlsx(), filename) if file else None
    return run(R.onboarding_intake_analyze(file=up, **kw))


def commit(db, data=None, filename="sales.xlsx", file=True, **form):
    use(db)
    kw = dict(source_kind="pos", pos_source="rq", layout="pos_product_sales", verified_by="tester", org_id=ORG)
    kw.update(form)
    up = FakeUpload(data if data is not None else sales_xlsx(), filename) if file else None
    return run(R.onboarding_intake_commit(file=up, **kw))


def http_error(fn, *a, **kw):
    try:
        return None, fn(*a, **kw)
    except R.HTTPException as e:
        return e.status_code, e.detail


SALES_MAP = {"trans_id": "Invoice #", "store": "Invoiced At", "salesperson": "Sold By", "trans_date": "Sold On",
             "sku": "Product SKU", "serial_1": "Tracking #", "product_desc": "Product Name", "category": "Category",
             "department": "Category", "ext_price": "Total Price", "gp": "Gross Profit", "voided": "Refund", "quantity": "Quantity"}
IDENTITY_OK = {"store": {STORE_NEW: {"action": "assign", "store_code": "B-100"}},
               "rep": {"bob.s": {"action": "assign", "canonical": "Bob Smith"}}}
INV_MAP = {"sku": "Product SKU", "imei": "Tracking #", "serial": "Tracking #", "item": "Product Name", "store": "Location",
           "unit_cost": "Unit Cost", "total_cost": "Total Cost", "quantity": "Quantity", "status": "Status"}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE VOCABULARY — every kind has a destination that already exists, or honestly none")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# PIN UPDATED 2026-09-20 (Stage C): the three TYPED 'other' kinds now have destinations — each an EXISTING
# table written by its own existing importer (index §23b X-report → pos_tender_summary mig 062, §12a merchant
# settlement → merchant_settlement_day mig 955, §4/§15 carrier bill-pay → the mig-939 processor feed table);
# free-text 'other' still has NONE. The Stage-B destinations are byte-identical.
check("sales and pos land in raw_sales, inventory in inventory_aging_device, commission in the ledger — free-text 'other' has NO destination",
      {k: OI.SOURCE_KIND_TARGET[k] for k in ("commission", "sales", "pos", "inventory")}
      == {"commission": "commission_ledger", "sales": "raw_sales", "pos": "raw_sales", "inventory": "inventory_aging_device"}
      and "other" not in OI.SOURCE_KIND_TARGET
      and OI.SOURCE_KIND_TARGET["x_report"] == "pos_tender_summary" and OI.SOURCE_KIND_TARGET["merchant_payments"] == "merchant_settlement_day"
      and OI.SOURCE_KIND_TARGET["bill_payments"] in OI.BILLPAY_FEED_TABLES)
_migs = "".join(open(os.path.join(MIG_DIR, f), encoding="utf-8").read() for f in os.listdir(MIG_DIR) if f.startswith(("062_", "955_", "903_", "083_")))
check("every destination is an EXISTING table — a column_mapping.TABLE_MAP target, or a table an existing migration creates (062 / 955 / 903 / 083); no sibling raw_* table",
      all(t in set(CM.TABLE_MAP.values()) or f"commcalc.{t}" in _migs for t in OI.SOURCE_KIND_TARGET.values())
      and all(t in set(CM.TABLE_MAP.values()) for t in OI.BILLPAY_FEED_TABLES))
check("the default layouts are registered report keys with those targets",
      all(CM.TABLE_MAP.get(OI.REPORT_KEY_BY_KIND[k]) == OI.SOURCE_KIND_TARGET[k] for k in OI.REPORT_KEY_BY_KIND))
lay = OI.layouts_for_kind("pos", CM.TABLE_MAP)
check("a kind offers every layout that lands in its table, its default first",
      [l["report_key"] for l in lay][:1] == ["pos_product_sales"] and {l["report_key"] for l in lay} == {"sales", "pos_product_sales"}
      and OI.layouts_for_kind("other", CM.TABLE_MAP) == [])
check("stage-2 instance keys: kind:source:layout; a second POS is a second instance",
      OI.stage2_instance_key("pos", "RQ", "pos_product_sales") == "pos:rq:pos_product_sales"
      and OI.stage2_instance_key("other", "", "Bill Payments") == "other:file:bill_payments"
      and OI.kind_of_instance("inventory:rq:pos_inventory_listing") == "inventory" and OI.stage_of_kind("inventory") == "2"
      and OI.stage_of_kind("commission") == "3")
check("inventory_aging_device stays OUTSIDE ingest_slice.INGEST_PARTITION (a snapshot table — landed through the snapshot writer, not a slice replace)",
      "inventory_aging_device" not in IS.INGEST_PARTITION and IS.INGEST_PARTITION["raw_sales"] == {"partition": "store", "date": "trans_date"})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE SHARED RESOLVER + THE GATE (pure)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def fake_resolve(raw):
    return {"100 main st": ("B-100", "address"), "b-200": ("B-200", "code")}.get(str(raw).lower(), (None, None))
rows = OI.identity_rows([{"store": STORE_ADDR, "ext_price": 10}, {"store": STORE_CODE, "ext_price": 5},
                         {"store": STORE_NEW, "ext_price": 7}, {"store": STORE_NEW, "ext_price": 1}], "store", "ext_price")
res = OI.resolve_stores(rows, fake_resolve)
check("two strings resolve (by address, by code), the third is UNRESOLVED with its count and Σ",
      [r["status"] for r in res] == ["unresolved", "resolved", "resolved"] and res[0]["value"] == STORE_NEW
      and res[0]["count"] == 2 and res[0]["sum_raw"] == 8.0 and {r["value"]: r.get("how") for r in res[1:]} == {STORE_ADDR: "address", STORE_CODE: "code"})
check("the gate: one unresolved string blocks; assign / create / not-ours-with-reason each clear it",
      OI.unresolved_stores(res) == [STORE_NEW]
      and OI.unresolved_stores(OI.resolve_stores(rows, fake_resolve, {STORE_NEW: {"action": "assign", "store_code": "B-100"}})) == []
      and OI.unresolved_stores(OI.resolve_stores(rows, fake_resolve, {STORE_NEW: {"action": "create", "store_code": "B-300"}})) == []
      and OI.unresolved_stores(OI.resolve_stores(rows, fake_resolve, {STORE_NEW: {"action": "not_ours", "reason": "a sister company's kiosk"}})) == [])
check("not-ours WITHOUT a reason does not clear the gate; company-level only where allowed (3.7)",
      OI.unresolved_stores(OI.resolve_stores(rows, fake_resolve, {STORE_NEW: {"action": "not_ours"}})) == [STORE_NEW]
      and OI.unresolved_stores(OI.resolve_stores(rows, fake_resolve, {STORE_NEW: {"action": "company_level"}})) == [STORE_NEW]
      and OI.unresolved_stores(OI.resolve_stores(rows, fake_resolve, {STORE_NEW: {"action": "company_level"}}, allow_company_level=True)) == [])
check("excluded_stores = the not-ours strings (with reasons) whose rows do not land",
      OI.excluded_stores(OI.resolve_stores(rows, fake_resolve, {STORE_NEW: {"action": "not_ours", "reason": "kiosk"}})) == {STORE_NEW: "kiosk"})
ref = OI.stage2_refusals("pos", ["store", "trans_date", "ext_price"], res, {"match": True}, None, rows_to_land=5)
check("stage2_refusals names the unresolved store; nothing else wrong → only that",
      len(ref) == 1 and STORE_NEW in ref[0])
check("…missing required columns, undated rows, storeless rows, a differing total and $0=$0 are each named",
      any("Map these columns" in r for r in OI.stage2_refusals("pos", ["ext_price"], [], {"match": True}, None, rows_to_land=1))
      and any("no parseable date" in r for r in OI.stage2_refusals("pos", ["store", "trans_date", "ext_price"], [], {"match": True}, None, undated_rows=3, rows_to_land=1))
      and any("no store" in r for r in OI.stage2_refusals("pos", ["store", "trans_date", "ext_price"], [], {"match": True}, None, storeless_rows=2, rows_to_land=1))
      and any("differs" in r for r in OI.stage2_refusals("pos", ["store", "trans_date", "ext_price"], [], OI.simple_tie(10, 12), None, rows_to_land=1))
      and any("$0.00" in r for r in OI.stage2_refusals("pos", ["store", "trans_date", "ext_price"], [], OI.simple_tie(0, 0), None, rows_to_land=1))
      and OI.stage2_refusals("pos", ["store", "trans_date", "ext_price"], [], OI.simple_tie(10, 12), {"reason": "POS re-priced two lines after export"}, rows_to_land=1) == [])
sv = OI.sales_verify([{"trans_id": "A", "store": "S1", "salesperson": "r", "trans_date": "2026-08-02", "ext_price": 10, "gp": 3, "voided": "No"},
                      {"trans_id": "A", "store": "S1", "salesperson": "r", "trans_date": "2026-08-05", "ext_price": -4, "gp": -1, "voided": "Yes"},
                      {"trans_id": "B", "store": "S2", "salesperson": "q", "trans_date": "bad", "ext_price": 1, "gp": 0, "voided": ""}])
check("sales_verify: rows, distinct txns, Σ amount, Σ GP, span, voided, per-store, undated — from the rows, no abs()",
      sv["rows"] == 3 and sv["distinct_txns"] == 2 and sv["sum_amount"] == 7.0 and sv["sum_gp"] == 2.0
      and sv["date_span"] == {"from": "2026-08-02", "to": "2026-08-05", "undated_rows": 1} and sv["voided_rows"] == 1
      and sv["per_store"][0] == {"value": "S1", "count": 2, "sum": 6.0})
iv = OI.inventory_verify([{"sku": "s", "imei": "1", "store": "S1", "unit_cost": 100, "total_cost": 100, "quantity": 1},
                          {"sku": "s", "imei": "", "serial": "", "store": "S1", "unit_cost": 50, "total_cost": "", "quantity": 2}])
check("inventory_verify: units from quantity, Σ cost (total_cost, else unit × qty), rows with / without a device key",
      iv["units"] == 3 and iv["sum_cost"] == 200.0 and iv["rows_with_device_key"] == 1 and iv["rows_without_device_key"] == 1)
check("simple_tie: footer beats typed, typed is recorded as such, nothing → match None",
      OI.simple_tie(5, 5, 9)["file_total_source"] == "footer" and OI.simple_tie(5, None, "5")["file_total_source"] == "typed"
      and OI.simple_tie(5, None, "5")["match"] is True and OI.simple_tie(5, None)["match"] is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE RAIL — 2.0 checklist rows, 'no inventory' recorded, the Stage-4 table, the Stage-5 runbook")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
rows = [{"stage": "2", "instance_key": "pos:rq:pos_product_sales", "step": "2.4", "status": "in_progress", "payload": {"source_ref": "rq"}},
        {"stage": "2", "instance_key": OI.INVENTORY_NONE_KEY, "step": "2.0", "status": "verified", "payload": {"reason": "POS has no inventory module"},
         "verified_numbers": {"declined": True}, "verified_by": "owner"},
        {"stage": "2", "instance_key": "other:file:bill_payments", "step": "2.6", "status": "needs_input", "payload": {"name": "bill payments"},
         "verified_numbers": {"basis": "received — no destination", "rows": 12}, "blocking_reason": "no destination for 'bill payments' yet"},
        {"stage": "3", "instance_key": "commission:c1:commission_statement", "step": "3.9", "status": "verified",
         "payload": {"carrier_name": "Northwind", "period": "August 2026"},
         "verified_numbers": {"tie": {"our_total": 100.0, "file_total": 100.0, "difference": 0.0, "match": True}, "rows_landed": 5}, "verified_by": "owner"}]
rl = OI.rail(rows, "pos:rq:pos_product_sales", {"companies": 1, "stores": 2, "carriers": 1})
check("stages: 1 reads the company rows (verified: stores + carriers exist, not built here), 2 in progress, 3 verified, 4/5 built",
      [s["status"] for s in rl["stages"]] == ["verified", "needs_input", "verified", "in_progress", "not_started"]
      and rl["stages"][0]["built"] is False and all(s["built"] for s in rl["stages"][1:]))
check("resume lands on the current instance's step, with its stage",
      rl["resume"] == {"instance_key": "pos:rq:pos_product_sales", "step": "2.4", "stage": "2"})
vt = {r["instance_key"]: r for r in rl["verify_table"]}
check("Stage-4 table: one row per source — our total, file total, diff, status, who; a red row links to its step",
      set(vt) == {"pos:rq:pos_product_sales", OI.INVENTORY_NONE_KEY, "other:file:bill_payments", "commission:c1:commission_statement"}
      and vt["commission:c1:commission_statement"]["our_total"] == 100.0 and vt["commission:c1:commission_statement"]["red"] is False
      and vt["pos:rq:pos_product_sales"]["red"] is True and vt["pos:rq:pos_product_sales"]["fix_step"] == "2.4"
      and vt["other:file:bill_payments"]["red"] is True and "no destination" in vt["other:file:bill_payments"]["blocking_reason"]
      and vt[OI.INVENTORY_NONE_KEY]["red"] is False and "no inventory export" in vt[OI.INVENTORY_NONE_KEY]["basis"])
rb = rl["runbook"]
check("Stage-5 runbook: the two links + what to upload each month, generated from the instances (the declined inventory is not an upload)",
      [l["label"] for l in rb["links"]] == ["Sales report", "Commissions"]
      and [m["instance_key"] for m in rb["monthly"]] == ["pos:rq:pos_product_sales", "other:file:bill_payments", "commission:c1:commission_statement"]
      and rb["monthly"][1]["lands_in"].startswith("(no destination") and rb["monthly"][0]["lands_in"] == "raw_sales")
check("labels: kind + source for stage 2, carrier + type for stage 3, 'none (recorded)' for the declined inventory",
      "POS report" in vt["pos:rq:pos_product_sales"]["label"] and "rq" in vt["pos:rq:pos_product_sales"]["label"]
      and vt["commission:c1:commission_statement"]["label"].startswith("Northwind") and "none" in vt[OI.INVENTORY_NONE_KEY]["label"])
check("company lamp: nothing → not started; stores only → in progress; stores + carriers → verified",
      OI.company_lamp({}) == "not_started" and OI.company_lamp({"stores": 3}) == "in_progress" and OI.company_lamp({"stores": 3, "carriers": 1}) == "verified")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. THE POS SALES EXPORT THROUGH THE REAL ENDPOINTS — detect → columns → stores → verify → commit → RE-READ")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db()
a1 = analyze(db)
check("2.2: both sheets read — sheet 1 primary with the header found under the title block, sheet 2 a continuation, appended",
      a1["detect"]["header_row"] == 2 and [s["role"] for s in a1["detect"]["sheets"]] == ["primary", "continuation"]
      and a1["detect"]["data_rows"] == len(LINES) + 1, a1["detect"])
check("2.2: the footer (every identity column blank, money present) is found once the layout's identity columns are proposed; its value is the file's total",
      a1["detect"]["footer"]["detected"] is True and a1["detect"]["footer"]["file_total_raw"] == SALES_TOTAL
      and a1["detect"]["footer"]["equals_lines_sum"] is True and a1["detect"]["footer_rows_dropped"] == 1, a1["detect"]["footer"])
cols = {c["target_field"]: c for c in a1["columns"]}
check("2.3: the layout's defaults propose the columns FROM THE FILE with samples (Invoice #, Invoiced At, Sold On, Total Price, Gross Profit, Refund)",
      all(cols[f]["column"] == h and cols[f]["provenance"] == OI.PROV_FILE for f, h in
          (("trans_id", "Invoice #"), ("store", "Invoiced At"), ("trans_date", "Sold On"), ("ext_price", "Total Price"), ("gp", "Gross Profit"), ("voided", "Refund")))
      and cols["store"]["samples"][0] in (STORE_ADDR, STORE_CODE, STORE_NEW) and len(cols["ext_price"]["samples"]) == 3, {k: (v["column"], v["provenance"]) for k, v in cols.items()})
check("the identity fields are the layout's non-money fields (the mig-1004 rule), the target is raw_sales, the instance is pos:rq:pos_product_sales",
      "trans_id" in a1["detect"]["identity_fields"] and "ext_price" not in a1["detect"]["identity_fields"]
      and a1["target_table"] == "raw_sales" and a1["instance_key"] == "pos:rq:pos_product_sales")
st = {r["value"]: r for r in a1["stores"]}
check("2.4: the shared resolver — the address spelling → B-100 (address), the code → B-200 (code), the kiosk UNRESOLVED",
      st[STORE_ADDR]["lands_as"] == "B-100" and st[STORE_ADDR]["how"] == "address"
      and st[STORE_CODE]["lands_as"] == "B-200" and st[STORE_CODE]["how"] == "code"
      and st[STORE_NEW]["status"] == "unresolved" and a1["unresolved_stores"] == [STORE_NEW], {k: (v["status"], v.get("how")) for k, v in st.items()})
rp = {r["value"]: r for r in a1["reps"]}
check("2.4: reps — 'Alice Rep' is on the roster by name, 'bob.s' by POS login (epay_salesperson) — both resolved",
      rp["Alice Rep"]["resolved_name"] == "Alice Rep" and rp["bob.s"]["resolved_name"] == "Bob Smith" and rp["bob.s"]["how"] == "roster")
vn = a1["verify"]["numbers"]
check("2.5: rows, distinct invoices, Σ amount, Σ GP, span, refunds, per-store, per-rep — beside the footer; the preview ties to the cent",
      vn["rows"] == len(LINES) == 48 and vn["distinct_txns"] == 24 and vn["sum_amount"] == SALES_TOTAL and vn["sum_gp"] == SALES_GP
      and vn["date_span"] == {"from": SPAN_LO, "to": SPAN_HI, "undated_rows": 0} and vn["voided_rows"] == REFUNDS
      and len(vn["per_store"]) == 3 and len(vn["per_rep"]) == 2
      and a1["verify"]["tie"] == {"our_total": SALES_TOTAL, "file_total": SALES_TOTAL, "file_total_source": "footer", "difference": 0.0, "match": True}, (vn, a1["verify"]["tie"]))
check("2.5: the preview names the open gate — the unresolved kiosk — as a refusal, before anyone presses commit",
      any(STORE_NEW in r for r in a1["verify"]["refusals"]))
check("the file is KEPT (private bucket, org-prefixed path) and its reference is in the stage row; nothing else was written",
      a1["file"]["stored"] is True and a1["file"]["path"].startswith(f"{ORG}/") and (R._INTAKE_BUCKET, a1["file"]["path"]) in db.storage.files
      and db.tables["onboarding_stage_state"][0]["payload"]["file"]["path"] == a1["file"]["path"]
      and not db.tables.get("column_mapping") and not db.tables.get("raw_sales") and not db.tables.get("store_aliases"), a1.get("file"))
check("a money column not picked as the amount is RECORDED with its Σ (Gross Profit)",
      any(m["header"] == "Gross Profit" and m["sum"] == SALES_GP for m in a1["verify"]["ignored_money_columns"]))
# the gate
s_, d_ = http_error(commit, db, column_map=json.dumps(SALES_MAP))
check("commit with the kiosk unresolved → 400 naming it; NOTHING written (no mapping, no alias, no raw_sales)",
      s_ == 400 and STORE_NEW in str(d_) and not db.tables.get("column_mapping") and not db.tables.get("store_aliases") and not db.tables.get("raw_sales"), (s_, str(d_)[:160]))
s_, d_ = http_error(commit, db, column_map=json.dumps(SALES_MAP), identity=json.dumps({"store": {STORE_NEW: {"action": "not_ours"}}}))
check("not-ours WITHOUT a reason → still refused", s_ == 400 and STORE_NEW in str(d_))
# assign the kiosk to B-100 → commit
c1 = commit(db, column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK))
check("COMMIT ok: the map saved (carrier-NULL rows for the layout) and READ BACK; the alias saved and READ BACK through the resolver; the rep alias saved",
      c1["ok"] is True and set(c1["mapping_saved"]) >= {"trans_id", "store", "trans_date", "ext_price", "gp"}
      and {r["target_field"]: r["source_header"] for r in db.tables["column_mapping"]}["ext_price"] == "Total Price"
      and all(r.get("carrier_id") is None and r["report_key"] == "pos_product_sales" for r in db.tables["column_mapping"])
      and db.tables["store_aliases"][0]["alias"] == STORE_NEW and db.tables["store_aliases"][0]["store_code"] == "B-100"
      and db.tables["store_aliases"][0]["source"] == "onboarding-intake"
      and db.tables["rep_aliases"][0]["alias"] == "bob.s" and db.tables["rep_aliases"][0]["canonical"] == "Bob Smith", c1.get("problems"))
check("…the rows LANDED through the existing mapped importer: raw_sales holds every line, period derived from each row's OWN date, the footer never landed",
      len(db.tables["raw_sales"]) == len(LINES) and all(r["period"] == "August 2026" and r["period_month"] == 8 for r in db.tables["raw_sales"])
      and all(r["trans_id"] for r in db.tables["raw_sales"]) and c1["landing"]["footer_rows_skipped"] == 0
      and c1["landing"]["replace_scope"] == {"column": "store", "values": 3, "from": SPAN_LO, "to": SPAN_HI}, c1.get("landing"))
check("…the store strings land AS THE POS WROTE THEM (the alias resolves them everywhere, §13a) — the kiosk rows are in the table under its own spelling",
      sum(1 for r in db.tables["raw_sales"] if r["store"] == STORE_NEW) == len(NEW_STORE_LINES))
check("…RE-READ: rows re-read = rows built = rows inserted; Σ re-read = Σ shown = the footer; the stage row is VERIFIED with the numbers",
      c1["verified_numbers"]["rows_landed"] == len(LINES) == c1["verified_numbers"]["rows_built"] == c1["verified_numbers"]["rows_inserted"]
      and c1["verified_numbers"]["tie"]["match"] is True and c1["verified_numbers"]["tie"]["our_total"] == SALES_TOTAL
      and c1["verified_numbers"]["numbers"]["sum_gp"] == SALES_GP
      and next(r for r in db.tables["onboarding_stage_state"] if r["instance_key"] == "pos:rq:pos_product_sales")["status"] == "verified"
      and c1["verified_numbers"]["basis"].startswith("re-read from commcalc.raw_sales"), c1.get("verified_numbers", {}).get("tie"))
check("…the identity decisions are on the record: what each store string lands as, how, and what was written",
      {i["value"]: i["lands_as"] for i in c1["verified_numbers"]["identity"]["stores"]} == {STORE_ADDR: "B-100", STORE_CODE: "B-200", STORE_NEW: "B-100"}
      and c1["identity_written"]["aliases"] == [(STORE_NEW, "B-100")] and c1["identity_written"]["rep_aliases"] == [("bob.s", "Bob Smith")])
# resume: the second visit needs no re-drop, and the kiosk now resolves by ALIAS
a2 = analyze(db, file=False, use_stored="1", instance_key="pos:rq:pos_product_sales", column_map=json.dumps(SALES_MAP))
check("a later visit re-analyzes from the KEPT file (no re-drop): same rows, the columns are now 'your earlier choice', the kiosk resolves by alias",
      a2["verify"]["numbers"]["rows"] == len(LINES) and a2["filename"] == "sales.xlsx"
      and {c["target_field"]: c["provenance"] for c in a2["columns"]}["ext_price"] == OI.PROV_EARLIER
      and {r["value"]: r.get("how") for r in a2["stores"]}[STORE_NEW] == "alias" and a2["unresolved_stores"] == [], [(r["value"], r.get("how")) for r in a2["stores"]])
c2 = commit(db, file=False, use_stored="1", instance_key="pos:rq:pos_product_sales", column_map=json.dumps(SALES_MAP))
check("re-committing the same file REPLACES its slice (stores × dates) — still exactly one row per line, never doubled; still ties",
      c2["ok"] is True and len(db.tables["raw_sales"]) == len(LINES) and c2["verified_numbers"]["tie"]["match"] is True)
db.seed("raw_sales", [{"org_id": ORG, "period": "July 2026", "period_month": 7, "period_year": 2026, "store": STORE_ADDR,
                       "trans_id": "OLD-1", "trans_date": "2026-07-15", "ext_price": 99.0, "gp": 1.0}])
c3 = commit(db, file=False, use_stored="1", instance_key="pos:rq:pos_product_sales", column_map=json.dumps(SALES_MAP))
check("a row of the same store OUTSIDE the file's date range survives the replace (the slice is store × dates, never the whole store)",
      c3["ok"] is True and any(r["trans_id"] == "OLD-1" for r in db.tables["raw_sales"]) and len(db.tables["raw_sales"]) == len(LINES) + 1)
# not ours: the kiosk's rows are EXCLUDED and counted
s_, d_ = http_error(commit, fresh_db(), column_map=json.dumps(SALES_MAP), identity=json.dumps({"store": {STORE_NEW: {"action": "not_ours", "reason": "sister company's kiosk"}}}))
check("'not ours' with a reason, no attestation: refused — the file's total includes the excluded rows, so the difference is named",
      s_ == 400 and "differs" in str(d_), (s_, str(d_)[:160]))
db = fresh_db()
c4 = commit(db, column_map=json.dumps(SALES_MAP), identity=json.dumps({"store": {STORE_NEW: {"action": "not_ours", "reason": "sister company's kiosk"}}}),
            attestation=json.dumps({"reason": "the kiosk's lines belong to the sister company"}))
check("…with the attestation: the kiosk's rows are EXCLUDED (counted), no alias written, the rest lands and re-reads",
      c4["ok"] is True and c4["verified_numbers"]["rows_excluded_not_ours"] == len(NEW_STORE_LINES)
      and len(db.tables["raw_sales"]) == len(LINES) - len(NEW_STORE_LINES) and not db.tables.get("store_aliases")
      and c4["verified_numbers"]["attestation"]["by"] == "tester", c4.get("problems"))
# create a store
db = fresh_db()
c5 = commit(db, column_map=json.dumps(SALES_MAP), identity=json.dumps({"store": {STORE_NEW: {"action": "create", "store_code": "B-300", "address": "300 River Rd"}}}))
check("'create': a storeops store row is created through storeops' own writer, the raw spelling aliased to it, and read back through the resolver",
      c5["ok"] is True and any(s["store_code"] == "B-300" and s["address"] == "300 River Rd" for s in db.tables["stores"]) and c5["identity_written"]["stores_created"] == ["B-300"]
      and c5["identity_written"]["aliases"] == [(STORE_NEW, "B-300")], c5.get("problems"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. THE INVENTORY LISTING — units, Σ cost, per-store, as-of; landed through the snapshot writer")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db()
i1 = analyze(db, data=inventory_csv(), filename="inventory.csv", source_kind="inventory", layout="pos_inventory_listing")
ivn = i1["verify"]["numbers"]
check("2.2/2.5: header under the title, the footer found (Σ cost), 30 units — 27 with a device key, 3 ordered units without — per-store units",
      i1["detect"]["header_row"] == 1 and i1["detect"]["footer"]["file_total_raw"] == INV_COST and ivn["rows"] == 30 and ivn["units"] == 30
      and ivn["rows_with_device_key"] == 27 and ivn["rows_without_device_key"] == 3 and ivn["sum_cost"] == INV_COST
      and {p["value"] for p in ivn["per_store"]} == {STORE_ADDR, STORE_CODE} and i1["verify"]["tie"]["match"] is True, (i1["detect"]["footer"], ivn))
check("2.4: both store strings resolve; there is no rep column on an inventory listing",
      i1["unresolved_stores"] == [] and i1["reps"] == [])
s_, d_ = http_error(commit, db, data=inventory_csv(), filename="inventory.csv", source_kind="inventory", layout="pos_inventory_listing", column_map=json.dumps(INV_MAP))
check("commit without an as-of date → refused (a snapshot has a date)", s_ == 400 and "as_of_date" in str(d_))
c6 = commit(db, data=inventory_csv(), filename="inventory.csv", source_kind="inventory", layout="pos_inventory_listing",
            column_map=json.dumps(INV_MAP), as_of_date="2026-08-31")
check("COMMIT ok: 27 keyed units landed through b2b_sweep.write_inventory_devices (upsert per device, as_of stamped), the 3 without a key REPORTED not landed",
      c6["ok"] is True and c6["saved"] == 27 and c6["verified_numbers"]["rows_without_device_key"] == 3
      and len(db.tables["inventory_aging_device"]) == 27 and all(r["as_of_date"] == "2026-08-31" and r["on_hand"] is True for r in db.tables["inventory_aging_device"]), c6.get("problems"))
check("…RE-READ: rows re-read = built = inserted, Σ unit cost re-read = shown; the raw row is kept on each device (status / total cost survive)",
      c6["verified_numbers"]["rows_landed"] == 27 and c6["verified_numbers"]["numbers"]["sum_unit_cost"] == money(sum(r["unit_cost"] for r in INV_KEYED))
      and db.tables["inventory_aging_device"][0]["raw_row"].get("status") == "In stock")
c7 = commit(db, data=inventory_csv(), filename="inventory.csv", source_kind="inventory", layout="pos_inventory_listing",
            column_map=json.dumps(INV_MAP), as_of_date="2026-09-30")
check("next month's listing: the same devices are UPSERTED (still 27 rows, re-stamped), never duplicated and never a 23505",
      c7["ok"] is True and len(db.tables["inventory_aging_device"]) == 27 and all(r["as_of_date"] == "2026-09-30" for r in db.tables["inventory_aging_device"]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. THE 'OTHER' REPORT — received and recorded honestly; never faked into a table")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db()
s_, d_ = http_error(analyze, db, data=other_csv(), filename="billpay.csv", source_kind="other", pos_source="", layout="")
check("an 'other' report needs a NAME", s_ == 400 and "name" in str(d_))
o1 = analyze(db, data=other_csv(), filename="billpay.csv", source_kind="other", pos_source="", name="Bill payments")
check("analyze: no target table, no column proposal (there is no field registry to propose into), the headers, 12 rows, and every money column's Σ",
      o1["target_table"] is None and o1["columns"] == [] and o1["verify"]["numbers"]["rows"] == 12
      and o1["verify"]["numbers"]["headers"] == ["Date", "Store", "Account", "Amount", "Fee"]
      and {m["header"]: m["sum"] for m in o1["verify"]["numbers"]["money_columns"]}["Amount"] == OTHER_AMOUNT
      and o1["instance_key"] == "other:file:bill_payments", o1["verify"])
o2 = commit(db, data=other_csv(), filename="billpay.csv", source_kind="other", pos_source="", name="Bill payments")
row = next(r for r in db.tables["onboarding_stage_state"] if r["instance_key"] == "other:file:bill_payments")
check("commit: RECORDED — ok:false, recorded:true, the stage row NEEDS_INPUT naming 'no destination', the numbers + the kept file on the record; NO table touched",
      o2["ok"] is False and o2["recorded"] is True and row["status"] == "needs_input" and "no destination" in row["blocking_reason"]
      and row["verified_numbers"]["rows"] == 12 and row["verified_numbers"]["file"]["stored"] is True
      and not db.tables.get("raw_sales") and not db.tables.get("column_mapping"), (o2.get("problems"), row.get("blocking_reason")))
st_ = R.onboarding_intake_state(org_id=ORG)
check("GET /state: the Stage-4 table carries the 'other' row red with its reason, the runbook says it has no destination",
      any(r["instance_key"] == "other:file:bill_payments" and r["red"] and "no destination" in r["blocking_reason"] for r in st_["rail"]["verify_table"])
      and any(m["kind"] == "other" and m["lands_in"].startswith("(no destination") for m in st_["rail"]["runbook"]["monthly"]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. 2.0 — the checklist, 'no inventory export' as a recorded choice, sign-off")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db()
use(db)
s_, d_ = http_error(R.onboarding_intake_put_state, R.OnboardingIntakeStateIn(instance_key=OI.INVENTORY_NONE_KEY, step="2.0", status="verified", payload={}), org_id=ORG)
check("'no inventory export' without a reason → refused", s_ == 400 and "why" in str(d_).lower())
r1 = R.onboarding_intake_put_state(R.OnboardingIntakeStateIn(instance_key=OI.INVENTORY_NONE_KEY, step="2.0", status="verified",
                                                              payload={"reason": "the POS has no inventory module"}, by="owner"), org_id=ORG)
check("…with a reason: recorded VERIFIED with reason, by and when — an explicit choice, never a blank",
      r1["save"]["saved"] is True and r1["rail"]["instances"][0]["verified_numbers"]["declined"] is True
      and r1["rail"]["instances"][0]["verified_numbers"]["reason"].startswith("the POS") and r1["rail"]["instances"][0]["verified_by"] == "owner")
s_, d_ = http_error(R.onboarding_intake_put_state, R.OnboardingIntakeStateIn(instance_key="pos:rq:pos_product_sales", step="2.1", status="verified"), org_id=ORG)
check("a real source can NOT be marked verified through PUT /state — only the commit's re-read verifies", s_ == 400 and "commit" in str(d_))
r2 = R.onboarding_intake_put_state(R.OnboardingIntakeStateIn(instance_key="pos:rq:pos_product_sales", step="2.1", payload={"source_ref": "rq", "layout": "pos_product_sales"}), org_id=ORG)
check("a ticked checklist item is an instance row at 2.1 (stage 2); steps of every stage validate",
      r2["save"]["saved"] is True and next(i for i in r2["rail"]["instances"] if i["instance_key"] == "pos:rq:pos_product_sales")["stage"] == "2"
      and http_error(R.onboarding_intake_put_state, R.OnboardingIntakeStateIn(instance_key="pos:rq:x", step="7.7"), org_id=ORG)[0] == 400
      and http_error(R.onboarding_intake_put_state, R.OnboardingIntakeStateIn(instance_key="bogus:x", step="2.1"), org_id=ORG)[0] == 400)
s_, d_ = http_error(R.onboarding_intake_sign_off, R.OnboardingSignOffIn(name="Owner", role="owner"), org_id=ORG)
check("sign-off while a source is not verified → refused naming it", s_ == 400 and "not verified" in str(d_))
commit(db, column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK))
so = R.onboarding_intake_sign_off(R.OnboardingSignOffIn(name="Owner", role="owner", on_behalf=False), org_id=ORG)
check("…after every source is verified: signed off (name, role, on-behalf flag) on the run; stages 4 and 5 read verified",
      so["signed_off"]["signed_off_by"] == "Owner (owner)" and [s["status"] for s in so["rail"]["stages"]][3:] == ["verified", "verified"]
      and so["rail"]["sign_off"]["signed"] is True)
sx = R.onboarding_intake_state(org_id=ORG)
check("GET /state offers the 2.0 vocabulary: every kind built with its layouts, the org's stores and employees for 2.4, the inventory-none key",
      all(k["built"] for k in sx["source_kinds"]) and {l["report_key"] for k in sx["source_kinds"] if k["value"] == "pos" for l in k["layouts"]} == {"sales", "pos_product_sales"}
      and {s["store_code"] for s in sx["stores"]} == {"B-100", "B-200"} and sx["employees"] == ["Alice Rep", "Bob Smith"]
      and sx["inventory_none_key"] == OI.INVENTORY_NONE_KEY and sx["company"] == {"companies": 0, "stores": 2, "carriers": 1})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. NEGATIVE CONTROLS — the save guarantee, the gates, honest degradation")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db()
db.drop_inserts["raw_sales"] = True
c8 = commit(db, column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK))
check("a landing that silently loses every row: ok=False, 'rows landed 0 ≠ rows built', stage NEEDS_INPUT — never 'success'",
      c8["ok"] is False and any("rows landed 0" in p for p in c8["problems"])
      and next(r for r in db.tables["onboarding_stage_state"] if r["instance_key"] == "pos:rq:pos_product_sales")["status"] == "needs_input", c8["problems"])
db = fresh_db()
db.drop_inserts["store_aliases"] = True
s_, d_ = http_error(commit, db, column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK))
check("an alias write that does not stick: the read-back through the resolver catches it → 400, NOTHING imported",
      s_ == 400 and "does not resolve" in str(d_) and not db.tables.get("raw_sales"), (s_, str(d_)[:160]))
s_, d_ = http_error(commit, fresh_db(), data=sales_xlsx(footer_value=SALES_TOTAL + 250.0), column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK))
check("a footer that disagrees with the lines by 250.00 → refused with the difference; nothing written",
      s_ == 400 and "250.00" in str(d_), (s_, str(d_)[:200]))
bad = {k: v for k, v in SALES_MAP.items() if k != "trans_date"}
no_date_hdr = [h if h != "Sold On" else "When" for h in SALES_HEADERS]      # a header nothing proposes a date from
s_, d_ = http_error(commit, fresh_db(), data=sales_xlsx(headers=no_date_hdr), column_map=json.dumps(bad), identity=json.dumps(IDENTITY_OK))
check("no date column in the file → refused naming trans_date (the slice and the period need it)", s_ == 400 and "trans_date" in str(d_), (s_, str(d_)[:160]))
s_, d_ = http_error(commit, fresh_db(), data=sales_xlsx(undated_rows=5), column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK))
check("5 rows with no parseable date → refused naming the count (never booked to a guessed month)", s_ == 400 and "5 row(s) carry no parseable date" in str(d_), str(d_)[:200])
s_, d_ = http_error(commit, fresh_db(), data=sales_xlsx(footer=False), column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK))
check("no footer → refused until a total is typed or attested", s_ == 400 and "no total" in str(d_))
c9 = commit(fresh_db(), data=sales_xlsx(footer=False), column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK), typed_total=f"{SALES_TOTAL:.2f}")
check("…a TYPED total ties out and is recorded as 'typed'", c9["ok"] is True and c9["verified_numbers"]["tie"]["file_total_source"] == "typed")
s_, d_ = http_error(commit, fresh_db(), column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK), pos_source="")
check("a sales export with no POS source named → 400 (the instance is keyed by it)", s_ == 400 and "pos_source" in str(d_))
s_, d_ = http_error(commit, fresh_db(), column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK), layout="pos_inventory_listing")
check("a layout that does not land in the kind's table → 400", s_ == 400 and "layout" in str(d_))
s_, d_ = http_error(analyze, fresh_db(), file=False, use_stored="1", instance_key="pos:rq:pos_product_sales")
check("use_stored with nothing kept → 400 asking for the file again (never someone else's)", s_ == 400 and "drop the file again" in str(d_).lower())
db = fresh_db(storage_broken=True)
a3 = analyze(db, column_map=json.dumps(SALES_MAP))
check("the file store unavailable: analyze still works, says the file was NOT kept and why, writes no stage row for it",
      a3["file"]["stored"] is False and "unavailable" in a3["file"]["reason"] and not db.tables.get("onboarding_stage_state"))
c10 = commit(db, column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK))
check("…and the commit from a dropped file still lands and re-reads", c10["ok"] is True)
db = fresh_db(with_state=False)
c11 = commit(db, column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK))
check("without mig 1007: the commit still LANDS and ties out; state.saved=False names the migration",
      c11["ok"] is True and c11["state"]["saved"] is False and "1007" in c11["state"]["reason"])
db = fresh_db()
db.drop_every_other["raw_sales"] = True
c12 = commit(db, column_map=json.dumps(SALES_MAP), identity=json.dumps(IDENTITY_OK))
check("a landing that drops HALF the rows: the re-read count disagrees AND the re-read Σ disagrees → ok=False with both problems",
      c12["ok"] is False and len(c12["problems"]) >= 2 and c12["verified_numbers"]["rows_landed"] < len(LINES), c12["problems"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. WIRED, REGISTERED, AND NO CARRIER / POS NAMED")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
paths = [rt.path for rt in R.router.routes]
check("the endpoints are mounted (state ×2, analyze, commit, sign-off)",
      all(p in paths for p in ("/commcalc/onboarding/intake/state", "/commcalc/onboarding/intake/analyze",
                               "/commcalc/onboarding/intake/commit", "/commcalc/onboarding/intake/sign-off")))
land_src = inspect.getsource(R._intake_land)
check("the landing is the EXISTING paths — _ingest_mapped_df for raw_sales, b2b_sweep.write_inventory_devices for inventory — and no .insert into raw_sales / inventory of its own",
      "_ingest_mapped_df(" in land_src and "write_inventory_devices(" in land_src
      and ".table('raw_sales')" not in land_src and '.table("raw_sales")' not in land_src
      and '.table("inventory_aging_device")' not in land_src)
c2_src = inspect.getsource(R._intake_commit_stage2)
check("the stage-2 commit saves through the ONE mapping writer, the identity through the alias / store writers, lands, and RE-READS",
      all(t in c2_src for t in ("upsert_column_mapping(", "_intake_apply_identity(", "_intake_land(", "_intake_reread_sales(", "_intake_reread_inventory(")))
id_src = inspect.getsource(R._intake_apply_identity)
check("identity writes go through POST /store-aliases' function and storeops' create_store; rep aliases through the rep_aliases upsert shape",
      "add_store_alias(" in id_src and "create_store(" in id_src and "rep_aliases" in id_src)
check("the resolver is built on the existing _store_maps + _store_code_resolver (no second store map)",
      "_store_maps(" in inspect.getsource(R._intake_store_resolver) and "_store_code_resolver(" in inspect.getsource(R._intake_store_resolver))
check("3.7 (commission) resolves through the SAME resolver and the SAME writer",
      "_intake_store_resolver(" in inspect.getsource(R._intake_prepare_commission) and "_intake_apply_identity(" in inspect.getsource(R._intake_commit_commission))
check("the analyze endpoint writes nothing but the kept-file reference",
      not any(t in inspect.getsource(R.onboarding_intake_analyze) for t in (".insert(", ".upsert(", ".update(", "upsert_column_mapping", "_intake_land(")))
vocab = re.compile(r"\b(boost|verizon|cricket|metro|vidapay|total\s+wireless|luxelink|novawave|b2bsoft|iqmetrix|rtpos)\b", re.I)
srcs = inspect.getsource(OI) + "".join(inspect.getsource(f) for f in (
    R._intake_prepare, R._intake_prepare_stage2, R._intake_commit_stage2, R._intake_commit_other, R._intake_land,
    R._intake_store_resolver, R._intake_rep_resolver, R._intake_apply_identity, R._intake_file_put, R._intake_file_get,
    R._intake_reread_sales, R._intake_reread_inventory, R.onboarding_intake_state, R.onboarding_intake_put_state,
    R.onboarding_intake_sign_off, R._intake_payload, R._intake_house_layout, R._intake_company))
check("no carrier, POS vendor or tenant name in onboarding_intake.py or the router's intake functions (RULE TWO)", not vocab.search(srcs))
check("no pandas / fastapi / supabase import in the pure module",
      not re.search(r"^\s*(import|from)\s+(pandas|fastapi|supabase)", inspect.getsource(OI), re.M))
idx = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("registered in the index: §30 Stage B, the sign-off endpoint, the resolver, the harness",
      all(t in idx for t in ("30.6", "/commcalc/onboarding/intake/sign-off", "_intake_store_resolver", "harness_onboarding_intake_b.py", "stage2.tsx")))
page = open(FE_PAGE, encoding="utf-8").read() if os.path.exists(FE_PAGE) else ""
s2 = open(FE_STAGE2, encoding="utf-8").read() if os.path.exists(FE_STAGE2) else ""
shared = open(FE_SHARED, encoding="utf-8").read() if os.path.exists(FE_SHARED) else ""
check("the page carries the 2.0 checklist, the Stage-4 table and the Stage-5 runbook, and the stage-2 flow is a co-located component (not a second page)",
      "What do you have?" in s2 and "No inventory export" in s2 and "Stage2Flow" in page and "verify_table" in page and "runbook" in page
      and "not ours" in s2 and "${BASE}/sign-off" in page)
check("the stage-2 flow calls the SAME analyze / commit endpoints; the SHARED resolver panel offers assign / create / not ours and is rendered by BOTH 2.4 and 3.7",
      all(t in s2 for t in ("${BASE}/analyze", "${BASE}/commit", "use_stored", "<StoreResolver"))
      and all(t in shared for t in ('"assign"', '"create"', '"not_ours"', "export function StoreResolver"))
      and "<StoreResolver" in page and "allowCompanyLevel" in page)

print(f"\n══ onboarding intake — Stage B: {_pass} passed, {_fail} failed ══")
if _failures:
    print("FAILED:\n  - " + "\n  - ".join(_failures))
sys.exit(1 if _fail else 0)
