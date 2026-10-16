#!/usr/bin/env python3
"""
PROOF HARNESS — A CHEAP ANSWER IS NEVER HELD HOSTAGE BY AN EXPENSIVE DECORATION
==============================================================================
(index §COMMCALC-TALLY — the lock for `commcalc/prospect_feed_tally.py`)

OWNER REPORT 2026-10-16, verbatim: *"Store Takeover Evaluation takes along time to load , and
existing evaluation is not in the pull up list"*. ONE CAUSE, BOTH COMPLAINTS.

`GET /commcalc/prospects` answers "which stores am I evaluating" — two rows, one indexed query,
milliseconds. It then DECORATED each row with "and how many rows has each feed got in each month",
and computed that decoration by SHIPPING EVERY FEED ROW TO PYTHON AND COUNTING THEM.

Measured live 2026-10-16, acting org, read-only over `commcalc.prospect_feed_row`:

    202,865 rows (137,374 + 65,491 across two prospects)
    = 203 round trips at the platform's 1,000-row page
    = 0.289 s/page timed from a container -> ~59 s, GROWING WITH EVERY UPLOAD

behind `gunicorn --timeout 120` with two workers. So the list the owner came for was empty for a
minute — which is exactly how a slow count reads as a missing evaluation — and the same read
occupied half the platform's capacity while it ran.

THE CLASS, not the instance (owner directive 2026-09-20):

    **a cheap primary answer must never be withheld until an expensive decoration beside it is
    ready; and a count of rows belongs in the database, not in a loop that ships the rows.**

THE SIBLING, found and fixed in the same change. `router.get_prospect_evaluation` did the same thing
one endpoint over: with no month named it read EVERY row the prospect has — 137,374 and ~137 round
trips for the biggest one — and looked at two columns of each, purely to decide which month to open
on. Both now dereference one home.

WHAT THIS PROVES

 A. TWO READINGS, ONE SHAPE. The grouped reading (the database's `GROUP BY`, migration 1085) and the
    row-by-row reading produce IDENTICAL tallies on equivalent input, through one accumulator. A
    second shape builder is the divergence the index rules forbid.
 B. THE LIST DOES NOT COUNT. Source checks on `list_prospects`: it reads no feed rows and sets no
    `feed_rows`/`periods`, and there is exactly ONE place that decides how a tally is read.
 C. UNKNOWN IS NOT ZERO, on both sides of the wire. A prospect whose tally has not been read is
    reported as unread, not as a prospect with nothing uploaded; the screen prints `counting…`
    rather than a confident 0, and every counted column goes through ONE guard.
 D. THE SIBLING IS WIRED. The evaluation's month pick dereferences the tally and no longer pages the
    prospect's rows.
 E. REGRESSION, ARMED. The pre-fix shapes are reproduced and asserted WRONG, so this file fails if
    either defect comes back.
 F. HOUSE RULES. RULE TWO (no tenant/carrier/product names in the logic), the module stays DB-free,
    and the migration is idempotent with a REVERT note.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harnesslib import js_code_only, py_code_only  # noqa: E402
from app.modules.commcalc import prospect_feed_tally as pft  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ROUTER = os.path.join(HERE, "app", "modules", "commcalc", "router.py")
MODULE = os.path.join(HERE, "app", "modules", "commcalc", "prospect_feed_tally.py")
PAGE = os.path.join(ROOT, "frontend", "src", "app", "(platform)", "commcalc",
                    "store-takeover", "page.tsx")
MIGRATION = os.path.join(ROOT, "database", "migrations", "1085_prospect_feed_tally.sql")
CI = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

# THE MEASUREMENT THIS FIX RESTS ON, carried as data so the numbers in the docstring cannot drift
# from the numbers the checks use.
LIVE_ROWS = 202865
LIVE_PAGE = 1000
LIVE_ROUND_TRIPS = 203

passed = failed = 0


def ok(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"  — {detail}" if detail else ""))


def head(t):
    print(f"\n{t}\n" + "-" * len(t))


def fn_source(src, name):
    """The source text of one top-level function, by name."""
    m = re.search(rf"^def {re.escape(name)}\(.*?(?=\n(?:@|def |class )|\Z)", src,
                  flags=re.S | re.M)
    return m.group(0) if m else ""


router_src = open(ROUTER).read()
router_code = py_code_only(router_src)
module_src = open(MODULE).read()
page_src = open(PAGE).read()
page_code = js_code_only(page_src)
mig_src = open(MIGRATION).read() if os.path.exists(MIGRATION) else ""
ci_src = open(CI).read() if os.path.exists(CI) else ""

# ─────────────────────────────────────────────────────────────────────────────────────────────────
head("A. TWO READINGS OF ONE FACT PRODUCE ONE SHAPE")

# A realistic slice set: two prospects, three feeds, two months, one month-less row, and a feed with
# a single row (the shape that a `> 1` filter would silently drop).
SLICES = [
    ("p1", "feed_a", "2026-08", 3),
    ("p1", "feed_b", "2026-08", 2),
    ("p1", "feed_a", "2026-09", 1),
    ("p1", "feed_c", "", 4),
    ("p2", "feed_a", "2026-09", 5),
]
grouped = [{"prospect_id": p, "feed_key": f, "period": per, "n": n} for p, f, per, n in SLICES]
rows = [{"prospect_id": p, "feed_key": f, "period": per}
        for p, f, per, n in SLICES for _ in range(n)]

t_grouped = pft.from_grouped(grouped)
t_rows = pft.from_rows(rows)
ok("A1 the grouped reading and the row-by-row reading are identical",
   t_grouped == t_rows, f"{t_grouped} != {t_rows}")
ok("A2 every slice is counted, none merged",
   t_grouped["p1"]["2026-08"] == {"feed_a": 3, "feed_b": 2}, str(t_grouped.get("p1")))
ok("A3 the total is the rows that went in",
   sum(n for pr in t_rows.values() for per in pr.values() for n in per.values()) == len(rows))
ok("A4 a month-less row keeps its row and is grouped under the empty month, never dropped",
   t_grouped["p1"][""] == {"feed_c": 4}, str(t_grouped.get("p1", {}).get("")))
ok("A5 a feed with ONE row survives",
   t_grouped["p2"]["2026-09"] == {"feed_a": 5} and "feed_a" in pft.by_feed(t_grouped["p1"]))
ok("A6 periods come back newest first", pft.periods_of(t_grouped["p1"]) == ["2026-09", "2026-08", ""],
   str(pft.periods_of(t_grouped["p1"])))
ok("A7 by_feed is the same tally read the other way",
   pft.by_feed(t_grouped["p1"]) == {"feed_a": ["2026-09", "2026-08"],
                                    "feed_b": ["2026-08"], "feed_c": [""]},
   str(pft.by_feed(t_grouped["p1"])))
ok("A8 a zero count is not a slice (the database omits it, so the shape must too)",
   pft.from_grouped([{"prospect_id": "p9", "feed_key": "feed_a", "period": "2026-08", "n": 0}]) == {})
ok("A9 a row with no prospect or no feed is refused rather than keyed on an empty string",
   pft.from_rows([{"prospect_id": "", "feed_key": "feed_a", "period": "x"},
                  {"prospect_id": "p1", "feed_key": "", "period": "x"}]) == {})
ok("A10 a non-numeric count cannot poison the tally",
   pft.from_grouped([{"prospect_id": "p1", "feed_key": "f", "period": "p", "n": "many"}]) == {})
ok("A11 junk entries are skipped, not raised on",
   pft.from_grouped([None, "x", 7, {"prospect_id": "p1", "feed_key": "f", "period": "p", "n": 1}])
   == {"p1": {"p": {"f": 1}}})
ok("A12 ONE accumulator — both readings reach the shape through `_add`",
   len(re.findall(r"_add\(acc,", py_code_only(module_src))) == 2
   and py_code_only(module_src).count("def _add(") == 1)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
head("B. THE LIST DOES NOT COUNT")

lst = fn_source(router_code, "list_prospects")
ok("B1 `list_prospects` was found", bool(lst))
ok("B2 it reads no feed rows at all",
   "prospect_feed_row" not in lst and "read_all" not in lst, lst[-400:])
ok("B3 it sets neither `feed_rows` nor `periods` — an uncounted prospect must not render as empty",
   '"feed_rows"' not in lst and 'r["feed_rows"]' not in lst and 'r["periods"]' not in lst)
ok("B4 it still answers the question it is for (the prospects themselves)",
   "store_prospect" in lst and '"prospects": rows' in lst)
ok("B5 it names where the tally is served from, so the screen is not left guessing",
   '"tally_in"' in lst)

tally_ep = fn_source(router_code, "prospect_feed_tally")
reader = fn_source(router_code, "_prospect_tally_read")
ok("B6 the tally endpoint exists and is a separate route",
   bool(tally_ep) and '@router.get("/prospects/feed-tally")' in router_code)
ok("B7 ONE place decides how a tally is read", bool(reader)
   and len(re.findall(r'rpc\("prospect_feed_tally"', router_code)) == 1,
   f"{len(re.findall(chr(39) + 'rpc' + chr(39), router_code))}")
ok("B8 it prefers the database's own grouping",
   'rpc("prospect_feed_tally"' in reader and "from_grouped" in reader)
ok("B9 it still answers where that function is not applied yet",
   "from_rows" in reader and "read_all" in reader)
ok("B10 and it SAYS which reading it used, so a slow answer is visible",
   '"grouped_in_database"' in reader and '"counted_row_by_row"' in reader
   and "source" in tally_ep)
ok("B11 neither endpoint swallows a failure into a plausible empty tally",
   '"unavailable"' in reader)
ok("B12 the one home is dereferenced, not re-implemented, by the endpoint",
   "_prospect_tally_read(client, org_id)" in tally_ep)
ok("B13 the tally stays org-scoped", 'eq("org_id", org_id)' in reader
   and '"p_org_id": org_id' in reader)
ok("B14 the endpoint requires an org like every other sensitive read",
   "require_org(org_id)" in tally_ep)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
head("C. UNKNOWN IS NOT ZERO")

ok("C1 a prospect absent from the tally is reported UNREAD",
   pft.missing_tally(["p1", "p2", "p9"], t_grouped) == ["p9"],
   str(pft.missing_tally(["p1", "p2", "p9"], t_grouped)))
ok("C2 a prospect that was counted is not reported unread even with a small tally",
   pft.missing_tally(["p2"], t_grouped) == [])
ok("C3 a known-empty tally is distinguishable from an unknown one",
   pft.periods_of({}) == [] and pft.missing_tally(["p9"], {}) == ["p9"])
ok("C4 `missing_tally` is the ONE place that difference is decided",
   py_code_only(module_src).count("def missing_tally(") == 1
   and "missing_tally" in router_code)
ok("C5 the payload carries it to the screen", '"missing"' in tally_ep)
ok("C6 the screen has a third state for a row whose counts are not in yet",
   "tally_unknown" in page_code and "counting: boolean" in page_code)
ok("C7 every counted column goes through ONE guard rather than each testing the flag",
   len(re.findall(r"\bcounted\(r =>", page_code)) >= 4
   and page_code.count("const counted = ") == 1,
   str(len(re.findall(r"\bcounted\(r =>", page_code))))
ok("C8 the guard prints a word, never a zero",
   "'counting…'" in page_code)
ok("C9 the counted columns no longer read the tally straight out of the row",
   "get: (r: ProspectRow) => r.months" not in page_code
   and "get: (r: ProspectRow) => r.rows" not in page_code)
ok("C10 the name column is NOT behind the guard — the list must show while counts load",
   "{ header: 'Store being evaluated', get: (r: ProspectRow) => r.name" in page_code)
ok("C11 the Open button is not behind it either, so an evaluation can be opened at once",
   "onOpen(r.id)" in page_code and "counted(() =>" not in page_code)

# ─────────────────────────────────────────────────────────────────────────────────────────────────
head("D. THE SIBLING IS WIRED, NOT EXCUSED")

ev = fn_source(router_code, "get_prospect_evaluation") or router_code
month_pick = re.search(r"period_basis = None.*?chosen = _pev\.default_period", router_code,
                       flags=re.S)
mp = month_pick.group(0) if month_pick else ""
ok("D1 the month pick was found", bool(mp))
ok("D2 it no longer pages the prospect's own rows to learn which months exist",
   "prospect_feed_row" not in mp and "read_all" not in mp, mp)
ok("D3 it dereferences the tally", "_pft.by_feed" in mp and "_prospect_tally_read" in mp)
ok("D4 it still falls back to an empty basis rather than raising",
   "by_feed = {}" in mp)
ok("D5 the evaluation still reads the rows it genuinely needs (it computes money from them)",
   "_prospect_feed_rows" in router_code
   and "prospect_feed_row" in fn_source(router_code, "_prospect_feed_rows"))
ok("D6 exactly two surfaces read the tally, and both through the one reader",
   len(re.findall(r"_prospect_tally_read\(", router_code)) == 3,
   str(len(re.findall(r"_prospect_tally_read\(", router_code))))

# ─────────────────────────────────────────────────────────────────────────────────────────────────
head("E. REGRESSION, ARMED AGAINST BOTH DEFECTS")


def prefix_list_shape(prospects, tally):
    """The PRE-FIX list shape: counts folded into the list, absent meaning empty."""
    out = []
    for p in prospects:
        fr = tally.get(p, {})
        out.append({"id": p, "feed_rows": fr, "periods": sorted(fr, reverse=True)})
    return out


# The defect, reproduced: with the tally not yet read, the old shape says "no months, nothing
# uploaded" about a prospect holding 137,374 rows.
old = prefix_list_shape(["p1"], {})
ok("E1 the pre-fix shape reports an UNCOUNTED prospect as having nothing — the defect",
   old[0]["periods"] == [] and old[0]["feed_rows"] == {})
ok("E2 the fixed shape refuses to say that", pft.missing_tally(["p1"], {}) == ["p1"])
ok("E3 and the screen's flag carries it through to the cell",
   "tally_unknown: unread.has(id)" in page_code)
ok("E4 the cost that was paid for that wrong answer, stated as the arithmetic it was",
   -(-LIVE_ROWS // LIVE_PAGE) == LIVE_ROUND_TRIPS,
   f"{-(-LIVE_ROWS // LIVE_PAGE)} != {LIVE_ROUND_TRIPS}")
ok("E5 the docstring's measurement is the one the checks use",
   str(LIVE_ROWS) in module_src.replace(",", "") or "202,865" in module_src)
ok("E6 the row-by-row fallback is still CORRECT, merely slow — it ties to the grouped reading",
   pft.from_rows(rows) == pft.from_grouped(grouped))

# ─────────────────────────────────────────────────────────────────────────────────────────────────
head("F. HOUSE RULES")

BANNED = ("boost", "total wireless", "luxelink", "cellfonz", "empire", "humjot", "vzone",
          "t-mobile", "verizon", "att", "metro")
mod_logic = py_code_only(module_src).lower()
ok("F1 RULE TWO — no tenant, carrier or prospect name in the tally's logic",
   not [w for w in BANNED if w in mod_logic], str([w for w in BANNED if w in mod_logic]))
ok("F2 the module touches no database and no network",
   not re.search(r"\b(import requests|supabase|psycopg|httpx|urllib)\b", module_src))
ok("F3 it reads no clock, so the same input is the same tally forever",
   "datetime" not in module_src and "time.time" not in module_src)
ok("F4 the migration exists and is a function, not a table",
   "CREATE OR REPLACE FUNCTION commcalc.prospect_feed_tally" in mig_src)
ok("F5 it is idempotent", "CREATE OR REPLACE" in mig_src and "DROP TABLE" not in mig_src)
ok("F6 it carries a REVERT note", "-- REVERT:" in mig_src)
ok("F7 it is org-scoped inside the function body",
   "WHERE r.org_id = p_org_id" in mig_src)
ok("F8 it moves no money and writes nothing",
   not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER TABLE)\b", mig_src.upper()))
ok("F9 it is STABLE, so it cannot be mistaken for a writer", "STABLE" in mig_src)
ok("F10 the fix does not depend on the migration being applied",
   "from_rows" in reader)
ok("F11 this lock runs in CI", "harness_prospect_feed_tally.py" in ci_src)
ok("F12 and its own inputs are in CI's paths filter",
   "prospect_feed_tally.py" in ci_src)

print(f"\n{'=' * 78}\n{passed} passed / {failed} failed\n{'=' * 78}")
sys.exit(1 if failed else 0)
