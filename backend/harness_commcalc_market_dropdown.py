"""Offline proof (no live DB/network) for the 2026-07-27 owner directive: "populate the market with a
drop down menu ... rather than typing in after the markets have been set up in the system."

Covers the commcalc side of that change — the OPTIONS read behind the dropdown and the server-side
normalization of a submitted market label:

  GET  /commcalc/markets        -> _org_markets      (distinct, non-blank, sorted, org-scoped, union of
                                                      commcalc.store_mapping + storeops.stores)
  PUT  /commcalc/stores/{id}    -> _canonical_market (trim + snap to an existing market's canonical
                                                      casing; blank stays blank = unassigned)

Runs the REAL functions from app.modules.commcalc.router against an in-memory fake Supabase client.
Run: `python3 harness_market_dropdown.py` from backend/.
"""


def run_route(x):
    """Call a commcalc route handler in EITHER shape.

    ASYNC-SWEEP 2026-08-04: commcalc's zero-`await` route handlers were converted from `async def` to
    `def` so FastAPI runs them in the threadpool instead of on the single uvicorn event loop (an
    `async def` doing blocking Supabase I/O froze the whole product for its duration). The only textual
    change was the keyword. This helper awaits a coroutine when it gets one and passes a plain result
    straight through, so the proof works against BOTH shapes and needs no further edit if a handler
    ever legitimately becomes a coroutine again."""
    import asyncio as _a
    return _a.run(x) if _a.iscoroutine(x) else x
import asyncio
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []
        self._mode, self._payload = None, None

    def select(self, *_a, **_k):
        self._mode = self._mode or "select"
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v))
        return self

    def order(self, *_a, **_k):
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = payload
        return self

    def _matches(self, row):
        return all(str(row.get(k)) == str(v) for _, k, v in self.filters)

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
        return Result(matched)


class FakeSchema:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def table(self, t):
        return FakeQuery(self.client.store, (self.name, t))


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, name):
        return FakeSchema(self, name)

    def seed(self, schema, table, rows):
        self.store[(schema, table)] = [dict(r) for r in rows]


fake = FakeClient()

import app.modules.commcalc.router as R  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake

ORG = "org-mkt-1"
ORG2 = "org-mkt-2"


def reset():
    fake.store.clear()
    fake.seed("commcalc", "store_mapping", [
        # ORG — the tenant under test
        {"id": "s1", "org_id": ORG, "store_code": "T-101", "store_address": "1800 Great Neck Rd", "market": "LI"},
        {"id": "s2", "org_id": ORG, "store_code": "T-102", "store_address": "3 Palisade Ave", "market": "LI"},
        {"id": "s3", "org_id": ORG, "store_code": "T-103", "store_address": "44 Fordham Rd", "market": "li"},
        {"id": "s4", "org_id": ORG, "store_code": "T-104", "store_address": "9 Boston Post Rd", "market": "Bronx"},
        {"id": "s5", "org_id": ORG, "store_code": "T-105", "store_address": "12 Yonkers Ave", "market": "bronx"},
        {"id": "s6", "org_id": ORG, "store_code": "T-106", "store_address": "77 Main St", "market": ""},
        {"id": "s7", "org_id": ORG, "store_code": "T-107", "store_address": "5 Elm St", "market": None},
        {"id": "s8", "org_id": ORG, "store_code": "T-108", "store_address": "8 Oak St", "market": "   "},
        {"id": "s9", "org_id": ORG, "store_code": "T-109", "store_address": "2 Apex Way", "market": "apex"},
        # ORG2 — a different tenant, must never bleed into ORG's options
        {"id": "x1", "org_id": ORG2, "store_code": "L-1", "store_address": "1 Lux Blvd", "market": "Miami"},
        {"id": "x2", "org_id": ORG2, "store_code": "L-2", "store_address": "2 Lux Blvd", "market": "li"},
    ])
    fake.seed("storeops", "stores", [
        # a market that lives ONLY on the storeops roster (union source)
        {"id": 1, "org_id": ORG, "store_code": "T-110", "address": "600 Route 9", "market": "Jersey"},
        {"id": 2, "org_id": ORG, "store_code": "T-111", "address": "601 Route 9", "market": " LI "},
        {"id": 3, "org_id": ORG2, "store_code": "L-3", "address": "3 Lux Blvd", "market": "Orlando"},
    ])


def sm(org_id, store_id):
    return next(r for r in fake.store[("commcalc", "store_mapping")]
                if r["org_id"] == org_id and r["id"] == store_id)


# ══ 1: the options read — distinct, non-blank, sorted ═══════════════════════════════════════════
reset()
opts = R._org_markets(fake, ORG)
check("1a options are distinct (no repeated label)", len(opts) == len(set(opts)), opts)
check("1b blank/NULL/whitespace-only markets are EXCLUDED (unassigned is a state, not an option)",
      all(m.strip() for m in opts) and len(opts) == 4, opts)
check("1c sorted case-insensitively", opts == sorted(opts, key=lambda s: (s.casefold(), s)), opts)
check("1d expected option set for ORG", opts == ["apex", "Bronx", "Jersey", "LI"], opts)

# ══ 2: RULE ONE — org isolation ═════════════════════════════════════════════════════════════════
check("2a ORG2's markets never appear in ORG's options", "Miami" not in opts and "Orlando" not in opts, opts)
opts2 = R._org_markets(fake, ORG2)
check("2b ORG2 sees only its OWN markets", opts2 == ["li", "Miami", "Orlando"], opts2)
check("2c ORG's 'apex'/'Bronx'/'Jersey' never leak into ORG2",
      not ({"apex", "Bronx", "Jersey"} & set(opts2)), opts2)
check("2d an org with no rows at all gets an empty list, not an error",
      R._org_markets(fake, "org-with-nothing") == [], R._org_markets(fake, "org-with-nothing"))

# ══ 3: case/whitespace variants collapse to ONE canonical option ════════════════════════════════
check("3a 'LI' (2 rows) + 'li' (1 row) + ' LI ' (storeops) collapse to the majority spelling 'LI'",
      opts.count("LI") == 1 and "li" not in opts, opts)
check("3b a 1-vs-1 tie ('Bronx' vs 'bronx') resolves alphabetically + deterministically to 'Bronx'",
      "Bronx" in opts and "bronx" not in opts, opts)
check("3c repeat calls are stable (same list, same order)", R._org_markets(fake, ORG) == opts, opts)

# ══ 4: union source — a market that exists only on the storeops roster is still offered ═════════
check("4a 'Jersey' (storeops.stores only, never in store_mapping) IS an option", "Jersey" in opts, opts)

# ══ 5: the endpoint itself ══════════════════════════════════════════════════════════════════════
reset()
resp = run_route(R.list_markets(org_id=ORG))
check("5a GET /markets returns {'markets': [...]}", resp == {"markets": ["apex", "Bronx", "Jersey", "LI"]}, resp)
resp2 = run_route(R.list_markets(org_id=ORG2))
check("5b the endpoint is org-scoped end-to-end", resp2 == {"markets": ["li", "Miami", "Orlando"]}, resp2)
try:
    run_route(R.list_markets(org_id=""))
    check("5c a missing org_id is rejected (require_org)", False, "no exception raised")
except Exception as e:
    check("5c a missing org_id is rejected (require_org)", getattr(e, "status_code", None) == 400, e)

# ══ 6: normalization on save — canonical casing of an EXISTING market ═══════════════════════════
reset()
run_route(R.update_store("s4", {"market": "li"}, org_id=ORG))
check("6a picking 'li' where 'LI' already exists STORES 'LI' (no second market bucket)",
      sm(ORG, "s4")["market"] == "LI", sm(ORG, "s4"))
reset()
run_route(R.update_store("s4", {"market": "  lI  "}, org_id=ORG))
check("6b whitespace + mixed case both normalize to the existing canonical 'LI'",
      sm(ORG, "s4")["market"] == "LI", sm(ORG, "s4"))
reset()
run_route(R.update_store("s1", {"market": "JERSEY"}, org_id=ORG))
check("6c canonical casing also snaps to a storeops-only market ('JERSEY' -> 'Jersey')",
      sm(ORG, "s1")["market"] == "Jersey", sm(ORG, "s1"))

# ══ 7: a genuinely NEW market is kept verbatim (trimmed) — that's how one gets created ══════════
reset()
run_route(R.update_store("s6", {"market": "  Westchester  "}, org_id=ORG))
check("7a an unmatched market is stored trimmed + verbatim (new market created)",
      sm(ORG, "s6")["market"] == "Westchester", sm(ORG, "s6"))
check("7b the new market immediately becomes an option for the next store",
      R._org_markets(fake, ORG) == ["apex", "Bronx", "Jersey", "LI", "Westchester"], R._org_markets(fake, ORG))
reset()
run_route(R.update_store("s6", {"market": "New   York"}, org_id=ORG))
check("7c inner whitespace runs are collapsed ('New   York' -> 'New York')",
      sm(ORG, "s6")["market"] == "New York", sm(ORG, "s6"))

# ══ 8: unassigned stays possible and explicit ═══════════════════════════════════════════════════
reset()
run_route(R.update_store("s1", {"market": ""}, org_id=ORG))
check("8a clearing a market stores '' (explicitly unassigned), not a bogus label",
      sm(ORG, "s1")["market"] == "", sm(ORG, "s1"))
reset()
run_route(R.update_store("s1", {"market": "   "}, org_id=ORG))
check("8b whitespace-only is the same explicit unassigned state", sm(ORG, "s1")["market"] == "", sm(ORG, "s1"))
reset()
run_route(R.update_store("s1", {"market": None}, org_id=ORG))
check("8c a null market is unassigned too (never the string 'None')", sm(ORG, "s1")["market"] == "", sm(ORG, "s1"))

# ══ 9: RULE ONE on the write — normalization + update stay inside the tenant ════════════════════
reset()
run_route(R.update_store("x2", {"market": "li"}, org_id=ORG))
check("9a another tenant's store id is NOT updated through this org's PUT",
      sm(ORG2, "x2")["market"] == "li", sm(ORG2, "x2"))
reset()
run_route(R.update_store("x2", {"market": "li"}, org_id=ORG2))
check("9b ORG2's own 'li' is NOT rewritten to ORG's 'LI' (canonicalization is org-scoped)",
      sm(ORG2, "x2")["market"] == "li", sm(ORG2, "x2"))

# ══ 10: non-market updates are untouched by the new code ════════════════════════════════════════
reset()
before = dict(sm(ORG, "s4"))
run_route(R.update_store("s4", {"store_code": "T-104B"}, org_id=ORG))
after = sm(ORG, "s4")
check("10a a store_code-only update leaves market exactly as it was",
      after["market"] == before["market"] and after["store_code"] == "T-104B", after)
try:
    run_route(R.update_store("s4", {"nonsense": 1}, org_id=ORG))
    check("10b a body with no allowed field still 400s (unchanged behaviour)", False, "no exception")
except Exception as e:
    check("10b a body with no allowed field still 400s (unchanged behaviour)",
          getattr(e, "status_code", None) == 400, e)

# ══ 11: degradation — an unreadable source table must never break the editor ════════════════════
reset()


class ExplodingSchema:
    def table(self, _t):
        raise RuntimeError("simulated outage")


class PartlyExplodingClient(FakeClient):
    def __init__(self, boom):
        super().__init__()
        self.boom = boom

    def schema(self, name):
        if name == self.boom:
            return ExplodingSchema()
        return FakeSchema(self, name)


pc = PartlyExplodingClient("storeops")
pc.store = fake.store
check("11a storeops unreachable -> options still returned from store_mapping alone",
      R._org_markets(pc, ORG) == ["apex", "Bronx", "LI"], R._org_markets(pc, ORG))
pc2 = PartlyExplodingClient("commcalc")
pc2.store = fake.store
check("11b store_mapping unreachable -> options still returned from the storeops roster alone",
      R._org_markets(pc2, ORG) == ["Jersey", "LI"], R._org_markets(pc2, ORG))


class FullyExplodingClient(PartlyExplodingClient):
    def schema(self, _name):
        return ExplodingSchema()


check("11c both sources down -> [] (never an exception; the editor still renders)",
      R._org_markets(FullyExplodingClient("x"), ORG) == [], "raised or non-empty")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
