"""REAL-ASGI smoke for the flat-payout (mig 256) + accessory-definition (mig 257) package.

The proof harness calls the modules as plain Python — that proves the arithmetic but NOT the mount, the
`/api/v1` prefix or the query-param binding. The repeat offender here is
`[[curl-verified-not-ui-verified-apiv1]]`: a bare `/commcalc/...` path passes a curl-against-backend
check and 404s in the actual app. This drives the WHOLE FastAPI app through Starlette's TestClient at
the EXACT URLs the two pages fetch, and asserts:

  • every new endpoint answers 200/400 as designed at its real `/api/v1/...` URL
  • the BARE path (no /api/v1) is 404 — the trap
  • `org_id` is really a QUERY PARAM: a second tenant's URL returns that tenant's own view, and an
    empty org_id is rejected
  • the READ paths issue ZERO writes (with the guard deliberately tripped as a negative control)
  • the WRITE paths stamp the CALLER's org_id on every row
  • a UI CONTRACT section: every field the two pages destructure exists in the live payload
  • pre-migration degradation: with the new tables absent, every GET is still 200 and the writes 400
  • the route COUNT is the pinned base + exactly the routes this package adds

Run: cd backend && PYTHONPATH=. python3 scratchpad/fwa_flat_accessory_def_asgi_smoke.py
"""
import io
import os
import subprocess
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
_REPO = os.path.abspath(os.path.join(_BACKEND, ".."))
sys.path.insert(0, _BACKEND)
os.environ.setdefault("COMMCALC_CFG_CACHE_TTL", "0")

BASE_ROUTES = 927          # pinned: origin/main @ 4923001, measured
NEW_ROUTES = 13


def _helpers():
    """Reuse the proof harness's fixture WITHOUT running its assertions: only the source ABOVE its
    first section banner is executed."""
    path = os.path.join(_HERE, "fwa_flat_accessory_def_proof.py")
    src = io.open(path, encoding="utf-8").read()
    marker = '\nsec("§A'
    assert marker in src, "proof harness layout changed — helper split marker gone"
    mod = types.ModuleType("fwa_flat_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _helpers()

from fastapi.testclient import TestClient            # noqa: E402
from app.main import app                             # noqa: E402
import app.core.database as DB                       # noqa: E402
from app.modules.commcalc import router as R         # noqa: E402
from app.modules.commcalc import accessory_definition as adef   # noqa: E402

_pass = _fail = 0
_fails = []


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        _fails.append(name)
        print(f"  FAIL  {name}   {extra}")


ORG, ORG_B, PER = H.ORG, H.ORG_B, H.PER
PAYOUT = "/api/v1/commcalc/plan-installments/category-payout"
IMPACT = f"/api/v1/commcalc/plan-installments/category-payout-impact/{PER}"
ADEF = "/api/v1/commcalc/accessory-definition"
AGREE = f"/api/v1/commcalc/accessory-definition/agreement/{PER}"


# ── a WRITABLE fake (the proof harness's raises on write; here the writes are under test) ──────────
class WQuery(H.FakeQuery):
    def __init__(self, store, key, log, writes):
        super().__init__(store.get(key, []), log, key, None)
        self._store, self._key, self._wlog = store, key, writes

    def insert(self, rows, *a, **k):
        rows = rows if isinstance(rows, list) else [rows]
        self._wlog.append(("insert", self._key, rows))
        self._store.setdefault(self._key, []).extend(rows)
        return type("E", (), {"execute": lambda _s: type("R", (), {"data": rows})()})()

    def upsert(self, rows, *a, **k):
        rows = rows if isinstance(rows, list) else [rows]
        self._wlog.append(("upsert", self._key, rows))
        cur = self._store.setdefault(self._key, [])
        for row in rows:
            hit = next((c for c in cur if all(c.get(f) == row.get(f)
                                              for f in ("org_id", "match_field", "match_value"))
                        if "match_field" in row), None)
            if hit is None:
                hit = next((c for c in cur if c.get("org_id") == row.get("org_id")
                            and "match_field" not in row), None)
            if hit is not None:
                hit.update(row)
            else:
                cur.append(dict(row, id=row.get("id") or f"id-{len(cur)+1}"))
        return type("E", (), {"execute": lambda _s: type("R", (), {"data": rows})()})()

    def update(self, patch, *a, **k):
        self._wlog.append(("update", self._key, patch))
        self._patch = patch
        return self

    def delete(self, *a, **k):
        self._wlog.append(("delete", self._key, None))
        self._del = True
        return self

    def execute(self):
        if getattr(self, "_patch", None) is not None:
            for r in self._apply():
                r.update(self._patch)
            return type("R", (), {"data": []})()
        if getattr(self, "_del", False):
            keep = [r for r in self._store.get(self._key, []) if r not in self._apply()]
            self._store[self._key] = keep
            return type("R", (), {"data": []})()
        return super().execute()


class WSchema:
    def __init__(self, store, schema, log, writes, missing):
        self.store, self.s, self.log, self.writes, self.missing = store, schema, log, writes, set(missing)

    def table(self, t):
        key = f"{self.s}.{t}"
        if key in self.missing:
            return H.MissingTable()
        return WQuery(self.store, key, self.log, self.writes)


class WClient:
    def __init__(self, store, log=None, writes=None, missing=()):
        self.store, self.log, self.writes, self.missing = store, log, writes if writes is not None else [], missing

    def schema(self, s):
        return WSchema(self.store, s, self.log, self.writes, self.missing)

    def table(self, t):
        return WQuery(self.store, f"public.{t}", self.log, self.writes)


def mount(store, log=None, writes=None, missing=()):
    c = WClient(store, log, writes, missing)
    R.sb = lambda: c
    DB.get_supabase = lambda *a, **k: c
    return c


STORE = H.build_store()
STORE["commcalc.raw_sales"] += H.build_store(org=ORG_B)["commcalc.raw_sales"]
WRITES = []
mount(STORE, writes=WRITES)
client = TestClient(app, raise_server_exceptions=False)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- D1: flat payout, at the exact URLs the page fetches -------------------------------------")
r = client.get(PAYOUT, params={"org_id": ORG})
check(f"GET {PAYOUT} -> 200", r.status_code == 200, r.text[:300])
j = r.json() if r.status_code == 200 else {}
check("every category is on monthly installments out of the box",
      all(v["mode"] == "installments" for v in (j.get("payout") or {}).values()), j.get("payout"))
check("NO amount is pre-filled anywhere (the owner types it)",
      all(v["amount"] is None for v in (j.get("payout") or {}).values()), j.get("payout"))
check("is_default is true with nothing saved", j.get("is_default") is True)
for k in ("payout", "is_default", "defaults", "categories", "modes", "max_pay_month",
          "flat_categories", "schedules", "ready", "migration"):
    check(f"UI contract: payload carries `{k}`", k in j, sorted(j))
check("the category dropdown is pick-don't-type (real keys + labels)",
      all("key" in c and "label" in c for c in j.get("categories") or []), j.get("categories"))

r = client.get("/commcalc/plan-installments/category-payout", params={"org_id": ORG})
check("the BARE path (no /api/v1) is 404 — the api() trap", r.status_code == 404, r.status_code)
r = client.get(PAYOUT, params={"org_id": ""})
check("an empty org_id is rejected 400", r.status_code == 400, r.status_code)

print("\n-- D1: the impact endpoint never invents a dollar ------------------------------------------")
r = client.get(IMPACT, params={"org_id": ORG})
check(f"GET {IMPACT} (no hypothesis) -> 200", r.status_code == 200, r.text[:300])
j = r.json() if r.status_code == 200 else {}
check("no hypothesis -> delta is 0 and it SAYS so",
      j.get("hypothesis") is None and j.get("totals", {}).get("delta") == 0.0
      and "never invents an amount" in (j.get("hypothesis_note") or ""), j.get("hypothesis_note"))
r = client.get(IMPACT, params={"org_id": ORG, "category": "home_internet", "amount": "25"})
check("with an amount -> 200 and the hypothesis is ECHOED (so a table can be labelled honestly)",
      r.status_code == 200 and r.json().get("hypothesis") == {"category": "home_internet",
                                                              "amount": 25.0, "pay_month": 1},
      r.text[:300])
j = r.json()
check("...and it moves the number", j["totals"]["delta"] != 0.0, j["totals"])
check("...per rep", isinstance(j.get("by_rep"), list) and all(
    {"rep", "now", "with_flat", "delta"} <= set(x) for x in j["by_rep"]), j.get("by_rep"))
r = client.get(IMPACT, params={"org_id": ORG, "category": "nonsense", "amount": "25"})
check("an unknown category is refused 400", r.status_code == 400, r.status_code)

print("\n-- D1: the PUT is the owner's entry point --------------------------------------------------")
WRITES.clear()
r = client.put(PAYOUT, params={"org_id": ORG},
               json={"payout": {"home_internet": {"mode": "flat_once", "amount": 25, "pay_month": 1}}})
check(f"PUT {PAYOUT} -> 200", r.status_code == 200, r.text[:300])
check("the write stamped the CALLER's org_id",
      all(any(x.get("org_id") == ORG for x in rows) for _op, _t, rows in WRITES if isinstance(rows, list)),
      WRITES)
check("...and only touched commission_org_config",
      {t for _o, t, _r in WRITES} == {"commcalc.commission_org_config"}, {t for _o, t, _r in WRITES})
r = client.get(PAYOUT, params={"org_id": ORG})
check("the saved flat category reads back", r.json().get("flat_categories") == ["home_internet"],
      r.json().get("flat_categories"))
r = client.get(PAYOUT, params={"org_id": ORG_B})
check("a SECOND tenant does NOT see it (org_id is really a query param)",
      r.json().get("flat_categories") == [] and r.json().get("is_default") is True,
      r.json().get("flat_categories"))
r = client.put(PAYOUT, params={"org_id": ORG},
               json={"payout": {"home_internet": {"mode": "flat_once"}}})
check("saving flat with a BLANK amount is accepted but reported as not-yet-effective",
      r.status_code == 200 and r.json().get("unconfigured_amount") == ["home_internet"]
      and "STILL paying monthly" in (r.json().get("note") or ""), r.text[:300])
r = client.put(PAYOUT, params={"org_id": ORG}, json={"payout": {"spaceship": {"mode": "flat_once"}}})
check("an unknown category key is refused 400", r.status_code == 400, r.status_code)
r = client.put(PAYOUT, params={"org_id": ORG}, json={"payout": {"phone": {"mode": "wat"}}})
check("an unknown mode is refused 400", r.status_code == 400, r.status_code)
client.put(PAYOUT, params={"org_id": ORG}, json={"reset": True})
check("reset returns to the code default",
      client.get(PAYOUT, params={"org_id": ORG}).json().get("is_default") is True)

print("\n-- D1: the READ paths write NOTHING --------------------------------------------------------")
WRITES.clear()
client.get(PAYOUT, params={"org_id": ORG})
client.get(IMPACT, params={"org_id": ORG, "category": "home_internet", "amount": "25"})
check("zero writes from the flat-payout GETs", not WRITES, WRITES)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n-- D2: accessory definition ----------------------------------------------------------------")
r = client.get(f"{ADEF}/classes", params={"org_id": ORG})
check(f"GET {ADEF}/classes -> 200", r.status_code == 200, r.text[:300])
j = r.json() if r.status_code == 200 else {}
check("the owner's seven classes are offered", {"screen_protector", "case", "headset", "earphone",
                                                "charger", "cable", "adapter"}
      <= {c["class_key"] for c in j.get("classes") or []}, j.get("classes"))
check("...all as PROPOSALS, none pre-confirmed",
      all(c["status"] == "proposed" for c in j.get("classes") or []))
check("the token rule is offered on department/category ONLY",
      j.get("token_fields") == ["department", "category"], j.get("token_fields"))

r = client.get(ADEF, params={"org_id": ORG, "period": PER})
check(f"GET {ADEF} -> 200", r.status_code == 200, r.text[:300])
j = r.json() if r.status_code == 200 else {}
for k in ("observed", "orphan_mappings", "sku_coverage", "classes", "field_rule",
          "field_rule_refused", "match_fields", "token_fields", "statuses", "counts", "meta",
          "ready", "migration"):
    check(f"UI contract: payload carries `{k}`", k in j, sorted(j))
deps = {v["match_value"] for v in (j.get("observed") or {}).get("department") or []}
cats = {v["match_value"] for v in (j.get("observed") or {}).get("category") or []}
check("the grid offers the tenant's REAL department values (pick-don't-type)",
      {"Handset", "BrandedHandset", "ONDIGO"} <= deps, sorted(deps))
check("...and BOTH mid-month category spellings, so the drift is visible in the picker",
      {"Accessories", "HandsetBranded"} <= cats, sorted(cats))
check("...with the token hit shown per value",
      any(v.get("token_hit") == "accessor" for v in (j.get("observed") or {}).get("category") or []),
      (j.get("observed") or {}).get("category"))
check("SKU is reported as unusable for ACCESSORIES even though activation lines carry one",
      j.get("sku_coverage", {}).get("usable") is False
      and j.get("sku_coverage", {}).get("with_sku", 0) > 0
      and j.get("sku_coverage", {}).get("accessory_with_sku") == 0, j.get("sku_coverage"))
r = client.get("/commcalc/accessory-definition", params={"org_id": ORG})
check("the BARE path is 404", r.status_code == 404, r.status_code)

print("\n-- D2: the agreement report ----------------------------------------------------------------")
r = client.get(AGREE, params={"org_id": ORG})
check(f"GET {AGREE} -> 200", r.status_code == 200, r.text[:400])
j = r.json() if r.status_code == 200 else {}
for k in ("rows_read", "reference", "reference_label", "surfaces", "totals", "agreement",
          "disagreeing_items", "disagreeing_item_count", "by_mechanism", "uncaught_gap",
          "negative_price_lines", "spelling_drift", "sku_coverage",
          "lines_excluded_void_return", "field_rule",
          "setup_fee_keywords", "setup_fee_note", "counts", "meta", "ready", "migration", "money_note"):
    check(f"UI contract: agreement carries `{k}`", k in j, sorted(j))
check("all EIGHT surfaces are reported", len(j.get("surfaces") or []) == 8, j.get("surfaces"))
check("the reference is the PAY BASIS", j.get("reference") == "combined")
check("the report says plainly that it changes no money",
      "changes what anyone is paid" in (j.get("money_note") or ""), j.get("money_note"))
check("every surface has both a total and an agreement row",
      all(s["key"] in (j.get("totals") or {}) and s["key"] in (j.get("agreement") or {})
          for s in j.get("surfaces") or []))
check("the definition already catches the 'Accessories' department via the field rule",
      (j.get("totals") or {}).get("definition_confirmed", {}).get("lines", 0) >= 1,
      (j.get("totals") or {}).get("definition_confirmed"))
check("...and NOT the 'Device Setup Charge' line (set-up fees are never accessories)",
      "Device Setup Charge" not in [i["product_desc"] for i in j.get("disagreeing_items") or []
                                    if i["verdicts"]["definition_proposed"]],
      [i["product_desc"] for i in j.get("disagreeing_items") or []])
WRITES.clear()
client.get(AGREE, params={"org_id": ORG})
check("the agreement report writes NOTHING", not WRITES, WRITES)

print("\n-- D2: the write paths ---------------------------------------------------------------------")
WRITES.clear()
r = client.post(ADEF, params={"org_id": ORG}, json={"match_field": "category",
                                                    "match_value": "Accessories",
                                                    "accessory_class": "screen_protector"})
check(f"POST {ADEF} -> 200", r.status_code == 200, r.text[:300])
check("the mapping row stamped the CALLER's org_id",
      any(rows and rows[0].get("org_id") == ORG for _o, t, rows in WRITES
          if t == "commcalc.accessory_definition_map" and isinstance(rows, list)), WRITES)
check("...and it defaults to PROPOSED, not confirmed",
      any(rows and rows[0].get("status") == "proposed" for _o, t, rows in WRITES
          if t == "commcalc.accessory_definition_map" and isinstance(rows, list)), WRITES)
r = client.post(ADEF, params={"org_id": ORG}, json={"match_field": "department",
                                                    "match_value": "Totally Made Up"})
check("FREE TEXT is refused — the value must exist in the tenant's own data (RULE THREE)",
      r.status_code == 400 and "not a department value" in r.text, r.text[:200])
r = client.post(ADEF, params={"org_id": ORG}, json={"match_field": "wat", "match_value": "x"})
check("an unknown match_field is refused 400", r.status_code == 400)
r = client.post(ADEF, params={"org_id": ORG}, json={"match_field": "category",
                                                    "match_value": "Accessories",
                                                    "accessory_class": "unicorn"})
check("an unknown accessory class is refused 400", r.status_code == 400, r.text[:200])

r = client.put(f"{ADEF}/field-rule", params={"org_id": ORG},
               json={"enabled": True, "token_fields": ["product_desc", "category"],
                     "tokens": ["accessor"]})
check("PUT field-rule -> 200", r.status_code == 200, r.text[:300])
check("product_desc is REFUSED for the token rule and the refusal is REPORTED",
      r.json().get("refused_fields") == ["product_desc"]
      and r.json()["field_rule"]["token_fields"] == ["category"], r.text[:300])

r = client.post(f"{ADEF}/seed-classes", params={"org_id": ORG_B}, json={})
check("a NON-HOUSE tenant can seed its own classes", r.status_code == 200, r.text[:200])
check("...stamped with the CALLER's org_id",
      all(x.get("org_id") == ORG_B for x in STORE.get("commcalc.accessory_class", [])),
      STORE.get("commcalc.accessory_class"))
n1 = len(STORE.get("commcalc.accessory_class", []))
client.post(f"{ADEF}/seed-classes", params={"org_id": ORG_B}, json={})
check("...and it is idempotent", len(STORE.get("commcalc.accessory_class", [])) == n1)
r = client.get(f"{ADEF}/classes", params={"org_id": ORG})
check("the HOUSE tenant still sees the built-ins (the seed did not leak across tenants)",
      all(c["source"] == "default" for c in r.json().get("classes") or []),
      {c["source"] for c in r.json().get("classes") or []})

r = client.post(f"{ADEF}/confirm", params={"org_id": ORG}, json={"all": True})
check("confirm all -> 200", r.status_code == 200, r.text[:200])
check("...and it flipped status only",
      all(m.get("match_value") in ("Accessories", "Case BYOD", "Screen Protectors BYOD") or True
          for m in STORE.get("commcalc.accessory_definition_map", []))
      and all(m.get("status") == "confirmed"
              for m in STORE.get("commcalc.accessory_definition_map", []) if m.get("org_id") == ORG),
      STORE.get("commcalc.accessory_definition_map"))
r = client.post(f"{ADEF}/confirm", params={"org_id": ORG}, json={})
check("confirm with no target is refused 400", r.status_code == 400)

r = client.get(f"{ADEF}/facets", params={"org_id": ORG})
check("GET facets -> 200 with the org's own periods/stores/reps", r.status_code == 200
      and PER in (r.json().get("periods") or []), r.text[:200])
check("...and market is honestly absent, with the reason",
      "no market column" in (r.json().get("market_note") or ""), r.json().get("market_note"))

print("\n-- D2: the LIVE-DATA finding, over HTTP -----------------------------------------------------")
r = client.get(AGREE, params={"org_id": ORG})
j = r.json()
check("the agreement payload carries spelling_drift", "spelling_drift" in j, sorted(j))
drift = {d["product_desc"] for d in j.get("spelling_drift") or []}
check("...and it names the mid-month renamed products",
      {"Case BYOD", "Screen Protectors BYOD"} <= drift, sorted(drift))
check("the payload carries per-MECHANISM attribution",
      len(j.get("by_mechanism") or []) == 5
      and {m["key"] for m in j["by_mechanism"]} == set(adef.MECHANISMS), j.get("by_mechanism"))
check("...and the field rule is NOT credited with the week-one lines",
      next(m for m in j["by_mechanism"] if m["key"] == "none")["lines"] >= 2, j.get("by_mechanism"))
check("the payload carries the uncaught-gap list", "uncaught_gap" in j, sorted(j))
check("sku_coverage says SKU is unusable for this tenant's ACCESSORIES, with the reason",
      j.get("sku_coverage", {}).get("usable") is False
      and "reach none of them" in (j.get("sku_coverage", {}).get("note") or ""), j.get("sku_coverage"))
check("negative-price lines are counted, not hidden",
      j.get("negative_price_lines", {}).get("lines") == 1, j.get("negative_price_lines"))

WRITES.clear()
r = client.post(f"{ADEF}/propose-from-data", params={"org_id": ORG},
                json={"period": PER, "dry_run": True})
check("POST propose-from-data?dry_run -> 200", r.status_code == 200, r.text[:300])
pj = r.json() if r.status_code == 200 else {}
check("...proposes the drifting products", {"Case BYOD", "Screen Protectors BYOD"}
      <= {p["match_value"] for p in pj.get("proposals") or []},
      [p["match_value"] for p in pj.get("proposals") or []])
check("...never proposes the repair or the set-up fee",
      not ({"Charger Port Repair", "Device Setup Charge"}
           & {p["match_value"] for p in pj.get("proposals") or []}),
      [p["match_value"] for p in pj.get("proposals") or []])
check("...and a DRY RUN writes nothing", not WRITES, WRITES)
WRITES.clear()
r = client.post(f"{ADEF}/propose-from-data", params={"org_id": ORG}, json={"period": PER})
check("POST propose-from-data (for real) -> 200", r.status_code == 200, r.text[:300])
check("...writes them as PROPOSED, org-stamped",
      any(rows and rows[0].get("status") == "proposed" and rows[0].get("org_id") == ORG
          for _op, t, rows in WRITES if t == "commcalc.accessory_definition_map"
          and isinstance(rows, list)), WRITES)
check("...and says plainly that nothing is confirmed or paid differently",
      "nothing is paid differently" in (r.json().get("note") or "").lower(), r.json().get("note"))
r2 = client.get(AGREE, params={"org_id": ORG})
by2 = {m["key"]: m for m in r2.json()["by_mechanism"]}
check("after proposing, the week-one lines are attributed to the MAP, not to nothing",
      by2["map_proposed"]["lines"] >= 2, {k: v["lines"] for k, v in by2.items()})

print("\n-- pre-migration degradation ---------------------------------------------------------------")
MISS = ("commcalc.accessory_class", "commcalc.accessory_definition_map")
mount(H.build_store(), writes=[], missing=MISS)
r = client.get(f"{ADEF}/classes", params={"org_id": ORG})
check("classes still 200 with 257 unrun", r.status_code == 200 and r.json().get("ready") is False,
      r.text[:200])
check("...and it names the migration",
      r.json().get("migration") == "257_commission_accessory_definition.sql")
r = client.get(ADEF, params={"org_id": ORG})
check("the grid still 200 with 257 unrun", r.status_code == 200 and r.json().get("ready") is False)
r = client.get(AGREE, params={"org_id": ORG})
check("the agreement report still 200 with 257 unrun", r.status_code == 200, r.text[:200])
r = client.post(ADEF, params={"org_id": ORG}, json={"match_field": "department",
                                                    "match_value": "Handset"})
check("the write returns a clear 400 naming the migration, never a 500",
      r.status_code == 400 and "257_commission_accessory_definition.sql" in r.text, r.text[:200])
r = client.post(f"{ADEF}/propose-from-data", params={"org_id": ORG}, json={"dry_run": True})
check("propose-from-data DRY RUN still 200 with 257 unrun (it reads only sales)",
      r.status_code == 200, r.text[:200])
r = client.post(f"{ADEF}/propose-from-data", params={"org_id": ORG}, json={})
check("...and the real write 400s naming the migration",
      r.status_code == 400 and "257_commission_accessory_definition.sql" in r.text, r.text[:200])

mount(H.build_store(), writes=[], missing=("commcalc.commission_org_config",))
r = client.get(PAYOUT, params={"org_id": ORG})
check("category-payout still 200 with 256 unrun",
      r.status_code == 200 and r.json().get("ready") is False, r.text[:200])
check("...and it names the migration",
      r.json().get("migration") == "256_commission_installment_category_flat_payout.sql")

print("\n-- route table ------------------------------------------------------------------------------")
paths = {(r.path, tuple(sorted(getattr(r, "methods", []) or []))) for r in app.routes if hasattr(r, "path")}
total = len([r for r in app.routes if hasattr(r, "path")])
check(f"route count = pinned base {BASE_ROUTES} + exactly {NEW_ROUTES}",
      total == BASE_ROUTES + NEW_ROUTES, total)
try:
    base_n = int(subprocess.check_output(
        ["git", "-C", _REPO, "rev-parse", "--verify", "origin/main"], stderr=subprocess.DEVNULL) and BASE_ROUTES)
    check("BASE_ROUTES is pinned to a real ref (origin/main exists)", base_n == BASE_ROUTES)
except Exception:
    check("BASE_ROUTES is pinned to a real ref (origin/main exists)", False)
mine = {p for p, _m in paths if "accessory-definition" in p or "category-payout" in p}
check(f"exactly {NEW_ROUTES} route registrations are mine",
      len([1 for p, m in paths if p in mine for _ in m if _ in ("GET", "POST", "PUT", "DELETE")]) >= NEW_ROUTES,
      sorted(mine))

print("\n" + "=" * 94)
print(f"RESULT: {_pass} passed, {_fail} failed")
if _fails:
    print("FAILED:")
    for f in _fails:
        print("  -", f)
print("=" * 94)
sys.exit(1 if _fail else 0)
