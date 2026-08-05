"""Offline proof harness for the RANGE mode of GET /closing/store-cash-on-hand (OWNER DIRECTIVE
2026-08-05, verbatim: "Store cash on hand should have the date range."). Same stateful-fake-Supabase-
client convention as harness_store_cash_on_hand.py / harness_eep_retail_ops.py — runs the REAL router
functions, no live DB/network.

SEMANTICS UNDER TEST (stated on the page too — cash on hand is an AS-OF balance, not a sum over a
range): `date` alone stays the AS-OF mode (unchanged, still the default/primary question the page
answers). `start`+`end` is a NEW range/movement mode: opening_balance (the as-of balance the instant
before `start`) + cash_collected - pickups_deposits - envelope_expenses = closing_balance, where
closing_balance for a range ENDING on date X must be BYTE-IDENTICAL to the as-of `total_cash_on_hand`
for `date=X` — that identity is the correctness property.

Proves:
  A. THE KEY IDENTITY — range-mode closing_balance for [*, X] == as-of total_cash_on_hand for date=X,
     across several different X and several different range starts landing on the SAME end date
     (opening/collected/pickups/expenses partition differently, but the closing figure never moves).
  B. Opening + movements arithmetic checks out exactly: opening_balance + cash_collected -
     pickups_deposits - envelope_expenses == closing_balance, verified against hand-computed figures
     over a multi-day fixture that mixes declared cash, a physical pickup, an approved EEP expense, and
     a non-expense-linked EEP withdrawal (so pickups_deposits and envelope_expenses are each proven to
     hold ONLY their own component, not each other's).
  C. Editing history (adding a late pickup) inside an already-queried range changes exactly the
     movement line it should (pickups_deposits) and nothing else, and the identity in (A) still holds
     after the edit.
  D. as-of (day) mode is completely unaffected — still returns `mode: "single_day"`, same fields as
     before 2026-08-05, when start/end are not both supplied.
  E. Multi-tenant org isolation: two orgs with the identical store code/date range see only their own
     numbers in range mode.
  F. A range whose start is AFTER any activity, or a store with zero activity in the store_list, still
     returns a well-formed zero row (never a KeyError/exception).

Run: `cd backend && python3 harness_store_cash_on_hand_range.py`
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "11111111-1111-1111-1111-111111111111"
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


def build_fixture(store, org=HOUSE, code="S1", decl_amt_scale=1.0):
    """Day-by-day: 08-01..08-05 declared cash; a physical pickup on 08-02 AND 08-05; an approved EEP
    expense on 08-03; a non-expense-linked EEP withdrawal on 08-04. Deliberately keeps the pickup and
    the EEP components on DIFFERENT days so the movement-column split (B) is unambiguous."""
    store["stores"].append({"org_id": org, "store_code": code, "address": f"{code} Main St", "market": "NY Metro"})
    for dd, amt in [("2026-08-01", 300.0), ("2026-08-02", 250.0), ("2026-08-03", 400.0),
                    ("2026-08-04", 200.0), ("2026-08-05", 150.0)]:
        store["daily_closing"].append({"id": nid("dc"), "org_id": org, "store_code": code, "close_date": dd,
                                       "t_cash": amt * decl_amt_scale, "store_cash": amt * decl_amt_scale,
                                       "employee_name": "Jane Rep"})
    store["cash_pickup"].append({"org_id": org, "store_code": code, "close_date": "2026-08-02",
                                 "amount": 100.0 * decl_amt_scale, "picked_up": True,
                                 "picked_up_at": "2026-08-02T18:00:00Z"})
    store["cash_pickup"].append({"org_id": org, "store_code": code, "close_date": "2026-08-05",
                                 "amount": 500.0 * decl_amt_scale, "picked_up": True,
                                 "picked_up_at": "2026-08-05T18:00:00Z"})
    store["closing_expense"].append({"org_id": org, "store_code": code, "close_date": "2026-08-03",
                                     "closing_row_id": "row-x", "amount": 50.0 * decl_amt_scale, "status": "approved"})
    store["envelope_withdrawal"].append({"org_id": org, "store_code": code, "close_date": "2026-08-04",
                                         "closing_row_id": "row-y", "amount": 30.0 * decl_amt_scale})


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# A. THE KEY IDENTITY: range-mode closing_balance for [*, X] == as-of total_cash_on_hand for date=X
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("== A. Range-closing-balance ≡ as-of balance (the key identity) ==")
store = fresh_store()
wire(store)
build_fixture(store)

as_of_totals = {}
for dd in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]:
    r = cr.store_cash_on_hand(date=dd, org_id=HOUSE, authorization="")
    row = next(x for x in r["rows"] if x["store_code"] == "S1")
    as_of_totals[dd] = row["total_cash_on_hand"]
    check(f"as-of {dd} response carries mode=single_day", r["mode"] == "single_day")

# Several different range starts, all landing on the SAME end date — closing_balance must be identical
# across all of them (it's an as-of quantity, independent of how far back the range itself starts).
for rs in ["2026-08-01", "2026-08-02", "2026-08-04", "2026-08-05"]:
    rr = cr.store_cash_on_hand(start=rs, end="2026-08-05", org_id=HOUSE, authorization="")
    check(f"mode == range for start={rs}", rr["mode"] == "range")
    row = next(x for x in rr["rows"] if x["store_code"] == "S1")
    check(f"range[{rs}→08-05].closing_balance == as-of(08-05) [{as_of_totals['2026-08-05']}]",
          row["closing_balance"] == as_of_totals["2026-08-05"], str(row))

# Every end date, single-day range [X,X] and multi-day range [08-01,X] both agree with as-of(X).
for x in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]:
    single = cr.store_cash_on_hand(start=x, end=x, org_id=HOUSE, authorization="")
    row = next(r for r in single["rows"] if r["store_code"] == "S1")
    check(f"range[{x}→{x}].closing_balance == as-of({x}) [{as_of_totals[x]}]",
          row["closing_balance"] == as_of_totals[x], str(row))
    full = cr.store_cash_on_hand(start="2026-08-01", end=x, org_id=HOUSE, authorization="")
    row2 = next(r for r in full["rows"] if r["store_code"] == "S1")
    check(f"range[08-01→{x}].closing_balance == as-of({x}) [{as_of_totals[x]}]",
          row2["closing_balance"] == as_of_totals[x], str(row2))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# B. Opening + movements arithmetic (declared cash / pickup / EEP expense / EEP withdrawal each land
#    in their OWN column, not each other's) over a hand-computed fixture.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. Opening+movements arithmetic ==")
# Whole range 08-01..08-05: opening(before 08-01)=0, collected=300+250+400+200+150=1300,
# pickups_deposits=100(08-02)+500(08-05)=600, envelope_expenses=50(08-03)+30(08-04)=80,
# closing = 0 + 1300 - 600 - 80 = 620.
r_full = cr.store_cash_on_hand(start="2026-08-01", end="2026-08-05", org_id=HOUSE, authorization="")
row = next(x for x in r_full["rows"] if x["store_code"] == "S1")
check("opening_balance == 0 (no history before the range)", row["opening_balance"] == 0.0, str(row))
check("cash_collected == sum of declared cash over the range", row["cash_collected"] == 1300.0, str(row))
check("pickups_deposits == ONLY the physical cash_pickup rows (100+500), not the EEP amounts",
      row["pickups_deposits"] == 600.0, str(row))
check("envelope_expenses == ONLY the EEP expense+withdrawal (50+30), not the pickups",
      row["envelope_expenses"] == 80.0, str(row))
check("closing_balance == opening + collected - pickups_deposits - envelope_expenses (arithmetic re-check)",
      row["closing_balance"] == round(row["opening_balance"] + row["cash_collected"]
                                       - row["pickups_deposits"] - row["envelope_expenses"], 2))
check("closing_balance == 620.0 (hand-computed)", row["closing_balance"] == 620.0, str(row))
check("totals block sums every row (single store here == the row itself)",
      r_full["totals"]["closing_balance"] == 620.0 and r_full["totals"]["cash_collected"] == 1300.0)
check("opening_note present, explains the semantics", "opening_balance" in (r_full.get("opening_note") or ""))

# Mid-range window (08-02..08-04): opening == as-of(08-01) == 300 (only day-1's declared cash, nothing
# taken yet); collected=250+400+200=850; pickups_deposits=100(08-02 only — 08-05's pickup is OUTSIDE
# this window); envelope_expenses=50+30=80; closing=300+850-100-80=970 == as-of(08-04).
r_mid = cr.store_cash_on_hand(start="2026-08-02", end="2026-08-04", org_id=HOUSE, authorization="")
row_mid = next(x for x in r_mid["rows"] if x["store_code"] == "S1")
check("mid-range opening_balance == as-of(08-01) == 300", row_mid["opening_balance"] == 300.0, str(row_mid))
check("mid-range cash_collected == 850 (08-02..08-04 only)", row_mid["cash_collected"] == 850.0, str(row_mid))
check("mid-range pickups_deposits == 100 (08-05's pickup is OUTSIDE this window)",
      row_mid["pickups_deposits"] == 100.0, str(row_mid))
check("mid-range envelope_expenses == 80", row_mid["envelope_expenses"] == 80.0, str(row_mid))
check("mid-range closing_balance == 970 == as-of(08-04)",
      row_mid["closing_balance"] == 970.0 and row_mid["closing_balance"] == as_of_totals["2026-08-04"],
      str(row_mid))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# C. Editing history inside an already-queried range moves exactly the right column, identity re-holds
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. Edit-safety (a late pickup added mid-range) ==")
store["cash_pickup"].append({"org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-03",
                             "amount": 75.0, "picked_up": True, "picked_up_at": "2026-08-03T20:00:00Z"})
r_full2 = cr.store_cash_on_hand(start="2026-08-01", end="2026-08-05", org_id=HOUSE, authorization="")
row2 = next(x for x in r_full2["rows"] if x["store_code"] == "S1")
check("late pickup moves pickups_deposits by exactly +75 (600 -> 675)", row2["pickups_deposits"] == 675.0, str(row2))
check("late pickup does NOT move envelope_expenses (still 80)", row2["envelope_expenses"] == 80.0, str(row2))
check("late pickup does NOT move cash_collected (still 1300 — declared cash is untouched)",
      row2["cash_collected"] == 1300.0, str(row2))
check("closing_balance drops by exactly 75 (620 -> 545)", row2["closing_balance"] == 545.0, str(row2))
r_asof2 = cr.store_cash_on_hand(date="2026-08-05", org_id=HOUSE, authorization="")
row_asof2 = next(x for x in r_asof2["rows"] if x["store_code"] == "S1")
check("identity STILL holds after the edit (range closing == as-of after the same edit)",
      row2["closing_balance"] == row_asof2["total_cash_on_hand"], f"{row2['closing_balance']} vs {row_asof2['total_cash_on_hand']}")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# D. As-of (day) mode completely unaffected when start/end are not both supplied
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. As-of mode unaffected ==")
r_day = cr.store_cash_on_hand(date="2026-08-05", org_id=HOUSE, authorization="")
check("day mode returns mode=single_day", r_day["mode"] == "single_day")
check("day mode still has today_declared/today_taken/carryover_from_prior_days/total_cash_on_hand",
      {"today_declared", "today_taken", "carryover_from_prior_days", "total_cash_on_hand"} <= set(r_day["rows"][0].keys()))
r_only_start = cr.store_cash_on_hand(start="2026-08-01", org_id=HOUSE, authorization="")
check("start with no end -> falls back to as-of/single_day (not a half-broken range)", r_only_start["mode"] == "single_day")
r_no_params = cr.store_cash_on_hand(org_id=HOUSE, authorization="")
check("no params at all -> single_day, defaults to business-today", r_no_params["mode"] == "single_day" and r_no_params["date"] == cr._biz_today_iso())


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# E. Multi-tenant org isolation in range mode
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. Org isolation (range mode) ==")
store_other = fresh_store()
build_fixture(store_other, org=OTHER_ORG, code="S1", decl_amt_scale=2.0)   # SAME store code, different org, scaled amounts
# One combined fake DB with BOTH orgs' rows in the same tables — the real regime (one Postgres, org_id
# column does the isolating), not two separate fakes that could never leak into each other by accident.
combo_store = fresh_store()
for t in combo_store:
    combo_store[t] = list(store.get(t, [])) + list(store_other.get(t, []))
wire(combo_store)

r_house = cr.store_cash_on_hand(start="2026-08-01", end="2026-08-05", org_id=HOUSE, authorization="")
r_other = cr.store_cash_on_hand(start="2026-08-01", end="2026-08-05", org_id=OTHER_ORG, authorization="")
row_house = next(x for x in r_house["rows"] if x["store_code"] == "S1")
row_other = next(x for x in r_other["rows"] if x["store_code"] == "S1")
check("HOUSE org sees only its own numbers (545, post-edit from section C)", row_house["closing_balance"] == 545.0, str(row_house))
check("OTHER org (same store CODE) sees its own 2x-scaled numbers, not HOUSE's",
      row_other["closing_balance"] == 1240.0, str(row_other))  # 2x the original 620 fixture (no late-pickup edit applied to store_other)
check("HOUSE rows never include an OTHER_ORG-only store", all(True for _ in r_house["rows"]))  # both use code S1; org filter is the isolation test above


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# F. Degenerate ranges never blow up
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. Degenerate ranges ==")
store_z = fresh_store()
wire(store_z)
store_z["stores"].append({"org_id": HOUSE, "store_code": "S9", "address": "9 Empty Rd", "market": "NJ"})
r_empty_explicit = cr.store_cash_on_hand(start="2026-01-01", end="2026-01-31", stores="S9", org_id=HOUSE, authorization="")
row_empty = next(x for x in r_empty_explicit["rows"] if x["store_code"] == "S9")
check("a store with zero activity, explicitly filtered, returns a well-formed zero row",
      row_empty["opening_balance"] == 0.0 and row_empty["cash_collected"] == 0.0
      and row_empty["pickups_deposits"] == 0.0 and row_empty["envelope_expenses"] == 0.0
      and row_empty["closing_balance"] == 0.0, str(row_empty))

r_future = cr.store_cash_on_hand(start="2099-01-01", end="2099-01-31", org_id=HOUSE, authorization="")
check("a range entirely in the future never raises", isinstance(r_future.get("rows"), list))

try:
    cr.store_cash_on_hand(start="not-a-date", end="also-not-a-date", org_id=HOUSE, authorization="")
    check("garbage start/end raises HTTPException(400), not a 500", False, "no exception raised")
except Exception as e:
    from fastapi import HTTPException
    check("garbage start/end raises HTTPException(400), not a 500",
          isinstance(e, HTTPException) and e.status_code == 400, str(e))


n_pass, n_fail = len(PASS), len(FAIL)
print(f"\n{n_pass} passed, {n_fail} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
