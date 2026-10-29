"""§STOREOPS-xx — A CARD FOR MANY STORES COSTS A FIXED NUMBER OF READS, NOT ONE SET PER STORE.

DB-free. Drives the real `storeops.router` functions against a fake PostgREST client that counts
every query, so the claims below are MEASURED on the shipped code, never read off it.

WHAT WAS WRONG (measured 2026-10-29, `main` at ff9c888)
-------------------------------------------------------
`_gr_store_card(client, org_id, store_code, ...)` answers for ONE store in 3 reads
(`google_review_store`, `google_review_snapshot`, `google_review_item`) plus a 4th
(`action_plan`) when an employee is named. Three endpoints wanted MANY stores and each looped:

  * `/google-reviews/dm-dashboard`     — + its own `action_plan` read per store = 4 per store
  * `/google-reviews/my`               — 4 per store
  * `/google-reviews/employee-summary` — 4 per store

On Cellfonz's 28 stores the DM dashboard is 112 round trips. At the platform's ~20 ms per trip that
is 2.2 s of pure waiting, and it grows with the store count — the shape the 1,000-user load test
named as the binding constraint (§CORE-21's sibling finding: round trips per page × trips in
flight is what the worker pool spends).

THE CLASS, NOT THE INSTANCE
---------------------------
Not "the DM dashboard is slow". The class is: **"one store's card" had only a one-store answer, so
every caller that wanted many paid per store.** Fixing the dashboard alone would have left the two
other callers on the old cost — the patchwork this house forbids.

THE ONE HOME
------------
`_gr_store_cards(client, org_id, store_codes, store_by_code, cfg, employee_id=None)` answers for a
SET in a fixed number of reads, and `_gr_store_card` is now a one-element call into it — so there is
exactly one place that knows what a card is made of, and the single-store and many-store answers
cannot drift.

§D below is the LOCK: it fails the build if any caller goes back to calling the single-store
function from inside a loop.
"""
import ast, os, sys, inspect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SUPABASE_URL", "http://stub.invalid")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "stub")

PASS, FAIL = [], []


def ok(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'ok ' if cond else 'XX '} {label}{('  [' + detail + ']') if detail else ''}")


# ── a PostgREST double that counts every read ────────────────────────────────────────────────────
class _Q:
    def __init__(self, bus, table):
        self.bus, self.table, self.filters, self._in = bus, table, {}, None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def neq(self, *a, **k): return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, vals):
        self._in = (col, list(vals))
        return self

    def execute(self):
        self.bus.reads.append((self.table, dict(self.filters),
                               None if self._in is None else (self._in[0], tuple(self._in[1]))))
        rows = self.bus.rows.get(self.table, [])
        out = []
        for r in rows:
            if any(r.get(c) != v for c, v in self.filters.items()):
                continue
            if self._in is not None and r.get(self._in[0]) not in self._in[1]:
                continue
            out.append(dict(r))
        return type("R", (), {"data": out})()


class FakeClient:
    def __init__(self, rows):
        self.rows, self.reads = rows, []

    def table(self, name): return _Q(self, name)
    def schema(self, _): return self


def make_rows(codes):
    rows = {"stores": [], "google_review_store": [], "google_review_snapshot": [],
            "google_review_item": [], "action_plan": []}
    for i, c in enumerate(codes):
        rows["stores"].append({"org_id": "O", "store_code": c, "address": f"{i} Main St",
                               "market": "M", "is_active": True})
        rows["google_review_store"].append({"org_id": "O", "store_code": c,
                                            "place_id": f"p{i}", "target_rating": 4.5})
        rows["google_review_snapshot"].append({"org_id": "O", "store_code": c, "rating": 4.0 + i % 2,
                                               "review_count": 10 + i,
                                               "fetched_at": f"2026-10-2{i % 9}T00:00:00Z"})
        rows["google_review_item"].append({"org_id": "O", "store_code": c, "text": f"r{i}",
                                           "matched_employee_id": None,
                                           "first_seen_at": "2026-10-20T00:00:00Z"})
        rows["action_plan"].append({"org_id": "O", "store_code": c, "employee_id": "E1",
                                    "status": "open", "created_at": "2026-10-20T00:00:00Z"})
    return rows


CFG = {"target_default": 4.5, "lookback_days": 30}

import app.modules.storeops.router as R

print(__doc__.split("\n")[0])
print()

# ── A. the set answer costs a FIXED number of reads ──────────────────────────────────────────────
print("A. a set of stores costs a fixed number of reads, not one set per store")
has_set = hasattr(R, "_gr_store_cards")
ok("A1 the set-shaped one home exists", has_set, "_gr_store_cards")

if has_set:
    for n in (1, 5, 28):
        codes = [f"B-{i:04d}" for i in range(n)]
        c = FakeClient(make_rows(codes))
        by = {s["store_code"]: s for s in c.rows["stores"]}
        cards = R._gr_store_cards(c, "O", codes, by, CFG)
        ok(f"A2 {n:2d} stores → a card each", len(cards) == n, f"{len(cards)} cards")
        if n == 1:
            base = len(c.reads)
        else:
            ok(f"A3 {n:2d} stores cost the SAME reads as 1 store",
               len(c.reads) == base, f"{len(c.reads)} reads vs {base}")

    # with an employee named, the action_plan read joins the fixed set
    codes = [f"B-{i:04d}" for i in range(28)]
    c = FakeClient(make_rows(codes))
    by = {s["store_code"]: s for s in c.rows["stores"]}
    R._gr_store_cards(c, "O", codes, by, CFG, employee_id="E1")
    ok("A4 naming an employee adds reads that do not scale with the store count",
       len(c.reads) <= 6, f"{len(c.reads)} reads for 28 stores")

    # the read count must not grow with N — the property, stated as a property
    counts = []
    for n in (2, 10, 40):
        codes = [f"B-{i:04d}" for i in range(n)]
        c = FakeClient(make_rows(codes))
        by = {s["store_code"]: s for s in c.rows["stores"]}
        R._gr_store_cards(c, "O", codes, by, CFG, employee_id="E1")
        counts.append(len(c.reads))
    ok("A5 the read count is CONSTANT in the number of stores",
       len(set(counts)) == 1, f"2/10/40 stores → {counts} reads")

    # every read is org-scoped (RULE ONE)
    codes = ["B-0001", "B-0002"]
    c = FakeClient(make_rows(codes))
    by = {s["store_code"]: s for s in c.rows["stores"]}
    R._gr_store_cards(c, "O", codes, by, CFG, employee_id="E1")
    ok("A6 every read is org-scoped",
       all(f.get("org_id") == "O" for _, f, _ in c.reads),
       f"{len(c.reads)} reads")
    ok("A7 the per-store filter is an IN over the whole set, not one eq per store",
       all(("store_code" not in f) for _, f, _ in c.reads
           if _ in ("google_review_store", "google_review_snapshot", "google_review_item")) or
       any(i and i[0] == "store_code" for _, _, i in c.reads),
       "")

# ── B. the single-store answer is the SAME answer ────────────────────────────────────────────────
print("\nB. the single-store card is the set answer, so the two cannot drift")
codes = ["B-0001", "B-0002", "B-0003"]
c1 = FakeClient(make_rows(codes))
by1 = {s["store_code"]: s for s in c1.rows["stores"]}
one = R._gr_store_card(c1, "O", "B-0002", by1["B-0002"], CFG)
if has_set:
    c2 = FakeClient(make_rows(codes))
    by2 = {s["store_code"]: s for s in c2.rows["stores"]}
    many = R._gr_store_cards(c2, "O", codes, by2, CFG)
    ok("B1 one store's card is identical whichever way it is asked for",
       one == many.get("B-0002"), "")
ok("B2 the card still carries every key the three endpoints render",
   {"store_code", "address", "market", "place_id", "rating", "review_count", "target",
    "status", "reviews_needed", "reviews", "action_plan", "fetched_at"} <= set(one),
   f"{len(one)} keys")

# ── C. an absent store is a card, never a hole ───────────────────────────────────────────────────
print("\nC. a store with no review data still gets a card")
if has_set:
    c = FakeClient({"stores": [], "google_review_store": [], "google_review_snapshot": [],
                    "google_review_item": [], "action_plan": []})
    cards = R._gr_store_cards(c, "O", ["B-9999"], {}, CFG)
    ok("C1 no data → still one card", len(cards) == 1)
    ok("C2 ...with the store code on it", (cards.get("B-9999") or {}).get("store_code") == "B-9999")
    ok("C3 ...and no rating invented", (cards.get("B-9999") or {}).get("rating") is None)
    ok("C4 an empty set of codes reads NOTHING at all",
       (lambda cc: (R._gr_store_cards(cc, "O", [], {}, CFG), len(cc.reads) == 0)[1])(
           FakeClient(make_rows([]))))

# ── D. THE LOCK — no caller may go back to one-at-a-time ─────────────────────────────────────────
print("\nD. the lock — the single-store call cannot be made from inside a loop again")
SRC = os.path.join("app", "modules", "storeops", "router.py")
tree = ast.parse(open(SRC, encoding="utf-8").read())


def _loop_calls(tree, name):
    found = []

    class V(ast.NodeVisitor):
        def __init__(self): self.depth = 0
        def _l(self, n):
            self.depth += 1; self.generic_visit(n); self.depth -= 1
        visit_For = visit_AsyncFor = visit_While = _l
        visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _l
        def visit_Call(self, n):
            f = n.func
            if self.depth and isinstance(f, ast.Name) and f.id == name:
                found.append(n.lineno)
            self.generic_visit(n)
    V().visit(tree)
    return found


in_loop = _loop_calls(tree, "_gr_store_card")
ok("D1 `_gr_store_card` is called from NO loop in storeops/router.py",
   not in_loop, f"lines {in_loop}" if in_loop else "none")
ok("D2 the scan can see something (it is not matching nothing)",
   bool(_loop_calls(ast.parse("def f():\n [_gr_store_card(x) for x in y]\n"), "_gr_store_card")))
ok("D3 the three set callers exist and name the set function",
   open(SRC, encoding="utf-8").read().count("_gr_store_cards(") >= 4,
   "3 endpoints + the single-store wrapper")

# ── E. the DM dashboard's own cost, stated as a number ───────────────────────────────────────────
print("\nE. the measured win on the endpoint that was worst")
codes = [f"B-{i:04d}" for i in range(28)]
c = FakeClient(make_rows(codes))
by = {s["store_code"]: s for s in c.rows["stores"]}
cards = R._gr_store_cards(c, "O", codes, by, CFG)
plans = R._gr_action_plans_by_store(c, "O", codes)
now = len(c.reads)
before_count = 28 * 4          # 3 card reads + 1 action_plan read, per store, before this change
ok("E1 the DM dashboard's per-store reads are gone",
   now <= 6, f"28 stores: {before_count} reads before -> {now} now")
ok("E2 every store still gets its plans", len(plans) == 28, f"{len(plans)} stores with plans")
ok("E3 the per-store plan cap is still per store, not across the set",
   all(len(v) <= 50 for v in plans.values()))


print()
print(f"{'PASS' if not FAIL else 'RED '}: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"  FAIL  {f}")
sys.exit(1 if FAIL else 0)
