"""Proof for agent/commission/upload-mapped-safety — the hardened POST /upload-mapped (router.
upload_mapped) over a COLUMN-AWARE in-memory FakeClient (unknown columns raise on select, like a real
42703; inserts can be made to fail on demand). NO live DB, NO browser.

Run:  cd backend && python3 scratchpad/upload_mapped_safety_proof.py

Proves:
 (A) COLUMN FILTER — a mapped key that isn't a real column is dropped + reported (dropped_columns), the
     insert never 42703s, and the friendly path is taken instead of a raw Postgres 500.
 (B) SUCCESS + SOURCE-AWARE (raw_ma_* has source_id) — the manual replace deletes ONLY source_id IS NULL
     rows; PORTAL-pulled rows (source_id set) AND a foreign tenant's rows survive; new rows land.
 (C) FAILED INSERT → RESTORE (source-aware) — when the new-rows insert fails, the saved slice is
     re-inserted so the table's business content is IDENTICAL to before, and a clear error is surfaced.
 (D) NO source_id table (carrier_commission) — full-period replace still happens, and a failed insert
     restores the whole period slice.
 (E) RESTORE ITSELF FAILS — status='restore_failed' trace written naming what was lost; error raised.
 (F) upload_trace written on success / column-drop / fail-with-restore / restore-failed (source=
     'onboarding-import'), and legacy upload_log on success.
 (G) ORG ISOLATION — reads/deletes are org-scoped; a foreign org's rows are never snapshotted, deleted
     or counted; every inserted row carries the caller org_id.
 (H) DEFAULT-MAPPING FALLBACK (#3) — an MA report with NO saved column_mapping still imports via the
     report_pull-derived default seed (used_defaults=True); a genuinely-unmapped key 400s.
"""
import sys, os, asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi import HTTPException                       # noqa: E402
from app.modules.commcalc import router                 # noqa: E402
from app.modules.commcalc import column_mapping as cm   # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "00000000-0000-0000-0000-000000000001"
CARR = "carrier-total"

# Real column sets (mig 083 raw_ma_daily_tx has source_id; mig 065 carrier_commission does NOT).
SCHEMA = {
    "raw_ma_daily_tx": {"org_id", "source_id", "carrier_id", "period", "period_month", "period_year",
                        "tx_date", "due_date", "account_id", "account_name", "direct_ma_id",
                        "direct_ma_name", "top_ma_id", "top_ma_name", "order_number", "user_name",
                        "order_type", "product_name", "retail_cost", "merchant_discount",
                        "merchant_invoice", "id", "created_at"},
    "carrier_commission": {"org_id", "carrier_id", "period", "period_month", "period_year", "trans_date",
                           "rep_name", "rep_user_id", "store", "account_id", "carrier_name",
                           "activation_type", "sub_type", "sku", "imei", "mdn", "order_id",
                           "device_margin", "consumer_margin", "rebate", "mrc_net_discount",
                           "fees_margin", "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5",
                           "spiff_m6", "residual", "other_amount", "total_commission", "id", "created_at"},
}


class _Resp:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, c, t):
        self.c, self.t = c, t
        self.op = "select"; self.cols = "*"; self.f = []; self.rows = None; self.rng = None
    def select(self, cols="*", **k): self.op = "select"; self.cols = cols; return self
    def insert(self, rows): self.op = "insert"; self.rows = rows if isinstance(rows, list) else [rows]; return self
    def delete(self): self.op = "delete"; return self
    def update(self, row): self.op = "update"; self.rows = [row]; return self
    def upsert(self, rows, **k): self.op = "insert"; self.rows = rows if isinstance(rows, list) else [rows]; return self
    def eq(self, c, v): self.f.append(("eq", c, v)); return self
    def neq(self, c, v): self.f.append(("neq", c, v)); return self
    def in_(self, c, vs): self.f.append(("in", c, list(vs))); return self
    def is_(self, c, v): self.f.append(("is", c, v)); return self
    def gte(self, c, v): self.f.append(("gte", c, v)); return self
    def lte(self, c, v): self.f.append(("lte", c, v)); return self
    def order(self, *a, **k): return self
    def limit(self, n): self.f.append(("limit", n)); return self
    def range(self, s, e): self.rng = (s, e); return self
    def _match(self, r):
        for f in self.f:
            k = f[0]
            if k == "limit":
                continue
            c = f[1]; v = r.get(c)
            if k == "eq" and str(v) != str(f[2]): return False
            if k == "neq" and str(v) == str(f[2]): return False
            if k == "in" and str(v) not in [str(x) for x in f[2]]: return False
            if k == "is":
                is_null = v is None
                if is_null != (str(f[2]).lower() == "null"): return False
            if k == "gte" and (v is None or str(v)[:10] < str(f[2])): return False
            if k == "lte" and (v is None or str(v)[:10] > str(f[2])): return False
        return True
    def execute(self):
        sch = SCHEMA.get(self.t)
        # a single-column select of an UNKNOWN column raises (models Postgres 42703) — drives
        # _table_has_column / _known_columns column pre-validation.
        if self.op == "select" and sch is not None and isinstance(self.cols, str) \
                and self.cols not in ("*", "") and "," not in self.cols and self.cols not in sch:
            raise RuntimeError(f'column "{self.cols}" does not exist (42703)')
        tbl = self.c.tables.setdefault(self.t, [])
        if self.op == "insert":
            # insert-failure injection: fail if any row matches the client's trip condition.
            if self.c._should_fail_insert(self.t, self.rows):
                raise RuntimeError(f"insert into {self.t} failed (injected)")
            for r in self.rows:
                d = dict(r); d.setdefault("id", self.c.nid()); tbl.append(d)
            self.c.ins.append((self.t, len(self.rows)))
            return _Resp(self.rows)
        if self.op == "delete":
            keep = [r for r in tbl if not self._match(r)]
            self.c.dels.append((self.t, len(tbl) - len(keep)))
            self.c.tables[self.t] = keep
            return _Resp([])
        if self.op == "update":
            for r in tbl:
                if self._match(r): r.update(self.rows[0])
            return _Resp([])
        rows = [dict(r) for r in tbl if self._match(r)]
        for f in self.f:
            if f[0] == "limit": rows = rows[:f[1]]
        if self.rng is not None:
            rows = rows[self.rng[0]:self.rng[1] + 1]
        return _Resp(rows)


class FakeClient:
    def __init__(self):
        self.tables = {"raw_ma_daily_tx": [], "carrier_commission": [], "column_mapping": [],
                       "upload_log": [], "upload_trace": [], "report_definitions": [],
                       "target_field_registry": [], "commission_field_catalog": []}
        self.ins = []; self.dels = []; self._id = 0
        # insert-failure trip: {table, marker_field, marker_val} fails the new-rows insert only;
        # fail_always=<table> fails EVERY insert into that table (primary + restore).
        self.trip = None; self.fail_always = None
    def nid(self): self._id += 1; return self._id
    def schema(self, _s): return self
    def table(self, t): return _Q(self, t)
    def _should_fail_insert(self, table, rows):
        if self.fail_always == table:
            return True
        if self.trip and self.trip[0] == table:
            mf, mv = self.trip[1], self.trip[2]
            return any(str(r.get(mf)) == str(mv) for r in rows)
        return False


class FakeUpload:
    def __init__(self, content, filename): self._c = content; self.filename = filename
    async def read(self): return self._c


DAILY_HEADERS = "Date of Transaction,Date Due,Account ID,Account Name,Order Number,User,Order Type,Product Name,Retail Cost,Merchant Discount,Merchant Invoice\n"


def daily_csv(rows):
    return (DAILY_HEADERS + "\n".join(rows) + "\n").encode()


def run(client, report_key, target_table, csv, period="July 2026", carrier=CARR, org=ORG):
    router.sb = lambda: client
    up = FakeUpload(csv, f"{report_key}.csv")
    return asyncio.run(router.upload_mapped(
        report_key=report_key, target_table=target_table, carrier_id=carrier,
        period=period, file=up, org_id=org))


def content(rows, strip=("id", "created_at")):
    """Business content of a set of rows (order-independent), excluding surrogate/serial columns."""
    return sorted(tuple(sorted((k, str(v)) for k, v in r.items() if k not in strip)) for r in rows)


# reset the process-level positive-column cache so schemas here are authoritative
router._TABLE_COL_PRESENT.clear()

NEW1 = "07/02/2026,08/01/2026,ACC1,Store A,ORD-1,Rep A,New,iPhone Case,10,2,8"
NEW2 = "07/03/2026,08/02/2026,ACC1,Store A,ORD-2,Rep B,New,Screen Protector,5,1,4"


# ── (H) default-mapping fallback (#3): MA report with NO saved column_mapping still imports ───────
print("(H) default-mapping fallback — MA report imports with zero saved rules (#3 WHY-nothing-uploaded)")
cH = FakeClient()
rH = run(cH, "ma_daily_tx", "raw_ma_daily_tx", daily_csv([NEW1, NEW2]))
check("import succeeded with NO saved column_mapping (default seed used)", rH["saved"] == 2)
check("used_defaults flag set (report_pull-derived MA seed)", rH.get("used_defaults") is True)
check("rows landed in raw_ma_daily_tx", len([r for r in cH.tables["raw_ma_daily_tx"] if r.get("org_id") == ORG]) == 2)
check("mapped real columns (product_name present)", any(r.get("product_name") == "iPhone Case" for r in cH.tables["raw_ma_daily_tx"]))
try:
    run(FakeClient(), "totally_unknown_report", "", daily_csv([NEW1]))
    check("genuinely-unmapped report_key 400s", False)
except HTTPException as e:
    check("genuinely-unmapped report_key 400s (no default layout)", e.status_code == 400)


# ── (B) success + source-aware replace; portal + foreign rows survive ─────────────────────────────
print("\n(B) source-aware replace — portal-pulled + foreign-tenant rows survive")
cB = FakeClient()
cB.tables["raw_ma_daily_tx"] += [
    {"org_id": ORG, "source_id": "portal-1", "period": "July 2026", "order_number": "P-1", "product_name": "PORTAL", "id": 900},
    {"org_id": ORG, "source_id": None, "period": "July 2026", "order_number": "OLD-1", "product_name": "OLD MANUAL", "id": 901},
    {"org_id": OTHER, "source_id": None, "period": "July 2026", "order_number": "F-1", "product_name": "FOREIGN", "id": 902},
]
rB = run(cB, "ma_daily_tx", "raw_ma_daily_tx", daily_csv([NEW1, NEW2]))
rows = cB.tables["raw_ma_daily_tx"]
check("new rows saved", rB["saved"] == 2)
check("source_scoped reported True", rB.get("source_scoped") is True)
check("portal-pulled row (source_id set) survived", any(r.get("order_number") == "P-1" for r in rows))
check("old MANUAL row (source_id NULL) replaced (gone)", not any(r.get("order_number") == "OLD-1" for r in rows))
check("foreign-tenant row untouched", any(r.get("org_id") == OTHER and r.get("order_number") == "F-1" for r in rows))
check("exactly 2 manual ORG rows now", len([r for r in rows if r.get("org_id") == ORG and r.get("source_id") is None]) == 2)


# ── (A) column filter — unknown mapped key dropped + reported, no 42703 ───────────────────────────
print("\n(A) column filter — a mapped key that isn't a real column is dropped + reported")
cA = FakeClient()
# saved rules incl. a bogus target_field; its source header IS in the file so the row isn't empty
cA.tables["column_mapping"] += [
    {"org_id": ORG, "report_key": "ma_daily_tx", "carrier_id": None, "is_active": True,
     "target_field": "product_name", "source_header": "Product Name", "transform": "text", "priority": 100},
    {"org_id": ORG, "report_key": "ma_daily_tx", "carrier_id": None, "is_active": True,
     "target_field": "order_number", "source_header": "Order Number", "transform": "text", "priority": 100},
    {"org_id": ORG, "report_key": "ma_daily_tx", "carrier_id": None, "is_active": True,
     "target_field": "not_a_real_col", "source_header": "Account Name", "transform": "text", "priority": 100},
]
rA = run(cA, "ma_daily_tx", "raw_ma_daily_tx", daily_csv([NEW1, NEW2]))
check("import succeeded despite a bogus mapped column", rA["saved"] == 2)
check("bogus column reported in dropped_columns", rA.get("dropped_columns") == ["not_a_real_col"])
check("bogus column NOT written to the table", not any("not_a_real_col" in r for r in cA.tables["raw_ma_daily_tx"]))
check("real columns still written", any(r.get("product_name") == "iPhone Case" for r in cA.tables["raw_ma_daily_tx"]))
# entirely-misaligned mapping → friendly 400, nothing changed
cA2 = FakeClient()
cA2.tables["column_mapping"] += [
    {"org_id": ORG, "report_key": "ma_daily_tx", "carrier_id": None, "is_active": True,
     "target_field": "bogus_only", "source_header": "Product Name", "transform": "text", "priority": 100}]
cA2.tables["raw_ma_daily_tx"].append({"org_id": ORG, "source_id": None, "period": "July 2026", "order_number": "KEEP", "id": 700})
try:
    run(cA2, "ma_daily_tx", "raw_ma_daily_tx", daily_csv([NEW1]))
    check("all-bogus mapping raises friendly 400", False)
except HTTPException as e:
    check("all-bogus mapping raises friendly 400 (not 500)", e.status_code == 400)
check("all-bogus mapping deleted NOTHING (existing row survives)", any(r.get("order_number") == "KEEP" for r in cA2.tables["raw_ma_daily_tx"]))


# ── (C) failed insert → restore (source-aware) leaves content identical ───────────────────────────
print("\n(C) failed insert → restore (source-aware) — table content identical, error surfaced")
cC = FakeClient()
cC.tables["raw_ma_daily_tx"] += [
    {"org_id": ORG, "source_id": "portal-9", "period": "July 2026", "order_number": "P-9", "product_name": "PORTAL", "id": 800},
    {"org_id": ORG, "source_id": None, "period": "July 2026", "order_number": "OLD-A", "product_name": "MANUAL A", "id": 801},
    {"org_id": ORG, "source_id": None, "period": "July 2026", "order_number": "OLD-B", "product_name": "MANUAL B", "id": 802},
]
before = content([r for r in cC.tables["raw_ma_daily_tx"] if r.get("org_id") == ORG])
cC.trip = ("raw_ma_daily_tx", "product_name", "iPhone Case")   # fail the NEW-rows insert only
raised = None
try:
    run(cC, "ma_daily_tx", "raw_ma_daily_tx", daily_csv([NEW1, NEW2]))
except HTTPException as e:
    raised = e
after = content([r for r in cC.tables["raw_ma_daily_tx"] if r.get("org_id") == ORG])
check("insert failure surfaced as an error", raised is not None)
check("error says no data lost", raised and "no data lost" in raised.detail.lower())
check("table CONTENT identical to before (manual slice restored, portal kept)", before == after)
check("portal row still present after restore", any(r.get("order_number") == "P-9" for r in cC.tables["raw_ma_daily_tx"]))
check("both old manual rows restored", sum(1 for r in cC.tables["raw_ma_daily_tx"] if r.get("order_number") in ("OLD-A", "OLD-B")) == 2)


# ── (D) no-source_id table — full replace + restore on failure ────────────────────────────────────
print("\n(D) carrier_commission (no source_id) — full-period replace + restore on failure")
CC_HEADERS = "User Name,Date,Rebate,Residual\n"
def cc_csv(rows): return (CC_HEADERS + "\n".join(rows) + "\n").encode()
cD = FakeClient()
cD.tables["carrier_commission"] += [
    {"org_id": ORG, "period": "July 2026", "rep_name": "OldRep", "rebate": 5, "id": 600},
    {"org_id": OTHER, "period": "July 2026", "rep_name": "ForeignRep", "rebate": 9, "id": 601},
]
# success first: full-period replace (no source_id scoping)
rD = run(cD, "carrier_commission", "carrier_commission", cc_csv(["Rep X,07/02/2026,10,20"]))
check("carrier_commission NOT source-scoped (no source_id col)", rD.get("source_scoped") is False)
check("full-period replace removed the old ORG row", not any(r.get("rep_name") == "OldRep" for r in cD.tables["carrier_commission"]))
check("foreign-org row survived full replace", any(r.get("org_id") == OTHER for r in cD.tables["carrier_commission"]))
check("new carrier row saved", rD["saved"] == 1)
# now a failing insert must restore the whole period slice
cD2 = FakeClient()
cD2.tables["carrier_commission"] += [
    {"org_id": ORG, "period": "July 2026", "rep_name": "Keep1", "rebate": 1, "id": 610},
    {"org_id": ORG, "period": "July 2026", "rep_name": "Keep2", "rebate": 2, "id": 611},
]
beforeD = content([r for r in cD2.tables["carrier_commission"] if r.get("org_id") == ORG])
cD2.trip = ("carrier_commission", "rep_name", "Rep X")
try:
    run(cD2, "carrier_commission", "carrier_commission", cc_csv(["Rep X,07/02/2026,10,20"]))
    check("carrier_commission failed insert raises", False)
except HTTPException:
    check("carrier_commission failed insert raises", True)
afterD = content([r for r in cD2.tables["carrier_commission"] if r.get("org_id") == ORG])
check("carrier_commission full slice restored (content identical)", beforeD == afterD)


# ── (E) restore itself fails → status='restore_failed' trace names the loss ───────────────────────
print("\n(E) restore-failed — loud upload_trace with what was lost")
cE = FakeClient()
cE.tables["raw_ma_daily_tx"] += [
    {"org_id": ORG, "source_id": None, "period": "July 2026", "order_number": "OLD-X", "product_name": "MANUAL X", "id": 500},
]
cE.fail_always = "raw_ma_daily_tx"   # primary insert AND restore both fail
raisedE = None
try:
    run(cE, "ma_daily_tx", "raw_ma_daily_tx", daily_csv([NEW1]))
except HTTPException as e:
    raisedE = e
tr = [t for t in cE.tables["upload_trace"] if t.get("status") == "restore_failed"]
check("restore_failed error raised", raisedE is not None and raisedE.status_code == 500)
check("upload_trace row with status=restore_failed written", len(tr) == 1)
check("trace note names rows at risk / recovery", tr and "at risk" in (tr[0].get("note") or "").lower())
check("trace error carries both original + restore error", tr and "restore error" in (tr[0].get("error") or "").lower())


# ── (F) upload_trace on success + upload_log ──────────────────────────────────────────────────────
print("\n(F) upload_trace (onboarding-import) on success + legacy upload_log")
cF = FakeClient()
rF = run(cF, "ma_daily_tx", "raw_ma_daily_tx", daily_csv([NEW1, NEW2]))
tF = cF.tables["upload_trace"]
check("one upload_trace row on success", len(tF) == 1)
check("trace source=onboarding-import", tF[0].get("source") == "onboarding-import")
check("trace status ok", tF[0].get("status") == "ok")
check("trace names target table", tF[0].get("target_table") == "raw_ma_daily_tx")
check("trace rows_saved == 2", tF[0].get("rows_saved") == 2)
check("trace org stamped", tF[0].get("org_id") == ORG)
lF = cF.tables["upload_log"]
check("legacy upload_log still written", len(lF) == 1 and lF[0].get("rows_saved") == 2)
# a column-drop success traces status='partial' with the dropped list in guard
check("drop-case trace status=partial", any(t.get("status") == "partial" for t in cA.tables["upload_trace"]))
check("drop-case trace guard lists dropped_columns",
      any((t.get("guard") or {}).get("dropped_columns") == ["not_a_real_col"] for t in cA.tables["upload_trace"]))


# ── (G) org isolation — foreign org never snapshotted/deleted/counted ─────────────────────────────
print("\n(G) org isolation")
cG = FakeClient()
cG.tables["raw_ma_daily_tx"] += [
    {"org_id": OTHER, "source_id": None, "period": "July 2026", "order_number": "OTHER-ROW", "id": 400},
]
rG = run(cG, "ma_daily_tx", "raw_ma_daily_tx", daily_csv([NEW1]), org=ORG)
check("foreign-org row never deleted", any(r.get("order_number") == "OTHER-ROW" for r in cG.tables["raw_ma_daily_tx"]))
check("every inserted row carries caller org", all(r.get("org_id") == ORG for r in cG.tables["raw_ma_daily_tx"] if r.get("order_number") != "OTHER-ROW"))
check("upload_trace stamped caller org, not foreign", all(t.get("org_id") == ORG for t in cG.tables["upload_trace"]))


print(f"\n=== upload_mapped_safety_proof: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
