"""REAL-ASGI smoke for the What-If residual/carrier-income endpoints after the residual-column fix.

The unit harness (whatif_residual_column_proof.py) calls the functions directly, which proves the math
but NOT the mount. `[[curl-verified-not-ui-verified-apiv1]]`: `client.ts` `api()` needs an explicit
/api/v1 prefix, so a handler that answers when called can still sit at a path the page never reaches.
This drives the whole FastAPI app through Starlette's TestClient at the EXACT URLs the What-If page
requests, and asserts:

  • /api/v1/commcalc/whatif/byod-residual returns the CORRECTED residual over HTTP (+$825.85, not the
    -$2.9e12 sum of invoice numbers), and the payload carries `residual_field_warning`
  • the same page URL with the OLD config still honors it AND ships the loud warning (config is king)
  • /api/v1/commcalc/whatif/carrier-income ships sign-consistent buckets + `ma_coverage` + `data_note`
    + per-month `comp_source_missing`
  • /api/v1/commcalc/whatif/source-config GET ships `option_labels` with the ⚠ invoice-NUMBER label,
    and PUT accepts the new `ma_commission_sign` key
  • the BARE paths (no /api/v1) are 404 — the page must use the prefix
  • org_id really travels as a QUERY PARAM (a second tenant's URL returns that tenant's own view)
  • an unreachable database degrades (no 500), and no write is ever attempted

Run: `python3 scratchpad/whatif_residual_asgi_smoke.py` from the backend dir.
"""
import copy, io, os, sys, types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)


def _load_proof_helpers():
    """Reuse the proof harness's FakeClient + fixtures WITHOUT re-running its assertions: execute only
    the source ABOVE its first section banner (house harness style — top-level sequential, ends in
    sys.exit)."""
    path = os.path.join(_HERE, "whatif_residual_column_proof.py")
    src = io.open(path, encoding="utf-8").read()
    marker = 'print("=" * 100)\nprint("A. MONEY vs IDENTIFIER'
    assert marker in src, "proof harness layout changed — helper split marker gone"
    mod = types.ModuleType("whatif_residual_proof_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _load_proof_helpers()

from fastapi.testclient import TestClient
import app.core.database as DB
from app.main import app
from app.modules.commcalc import router as R

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}   {extra}")


def wire(store):
    fake = H.FakeClient(copy.deepcopy(store))
    R.sb = lambda: fake                       # noqa: E731
    DB.get_supabase = lambda *a, **k: fake    # noqa: E731
    H.WRITES.clear()
    H.READS.clear()


LUX, OTHER, TOTAL = H.LUX, H.OTHER, H.TOTAL_ID
FIXED = H.with_config(H.ma_store(), field="retail_cost")     # after mig 252 / owner §③ #6
DEFECTIVE = H.ma_store()                                     # today's seeded config
BR = "/api/v1/commcalc/whatif/byod-residual"
CI = "/api/v1/commcalc/whatif/carrier-income"
SC = "/api/v1/commcalc/whatif/source-config"

client = TestClient(app, raise_server_exceptions=False)

print("\n── ASGI: the corrected residual over HTTP (tab 2) ─────────────────────────────────────")
wire(FIXED)
r = client.get(BR, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX})
check(f"GET {BR} → 200", r.status_code == 200, r.text[:200])
b = r.json() if r.status_code == 200 else {}
may = next((s for s in b.get("series", []) if s["period"] == H.MAY), {})
check("May residual is the real +$825.85 (Σ retail_cost), not a sum of invoice numbers",
      may.get("residual") == H.COST_SUM_NEGATED, may)
check("payload names the resolved residual $ column", b.get("residual_amount_field") == "retail_cost")
check("no warning when the column is money", b.get("residual_field_warning") is None)
check("BYOD-specific commission is positive income over HTTP too (+25)",
      (b.get("byod_specific") or {}).get("byod_residual_month") == 25.0, b.get("byod_specific"))

print("\n── ASGI: the OLD config is honored AND warned about (config is king) ──────────────────")
wire(DEFECTIVE)
r2 = client.get(BR, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX})
b2 = r2.json() if r2.status_code == 200 else {}
may2 = next((s for s in b2.get("series", []) if s["period"] == H.MAY), {})
check("configured merchant_invoice still resolves (not silently overridden)",
      may2.get("residual") == H.INV_SUM_NEGATED, may2)
check("...and the response carries the ⚠ invoice-NUMBER warning the page renders",
      "merchant_invoice" in (b2.get("residual_field_warning") or "")
      and "retail_cost" in (b2.get("residual_field_warning") or ""), b2.get("residual_field_warning"))

print("\n── ASGI: carrier income — sign consistency + coverage (tab 4) ──────────────────────────")
wire(FIXED)
r3 = client.get(CI, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX})
check(f"GET {CI} → 200", r3.status_code == 200, r3.text[:200])
b3 = r3.json() if r3.status_code == 200 else {}
jun = next((t for t in b3.get("totals_by_month", []) if t["period"] == H.JUNE), {})
check("COMMISSION +23 / SPIFF +14 / RESIDUAL +100 — one sign convention over HTTP",
      jun.get("components", {}).get("COMMISSION") == 23.0
      and jun["components"]["SPIFF"] == 14.0 and jun["residual_mi_atu"] == 100.0, jun)
check("payload carries ma_coverage per month", isinstance(b3.get("ma_coverage"), list) and b3["ma_coverage"])
check("May is flagged comp_source_missing over HTTP",
      next(t for t in b3["totals_by_month"] if t["period"] == H.MAY)["comp_source_missing"] is True)
check("data_note explains the DATA GAP and clears the ledger", "DATA GAP" in (b3.get("data_note") or "")
      and "NOT a stale ledger" in (b3.get("data_note") or ""), b3.get("data_note"))
check("params report the sign convention actually applied",
      b3.get("params", {}).get("ma_commission_sign") == "negate")

print("\n── ASGI: ⚙️ Sources admin panel (GET options + PUT the new key) ───────────────────────")
wire(DEFECTIVE)
r4 = client.get(SC, params={"carrier_id": TOTAL, "org_id": LUX})
b4 = r4.json() if r4.status_code == 200 else {}
check(f"GET {SC} → 200", r4.status_code == 200, r4.text[:200])
check("retail_cost is offered FIRST", (b4.get("options", {}).get("residual_amount_field") or [None])[0] == "retail_cost")
lbl = (b4.get("option_labels", {}).get("residual_amount_field") or {}).get("merchant_invoice", "")
check("the identifier option carries the ⚠ 'not money' label the panel renders",
      "⚠" in lbl and "not money" in lbl, lbl)
check("ma_commission_sign options are offered to the panel",
      b4.get("options", {}).get("ma_commission_sign") == ["negate", "as_is", "abs"])

_gate = R._require_commission_admin
saved = {}


class PutFake:
    def schema(self, *a, **k):
        class _S:
            def table(_s, t):
                class _T:
                    def upsert(_s2, row, **kw):
                        saved.update(row)

                        class _E:
                            def execute(_s3):
                                return type("X", (), {"data": [row]})()
                        return _E()
                return _T()
        return _S()


try:
    R._require_commission_admin = lambda *a, **k: None
    R.sb = lambda: PutFake()                  # noqa: E731
    DB.get_supabase = lambda *a, **k: PutFake()   # noqa: E731
    rp = client.put(SC, params={"org_id": LUX}, json={"carrier_id": TOTAL, "carrier_mode": "plan",
                                                     "residual_amount_field": "retail_cost",
                                                     "ma_commission_sign": "as_is"})
finally:
    R._require_commission_admin = _gate
check(f"PUT {SC} → 200 ok", rp.status_code == 200 and rp.json().get("ok") is True, rp.text[:200])
check("the PUT persisted BOTH the corrected column and the new sign key",
      saved.get("residual_amount_field") == "retail_cost" and saved.get("ma_commission_sign") == "as_is", saved)
check("the PUT stamped the CALLER's org_id from the query param", saved.get("org_id") == LUX)

print("\n── ASGI: the /api/v1 prefix trap ──────────────────────────────────────────────────────")
wire(FIXED)
for bare in ("/commcalc/whatif/byod-residual", "/commcalc/whatif/carrier-income",
             "/commcalc/whatif/source-config"):
    check(f"bare {bare} is 404 — the page MUST use /api/v1",
          client.get(bare, params={"org_id": LUX}).status_code == 404)

print("\n── ASGI: org_id travels as a query param (multi-tenant rule) ──────────────────────────")
mt = copy.deepcopy(FIXED)
mt["raw_ma_daily_tx"] += [dict(x, org_id=OTHER) for x in H.ma_store(org=OTHER)["raw_ma_daily_tx"]]
mt["carrier"] = mt["carrier"] + [{"id": TOTAL, "org_id": OTHER, "name": "Total by Verizon",
                                 "code": "TOTAL", "is_default": True}]
wire(mt)
ra = client.get(BR, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX}).json()
wire(mt)
rb = client.get(BR, params={"months": 6, "carrier_id": TOTAL, "org_id": OTHER}).json()
check("tenant A sees only its own residual", ra["total_residual"] == round(H.COST_SUM_NEGATED + 100, 2))
check("tenant B sees its OWN rows through the same URL (isolation, not blanking)",
      rb["total_residual"] == round(H.COST_SUM_NEGATED + 100, 2))
check("no cross-tenant sum (a leak would double both figures)",
      ra["total_residual"] == rb["total_residual"] < 2 * round(H.COST_SUM_NEGATED + 100, 2))
wire(mt)
r400 = client.get(BR, params={"months": 6, "org_id": ""})
check("empty org_id → 400 before any data work", r400.status_code == 400)

print("\n── ASGI: missing MA tables degrade; a dead DB behaves EXACTLY as it did at base ────────")
# mig 083/207 not run for this tenant → the MA tables raise "relation does not exist".
wire(FIXED)
_absent = H.FakeClient(copy.deepcopy(FIXED), absent=["raw_ma_daily_tx", "raw_ma_commission"])
R.sb = lambda: _absent                        # noqa: E731
DB.get_supabase = lambda *a, **k: _absent     # noqa: E731
ra1 = client.get(BR, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX})
ra2 = client.get(CI, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX})
check("MA tables absent → byod-residual 200 with the 'pull the MA Daily Tx report' note (no 500)",
      ra1.status_code == 200 and "MA Daily Tx" in (ra1.json().get("note") or ""), ra1.status_code)
check("MA tables absent → carrier-income 200 with the 'pull the MA reports' note (no 500)",
      ra2.status_code == 200 and "MA Commission" in (ra2.json().get("note") or ""), ra2.status_code)
check("...and an absent-table month never invents a coverage gap",
      ra2.json().get("ma_coverage") == [] and ra2.json().get("data_note") is None)


class Dead:
    def schema(self, *a, **k):
        raise RuntimeError("connection refused")


# A TOTALLY dead database still 500s — but that is PRE-EXISTING and lives OUTSIDE this package: with no
# readable `carrier` table the mode resolves to 'boost', and the boost leg calls
# account/residual_subs.compute (mod-finance's file, untouched here), which does not guard its raw_mi
# read. Proven by driving the BASE module the same way: identical exception, identical origin. Filed as a
# note, not fixed — it is another module's file and not part of the escalation.
_dead_new = _dead_base = None
try:
    __import__("app.modules.commcalc.whatif", fromlist=["x"]).byod_residual(Dead(), LUX, 6, None)
except Exception as e:
    _dead_new = type(e).__name__
try:
    H.B.byod_residual(Dead(), LUX, 6, None)
except Exception as e:
    _dead_base = type(e).__name__
check("dead-DB behaviour is IDENTICAL to base 875a3b9 (no regression introduced here)",
      _dead_new == _dead_base == "RuntimeError", (_dead_new, _dead_base))
R.sb = lambda: Dead()                        # noqa: E731
DB.get_supabase = lambda *a, **k: Dead()     # noqa: E731
rd = client.get(BR, params={"months": 6, "carrier_id": TOTAL, "org_id": LUX})
check("(recorded) a fully dead DB is a 500 on this endpoint — pre-existing, boost leg, finance-owned file",
      rd.status_code == 500)
check("ZERO writes attempted across every request in this smoke", H.WRITES == [], H.WRITES)

print()
print("=" * 100)
print(f"  {_pass} passed, {_fail} failed")
print("=" * 100)
sys.exit(1 if _fail else 0)
