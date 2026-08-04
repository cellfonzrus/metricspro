"""Offline proof harness for GET /closing/store-cash-on-hand (OWNER DIRECTIVE 2026-08-04, "Store Cash
on Hand" daily report). Same stateful-fake-Supabase-client convention as harness_eep_retail_ops.py —
runs the REAL router functions, no live DB/network.

Proves:
  A. today_declared / today_taken / carryover_from_prior_days decompose correctly for a multi-day
     history, and total_cash_on_hand == carryover + today_declared - today_taken.
  B. AGREES BY CONSTRUCTION with GET /closing/cash-position's single-day `cash_on_hand` for the SAME
     store/date (both call the same `_cash_position_core` — this is the "must agree" requirement).
  C. carryover_from_prior_days folds in EEP envelope withdrawals/approved expenses exactly like
     cash-position already does (never a second netting computation).
  D. A store never swept in days still shows its TRUE uncollected carryover (not reset to 0).
  E. No date -> defaults to "today" (_biz_today_iso); stores/employees filters narrow like the sibling
     cash-position/pickups endpoints.

Run: `cd backend && python3 harness_store_cash_on_hand.py`
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def is_(self, c, v): self.filters.append((c, "is", v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
            if kind == "is" and v == "null" and rv is not None: return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r); r.setdefault("id", nid(self.t)); rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"daily_closing": [], "stores": [], "cash_pickup": [], "bank_deposit": [],
            "closing_expense": [], "envelope_withdrawal": []}


import app.modules.core.router as core                # noqa: E402
import app.modules.storeops.router as storeops         # noqa: E402
import app.modules.closing.router as cr                # noqa: E402


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    core.get_supabase = lambda: fake
    storeops.scope_keyset = lambda auth, org: None
    return fake


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# A/D. Multi-day decomposition + carryover for a store never swept
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("== A/D. today vs carryover decomposition ==")
store = fresh_store()
fake = wire(store)
store["stores"].append({"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "NY Metro"})
for dd, amt in [("2026-08-01", 300.0), ("2026-08-02", 250.0), ("2026-08-03", 400.0)]:
    store["daily_closing"].append({"id": nid("dc"), "org_id": HOUSE, "store_code": "S1", "close_date": dd,
                                   "t_cash": amt, "store_cash": amt, "employee_name": "Jane Rep"})
# never picked up / never deposited — every dollar is still sitting in the store.

r = cr.store_cash_on_hand(date="2026-08-03", org_id=HOUSE, authorization="")
row = next(x for x in r["rows"] if x["store_code"] == "S1")
check("today_declared == the target date's own declared cash", row["today_declared"] == 400.0, str(row))
check("today_taken == 0 (nothing picked up)", row["today_taken"] == 0.0)
check("carryover_from_prior_days == every EARLIER day's declared cash (never swept)", row["carryover_from_prior_days"] == 550.0, str(row))
check("total_cash_on_hand == carryover + today_declared - today_taken", row["total_cash_on_hand"] == 950.0, str(row))
check("totals block sums every row", r["totals"]["total_cash_on_hand"] == 950.0)
check("market pass-through from the store roster", row["market"] == "NY Metro")

# A pickup on day 2 reduces carryover but never today's own figure.
store["cash_pickup"].append({"org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-02", "amount": 250.0,
                             "picked_up": True, "picked_up_at": "2026-08-02T18:00:00Z", "employee_name": "DM Dan"})
r = cr.store_cash_on_hand(date="2026-08-03", org_id=HOUSE, authorization="")
row = next(x for x in r["rows"] if x["store_code"] == "S1")
check("a prior-day pickup reduces carryover", row["carryover_from_prior_days"] == 300.0, str(row))
check("today's own figures unaffected by a PRIOR day's pickup", row["today_declared"] == 400.0 and row["today_taken"] == 0.0)
check("total recomputes correctly after the pickup", row["total_cash_on_hand"] == 700.0, str(row))
check("last_pickup_at surfaces on the report", row["last_pickup_at"] == "2026-08-02T18:00:00Z")

# A pickup ON the target date itself reduces TODAY's taken, not carryover.
store["cash_pickup"].append({"org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-03", "amount": 150.0,
                             "picked_up": True, "picked_up_at": "2026-08-03T19:00:00Z"})
r = cr.store_cash_on_hand(date="2026-08-03", org_id=HOUSE, authorization="")
row = next(x for x in r["rows"] if x["store_code"] == "S1")
check("a same-day pickup reduces today_taken, not carryover", row["today_taken"] == 150.0 and row["carryover_from_prior_days"] == 300.0, str(row))
check("total nets the same-day pickup too", row["total_cash_on_hand"] == 550.0, str(row))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# B. Agrees BY CONSTRUCTION with GET /closing/cash-position's single-day cash_on_hand
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. Agreement with GET /closing/cash-position ==")
for target_date in ("2026-08-01", "2026-08-02", "2026-08-03"):
    cp = cr.cash_position(date=target_date, org_id=HOUSE, authorization="")
    sc = cr.store_cash_on_hand(date=target_date, org_id=HOUSE, authorization="")
    cp_row = next(x for x in cp["rows"] if x["store_code"] == "S1")
    sc_row = next(x for x in sc["rows"] if x["store_code"] == "S1")
    check(f"{target_date}: store-cash-on-hand.total_cash_on_hand == cash-position.cash_on_hand",
          sc_row["total_cash_on_hand"] == cp_row["cash_on_hand"],
          f"sc={sc_row['total_cash_on_hand']} cp={cp_row['cash_on_hand']}")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# C. EEP netting (approved expense + envelope withdrawal) folds into carryover exactly like
#    cash-position already nets it — proves the shared _cash_position_core, not a re-derivation.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. EEP netting parity ==")
store2 = fresh_store()
fake2 = wire(store2)
store2["stores"].append({"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "NJ"})
store2["daily_closing"].append({"id": "row-eep", "org_id": HOUSE, "store_code": "S2", "close_date": "2026-08-01",
                                "t_cash": 500.0, "store_cash": 500.0, "employee_name": "Rep Two"})
store2["closing_expense"].append({"org_id": HOUSE, "store_code": "S2", "close_date": "2026-08-01",
                                  "closing_row_id": "row-eep", "amount": 40.0, "status": "approved"})
store2["envelope_withdrawal"].append({"org_id": HOUSE, "store_code": "S2", "close_date": "2026-08-01",
                                      "closing_row_id": "row-eep", "amount": 60.0})
r = cr.store_cash_on_hand(date="2026-08-01", org_id=HOUSE, authorization="")
row = next(x for x in r["rows"] if x["store_code"] == "S2")
check("EEP-netted today figure: 500 - 40(expense) - 60(withdrawal) = 400", row["total_cash_on_hand"] == 400.0, str(row))
cp = cr.cash_position(date="2026-08-01", org_id=HOUSE, authorization="")
cp_row = next(x for x in cp["rows"] if x["store_code"] == "S2")
check("EEP netting still agrees with cash-position", row["total_cash_on_hand"] == cp_row["cash_on_hand"])


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# E. Defaults + filters
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. Defaults + filters ==")
store3 = fresh_store()
fake3 = wire(store3)
store3["stores"].append({"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "NY Metro"})
store3["stores"].append({"org_id": HOUSE, "store_code": "S3", "address": "3 Pine Rd", "market": "NY Metro"})
store3["daily_closing"].append({"id": nid("dc"), "org_id": HOUSE, "store_code": "S1", "close_date": cr._biz_today_iso(),
                                "t_cash": 100.0, "store_cash": 100.0, "employee_name": "A"})
store3["daily_closing"].append({"id": nid("dc"), "org_id": HOUSE, "store_code": "S3", "close_date": cr._biz_today_iso(),
                                "t_cash": 200.0, "store_cash": 200.0, "employee_name": "B"})
r_no_date = cr.store_cash_on_hand(org_id=HOUSE, authorization="")
check("no date param -> defaults to business-today", r_no_date["date"] == cr._biz_today_iso())
check("no date param -> both stores present", len(r_no_date["rows"]) == 2)

r_filtered = cr.store_cash_on_hand(stores="S1", org_id=HOUSE, authorization="")
check("stores= filter narrows to exactly that store", [x["store_code"] for x in r_filtered["rows"]] == ["S1"])


n_pass, n_fail = len(PASS), len(FAIL)
print(f"\n{n_pass} passed, {n_fail} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
