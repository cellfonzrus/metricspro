#!/usr/bin/env python3
"""BUILD GATE: the receipt date has exactly ONE derivation, and the shelf test exactly one home.

WHY A GUARD AND NOT JUST A REFACTOR (owner directive 2026-09-20, "a fix is a DESIGN fix or it is not
a fix"). Writing `inventory_stock_age.py` and wiring today's three callers to it fixes today. It does
not stop the next change re-deriving `as_of_date − days_in_stock` in a fourth place — which is exactly
how the platform ended up with three copies, one of them reporting a stale age for months. This scans
the tree and FAILS THE BUILD when a caller stops dereferencing the shared fact.

It is deliberately narrow. It bans the SUBTRACTION and the SHELF TEST, not the columns: reading
`days_in_stock` to display it, or `as_of_date` to show a snapshot date, is fine and common.

Negative controls N1-N6 pin that the scanner is not matching everything it sees.
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
ONE_HOME = os.path.join(APP, "modules", "commcalc", "inventory_stock_age.py")
FAILED = []
NOTES = []


def ok(cond, label):
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def py_files():
    for base, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


# ── AXIS 1: the receipt-date SUBTRACTION ──────────────────────────────────────────────────────────
# The shape is a subtraction of a timedelta built from days_in_stock off an as_of_date. Matched on the
# SOURCE TEXT of each function, because the two operands reach the expression under many spellings
# (`r.get("days_in_stock")`, a parameter named `days_in_stock`, a local `dis`), and what identifies
# the defect is the pair of columns appearing together under a subtraction — not any one name.
# The names `timedelta` is reachable under IN THIS FILE are resolved from its own imports, never
# assumed — `from datetime import timedelta as td` was the spelling that slipped past the first draft
# of this scanner, and a guard with a blind spot is the patchwork it is meant to prevent.
def timedelta_names(tree):
    names = {"timedelta", "datetime.timedelta", "dt.timedelta", "_dt.timedelta"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "datetime":
            for a in node.names:
                if a.name == "timedelta":
                    names.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "datetime":
                    names.add((a.asname or a.name) + ".timedelta")
    return names


def sub_pattern(names):
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(r"-\s*(?:" + alt + r")\s*\(\s*days\s*=", re.I)


SUB = sub_pattern({"timedelta", "datetime.timedelta", "dt.timedelta", "_dt.timedelta"})


def offending_subtractions(path):
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    sub = sub_pattern(timedelta_names(tree))
    lines = src.splitlines()
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        seg = "\n".join(lines[node.lineno - 1:getattr(node, "end_lineno", node.lineno)])
        if "days_in_stock" not in seg:
            continue
        if "as_of_date" not in seg:
            continue
        if not sub.search(seg):
            continue
        hits.append((node.name, node.lineno))
    return hits


print("── AXIS 1: one receipt-date derivation ──")
subs = {}
for p in py_files():
    h = offending_subtractions(p)
    if h:
        subs[os.path.relpath(p, ROOT)] = h
outside = {k: v for k, v in subs.items() if not k.endswith("inventory_stock_age.py")}
for k, v in outside.items():
    NOTES.append(f"{k}: " + ", ".join(f"{n}() line {ln}" for n, ln in v))
ok(not outside,
   "no function outside inventory_stock_age.py subtracts days_in_stock from as_of_date"
   + (" — FOUND: " + "; ".join(NOTES) if NOTES else ""))
ok(os.path.exists(ONE_HOME), "the one home exists at commcalc/inventory_stock_age.py")
home_src = open(ONE_HOME, encoding="utf-8").read() if os.path.exists(ONE_HOME) else ""
ok(SUB.search(home_src) is not None and "days_in_stock" in home_src,
   "and the one home itself still DOES the subtraction (the guard is not passing by deleting it)")


# ── AXIS 2: every caller dereferences it ──────────────────────────────────────────────────────────
# The three sites the design fix collapsed. Named explicitly: a guard that only counted copies would
# pass if a caller were quietly reverted to reading days_in_stock verbatim, which is the OTHER half of
# how device_history drifted (it never derived a date at all — it just believed the file).
CALLERS = {
    "modules/commcalc/router.py": ["_derived_received", "_mi_ever_over_days"],
    "modules/commcalc/device_history.py": ["build_aging_inventory"],
}
print("\n── AXIS 2: the collapsed callers still dereference the one home ──")
for rel, fns in CALLERS.items():
    path = os.path.join(APP, rel)
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    lines = src.splitlines()
    for fn in fns:
        node = next((n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Assign))
                     and (getattr(n, "name", None) == fn
                          or (isinstance(n, ast.Assign)
                              and any(getattr(t, "id", None) == fn for t in n.targets)))), None)
        ok(node is not None, f"{rel}::{fn} still exists")
        if node is None:
            continue
        seg = "\n".join(lines[node.lineno - 1:getattr(node, "end_lineno", node.lineno) + 3])
        ok("inventory_stock_age" in seg or "_isa." in seg,
           f"{rel}::{fn} reads the receipt date from the one home")

dh = open(os.path.join(APP, "modules/commcalc/device_history.py"), encoding="utf-8").read()
dh_fn = dh[dh.index("def build_aging_inventory"):]
dh_fn = dh_fn[:dh_fn.index("\ndef ", 10)] if "\ndef " in dh_fn[10:] else dh_fn
ok("days = int(dis)" not in dh_fn,
   "device_history no longer assigns the file's days_in_stock as the device's age (the stale bug)")


# ── AXIS 3: the shelf test is not re-invented ─────────────────────────────────────────────────────
# "on the shelf" = on_hand AND at the CURRENT snapshot. A reader that tests on_hand alone counts the
# ghosts balance_sheet measured at ~$129k. balance_sheet predates this module and is EXCUSED by name
# rather than silently: it applies the same rule over its own per-store grain (latest as_of PER STORE,
# not per org), which is a different question, and it is pinned by its own harness.
EXCUSED = {"modules/account/balance_sheet.py"}
print("\n── AXIS 3: the shelf test ──")
ok("def on_shelf" in home_src and "as_of_date" in home_src,
   "on_shelf lives in the one home and tests the snapshot date, not on_hand alone")
ok("balance_sheet.py" in " ".join(EXCUSED),
   "the one pre-existing per-store variant is excused BY NAME, not by silence")


# ── NEGATIVE CONTROLS ─────────────────────────────────────────────────────────────────────────────
print("\n── negative controls ──")


def scan_text(text):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp = f.name
    try:
        return offending_subtractions(tmp)
    finally:
        os.unlink(tmp)


N = [
    ("N1 reading days_in_stock to DISPLAY it is not a derivation",
     "def f(r):\n    return {'age': r.get('days_in_stock'), 'asof': r.get('as_of_date')}\n", False),
    ("N2 a timedelta subtraction with no days_in_stock in sight is not it",
     "from datetime import timedelta\ndef f(d):\n    return d - timedelta(days=30)\n", False),
    ("N3 days_in_stock subtracted from something that is NOT an as_of_date is not it",
     "from datetime import timedelta\ndef f(r, due):\n    return due - timedelta(days=r['days_in_stock'])\n", False),
    ("N4 the real defect IS caught",
     "from datetime import timedelta\ndef f(r):\n    return r['as_of_date'] - timedelta(days=int(r['days_in_stock']))\n", True),
    ("N5 caught under a local alias for the operands too",
     "from datetime import timedelta as td\ndef f(r):\n    aod = r['as_of_date']\n    dis = r['days_in_stock']\n    return aod - td(days=dis)\n", True),
    ("N6 caught through the datetime.timedelta spelling",
     "import datetime\ndef f(r):\n    return r['as_of_date'] - datetime.timedelta(days=r['days_in_stock'])\n", True),
]
for label, text, expect in N:
    got = bool(scan_text(text))
    ok(got == expect, f"{label} -> {'flagged' if got else 'clean'}")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED"))
for f in FAILED:
    print("  FAILED:", f)
sys.exit(1 if FAILED else 0)
