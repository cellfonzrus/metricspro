"""DB-FREE PROOF + BUILD LOCK — a zero-row pull is published as the source's own answer ONLY when
the sweep can show it asked an answerable question and nothing it depended on was broken.

THE LIVE DEFECT THIS PINS (house org, index §19.41). `commcalc.raw_comp_report` held rows for six
periods and nothing after 2026-08-06; September and October were empty; the `carrier_comm` P&L line
was understated for August and absent for September. The automation was enabled, on a cron, and
reporting success the whole time. From `core.job_run`, `sweep:<connector>`, verbatim:

    2026-10-03T03:31:01Z   status = SUCCEEDED
      reports: [{'report': 'comp_report', 'rows': 0, 'mode': 'no_data',
                 'window': '2026-10-03..2026-10-03',
                 'note': 'no compensation posted for 2026-10-03..2026-10-03 — not an error'}]
      errors:  ["... could not set the report's daily date filter — 'Summarize by' could not be set
                to Daily (hidden field = None); the report returns an empty workbook without it"]

ONE run, ONE browser session, TWO legs driving the SAME portal control. The second leg proved the
control unsettable. The first leg's zero was published as a fact about the carrier anyway, because
its registry entry carried `empty_ok: True` — a declaration, not evidence.

TWO independent faults, both fixed as properties of the design:

  (1) THE FLAG DECIDED. `empty_ok` cannot tell "the source posted nothing" from "we asked an
      unanswerable question". The decision now lives in `empty_pull_verdict.classify_empty_pull`,
      ONE pure home that every sweep dereferences, and it is settled at the END of a run so a leg is
      judged against its siblings' evidence (on 2026-10-03 the leg that proved the control broken
      ran SECOND — a per-leg check would have cleared the comp zero).
  (2) THE WINDOW COULD NOT CONTAIN DATA. `report_definitions.refresh_days` was 1 for an in-arrears
      source, so every nightly run asked for TODAY and every zero looked legitimate, forever. The
      day-grain window is now max(refresh_days, arrears_days) with `arrears_days` per-org config
      (mig 1042).

THREE SWEEPS HELD THREE ANSWERS to this one question before this change — the duplicate defect the
index rules forbid. §F pins that they now all read one home and that no second copy may reappear.

Run:  cd backend && python3 harness_empty_pull_verdict.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.commcalc import empty_pull_verdict as V        # noqa: E402
from app.modules.commcalc import epay_sweep as ES               # noqa: E402
from app.modules.commcalc import feed_period as FP              # noqa: E402  (§19.53 — the one boundary home)

PASS = FAIL = 0
FAILURES = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL  {label}" + (f"\n          {detail}" if detail else ""))


def src(mod):
    return open(mod.__file__).read()


# ── §A — THE 2026-10-03 RUN, REPLAYED BOTH WAYS ROUND ─────────────────────────────────────────────
print("\n§A  the live run replayed — the comp zero must never be published as the carrier's answer")

COMP = {"empty_allowed": True, "label": "Comprehensive Comp",
        "window": ["2026-10-03", "2026-10-03"], "arrears_days": 7,
        "ever_landed": True, "days_since_last_row": 58}
CONTROL = ES.DAILY_RANGE_CONTROL

for order in ("comp first (the live order)", "comp second"):
    led = V.ControlLedger()
    comp_res = {"report": "comp_report", "label": "Comprehensive Comp", "rows": 0, "mode": "no_data"}
    if order.startswith("comp first"):
        led.defer(comp_res, controls=(CONTROL,), **COMP)
        led.control_failed(CONTROL, "hidden field = None")
    else:
        led.control_failed(CONTROL, "hidden field = None")
        led.defer(comp_res, controls=(CONTROL,), **COMP)
    settled = led.settle()
    v = settled[0][1]
    check(f"{order}: verdict is SUSPECT, not a clean no_data", v["verdict"] == V.SUSPECT, v)
    check(f"{order}: the run may not report it as trusted", v["trusted"] is False)
    check(f"{order}: the result's mode is rewritten away from 'no_data'",
          comp_res["mode"] == "unverified_no_data", comp_res)
    check(f"{order}: the sentence names the control that was proven broken",
          CONTROL in v["sentence"], v["sentence"])

# The window alone is enough, with no broken control anywhere in the run — this is what was wrong on
# every night the portal was healthy.
v = V.classify_empty_pull(**COMP)
check("A5 a 1-day window on a 7-day-arrears source is UNVERIFIED on its own",
      v["verdict"] == V.UNVERIFIED and v["reason"] == "window_narrower_than_arrears", v)
check("A6 and it says WHY, in the operator's words", "cannot contain data" in v["sentence"])

# CONTROL (mutation proof): the same zero, asked over a window that CAN hold data, on a working path.
ok = V.classify_empty_pull(**{**COMP, "window": ["2026-09-27", "2026-10-03"],
                              "days_since_last_row": 1})
check("A7 control: a 7-day window on a landing path IS the source's answer",
      ok["verdict"] == V.CONFIRMED and ok["trusted"] is True, ok)


# ── §B — EVERY RULE, AND THE ORDER THEY RESOLVE IN ────────────────────────────────────────────────
print("\n§B  the rules")

base = {"empty_allowed": True, "label": "r", "window": ["2026-10-01", "2026-10-07"],
        "arrears_days": 7}
check("B1 a report that may never be empty is SUSPECT",
      V.classify_empty_pull(**{**base, "empty_allowed": False})["reason"] == "empty_never_legitimate")
check("B2 a broken control outranks the report being allowed to be empty",
      V.classify_empty_pull(**{**base, "broken_controls": ["x"]})["reason"] == "control_proven_broken")
check("B3 a broken control outranks even the source's own 'no records'",
      V.classify_empty_pull(**{**base, "broken_controls": ["x"],
                               "corroboration": V.SOURCE_REPORTED_EMPTY})["verdict"] == V.SUSPECT)
check("B4 the source's own 'no records' IS trusted",
      V.classify_empty_pull(**{**base, "corroboration": V.SOURCE_REPORTED_EMPTY})["trusted"] is True)
check("B5 and it outranks a too-narrow window (the source answered the question we asked)",
      V.classify_empty_pull(**{**base, "window": ["2026-10-07", "2026-10-07"],
                               "corroboration": V.SOURCE_REPORTED_EMPTY})["trusted"] is True)
check("B6 a path that has NEVER landed a row cannot vouch for a zero",
      V.classify_empty_pull(**{**base, "ever_landed": False})["reason"] == "never_landed")
check("B7 a silence longer than the configured limit is UNVERIFIED",
      V.classify_empty_pull(**{**base, "ever_landed": True, "days_since_last_row": 58,
                               "stale_after_days": 7})["reason"] == "silent_longer_than_allowed")
check("B8 a silence inside the limit is the source being quiet",
      V.classify_empty_pull(**{**base, "ever_landed": True, "days_since_last_row": 3,
                               "stale_after_days": 7})["trusted"] is True)
check("B9 UNKNOWN evidence is never read as good news — an absent fact triggers no rule, and an "
      "absent window cannot be called wide enough",
      V.classify_empty_pull(empty_allowed=True, label="r")["trusted"] is True
      and V.classify_empty_pull(empty_allowed=True, label="r",
                                ever_landed=False)["trusted"] is False)
check("B10 only CONFIRMED is trusted", V.TRUSTED_VERDICTS == (V.CONFIRMED,))
check("B11 every verdict carries the evidence it was decided on",
      set(V.classify_empty_pull(**base)["evidence"]) >= {
          "empty_allowed", "corroboration", "broken_controls", "window", "window_span_days",
          "required_window_days", "ever_landed", "days_since_last_row", "stale_after_days"})

check("B12 window_days is inclusive and refuses a reversed or junk window",
      V.window_days("2026-10-01", "2026-10-07") == 7
      and V.window_days("2026-10-01", "2026-10-01") == 1
      and V.window_days("2026-10-07", "2026-10-01") is None
      and V.window_days("", None) is None)
check("B13 required_window_days falls back to the house default for junk config",
      V.required_window_days(None) == V.DEFAULT_ARREARS_DAYS
      and V.required_window_days("x") == V.DEFAULT_ARREARS_DAYS
      and V.required_window_days(0) == 1 and V.required_window_days(7) == 7)


# ── §C — THE WINDOW IS NEVER NARROWER THAN THE ARREARS ────────────────────────────────────────────
print("\n§C  the day-grain window (the root cause of the two missing months)")

# THE SPAN IS MEASURED ON THE INTENT, NOT ON THE BOUNDARY ASKED FOR (§19.53, 2026-10-08). The job now
# carries both: `covers_through` is the last day it means to receive, and `end` is what goes in the
# source's End Date widget — one day later under an end-exclusive source, because the carrier statement
# was short the final day of every month ($111,949.22) for exactly that reason. The arrears FLOOR is a
# property of the days we intend to cover, so these checks read `covers_through`; C6 below pins the
# boundary itself so neither half can drift.
jobs = ES._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 1}})
_k, t = jobs[0]
check("C1 with no arrears configured the house default still applies",
      V.window_days(t["begin"], t["covers_through"]) >= V.required_window_days(None), t)

jobs = ES._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 1, "arrears_days": 7}})
_k, t = jobs[0]
span = V.window_days(t["begin"], t["covers_through"])
check("C2 THE LIVE CONFIG (refresh_days=1) can no longer produce a 1-day window", span == 7,
      f"span={span} window={t['begin']}..{t['covers_through']}")
check("C3 the window ends today and reaches back, so it can contain in-arrears data",
      len(t["days"]) == 7 and t["covers_through"] == t["days"][-1] and t["begin"] == t["days"][0])
check("C6 and the END ASKED FOR cannot lose that last day (§19.53): under the house end-exclusive "
      "boundary the request runs one day past the intent, and says so",
      t["end"] == FP._next_day(t["covers_through"]) and t["end_widened"] is True
      and t["end_boundary"] == FP.END_EXCLUSIVE)

jobs = ES._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 14, "arrears_days": 7}})
_k, t = jobs[0]
check("C4 a wider refresh_days still wins — arrears is a FLOOR, not an override",
      V.window_days(t["begin"], t["covers_through"]) == 14)

check("C5 a zero the widened window produces is now classifiable as a real answer",
      V.classify_empty_pull(empty_allowed=True, label="r", arrears_days=7,
                            window=[t["days"][0], t["days"][-1]],
                            ever_landed=True, days_since_last_row=0)["trusted"] is True)


# ── §D — THE CONFIG ACTUALLY REACHES THE SWEEP (the §19.18 unwired-registry failure) ──────────────
print("\n§D  a setting nothing reads is the defect this house has shipped four times")

R = open(os.path.join(HERE, "app", "modules", "commcalc", "router.py")).read()
check("D1 arrears_days is in the columns the sweep reads from report_definitions",
      "arrears_days" in R.split("_REGISTRY_SWEEP_COLS")[1].split(")")[0], "mig 1042 column unwired")
check("D2 _expand_jobs dereferences arrears_days", 'rc.get("arrears_days")' in src(ES))
check("D3 the stale-after threshold is read from config too",
      'empty_stale_after_days' in src(ES) and 'empty_stale_after_days' in R)

MIG = os.path.join(HERE, os.pardir, "database", "migrations", "1042_report_arrears_window.sql")
M = open(MIG).read()
check("D4 the migration declares both columns and is idempotent",
      "ADD COLUMN IF NOT EXISTS arrears_days" in M
      and "ADD COLUMN IF NOT EXISTS empty_stale_after_days" in M)
check("D5 the migration carries a REVERT note", "-- REVERT:" in M)
check("D6 the migration names no carrier, tenant or portal (RULE TWO)",
      not any(w in M.lower() for w in ("boost", "epay", "vidapay", "luxelink", "novawave")))
check("D7 the verdict module names no carrier, tenant or portal (RULE TWO)",
      not any(w in src(V).lower() for w in ("boost", "epay", "vidapay", "luxelink", "novawave")))


# ── §E — SUCCESS IS WHAT LANDED ───────────────────────────────────────────────────────────────────
print("\n§E  an unverified zero is not an import, so it may not advance last_run_at")

check("E1 the sweep reports how many rows LANDED", '"rows_landed"' in src(ES))
check("E2 the connector's status call decides `success` on rows landed, not on the status word",
      "rows_landed" in R and "success=_landed > 0" in R)
check("E3 an untrusted verdict joins `errors`, which is what makes the connector say 'partial'",
      'if not _v["trusted"]:' in src(ES) and "errors.append" in src(ES))


# ── §F — THE LOCK: ONE HOME, DEREFERENCED, AND IT CANNOT UN-WIRE ──────────────────────────────────
print("\n§F  the lock — a sweep may not grow its own answer to 'is this zero real?'")

SWEEPS = {}
for name in ("epay_sweep", "dlar_sweep", "vidapay_sweep"):
    SWEEPS[name] = open(os.path.join(HERE, "app", "modules", "commcalc", f"{name}.py")).read()

for name, text in SWEEPS.items():
    check(f"F1 {name} dereferences the one home", "empty_pull_verdict" in text,
          "this sweep decides a zero-row pull and must read empty_pull_verdict, not its own rule")

# Every registry entry that may legitimately be empty must declare the controls its zero depends on —
# otherwise `empty_ok` is back to deciding on its own.
for key, spec in ES.REPORTS.items():
    if spec.get("empty_ok"):
        check(f"F2 {key}: empty_ok declares the controls its zero depends on",
              bool(spec.get("controls")),
              "add `controls` or the flag decides unaided again (index §19.41)")
        check(f"F2 {key}: those controls are the named ones, not free text",
              all(c == ES.DAILY_RANGE_CONTROL or c in ES.REPORTS[key]["controls"]
                  for c in spec["controls"]))

# Structural: the ONLY place in the sweep that turns an empty download into a result is _defer_empty.
tree = ast.parse(SWEEPS["epay_sweep"])
defer_calls, nodata_literals = 0, 0
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_defer_empty":
        defer_calls += 1
    if isinstance(node, ast.Constant) and node.value == "no_data":
        nodata_literals += 1
check("F3 every empty_ok branch in the sweep routes through the single decision site",
      defer_calls == len([s for s in ES.REPORTS.values() if s.get("empty_ok")]),
      f"_defer_empty call sites = {defer_calls}")
check("F4 no leg hands back a bare 'no_data' the ledger never saw",
      nodata_literals == defer_calls,
      f"'no_data' literals = {nodata_literals}, decision sites = {defer_calls}")

# A second copy of the vocabulary is a future divergence: the published reason key lives in ONE place.
for name, text in SWEEPS.items():
    check(f"F5 {name} carries no literal copy of the published reason key",
          '"portal_reported_empty"' not in text and "'portal_reported_empty'" not in text,
          "dereference empty_pull_verdict.SOURCE_REPORTED_EMPTY instead")
check("F6 the one home holds it", V.SOURCE_REPORTED_EMPTY == "portal_reported_empty")

# The ledger must be run-scoped, not per-leg — the whole reason the live defect escaped.
check("F7 the sweep builds exactly one ledger per run and settles it before reporting",
      SWEEPS["epay_sweep"].count("_verdict.ControlLedger()") == 1
      and "ledger.settle()" in SWEEPS["epay_sweep"])
check("F8 the control name is declared once and used by the registry and the error text alike",
      SWEEPS["epay_sweep"].count('DAILY_RANGE_CONTROL = "') == 1
      and "could not set {DAILY_RANGE_CONTROL}" in SWEEPS["epay_sweep"])

# The verdict home must stay pure: no DB, no network, no config reads.
check("F9 the one home is pure — no client, no requests, no os.environ",
      not any(w in src(V) for w in ("supabase", "requests", "os.environ", ".execute()", "client")))

check("F10 DLAR's own empty guard is gone and the shared sentence is used",
      "aborting before wiping the period" not in SWEEPS["dlar_sweep"]
      and "_empty_verdict.classify_empty_pull" in SWEEPS["dlar_sweep"])


print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    print("FAILURES:")
    for f in FAILURES:
        print(f"  - {f}")
sys.exit(1 if FAIL else 0)
