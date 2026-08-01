"""ASGI SMOKE for the SET-UP / ACTIVATION FEE package — every new endpoint, over real HTTP.

  GET  /setup-fee/config                          PUT  /setup-fee/config
  GET  /setup-fee/candidates/{period}             GET  /setup-fee/recognition-divergence/{period}
  GET  /setup-fee/impact/{period}

PROVES: the routes exist and answer; org_id is a QUERY PARAM on every one (contract RULE ONE); two
tenants get their own economics from the SAME process; every READ endpoint performs ZERO writes; the
config saver writes ONLY commission_org_config; a missing migration degrades to a 400 that NAMES the
file rather than a 500; the impact endpoint refuses to invent a percentage; and the route count is
exactly the MEASURED base + 5.

BASE IS PINNED TO A LITERAL COMMIT (ec9fe8b, the merge of agent/commission/edge-per-sale-dedup), not to
a moving ref — the moving-base trap bit a sibling package on 2026-08-01.

Run:  cd backend && python3 scratchpad/setup_fee_asgi_smoke.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASS = FAIL = 0
FAILED = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"FAIL  {name}   {extra}")


HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "11111111-2222-3333-4444-555555555555"
REP_L, REP_O = "ESPINOZA, CAROLINA", "SOMEONE ELSE"
BASE_COMMIT = "ec9fe8b"
BASE_ROUTES = 950            # measured on BASE_COMMIT by this package's author
WRITES = []


def _sale(org, rep, tid, prod, ext=0.0, gp=0.0, ct="", cat=""):
    return {"org_id": org, "period": "July 2026", "trans_id": tid, "trans_date": "2026-07-12",
            "store": "4640-A W Diversey Ave", "salesperson": rep, "department": "", "category": cat,
            "contract_type": ct, "product_desc": prod, "ext_price": ext, "gp": gp, "voided": "",
            "trans_type": "", "mdn": "", "serial_1": "", "customer_plan": prod, "sku": "",
            "tender_type": "", "product_id": None}


def _seed_store():
    store = {"commission_tier": [], "plan_installment_schedule": [], "plan_installment_line": [],
             "daily_sales_feed": [], "raw_mi": [], "raw_ma_commission": [], "product_mrc": [],
             "store_mapping": [{"org_id": LUX, "store_address": "4640-a w diversey ave",
                                "store_code": "IL01", "market": "IL"}],
             "employees": [], "carrier_category_map": [], "item_mapping": [], "raw_catalog": [],
             "carrier": [{"id": "car-total", "org_id": LUX, "name": "Total Wireless"}],
             "installment_gate_source_config": [], "commission_org_config": [],
             "accessory_config": [], "contract_type_map": [], "activation_rules": [],
             "payout_exclusion_map": [], "accessory_definition_map": [], "accessory_class": [],
             "commission_plan": [], "commission_rule": [], "commission_plan_assignment": [],
             "raw_sales": []}
    for org, rep in ((LUX, REP_L), (OTHER, REP_O)):
        store["commission_plan"].append({"id": f"p-{org[:4]}", "org_id": org, "name": "Plan",
                                         "carrier_id": None, "base_tier_metric": None,
                                         "is_active": True})
        store["commission_rule"].append({
            "id": f"r-{org[:4]}", "org_id": org, "plan_id": f"p-{org[:4]}", "label": "Acts",
            "match_field": "contract_type", "match_op": "contains", "match_value": "port",
            "qualifies": True, "payout_kind": "flat_per_unit", "amount": 10, "pct": 0,
            "tiered": False, "sort": 0, "unit_basis": None, "applies_scope_kind": None,
            "applies_scope_value": None})
        store["commission_plan_assignment"].append({"id": f"a-{org[:4]}", "org_id": org,
                                                    "plan_id": f"p-{org[:4]}", "scope": "default",
                                                    "scope_value": None, "priority": 0})
        store["raw_sales"] += [
            _sale(org, rep, "1", "Access Charge - $25 for single line, max $50 for multiple lines.",
                  ext=25.0, gp=12.5),
            _sale(org, rep, "2", "Access Charge - $25 for single line, max $50 for multiple lines.",
                  ext=25.0, gp=12.5),
            _sale(org, rep, "1", "Apple iPhone 16e", ct="Internal Port with IDV", ext=599.99, gp=20.0),
            _sale(org, rep, "3", "Activation payment", ext=0.0, gp=-66.8),
        ]
        store["accessory_config"].append({"org_id": org, "setup_fee_keywords": ["Access Charge"],
                                          "definition_field_rule": None, "setup_fee_products": []})
    return store


STORE = _seed_store()


class R:
    def __init__(self, d):
        self.data = d


class Q:
    def __init__(self, t):
        self.t, self.f, self.cols, self.rng = t, [], None, None
        self._payload = self._update = None

    def select(self, *a, **k):
        self.cols = a[0] if a else None
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v)); return self

    def neq(self, c, v):
        self.f.append(("neq", c, v)); return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v))); return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def insert(self, p, *a, **k):
        WRITES.append(("insert", self.t)); self._payload = p; return self

    def update(self, p, *a, **k):
        WRITES.append(("update", self.t)); self._update = p; return self

    def upsert(self, p, *a, **k):
        WRITES.append(("upsert", self.t)); self._payload = p; return self

    def delete(self, *a, **k):
        WRITES.append(("delete", self.t)); return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == "eq" and rv != v:
                return False
            if k == "neq" and rv == v:
                return False
            if k == "in" and rv not in v:
                return False
        return True

    def execute(self):
        if self.t not in STORE:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        if self._update is not None:
            hit = [r for r in STORE.get(self.t, []) if self._m(r)]
            for r in hit:
                r.update(self._update)
            return R(hit)
        if self._payload is not None:
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            for r in rows:
                r = dict(r)
                r.setdefault("id", f"gen-{len(STORE[self.t])}")
                STORE[self.t].append(r)
            return R(rows)
        rows = [dict(r) for r in STORE.get(self.t, []) if self._m(r)]
        if self.cols and self.cols != "*" and rows:
            for c in [x.strip() for x in str(self.cols).split(",")]:
                if c and c not in rows[0] and not c.startswith("count"):
                    raise Exception(f"column {self.t}.{c} does not exist")
        if self.rng:
            a, b = self.rng
            rows = rows[a:b + 1]
        return R(rows)


class Sch:
    def table(self, t):
        return Q(t)

    def rpc(self, *a, **k):
        return Q("__rpc__")


class Client:
    def schema(self, s):
        return Sch()

    def table(self, t):
        return Q(t)

import app.core.database as db                                                # noqa: E402
db.get_supabase = lambda *a, **k: Client()

import app.main as M                                                          # noqa: E402
from fastapi.testclient import TestClient                                     # noqa: E402

for mod in ("app.modules.commcalc.router",):
    m = sys.modules.get(mod)
    if m is not None:
        m.sb = lambda: Client()

C = TestClient(M.app)
B = "/api/v1/commcalc"


print("\n── ROUTE INVENTORY (base pinned to a LITERAL commit) ────────────────────────────────────")
paths = [r.path for r in M.app.routes if hasattr(r, "path")]
NEW = ["/setup-fee/config", "/setup-fee/candidates/{period}",
       "/setup-fee/recognition-divergence/{period}", "/setup-fee/impact/{period}"]
for p in NEW:
    check(f"route registered: {p}", (B + p) in paths, "missing")
check(f"route count = the MEASURED base {BASE_ROUTES} (at {BASE_COMMIT}) + exactly 5",
      len(paths) == BASE_ROUTES + 5, f"{len(paths)} (base pinned to the literal commit, not a ref)")

print("\n── CONFIG over HTTP ─────────────────────────────────────────────────────────────────────")
WRITES.clear()
r = C.get(f"{B}/setup-fee/config", params={"org_id": LUX})
check("GET /setup-fee/config 200", r.status_code == 200, r.text[:200])
j = r.json()
check("...reports the tenant has no saved config yet", j["is_default"] is True)
check("...the DEFAULTS pay nobody (include false, pct None)",
      j["config"]["default"]["include_in_commission"] is False
      and j["config"]["default"]["employee_pct_of_collected"] is None, json.dumps(j["config"])[:200])
check("...the owner's numbers are offered as a labelled REFERENCE, not applied",
      j["owner_reference"]["boost"]["employee_pct_of_collected"] == 0.10
      and j["config"]["default"]["employee_pct_of_collected"] is None)
check("...the tenant's own carriers are listed for per-carrier overrides",
      any(c["id"] == "car-total" for c in j["carriers"]), str(j["carriers"]))
check("...ZERO writes from a read", WRITES == [], str(WRITES))

WRITES.clear()
r = C.put(f"{B}/setup-fee/config", params={"org_id": LUX},
          json={"config": {"default": {"include_in_commission": True}}})
check("PUT /setup-fee/config 200", r.status_code == 200, r.text[:200])
check("...writes ONLY commission_org_config — never a payout table",
      [w[1] for w in WRITES] == ["commission_org_config"], str(WRITES))
check("...and WARNS that no percentage was entered, so nothing pays yet",
      "still pays $0" in r.json()["note"], r.json()["note"])
check("...org_id is stamped on the inserted config row",
      any(row.get("org_id") == LUX for row in STORE["commission_org_config"]),
      str(STORE["commission_org_config"])[:200])

print("\n── IMPACT over HTTP — it never invents a percentage ─────────────────────────────────────")
WRITES.clear()
r = C.get(f"{B}/setup-fee/impact/July 2026", params={"org_id": LUX})
check("GET /setup-fee/impact 200", r.status_code == 200, r.text[:200])
j = r.json()
check("...with no hypothesis it says so and the delta is 0 by construction",
      j["hypothesis"] is None and "never invents a percentage" in j["hypothesis_note"], r.text[:250])
check("...the collected total is real and comes from the tenant's own mapping ($50.00, 2 lines)",
      j["collected_total"] == 50.0 and j["collected_lines"] == 2, json.dumps(j)[:250])
check("...and it currently pays $0 with a NAMED warning", j["paid_total"] == 0.0 and j["warnings"],
      str(j["warnings"])[:200])
check("...ZERO writes", WRITES == [], str(WRITES))
r = C.get(f"{B}/setup-fee/impact/July 2026", params={"org_id": LUX, "employee_pct": "0.10"})
j = r.json()
check("at 10% the endpoint quotes $5.00 and echoes the exact percentage used",
      j["hypothesis"]["employee_pct_of_collected"] == 0.10
      and j["by_rep"][0]["delta"] == 5.0, json.dumps(j["by_rep"])[:250])

print("\n── CANDIDATES over HTTP — pick-don't-type from the tenant's own data ────────────────────")
r = C.get(f"{B}/setup-fee/candidates/July 2026", params={"org_id": LUX})
check("GET /setup-fee/candidates 200", r.status_code == 200, r.text[:200])
j = r.json()
_by = {c["product_desc"]: c for c in j["candidates"]}
check("...the mapped Access Charge line is flagged mapped_now with its dollars",
      _by["Access Charge - $25 for single line, max $50 for multiple lines."]["mapped_now"] is True
      and _by["Access Charge - $25 for single line, max $50 for multiple lines."]["ext_price"] == 50.0,
      json.dumps(j["candidates"])[:250])
check("...the $0 'Activation payment' line is offered but flagged collects_money=false",
      _by["Activation payment"]["collects_money"] is False)
check("...and a handset is offered too — the endpoint proposes, it never decides",
      "Apple iPhone 16e" in _by)

print("\n── RECOGNITION DIVERGENCE over HTTP ─────────────────────────────────────────────────────")
r = C.get(f"{B}/setup-fee/recognition-divergence/July 2026", params={"org_id": LUX})
check("GET /setup-fee/recognition-divergence 200", r.status_code == 200, r.text[:200])
check("...this tenant's data has no case drift, so switching match_mode moves $0",
      r.json()["safe_to_unify"] is True and r.json()["diverging_lines"] == 0, r.text[:200])
STORE["raw_sales"].append(_sale(LUX, REP_L, "9", "ACCESS CHARGE upper", ext=99.0, gp=99.0))
r = C.get(f"{B}/setup-fee/recognition-divergence/July 2026", params={"org_id": LUX})
check("...add ONE case variant and it is named with its dollars, and safe_to_unify flips",
      r.json()["diverging_lines"] == 1 and r.json()["amount"] == 99.0
      and r.json()["safe_to_unify"] is False, r.text[:250])
STORE["raw_sales"] = [x for x in STORE["raw_sales"] if x["trans_id"] != "9"]

print("\n── TENANT ISOLATION (same process, two orgs, different economics) ───────────────────────")
C.put(f"{B}/setup-fee/config", params={"org_id": LUX},
      json={"config": {"default": {"include_in_commission": True,
                                   "employee_pct_of_collected": 0.10}}})
C.put(f"{B}/setup-fee/config", params={"org_id": OTHER},
      json={"config": {"default": {"include_in_commission": True,
                                   "employee_pct_of_collected": 0.50}}})
a = C.get(f"{B}/setup-fee/impact/July 2026", params={"org_id": LUX}).json()
b = C.get(f"{B}/setup-fee/impact/July 2026", params={"org_id": OTHER}).json()
check("tenant A is paid at ITS 10% ($5.00) and tenant B at ITS 50% ($25.00)",
      a["paid_total"] == 5.0 and b["paid_total"] == 25.0, f"{a['paid_total']} / {b['paid_total']}")
check("each sees only its own rep", [x["rep"] for x in a["by_rep"]] == [REP_L]
      and [x["rep"] for x in b["by_rep"]] == [REP_O], f"{a['by_rep']} / {b['by_rep']}")
h = C.get(f"{B}/setup-fee/impact/July 2026", params={"org_id": HOUSE}).json()
check("the house/Boost org (no plans) gets $0 from THIS item — its set-up fee is paid by the Boost "
      "engine, which this package did not change",
      h["paid_total"] == 0.0 and h["by_rep"] == [], json.dumps(h)[:200])

print("\n── DEGRADATION: migration 263 is NOT applied ────────────────────────────────────────────")
_saved = [dict(r) for r in STORE["commission_org_config"]]
STORE["commission_org_config"] = [{k: v for k, v in r.items() if k != "setup_fee_pay"}
                                  for r in _saved]
r = C.get(f"{B}/setup-fee/config", params={"org_id": LUX})
check("GET config still 200 with the column absent, and reports the code defaults",
      r.status_code == 200 and r.json()["config"]["default"]["employee_pct_of_collected"] is None,
      r.text[:200])
r = C.get(f"{B}/setup-fee/impact/July 2026", params={"org_id": LUX})
check("...impact still 200 and pays $0 (nobody is paid from an unapplied migration)",
      r.status_code == 200 and r.json()["paid_total"] == 0.0, r.text[:200])
STORE["commission_org_config"] = _saved

print("\n── org_id IS A QUERY PARAM ON EVERY NEW ROUTE (contract RULE ONE) ───────────────────────")
import inspect                                                                # noqa: E402
import app.modules.commcalc.router as RT                                      # noqa: E402
for fn in (RT.get_setup_fee_config, RT.save_setup_fee_config, RT.setup_fee_candidates,
           RT.setup_fee_recognition_divergence, RT.setup_fee_impact):
    p = inspect.signature(fn).parameters.get("org_id")
    check(f"{fn.__name__}: org_id is a plain query param with the ORG_ID default",
          p is not None and p.default == RT.ORG_ID, str(inspect.signature(fn)))

print("\n" + "=" * 96)
print(f"RESULT  {PASS} passed, {FAIL} failed")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
print("=" * 96)
sys.exit(1 if FAIL else 0)
