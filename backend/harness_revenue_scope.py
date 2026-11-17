#!/usr/bin/env python3
"""PROOF — money no declared category claimed, and whether it is the day's revenue. DB-free, stdlib.

The oracle is the owner's own answer, 2026-11-17, choosing "Whole day's revenue counts" when asked
how $22,653.31 of his October report's unclassified product lines should count toward the day; and,
of the $1,300 gate: *"that is the total revenue for the day, which ever sales rep generates the most
revenue he get his proportional share of commission"*.

THE TWO HALVES THIS MUST HOLD APART, because the whole design is the boundary between them:

    the DAY's revenue      includes money nobody classified — every line was a sale.
    a CATEGORY's figure    never does — an unclassified line is not evidence of any category.

A check that only proved the first would pass a build that paid a per-unit rule out of unclassified
money. §D and §E are that negative half, and they are the half that matters.

  §A  the scope vocabulary, the default, and a spelling nobody knows
  §B  the feed carries unclassified money at the DAY/STORE/OPERATOR grain
  §C  the day's revenue, both scopes, off one fixture
  §D  a NAMED category never sees it — not in a payout, not in a gate
  §E  the engine end to end: the gate, the pool, the proportional split
  §F  ONE home — the arithmetic and the policy are each dereferenced, not copied
  §G  negative controls + RULE TWO
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

import harnesslib as _hl
from app.modules.commcalc import revenue_scope as rs
from app.modules.commcalc import pos_report_feed as feed
from app.modules.commcalc import metric_authority as ma
from app.modules.commcalc import daily_incentive as di
from app.modules.closing import metric_catalog as mc

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
def raises(fn, label):
    try:
        fn()
    except Exception:
        ok(True, label)
        return
    ok(False, label, "no exception")


_HERE = os.path.dirname(os.path.abspath(__file__))
_SCOPE = os.path.join(_HERE, "app/modules/commcalc/revenue_scope.py")
_FEED = os.path.join(_HERE, "app/modules/commcalc/pos_report_feed.py")
_ENGINE = os.path.join(_HERE, "app/modules/commcalc/daily_incentive.py")
_CATALOG = os.path.join(_HERE, "app/modules/closing/metric_catalog.py")


def _code_only(path):
    """The file's CODE, with comments and docstrings thrown away — dereferenced from `harnesslib`,
    never hand-tokenised (PEP 701 made a hand-rolled f-string reader version-dependent, and the
    duplicate was banned after it passed here on 3.13 and failed CI on 3.11).

    A guard that greps RAW source can be both SATISFIED and TRIPPED by prose: a dereference check is
    satisfied by a name in a docstring, and a forbidden-word check is tripped by the owner's own
    quote. Every check below about what the code DOES reads this; §G's figure check deliberately
    reads the whole file instead, because a tenant's number may not live in a comment either."""
    return _hl.py_code_only(open(path).read()).lower()


# ── the fixture: a tenant with two declared categories and a report that outruns them ────────────
# CONFIG, not code. Nothing here is spelled in any module under test, and §G proves it.
CATALOG = mc.build([
    {"field_key": "printing", "label": "Printing", "value_kind": "amount",
     "declared_by": "employee", "in_revenue": True},
    {"field_key": "parcels", "label": "Parcels", "value_kind": "count",
     "declared_by": "employee", "unit_price": 10.0, "in_revenue": True},
])
# What the reps' own declaration carries: $400 of printing (ANA) and 3 parcels (BEN) = $430.
LINES = [
    {"metric_key": "printing", "units": 0, "amount": 400.0, "employee_name": "ANA"},
    {"metric_key": "parcels", "units": 3, "amount": 0.0, "employee_name": "BEN"},
]
DECLARED = mc.declared(CATALOG, None, LINES)
PER_EMP = mc.by_employee(CATALOG, LINES)

# The report's own rows AS THE FEED LEAVES THEM: two placed, three not. CAM appears ONLY on
# unclassified lines — the case that reads as "sold nothing" if unclassified rows are dropped.
FEED_ROWS = [
    {"business_date": "2026-10-20", "store_code": "S1", "employee_name": "ANA",
     "metric_key": "printing", "metric_basis": feed.BASIS_LINE_MAP, "units": 0, "amount": 400.0},
    {"business_date": "2026-10-20", "store_code": "S1", "employee_name": "BEN",
     "metric_key": "parcels", "metric_basis": feed.BASIS_LINE_MAP, "units": 3, "amount": 0.0},
    {"business_date": "2026-10-20", "store_code": "S1", "employee_name": "ANA",
     "metric_key": feed.UNCLASSIFIED_KEY, "metric_basis": feed.BASIS_UNMATCHED, "units": 1,
     "amount": 500.0, "line_label": "Ground Residential"},
    {"business_date": "2026-10-20", "store_code": "S1", "employee_name": "CAM",
     "metric_key": feed.UNCLASSIFIED_KEY, "metric_basis": feed.BASIS_UNMATCHED, "units": 1,
     "amount": 600.0, "line_label": "Art Box"},
    {"business_date": "2026-10-21", "store_code": "S1", "employee_name": "ANA",
     "metric_key": feed.UNCLASSIFIED_KEY, "metric_basis": feed.BASIS_UNMATCHED, "units": 1,
     "amount": 999.0, "line_label": "Another Day"},
]

print("\n§A  the scope vocabulary")
eq(rs.SCOPE_DEFAULT, rs.SCOPE_CLASSIFIED_ONLY,
   "A1: the DEFAULT is today's behaviour, so an unanswered question moves nobody")
eq(rs.resolve(""), rs.SCOPE_CLASSIFIED_ONLY, "A2: blank reads as the default")
eq(rs.resolve(None), rs.SCOPE_CLASSIFIED_ONLY, "A3: so does None")
eq(rs.resolve("  WHOLE_DAY "), rs.SCOPE_WHOLE_DAY, "A4: a declared scope is read case/space-insensitively")
raises(lambda: rs.resolve("whole-day"), "A5: a MISTYPED scope is refused, never silently defaulted")
raises(lambda: rs.resolve("everything"), "A6: and so is a scope nobody declared")
ok(not rs.counts_unclassified(""), "A7: by default unclassified money is NOT revenue")
ok(rs.counts_unclassified(rs.SCOPE_WHOLE_DAY), "A8: declared whole_day, it is")
eq(sorted(rs.SCOPES), sorted(rs.SCOPE_LABELS), "A9: every scope has a label a screen can show")
eq(rs.unclassified_addend(rs.SCOPE_CLASSIFIED_ONLY, 22653.31), 0.0,
   "A10: the addend under classified_only is nothing, whatever money was found")
eq(rs.unclassified_addend(rs.SCOPE_WHOLE_DAY, 22653.31), 22653.31,
   "A11: and under whole_day it is the money")
eq(rs.unclassified_addend(rs.SCOPE_WHOLE_DAY, None), 0.0, "A12: no money found adds nothing, never a crash")
eq(rs.unclassified_addend(rs.SCOPE_WHOLE_DAY, "nonsense"), 0.0, "A13: nor does an unreadable figure")

print("\n§B  the feed carries unclassified money at the day/store/operator grain")
ok(feed.is_unclassified({"metric_key": ""}), "B1: an empty key IS the absence of a category")
ok(feed.is_unclassified({"metric_key": "   "}), "B2: and so is whitespace")
ok(not feed.is_unclassified({"metric_key": "printing"}), "B3: a real category is placed")
ok(feed.is_unclassified({}), "B4: a row with no key at all is unclassified, never a crash")
UP = feed.unclassified_by_store_day(FEED_ROWS)
eq(sorted(UP), [("S1", "2026-10-20"), ("S1", "2026-10-21")],
   "B5: the grain is the STORE-DAY — unclassified money is dated, which is the whole point")
eq(UP[("S1", "2026-10-20")]["amount"], 1100.0, "B6: the day's unclassified total")
eq(UP[("S1", "2026-10-20")]["rows"], 2, "B7: and how many lines it came from")
eq(UP[("S1", "2026-10-20")]["by_employee"], {"ANA": 500.0, "CAM": 600.0},
   "B8: split by the OPERATOR the report names, which is what a proportional share needs")
eq(UP[("S1", "2026-10-21")]["amount"], 999.0, "B9: another day is another day, never pooled")
_noplace = feed.unclassified_by_store_day([dict(FEED_ROWS[3], store_code=None)])
eq(_noplace, {}, "B10: a row whose STORE could not be placed joins no store's day")
_noemp = feed.unclassified_by_store_day([dict(FEED_ROWS[3], employee_name=None)])
eq(_noemp[("S1", "2026-10-20")]["by_employee"], {},
   "B11: a line with no operator is in the day's total and in NOBODY's share")
eq(_noemp[("S1", "2026-10-20")]["amount"], 600.0, "B12: ...and the day's total still carries it")
eq(feed.totals_by_store_day(FEED_ROWS).get(("S1", "2026-10-20", "")), None,
   "B13: the per-CATEGORY shape has NO bucket for 'no category' — it would compare against nothing")
eq(sorted(k[2] for k in feed.totals_by_store_day(FEED_ROWS)), ["parcels", "printing"],
   "B14: ...it carries exactly the placed categories, unchanged")

print("\n§C  reading it out of the feed, and into the day's revenue")
U = ma.unclassified_from_feed(FEED_ROWS, business_date="2026-10-20")
eq(U["amount"], 1100.0, "C1: one day's unclassified money")
eq(U["rows"], 2, "C2: on two lines")
eq(U["by_employee"], {"ANA": 500.0, "CAM": 600.0}, "C3: by operator")
eq(ma.unclassified_from_feed(FEED_ROWS)["amount"], 2099.0, "C4: unnarrowed, every day given")
eq(ma.unclassified_from_feed([])["amount"], 0.0, "C5: no rows is a real zero, never None")
# THE LOAD-BEARING EXCLUSION. `lines_from_feed` is the shape the catalog folds INTO CATEGORIES and
# that every payout is measured from. An unclassified row reaching it would pay a per-unit rule out of
# money nobody classified, which is the one outcome this whole design exists to prevent.
_lf = ma.lines_from_feed(FEED_ROWS, business_date="2026-10-20")
eq(sorted(l["metric_key"] for l in _lf), ["parcels", "printing"],
   "C5a: `lines_from_feed` carries the PLACED lines and no others")
ok(all(l["metric_key"].strip() for l in ma.lines_from_feed(FEED_ROWS)),
   "C5b: ...so no line it yields can ever be unclassified")
eq([l for l in ma.lines_from_feed(FEED_ROWS) if l.get("employee_name") == "CAM"], [],
   "C5c: ...and an operator with only unclassified lines contributes NO category line")
# AND THE KEY GUARD ON ITS OWN. The basis allowlist happens to exclude today's unclassified rows too,
# because `match_metric` only ever returns a declared basis TOGETHER with a key. These rows come out
# of a table where the two are separate columns, so that pairing is a convention and not a
# constraint: a stale row, a backfill or a later change could present a declared basis with no key.
# This fixture IS that pathological pairing, so the key guard is proven necessary by itself rather
# than being shadowed by the one after it.
_PATHOLOGICAL = [{"business_date": "2026-10-20", "store_code": "S1", "employee_name": "ANA",
                  "metric_key": feed.UNCLASSIFIED_KEY, "metric_basis": feed.BASIS_LINE_MAP,
                  "units": 1, "amount": 777.0, "line_label": "Declared basis, no category"}]
eq(ma.lines_from_feed(_PATHOLOGICAL), [],
   "C5e: a row with a DECLARED basis and NO category still yields no category line")
eq(ma.unclassified_from_feed(_PATHOLOGICAL)["amount"], 777.0,
   "C5f: ...and that money is read as the day's, which is the only thing it can be")
_exc = ma.excluded_from_feed(FEED_ROWS)
ok(all(str(e.get("metric_key") or "").strip() for e in _exc),
   "C5d: the 'matched by wording only' report is also per-category — unclassified rows are not in it")
eq(mc.gross_revenue(CATALOG, DECLARED), 430.0,
   "C6: the day's PLACED revenue — $400 printing + 3 parcels at $10")
eq(mc.gross_revenue(CATALOG, DECLARED, unclassified=0.0), 430.0,
   "C7: ...and passing no unclassified money is byte-identical, so every existing caller is unmoved")
eq(mc.gross_revenue(CATALOG, DECLARED, unclassified=1100.0), 1530.0,
   "C8: the WHOLE day is the placed categories PLUS what nobody could classify")
eq(mc.employee_revenue(CATALOG, PER_EMP), {"ANA": 400.0, "BEN": 30.0},
   "C9: per-operator placed revenue, unchanged")
_er = mc.employee_revenue(CATALOG, PER_EMP, unclassified_by_employee=U["by_employee"])
eq(_er, {"ANA": 900.0, "BEN": 30.0, "CAM": 600.0},
   "C10: each operator's own unclassified lines join their own revenue")
ok("CAM" in _er,
   "C11: AND an operator whose EVERY line was unclassified is present — not reading as zero")

print("\n§D  a NAMED category never sees unclassified money")
eq(di.measure(CATALOG, DECLARED, "amount", ["printing"], unclassified=1100.0)[0], 400.0,
   "D1: a payout naming a category measures that category, not the day")
eq(di.measure(CATALOG, DECLARED, "units", ["parcels"], unclassified=1100.0)[0], 3,
   "D2: nor can unclassified DOLLARS become a category's UNITS")
eq(di.measure(CATALOG, DECLARED, "revenue", ["printing"], unclassified=1100.0)[0], 400.0,
   "D3: 'revenue' WITH a category named is still that category")
eq(di.measure(CATALOG, DECLARED, "revenue", [], unclassified=1100.0)[0], 1530.0,
   "D4: only a measure naming NOTHING means the whole day")
eq(di.measure(CATALOG, DECLARED, "revenue", [])[0], 430.0,
   "D5: ...and with no unclassified money passed it is the placed day, unchanged")
_g = di._gate(CATALOG, {"scope": "store", "basis": "amount", "metric_keys": ["printing"],
                        "op": "gte", "threshold": 1000.0}, DECLARED, None, unclassified=1100.0)
ok(not _g[0], "D6: a GATE on a named category is not opened by unclassified money either")
_g2 = di._gate(CATALOG, {"scope": "store", "basis": "revenue", "op": "gte", "threshold": 1000.0},
               DECLARED, None, unclassified=1100.0)
ok(_g2[0], "D7: a gate on the WHOLE DAY is opened by it — $1,530 clears $1,000")
_g3 = di._gate(CATALOG, {"scope": "store", "basis": "revenue", "op": "gte", "threshold": 1000.0},
               DECLARED, None)
ok(not _g3[0], "D8: ...and without it the same day fails — the defect this change fixes")

print("\n§E  the engine end to end")
POOL_RULE = [{"key": "day_pool", "label": "Day pool", "rule_kind": "threshold_pool",
              "is_active": True,
              "qualifier": {"scope": "store", "basis": "revenue", "op": "gte",
                            "threshold": 1000.0},
              "payout": {"rate": 0.01, "basis": "amount", "metric_keys": [],
                         "split": "individual_sales"}}]
R_PLACED = di.compute_day(CATALOG, POOL_RULE, declared_map=DECLARED, per_employee=PER_EMP)
eq(R_PLACED["store_revenue"], 430.0, "E1: classified_only — the day reads $430")
eq(R_PLACED["total"], 0.0, "E2: ...so the $1,000 gate fails and nothing pays")
R_WHOLE = di.compute_day(CATALOG, POOL_RULE, declared_map=DECLARED, per_employee=PER_EMP,
                         unclassified=1100.0, unclassified_by_employee={"ANA": 500.0, "CAM": 600.0})
eq(R_WHOLE["store_revenue"], 1530.0, "E3: whole_day — the day reads $1,530")
eq(R_WHOLE["unclassified_revenue"], 1100.0,
   "E4: ...and the answer SAYS how much of that nobody could classify")
eq(R_WHOLE["unclassified_by_employee"], {"ANA": 500.0, "CAM": 600.0}, "E5: ...and whose it was")
eq(R_WHOLE["total"], 15.3, "E6: the gate opens and the pool is 1% of the whole day")
eq(R_WHOLE["by_employee"], {"ANA": 9.0, "BEN": 0.3, "CAM": 6.0},
   "E7: split PROPORTIONAL to each operator's own revenue — the owner's own words")
eq(round(sum(R_WHOLE["by_employee"].values()), 2), R_WHOLE["total"],
   "E8: the shares sum to the pool exactly, to the cent")
eq(R_WHOLE["unallocated"], 0.0, "E9: nothing is left unallocated")
eq(R_WHOLE["top_earner"], "ANA", "E10: 'who generated the most' counts the whole day too")
_cam = [l for l in R_WHOLE["lines"] if l["employee"] == "CAM"]
eq(len(_cam), 1,
   "E11: the operator whose EVERY line was unclassified is paid — not silently absent")
PER_UNIT = [{"key": "parcel_pay", "label": "Parcel pay", "rule_kind": "per_unit", "is_active": True,
             "qualifier": {"scope": "none"},
             "payout": {"rate": 2.0, "basis": "units", "metric_keys": ["parcels"]}}]
R_PU = di.compute_day(CATALOG, PER_UNIT, declared_map=DECLARED, per_employee=PER_EMP,
                      unclassified=1100.0, unclassified_by_employee={"ANA": 500.0, "CAM": 600.0})
eq(R_PU["by_employee"], {"BEN": 6.0},
   "E12: a per-unit rule on a named category pays only its units — CAM's $600 buys nothing")
eq(R_PU["total"], 6.0, "E13: ...and the total is that and only that")

print("\n§F  ONE home — dereferenced, never copied")
_sc, _fd, _en, _ct = _code_only(_SCOPE), _code_only(_FEED), _code_only(_ENGINE), _code_only(_CATALOG)
ok("gross_revenue" in _en,
   "F1: the engine DEREFERENCES the catalog's revenue definition rather than summing locally")
ok("unclassified" in _ct and "revenue_keys" in _ct,
   "F2: the catalog's revenue definition is the one place placed and unclassified money are added")
ok("revenue_scope" not in _ct and "revenue_scope" not in _en,
   "F3: neither the catalog nor the engine decides the POLICY — a caller resolves it and passes it")
ok("scope_classified_only" not in _en and "scope_whole_day" not in _en,
   "F4: ...and neither spells a scope, so the switch cannot be re-decided next to the money")
ok("unclassified_key" in _fd or "is_unclassified" in _fd,
   "F5: the feed names the absence of a category ONCE")
ok(_fd.count('"metric_key": key') + _fd.count("'metric_key': key") <= 1,
   "F6: one place builds a row's metric key")
for _n in ("scopes", "scope_default", "resolve", "counts_unclassified", "unclassified_addend"):
    ok(_n in _sc, f"F7.{_n}: the scope home carries `{_n}`")
ok("unclassified_addend" in _sc, "F8: the ADDEND is the scope home's arithmetic, so no caller branches")

print("\n§G  negative controls + RULE TWO")
# ARMED: each of these is a defect this harness must catch. Verified red by planting them.
eq(mc.gross_revenue(CATALOG, DECLARED, unclassified=-50.0), 380.0,
   "G1: a NEGATIVE unclassified figure (a refund nobody classified) reduces the day, never abs()'d")
eq(di.measure(CATALOG, {}, "revenue", [], unclassified=1100.0)[0], 1100.0,
   "G2: a day with NO declaration at all still has the revenue the report shows")
eq(mc.gross_revenue(CATALOG, DECLARED, unclassified=0.001), 430.0,
   "G3: the sum is rounded to the cent")
_sent_w = rs.scope_sentence(rs.SCOPE_WHOLE_DAY, unclassified=22653.31, lines=50)
ok("22,653.31" in _sent_w and "50" in _sent_w,
   "G4: the sentence a screen shows NAMES the money and the line count")
ok("unmapped" in _sent_w.lower(),
   "G5: ...and says the lines are still unmapped — counting them is not classifying them")
_sent_p = rs.scope_sentence(rs.SCOPE_CLASSIFIED_ONLY, unclassified=22653.31, lines=50)
ok("not" in _sent_p.lower(),
   "G6: under classified_only the sentence says the money is NOT in the figure")
ok(rs.scope_sentence(rs.SCOPE_WHOLE_DAY, unclassified=0.0, lines=0),
   "G7: a fully-mapped day still gets a sentence, so the figure is never bare")
# RULE TWO over CODE, so the owner's quote may stay in a docstring...
for _w in ("ups", "mailbox", "printing", "parcel", "ground", "platinum", "logistics", "boost",
           "total wireless"):
    ok(_w not in _sc, f"G8.{_w}: the scope home names no tenant, product or category — '{_w}'")
# ...and over the WHOLE file for FIGURES, so a tenant's number cannot live in a comment either.
_raw = open(_SCOPE).read()
for _n in ("1300", "1,300", "1000000", "1,000,000"):
    ok(_n not in _raw, f"G9.{_n}: no tenant's threshold or goal is written in the scope home — '{_n}'")
ok("22,653.31" in _raw or "22653.31" in _raw,
   "G10: the ONE figure allowed is the owner's own measured finding, quoted as the reason")
ok(_raw.count("22,653.31") + _raw.count("22653.31") <= 3,
   "G11: ...and it is quoted, not used — it appears only in prose")
ok("22653.31" not in _sc and "22,653.31" not in _sc,
   "G12: ...proven by its absence from the file's CODE")

print(f"\n{'='*70}\n  {P} passed / {F} failed\n{'='*70}")
sys.exit(1 if F else 0)
