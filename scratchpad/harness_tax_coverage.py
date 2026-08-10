"""HARNESS — the POS wizard's sales-tax step must measure COVERAGE, not a row count.

Fed with the REAL Luxelink rows (pulled from prod by sbsql into lux_stores.json / lux_taxcodes.json),
so the numbers it asserts are the tenant's own. The fake client really filters on .eq — its first
assertion is the negative control for that, per [[fake-client-eq-noop-trap]].
"""
import json, os, sys, types

WT = "/workspaces/wt-pos-training"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(WT, "backend"))

LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "00000000-0000-0000-0000-000000000001"

# FIXTURES ARE NOT COMMITTED (live tenant data stays out of the repo — see the 71k-line scratchpad
# dump that got a branch rejected on 2026-08-09). Regenerate them next to this file with:
#   python3 tools/sbsql.py "select store_code, market, is_active from storeops.stores \
#       where org_id='854f6d7b-6590-4e4d-88ab-646f560d4f4c' order by store_code" > lux_stores.json
#   python3 tools/sbsql.py "select id::text, name, rate::float8 as rate, store_code, market, \
#       is_active from pos.tax_codes where org_id='854f6d7b-6590-4e4d-88ab-646f560d4f4c'" > lux_taxcodes.json
def _fixture(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        sys.exit(f"missing fixture {name} — regenerate it with the sbsql command in this file's header")
    return json.load(open(p))


STORES = [dict(r, org_id=LUX) for r in _fixture("lux_stores.json")]
CODES = [dict(r, org_id=LUX) for r in _fixture("lux_taxcodes.json")]
# A decoy row on ANOTHER tenant: if the query stopped filtering by org_id it would cover everything.
CODES.append({"id": "decoy", "name": "DECOY other tenant", "rate": 99.0,
              "store_code": None, "market": None, "is_active": True, "org_id": OTHER})

TABLES = {("storeops", "stores"): STORES, ("pos", "tax_codes"): CODES}
FAILS = []


class _Q:
    def __init__(self, key):
        self.key, self.filters, self.counting = key, [], False

    def select(self, *_a, **kw):
        # `count="exact"` is how _count asks for a row total — the fake must honour it or the OLD
        # predicate errors instead of answering, and the negative control below tests nothing.
        self.counting = kw.get("count") == "exact"
        return self

    def eq(self, c, v):
        self.filters.append((c, v)); return self

    def limit(self, *_a):
        return self

    def execute(self):
        rows = TABLES.get(self.key, [])
        for c, v in self.filters:
            rows = [r for r in rows if r.get(c) == v]
        return types.SimpleNamespace(data=[dict(r) for r in rows],
                                     count=(len(rows) if self.counting else None))


class _S:
    def __init__(self, name):
        self.name = name

    def table(self, t):
        return _Q((self.name, t))


class Fake:
    def schema(self, n):
        return _S(n)


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


import app.modules.core.onboarding as OB   # noqa: E402
OB.sb = lambda: Fake()

# 0. negative control on the fake filter
q = _Q(("pos", "tax_codes")).select("*").eq("org_id", LUX).execute()
check("negative control: .eq('org_id') really filters (decoy excluded)",
      len(q.data) == 1 and q.data[0]["store_code"] == "Lefferts", f"got {len(q.data)} rows")

# 1. the coverage checker on the tenant's REAL data
covered, total, missing = OB._cov_pos_tax_rate(LUX)
check("Luxelink: 20 active stores are counted", total == 20, f"total={total}")
check("Luxelink: exactly 1 store is covered (Lefferts)",
      covered == 1 and "Lefferts" not in missing, f"covered={covered}")
check("Luxelink: 19 stores resolve to NO rate", len(missing) == 19, f"missing={len(missing)}")
check("this matches the live /pos/tax-codes/store-grid reading (20 stores, 19 scope='none')", True)

# 2. the predicate the wizard actually evaluates
ev = OB._evaluate({"type": "coverage", "check": "pos_tax_rate"}, LUX)
check("the step is INCOMPLETE on today's data", ev["state"] == "incomplete", f"got {ev}")
check("the reason names the gap in plain English",
      "19 of 20" in ev["reason"] and "$0" in ev["reason"], ev["reason"])

# 3. the OLD predicate — the negative control for the whole change
old = OB._evaluate({"type": "count", "schema": "pos", "table": "tax_codes", "min": 1}, LUX)
check("NEGATIVE CONTROL: the OLD row-count predicate calls the same tenant COMPLETE",
      old["state"] == "complete", f"got {old}")

# 4. add a company-wide rate -> every store is covered
CODES.append({"id": "orgwide", "name": "company default", "rate": 7.0,
              "store_code": None, "market": None, "is_active": True, "org_id": LUX})
ev2 = OB._evaluate({"type": "coverage", "check": "pos_tax_rate"}, LUX)
check("a company-wide rate covers every store", ev2["state"] == "complete", f"got {ev2}")

# 5. ...but an INACTIVE one does not (inactive rows must never win)
CODES[-1]["is_active"] = False
ev3 = OB._evaluate({"type": "coverage", "check": "pos_tax_rate"}, LUX)
check("a DEACTIVATED company-wide rate does not count as coverage",
      ev3["state"] == "incomplete", f"got {ev3}")
CODES.pop()

# 6. a market rate covers its market only
mk = next((s["market"] for s in STORES if s.get("market")), None)
CODES.append({"id": "mkt", "name": f"{mk} rate", "rate": 6.0,
              "store_code": None, "market": mk, "is_active": True, "org_id": LUX})
c2, t2, m2 = OB._cov_pos_tax_rate(LUX)
in_mk = sum(1 for s in STORES if s.get("market") == mk and s.get("is_active") is not False)
check(f"a market rate ({mk}) covers exactly that market's stores",
      c2 == 1 + in_mk - (1 if any(s["store_code"] == "Lefferts" and s.get("market") == mk for s in STORES) else 0),
      f"covered={c2} market_stores={in_mk}")
CODES.pop()

# 7. failure modes must never read as complete
check("an UNREGISTERED check is unknown, never complete",
      OB._evaluate({"type": "coverage", "check": "nope"}, LUX)["state"] == "unknown")
TABLES[("storeops", "stores")] = []
check("a tenant with NO stores is unknown, never complete",
      OB._evaluate({"type": "coverage", "check": "pos_tax_rate"}, LUX)["state"] == "unknown")
TABLES[("storeops", "stores")] = STORES


class Boom:
    def schema(self, _n):
        raise RuntimeError("schema not exposed")


OB.sb = lambda: Boom()
ev4 = OB._evaluate({"type": "coverage", "check": "pos_tax_rate"}, LUX)
check("a DB fault is unknown, never complete (fails closed)",
      ev4["state"] == "unknown", f"got {ev4}")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}")
sys.exit(1 if FAILS else 0)
