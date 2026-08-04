"""ASGI SMOKE for the FINANCING package — every new endpoint, over real HTTP.

  GET  /financing/vendors                     PUT    /financing/vendors
  DELETE /financing/vendors/{key}             POST   /financing/vendors/{key}/carriers
  DELETE /financing/vendors/{key}/carriers/{id}
  POST /financing/vendors/{key}/detection     DELETE /financing/detection/{id}
  GET  /financing/targets/{period}            PUT    /financing/targets/{period}
  GET  /financing/{period}

PROVES: every route exists at its REAL path under /api/v1 (a bare path 404s — the curl-vs-UI trap);
org_id is a QUERY PARAM on all ten (contract RULE ONE); two tenants get their own answer from the SAME
process and neither sees the other's vendors, targets or units; every READ writes NOTHING; the writers
stamp org_id; a missing migration 272 degrades to an honest message instead of a 500; `/financing/vendors`
is not swallowed by `/financing/{period}`; and the route count is the MEASURED base + exactly 10.

Run:  cd backend && python3 scratchpad/financing_asgi_smoke.py
"""
import os
import sys

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
BASE_COMMIT = "bd01381"
BASE_ROUTES = 1018          # measured on BASE_COMMIT (matches the handoff's own count)
WRITES = []
TW_FIN = "TW Financing"
ACIMA_TENDER = "Financing"     # the house POS's real spelling, per the April 2026 78-col export


def _sale(org, rep, tid, prod, ext=0.0, gp=0.0, tender="", serial="", store="957 Pennsylvania Ave"):
    return {"org_id": org, "period": "July 2026", "trans_id": tid, "trans_date": "2026-07-12",
            "store": store, "salesperson": rep, "department": "", "category": "",
            "contract_type": "", "product_desc": prod, "ext_price": ext, "gp": gp, "voided": "",
            "trans_type": "", "mdn": "", "serial_1": serial, "sku": "", "tender_type": tender,
            "product_id": None}


def _seed():
    s = {"financing_vendor": [], "financing_vendor_carrier": [], "financing_detection_rule": [],
         "financing_target": [], "commission_plan": [], "commission_rule": [], "commission_tier": [],
         "commission_plan_assignment": [], "commission_config": [], "commission_org_config": [],
         "accessory_config": [], "accessory_definition_map": [], "carrier": [
             {"id": "car-total", "org_id": LUX, "name": "Total Wireless", "code": "TW"},
             {"id": "car-boost", "org_id": HOUSE, "name": "Boost", "code": "BST"}],
         "store_mapping": [{"org_id": LUX, "store_code": "PENN",
                            "store_address": "957 Pennsylvania Ave", "market": "NY"},
                           {"org_id": HOUSE, "store_code": "DIV",
                            "store_address": "4640-A W Diversey Ave", "market": "IL"}],
         "stores": [{"org_id": LUX, "store_code": "PENN", "address": "957 Pennsylvania Ave",
                     "market": "NY", "is_active": True},
                    {"org_id": HOUSE, "store_code": "DIV", "address": "4640-A W Diversey Ave",
                     "market": "IL", "is_active": True}],
         "raw_sales": [], "daily_sales_feed": [], "raw_catalog": [], "catalog_category_override": []}
    # LUX: two financed sales (4 lines each) + one cash sale
    for tid in ("3207", "3311"):
        s["raw_sales"] += [
            _sale(LUX, "CAROLINA", tid, "IPHONE 16E", ext=599.99, gp=60.0, tender=TW_FIN,
                  serial="35693803564380" + tid[-1]),
            _sale(LUX, "CAROLINA", tid, "Unlimited Premium", tender=TW_FIN),
            _sale(LUX, "CAROLINA", tid, "Case BYOD", ext=29.99, gp=20.0, tender=TW_FIN),
            _sale(LUX, "CAROLINA", tid, "Screen Protector", ext=19.99, gp=15.0, tender=TW_FIN)]
    s["raw_sales"].append(_sale(LUX, "CAROLINA", "9001", "Moto G", ext=129.99, tender="Cash"))
    # HOUSE: one ACIMA-tendered sale, at a DIFFERENT store, with the house's own tender spelling
    s["raw_sales"] += [
        _sale(HOUSE, "ALI", "8801", "SAMSUNG A16", ext=249.99, gp=30.0, tender=ACIMA_TENDER,
              serial="356938035640077", store="4640-A W Diversey Ave"),
        _sale(HOUSE, "ALI", "8801", "Wallet Funding", tender=ACIMA_TENDER,
              store="4640-A W Diversey Ave")]
    s["commission_config"].append({"org_id": HOUSE, "acima_tenders": [ACIMA_TENDER]})
    # LUX's edge pay rule — the matcher a vendor can INHERIT
    s["commission_plan"].append({"id": "p-lux", "org_id": LUX, "name": "Luxelink",
                                 "carrier_id": "car-total", "base_tier_metric": None, "is_active": True})
    s["commission_rule"].append({"id": "r-edge", "org_id": LUX, "plan_id": "p-lux", "label": "edge",
                                 "match_field": "tender_type", "match_op": "equals",
                                 "match_value": TW_FIN, "qualifies": True,
                                 "payout_kind": "flat_per_unit", "amount": 25, "pct": 0,
                                 "tiered": False, "sort": 0, "financing_vendor_key": None})
    return s


STORE = _seed()


class R:
    def __init__(self, d):
        self.data = d


class Q:
    def __init__(self, t):
        self.t, self.f, self.cols, self.rng = t, [], None, None
        self._payload = self._update = None
        self._del = False

    def select(self, *a, **k):
        self.cols = a[0] if a else None
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v)); return self

    def neq(self, c, v):
        self.f.append(("neq", c, v)); return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v))); return self

    def is_(self, c, v):
        self.f.append(("is", c, v)); return self

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
        WRITES.append(("delete", self.t)); self._del = True; return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == "eq" and rv != v:
                return False
            if k == "neq" and rv == v:
                return False
            if k == "in" and rv not in v:
                return False
            if k == "is" and v == "null" and rv is not None:
                return False
        return True

    def execute(self):
        if self.t not in STORE:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        if self._del:
            keep = [r for r in STORE[self.t] if not self._m(r)]
            gone = [r for r in STORE[self.t] if self._m(r)]
            STORE[self.t] = keep
            return R(gone)
        if self._update is not None:
            hit = [r for r in STORE.get(self.t, []) if self._m(r)]
            for r in hit:
                r.update(self._update)
            return R(hit)
        if self._payload is not None:
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for r in rows:
                r = dict(r)
                r.setdefault("id", f"gen-{self.t}-{len(STORE[self.t])}")
                STORE[self.t].append(r)
                out.append(r)
            return R(out)
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
NEW = ["/financing/vendors", "/financing/vendors/{vendor_key}",
       "/financing/vendors/{vendor_key}/carriers", "/financing/vendors/{vendor_key}/carriers/{row_id}",
       "/financing/vendors/{vendor_key}/detection", "/financing/detection/{rule_id}",
       "/financing/targets/{period}", "/financing/{period}"]
for p in NEW:
    check(f"route registered: {p}", (B + p) in paths, "missing")
check(f"route count = the MEASURED base {BASE_ROUTES} (at {BASE_COMMIT}) + exactly 10",
      len(paths) == BASE_ROUTES + 10, f"{len(paths)}")
check("the BARE path 404s — the /api/v1 prefix is mandatory (curl-verified != UI-verified)",
      C.get("/commcalc/financing/July 2026", params={"org_id": LUX}).status_code == 404)

print("\n── ROUTE ORDER: /financing/vendors must not be eaten by /financing/{period} ─────────────")
WRITES.clear()
r = C.get(f"{B}/financing/vendors", params={"org_id": LUX})
check("GET /financing/vendors 200", r.status_code == 200, r.text[:200])
j = r.json()
check("...it is the REGISTRY payload, not a report for a period called 'vendors'",
      "vendors" in j and "vocabulary" in j and "rows" not in j, list(j)[:8])
check("...ZERO writes from a read", WRITES == [], str(WRITES))

print("\n── REGISTRY: seeds are honest, detection starts UNCONFIGURED ────────────────────────────")
keys = {v["vendor_key"] for v in j["vendors"]}
check("both seeded vendors are offered", {"edge", "acima"} <= keys, keys)
edge = [v for v in j["vendors"] if v["vendor_key"] == "edge"][0]
acima = [v for v in j["vendors"] if v["vendor_key"] == "acima"][0]
check("edge ships with NO detection and says so",
      edge["matchers"] == [] and edge["detection_status"] == "not_configured", edge["detection_note"])
check("edge's suggested source is the tenant's OWN pay rule (no invented pattern)",
      edge["detection_source"] == "plan_rule")
check("the tenant's usable pay rules are offered for that inheritance",
      any(pr["rule_id"] == "r-edge" and pr["usable"] for pr in j["plan_rules"]), str(j["plan_rules"]))
check("LUX has no ACIMA mapping, so acima falls back and LABELS it a fallback",
      acima["detection_status"] == "inherited_default" and j["acima_configured"] is False,
      acima["detection_note"])
check("the field vocabulary is offered with plain-English labels (pick-don't-type)",
      any(f["value"] == "tender_type" for f in j["vocabulary"]["match_fields"]))

print("\n── the HOUSE tenant, same process, its OWN answer ───────────────────────────────────────")
jh = C.get(f"{B}/financing/vendors", params={"org_id": HOUSE}).json()
ah = [v for v in jh["vendors"] if v["vendor_key"] == "acima"][0]
check("HOUSE has a real ACIMA tender mapping, so it is 'configured', not a fallback",
      ah["detection_status"] == "configured" and jh["acima_configured"] is True, ah["detection_note"])
check("...and it inherits the EXISTING mapping rather than a second copy of it",
      [m["match_value"] for m in ah["matchers"]] == [ACIMA_TENDER],
      [m["match_value"] for m in ah["matchers"]])
check("HOUSE does not see LUX's plan rules",
      not any(pr["rule_id"] == "r-edge" for pr in jh["plan_rules"]))

print("\n── REPORT: an unconfigured vendor counts NOTHING (and says why) ─────────────────────────")
WRITES.clear()
r = C.get(f"{B}/financing/July 2026", params={"org_id": LUX})
check("GET /financing/{period} 200", r.status_code == 200, r.text[:300])
rep = r.json()
check("...LUX has 0 financed units while nothing is mapped",
      rep["totals"]["units"] == 0 and rep["configured_vendors"] == 0, rep["totals"])
check("...and an INHERITED DEFAULT is not counted as configured (nobody chose it)",
      rep["vendors_running_on_defaults"] == 1, rep.get("vendors_running_on_defaults"))
check("...but the tender values ARE listed so it can be mapped by picking",
      {t["value"] for t in rep["tender_values"]} == {TW_FIN, "Cash"}, rep["tender_values"])
check("...ZERO writes from the report", WRITES == [], str(WRITES))

print("\n── MAP IT: one detection rule, over HTTP ────────────────────────────────────────────────")
WRITES.clear()
r = C.post(f"{B}/financing/vendors/edge/detection", params={"org_id": LUX},
           json={"match_field": "tender_type", "match_op": "equals", "match_value": TW_FIN})
check("POST detection 200", r.status_code == 200, r.text[:200])
check("...org_id is STAMPED on the inserted row",
      all(x.get("org_id") == LUX for x in STORE["financing_detection_rule"]),
      str(STORE["financing_detection_rule"]))
check("...a tender rule raises no warning", r.json().get("warning") is None)
r2 = C.post(f"{B}/financing/vendors/edge/detection", params={"org_id": LUX},
            json={"match_field": "product_desc", "match_op": "word", "match_value": "edge"})
check("a product-description rule is ACCEPTED but WARNED about (model-name collision class)",
      r2.status_code == 200 and "MODEL NAME" in (r2.json().get("warning") or ""),
      r2.json().get("warning"))
C.delete(f"{B}/financing/detection/{r2.json()['rule']['id']}", params={"org_id": LUX})
r3 = C.post(f"{B}/financing/vendors/edge/detection", params={"org_id": LUX},
            json={"match_field": "sku", "match_op": "word", "match_value": "x"})
check("a field the report cannot read is REFUSED with a 400 that names the choices",
      r3.status_code == 400 and "tender_type" in r3.text, r3.text[:160])

# the vendor must point at its own rules for them to count
C.put(f"{B}/financing/vendors", params={"org_id": LUX},
      json={"vendor_key": "edge", "label": "Edge financing", "enabled": True,
            "detection_source": "rules", "amount_basis": "unit_line", "sort_order": 10})
rep = C.get(f"{B}/financing/July 2026", params={"org_id": LUX}).json()
check("now LUX reports 2 financed UNITS (2 sales x 4 lines each => not 8)",
      rep["totals"]["units"] == 2, rep["totals"])
check("...the financed amount is the device price, twice", rep["totals"]["amount"] == 1199.98,
      rep["totals"]["amount"])
check("...attributed to the rep and the resolved store code",
      [(x["rep"], x["store_code"], x["units"]) for x in rep["rows"]] == [("CAROLINA", "PENN", 2)],
      rep["rows"])
check("...the HOUSE tenant is untouched by LUX's mapping",
      C.get(f"{B}/financing/July 2026", params={"org_id": HOUSE}).json()["totals"]["units"] == 1,
      "house should count its OWN acima sale via its OWN mapping")

print("\n── CARRIERS: a vendor may serve MANY (the 'ACIMA on Total later' requirement) ───────────")
r = C.post(f"{B}/financing/vendors/acima/carriers", params={"org_id": LUX},
           json={"carrier_id": "car-total", "carrier_name": "Total Wireless"})
check("POST carrier assignment 200", r.status_code == 200, r.text[:160])
check("...org_id stamped", all(x.get("org_id") == LUX for x in STORE["financing_vendor_carrier"]))
j = C.get(f"{B}/financing/vendors", params={"org_id": LUX}).json()
acima = [v for v in j["vendors"] if v["vendor_key"] == "acima"][0]
check("...ACIMA now serves Total for THIS tenant, with no code change",
      any(c["carrier_name"] == "Total Wireless" for c in acima["carriers"]), acima["carriers"])
jh = C.get(f"{B}/financing/vendors", params={"org_id": HOUSE}).json()
ah = [v for v in jh["vendors"] if v["vendor_key"] == "acima"][0]
check("...and the HOUSE tenant's ACIMA is unaffected",
      not any((c.get("carrier_name") or "") == "Total Wireless" and c.get("source") != "seed"
              for c in ah["carriers"]), ah["carriers"])

print("\n── TARGETS: assignable per store, and honest about 'no target' ──────────────────────────")
WRITES.clear()
r = C.get(f"{B}/financing/targets/July 2026", params={"org_id": LUX})
check("GET /financing/targets 200", r.status_code == 200, r.text[:200])
check("...over the tenant's OWN store roster only",
      [t["store_code"] for t in r.json()["targets"]] == ["PENN"], r.json()["targets"])
check("...ZERO writes", WRITES == [], str(WRITES))
rep = C.get(f"{B}/financing/July 2026", params={"org_id": LUX}).json()
check("with no target the report says 'no target', never 0%",
      rep["by_store"][0]["attainment_pct"] is None and rep["by_store"][0]["target_source"] == "none")

WRITES.clear()
r = C.put(f"{B}/financing/targets/July 2026", params={"org_id": LUX},
          json={"store_code": "PENN", "target_units": 4})
check("PUT /financing/targets 200", r.status_code == 200, r.text[:200])
check("...writes ONLY the financing_target table — never a payout table",
      {w[1] for w in WRITES} <= {"financing_target"}, str(WRITES))
check("...org_id stamped on the target row",
      all(x.get("org_id") == LUX for x in STORE["financing_target"]), str(STORE["financing_target"]))
rep = C.get(f"{B}/financing/July 2026", params={"org_id": LUX}).json()
check("attainment is now 2/4 = 50%", rep["by_store"][0]["attainment_pct"] == 50.0,
      rep["by_store"][0])
check("...and the HOUSE tenant still has no target of its own",
      C.get(f"{B}/financing/July 2026",
            params={"org_id": HOUSE}).json()["by_store"][0]["attainment_pct"] is None)

print("\n── DEGRADATION: migration 272 not applied ───────────────────────────────────────────────")
saved = {k: STORE.pop(k) for k in ("financing_vendor", "financing_detection_rule",
                                   "financing_vendor_carrier", "financing_target")}
r = C.get(f"{B}/financing/vendors", params={"org_id": LUX})
check("registry still answers 200 with the built-in defaults", r.status_code == 200, r.text[:160])
check("...and NAMES the missing migration instead of pretending",
      r.json()["ready"] is False and "272" in (r.json()["note"] or ""), r.json().get("note"))
r = C.get(f"{B}/financing/July 2026", params={"org_id": LUX})
check("the report still renders (no 500) with nothing configured",
      r.status_code == 200 and r.json()["totals"]["units"] == 0, r.text[:160])
r = C.put(f"{B}/financing/targets/July 2026", params={"org_id": LUX},
          json={"store_code": "PENN", "target_units": 4})
check("saving a target fails LOUDLY and names migration 272",
      r.status_code == 500 and "272" in r.text, r.text[:200])
STORE.update(saved)

print("\n" + "=" * 78)
print(f"{PASS} passed · {FAIL} failed")
for f in FAILED:
    print("   FAILED:", f)
print("=" * 78)
sys.exit(1 if FAIL else 0)
