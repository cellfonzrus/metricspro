"""REAL-ASGI smoke for MA Daily Tx product-name classification.

The unit harness (ma_product_class_proof.py) calls the handlers directly, which proves the logic but
NOT the mount. `[[curl-verified-not-ui-verified-apiv1]]`: `client.ts` `api()` needs an explicit /api/v1
prefix, so a handler that answers when called can still sit at a path the page never reaches. This
drives the whole FastAPI app through Starlette's TestClient at the EXACT URLs
`(platform)/commcalc/ma-product-class/page.tsx` fetches, and asserts:

  • every URL the page calls answers 200 over HTTP, with the keys the page destructures
  • the BARE paths (no /api/v1) are 404 — the page must use the prefix
  • org_id really travels as a QUERY PARAM (a second tenant's URL returns that tenant's own view),
    and an empty org_id is rejected
  • the read endpoints attempt ZERO writes (the fake client raises on any write)
  • the write endpoints refuse the reserved class, an unknown class and an identifier amount column
  • confirming over HTTP flips status only — no class changes, no money surface is touched
  • the preview's two readings + delta ship over HTTP
  • pre-254 (all three config tables absent) every GET still answers 200 with ready=false

Run: `python3 scratchpad/ma_product_class_asgi_smoke.py` from the backend dir.
"""
import copy, io, os, sys, types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)


def _load_proof_helpers():
    """Reuse the proof harness's FakeClient + fixtures WITHOUT re-running its assertions: execute only
    the source ABOVE its first section banner (house harness style)."""
    path = os.path.join(_HERE, "ma_product_class_proof.py")
    src = io.open(path, encoding="utf-8").read()
    marker = 'print("=" * 100)\nprint("A. SEED FIDELITY")'
    assert marker in src, "proof harness layout changed — helper split marker gone"
    mod = types.ModuleType("ma_product_class_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _load_proof_helpers()

from fastapi.testclient import TestClient
import app.core.database as DB
from app.main import app
from app.modules.commcalc import router as R
from app.modules.commcalc import ma_product_class as M

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}   {extra}")


def wire(store, **kw):
    fake = H.FakeClient(copy.deepcopy(store), **kw)
    R.sb = lambda: fake                       # noqa: E731
    DB.get_supabase = lambda *a, **k: fake    # noqa: E731
    H.WRITES.clear()
    H.READS.clear()
    return fake


LUX, OTHER = H.LUX, H.OTHER
GRID = "/api/v1/commcalc/ma-product-class"
CLASSES = "/api/v1/commcalc/ma-product-class/classes"
FACETS = "/api/v1/commcalc/ma-product-class/facets"
PREVIEW = "/api/v1/commcalc/ma-product-class/preview"
CONFIRM = "/api/v1/commcalc/ma-product-class/confirm"
SEED = "/api/v1/commcalc/ma-product-class/seed-proposals"

tc = TestClient(app)

print("=" * 100)
print("1. THE EXACT URLs THE PAGE FETCHES — 200 + the keys the page reads")
print("=" * 100)
wire(H.base_store(LUX))
r = tc.get(GRID, params={"org_id": LUX})
check("GET /api/v1/commcalc/ma-product-class -> 200", r.status_code == 200, r.text[:200])
b = r.json()
for k in ("items", "counts", "dollars", "classes", "assignable", "unmapped_key", "statuses",
          "using_builtin_proposals", "ready", "migration", "source", "read"):
    check("grid payload carries `%s` (the page destructures it)" % k, k in b)
it = {i["product_name"]: i for i in b["items"]}
for k in ("product_name", "product_class", "status", "note", "lines", "total", "sign",
          "months", "raw_variants", "first_seen", "last_seen", "id", "saved"):
    check("each grid row carries `%s`" % k, k in it["Total MAX 5G Plan $55"])

r = tc.get(CLASSES, params={"org_id": LUX})
check("GET .../classes -> 200", r.status_code == 200, r.text[:200])
cb = r.json()
check("classes payload offers the vocabulary + assignable set",
      len(cb["classes"]) == len(M.DEFAULT_CLASSES) and M.UNMAPPED not in cb["assignable"])

r = tc.get(FACETS, params={"org_id": LUX})
check("GET .../facets -> 200", r.status_code == 200, r.text[:200])
fb = r.json()
check("facets are built from the org's REAL data (periods/stores/reps present)",
      fb["periods"] and fb["stores"] == ["Store A", "Store B"] and fb["reps"] == ["rep1", "rep2"], fb)
check("facets offer only MONEY columns for the amount picker",
      fb["money_columns"] == ["retail_cost", "merchant_discount"], fb["money_columns"])
check("facets never offer the merchant_invoice identifier",
      "merchant_invoice" not in fb["money_columns"])

r = tc.get(PREVIEW, params={"org_id": LUX})
check("GET .../preview -> 200", r.status_code == 200, r.text[:200])
pb = r.json()
for k in ("preview", "unmapped", "class_labels", "note", "ready", "migration"):
    check("preview payload carries `%s`" % k, k in pb)
for k in ("months", "confirmed", "proposed", "classes_present", "delta"):
    check("preview.preview carries `%s`" % k, k in pb["preview"])
check("the preview NOTE says out loud that it changes nothing",
      "READ-ONLY" in pb["note"] and "payout" in pb["note"], pb["note"][:120])

print()
print("=" * 100)
print("2. THE /api/v1 TRAP — bare paths must 404")
print("=" * 100)
for p in ("/commcalc/ma-product-class", "/commcalc/ma-product-class/classes",
          "/commcalc/ma-product-class/facets", "/commcalc/ma-product-class/preview"):
    check("bare %s is 404 (page must use /api/v1)" % p, tc.get(p, params={"org_id": LUX}).status_code == 404)

print()
print("=" * 100)
print("3. org_id IS A QUERY PARAM — real tenant isolation over HTTP")
print("=" * 100)
lux = tc.get(GRID, params={"org_id": LUX}).json()
oth = tc.get(GRID, params={"org_id": OTHER}).json()
check("a second tenant's URL returns that tenant's OWN view", lux["dollars"] != oth["dollars"],
      (lux["dollars"], oth["dollars"]))
lux_names = {i["product_name"] for i in lux["items"] if i["lines"]}
oth_names = {i["product_name"] for i in oth["items"] if i["lines"]}
check("the second tenant sees strictly fewer real names (no leak)", oth_names < lux_names)
check("the unmapped name is the first tenant's alone",
      "Brand New Widget Nobody Mapped" in lux_names and "Brand New Widget Nobody Mapped" not in oth_names)
check("an EMPTY org_id is rejected, not defaulted to the house org",
      tc.get(GRID, params={"org_id": ""}).status_code == 400)
check("an empty org_id is rejected on the preview too",
      tc.get(PREVIEW, params={"org_id": ""}).status_code == 400)

print()
print("=" * 100)
print("4. READ ENDPOINTS ATTEMPT ZERO WRITES")
print("=" * 100)
wire(H.base_store(LUX))                      # allow_writes=False -> any write raises
for url in (GRID, CLASSES, FACETS, PREVIEW):
    tc.get(url, params={"org_id": LUX})
check("no GET attempted a single write", H.WRITES == [], H.WRITES[:3])
check("every read over HTTP was org-scoped",
      all(any(k == 'eq' and c == 'org_id' and v == LUX for k, c, v in f) for _t, f, _c in H.READS))

print()
print("=" * 100)
print("5. WRITE ENDPOINTS — refusals + the confirm flow")
print("=" * 100)
wire(H.base_store(LUX), allow_writes=True)
r = tc.post(GRID, params={"org_id": LUX},
            json={"product_name": "X", "product_class": M.UNMAPPED})
check("POST refuses the reserved class 'unmapped' (400)", r.status_code == 400, r.text[:160])
check("...and says why", "reserved" in r.text)
r = tc.post(GRID, params={"org_id": LUX}, json={"product_name": "X", "product_class": "nope"})
check("POST refuses an unknown class (400)", r.status_code == 400 and "unknown class" in r.text, r.text[:160])
r = tc.post(GRID, params={"org_id": LUX}, json={"product_name": "", "product_class": "fee"})
check("POST refuses a blank product name (400)", r.status_code == 400)
r = tc.get(GRID, params={"org_id": LUX, "amount_column": "merchant_invoice"})
check("GET refuses the merchant_invoice IDENTIFIER as an amount column (400)",
      r.status_code == 400 and "identifier" in r.text, r.text[:160])
r = tc.get(PREVIEW, params={"org_id": LUX, "amount_column": "merchant_invoice"})
check("...on the preview too", r.status_code == 400 and "identifier" in r.text)

before = tc.get(GRID, params={"org_id": LUX}).json()
cls_before = {i["product_name"]: i["product_class"] for i in before["items"]}
r = tc.post(CONFIRM, params={"org_id": LUX}, json={"product_names": ["Total MAX 5G Plan $55"]})
check("POST .../confirm -> 200", r.status_code == 200, r.text[:200])
check("it reports exactly what it confirmed", r.json()["confirmed"] == ["Total MAX 5G Plan $55"], r.json())
after = tc.get(GRID, params={"org_id": LUX}).json()
cls_after = {i["product_name"]: i["product_class"] for i in after["items"]}
check("confirming changed NO class anywhere", cls_before == cls_after)
check("confirming moved exactly one name to confirmed",
      after["counts"]["confirmed"] == before["counts"]["confirmed"] + 1,
      (before["counts"], after["counts"]))
pv = tc.get(PREVIEW, params={"org_id": LUX}).json()["preview"]
check("the confirmed reading now counts that line, the proposed reading is unchanged",
      pv["confirmed"]["by_class"].get("billpayment", {}).get("lines") == 1
      and pv["proposed"]["by_class"][M.UNMAPPED]["lines"] == 1, pv["confirmed"]["by_class"])
check("the delta shrank by exactly that line", pv["delta"]["lines_newly_classified"] == len(H.SAMPLE) - 2,
      pv["delta"])

wire(H.base_store(OTHER, with_map=False), allow_writes=True)
r = tc.post(SEED, params={"org_id": OTHER}, json={})
check("POST .../seed-proposals -> 200 for a NON-house tenant", r.status_code == 200, r.text[:200])
check("it seeds all 69 built-ins as PROPOSALS",
      r.json()["inserted"] == len(M.DEFAULT_PROPOSALS) and r.json()["status"] == "proposed", r.json())
seeded = [p for k, t, p in H.WRITES if k == "insert" and t == M.MAP_TABLE]
check("every seeded row is stamped with the CALLER's org_id (not the house org)",
      seeded and all(row["org_id"] == OTHER for row in seeded[0]))
r2 = tc.post(SEED, params={"org_id": OTHER}, json={})
check("seeding twice is idempotent (0 inserted the second time)", r2.json()["inserted"] == 0, r2.json())

print()
print("=" * 100)
print("6. PRE-254 — every GET still answers 200")
print("=" * 100)
wire({"raw_ma_daily_tx": H.tx_rows(LUX)}, absent=[M.CLASS_TABLE, M.MAP_TABLE, M.SOURCE_TABLE])
for url in (GRID, CLASSES, PREVIEW, FACETS):
    r = tc.get(url, params={"org_id": LUX})
    check("pre-254 GET %s -> 200 (never a 500)" % url.rsplit("/", 1)[-1], r.status_code == 200, r.text[:160])
gb = tc.get(GRID, params={"org_id": LUX}).json()
check("pre-254 the grid says ready=false and names the migration",
      gb["ready"] is False and gb["migration"] == "254_commission_ma_product_class.sql")
check("pre-254 the built-in proposals still classify, read-only",
      {i["product_name"]: i for i in gb["items"]}["Total MAX 5G Plan $55"]["product_class"] == "billpayment")
check("pre-254 nothing is confirmed", gb["counts"]["confirmed"] == 0, gb["counts"])
r = tc.post(CONFIRM, params={"org_id": LUX}, json={"all": True})
check("pre-254 confirm returns a clear 400 naming the migration",
      r.status_code == 400 and "254" in r.text, r.text[:160])

print()
print("=" * 100)
print("RESULT: %d passed, %d failed" % (_pass, _fail))
print("=" * 100)
sys.exit(1 if _fail else 0)
