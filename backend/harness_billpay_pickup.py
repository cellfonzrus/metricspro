"""Offline proof harness for BILL PAYMENT PICKUP & DEPOSIT (owner directive 2026-09-02, mig 942)
plus the same-day follow-up (submit-form wording is UI-only; here: the POS cross-check math and
the management cash-recon gate). No live DB/network — same stateful fake-Supabase-chain-client
convention as harness_cash_pickup.py / harness_cash_on_hand_pickup.py, driving the REAL router
functions (billpay_pickups / billpay_confirm_pickup / billpay_undo_pickup /
billpay_record_deposit / _cash_position_core / cash_recon_management), not reimplementations.

WHAT IS PROVEN
  A. The bill-pay envelope list mirrors /pickups on the bill-pay side: envelope amount =
     declared ePay-on-cash; rows without bill-pay cash are excluded; the GENERAL /pickups list
     is untouched by billpay rows.
  B. Movement math: per-store bill-pay position = declared-to-date − picked-to-date (multi-day
     carryover included), DM-verified dm_epay_cash replacing a verified day's declared figure.
  C. Same process as cash: confirm → undo (idempotent) → 409 once a disposition is recorded;
     deposit's declared default = the envelope's ePay-on-cash.
  D. NO-DOUBLE-COUNT INVARIANTS (the money core):
       relief knob OFF (house default) → the general cash movement (_cash_position_core) is
       BYTE-IDENTICAL with and without billpay pickup rows — the same physical dollars, already
       inside the declared cash the general envelope sweeps, are never relieved twice;
       relief knob ON (split-envelope org) → each billpay pickup folds in exactly ONCE, and the
       pickup_by_store_day + eep_by_store_day == pick_by_store_day breakdown invariant holds;
       the mig-938 BS line (store_cash_cells) keeps its zero floor even under a pathological
       double-relief (full pickup AND billpay pickup with the knob on) — floored + reported.
  E. Management cash-recon GATE (owner: "employee is gated out of it, dm is gated out of it
     only market manager and above see it"): the resolve_recon_access truth table (fail-closed)
     and can_see_cash_recon's platform-parity carve-out; plus the endpoint's row assembly
     (declared splits, pickup columns, mismatch flag path).
  F. Org isolation: another org's rows never appear, cross-tenant undo is a no-op.

Run: `cd backend && python3 harness_billpay_pickup.py`
"""
import sys
import asyncio
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "11111111-1111-1111-1111-111111111111"


class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload, self.on_conflict = "select", None, None
        self.filters = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def upsert(self, rows, on_conflict=None, **k):
        self.op = "upsert"; self.payload = rows; self.on_conflict = on_conflict; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
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
                r = dict(r); r.setdefault("id", f"id-{len(rows) + 1}")
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "upsert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            key_cols = [c.strip() for c in (self.on_conflict or "").split(",") if c.strip()]
            out = []
            for r in payload:
                r = dict(r)
                existing = None
                if key_cols:
                    for row in rows:
                        if all(row.get(c) == r.get(c) for c in key_cols):
                            existing = row; break
                if existing is not None:
                    existing.update(r); out.append(dict(existing))
                else:
                    r.setdefault("id", f"id-{len(rows) + 1}")
                    rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload); out.append(dict(r))
            return SimpleNamespace(data=out)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"daily_closing": [], "cash_pickup": [], "billpay_pickup": [], "stores": [],
            "store_mapping": [], "cash_pickup_config": [], "billpay_pickup_config": [],
            "bank_deposit": [], "closing_expense": [], "envelope_withdrawal": [],
            "daily_closing_verification": [], "app_config": [], "tenants": []}


import app.modules.closing.router as cr                     # noqa: E402
import app.modules.closing.billpay_pickup as bp             # noqa: E402
from app.modules.account.balance_sheet import store_cash_cells   # noqa: E402


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    cr._signed_envelope = lambda path: (f"signed://{path}" if path else None)

    async def _notify_stub(*a, **k):
        return []
    cr._notify_pickup = _notify_stub
    return fake


def dc_row(**kw):
    r = {"org_id": HOUSE, "close_date": "2026-08-01", "store_code": "S1", "store_name": "1 Main St",
         "employee_name": "Jane Rep", "store_cash": 100.0, "epay_cash": 0.0, "t_cash": 100.0,
         "epay_on_cash": 0.0, "epay_on_credit": 0.0, "envelope_picture": None}
    r.update(kw)
    return r


def bconfirm(org_id, close_date, amount, store_code="S1", emp="Jane Rep", dm="DM Test"):
    return asyncio.new_event_loop().run_until_complete(cr.billpay_confirm_pickup(
        {"date": close_date, "picked_up_by": dm,
         "items": [{"store_code": store_code, "store_name": "1 Main St", "employee_name": emp,
                    "close_date": close_date, "amount": amount, "note": ""}]}, org_id=org_id))


# ═══ A. Bill-pay envelope list mirrors /pickups on the BILL-PAY side ═══════════════════════════
st = fresh_store(); wire(st)
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st["daily_closing"] = [
    dc_row(id="d1", close_date="2026-08-01", t_cash=436.0, store_cash=436.0, epay_on_cash=385.0),
    dc_row(id="d2", close_date="2026-08-01", employee_name="No Epay Rep", t_cash=50.0, store_cash=50.0, epay_on_cash=0.0),
]
resp = cr.billpay_pickups(date="2026-08-01", org_id=HOUSE)
check("A1. one bill-pay envelope (the epay_on_cash>0 row); the zero-epay row is excluded",
      len(resp["envelopes"]) == 1 and resp["envelopes"][0]["employee_name"] == "Jane Rep", str(resp["envelopes"]))
check("A2. envelope amount = the declared ePay-on-cash (385.0), NOT the full cash (436.0)",
      resp["envelopes"][0]["cash"] == 385.0, str(resp["envelopes"][0]))
check("A3. by_store bill-pay position: declared 385, picked 0, pending 385",
      resp["by_store"][0]["billpay_declared"] == 385.0 and resp["by_store"][0]["billpay_pending"] == 385.0,
      str(resp["by_store"]))
resp_cash = cr.closing_pickups(date="2026-08-01", org_id=HOUSE)
check("A4. the GENERAL /pickups list still carries BOTH reps' FULL cash (436 + 50) — untouched by the billpay module",
      len(resp_cash["envelopes"]) == 2 and resp_cash["total_cash"] == 486.0, str(resp_cash["total_cash"]))

# ═══ B. Movement math: carryover + DM-verified dm_epay_cash replacement ════════════════════════
st2 = fresh_store(); wire(st2)
st2["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st2["daily_closing"] = [
    dc_row(id="d1", close_date="2026-08-01", epay_on_cash=100.0),
    dc_row(id="d2", close_date="2026-08-02", epay_on_cash=150.0),
    dc_row(id="d3", close_date="2026-08-03", epay_on_cash=80.0),
]
bconfirm(HOUSE, "2026-08-01", 100.0)
resp2 = cr.billpay_pickups(date="2026-08-03", org_id=HOUSE)
s1 = resp2["by_store"][0]
check("B1. multi-day carryover: declared 330 − picked 100 = pending 230 as of day 3 (Day-mode view)",
      s1["billpay_declared"] == 330.0 and s1["billpay_picked"] == 100.0 and s1["billpay_pending"] == 230.0, str(s1))
# a DM verifies day 2 and corrects the ePay-on-cash split from 150 → 120
st2["daily_closing_verification"] = [{"org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-02",
                                      "verified": True, "dm_epay_cash": 120.0}]
resp2v = cr.billpay_pickups(date="2026-08-03", org_id=HOUSE)
s1v = resp2v["by_store"][0]
check("B2. DM-verified dm_epay_cash REPLACES the verified day (150→120): declared 300, pending 200",
      s1v["billpay_declared"] == 300.0 and s1v["billpay_pending"] == 200.0, str(s1v))

# ═══ C. Same process as cash: confirm → undo → disposition guard; declared default ═════════════
st3 = fresh_store(); wire(st3)
st3["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st3["daily_closing"] = [dc_row(id="d1", close_date="2026-08-01", epay_on_cash=200.0, t_cash=500.0, store_cash=500.0)]
bconfirm(HOUSE, "2026-08-01", 200.0)
r_list = cr.billpay_pickups(date="2026-08-01", org_id=HOUSE)
check("C1. after confirm, the envelope shows picked_up=True", r_list["envelopes"][0]["picked_up"] is True)
r_undo = cr.billpay_undo_pickup({"store_code": "S1", "close_date": "2026-08-01", "employee_name": "Jane Rep"}, org_id=HOUSE)
check("C2. undo works (edit-safe recording), envelope back to ready",
      r_undo.get("ok") is True and not r_undo.get("already")
      and cr.billpay_pickups(date="2026-08-01", org_id=HOUSE)["envelopes"][0]["picked_up"] is False)
r_undo2 = cr.billpay_undo_pickup({"store_code": "S1", "close_date": "2026-08-01", "employee_name": "Jane Rep"}, org_id=HOUSE)
check("C3. double-undo is an idempotent no-op (`already`), never an error", r_undo2.get("already") is True)
bconfirm(HOUSE, "2026-08-01", 200.0)
r_dep = cr.billpay_record_deposit({"store_code": "S1", "close_date": "2026-08-01", "employee_name": "Jane Rep",
                                   "disposition": "deposited", "deposit_amount": 200.0}, org_id=HOUSE)
check("C4. deposit's declared default = the envelope's ePay-on-cash (200.0, NOT the full 500 cash) → matched",
      r_dep.get("declared_amount") == 200.0 and r_dep.get("matched") is True, str(r_dep))
try:
    cr.billpay_undo_pickup({"store_code": "S1", "close_date": "2026-08-01", "employee_name": "Jane Rep"}, org_id=HOUSE)
    check("C5. undo AFTER a recorded disposition refuses (409)", False, "no exception raised")
except Exception as e:
    check("C5. undo AFTER a recorded disposition refuses (409)", getattr(e, "status_code", None) == 409, str(e))

# ═══ D. NO-DOUBLE-COUNT: the money core ════════════════════════════════════════════════════════
def _core(fake, org=HOUSE, as_of="2026-08-31"):
    return cr._cash_position_core(fake, org, as_of, [], [], None)

st4 = fresh_store(); f4 = wire(st4)
st4["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st4["daily_closing"] = [dc_row(id="d1", close_date="2026-08-01", t_cash=436.0, store_cash=436.0, epay_on_cash=385.0)]
base = _core(f4)
# the DM picks up the FULL general envelope (436, ePay included) AND records the billpay pickup (385)
asyncio.new_event_loop().run_until_complete(cr.confirm_pickup(
    {"date": "2026-08-01", "picked_up_by": "DM",
     "items": [{"store_code": "S1", "employee_name": "Jane Rep", "close_date": "2026-08-01", "amount": 436.0}]},
    org_id=HOUSE))
bconfirm(HOUSE, "2026-08-01", 385.0)
off = _core(f4)
check("D1. knob OFF (house default): billpay pickups NEVER touch the general movement — taken = 436 only "
      "(the same physical dollars are not relieved twice)",
      off[2]["S1"]["2026-08-01"] == 436.0, str(off[2]))
check("D2. knob OFF: breakdown invariant pickup + eep == pick holds",
      off[6]["S1"]["2026-08-01"] + off[7].get("S1", {}).get("2026-08-01", 0.0) == off[2]["S1"]["2026-08-01"])
st4["cash_pickup_config"] = [{"org_id": HOUSE, "billpay_relieves_cash": True}]
on = _core(f4)
check("D3. knob ON (split-envelope org): the billpay pickup folds in exactly ONCE — taken = 436 + 385 = 821",
      on[2]["S1"]["2026-08-01"] == 821.0, str(on[2]))
check("D4. knob ON: breakdown invariant pickup + eep == pick STILL holds (billpay folds into both)",
      on[6]["S1"]["2026-08-01"] + on[7].get("S1", {}).get("2026-08-01", 0.0) == on[2]["S1"]["2026-08-01"])
check("D5. declared side is identical under both knob values (the knob only touches outflows)",
      off[1] == on[1], f"{off[1]} vs {on[1]}")
# mig-938 BS line: even the pathological double-relief (D3's 821 taken vs 436 declared) can never
# book a negative asset — floored to zero and reported, per the 5fc0c02 zero-floor fix.
cells, meta = store_cash_cells(on[1], on[2], {("S1", "2026-08-01")}, "verified", "2026-08-31")
check("D6. mig-938 store_cash_cells: double-relief floors at 0 (never a negative cash asset) and REPORTS it",
      cells.get("S1") is None and meta["floored"].get("S1") == 385.0, f"cells={cells} meta_floored={meta['floored']}")
cells_off, meta_off = store_cash_cells(off[1], off[2], {("S1", "2026-08-01")}, "verified", "2026-08-31")
check("D7. knob OFF: BS line = declared − general pickup = 0 exactly, nothing floored (clean books)",
      cells_off.get("S1") is None and meta_off["floored"] == {}, f"cells={cells_off} floored={meta_off['floored']}")

# ═══ E. Management cash-recon gate + assembly ══════════════════════════════════════════════════
DEF_ROLES = None  # built-in default list ("market manager and above")
check("E1. gate: scope 'all' (company-wide role) passes", bp.resolve_recon_access("director", "all", DEF_ROLES) is True)
check("E2. gate: market_manager passes (the owner's threshold)", bp.resolve_recon_access("Market Manager", "market", DEF_ROLES) is True)
check("E3. gate: district_manager is OUT (owner: 'dm is gated out of it')",
      bp.resolve_recon_access("district_manager", "market", DEF_ROLES) is False)
check("E4. gate: 'dm' alias is OUT", bp.resolve_recon_access("DM", "market", DEF_ROLES) is False)
check("E5. gate: store employee/rep is OUT (owner: 'the employee is gated out')",
      bp.resolve_recon_access("sales_rep", "store", DEF_ROLES) is False)
check("E6. gate: unresolvable role + narrow scope fails CLOSED", bp.resolve_recon_access("", "store", DEF_ROLES) is False)
check("E7. gate: tenant override list is honored (config over code — RULE TWO)",
      bp.resolve_recon_access("regional_lead", "market", ["regional_lead"]) is True
      and bp.resolve_recon_access("market_manager", "market", ["regional_lead"]) is False)

st5 = fresh_store(); f5 = wire(st5)
st5["app_config"] = [{"id": 1, "rbac_enabled": True}]
check("E8. can_see_cash_recon: NO token while login IS enforced → hidden (fail closed)",
      bp.can_see_cash_recon("", HOUSE, f5) is False)
st5["app_config"] = [{"id": 1, "rbac_enabled": False}]
check("E9. can_see_cash_recon: open-app parity (no token, enforcement OFF) → allowed (platform convention)",
      bp.can_see_cash_recon("", HOUSE, f5) is True)

# endpoint assembly (open-app parity lets the fake through the gate)
st6 = fresh_store(); f6 = wire(st6)
st6["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st6["daily_closing"] = [
    dc_row(id="d1", close_date="2026-08-01", t_cash=436.0, store_cash=436.0, epay_on_cash=385.0,
           epay_on_credit=20.0, t_credit=120.0),
]
asyncio.new_event_loop().run_until_complete(cr.confirm_pickup(
    {"date": "2026-08-01", "picked_up_by": "DM",
     "items": [{"store_code": "S1", "employee_name": "Jane Rep", "close_date": "2026-08-01", "amount": 436.0}]},
    org_id=HOUSE))
bconfirm(HOUSE, "2026-08-01", 385.0)
recon = cr.cash_recon_management(date="2026-08-01", org_id=HOUSE)
row = recon["rows"][0]
check("E10. one-screen row carries ALL the owner-named columns: cash/credit declared, ePay splits, both pickups",
      row["cash_declared"] == 436.0 and row["credit_declared"] == 120.0
      and row["epay_cash_declared"] == 385.0 and row["epay_credit_declared"] == 20.0
      and row["cash_pickup"] == 436.0 and row["billpay_pickup"] == 385.0, str(row))
check("E11. with NO processor feed resolved: pos_billpay is None and status 'no_pos_data' — never a fake mismatch",
      row["pos_billpay"] is None and row["billpay_status"] == "no_pos_data", str(row))
# the POS cross-check math itself (pure): declared vs POS-reported bill payments
rows_mm, summ = bp.billpay_pos_mismatch({("S1", "2026-08-01"): 405.0, ("S2", "2026-08-01"): 100.0},
                                        {("S1", "2026-08-01"): 405.5, ("S2", "2026-08-01"): 250.0}, tolerance=1.0)
check("E12. POS cross-check: within tolerance → ok; beyond → mismatch with signed delta (declared − POS)",
      rows_mm[0]["status"] == "ok" and rows_mm[1]["status"] == "mismatch" and rows_mm[1]["delta"] == -150.0,
      str(rows_mm))
check("E13. POS cross-check: a day present on only ONE side still compares against 0 (never dropped)",
      bp.billpay_pos_mismatch({("S3", "2026-08-02"): 50.0}, {}, 1.0)[0][0]["status"] == "mismatch")

# ═══ F. Org isolation ══════════════════════════════════════════════════════════════════════════
st7 = fresh_store(); wire(st7)
st7["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st7["daily_closing"] = [
    dc_row(id="d1", close_date="2026-08-01", epay_on_cash=100.0),
    dc_row(id="d2", org_id=OTHER_ORG, close_date="2026-08-01", store_code="X9", epay_on_cash=999.0),
]
st7["billpay_pickup"] = [{"org_id": OTHER_ORG, "close_date": "2026-08-01", "store_code": "X9",
                          "employee_name": "Jane Rep", "amount": 999.0, "picked_up": True}]
r_iso = cr.billpay_pickups(date="2026-08-01", org_id=HOUSE)
check("F1. another org's closings/pickups NEVER appear (org-scoped, fail closed)",
      len(r_iso["envelopes"]) == 1 and r_iso["total_cash"] == 100.0
      and all(b["store_code"] != "X9" for b in r_iso["by_store"]), str(r_iso["envelopes"]))
r_xundo = cr.billpay_undo_pickup({"store_code": "X9", "close_date": "2026-08-01", "employee_name": "Jane Rep"},
                                 org_id=HOUSE)
check("F2. cross-tenant undo attempt is a no-op (`already`) — never reaches the other tenant's row",
      r_xundo.get("already") is True and st7["billpay_pickup"][0]["picked_up"] is True)

# ── Summary ────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
