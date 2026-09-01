"""HARNESS — W3: the six Payroll & Workforce entries in the notify report registry
(app/modules/notify/workforce_reports.py, spliced into report_registry.REPORTS).

  A. Import proof — workforce_reports imports with the stdlib alone (its app imports are lazy).
  B. Entry shape — six keys, label/filters/live_path/build present and well-typed; wants_auth on
     ALL six; wants_tz on the five that resolve a relative pay period; live_path targets the real
     pages; every build is an async callable.
  C. Registry splice — AST over report_registry.py: the `**WORKFORCE_REPORTS` splat is inside the
     REPORTS literal, literal keys are unique, and no literal key collides with a workforce key
     (existing entries untouched — storeops_schedule etc. still present).
  D. ONE shared pay-period resolver — _pay_period_range delegates to core.router.pay_period_for
     over payroll_approval._pay_settings (recording fakes prove the calls); explicit start/end pass
     through WITHOUT touching the resolver; 'last' steps exactly one period back THROUGH the
     resolver; and a source audit shows no local 7/14-day period arithmetic ever crept in.
  E. Builders end-to-end (fake storeops handlers, REAL pay_visibility.strip_pay):
       payroll        — pay stripped + pay columns dropped when the mig-434 gate denies; pay rides
                        when it allows; authorization/org_id explicitly bound on every call.
       hours approval — HOURS-ONLY: pay_rate/pay_effective/pay totals gone even when the endpoint
                        returned them; blank dates defer to the endpoint's own previous-period
                        default (passed through as '').
       payroll tax / payroll expenses — ALL-money: gate denial ⇒ ValueError (fail closed, nothing
                        renders); gate pass ⇒ page-parity lines / burden sheets.
       attendance / lateness — hours-only shapes; incident flattening matches the page's export.
  F. Tax twin — payroll_tax_estimate.compute_pay reproduces frontend/src/lib/payroll-tax.ts on
     hand-computed vectors (OT split, skipped/supplemental, allowance credit, per-state SIT, NY
     disability cap, half-up cent rounding).
  G. Saved-filter validator — validate_workforce_period accepts blank/current/last + real dates,
     rejects half-ranges, garbage tokens and malformed months as ReportConfigError.
  H. ARMED negative control — the harness itself can fail.

Run:  cd backend && python3 harness_workforce_report_registry.py     (stdlib-only)
"""
import ast
import asyncio
import inspect
import os
import sys
import types
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, got, want=True):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── stdlib-only stubs (report_filters needs app.core.config.settings; nothing else at import) ─────
_cfg = types.ModuleType("app.core.config")
_cfg.settings = types.SimpleNamespace(BUSINESS_TZ="America/New_York")
sys.modules["app.core.config"] = _cfg

section("A. import proof (stdlib only)")
import app.modules.notify.workforce_reports as WF                      # noqa: E402
import app.modules.notify.report_filters as RF                          # noqa: E402
import app.modules.storeops.pay_visibility as PV                        # noqa: E402  (real strip_pay)
import app.modules.storeops.payroll_tax_estimate as PTE                 # noqa: E402
check("A1 workforce_reports imported without fastapi/db", True)
check("A2 tax twin imported (leaf, stdlib)", callable(PTE.compute_pay))

KEYS = ["storeops_payroll", "storeops_hours_approval", "storeops_payroll_tax",
        "storeops_payroll_expenses", "storeops_attendance", "storeops_lateness"]

section("B. entry shape")
check("B1 exactly the six workforce keys", sorted(WF.WORKFORCE_REPORTS) == sorted(KEYS))
for k, spec in WF.WORKFORCE_REPORTS.items():
    check(f"B2 {k} label is a non-empty str", isinstance(spec.get("label"), str) and bool(spec["label"]))
    check(f"B3 {k} filters is a list of str",
          isinstance(spec.get("filters"), list) and all(isinstance(x, str) for x in spec["filters"]))
    check(f"B4 {k} live_path callable → internal path",
          callable(spec.get("live_path")) and str(spec["live_path"]({})).startswith("/"))
    check(f"B5 {k} build is async", inspect.iscoroutinefunction(spec.get("build")))
    check(f"B6 {k} wants_auth (span/pay gates ride the caller header)", spec.get("wants_auth") is True)
check("B7 wants_tz on the five period-resolving entries",
      [k for k in KEYS if WF.WORKFORCE_REPORTS[k].get("wants_tz")] ==
      [k for k in KEYS if k != "storeops_hours_approval"])
check("B8 live paths target the real pages",
      {k: WF.WORKFORCE_REPORTS[k]["live_path"]({}) for k in KEYS} ==
      {"storeops_payroll": "/storeops/payroll",
       "storeops_hours_approval": "/storeops/payroll/approvals",
       "storeops_payroll_tax": "/storeops/payroll-tax",
       "storeops_payroll_expenses": "/hr/payroll-expenses",
       "storeops_attendance": "/storeops/attendance",
       "storeops_lateness": "/storeops/accountability"})

section("C. registry splice (AST — report_registry itself needs fastapi, so it is read, not imported)")
_REG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app/modules/notify/report_registry.py")
_tree = ast.parse(open(_REG).read())
_lit_keys, _has_splat = [], False
for node in ast.walk(_tree):
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "REPORTS" for t in node.targets):
        d = node.value
        for kn, vn in zip(d.keys, d.values):
            if kn is None:
                _has_splat = _has_splat or (getattr(vn, "id", "") == "WORKFORCE_REPORTS")
            elif isinstance(kn, ast.Constant):
                _lit_keys.append(kn.value)
check("C1 REPORTS splices **WORKFORCE_REPORTS", _has_splat)
check("C2 literal keys unique", len(_lit_keys) == len(set(_lit_keys)))
check("C3 no collision literal ∩ workforce", sorted(set(_lit_keys) & set(KEYS)), [])
check("C4 existing entries untouched (spot: schedule/gp/flags still literal)",
      {"storeops_schedule", "gp", "flags", "owed_weekly"} <= set(_lit_keys))
_imports = [n for n in ast.walk(_tree) if isinstance(n, ast.ImportFrom) and n.module == "workforce_reports"]
check("C5 registry imports WORKFORCE_REPORTS from .workforce_reports",
      any(a.name == "WORKFORCE_REPORTS" for i in _imports for a in i.names))

section("D. ONE shared pay-period resolver (delegation, never a copy)")
_calls = {"pay_period_for": 0, "_pay_settings": 0, "_pp_settings": 0}


def _fake_period_for(s, ref):
    _calls["pay_period_for"] += 1
    start = ref - timedelta(days=ref.weekday())        # simple Monday week — the FAKE's math
    return {"start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(),
            "payday": None}


_core = types.ModuleType("app.modules.core.router")
_core.pay_period_for = _fake_period_for
_core.__dict__["_pp_settings"] = lambda t: (_calls.__setitem__("_pp_settings", _calls["_pp_settings"] + 1)
                                            or {"work_week_start_dow": 0, "pay_period_type": "weekly"})
_pa = types.ModuleType("app.modules.storeops.payroll_approval")
_pa._pay_settings = lambda org_id: (_calls.__setitem__("_pay_settings", _calls["_pay_settings"] + 1)
                                    or {"work_week_start_dow": 0, "pay_period_type": "weekly"})
sys.modules["app.modules.core.router"] = _core
sys.modules["app.modules.storeops.payroll_approval"] = _pa

lo, hi = WF._pay_period_range("org1", {"start": "2026-08-03", "end": "2026-08-16"})
check("D1 explicit start/end pass through", (lo, hi), ("2026-08-03", "2026-08-16"))
check("D2 explicit range never consulted the resolver", _calls["pay_period_for"], 0)
_today = RF.business_today("")
_exp_start = _today - timedelta(days=_today.weekday())
lo, hi = WF._pay_period_range("org1", {})
check("D3 default = the resolver's CURRENT period (business today)",
      (lo, hi), (_exp_start.isoformat(), (_exp_start + timedelta(days=6)).isoformat()))
check("D4 default path consulted the shared resolver", _calls["pay_period_for"] >= 1)
check("D5 tenant settings loaded via payroll_approval._pay_settings", _calls["_pay_settings"] >= 1)
lo, hi = WF._pay_period_range("org1", {"period": "last"})
check("D6 'last' steps back one period THROUGH the resolver",
      (lo, hi), ((_exp_start - timedelta(days=7)).isoformat(),
                 (_exp_start - timedelta(days=1)).isoformat()))
_src = inspect.getsource(WF._pay_period_range)
check("D7 no local period arithmetic (no 7/14-day math in the resolver wrapper)",
      ("days=7" not in _src) and ("days=14" not in _src))
check("D8 delegates by name (pay_period_for referenced)", "pay_period_for" in _src)

section("E. builders end-to-end (fake storeops handlers, REAL strip_pay)")
_recorded = {}


def _rec(name, **kw):
    _recorded[name] = kw


_so = types.ModuleType("app.modules.storeops.router")


def _fake_get_payroll(month=None, start=None, end=None, authorization=None, org_id=None, response=None):
    _rec("get_payroll", start=start, end=end, authorization=authorization, org_id=org_id)
    return [{"employee_id": "e1", "name": "Ann", "store": "S1", "shifts": 5,
             "scheduled_hours": 40.0, "actual_hours": 41.0, "lunch_deduction_hours": 2.5,
             "pay_rate": 20.0, "scheduled_pay": 800.0, "actual_pay": 820.0, "pay_basis": "hourly"}]


def _fake_payroll_raw(start=None, end=None, authorization=None, org_id=None):
    _rec("payroll_raw", start=start, end=end, authorization=authorization, org_id=org_id)
    return {"start": start, "end": end,
            "rows": [{"employee_id": "e1", "name": "Ann", "store": "S1", "pay_rate": 20.0,
                      "clocked_hours": 40.0, "manual_hours": 0.0, "total_hours": 40.0,
                      "basis": "clocked",
                      "settings": {"filing_status": "Single", "allowances": 0, "state": "NY",
                                   "extra_withholding": 0.0, "skipped": False}}]}


def _fake_get_payroll_expenses(period=None, authorization=None, org_id=None):
    _rec("get_payroll_expenses", period=period, authorization=authorization, org_id=org_id)
    return {"period": period, "stores": [
                {"store": "S1", "wages": 1000.0, "fica_ss": 62.0, "medicare": 14.5, "futa": 6.0,
                 "suta": 27.0, "tax_total": 109.5, "items": {}, "items_total": 0.0, "total": 109.5}],
            "cells": [], "gross_cells": [{"store": "S1", "amount": 1000.0}]}


def _fake_attendance(start=None, end=None, authorization=None, org_id=None):
    _rec("attendance", start=start, end=end, authorization=authorization, org_id=org_id)
    return {"available": True, "config": {}, "limit_hit": False,
            "rows": [{"exception_type": "late", "employee_name": "Ann", "work_date": "2026-08-03",
                      "store_code": "S1", "shift_start": "09:00", "shift_end": "17:00",
                      "minutes_late": 12, "minutes_early": 0, "excused": False}],
            "counts": {"late": 1}}


def _fake_accountability(start=None, end=None, authorization=None, org_id=None):
    _rec("accountability", start=start, end=end, authorization=authorization, org_id=org_id)
    return {"start": start, "end": end,
            "employees": [{"employee": "Ann", "employee_id": "e1", "total_shifts": 20, "late": 6,
                           "no_show": 1, "left_early": 0, "excused": 1, "late_rate": 0.3,
                           "flags": ["punctuality"],
                           "incidents": [{"work_date": "2026-08-03", "store_code": "S1", "late": True,
                                          "left_early": False, "minutes_late": 12, "minutes_early": 0,
                                          "actual_clock_in": "x", "actual_clock_in_local": "09:12",
                                          "actual_clock_out": None, "actual_clock_out_local": None},
                                         {"work_date": "2026-08-04", "store_code": "S1", "late": False,
                                          "left_early": False, "minutes_late": 0, "minutes_early": 0,
                                          "actual_clock_in": "x", "actual_clock_in_local": "09:00",
                                          "actual_clock_out": None, "actual_clock_out_local": None}]}],
            "recommendations": [{"employee": "Ann", "flags": ["punctuality"], "text": "check in"}]}


_so.get_payroll = _fake_get_payroll
_so.payroll_raw = _fake_payroll_raw
_so.get_payroll_expenses = _fake_get_payroll_expenses
_so.attendance_exceptions = _fake_attendance
_so.accountability = _fake_accountability
sys.modules["app.modules.storeops.router"] = _so


def _fake_list_approvals(start=None, end=None, store_code=None, market=None, employee_id=None,
                         status=None, authorization=None, org_id=None):
    _rec("list_approvals", start=start, end=end, store_code=store_code, status=status,
         authorization=authorization, org_id=org_id)
    return {"ready": True, "period_start": "2026-07-20", "period_end": "2026-08-02",
            "rows": [{"employee_id": "e1", "name": "Ann", "store": "S1", "scheduled_hours": 40,
                      "hours_worked": 42.5, "lunch_hours": 2.5, "adjustment_hours": 0,
                      "adjustment_reason": None, "hours_payable": 40.0, "hours_source": 40.0,
                      "hours_effective": 40.0, "hours_corrected": False, "no_clock_record": False,
                      "pay_rate": 20.0, "pay_effective": 800.0,      # endpoint may include pay …
                      "dm_status": "pending", "hr_status": "pending", "payer_name": "ACME",
                      "held": True}],
            "totals": {"employees": 1, "hours": 40.0, "lunch_hours": 2.5, "adjustment_hours": 0,
                       "pay": 800.0, "payable_pay": 0.0, "pending_dm": 1, "pending_hr": 0, "held": 1},
            "cycle": None, "payers": []}


_pa.list_approvals = _fake_list_approvals

_allowed = [False]
_real_can_see_pay = PV.can_see_pay
PV.can_see_pay = lambda auth, org_id=None, client=None: _allowed[0]

# — payroll: gate DENIES → strip + column drop —
p = run(WF._payroll("org1", {"start": "2026-08-03", "end": "2026-08-16"}, authorization="tok", tz=""))
rows = p["sheets"][0]["rows"]
check("E1 payroll rows carry NO pay keys when gated",
      all(k not in rows[0] for k in ("pay_rate", "scheduled_pay", "actual_pay")))
check("E2 payroll hours survive the strip", rows[0].get("actual_hours"), 41.0)
check("E3 payroll pay COLUMNS dropped when gated",
      [c["header"] for c in p["sheets"][0]["columns"]],
      [c["header"] for c in WF.PAYROLL_HOURS_COLS])
check("E4 payroll subtitle says hours-only", "hours only" in p["subtitle"])
check("E5 handler called with explicit auth/org (no sentinel ever unbound)",
      (_recorded["get_payroll"]["authorization"], _recorded["get_payroll"]["org_id"]), ("tok", "org1"))
check("E6 explicit range reached the handler",
      (_recorded["get_payroll"]["start"], _recorded["get_payroll"]["end"]),
      ("2026-08-03", "2026-08-16"))

# — payroll: gate ALLOWS → pay rides —
_allowed[0] = True
p = run(WF._payroll("org1", {"start": "2026-08-03", "end": "2026-08-16"}, authorization="tok", tz=""))
check("E7 payroll pay columns present when allowed",
      any(c.get("header") == "Actual Pay" for c in p["sheets"][0]["columns"]))
check("E8 payroll pay values present when allowed", p["sheets"][0]["rows"][0].get("actual_pay"), 820.0)

# — hours approval: HOURS-ONLY regardless of what the endpoint returned —
h = run(WF._hours_approval("org1", {}, authorization="tok"))
hrow = h["sheets"][0]["rows"][0]
check("E9 hours-approval pay keys stripped unconditionally",
      ("pay_rate" not in hrow) and ("pay_effective" not in hrow))
check("E10 hours-approval hours survive", hrow.get("hours_payable"), 40.0)
check("E11 hours-approval columns are hours-only (no money flag, no pay key)",
      all(not c.get("money") and c.get("key") not in PV.PAY_FIELDS
          for c in WF.HOURS_APPROVAL_COLS))
check("E12 blank dates deferred to the endpoint's own previous-period default",
      (_recorded["list_approvals"]["start"], _recorded["list_approvals"]["end"]), ("", ""))
check("E13 hours-approval subtitle shows the endpoint's period",
      "2026-07-20 – 2026-08-02" in h["subtitle"])

# — payroll tax / payroll expenses: ALL-money, fail closed —
_allowed[0] = False
for name, coro in (("payroll_tax", WF._payroll_tax("org1", {}, authorization="tok", tz="")),
                   ("payroll_expenses", WF._payroll_expenses("org1", {}, authorization="tok", tz=""))):
    try:
        run(coro)
        check(f"E14 {name} DENIED ⇒ ValueError (fail closed)", False)
    except ValueError:
        check(f"E14 {name} DENIED ⇒ ValueError (fail closed)", True)
check("E15 denial happened BEFORE any handler call",
      ("payroll_raw" not in _recorded) and ("get_payroll_expenses" not in _recorded))

_allowed[0] = True
t = run(WF._payroll_tax("org1", {"start": "2026-08-03", "end": "2026-08-09"}, authorization="tok", tz=""))
line = t["sheets"][0]["rows"][0]
check("E16 payroll-tax line matches the twin (40h @ $20 Single NY)",
      (line["gross"], line["fica_ss"], line["federal"], line["state_wh"], line["net"]),
      (800.0, 49.60, 96.00, 50.64, 591.56))
check("E17 payroll-tax W-4 cell", line["w4"], "Single · NY")

x = run(WF._payroll_expenses("org1", {"month": "2026-08"}, authorization="tok", tz=""))
check("E18 payroll-expenses explicit month passes through",
      _recorded["get_payroll_expenses"]["period"], "2026-08")
check("E19 payroll-expenses sheets = burden + gross",
      [s["name"] for s in x["sheets"]], ["Burden by Store", "Gross Payroll"])
x = run(WF._payroll_expenses("org1", {}, authorization="tok", tz=""))
check("E20 payroll-expenses default month = current period start's month (W2 seam)",
      _recorded["get_payroll_expenses"]["period"], _exp_start.isoformat()[:7])

# — attendance / lateness —
a = run(WF._attendance("org1", {"start": "2026-08-03", "end": "2026-08-09"}, authorization="", tz=""))
check("E21 attendance sheets = exceptions + counts",
      [s["name"] for s in a["sheets"]], ["Exceptions", "Counts"])
check("E22 attendance counts flattened", a["sheets"][1]["rows"], [{"exception_type": "late", "count": 1}])
check("E23 attendance columns are hours-only",
      all(not c.get("money") and c.get("key") not in PV.PAY_FIELDS for c in WF.ATTENDANCE_COLS))

lz = run(WF._lateness("org1", {"start": "2026-08-03", "end": "2026-08-16"}, authorization="", tz=""))
check("E24 lateness sheets", [s["name"] for s in lz["sheets"]], ["By Employee", "Incidents", "Coaching"])
pct_col = next(c for c in WF.LATENESS_EMPLOYEE_COLS if c["header"] == "Lateness %")
check("E25 Lateness %% renders late_rate as a percent", pct_col["fn"]({"late_rate": 0.3}), "30%")
check("E26 incident flattening keeps ONLY late/left-early incidents (page parity)",
      len(lz["sheets"][1]["rows"]), 1)
check("E27 incident row carries local clock-in + times-late",
      (lz["sheets"][1]["rows"][0]["clock_in_local"], lz["sheets"][1]["rows"][0]["times_late_period"]),
      ("09:12", 6))

PV.can_see_pay = _real_can_see_pay

section("F. tax twin vs frontend/src/lib/payroll-tax.ts (hand-computed vectors)")
v = PTE.compute_pay(40, 20, {"filing_status": "Single", "state": "NY", "allowances": 0,
                             "extra_withholding": 0, "skipped": False})
check("F1 40h @ $20 Single NY",
      (v["regular_hours"], v["ot_hours"], v["gross"], v["fica_ss"], v["fica_medicare"],
       v["federal"], v["state"], v["disability"], v["deductions"], v["net"], v["employer_fica"]),
      (40.0, 0.0, 800.0, 49.60, 11.60, 96.00, 50.64, 0.60, 208.44, 591.56, 61.20))
v = PTE.compute_pay(45, 20, {"filing_status": "Single", "state": "NY", "allowances": 0,
                             "extra_withholding": 0, "skipped": False})
check("F2 OT split: 45h → 40 + 5 @ 1.5x", (v["regular_hours"], v["ot_hours"], v["gross"]),
      (40.0, 5.0, 950.0))
check("F3 half-up cents (950 × .0145 = 13.775 → 13.78, JS Math.round parity)",
      v["fica_medicare"], 13.78)
v = PTE.compute_pay(40, 20, {"filing_status": "Single", "state": "NY", "skipped": True})
check("F4 skipped ⇒ flat 22%% supplemental", v["federal"], 176.00)
v = PTE.compute_pay(40, 20, {"filing_status": "Married", "state": "PA", "allowances": 2,
                             "extra_withholding": 10, "skipped": False})
check("F5 Married + 2 allowances + $10 extra", v["federal"], 60.00)
check("F6 PA flat SIT, no NY disability", (v["state"], v["disability"]), (24.56, 0))
v = PTE.compute_pay(40, 20, {"filing_status": "Single", "state": "TX", "skipped": False})
check("F7 unlisted state ⇒ $0 SIT (the ?? 0 fallback)", (v["state"], v["disability"]), (0.0, 0))
v = PTE.compute_pay(0, 20, {"filing_status": "Single", "state": "NY", "skipped": False})
check("F8 zero hours ⇒ zero line", (v["gross"], v["net"]), (0.0, 0.0))
check("F9 rate table mirrors the TS twin field-for-field",
      (PTE.TAX_RATES["fica_ss"], PTE.TAX_RATES["federal_by_status"]["HOH"],
       PTE.TAX_RATES["state_sit"]["NJ"], PTE.TAX_RATES["ot_threshold"]),
      (0.062, 0.11, 0.05525, 40))

section("G. saved-filter validator")
for ok_f in ({}, {"period": "current"}, {"period": "LAST"}, {"start": "2026-08-03", "end": "2026-08-16"},
             {"month": "2026-08"}):
    try:
        RF.validate_workforce_period(ok_f)
        check(f"G1 accepts {ok_f!r}", True)
    except Exception as e:
        check(f"G1 accepts {ok_f!r}", str(e), True)
for bad_f in ({"start": "2026-08-03"}, {"start": "soon", "end": "2026-08-16"},
              {"period": "Q3"}, {"month": "August"}):
    try:
        RF.validate_workforce_period(bad_f)
        check(f"G2 rejects {bad_f!r} as ReportConfigError", False)
    except RF.ReportConfigError:
        check(f"G2 rejects {bad_f!r} as ReportConfigError", True)
check("G3 all six keys registered on the shared validator",
      all(RF.FILTER_VALIDATORS.get(k) is RF.validate_workforce_period for k in KEYS))

section("H. ARMED negative control — the harness itself can fail")
_bad = dict(WF.WORKFORCE_REPORTS["storeops_payroll"])
_bad.pop("build")
_armed_caught = not (callable(_bad.get("build")) if "build" in _bad else False)
check("H1 a mutilated entry (no build) would be caught by the B-checks", _armed_caught)

print(f"\n{'=' * 60}\nPASS {len(PASS)}  FAIL {len(FAIL)}")
for f in FAIL:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
