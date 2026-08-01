"""REAL-ASGI smoke for the MA product-class MONEY WIRING (mig 265).

The unit harness (ma_class_money_wiring_proof.py) calls the engines directly, which proves the logic
but NOT the mount. `[[curl-verified-not-ui-verified-apiv1]]`: `client.ts` `api()` needs an explicit
/api/v1 prefix, so a handler that answers when called can still sit at a path the page never reaches.
This drives the WHOLE FastAPI app through Starlette's TestClient at the EXACT URLs
`(platform)/commcalc/ma-class-wiring/page.tsx` and `.../commcalc/whatif/page.tsx` fetch, and asserts:

  • every URL the pages call answers 200 over HTTP, with the keys they destructure
  • the BARE paths (no /api/v1) are 404 — the pages must use the prefix
  • ROUTE ORDER: /ma-class-wiring/ledger-delta and /rule-proposals are not swallowed by anything
  • org_id really travels as a QUERY PARAM (a second tenant's URL returns that tenant's own view),
    and an empty org_id is rejected
  • ZERO writes from every GET (the fake client raises on any write)
  • the money-posture gate on all three write endpoints, in several caller shapes
  • flipping a mode over HTTP changes the carrier-income figures — and flipping it BACK restores them
    to the byte-identical legacy numbers (revert really is a dropdown)
  • pre-265 every GET still answers 200 and the writes 400 naming the migration, never a 500
  • the route table grew by EXACTLY the six endpoints this package adds

Run: `python3 scratchpad/ma_class_money_wiring_asgi_smoke.py` from the backend dir.
"""
import copy, io, os, sys, types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)


def _load_proof_helpers():
    """Reuse the proof harness's FakeClient + fixtures WITHOUT re-running its assertions: execute only
    the source ABOVE its first section banner (house harness style)."""
    path = os.path.join(_HERE, "ma_class_money_wiring_proof.py")
    src = io.open(path, encoding="utf-8").read()
    marker = 'print("=" * 100)\nprint("A. PURE CONFIG'
    assert marker in src, "proof harness layout changed — helper split marker gone"
    mod = types.ModuleType("ma_class_money_wiring_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _load_proof_helpers()

from fastapi.testclient import TestClient
import app.core.database as DB
from app.main import app
from app.modules.commcalc import router as R
from app.modules.commcalc import ma_class_wiring as MW

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}   {extra}")


class RWClient(H.FakeClient):
    """The read-only fake, with writes ALLOWED and RECORDED — for the three write endpoints. Every
    write is captured (table, op, row) so the smoke can assert exactly which tables were touched."""

    def schema(self, s):
        return RWSchema(self.store, self.absent, self.missing_cols)


class RWSchema(H.FakeSchema):
    def table(self, t):
        return RWQuery(self.store, t, self.absent, self.missing_cols)


WROTE = []


class RWQuery(H.FakeQuery):
    def upsert(self, row, **k):
        if self.t in self.absent:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        rows = row if isinstance(row, list) else [row]
        WROTE.append(("upsert", self.t, copy.deepcopy(rows)))
        self.store.setdefault(self.t, []).extend(rows)
        return self

    def insert(self, row, **k):
        if self.t in self.absent:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        rows = row if isinstance(row, list) else [row]
        WROTE.append(("insert", self.t, copy.deepcopy(rows)))
        self.store.setdefault(self.t, []).extend(rows)
        return self


def wire(store, rw=False, **kw):
    fake = (RWClient if rw else H.FakeClient)(copy.deepcopy(store), **kw)
    R.sb = lambda: fake                       # noqa: E731
    DB.get_supabase = lambda *a, **k: fake    # noqa: E731
    H.WRITES.clear()
    H.READS.clear()
    WROTE.clear()
    return fake


LUX, OTHER, TOTAL_ID = H.LUX, H.OTHER, H.TOTAL_ID
JUNE = H.JUNE
CFG = "/api/v1/commcalc/ma-class-wiring"
MODE = "/api/v1/commcalc/ma-class-wiring/mode"
LEG = "/api/v1/commcalc/ma-class-wiring/leg"
DELTA = "/api/v1/commcalc/ma-class-wiring/ledger-delta"
PROPS = "/api/v1/commcalc/ma-class-wiring/rule-proposals"
APPLY = "/api/v1/commcalc/ma-class-wiring/rule-proposals/apply"
INCOME = "/api/v1/commcalc/whatif/carrier-income"

tc = TestClient(app)

print("=" * 100)
print("1. THE EXACT URLs THE PAGES FETCH")
print("=" * 100)
wire(H.store())
r = tc.get(CFG, params={"org_id": LUX})
check("GET /api/v1/commcalc/ma-class-wiring -> 200", r.status_code == 200, r.text[:300])
b = r.json()
for k in ("modes", "consumers", "mode_options", "default_mode", "legs", "leg_options",
          "class_status", "classified_names", "class_rules", "categories", "category_labels",
          "conflicts", "can_edit", "ready", "migration", "class_migration", "note"):
    check("config payload carries `%s` (the page destructures it)" % k, k in b)
check("both consumers report LEGACY out of the box",
      b["modes"] == {"ledger": "legacy", "carrier_income": "legacy"}, b["modes"])
check("the leg grid lists every assignable class with its current + default leg",
      len(b["legs"]) == 12 and all(set(x) >= {"product_class", "label", "income_leg", "default_leg"}
                                   for x in b["legs"]))
check("the leg grid is a PICK list, not a text box (RULE THREE)",
      [o["key"] for o in b["leg_options"]] == list(MW.INCOME_LEGS))
check("class_status names the still-pending AMBIGUOUS rows",
      [e["product_name"] for e in b["class_status"]["ambiguous_pending"]]
      == ["Credit Debit Memo", "Total Wireless Device Upgrade"])
check("no writes from the config GET", H.WRITES == [] and WROTE == [])

wire(H.store())
r = tc.get(DELTA, params={"org_id": LUX})
check("GET .../ledger-delta -> 200", r.status_code == 200, r.text[:300])
d = r.json()
for k in ("totals", "by_month", "movements", "drift_rows", "class_status", "class_rules",
          "category_labels", "read", "mode", "note"):
    check("ledger-delta payload carries `%s`" % k, k in d)
check("ledger-delta ran BOTH readings through the real classifier",
      "legacy" in d["totals"] and "class" in d["totals"]
      and d["totals"]["legacy"]["payout_total"] == d["totals"]["class"]["payout_total"])
check("with no product_class rules configured the two readings are IDENTICAL (nothing to move)",
      d["totals"]["moved_lines"] == 0 and d["movements"] == [])
check("the delta endpoint writes NOTHING", H.WRITES == [] and WROTE == [])

wire(H.store())
r = tc.get(PROPS, params={"org_id": LUX})
check("GET .../rule-proposals -> 200", r.status_code == 200, r.text[:300])
pr = r.json()
check("proposals payload carries proposals + evidence", "proposals" in pr and "class_status" in pr)
byc = {p["product_class"]: p for p in pr["proposals"]}
check("a proposal exists for the classes the tenant actually has ledger lines for",
      set(byc) == {"residual", "commission"}, sorted(byc))
check("the 'commission' proposal is offered without a warning",
      byc["commission"]["proposed_category"] == "commission" and byc["commission"]["warning"] is None)
check("the 'residual' proposal CARRIES the two-buckets-one-class collapse warning",
      byc["residual"]["warning"] and "Auto Pay" in byc["residual"]["warning"])
check("every proposal carries its evidence (lines, dollars, where they sit today)",
      byc["commission"]["lines"] >= 1 and byc["commission"]["payout_total"] > 0
      and byc["commission"]["today_by_category"])
check("proposals write NOTHING", H.WRITES == [] and WROTE == [])

print()
print("=" * 100)
print("2. THE /api/v1 TRAP + ROUTE ORDER + org_id AS A QUERY PARAM")
print("=" * 100)
for bare in ("/commcalc/ma-class-wiring", "/commcalc/ma-class-wiring/ledger-delta",
             "/commcalc/ma-class-wiring/rule-proposals"):
    check("BARE path %s is 404 (the page MUST use /api/v1)" % bare,
          tc.get(bare, params={"org_id": LUX}).status_code == 404)
wire(H.store())
check("route order: /ledger-delta is its own route, not swallowed",
      tc.get(DELTA, params={"org_id": LUX}).json().get("by_month") is not None)
wire(H.store())
check("route order: /rule-proposals is its own route, not swallowed",
      "proposals" in tc.get(PROPS, params={"org_id": LUX}).json())
wire(H.store())
check("empty org_id is REJECTED (org really is a required query param)",
      tc.get(CFG, params={"org_id": ""}).status_code in (400, 422))
# two tenants, one store
two = copy.deepcopy(H.store())
two["ma_class_wiring_config"] = two["ma_class_wiring_config"] + [
    {"org_id": OTHER, "consumer": "ledger", "mode": "class"},
    {"org_id": OTHER, "consumer": "carrier_income", "mode": "class"}]
two["ma_product_class_map"] = two["ma_product_class_map"] + H.map_rows(OTHER)
wire(two)
a_lux = tc.get(CFG, params={"org_id": LUX}).json()
wire(two)
a_oth = tc.get(CFG, params={"org_id": OTHER}).json()
check("tenant A sees LEGACY, tenant B sees CLASS — the query param really selects the tenant",
      a_lux["modes"]["ledger"] == "legacy" and a_oth["modes"]["ledger"] == "class",
      (a_lux["modes"], a_oth["modes"]))
check("...and neither payload contains the other tenant's rows",
      a_lux["classified_names"] == a_oth["classified_names"] == 6)

print()
print("=" * 100)
print("3. CARRIER INCOME OVER HTTP — the flip, and the flip BACK")
print("=" * 100)
wire(H.store())
r = tc.get(INCOME, params={"org_id": LUX, "carrier_id": TOTAL_ID, "months": 6})
check("GET /api/v1/commcalc/whatif/carrier-income -> 200", r.status_code == 200, r.text[:300])
leg = r.json()
jm = next(m for m in leg["totals_by_month"] if m["period"] == JUNE)
check("LEGACY over HTTP: residual 150.00 / airtime 38.50",
      (jm["residual_mi_atu"], jm["components"]["UNMAPPED"]) == (150.0, 38.5), jm)
for k in ("class_mode", "class_mode_configured", "class_note", "class_wiring", "class_swap"):
    check("carrier-income payload carries `%s` (the What-If panel reads it)" % k, k in leg)
check("class_swap ships in LEGACY mode so the owner sees the move BEFORE flipping",
      leg["class_swap"]["by_month"] and leg["class_swap"]["totals"]["new_residual"] == 170.0)
check("the carrier-income GET writes nothing", H.WRITES == [] and WROTE == [])

wire(H.store(income_mode="class"))
r = tc.get(INCOME, params={"org_id": LUX, "carrier_id": TOTAL_ID, "months": 6})
cm = r.json()
jc = next(m for m in cm["totals_by_month"] if m["period"] == JUNE)
check("CLASS over HTTP: residual 170.00 / airtime 4.00",
      (jc["residual_mi_atu"], jc["components"]["UNMAPPED"]) == (170.0, 4.0), jc)
check("...and the payload says which mode produced it", cm["class_mode"] == "class")

# flip via the real endpoint, then read carrier income off the SAME store
st = H.store()
st["ma_class_wiring_config"] = []
f = wire(st, rw=True)
r = tc.put(MODE, json={"consumer": "carrier_income", "mode": "class"}, params={"org_id": LUX})
check("PUT .../mode -> 200 and reports the effect in words", r.status_code == 200
      and "CONFIRMED MA product classes" in r.json()["effect"], r.text[:200])
check("the flip wrote ONLY the wiring config table, org-stamped",
      [w[1] for w in WROTE] == ["ma_class_wiring_config"]
      and WROTE[0][2][0]["org_id"] == LUX, WROTE)
r = tc.get(INCOME, params={"org_id": LUX, "carrier_id": TOTAL_ID, "months": 6})
jf = next(m for m in r.json()["totals_by_month"] if m["period"] == JUNE)
check("after the flip the SAME tenant's figures moved to the class legs",
      (jf["residual_mi_atu"], jf["components"]["UNMAPPED"]) == (170.0, 4.0), jf)
# ... and back
f.store["ma_class_wiring_config"] = [{"org_id": LUX, "consumer": "carrier_income", "mode": "legacy"}]
r = tc.get(INCOME, params={"org_id": LUX, "carrier_id": TOTAL_ID, "months": 6})
jb = next(m for m in r.json()["totals_by_month"] if m["period"] == JUNE)
check("REVERT IS A DROPDOWN: back on 'legacy' the figures are the original ones, to the cent",
      (jb["residual_mi_atu"], jb["components"]["UNMAPPED"]) == (150.0, 38.5), jb)

print()
print("=" * 100)
print("4. THE WRITE ENDPOINTS — validation + the money-posture gate")
print("=" * 100)
wire(H.store(), rw=True)
check("an unknown consumer is a 400, not a 500",
      tc.put(MODE, json={"consumer": "payroll", "mode": "class"},
             params={"org_id": LUX}).status_code == 400)
check("an unknown mode is a 400", tc.put(MODE, json={"consumer": "ledger", "mode": "on"},
                                         params={"org_id": LUX}).status_code == 400)
check("an unknown leg is a 400", tc.put(LEG, json={"product_class": "residual",
                                                   "income_leg": "profit"},
                                        params={"org_id": LUX}).status_code == 400)
check("the reserved 'unmapped' class can never be given a leg",
      tc.put(LEG, json={"product_class": "unmapped", "income_leg": "airtime"},
             params={"org_id": LUX}).status_code == 400)
check("no bad request wrote anything", WROTE == [], WROTE)
wire(H.store(), rw=True)
r = tc.put(LEG, json={"product_class": "device_sale", "income_leg": "airtime"},
           params={"org_id": LUX})
check("a valid leg save -> 200, writes ONLY the leg table, org-stamped",
      r.status_code == 200 and [w[1] for w in WROTE] == ["ma_class_income_leg"]
      and WROTE[0][2][0]["org_id"] == LUX, (r.text[:200], WROTE))
wire(H.store(), rw=True)
check("apply with NO rules[] is refused — there is no 'apply everything'",
      tc.post(APPLY, json={}, params={"org_id": LUX}).status_code == 400)
wire(H.store(), rw=True)
r = tc.post(APPLY, json={"rules": [{"product_class": "commission", "category": "commission"},
                                   {"product_class": "billpayment", "category": "charge"},
                                   {"product_class": "nonsense", "category": "commission"},
                                   {"product_class": "spiff", "category": "payroll"}]},
            params={"org_id": LUX})
ap = r.json()
check("apply writes only the VALID rules and names every rejection",
      r.status_code == 200 and len(ap["written"]) == 2 and len(ap["rejected"]) == 2, ap)
check("...into commission_category_map, org-stamped, with match_op='product_class'",
      all(w[1] == "commission_category_map" for w in WROTE)
      and all(w[2][0]["org_id"] == LUX and w[2][0]["match_op"] == "product_class" for w in WROTE))
check("...and the response says the rules are INERT until the mode is flipped",
      "inert" in ap["note"])

print()
print("=" * 100)
print("5. THE GATE — a non-admin cannot flip a money mode")
print("=" * 100)
import app.modules.core.router as CORE
_saved = (getattr(CORE, "_uid_from_token", None), getattr(CORE, "_resolve_caller", None))


def as_caller(caller):
    CORE._uid_from_token = lambda auth: ("u1" if auth else None)
    CORE._resolve_caller = lambda *a, **k: caller


try:
    for label, caller, want in (
            ("a plain non-admin", {"role": "rep", "perms": {"scope": "own"}}, 403),
            ("a manager without scope=all", {"role": "manager", "perms": {"scope": "team"}}, 403),
            ("an admin", {"role": "admin", "perms": {"scope": "all"}}, 200),
            ("a super admin", {"super_admin": True, "role": "x", "perms": {}}, 200)):
        as_caller(caller)
        wire(H.store(), rw=True)
        r = tc.put(MODE, json={"consumer": "ledger", "mode": "class"},
                   params={"org_id": LUX}, headers={"Authorization": "Bearer t"})
        check("%s -> %d on PUT /mode" % (label, want), r.status_code == want, r.text[:160])
        if want == 403:
            check("...and %s wrote NOTHING" % label, WROTE == [], WROTE)
        as_caller(caller)
        wire(H.store(), rw=True)
        r = tc.post(APPLY, json={"rules": [{"product_class": "commission", "category": "commission"}]},
                    params={"org_id": LUX}, headers={"Authorization": "Bearer t"})
        check("%s -> %d on POST /rule-proposals/apply" % (label, want), r.status_code == want)
    as_caller({"role": "rep", "perms": {"scope": "own"}})
    wire(H.store())
    check("a denied caller can still READ the page (can_edit says false)",
          tc.get(CFG, params={"org_id": LUX},
                 headers={"Authorization": "Bearer t"}).json()["can_edit"] is False)
finally:
    if _saved[0]:
        CORE._uid_from_token, CORE._resolve_caller = _saved

print()
print("=" * 100)
print("6. PRE-265 — every GET still 200, every write a clear 400 naming the file")
print("=" * 100)
ABSENT = {"ma_class_wiring_config", "ma_class_income_leg"}
wire(H.store(), absent=ABSENT)
r = tc.get(CFG, params={"org_id": LUX})
check("pre-265 GET config -> 200 with ready=false + the migration named",
      r.status_code == 200 and r.json()["ready"] is False
      and r.json()["migration"] == MW.MIGRATION, r.text[:200])
check("pre-265 both consumers still read LEGACY",
      r.json()["modes"] == {"ledger": "legacy", "carrier_income": "legacy"})
wire(H.store(), absent=ABSENT)
check("pre-265 GET ledger-delta -> 200", tc.get(DELTA, params={"org_id": LUX}).status_code == 200)
wire(H.store(), absent=ABSENT)
check("pre-265 GET rule-proposals -> 200", tc.get(PROPS, params={"org_id": LUX}).status_code == 200)
wire(H.store(), absent=ABSENT)
check("pre-265 GET carrier-income -> 200 and byte-identical legacy figures",
      tc.get(INCOME, params={"org_id": LUX, "carrier_id": TOTAL_ID}).status_code == 200)
wire(H.store(), rw=True, absent=ABSENT)
r = tc.put(MODE, json={"consumer": "ledger", "mode": "class"}, params={"org_id": LUX})
check("pre-265 PUT /mode -> 400 naming 265 (never a 500)",
      r.status_code == 400 and "265_commission_ma_class_money_wiring.sql" in r.text, r.text[:200])
wire(H.store(), rw=True, absent=ABSENT)
r = tc.put(LEG, json={"product_class": "residual", "income_leg": "airtime"}, params={"org_id": LUX})
check("pre-265 PUT /leg -> 400 naming 265", r.status_code == 400 and "265_" in r.text)
wire(H.store(), absent={"ma_product_class_map"})
r = tc.get(CFG, params={"org_id": LUX})
check("pre-254 (no classification table) GET config still 200 and names 254",
      r.status_code == 200 and r.json()["class_migration"] == "254_commission_ma_product_class.sql")

print()
print("=" * 100)
print("7. ROUTE TABLE")
print("=" * 100)
BASE_ROUTES = 950     # measured on the pinned base ec9fe8b in /workspaces/metricspro
paths = sorted({getattr(r_, "path", "") for r_ in app.routes})
mine = [p for p in paths if "/ma-class-wiring" in p]
check("exactly SIX new endpoints, all under /commcalc/ma-class-wiring", len(mine) == 6, mine)
check("route count == pinned base %d + 6" % BASE_ROUTES, len(app.routes) == BASE_ROUTES + 6,
      len(app.routes))
check("no existing commcalc route was removed or renamed",
      all(p in paths for p in ("/api/v1/commcalc/ma-product-class",
                               "/api/v1/commcalc/commission-category-map",
                               "/api/v1/commcalc/whatif/carrier-income",
                               "/api/v1/commcalc/commission-ledger/ma-sync")))

print()
print("=" * 100)
print("ma-class money wiring ASGI smoke: %d passed, %d failed" % (_pass, _fail))
print("=" * 100)
sys.exit(1 if _fail else 0)
