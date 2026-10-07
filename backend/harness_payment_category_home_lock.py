#!/usr/bin/env python3
"""LOCK: one home for "which pay category did this org declare for this payment type".

`commcalc.payment_categories` had NINE private readers before `commcalc/payment_category.py`, each
folding the lookup key its own way — some `.strip()`, some `.strip().lower()`, some neither. That is
nine copies of one question, free to disagree about whether `' Boost Auto Top-Up'` is the same
payment type as `'boost auto top-up'`. This lock makes the tenth impossible to add by accident.

WHAT IT ENFORCES
  A. the home exists, is the module the graph registers, and is the only NEW reader
  B. every other direct reader is in the EXACT inventory below — a new site FAILS, and an excused
     site that gets rewired and left listed ALSO fails, so "excused" cannot become "forgotten"
  C. the home does not restate a fact that has its own home (the sentinel, the placeable set)
  D. RULE TWO — no category name is written in this module's code
  E. the ledger's category filter is wired to the home and is in CI

The inventory is EXACT, not a skip-list. Each entry carries the reason that site has not been
rewired, and every one of them is a MONEY path: rewiring them changes which bucket a dollar lands
in, and the commission engine is additionally owned by the Boost commission numbers audit in
flight. Rewiring is a separate, surfaced change — not a side effect of adding a report filter.
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app")
sys.path.insert(0, HERE)

from app.modules.core.module_graph import home_under_app                      # noqa: E402

TABLE = "payment_categories"
ONE_HOME_REL = home_under_app("payment_category_map")
ONE_HOME = os.path.join(APP, ONE_HOME_REL)

# path relative to backend/app  ->  why this reader has NOT been rewired onto the one home.
EXCUSED_READERS = {
    "modules/commcalc/router.py":
        "five money paths in one file — the pay-feed balance report, `_calc_inputs` (the REP PAY "
        "engine's own inputs), `_compute_gp` (GROSS PROFIT), and the two commission-leg trend "
        "endpoints. Rewiring changes which bucket a dollar lands in, and the commission engine is "
        "owned by the Boost commission numbers audit in flight.",
    "modules/commcalc/pay_data_quality.py":
        "takes `category_of` as an ARGUMENT and reads no table — it is the home for the "
        "reconciliation and for the UNCATEGORISED / PLACEABLE_CATEGORIES facts this module "
        "dereferences. Listed so the lock's own scan is not mistaken for a violation.",
    "modules/asset/purchase_orders.py":
        "`_commission_types` decides which payment types a hotsheet-recon purchase order may bill "
        "against — it books money to a vendor PO.",
    "modules/payables/engine.py":
        "`_reimb_types` drives what the payables engine treats as a reimbursement, which is money "
        "owed out.",
    "modules/marketing/router.py":
        "`_es_payment_categories` feeds the event-sales commission attribution the withholding "
        "report's own recovery leg reads; changing its folding would move recovery verdicts.",
}

PASS = FAIL = 0


def ok(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}   {detail}")


def py_files():
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def rel(path):
    return os.path.relpath(path, APP).replace(os.sep, "/")


def reads_table(path):
    """True when this file names `payment_categories` as a TABLE — `.table("payment_categories")`
    or the router's own `fetch('payment_categories')` helper. A mention in a comment or docstring
    is NOT a read, which is what keeps the written-down history of this defect legal."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("table", "fetch"):
            continue
        for a in node.args:
            if isinstance(a, ast.Constant) and a.value == TABLE:
                return True
    return False


print("\nA. the home exists and the graph agrees where it is")
ok(os.path.exists(ONE_HOME), f"the one home exists at app/{ONE_HOME_REL}")
ok(ONE_HOME_REL == "modules/commcalc/payment_category.py",
   "module_graph names it, so this lock carries no literal path of its own", ONE_HOME_REL)
ok(reads_table(ONE_HOME), "the home does read the table (it is the reader, not a wrapper)")

print("\nB. the exact inventory of other readers")
found = {}
for p in py_files():
    r = rel(p)
    if p == ONE_HOME:
        continue
    if reads_table(p):
        found[r] = True
# pay_data_quality is listed for the reader's benefit but genuinely reads nothing; keep the
# inventory honest by only comparing the files that really do read.
expected = {k for k in EXCUSED_READERS if k != "modules/commcalc/pay_data_quality.py"}
new_sites = sorted(set(found) - expected)
gone = sorted(expected - set(found))
ok(not new_sites,
   "no NEW site reads payment_categories directly — the tenth copy fails the build",
   f"unexcused readers: {new_sites}")
ok(not gone,
   "every excused reader still reads it (one rewired must be REMOVED from the inventory, not left)",
   f"listed but no longer reading: {gone}")
ok(not reads_table(os.path.join(APP, "modules/commcalc/pay_data_quality.py")),
   "pay_data_quality still takes category_of as an argument and reads no table")
ok(all(len(v) > 40 for v in EXCUSED_READERS.values()),
   "every excuse carries a written reason, not a bare flag")

print("\nC. the home does not restate a fact that has its own home")
src = open(ONE_HOME, encoding="utf-8").read()
tree = ast.parse(src)
assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)}
ok("UNCATEGORISED" not in assigned,
   "the unmapped sentinel is dereferenced from pay_data_quality, never defined here")
ok("PLACEABLE_CATEGORIES" not in assigned,
   "the placeable set is dereferenced, never copied here")
ok("pay_data_quality" in src, "...and the module it dereferences is actually imported")

print("\nD. RULE TWO — no category name in this module's code")


def code_strings(path):
    """Every string constant OUTSIDE a docstring. The docstrings here legitimately quote the live
    category names as EVIDENCE of the measured defect, so they are blanked before the scan — the
    same approach harness_clawback.py takes."""
    t = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(t):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
    return [n.value for n in ast.walk(t) if isinstance(n, ast.Constant)
            and isinstance(n.value, str)]


CATEGORY_WORDS = ("commission", "re-imbursement", "reimbursement", "mdf", "rebate", "chargeback",
                  "spiff", "bounty")
offenders = [s for s in code_strings(ONE_HOME)
             if any(w in s.lower() for w in CATEGORY_WORDS)]
ok(not offenders, "no category name is written in code — they are the tenant's rows",
   f"found {offenders}")

print("\nE. the ledger filter is wired to the home, and this runs in CI")
led = open(os.path.join(APP, "modules/commcalc/processor_ledger.py"), encoding="utf-8").read()
ok("payment_category" in led, "the ledger dereferences the home")
ok(not reads_table(os.path.join(APP, "modules/commcalc/processor_ledger.py")),
   "...and does NOT read the table itself")
ok("NO_CATEGORY_ID" in led, "the ledger names the unclassified pick once, as a constant")
api = open(os.path.join(APP, "modules/commcalc/processor_ledger_api.py"), encoding="utf-8").read()
ok("categories" in api, "the endpoint accepts the category filter (deep links / the W3 builder)")
wf = os.path.join(os.path.dirname(HERE), ".github", "workflows")
wired = any("harness_payment_category_home_lock.py" in open(os.path.join(wf, f), encoding="utf-8").read()
            for f in os.listdir(wf) if f.endswith((".yml", ".yaml")))
ok(wired, "this lock runs in CI")
wired2 = any("harness_payment_category.py" in open(os.path.join(wf, f), encoding="utf-8").read()
             for f in os.listdir(wf) if f.endswith((".yml", ".yaml")))
ok(wired2, "and so does the proof harness")

print("\nF. the lock's own detector works")
ok(reads_table(os.path.join(APP, "modules/payables/engine.py")),
   "a real reader is detected")
ok(not reads_table(os.path.join(APP, "modules/commcalc/clawback.py")),
   "a file that only MENTIONS the table in prose is not a reader")

print("\n" + "=" * 94)
print(f"{PASS} passed, {FAIL} failed")
if FAIL:
    print("One payment-category home — LOCK BROKEN")
    sys.exit(1)
print(f"OK — one home, {len(EXCUSED_READERS) - 1} readers excused by name with reasons, "
      "no tenth copy.")
