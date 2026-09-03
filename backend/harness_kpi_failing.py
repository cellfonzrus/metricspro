"""HARNESS — Failing-KPI report classification (commcalc/kpi_failing.py, owner directive
2026-09-03: "Create new report from the KPI for the failing KPI … a high level overview of
failing KPI with the option to drill down").

  A. evaluate — the classification TRUTH TABLE: below target fails, at/above passes, missing
     value = no_data (NEVER failing), missing target = metric skipped, payout_config target wins
     over carrier default, NaN/garbage treated as no data, gap arithmetic.
  B. store_values / store_rows — the STORE_KPI_COLUMNS mapping, canonical market resolution
     injected, failing-first ordering, per-call column override.
  C. rep_rows — rep_commissions.kpi_values drill-down; rows without kpi_values skipped; tier /
     kpis_met passed through untouched (the pay engine's own numbers).
  D. summarize — org rollup: stores/reps failing, failing cells, per-metric tallies worst-first.
  Z. ARMED negative control.

Run: python3 harness_kpi_failing.py     (stdlib-only, pure module — no stubs needed)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import kpi_failing as kf      # noqa: E402

PASS, FAIL = [], []


def check(label, got, want=True):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}: got {got!r}, want {want!r}")


# The defs shape = router._kpi_defs tuples (key, label, payout_config_col, target_default)
DEFS = [
    ("atu", "ATU", "kpi_atu_target", 55),
    ("protect", "Protect", "kpi_protect_target", 80),
    ("byod", "BYOD", "kpi_byod_target", 35),
    ("tmr3", "TMR3", "kpi_tmr3_target", 70),
    ("boostapp", "Carrier App", "kpi_boostapp_target", 65),
    ("custom_x", "Custom X", "kpi_custom_x_target", None),   # no target anywhere → skipped
]
TARGETS = {"atu": 60, "protect": 80, "byod": 35, "tmr3": 70, "boostapp": 65}

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A. evaluate — classification truth table
# ══════════════════════════════════════════════════════════════════════════════════════════════════
ev, nd = kf.evaluate({"atu": 59.9, "protect": 80, "byod": 40, "tmr3": None, "boostapp": "junk"},
                     DEFS, TARGETS)
by = {e["kpi"]: e for e in ev}
check("A1 below target fails", by["atu"]["met"], False)
check("A2 gap is target − actual", by["atu"]["gap"], 0.1)
check("A3 exactly at target passes", by["protect"]["met"], True)
check("A4 above target passes", by["byod"]["met"], True)
check("A5 None value = no_data, not failing", [d["kpi"] for d in nd], ["tmr3", "boostapp"])
check("A6 garbage value = no_data", any(d["kpi"] == "boostapp" for d in nd), True)
check("A7 metric with no target skipped entirely",
      all(e["kpi"] != "custom_x" for e in ev) and all(d["kpi"] != "custom_x" for d in nd))
# payout_config target wins over the carrier default
ev2, _ = kf.evaluate({"atu": 57}, DEFS, TARGETS)          # target 60 (config) → fails
check("A8 config target wins over default", ev2[0]["met"], False)
ev3, _ = kf.evaluate({"atu": 57}, DEFS, {})               # falls back to default 55 → passes
check("A9 default target used when config silent", ev3[0]["met"], True)
check("A10 empty everything", kf.evaluate({}, [], {}), ([], []))
check("A11 '' value is no_data", kf.evaluate({"atu": ""}, DEFS, TARGETS)[1][0]["kpi"], "atu")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# B. store_values / store_rows
# ══════════════════════════════════════════════════════════════════════════════════════════════════
DLAR = [
    {"store_code": "S01", "location": "Main St", "address": "1 Main St",
     "atu": 50, "protect_pct": 90, "byod_pct": 30, "family_plan_pct": 45, "tmr3": 71,
     "aal_conversion": 4},
    {"store_code": "S02", "location": "Oak Ave", "address": "2 Oak Ave",
     "atu": 65, "protect_pct": 85, "byod_pct": 40, "family_plan_pct": 50, "tmr3": 75,
     "aal_conversion": 6},
    {"store_code": None, "location": "Ghost Rd", "address": "",
     "atu": None, "protect_pct": None, "byod_pct": None, "family_plan_pct": None,
     "tmr3": None, "aal_conversion": None},
]
vals = kf.store_values(DLAR[0])
check("B1 column map protect", vals["protect"], 90)
check("B2 column map familyplan", vals["familyplan"], 45)
check("B3 boostapp has no store column", "boostapp" in vals, False)

resolve = lambda s: {"MAIN ST": "NYC", "1 MAIN ST": "NYC", "OAK AVE": "LI"}.get(str(s).upper(), "")
srows = kf.store_rows(DLAR, DEFS, TARGETS, resolve_market=lambda s: resolve(s))
check("B4 failing store sorts first", srows[0]["store_code"], "S01")
check("B5 S01 failing metrics", [e["kpi"] for e in srows[0]["failing"]], ["atu", "byod"])
check("B6 market resolved canonically", srows[0]["market"], "NYC")
check("B7 S02 nothing failing", srows[1]["failing_count"], 0)
ghost = [r for r in srows if r["location"] == "Ghost Rd"][0]
check("B8 all-blank store: zero failing, all no_data",
      (ghost["failing_count"], ghost["evaluated_count"]), (0, 0))
check("B9 no_data lists targets", ghost["no_data"][0]["target"] > 0, True)
# per-call column override (tenant-custom mapping threads through without touching the module)
srows2 = kf.store_rows([{"store_code": "S09", "location": "X", "the_atu": 10}],
                       [("atu", "ATU", "c", 55)], {"atu": 55}, columns={"atu": "the_atu"})
check("B10 column override honored", srows2[0]["failing"][0]["kpi"], "atu")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# C. rep_rows
# ══════════════════════════════════════════════════════════════════════════════════════════════════
COMMS = [
    {"storeops_name": "Ana", "store": "1 Main St", "tier": 0.75, "kpis_met": 5, "total_kpis": 7,
     "kpi_values": {"atu": 40, "protect": 95, "byod": 36, "tmr3": 60, "boostapp": 70}},
    {"storeops_name": "Bo", "store": "2 Oak Ave", "tier": 1.0, "kpis_met": 7, "total_kpis": 7,
     "kpi_values": {"atu": 70, "protect": 95, "byod": 40, "tmr3": 80, "boostapp": 70}},
    {"epay_salesperson": "ghost", "store": "2 Oak Ave", "kpi_values": {}},     # no values → skipped
]
rrows = kf.rep_rows(COMMS, DEFS, TARGETS)
check("C1 only rows with kpi_values", [r["rep"] for r in rrows], ["Ana", "Bo"])
check("C2 Ana failing", [e["kpi"] for e in rrows[0]["failing"]], ["atu", "tmr3"])
check("C3 tier passthrough untouched", rrows[0]["tier"], 0.75)
check("C4 kpis_met passthrough", rrows[0]["kpis_met"], 5)
check("C5 Bo clean", rrows[1]["failing_count"], 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# D. summarize
# ══════════════════════════════════════════════════════════════════════════════════════════════════
summ = kf.summarize(srows, rrows)
check("D2 stores failing", (summ["stores_total"], summ["stores_failing"]), (3, 1))
check("D3 reps failing", (summ["reps_total"], summ["reps_failing"]), (2, 1))
check("D4 failing cells", summ["failing_cells"], 2 + 2)               # S01 atu+byod, Ana atu+tmr3
atu = [m for m in summ["by_metric"] if m["kpi"] == "atu"][0]
check("D5 per-metric tallies", (atu["stores_failing"], atu["reps_failing"]), (1, 1))
check("D6 worst metric first", summ["by_metric"][0]["kpi"], "atu")
check("D7 empty", kf.summarize([], []),
      {"stores_total": 0, "stores_failing": 0, "reps_total": 0, "reps_failing": 0,
       "failing_cells": 0, "by_metric": []})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Z. ARMED negative control — a blank cell must never read as failing
# ══════════════════════════════════════════════════════════════════════════════════════════════════
before = len(FAIL)
check("Z1 armed control (no_data must NOT fail)", ghost["failing_count"], 6)
if len(FAIL) == before + 1 and "Z1" in FAIL[-1]:
    FAIL.pop()
    PASS.append("Z1 armed negative control fired")
else:
    FAIL.append("Z1 armed negative control DID NOT fire — harness cannot detect failures")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL: {f}")
sys.exit(1 if FAIL else 0)
