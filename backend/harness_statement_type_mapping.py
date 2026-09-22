"""DB-FREE PROOF — the column-mapping key is DERIVED PER STATEMENT TYPE (owner 2026-09-20; index §30.10).

    python3 harness_statement_type_mapping.py        (from backend/; no network, no DB)

THE CLASS (PR #254 review; index §30.8 OPEN (c)): Stage A keyed `commcalc.column_mapping` for the
intake by (org, 'commission_ledger', carrier, field). A second statement type from the SAME carrier —
the owner's residual report — has a different layout and its own sign, and would have OVERWRITTEN the
commission statement's column map AND its `sign_convention` (which lives on that key's `raw_amount`
row). The rules were already namespaced per statement (`commission_category_map.source_report =
<carrier>__<statement slug>`); the mapping was the one fact without its home.

THE FIX THIS PROVES: ONE derivation, `commission_ledger.mapping_report_key(statement_type, registry_rows)`
— `commission_ledger` for the default type (TODAY's key, byte-for-byte, so nothing existing is re-keyed),
`commission_ledger__<token>` for every other type, the token being the report-kind registry's statement
type vocabulary (a third type is a registry ROW, not code) — called by every reader and writer: the
intake's analyze / commit, the older /commission-ledger/analyze + /import wizard, the MA refresh, the
read endpoints, the Column Mapping picker. The sign answer per statement type falls out of it.

WRITTEN AS THE SCENARIO. One carrier, two statement types:
    the COMMISSION statement (Gross / Report Section … ; earned is POSITIVE)         → 3.9 commit
    the RESIDUAL statement  (Account / Plan / Service Month / Residual; earned is NEGATIVE, chargebacks
                             positive and netted)                                    → 3.9 commit
Each keeps its own column map and its own 3.4 answer; each lands under its own `source_report` and ties
to its own total; Stage 4 shows one row per (carrier, statement type). Then: a mapping saved BEFORE this
change still loads byte-identically (§C, the migration-free compatibility pin); the older wizard reads
the same key (§D); a THIRD statement type needs a registry row and no code (§E); NEGATIVE CONTROLS (§F):
re-key through the literal → the residual overwrites the commission statement's sign → RED; the lock
scanner goes red on a literal key. A harness that cannot fail proves nothing.

Runs the REAL router functions over an in-memory fake of the supabase client (the intake harness's
pattern), so the endpoint's own save path — upsert_column_mapping, upsert_commission_category_map,
_ledger_land_rows, _intake_reread — is exercised, not re-stated. RULE TWO: no carrier, tenant or product
is named in any module this proves (§G reads them back with `inspect`).
"""
import asyncio
import copy
import inspect
import json
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import onboarding_intake as OI
from app.modules.commcalc import commission_ledger as CL
from app.modules.commcalc import column_mapping as CM
from app.modules.commcalc import report_kinds as RK
from app.modules.commcalc import router as R
import harness_mapping_key_lock as LOCK

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ORG = "11111111-2222-3333-4444-555555555555"
HOUSE = R.ORG_ID
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
        print(f"  FAIL  {name}{(' — ' + str(extra)) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


def money(x):
    return round(float(x or 0.0), 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURES — two statements from ONE carrier, two layouts, two signs. DATA, not code.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
COMMISSION_HEADERS = ["Report Section", "Report SubSection", "AgentSSOID", "Master Service Date", "Gross"]
COMMISSION_LINES = [   # (label, sub-label, signed amount) — earned is POSITIVE, a chargeback negative
    ("Activations", "Price Plan Activations", 150.0), ("Activations", "Price Plan Activations", 150.0),
    ("Activations", "Price Plan Activations", 150.0), ("Activations", "Price Plan Deactivations", -75.0),
    ("Incentives", "Spiff", 20.0), ("Incentives", "Spiff", 20.0),
]
COMMISSION_TOTAL = money(sum(a for _, _, a in COMMISSION_LINES))            # 415.00
COMMISSION_MAP = {"product_name": "Report Section", "order_type": "Report SubSection", "rep_user": "AgentSSOID",
                  "trans_date": "Master Service Date", "raw_amount": "Gross"}
COMMISSION_CHOSEN = {"Activations": "commission", "Incentives": "spiff"}

RESIDUAL_HEADERS = ["Account", "Plan", "Service Month", "Rep", "Residual"]
RESIDUAL_LINES = [     # (account, plan, signed amount) — earned is NEGATIVE, a chargeback positive (netted)
    ("555-0101", "Unlimited Plus", -12.50), ("555-0102", "Unlimited Plus", -12.50), ("555-0103", "Unlimited Plus", -12.50),
    ("555-0101", "Unlimited Plus", 12.50),                                          # a clawback, the other way up
    ("555-0104", "Basic Talk", -4.25), ("555-0105", "Basic Talk", -4.25),
    ("555-0102", "Auto Pay Discount", -2.00), ("555-0103", "Auto Pay Discount", -2.00),
]
RESIDUAL_TOTAL_RAW = money(sum(a for _, _, a in RESIDUAL_LINES))            # -37.50 (as the file writes it)
RESIDUAL_NET = money(-RESIDUAL_TOTAL_RAW)                                   # 37.50 canonical (earned is negative)
RESIDUAL_MAP = {"account_id": "Account", "product_name": "Plan", "trans_date": "Service Month", "rep_user": "Rep",
                "raw_amount": "Residual"}
RESIDUAL_CHOSEN = {"Unlimited Plus": "residual_monthly", "Basic Talk": "residual_monthly", "Auto Pay Discount": "autopay_residual"}


def commission_csv():
    lines = ["Dealer Compensation Statement", ",".join(COMMISSION_HEADERS)]
    for i, (pn, ot, amt) in enumerate(COMMISSION_LINES):
        lines.append(f"{pn},{ot},R{1 + i % 2},2026-08-{1 + i:02d},{amt:.2f}")
    lines.append(f",,,,{COMMISSION_TOTAL:.2f}")                       # the file's own total row (blank identity)
    return ("\n".join(lines) + "\n").encode("utf-8")


def residual_csv():
    lines = [",".join(RESIDUAL_HEADERS)]
    for i, (acct, plan, amt) in enumerate(RESIDUAL_LINES):
        lines.append(f"{acct},{plan},2026-08-01,R{1 + i % 2},{amt:.2f}")
    lines.append(f",,,,{RESIDUAL_TOTAL_RAW:.2f}")
    return ("\n".join(lines) + "\n").encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# AN IN-MEMORY SUPABASE CLIENT — the intake harness's fake (harness_onboarding_intake.py), trimmed to
# what this path touches. Declared columns per table; a probe of an absent column raises, like 42703.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _Q:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.rows, self.filters = "select", None, []
        self._order, self._limit, self._range = None, None, None

    def select(self, cols="*"):
        self.op = "select"
        for c in [c.strip() for c in str(cols).split(",") if c.strip() and c.strip() != "*"]:
            if c not in self.db.columns(self.table):
                raise RuntimeError(f'42703 column "{c}" of {self.table} does not exist')
        return self

    def insert(self, rows):   self.op, self.rows = "insert", (rows if isinstance(rows, list) else [rows]); return self
    def upsert(self, rows, on_conflict=None): self.op, self.rows = "upsert", (rows if isinstance(rows, list) else [rows]); self.conflict = on_conflict; return self
    def update(self, row):    self.op, self.rows = "update", [row]; return self
    def delete(self):         self.op = "delete"; return self
    def eq(self, k, v):       self.filters.append(("eq", k, v)); return self
    def neq(self, k, v):      self.filters.append(("neq", k, v)); return self
    def in_(self, k, v):      self.filters.append(("in", k, list(v))); return self
    def is_(self, k, v):      self.filters.append(("is", k, v)); return self
    def order(self, k, desc=False): self._order = (k, desc); return self
    def limit(self, n):       self._limit = n; return self
    def range(self, lo, hi):  self._range = (lo, hi); return self

    def _match(self, r):
        for op, k, v in self.filters:
            x = r.get(k)
            if op == "eq" and x != v: return False
            if op == "neq" and x == v: return False
            if op == "in" and x not in v: return False
            if op == "is" and not ((v == "null" and x is None) or (v is not None and v != "null" and x == v)): return False
        return True

    def execute(self):
        if self.db.tables.get(self.table, []) is None:
            raise RuntimeError(f'42P01 relation "{self.table}" does not exist')
        t = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in t if self._match(r)]
            if self._order:
                k, desc = self._order
                out.sort(key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
            if self._range:
                out = out[self._range[0]:self._range[1] + 1]
            if self._limit is not None:
                out = out[:self._limit]
            return _Res(out)
        if self.op == "insert":
            self.db.check_rows(self.table, self.rows)
            written = []
            for r in self.rows:
                row = dict(r); row.setdefault("id", str(uuid.uuid4())); t.append(row); written.append(dict(row))
            return _Res(written)
        if self.op == "upsert":
            self.db.check_rows(self.table, self.rows)
            keys = [k.strip() for k in (getattr(self, "conflict", "") or "").split(",") if k.strip()]
            written = []
            for r in self.rows:
                row = dict(r)
                hit = next((x for x in t if keys and all(x.get(k) == row.get(k) for k in keys)), None)
                if hit: hit.update(row); written.append(dict(hit))
                else: row.setdefault("id", str(uuid.uuid4())); t.append(row); written.append(dict(row))
            return _Res(written)
        if self.op == "update":
            self.db.check_rows(self.table, self.rows)
            out = []
            for r in t:
                if self._match(r): r.update(self.rows[0]); out.append(dict(r))
            return _Res(out)
        if self.op == "delete":
            keep = [r for r in t if not self._match(r)]; gone = len(t) - len(keep); t[:] = keep
            return _Res([{}] * gone)
        raise RuntimeError(self.op)


class _Res:
    def __init__(self, data): self.data = data


class FakeStorage:
    def __init__(self): self.buckets, self.files = set(), {}
    def get_bucket(self, name):
        if name not in self.buckets: raise RuntimeError("bucket not found")
        return {"name": name}
    def create_bucket(self, name): self.buckets.add(name)
    def from_(self, bucket):
        st = self
        class _B:
            def upload(self_, path, data, opts=None): st.files[(bucket, path)] = bytes(data); return {"Key": path}
            def download(self_, path):
                if (bucket, path) not in st.files: raise RuntimeError("object not found")
                return st.files[(bucket, path)]
        return _B()


class FakeDB:
    def __init__(self, with_registry=False):
        self.tables = {}
        self.storage = FakeStorage()
        self.declared = {
            "commission_bucket": ["id", "org_id", "key", "label", "kind", "sort_order", "is_active", "hint_words",
                                  "pl_line_key", "is_builtin", "created_at", "updated_at"],
            "carrier": ["id", "org_id", "name", "code", "is_default"],
            "column_mapping": ["id", "org_id", "report_key", "carrier_id", "target_field", "source_header",
                               "transform", "is_active", "priority", "updated_at", "sign_convention"],
            "commission_category_map": ["id", "org_id", "source_report", "match_field", "match_op", "pattern",
                                        "category", "sign_rule", "priority", "is_seeded", "updated_at", "leg_bucket"],
            "commission_ledger": ["id", "org_id", "period", "source_report", "account_id", "account_name", "store",
                                  "rep_user", "order_number", "order_type", "product_name", "trans_date", "due_date",
                                  "payment_month", "category", "commission", "spiff", "equipment_rebate",
                                  "residual_monthly", "autopay_residual", "payout_total", "raw_amount", "is_payout",
                                  "origin", "source_table", "source_row_id", "synced_at"],
            "onboarding_run": ["id", "org_id", "run_kind", "period", "status", "current_step", "started_by",
                               "started_at", "signed_off_by", "signed_off_on_behalf", "signed_off_at", "updated_at"],
            "onboarding_stage_state": ["id", "run_id", "org_id", "stage", "step", "instance_key", "status", "payload",
                                       "verified_numbers", "verified_by", "verified_at", "blocking_reason", "updated_at"],
        }
        if with_registry:                         # mig 1010 applied: the house seed is rows, signatures can be learned
            self.declared[RK.TABLE] = ["id", "org_id", "defined_by_org", "created_at", "updated_at"] + list(RK._COLS)
            self.seed(RK.TABLE, [{**{k: r.get(k) for k in RK._COLS}, "org_id": HOUSE} for r in RK.HOUSE_KINDS])
            self.declared[RK.SIGNATURE_TABLE] = ["id", "org_id", "fingerprint", "report_kind_key", "statement_type", "layout",
                                                 "header_count", "confirmations", "first_confirmed_at", "last_confirmed_at", "house_copy"]
        else:                                     # pre-1010: neither table exists → the mirror; learning degrades honestly
            self.tables[RK.TABLE] = None
            self.tables[RK.SIGNATURE_TABLE] = None

    def columns(self, table):
        if table in self.declared:
            return self.declared[table]
        return ["*"] + [k for r in (self.tables.get(table) or []) for k in r]

    def check_rows(self, table, rows):
        if table == "commission_ledger":
            for r in rows:
                r.setdefault("origin", "file")

    def schema(self, _name): return self
    def table(self, name): return _Q(self, name)

    def seed(self, table, rows):
        for r in rows:
            row = dict(r); row.setdefault("id", str(uuid.uuid4()))
            self.tables.setdefault(table, []).append(row)


class FakeUpload:
    def __init__(self, data, filename="statement.csv"):
        self.data, self.filename = data, filename
    async def read(self): return self.data


def fresh_db(with_registry=False):
    db = FakeDB(with_registry=with_registry)
    db.seed("carrier", [{"id": CARRIER_ID, "org_id": ORG, "name": "Northwind Cellular", "code": "northwind"}])
    db.seed("commission_bucket", [{k: v for k, v in dict(b, org_id=HOUSE).items() if k in db.declared["commission_bucket"]}
                                  for b in CL.builtin_buckets()])
    return db


def use(db):
    R.sb = lambda: db
    R._TABLE_COL_PRESENT.clear()
    return db


def run(coro):
    return asyncio.run(coro)


def analyze(db, data, **form):
    use(db)
    kw = dict(source_kind="commission", carrier_id=CARRIER_ID, statement_type="", column_map="",
              sign_answer="", assignments="", typed_total="", org_id=ORG)
    kw.update(form)
    return run(R.onboarding_intake_analyze(file=FakeUpload(data), **kw))


def commit(db, data, **form):
    use(db)
    kw = dict(source_kind="commission", carrier_id=CARRIER_ID, statement_type="", period="August 2026",
              column_map="", sign_answer="", assignments="", attestation="", typed_total="", verified_by="tester", org_id=ORG)
    kw.update(form)
    return run(R.onboarding_intake_commit(file=FakeUpload(data), **kw))


def assign(chosen):
    return json.dumps([{"label": k, "bucket": v, "is_reversal": False} for k, v in chosen.items()])


def mapping_rows(db, report_key):
    return {r["target_field"]: {k: v for k, v in r.items() if k not in ("id", "updated_at")}
            for r in db.tables.get("column_mapping", []) if r["report_key"] == report_key}


def http_error(fn, *a, **kw):
    try:
        return None, fn(*a, **kw)
    except R.HTTPException as e:
        return e.status_code, e.detail


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE ONE DERIVATION — the default type IS today's key; every other type its own")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
K_COMM, K_RES = CL.mapping_report_key(""), CL.mapping_report_key("residual statement")
check("the blank / default statement type derives TODAY's key, byte-for-byte (nothing is re-keyed)",
      K_COMM == CL.MAPPING_REPORT_KEY == "commission_ledger"
      and CL.mapping_report_key("commission statement") == K_COMM and CL.mapping_report_key(OI.STATEMENT_TYPE_DEFAULT) == K_COMM
      and CL.mapping_report_key("Commission") == K_COMM)
check("the residual statement derives its OWN key `<base>__<token>`", K_RES == "commission_ledger__residual")
check("the token is read off the free text however it is spelled ('Residual file', 'monthly residuals', 'RESIDUAL')",
      all(CL.mapping_report_key(t) == K_RES for t in ("Residual file", "monthly residuals", "RESIDUAL", "residual")))
check("a type the registry does not know keys as its OWN slug — never onto the default's key (that is the overwrite)",
      CL.mapping_report_key("spiff file") == "commission_ledger__spiff_file")
check("the vocabulary is the registry's commission-family statement types, default first",
      RK.statement_types() == ["commission", "residual"] and CL.mapping_report_keys() == [K_COMM, K_RES])
check("split / variant helpers round-trip; a key with no variant splits to ('key', '')",
      CM.split_report_key(K_RES) == ("commission_ledger", "residual") and CM.split_report_key(K_COMM) == (K_COMM, "")
      and CM.variant_report_key("commission_ledger", "") == K_COMM and CM.variant_report_key("commission_ledger", "residual") == K_RES)
check("a READ endpoint that knows only the ledger `source_report` recovers the statement type from its suffix",
      CL.statement_type_of_source_report(OI.source_report_key("northwind", "residual statement")) == "residual_statement"
      and CL.mapping_report_key(CL.statement_type_of_source_report("northwind__residual_statement")) == K_RES
      and CL.statement_type_of_source_report("ma_daily_tx") == "" and CL.mapping_report_key(CL.statement_type_of_source_report("ma_daily_tx")) == K_COMM)
res_fields = {t[0]: t for t in CM._base_fields(K_RES)}
check("the residual layout is a house-default TARGET_FIELDS entry: line identity (account / mobile number), plan or product "
      "(required label), residual amount (required, number), service month (date_auto), store, rep, order id",
      set(res_fields) == {"account_id", "account_name", "store", "rep_user", "order_number", "order_type", "product_name", "trans_date", "raw_amount"}
      and res_fields["raw_amount"][2] == "number" and res_fields["raw_amount"][3] is True
      and res_fields["product_name"][3] is True and res_fields["trans_date"][2] == "date_auto"
      and "MDN" in res_fields["account_id"][5] and "Mobile Number" in res_fields["account_id"][5], sorted(res_fields))
check("…and it lands in the SAME ledger table (a statement type, not a second table)", CM.TABLE_MAP[K_RES] == CL.LEDGER_TABLE == CM.TABLE_MAP[K_COMM])
check("the residual layout's identity fields (for the footer rule) are every non-numeric field — the widest list, as the ledger's",
      CM.identity_fields(K_RES) == [t[0] for t in CM.TARGET_FIELDS[K_RES] if t[2] != "number"] and "raw_amount" not in CM.identity_fields(K_RES))
check("the Column Mapping picker lists both keys (from the registry, no page literal)",
      K_COMM in CM.known_report_keys() and K_RES in CM.known_report_keys())
check("the residual card in the report-kind registry names the residual layout and its signature fields through it",
      next(r for r in RK.house_mirror() if r["key"] == "residual_statement")["layout"] == K_RES
      and next(r for r in RK.house_mirror() if r["key"] == "residual_statement")["signature_fields"] == ["raw_amount", "account_id", "product_name", "trans_date"])
TF = {k: CM._base_fields(k) for k in CM.TARGET_FIELDS}
dec = RK.decide(RK.detect_report_kind(["Account", "MDN", "Plan", "Service Month", "Residual Amount"], RK.house_mirror(), [], TF))
check("detection still recognises a residual file through the new layout's aliases", dec["mode"] == "confirm" and dec["candidates"][0][0] == "residual_statement", dec)
check("kind_key_for resolves the statement type through the same vocabulary (no type named in code)",
      RK.kind_key_for(RK.house_mirror(), "commission", statement_type="residual statement", layout=K_RES) == "residual_statement"
      and RK.kind_key_for(RK.house_mirror(), "commission", statement_type="", layout=K_COMM) == "commission_statement")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE SCENARIO — one carrier, two statement types → two mappings, two sign answers, two tie-outs")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db()
a_c = analyze(db, commission_csv(), statement_type="commission statement", column_map=json.dumps(COMMISSION_MAP), sign_answer="positive",
              assignments=assign(COMMISSION_CHOSEN))
check("commission analyze: the payload names the DEFAULT type's key and the carrier__type rule-set",
      a_c["report_key"] == K_COMM and a_c["source_report"] == "northwind__commission_statement" and a_c["statement_type"] == "commission statement")
check("commission analyze: ties out to the file's own total (415.00), nothing unassigned",
      a_c["verify"]["tie"]["match"] is True and a_c["verify"]["tie"]["our_total"] == COMMISSION_TOTAL and a_c["unassigned"] == [], a_c["verify"]["tie"])
c_c = commit(db, commission_csv(), statement_type="commission statement", column_map=json.dumps(COMMISSION_MAP), sign_answer="positive",
             assignments=assign(COMMISSION_CHOSEN))
check("commission commit: ok, 6 rows landed, tie-out 415.00 re-read from the table",
      c_c["ok"] is True and c_c["verified_numbers"]["rows_landed"] == 6 and c_c["verified_numbers"]["tie"]["our_total"] == COMMISSION_TOTAL, c_c.get("problems"))
comm_rows = mapping_rows(db, K_COMM)
check("the commission mapping is saved under TODAY's key, carrier-scoped, amount ← Gross, sign payout_positive",
      set(comm_rows) == set(COMMISSION_MAP) and all(r["carrier_id"] == CARRIER_ID and r["org_id"] == ORG for r in comm_rows.values())
      and comm_rows["raw_amount"]["source_header"] == "Gross" and comm_rows["raw_amount"]["sign_convention"] == CL.SIGN_PAYOUT_POSITIVE, comm_rows)
COMM_SNAPSHOT = copy.deepcopy(comm_rows)

# the RESIDUAL statement from the SAME carrier
a_r0 = analyze(db, residual_csv(), statement_type="residual statement")
check("residual analyze: the payload names the RESIDUAL key and its own rule-set namespace",
      a_r0["report_key"] == K_RES and a_r0["source_report"] == "northwind__residual_statement" and a_r0["instance_key"] != a_c["instance_key"])
prop = {p["target_field"]: p for p in a_r0["columns"]}
check("residual analyze: the columns are proposed FROM THE RESIDUAL LAYOUT (amount ← Residual, label ← Plan, line ← Account, "
      "period ← Service Month) — not from the commission statement's saved map",
      prop["raw_amount"]["column"] == "Residual" and prop["product_name"]["column"] == "Plan" and prop["account_id"]["column"] == "Account"
      and prop["trans_date"]["column"] == "Service Month" and all(p["provenance"] != "your earlier choice" for p in prop.values()),
      {k: (p["column"], p["provenance"]) for k, p in prop.items()})
check("residual analyze: 3.4 is UNANSWERED for this statement type although the commission statement's answer is stored",
      a_r0["sign"]["stored_answer"] is None and a_r0["sign"]["answered"] is False and a_r0["sign"]["stored"]["source"] == "unmapped")
a_r = analyze(db, residual_csv(), statement_type="residual statement", column_map=json.dumps(RESIDUAL_MAP), sign_answer="negative",
              assignments=assign(RESIDUAL_CHOSEN))
check("residual analyze under 'earned is negative': the footer (−37.50 raw) is found, 8 lines to land, ties out to +37.50 "
      "with the +12.50 clawback NETTED into residual-monthly",
      a_r["detect"]["footer"]["file_total_raw"] == RESIDUAL_TOTAL_RAW and a_r["verify"]["rows_to_land"] == 8
      and a_r["verify"]["tie"]["match"] is True and a_r["verify"]["tie"]["our_total"] == RESIDUAL_NET
      and a_r["verify"]["totals"]["buckets"]["residual_monthly"]["chargebacks"] == -12.5
      and a_r["verify"]["totals"]["buckets"]["residual_monthly"]["net"] == 33.5
      and a_r["verify"]["totals"]["buckets"]["autopay_residual"]["net"] == 4.0, a_r["verify"])
c_r = commit(db, residual_csv(), statement_type="residual statement", column_map=json.dumps(RESIDUAL_MAP), sign_answer="negative",
             assignments=assign(RESIDUAL_CHOSEN))
check("residual commit: ok, 8 rows landed under its own source_report, tie-out 37.50 re-read",
      c_r["ok"] is True and c_r["source_report"] == "northwind__residual_statement" and c_r["verified_numbers"]["rows_landed"] == 8
      and c_r["verified_numbers"]["tie"]["our_total"] == RESIDUAL_NET and c_r["sign_convention"] == CL.SIGN_PAYOUT_NEGATIVE_NETTED, c_r.get("problems"))
res_rows = mapping_rows(db, K_RES)
check("the residual mapping is saved under ITS key, carrier-scoped, amount ← Residual, sign payout_negative_netted",
      set(res_rows) == set(RESIDUAL_MAP) and res_rows["raw_amount"]["source_header"] == "Residual"
      and res_rows["raw_amount"]["sign_convention"] == CL.SIGN_PAYOUT_NEGATIVE_NETTED
      and all(r["carrier_id"] == CARRIER_ID for r in res_rows.values()), res_rows)
check("THE PIN: the commission statement's mapping and sign answer are BYTE-IDENTICAL after the residual commit",
      mapping_rows(db, K_COMM) == COMM_SNAPSHOT, mapping_rows(db, K_COMM))
check("two independent sign answers on one carrier: positive on the commission key, negative-netted on the residual key",
      CL.convention_from_mapping(list(mapping_rows(db, K_COMM).values()))[1]["declared"] == CL.SIGN_PAYOUT_POSITIVE
      and CL.convention_from_mapping(list(mapping_rows(db, K_RES).values()))[1]["declared"] == CL.SIGN_PAYOUT_NEGATIVE_NETTED)
led = db.tables["commission_ledger"]
check("the ledger rows CARRY the statement type in source_report: 6 under the commission key, 8 under the residual key, one org",
      sum(1 for r in led if r["source_report"] == "northwind__commission_statement") == 6
      and sum(1 for r in led if r["source_report"] == "northwind__residual_statement") == 8 and all(r["org_id"] == ORG for r in led))
check("each statement's landed rows sum to ITS OWN total (415.00 commission; 37.50 residual with the clawback netted)",
      money(sum(r["payout_total"] for r in led if r["source_report"] == "northwind__commission_statement")) == COMMISSION_TOTAL
      and money(sum(r["payout_total"] for r in led if r["source_report"] == "northwind__residual_statement")) == RESIDUAL_NET)
rules = db.tables["commission_category_map"]
check("the bucket rules stay namespaced per statement (2 under the commission key, 3 under the residual key)",
      sum(1 for r in rules if r["source_report"] == "northwind__commission_statement") == 2
      and sum(1 for r in rules if r["source_report"] == "northwind__residual_statement") == 3)
st = R.onboarding_intake_state(org_id=ORG)
vt = st["rail"]["verify_table"]
check("Stage 4: ONE row per (carrier, statement type), both verified, each with its own tie-out",
      len(vt) == 2 and {r["instance_key"] for r in vt} == {a_c["instance_key"], a_r["instance_key"]}
      and all(r["status"] == OI.STATUS_VERIFIED and r["match"] is True for r in vt)
      and {r["our_total"] for r in vt} == {COMMISSION_TOTAL, RESIDUAL_NET}
      and {r["label"] for r in vt} == {"Northwind Cellular — commission statement", "Northwind Cellular — residual statement"}, vt)
a_c2 = analyze(db, commission_csv(), statement_type="commission statement")
check("re-opening the commission statement: its OWN saved map prefills ('your earlier choice') and its OWN 3.4 answer is shown",
      a_c2["sign"]["stored_answer"] == "positive"
      and all(p["provenance"] == "your earlier choice" for p in a_c2["columns"] if p["target_field"] in COMMISSION_MAP), [(p["target_field"], p["provenance"]) for p in a_c2["columns"]])
a_r2 = analyze(db, residual_csv(), statement_type="residual statement")
check("re-opening the residual statement: its OWN map and its OWN answer ('negative')",
      a_r2["sign"]["stored_answer"] == "negative" and next(p for p in a_r2["columns"] if p["target_field"] == "raw_amount")["column"] == "Residual")
s_c = R.commission_ledger_summary(source_report="northwind__commission_statement", org_id=ORG)
s_r = R.commission_ledger_summary(source_report="northwind__residual_statement", org_id=ORG)
check("the read endpoints derive the statement type (and the carrier) from the source_report: the summary reports each statement's OWN convention",
      s_c["convention_meta"]["declared"] == CL.SIGN_PAYOUT_POSITIVE and s_r["convention_meta"]["declared"] == CL.SIGN_PAYOUT_NEGATIVE_NETTED,
      (s_c["convention_meta"], s_r["convention_meta"]))
check("…and each summary's payout total is its own statement's", s_c["payout_total"] == COMMISSION_TOTAL and s_r["payout_total"] == RESIDUAL_NET)
tm = R.commission_ledger_templates(org_id=ORG)["templates"]
# §30.15 (the ledger statement identity): the picker lists ONE entry per statement identity, keyed by
# `commission_ledger.template_key` — the bare base for the default statement type, `<base>__<slug>`
# otherwise — derived from the stored keys in the data, never spelled here.
_picked = {CL.template_key("northwind__commission_statement"), CL.template_key("northwind__residual_statement")}
check("the Commission Ledger page's template picker lists BOTH statements FROM THE DATA, one entry per statement identity (template_key; no hardcoded name)",
      len(_picked) == 2 and _picked <= {t["key"] for t in tm}, ({t["key"] for t in tm}, _picked))
s_pick = {k: R.commission_ledger_summary(source_report=k, org_id=ORG)["payout_total"] for k in _picked}
check("…and each picked key reads ITS OWN statement (the default type's bare key reads the commission statement's family)",
      s_pick.get(CL.template_key("northwind__commission_statement")) == COMMISSION_TOTAL
      and s_pick.get(CL.template_key("northwind__residual_statement")) == RESIDUAL_NET, s_pick)
check("the confirmed layouts are learned under their statement type's card (pre-1010 here: the kind is resolved, learning degrades honestly)",
      c_c["report_kind"].get("kind") == "commission_statement" and c_r["report_kind"].get("kind") == "residual_statement"
      and c_r["report_kind"]["learned"] is False and RK.MIGRATION in c_r["report_kind"]["reason"], (c_c["report_kind"], c_r["report_kind"]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE MIGRATION-FREE COMPATIBILITY PIN — a mapping saved BEFORE this change loads byte-identically")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db_old = fresh_db()
PRE = [{"org_id": ORG, "report_key": "commission_ledger", "carrier_id": CARRIER_ID, "target_field": tf, "source_header": sh,
        "transform": ("number" if tf == "raw_amount" else "text"), "is_active": True, "priority": 100,
        "sign_convention": (CL.SIGN_PAYOUT_POSITIVE if tf == "raw_amount" else None)} for tf, sh in COMMISSION_MAP.items()]
db_old.seed("column_mapping", PRE)                       # the pre-change key, spelled as the table holds it
use(db_old)
loaded = R._ledger_source_rules(db_old, ORG, CARRIER_ID)             # no key passed — the default derivation
check("_ledger_source_rules with no statement type loads the PRE-EXISTING rows exactly (same headers, same sign)",
      {r["target_field"]: r["source_header"] for r in loaded} == COMMISSION_MAP
      and next(r for r in loaded if r["target_field"] == "raw_amount")["sign_convention"] == CL.SIGN_PAYOUT_POSITIVE)
check("_ledger_convention_for with no statement type reads the pre-existing sign answer",
      R._ledger_convention_for(db_old, ORG, CARRIER_ID)[1]["declared"] == CL.SIGN_PAYOUT_POSITIVE)
a_old = analyze(db_old, commission_csv())                            # statement_type '' = the default
check("the intake with NO statement type stated reads the pre-existing mapping as 'your earlier choice' and shows its 3.4 answer",
      a_old["report_key"] == "commission_ledger" and a_old["sign"]["stored_answer"] == "positive"
      and all(p["provenance"] == "your earlier choice" and p["column"] == COMMISSION_MAP[p["target_field"]]
              for p in a_old["columns"] if p["target_field"] in COMMISSION_MAP))
c_old = commit(db_old, commission_csv(), sign_answer="positive", assignments=assign(COMMISSION_CHOSEN))
after = mapping_rows(db_old, "commission_ledger")
check("committing under the default type UPDATES those same rows in place (5 rows, same key, no duplicate, no re-key)",
      c_old["ok"] is True and len(db_old.tables["column_mapping"]) == 5 and set(after) == set(COMMISSION_MAP)
      and all(r["report_key"] == "commission_ledger" for r in after.values()), len(db_old.tables["column_mapping"]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. THE OLDER WIZARD (/commission-ledger/analyze + /import) reads the SAME derived key")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
use(db)                                                              # the §B database: both mappings saved
w_r = run(R.commission_ledger_analyze(file=FakeUpload(residual_csv()), source_report="northwind__residual_statement",
                                      carrier_id=CARRIER_ID, statement_type="residual statement", org_id=ORG))
check("analyze with statement_type 'residual statement' → the residual key, the residual mapping's convention, amount ← Residual",
      w_r["report_key"] == K_RES and w_r["convention_meta"]["declared"] == CL.SIGN_PAYOUT_NEGATIVE_NETTED
      and w_r["amount_source"] == "Residual" and w_r["usable_rows"] == 8 and w_r["footer_rows_dropped"] == 1, (w_r["report_key"], w_r["convention_meta"]))
w_r2 = run(R.commission_ledger_analyze(file=FakeUpload(residual_csv()), source_report="northwind__residual_statement",
                                       carrier_id=CARRIER_ID, statement_type="", org_id=ORG))
check("analyze with NO statement_type but the residual template's source_report → the type is read off the source_report: same key",
      w_r2["report_key"] == K_RES and w_r2["convention_meta"]["declared"] == CL.SIGN_PAYOUT_NEGATIVE_NETTED)
w_c = run(R.commission_ledger_analyze(file=FakeUpload(commission_csv().split(b"\n", 1)[1]), source_report="northwind__commission_statement",
                                      carrier_id=CARRIER_ID, statement_type="", org_id=ORG))
check("analyze for the commission template → today's key, the commission mapping's convention, amount ← Gross",
      w_c["report_key"] == K_COMM and w_c["convention_meta"]["declared"] == CL.SIGN_PAYOUT_POSITIVE and w_c["amount_source"] == "Gross")
w_ma = run(R.commission_ledger_analyze(file=FakeUpload(commission_csv().split(b"\n", 1)[1]), source_report="ma_daily_tx",
                                       carrier_id="", statement_type="", org_id=ORG))
check("analyze for a template with no statement part ('ma_daily_tx', no carrier) → today's key and the default convention — unchanged",
      w_ma["report_key"] == K_COMM and w_ma["convention_meta"]["source"] in ("default", "unmapped") and w_ma["convention"] == CL.DEFAULT_CONVENTION)
n_before = len(db.tables["commission_ledger"])
w_i = run(R.commission_ledger_import(file=FakeUpload(residual_csv()), source_report="northwind__residual_statement", period="September 2026",
                                     carrier_id=CARRIER_ID, statement_type="residual statement", org_id=ORG))
check("import through the older wizard lands the residual statement with ITS mapping and sign (8 rows, 37.50, September slice)",
      w_i["saved"] == 8 and w_i["report_key"] == K_RES and w_i["summary"]["payout_total"] == RESIDUAL_NET
      and len(db.tables["commission_ledger"]) == n_before + 8 and w_i["convention_meta"]["declared"] == CL.SIGN_PAYOUT_NEGATIVE_NETTED)
check("the MA refresh's mapping reads the DEFAULT type's key (the raw MA tables feed the commission statement) — derived, not spelled",
      "ledger_rk = commission_ledger.mapping_report_key(\"\")" in inspect.getsource(R._ledger_ma_derive)
      and 'default_mapping("commission_ledger")' not in inspect.getsource(R._ledger_ma_derive))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. A THIRD STATEMENT TYPE IS A REGISTRY ROW — no code")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db3 = fresh_db(with_registry=True)
db3.seed(RK.TABLE, [{**{k: None for k in RK._COLS}, "org_id": HOUSE, "key": "spiff_statement", "label": "Spiff report from the carrier",
                     "what_in_it": "The carrier's separate spiff / incentive statement.", "landing": "commission",
                     "statement_type": "spiff", "defined_by": "house", "sort_order": 45, "is_active": True}])
rows3, ready3 = RK.load_registry(db3, ORG)
check("with mig 1010 applied and a 'spiff' row added, the vocabulary gains the token and the picker gains the key",
      ready3 and RK.statement_types(rows3) == ["commission", "residual", "spiff"]
      and CM.known_report_keys(db3, ORG)[-1] == "commission_ledger__spiff" and CL.mapping_report_key("spiff statement", rows3) == "commission_ledger__spiff")
a_s = analyze(db3, commission_csv(), statement_type="spiff statement", column_map=json.dumps(COMMISSION_MAP), sign_answer="positive",
              assignments=assign({"Activations": "spiff", "Incentives": "spiff"}))
check("the intake keys the third type's mapping on its own key, and its fields INHERIT the family layout (no TARGET_FIELDS entry needed)",
      a_s["report_key"] == "commission_ledger__spiff" and a_s["source_report"] == "northwind__spiff_statement"
      and {p["target_field"] for p in a_s["columns"]} == {t[0] for t in CM.TARGET_FIELDS["commission_ledger"]})
c_s = commit(db3, commission_csv(), statement_type="spiff statement", column_map=json.dumps(COMMISSION_MAP), sign_answer="positive",
             assignments=assign({"Activations": "spiff", "Incentives": "spiff"}))
check("…commits with its own mapping and sign, ties out, and is learned under the new card",
      c_s["ok"] is True and set(mapping_rows(db3, "commission_ledger__spiff")) == set(COMMISSION_MAP)
      and mapping_rows(db3, "commission_ledger__spiff")["raw_amount"]["sign_convention"] == CL.SIGN_PAYOUT_POSITIVE
      and not mapping_rows(db3, K_COMM) and c_s["report_kind"].get("kind") == "spiff_statement" and c_s["report_kind"]["learned"] is True
      and {r["statement_type"] for r in db3.tables[RK.SIGNATURE_TABLE]} == {"spiff statement"}
      and {r["layout"] for r in db3.tables[RK.SIGNATURE_TABLE]} == {"commission_ledger__spiff"}, (c_s.get("problems"), c_s["report_kind"]))
check("RK.kind_key_for names no statement type (the token comes from the rows)",
      "residual" not in inspect.getsource(RK.kind_key_for))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. NEGATIVE CONTROLS — the defect put back, the checks watched to go red")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_orig = R._ledger_mapping_key
R._ledger_mapping_key = lambda client, org_id, statement_type="", source_report="": CL.MAPPING_REPORT_KEY   # the literal, for every type
try:
    dbx = fresh_db()
    commit(dbx, commission_csv(), statement_type="commission statement", column_map=json.dumps(COMMISSION_MAP), sign_answer="positive",
           assignments=assign(COMMISSION_CHOSEN))
    snap = copy.deepcopy(mapping_rows(dbx, K_COMM))
    commit(dbx, residual_csv(), statement_type="residual statement", column_map=json.dumps(RESIDUAL_MAP), sign_answer="negative",
           assignments=assign(RESIDUAL_CHOSEN))
    over = mapping_rows(dbx, K_COMM)
    check("RE-KEY THROUGH THE LITERAL → the residual commit OVERWRITES the commission statement's amount column and its sign (the class) — the pin goes RED",
          over != snap and over["raw_amount"]["source_header"] == "Residual" and over["raw_amount"]["sign_convention"] == CL.SIGN_PAYOUT_NEGATIVE_NETTED
          and not mapping_rows(dbx, K_RES), (snap.get("raw_amount"), over.get("raw_amount")))
    a_x = analyze(dbx, commission_csv(), statement_type="commission statement")
    check("…and re-opening the commission statement would now show the RESIDUAL's answer ('negative') and header — the defect, made visible",
          a_x["sign"]["stored_answer"] == "negative")
finally:
    R._ledger_mapping_key = _orig
dby = fresh_db()
commit(dby, commission_csv(), statement_type="commission statement", column_map=json.dumps(COMMISSION_MAP), sign_answer="positive",
       assignments=assign(COMMISSION_CHOSEN))
snap = copy.deepcopy(mapping_rows(dby, K_COMM))
commit(dby, residual_csv(), statement_type="residual statement", column_map=json.dumps(RESIDUAL_MAP), sign_answer="negative",
       assignments=assign(RESIDUAL_CHOSEN))
check("restored: the same two commits leave the commission statement's rows untouched (control of the control)",
      mapping_rows(dby, K_COMM) == snap and mapping_rows(dby, K_RES)["raw_amount"]["sign_convention"] == CL.SIGN_PAYOUT_NEGATIVE_NETTED)
check("the lock scanner goes RED on a literal key in a load and in a save, GREEN on the table name",
      any(i[1] == "literal_key" for i in LOCK.scan_text("backend/app/modules/x.py", 'rules = column_mapping.load_rules(client, org_id, "commission_ledger", cid)\n'))
      and any(i[1] == "literal_key" for i in LOCK.scan_text("backend/app/modules/x.py", 'kw = {"report_key": "commission_ledger"}\n'))
      and not LOCK.scan_text("backend/app/modules/x.py", 'client.schema("commcalc").table("commission_ledger").select("*")\n'))
lv, lstale = LOCK.scan(LOCK.walk(), LOCK.ALLOW)
check("the lock is GREEN on the real tree (no literal mapping key outside the allow set; no stale entry)", not lv and not lstale, (lv[:3], lstale))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. WIRING, RULE TWO, REGISTRATION")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
src_router = inspect.getsource(R)
check("the router has no stand-in constant and every commission-family mapping read/write goes through the derived key",
      "_INTAKE_REPORT_KEY" not in src_router and src_router.count("_ledger_mapping_key(") >= 4
      and 'kw = {"report_key": report_key,' in src_router)
check("RULE TWO: no carrier, tenant or product is named in the derivation, the vocabulary or the residual layout's aliases",
      not re.search(r"verizon|boost|total wireless|vidapay|t-cetra|cricket|metro", inspect.getsource(CL.mapping_report_key) + inspect.getsource(RK.statement_type_token)
                    + inspect.getsource(RK.statement_types) + json.dumps(CM.TARGET_FIELDS[K_RES]), re.I))
seed_sql = open(os.path.join(ROOT, "database", "migrations", "1010_report_kind_registry.sql"), encoding="utf-8").read()
check("mig 1010's residual row carries the residual layout (mirror and seed pinned equal by harness_report_kinds §A)",
      "'residual', 'commission', 'commission_ledger__residual'" in seed_sql)
buckets = {b["key"]: b for b in CL.builtin_buckets()}
check("the residual lines' bucket hints are CONFIG (mig 1009 hint_words): 'residual' and the auto-pay spellings are already present — nothing added",
      "residual" in buckets["residual_monthly"]["hint_words"] and {"autopay", "auto pay", "auto-pay"} <= set(buckets["autopay_residual"]["hint_words"]))
wf = open(os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml"), encoding="utf-8").read()
check("the lock runs in CI beside the report-kind lock (carrier-vocab-guard.yml)", "harness_mapping_key_lock.py" in wf)
idx = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("registered in the index: §30.10, the derived key in §25.11, this harness and the lock",
      "### 30.10" in idx and "mapping_report_key" in idx and "harness_statement_type_mapping.py" in idx and "harness_mapping_key_lock.py" in idx)
setup_page = open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "commcalc", "commission-ledger", "setup", "page.tsx"), encoding="utf-8").read()
intake_page = open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "onboarding", "intake", "page.tsx"), encoding="utf-8").read()
check("the older wizard's page offers the registry's statement types and saves under the payload's key; the intake's 3.1 names no type",
      "useReportKinds" in setup_page and "report_key: rk" in setup_page and "'commission_ledger'" not in setup_page
      and "statementTypeToken(" in intake_page and "'residual statement'" not in intake_page)

print(f"\n══ statement-type mapping: {_pass} passed, {_fail} failed ══")
if _failures:
    print("failed:\n  " + "\n  ".join(_failures))
sys.exit(1 if _fail else 0)
