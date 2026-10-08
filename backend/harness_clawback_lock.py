"""LOCK — a clawback is recognised by the money's DIRECTION, in one home, or the build fails
(index §55, owner ask 2026-10-06).

THE DEFECT (measured live 2026-10-06, house org). `commcalc/flags.py`'s CHARGEBACK detector asked
`row['category'] == 'Chargeback'`. `commcalc.payment_categories` is a free-text map a tenant fills
in, and no tenant has ever been able to put `Chargeback` in it — `pay_data_quality` already recorded
the consequence in a comment: *"the calculator tests for it and payment_categories has never
contained it, so that bucket has always been $0"*. So:

    commcalc.flags       flag_type = 'CHARGEBACK'                   0 rows, ever
    raw_payment_detail   payment_type = 'Commission Withholding'  474 rows, $7,123.39 taken back

THE CLASS, not the instance. It is not "the house tenant's withholding rows are missed". It is
**a clawback was recognised by a category name nobody can declare, so it was recognised nowhere —
and, declared instead as earnings, it netted silently into commission.** A feed that renames its
withholding line next quarter breaks a name-based test again; `pay_data_quality` measured exactly
that failure at $288,813 for quarter-named promo lines.

THE DESIGN FIX. ONE home, `commcalc/clawback.py`: a clawback is a row the processor DEBITED (the
sign rule dereferenced from `processor_ledger.FEED_SHAPES`, never restated) whose type the ORG's own
map places in a pay category (`pay_data_quality.PLACEABLE_CATEGORIES`, dereferenced, minus the
phantom entry). Every caller reads it. The live mis-declaration is REPORTED by
`clawback.declaration_findings`, never repaired by code that hides it — re-declaring it moves money
and is the owner's call.

WHAT THIS FAILS THE BUILD ON (stdlib + ast; no DB, no network, no pip install):
  A. ONE HOME EXISTS. `clawback.py` defines `classify_row` / `is_clawback` / `debit_of` /
     `pay_categories` exactly once each, and imports the sign rule from `processor_ledger` rather
     than carrying its own.
  B. NO SECOND SIGN TABLE. No module under backend/app outside `processor_ledger.py` declares a
     `credit_positive` key or a feed-shape dict of its own.
  C. NO CATEGORY-LITERAL TEST. No module under backend/app compares anything to a clawback-ish
     category literal ('Chargeback', 'Clawback', 'Withholding', …) in a boolean test. The
     `PLACEABLE_CATEGORIES` tuple entry and `clawback.py`'s own `_PHANTOM` are the two named
     excuses, because they are what the one home exists to exclude.
  D. THE CALLERS ARE WIRED. `commcalc/flags.py` and `commcalc/withholding_report.py` both import
     the one home and route their clawback test through it. A registry written and left un-wired is
     the failure this house has had three times (CLAUDE.md, §19.18), so wiring is CHECKED, not
     assumed.
  E. REGRESSION. The pre-fix test, replayed against the live row shape, finds nothing; the one home
     finds it. Both halves are asserted, so a future "simplification" back to a name test fails.
  F. NEGATIVE CONTROLS. Each violation is planted in source text and this lock must catch it — and
     a legitimate shape is planted and must NOT be caught (no over-catching).

Run: python3 backend/harness_clawback_lock.py
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

ONE_HOME = os.path.join(APP, home_under_app("clawback_direction"))
SIGN_HOME = os.path.join(APP, "modules/commcalc/processor_ledger.py")
CALLERS = (os.path.join(APP, "modules/commcalc/flags.py"),
           os.path.join(APP, "modules/commcalc/withholding_report.py"))

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


# ── The detectors, as functions, so the negative controls can run them over planted source. ─────
#: THE EXCUSE INVENTORY — every category-name clawback test left in place, with the reason.
#:
#: These are not oversights and they are not fixed in this PR. Each one BOOKS MONEY off that
#: dead `Chargeback` bucket, so wiring it to the one home would start moving money the moment it
#: merged: the P&L's chargebacks line, a store's gross profit, and a rep's payout. CLAUDE.md is
#: explicit that money-touching changes are surfaced for owner approval rather than shipped with the
#: mechanism, and the commission engine itself is owned by a separate audit in flight.
#:
#: The inventory is EXACT, not a skip-list: the check below asserts the found set equals this set,
#: so a NEW category-name test fails the build, and so does an excused one that gets fixed without
#: being removed from here. That is what stops "excused" quietly becoming "forgotten".
#: REMOVED 2026-10-08 (index §58.7): `commcalc/gp_report.py`, which held
#:     ("app/modules/commcalc/gp_report.py", "Chargeback")
#: — "books the per-number `chb` bucket that feeds GROSS PROFIT — money; owner ruling needed."
#: That site's `cat == 'Chargeback'` compare is GONE. It was one of four category literals the GP
#: engine spelled itself, and all four were deleted when the report stopped classifying carrier
#: money and started dereferencing the one home (§58). The `chb` column is now reached by the org's
#: own DECLARED category through per-org config (`carrier_gp_category_columns`, mig 1064), so there
#: is no category name in code to test any more — which is why this entry had to go rather than be
#: re-excused: the inventory is EXACT in both directions.
#:
#: WHAT THAT DID *NOT* FIX, and is still an owner-approval money change: the GP `chb` column is
#: still reached by a category DECLARATION, not by §55's direction-of-money rule. Wiring it to
#: `clawback.py` would move dollars between `comm` and `chb` the moment it merged. Measured live
#: read-only, house org July–October 2026: the house declares no chargeback category at all, so the
#: column is $0.00 before and after §58.7 — the bucket is as dead as §55 found it.
EXCUSED_SITES = {
    ("app/modules/commcalc/router.py", "Chargeback"):
        "builds commcalc.chargeback_items, which the P&L's chargebacks line BOOKS — money; owner "
        "ruling needed.",
    ("app/modules/commcalc/calculator.py", "Chargeback"):
        "books the per-login `chb` bucket in REP PAY — money; and the commission engine is owned by "
        "the Boost commission numbers audit in flight.",
}

#: Files whose clawback literal is what the one home exists to EXCLUDE, so it must stay.
LITERAL_EXCUSES = {
    os.path.join(APP, "modules/commcalc/pay_data_quality.py"),   # PLACEABLE_CATEGORIES tuple entry
    ONE_HOME,                                                     # _PHANTOM
}

#: Category words that mean "money came back". Lower-cased substring match on a compared literal.
CLAWBACK_WORDS = ("chargeback", "charge back", "charge-back", "clawback", "claw back",
                  "claw-back", "withhold", "withholding", "reversal")

#: The fields whose VALUE naming a clawback is the defect. A comparison only counts when one side
#: reads one of these — that is the actual rule ("do not identify a clawback by its category name"),
#: and it is what keeps the lock off an internal reason code (`reason == 'net_clawback'`) or a filter
#: selector (`status == 'chargeback'`), neither of which is a category at all.
CATEGORY_FIELDS = ("category", "payment_type", "comp_type", "payment type")


def _reads_category(node):
    """True when `node` reads one of CATEGORY_FIELDS — by subscript, attribute or local name."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                and sub.value.strip().lower() in CATEGORY_FIELDS:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr.lower() in CATEGORY_FIELDS:
            return True
        if isinstance(sub, ast.Name) and sub.id.lower() in ("cat", "category", "ptype",
                                                            "payment_type", "comp_type"):
            return True
    return False


def literal_tests(src, path="<planted>"):
    """Every boolean comparison in `src` that identifies a clawback BY ITS CATEGORY NAME, as
    (line, literal).

    Two narrowings, each deliberate:
      · only COMPARISONS and membership tests count — a docstring, a label, a note or a dict key is
        evidence or display, not behavior. This lock must not force a module to stop NAMING the
        defect it documents.
      · one side must READ a category / payment-type field. A clawback-ish word compared against
        anything else is not a category test: `reason == 'net_clawback'` is an internal reason code
        and `status == 'chargeback'` is an envelope filter selector, and neither is this defect.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        parts = [node.left] + list(node.comparators)
        if not any(_reads_category(p) for p in parts):
            continue
        lits = []
        for p in parts:
            if isinstance(p, ast.Constant) and isinstance(p.value, str):
                lits.append(p.value)
            elif isinstance(p, (ast.Tuple, ast.List, ast.Set)):
                lits += [e.value for e in p.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        for lit in lits:
            low = lit.strip().lower()
            if any(w in low for w in CLAWBACK_WORDS):
                hits.append((getattr(node, "lineno", 0), lit))
    return hits


def sign_tables(src):
    """Every place `src` DECLARES a sign convention of its own, as line numbers.

    Declaring means: the word is a KEY in a dict literal, or a keyword argument carrying a constant.
    READING the registered fact — `shape["credit_positive"]`, or passing it straight through — is
    the correct behavior and must not be caught, or the one home could not dereference it.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value == "credit_positive":
                    out.append(getattr(k, "lineno", 0))
        if isinstance(node, ast.keyword) and node.arg == "credit_positive" \
                and isinstance(node.value, ast.Constant):
            out.append(getattr(node, "lineno", 0))
    return out


def defs_of(src):
    """{name: count} of top-level function definitions."""
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = out.get(node.name, 0) + 1
    return out


def imports_one_home(src):
    """True when `src` imports the clawback one home."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("commcalc"):
            if any(a.name == "clawback" for a in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("commcalc.clawback"):
            return True
        if isinstance(node, ast.Import):
            if any((a.name or "").endswith("commcalc.clawback") for a in node.names):
                return True
    return False


def calls_one_home(src):
    """The one-home functions actually CALLED in `src` — a registry nobody dereferences is no fix."""
    tree = ast.parse(src)
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


# ── A. the one home exists, and borrows the sign rule ───────────────────────────────────────────
HOME_SRC = read(ONE_HOME)
d = defs_of(HOME_SRC)
for fn in ("classify_row", "is_clawback", "debit_of", "pay_categories", "declaration_findings",
           "feed_shape", "row_type"):
    check("A: the one home defines %s() exactly once" % fn, d.get(fn) == 1)
check("A: the one home IMPORTS the sign rule rather than carrying one",
      "from app.modules.commcalc.processor_ledger import" in HOME_SRC
      and "FEED_SHAPES" in HOME_SRC and "classify_amount" in HOME_SRC)
check("A: the one home declares no sign convention of its own", sign_tables(HOME_SRC) == [])
check("A: the one home DEREFERENCES the pay engine's category list",
      "PLACEABLE_CATEGORIES" in HOME_SRC)

# ── B. no second sign table anywhere under backend/app ──────────────────────────────────────────
second = []
for p in py_files(APP):
    if os.path.abspath(p) == os.path.abspath(SIGN_HOME):
        continue
    for ln in sign_tables(read(p)):
        second.append("%s:%s" % (os.path.relpath(p, HERE), ln))
check("B: no second sign convention outside the feed-shape home%s"
      % ("" if not second else " — found " + ", ".join(second[:5])), not second)

# ── C. every category-name clawback test is either GONE or in the exact excuse inventory ────────
found = {}
for p in py_files(APP):
    if os.path.abspath(p) in {os.path.abspath(x) for x in LITERAL_EXCUSES}:
        continue
    rel = os.path.relpath(p, HERE).replace(os.sep, "/")
    for ln, lit in literal_tests(read(p), p):
        found.setdefault((rel, lit), []).append(ln)

new_sites = sorted(set(found) - set(EXCUSED_SITES))
fixed_but_listed = sorted(set(EXCUSED_SITES) - set(found))
check("C: no NEW category-name clawback test%s"
      % ("" if not new_sites else " — found " + "; ".join("%s (%r)" % x for x in new_sites[:5])),
      not new_sites)
check("C: no excused site was fixed without being removed from the inventory%s"
      % ("" if not fixed_but_listed else " — stale: " + "; ".join("%s (%r)" % x
                                                                  for x in fixed_but_listed)),
      not fixed_but_listed)
check("C: the pay engine's phantom tuple entry is still there to be excluded",
      "Chargeback" in read(os.path.join(APP, "modules/commcalc/pay_data_quality.py")))
print("     excuse inventory (%d site(s), each money-touching — see §55):" % len(EXCUSED_SITES))
for (rel, lit), reason in sorted(EXCUSED_SITES.items()):
    print("       %s %r at line(s) %s" % (rel, lit, found.get((rel, lit), [])))
    print("         %s" % reason)

# ── D. the callers are WIRED, not merely adjacent ───────────────────────────────────────────────
for p in CALLERS:
    src = read(p)
    rel = os.path.relpath(p, HERE)
    check("D: %s imports the one home" % rel, imports_one_home(src))
    called = calls_one_home(src)
    check("D: %s routes its clawback test THROUGH the one home" % rel,
          bool(called & {"classify_row", "is_clawback", "debit_of", "pay_categories"}))
    check("D: %s carries no clawback category literal test" % rel, not literal_tests(src, p))

# ── E. regression: the old test finds nothing, the new one finds it ─────────────────────────────
from app.modules.commcalc import clawback as _cb            # noqa: E402

LIVE_ROW = {"payment_type": "Commission Withholding", "amount": -11.0,
            "imei": "352700326611885", "mdn": "", "payment_date": "2026-04-24"}
LIVE_DEC = {"Commission Withholding": "Commission"}


def old_detector(row, declaration):
    """The pre-fix test, verbatim in shape: a category-name comparison."""
    return str(declaration.get(str(row.get("payment_type") or "").strip()) or "").strip() \
        == "Chargeback"


check("E: REGRESSION — the pre-fix name test misses the live clawback",
      old_detector(LIVE_ROW, LIVE_DEC) is False)
check("E: the one home finds it, at the debited magnitude",
      _cb.is_clawback(LIVE_ROW, LIVE_DEC) is True
      and _cb.classify_row(LIVE_ROW, LIVE_DEC)["amount"] == 11.0)
check("E: and still finds it after the feed renames the line",
      _cb.is_clawback({"payment_type": "2027 Q1 Commission Recovery", "amount": -11.0},
                      {"2027 Q1 Commission Recovery": "Commission"}) is True)

# ── F. negative controls — each planted violation must be CAUGHT ────────────────────────────────
PLANTS = [
    ("a revived category-name test",
     "def f(r):\n    return r['category'] == 'Chargeback'\n", literal_tests, True),
    ("a membership test against clawback words",
     "def f(r):\n    return r['category'] in ('Chargeback', 'Clawback')\n", literal_tests, True),
    ("a withholding payment-type test",
     "def f(r):\n    return r['payment_type'] == 'Commission Withholding'\n", literal_tests, True),
    ("a reversal test",
     "if cat == 'reversal':\n    pass\n", literal_tests, True),
    ("a second sign table as a dict",
     "SHAPES = {'x': {'credit_positive': True}}\n", sign_tables, True),
    ("a second sign convention passed as a keyword",
     "classify_amount(a, credit_positive=False)\n", sign_tables, True),
]
for name, src, detector, should_catch in PLANTS:
    caught = bool(detector(src))
    check("F: catches %s" % name, caught is should_catch)

NON_VIOLATIONS = [
    ("a docstring naming the defect",
     '"""The Chargeback category nobody can declare."""\n', literal_tests),
    ("a human label for display",
     "LABEL = 'Chargebacks / clawbacks'\n", literal_tests),
    ("a note mentioning withholding",
     "NOTE = {'k': 'the carrier withheld it'}\n", literal_tests),
    ("calling the one home",
     "import clawback\nx = clawback.is_clawback(r, m)\n", literal_tests),
    ("reading the registered shape",
     "s = FEED_SHAPES['epay']\nd = classify_amount(a, s['credit_positive'])\n", sign_tables),
    # The two live shapes that are NOT this defect. Both carry a clawback word and neither is a
    # category test, so catching either would be the over-catch that makes a lock get disabled.
    ("an internal reason code", "if reason == 'net_clawback':\n    pass\n", literal_tests),
    ("a filter selector", "if status == 'chargeback':\n    pass\n", literal_tests),
    ("a P&L line key", "if line_key == 'chargebacks':\n    pass\n", literal_tests),
]
for name, src, detector in NON_VIOLATIONS:
    check("F: does NOT over-catch %s" % name, not detector(src))

print()
if FAILURES:
    print("%d FAILURE(S):" % len(FAILURES))
    for f in FAILURES:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
