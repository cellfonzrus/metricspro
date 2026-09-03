"""HARNESS — cross-tenant content isolation on the sales/pay basis (the Diversey class).

THE INCIDENT THIS PINS (owner escalation 2026-09-02, root-caused 2026-09-03). On 2026-07-14 a
Luxelink sales export was ingested under the HOUSE org (the pre-2026-08-09 "acting tenant is a
guess" resolution): 6 line items for Luxelink rep "Espinoza, Carolina" at Luxelink store
"4640-A W Diversey Ave" landed in HOUSE `commcalc.raw_sales`. Every query along the way was
correctly `.eq('org_id', …)`-scoped — the org_id VALUE was wrong at write time — so the
org-scope CI guard was green while the July recompute paid a phantom rep $2.9995, wrote a
phantom flag, and accrued a phantom $3.00 true-up. That is the leak class the static guard
cannot see: right filter, wrong tenant attribution, caught only by asking "does this org even
HAVE a store called that?" — which is `ingest_store_guard` (mig 280).

WHAT THIS HARNESS PROVES (pure, in-memory FakeClient over the REAL router/guard functions —
no DB, no network):

  A. `ingest_store_guard.screen` catches the exact incident batch: the six real Diversey line
     shapes filed under the house org are flagged (warn) and withheld (block), legit rows and
     blank-store rows are never touched, an empty roster fails OPEN, and — negative control —
     warn mode alone writes every row (why the 2026-09-03 cleanup had to delete data, and why
     `block` exists).

  B. The sales read/write paths are org-airtight even when BOTH tenants' rows share one table
     AND collide on trans_id/store content:
       - `_sales_rows_union_txn(org B)` returns zero org-A rows (trans_id dedupe never reaches
         across orgs);
       - `_promote_feed_to_raw_sales(org B)` writes only org-B-stamped rows, never copies an
         org-A feed row (Espinoza/Diversey seeded in org A must not appear), and its
         delete+reinsert leaves org A's raw_sales byte-untouched;
       - in `block` mode the promotion STOPS re-inserting a pre-poisoned foreign monthly row
         (the "re-inserted hourly for three weeks" half of the incident), with the row parked
         in the flag payload — nothing silently discarded.

Run:  cd backend && python3 harness_cross_tenant_isolation.py     (exit 0 = isolated)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.modules.commcalc.router as R                     # noqa: E402
from app.modules.commcalc import ingest_store_guard as isg  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


# ── in-memory fake supabase client (same shape as scratchpad/luxelink_sales_flow_proof.py) ──────
class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.t = table
        self.f = []
        self.cnt = False
        self.op = 'select'
        self.ins = None
        self.rng = None

    def select(self, *a, **k):
        if k.get('count') == 'exact':
            self.cnt = True
        return self

    def eq(self, c, v):
        self.f.append(('eq', c, v))
        return self

    def in_(self, c, v):
        self.f.append(('in', c, list(v)))
        return self

    def neq(self, c, v):
        self.f.append(('neq', c, v))
        return self

    def gte(self, c, v):
        self.f.append(('gte', c, v))
        return self

    def lt(self, c, v):
        self.f.append(('lt', c, v))
        return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def order(self, *a, **k):
        return self

    def delete(self):
        self.op = 'delete'
        return self

    def insert(self, rows):
        self.op = 'insert'
        self.ins = rows if isinstance(rows, list) else [rows]
        return self

    def upsert(self, rows, **k):
        self.op = 'upsert'
        self.ins = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, patch):
        self.op = 'update'
        self.ins = [patch]
        return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v:
                return False
            if k == 'in' and rv not in v:
                return False
            if k == 'neq' and rv == v:
                return False
            if k == 'gte' and not (rv is not None and str(rv) >= str(v)):
                return False
            if k == 'lt' and not (rv is not None and str(rv) < str(v)):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.t, [])
        if self.op == 'select':
            m = [r for r in rows if self._m(r)]
            if self.rng:
                a, b = self.rng
                m = m[a:b + 1]
            if self.cnt:
                return FakeResult(data=m, count=len(m))
            return FakeResult(data=[dict(r) for r in m])
        if self.op == 'delete':
            self.store[self.t] = [r for r in rows if not self._m(r)]
            return FakeResult(data=[])
        if self.op in ('insert', 'upsert'):
            for r in self.ins:
                rows.append(dict(r))
            return FakeResult(data=list(self.ins))
        if self.op == 'update':
            for r in rows:
                if self._m(r):
                    r.update(self.ins[0])
            return FakeResult(data=[])
        return FakeResult()


class FakeSchema:
    def __init__(self, store):
        self.store = store

    def table(self, t):
        return FakeQuery(self.store, t)


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, s):
        return FakeSchema(self.store)


def new_client(store=None):
    store = store if store is not None else {}
    c = FakeClient(store)
    R.sb = lambda: c                      # _write_upload_trace etc. stay in-memory
    return c, store


# The approvals intimation bridge is best-effort side decoration; keep the harness hermetic.
_intimations = []
isg._intimate_quarantine = lambda org_id, rows: _intimations.append((org_id, len(rows)))

ORG_A = "org-luxelink"                    # content owner
ORG_B = "org-house"                       # the org the content leaked INTO

# The REAL leaked line shapes (evidence file leaked_rows_evidence.json, 2026-09-03).
DIVERSEY = "4640-A W Diversey Ave"
LEAKED = [
    {"org_id": ORG_B, "period": "July 2026", "store": DIVERSEY, "salesperson": "Espinoza, Carolina",
     "trans_id": "3400", "trans_date": "2026-07-14", "ext_price": 30.0, "department": "System"},
    {"org_id": ORG_B, "period": "July 2026", "store": DIVERSEY, "salesperson": "Espinoza, Carolina",
     "trans_id": "3400", "trans_date": "2026-07-14", "ext_price": 0.0, "department": ""},
    {"org_id": ORG_B, "period": "July 2026", "store": DIVERSEY, "salesperson": "Espinoza, Carolina",
     "trans_id": "3400", "trans_date": "2026-07-14", "ext_price": 0.0, "department": "System"},
    {"org_id": ORG_B, "period": "July 2026", "store": DIVERSEY, "salesperson": "Espinoza, Carolina",
     "trans_id": "3402", "trans_date": "2026-07-14", "ext_price": 40.0, "department": "Rtr"},
    {"org_id": ORG_B, "period": "July 2026", "store": DIVERSEY, "salesperson": "Espinoza, Carolina",
     "trans_id": "3400", "trans_date": "2026-07-14", "ext_price": 29.99, "department": "BrandedHandset"},
    {"org_id": ORG_B, "period": "July 2026", "store": DIVERSEY, "salesperson": "Espinoza, Carolina",
     "trans_id": "3400", "trans_date": "2026-07-14", "ext_price": 0.0, "department": "Handset"},
]
LEGIT_B = [
    {"org_id": ORG_B, "period": "July 2026", "store": "103 Fulton Ave", "salesperson": "Khan, Ismail",
     "trans_id": "9001", "trans_date": "2026-07-14", "ext_price": 55.0, "department": "Handset"},
    {"org_id": ORG_B, "period": "July 2026", "store": "", "salesperson": "LAST, FIRST",
     "trans_id": "9002", "trans_date": "2026-07-14", "ext_price": 5.0, "department": "Ondigo"},
]
ROSTER_B = [{"org_id": ORG_B, "store_code": "B-103", "store_address": "103 Fulton Ave"},
            {"org_id": ORG_B, "store_code": "B-559", "store_address": "559 BROADWAY"}]


def guard_store(mode):
    return {"store_mapping": [dict(r) for r in ROSTER_B],
            "stores": [],
            "store_aliases": [],
            "ingest_store_guard": [{"org_id": ORG_B, "mode": mode, "block_min_rows": 0}],
            "ingest_store_quarantine": []}


print("\n== A. ingest_store_guard on the REAL incident batch ==")
batch = [dict(r) for r in (LEAKED + LEGIT_B)]

c, st = new_client(guard_store("warn"))
res = isg.screen(c, ORG_B, batch, "raw_sales", source="manual", upload_type="sales", period="July 2026")
check("A1. warn: the foreign store is flagged", res["unknown_stores"] == 1
      and res["flags"] and res["flags"][0]["store_raw"] == DIVERSEY, str(res)[:160])
check("A2. warn: all 6 leaked rows counted in the flag", res["rows_flagged"] == 6, str(res["rows_flagged"]))
check("A3. warn NEGATIVE CONTROL: every row is still written (warn alone cannot stop the leak)",
      res["kept"] is batch and res["rows_withheld"] == 0)
n = isg.record(c, ORG_B, res)
qrows = st["ingest_store_quarantine"]
check("A4. warn: the flag lands in the quarantine review queue, org-stamped",
      n == 1 and len(qrows) == 1 and qrows[0]["org_id"] == ORG_B and qrows[0]["store_raw"] == DIVERSEY)

c, st = new_client(guard_store("block"))
res = isg.screen(c, ORG_B, batch, "raw_sales", source="manual", upload_type="sales", period="July 2026")
kept_stores = {r["store"] for r in res["kept"]}
check("A5. block: the 6 foreign rows are withheld", res["rows_withheld"] == 6 and DIVERSEY not in kept_stores)
check("A6. block: legit + blank-store rows pass untouched",
      len(res["kept"]) == 2 and {"103 Fulton Ave", ""} == kept_stores)
check("A7. block: nothing silently discarded — withheld rows parked intact in the flag",
      len(res["flags"][0]["withheld_rows"] or []) == 6)

c, st = new_client(guard_store("off"))
res = isg.screen(c, ORG_B, batch, "raw_sales")
check("A8. off: byte-untouched, no flags", res["kept"] is batch and not res["flags"])

c, st = new_client({"store_mapping": [], "stores": [], "store_aliases": [],
                    "ingest_store_guard": [{"org_id": ORG_B, "mode": "block"}]})
res = isg.screen(c, ORG_B, batch, "raw_sales")
check("A9. empty roster FAILS OPEN even in block mode (a new tenant is never walled off)",
      res["kept"] is batch and res["rows_withheld"] == 0)

res = isg.screen(c, ORG_B, batch, "payout_config")
check("A10. non-guarded tables are never screened", res["kept"] is batch and not res["checked"])


print("\n== B. read/write org isolation with BOTH tenants in one table ==")
# Org A's July: the Espinoza/Diversey content, deliberately REUSING trans_ids 3400/9001 so a
# cross-org dedupe-by-trans_id would be caught red-handed.
FEED_A = [
    {"id": f"a-f{i}", "org_id": ORG_A, "period": "July 2026", "store": DIVERSEY,
     "salesperson": "Espinoza, Carolina", "trans_id": t, "trans_date": "2026-07-14", "ext_price": p}
    for i, (t, p) in enumerate([("3400", 29.99), ("3402", 40.0), ("9001", 12.0)])]
RAW_A = [
    {"id": f"a-r{i}", "org_id": ORG_A, "period": "July 2026", "store": DIVERSEY,
     "salesperson": "Espinoza, Carolina", "trans_id": t, "trans_date": "2026-07-14", "ext_price": p}
    for i, (t, p) in enumerate([("3400", 29.99), ("7777", 65.0)])]
FEED_B = [
    {"id": f"b-f{i}", "org_id": ORG_B, "period": "July 2026", "store": "103 Fulton Ave",
     "salesperson": "Khan, Ismail", "trans_id": t, "trans_date": "2026-07-14", "ext_price": p}
    for i, (t, p) in enumerate([("9001", 55.0), ("9002", 5.0)])]
RAW_B = [
    {"id": f"b-r0", "org_id": ORG_B, "period": "July 2026", "store": "559 BROADWAY",
     "salesperson": "Sharma, Radhika", "trans_id": "8001", "trans_date": "2026-07-02", "ext_price": 20.0}]

base = {"daily_sales_feed": [dict(r) for r in FEED_A + FEED_B],
        "raw_sales": [dict(r) for r in RAW_A + RAW_B],
        "store_mapping": [dict(r) for r in ROSTER_B], "stores": [], "store_aliases": [],
        "ingest_store_guard": [{"org_id": ORG_B, "mode": "warn"}],
        "ingest_store_quarantine": [], "metric_source_of_truth": [],
        "commission_org_config": [], "upload_trace": []}

c, st = new_client({k: [dict(r) for r in v] for k, v in base.items()})
rows, meta = R._sales_rows_union_txn(c, ORG_B, "July 2026", cols="*")
check("B1. union read for org B returns ZERO org-A rows",
      rows and all(r["org_id"] == ORG_B for r in rows), str(meta))
check("B2. …and no org-A content sneaks in under a shared trans_id",
      not any(r.get("store") == DIVERSEY or "Espinoza" in str(r.get("salesperson")) for r in rows))
check("B3. …while org B's own union is complete (feed 9001/9002 + monthly-only 8001)",
      {r["trans_id"] for r in rows} == {"9001", "9002", "8001"}, str({r["trans_id"] for r in rows}))

c, st = new_client({k: [dict(r) for r in v] for k, v in base.items()})
summ = R._promote_feed_to_raw_sales(c, ORG_B, "July 2026", dry_run=False, force=True)
after = st["raw_sales"]
a_after = [r for r in after if r["org_id"] == ORG_A]
b_after = [r for r in after if r["org_id"] == ORG_B]
check("B4. promotion for org B leaves org A's raw_sales byte-untouched",
      sorted((r["id"] for r in a_after)) == ["a-r0", "a-r1"]
      and all(any(r == dict(x) for x in RAW_A) for r in a_after), str(summ)[:160])
check("B5. every promoted row is stamped org B — no org-A feed row was copied",
      b_after and all(r["org_id"] == ORG_B for r in b_after))
check("B6. the Espinoza/Diversey content never crosses into org B",
      not any(r.get("store") == DIVERSEY or "Espinoza" in str(r.get("salesperson")) for r in b_after))
check("B7. org B's result is exactly its own feed + its own monthly-only rows",
      {r["trans_id"] for r in b_after} == {"9001", "9002", "8001"})

# The second half of the incident: a foreign row ALREADY in org B's raw_sales (the 2026-07-14
# mis-file) is carried over as 'monthly_only' by every promotion — warn re-inserts it hourly
# (negative control), block parks it.
poison = {"id": "b-poison", "org_id": ORG_B, "period": "July 2026", "store": DIVERSEY,
          "salesperson": "Espinoza, Carolina", "trans_id": "3400", "trans_date": "2026-07-14",
          "ext_price": 29.99}
for mode, expect_gone, name in (("warn", False, "B8. warn NEGATIVE CONTROL: promotion re-carries a "
                                                "pre-poisoned foreign row (the 3-week hourly re-insert)"),
                                ("block", True, "B9. block: promotion STOPS the re-insert — the "
                                                "poisoned row is withheld and parked")):
    stx = {k: [dict(r) for r in v] for k, v in base.items()}
    stx["raw_sales"].append(dict(poison))
    stx["ingest_store_guard"] = [{"org_id": ORG_B, "mode": mode, "block_min_rows": 0}]
    c, stx = new_client(stx)
    R._promote_feed_to_raw_sales(c, ORG_B, "July 2026", dry_run=False, force=True)
    b_rows = [r for r in stx["raw_sales"] if r["org_id"] == ORG_B]
    has_poison = any(r.get("store") == DIVERSEY for r in b_rows)
    check(name, has_poison is (not expect_gone),
          f"mode={mode} b_stores={sorted({r.get('store') for r in b_rows})}")
    if mode == "block":
        q = stx.get("ingest_store_quarantine") or []
        check("B10. …with the withheld rows parked in quarantine (nothing silently discarded)",
              q and (q[0].get("withheld_rows") or []), str(q)[:120])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
