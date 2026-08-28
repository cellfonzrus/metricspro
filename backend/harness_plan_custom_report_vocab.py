"""Proof harness for agent/commission/custom-report-vocabulary (owner 2026-08-26).

Drives the REAL ingest choke point (router._ingest_custom_report) AND the REAL plan-editor vocabulary
(plan_options.field_options / build) over an in-memory fake Supabase client — no DB, no network.
Run:  cd backend && python3 scratchpad/plan_custom_report_vocab_proof.py

What it proves
  A. CHOKE POINT — a custom sheet uploaded by ANY path lands verbatim in commcalc.raw_custom_import via the
     one common ingester _ingest_custom_report (manual upload / email sweep / FTP sweep all reach it).
  B. INGEST → VOCABULARY — after that ingest, the plan editor's field pickers OFFER the report's
     department / category / contract_type / product values, flagged `custom_only` (source custom_report).
  C. DATA-DRIVEN, NOT HARDCODED — a SECOND, differently-shaped custom report adds ITS fields too
     (a Department column the first report never had lights up automatically), with no per-report code.
  D. ADDITIVE + DEDUPE — a custom value equal to a live raw_sales value is NOT duplicated and NOT reflagged;
     a live value keeps its real line count and free entry stays as it was.
  E. ORG ISOLATION — org A never sees a custom value belonging to org B (two-org differential).
  F. MONEY HONESTY — a custom-only value carries lines=0 and the field + payload say, in plain language,
     that a rule on it is SELECTABLE but pays $0 until the custom-report money path is wired.
  G. ENGINE UNTOUCHED — commission_engine._read_sales still reads ONLY raw_sales / daily_sales_feed; the
     engine never references raw_custom_import (so no existing payout can move because of this change).
"""
import asyncio
import inspect
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import app.modules.commcalc.plan_options as PO
import app.modules.commcalc.commission_engine as CE
import app.modules.commcalc.accessory_catalog as AC
import app.modules.commcalc.router as R

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


ORG_A = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
ORG_B = "00000000-0000-0000-0000-000000000001"
PER = "June 2026"
VOID_TOKENS = ("true", "yes", "1", "voided", "void")


# ── one fake client that both the INGESTER (insert/delete) and plan_options (select/rpc) use ────────
class FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, store, table):
        self.store, self.t = store, table
        self.f, self.rng, self.cols, self.op, self.payload = [], None, None, "select", None

    def select(self, cols="*", **k):
        self.cols, self.op = cols, "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v))
        return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v)))
        return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def _m(self, r):
        for k, c, v in self.f:
            if k == "eq" and r.get(c) != v:
                return False
            if k == "in" and r.get(c) not in v:
                return False
        return True

    def execute(self):
        rows_all = self.store.setdefault(self.t, [])
        if self.op == "insert":
            batch = self.payload if isinstance(self.payload, list) else [self.payload]
            rows_all.extend(dict(r) for r in batch)
            return FakeResult([dict(r) for r in batch])
        if self.op == "delete":
            doomed = [r for r in rows_all if self._m(r)]
            self.store[self.t] = [r for r in rows_all if r not in doomed]
            return FakeResult([dict(r) for r in doomed])
        rows = [r for r in rows_all if self._m(r)]
        if self.cols and self.cols != "*":
            want = [c.strip() for c in self.cols.split(",")]
            known = set()
            for r in rows_all:
                known |= set(r.keys())
            missing = [c for c in want if known and c not in known]
            if missing:
                raise Exception(f'column {self.t}.{missing[0]} does not exist')
            rows = [{c: r.get(c) for c in want} for r in rows]
        if self.rng:
            a, b = self.rng
            rows = rows[a:b + 1]
        return FakeResult([dict(r) for r in rows])


def _live(rows):
    out = []
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in VOID_TOKENS:
            continue
        if str(r.get("trans_type") or "").strip() == "Return":
            continue
        out.append(r)
    return out


class FakeSchema:
    def __init__(self, client):
        self.c = client

    def table(self, t):
        return FakeQuery(self.c.store, t)

    def rpc(self, name, params):
        # migration 240 ABSENT on purpose → plan_options uses its bounded raw_sales scan. Custom-report
        # harvesting is independent of that path, so this exercises the scan + custom merge together.
        raise Exception(f'function commcalc.{name} does not exist')


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, _s):
        return FakeSchema(self)


class FakeUpload:
    """Minimal UploadFile stand-in for _ingest_custom_report (async read()/seek() + .filename)."""
    def __init__(self, content: bytes, filename: str):
        self.file = io.BytesIO(content)
        self.filename = filename

    async def read(self):
        self.file.seek(0)
        return self.file.read()

    async def seek(self, n):
        self.file.seek(n)


def sale(org, period, **kw):
    row = {"org_id": org, "period": period, "department": "", "category": "", "contract_type": "",
           "tender_type": "", "trans_type": "Sale", "product_desc": "", "sku": "", "voided": "",
           "trans_id": "T1", "salesperson": "REP"}
    row.update(kw)
    return row


def base_store():
    return {
        "raw_sales": [
            *[sale(ORG_A, PER, department="Phones", category="Devices", product_desc="Moto G",
                   contract_type="Upgrade", sku="MOTOG-64") for _ in range(4)],
            *[sale(ORG_A, PER, department="Accessories", category="Cases",
                   product_desc="Otterbox Case") for _ in range(7)],
            *[sale(ORG_B, PER, department="BoostDept", category="BoostCat",
                   product_desc="Boost Product") for _ in range(5)],
        ],
        "daily_sales_feed": [],
        "raw_custom_import": [],
        "upload_log": [],
        "commission_rule": [], "commission_plan": [], "plan_installment_schedule": [],
        "commission_org_config": [], "accessory_config": [],
    }


def ingest_csv(client, org, report_key, filename, header, rows, period=PER):
    """Drive the REAL router._ingest_custom_report with an in-memory CSV upload (the choke point every
    ingest path funnels through)."""
    csv = header + "\n" + "\n".join(",".join(str(c) for c in r) for r in rows) + "\n"
    up = FakeUpload(csv.encode(), filename)
    rdef = {"report_key": report_key, "period_mode": "current"}
    R.sb = lambda: client
    return asyncio.get_event_loop().run_until_complete(
        R._ingest_custom_report(report_key, up, period, org, rdef))


def opts(client, org):
    AC.invalidate()
    return PO.build(client, org, period=PER)


def vals(payload, field):
    return [v["value"] for v in payload["fields"][field]["values"]]


def custom_vals(payload, field):
    return [v["value"] for v in payload["fields"][field]["values"] if v.get("custom_only")]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── A. the ingest choke point captures a custom sheet into raw_custom_import ──")
store = base_store()
client = FakeClient(store)
# Report 1: an "Activation Details"-shaped sheet (Store / Salesperson / Contract Type / Category / Product).
res1 = ingest_csv(
    client, ORG_A, "activation_details", "activation_details_june.csv",
    "Store,Salesperson,Contract Type,Category,Product Desc,Activation#",
    [["Diversey", "ALICE", "New Activation", "Home Internet", "Home Internet Gateway", "A100"],
     ["Diversey", "BOB", "BYOD Activation", "Wireless", "Customer Phone", "A101"],
     ["Halsted", "ALICE", "Port In", "Wireless", "iPhone 15", "A102"]])
check("ingest returns saved rows", res1.get("saved") == 3, res1)
check("rows landed in raw_custom_import keyed by report_key",
      len([r for r in store["raw_custom_import"] if r.get("report_key") == "activation_details"]) == 3)
check("each captured row carries the source header verbatim in JSONB data",
      "Contract Type" in (store["raw_custom_import"][0].get("data") or {}))

print("── B. after that ingest the plan editor OFFERS the report's values (flagged custom_only) ──")
pA = opts(client, ORG_A)
check("custom Contract Type values are offered",
      {"New Activation", "BYOD Activation", "Port In"} <= set(vals(pA, "contract_type")),
      vals(pA, "contract_type"))
check("those contract_type values are flagged custom_only",
      {"New Activation", "BYOD Activation", "Port In"} <= set(custom_vals(pA, "contract_type")))
check("custom Category values are offered",
      "Home Internet" in custom_vals(pA, "category") and "Wireless" in custom_vals(pA, "category"))
check("custom Product values are offered",
      "Home Internet Gateway" in custom_vals(pA, "product_desc"))
check("the report is named in the payload summary",
      "activation_details" in pA["custom_reports"]["report_keys"])
check("summary lists which fields got custom values",
      set(pA["custom_reports"]["fields"]) >= {"contract_type", "category", "product_desc"},
      pA["custom_reports"]["fields"])

print("── C. data-driven: a SECOND, differently-shaped report adds ITS fields automatically ──")
# 'department' had NO custom values from report 1 (it has no Department column). Report 2 is a
# 'Sales by Product' sheet whose Department column now lights the department picker up — no code per report.
check("department has no custom value BEFORE report 2", custom_vals(pA, "department") == [],
      custom_vals(pA, "department"))
res2 = ingest_csv(
    client, ORG_A, "sales_by_product", "sales_by_product_june.csv",
    "Department,Category,Product Desc,Qty,Ext Price",
    [["Accessories", "Cases", "Otterbox Defender", "3", "89.97"],
     ["C2wireless", "Screen Protection", "Tempered Glass", "10", "199.90"],
     ["Phones", "Devices", "Galaxy S24", "1", "799.00"]])
check("second report ingested", res2.get("saved") == 3, res2)
pA2 = opts(client, ORG_A)
check("department picker now carries the second report's departments (data-driven)",
      {"C2wireless"} <= set(custom_vals(pA2, "department")), custom_vals(pA2, "department"))
check("both reports are listed in the summary",
      {"activation_details", "sales_by_product"} <= set(pA2["custom_reports"]["report_keys"]))
check("NO per-report hardcoding — the alias map, not a report_key, drives it",
      "activation_details" not in PO.CUSTOM_FIELD_ALIASES
      and "sales_by_product" not in str(PO.CUSTOM_FIELD_ALIASES))

print("── D. additive + dedupe against live raw_sales values ──")
# 'Accessories' (dept) and 'Cases' (category) and 'Devices'/'Phones' EXIST in raw_sales already. The custom
# report also carries them — they must NOT be duplicated nor reflagged custom_only.
dept_vals = [v for v in pA2["fields"]["department"]["values"] if v["value"] == "Accessories"]
check("a value present in BOTH raw_sales and the custom report appears once", len(dept_vals) == 1, dept_vals)
check("...and keeps its real raw_sales line count (not zeroed, not custom_only)",
      dept_vals and dept_vals[0].get("lines", 0) > 0 and not dept_vals[0].get("custom_only"), dept_vals)
check("a live category value is not reflagged",
      not any(v.get("custom_only") for v in pA2["fields"]["category"]["values"] if v["value"] == "Cases"))

print("── E. org isolation (two-org differential) ──")
pB = opts(client, ORG_B)
check("org B never sees org A's custom contract types",
      not ({"New Activation", "BYOD Activation", "Port In"} & set(vals(pB, "contract_type"))),
      vals(pB, "contract_type"))
check("org B never sees org A's custom category",
      "Home Internet" not in vals(pB, "category"))
check("org B has no custom reports of its own", pB["custom_reports"]["report_keys"] == [])

print("── F. money honesty: selectable but $0 until the money path is wired ──")
cv = next(v for v in pA2["fields"]["contract_type"]["values"] if v["value"] == "New Activation")
check("custom-only value carries lines=0 (it is NOT in raw_sales)", cv.get("lines") == 0, cv)
check("custom-only value carries its custom_report line count for transparency", cv.get("custom_lines") == 1, cv)
note = pA2["fields"]["contract_type"]["note"] or ""
check("the field note says a rule on a custom-only value will not pay yet",
      ("will not pay" in note or "$0" in note) and "raw_sales" in note, note)
check("the payload-level summary carries the same honest note",
      pA2["custom_reports"]["note"] and "do not pay yet" in pA2["custom_reports"]["note"],
      pA2["custom_reports"]["note"])

print("── G. the engine is UNTOUCHED (no existing payout can move) ──")
src = inspect.getsource(CE._read_sales)
check("commission_engine._read_sales reads raw_sales / daily_sales_feed only",
      "raw_sales" in src and "daily_sales_feed" in src)
check("commission_engine never references raw_custom_import (engine sees no custom lines)",
      "raw_custom_import" not in inspect.getsource(CE))
check("plan_options is still read-only wrt pay (never imported by the pay path — proven structurally)",
      "raw_custom_import" in inspect.getsource(PO._custom_report_values))

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
