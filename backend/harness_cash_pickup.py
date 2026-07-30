"""Offline proof harness for the Cash Pickup market-filter fix (OWNER BUG REPORT 2026-07-29, Abid/
Ismail: "when you go to cash pick up and choose a date, there are no dates available to show cash
pickup ... there is no way to save the cash pickup if it is even for the same day but not for a day
other than today").

Root cause (issues 2+3, unified): `GET /closing/pickups` predates retail-ops-14's bucket-aware market
fix (`_market_bucket`/`_resolve_market_filter`, already applied to /closing/summary, /closing/rollup,
/closing/ops-chargebacks/dm-verify) and was never retrofitted — it did a raw exact-string match, so
ANY envelope whose store hadn't resolved a market (blank/mismatched) was silently dropped the instant
a market filter was active. This page auto-applies the logged-in DM's OWN market
(`useEffect` in pickup/page.tsx), so a market-scoped DM lost every envelope at an unresolved-market
store — for ANY date, though which specific dates are affected depends on which days' rows happen to
have a resolvable market, which is why it read as "today works, no other day does" rather than "every
day is broken the same way." With zero envelopes ever shown for the picked date, there was nothing to
check off — hence "no way to save" too (POST /closing/pickup itself has no date restriction at all;
proven directly below).

Run: `cd backend && python3 harness_cash_pickup.py`

No live DB/network — same fake-Supabase-chain-client convention as harness_dmverify_parity.py /
harness_closing_submissions.py, driving the REAL `closing_pickups` / `confirm_pickup` functions.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"


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
    return {"daily_closing": [], "cash_pickup": [], "stores": [], "store_mapping": [],
            "cash_pickup_config": []}


import app.modules.closing.router as cr   # noqa: E402


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    cr._signed_envelope = lambda path: (f"signed://{path}" if path else None)
    return fake


def dc_row(**kw):
    r = {"org_id": HOUSE, "close_date": "2026-07-15", "store_code": "S1", "store_name": "1 Main St",
         "employee_name": "Jane Rep", "store_cash": 90.0, "epay_cash": 0.0, "envelope_picture": None}
    r.update(kw)
    return r


# ═══ 1. THE BUG, reproduced: an unresolved-market store's envelope vanishes for ANY filtered date ═══
# Two stores: S1 resolves to "Texas" (the DM's own market); S2 has NO row in storeops.stores at all
# (an unmapped/renamed store — its market can never resolve). A market-scoped DM (this page
# auto-applies their own market) picks up envelopes on TWO different days; S2's envelope on day 2
# happens to be the one affected — exactly the "today [day 1, S1 only] works, day 2 doesn't" shape of
# the owner's report.
st = fresh_store(); wire(st)
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st["daily_closing"] = [
    dc_row(id="d1", store_code="S1", close_date="2026-07-14", store_cash=50.0),
    dc_row(id="d2", store_code="S2", store_name="2 Oak Ave", close_date="2026-07-15",
           employee_name="Mo Rep", store_cash=70.0),
]
resp_today = cr.closing_pickups(date="2026-07-14", market="Texas", org_id=HOUSE)
check("1a. day 1 (S1, resolves to Texas): envelope visible under the DM's own market filter",
      len(resp_today["envelopes"]) == 1 and resp_today["envelopes"][0]["store_code"] == "S1",
      str(resp_today["envelopes"]))

resp_other_day = cr.closing_pickups(date="2026-07-15", market="Texas", org_id=HOUSE)
check("1b. FIX: day 2 (S2, store NOT in the roster -> unresolved market) — envelope now VISIBLE under "
      "the same market filter (was silently dropped before this fix, reproducing 'no dates available')",
      len(resp_other_day["envelopes"]) == 1 and resp_other_day["envelopes"][0]["store_code"] == "S2",
      str(resp_other_day["envelopes"]))

# ═══ 2. A market filter still correctly EXCLUDES a real, different, resolved market (not a blanket
#         bypass — the fix is bucket-aware, not "market filter no longer does anything") ═══════════
st2 = fresh_store(); wire(st2)
st2["stores"] = [
    {"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True},
    {"org_id": HOUSE, "store_code": "S3", "address": "3 Elm Rd", "market": "Ohio", "is_active": True},
]
st2["daily_closing"] = [
    dc_row(id="tx", store_code="S1", close_date="2026-07-15", store_cash=10.0),
    dc_row(id="oh", store_code="S3", store_name="3 Elm Rd", employee_name="Ana Rep",
           close_date="2026-07-15", store_cash=20.0),
]
resp_tx = cr.closing_pickups(date="2026-07-15", market="Texas", org_id=HOUSE)
check("2. market=Texas -> ONLY the real Texas store shown; a REAL, different, resolved market (Ohio) "
      "is still correctly excluded (this is not a blanket bypass)",
      [e["store_code"] for e in resp_tx["envelopes"]] == ["S1"], str(resp_tx["envelopes"]))

# ═══ 3. No market filter at all -> both envelopes, byte-identical to before this fix ═══════════════
resp_all = cr.closing_pickups(date="2026-07-15", org_id=HOUSE)
check("3. no market filter -> both stores' envelopes visible (unchanged baseline)",
      sorted(e["store_code"] for e in resp_all["envelopes"]) == ["S1", "S3"], str(resp_all["envelopes"]))

# ═══ 4. The "not_closed" stragglers list gets the SAME bucket-aware fix ════════════════════════════
st3 = fresh_store(); wire(st3)
st3["stores"] = [
    {"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True},
    {"org_id": HOUSE, "store_code": "S4", "address": "4 Pine St", "market": "", "is_active": True},
]
st3["daily_closing"] = [dc_row(id="x", store_code="S1", close_date="2026-07-15")]
resp_nc = cr.closing_pickups(date="2026-07-15", market="Texas", org_id=HOUSE)
check("4. not_closed stragglers: a blank-market store (S4) is not silently hidden the moment a market "
      "filter is active, either (never dropped by _market_bucket's '(no market)' bucketing)",
      any(s["store_code"] == "S4" for s in resp_nc["not_closed"]), str(resp_nc["not_closed"]))

# ═══ 5. POST /closing/pickup (confirm_pickup) has NO today-only restriction — proven directly by
#         saving a pickup for a date well in the past, once the envelope is visible ═════════════════
st4 = fresh_store(); wire(st4)
st4["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st4["daily_closing"] = [dc_row(id="past", store_code="S1", close_date="2026-01-05", store_cash=40.0)]
import asyncio   # noqa: E402
async def _notify_stub(*a, **k):
    return []
cr._notify_pickup = _notify_stub
resp_past_pick = cr.closing_pickups(date="2026-01-05", org_id=HOUSE)
check("5a. envelope for a date FAR from today is visible pre-save", len(resp_past_pick["envelopes"]) == 1)
save_resp = asyncio.new_event_loop().run_until_complete(cr.confirm_pickup(
    {"date": "2026-01-05", "picked_up_by": "DM Test",
     "items": [{"store_code": "S1", "store_name": "1 Main St", "employee_name": "Jane Rep",
                "close_date": "2026-01-05", "amount": 40.0, "note": ""}]},
    org_id=HOUSE))
check("5b. POST /closing/pickup SAVES successfully for a non-today date (no code path rejects it)",
      save_resp.get("ok") is True and save_resp.get("count") == 1, str(save_resp))
resp_after = cr.closing_pickups(date="2026-01-05", org_id=HOUSE)
check("5c. after saving, the envelope shows picked_up=True for that SAME non-today date",
      resp_after["envelopes"][0]["picked_up"] is True, str(resp_after["envelopes"]))

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
