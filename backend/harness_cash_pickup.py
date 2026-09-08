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

# ═══ 6. ACTUAL CASH PICKED FROM ENVELOPE (owner 2026-09-04; mig 949) — the truth table ═══════════
# The DM confirming a pickup records the ACTUAL cash physically taken beside the declared snapshot.
# Storage: cash_pickup.actual_picked_amount (never envelope_count.counted_amount — that's
# MANAGEMENT's later count). Money posture: declared relieves the cash movement UNLESS the org's
# pickup_actual_relieves_cash knob (default false) is flipped.
from app.modules.closing import pickup_actual as _pa   # noqa: E402

st6 = fresh_store(); wire(st6)
st6["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True}]
st6["daily_closing"] = [dc_row(id="d6", store_code="S1", close_date="2026-09-01", store_cash=100.0)]
cr._notify_pickup = _notify_stub
r6 = asyncio.new_event_loop().run_until_complete(cr.confirm_pickup(
    {"date": "2026-09-01", "picked_up_by": "DM Six",
     "items": [{"store_code": "S1", "store_name": "1 Main St", "employee_name": "Jane Rep",
                "close_date": "2026-09-01", "amount": 100.0, "actual_amount": 90.0}]},
    org_id=HOUSE))
row6 = st6["cash_pickup"][0]
check("6a. confirm with actual: actual_picked_amount saved BESIDE the declared snapshot "
      "(amount unchanged = 100, actual = 90)",
      row6.get("amount") == 100.0 and row6.get("actual_picked_amount") == 90.0, str(row6))
check("6b. confirm response summarizes: actual_total 90, one SHORT variance",
      r6.get("actual_total") == 90.0 and r6.get("variance_short") == 1 and r6.get("variance_over") == 0,
      str(r6))

lp6 = cr.closing_pickups(date="2026-09-01", org_id=HOUSE)
e6 = lp6["envelopes"][0]
check("6c. GET /pickups exposes the new column + variance (envelope-report short/over language: "
      "variance = actual - declared = -10, status 'short')",
      e6.get("actual_picked_amount") == 90.0 and e6.get("pickup_variance") == -10.0
      and e6.get("pickup_variance_status") == "short", str(e6))
check("6d. KNOB OFF (house default): the DECLARED figure still relieves cash on hand — by_store "
      "= 100 declared - 100 declared-outflow = 0, BYTE-IDENTICAL to pre-949",
      lp6["by_store"][0]["cash_on_hand"] == 0.0, str(lp6["by_store"]))

st6["cash_pickup_config"] = [{"org_id": HOUSE, "pickup_actual_relieves_cash": True}]
lp6on = cr.closing_pickups(date="2026-09-01", org_id=HOUSE)
check("6e. KNOB ON (pickup_actual_relieves_cash, owner-gated seed): the ACTUAL relieves the "
      "movement — cash on hand = 100 - 90 = 10 (the short 10 is still sitting in the store)",
      lp6on["by_store"][0]["cash_on_hand"] == 10.0, str(lp6on["by_store"]))

# no-actual fallback: a second envelope confirmed WITHOUT the input keeps declared semantics even
# with the knob on (absence of a count is not evidence of zero cash)
st6["daily_closing"].append(dc_row(id="d7", store_code="S1", close_date="2026-09-02", store_cash=50.0))
asyncio.new_event_loop().run_until_complete(cr.confirm_pickup(
    {"date": "2026-09-02", "picked_up_by": "DM Six",
     "items": [{"store_code": "S1", "store_name": "1 Main St", "employee_name": "Jane Rep",
                "close_date": "2026-09-02", "amount": 50.0}]},
    org_id=HOUSE))
row7 = [r for r in st6["cash_pickup"] if str(r.get("close_date")) == "2026-09-02"][0]
lp7 = cr.closing_pickups(date="2026-09-02", org_id=HOUSE)
check("6f. confirm WITHOUT actual: nothing stored (no fake 0), variance honestly None, and — knob "
      "still ON — the declared 50 relieves that envelope (fallback), total = 150 - 90 - 50 = 10",
      "actual_picked_amount" not in row7
      and lp7["envelopes"][0].get("pickup_variance_status") is None
      and lp7["by_store"][0]["cash_on_hand"] == 10.0,
      f"{row7} / {lp7['by_store']}")

# blank actual clears to None (edit-safe re-confirm), never coerces to 0
asyncio.new_event_loop().run_until_complete(cr.confirm_pickup(
    {"date": "2026-09-01", "picked_up_by": "DM Six",
     "items": [{"store_code": "S1", "store_name": "1 Main St", "employee_name": "Jane Rep",
                "close_date": "2026-09-01", "amount": 100.0, "actual_amount": ""}]},
    org_id=HOUSE))
row6b = [r for r in st6["cash_pickup"] if str(r.get("close_date")) == "2026-09-01"][0]
check("6g. re-confirm with a BLANK actual clears it to None (not recorded ≠ picked zero dollars)",
      row6b.get("actual_picked_amount") is None, str(row6b))

# billpay mirror (mig 942 shared machinery — the sibling gets the column for free)
r6bp = asyncio.new_event_loop().run_until_complete(cr.billpay_confirm_pickup(
    {"date": "2026-09-01", "picked_up_by": "DM Six",
     "items": [{"store_code": "S1", "store_name": "1 Main St", "employee_name": "Jane Rep",
                "close_date": "2026-09-01", "amount": 40.0, "actual_amount": 42.0}]},
    org_id=HOUSE))
bprow = st6["billpay_pickup"][0]
check("6h. BILLPAY MIRROR: same parameterized confirm stores the actual on billpay_pickup "
      "(actual 42 vs declared 40 -> one OVER variance)",
      bprow.get("actual_picked_amount") == 42.0 and r6bp.get("variance_over") == 1, str(bprow))

# pure gates directly: outflow_amount + pickup_totals_by_store_day(actual_wins)
check("6i. pickup_actual.outflow_amount: knob off -> declared ALWAYS (identity); knob on -> "
      "actual where present, declared where not",
      _pa.outflow_amount({"amount": 100.0, "actual_picked_amount": 90.0}, False) == 100.0
      and _pa.outflow_amount({"amount": 100.0, "actual_picked_amount": 90.0}, True) == 90.0
      and _pa.outflow_amount({"amount": 50.0}, True) == 50.0
      and _pa.outflow_amount({"amount": 50.0, "actual_picked_amount": None}, True) == 50.0)
from app.modules.closing.billpay_pickup import pickup_totals_by_store_day as _bptot   # noqa: E402
_bp_rows = [{"store_code": "S1", "close_date": "2026-09-01", "amount": 40.0,
             "actual_picked_amount": 42.0, "picked_up": True}]
check("6j. billpay pickup_totals_by_store_day: default byte-identical (40), actual_wins=True "
      "folds the actual (42)",
      _bptot(_bp_rows)[0] == {"S1": {"2026-09-01": 40.0}}
      and _bptot(_bp_rows, actual_wins=True)[0] == {"S1": {"2026-09-01": 42.0}})

# ═══ 7. The REP filter can only match names the picker can offer (owner report 2026-09-07) ═══════
# "It does not hold sort by rep." `employees=` is an EXACT match, and the picker offered ONLY
# storeops.employees — but 252 of the org's 1,729 closing rows since May carry a name in no roster
# ('Waleed', 'Syed 117', 'Abdul K', 'arif', 'Naima', 'Asad Umar', 'Yasir', 'David', 'Venkata
# Penumatcha'), usually a short form of a real person. Those reps were UNPICKABLE: absent from the
# dropdown, and unmatched by the roster spelling of the same person. The endpoint now returns the rep
# names the DATA carries, which the page unions into the picker. The MATCH rule is unchanged.
st = fresh_store(); wire(st)
st["stores"] = [
    {"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True},
    {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Ohio", "is_active": True},
]
st["daily_closing"] = [
    dc_row(id="r1", store_code="S1", employee_name="Jane Rep", store_cash=50.0),
    dc_row(id="r2", store_code="S1", employee_name="Waleed", store_cash=60.0),
    dc_row(id="r3", store_code="S2", employee_name="Syed 117", store_cash=70.0),
]
_all = cr.closing_pickups(date="2026-07-15", org_id=HOUSE)
check("7a. every rep with an envelope is offered — including the ones no roster knows "
      "(pre-fix these were invisible in the picker and unmatchable by the roster spelling)",
      _all.get("employee_options") == ["Jane Rep", "Syed 117", "Waleed"],
      str(_all.get("employee_options")))
_one = cr.closing_pickups(date="2026-07-15", employees="Waleed", org_id=HOUSE)
check("7b. picking that rep now actually filters (1 envelope, $60)",
      [e["employee_name"] for e in _one["envelopes"]] == ["Waleed"]
      and _one["envelopes"][0]["cash"] == 60.0,
      str(_one["envelopes"]))
check("7c. …and the option list does NOT shrink to the filtered rep — the names are collected "
      "BEFORE the employee filter, so the next pick is still possible",
      _one.get("employee_options") == ["Jane Rep", "Syed 117", "Waleed"],
      str(_one.get("employee_options")))
_scoped = cr.closing_pickups(date="2026-07-15", stores="S1", org_id=HOUSE)
check("7d. the option list RESPECTS the store/market/keyset narrowing — one scope never leaks "
      "another scope's rep names into the picker",
      _scoped.get("employee_options") == ["Jane Rep", "Waleed"],
      str(_scoped.get("employee_options")))
_miss = cr.closing_pickups(date="2026-07-15", employees="Waleed Ahmed", org_id=HOUSE)
check("7e. matching is STILL exact — a roster spelling that no envelope carries matches nothing, "
      "rather than the filter silently widening",
      _miss["envelopes"] == [], str(_miss["envelopes"]))

# ═══ 8. The STORE filter must filter the WHOLE screen, not just the envelope list ════════════════
# Owner 2026-09-07: "the data gets lost when the store is picked and the filter does not work as a
# proper filter" — and earlier, "result screen change to all stores in few second". Both are the same
# defect: `store_set` narrowed the ENVELOPE list but was never applied to `not_closed` (the "stores
# that did not submit a closing" list), so picking ONE store emptied the envelopes while that list
# still showed EVERY store. The screen looked like it had reset to "all stores" exactly when it had
# been filtered hardest.
#
# It bites hardest on a store that has filed nothing — 13 of the 33 stores the picker offers have no
# daily_closing rows at all — because then the envelope list is legitimately empty and the unfiltered
# straggler list is the ONLY thing on screen.
st = fresh_store(); wire(st)
st["stores"] = [
    {"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True},
    {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Texas", "is_active": True},
    {"org_id": HOUSE, "store_code": "S3", "address": "3 Elm Rd", "market": "Texas", "is_active": True},
]
st["daily_closing"] = [dc_row(id="e1", store_code="S1", employee_name="Jane Rep", store_cash=90.0)]
_all = cr.closing_pickups(date="2026-07-15", org_id=HOUSE)
check("8a. with NO store filter, both stores that did not close are listed (S2, S3)",
      sorted(x["store_code"] for x in _all["not_closed"]) == ["S2", "S3"],
      str(_all["not_closed"]))
_one = cr.closing_pickups(date="2026-07-15", stores="S2", org_id=HOUSE)
check("8b. picking S2 narrows the straggler list to S2 — it no longer shows every store "
      "(THE BUG: this used to return S2 AND S3 while the envelope list went empty)",
      [x["store_code"] for x in _one["not_closed"]] == ["S2"], str(_one["not_closed"]))
check("8c. …and picking a store that filed NOTHING gives an empty envelope list beside a straggler "
      "row that says so — the screen answers the question instead of looking broken",
      _one["envelopes"] == [] and len(_one["not_closed"]) == 1,
      f"envelopes={_one['envelopes']} not_closed={_one['not_closed']}")
_s1 = cr.closing_pickups(date="2026-07-15", stores="S1", org_id=HOUSE)
check("8d. picking the store that DID close shows its envelope and no stragglers",
      [e["store_code"] for e in _s1["envelopes"]] == ["S1"] and _s1["not_closed"] == [],
      f"envelopes={_s1['envelopes']} not_closed={_s1['not_closed']}")
_multi = cr.closing_pickups(date="2026-07-15", stores="S2,S3", org_id=HOUSE)
check("8e. a multi-store pick ORs them (S2 and S3), it does not intersect to nothing",
      sorted(x["store_code"] for x in _multi["not_closed"]) == ["S2", "S3"],
      str(_multi["not_closed"]))
_lower = cr.closing_pickups(date="2026-07-15", stores="s2", org_id=HOUSE)
check("8f. the store match stays case-insensitive on this list too",
      [x["store_code"] for x in _lower["not_closed"]] == ["S2"], str(_lower["not_closed"]))
check("8g. the market filter still works alongside it (unchanged)",
      len(cr.closing_pickups(date="2026-07-15", market="Ohio", org_id=HOUSE)["not_closed"]) == 0)

# ══ 9. THE FILTERED ANSWER MUST NOT BE OVERWRITTEN BY A STALE ONE ════════════════════════════════
# OWNER BUG REPORT 2026-09-08, with the screen: Cash Pickup filtered to "103 Fulton Ave" showed the
# filtered rows "for a few seconds then all stores came back as results" — while the filter chip
# stayed on. Section 8 above proves the SERVER filter is right (and a live org-scoped read on
# 2026-09-08 confirmed all 539 August closing rows carry a real store_code, so nothing was slipping
# through the deliberate "never drop an unresolved row" bypass either). The defect was in the page:
# every filter change fires a fetch, and the responses were applied in the order they RETURNED, so a
# slower earlier request landing last replaced the filtered rows with everything.
#
# STATIC ON PURPOSE (the §24 rule): the page compiled, rendered, and simply showed the wrong rows —
# neither tsc nor a build can see a promise resolving out of order. This pins the guard's presence in
# BOTH pickup pages, so removing it fails here instead of in front of a DM.
import os as _os                                                                    # noqa: E402
_FE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "frontend", "src", "app",
                    "(platform)", "closing")
for _page, _fetch in (("pickup", "/api/v1/closing/pickups"),
                      ("billpay-pickup", "/api/v1/closing/billpay-pickups")):
    _path = _os.path.join(_FE, _page, "page.tsx")
    try:
        _src = open(_path).read()
    except Exception as _e:
        check(f"9. {_page}/page.tsx is readable", False, str(_e))
        continue
    check(f"9a[{_page}] the fetch carries a request ticket",
          "const ticket = ++reqSeq.current" in _src and "useRef(0)" in _src)
    check(f"9b[{_page}] a stale response can never call setData",
          "if (ticket === reqSeq.current) setData(d)" in _src)
    check(f"9c[{_page}] nor clear the spinner out from under the live request",
          "if (ticket === reqSeq.current) setLoading(false)" in _src)
    check(f"9d[{_page}] the un-guarded `.then(setData)` shape is gone",
          f"api(`{_fetch}?${{qs}}`).then(setData)" not in _src)
    check(f"9e[{_page}] the store filter is still SENT (a guard must not mask a dropped param)",
          "stores=${encodeURIComponent(" in _src)

# ══ 10. THE ENVELOPE NETS OUT THE BILL-PAY CASH COLLECTED ON THE OTHER SCREEN ════════════════════
# OWNER DIRECTIVE 2026-09-08: "on the cash pick up it shows the full amount but it should only show
# the store cash amount to be picked up, as the epay amount is being declared and picked up on a
# different menu — this is duplicating the total cash", and, asked which figure: "not as declared by
# the employee but as CALCULATED BY THE POS".
#
# Section 10 drives the REAL endpoint. The split rule itself is proven separately and exhaustively in
# harness_billpay_netting.py; what is pinned here is the WIRING: the switch, the source precedence,
# and that the number the DM is told to collect actually changes.
st = fresh_store(); wire(st)
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas",
                 "is_active": True}]
st["daily_closing"] = [
    dc_row(id="n1", store_code="S1", close_date="2026-07-20", employee_name="Jane Rep",
           store_cash=1000.0, t_cash=1000.0, epay_on_cash=600.0),
    dc_row(id="n2", store_code="S1", close_date="2026-07-20", employee_name="Mo Rep",
           store_cash=500.0, t_cash=500.0, epay_on_cash=200.0),
]

def _with_netting(on, pos_cash):
    """Run the endpoint with the tenant switch `on` and the POS sales leg reporting `pos_cash`."""
    _e, _s = cr.billpay_netting_enabled, cr._sales_billpay_for_days
    cr.billpay_netting_enabled = lambda *_a, **_k: on
    cr._sales_billpay_for_days = (lambda *_a, **_k: (
        ({("S1", "2026-07-20"): {"cash": pos_cash}}, "sales_tx", (lambda x: x))
        if pos_cash is not None else ({}, "none", (lambda x: x))))
    _p = cr._pos_billpay_for_days
    cr._pos_billpay_for_days = lambda *_a, **_k: ({}, "none", (lambda x: x))
    try:
        return cr.closing_pickups(date="2026-07-20", org_id=HOUSE)
    finally:
        cr.billpay_netting_enabled, cr._sales_billpay_for_days, cr._pos_billpay_for_days = _e, _s, _p

_by = lambda r: {e["employee_name"]: e for e in r["envelopes"]}

r_off = _with_netting(False, 800.0)
e_off = _by(r_off)
check("10a. switch OFF -> the envelopes are untouched (byte-identical to before this feature)",
      e_off["Jane Rep"]["cash"] == 1000.0 and e_off["Mo Rep"]["cash"] == 500.0,
      str([(k, v["cash"]) for k, v in e_off.items()]))
check("10b. and they say so, rather than claiming to be netted",
      all(e["billpay_basis"] == "off" and e["billpay_note"] is None for e in e_off.values()))

r_on = _with_netting(True, 800.0)
e_on = _by(r_on)
check("10c. switch ON -> the POS $800 comes out, split 3:1 on the reps' own declarations",
      e_on["Jane Rep"]["billpay_netted"] == 600.0 and e_on["Mo Rep"]["billpay_netted"] == 200.0,
      str([(k, v["billpay_netted"]) for k, v in e_on.items()]))
check("10d. the DM is told to collect the NET, not the drawer",
      e_on["Jane Rep"]["cash"] == 400.0 and e_on["Mo Rep"]["cash"] == 300.0,
      str([(k, v["cash"]) for k, v in e_on.items()]))
check("10e. the gross is kept beside it, so nothing is hidden",
      e_on["Jane Rep"]["cash_gross"] == 1000.0 and e_on["Mo Rep"]["cash_gross"] == 500.0)
check("10f. the source is named — this is 'as calculated by the POS', and it says which POS figure",
      all(e["billpay_basis"] == "pos" and str(e["billpay_source"]).startswith("sales:")
          for e in e_on.values()), str([e["billpay_source"] for e in e_on.values()]))
check("10g. and the envelope carries one plain sentence for the DM",
      "bill-pay screen" in (e_on["Jane Rep"]["billpay_note"] or ""), e_on["Jane Rep"]["billpay_note"])

# THE AMOUNT IS THE POS'S, NEVER THE DECLARATION. Same rows, same $800 declared between them, but the
# POS says only $300 of bill-pay cash actually passed through.
r_small = _with_netting(True, 300.0)
e_small = _by(r_small)
check("10h. the reps declared $800 between them but the POS says $300 — $300 is what comes out",
      round(sum(e["billpay_netted"] for e in e_small.values()), 2) == 300.0,
      str([(k, v["billpay_netted"]) for k, v in e_small.items()]))
check("10i. leaving $1,200 of the $1,500 drawer to collect",
      round(sum(e["cash"] for e in e_small.values()), 2) == 1200.0)

# NO POS FIGURE IS NOT A ZERO.
r_none = _with_netting(True, None)
e_none = _by(r_none)
check("10j. switch ON but no POS figure for the store-day -> NOTHING is netted",
      all(e["billpay_basis"] == "none" and e["billpay_netted"] == 0.0 for e in e_none.values()))
check("10k. the envelope keeps its full amount AND says why it was not netted",
      e_none["Jane Rep"]["cash"] == 1000.0
      and "No POS bill-pay figure" in (e_none["Jane Rep"]["billpay_note"] or ""),
      e_none["Jane Rep"]["billpay_note"])

# THE 27.3% OF LIVE ROWS THAT DECLARE MORE BILL-PAY THAN THEY HOLD (owner: flag them for the DM).
st["daily_closing"].append(dc_row(id="n3", store_code="S1", close_date="2026-07-20",
                                  employee_name="Impossible Rep", store_cash=0.0, t_cash=0.0,
                                  epay_on_cash=891.0, envelope_picture="e.jpg"))
r_flag = _by(_with_netting(True, 300.0))
check("10l. a rep declaring $891 of bill-pay against $0.00 of cash is FLAGGED",
      r_flag["Impossible Rep"]["billpay_declared_exceeds_cash"] is True, str(r_flag.get("Impossible Rep")))
check("10m. and their honest neighbours are not",
      r_flag["Jane Rep"]["billpay_declared_exceeds_cash"] is False
      and r_flag["Mo Rep"]["billpay_declared_exceeds_cash"] is False)
check("10n. an envelope with no cash can never be netted below zero",
      r_flag["Impossible Rep"]["cash"] == 0.0 and r_flag["Impossible Rep"]["billpay_netted"] == 0.0)
check("10o. the flag stands even with netting switched OFF — it is a data-entry defect, not a "
      "netting one",
      _by(_with_netting(False, None))["Impossible Rep"]["billpay_declared_exceeds_cash"] is True)

_src_cr = open("app/modules/closing/router.py").read()
check("10p. the switch is per-org config, not a code branch (RULE TWO)",
      "def billpay_netting_enabled" in _src_cr and "pickup_nets_pos_billpay_cash" in _src_cr)
check("10q. it defaults OFF, so an un-migrated database is unchanged",
      "except Exception:\n        return False" in _src_cr)

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
