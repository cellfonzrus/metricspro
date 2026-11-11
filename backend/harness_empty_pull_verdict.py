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
import inspect
import os
import pathlib
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.commcalc import empty_pull_verdict as V        # noqa: E402
from app.modules.commcalc import epay_sweep as ES               # noqa: E402
from app.modules.commcalc import feed_period as FP              # noqa: E402  (§19.54 — the one boundary home)

#: A tenant's business timezone. A feed window's day comes from the ORG's zone, never the
#: server's UTC day (index §COMMCALC-53), so `_expand_jobs` requires it and these checks supply
#: one explicitly rather than letting the harness inherit the container's clock.
TENANT_TZ = "America/New_York"

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


def _raises(fn, *a, **kw):
    """True when `fn` refuses rather than guesses."""
    try:
        fn(*a, **kw)
    except Exception:
        return True
    return False


def src(mod):
    return open(mod.__file__).read()


def fsrc(fn):
    """One function's own source. Separate from `src`, which reads a MODULE's file."""
    return inspect.getsource(fn)


def fexec(fn):
    """One function's EXECUTABLE source — comments and docstrings stripped.

    A docstring that explains a neighbouring per-org setting otherwise reads as that function taking
    an org, which is the opposite of what the check means. House precedent 2026-10-30: a scan of
    prose is not a scan.
    """
    import io
    import textwrap
    import tokenize
    seg = textwrap.dedent(inspect.getsource(fn))
    kept = [t[:2] for t in tokenize.generate_tokens(io.StringIO(seg).readline)
            if t.type != tokenize.COMMENT]
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            body.pop(0)
    return ast.unparse(tree)


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

# THE SPAN IS MEASURED ON THE INTENT, NOT ON THE BOUNDARY ASKED FOR (§19.54, 2026-10-08). The job now
# carries both: `covers_through` is the last day it means to receive, and `end` is what goes in the
# source's End Date widget — one day later under an end-exclusive source, because the carrier statement
# was short the final day of every month ($111,949.22) for exactly that reason. The arrears FLOOR is a
# property of the days we intend to cover, so these checks read `covers_through`; C6 below pins the
# boundary itself so neither half can drift.
jobs = ES._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 1}}, tz=TENANT_TZ)
_k, t = jobs[0]
check("C1 with no arrears configured the house default still applies",
      V.window_days(t["begin"], t["covers_through"]) >= V.required_window_days(None), t)

jobs = ES._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 1, "arrears_days": 7}}, tz=TENANT_TZ)
_k, t = jobs[0]
span = V.window_days(t["begin"], t["covers_through"])
check("C2 THE LIVE CONFIG (refresh_days=1) can no longer produce a 1-day window", span == 7,
      f"span={span} window={t['begin']}..{t['covers_through']}")
check("C3 the window ends today and reaches back, so it can contain in-arrears data",
      len(t["days"]) == 7 and t["covers_through"] == t["days"][-1] and t["begin"] == t["days"][0])
check("C6 and the END ASKED FOR cannot lose that last day (§19.54): under the house end-exclusive "
      "boundary the request runs one day past the intent, and says so",
      t["end"] == FP._next_day(t["covers_through"]) and t["end_widened"] is True
      and t["end_boundary"] == FP.END_EXCLUSIVE)

jobs = ES._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 14, "arrears_days": 7}}, tz=TENANT_TZ)
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


class _StagedPage:
    """A page that answers differently each time it is asked - a panel filling itself in."""

    def __init__(self, stages):
        self._stages = list(stages)
        self.asked = 0
        self.waited = []

    def evaluate(self, script, arg=None):
        self.asked += 1
        return self._stages[min(self.asked, len(self._stages)) - 1]

    def wait_for_timeout(self, ms):
        self.waited.append(ms)


class _DeadPage:
    """A page that cannot be asked at all."""

    def evaluate(self, script, arg=None):
        raise RuntimeError("page closed")

    def wait_for_timeout(self, ms):
        return None


class _FixedPage:
    """A page that answers the scan with exactly this."""

    def __init__(self, result):
        self._result = result

    def evaluate(self, script, arg=None):
        return self._result

    def wait_for_timeout(self, ms):
        return None


# ── §G — A CONTROL IS VERIFIED FROM THE CONTROL (index §COMMCALC-56) ──────────────────────────────
print("\n§G  the control is verified from the thing we set, and an unproven set keeps a zero quiet")

EPAY = SWEEPS["epay_sweep"]

# The defect, as source. The old predicate read the form field INSTEAD of the widget, so the only
# way to be told the filter took was a name the portal owns — and when that name left the page, a
# settable filter read as unsettable on every tick from 2026-10-08 to 2026-11-09.
check("G1 the setter no longer gates on the form field alone",
      "or (iv.get(\"hidden\") or \"\") != \"Daily\"" not in EPAY
      and "(iv.get('hidden') or '') != 'Daily'" not in EPAY,
      "the field beside the control is corroboration, never the verification")
check("G2 it reads the WIDGET'S own selected item",
      "getSelectedItem" in EPAY and "selected" in ES._PICK_JQX_ITEM_JS)
check("G3 the value it must take is declared once, not spelled at each use",
      ES.DAILY_INTERVAL == "Daily" and EPAY.count('DAILY_INTERVAL = "') == 1)
check("G4 …and the corroborating field's name is declared too, marked as not ours",
      ES._INTERVAL_FIELD == "DateIntervalOption"
      and EPAY.count('_INTERVAL_FIELD = "') == 1)
check("G5 the setter returns the third fact — whether the set was CONFIRMED",
      len(inspect.signature(ES._set_date_window).parameters) == 4
      and "confirmed" in fsrc(ES._set_date_window))
check("G6 the only caller unpacks all three, so the fact cannot be dropped silently",
      EPAY.count("_set_date_window(page, target") == 1
      and "ok, detail, confirmed = _set_date_window(" in EPAY
      and EPAY.count("def _set_daily_range(") == 0)
check("G7 an unconfirmed set reaches the RUN's ledger, not just this leg",
      "ledger.control_unconfirmed(DAILY_RANGE_CONTROL" in EPAY)
check("G8 a real failure is still fatal — the fail-loud path is untouched",
      "ledger.control_failed(DAILY_RANGE_CONTROL" in EPAY
      and "raise EpayPortalError(f\"could not set {DAILY_RANGE_CONTROL}" in EPAY)

# The rung itself, in the one home.
check("G9 the one home carries the weaker rung, with its own machine key",
      V.CONTROL_NOT_CONFIRMED == "control_not_confirmed"
      and V.CONTROL_NOT_CONFIRMED != V.CONTROL_PROVEN_BROKEN)
_unc = V.classify_empty_pull(empty_allowed=True, label="X", ever_landed=True,
                             unconfirmed_controls=["the filter"])
check("G10 an unconfirmed control makes a zero UNTRUSTED", _unc["trusted"] is False, repr(_unc))
check("G11 …and says so in words an operator can act on, without claiming it is broken",
      "being confirmed" in _unc["sentence"] and "unsettable" not in _unc["sentence"],
      _unc["sentence"])
check("G12 it outranks the source's own 'no records' — same reason proven-broken does",
      V.classify_empty_pull(empty_allowed=True, label="X", ever_landed=True,
                            corroboration=V.SOURCE_REPORTED_EMPTY,
                            unconfirmed_controls=["the filter"])["trusted"] is False)
check("G13 …and is OUTRANKED by a control proven broken, so one fact prints one sentence",
      V.classify_empty_pull(empty_allowed=True, label="X", ever_landed=True,
                            broken_controls=["the filter"],
                            unconfirmed_controls=["the filter"])["reason"]
      == V.CONTROL_PROVEN_BROKEN)
check("G14 the evidence dict carries it, so the stored journal shows what was weighed",
      "unconfirmed_controls" in _unc["evidence"])
check("G15 a zero with nothing unconfirmed is unaffected — the rung cannot fire on its own",
      V.classify_empty_pull(empty_allowed=True, label="X", ever_landed=True,
                            days_since_last_row=1, window=("2026-11-08", "2026-11-08"),
                            unconfirmed_controls=[])["reason"] != V.CONTROL_NOT_CONFIRMED)

# The ledger: run-scoped, and a later leg's discovery must reach an earlier leg's zero.
_led = V.ControlLedger()
_res = {"rows": 0}
_led.defer(_res, controls=("c",), empty_allowed=True, label="X", ever_landed=True)
_led.control_unconfirmed("c", "field absent")
check("G16 a later leg's unconfirmed control settles an EARLIER leg's zero",
      _led.settle()[0][1]["reason"] == V.CONTROL_NOT_CONFIRMED
      and _res["mode"] == "unverified_no_data")
check("G17 an unconfirmed control defers nothing by itself — real rows stay real",
      V.ControlLedger().control_unconfirmed("c", "x").deferred == [])
check("G18 a control nobody declared a dependency on does not touch a zero",
      V.ControlLedger().control_unconfirmed("other", "x") is not None
      and (lambda led, r: (led.defer(r, controls=("c",), empty_allowed=True, label="X",
                                    ever_landed=True, days_since_last_row=1),
                           led.control_unconfirmed("other", "x"),
                           led.settle()[0][1]["reason"] != V.CONTROL_NOT_CONFIRMED)[-1])(
          V.ControlLedger(), {"rows": 0}))

# EVERY TENANT (owner directive 2026-10-30): none of this reads an org, so it cannot differ per
# tenant — pinned as source, because an org argument appearing here is how that would start.
check("G19 neither the setter nor the rung knows which tenant it is acting for",
      not re.search(r"\borg\w*", fexec(ES._set_date_window))
      and not re.search(r"\borg\w*", fexec(V.classify_empty_pull)))


# ── §H — A CONTROL IS FOUND BY WHAT IT ANSWERS TO (index §COMMCALC-58) ───────────────────────────
print("\n§H  a picker is found by what it answers to, not by how it looks — one home, both callers")

# THE DEFECT, settled by a photograph on 2026-11-10. §COMMCALC-56's new wording said "no
# 'Summarize by' dropdown offering Daily was found on the report's filter panel". The owner
# photographed that panel: the control was there, offering Daily / Weekly / Monthly, re-skinned to
# the SEARCHABLE jqWidgets flavour. The scan knew one styling class and reported a present control
# as absent — the one answer an operator cannot act on.
#: Every place a jqWidgets picker may be looked for, written out by a human. Compared whole, not
#: counted: a scan that silently GAINS a shape is a shape the executed proof has not been taught,
#: and one that silently LOSES one is how a present control read as absent on 2026-11-10.
EXPECTED_PICKER_SELECTORS = {
    '[class*="jqx-dropdownlist"]',
    '[class*="jqx-combobox"]',
    '[role="combobox"]',
    '[role="listbox"]',
}

PICK = fsrc(ES.pick_jqx_item)

check("H1 the scan is not keyed on a widget STATE class",
      "-state-normal" not in ES._PICK_JQX_ITEM_JS
      and not any("-state-" in sel for sel in ES.JQX_PICKER_SELECTORS),
      "'-state-normal' is a moment in a control's life, not its identity")
check("H2 where a picker may BE is exactly the declared set",
      isinstance(ES.JQX_PICKER_SELECTORS, tuple)
      and set(ES.JQX_PICKER_SELECTORS) == EXPECTED_PICKER_SELECTORS
      and EPAY.count("JQX_PICKER_SELECTORS = (") == 1,
      "a selector that joins or leaves must be taught to prove_jqx_picker.mjs in the same "
      "change — an untaught shape matches nothing and reports a present control as absent")
check("H3 how a picker is DRIVEN is declared once, and covers both known families",
      ES.JQX_PICKER_WIDGETS == ("jqxDropDownList", "jqxComboBox")
      and EPAY.count("JQX_PICKER_WIDGETS = (") == 1,
      "the plain list AND the searchable one; dropping either re-opens this defect")
check("H4 the scan is given the declarations, never its own copy of them",
      "JQX_PICKER_SELECTORS" in PICK and "JQX_PICKER_WIDGETS" in PICK
      and "jqx-combobox" not in PICK and "jqx-dropdownlist" not in PICK)
check("H5 a family is driven through its OWN api, chosen at runtime",
      "$(host)[w]" in ES._PICK_JQX_ITEM_JS,
      "naming one widget's method is how the scan got pinned to one family")
check("H6 the scan reports what the panel DOES offer",
      "offered" in ES._PICK_JQX_ITEM_JS and "offered" in PICK,
      "a failure that names only what is missing cost one photograph and a month of empty days")
check("H7 …and 'no picker at all' is a different sentence from 'none offering it'",
      "no picker was found" in ES._PICK_JQX_ITEM_JS
      and "none offering it" in ES._PICK_JQX_ITEM_JS,
      "opposite fixes: rebuild the leg, or widen the scan")
check("H8 the wrapper always returns every key its callers unpack",
      (lambda r: set(r) == {"ok", "selected", "widget", "offered", "candidates", "pickers",
                            "ready", "empty", "looked", "waited_ms", "panel", "why"})(
          ES.pick_jqx_item(_DeadPage(), ["x"])))

# ── COMMCALC-61 — PRESENT BUT NOT READY IS NOT ABSENT ────────────────────────────────────────────
# Measured 2026-11-10 16:30, the first run on COMMCALC-58's wording: the panel reported ONE picker
# and it was the dealer list. The filter control was on the page and had not populated, and an
# empty control was skipped entirely — so a control we were looking straight at read as missing.
check("H21 an empty picker is COUNTED, not skipped",
      "empty += 1" in ES._PICK_JQX_ITEM_JS and "ready += 1" in ES._PICK_JQX_ITEM_JS)
check("H22 'read too early' is its OWN sentence, not folded into 'none offering it'",
      "finished filling" in ES._PICK_JQX_ITEM_JS
      and "none offering it" in ES._PICK_JQX_ITEM_JS
      and "no picker was found" in ES._PICK_JQX_ITEM_JS)
check("H23 the wait is BOUNDED and each bound is declared once",
      isinstance(ES.PICKER_PATIENCE, int) and 1 <= ES.PICKER_PATIENCE <= 10
      and isinstance(ES.PICKER_SETTLE_MS, int) and 0 < ES.PICKER_SETTLE_MS <= 5000
      and isinstance(ES.PICKER_DEADLINE_MS, int) and 0 < ES.PICKER_DEADLINE_MS <= 60_000
      and EPAY.count("PICKER_PATIENCE = ") == 1 and EPAY.count("PICKER_SETTLE_MS = ") == 1
      and EPAY.count("PICKER_DEADLINE_MS = ") == 1,
      "a wait with no ceiling is a sweep that never reports")
check("H24 …and a look that cannot help is not taken",
      'out["empty"] > 0' in PICK and 'out["candidates"] == 0' in PICK,
      "a READY picker will not start offering a value it does not have")
check("H25 a success never waits",
      (lambda p: (ES.pick_jqx_item(p, ["Daily"]), p.waited == [])[-1])(_StagedPage(
          [{"ok": True, "hit": {"id": "x", "widget": "jqxComboBox", "selected": "Daily"},
            "offered": ["Daily"], "candidates": 1, "pickers": 1, "ready": 1, "empty": 0}])))
check("H26 …and a panel that fills in on the second look IS found",
      (lambda p: ES.pick_jqx_item(p, ["Daily"])["ok"])(_StagedPage([
          {"ok": False, "hit": None, "offered": [], "candidates": 2, "pickers": 2, "ready": 1,
           "empty": 1, "why": "still empty"},
          {"ok": True, "hit": {"id": "x", "widget": "jqxComboBox", "selected": "Daily"},
           "offered": ["Daily"], "candidates": 2, "pickers": 2, "ready": 2, "empty": 0}])))
check("H27 the operator is told how many looks it took",
      "looked" in PICK and "while the panel settled" in PICK,
      "a slow panel and an absent control read identically without it")
# ── COMMCALC-62 — THE WAIT FOLLOWS PROGRESS, AND NO ONE CONTROL MAY DROWN THE REPORT ────────────
# Measured 2026-11-10 18:30 on COMMCALC-61's code: "3 picker(s) with options, none offering it
# (and 10 still empty) (looked 4 times over 4.5s)". THIRTEEN controls, ten unfilled when the fixed
# budget expired — the budget decided the answer, not the portal. And the sentence was almost
# entirely the dealer list: `offered` merged every label, the recorder truncated at 600 chars, and
# the two other loaded controls were unreadable. That is why the panel's shape took three rounds.
check("H28 the wait is spent on PROGRESS, not on a fixed count",
      "stalled" in PICK and "PICKER_PATIENCE" in PICK and "PICKER_DEADLINE_MS" in PICK
      and "PICKER_LOOKS" not in EPAY,
      "a guessed number cannot know how slow the portal is today")
check("H29 a panel that STOPPED filling says so, with the count still empty",
      "stopped filling" in PICK,
      "the controls are there and the page gave up on them - its own, actionable fact")
check("H30 a stalled panel is not waited on for ever",
      (lambda p: (ES.pick_jqx_item(p, ["Daily"], settle_ms=1),
                  p.asked <= ES.PICKER_PATIENCE + 2)[-1])(_StagedPage(
          [{"ok": False, "hit": None, "offered": [], "panel": [], "candidates": 7, "pickers": 7,
            "ready": 1, "empty": 6, "why": "none offering it"}])),
      "waiting on a page that has stopped changing has become guessing")
check("H31 …and a panel that keeps filling IS waited on past the old four looks",
      (lambda p: (ES.pick_jqx_item(p, ["Daily"], settle_ms=1), p.asked >= 6)[-1])(_StagedPage(
          [{"ok": False, "hit": None, "offered": [], "panel": [], "candidates": 10, "pickers": 10,
            "ready": r, "empty": 10 - r, "why": "none offering it"} for r in range(1, 9)])),
      "ten controls still empty at the deadline is the measurement that forced this")
check("H32 the per-control sample size is DECLARED, not inlined in the JavaScript",
      isinstance(ES.PICKER_SAMPLE, int) and 0 < ES.PICKER_SAMPLE <= 20
      and EPAY.count("PICKER_SAMPLE = ") == 1
      and "int(PICKER_SAMPLE)" in PICK and "slice(0, sample)" in ES._PICK_JQX_ITEM_JS,
      "a sample size buried in a string is one nobody can find or lock")
check("H33 each control reports its own size and its own sample",
      "panel.push({" in ES._PICK_JQX_ITEM_JS
      and "options: labels.length" in ES._PICK_JQX_ITEM_JS
      and "options: 0" in ES._PICK_JQX_ITEM_JS,
      "the empty control must appear in the report too, not only in the counts")
check("H34 the panel description has ONE home and both the size and the sample reach it",
      EPAY.count("def describe_panel(") == 1
      and "option(s)" in fexec(ES.describe_panel) and "more" in fexec(ES.describe_panel)
      and "empty" in fexec(ES.describe_panel),
      "a second copy of this sentence is a future divergence")
check("H35 one huge control cannot fill the sentence",
      (lambda d: len(d) < 1200 and "PaymentCategory" in d and "480" in d
                 and "474517 - Wireless LLC" in d)(
          ES.describe_panel({"panel": [
              {"id": "DealerList", "widget": "jqxComboBox", "options": 480,
               "sample": [f"4745{n} - Wireless LLC" for n in range(17, 17 + ES.PICKER_SAMPLE)]},
              {"id": "PaymentCategory", "widget": "jqxDropDownList", "options": 2,
               "sample": ["All", "Commission"]},
              {"id": "SummarizeBy", "widget": "jqxComboBox", "options": 0, "sample": []}]})),
      "the recorder truncates at 600 chars, so a long sentence loses the control we are hunting")
check("H36 …and the control we are hunting is named even while it is empty",
      "SummarizeBy: empty" in ES.describe_panel({"panel": [
          {"id": "SummarizeBy", "widget": "jqxComboBox", "options": 0, "sample": []}]}))

# ── COMMCALC-64 — A THING INSIDE A THING IS NOT A SECOND CONTROL ────────────────────────────────
# Measured 2026-11-10 19:30 on COMMCALC-62's code. The per-control report finally named them, and
# the names were jqWidgets' own internal pieces with the hashes REPEATING — `listBoxContent…0206f1e1`
# beside `innerListBox…0206f1e1` beside `jqxWidget0206f1e1`. One control counted three times through
# its own scaffolding. Two consequences: the panel never had thirteen controls, and an inner piece
# can never carry items, so `empty > 0` was permanently true and every tick burnt its whole wait
# budget on scaffolding before reporting a stall.
check("H37 only the OUTERMOST match is a control",
      "const hosts = matched.filter(" in ES._PICK_JQX_ITEM_JS
      and "o.contains(h)" in ES._PICK_JQX_ITEM_JS
      and "hosts.forEach(" in ES._PICK_JQX_ITEM_JS,
      "a dropdown's inner divs carry its own jqx-* classes, so they match the same selectors")
check("H38 a per-page-load generated id is NOT a name, and that rule is declared once",
      EPAY.count("GENERATED_ID_RE = ") == 1
      and ES.GENERATED_ID_RE.search("innerListBoxjqxWidget0206f1e1")
      and ES.GENERATED_ID_RE.search("jqxWidget349f357e")
      and not ES.GENERATED_ID_RE.search("SummarizeBy"),
      "a name that changes every page load tells the operator nothing and compares across no ticks")
check("H39 what we call a control has ONE home, and the description dereferences it",
      EPAY.count("def control_name(") == 1
      and "control_name(e)" in fexec(ES.describe_panel)
      and "GENERATED_ID_RE" in fexec(ES.control_name),
      "a second naming rule is the divergence the house forbids")
check("H40 the caption printed beside the control beats its id",
      ES.control_name({"label": "Summarize by", "id": "jqxWidget0206f1e1",
                       "widget": "jqxComboBox"}) == "Summarize by"
      and ES.control_name({"label": "", "id": "SummarizeBy",
                           "widget": "jqxComboBox"}) == "SummarizeBy")
check("H41 a control with no real name is reported as unnamed, never as its generated id",
      "jqxWidget" not in ES.control_name({"id": "jqxWidget0206f1e1", "widget": "jqxComboBox"})
      and "unnamed" in ES.control_name({"id": "jqxWidget0206f1e1", "widget": "jqxComboBox"})
      and "unnamed" in ES.control_name({}),
      "papering a generated id over a missing label is how three rounds went to the wrong question")
check("H42 the caption is read FROM THE PAGE, by all three of the ways a panel labels a control",
      "aria-label" in ES._PICK_JQX_ITEM_JS
      and "label[for=" in ES._PICK_JQX_ITEM_JS
      and "previousElementSibling" in ES._PICK_JQX_ITEM_JS
      and "label: labelOf(host)" in ES._PICK_JQX_ITEM_JS,
      "only the browser can see what is printed beside a control")
# ── COMMCALC-66 — WHICH CONTROLS SET A REPORT'S WINDOW IS THAT REPORT'S PAGE'S PROPERTY ─────────
# Measured 2026-11-10 21:00, the first tick after COMMCALC-64 named controls by their captions:
# the Daily Transaction Detail panel carries an account selector, Username, Retailer Account ID,
# Terminal ID and "Date of most recent transaction to include (mandatory)" — and NO "Summarize by".
# Both day-grain reports declared `filter: "daily_range"`, so comp's Summarize-by routine was being
# driven against a page that has never had the control.
check("H44 how a report's window is set has ONE home and is DECLARED per report",
      EPAY.count("def date_filter_of(") == 1 and EPAY.count("def resolve_date_filter(") == 1
      and ES.date_filter_of(ES.REPORTS["comp_report"]) == ES.SUMMARIZE_BY_DAILY
      and ES.date_filter_of(ES.REPORTS["epay_daily_tx"]) != ES.SUMMARIZE_BY_DAILY,
      "one word meaning 'day-grain' AND 'driven by Summarize by' is what crossed the two pages")
check("H45 silence is never another report's routine",
      ES.date_filter_of({}) == ES.DATE_FILTER_UNDECLARED
      and ES.date_filter_of(None) == ES.DATE_FILTER_UNDECLARED,
      "a default that is really one report's page is the per-tenant-vocabulary defect again")
check("H46 every day-grain report declares one, and an undeclared routine is REFUSED",
      all(ES.date_filter_of(sp) in ES.DATE_FILTERS
          for sp in ES.REPORTS.values() if sp.get("grain") == "day")
      and _raises(ES.date_filter_of, {"date_filter": "whatever_the_next_page_wants"}),
      "a routine nobody wrote is not guessed at on a live portal")
check("H47 the dispatch dereferences the declaration rather than the word 'daily'",
      "resolve_date_filter(" in fexec(ES._open_and_download)
      and "DATE_FILTER_UNDECLARED" in fexec(ES._open_and_download)
      and "describe_date_controls(page)" in fexec(ES._open_and_download)
      # COMMCALC-67: the routine is PASSED to the one shared setter, not branched on here, so a
      # new report's routine never means a new branch in the dispatch.
      # (`fexec` unparses the AST, so the subscripts come back single-quoted.)
      and "_set_date_window(page, target['begin'], target['end'], routine)" in fexec(
          ES._open_and_download),
      "this is the wiring; a registry nobody reads is not a fix (19.18)")
check("H48 an undeclared report is not run unfiltered, and says so",
      "was not run" in fexec(ES.describe_date_controls)
      and "raise EpayPortalError" in fexec(ES._open_and_download),
      "a day-grain pull with no filter lands somebody else's window and looks like a quiet day")
check("H49 the two legs no longer share one ledger control key",
      ES.REPORTS["epay_daily_tx"]["controls"] != ES.REPORTS["comp_report"]["controls"]
      and ES.UNDECLARED_DATE_CONTROL != ES.DAILY_RANGE_CONTROL,
      "one shared key let a control broken on one page condemn the other page's honest zero")
check("H50 the page's date fields are reported by NAME and never by value",
      "getAttribute('name')" in ES._DATE_FIELD_NAMES_JS
      and ".value" not in ES._DATE_FIELD_NAMES_JS,
      "a value on these panels can be an account id")
check("H51 the captions in that verdict come from the ONE naming home, not a second scan",
      "describe_panel(scan)" in fexec(ES.describe_date_controls)
      and "pick_jqx_item(page" in fexec(ES.describe_date_controls),
      "a second scan of the same panel is the duplicate the house forbids")

# ── COMMCALC-67 — THE FIELDS A WINDOW IS TYPED INTO ARE DECLARED TOO ────────────────────────────
# Measured 2026-11-10 22:00, on COMMCALC-66's wording: "the page's date fields are StartDate,
# EndDate" — while this module's date step had always typed into BeginDate/EndDate. Two facts per
# report, neither of them a word in the code: whether an interval is set first, and which two
# fields carry the dates. The comp report's own page has STILL not been measured, so correcting it
# must be a row in the declaration rather than an edit to a string of JavaScript.
check("H52 every declared routine names the two fields it types into",
      all(len(set(ES.date_window_fields(n))) == 2
          for n in ES.DATE_FILTERS if n != ES.DATE_FILTER_UNDECLARED)
      and ES.date_window_fields(ES.SUMMARIZE_BY_DAILY) == ("BeginDate", "EndDate")
      and ES.date_window_fields(ES.START_END_DATES) == ("StartDate", "EndDate"),
      "a field name nothing declares is a field no report can correct")
check("H53 the field names are PASSED INTO the page, never written into it",
      "BeginDate" not in ES._SET_DATES_JS and "StartDate" not in ES._SET_DATES_JS
      and "beginName" in ES._SET_DATES_JS and "endName" in ES._SET_DATES_JS,
      "a name baked into the JavaScript is the per-report code the house forbids")
check("H54 only a report that SUMMARISES is asked for an interval",
      ES.date_window_interval(ES.SUMMARIZE_BY_DAILY) == ES.DAILY_INTERVAL
      and ES.date_window_interval(ES.START_END_DATES) is None
      and "if interval is None:" in fexec(ES._set_date_window),
      "asking a page for a control it has not got is the whole COMMCALC-66 defect")
check("H55 the plan has ONE home and an undeclared routine is still refused",
      EPAY.count("def date_window_plan(") == 1
      and EPAY.count("def date_window_fields(") == 1
      and EPAY.count("def date_window_interval(") == 1
      and _raises(ES.date_window_fields, ES.DATE_FILTER_UNDECLARED)
      and _raises(ES.date_window_fields, "whatever_the_next_page_wants"),
      "a routine nobody wrote is not guessed at on a live portal")
check("H56 there is ONE date step, shared by the report that summarises and the one that does not",
      EPAY.count("def _put_date_fields(") == 1
      and EPAY.count("page.evaluate(_SET_DATES_JS") == 1
      and "_put_date_fields(" in fexec(ES._set_date_window),
      "two date steps is how one page's controls came to be driven against another's")
check("H57 the fields that did not take are named back with the REPORT'S names",
      "{begin_field}/{end_field} did not take" in fsrc(ES._put_date_fields),
      "'Start/End Date did not take' named a control the operator could not find on the page")
check("H58 the two day-grain legs keep separate ledger keys",
      len({ES.REPORTS["comp_report"]["controls"], ES.REPORTS["epay_daily_tx"]["controls"]}) == 2
      and ES.START_END_DATES_CONTROL != ES.DAILY_RANGE_CONTROL,
      "a shared key made each leg's failure condemn the other leg's honest zero")

# ── A REPORT'S OWN VERDICT BELONGS ON THE REPORT'S OWN ROW (index §COMMCALC-68) ───────────────
# `report_definitions.sweep_last_status/_detail` are read by `router._setup_documents` to flag a
# report urgent and show its error, and NOTHING wrote them — NULL on every row of every tenant.
# Every leg wrote the ONE shared `epay_sweep_config.last_detail` instead, so comp's reason was
# erased by the next transaction-detail tick within thirty minutes, every night. These checks fail
# the build if the per-report write un-wires or a second copy of either fact appears.
import importlib                                                       # noqa: E402
RS = importlib.import_module("app.modules.commcalc.report_status")
RSRC = src(RS)
_ROUTER = importlib.import_module("app.modules.commcalc.router")
_DO = fsrc(_ROUTER._do_epay_sweep)
_RUN = fsrc(ES.run_epay_sweep)

check("H59 which columns a report's verdict lives in is named ONCE",
      RS.REPORT_STATUS_COLUMNS == ("sweep_last_status", "sweep_last_detail")
      and all(c in RSRC for c in RS.REPORT_STATUS_COLUMNS),
      "a third spelling of the column is the divergence this module exists to stop")
check("H60 which registry row a sweep key belongs to is DEREFERENCED, never re-derived",
      EPAY.count('registry_key") or k') == 0
      and EPAY.count('registry_key") or key') == 0
      and EPAY.count("def registry_key_of(") == 1
      and 'spec["registry_key"]' not in src(RS),
      "`spec.get('registry_key') or k` invents a row name for a report that declares none")
check("H61 …and an undeclared report is refused rather than written to a guessed row",
      _raises(ES.registry_key_of, "not_a_report"))
check("H62 every declared report resolves to its own registry row",
      all(ES.registry_key_of(k) == v["registry_key"] for k, v in ES.REPORTS.items())
      and len({ES.registry_key_of(k) for k in ES.REPORTS}) == len(ES.REPORTS),
      "two reports sharing one row is two verdicts in one column again")
check("H63 the run records a verdict PER REPORT, where the key is in scope",
      'summary["per_report"] = outcomes' in _RUN
      and _RUN.count("outcomes[key] = ") >= 2
      and "outcomes = {}" in _RUN,
      "one verdict for a run that pulled four reports cannot say which report it is about")
check("H64 a run that failed ENTIRELY still carries each report's reason",
      "per_report=outcomes" in _RUN
      and ES.EpayPortalError("x", per_report={"a": ("error", "y")}).per_report == {"a": ("error", "y")}
      and ES.EpayPortalError("plain").per_report == {},
      "the all-failed case is the one case the per-report rows matter most")
check("H65 the caller WRITES those rows — on the mixed run and on the total failure",
      _DO.count("report_status.write(") == 2
      and "getattr(e, 'per_report', None)" in _DO,
      "a registry written but not wired is not a fix; it has happened four times here (§19.18)")
check("H66 the sentence is FITTED through the one home, not head-cut",
      "_status_detail.fit(" in RSRC
      and "[:600]" not in RSRC and f"[:{RS.DETAIL_CAP}]" not in RSRC
      and "THE CONCLUSION" in RS.rows_for({"mi": ("error", "A" * 800 + "THE CONCLUSION")})[0]["sweep_last_detail"],
      "a sweep's conclusion is written LAST, so a head cut throws away the only part worth reading")
check("H68 recording the verdicts costs a FIXED number of round trips",
      ".in_(" in fexec(RS.write) and ".upsert(" in fexec(RS.write)
      and "for row in rows_for(" not in fexec(RS.write),
      "one UPDATE per report is the N+1 harness_round_trip_ratchet.py holds the estate to")
check("H67 a verdict never reaches a report other than its own",
      [r["report_key"] for r in RS.rows_for({"comp_report": ("error", "comp's own")})] == ["comp_report"]
      and len(RS.rows_for({"mi": ("ok", "a"), "epay_daily_tx": ("ok", "b")})) == 2)

check("H9 ok is never true without the widget's own answer (§COMMCALC-56 holds here too)",
      ES.pick_jqx_item(_FixedPage({"ok": True, "hit": {"id": "a", "selected": None}}),
                       ["x"])["ok"] is False)

# ONE HOME, BOTH CALLERS. Two near-identical scans in this file is what let one of them go blind
# while the other kept working — the duplicate the house forbids, with one copy fixed.
check("H10 the shared routine is the ONLY picker scan in the module",
      EPAY.count("_PICK_JQX_ITEM_JS = ") == 1
      and EPAY.count("jqxDropDownList('getItems')") == 0
      and EPAY.count('jqxDropDownList("getItems")') == 0,
      "a second scan is a future divergence")
check("H11 'Summarize by' dereferences it",
      "pick_jqx_item(page, [interval])" in fexec(ES._set_date_window)
      and ES.date_window_interval(ES.SUMMARIZE_BY_DAILY) == ES.DAILY_INTERVAL)
check("H12 the month picker dereferences it too",
      "pick_jqx_item(page," in fexec(ES._set_report_month))
check("H13 the failure the operator reads names the panel's real options",
      "describe_panel(iv)" in fexec(ES._set_date_window)
      and "panel's controls are" in fexec(ES.describe_panel))

# MATCHING ON MEANING. The portal writes "June- 26" and re-spells its own labels; a comparison on
# punctuation fails a control that took the value.
check("H14 labels are compared on meaning, in one place, through THE spelling fold",
      EPAY.count("def _same_label(") == 1
      and "_squash_key(" in fexec(ES._same_label)
      and "isalnum" not in fexec(ES._same_label)
      and ES._same_label("Daily", " daily: ") and not ES._same_label("Daily", "Weekly")
      and ES._same_label("Caf\u00e9", "Cafe"),
      "a private alnum fold does NOT fold accents - str.isalnum() is true for them (2026-10-10)")
check("H15 the setter compares the control's answer that way, not by ==",
      "_same_label(selected, DAILY_INTERVAL)" in fexec(ES._set_date_window)
      and 'str(selected).strip() != DAILY_INTERVAL' not in EPAY)

# THE REGRESSION IS EXECUTED, because the defect was in JavaScript no python test runs.
_PROOF = pathlib.Path(__file__).resolve().parent / "prove_jqx_picker.mjs"
_proof_src = _PROOF.read_text(encoding="utf-8") if _PROOF.exists() else ""
check("H16 the executed proof exists beside this lock",
      _PROOF.exists(),
      "a scan that no test RUNS is how this shipped")
check("H17 …and it reads the real source rather than a copy of the scan",
      "epay_sweep.py" in _proof_src and "_PICK_JQX_ITEM_JS" in _proof_src)
check("H18 …against the panel in the photograph (the searchable flavour)",
      "jqx-combobox-state-normal" in _proof_src and "jqxComboBox" in _proof_src)
check("H19 …and its fake DOM refuses a selector shape it has not been taught",
      "does not understand the selector" in _proof_src,
      "silently matching nothing is the failure being locked out")
check("H43 the browser proof's fake page is a real TREE, not a flat list",
      "contains(other)" in _proof_src and "previousElementSibling" in _proof_src
      and "children" in _proof_src and "COMMCALC-64" in _proof_src,
      "a fake with no nesting cannot express this defect, so a proof on it is not a proof")


# EVERY TENANT (owner directive 2026-10-30).
check("H20 the shared routine does not know which tenant it is acting for",
      not re.search(r"\borg\w*", fexec(ES.pick_jqx_item))
      and "org" not in inspect.signature(ES.pick_jqx_item).parameters)

print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    print("FAILURES:")
    for f in FAILURES:
        print(f"  - {f}")
sys.exit(1 if FAIL else 0)
