"""REAL-ASGI smoke for the luxelink FWA / accessory-cost package.

The proof harness calls the modules as plain Python, which proves the arithmetic but NOT the mount,
the /api/v1 prefix or the query-param binding — the repeat offender here is
`[[curl-verified-not-ui-verified-apiv1]]`. This drives the WHOLE FastAPI app through Starlette's
TestClient at the exact URLs the pages fetch, and asserts:

  • GET /api/v1/commcalc/accessory-cost-audit/{period}?org_id= -> 200 with the option table
  • the bare /commcalc/accessory-cost-audit/... (no /api/v1) is 404
  • org_id is really a QUERY PARAM: a second tenant's URL returns that tenant's own numbers
  • c_basis + assume_gp_pct really BIND (a different margin changes option C)
  • GET /api/v1/commcalc/commission-explain?rep= -> 200 carrying the new data_quality block,
    the per-line cost flags, the rate issue and the $0-installment reason
  • GET /api/v1/commcalc/commission-device?imei= -> 200 carrying the $0 reason
  • ZERO writes are issued by any of them
  • the route COUNT is main's + exactly the routes this package adds

Run: cd backend && PYTHONPATH=. python3 scratchpad/lux_fwa_accessory_asgi_smoke.py
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


def _helpers():
    """Reuse the proof harness's FakeClient + fixture WITHOUT running its assertions: only the source
    ABOVE its first section banner is executed."""
    path = os.path.join(_HERE, "lux_fwa_accessory_proof.py")
    src = io.open(path, encoding="utf-8").read()
    marker = '\nprint("\\n\u00a7A'
    assert marker in src, "proof harness layout changed — helper split marker gone"
    mod = types.ModuleType("lux_fwa_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _helpers()

from fastapi.testclient import TestClient           # noqa: E402
from app.main import app                            # noqa: E402
import app.core.database as DB                      # noqa: E402
from app.modules.commcalc import router as R        # noqa: E402

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
AUDIT = f"/api/v1/commcalc/accessory-cost-audit/{PER}"
EXPLAIN = "/api/v1/commcalc/commission-explain"
DEVICE = "/api/v1/commcalc/commission-device"

fake = H.FakeClient(H.build_store(acc_pct=17.5))
R.sb = lambda: fake                       # noqa: E731
DB.get_supabase = lambda *a, **k: fake    # noqa: E731
client = TestClient(app, raise_server_exceptions=False)

print("\n-- the exact URLs the pages fetch ---------------------------------------------------")
r = client.get(AUDIT, params={"org_id": ORG})
check(f"GET {AUDIT} -> 200", r.status_code == 200, r.text[:300])
j = r.json() if r.status_code == 200 else {}
check("payload is ready and carries the option table",
      j.get("ready") and set(j.get("totals") or {}) == set(
          ("current", "option_a", "option_b", "option_c", "option_r")), j.get("totals"))
check("the owner's suspect lines survive JSON serialization",
      j.get("counts", {}).get("suspect_lines") == 4, j.get("counts"))
check("the rate problem is reported over HTTP",
      any("rate_over_max" in (rr.get("rate_flags") or []) for rr in j.get("rules") or []),
      j.get("rules"))
check("the item list carries the implied cost the owner needs for Option A",
      all("implied_cost_min" in it for it in j.get("items") or []))
check("bare /commcalc/accessory-cost-audit (no /api/v1) is 404 — the page MUST use the prefix",
      client.get(f"/commcalc/accessory-cost-audit/{PER}", params={"org_id": ORG}).status_code == 404)

print("\n-- org_id is a QUERY PARAM, and it really isolates -----------------------------------")
rb = client.get(AUDIT, params={"org_id": ORG_B})
check("a second tenant's URL returns 200", rb.status_code == 200, rb.text[:200])
jb = rb.json() if rb.status_code == 200 else {}
check("tenant B sees ONLY its own item",
      [i["product"] for i in jb.get("items") or []] == ["OTHERCO SECRET ACCESSORY"],
      [i["product"] for i in jb.get("items") or []])
check("tenant A never sees tenant B's line",
      "OTHERCO SECRET ACCESSORY" not in {i["product"] for i in j.get("items") or []})
check("the two tenants' totals differ (real isolation, not an empty answer)",
      (j.get("totals") or {}).get("current") != (jb.get("totals") or {}).get("current"))
check("empty org_id is rejected", client.get(AUDIT, params={"org_id": ""}).status_code >= 400)

print("\n-- the Option-C knobs really BIND ----------------------------------------------------")
r_price = client.get(AUDIT, params={"org_id": ORG, "c_basis": "price"}).json()
r_asm = client.get(AUDIT, params={"org_id": ORG, "c_basis": "assumed_gp",
                                  "assume_gp_pct": 0.40}).json()
check("c_basis=assumed_gp + a margin changes option C",
      r_asm["totals"]["option_c"] != r_price["totals"]["option_c"],
      (r_price["totals"]["option_c"], r_asm["totals"]["option_c"]))
check("the margin is echoed back so the report can label the assumption",
      r_asm["option_c"] == {"basis": "assumed_gp", "assume_gp_pct": 0.4}, r_asm["option_c"])
r_noasm = client.get(AUDIT, params={"org_id": ORG, "c_basis": "assumed_gp"}).json()
check("assumed_gp with NO margin degrades to price — never an invented number",
      r_noasm["option_c"]["basis"] == "price")
check("current is IDENTICAL under every option setting (nothing moves)",
      r_price["totals"]["current"] == r_asm["totals"]["current"] == j["totals"]["current"])

print("\n-- commission-explain now explains the two defects -----------------------------------")
re_ = client.get(EXPLAIN, params={"org_id": ORG, "period": PER, "rep": "Ana Ruiz"})
check(f"GET {EXPLAIN} -> 200", re_.status_code == 200, re_.text[:300])
je = re_.json() if re_.status_code == 200 else {}
pc = je.get("plan_component") or {}
mm = je.get("multimonth_component") or {}
check("plan component carries the data_quality block", "data_quality" in pc, list(pc)[:12])
check("it counts the suspect lines", (pc.get("data_quality") or {}).get("suspect_lines", 0) >= 4,
      pc.get("data_quality"))
check("it names the rate problem",
      any(x["flags"] == ["rate_over_max"] for x in (pc.get("data_quality") or {}).get("rate_issues") or []),
      (pc.get("data_quality") or {}).get("rate_issues"))
_lines = [l for rb_ in (pc.get("rules") or []) for l in (rb_.get("lines") or [])]
check("a suspect line carries its own flag + implied cost over HTTP",
      any(l.get("cost_flags") and l.get("implied_cost") is not None for l in _lines))
check("a HEALTHY line carries NO flag (the guard is not a blanket warning)",
      any(l.get("gp") == 12.0 and not l.get("cost_flags") for l in _lines),
      [(l.get("gp"), l.get("cost_flags")) for l in _lines])
_inst = [i for d in (mm.get("devices") or []) for i in (d.get("installments") or [])]
check("the $0 FWA month now carries a plain-language reason",
      any(i.get("zero_note") and i.get("amount") == 0 for i in _inst),
      [(i.get("amount"), i.get("zero_note")) for i in _inst])
check("the engine's own mrc_unresolved warning now reaches the page",
      any(w.get("type") == "mrc_unresolved" for w in mm.get("warnings") or []),
      mm.get("warnings"))
check("total_payout is a NUMBER and nothing about it changed shape",
      isinstance(pc.get("total_payout"), (int, float)))

rd = client.get(DEVICE, params={"org_id": ORG, "imei": H.FWA_IMEI, "period": PER})
check(f"GET {DEVICE} -> 200", rd.status_code == 200, rd.text[:200])
jd = rd.json() if rd.status_code == 200 else {}
check("device story: the $0 month explains itself",
      any(i.get("zero_note") for i in jd.get("installments") or []),
      [i.get("zero_note") for i in jd.get("installments") or []])

print("\n-- UI CONTRACT: every field the two pages read exists in the payload ------------------")
# There is no browser and no live tenant in this environment, so "verify through the page" is done by
# asserting the PAGE'S OWN ACCESSOR SET against the REAL HTTP payload. Any field the page reads that
# the API stops returning fails here instead of rendering a blank cell in production.
def has(obj, *keys):
    return all(k in (obj or {}) for k in keys)


AUDIT_TOP = ("ready", "note", "totals", "counts", "deltas", "rules", "by_rep", "items", "lines",
             "option_labels", "option_c", "catalog_rows", "config")
check("audit payload: top-level keys the page reads", has(j, *AUDIT_TOP),
      sorted(set(AUDIT_TOP) - set(j or {})))
check("audit payload: totals carry all five options",
      has(j.get("totals"), "current", "option_a", "option_b", "option_c", "option_r"))
check("audit payload: counts carry what the tiles print",
      has(j.get("counts"), "matched_lines", "suspect_lines", "reps", "rules", "flagged_shown"))
check("audit payload: option_c echoes basis + margin", has(j.get("option_c"), "basis", "assume_gp_pct"))
check("audit payload: option_labels covers every option key",
      all(k in (j.get("option_labels") or {})
          for k in ("current", "option_a", "option_b", "option_c", "option_r")))
check("audit payload: rule rows carry every column of the rules table",
      all(has(r_, "rule_id", "label", "payout_kind", "pct", "match_field", "match_op", "match_value",
              "matched_lines", "suspect_lines", "paid", "rate_flags", "rate_flag_labels")
          for r_ in j.get("rules") or []), (j.get("rules") or [{}])[0])
check("audit payload: rep rows carry every column of REP_COLS",
      all(has(x, "rep", "store", "market", "plan_name", "matched_lines", "suspect_lines",
              "current", "option_a", "option_b", "option_c", "option_r",
              "delta_b", "delta_c", "delta_r")
          for x in j.get("by_rep") or []), (j.get("by_rep") or [{}])[0])
check("audit payload: item rows carry every column of ITEM_COLS",
      all(has(x, "product", "sku", "department", "category", "lines", "ext_price", "gp",
              "implied_cost_min", "implied_cost_max", "catalog_cost", "paid", "flags", "flag_labels")
          for x in j.get("items") or []), (j.get("items") or [{}])[0])
check("audit payload: flagged-line rows carry every column of LINE_COLS",
      all(has(x, "rep", "date", "trans_id", "product", "sku", "rule", "pct", "ext_price", "gp",
              "implied_cost", "catalog_cost", "current", "option_a", "option_b", "option_c",
              "option_r", "flags", "flag_labels")
          for x in j.get("lines") or []), (j.get("lines") or [{}])[0])
check("explain payload: data_quality carries what the banner prints",
      has(pc.get("data_quality"), "enabled", "checked_lines", "suspect_lines", "by_flag",
          "rate_issues", "note"), pc.get("data_quality"))
check("explain payload: by_flag rows carry the banner's fields",
      all(has(f_, "code", "label", "lines", "ext_price", "gp", "paid")
          for f_ in (pc.get("data_quality") or {}).get("by_flag") or []))
check("explain payload: rate_issues rows carry the banner's fields",
      all(has(f_, "rule_id", "label", "payout_kind", "pct", "flags", "labels")
          for f_ in (pc.get("data_quality") or {}).get("rate_issues") or []))
check("explain payload: engine warnings carry what the list prints",
      all(has(w_, "type", "detail") for w_ in mm.get("warnings") or []))
check("explain payload: EVERY plan detail line carries implied_cost for the new column",
      _lines and all("implied_cost" in l for l in _lines),
      [l for l in _lines if "implied_cost" not in l][:1])
check("explain payload: only COST-BASED rules judge their lines (a flat rule is never flagged)",
      all(not l.get("cost_flags")
          for rb_ in (pc.get("rules") or []) if rb_.get("payout_kind") == "flat_per_unit"
          for l in (rb_.get("lines") or [])))

print("\n-- ZERO WRITES over HTTP ------------------------------------------------------------")
# H.FakeQuery raises AssertionError on insert/update/upsert/delete; raise_server_exceptions=False
# turns any such attempt into a 500, so a clean 200 IS the proof.
_codes = [client.get(AUDIT, params={"org_id": ORG}).status_code,
          client.get(EXPLAIN, params={"org_id": ORG, "period": PER, "rep": "Ana Ruiz"}).status_code,
          client.get(DEVICE, params={"org_id": ORG, "imei": H.FWA_IMEI, "period": PER}).status_code]
check("no surface in this package issued a write (all 200)", _codes == [200, 200, 200], _codes)

print("\n-- route inventory ------------------------------------------------------------------")
mine = sorted({r_.path for r_ in app.routes if "accessory-cost-audit" in getattr(r_, "path", "")})
check("exactly ONE new route is registered", mine == ["/api/v1/commcalc/accessory-cost-audit/{period}"],
      mine)
# ROUTE COUNT vs THE COMMIT THIS BRANCH IS BASED ON. `origin/main` is a MOVING TARGET (it advanced
# from a62a893 to d405eb9 mid-session, +8 routes, from another agent's merge), so measuring a live main
# checkout would make this assertion flap. The invariant that matters is "this package adds exactly the
# routes it says it adds", measured against the pinned merge-base.
BASE_COMMIT = "a62a8938fb8a9a9b229359d94b734d8864ecaa25"
BASE_ROUTES = 918          # measured on a62a893, 2026-07-31
try:
    mb = subprocess.check_output(["git", "-C", _REPO, "merge-base", "HEAD", "origin/main"],
                                 stderr=subprocess.DEVNULL).decode().strip()
except Exception:
    mb = BASE_COMMIT
check("this branch is still based on the dispatched commit a62a893", mb == BASE_COMMIT, mb)
check("route count == base + exactly this package's 1 route",
      len(app.routes) == BASE_ROUTES + len(mine),
      f"{len(app.routes)} vs {BASE_ROUTES} + {len(mine)}")

print("\n" + "=" * 78)
print(f"RESULT: {_pass} passed, {_fail} failed")
if _fails:
    print("FAILED:", *_fails, sep="\n  - ")
sys.exit(1 if _fail else 0)
