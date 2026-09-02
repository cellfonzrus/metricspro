"""Proof harness — the deterministic projection engine (roadmap Phase 4). Stdlib only.

Proves app/modules/account/projection_engine.py (pure) on FIXED fixtures:

  A. config resolution — house defaults; per-field validation (garbage degrades that field only);
     idempotent on an already-resolved config.
  B. linear method — an exact arithmetic series projects its own next terms (the fit reproduces
     the generating line); a flat series projects flat; window respected.
  C. overrides win over the fit — growth_rate_override compounds revenue from the last actual;
     expense_inflation compounds COGS+OPEX; both echoed in assumptions.
  D. seasonal_naive — same-month-last-year × recent year-over-year level; short history falls
     back to linear WITH the fallback noted; 'auto' picks by history length.
  E. derivation identities — every projected month satisfies GP = revenue − COGS and
     NI = GP − OPEX − other to the cent (never independently trended).
  F. floors — a magnitude line trending below zero clamps at 0 and says so in assumptions.
  G. cash runway — burn ⇒ cash ÷ avg projected burn; profitable trend ⇒ months: null + reason.
  H. determinism + display-only — same input twice is byte-identical; every row projected: true.

Run:  cd backend && python3 harness_projection_engine.py
"""
import sys

sys.path.insert(0, "app")

from app.modules.account import projection_engine as PE  # noqa: E402

FAIL = 0


def ok(name, cond, detail=None):
    global FAIL
    mark = "PASS" if cond else "FAIL"
    if not cond:
        FAIL += 1
    print(f"  [{mark}] {name}" + ("" if cond else f"  << {detail}"))


def hist(rows):
    """[(period, revenue, cogs, opex, other, cash)] → analysis-monthly-shaped dicts."""
    out = []
    for p, rev, cogs, opex, other, cash in rows:
        gp = round(rev - cogs, 2)
        out.append({"period": p, "revenue": rev, "cogs": cogs, "opex": opex, "other": other,
                    "gross_profit": gp, "net_income": round(gp - opex - other, 2), "cash": cash})
    return out


MONTHS_2026 = ["January 2026", "February 2026", "March 2026", "April 2026", "May 2026",
               "June 2026", "July 2026", "August 2026"]

print("A. config resolution")
d = PE.resolve_projection_config(None)
ok("house defaults", d == PE.DEFAULTS and d["method"] == "auto" and d["trailing_months"] == 6
   and d["horizon_months"] == 3 and d["growth_rate_override"] is None)
g = PE.resolve_projection_config({"method": "bogus", "trailing_months": 999, "horizon_months": 4,
                                  "growth_rate_override": "x", "expense_inflation": 0.02})
ok("per-field degradation: bad fields default, good fields stick",
   g["method"] == "auto" and g["trailing_months"] == 6 and g["horizon_months"] == 4
   and g["growth_rate_override"] is None and g["expense_inflation"] == 0.02, g)
ok("idempotent on resolved", PE.resolve_projection_config(g) == g)

print("B. linear")
# revenue 100k + 10k/mo, cogs 40k flat, opex 30k + 1k/mo, other 0 — exact lines
rows = [(MONTHS_2026[i], 100000.0 + 10000.0 * i, 40000.0, 30000.0 + 1000.0 * i, 0.0, 50000.0)
        for i in range(6)]
out = PE.project(hist(rows), {"method": "linear", "horizon_months": 3})
ok("computed, 3 projected months with successor labels",
   out["computed"] and [s["period"] for s in out["series"]]
   == ["July 2026", "August 2026", "September 2026"], out.get("series"))
ok("exact arithmetic series reproduces its own line",
   [s["revenue"] for s in out["series"]] == [160000.0, 170000.0, 180000.0]
   and [s["opex"] for s in out["series"]] == [36000.0, 37000.0, 38000.0],
   [(s["revenue"], s["opex"]) for s in out["series"]])
ok("flat series projects flat", [s["cogs"] for s in out["series"]] == [40000.0] * 3)
# window: only the last 2 months of a kinked series drive the fit
kink = [(MONTHS_2026[i], 100000.0, 0.0, 0.0, 0.0, 0.0) for i in range(4)] + \
       [(MONTHS_2026[4], 200000.0, 0.0, 0.0, 0.0, 0.0), (MONTHS_2026[5], 210000.0, 0.0, 0.0, 0.0, 0.0)]
outw = PE.project(hist(kink), {"method": "linear", "trailing_months": 2, "horizon_months": 1})
ok("trailing window respected (fit over last 2 only)", outw["series"][0]["revenue"] == 220000.0,
   outw["series"][0])

print("C. overrides")
o = PE.project(hist(rows), {"method": "linear", "horizon_months": 2,
                            "growth_rate_override": 0.10, "expense_inflation": 0.02})
ok("growth override compounds revenue from last actual (150k → 165k → 181.5k)",
   [s["revenue"] for s in o["series"]] == [165000.0, 181500.0], [s["revenue"] for s in o["series"]])
ok("expense inflation compounds COGS from last actual (40k → 40.8k → 41.62k)",
   [s["cogs"] for s in o["series"]] == [40800.0, 41616.0], [s["cogs"] for s in o["series"]])
ok("expense inflation compounds OPEX from last actual (35k → 35.7k → 36.41k)",
   [s["opex"] for s in o["series"]] == [35700.0, 36414.0], [s["opex"] for s in o["series"]])
ok("both overrides echoed in assumptions",
   any("OVERRIDE" in a for a in o["assumptions"]) and any("inflation" in a for a in o["assumptions"]))

print("D. seasonal_naive")
# 16 months: seasonal revenue = month index pattern repeating yearly, recent level 2× year-ago
labels = []
for y in (2025, 2026):
    for m in range(1, 13):
        labels.append(f"{PE._period._MONTHS[m]} {y}")
labels = labels[:16]   # Jan 2025 .. Apr 2026
seas = []
for i, lbl in enumerate(labels):
    base = 10000.0 + 1000.0 * (i % 12)
    lvl = 2.0 if i >= 12 else 1.0                      # 2026 runs at exactly 2× 2025
    seas.append((lbl, base * lvl, 0.0, 0.0, 0.0, 0.0))
outs = PE.project(hist(seas), {"method": "seasonal_naive", "horizon_months": 2})
# recent 3 (Feb–Apr 2026) = 2×(11k,12k,13k); year-ago 3 (Feb–Apr 2025) = (11k,12k,13k) → level 2.0
ok("seasonal: same-month-last-year × recent level (May/Jun 2025 × 2)",
   [s["revenue"] for s in outs["series"]] == [28000.0, 30000.0],
   [s["revenue"] for s in outs["series"]])
short = PE.project(hist(rows), {"method": "seasonal_naive", "horizon_months": 1})
ok("short history falls back to linear WITH a note", short["method"] == "linear"
   and any("fell back to linear" in a for a in short["assumptions"]))
ok("'auto' = linear on short history, seasonal on long",
   PE.project(hist(rows), {"method": "auto"})["method"] == "linear"
   and PE.project(hist(seas), {"method": "auto"})["method"] == "seasonal_naive")

print("E. derivation identities")
for s in o["series"] + outs["series"] + out["series"]:
    if abs(s["gross_profit"] - round(s["revenue"] - s["cogs"], 2)) > 0.01 or \
       abs(s["net_income"] - round(s["gross_profit"] - s["opex"] - s["other"], 2)) > 0.01:
        ok("GP/NI derived to the cent", False, s)
        break
else:
    ok("GP/NI derived to the cent in every projected month", True)

print("F. floors")
dec = [(MONTHS_2026[i], 50000.0 - 20000.0 * i, 1000.0, 1000.0, 0.0, 10000.0) for i in range(4)]
outf = PE.project(hist(dec), {"method": "linear", "horizon_months": 2})
ok("revenue trend crossing zero floors at 0",
   all(s["revenue"] >= 0.0 for s in outf["series"]) and outf["series"][-1]["revenue"] == 0.0,
   [s["revenue"] for s in outf["series"]])
ok("clamp reported in assumptions", any("Floored at $0" in a for a in outf["assumptions"]))

print("G. cash runway")
burn = [(MONTHS_2026[i], 10000.0, 0.0, 30000.0, 0.0, 100000.0) for i in range(4)]  # −20k/mo
outb = PE.project(hist(burn), {"method": "linear", "horizon_months": 3})
ok("burn ⇒ runway = cash ÷ avg projected burn (100k ÷ 20k = 5.0)",
   outb["cash_runway"]["months"] == 5.0, outb["cash_runway"])
ok("profitable trend ⇒ months null + reason", out["cash_runway"]["months"] is None
   and "profitable" in out["cash_runway"]["reason"])

print("H. determinism + display-only")
ok("same input twice is byte-identical",
   PE.project(hist(rows), {"method": "linear"}) == PE.project(hist(rows), {"method": "linear"}))
ok("every projected row flagged projected:true",
   all(s.get("projected") is True for s in out["series"] + o["series"] + outs["series"]))
ok("empty history -> computed:false", PE.project([], None)["computed"] is False)

print()
if FAIL:
    print(f"{FAIL} CHECK(S) FAILED")
    sys.exit(1)
print("harness_projection_engine: ALL CHECKS PASSED")
