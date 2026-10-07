"""LOCK — there is ONE appeal state machine, and every writer of an appeal column dereferences it
(index §55, mig 1060, owner ask 2026-10-06).

THE CLASS THIS PREVENTS. `commcalc.flags` (mig 1060) now carries the same four appeal columns
`commcalc.discrepancy_results` has carried since mig 947. Two tables holding one workflow is exactly
how a second truth table gets written: the next author adds a transition to one surface, the other
surface keeps the old rule, and the two quietly disagree about whether a denied appeal may be
re-filed. CLAUDE.md names it — *"one fact, one home, dereferenced — never copied"* — and adds that
writing the shared home without WIRING the callers to it has happened here three times, so the
wiring is what this lock checks, not the existence of the module.

THE DESIGN. `commcalc/discrepancy_appeals.py` holds the states, the transition table, the patch
builder and the note clamp. Both PATCH endpoints call `apply_appeal`, and the column names match
byte for byte so no translation layer exists to drift either.

WHAT THIS FAILS THE BUILD ON (stdlib + ast; no DB, no network):
  A. ONE MACHINE. `APPEAL_STATES` and `ALLOWED_TRANSITIONS` are defined exactly once under
     backend/app, in the one home; `validate_transition` / `apply_appeal` / `allowed_next` likewise.
  B. NO SECOND TRANSITION TABLE. No module outside the one home declares a dict whose keys and
     values are appeal-state strings, and nothing outside it compares a variable to an appeal-state
     literal in a way that re-decides a transition.
  C. EVERY WRITER IS WIRED. Every assignment of `appeal_status` under backend/app (a dict-literal
     key, a `['appeal_status'] =`, or an `appeal_status=` keyword) occurs in a function that also
     calls `apply_appeal` — so no endpoint can set the state by hand.
  D. THE COLUMNS MATCH. The four column names migration 1060 adds to commcalc.flags are exactly
     the four migration 947 added to discrepancy_results, and exactly the four keys `apply_appeal`
     emits. A mismatch in any direction fails.
  E. NEGATIVE CONTROLS. A second transition table, a hand-set state, a renamed column and a
     bypassed patch builder are each planted and must be caught; a legitimate call and a display
     label must NOT be.

Run: python3 backend/harness_appeal_one_machine_lock.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
APP = os.path.join(HERE, "app")

# THE GRAPH IS THE ONE HOME FOR THE RULING (index §19.18 / §50). This lock does not carry its own
# literal: `module_graph` says which file is allowed to answer this question, and this reads it. A
# home moved there moves here with no edit.
from app.modules.core.module_graph import home_under_app   # noqa: E402
MIGS = os.path.normpath(os.path.join(HERE, "..", "database", "migrations"))

ONE_HOME = os.path.join(APP, home_under_app("appeal_state_machine"))
APPEAL_COLS = ("appeal_status", "appeal_note", "appealed_by", "appealed_at")

FAILURES = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILURES.append(name)


def py_files(root):
    for base, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


from app.modules.commcalc.discrepancy_appeals import (            # noqa: E402
    APPEAL_STATES, ALLOWED_TRANSITIONS, apply_appeal, validate_transition)

STATE_WORDS = set(APPEAL_STATES)


# ── detectors ───────────────────────────────────────────────────────────────────────────────────
def top_level_names(src):
    """{name: count} of module-level assignments and function definitions."""
    out = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = out.get(node.name, 0) + 1
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = out.get(t.id, 0) + 1
    return out


def transition_tables(src):
    """Lines declaring a dict that MAPS appeal states to appeal states — a transition table."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        vals = []
        for v in node.values:
            if isinstance(v, (ast.Tuple, ast.List, ast.Set)):
                vals += [e.value for e in v.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            elif isinstance(v, ast.Constant) and isinstance(v.value, str):
                vals.append(v.value)
        if (set(keys) & STATE_WORDS) and (set(vals) & STATE_WORDS):
            out.append(getattr(node, "lineno", 0))
    return out


def appeal_status_writers(src):
    """Every function in `src` that SETS appeal_status, as (func_name, line). A module-level write
    is reported under '<module>'."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    owner = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(fn):
                owner[id(sub)] = fn
    hits = []

    def _name(node):
        fn = owner.get(id(node))
        return fn.name if fn is not None else "<module>"

    def _is_passthrough(value):
        """True when the value simply COPIES appeal_status off another object — a read for display,
        not a write of the state. `{"appeal_status": row.get("appeal_status")}` in a response payload
        is not an attempt to set the state, and catching it would force every reader to rename the
        field it is showing."""
        for sub in ast.walk(value):
            if isinstance(sub, ast.Constant) and sub.value == "appeal_status":
                return True
            if isinstance(sub, ast.Attribute) and sub.attr == "appeal_status":
                return True
        return False

    for node in ast.walk(tree):
        ln = getattr(node, "lineno", 0)
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "appeal_status" \
                        and not _is_passthrough(v):
                    hits.append((_name(k), ln))
        if isinstance(node, ast.keyword) and node.arg == "appeal_status":
            hits.append((_name(node), ln))
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                        and t.slice.value == "appeal_status":
                    hits.append((_name(node), ln))
                if isinstance(t, ast.Attribute) and t.attr == "appeal_status":
                    hits.append((_name(node), ln))
    return hits


def funcs_calling(src, fname):
    """Names of functions in `src` that call `fname` (bare or as an attribute)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call):
                f = sub.func
                if (isinstance(f, ast.Name) and f.id == fname) or \
                   (isinstance(f, ast.Attribute) and f.attr == fname):
                    out.add(fn.name)
    return out


# ── A. one machine ──────────────────────────────────────────────────────────────────────────────
HOME = read(ONE_HOME)
names = top_level_names(HOME)
for n in ("APPEAL_STATES", "ALLOWED_TRANSITIONS", "validate_transition", "apply_appeal",
          "allowed_next", "normalize_state"):
    check("A: the one home defines %s exactly once" % n, names.get(n) == 1)

dupes = []
for p in py_files(APP):
    if os.path.abspath(p) == os.path.abspath(ONE_HOME):
        continue
    nm = top_level_names(read(p))
    for n in ("APPEAL_STATES", "ALLOWED_TRANSITIONS", "validate_transition", "apply_appeal",
              "allowed_next"):
        if n in nm:
            dupes.append("%s defines %s" % (os.path.relpath(p, HERE), n))
check("A: nothing else under backend/app defines any of them%s"
      % ("" if not dupes else " — " + "; ".join(dupes[:5])), not dupes)

# ── B. no second transition table ───────────────────────────────────────────────────────────────
second = []
for p in py_files(APP):
    if os.path.abspath(p) == os.path.abspath(ONE_HOME):
        continue
    for ln in transition_tables(read(p)):
        second.append("%s:%s" % (os.path.relpath(p, HERE), ln))
check("B: no second appeal transition table%s"
      % ("" if not second else " — " + ", ".join(second[:5])), not second)
check("B: the one home DOES declare the only one", bool(transition_tables(HOME)))

# ── C. every writer is wired to the patch builder ───────────────────────────────────────────────
unwired = []
for p in py_files(APP):
    src = read(p)
    writers = appeal_status_writers(src)
    if not writers:
        continue
    wired = funcs_calling(src, "apply_appeal")
    rel = os.path.relpath(p, HERE)
    if os.path.abspath(p) == os.path.abspath(ONE_HOME):
        continue                       # apply_appeal IS the builder; it is the home
    for fn, ln in writers:
        if fn not in wired:
            unwired.append("%s:%s in %s()" % (rel, ln, fn))
check("C: every appeal_status writer calls the shared patch builder%s"
      % ("" if not unwired else " — unwired: " + "; ".join(unwired[:5])), not unwired)

# ── D. the columns match, in all three places ───────────────────────────────────────────────────
patch = apply_appeal("", "appeal_filed", "n", None, "2026-10-06T00:00:00Z")
check("D: the patch builder emits exactly the four appeal columns",
      set(patch) == set(APPEAL_COLS))
cleared = apply_appeal("appeal_filed", "", None, None, "2026-10-06T00:00:00Z")
check("D: clearing resets all four to NULL and touches nothing else",
      set(cleared) == set(APPEAL_COLS) and all(v is None for v in cleared.values()))
check("D: the patch builder never emits a money field",
      not ({"expected", "received", "gap", "amount", "withheld", "recovered"} & set(patch)))


def mig_cols(path, table):
    """The appeal column names an ALTER on `table` adds in `path`."""
    src = read(path).lower()
    i = src.find("alter table " + table)
    if i < 0:
        return set()
    seg = src[i:src.find(";", i)]
    return {c for c in APPEAL_COLS if c in seg}


m1060 = os.path.join(MIGS, "1060_flag_appeal_state.sql")
m947 = os.path.join(MIGS, "947_commission_discrepancy_hub.sql")
check("D: mig 1060 adds all four columns to commcalc.flags",
      mig_cols(m1060, "commcalc.flags") == set(APPEAL_COLS))
check("D: and they are byte-identical to the four mig 947 added to discrepancy_results",
      mig_cols(m1060, "commcalc.flags") == mig_cols(m947, "commcalc.discrepancy_results"))
check("D: mig 1060 declares no CHECK on appeal_status (the transitions are the state machine's)",
      "check (appeal_status" not in read(m1060).lower()
      and "check(appeal_status" not in read(m1060).lower())
check("D: mig 1060 is additive and idempotent",
      "add column if not exists" in read(m1060).lower()
      and "drop table" not in read(m1060).lower()
      and "-- REVERT:" in read(m1060))

# ── E. negative controls ────────────────────────────────────────────────────────────────────────
PLANT_CATCH = [
    ("a second transition table",
     "T = {'appeal_filed': ('appeal_won', 'written_off'), '': ('appeal_filed',)}\n",
     transition_tables),
    ("a flattened second table",
     "T = {'appeal_denied': 'appeal_filed'}\n", transition_tables),
]
for name, src, det in PLANT_CATCH:
    check("E: catches %s" % name, bool(det(src)))

HAND_SET = ("def patch(row, new):\n"
            "    return {'appeal_status': new, 'appeal_note': None}\n")
check("E: catches a hand-set appeal_status with no patch builder",
      bool(appeal_status_writers(HAND_SET))
      and not (set(fn for fn, _ in appeal_status_writers(HAND_SET))
                   & funcs_calling(HAND_SET, "apply_appeal")))
# A writer that goes through the patch builder leaves NOTHING unwired. Whether it also reads
# appeal_status back off the patch is immaterial — what the lock must never report is an unwired
# write in a function that calls the builder.
WIRED = ("def patch(row, new):\n"
         "    p = apply_appeal(row.get('appeal_status'), new, '', None, 'now')\n"
         "    return {'appeal_status': p['appeal_status']}\n")
check("E: reports nothing unwired for a writer that goes through the patch builder",
      not [fn for fn, _ in appeal_status_writers(WIRED)
           if fn not in funcs_calling(WIRED, "apply_appeal")])
# A label map is keyed BY state but its values are human text, not states, so it maps nothing to a
# transition. Catching it would force every surface to stop naming the states it displays.
check("E: does NOT catch a display label map keyed by state",
      not transition_tables(
          "LABELS = {'appeal_filed': 'Appeal filed', 'appeal_won': 'Carrier paid it'}\n"))
check("E: does NOT catch a bucket counter keyed by state",
      not appeal_status_writers("c = {'appeal_filed': 0, 'appeal_won': 0}\n"))
# A response payload that COPIES the stored state for display is a read, not a write. The report
# endpoint does exactly this, and catching it would force every reader to rename the field it shows.
check("E: does NOT catch a payload passing the stored state through for display",
      not appeal_status_writers(
          "def shape(row):\n    return {'appeal_status': row.get('appeal_status')}\n"))
check("E: STILL catches a write whose value does not come from the stored state",
      bool(appeal_status_writers(
          "def save(row, new):\n    return {'appeal_status': new}\n")))

print()
if FAILURES:
    print("%d FAILURE(S):" % len(FAILURES))
    for f in FAILURES:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
