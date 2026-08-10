"""Proof that the evidence/attention scans no longer silently drop rows.

Three production reads used a bare `.limit(200000)` against tables measured live at 234,610
(commcalc.raw_mi) and 205,886 (commcalc.raw_payment_detail) rows, with no ORDER BY — so 34,610 and
5,886 rows were discarded, and WHICH ones was arbitrary between calls.

This drives the real `recovery.engine._read_all` and `core.import_health._scan_all` against a stub
that returns rows page by page exactly as PostgREST does (`.range(lo, hi)` inclusive, capped at 1000
per response). The seeded table is deliberately LARGER than one page and not a multiple of it, so an
off-by-one in the paging arithmetic shows up as a wrong count rather than passing by luck.

Run: python3 harness_scan_truncation.py
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PAGE_CAP = 1000                      # PostgREST's own per-response ceiling
N_ROWS = 3_501                       # > 3 pages, deliberately NOT a multiple of PAGE_CAP
ROWS = [{"org_id": "ORG", "n": i, "category": ("A" if i % 2 else "B")} for i in range(N_ROWS)]
OTHER_ORG = [{"org_id": "OTHER", "n": -1, "category": "A"}]


class _Q:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        return _Q([r for r in self.rows if r.get(col) == val])

    def in_(self, col, vals):
        vs = set(vals)
        return _Q([r for r in self.rows if r.get(col) in vs])

    def limit(self, n):
        return _Q(self.rows[:n])

    def range(self, lo, hi):
        # PostgREST: inclusive bounds, and never more than PAGE_CAP rows in one response.
        return _Q(self.rows[lo:min(hi + 1, lo + PAGE_CAP)])

    def execute(self):
        return SimpleNamespace(data=list(self.rows))


class _Schema:
    def table(self, _name):
        return _Q(ROWS + OTHER_ORG)


class StubClient:
    def schema(self, _name):
        return _Schema()


def run():
    from app.modules.recovery import engine
    from app.modules.core import import_health

    ok = True

    # A single-shot read is capped by the transport at ONE page — this is the bug, demonstrated.
    one_shot = StubClient().schema("commcalc").table("raw_mi").eq("org_id", "ORG") \
        .range(0, 199_999).execute().data
    good = len(one_shot) == PAGE_CAP
    ok &= good
    print(f"  {'✓' if good else '✗'} a single .limit()/.range() shot returns {len(one_shot):,} of "
          f"{N_ROWS:,} rows — the truncation, reproduced")

    for name, fn in (("recovery.engine._read_all",
                      lambda: engine._read_all(StubClient(), "raw_mi", "n", org_id="ORG")),
                     ("import_health._scan_all",
                      lambda: import_health._scan_all(StubClient(), "commcalc", "raw_mi", "n",
                                                      org_id="ORG"))):
        rows = fn()
        good = len(rows) == N_ROWS
        ok &= good
        print(f"  {'✓' if good else '✗'} {name:32} returned {len(rows):,} / {N_ROWS:,} rows")

        # Every row exactly once — paging must not duplicate at the boundaries either.
        ns = [r["n"] for r in rows]
        good = len(set(ns)) == N_ROWS and max(ns) == N_ROWS - 1
        ok &= good
        print(f"  {'✓' if good else '✗'} {name:32} no duplicates, reached the final row")

        # org scoping survives pagination — a multi-tenant read must not widen while looping.
        good = all(r.get("org_id", "ORG") == "ORG" for r in rows)
        ok &= good
        print(f"  {'✓' if good else '✗'} {name:32} stayed org-scoped across every page")

    # IN (...) filters must survive too — recovery scopes asset_ledger by category list.
    rows = engine._read_all(StubClient(), "asset_ledger", "n", org_id="ORG", category=["A"])
    good = len(rows) == len([r for r in ROWS if r["category"] == "A"])
    ok &= good
    print(f"  {'✓' if good else '✗'} {'in_() filter across pages':32} returned {len(rows):,} rows")

    print("\n" + ("PASS — full scans, no silent truncation" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
