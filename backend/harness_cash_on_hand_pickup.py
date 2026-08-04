"""Offline proof harness for "cash on hand needs to be completed along with cash pickup" (OWNER
DIRECTIVE 2026-08-04). No live DB/network — same stateful fake-Supabase-chain-client convention as
harness_cash_pickup.py / harness_dmverify_parity.py, driving the REAL `closing_pickups` / `cash_position`
/ `confirm_pickup` / `undo_pickup` functions (not reimplementations).

GAP FOUND (see docs/handoffs/retail-ops.md for the full writeup): Store Cash on Hand (the report) and
Cash Pickup (the action screen) existed as two DISCONNECTED surfaces. The math was already correctly
wired end-to-end (`_cash_position_core` already nets pickups + EEP withdrawals out of "cash on hand"),
but a DM working the Cash Pickup page — the actual action screen — had ZERO visibility into a store's
TRUE accumulated cash on hand (declared-to-date minus everything already taken, including carryover
from days outside the currently-viewed date/range). And there was no way to UNDO a mis-tapped pickup
confirmation at all — no edit/delete path existed on `cash_pickup` whatsoever.

Built:
  1. GET /closing/pickups now also returns `by_store` (+ `as_of`) — a per-store cash-on-hand summary
     computed via `_cash_position_core`, the SAME function GET /cash-position and
     GET /store-cash-on-hand already call — proven BYTE-IDENTICAL to `cash_position`'s single-day
     `cash_on_hand` for the same store/date (section B).
  2. POST /closing/pickup/undo — resets a mistaken `picked_up=true` row back to ready. Cash-on-hand is
     ALWAYS derived live from `cash_pickup` rows (never a stored running-balance column), so undoing
     re-derives every downstream number with NO drift, by construction — proven across pick up -> undo
     -> re-pick-up giving IDENTICAL numbers to a scenario that never had the undo at all (section D/E).
     Refuses (409) once a disposition (deposited/handed_to_mgmt) is already recorded (section D3);
     idempotent no-op when there's nothing to undo (section D4/D5); org-isolated (section F).

Run: `cd backend && python3 harness_cash_on_hand_pickup.py`
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
        if self.op == "delete":
            keep, removed = [], []
            for r in rows:
                (removed if self._match(r) else keep).append(r)
            rows[:] = keep
            return SimpleNamespace(data=removed)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"daily_closing": [], "cash_pickup": [], "stores": [], "store_mapping": [],
            "cash_pickup_config": [], "bank_deposit": [], "closing_expense": [],
            "envelope_withdrawal": []}


import app.modules.closing.router as cr   # noqa: E402


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
         "envelope_picture": None}
    r.update(kw)
    return r


def confirm(store, org_id, store_code, close_date, employee_name, amount, dm="DM Test"):
    return asyncio.new_event_loop().run_until_complete(cr.confirm_pickup(
        {"date": close_date, "picked_up_by": dm,
         "items": [{"store_code": store_code, "store_name": "1 Main St", "employee_name": employee_name,
                    "close_date": close_date, "amount": amount, "note": ""}]}, org_id=org_id))


# ═══ A. GAP REPRODUCED (pre-fix baseline character): GET /closing/pickups carried NO per-store
#         cash-on-hand info at all — only the currently-viewed envelopes' own totals. Proven here as
#         a POSITIVE assertion that the fix actually adds it (the response key literally didn't exist
#         before this package). ═══════════════════════════════════════════════════════════════════
st = fresh_store(); wire(st)
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st["daily_closing"] = [dc_row(id="d1", close_date="2026-08-01", store_cash=100.0, t_cash=100.0)]
resp = cr.closing_pickups(date="2026-08-01", org_id=HOUSE)
check("A1. GET /closing/pickups response now carries `by_store`", "by_store" in resp and isinstance(resp["by_store"], list))
check("A2. GET /closing/pickups response now carries `as_of` (Day mode = the requested date)",
      resp.get("as_of") == "2026-08-01", str(resp.get("as_of")))
check("A3. by_store has exactly one row for S1", len(resp["by_store"]) == 1 and resp["by_store"][0]["store_code"] == "S1",
      str(resp["by_store"]))
check("A4. S1's cash_on_hand == 100.0 (nothing picked up yet)", resp["by_store"][0]["cash_on_hand"] == 100.0,
      str(resp["by_store"][0]))

# ═══ B. BYTE-IDENTICAL to GET /cash-position's single-day cash_on_hand, for a MULTI-DAY carryover
#         fixture (the exact scenario the gap was about: a DM in Day-mode never saw carryover). ═════
st2 = fresh_store(); wire(st2)
st2["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st2["daily_closing"] = [
    dc_row(id="d1", close_date="2026-08-01", store_cash=100.0, t_cash=100.0),
    dc_row(id="d2", close_date="2026-08-02", store_cash=150.0, t_cash=150.0),
    dc_row(id="d3", close_date="2026-08-03", store_cash=80.0, t_cash=80.0),
]
# day 1 fully picked up; days 2+3 still sitting in the store (carryover).
confirm(st2, HOUSE, "S1", "2026-08-01", "Jane Rep", 100.0)
resp_pickups = cr.closing_pickups(date="2026-08-03", org_id=HOUSE)
resp_cashpos = cr.cash_position(date="2026-08-03", org_id=HOUSE)
s1_pickups = next(r for r in resp_pickups["by_store"] if r["store_code"] == "S1")
s1_cashpos = next(r for r in resp_cashpos["rows"] if r["store_code"] == "S1")
check("B1. Cash Pickup's by_store cash_on_hand == Cash Position's cash_on_hand for the SAME store/date (byte-identical by construction)",
      s1_pickups["cash_on_hand"] == s1_cashpos["cash_on_hand"], f"{s1_pickups['cash_on_hand']} vs {s1_cashpos['cash_on_hand']}")
check("B2. The number itself is correct: 100+150+80 declared - 100 picked = 230 (carryover from days "
      "2+3 IS visible even though the DM is viewing single-day 2026-08-03)",
      s1_pickups["cash_on_hand"] == 230.0, str(s1_pickups))

# ═══ C. Range mode: `as_of` == the range's END date (matches cash_position's range-mode convention) ═
resp_range = cr.closing_pickups(start="2026-08-01", end="2026-08-03", org_id=HOUSE)
check("C1. Range mode: as_of == end date", resp_range.get("as_of") == "2026-08-03", str(resp_range.get("as_of")))
s1_range = next(r for r in resp_range["by_store"] if r["store_code"] == "S1")
check("C2. Range mode by_store cash_on_hand == the same 230.0 (same underlying history either way)",
      s1_range["cash_on_hand"] == 230.0, str(s1_range))

# ═══ D. Undo — edit-safe recording (idempotent, no drift) ═══════════════════════════════════════════
# D1: pick up day 2's $150 -> cash_on_hand drops by exactly 150.
confirm(st2, HOUSE, "S1", "2026-08-02", "Jane Rep", 150.0)
resp_after_pickup = cr.closing_pickups(date="2026-08-03", org_id=HOUSE)
s1_after = next(r for r in resp_after_pickup["by_store"] if r["store_code"] == "S1")
check("D1. after picking up day 2's $150, cash_on_hand == 230 - 150 == 80.0", s1_after["cash_on_hand"] == 80.0, str(s1_after))
resp_day2_only = cr.closing_pickups(date="2026-08-02", org_id=HOUSE)
env_d2 = next(e for e in resp_day2_only["envelopes"] if e["close_date"] == "2026-08-02")
check("D1b. day 2's envelope now shows picked_up=True with a pickup_id", env_d2["picked_up"] is True and env_d2["pickup_id"], str(env_d2))

# D2: undo day 2's pickup -> cash_on_hand returns to EXACTLY 230.0 (no drift), envelope shows ready again.
undo_resp = cr.undo_pickup({"store_code": "S1", "close_date": "2026-08-02", "employee_name": "Jane Rep"}, org_id=HOUSE)
check("D2a. undo_pickup returns ok", undo_resp.get("ok") is True, str(undo_resp))
resp_after_undo = cr.closing_pickups(date="2026-08-03", org_id=HOUSE)
s1_undone = next(r for r in resp_after_undo["by_store"] if r["store_code"] == "S1")
check("D2b. after undo, cash_on_hand is back to EXACTLY 230.0 — same as before the pickup, no drift",
      s1_undone["cash_on_hand"] == 230.0, str(s1_undone))
resp_day2_undone = cr.closing_pickups(date="2026-08-02", org_id=HOUSE)
env_d2_undone = next(e for e in resp_day2_undone["envelopes"] if e["close_date"] == "2026-08-02")
check("D2c. day 2's envelope shows picked_up=False again (back in the 'ready' / still-to-collect list)",
      env_d2_undone["picked_up"] is False, str(env_d2_undone))

# D2d: re-pick it up AFTER the undo -> IDENTICAL numbers to section D1 (undo->redo is a true no-op cycle).
confirm(st2, HOUSE, "S1", "2026-08-02", "Jane Rep", 150.0)
resp_redo = cr.closing_pickups(date="2026-08-03", org_id=HOUSE)
s1_redo = next(r for r in resp_redo["by_store"] if r["store_code"] == "S1")
check("D2d. re-picking up after an undo gives the IDENTICAL cash_on_hand as the original pickup (80.0) "
      "— undo->redo is a true no-op cycle, no drift accumulated",
      s1_redo["cash_on_hand"] == 80.0, str(s1_redo))

# D3: refuse to undo once a disposition (deposit) is recorded.
cr.record_deposit({"store_code": "S1", "close_date": "2026-08-02", "employee_name": "Jane Rep",
                    "disposition": "deposited", "deposit_amount": 150.0, "declared_amount": 150.0}, org_id=HOUSE)
try:
    cr.undo_pickup({"store_code": "S1", "close_date": "2026-08-02", "employee_name": "Jane Rep"}, org_id=HOUSE)
    d3_raised = False
except Exception as e:
    d3_raised = getattr(e, "status_code", None) == 409
check("D3. undo REFUSES (409) once a disposition is already recorded — a completed cash event, "
      "never silently reversed", d3_raised)
resp_after_refused_undo = cr.closing_pickups(date="2026-08-03", org_id=HOUSE)
s1_after_refused = next(r for r in resp_after_refused_undo["by_store"] if r["store_code"] == "S1")
check("D3b. the refused undo made NO state change — cash_on_hand still 80.0", s1_after_refused["cash_on_hand"] == 80.0,
      str(s1_after_refused))

# D4: idempotent no-op — undoing an envelope that was NEVER picked up (day 3, still outstanding).
noop_resp = cr.undo_pickup({"store_code": "S1", "close_date": "2026-08-03", "employee_name": "Jane Rep"}, org_id=HOUSE)
check("D4. undo on a never-picked-up envelope is an idempotent no-op (`already: true`), not an error",
      noop_resp.get("ok") is True and noop_resp.get("already") is True, str(noop_resp))

# D5: double-tap — undo an already-undone (day-1-style clean) pickup twice in a row.
confirm(st2, HOUSE, "S1", "2026-08-03", "Jane Rep", 80.0)
first_undo = cr.undo_pickup({"store_code": "S1", "close_date": "2026-08-03", "employee_name": "Jane Rep"}, org_id=HOUSE)
second_undo = cr.undo_pickup({"store_code": "S1", "close_date": "2026-08-03", "employee_name": "Jane Rep"}, org_id=HOUSE)
check("D5. first undo succeeds, second (double-tap) undo is an idempotent no-op — never a 2nd error/side-effect",
      first_undo.get("ok") is True and not first_undo.get("already") and
      second_undo.get("ok") is True and second_undo.get("already") is True,
      f"{first_undo} / {second_undo}")

# ═══ E. Full multi-day fixture WITH an EEP-approved expense netted in, proving cash_on_hand still
#         re-derives cleanly through pickups + an expense + an edit(undo) — the dispatch's exact ask
#         ("closings, expenses, pickups, edits/deletions"). ═════════════════════════════════════════
st3 = fresh_store(); wire(st3)
st3["stores"] = [{"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Texas", "is_active": True}]
st3["daily_closing"] = [
    dc_row(id="e1", store_code="S2", close_date="2026-08-01", store_cash=100.0, t_cash=100.0, employee_name="Ann"),
    dc_row(id="e2", store_code="S2", close_date="2026-08-02", store_cash=150.0, t_cash=150.0, employee_name="Ann"),
]
st3["closing_expense"] = [{"org_id": HOUSE, "closing_row_id": "e2", "store_code": "S2",
                            "close_date": "2026-08-02", "amount": 20.0, "status": "approved", "paid": False}]
# Expected as-of 08-02, nothing picked up yet: (100 + 150) declared - 20 approved-expense = 230.
resp_e = cr.closing_pickups(date="2026-08-02", org_id=HOUSE)
s2_e = next(r for r in resp_e["by_store"] if r["store_code"] == "S2")
check("E1. cash_on_hand nets an EEP-approved expense too (100+150-20 == 230)", s2_e["cash_on_hand"] == 230.0, str(s2_e))
# Pick up day 1's envelope in full ($100) -> 230 - 100 = 130.
confirm(st3, HOUSE, "S2", "2026-08-01", "Ann", 100.0)
resp_e2 = cr.closing_pickups(date="2026-08-02", org_id=HOUSE)
s2_e2 = next(r for r in resp_e2["by_store"] if r["store_code"] == "S2")
check("E2. after picking up day 1, cash_on_hand == 130.0 (230 - 100)", s2_e2["cash_on_hand"] == 130.0, str(s2_e2))
# Undo it -> back to 230.0 exactly.
cr.undo_pickup({"store_code": "S2", "close_date": "2026-08-01", "employee_name": "Ann"}, org_id=HOUSE)
resp_e3 = cr.closing_pickups(date="2026-08-02", org_id=HOUSE)
s2_e3 = next(r for r in resp_e3["by_store"] if r["store_code"] == "S2")
check("E3. undo re-derives cleanly even with an EEP expense in the mix — back to EXACTLY 230.0, no drift",
      s2_e3["cash_on_hand"] == 230.0, str(s2_e3))

# ═══ F. Multi-tenant org isolation: undo/by_store never cross an org boundary ═══════════════════════
st4 = fresh_store(); wire(st4)
st4["stores"] = [
    {"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True},
    {"org_id": OTHER_ORG, "store_code": "S1", "address": "1 Main St (other tenant)", "market": "Texas", "is_active": True},
]
st4["daily_closing"] = [
    dc_row(id="h1", org_id=HOUSE, store_code="S1", close_date="2026-08-01", store_cash=100.0, t_cash=100.0),
    dc_row(id="o1", org_id=OTHER_ORG, store_code="S1", close_date="2026-08-01", store_cash=999.0, t_cash=999.0),
]
resp_house = cr.closing_pickups(date="2026-08-01", org_id=HOUSE)
resp_other = cr.closing_pickups(date="2026-08-01", org_id=OTHER_ORG)
s1_house = next(r for r in resp_house["by_store"] if r["store_code"] == "S1")
s1_other = next(r for r in resp_other["by_store"] if r["store_code"] == "S1")
check("F1. by_store is org-scoped — house tenant sees its own 100.0, not the other org's 999.0",
      s1_house["cash_on_hand"] == 100.0, str(s1_house))
check("F2. the OTHER org sees its own 999.0 — no cross-tenant leak either direction",
      s1_other["cash_on_hand"] == 999.0, str(s1_other))
# An undo attempt using the OTHER org's org_id against a row that DOES exist for HOUSE must be a
# no-op (can never see/touch it), and HOUSE's own pickup must be left completely untouched.
confirm(st4, HOUSE, "S1", "2026-08-01", "Jane Rep", 100.0)
cross_undo = cr.undo_pickup({"store_code": "S1", "close_date": "2026-08-01", "employee_name": "Jane Rep"}, org_id=OTHER_ORG)
check("F3. cross-tenant undo attempt (OTHER_ORG's id against HOUSE's row) is a no-op (`already`) — "
      "never reaches into another tenant's row", cross_undo.get("ok") is True and cross_undo.get("already") is True,
      str(cross_undo))
resp_house_after = cr.closing_pickups(date="2026-08-01", org_id=HOUSE)
env_house_after = next(e for e in resp_house_after["envelopes"] if e["store_code"] == "S1")
check("F4. HOUSE's own pickup is completely untouched by the cross-tenant attempt (still picked_up=True)",
      env_house_after["picked_up"] is True, str(env_house_after))

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
