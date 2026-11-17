"""LOCK: a tenant's own export answers its own declared categories, and no caller may stop asking.

WHY A LOCK AND NOT ONLY TESTS (CLAUDE.md, owner 2026-09-20): *"a design fix ships with a check that
FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears"*. The
four facts this change gave a home to are exactly the kind that grow a second copy:

  1. WHICH COLUMN is which fact      → `report_columns.bound_columns` (nobody else may bind)
  2. HOW MANY a line did            → `pos_report_feed.unit_basis_named`
  3. WHICH SIGN is a sale           → `commission_ledger`, DEREFERENCED, never re-decided
  4. WHICH SOURCE answers a day     → `commcalc/metric_authority.resolve`

and the fifth, inherited: only a basis the tenant DECLARED may pay (`basis_is_declared`).

No DB, no network, no browser. Run:  cd backend && python3 harness_feed_declared_metrics_lock.py
Exit 0 = all green.
"""
import ast
import os
import re
import sys

sys.path.insert(0, ".")

from app.modules.closing import metric_catalog as mc                  # noqa: E402
from app.modules.commcalc import commission_ledger as cl              # noqa: E402
from app.modules.commcalc import daily_incentive as di                # noqa: E402
from app.modules.commcalc import metric_authority as ma               # noqa: E402
from app.modules.commcalc import pos_report_feed as feed              # noqa: E402
from app.modules.commcalc import report_columns as rc                 # noqa: E402
from app.modules.commcalc import report_declaration as rd             # noqa: E402

PASS = FAIL = 0
_HERE = os.path.dirname(os.path.abspath(__file__))
_MIG = os.path.normpath(os.path.join(_HERE, "..", "database", "migrations",
                                     "1124_declared_metric_authority.sql"))


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s" % msg)


def _src(rel):
    with open(os.path.join(_HERE, rel)) as fh:
        return fh.read()


def _executable(text):
    """Source with every docstring and comment stripped — RULE TWO is about what the CODE says."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
    return ast.unparse(tree)


ROUTER = _src("app/modules/commcalc/router.py")
ROUTER_X = _executable(ROUTER)
FEED_X = _executable(_src("app/modules/commcalc/pos_report_feed.py"))
AUTH_X = _executable(_src("app/modules/commcalc/metric_authority.py"))
COLS_X = _executable(_src("app/modules/commcalc/report_columns.py"))

print("\n§A — WHICH COLUMN: one binder, and it wins")
ok(hasattr(rc, "bound_columns") and hasattr(rc, "binding_misses"),
   "report_columns owns binding and reports what a binding missed")
_hdr = ["Category", "Product name", "Operator ID"]
ok(rc.map_headers(_hdr, feed.COLUMN_SYNONYMS)["line_label"] == 0,
   "control: with nothing bound the house default still claims its column")
ok(rc.map_headers(_hdr, feed.COLUMN_SYNONYMS,
                  bindings={"line_label": "Product name"})["line_label"] == 1,
   "a binding outranks every synonym, however long")
ok("line_label" not in rc.map_headers(_hdr, feed.COLUMN_SYNONYMS,
                                      bindings={"line_label": "Nope"}),
   "a binding that names no column of the export identifies NOTHING — no silent fall back")
ok(rc.map_headers(_hdr, feed.COLUMN_SYNONYMS) == rc.map_headers(_hdr, feed.COLUMN_SYNONYMS,
                                                                bindings={}),
   "binding nothing is byte-identical to before bindings existed")
ok("column_bindings" in FEED_X and "bound_columns" not in FEED_X,
   "the feed READS bindings and does not implement a second binder")
ok(len(re.findall(r"def bound_columns", COLS_X)) == 1, "exactly one binder exists")

print("\n§B — HOW MANY: the unit basis is declared, and an unknown one is refused")
ok(feed.UNIT_BASIS_DEFAULT == feed.UNIT_BASIS_COLUMN,
   "the default is today's behaviour, so an undeclared export reads exactly as it does now")
ok(feed.unit_basis_named("") == feed.UNIT_BASIS_COLUMN, "blank reads as the house default")
try:
    feed.unit_basis_named("per_pallet")
    ok(False, "an unknown unit basis must be REFUSED, not guessed")
except ValueError:
    ok(True, "an unknown unit basis is refused rather than guessed")
ok(set(rd.UNIT_BASES) == set(feed.UNIT_BASES),
   "the declaration editor offers exactly the bases the parser serves — no third list")

print("\n§C — WHICH SIGN: dereferenced from the one home, never re-decided")
ok(tuple(feed.AMOUNT_SIGNS) == tuple(cl.SIGN_CONVENTIONS),
   "the sign vocabulary IS commission_ledger's (mig 1006), not a copy")
ok(feed.AMOUNT_SIGN_DEFAULT == cl.SIGN_PAYOUT_POSITIVE,
   "the feed's default is amount-as-written, so no live tenant's revenue moves")
ok(feed.earned_amount("-10.00", cl.SIGN_PAYOUT_NEGATIVE) == 10.0,
   "a negative-signed export reads as money earned")
ok(feed.earned_amount("10.00", cl.SIGN_PAYOUT_NEGATIVE) == -10.0,
   "its opposite sign NETS OFF — it is never abs()-ed into a second sale")
ok(feed.earned_amount("10.00") == 10.0, "declaring nothing takes the amount as written")
ok("PAYOUT_SIGN" not in FEED_X and "reversal_handling" not in FEED_X,
   "the feed holds no second copy of the convention's internals")
try:
    feed.amount_sign_named("whatever")
    ok(False, "an unknown sign convention must be REFUSED")
except ValueError:
    ok(True, "an unknown sign convention is refused rather than silently defaulted")

print("\n§D — WHICH SOURCE: one home, absent means today, unknown is refused")
ok(ma.SOURCES == (ma.SOURCE_DECLARATION, ma.SOURCE_FEED), "the vocabulary is spelled once")
ok(ma.resolve(None) == ma.SOURCE_DECLARATION and ma.resolve("") == ma.SOURCE_DECLARATION,
   "an unanswered question changes nothing for any existing tenant")
try:
    ma.resolve("portal")
    ok(False, "an unknown authority must be REFUSED, never guessed")
except ValueError:
    ok(True, "an unknown authority is refused rather than guessed")
ok("_metric_authority.resolve" in ROUTER_X,
   "the endpoint DEREFERENCES the one home — it does not compare the string itself")
ok(not re.search(r'==\s*["\']feed["\']', ROUTER_X),
   "no caller compares the authority string itself")
ok(len(re.findall(r"def resolve\(", AUTH_X)) == 1, "exactly one resolver exists")

print("\n§E — a guess may never become a payment")
_rows = [{"metric_key": "k", "metric_basis": feed.BASIS_LABEL_PART, "units": 9, "amount": 9.0},
         {"metric_key": "k", "metric_basis": feed.BASIS_LINE_MAP, "units": 1, "amount": 1.0}]
ok(len(ma.lines_from_feed(_rows)) == 1, "only a basis the tenant DECLARED is carried through")
ok(len(ma.excluded_from_feed(_rows)) == 1, "and what was left out is REPORTED, never dropped")
ok("basis_is_declared" in AUTH_X,
   "the allowlist is dereferenced from the feed, not restated as a second list")
ok(set(feed.DECLARED_BASES) < set(feed.BASES),
   "control: the declared bases are a strict subset, so a basis added later reads as NOT evidence")

print("\n§F — the projection is a projection, not a second fold")
_line = ma.lines_from_feed([{"metric_key": "k", "metric_basis": feed.BASIS_LABEL,
                             "units": 2, "amount": 5.0, "employee_name": "A",
                             "business_date": "2026-10-21"}])[0]
ok({"metric_key", "units", "amount", "employee_name"} <= set(_line),
   "feed rows arrive in the shape metric_catalog already reads")
_cat = [{"key": "k", "label": "K", "value_kind": "both", "declared_by": "employee",
         "in_revenue": True, "unit_price": None}]
ok(mc.declared(_cat, None, [_line])["k"]["amount"] == 5.0,
   "the catalog's own fold answers it — no reader was written for the feed side")
ok("portal_metric_day" not in AUTH_X,
   "the one home names no table: the caller does the org-scoped read, the home decides the rule")
ok("def declared(" not in AUTH_X and "def by_employee(" not in AUTH_X,
   "no second fold of a store-day exists")

print("\n§G — a payout that can measure nothing says so, and still moves no money")
ok(di.payout_measurable("revenue", [])[0] is True,
   "the whole declared day is a real number — that is not the unmeasurable case")
# NARROWED 2026-11-17 (§COMMCALC-78, owner ruling). This asserted that DOLLARS with no category
# named cannot be measured. The owner overruled the premise — of the $1,300 a day a pool is paid on:
# *"that is the total revenue for the day"* — so `payout_measure_basis` resolves a money payout
# naming nothing to the day's REVENUE, which is a real number. The unmeasurable case is `units`:
# dollars are dollars and the day has a revenue figure to fall back to, while there is no "day's
# units" to count, so a unit payout naming nothing stays unmeasurable and still says why.
#
# THIS LOCK AND `tests/unit/test_feed_declared_metrics.py` ASSERT THE SAME FACT, and #711 updated
# only the unit test — two homes for one assertion, and I fixed one. Both halves are pinned in both
# places now.
ok(di.payout_measurable("units", [])[0] is False and di.payout_measurable("units", [])[1],
   "a COUNT with no category named cannot be measured, and the reason is in words")
ok(di.payout_measurable("amount", [])[0] is True,
   "but DOLLARS with no category named measure the whole day's revenue (the owner's own rule)")
ok(di.payout_measure_basis("amount", []) == "revenue",
   "...resolved by the one home, so the verdict and the measurement cannot disagree")
ok(di.payout_measure_basis("units", []) == "units",
   "...which does NOT invent a dollar figure for a payout that asked for a count")
ok(di.payout_measurable("units", ["k"])[0] is True, "a named category is measurable")
ok("payout_measurable" in _executable(_src("app/modules/commcalc/daily_incentive.py")),
   "the engine stamps it on its own why-trail")

print("\n§H — the migration degrades, grants, and carries its revert")
_mig = open(_MIG).read() if os.path.exists(_MIG) else ""
ok(bool(_mig), "migration 1124 exists")
ok("add column if not exists" in _mig and "alter table if exists" in _mig,
   "additive and idempotent — safe to re-run")
ok("-- REVERT:" in _mig, "it carries its REVERT note")
ok("to service_role" in _mig, "it grants what it touches to service_role")
ok("'declaration'" in _mig and "'feed'" in _mig and "check (" in _mig,
   "a value this platform cannot resolve may not even be STORED")
ok("_declared_metric_authority" in ROUTER_X,
   "the endpoint reads the declaration through its own degrading helper")
ok("except Exception" in ROUTER[ROUTER.find("def _declared_metric_authority"):
                                ROUTER.find("def _portal_metric_rows")],
   "a read of the column BEFORE the migration lands answers 'nothing declared', never a 500")

print("\n§I — RULE TWO: no tenant's words are in any of it")
FORBIDDEN = ("nda", "2da", "ups domestic", "mailbox", "overnight", "notary", "luxelink",
             "cellfonz", "boost", "logistics")


def spells(text, word):
    return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(word), text.lower()) is not None


for name, text in (("pos_report_feed", FEED_X), ("metric_authority", AUTH_X),
                   ("report_columns", COLS_X)):
    for word in FORBIDDEN:
        ok(not spells(text, word), "%s does not spell %r" % (name, word))
ok(spells("a notary service", "notary") and not spells("upsert", "ups"),
   "control: the word scan finds a real mention and is not fooled by 'upsert'")

print("\n§J — the upload door is the SAME ingest, not a second one")
ok("/pos-report/upload" in ROUTER, "the door exists")
ok("_pos_report_sweep.ingest_report" in ROUTER_X,
   "it lands through the pull's own ingest — a delivered file and a pulled one are the same")
ok("store_rows" not in ROUTER_X.split("/pos-report/upload")[-1][:4000],
   "the door does not land rows itself")
ok(len(re.findall(r"def upload_pos_report", ROUTER_X)) == 1, "exactly one door exists")

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
