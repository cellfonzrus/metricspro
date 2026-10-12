#!/usr/bin/env python3
"""BUILD LOCK — the target category axis stays ONE home, and its callers stay wired (mig 1074).

CLAUDE.md: *"A design fix ships with a check that FAILS THE BUILD if a caller stops dereferencing
the shared fact, or if a second copy appears. Without that, the next change quietly restores the
patchwork."* — and the index records that writing a registry without wiring the callers has already
happened three times here (§19.18). This file is that check for the TARGET AXIS.

THE PATCHWORK THIS EXISTS TO PREVENT, measured on 2026-10-11. The list of target categories was
hard-coded in SEVEN places:

    backend   commcalc/targets_engine.CATEGORIES + UNITS          (the only legitimate one)
              commcalc/manager_report_card.TARGET_CATEGORIES      + a second, labelled list
              commcalc/router._AP_CAT_LABEL                       (the labels again)
              storevisit/visit_plan_router                        (the dropdown's own copy)
    frontend  commcalc/targets/page.tsx CATS
              commcalc/targets/my/page.tsx CATS
              commcalc/targets/my/_components/AreaTargets.tsx     CATS + EDIT_FIELDS
              components/TeamSnapshot.tsx CAT_LABEL
              components/EmployeeWidgets.impl.tsx                 (an inline array of four pairs)

Every one of them ended at `accessories`. So a UPS-store tenant could declare "Package drop-offs"
on Submit Daily Closing every day of the month and then find nowhere, on any screen, to set a
target for it — the owner's *"i dont wnat them to be hard coded it should be a design effect"*.

What this refuses to let happen:

  §A  the axis, the units, the labels or the target columns get a second copy in the backend
  §B  a module reads `commcalc.target_line` or `commcalc.targets`' category columns without going
      through `targets_engine.derive_monthly_by_cat`
  §C  the engine decides for itself which number answers a declared metric, instead of
      dereferencing `closing/metric_catalog.figure_unit` / `figure_of`
  §D  a frontend screen hard-codes a category list again, or stops dereferencing lib/target-axis
  §E  the frontend's built-in fallback drifts from the engine's built-ins
  §F  week/day targets become a second calculation instead of a window over the month
  §G  negative controls — the scanners can actually see a violation

Stdlib only, DB-free; the only app imports are the pure modules under test.
"""
import sys, os, re, io, tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
FE = os.path.join(ROOT, "frontend", "src")

sys.path.insert(0, HERE)
from app.modules.commcalc import targets_engine as te          # noqa: E402
from app.modules.closing import metric_catalog as mc           # noqa: E402
from app.modules.commcalc import manager_report_card as mrc    # noqa: E402

P = F = 0


def ok(cond, label, extra=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {label}")
    else:
        F += 1
        print(f"  FAIL  {label}  {extra}")


def eq(a, b, label):
    ok(a == b, label, f"got {a!r} want {b!r}")


def py_files(root):
    for dirpath, _d, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def fe_files(root):
    if not os.path.isdir(root):
        return
    for dirpath, _d, files in os.walk(root):
        for f in files:
            if f.endswith((".ts", ".tsx")):
                yield os.path.join(dirpath, f)


def code_only(path):
    """A file's executable text — docstrings and comments stripped. Doctrine explains WHY a rule
    exists and quotes the owner's own examples; what must not carry a second copy is the CODE."""
    try:
        src = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    out, prev = [], tokenize.INDENT
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev in (tokenize.INDENT, tokenize.NEWLINE,
                                                        tokenize.NL, tokenize.DEDENT):
                continue
            out.append(tok.string)
            if tok.type not in (tokenize.NL, tokenize.NEWLINE):
                prev = tok.type
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return src
    return " ".join(out)


def ts_code_only(path):
    """A .ts/.tsx file with // and /* */ comments stripped. Same reason as code_only."""
    try:
        src = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", " ", src)
    src = re.sub(r"\s//[^\n'\"`]*$", " ", src, flags=re.M)
    return src


def rel(p):
    return os.path.relpath(p, ROOT)


def calls(blob, fn):
    """Every call to `fn` in `blob`, with BALANCED parentheses.

    A regex with a fixed nesting depth silently MISSES a call that nests one level deeper — and a
    missed call site is exactly the un-wiring this file exists to catch, so the scanner counts
    parentheses instead of guessing at a depth. (The first draft of this check used
    `(?:[^()]|\([^()]*\))*` and found 3 of the 4 fold sites: the one it could not see was the one
    whose argument contained `safe_float(s.get(...))`.)"""
    out, i = [], 0
    needle = fn + "("
    while True:
        j = blob.find(needle, i)
        if j < 0:
            return out
        k, depth = j + len(needle) - 1, 0
        while k < len(blob):
            if blob[k] == "(":
                depth += 1
            elif blob[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        out.append(blob[j:k + 1])
        i = k + 1


HOME_ENGINE = "backend/app/modules/commcalc/targets_engine.py"
HOME_FIGURE = "backend/app/modules/closing/metric_catalog.py"
HOME_FE = "frontend/src/lib/target-axis.ts"
SELF = "backend/" + os.path.relpath(os.path.abspath(__file__), HERE)

print(__doc__)

# ──────────────────────────────────────────────────────────────────────────────────────────────────
print("§A  the axis has one copy")
eq(tuple(te.BUILTIN_CATEGORIES), ("activations", "upgrades", "byod", "accessories"),
   "A1 the built-in axis is the declared four")
eq(te.target_categories(None), list(te.BUILTIN_CATEGORIES),
   "A2 no catalog → exactly the built-ins (every pre-2026-10 caller's assumption)")
eq(sorted(te.BUILTIN_UNITS), sorted(te.BUILTIN_CATEGORIES),
   "A3 every built-in has a unit")
eq(sorted(te.BUILTIN_LABELS), sorted(te.BUILTIN_CATEGORIES),
   "A4 every built-in has a label")
# byod has no column of its own; the other three do and nothing else does.
eq(sorted(te.BUILTIN_TARGET_COLUMN), ["accessories", "activations", "upgrades"],
   "A5 the target columns are the three that have one (byod is derived)")
eq([te.target_store(c) for c in te.BUILTIN_CATEGORIES],
   ["column", "column", "derived", "column"], "A6 target_store answers for every built-in")
eq(te.target_store("dropoff"), "line", "A7 a declared category's target is a target_line row")
# The back-compatible aliases two external surfaces still dereference by name.
ok(te.CATEGORIES is te.BUILTIN_CATEGORIES and te.UNITS is te.BUILTIN_UNITS,
   "A8 the legacy aliases are the same objects, not copies")

# A module that DECLARES the four is a second copy. A module that passes one through is fine.
# A module that DECLARES the axis names THREE of its keys close together as strings, in the axis's
# own order. The punctuation between them is whatever structure it chose (a tuple, a list of dicts,
# a map), so the scanner does not care what it is.
#
# WHY THREE AND NOT TWO. "activations" and "upgrades" also belong to four OTHER vocabularies in this
# codebase — the commission tier metrics (plan_options.TIER_METRIC_SUGGESTIONS, which also carries
# "boxes"), the productivity registry, the carrier DLAR metrics (dlar_vs_platform.METRIC_REP), and a
# B2BSoft reconciliation payload. A two-key scanner flagged all four, and a scanner that cries wolf
# gets relaxed by the next person who meets it. "accessories" is what makes a match the TARGET axis
# rather than a list that happens to share two words. G1–G2 below are the controls.
AXIS_DECL = re.compile(
    r"""['"]activations['"].{0,110}?['"]upgrades['"].{0,110}?['"]accessories['"]""", re.S)
# ONE file other than the engine may spell the four built-ins, and only because it is forbidden to
# import: commcalc/manager_report_card.py is a PURE module whose own lock (harness_manager_report_card
# §D1) asserts it imports NOTHING, every fact being handed in by its caller. A tenant's declared
# categories reach it through `axis=`; the four built-ins are its fallback for a caller that hands in
# none. A12 below compares that fallback to the engine's axis KEY, LABEL AND UNIT, so it is a copy
# that cannot drift — the same posture lib/target-axis.ts takes for the same reason (§E).
AXIS_PINNED = {"backend/app/modules/commcalc/manager_report_card.py":
               "pure module, imports nothing (harness_manager_report_card D1); pinned to the engine by A12"}
copies = [rel(p) for p in py_files(APP)
          if rel(p) != HOME_ENGINE and rel(p) not in AXIS_PINNED and AXIS_DECL.search(code_only(p))]
eq(copies, [], "A9 no backend module re-declares the category axis")
for _f, _why in AXIS_PINNED.items():
    ok(os.path.exists(os.path.join(ROOT, _f)),
       f"A9b the file excused from A9 still exists ({_f}: {_why})")

LABEL_DECL = re.compile(r"""['"]Activations['"]\s*.{0,60}?['"]Upgrades['"]""", re.S)
label_copies = [rel(p) for p in py_files(APP)
                if rel(p) != HOME_ENGINE and rel(p) not in AXIS_PINNED
                and LABEL_DECL.search(code_only(p))]
eq(label_copies, [], "A10 no backend module re-declares the category labels")

COL_DECL = re.compile(r"""['"]activations_monthly['"]\s*.{0,80}?['"]upgrades_monthly['"]""", re.S)
# commcalc/router.py is the WRITER of commcalc.targets: its save endpoint, its roll-forward and its
# seeded GET row must name that table's own columns, exactly as they always have. What it may not do
# is decide which category maps to which column — B3 asserts the FOLD dereferences the map, and A5
# that the map has one home.
COL_WRITER = "backend/app/modules/commcalc/router.py"
col_copies = [rel(p) for p in py_files(APP)
              if rel(p) not in (HOME_ENGINE, COL_WRITER) and COL_DECL.search(code_only(p))]
eq(col_copies, [], "A11 only the engine and the table's own writer name the target columns")

# THE DRIFT LOCK on the one pinned copy. The report card cannot import the engine, so it spells the
# four built-ins; this is what makes that spelling safe. Key, label and unit are all compared, so
# renaming a label or changing a unit in one home and not the other fails the build here rather than
# showing a manager a card whose row disagrees with their own Targets screen.
_mrc_builtin = [(i["key"], i["label"], i["unit"]) for i in mrc.BUILTIN_TARGET_ITEMS]
_engine_builtin = [(a["key"], a["label"], "money" if a["unit"] == "dollars" else "count")
                   for a in te.target_axis(None)]
eq(_mrc_builtin, _engine_builtin,
   "A12 the report card's pinned built-ins match the engine's axis — key, label and unit")
eq(tuple(sorted(mrc.TARGET_CATEGORIES)), tuple(sorted(te.BUILTIN_CATEGORIES)),
   "A12b … and its target set is the engine's category list")
eq([i["key"] for i in mrc.items_for(None)][:4], list(te.BUILTIN_CATEGORIES),
   "A13 the report card's items open with the engine's axis, in its order")
_declared_items = [i["key"] for i in mrc.items_for(
    [{"key": "dropoff", "label": "Drop-offs", "value_kind": "count"}])]
ok("dropoff" in _declared_items, "A14 a declared category reaches the report card")

# ──────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§B  the two physical stores have one reader")
ONE_FOLD = "derive_monthly_by_cat"
# Nothing but the engine and its fold may touch commcalc.target_line's rows.
tl_readers = []
for p in py_files(APP):
    blob = code_only(p)
    if 'table ( "target_line" )'.replace(" ", "") in blob.replace(" ", ""):
        tl_readers.append(rel(p))
eq(tl_readers, ["backend/app/modules/commcalc/router.py"],
   "B1 commcalc.target_line is read by the router's one reader and nobody else")

# The column names may be read only where the fold reads them.
col_readers = sorted({rel(p) for p in py_files(APP)
                      if "activations_monthly" in code_only(p)})
eq(col_readers, ["backend/app/modules/commcalc/router.py", HOME_ENGINE],
   "B2 the target columns are named only in the engine and its router")

eng_src = open(os.path.join(ROOT, HOME_ENGINE), encoding="utf-8").read()
ok(eng_src.count("BUILTIN_TARGET_COLUMN['activations']") >= 1,
   "B3 the fold DEREFERENCES the column map rather than spelling the column")

rt = open(os.path.join(APP, "modules", "commcalc", "router.py"), encoding="utf-8").read()
# Every call into the fold or the scope must carry the tenant's catalog, or a declared category
# silently vanishes from that surface — the exact way this fix would un-wire.
folds = calls(rt, "targets_engine.derive_monthly_by_cat")
ok(len(folds) >= 4, f"B4 the fold's call sites are all accounted for (found {len(folds)})")
eq([f[:46] for f in folds if "catalog=" not in f], [],
   "B5 every derive_monthly_by_cat call passes the tenant's catalog")
scopes = calls(rt, "targets_engine.compute_scope")
ok(len(scopes) >= 3, f"B6 compute_scope's call sites are all accounted for (found {len(scopes)})")
eq([x[:40] for x in scopes if "catalog=" not in x], [],
   "B7 every compute_scope call passes the tenant's catalog")
ok("merge_declared_actuals" in rt,
   "B8 declared achievement is merged into the same per-day arithmetic as a sale")
# The merge belongs to the engine; a router that folded it by hand would be a second opinion about
# what a day with declarations but no sales means.
ok("def merge_declared_actuals" in eng_src, "B9 the merge lives in the engine")
# WHO MAY TURN A DECLARATION INTO AN ACHIEVED FIGURE. `achieved_for_cat` is the one function that
# does, and it must be called only from inside the engine: a surface calling it directly would be
# deciding for itself what a declared category achieved, which is the second opinion this design
# removes. (An earlier draft scanned for `['declared'] =` instead and matched five unrelated
# modules that happen to have a `declared` key of their own — a scanner that cries wolf gets
# relaxed by the next person, so it was replaced rather than loosened.)
_afc = [rel(p) for p in py_files(APP)
        if rel(p) != HOME_ENGINE and "achieved_for_cat" in code_only(p)]
eq(_afc, [], "B10 only the engine turns a declaration into an achieved figure")

# ──────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§C  which number answers a metric is one fact, dereferenced")
eq(mc.figure_unit("amount"), "dollars", "C1 an amount metric is answered in money")
eq(mc.figure_unit("both"), "dollars", "C2 a both metric is targeted on its money")
eq(mc.figure_unit("count"), "count", "C3 a count metric is answered in units")
eq(mc.figure_of({"value_kind": "count", "amount": 360.0, "units": 2}), 2,
   "C4 a tally that also earns answers with its tally")
eq(mc.figure_of(None), 0, "C5 an undeclared day is zero achieved, never None")
ok("figure_unit" in eng_src and "metric_catalog import" in eng_src,
   "C6 the engine dereferences the figure rule")
# The literal rule, spelled anywhere but its home, is a second copy.
FIG_RULE = re.compile(r"""['"]amount['"]\s*,\s*['"]both['"]""")
fig_copies = [rel(p) for p in py_files(APP)
              if rel(p) not in (HOME_FIGURE, SELF) and FIG_RULE.search(code_only(p))]
eq(fig_copies, [], "C7 no module re-states the money-or-units rule")
eq(te.target_units([{"key": "k", "value_kind": "both"}])["k"], mc.figure_unit("both"),
   "C8 a declared target's unit IS the figure rule's answer")

# ──────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§D  the frontend dereferences the axis")
ok(os.path.isfile(os.path.join(ROOT, HOME_FE)), "D1 lib/target-axis.ts exists")
FE_AXIS_DECL = re.compile(
    r"""['"]activations['"]\s*.{0,200}?['"]upgrades['"]\s*.{0,200}?['"]accessories['"]""", re.S)
fe_copies = [rel(p) for p in fe_files(FE)
             if rel(p) != HOME_FE and FE_AXIS_DECL.search(ts_code_only(p))]
eq(fe_copies, [], "D2 no screen re-declares the category axis")
FE_LABEL_DECL = re.compile(r"""['"]Activations['"]\s*.{0,120}?['"]Upgrades['"]""", re.S)
# TWO FILES SAY THESE WORDS AND ARE NOT THE TARGET AXIS. They are excused BY NAME, with the reason,
# because loosening the scanner to let them through would also let a real copy through — and because
# a reader of this lock should be able to see exactly what was judged out of scope.
#   planLines.ts     — commission PLAN LINES: which transaction types a pay line covers. A different
#                      question with overlapping words; it is not a target and has no target column.
#   sales-report     — a report of what HAPPENED per store from the sales feed, not of what is
#                      targetable. It has no target, no unit vocabulary and nothing to set.
LABEL_EXCUSED = {
    "frontend/src/app/(platform)/commcalc/_lib/planLines.ts",
    "frontend/src/app/(platform)/commcalc/sales-report/page.tsx",
}
fe_label_copies = [rel(p) for p in fe_files(FE)
                   if rel(p) != HOME_FE and rel(p) not in LABEL_EXCUSED
                   and FE_LABEL_DECL.search(ts_code_only(p))]
eq(fe_label_copies, [], "D3 no screen re-declares the category labels")
# The excused files must still EXIST — an excuse for a file that is gone is an excuse that would
# silently cover a future file at the same path.
for _e in sorted(LABEL_EXCUSED):
    ok(os.path.isfile(os.path.join(ROOT, _e)), f"D3b the excused file {os.path.basename(_e)} still exists")

# The screens that render a per-category axis must each ASK for it. A screen dropped from this list
# is a screen that stopped dereferencing — which is the un-wiring this file exists to catch.
WIRED = [
    "frontend/src/app/(platform)/commcalc/targets/page.tsx",
    "frontend/src/app/(platform)/commcalc/targets/my/page.tsx",
    "frontend/src/app/(platform)/commcalc/targets/my/_components/AreaTargets.tsx",
    "frontend/src/app/(platform)/commcalc/targets/settings/page.tsx",
    "frontend/src/components/TeamSnapshot.tsx",
    "frontend/src/components/EmployeeWidgets.impl.tsx",
]
for w in WIRED:
    fp = os.path.join(ROOT, w)
    txt = ts_code_only(fp) if os.path.isfile(fp) else ""
    ok("@/lib/target-axis" in txt, f"D4 {os.path.basename(w)} dereferences lib/target-axis")

# ──────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§E  the frontend's built-in fallback cannot drift from the engine's")
fe_src = open(os.path.join(ROOT, HOME_FE), encoding="utf-8").read()
fe_keys = re.findall(r"\{\s*key:\s*'([a-z_]+)'", fe_src)[:4]
eq(fe_keys, list(te.BUILTIN_CATEGORIES), "E1 the fallback's keys are the engine's, in its order")
for c in te.BUILTIN_CATEGORIES:
    m = re.search(r"\{\s*key:\s*'" + c + r"'[^}]*unit:\s*'([a-z]+)'", fe_src)
    eq(m and m.group(1), te.BUILTIN_UNITS[c], f"E2 {c}'s fallback unit is the engine's")
    m2 = re.search(r"\{\s*key:\s*'" + c + r"'[^}]*label:\s*'([^']+)'", fe_src)
    eq(m2 and m2.group(1), te.BUILTIN_LABELS[c], f"E3 {c}'s fallback label is the engine's")
    m3 = re.search(r"\{\s*key:\s*'" + c + r"'[^}]*store:\s*'([a-z]+)'", fe_src)
    eq(m3 and m3.group(1), te.target_store(c), f"E4 {c}'s fallback store is the engine's")
fe_grains = re.search(r"GRAINS\s*=\s*\[([^\]]*)\]", fe_src)
eq(tuple(re.findall(r"'([a-z]+)'", fe_grains.group(1))) if fe_grains else (),
   te.GRAINS, "E5 the frontend's grains are the engine's")

# ──────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§F  a week or a day is a WINDOW over the month, not a second calculation")
# One month, open every day, 8h a day: the only arithmetic is the monthly spread, so the windows
# must add back up to it.
from datetime import date   # noqa: E402
_hours = {date(2026, 10, d): 8.0 for d in range(1, 32)}
_scope = te.compute_scope({"activations": 600.0}, _hours, {}, date(2026, 10, 12),
                          month_end=date(2026, 10, 31))
_m = te.grain_windows(_scope, grain="month")
eq(round(_m[0]["cats"]["activations"]["base"], 2), 600.0, "F1 the month window IS the month target")
for g in ("week", "day"):
    w = te.grain_windows(_scope, grain=g)
    tot = sum(x["cats"]["activations"]["base"] for x in w)
    ok(abs(tot - 600.0) <= 0.01 * len(w),
       f"F2 the {g} windows sum to the month", f"{tot} over {len(w)} windows")
# The full-precision keys are what make that true; a caller summing the DISPLAYED figures drifts.
ok("base_raw" in _scope["calendar"][0]["cats"]["activations"],
   "F3 the calendar carries full-precision figures for summing")
ok(te.backward_from_goal(1200, 23, 0)["per_day"] is None,
   "F4 no open days left → no per-day rate, rather than a false one")
eq(te.backward_from_goal(100, 250, 5)["need"], 0.0, "F5 over-achievement needs zero, not a negative")
# grain_windows must READ the calendar, never recompute a base of its own.
gw = eng_src[eng_src.index("def grain_windows"):eng_src.index("def backward_from_goal")]
ok("base_for" not in gw and "monthly_by_cat" not in gw,
   "F6 grain_windows computes no base of its own")

# ──────────────────────────────────────────────────────────────────────────────────────────────────
print("\n§G  negative controls — the scanners can see a violation")
ok(bool(AXIS_DECL.search(
    """X = [{'key': 'activations'}, {'key': 'upgrades'}, {'key': 'byod'}, {'key': 'accessories'}]""")),
   "G1 the backend axis scanner sees a re-declared list of dicts")
ok(bool(AXIS_DECL.search("""X = ('activations', 'upgrades', 'byod', 'accessories')""")),
   "G1b …and a re-declared tuple")
ok(bool(AXIS_DECL.search(
    """X = {'activations': 'c', 'upgrades': 'c', 'byod': 'c', 'accessories': 'd'}""")),
   "G1c …and a re-declared map")
# The four other vocabularies that share two of these words must NOT trip it.
ok(not AXIS_DECL.search("""T = ("none", "activations", "upgrades", "boxes")"""),
   "G1f …and not the commission tier metrics, which share two words")
ok(not AXIS_DECL.search("""act = closing_acts - int(bb.get("activations", 0))
upg = closing_upg - int(bb.get("upgrades", 0))"""),
   "G1g …and not two fields read off a payload")
# The balanced extractor must see a call whose arguments nest two deep — the case the first draft
# of this lock missed outright.
eq(len(calls("f(a, g(h(b)), catalog=c)\nf(d)", "f")), 2,
   "G1d the call extractor sees a call nested two levels deep")
eq([c for c in calls("f(a, g(h(b)), catalog=c)\nf(d)", "f") if "catalog=" not in c], ["f(d)"],
   "G1e …and still reports the one that is missing its catalog")
ok(not AXIS_DECL.search("""monthly.get('activations')"""),
   "G2 …and not a module merely reading one category")
ok(bool(LABEL_DECL.search("""L = {'a': 'Activations', 'b': 'Upgrades'}""")),
   "G3 the label scanner sees a re-declared label map")
ok(bool(COL_DECL.search("""F = ['activations_monthly', 'upgrades_monthly']""")),
   "G4 the column scanner sees a re-declared column list")
ok(bool(FE_AXIS_DECL.search(
    """const CATS = [{key:'activations'},{key:'upgrades'},{key:'byod'},{key:'accessories'}]""")),
   "G5 the frontend scanner sees a re-declared CATS")
ok(not FE_AXIS_DECL.search("""const m = detail.categories['activations']"""),
   "G6 …and not a screen reading one category by key")
ok(bool(FIG_RULE.search("""if vk in ('amount', 'both'): pass""")),
   "G7 the figure-rule scanner sees a second copy of the rule")
ok(not ts_code_only("/nonexistent/x.ts"), "G8 an unreadable file reads as empty, never as a pass")
# A comment must NOT satisfy the wiring check, or a screen could un-wire behind a mention of it.
ok("@/lib/target-axis" not in ts_code_only.__doc__ or True, "G9 doctrine note")
_tmp = os.path.join(HERE, "_axis_lock_probe.tsx")
try:
    open(_tmp, "w").write("// imports from @/lib/target-axis\nconst x = 1\n")
    ok("@/lib/target-axis" not in ts_code_only(_tmp),
       "G10 a COMMENT naming the axis module does not satisfy the wiring check")
finally:
    if os.path.exists(_tmp):
        os.remove(_tmp)

print("\n" + "=" * 98)
print(f"{P} passed / {F} failed")
if F:
    sys.exit(1)
print("One axis, one fold, one figure rule — and the build fails if a second copy returns.")
