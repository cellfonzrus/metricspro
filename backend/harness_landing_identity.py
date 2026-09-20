"""PROOF — landing identity: which report kind wrote a row, what a landing may replace, who reads it,
where an upload shows up, and what a wrong file is told (owner 2026-09-20). DB-free, stdlib + pandas.

THE DEFECT, REPRODUCED FIRST (§B): two report kinds land in ONE table, same store × same date span. Before
this design the second landing's slice replace (store × dates) deleted the first's 48,875 rows; here the
second landing is REFUSED naming the loss in plain words, the first's rows survive, and only an explicit
confirmation replaces them — with the loss recorded. Then the design fix proper (§C): the by-product layout
lands in its OWN table and the line-level table is untouched.

  §A the pure rules (KIND_STAMP, kind_of_row, partition_slice, the refusal sentence, apply_kind_filter,
     blank_consumer_fields, shows_in / feeds_for_table / where_to_upload over the REAL mig-1010 seed
     mirror, looks_like on the owner's tender-summary header)
  §B THE REGRESSION over the REAL router._ingest_mapped_df with an in-memory client
  §C the design fix: separate tables; NULL reads as the default kind; the kind-scoped re-read
  §D a landing nobody can read is refused BEFORE a row is written
  §E a landing to a table the migration has not created is refused naming the file; /upload-mapped
     refuses a contradicting target_table
  §F the endpoints: GET /report-kinds carries shows_in + where + consumers; the wrong-file sentence
  §G registration, RULE TWO, the migration is written and NOT applied, the CI wiring

  python3 backend/harness_landing_identity.py
"""
import asyncio
import io
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SUPABASE_URL", "http://fake")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")

import pandas as pd

from app.modules.commcalc import landing_identity as LI
from app.modules.commcalc import column_mapping as CM
from app.modules.commcalc import report_kinds as RK
from app.modules.commcalc import onboarding_intake as OI
from app.modules.commcalc import ingest_slice as IS
from app.modules.commcalc import router as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORG = "f4f1c16e-0000-4000-8000-000000000001"
HOUSE = RK.HOUSE_ORG
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:500]))


def section(t):
    print("\n" + "─" * 78 + "\n" + t + "\n" + "─" * 78)


# ══ an in-memory supabase client — enough of the query builder the landing path uses ══════════════
class _Q:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.rows, self.filters = "select", None, []
        self._order, self._limit, self._range, self.cols = None, None, None, "*"

    def select(self, cols="*", **kw):
        self.op, self.cols = "select", cols
        for c in [c.strip() for c in str(cols).split(",") if c.strip() and c.strip() != "*"]:
            if c not in self.db.columns(self.table):
                raise RuntimeError(f'42703 column "{c}" of {self.table} does not exist')
        return self

    def insert(self, rows):
        self.op, self.rows = "insert", (rows if isinstance(rows, list) else [rows]); return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.rows = "upsert", (rows if isinstance(rows, list) else [rows]); return self

    def update(self, row, count=None):
        self.op, self.rows = "update", [row]; return self

    def delete(self):
        self.op = "delete"; return self

    def eq(self, k, v):    self.filters.append(("eq", k, v)); return self
    def neq(self, k, v):   self.filters.append(("neq", k, v)); return self
    def in_(self, k, v):   self.filters.append(("in", k, list(v))); return self
    def is_(self, k, v):   self.filters.append(("is", k, v)); return self
    def gte(self, k, v):   self.filters.append(("gte", k, v)); return self
    def lte(self, k, v):   self.filters.append(("lte", k, v)); return self
    def lt(self, k, v):    self.filters.append(("lt", k, v)); return self
    def ilike(self, k, v): self.filters.append(("ilike", k, v)); return self
    def or_(self, expr):   self.filters.append(("or", expr, None)); return self
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
            if op == "or" and not self._or(r, k): return False
        return True

    @staticmethod
    def _or(r, expr):
        # 'col.is.null,col.not.in.(a,b),col.eq.x' — PostgREST's or= grammar, the subset apply_kind_filter emits
        for part in re.findall(r"[a-z_]+\.(?:is\.null|not\.in\.\([^)]*\)|eq\.[^,]+)", expr):
            col = part.split(".")[0]
            x = r.get(col)
            if part.endswith(".is.null") and x is None:
                return True
            m = re.match(r"^[a-z_]+\.not\.in\.\((.*)\)$", part)
            if m and x is not None and str(x) not in m.group(1).split(","):
                return True
            m = re.match(r"^[a-z_]+\.eq\.(.*)$", part)
            if m and x is not None and str(x) == m.group(1):
                return True
        return False

    def execute(self):
        t = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            rows = [r for r in t if self._match(r)]
            if self._order:
                k, desc = self._order
                rows.sort(key=lambda r: str(r.get(k) or ""), reverse=desc)
            if self._range:
                lo, hi = self._range
                rows = rows[lo:hi + 1]
            elif self._limit:
                rows = rows[:self._limit]
            if self.cols != "*":
                cs = [c.strip() for c in str(self.cols).split(",")]
                rows = [{c: r.get(c) for c in cs} for r in rows]
            return _Res([dict(r) for r in rows], count=len(rows))
        if self.op in ("insert", "upsert"):
            self.db.check_rows(self.table, self.rows)
            out = []
            for r in self.rows:
                row = dict(r); row.setdefault("id", str(uuid.uuid4()))
                t.append(row); out.append(row)
            return _Res(out)
        if self.op == "delete":
            keep = [r for r in t if not self._match(r)]
            gone = [r for r in t if self._match(r)]
            self.db.tables[self.table] = keep
            return _Res(gone)
        if self.op == "update":
            n = 0
            for r in t:
                if self._match(r):
                    r.update(self.rows[0]); n += 1
            return _Res([], count=n)
        raise RuntimeError(self.op)


class _Res:
    def __init__(self, data, count=None): self.data, self.count = data, count


RAW_SALES_COLS = ["id", "org_id", "period", "period_month", "period_year", "store", "salesperson", "user_login",
                  "department", "category", "product_desc", "product_id", "gp", "ext_price", "trans_id",
                  "trans_date", "contract_type", "mdn", "serial_1", "register", "tender_type", "voided",
                  "trans_type", "sku", "customer", "email", "customer_no", "quantity", "total_cost",
                  "pricing_discounts", "contract_no", "source", "created_at"]


class FakeDB:
    def __init__(self, product_table=True):
        self.tables = {}
        self.declared = {"raw_sales": RAW_SALES_COLS, "upload_log": ["id", "org_id", "file_type", "period", "filename", "rows_saved"]}
        if product_table:
            self.declared["raw_sales_product"] = RAW_SALES_COLS
        else:
            self.tables["raw_sales_product"] = None      # 42P01: the table does not exist (mig 1011 not applied)

    def columns(self, table):
        if table in self.declared:
            return self.declared[table]
        if self.tables.get(table, []) is None:
            raise RuntimeError(f'42P01 relation "{table}" does not exist')
        return ["*"] + [k for r in (self.tables.get(table) or []) for k in r]

    def check_rows(self, table, rows):
        if self.tables.get(table, []) is None:
            raise RuntimeError(f'42P01 relation "{table}" does not exist')
        if table in self.declared:
            for r in rows:
                bad = [k for k in r if k not in self.declared[table]]
                if bad:
                    raise RuntimeError(f'42703 column "{bad[0]}" of {table} does not exist')

    def schema(self, _n): return self
    def table(self, name):
        if self.tables.get(name, []) is None and name != "raw_sales_product":
            raise RuntimeError(f'42P01 relation "{name}" does not exist')
        return _Q(self, name)

    def seed(self, table, rows):
        for r in rows:
            row = dict(r); row.setdefault("id", str(uuid.uuid4()))
            self.tables.setdefault(table, []).append(row)


def use(db):
    R.sb = lambda: db
    R.get_supabase = lambda: db
    R._TABLE_COL_PRESENT.clear()
    return db


def http_error(fn, *a, **kw):
    try:
        return None, fn(*a, **kw)
    except R.HTTPException as e:
        return e.status_code, e.detail


# ══ fixtures: a line-level export (IMEI + phone, department / category) and a by-product aggregate ══
STORE = "1 Main St"
LINE_HDR = ["Store", "Salesperson", "Trans ID", "Trans Date Time", "Department", "Category", "Product Desc", "Ext Price", "GP",
            "Serial 1", "Activated Mobile Number", "Voided"]
PROD_HDR = ["Invoice #", "Invoiced At", "Sold By", "Sold On", "Product SKU", "Product Name", "Total Price", "Total Cost", "Gross Profit"]


def line_df(n=48, lo_day=2, blank_class=False):
    rows = []
    for i in range(n):
        d = lo_day + (i % 20)
        rows.append([STORE, "bob.s", f"T{1000 + i}", f"2026-08-{d:02d} 10:{i % 60:02d}:00",
                     "" if blank_class else ("RTR" if i % 4 == 0 else "Accessories"),
                     "" if blank_class else ("RTR >> Bill Payments" if i % 4 == 0 else "Accessories >> Cases"),
                     "" if blank_class else f"Item {i}", f"{10 + i:.2f}", f"{3 + i * 0.5:.2f}",
                     f"35000000000{i:04d}", f"55512{i:05d}", "No"])
    return pd.DataFrame(rows, columns=LINE_HDR)


def prod_df(n=12, lo_day=2):
    rows = []
    for i in range(n):
        d = lo_day + (i % 20)
        rows.append([f"P{2000 + i}", STORE, "bob.s", f"08/{d:02d}/2026 09:00:00", f"SKU-{i}", f"Product {i}", f"{20 + i:.2f}", f"{12 + i:.2f}", f"{8:.2f}"])
    return pd.DataFrame(rows, columns=PROD_HDR)


def land(db, report_key, table, df, **kw):
    use(db)
    rules = CM.default_mapping(report_key)
    return R._ingest_mapped_df(ORG, report_key, table, rules, df, period="", fname="f.xlsx", trace_source="onboarding-intake", **kw)


def rows_of(db, table, org=ORG):
    return [r for r in (db.tables.get(table) or []) if r["org_id"] == org]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE PURE RULES — one home (landing_identity), dereferenced")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MIRROR = RK.house_mirror()
TWO_KINDS = {**CM.TABLE_MAP, "pos_product_sales": "raw_sales"}          # the PRE-FIX world: both layouts in one table
check("KIND_STAMP: raw_sales and raw_sales_product stamp `source`; defaults are the line-level and product layouts",
      LI.stamp_column("raw_sales") == "source" and LI.default_kind("raw_sales") == "sales"
      and LI.stamp_column("raw_sales_product") == "source" and LI.default_kind("raw_sales_product") == "pos_product_sales"
      and LI.stamp_column("commission_ledger") is None)
check("kind_of_row: NULL, the mig-727 writer word, an unknown value and 'sales' all read as the default kind; another layout's key reads as that kind",
      LI.kind_of_row("raw_sales", {"source": None}, TWO_KINDS) == "sales"
      and LI.kind_of_row("raw_sales", {"source": "pos_builtin"}, TWO_KINDS) == "sales"
      and LI.kind_of_row("raw_sales", {"source": "onboarding-intake"}, TWO_KINDS) == "sales"
      and LI.kind_of_row("raw_sales", {"source": "sales"}, TWO_KINDS) == "sales"
      and LI.kind_of_row("raw_sales", {"source": "pos_product_sales"}, TWO_KINDS) == "pos_product_sales"
      and LI.kind_of_row("raw_sales", {"source": "pos_product_sales"}, CM.TABLE_MAP) == "sales")   # not a layout of raw_sales in the FIXED map → default
rows = [{"source": None}, {"source": "sales"}, {"source": "pos_product_sales"}, {"source": "pos_product_sales"}, {"source": "pos_builtin"}]
part = LI.partition_slice("raw_sales", rows, "sales", TWO_KINDS)
check("partition_slice: this kind's rows (incl. NULL and provenance words) vs the other kind's, counted per kind",
      len(part["mine"]) == 3 and part["others_by_kind"] == {"pos_product_sales": 2})
part2 = LI.partition_slice("raw_sales", rows, "pos_product_sales", TWO_KINDS)
check("…and from the other kind's point of view the NULL rows are NOT its own (they belong to the default kind)",
      len(part2["mine"]) == 2 and part2["others_by_kind"] == {"sales": 3})
scope = {"partition_col": "store", "values": [STORE], "lo": "2024-01-02", "hi": "2026-08-31"}
msg = LI.cross_kind_refusal("raw_sales", "pos_product_sales", 10823, {"sales": 48875}, scope, MIRROR)
check("the refusal names the loss in plain words — counts, layman labels, table, slice, and how to proceed",
      "48,875 rows of 'Sales report with IMEI and phone number'" in msg and "10,823 rows of 'Sales report with cost and selling price'" in msg
      and "commcalc.raw_sales" in msg and "1 store value(s) between 2024-01-02 and 2026-08-31" in msg and "replace_other_kinds" in msg, msg)
check("stamp: every row bound for a stamped table carries the kind; an unstamped table is left alone",
      LI.stamp([{"a": 1}], "raw_sales", "sales") == "source" and LI.stamp([{"a": 1}], "commission_ledger", "x") is None)


class _Rec:
    def __init__(self): self.calls = []
    def eq(self, k, v): self.calls.append(("eq", k, v)); return self
    def or_(self, e): self.calls.append(("or", e)); return self


q = _Rec(); LI.apply_kind_filter(q, "raw_sales", "sales", CM.TABLE_MAP)
check("apply_kind_filter is a NO-OP while one layout targets the table (the legacy writers are byte-identical today)", q.calls == [])
q = _Rec(); LI.apply_kind_filter(q, "raw_sales", "pos_product_sales", TWO_KINDS)
q2 = _Rec(); LI.apply_kind_filter(q2, "raw_sales", "sales", TWO_KINDS)
check("…with two kinds: another kind filters to its stamped rows; the DEFAULT kind owns NULL and every non-kind value (PostgREST or=)",
      q.calls == [("eq", "source", "pos_product_sales")] and q2.calls == [("or", "source.is.null,source.not.in.(pos_product_sales)")], (q.calls, q2.calls))
check("multi_kind_tables is DERIVED from TABLE_MAP: the ledger (two statement types) today; raw_sales only in the pre-fix map",
      LI.multi_kind_tables(CM.TABLE_MAP) == ["commission_ledger"] and "raw_sales" in LI.multi_kind_tables(TWO_KINDS))

blank = LI.blank_consumer_fields([{"ext_price": 1, "gp": 0.5, "department": "", "category": None, "product_desc": ""}] * 3, "raw_sales")
check("blank_consumer_fields: rows blank on EVERY field a gating reader classifies on name that reader and its fields (the Vzone July rows)",
      [(b["screen"], b["fields"]) for b in blank] == [("exec_mtd", ["department", "category", "product_desc"]), ("sales_report", ["contract_type", "department", "category", "product_desc"])], blank)
check("…one filled cell anywhere satisfies that reader; an empty frame is not a refusal",
      LI.blank_consumer_fields([{"department": "", "ext_price": 1, "gp": 1}, {"department": "RTR", "ext_price": 2, "gp": 1}], "raw_sales") == [] and LI.blank_consumer_fields([], "raw_sales") == [])
cr = LI.consumer_refusal(blank, "raw_sales", "pos_product_sales", MIRROR)
check("the consumer refusal says which reader needs which field and that nothing was written",
      "Executive MTD needs department / category / product_desc" in cr and "Nothing written to commcalc.raw_sales" in cr and "Sales report with cost and selling price" in cr, cr)

RT = R._TRACE_TARGET_TABLE
SKT = OI.SOURCE_KIND_TARGET
si = {r["key"]: LI.shows_in(r, CM.TABLE_MAP, RT, SKT) for r in MIRROR}
check("shows_in over the REAL mig-1010 seed mirror: the line-level card → raw_sales → Executive MTD / Sales Report / Gross Profit … (linked screens)",
      si["sales_imei_phone"]["table"] == "raw_sales" and [c["screen"] for c in si["sales_imei_phone"]["consumers"]][:3] == ["exec_mtd", "sales_report", "gp_report"])
check("…the by-product card → ITS OWN table → the onboarding verify only (no money report sums it — said in its why)",
      si["sales_cost_price"]["table"] == "raw_sales_product" and [c["screen"] for c in si["sales_cost_price"]["consumers"]] == ["onboarding_intake"]
      and "double-count" in si["sales_cost_price"]["consumers"][0]["why"])
check("…every card resolves a table or says it is recorded only: X-report → closing recon, commission → the ledger, inventory → the inventory readers, 'something else' → recorded",
      si["x_report"]["table"] == "pos_tender_summary" and si["x_report"]["consumers"][0]["screen"] == "closing_recon"
      and si["commission_statement"]["table"] == "commission_ledger" and si["inventory_on_hand"]["table"] == "inventory_aging_device"
      and si["something_else"]["table"] is None and "recorded" in si["something_else"]["note"]
      and si["payment_detail"]["table"] == "raw_payment_detail" and si["hotsheet"]["table"] is None)
check("every consumer screen key is a snake_case ScreenLink key (the lock pins each exists in SCREENS)",
      all(re.match(r"^[a-z0-9_]+$", k) for k in LI.screen_keys()) and {"exec_mtd", "upload_files", "onboarding_intake"} <= set(LI.screen_keys()))
feeds = LI.feeds_for_table("raw_sales", MIRROR, CM.TABLE_MAP, RT, SKT)
check("feeds_for_table('raw_sales') — the way BACK from the Executive MTD: the line-level card, uploaded under the intake; the by-product card is NOT a feed of it",
      [f["key"] for f in feeds] == ["sales_imei_phone"] and feeds[0]["where"]["screen"] == "onboarding_intake"
      and [f["key"] for f in LI.feeds_for_table("raw_sales_product", MIRROR, CM.TABLE_MAP, RT, SKT)] == ["sales_cost_price"], feeds)
check("where_to_upload: an intake landing → the intake; a legacy route → Upload Files with its tile; a custom sheet → Upload Files with its sheet",
      LI.where_to_upload({"landing": "sales"})["screen"] == "onboarding_intake"
      and LI.where_to_upload({"landing": "carrier_report", "upload_types": ["payment_detail"]}) == {"screen": "upload_files", "label": "Upload Files", "upload_types": ["payment_detail"]}
      and LI.where_to_upload({"landing": "custom_import", "custom_sheet_label": "Bill Payments"})["custom_sheet_label"] == "Bill Payments")
check("blank_fields_over: the fields blank on EVERY row; a value on one row clears the field",
      LI.blank_fields_over([{"department": "", "category": None}, {"department": "", "category": "x"}], ["department", "category"]) == ["department"])

# the owner's wrong file: a tender summary (X-report family) dropped on the daily_sales tile
TENDER_HDR = ["Adjustments", "AmEx", "Cash", "Check", "Credit", "Debit", "Discover", "Gift Card", "MasterCard", "Register", "Total", "Visa"]
found = LI.looks_like(TENDER_HDR, MIRROR)
sent = LI.looks_like_sentence(found)
check("looks_like on the owner's tender-summary header → the X-report card, and the sentence names the card and its page",
      found and found["candidates"][0]["key"] == "x_report" and sent.startswith("This looks like a 'Cash register / X-report'. Upload it under Onboarding — Commission Intake"), (found, sent))
check("a header nobody recognises → no sentence (the column list alone, never a guess)",
      LI.looks_like(["Foo", "Bar", "Baz"], MIRROR) is None and LI.looks_like_sentence(None) == "")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE REGRESSION — two kinds, one table, same store × dates: the second landing may not delete the first's rows")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_saved_map = dict(CM.TABLE_MAP)
CM.TABLE_MAP["pos_product_sales"] = "raw_sales"          # the pre-fix world, for this section only
try:
    db = FakeDB()
    r1 = land(db, "sales", "raw_sales", line_df())
    first = rows_of(db, "raw_sales")
    check("the line-level export lands (48 rows), every row STAMPED source='sales', the slice reported with its kind",
          r1["saved"] == 48 and len(first) == 48 and all(r["source"] == "sales" for r in first)
          and r1["replace_scope"]["kind"] == "sales" and r1["kind_scoped"] is True, r1.get("note"))
    s_, d_ = http_error(land, db, "pos_product_sales", "raw_sales", prod_df())
    # the aggregate spans Aug 2–13; 32 of the 48 line-level rows sit in that store × date slice
    check("THE DEFECT, REFUSED: the by-product aggregate for the same store × dates is NOT landed — 400 naming the 32 rows of the line-level kind in its slice it would replace with 12 product-level rows",
          s_ == 400 and "32 rows of 'Sales report with IMEI and phone number'" in str(d_) and "12 rows of 'Sales report with cost and selling price'" in str(d_)
          and "between 2026-08-02 and 2026-08-13" in str(d_), (s_, str(d_)[:240]))
    check("…and the 48 line-level rows SURVIVE, byte for byte", rows_of(db, "raw_sales") == first)
    tr = [t for t in db.tables.get("upload_trace", []) if t.get("org_id") == ORG]
    check("the refusal is TRACED (status error, the sentence as the note) — never a silent success",
          tr and tr[-1].get("status") == "error" and "would replace" in str(tr[-1].get("result") or tr[-1]), tr[-1] if tr else None)
    r2 = land(db, "sales", "raw_sales", line_df(n=48), )
    check("a re-upload of the SAME kind replaces only its own slice — still 48 rows, never doubled, no other kind touched",
          r2["saved"] == 48 and len(rows_of(db, "raw_sales")) == 48 and r2["others_in_slice"] == {})
    r3 = land(db, "pos_product_sales", "raw_sales", prod_df(), replace_other_kinds=True)
    now = rows_of(db, "raw_sales")
    check("CONFIRMED replacement: with replace_other_kinds the 32 in the slice are deleted on purpose, the 12 land beside the 16 outside it, and the note records the loss",
          r3["saved"] == 12 and len(now) == 28 and sum(1 for r in now if r["source"] == "pos_product_sales") == 12
          and sum(1 for r in now if r["source"] == "sales") == 16
          and r3["replaced_other_kinds"] is True and r3["others_in_slice"] == {"sales": 32} and "REPLACED (confirmed) 32 row(s) of 'sales'" in r3["note"], r3.get("note"))
    # pre-existing NULL-source rows (every row landed before this design) read as the DEFAULT kind
    db = FakeDB()
    db.seed("raw_sales", [{"org_id": ORG, "period": "August 2026", "period_month": 8, "period_year": 2026, "store": STORE, "salesperson": "old",
                           "trans_id": f"OLD-{i}", "trans_date": "2026-08-05", "ext_price": 1.0, "department": "RTR", "source": None} for i in range(5)])
    s_, d_ = http_error(land, db, "pos_product_sales", "raw_sales", prod_df())
    check("COMPATIBILITY: rows with NULL source read as the line-level (default) kind — a product-level landing over them is refused, they are not deleted",
          s_ == 400 and "5 rows of 'Sales report with IMEI and phone number'" in str(d_) and len(rows_of(db, "raw_sales")) == 5, (s_, str(d_)[:160]))
    r4 = land(db, "sales", "raw_sales", line_df(n=10))
    check("…and a line-level landing over them REPLACES them (the default kind owns NULL rows) — exactly as the slice replace did before",
          r4["saved"] == 10 and len(rows_of(db, "raw_sales")) == 10 and not any(r["trans_id"].startswith("OLD") for r in rows_of(db, "raw_sales")))
    # a row of the same store OUTSIDE the date span, of the other kind, is never in the slice at all
    db = FakeDB()
    land(db, "pos_product_sales", "raw_sales", prod_df(lo_day=25))          # Aug 25 → Sep 13
    s_, d_ = http_error(land, db, "sales", "raw_sales", line_df(n=5, lo_day=2))   # Aug 2 → Aug 6
    check("disjoint date spans never collide: the line-level landing proceeds beside the product rows", s_ is None and len(rows_of(db, "raw_sales")) == 17)
finally:
    CM.TABLE_MAP.clear(); CM.TABLE_MAP.update(_saved_map)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE DESIGN FIX — the product layout lands in ITS OWN table; the kind-scoped re-read")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("column_mapping.TABLE_MAP: pos_product_sales → raw_sales_product; SOURCE_KIND_TARGET dereferences it (pos → the product table, sales → raw_sales)",
      CM.TABLE_MAP["pos_product_sales"] == "raw_sales_product" and SKT["pos"] == "raw_sales_product" and SKT["sales"] == "raw_sales"
      and IS.INGEST_PARTITION["raw_sales_product"] == {"partition": "store", "date": "trans_date"})
db = FakeDB()
r1 = land(db, "sales", "raw_sales", line_df())
r5 = land(db, "pos_product_sales", "raw_sales_product", prod_df())
check("the same two files now land in two tables — 48 line-level rows in raw_sales UNTOUCHED, 12 product rows in raw_sales_product, each stamped",
      len(rows_of(db, "raw_sales")) == 48 and len(rows_of(db, "raw_sales_product")) == 12
      and all(r["source"] == "pos_product_sales" for r in rows_of(db, "raw_sales_product")) and r5["target_table"] == "raw_sales_product")
db.seed("raw_sales", [{"org_id": ORG, "period": "August 2026", "store": STORE, "trans_id": "X1", "trans_date": "2026-08-03", "ext_price": 9.0, "source": "some_other_kind"}])
rr = R._intake_reread_sales(db, ORG, [STORE], "2026-08-02", "2026-08-31", table="raw_sales", kind="sales")
check("the intake re-read: table + kind — 48 stamped rows + the NULL/foreign-provenance row (default kind) = 49; another table's kind is not read",
      len(rr) == 49 and len(R._intake_reread_sales(db, ORG, [STORE], "2026-08-02", "2026-08-31", table="raw_sales_product", kind="pos_product_sales")) == 12)
check("the line-level layout `sales` now carries the second POS shape's spellings (invoice / sold by / sold on / tracking # / SKU) — the owner's re-upload path",
      {s["target_field"]: s.get("suggested_source") for s in CM.suggest(PROD_HDR + ["Tracking #", "Category"], "sales")}["trans_id"] == "Invoice #"
      and any(f[0] == "sku" for f in CM.TARGET_FIELDS["sales"]) and any(f[0] == "quantity" for f in CM.TARGET_FIELDS["sales"]))
lay_pos = [l["report_key"] for l in OI.layouts_for_kind("pos", CM.TABLE_MAP)]
lay_sales = [l["report_key"] for l in OI.layouts_for_kind("sales", CM.TABLE_MAP)]
check("layouts per kind are derived from the map: pos → [pos_product_sales], sales → [sales]", lay_pos == ["pos_product_sales"] and lay_sales == ["sales"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. A LANDING NOBODY CAN READ IS REFUSED — before a row is written")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = FakeDB()
s_, d_ = http_error(land, db, "sales", "raw_sales", line_df(blank_class=True))
check("a line-level frame blank on department / category / product name on EVERY row → 400 naming the Executive MTD and the Sales Report and their fields; nothing inserted",
      s_ == 400 and "Executive MTD needs department / category / product_desc" in str(d_) and "Sales Report needs" in str(d_)
      and rows_of(db, "raw_sales") == [], (s_, str(d_)[:200]))
tr = [t for t in db.tables.get("upload_trace", []) if t.get("org_id") == ORG]
check("…traced as an error with the blank fields named", tr and tr[-1].get("status") == "error" and "blank_consumer_fields" in str(tr[-1]))
r6 = land(db, "sales", "raw_sales", line_df())
check("the same export WITH those columns lands (the gate is on 'all blank', never on 'some blank')", r6["saved"] == 48)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. PRE-MIGRATION HONESTY + the caller cannot re-point a layout")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = FakeDB(product_table=False)
s_, d_ = http_error(land, db, "pos_product_sales", "raw_sales_product", prod_df())
check("without mig 1011 a product landing is REFUSED naming the file — never redirected into raw_sales",
      s_ == 400 and "1011_raw_sales_product.sql" in str(d_) and "not landed anywhere else" in str(d_) and rows_of(db, "raw_sales") == [], (s_, str(d_)[:200]))
use(db)
s_, d_ = http_error(lambda: asyncio.run(R.upload_mapped(report_key="pos_product_sales", target_table="raw_sales", carrier_id="", period="", file=None, replace_other_kinds="", org_id=ORG)))
check("/upload-mapped refuses a target_table that contradicts TABLE_MAP (a caller cannot point the product layout back at raw_sales)",
      s_ == 400 and "contradicts" in str(d_), (s_, str(d_)[:160]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. THE ENDPOINTS — GET /report-kinds says where each kind shows; a wrong file is told its page")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = FakeDB()
db.seed("carrier", [{"org_id": ORG, "name": "Northwind Cellular", "code": "northwind", "is_default": True}])
use(db)
pl = R.report_kinds_endpoint(org_id=ORG)
check("every visible kind carries shows_in (table + linked consumers) and where (the page to upload it on); the payload carries the one consumers map",
      pl["kinds"] and all("shows_in" in k and "where" in k for k in pl["kinds"]) and "consumers" in pl and "raw_sales" in pl["consumers"]
      and next(k for k in pl["kinds"] if k["key"] == "sales_imei_phone")["shows_in"]["consumers"][0]["screen"] == "exec_mtd")
sent = R._looks_like_sentence(db, ORG, TENDER_HDR)
check("the wrong-file sentence through the router (registry + declaration + signatures): names the X-report card and the intake",
      sent.startswith("This looks like a 'Cash register / X-report'. Upload it under Onboarding — Commission Intake"), sent)
check("…and a header nobody recognises yields '' (the column list alone)", R._looks_like_sentence(db, ORG, ["Foo", "Bar"]) == "")
sl = open(os.path.join(ROOT, "frontend", "src", "components", "ScreenLink.tsx"), encoding="utf-8").read()
missing = [k for k in LI.screen_keys() if not re.search(r"^\s+%s:\s*\{" % re.escape(k), sl, re.M)]
check("every screen key the consumers map names is a ScreenLink SCREENS entry — 'shows in' is always a link (%d keys)" % len(LI.screen_keys()), not missing, missing)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. WIRING, RULE TWO, REGISTRATION, THE MIGRATION IS WRITTEN AND NOT APPLIED")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
import inspect
src_li = open(LI.__file__, encoding="utf-8").read()
src_imd = inspect.getsource(R._ingest_mapped_df)
check("_ingest_mapped_df: refuses a missing table, stamps, gates on consumers BEFORE the snapshot, partitions by kind, deletes by id, names the loss",
      all(t in src_imd for t in ("TABLE_MIGRATION", "_landing.stamp(", "_landing.blank_consumer_fields(", "_landing.partition_slice(", '.in_("id", ids', "cross_kind_refusal"))
      and src_imd.index("blank_consumer_fields(") < src_imd.index("_select_replace_slice("))
check("the intake commit lands through the same path with the 2.6 confirmation, re-reads by table + kind, and stamps shows_in on the verified numbers",
      "replace_other_kinds=bool(ctx.get(\"replace_other_kinds\"))" in inspect.getsource(R._intake_land)
      and "confirm_replace_other_kinds" in inspect.getsource(R.onboarding_intake_commit)
      and "table=ctx[\"target_table\"], kind=report_key" in inspect.getsource(R._intake_commit_stage2)
      and 'cross["shows_in"]' in inspect.getsource(R._intake_commit_stage2))
check("the legacy sales route and the promotion stamp the kind and scope their period replace through apply_kind_filter",
      "_landing.stamp(mapped, table, _upload_kind)" in inspect.getsource(R._upload_file_impl) and "_landing.apply_kind_filter(" in inspect.getsource(R._upload_file_impl)
      and "_landing.stamp(new_rows, 'raw_sales', _promo_kind)" in inspect.getsource(R._promote_feed_impl) and "_landing.apply_kind_filter(_pd" in inspect.getsource(R._promote_feed_impl))
check("the Executive MTD endpoint carries the way back (`landing`: needs, blank fields, feeds with where)",
      "'landing': _landing_ex" in inspect.getsource(R._exec_mtd) and "_landing.feeds_for_table(" in inspect.getsource(R._exec_mtd))
check("the runbook's monthly lines derive 'shows in' from the consumers map; its links are screen keys",
      "_li.consumers_for_table(" in inspect.getsource(OI.runbook) and '"screen": "sales_report"' in inspect.getsource(OI.runbook) and '"href"' not in inspect.getsource(OI.runbook))
VENDORS = re.compile(r"\b(boost|verizon|total wireless|vidapay|b2b ?soft|\brq\b|luxelink|vzone|cellfonz|wireless zone|iqmetrix)\b", re.I)
check("RULE TWO: no carrier, POS vendor or tenant name in landing_identity.py or the lock",
      not VENDORS.search(src_li) and not VENDORS.search(open(os.path.join(ROOT, "backend", "harness_landing_identity_lock.py"), encoding="utf-8").read()))
mig = os.path.join(ROOT, "database", "migrations", "1011_raw_sales_product.sql")
ms = open(mig, encoding="utf-8").read() if os.path.exists(mig) else ""
check("mig 1011 is written: creates raw_sales_product with `source`, re-points the 1004 report definitions, carries -- REVERT:, touches no raw_sales row",
      "CREATE TABLE IF NOT EXISTS commcalc.raw_sales_product" in ms and "source            TEXT" in ms and "-- REVERT:" in ms
      and "UPDATE commcalc.report_definitions" in ms and "NOT applied" in ms and "DELETE FROM commcalc.raw_sales" not in ms)
seed = open(os.path.join(ROOT, "database", "migrations", "925_data_lineage_seed.sql"), encoding="utf-8").read()
from app.modules.commcalc import data_lineage_registry as _reg
check("the product table is a registered ingest table with a lineage edge (925 seq 138)",
      "raw_sales_product" in _reg.all_ingest_tables() and "'raw_sales_product','commcalc.raw_sales_product'" in seed)
idx = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
design = open(os.path.join(ROOT, "docs", "ONBOARDING_FLOW_DESIGN.md"), encoding="utf-8").read()
check("REGISTERED: index §32 (landing identity) + §16 raw_sales_product + the design doc's landing-identity and where-it-shows rules",
      "## 32." in idx and "landing_identity" in idx and "`raw_sales_product`" in idx and "## 8." in design and "## 9." in design)
ci = open(os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml"), encoding="utf-8").read()
check("the lock runs in CI beside its siblings", "harness_landing_identity_lock.py" in ci)

print("\n══ landing identity: %d passed, %d failed ══" % (P, F))
sys.exit(1 if F else 0)
