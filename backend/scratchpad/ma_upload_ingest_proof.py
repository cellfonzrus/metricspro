"""Proof for agent/commission/ma-manual-upload — the INGEST ENDPOINT orchestration (router.
manual_upload_ingest) over a faithful in-memory FakeClient (org/source/date filters actually applied).
NO live DB, NO browser.

Run:  cd backend && python3 scratchpad/ma_upload_ingest_proof.py

Proves at the DB layer:
 (1) HISTORICAL is idempotent — running the SAME multi-month file twice leaves the table with the SAME
     row count (each covered month's MANUAL rows are replaced, not doubled), and split per real month.
 (2) HISTORICAL never touches PORTAL-pulled rows (source_id set) or ANOTHER TENANT's rows (org scoped).
 (3) APPEND is idempotent — second upload of the same file inserts ZERO new rows (dedup vs existing).
 (4) MULTI-TENANT — existing-key reads + deletes are org-scoped; a foreign org's rows are never read,
     counted, or deleted; every inserted row carries the caller's org_id.
 (5) upload_trace gets one row per upload (source='manual', org_id, rows_saved, per-period counts).
 (6) The Activation Order ↔ Order Number linkage indicator is computed from the counterpart table.
"""
import sys, os, asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.modules.commcalc import router  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "00000000-0000-0000-0000-000000000001"
CARR = "carrier-total"


class _Resp:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, c, t):
        self.c, self.t = c, t
        self.op = "select"; self.f = []; self.rows = None; self.rng = None
    def select(self, cols="*", **k): self.op = "select"; return self
    def insert(self, rows): self.op = "insert"; self.rows = rows if isinstance(rows, list) else [rows]; return self
    def delete(self): self.op = "delete"; return self
    def update(self, row): self.op = "update"; self.rows = [row]; return self
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
        tbl = self.c.tables.setdefault(self.t, [])
        if self.op == "insert":
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
        self.tables = {"report_pull_map": [], "manual_report_mapping": [],
                       "raw_ma_commission": [], "raw_ma_daily_tx": [], "upload_trace": []}
        self.ins = []; self.dels = []; self._id = 0
    def nid(self): self._id += 1; return self._id
    def schema(self, _s): return self
    def table(self, t): return _Q(self, t)


class FakeUpload:
    def __init__(self, content, filename): self._c = content; self.filename = filename
    async def read(self): return self._c


COMM_CSV = (
    "Date,Activation Order,IMEI,SKU,Sub Type,Rebate,User Name\n"
    "05/31/2026,AO-1,111,S1,TWP,10,Rep A\n"
    "06/01/2026,AO-2,222,S1,TWP,20,Rep A\n"
    "06/15/2026,AO-3,333,S2,TWP,5,Rep B\n"
    "07/02/2026,AO-4,444,S2,TWP,0,Rep B\n"
    "07/02/2026,AO-4,,S9,SIM,0,Rep B\n"
).encode()


def ingest(client, mode, csv=COMM_CSV, org=ORG):
    router.sb = lambda: client
    up = FakeUpload(csv, "ma_commission.csv")
    return asyncio.run(router.manual_upload_ingest(
        report_key="ma_commission", carrier_id=CARR, mode=mode,
        date_from="", date_to="", file=up, org_id=org))


# ── (1)(2) HISTORICAL idempotence + isolation ────────────────────────────────────────────────────
print("(1)(2) historical replace-by-month idempotence + isolation")
c = FakeClient()
# a portal-pulled row (source_id set) and a foreign-tenant row must both survive
c.tables["raw_ma_commission"].append({"org_id": ORG, "carrier_id": CARR, "source_id": "portal-1",
                                       "activation_order": "AO-PORTAL", "tx_date": "2026-07-02",
                                       "period": "July 2026", "id": 900})
c.tables["raw_ma_commission"].append({"org_id": OTHER, "source_id": None, "activation_order": "AO-OTHER",
                                       "tx_date": "2026-07-02", "period": "July 2026", "id": 901})
r1 = ingest(c, "historical")
n_after1 = len([r for r in c.tables["raw_ma_commission"] if r.get("org_id") == ORG and r.get("source_id") is None])
check("historical run1 saved all 5 file rows", r1["saved"] == 5)
check("historical run1 split into May/June/July", r1["periods"] == {"May 2026": 1, "June 2026": 2, "July 2026": 2})
check("5 manual org rows present after run1", n_after1 == 5)
r2 = ingest(c, "historical")
n_after2 = len([r for r in c.tables["raw_ma_commission"] if r.get("org_id") == ORG and r.get("source_id") is None])
check("historical run2 is idempotent (still 5 manual rows, not 10)", n_after2 == 5)
check("portal-pulled row (source_id set) untouched", any(r.get("activation_order") == "AO-PORTAL" for r in c.tables["raw_ma_commission"]))
check("foreign-tenant row untouched", any(r.get("org_id") == OTHER and r.get("activation_order") == "AO-OTHER" for r in c.tables["raw_ma_commission"]))
check("every inserted row carries the caller org_id", all(r.get("org_id") == ORG for r in c.tables["raw_ma_commission"] if r.get("source_id") is None and r.get("activation_order") != "AO-OTHER"))

# ── (3)(4) APPEND idempotence + org-scoped dedup ─────────────────────────────────────────────────
print("\n(3)(4) append dedup idempotence + multi-tenant read scope")
c2 = FakeClient()
# same-key rows under a DIFFERENT org must NOT count as existing for ORG
c2.tables["raw_ma_commission"].append({"org_id": OTHER, "source_id": None, "activation_order": "AO-1",
                                        "tx_date": "2026-05-31", "imei": "111", "sku": "S1",
                                        "sub_type": "TWP", "period": "May 2026", "id": 800})
a1 = ingest(c2, "append")
check("append run1 inserts all 5 (foreign-org dup ignored)", a1["saved"] == 5)
a2 = ingest(c2, "append")
check("append run2 inserts 0 (dedup vs existing)", a2["saved"] == 0)
check("append run2 counts 5 duplicates", a2["dupes_dropped"] == 5)
org_rows = [r for r in c2.tables["raw_ma_commission"] if r.get("org_id") == ORG]
check("still exactly 5 org rows after two appends", len(org_rows) == 5)

# ── (5) upload_trace ─────────────────────────────────────────────────────────────────────────────
print("\n(5) upload_trace")
traces = c2.tables["upload_trace"]
check("one trace row per upload (2 appends)", len(traces) == 2)
check("trace stamped with caller org", all(t.get("org_id") == ORG for t in traces))
check("trace source=manual", all(t.get("source") == "manual" for t in traces))
check("trace names target table", traces[0].get("target_table") == "raw_ma_commission")
check("trace carries per-period counts", traces[0].get("periods") == {"May 2026": 1, "June 2026": 2, "July 2026": 2})

# ── (6) linkage indicator from the counterpart table ─────────────────────────────────────────────
print("\n(6) linkage indicator")
c3 = FakeClient()
# MA Daily Tx already has order AO-2 and AO-3 → 2 of the commission file's orders should link
for on in ("AO-2", "AO-3", "AO-99"):
    c3.tables["raw_ma_daily_tx"].append({"org_id": ORG, "order_number": on, "tx_date": "2026-06-10"})
r = ingest(c3, "append")
check("linkage computed", r["linkage"] is not None)
check("linkage matched == 2 (AO-2, AO-3)", r["linkage"]["matched"] == 2)
check("linkage distinct == 4 activation orders", r["linkage"]["distinct"] == 4)
check("money note present (ingest-only)", "no payout" in r["money_note"].lower())

print(f"\n=== ma_upload_ingest_proof: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
