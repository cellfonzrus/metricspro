#!/usr/bin/env python3
"""
PROOF HARNESS — A PAGED READ IS A READ OF THE DATA (index §19.48, §CORE-05)
========================================================================

The lock `app/modules/core/feed_read.py` has named in its own docstring since 2026-10-06 and which
did not exist until 2026-10-14.

OWNER REPORT 2026-10-14, verbatim: *"do tehy mean there is only ne accoutn for 27 lines"* — about a
Store Takeover Evaluation row claiming **27 activated lines** on one subscriber account, with the
same five IMEIs printed three times over. The account carries **nine**.

THE CLASS, not the instance:

    **`OFFSET` / `range()` paging without an `ORDER BY` pages an UNORDERED set, so the backend may
    serve one row in two pages and never serve another at all.**

Postgres promises no row order without `ORDER BY` and does not have to — each page is a fresh plan.
Measured live 2026-10-14, `commcalc.prospect_feed_row`, one prospect holding 137,374 rows:

    no ORDER BY   → 137,374 rows returned, 88,294 distinct, 49,080 duplicate copies, 49,080 never served
    order=id.asc  → 137,374 rows returned, 137,374 distinct, 0 duplicates, 0 missing

A third of the feed was invisible to every surface reading it while another third was counted twice,
and nothing reported a problem: the loop still ended on a short page and still returned a plausible
count. That is the §19.48 failure mode again — a read that is wrong QUIETLY — and worse, because a
literal ceiling at least drops the same rows every time.

WHAT THIS PROVES

 A. THE ONE HOME PAGES ON A STABLE KEY. Driven over a fake backend that models PostgREST: when a
    read is ordered it serves a sorted slice, and when it is NOT it serves an arbitrarily permuted
    one — exactly what the live measurement above found. §A2 is the ARMED detector: it FAILS against
    the pre-fix module, because an unordered loop over that backend really does duplicate and lose
    rows.

 B. IT CANNOT UN-WIRE. Source checks: one `.range(` in the module and an `.order(` before it, the
    key is a named constant rather than spelled at the call, and the module still carries no row
    ceiling (the §19.48 invariant this file also now locks).

 C. THE SIBLINGS ARE COUNTED, NOT ASSUMED AWAY. An AST scan counts every `range()` read in
    `backend/app` with no `.order()` in its chain, and `PINNED_UNORDERED` must EQUAL that count:
    a new one fails the build, and fixing one requires lowering the pin in the same commit. The
    number is the honest state of the platform, not a claim that the platform is fixed. Negative
    controls prove the detector can tell an ordered chain from an unordered one.

 D. THE DUPLICATE LOOP IS GONE. `recovery/engine.py` kept its own copy of the page loop — its own
    docstring already warned that "with no ORDER BY, WHICH rows vanished was arbitrary from one call
    to the next" and it paged without one anyway. It dereferences the one home now.

DB-free, no I/O, no network.
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.core import feed_read as FR            # noqa: E402
from app.modules.core import module_graph as MG         # noqa: E402

APP = os.path.join(HERE, "app")
# WHICH FILE IS THE ONE HOME is not spelled here — it is read from the module graph, the one home
# for "which file answers this question". A lock carrying its own copy of that fact is the §19.18
# defect wearing a lock's hat, and the graph guard's own check H2 fails the build over it.
ONE_HOME = os.path.join(HERE, MG.FACTS["complete_feed_read"]["homes"][0])
RECOVERY = os.path.join(APP, "modules", "recovery", "engine.py")
SRC = open(ONE_HOME).read()

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


# ══ A FAKE BACKEND THAT BEHAVES LIKE THE REAL ONE ═════════════════════════════════════════════════
# The point of the fake is the UNSTABLE case. A paged read of an unordered set is not "random" in a
# way a test can wave at — it is a different permutation per page, which is why one row comes twice
# and another never comes. `permute` models that deterministically (a rotation by page index), so the
# detector is reproducible while still being the real failure.
class FakeResp:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Models a postgrest builder: `.order()` APPENDS (verified against the installed library's
    `base_request_builder.order`, which concatenates onto any existing order), `.range()` sets the
    window, `.execute()` serves it."""

    def __init__(self, table, log):
        self.t, self.log, self.orders, self.win = table, log, [], None

    def order(self, column, desc=False):
        self.orders.append(column)
        self.log.append(("order", column))
        return self

    def range(self, a, b):
        self.win = (a, b)
        self.log.append(("range", a, b))
        return self

    def execute(self):
        if self.t.raise_at is not None and self.win and self.win[0] == self.t.raise_at:
            raise RuntimeError("backend said no")
        rows = self.t.serve(self.orders, self.win)
        self.t.pages += 1
        return FakeResp(rows)


class FakeTable:
    def __init__(self, rows, stable=True, raise_at=None):
        self.rows, self.stable, self.raise_at = list(rows), stable, raise_at
        self.pages, self.log = 0, []

    def _ordered(self, orders):
        if orders:
            return sorted(self.rows, key=lambda r: tuple(r.get(c) for c in orders))
        if self.stable:
            return list(self.rows)
        # UNORDERED: a different permutation for every page the caller asks for.
        n = len(self.rows)
        k = (self.pages * 37 + 11) % max(1, n)
        return self.rows[k:] + self.rows[:k]

    def serve(self, orders, win):
        body = self._ordered(orders)
        a, b = win if win else (0, len(body) - 1)
        return body[a:b + 1]

    def factory(self):
        return lambda: FakeQuery(self, self.log)


def rows_of(n):
    return [{"id": i, "period": "2026-%02d" % (1 + i % 9), "v": i * 2} for i in range(n)]


print("══ A. THE ONE HOME PAGES ON A STABLE KEY ══════════════════════════════════════════════════")

t = FakeTable(rows_of(2500))
got = FR.read_all(t.factory(), page=1000)
check("A1 a stable backend: every row once, in key order, from a short final page",
      len(got) == 2500 and [r["id"] for r in got] == list(range(2500)) and t.pages == 3)

unstable = FakeTable(rows_of(2500), stable=False)
got = FR.read_all(unstable.factory(), page=1000)
ids = [r["id"] for r in got]
check("A2 ARMED — an UNORDERED backend still yields every row exactly once "
      "(this is the live 49,080-duplicate defect; it FAILS against the pre-fix module)",
      len(ids) == 2500 and len(set(ids)) == 2500,
      f"{len(ids)} rows, {len(set(ids))} distinct, {len(ids) - len(set(ids))} duplicate copies")

exact = FakeTable(rows_of(2000))
got = FR.read_all(exact.factory(), page=1000)
check("A3 a feed that is an exact multiple of the page size terminates on an empty page",
      len(got) == 2000 and exact.pages == 3)

t4 = FakeTable(rows_of(1200))
FR.read_all(t4.factory(), page=1000)
check("A4 the order is applied BEFORE the window on every page, never after",
      all(t4.log[i][0] == "order" and t4.log[i + 1][0] == "range" for i in range(0, len(t4.log), 2))
      and len(t4.log) == 4)


class PreOrdered(FakeTable):
    def factory(self):
        return lambda: FakeQuery(self, self.log).order("period")


t5 = PreOrdered(rows_of(1100))
FR.read_all(t5.factory(), page=1000)
check("A5 a caller's own order is KEPT and the key appended after it, so its sort survives and "
      "becomes total",
      [x[1] for x in t5.log if x[0] == "order"][:2] == ["period", FR.ORDER_KEY])

try:
    FR.read_all(FakeTable(rows_of(10)).factory(), order="")
    bad = True
except FR.IncompleteRead as e:
    bad, msg = False, str(e)
check("A6 a read with no order key is REFUSED, not served unordered", not bad and "stable order" in msg)

try:
    FR.read_all(FakeTable(rows_of(2500), raise_at=1000).factory(), page=1000)
    bad = True
except FR.IncompleteRead as e:
    bad, msg = False, str(e)
check("A7 a failed page raises IncompleteRead naming the offset and the key — never a short answer",
      not bad and "1000" in msg and FR.ORDER_KEY in msg and "loses rows" in msg)

check("A8 an empty feed is an empty list, not an error", FR.read_all(FakeTable([]).factory()) == [])

print("\n══ B. IT CANNOT UN-WIRE ═══════════════════════════════════════════════════════════════════")

check("B1 exactly one `.range(` in the one home, and an `.order(` on the same chain",
      SRC.count(".range(") == 1 and ".order(key).range(" in SRC)
check("B2 the key is a named constant, not spelled at the call site",
      'ORDER_KEY = "id"' in SRC and "order: str = ORDER_KEY" in SRC)
check("B3 §19.48 still holds — no row ceiling and no `limit=` in the one home",
      "limit" not in SRC.split("def read_all")[1].split("def ")[0]
      and "MAX_PAGES" in SRC)
check("B4 the live measurement is recorded where the next reader will see it",
      "88,294" in SRC and "49,080" in SRC)

print("\n══ C. THE SIBLINGS ARE COUNTED, NOT ASSUMED AWAY ══════════════════════════════════════════")


def chain_has_order(call):
    """Walk the receiver chain of a `.range(...)` call looking for `.order(...)` on it."""
    cur = call.func.value if isinstance(call.func, ast.Attribute) else None
    while cur is not None:
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
            if cur.func.attr == "order":
                return True
            cur = cur.func.value
        elif isinstance(cur, ast.Attribute):
            cur = cur.value
        else:
            return False
    return False


def unordered_range_sites(tree):
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "range" and not chain_has_order(n)):
            out.append(n.lineno)
    return out


# THE PIN. Measured 2026-10-14 over `backend/app`, the one home excluded. It may only go DOWN, and
# the equality is deliberate: converting a loop to the one home means lowering this number in the
# same commit, so the count on screen is always the real one.
PINNED_UNORDERED = 99

sites = {}
for dirpath, _dirs, files in os.walk(APP):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        p = os.path.join(dirpath, fn)
        if os.path.realpath(p) == os.path.realpath(ONE_HOME):
            continue
        try:
            tree = ast.parse(open(p).read())
        except SyntaxError:
            continue
        hits = unordered_range_sites(tree)
        if hits:
            sites[os.path.relpath(p, HERE)] = hits

total = sum(len(v) for v in sites.values())
check(f"C1 every unordered paged read in backend/app is counted — the pin is {PINNED_UNORDERED}",
      total == PINNED_UNORDERED,
      f"found {total} in {len(sites)} files"
      + ("" if total == PINNED_UNORDERED else
         " — if you FIXED one, lower PINNED_UNORDERED; if you ADDED one, it reads the feed wrong"))
check("C2 the one home itself is not on that list", not any(
    os.path.realpath(os.path.join(HERE, f)) == os.path.realpath(ONE_HOME) for f in sites))

_NEG_ORDERED = "rows = q.order('id').range(start, start + page - 1).execute().data"
_NEG_UNORDERED = "rows = q.range(start, start + page - 1).execute().data"
_NEG_ORDER_LAST = "rows = q.range(0, 99).order('id').execute().data"
check("C3 negative controls — the detector tells an ordered chain from an unordered one, and does "
      "not accept an order applied AFTER the window",
      unordered_range_sites(ast.parse(_NEG_ORDERED)) == []
      and len(unordered_range_sites(ast.parse(_NEG_UNORDERED))) == 1
      and len(unordered_range_sites(ast.parse(_NEG_ORDER_LAST))) == 1)
check("C4 …and a plain `range(10)` loop is not mistaken for a paged read",
      unordered_range_sites(ast.parse("for i in range(10):\n    pass\n")) == [])

print("\n══ D. THE DUPLICATE LOOP IS GONE ══════════════════════════════════════════════════════════")

REC = open(RECOVERY).read()
check("D1 recovery/engine.py has no page loop of its own left",
      ".range(" not in REC and "while start" not in REC and "start += page" not in REC)
check("D2 …it dereferences the one home instead",
      "feed_read" in REC and "read_all(" in REC)
check("D3 …and all three appeal evidence reads are still org-scoped",
      REC.count("org_id=org_id") == 3)

print("\n" + "=" * 98)
print(f"{len(PASS)} passed / {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
print("A paged read is a read of the data: one stable key, every row once, and the loops that still "
      f"page without one are counted ({PINNED_UNORDERED}), not assumed away.")
