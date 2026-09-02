"""Proof harness — company valuation (roadmap Phase 5). Stdlib only.

Proves app/modules/account/valuation.py (pure) on FIXED fixtures:

  A. config resolution — house defaults with per-field validation and SOURCE tracking
     ('house default' vs 'org config'); malformed fields degrade individually.
  B. TTM basis — 12-month sums; fewer months ANNUALIZE (×12/n) and flag it; EBITDA = NI + other;
     SDE = EBITDA + owner addbacks; net assets/cash from the latest month.
  C. multiple methods — low/mid/high = basis × [lo, mid, hi] exactly; a zero/negative basis is
     marked not-meaningful (never silently priced).
  D. DCF arithmetic — closed-form check: constant monthly FCF discounts to the annuity PV +
     discounted terminal, to the cent; harsh/friendly corners order low ≤ mid ≤ high; 3×3 grid.
  E. summary range — min/median/max across meaningful earnings methods; the ASSET FLOOR lifts the
     low end when higher (flagged); no meaningful earnings ⇒ range collapses to the floor.
  F. presentation guarantees — assumptions name the annualization and config sources; the
     disclaimer ('not an appraisal') is always present; deterministic (same input twice identical).

Run:  cd backend && python3 harness_valuation.py
"""
import sys

sys.path.insert(0, "app")

from app.modules.account import valuation as V  # noqa: E402

FAIL = 0


def ok(name, cond, detail=None):
    global FAIL
    mark = "PASS" if cond else "FAIL"
    if not cond:
        FAIL += 1
    print(f"  [{mark}] {name}" + ("" if cond else f"  << {detail}"))


def month(p, rev, ni, other=0.0, assets=0.0, liab=0.0, cash=0.0):
    return {"period": p, "revenue": rev, "net_income": ni, "other": other,
            "assets": assets, "liabilities": liab, "cash": cash}


MONTHS = [f"{m} 2026" for m in ("January", "February", "March", "April", "May", "June",
                                "July", "August", "September", "October", "November", "December")]

print("A. config resolution + source tracking")
cfg, src = V.resolve_valuation_config(None)
ok("house defaults, all sourced 'house default'", cfg == V.DEFAULTS
   and set(src.values()) == {"house default"})
cfg2, src2 = V.resolve_valuation_config({"sde_multiple_range": [2.0, 3.0],
                                         "owner_addbacks_annual": 60000,
                                         "discount_rate_range": [0.9, 0.1],   # lo > hi → invalid
                                         "terminal_multiple_range": "x"})
ok("valid overrides stick and are sourced 'org config'",
   cfg2["sde_multiple_range"] == [2.0, 3.0] and src2["sde_multiple_range"] == "org config"
   and cfg2["owner_addbacks_annual"] == 60000.0 and src2["owner_addbacks_annual"] == "org config")
ok("malformed fields degrade individually to defaults",
   cfg2["discount_rate_range"] == V.DEFAULTS["discount_rate_range"]
   and cfg2["terminal_multiple_range"] == V.DEFAULTS["terminal_multiple_range"]
   and src2["discount_rate_range"] == "house default")

print("B. TTM basis")
# 12 flat months: rev 100k, NI 20k, other 1k; latest BS assets 300k / liab 120k / cash 40k
full = [month(MONTHS[i], 100000.0, 20000.0, 1000.0, 300000.0, 120000.0, 40000.0)
        for i in range(12)]
b = V.ttm_metrics(full, owner_addbacks_annual=60000.0)
ok("12-month sums, not annualized", b["months_used"] == 12 and b["annualized"] is False
   and b["ttm_revenue"] == 1200000.0 and b["ttm_net_income"] == 240000.0)
ok("EBITDA = NI + other; SDE = EBITDA + addbacks",
   b["ebitda"] == 252000.0 and b["sde"] == 312000.0, (b["ebitda"], b["sde"]))
ok("net assets + cash from latest month", b["net_assets"] == 180000.0 and b["cash"] == 40000.0)
b6 = V.ttm_metrics(full[:6], owner_addbacks_annual=0.0)
ok("6 months ANNUALIZE ×2 and flag it", b6["annualized"] is True
   and b6["ttm_revenue"] == 1200000.0 and b6["ebitda"] == 252000.0, b6)

print("C. multiple methods")
out = V.valuation(full, *V.resolve_valuation_config({"owner_addbacks_annual": 60000}))
ms = {m["key"]: m for m in out["methods"]}
ok("revenue multiple = TTM rev × [0.3, 0.6]",
   (ms["revenue_multiple"]["low"], ms["revenue_multiple"]["high"]) == (360000.0, 720000.0))
ok("SDE multiple = 312k × [2.5, 4.0]",
   (ms["sde_multiple"]["low"], ms["sde_multiple"]["mid"], ms["sde_multiple"]["high"])
   == (780000.0, 1014000.0, 1248000.0), ms["sde_multiple"])
ok("EBITDA multiple = 252k × [3, 5]",
   (ms["ebitda_multiple"]["low"], ms["ebitda_multiple"]["high"]) == (756000.0, 1260000.0))
ok("asset floor = net assets on all three points",
   ms["asset_floor"]["low"] == ms["asset_floor"]["high"] == 180000.0)
loss = [month(MONTHS[i], 100000.0, -5000.0, 0.0, 300000.0, 120000.0, 40000.0) for i in range(12)]
lout = V.valuation(loss)
lms = {m["key"]: m for m in lout["methods"]}
ok("negative earnings basis marked not-meaningful, never silently priced",
   lms["sde_multiple"]["meaningful"] is False and "note" in lms["sde_multiple"])

print("D. DCF arithmetic")
# constant 10k/mo for 24 months at 12% annual, terminal 2.5× final year
r, tm, n, f = 0.12, 2.5, 24, 10000.0
d = (1 + r) ** (1 / 12) - 1
pv_annuity = sum(f / (1 + d) ** t for t in range(1, n + 1))
terminal = tm * (f * 12) / (1 + d) ** n
ok("closed-form: annuity PV + discounted terminal to the cent",
   V.dcf_value([f] * n, r, tm) == round(pv_annuity + terminal, 2),
   (V.dcf_value([f] * n, r, tm), round(pv_annuity + terminal, 2)))
ok("empty FCFs → 0.0", V.dcf_value([], r, tm) == 0.0)
proj = [10000.0] * 36
outd = V.valuation(full, *V.resolve_valuation_config(None), projected_fcfs=proj,
                   projection_meta={"method": "linear"})
dcf = {m["key"]: m for m in outd["methods"]}["dcf"]
ok("DCF corners ordered low ≤ mid ≤ high (harsh rate+low mult … friendly rate+high mult)",
   dcf["low"] <= dcf["mid"] <= dcf["high"] and dcf["meaningful"], dcf)
ok("3×3 sensitivity grid", len(dcf["sensitivity"]) == 3
   and all(len(r_["values"]) == 3 for r_ in dcf["sensitivity"]))
ok("grid corners equal the reported low/high",
   dcf["sensitivity"][2]["values"][0]["value"] == dcf["low"]
   and dcf["sensitivity"][0]["values"][2]["value"] == dcf["high"])

print("E. summary range")
s = outd["summary"]
earn = [m for m in outd["methods"] if m["key"] in ("sde_multiple", "ebitda_multiple", "dcf")]
ok("low/high span the meaningful earnings methods (floor below low here)",
   s["low"] == min(m["low"] for m in earn) and s["high"] == max(m["high"] for m in earn)
   and s["asset_floor_applied"] is False, s)
# huge floor case: net assets above every earnings low
rich = [month(MONTHS[i], 100000.0, 2000.0, 0.0, 5000000.0, 100000.0, 40000.0) for i in range(12)]
rout = V.valuation(rich)
ok("asset floor lifts the low end when higher (flagged)",
   rout["summary"]["asset_floor_applied"] is True
   and rout["summary"]["low"] == 4900000.0, rout["summary"])
ok("no meaningful earnings ⇒ range collapses to the floor",
   lout["summary"]["low"] == lout["summary"]["high"] == 180000.0
   and lout["summary"]["asset_floor_applied"] is True, lout["summary"])

print("F. presentation guarantees")
short = V.valuation(full[:6])
ok("annualization named in assumptions", any("ANNUALIZED" in a for a in short["assumptions"]))
ok("disclaimer always present ('not an appraisal')",
   "not an appraisal" in out["disclaimer"] and "not an appraisal" in lout["disclaimer"])
ok("config + sources echoed in the payload", out["config"]["sde_multiple_range"] == [2.5, 4.0]
   and out["config_source"]["sde_multiple_range"] == "house default")
ok("deterministic: same input twice identical",
   V.valuation(full, *V.resolve_valuation_config(None), projected_fcfs=proj)
   == V.valuation(full, *V.resolve_valuation_config(None), projected_fcfs=proj))
ok("empty history -> computed:false", V.valuation([])["computed"] is False)

print()
if FAIL:
    print(f"{FAIL} CHECK(S) FAILED")
    sys.exit(1)
print("harness_valuation: ALL CHECKS PASSED")
