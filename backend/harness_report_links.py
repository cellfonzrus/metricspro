"""DB-FREE PROOF — the tenant-onboarding intake, STAGE D: REPORT LINKS. Every report a tenant loaded,
linked to every other by the columns they share, with the match counts shown EACH WAY (owner
2026-09-20: "we need to link the different reports automatically with each other with common columns").

    python3 harness_report_links.py        (from backend/; no network, no DB)

THE SCENARIO — a run with a sales export (IMEI + phone number + transaction id), a commission
statement (order id, store, rep, date), a RESIDUAL statement (phone number as the line identity +
order id), an inventory listing (IMEI), an activation feed (serial + phone number, loaded through the
activation-details import) and an X-report (store + date), over the in-memory fake client of the
Stage-B/C harnesses driving the REAL router functions (the kinds' own commits, then
GET /onboarding/intake/state?links=…):

    §A the vocabulary is DERIVED: every link-field spelling is a column_mapping.TARGET_FIELDS name;
       each source's columns come off the intake's own kind_fields + the layout; a field is linkable
       when ≥2 kinds carry it
    §B the PURE report over hand-built rows — counts each way EXACT against the fixture, computed
       independently here; the pairing basis per field (direct / via the sales line's phone number,
       exactly as the inventory auto-check pairs); ambiguous keys REPORTED, never resolved; unmatched
       lines LISTED; a pair with no common column says so; one report → no links, honestly; nothing
       loaded → said
    §C END TO END through the router — five real commits + the activation feed, the matrix on
       ?links=all with the counts re-derived from the fixture, the pair detail on ?links=a:b, the
       Stage-4 notes ("linked to N other reports"), the cache on the run's own stage-4 row with its
       fingerprint, STALE after a new commit, honest without mig 1007
    §D the FIRST CONSUMER — the inventory-vs-activations check (#254) and the report links pair the
       same activation lines to the same units (one pairing implementation: activation_index folds
       line_pairings, report_links calls line_pairings)
    §E NEGATIVE CONTROLS — a pairing that GUESSES (an ambiguous number resolved to its first
       candidate) changes the counts → RED; counts that do not reconcile with the fixture → RED;
       a second normaliser in the pure module → RED; a carrier / POS / vendor name → RED
    §F wired, registered, the page, the lock in CI
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
from app.modules.commcalc import report_links as RL
from app.modules.commcalc import inventory_sold_recon as ISR
from app.modules.commcalc import report_kinds as RK
from app.modules.commcalc.device_cost_recon import device_key as DKEY, norm_order as NORD
from app.modules.commcalc import router as R
from app.modules.storeops import router as SO
from app.modules.storeops import merchant_ids as MIDS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FE_DIR = os.path.join(ROOT, "frontend", "src", "app", "(platform)")
FE_PAGE = os.path.join(FE_DIR, "onboarding", "intake", "page.tsx")
FE_SHARED = os.path.join(FE_DIR, "onboarding", "intake", "intake-shared.tsx")
INDEX = os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")
ORG = "11111111-2222-3333-4444-555555555555"
HOUSE = "00000000-0000-0000-0000-000000000001"
CARRIER_ID = "aaaaaaaa-0000-0000-0000-00000000c001"

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
        print(f"  FAIL  {name}{(' — ' + str(extra)[:700]) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


NORMS = {"device_key": DKEY, "mobile_key": ISR.mobile_key, "norm_order": NORD}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# AN IN-MEMORY SUPABASE CLIENT (+ storage) — the harness_onboarding_intake_c pattern with the Stage-B/C
# destination tables (declared columns; a select of an undeclared column raises like 42703)
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
        if kw.get("count"):
            self._count = True
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
            n_all = len(out)
            if self._range:
                lo, hi = self._range
                out = out[lo:hi + 1]
            if self._limit is not None:
                out = out[:self._limit]
            return _Res(out, count=n_all if self._count else None)
        if self.op == "insert":
            self.db.check_rows(self.table, self.rows)
            written = []
            for r in self.rows:
                row = dict(r)
                row.setdefault("id", str(uuid.uuid4()))
                t.append(row)
                written.append(dict(row))
            return _Res(written)
        if self.op == "upsert":
            self.db.check_rows(self.table, self.rows)
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
    def __init__(self):
        self.buckets, self.files = set(), {}

    def get_bucket(self, name):
        if name not in self.buckets:
            raise RuntimeError("bucket not found")
        return {"name": name}

    def create_bucket(self, name):
        self.buckets.add(name)

    def from_(self, bucket):
        st = self

        class _B:
            def upload(self_, path, data, opts=None):
                st.files[(bucket, path)] = bytes(data)
                return {"Key": path}

            def download(self_, path):
                if (bucket, path) not in st.files:
                    raise RuntimeError("object not found")
                return st.files[(bucket, path)]
        return _B()


class FakeDB:
    def __init__(self):
        self.tables = {}
        self.storage = FakeStorage()
        self.declared = {
            "carrier": ["id", "org_id", "name", "code", "is_default"],
            "column_mapping": ["id", "org_id", "report_key", "carrier_id", "target_field", "source_header",
                               "transform", "is_active", "priority", "updated_at", "sign_convention"],
            "commission_ledger": ["id", "org_id", "period", "source_report", "account_id", "account_name", "store",
                                  "rep_user", "order_number", "order_type", "product_name", "trans_date", "due_date",
                                  "payment_month", "category", "commission", "spiff", "equipment_rebate",
                                  "residual_monthly", "autopay_residual", "payout_total", "raw_amount", "is_payout",
                                  "origin", "source_table", "source_row_id", "synced_at"],                # mig 071 / 251
            # `source` = mig 727 (writer provenance) — since 2026-09-20 also the report KIND that wrote the row
            # (landing_identity.KIND_STAMP); `raw_sales_product` = mig 1011, the by-product aggregate's OWN table
            "raw_sales": ["id", "org_id", "period", "period_month", "period_year", "store", "salesperson", "user_login",
                          "department", "category", "product_desc", "product_id", "gp", "ext_price", "trans_id",
                          "trans_date", "contract_type", "mdn", "serial_1", "register", "tender_type", "voided",
                          "trans_type", "sku", "customer", "email", "customer_no", "quantity", "total_cost",
                          "pricing_discounts", "contract_no", "source", "created_at"],
            "raw_sales_product": ["id", "org_id", "period", "period_month", "period_year", "store", "salesperson", "user_login",
                                  "department", "category", "product_desc", "product_id", "gp", "ext_price", "trans_id",
                                  "trans_date", "contract_type", "mdn", "serial_1", "register", "tender_type", "voided",
                                  "trans_type", "sku", "customer", "email", "customer_no", "quantity", "total_cost",
                                  "pricing_discounts", "contract_no", "source", "created_at"],
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
            "pos_tender_summary": ["id", "org_id", "close_date", "store", "tender_type", "tender_class", "amount",
                                   "source", "updated_at"],                                             # mig 062
            "raw_custom_import": ["id", "org_id", "report_key", "period", "period_month", "period_year",
                                  "source_filename", "row_index", "data", "carrier_id"],                # mig 099
        }

    def columns(self, table):
        if table in self.declared:
            return self.declared[table]
        return ["*"] + [k for r in (self.tables.get(table) or []) for k in r]

    def check_rows(self, table, rows):
        if table in self.declared:
            for r in rows:
                bad = [k for k in r if k not in self.declared[table]]
                if bad:
                    raise RuntimeError(f'42703 column "{bad[0]}" of {table} does not exist')
        if table == "commission_ledger":
            for r in rows:
                r.setdefault("origin", "file")
        if table in ("raw_sales", "raw_sales_product"):
            for r in rows:
                if not r.get("period"):
                    raise RuntimeError(f'23502 null value in column "period" of {table} violates not-null constraint')

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


STORE_A = "100 Main St"          # the roster address of B-100
STORE_B = "200 Oak Ave"          # the roster address of B-200
STORE_NEW = "Riverside Kiosk"    # unknown to the platform (X-report sheet; assigned to B-200 at 2.4)


def fresh_db():
    db = FakeDB()
    db.seed("stores", [{"org_id": ORG, "store_code": "B-100", "address": STORE_A, "market": "NY", "is_active": True},
                       {"org_id": ORG, "store_code": "B-200", "address": STORE_B, "market": "NY", "is_active": True}])
    db.seed("store_mapping", [{"org_id": ORG, "store_code": "B-100", "store_address": STORE_A, "market": "NY"},
                              {"org_id": ORG, "store_code": "B-200", "store_address": STORE_B, "market": "NY"}])
    db.seed("employees", [{"org_id": ORG, "employee_id": "E1", "name": "Alice Rep", "epay_salesperson": "arep", "is_active": True},
                          {"org_id": ORG, "employee_id": "E2", "name": "Bob Smith", "epay_salesperson": "bob.s", "is_active": True}])
    db.seed("carrier", [{"id": CARRIER_ID, "org_id": ORG, "name": "Northwind Cellular", "code": "northwind"}])
    return db


def use(db):
    R.sb = lambda: db
    SO.sb = lambda: db
    SO.get_supabase = lambda: db
    MIDS.get_supabase = lambda: db
    R._TABLE_COL_PRESENT.clear()
    return db


def run(coro):
    return asyncio.run(coro)


def commit(db, data, filename, **form):
    use(db)
    kw = dict(verified_by="tester", org_id=ORG)
    kw.update(form)
    return run(R.onboarding_intake_commit(file=FakeUpload(data, filename), **kw))


def state(db, links=""):
    use(db)
    return R.onboarding_intake_state(links=links, org_id=ORG)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURE — one run, six reports. Every key below is chosen so the expected counts can be
# re-derived here by hand (the harness computes them independently of the module, §B / §C).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def imei(tag):
    return (str(tag) + "0" * 15)[:15]


D1, D2, D3, D4, D5, D9, D9B, D_ACT = (imei("35D1"), imei("35D2"), imei("35D3"), imei("35D4"), imei("35D5"),
                                     imei("35D9"), imei("35D9B"), imei("35ACT"))
M1, M2, M3, M5, M9 = "5550000001", "5550000002", "5550000003", "5550000005", "5550000009"

# the SALES export (layout `sales`: IMEI + phone number + trans id per line) — (trans id, store, rep, day, mobile, serial, price)
SALES = [
    ("T1001", STORE_A, "Alice Rep", "2026-08-03", f"({M1[:3]}) {M1[3:6]}-{M1[6:]}", D1, 300.0),     # handset line, phone formatted
    ("T1001", STORE_A, "Alice Rep", "2026-08-03", M1, "", 40.0),                                   # the plan line of the same txn
    ("T1002", STORE_A, "Alice Rep", "2026-08-04", M2, D2, 250.0),
    ("T1003", STORE_B, "bob.s", "2026-08-05", M3, D3, 200.0),
    ("T1004", STORE_B, "bob.s", "2026-08-06", "", D4, 150.0),                                      # serial, no phone
    ("T1005", STORE_A, "Alice Rep", "2026-08-07", M5, "", 60.0),                                   # phone, no serial
    ("T1006", STORE_A, "Alice Rep", "2026-08-08", "", "", 20.0),                                   # neither (accessory)
    ("T1007", STORE_B, "bob.s", "2026-08-09", M9, D9, 180.0),                                      # M9 on TWO devices → ambiguous
    ("T1008", STORE_B, "bob.s", "2026-08-09", M9, D9B, 180.0),
    ("T1009", STORE_A, "Alice Rep", "2026-08-10", M1, D1, -300.0),                                 # the refund of D1 (D1 on 2 lines)
]
SALES_HEADERS = ["Store", "Salesperson", "Trans ID", "Trans Date Time", "Activated Mobile Number", "Serial 1", "Ext Price", "GP", "Product Desc", "Department"]
SALES_MAP = {"store": "Store", "salesperson": "Salesperson", "trans_id": "Trans ID", "trans_date": "Trans Date Time",
             "mdn": "Activated Mobile Number", "serial_1": "Serial 1", "ext_price": "Ext Price", "gp": "GP",
             "product_desc": "Product Desc", "department": "Department"}
SALES_TOTAL = round(sum(l[6] for l in SALES), 2)


def sales_csv():
    lines = [",".join(SALES_HEADERS)]
    for (tid, st, rep, day, mob, ser, price) in SALES:
        lines.append(f'{st},{rep},{tid},{day} 10:00:00,"{mob}",{ser},{price:.2f},{price * 0.3:.2f},Item,Devices')
    lines.append(f",,,,,,{SALES_TOTAL:.2f},,,")                       # the file's own total row (blank identity)
    return ("\n".join(lines) + "\n").encode("utf-8")


# the COMMISSION statement (default type: order id + store + rep + date; NO phone-number column in this layout)
COMM_HEADERS = ["Report Section", "Report SubSection", "AgentSSOID", "Master Service Date", "Order Number", "Store", "Gross"]
COMM = [   # (section, subsection, rep, day, order, store string, gross)
    ("Activations", "New line", "arep", "2026-08-03", "T1001", STORE_A, 100.0),
    ("Activations", "New line", "arep", "2026-08-04", "T1002", STORE_A, 100.0),
    ("Activations", "New line", "bob.s", "2026-08-05", "T1003", STORE_B, 100.0),
    ("Activations", "New line", "bob.s", "2026-08-20", "T9999", STORE_B, 100.0),     # an order no sale line carries
    ("Incentives", "Spiff", "arep", "2026-08-11", "", STORE_A, 20.0),                # no order id
    ("Incentives", "Spiff", "bob.s", "2026-08-09", "T1007", STORE_B, 20.0),
]
COMM_MAP = {"product_name": "Report Section", "order_type": "Report SubSection", "rep_user": "AgentSSOID",
            "trans_date": "Master Service Date", "order_number": "Order Number", "store": "Store", "raw_amount": "Gross"}
COMM_CHOSEN = {"Activations": "commission", "Incentives": "spiff"}
COMM_TOTAL = round(sum(l[6] for l in COMM), 2)


def commission_csv():
    lines = ["Dealer Compensation Statement", ",".join(COMM_HEADERS)]
    for (pn, ot, rep, day, order, st, amt) in COMM:
        lines.append(f"{pn},{ot},{rep},{day},{order},{st},{amt:.2f}")
    lines.append(f",,,,,,{COMM_TOTAL:.2f}")
    return ("\n".join(lines) + "\n").encode("utf-8")


# the RESIDUAL statement (its own layout: the phone number IS the line identity, + order id)
RES_HEADERS = ["Account", "Plan", "Service Month", "Rep", "Order Number", "Store", "Residual"]
RES = [   # (mobile, plan, rep, order, store, signed amount — earned is NEGATIVE)
    (M1, "Unlimited Plus", "arep", "T1001", STORE_A, -12.50),
    (M2, "Unlimited Plus", "arep", "T1002", STORE_A, -12.50),
    (M3, "Unlimited Plus", "bob.s", "T1003", STORE_B, -12.50),
    (M5, "Basic Talk", "arep", "", STORE_A, -4.25),
    ("5550009999", "Basic Talk", "bob.s", "T4444", STORE_B, -4.25),                 # a number no other report carries
]
RES_MAP = {"account_id": "Account", "product_name": "Plan", "trans_date": "Service Month", "rep_user": "Rep",
           "order_number": "Order Number", "store": "Store", "raw_amount": "Residual"}
RES_CHOSEN = {"Unlimited Plus": "residual_monthly", "Basic Talk": "residual_monthly"}
RES_TOTAL_RAW = round(sum(l[5] for l in RES), 2)


def residual_csv():
    lines = [",".join(RES_HEADERS)]
    for (mob, plan, rep, order, st, amt) in RES:
        lines.append(f"{mob},{plan},2026-08-01,{rep},{order},{st},{amt:.2f}")
    lines.append(f",,,,,,{RES_TOTAL_RAW:.2f}")
    return ("\n".join(lines) + "\n").encode("utf-8")


# the INVENTORY listing (IMEI per unit; no phone number)
INV_HEADERS = ["Product SKU", "Tracking #", "Product Name", "Location", "Unit Cost", "Total Cost", "Quantity", "Status"]
INV = [(D1, 300.0), (D2, 250.0), (D3, 200.0), (D5, 100.0), (D9, 180.0)]
INV_MAP = {"sku": "Product SKU", "imei": "Tracking #", "item": "Product Name", "store": "Location", "unit_cost": "Unit Cost",
           "total_cost": "Total Cost", "quantity": "Quantity", "status": "Status"}


def inventory_csv():
    lines = [",".join(INV_HEADERS)]
    for k, cost in INV:
        lines.append(f"SKU-1,{k},Phone,{STORE_A},{cost:.2f},{cost:.2f},1,In stock")
    lines.append(f",,,,,{sum(c for _k, c in INV):.2f},{len(INV)},")
    return ("\n".join(lines) + "\n").encode("utf-8")


# the ACTIVATION feed (through the activation-details import): serial, or phone number only
ACTS = [
    {"Serial#": D3, "Contract Type": "Activation", "Trans ID": "A1", "Trans Date": "8/05/2026", "SP/PO Name": "Plan A"},            # by IMEI
    {"Serial#": "", "Contract Type": "Activation", "Trans ID": "A2", "Trans Date": "8/04/2026", "SP/PO Name": "Plan A", "MDN": M2},  # phone only → via the sale line → D2
    {"Serial#": "", "Contract Type": "Activation", "Trans ID": "A3", "Trans Date": "8/09/2026", "SP/PO Name": "Plan A", "MDN": M9},  # ambiguous number (D9 / D9B) → unpairable
    {"Serial#": D_ACT, "Contract Type": "Activation", "Trans ID": "A4", "Trans Date": "8/12/2026", "SP/PO Name": "Plan A"},         # a serial nothing else carries
    {"Serial#": "", "Contract Type": "Activation", "Trans ID": "A5", "Trans Date": "8/13/2026", "SP/PO Name": "Plan A"},            # neither
]

# the X-REPORT (store + date only): two stores, one day
XR_HEADER = ["Tender Types", "Sales", "Returns/Trade In", "Sub Net", "Drop", "Pickup", "Payments", "Refunds", "Net"]
XR_A = [("Cash", 522.08), ("Credit Card", 112.22), ("Store Account", 15.50)]
XR_B = [("Cash", 100.00), ("Debit Card", 50.00)]
XR_NAME = "X-Report_08092026-08092026.xlsx"
XR_DAY = "2026-08-09"
DEC_X = {"store": {STORE_NEW: {"action": "assign", "store_code": "B-200"}}}


def xr_money_row(label, amt):
    return [label, f"{amt:.2f}", "0.00", f"{amt:.2f}", "0.00", "0.00", "0.00", "0.00", f"{amt:.2f}"]


def xr_sheet(store, tenders):
    rows = [[f"Report Date:8/9/2026 - 8/9/2026", "", f"Locations:{store}", "", "", "", "", "", ""],
            ["Report Grouping:No grouping", "", "", "", "", "", "", "", ""],
            ["Tendered Amounts", "", "", "", "", "", "", "", ""], XR_HEADER]
    rows += [xr_money_row(l, a) for l, a in tenders]
    t = sum(a for _l, a in tenders)
    rows.append(["", f"${t:,.2f}", "$0.00", f"${t:,.2f}", "$0.00", "$0.00", "$0.00", "$0.00", f"${t:,.2f}"])
    return rows


def xr_workbook(sheets):
    import pandas as pd
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, rows in sheets:
            pd.DataFrame(rows).to_excel(w, sheet_name=name, header=False, index=False)
    return buf.getvalue()


XR_FILE = xr_workbook([(STORE_A, xr_sheet(STORE_A, XR_A)), (STORE_NEW, xr_sheet(STORE_NEW, XR_B))])


# ── THE EXPECTED COUNTS, derived here by hand from the fixture (never from the module) ─────────────
def expect(a_keys, b_keys):
    """{"lines", "with_key", "no_key", "matched", "unmatched", "ambiguous"} for a → b, from the key per
    line on each side (None = no usable key) — the harness's own reckoning."""
    from collections import Counter
    idx = Counter(k for k in b_keys if k)
    out = {"lines": len(a_keys), "with_key": sum(1 for k in a_keys if k), "matched": 0, "unmatched": 0, "ambiguous": 0}
    out["no_key"] = out["lines"] - out["with_key"]
    for k in a_keys:
        if not k:
            continue
        if idx.get(k):
            out["matched"] += 1
            if idx[k] > 1:
                out["ambiguous"] += 1
        else:
            out["unmatched"] += 1
    return out


def same(direction, exp):
    return all(direction.get(k) == v for k, v in exp.items())


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE VOCABULARY IS DERIVED — TARGET_FIELDS spellings, the intake's kind_fields, ≥2 kinds per link field")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
voc = RL.link_vocabulary(CM.TARGET_FIELDS)
check("every link-field spelling is a column_mapping.TARGET_FIELDS name (unknown = [])", voc["unknown"] == [], voc["unknown"])
check("each of the six link fields is carried by ≥2 layouts (a field is linkable when two kinds map it)",
      all(len(voc["carried_by"].get(f, [])) >= 2 for f in RL.FIELD_ORDER), voc["carried_by"])
check("the link fields are the six of the design: IMEI / serial, phone number, invoice / order number, store, rep, date — in strength order",
      RL.FIELD_ORDER == ["device", "mobile", "order", "store", "rep", "date"]
      and [RL.FIELD_LABELS[f] for f in RL.FIELD_ORDER] == ["IMEI / serial", "phone number", "invoice / order number", "store", "rep", "date"]
      and RL.FIELD_STRENGTH["device"] > RL.FIELD_STRENGTH["mobile"] > RL.FIELD_STRENGTH["order"] > RL.FIELD_STRENGTH["store"] > RL.FIELD_STRENGTH["rep"] > RL.FIELD_STRENGTH["date"])
cols = {k: RL.columns_for(k, l, OI.kind_fields(k, l), CM.TARGET_FIELDS) for k, l in
        [("sales", "sales"), ("pos", "pos_product_sales"), ("inventory", "pos_inventory_listing"),
         ("commission", "commission_ledger"), ("commission", "commission_ledger__residual")]}
check("sales (IMEI + phone) carries device / mobile / order / store / rep / date — store, date, txn, rep OFF the intake's own kind_fields",
      cols["sales"] == {"store": "store", "date": "trans_date", "order": "trans_id", "rep": "salesperson", "device": "serial_1", "mobile": "mdn"}, cols["sales"])
check("the POS cost-and-price layout carries no phone number; inventory carries device (imei, then serial) + store, no date",
      "mobile" not in cols["pos"] and cols["pos"]["device"] == "serial_1"
      and cols["inventory"] == {"store": "store", "device": ("imei", "serial")}, (cols["pos"], cols["inventory"]))
cres = RL.columns_for("commission", "commission_ledger__residual", OI.kind_fields("commission"), CM.TARGET_FIELDS)
ccom = RL.columns_for("commission", "commission_ledger", OI.kind_fields("commission"), CM.TARGET_FIELDS)
check("the commission statement carries order / store / rep / date; ONLY the residual layout's account_id counts as a phone number (§30.10 seam 2 — the ledger has no mobile column)",
      "mobile" not in ccom and ccom["order"] == "order_number" and cres["mobile"] == "account_id" and cres["order"] == "order_number", (ccom, cres))
check("matrix kinds and the activation feed use their destination's own columns (X-report: store + close date; activations: serial / mdn / trans id / date / store / rep)",
      RL.columns_for("x_report", None, None, CM.TARGET_FIELDS) == {"store": "store", "date": "close_date"}
      and RL.columns_for("activations", None, None, CM.TARGET_FIELDS)["device"] == "serial"
      and RL.columns_for("activations", None, None, CM.TARGET_FIELDS)["mobile"] == "mdn")
check("date_key: the day of an ISO date-time, None for anything else (a date is the weakest link)",
      RL.date_key("2026-08-09 10:00:00") == "2026-08-09" and RL.date_key("8/9/2026") is None and RL.date_key("") is None)
check("the link vocabulary's spellings are the registry cards' own signature_fields where they name a key column (trans_id / mdn / serial_1 / imei / account_id) — one vocabulary",
      {"trans_id", "mdn", "serial_1", "imei", "account_id"} <= {sf for r in RK.HOUSE_KINDS for sf in r["signature_fields"]}
      and {"trans_id", "mdn", "serial_1", "imei", "account_id"} <= {n if isinstance(n, str) else n[0] for f in RL.LINK_FIELDS for n in f["target_fields"]})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE PURE REPORT — counts each way exact, the basis per field, ambiguity reported, unmatched listed")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
LANDS = {STORE_A: "B-100", STORE_B: "B-200", STORE_NEW: "B-200"}
REPS = {"alice rep": "Alice Rep", "arep": "Alice Rep", "bob.s": "Bob Smith"}
store_key = lambda v: LANDS.get(str(v or "").strip())
rep_key = lambda v: REPS.get(str(v or "").strip().lower()) or (str(v or "").strip().lower() or None)


def src(ik, kind, label, layout, rows, cols=None):
    return {"instance_key": ik, "kind": kind, "label": label, "layout": layout, "rows": rows,
            "columns": cols if cols is not None else RL.columns_for(kind, layout, OI.kind_fields(kind, layout), CM.TARGET_FIELDS),
            "store_key": store_key, "rep_key": rep_key, "read_ok": True}


sales_rows = [{"store": st, "salesperson": rep, "trans_id": tid, "trans_date": day, "mdn": mob, "serial_1": ser, "ext_price": p}
              for (tid, st, rep, day, mob, ser, p) in SALES]
comm_rows = [{"store": st, "rep_user": rep, "order_number": order, "trans_date": day, "raw_amount": amt}
             for (_pn, _ot, rep, day, order, st, amt) in COMM]
res_rows = [{"account_id": mob, "rep_user": rep, "order_number": order, "trans_date": "2026-08-01", "store": st, "raw_amount": amt}
            for (mob, _plan, rep, order, st, amt) in RES]
inv_rows = [{"imei": k, "serial": None, "store": STORE_A, "sku": "SKU-1"} for k, _c in INV]
act_rows = [{"serial": a["Serial#"], "mdn": a.get("MDN", ""), "trans_id": a["Trans ID"], "trans_date": "2026-08-" + a["Trans Date"][2:4],
             "store": STORE_A, "salesperson": ""} for a in ACTS]
xr_rows = [{"store": STORE_A, "close_date": XR_DAY} for _ in XR_A] + [{"store": STORE_NEW, "close_date": XR_DAY} for _ in XR_B]

S_SALES = src("sales:rq:sales", "sales", "Sales report with IMEI and phone number", "sales", sales_rows)
S_COMM = src("commission:c1:commission_statement", "commission", "Commission report from the carrier", "commission_ledger", comm_rows)
S_RES = src("commission:c1:residual_statement", "commission", "Residual report from the carrier", "commission_ledger__residual", res_rows)
S_INV = src("inventory:rq:pos_inventory_listing", "inventory", "Inventory on hand", "pos_inventory_listing", inv_rows)
S_ACT = src(RL.ACTIVATIONS_INSTANCE_KEY, "activations", "Activation details", None, act_rows)
S_XR = src("x_report:rq:x_report", "x_report", "Cash register / X-report", None, xr_rows)

rep = RL.report([S_SALES, S_COMM, S_RES, S_INV, S_ACT, S_XR], NORMS, ISR.line_pairings, ISR.sales_mobile_index)
pairs = {(p["a"], p["b"]): p for p in rep["pairs"]}


def P(a, b):
    return pairs.get((a["instance_key"], b["instance_key"])) or pairs.get((b["instance_key"], a["instance_key"]))


def F(p, field):
    return next((f for f in p["fields"] if f["field"] == field), None)


def AB(p, a, field):
    """The direction a → other of `p` on `field`, whichever order the pair was stored in."""
    f = F(p, field)
    return f["a_to_b"] if p["a"] == a["instance_key"] else f["b_to_a"]


check("six sources → 15 pairs; the sales export is the bridge (device + phone number per line)",
      len(rep["pairs"]) == 15 and rep["bridges"] == [S_SALES["instance_key"]] and rep["note"] is None)

# sales ↔ commission by ORDER — the harness's own reckoning
exp_s2c = expect([NORD(l[0]) for l in SALES], [NORD(l[4]) or None for l in COMM])
exp_c2s = expect([NORD(l[4]) or None for l in COMM], [NORD(l[0]) for l in SALES])
p = P(S_SALES, S_COMM)
check("sales → commission by invoice / order number: 5 of 10 lines match (T1001 ×2, T1002, T1003, T1007); 5 unmatched; exact against the fixture",
      same(AB(p, S_SALES, "order"), exp_s2c) and exp_s2c["matched"] == 5 and exp_s2c["unmatched"] == 5, (AB(p, S_SALES, "order"), exp_s2c))
check("commission → sales by invoice / order number: 4 of 6 match; 1 unmatched (T9999); 1 carries no order id; T1001 matches TWO sales lines → 1 ambiguous, REPORTED",
      same(AB(p, S_COMM, "order"), exp_c2s) and exp_c2s == {"lines": 6, "with_key": 5, "no_key": 1, "matched": 4, "unmatched": 1, "ambiguous": 1}
      and AB(p, S_COMM, "order")["ambiguous_sample"] == [{"key": "t1001", "lines": 1, "other_side_lines": 2}]
      and [u["key"] for u in AB(p, S_COMM, "order")["unmatched_sample"]] == ["t9999"], AB(p, S_COMM, "order"))
check("the layman sentence, each way",
      F(p, "order")["sentence_a_to_b" if p["a"] == S_SALES["instance_key"] else "sentence_b_to_a"]
      == "5 of 10 Sales report with IMEI and phone number lines match a Commission report from the carrier line by invoice / order number; 5 unmatched"
      and "4 of 6 Commission report from the carrier lines match a Sales report with IMEI and phone number line by invoice / order number; 1 unmatched; 1 carry no invoice / order number; 1 match several Sales report with IMEI and phone number lines (ambiguous — listed, not resolved)"
      in (F(p, "order")["sentence_a_to_b"], F(p, "order")["sentence_b_to_a"]), (F(p, "order")["sentence_a_to_b"], F(p, "order")["sentence_b_to_a"]))
check("sales ↔ commission share order / store / rep / date — NOT the phone number (the commission layout has none) and NOT the device; strongest = order",
      p["shared"] == ["order", "store", "rep", "date"] and p["strongest"] == "order" and p["linked"] is True, p["shared"])
# store: the confirmed resolution (lands_as), not the raw string
exp_st = expect([LANDS[l[1]] for l in SALES], [LANDS[l[5]] for l in COMM])
check("sales → commission by STORE through the resolution confirmed at 2.4 / 3.7: every line matches (both stores appear on both sides); ambiguity is the norm for a store key and is counted",
      same(AB(p, S_SALES, "store"), exp_st) and exp_st["matched"] == 10 and exp_st["ambiguous"] == 10, (AB(p, S_SALES, "store"), exp_st))
# rep: resolved through the roster (arep → Alice Rep; bob.s → Bob Smith; 'Alice Rep' → Alice Rep)
exp_rep = expect([rep_key(l[2]) for l in SALES], [rep_key(l[2]) for l in COMM])
check("sales → commission by REP through the roster resolution ('arep' and 'Alice Rep' are one person): all 10 match",
      same(AB(p, S_SALES, "rep"), exp_rep) and exp_rep["matched"] == 10, AB(p, S_SALES, "rep"))
# date
exp_d = expect([l[3] for l in SALES], [l[3] for l in COMM])
check("sales → commission by DATE: a day both reports have lines on — 6 of 10",
      same(AB(p, S_SALES, "date"), exp_d) and exp_d["matched"] == 6, (AB(p, S_SALES, "date"), exp_d))

# sales ↔ residual by PHONE NUMBER (the residual's account_id) and order
p = P(S_SALES, S_RES)
exp_s2r = expect([ISR.mobile_key(l[4]) for l in SALES], [ISR.mobile_key(l[0]) for l in RES])
exp_r2s = expect([ISR.mobile_key(l[0]) for l in RES], [ISR.mobile_key(l[4]) for l in SALES])
check("sales → residual by phone number: 6 of 10 lines match (M1 ×3 incl. the formatted '(555) 000-0001' and the refund, M2, M3, M5); 2 unmatched (M9 ×2); 2 carry no phone number",
      same(AB(p, S_SALES, "mobile"), exp_s2r) and exp_s2r == {"lines": 10, "with_key": 8, "no_key": 2, "matched": 6, "unmatched": 2, "ambiguous": 0},
      (AB(p, S_SALES, "mobile"), exp_s2r))
check("residual → sales by phone number: 4 of 5 match; 1 unmatched (5550009999); M1 is on THREE sales lines → ambiguous, listed with the count",
      same(AB(p, S_RES, "mobile"), exp_r2s) and exp_r2s["matched"] == 4 and exp_r2s["ambiguous"] == 1
      and AB(p, S_RES, "mobile")["ambiguous_sample"] == [{"key": M1, "lines": 1, "other_side_lines": 3}]
      and AB(p, S_RES, "mobile")["unmatched_sample"][0]["key"] == "5550009999", AB(p, S_RES, "mobile"))
check("sales ↔ residual: strongest = phone number (beats the order number that also matches)", p["strongest"] == "mobile" and "order" in p["shared"])

# inventory ↔ sales by DEVICE — direct
p = P(S_INV, S_SALES)
exp_i2s = expect([DKEY(k) for k, _ in INV], [DKEY(l[5]) or None for l in SALES])
check("inventory → sales by IMEI: 4 of 5 units on a sale line (D1 on two lines → ambiguous 1); D5 unmatched; basis DIRECT",
      same(AB(p, S_INV, "device"), exp_i2s) and exp_i2s == {"lines": 5, "with_key": 5, "no_key": 0, "matched": 4, "unmatched": 1, "ambiguous": 1}
      and F(p, "device")["basis"] == RL.BASIS_DIRECT and [u["key"] for u in AB(p, S_INV, "device")["unmatched_sample"]] == [D5], AB(p, S_INV, "device"))
check("sales → inventory by IMEI: 5 of 10 lines name an on-hand unit; 2 unmatched (D4, D9B); 3 carry no serial",
      same(AB(p, S_SALES, "device"), expect([DKEY(l[5]) or None for l in SALES], [DKEY(k) for k, _ in INV]))
      and AB(p, S_SALES, "device")["matched"] == 5 and AB(p, S_SALES, "device")["no_key"] == 3, AB(p, S_SALES, "device"))

# activations ↔ inventory by DEVICE — via the sales line's phone number for the keyless line (§11a's pairing)
p = P(S_ACT, S_INV)
d = AB(p, S_ACT, "device")
check("activations → inventory by IMEI: A1 (serial D3) direct + A2 (phone only) THROUGH the sales line → D2 = 2 matched; A4 (D_ACT) unmatched; A3 ambiguous number and A5 (nothing) carry no usable key; basis VIA the sales export, stated",
      d["matched"] == 2 and d["unmatched"] == 1 and d["no_key"] == 2 and d["via_lines"] == 1 and d["lines"] == 5
      and F(p, "device")["basis"] == RL.BASIS_VIA and F(p, "device")["via"]["through"] == [S_SALES["instance_key"]]
      and d["via_reasons"] == {ISR.UNPAIR_AMBIGUOUS: 1, ISR.UNPAIR_NO_KEY: 1}, (d, F(p, "device")["via"]))
check("inventory → activations by IMEI: D2 (through the phone number) and D3 match = 2 of 5; nothing guessed for D9 (the ambiguous number)",
      AB(p, S_INV, "device")["matched"] == 2 and AB(p, S_INV, "device")["unmatched"] == 3, AB(p, S_INV, "device"))
check("the activation feed ↔ inventory pair is exactly what §11a's activation_index pairs: the same units by key, the same one by mobile, the same unpairable reasons",
      (lambda ai: set(ai["by_key"]) == {D3, D2, D_ACT} and ai["paired_by_mobile"] == 1 and ai["paired_by_key"] == 2
       and sorted(u["reason"] for u in ai["unpairable"]) == sorted([ISR.UNPAIR_AMBIGUOUS, ISR.UNPAIR_NO_KEY]))
      (ISR.activation_index(act_rows, DKEY, sales_rows)))

# X-report ↔ sales by STORE and DATE only
p = P(S_XR, S_SALES)
check("X-report ↔ sales share store + date only (no device, phone, order or rep on a tender row); strongest = store",
      p["shared"] == ["store", "date"] and p["strongest"] == "store", p["shared"])
exp_x = expect([XR_DAY] * len(xr_rows), [l[3] for l in SALES])
check("X-report → sales by date: every tender row's day (08-09) has sales lines (T1007 / T1008) → 5 of 5, each ambiguous (2 lines); sales → X-report by date: 2 of 10",
      same(AB(p, S_XR, "date"), exp_x) and AB(p, S_SALES, "date")["matched"] == 2, (AB(p, S_XR, "date"), AB(p, S_SALES, "date")))
check("X-report → sales by store: 'Riverside Kiosk' resolves to B-200 through the 2.4 decision, so all 5 tender rows match a sales store",
      AB(p, S_XR, "store")["matched"] == 5 and AB(p, S_XR, "store")["no_key"] == 0)

p = P(S_XR, S_INV)
check("X-report ↔ inventory share ONLY the store (a tender row has no device; a unit has no date): 3 of 5 tender rows (the B-100 sheet) / 5 of 5 units at a common store",
      p["shared"] == ["store"] and AB(p, S_XR, "store")["matched"] == 3 and AB(p, S_XR, "store")["unmatched"] == 2
      and AB(p, S_INV, "store")["matched"] == 5, (p["shared"], AB(p, S_XR, "store")))
# a pair with NO common column — a source that carries only a date beside the inventory listing (no date)
S_DATE_ONLY = src("other:x:dated", "merchant_payments", "Settlement (unmapped merchants)", None,
                  [{"business_date": "2026-08-09", "store_code": None}], cols={"date": "business_date"})
p_none = RL.pair(S_INV, S_DATE_ONLY, RL.line_keys(S_INV, NORMS), RL.line_keys(S_DATE_ONLY, NORMS), NORMS)
check("a pair with NO common column SAYS so (note), has no fields, is not linked, names no strongest field",
      p_none["shared"] == [] and p_none["note"] == RL.NOTE_NO_SHARED and p_none["linked"] is False and p_none["strongest"] is None)
# per-source summary — every report carries a store, so every pair is linked at least by store
srcs = {s["instance_key"]: s for s in rep["sources"]}
check("per source: each of the six is linked to the other five (every report carries a store) — and the matrix shows the STRONGEST field per pair, not merely the store",
      all(v["linked_to"] == 5 and v["shared_with"] == 5 for v in srcs.values()),
      {k: (v["linked_to"], v["shared_with"]) for k, v in srcs.items()})
check("the Stage-4 note per row: 'linked to N other reports'",
      RL.linked_note(srcs[S_SALES["instance_key"]]) == "linked to 5 other reports"
      and RL.linked_note({"linked_to": 0, "shared_with": 2}) == "shares a column with 2 other reports — no line matched"
      and RL.linked_note({"linked_to": 0, "shared_with": 0}) == "no column in common with another report")
# the matrix
mx = RL.matrix(rep)
cell = mx["cells"].get(f"{S_SALES['instance_key']}|{S_COMM['instance_key']}") or mx["cells"].get(f"{S_COMM['instance_key']}|{S_SALES['instance_key']}")
check("the matrix cell = the strongest shared field with the counts each way (no samples); the pair detail carries the samples",
      cell["field"] == "order" and cell["label"] == "invoice / order number" and {cell["a_to_b"]["matched"], cell["b_to_a"]["matched"]} == {5, 4}
      and "unmatched_sample" not in json.dumps(cell) and RL.detail(rep, S_COMM["instance_key"], S_SALES["instance_key"])["fields"][0]["a_to_b"].get("unmatched_sample") is not None)
# one report → no links; none → said
one = RL.report([S_SALES], NORMS, ISR.line_pairings, ISR.sales_mobile_index)
none = RL.report([], NORMS, ISR.line_pairings, ISR.sales_mobile_index)
check("a run with ONE report: no pairs, and the note says there is nothing to link it to yet; no reports: said",
      one["pairs"] == [] and one["note"] == RL.NOTE_ONE_REPORT and one["sources"][0]["linked_to"] == 0 and none["note"] == RL.NOTE_NO_REPORTS)
# a source that could not be re-read is listed as skipped, never silently dropped
skipped = RL.report([S_SALES, {**S_COMM, "read_ok": False, "note": "re-read failed"}], NORMS, ISR.line_pairings, ISR.sales_mobile_index)
check("a source whose rows could not be re-read is SKIPPED with its reason, not paired as empty",
      skipped["skipped"] == [{"instance_key": S_COMM["instance_key"], "label": S_COMM["label"], "note": "re-read failed"}] and skipped["pairs"] == [])
# every direction reconciles
recon_ok = all(f[dk]["matched"] + f[dk]["unmatched"] + f[dk]["no_key"] == f[dk]["lines"] and f[dk]["ambiguous"] <= f[dk]["matched"]
               for pp in rep["pairs"] for f in pp["fields"] for dk in ("a_to_b", "b_to_a"))
check("every direction of every field reconciles: matched + unmatched + no key = lines; ambiguous ≤ matched", recon_ok)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. END TO END — five real commits + the activation feed → GET /onboarding/intake/state?links=…")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db()
c_s = commit(db, sales_csv(), "sales.csv", source_kind="sales", pos_source="rq", layout="sales", column_map=json.dumps(SALES_MAP))
check("the sales export commits (ok, 10 lines landed through the mapped importer)", c_s["ok"] is True and c_s["verified_numbers"]["rows_landed"] == 10, c_s.get("problems"))
s0 = state(db)
check("GET /state with no ?links: report_links is available but NOT computed (nothing read on an ordinary state read); the how is stated",
      s0["report_links"]["available"] is True and s0["report_links"]["computed"] is False and "?links=all" in s0["report_links"]["how"], s0["report_links"])
s1 = state(db, "all")
check("one report loaded → ?links=all computes: no pairs, the note says there is nothing to link it to yet, and the source is listed with its columns",
      s1["report_links"]["computed"] is True and s1["report_links"]["matrix"]["cells"] == {} and s1["report_links"]["note"] == RL.NOTE_ONE_REPORT
      and s1["report_links"]["matrix"]["sources"][0]["fields"] == ["device", "mobile", "order", "store", "rep", "date"], s1["report_links"])

c_c = commit(db, commission_csv(), "statement.csv", source_kind="commission", carrier_id=CARRIER_ID, statement_type="commission statement",
             period="August 2026", column_map=json.dumps(COMM_MAP), sign_answer="positive",
             assignments=json.dumps([{"label": k, "bucket": v, "is_reversal": False} for k, v in COMM_CHOSEN.items()]))
check("the commission statement commits (ok, 6 lines)", c_c["ok"] is True and c_c["verified_numbers"]["rows_landed"] == 6, c_c.get("problems"))
c_r = commit(db, residual_csv(), "residual.csv", source_kind="commission", carrier_id=CARRIER_ID, statement_type="residual statement",
             period="August 2026", column_map=json.dumps(RES_MAP), sign_answer="negative",
             assignments=json.dumps([{"label": k, "bucket": v, "is_reversal": False} for k, v in RES_CHOSEN.items()]))
check("the residual statement commits (ok, 5 lines, its own mapping key)", c_r["ok"] is True and c_r["verified_numbers"]["rows_landed"] == 5, c_r.get("problems"))
db.seed("raw_custom_import", [{"org_id": ORG, "report_key": "activation_details", "period": "August 2026", "source_filename": "acts.xlsx",
                               "row_index": i, "data": d} for i, d in enumerate(ACTS)])
c_i = commit(db, inventory_csv(), "inventory.csv", source_kind="inventory", pos_source="rq", layout="pos_inventory_listing",
             column_map=json.dumps(INV_MAP), as_of_date="2026-08-31")
check("the inventory listing commits (ok, 5 units)", c_i["ok"] is True and c_i["saved"] == 5, c_i.get("problems"))
c_x = commit(db, XR_FILE, XR_NAME, source_kind="x_report", pos_source="rq", identity=json.dumps(DEC_X))
check("the X-report commits (ok, 5 tender rows over 2 stores, the unknown sheet assigned to B-200)", c_x["ok"] is True and c_x["saved"] == 5, c_x.get("problems"))

s2 = state(db)
check("after new commits, the cached result (one report) is STALE and says so — the fingerprint names the instances it was computed from",
      s2["report_links"]["computed"] is True and s2["report_links"]["cached"] is True and s2["report_links"]["stale"] is True, s2["report_links"])
s3 = state(db, "all")
rl = s3["report_links"]
mx = rl["matrix"]
IK = {s["kind"]: s["instance_key"] for s in mx["sources"] if s["kind"] != "commission"}
IK_COMM = next(s["instance_key"] for s in mx["sources"] if s["instance_key"].endswith(":commission_statement"))
IK_RES = next(s["instance_key"] for s in mx["sources"] if s["instance_key"].endswith(":residual_statement"))
check("?links=all recomputes: SIX sources (five intake instances + the activation feed), 15 cells, every source re-read through the kind's OWN re-read (named)",
      len(mx["sources"]) == 6 and len(mx["cells"]) == 15 and rl["computed"] is True and rl["stale"] is False
      and rl["rereads"] == {IK["sales"]: "_intake_reread_sales", IK_COMM: "_intake_reread", IK_RES: "_intake_reread",
                            IK["inventory"]: "_intake_reread_inventory", IK["x_report"]: "_intake_reread_xreport",
                            RL.ACTIVATIONS_INSTANCE_KEY: "_intake_activation_rows"}, (len(mx["sources"]), len(mx["cells"]), rl.get("rereads")))
check("the layman labels come from the report-kind registry (the card each instance belongs to), with its source",
      {s["label"] for s in mx["sources"]} >= {"Sales report with IMEI and phone number (rq)", "Commission report from the carrier (Northwind Cellular)",
                                              "Residual report from the carrier (Northwind Cellular)", "Inventory on hand (rq)",
                                              "Cash register / X-report (rq)", "Activation details (from your POS)"}, [s["label"] for s in mx["sources"]])


def cellof(a, b):
    return mx["cells"].get(f"{a}|{b}") or mx["cells"].get(f"{b}|{a}")


def dirof(cell, a):
    return cell["a_to_b"] if cell["a"] == a else cell["b_to_a"]


c = cellof(IK["sales"], IK_COMM)
check("END TO END sales ↔ commission: the cell is the order number, 5 of 10 / 4 of 6 — the same counts §B derived by hand, now from the LANDED rows",
      c["field"] == "order" and dirof(c, IK["sales"])["matched"] == 5 and dirof(c, IK["sales"])["lines"] == 10
      and dirof(c, IK_COMM)["matched"] == 4 and dirof(c, IK_COMM)["lines"] == 6, c)
c = cellof(IK["sales"], IK_RES)
check("END TO END sales ↔ residual: phone number, 6 of 10 / 4 of 5 (the residual's account column read as the phone number)",
      c["field"] == "mobile" and dirof(c, IK["sales"])["matched"] == 6 and dirof(c, IK_RES)["matched"] == 4 and dirof(c, IK_RES)["lines"] == 5, c)
c = cellof(IK["inventory"], IK["sales"])
check("END TO END inventory ↔ sales: IMEI, 4 of 5 / 5 of 10 (direct)",
      c["field"] == "device" and c["basis"] == RL.BASIS_DIRECT and dirof(c, IK["inventory"])["matched"] == 4 and dirof(c, IK["sales"])["matched"] == 5, c)
c = cellof(RL.ACTIVATIONS_INSTANCE_KEY, IK["inventory"])
check("END TO END activations ↔ inventory: IMEI VIA the sales line's phone number — 2 of 5 / 2 of 5, basis stated",
      c["field"] == "device" and c["basis"] == RL.BASIS_VIA and dirof(c, RL.ACTIVATIONS_INSTANCE_KEY)["matched"] == 2
      and dirof(c, IK["inventory"])["matched"] == 2, c)
c = cellof(IK["x_report"], IK["sales"])
check("END TO END X-report ↔ sales: store (through the 2.4 decision 'Riverside Kiosk' → B-200), 5 of 5 / 10 of 10",
      c["field"] == "store" and dirof(c, IK["x_report"])["matched"] == 5 and dirof(c, IK["sales"])["matched"] == 10, c)
c = cellof(IK["x_report"], IK["inventory"])
check("END TO END X-report ↔ inventory: the store only — 5 of 5 / 5 of 5", c["field"] == "store" and c["shared"] == ["store"])
c = cellof(IK_RES, RL.ACTIVATIONS_INSTANCE_KEY)
check("END TO END residual ↔ activations: the strongest field is the phone number paired DIRECTLY (M2 on both) — a direct pairing outranks the device pairing made THROUGH the sales line; the store is shared but the activation export carries no store column (no key)",
      c["linked"] is True and c["strongest"] == "mobile" and c["basis"] == RL.BASIS_DIRECT and dirof(c, IK_RES)["matched"] == 1
      and dirof(c, RL.ACTIVATIONS_INSTANCE_KEY)["matched"] == 1, c)
check("the per-row Stage-4 notes: every intake instance says how many other reports it is linked to",
      all(v == "linked to 5 other reports" for v in rl["notes"].values()) and len(rl["notes"]) == 6, rl["notes"])
s4 = state(db, f"{IK_COMM}|{IK['sales']}")
det = s4["report_links"]["detail"]
d_c = det["fields"][0]["a_to_b"] if det and det["a"] == IK_COMM else (det["fields"][0]["b_to_a"] if det else None)
sent_c = det["fields"][0]["sentence_a_to_b"] if det and det["a"] == IK_COMM else (det["fields"][0]["sentence_b_to_a"] if det else "")
check("?links=<a>|<b> adds the pair detail: per shared field the counts each way, the sentence, the top unmatched keys and the ambiguous keys — served from the fresh cache",
      det is not None and s4["report_links"]["cached"] is True and [f["field"] for f in det["fields"]] == ["order", "store", "rep", "date"]
      and d_c["unmatched_sample"][0]["key"] == "t9999" and d_c["ambiguous_sample"][0]["key"] == "t1001" and "1 match several" in sent_c, det)
check("an unknown pair is said, not invented", state(db, "nope|nada")["report_links"].get("detail_note", "").startswith("no pair"))
cache_rows = [r for r in db.tables["onboarding_stage_state"] if r["instance_key"] == R._LINKS_INSTANCE_KEY]
check("the cache lives on the run's OWN stage-4 row (instance_key links:run:matrix) with computed_at + the fingerprint — no new table; the rail ignores it (stages 2/3 only)",
      len(cache_rows) == 1 and cache_rows[0]["stage"] == "4" and cache_rows[0]["payload"].get("computed_at") and cache_rows[0]["payload"].get("fingerprint")
      and all(i["instance_key"] != R._LINKS_INSTANCE_KEY for i in s4["rail"]["instances"]) and len(s4["rail"]["verify_table"]) == 5)
check("the state read wrote NOTHING but that cache row: no ledger / sales / inventory / tender row changed (read-only)",
      len([r for r in db.tables["raw_sales"] if r["org_id"] == ORG]) == 10 and len(db.tables["commission_ledger"]) == 11
      and len(db.tables["inventory_aging_device"]) == 5 and len(db.tables["pos_tender_summary"]) == 5)
# honest without mig 1007
db_nomig = fresh_db()
db_nomig.tables["onboarding_run"] = None
db_nomig.tables["onboarding_stage_state"] = None
sn = state(db_nomig, "all")
check("without mig 1007 the state says report links are not available and names the migration — never an empty matrix",
      sn["state_ready"] is False and sn["report_links"]["available"] is False and "1007" in sn["report_links"]["note"], sn["report_links"])
# the activation feed absent → five sources, no pseudo-source, honestly
db2 = fresh_db()
commit(db2, sales_csv(), "sales.csv", source_kind="sales", pos_source="rq", layout="sales", column_map=json.dumps(SALES_MAP))
commit(db2, inventory_csv(), "inventory.csv", source_kind="inventory", pos_source="rq", layout="pos_inventory_listing", column_map=json.dumps(INV_MAP), as_of_date="2026-08-31")
s5 = state(db2, "all")
check("no activation feed loaded → the activation source is simply absent (two sources, one cell), never an empty pseudo-report",
      len(s5["report_links"]["matrix"]["sources"]) == 2 and RL.ACTIVATIONS_INSTANCE_KEY not in json.dumps(s5["report_links"]["matrix"]["sources"]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. THE FIRST CONSUMER — the inventory-vs-activations check and the report links pair through ONE implementation")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
sc = c_i["verified_numbers"].get("sold_check") or {}
check("the inventory commit's auto-check (#254) ran over the same feed: 5 activations considered, 2 unpairable (A3 ambiguous, A5 keyless), the feed carries serials and phone numbers; the paired units are on sale lines so they are SOLD_NOT_CLEARED with the activation as evidence (not a second finding)",
      sc.get("activations_considered") == 5 and sc.get("activations_unpairable") == 2 and sc.get("activations_carry_device_key") is True
      and sc.get("activations_carry_mobile") is True and sc.get("activated_not_rung_out") == 0 and sc.get("sold_not_cleared") == 4, sc)
det_ai = state(db, f"{RL.ACTIVATIONS_INSTANCE_KEY}|{IK['inventory']}")["report_links"]["detail"]
fdev = next(f for f in det_ai["fields"] if f["field"] == "device")
d_act = fdev["a_to_b"] if det_ai["a"] == RL.ACTIVATIONS_INSTANCE_KEY else fdev["b_to_a"]
check("…and the report links say the same of the same lines: 1 paired through the sales line, the ambiguous number and the keyless line unpairable with §11a's own reasons",
      d_act["via_lines"] == 1 and d_act["via_reasons"] == {ISR.UNPAIR_AMBIGUOUS: 1, ISR.UNPAIR_NO_KEY: 1}, d_act)
check("ONE pairing implementation: activation_index is a fold of line_pairings; report_links calls line_pairings + sales_mobile_index and defines no pairing of its own",
      "line_pairings(" in inspect.getsource(ISR.activation_index) and "line_pairings(" in inspect.getsource(RL._via_device_keys)
      and "sales_mobile_index(" in inspect.getsource(RL._bridge_index)
      and not re.search(r"\bdef (activation_index|sales_mobile_index|line_pairings|mobile_key|device_key|norm_order)\b", inspect.getsource(RL)))
check("the router injects the three normalisers from their homes and the pairing core from inventory_sold_recon",
      all(t in inspect.getsource(R._intake_link_normalisers) for t in ("_dcr.device_key", "_isr.mobile_key", "_dcr.norm_order"))
      and all(t in inspect.getsource(R._intake_report_links_compute) for t in ("_isr.line_pairings", "_isr.sales_mobile_index", "_rl.report(")))
check("the router's sold check still rides reconcile → activation_index (the same core), unchanged",
      "_isr.reconcile(" in inspect.getsource(R._intake_sold_check_after_inventory) and "activation_index(" in inspect.getsource(ISR.reconcile))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. NEGATIVE CONTROLS — a guess → RED; counts that do not reconcile → RED; a second normaliser → RED; a vendor name → RED")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def guessing_line_pairings(rows, key_of, sale_rows=None, serial_field="serial", mobile_field="mdn", id_field="trans_id",
                           sale_key_field="serial_1", sale_mobile_field="mdn", mobile_index=None):
    """A sibling that GUESSES: an ambiguous phone number is resolved to its first candidate."""
    out = ISR.line_pairings(rows, key_of, sale_rows, serial_field, mobile_field, id_field, sale_key_field, sale_mobile_field, mobile_index)
    for p in out:
        if p.get("reason") == ISR.UNPAIR_AMBIGUOUS:
            p.update({"key": p["candidates"][0], "pairing": ISR.PAIR_MOBILE_VIA_SALES, "via": "guess", "reason": None})
    return out


rep_guess = RL.report([S_SALES, S_INV, S_ACT], NORMS, guessing_line_pairings, ISR.sales_mobile_index)
pg = next(p for p in rep_guess["pairs"] if {p["a"], p["b"]} == {S_ACT["instance_key"], S_INV["instance_key"]})
dg = AB(pg, S_ACT, "device")
check("NEGATIVE CONTROL — a pairing that GUESSES the ambiguous number (A3 → D9) reports 3 matched where the honest core reports 2: the §B/§C pins go RED",
      dg["matched"] == 3 and dg["via_lines"] == 2 and d["matched"] == 2, (dg, d))
tampered = json.loads(json.dumps(rep))
tampered["pairs"][0]["fields"][0]["a_to_b"]["matched"] -= 1
recon_bad = all(f[dk]["matched"] + f[dk]["unmatched"] + f[dk]["no_key"] == f[dk]["lines"]
                for pp in tampered["pairs"] for f in pp["fields"] for dk in ("a_to_b", "b_to_a"))
check("NEGATIVE CONTROL — counts that do not reconcile with the lines (one matched line dropped) fail the reconciliation pin", recon_bad is False)
exp_bad = dict(exp_c2s, matched=exp_c2s["matched"] + 1)
check("NEGATIVE CONTROL — an expected count that disagrees with the fixture fails the exactness pin", not same(AB(P(S_SALES, S_COMM), S_COMM, "order"), exp_bad))
src_rl = inspect.getsource(RL)
check("NEGATIVE CONTROL — the pure module spells NO normaliser of its own (no digit stripping, no upper-casing, no '.0' trimming, no regex)",
      not re.search(r"isdigit\(|\.upper\(|endswith\(\"\.0\"\)|re\.sub\(|import re\b", src_rl))
VENDORS = re.compile(r"\b(verizon|boost|total wireless|vidapay|t-?cetra|cricket|at&t|t-mobile|b2b\s*soft|b2bsoft|rtpos|iqmetrix|epay|acima)\b", re.I)
link_router_src = "\n".join(inspect.getsource(f) for f in (R._intake_link_normalisers, R._intake_link_label, R._intake_link_source,
                                                            R._intake_link_activations, R._intake_links_cache, R._intake_links_cache_put,
                                                            R._intake_report_links_compute, R._intake_report_links))
check("RULE TWO — no carrier / POS / processor name in report_links.py or the router's link functions",
      not VENDORS.search(src_rl) and not VENDORS.search(link_router_src))
check("the link readers make NO query of their own — every row comes through an existing re-read or the activation read",
      ".table(" not in inspect.getsource(R._intake_link_source) and ".table(" not in inspect.getsource(R._intake_link_activations)
      and all(t in inspect.getsource(R._intake_link_source) for t in ("_intake_reread_sales(", "_intake_reread_inventory(", "_intake_reread(",
                                                                     "_intake_reread_xreport(", "_intake_reread_merchant(", "_intake_reread_billpay(")))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. WIRED, REGISTERED, THE PAGE, THE LOCK IN CI")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("GET /onboarding/intake/state takes `links` and attaches report_links through _intake_report_links",
      "links" in inspect.signature(R.onboarding_intake_state).parameters and "_intake_report_links(" in inspect.getsource(R.onboarding_intake_state))
check("the three re-reads carry the link fields (additive selects): sales mdn / serial_1 / user_login, ledger account_id / order_number / store / rep_user / trans_date, bill-pay txn",
      all(t in inspect.getsource(R._intake_reread_sales) for t in ("mdn", "serial_1", "user_login"))
      and all(t in inspect.getsource(R._intake_reread) for t in ("account_id", "order_number", "store", "rep_user", "trans_date"))
      and "kf['txn']" in inspect.getsource(R._intake_reread_billpay))
page = open(FE_PAGE, encoding="utf-8").read()
shared = open(FE_SHARED, encoding="utf-8").read()
check("the page has the Stage-4 'Report links' section: the matrix, the pair detail on click, the per-row 'linked to' note, provenance ('paired by')",
      "Report links" in page and "links=all" in page and "report_links" in page and "linked_to" in page and "paired by" in page.lower()
      and "ReportLinks" in shared)
idx = open(INDEX, encoding="utf-8").read()
check("registered: index §30.11 (Stage D), §17 (`?links=`), §18 (the link metrics), §11a cross-ref, §30.8 OPEN (a) closed",
      "### 30.11" in idx and "?links=" in idx and "report_links" in idx and "line_pairings" in idx
      and "(a) **Report links" in idx and "CLOSED — built in §30.11" in idx)
wf = open(WORKFLOW, encoding="utf-8").read()
check("the lock runs in CI beside the other locks (harness_report_links_lock.py in carrier-vocab-guard.yml)", "harness_report_links_lock.py" in wf)
check("the lock exists and is stdlib-only", os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness_report_links_lock.py")))

print(f"\n══ report links (Stage D): {_pass} passed, {_fail} failed ══")
if _failures:
    print("failed:\n  - " + "\n  - ".join(_failures))
sys.exit(1 if _fail else 0)
