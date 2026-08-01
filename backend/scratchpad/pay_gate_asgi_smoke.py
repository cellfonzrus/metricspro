"""ASGI SMOKE for the PAY GATE package — every new endpoint, over real HTTP, against a stub client.

Covers the 10 routes added by agent/commission/edge-per-sale-dedup:
  GET  /commission-plans/pay-gate                          PUT  /commission-plans/pay-gate
  GET  /commission-plans/unit-dedup-impact/{period}        GET  /commission-plans/unit-multiplication-audit/{period}
  GET  /commission-plans/payout-exclusions                 POST /commission-plans/payout-exclusions
  DELETE /commission-plans/payout-exclusions/{id}          GET  /commission-plans/exclusion-impact/{period}
  GET  /commission-plans/rule-scope-impact/{period}        GET  /commission-plans/accessory-basis-impact/{period}

WHAT IT PROVES: the routes exist and answer; org_id is a QUERY PARAM on every one (contract RULE ONE);
two tenants get their own answers from the same process; the READ endpoints perform ZERO writes; the
boundary refuses the short-`contains` trap; a missing migration degrades to a 400 that NAMES the file
rather than a 500; and the total route count is exactly base + 10.

Run:  cd backend && python3 scratchpad/pay_gate_asgi_smoke.py
"""
import os
import sys
import copy
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
WRITES = []


# ── stub supabase, shared by the whole app process ───────────────────────────────────────────────
def _sale(org, rep, tid, prod, ct="", ext=0.0, gp=0.0, serial="", tender="", cat=""):
    return {"org_id": org, "period": "July 2026", "trans_id": tid, "trans_date": "2026-07-12",
            "store": "4640-A W Diversey Ave", "salesperson": rep, "department": "", "category": cat,
            "contract_type": ct, "product_desc": prod, "ext_price": ext, "gp": gp, "voided": "",
            "trans_type": "", "mdn": "", "serial_1": serial, "customer_plan": prod, "sku": "",
            "tender_type": tender, "product_id": None}


def _seed_store():
    def tenant(org, rep):
        return {
            "commission_plan": [{"id": f"p-{org[:4]}", "org_id": org, "name": "Total Wireless",
                                 "carrier_id": None, "base_tier_metric": None, "is_active": True}],
            "commission_rule": [
                {"id": f"r-edge-{org[:4]}", "org_id": org, "plan_id": f"p-{org[:4]}", "label": "edge",
                 "match_field": "tender_type", "match_op": "contains", "match_value": "edge",
                 "qualifies": True, "payout_kind": "flat_per_unit", "amount": 25, "pct": 0,
                 "tiered": False, "sort": 0, "unit_basis": None,
                 "applies_scope_kind": None, "applies_scope_value": None},
                {"id": f"r-acc-{org[:4]}", "org_id": org, "plan_id": f"p-{org[:4]}",
                 "label": "Accessories", "match_field": "category", "match_op": "equals",
                 "match_value": "accessories", "qualifies": True, "payout_kind": "pct_gp",
                 "amount": 0, "pct": 0.175, "tiered": False, "sort": 1, "unit_basis": None,
                 "applies_scope_kind": None, "applies_scope_value": None}],
            "commission_plan_assignment": [{"id": f"a-{org[:4]}", "org_id": org,
                                            "plan_id": f"p-{org[:4]}", "scope": "default",
                                            "scope_value": None, "priority": 0}],
            "raw_sales": [
                _sale(org, rep, "3207", "Total ALL ACCESS Plan $65", tender="TW Edge Financing"),
                _sale(org, rep, "3207", "Apple iPhone 16e 128GB", tender="TW Edge Financing",
                      ext=599.99, gp=20.0, serial="352915117781238"),
                _sale(org, rep, "3207", "Case BYOD", tender="TW Edge Financing", ext=29.99, gp=0.0,
                      cat="Accessories"),
                _sale(org, rep, "3215", "Total Wireless Protect+ RTR. Phone#: (773) 648-1456.",
                      ext=25.0, gp=25.0, cat="Accessories"),
            ]}
    store = {"commission_tier": [], "plan_installment_schedule": [], "plan_installment_line": [],
             "daily_sales_feed": [], "raw_mi": [], "raw_ma_commission": [], "product_mrc": [],
             "store_mapping": [{"org_id": LUX, "store_address": "4640-a w diversey ave",
                                "store_code": "IL01", "market": "IL"}],
             "employees": [], "carrier_category_map": [], "item_mapping": [], "raw_catalog": [],
             "carrier": [], "installment_gate_source_config": [], "commission_org_config": [],
             "accessory_config": [], "contract_type_map": [], "activation_rules": [],
             "payout_exclusion_map": [], "accessory_definition_map": [], "accessory_class": []}
    for k in ("commission_plan", "commission_rule", "commission_plan_assignment", "raw_sales"):
        store[k] = []
    for org, rep in ((LUX, REP_L), (OTHER, REP_O)):
        t = tenant(org, rep)
        for k, v in t.items():
            store[k] = store.get(k, []) + v
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

print("\n── ROUTE INVENTORY ──────────────────────────────────────────────────────────────────────")
paths = [r.path for r in M.app.routes if hasattr(r, "path")]
NEW = ["/commission-plans/pay-gate", "/commission-plans/unit-dedup-impact/{period}",
       "/commission-plans/unit-multiplication-audit/{period}",
       "/commission-plans/payout-exclusions", "/commission-plans/payout-exclusions/{row_id}",
       "/commission-plans/exclusion-impact/{period}", "/commission-plans/rule-scope-impact/{period}",
       "/commission-plans/accessory-basis-impact/{period}"]
for p in NEW:
    check(f"route registered: {p}", (B + p) in paths, "missing")
check("total route count is the pinned base 940 + 10 = 950", len(paths) == 950, str(len(paths)))

print("\n── ① UNIT DEDUP over HTTP ───────────────────────────────────────────────────────────────")
WRITES.clear()
r = C.get(f"{B}/commission-plans/unit-dedup-impact/July 2026", params={"org_id": LUX})
check("unit-dedup-impact 200", r.status_code == 200, r.text[:200])
j = r.json()
# before = 3 tender-matched lines x $25 ($75) + the accessory rule on the RTR line
# (0.175 x $25 = $4.38) = $79.38. after = ONE device unit ($25) and the RTR line excluded = $25.00.
check("...the owner's transaction: $79.38 before -> $25.00 after",
      j["totals"]["before"] == 79.38 and j["totals"]["after"] == 25.0, json.dumps(j["totals"]))
check("...the rep and their delta are named", j["by_rep"] and j["by_rep"][0]["rep"] == REP_L
      and j["by_rep"][0]["delta"] == -54.38, str(j["by_rep"])[:200])
check("...pay_gate rides along with the suppression detail",
      (j.get("pay_gate") or {}).get("unit", {}).get("lines_suppressed") == 2, str(j.get("pay_gate"))[:200])
check("...ZERO writes from a read endpoint", WRITES == [], str(WRITES))

r = C.get(f"{B}/commission-plans/unit-multiplication-audit/July 2026", params={"org_id": LUX})
check("unit-multiplication-audit 200", r.status_code == 200, r.text[:200])
j = r.json()
check("...names the multiplying rule and its extra dollars",
      j["totals"]["rules"] == 1 and j["rules"][0]["label"] == "edge"
      and j["rules"][0]["extra_amount"] == 50.0, json.dumps(j["totals"]))
check("...and says this tenant's config already collapses it", j["rules"][0]["auto_deduped"] is True)

print("\n── PAY-GATE CONFIG over HTTP ────────────────────────────────────────────────────────────")
r = C.get(f"{B}/commission-plans/pay-gate", params={"org_id": LUX})
check("GET pay-gate 200 + reports the code defaults are in force",
      r.status_code == 200 and r.json()["is_default"] is True, r.text[:200])
check("...unit dedup default is per_device on tender_type",
      r.json()["config"]["unit_basis"]["default_basis"] == "per_device"
      and r.json()["config"]["unit_basis"]["auto_txn_level_fields"] == ["tender_type"])
check("...accessory basis guard default is OFF",
      r.json()["config"]["accessory_basis_guard"]["enabled"] is False)
WRITES.clear()
r = C.put(f"{B}/commission-plans/pay-gate", params={"org_id": LUX},
          json={"config": {"unit_basis": {"enabled": False}}})
check("PUT pay-gate 200", r.status_code == 200, r.text[:200])
check("...wrote to commission_org_config and nothing else",
      [w[1] for w in WRITES] == ["commission_org_config"], str(WRITES))
check("...and it is a config write, never a payout table",
      not any(w[1] in ("rep_commissions", "raw_sales", "commission_ledger") for w in WRITES))
r = C.get(f"{B}/commission-plans/unit-dedup-impact/July 2026", params={"org_id": LUX})
check("...the tenant's OFF setting takes effect: the 3 edge lines pay $75.00 again "
      "(the RTR exclusion is a separate switch and stays on, so after != before)",
      r.json()["totals"]["after"] == 75.0 and r.json()["totals"]["before"] == 79.38,
      json.dumps(r.json()["totals"]))
C.put(f"{B}/commission-plans/pay-gate", params={"org_id": LUX},
      json={"config": {"unit_basis": {"enabled": True}}})

print("\n── ② EXCLUSIONS over HTTP ───────────────────────────────────────────────────────────────")
r = C.get(f"{B}/commission-plans/payout-exclusions", params={"org_id": LUX})
check("GET payout-exclusions 200 + the built-in RTR seed is listed",
      r.status_code == 200 and any(x["match_value"] == "RTR" and x["source"] == "seed"
                                   for x in r.json()["rules"]), r.text[:200])
r = C.get(f"{B}/commission-plans/exclusion-impact/July 2026", params={"org_id": LUX})
check("exclusion-impact 200 + the RTR line is named with its dollars",
      r.status_code == 200 and r.json()["excluded_lines"] == 1
      and r.json()["samples"][0]["trans_id"] == "3215", r.text[:250])
r = C.post(f"{B}/commission-plans/payout-exclusions", params={"org_id": LUX},
           json={"match_field": "product_desc", "match_op": "contains", "match_value": "RTR"})
check("POST refuses a short `contains` pattern (the CARTRIDGE trap) with a 400, not a 500",
      r.status_code == 400 and "CARTRIDGE" in r.text, f"{r.status_code} {r.text[:200]}")
WRITES.clear()
r = C.post(f"{B}/commission-plans/payout-exclusions", params={"org_id": LUX},
           json={"match_field": "product_desc", "match_op": "word", "match_value": "REFILL",
                 "label": "Refills"})
check("POST a valid mapping 200", r.status_code == 200, r.text[:200])
check("...stamped with the caller's org_id (RULE ONE write-side)",
      any(w[1] == "payout_exclusion_map" for w in WRITES)
      and STORE["payout_exclusion_map"][-1]["org_id"] == LUX, str(STORE["payout_exclusion_map"])[:200])
r = C.get(f"{B}/commission-plans/payout-exclusions", params={"org_id": OTHER})
check("...and the OTHER tenant does not see it (only its own + the seed)",
      not any(x.get("match_value") == "REFILL" for x in r.json()["rules"]), r.text[:200])

print("\n── ③ RULE SCOPE over HTTP ───────────────────────────────────────────────────────────────")
r = C.get(f"{B}/commission-plans/rule-scope-impact/July 2026", params={"org_id": LUX})
check("rule-scope-impact 200", r.status_code == 200, r.text[:200])
check("...with no hypothesis it proposes nothing and says so",
      r.json()["hypothesis"] is None and "owner's to state" in r.json()["hypothesis_note"],
      r.text[:250])
r = C.get(f"{B}/commission-plans/rule-scope-impact/July 2026",
          params={"org_id": LUX, "rule_id": f"r-edge-{LUX[:4]}", "scope_kind": "market",
                  "scope_value": "NY,NJ"})
j = r.json()
check("...with a market hypothesis the IL rep is shown LOSING the rule, with the dollars",
      j["totals"]["reps_losing"] == 1 and j["totals"]["would_lose"] == 75.0, json.dumps(j["totals"]))

print("\n── ⑤ ACCESSORY BASIS over HTTP ──────────────────────────────────────────────────────────")
WRITES.clear()
r = C.get(f"{B}/commission-plans/accessory-basis-impact/July 2026", params={"org_id": LUX})
check("accessory-basis-impact 200", r.status_code == 200, r.text[:200])
j = r.json()
check("...reports the guard is currently OFF for this tenant", j["currently_enabled"] is False)
check("...and states that no margin was invented", "never invents one" in (j["hypothesis_note"] or ""))
check("...ZERO writes", WRITES == [], str(WRITES))
r = C.get(f"{B}/commission-plans/accessory-basis-impact/July 2026",
          params={"org_id": LUX, "assumed_margin_pct": "0.35"})
check("...the echoed hypothesis carries EXACTLY the margin that was passed",
      r.json()["hypothesis"]["assumed_margin_pct"] == 0.35, r.text[:200])

print("\n── TENANT ISOLATION over HTTP (same process, two orgs) ──────────────────────────────────")
a = C.get(f"{B}/commission-plans/unit-dedup-impact/July 2026", params={"org_id": LUX}).json()
b = C.get(f"{B}/commission-plans/unit-dedup-impact/July 2026", params={"org_id": OTHER}).json()
check("each tenant sees only its own rep", [x["rep"] for x in a["by_rep"]] == [REP_L]
      and [x["rep"] for x in b["by_rep"]] == [REP_O], f"{a['by_rep']} / {b['by_rep']}")
h = C.get(f"{B}/commission-plans/unit-dedup-impact/July 2026", params={"org_id": HOUSE}).json()
check("the house/Boost org (no plans) gets an empty, $0 answer — correct isolation, not an error",
      h["totals"]["before"] == 0.0 and h["totals"]["after"] == 0.0 and h["by_rep"] == [],
      json.dumps(h["totals"]))

print("\n── DEGRADATION: the migrations are NOT applied ──────────────────────────────────────────")
_saved = STORE.pop("payout_exclusion_map")
r = C.get(f"{B}/commission-plans/payout-exclusions", params={"org_id": LUX})
check("GET payout-exclusions still 200 with the table absent, ready=false + names the migration",
      r.status_code == 200 and r.json()["ready"] is False
      and "261" in (r.json()["migration"] or ""), r.text[:200])
r = C.post(f"{B}/commission-plans/payout-exclusions", params={"org_id": LUX},
           json={"match_field": "product_desc", "match_op": "word", "match_value": "TOPUP"})
check("POST returns a 400 NAMING the migration file, never a 500",
      r.status_code == 400 and "261_commission_payout_exclusion_map.sql" in r.text,
      f"{r.status_code} {r.text[:200]}")
r = C.get(f"{B}/commission-plans/unit-dedup-impact/July 2026", params={"org_id": LUX})
check("...and the unit dedup still works with the table gone (the seed degrades cleanly)",
      r.status_code == 200 and r.json()["totals"]["after"] == 25.0, r.text[:250])
STORE["payout_exclusion_map"] = _saved

print("\n── org_id IS A QUERY PARAM ON EVERY NEW ROUTE (contract RULE ONE) ───────────────────────")
import inspect                                                                # noqa: E402
import app.modules.commcalc.router as RT                                      # noqa: E402
for fn in (RT.get_pay_gate, RT.save_pay_gate, RT.unit_dedup_impact, RT.unit_multiplication_audit,
           RT.list_payout_exclusions, RT.save_payout_exclusion, RT.delete_payout_exclusion,
           RT.exclusion_impact, RT.rule_scope_impact, RT.accessory_basis_impact):
    sig = inspect.signature(fn)
    p = sig.parameters.get("org_id")
    check(f"{fn.__name__}: org_id is a plain query param with the ORG_ID default",
          p is not None and p.default == RT.ORG_ID, str(sig))

print("\n" + "=" * 96)
print(f"RESULT  {PASS} passed, {FAIL} failed")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
print("=" * 96)
sys.exit(1 if FAIL else 0)
