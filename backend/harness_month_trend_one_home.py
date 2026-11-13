#!/usr/bin/env python3
"""ONE ANSWER TO "WHAT IS THIS MONTH TRENDING TO", AND IT CANNOT UN-WIRE. Index §COMMCALC-72.

DB-free. Reads the source, so it fails the build the moment a caller stops dereferencing the home
or a second copy of the arithmetic appears — which is the only part of a design fix that lasts
(CLAUDE.md: "Lock it so it cannot un-wire").

WHY THIS PARTICULAR LOCK. The projection was inline in `router.py` TWICE before 2026-11-12, and its
answer was already on two screens. The factoring is only a fix while both callers keep reading the
home, and the cheapest way for the next change to undo it is to write `x / elapsed` again where it
is needed rather than to ask for it.

    §A  the home exists, is pure, and derives nothing it could dereference
    §B  no second copy of the division, anywhere under backend/app
    §C  both of the rewired callers still dereference it
    §D  the trend is SERVED, not computed on the screen
    §E  the honesty: not-measured and no-target can never render as on-track
    §F  one outlier rule, decided on the server
    §G  one focus predicate for the tiles and the detail
    §H  the tests that prove all this actually run in CI
    §I  negative controls — every scan above is shown to be capable of failing
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
REPO = ROOT.parent
APP = ROOT / "app"
sys.path.insert(0, str(ROOT))

# WHICH FILE IS THE HOME is itself a fact with one home — `core.module_graph` (§50). This lock READS
# it instead of keeping its own path literal, so moving the module updates the graph and every lock
# that reads it, rather than leaving a stale literal that passes while pointing at nothing.
from app.modules.core import module_graph as _mg      # noqa: E402

FACT = "month_trend"
HOME = APP / _mg.home_under_app(FACT)
# The CALLERS, also read from the graph rather than spelled here — these are the two sites that held
# the inline copies. `targets_drilldown` is not a caller of the home; it carries the served figure
# through the team fold, which is why it is named separately below.
_CALLERS = {pathlib.PurePath(c).name: APP / c.split("app/", 1)[-1] for c in _mg.callers_for(FACT)}
ROUTER = _CALLERS["router.py"]
ENGINE = _CALLERS["targets_engine.py"]
DRILL = APP / "modules" / "commcalc" / "targets_drilldown.py"
FE = REPO / "frontend" / "src"
PAGE = FE / "app" / "(platform)" / "commcalc" / "targets" / "action-plan" / "page.tsx"
TILES = FE / "lib" / "action-plan-tiles.ts"
STATE = FE / "lib" / "target-state.ts"
UNIT = ROOT / "tests" / "unit" / "test_month_trend.py"
INTEG = ROOT / "tests" / "integration" / "test_action_plan_tiles_endpoint.py"
WORKFLOWS = REPO / ".github" / "workflows"

FAILS: list[str] = []
PASSES = 0


def check(tag: str, ok: bool, why: str) -> bool:
    global PASSES
    if ok:
        PASSES += 1
    else:
        FAILS.append(f"{tag}: {why}")
    return ok


def text(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def strip_py_comments(src: str) -> str:
    """Code only. A rule that matched a comment could be satisfied by deleting prose, and a ban that
    fired on prose would stop anybody explaining the ban."""
    out = []
    for line in src.splitlines():
        s = line.split("#", 1)[0]
        out.append(s)
    body = "\n".join(out)
    body = re.sub(r'"""(?:.|\n)*?"""', '""', body)
    body = re.sub(r"'''(?:.|\n)*?'''", "''", body)
    return body


def strip_ts_comments(src: str) -> str:
    src = re.sub(r"/\*(?:.|\n)*?\*/", "", src)
    return "\n".join(ln.split("//", 1)[0] for ln in src.splitlines())


HOME_SRC = text(HOME)
HOME_CODE = strip_py_comments(HOME_SRC)
ROUTER_CODE = strip_py_comments(text(ROUTER))
ENGINE_CODE = strip_py_comments(text(ENGINE))
DRILL_CODE = strip_py_comments(text(DRILL))
PAGE_SRC = text(PAGE)
PAGE_CODE = strip_ts_comments(PAGE_SRC)
TILES_SRC = text(TILES)
TILES_CODE = strip_ts_comments(TILES_SRC)
STATE_SRC = text(STATE)
STATE_CODE = strip_ts_comments(STATE_SRC)

# ── §A  THE HOME ──────────────────────────────────────────────────────────────────────────────────
check("A1", HOME.exists(), f"{HOME} is missing — the one home for the trend window")
for name in ("month_window", "factor", "project", "verdict", "trend_of", "outliers",
             "days_in_month", "is_open_month"):
    check(f"A2:{name}", re.search(rf"^def {name}\(", HOME_CODE, re.M) is not None,
          f"month_trend.{name} is not exported — a caller cannot dereference what is not there")
check("A3", "OUTLIER_SPREAD_MULTIPLE" in HOME_CODE and "OUTLIER_MIN_COUNT" in HOME_CODE,
      "the outlier thresholds are not named constants, so they cannot be read or re-pinned")
# PURE. A clock, a client or a config read here would make the arithmetic untestable and the figure
# dependent on when it was asked.
for banned, why in (("get_supabase", "reaches the database"),
                    ("requests.", "makes a network call"),
                    ("os.environ", "reads configuration")):
    check(f"A4:{banned}", banned not in HOME_CODE, f"month_trend {why} — it must stay pure")
check("A5", "_dt.date.today()" in HOME_CODE,
      "month_window must still accept an injected `today`; its only default may be the real date")
check("A6", HOME_CODE.count("_dt.date.today()") == 1,
      "more than one place in month_trend reads the clock — `today` is injected once and passed on")
# IT DERIVES NOTHING IT COULD DEREFERENCE. These three facts live in feed_period and the division
# lives in demand_projection; the first draft of this module re-derived all four.
check("A7", "from app.modules.commcalc import feed_period" in HOME_CODE,
      "month_trend does not dereference feed_period — the month facts have a home")
for fn in ("period_month_year", "month_days", "month_state"):
    check(f"A8:{fn}", f"_fp.{fn}(" in HOME_CODE,
          f"month_trend does not read feed_period.{fn} — that is the one home for it")
check("A9", "monthrange" not in HOME_CODE,
      "month_trend calls calendar.monthrange itself; feed_period.month_days already answers it")
check("A10", "_dp.velocity(" in HOME_CODE,
      "month_trend does not dereference demand_projection.velocity — the division has one home")

# ── §B  NO SECOND COPY OF THE DIVISION ────────────────────────────────────────────────────────────
# The shape that was inline twice. Scanned over ALL of backend/app rather than the two files that
# were reported: fixing the reported lines and leaving the pattern available is not a fix.
_DIV = re.compile(r"/\s*elapsed\b")
_FACTOR_ASSIGN = re.compile(r"^\s*trend_factor\s*=\s*\(?\s*dim\s*/", re.M)
# `module_graph.py` is PROSE ABOUT the code — its `question` fields quote the very line this scan
# bans, because the registry's job is to record what was factored and why. Excluded by name, with
# the reason, rather than by weakening the pattern: a looser pattern would stop catching the real
# thing everywhere else. I2 and I9 below show the exclusion is this one file and not a hole.
_PROSE = {(APP / "modules" / "core" / "module_graph.py").resolve()}
offenders = []
for py in sorted(APP.rglob("*.py")):
    if py.resolve() == HOME.resolve() or py.resolve() in _PROSE:
        continue
    code = strip_py_comments(py.read_text(encoding="utf-8", errors="replace"))
    if _DIV.search(code) or _FACTOR_ASSIGN.search(code):
        offenders.append(str(py.relative_to(REPO)))
check("B1", not offenders,
      "a second copy of the month-end division lives in: " + ", ".join(offenders))
check("B2", _DIV.search(HOME_CODE) is None,
      "even the home should not write `/ elapsed`; it asks demand_projection.velocity")

# ── §C  BOTH REWIRED CALLERS STILL DEREFERENCE IT ─────────────────────────────────────────────────
check("C1", "import month_trend as _month_trend" in ROUTER_CODE,
      "router.py no longer imports the trend home")
check("C2", ROUTER_CODE.count("_month_trend.month_window(") >= 2,
      "router.py asks the home for fewer than the two windows it needs (the month AND the range) — "
      "one of the two inline sites has been restored or dropped")
check("C3", ROUTER_CODE.count("_month_trend.factor(") >= 2,
      "router.py computes a factor itself at one of the two sites")
check("C4", "span_from=" in ROUTER_CODE and "span_to=" in ROUTER_CODE,
      "the date-range site no longer passes its span to the home, so the range and the month can "
      "drift apart again")
check("C5", "import month_trend as _month_trend" in ENGINE_CODE,
      "targets_engine no longer dereferences the trend home")
check("C6", "_month_trend.trend_of(" in ENGINE_CODE,
      "targets_engine does not call trend_of — the per-category trend is gone or hand-rolled")
check("C7", "_month_trend.verdict(" in ENGINE_CODE,
      "the team roll-up decides its own verdict instead of asking the home")

# ── §D  SERVED, NOT COMPUTED ON THE SCREEN ────────────────────────────────────────────────────────
check("D1", "'trend':" in ENGINE_CODE or '"trend":' in ENGINE_CODE,
      "targets_engine does not put a `trend` on a category, so nothing can be served")
check("D2", "'trend': m.get('trend')" in ROUTER_CODE,
      "the action-plan endpoint does not pass the served trend into its metrics list")
check("D3", '"trend": m.get("trend")' in DRILL_CODE,
      "targets_drilldown drops the trend when shaping plan rows for the team fold, which would "
      "leave every store row trending and the team total silently not")
check("D4", "m.trend" in PAGE_CODE or "a.trend" in PAGE_CODE,
      "the page does not read a served trend")
# The frontend must not re-derive the projection. These are the shapes it would take.
for banned in ("days_in_month /", "/ elapsed", "daysInMonth", "* 30 /", "monthEndFactor"):
    check(f"D5:{banned}", banned not in PAGE_CODE and banned not in TILES_CODE,
          f"the screen computes its own month-end projection (`{banned}`) — the server decides it")
check("D6", "trendState(" in PAGE_CODE,
      "the page does not use the shared trend verdict, so its words can drift from the mailed copy")

# ── §E  THE HONESTY ───────────────────────────────────────────────────────────────────────────────
check("E1", "'not_measured'" in HOME_CODE,
      "the home has no not-measured state, so an unprojectable metric must read as something else")
check("E2", "'no_target'" in HOME_CODE,
      "the home has no no-target state — 'target met' for a scope with no target is the 2026-09-10 "
      "defect and a trend must not reintroduce it")
_V = HOME_CODE[HOME_CODE.find("def verdict("):]
_V = _V[:_V.find("\ndef ")] if "\ndef " in _V else _V
check("E3", re.search(r"if tgt <= 0:\s*\n\s*return \{'kind': 'no_target'", _V) is not None,
      "verdict() does not refuse a non-positive target FIRST — it could return on_track for one")
check("E4", _V.find("'no_target'") < _V.find("projected is None"),
      "verdict() tests the projection before the target, so a scope with no target and no "
      "projection would be reported as not_measured rather than as having no target")
check("E5", "if not w.get('complete')" in HOME_CODE,
      "project() no longer refuses an incomplete window — a run rate off a part-day is not a trend")
check("E6", "'future'" in HOME_CODE,
      "the home no longer distinguishes a FUTURE month, so it would divide next month's figures by "
      "this month's elapsed days")
check("E7", "'not_measured'" in STATE_CODE and "OUTLIER_COLOR" in STATE_CODE,
      "target-state.ts does not carry the trend verdict and the outlier colour")
_TS = STATE_CODE[STATE_CODE.find("export function trendState("):]
_TS = _TS.split("\nexport ")[0]
_last_return = _TS.rstrip().rsplit("return ", 1)[-1]
check("E8", "passing: false" in _last_return,
      "trendState does not FALL THROUGH to a non-passing state — an unrecognised kind from a server "
      "that grew a fifth state could make the screen claim a scope is fine")
check("E9", "partial" in ENGINE_CODE,
      "the team trend does not report that it is partial, so a floor would print as a total")

# ── §F  ONE OUTLIER RULE, ON THE SERVER ───────────────────────────────────────────────────────────
check("F1", "def outliers(" in HOME_CODE, "the outlier rule has no home")
check("F2", "'too_few'" in HOME_CODE,
      "the rule no longer refuses a pack too small to be a distribution")
check("F3", "_median(" in HOME_CODE and "mean" not in HOME_CODE.lower().replace("meaning", ""),
      "the outlier rule is not median-based — a mean is dragged by the very figure being looked for")
check("F4", "'equal_pack'" in HOME_CODE,
      "a pack with zero spread would make every unequal figure an outlier")
check("F5", "'outliers'" in ENGINE_CODE,
      "the roll-up does not serve the outliers, so a screen would have to decide them")
check("F6", "_month_trend.outliers(" in ENGINE_CODE,
      "the roll-up hand-rolls its outlier rule instead of dereferencing the home")
# No second rule in TypeScript.
# Reading the server's `outliers.median` is the point; COMPUTING one is the defect. So the ban is on
# the shapes a second rule would take, never on the word — a ban on the word would also forbid
# printing the figure the server sent, which is how a surface explains itself.
for banned in ("function median", "const median =", "medianOf", "deviation", "quantile",
               "stdDev", "standardDeviation"):
    check(f"F7:{banned}", banned not in PAGE_CODE and banned not in TILES_CODE,
          f"the screen computes its own outlier rule (`{banned}`) — the server decides who is one")
check("F7:read", "outliers?.median" in PAGE_CODE or "outliers.median" in PAGE_CODE,
      "the page does not print the median it was served, so a highlighted store comes with no "
      "stated basis a manager could argue with")
check("F8", "OUTLIER_COLOR" in PAGE_CODE,
      "the page does not use the shared outlier colour, so 'a different colour' means two things")

# ── §G  ONE FOCUS PREDICATE ───────────────────────────────────────────────────────────────────────
check("G1", "export function focusPlan(" in TILES_CODE,
      "the detail's narrowing does not live beside the tile fold")
check("G2", "focusPlan(" in PAGE_CODE,
      "the page does not narrow the detail through the one home — the tiles and the detail can "
      "disagree about what 'focused' means")
check("G3", "metricKey('store'" in TILES_CODE and "metricKey('kpi'" in TILES_CODE,
      "focusPlan does not match on the metric's GRAIN, so a target category and a KPI sharing a "
      "word would pick each other up")
check("G4", "at_risk" not in TILES_CODE.split("export function focusPlan(")[-1],
      "focusPlan narrows the at-risk money — a tier is earned across all of a rep's KPIs, so a "
      "payout attributed to one of them is a figure nobody computed")

# ── §H  THE TESTS RUN ─────────────────────────────────────────────────────────────────────────────
check("H1", UNIT.exists(), "the home's unit tests are missing")
check("H2", "pytest.mark.unit" in text(UNIT),
      "the home's unit tests are not marked `unit`, so CI's unit job does not run them")
check("H3", "trend" in text(INTEG),
      "no integration test presses the endpoint for the trend")
check("H4", (FE / "lib" / "__tests__" / "trend-state.test.ts").exists(),
      "the trend verdict's own tests are missing")
check("H5", "focusPlan" in text(FE / "lib" / "__tests__" / "action-plan-tiles.test.ts"),
      "focusPlan is not proved beside the fold it belongs to")
_wf = "\n".join(text(p) for p in sorted(WORKFLOWS.glob("*.yml")))
check("H6", "harness_month_trend_one_home" in _wf,
      "THIS lock is not wired into a workflow, so nothing above fails the build")

# ── §I  NEGATIVE CONTROLS ─────────────────────────────────────────────────────────────────────────
# Each scan above is shown to be capable of FAILING. A guard that cannot fail is not a guard, and
# this file is the only thing standing between the factoring and a quiet third copy.
check("I1", bool(_DIV.search("x = total / elapsed")),
      "the division scan does not match the shape it bans")
check("I2", not _DIV.search("x = total / days_open"),
      "the division scan matches an unrelated divisor, so it would block honest code")
check("I3", bool(_FACTOR_ASSIGN.search("    trend_factor = (dim / elapsed) if elapsed else 1.0")),
      "the inline-factor scan does not match the literal line that was removed")
check("I4", strip_py_comments("# trend_factor = dim / elapsed\nx = 1").strip() == "x = 1",
      "the Python comment stripper leaves prose in, so a ban could fire on an explanation of itself")
check("I5", strip_ts_comments("// a / elapsed\nconst x = 1").strip() == "const x = 1",
      "the TypeScript comment stripper leaves prose in")
check("I6", strip_py_comments('"""a / elapsed"""\nx = 1').strip().endswith("x = 1"),
      "the Python stripper leaves docstrings in, so documenting the rule would break the rule")
check("I7", "def outliers(" not in strip_py_comments("# def outliers(x)\n"),
      "a commented-out definition would satisfy the export checks")
check("I8", not (APP / "modules" / "commcalc" / "month_trend_v2.py").exists(),
      "a sibling month_trend_v2.py exists — the second copy this lock exists to prevent")
check("I9", len(_PROSE) == 1 and next(iter(_PROSE)).name == "module_graph.py",
      "the prose exclusion covers more than the registry itself, which would be a hole in B1")
check("I10", _mg.home_under_app(FACT).endswith("month_trend.py"),
      "module_graph no longer names month_trend.py as this fact's home, so every path above is "
      "being read from a graph that has moved on")

print(f"\nharness_month_trend_one_home: {PASSES} checks passed, {len(FAILS)} failed")
if FAILS:
    print("\nFAILURES")
    for f in FAILS:
        print(f"  ✗ {f}")
    sys.exit(1)
print("OK — one answer to what this month is trending to, and every caller reads it.")
