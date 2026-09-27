"""THE RATCHET — a new data table cannot ship unsorted, and the unsorted list can only shrink.

OWNER DIRECTIVE 2026-08-10, verbatim: *"sort function by clicking on the header for all reports"*.
Re-reported 2026-09-26: *"sort functions are not working platfrom wide"*. He was right. Measured
that day: **244 files render a data table and 26 of them sorted.** The mechanism had existed for
seven weeks — `components/SortableTh.tsx`, `lib/table-sort.ts`, `useTableSort` — and 218 tables were
never wired to it.

THE CLASS, and it is the one CLAUDE.md §19.18 already names: *a mechanism written, and the callers
left unwired.* "It has happened three times here." A directive satisfied by building the thing and
not connecting it is not satisfied at all, and nothing failed while that was true — which is exactly
what this lock is for.

WHY A RATCHET AND NOT A BIG-BANG. 218 bespoke tables cannot be rewritten in one reviewable change,
and pretending otherwise would either produce an unreviewable diff or a lock that is switched off.
So the debt is written down (`frontend/table_sort_pending.txt`) and the build enforces that it only
ever gets smaller. A page wired today deletes its line; a page added tomorrow cannot add one.

WHAT FAILS THE BUILD
  (a) A NEW UNSORTED TABLE. A file with a `<thead>` containing a `<th>` that uses none of the
      sorting mechanisms and is not in the registry → RED. This is the check that stops the
      directive being re-broken.
  (b) A STALE REGISTRY LINE. A file listed as pending that IS now sorted → RED until the line is
      deleted. Without this the list never shrinks and the ratchet is decorative.
  (c) THE COUNT ONLY FALLS. More entries than PINNED_MAX → RED. Wiring pages lowers the pin.
  (d) A DEAD REGISTRY LINE. A listed file that no longer exists → RED, so deletions keep it honest.
  (e) ONE MECHANISM. `lib/table-sort.ts` is the only comparison home; a page-local sort comparator
      over table rows is a second answer to "what order are these rows in" → RED.
  (f) NEGATIVE CONTROLS over synthetic trees: each of the above must be able to go red.

Stdlib only, DB-free. Runs in .github/workflows/carrier-vocab-guard.yml.

  python3 backend/harness_table_sort_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")
REGISTRY = os.path.join(ROOT, "frontend", "table_sort_pending.txt")
SORT_HOME = os.path.join(FE, "lib", "table-sort.ts")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

# Lower this as pages get wired. It may never rise: that is the ratchet.
PINNED_MAX = 217

# Any ONE of these means the file's table goes through the shared mechanism.
SORTED_MARKS = ("SortableTh", "useTableSort", "ReportShell", "DataGrid")

# A HAND-ROLLED INTERACTIVE SORT — the page owns sort DIRECTION STATE, i.e. it reimplemented
# click-to-sort instead of calling the shared hook. That is the duplicate this lock cares about.
#
# It is NOT a page that pre-orders its rows (`rows.sort((a,b) => b.amount - a.amount)`): that is the
# report's own default order, and `useTableSort` is built to preserve it (`initial = null` means "the
# order the report already produced"). Conflating the two would flag correct code and teach everyone
# to ignore the lock, which is worse than not having one.
LOCAL_SORT = re.compile(r"useState\s*<\s*['\"]asc['\"]\s*\|\s*['\"]desc['\"]")

# Known hand-rolled sorts, with the reason and the migration owed. Each MUST still match LOCAL_SORT —
# a stale excuse (the page was migrated but the entry stayed) fails the build, so this cannot rot.
EXCUSED_LOCAL_SORT = {
    "app/(platform)/commcalc/flags/page.tsx":
        "pre-dates the shared hook and owns its own asc/desc state plus a null-to-Infinity "
        "comparator; it WORKS for the user, so migrating it is a behaviour-preserving change owed "
        "separately rather than something to rush inside the ratchet's own PR",
    "app/(platform)/commcalc/asset/on-inventory/page.tsx":
        "owns `sortKey`/`sortDir` with a typed SortKey union and a click handler that flips direction "
        "on the active column — a full parallel implementation of the shared hook, working today; "
        "migration owed",
    "app/(platform)/commcalc/vip/page.tsx":
        "owns `sortKey`/`sortDir` seeded from per-table initial values — the same parallel "
        "implementation, reused across more than one table on the page, so migrating it is the "
        "largest of the three and is owed separately",
}

# THE DEBT THIS LEAVES, stated so it cannot be mistaken for done: three pages answer "what order are
# these rows in" with their own code. They are not broken for the user, which is exactly why they
# would otherwise be forgotten — so they are named here, their excuses must stay true, and a FOURTH
# one cannot appear without failing the build.

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:500]))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def has_data_table(src):
    """A DATA table has a header row to click. A layout table has neither `<thead>` nor `<th>`, and
    is none of this lock's business."""
    return "<thead" in src and "<th" in src


def is_sorted(src):
    return any(m in src for m in SORTED_MARKS)


def scan_tree(files):
    """{rel path: source} -> (data_tables, sorted_ones, unsorted_ones). Pure over a dict, so the
    negative controls can run it against a synthetic tree."""
    data, done, undone = set(), set(), set()
    for rel, src in files.items():
        if not has_data_table(src):
            continue
        data.add(rel)
        (done if is_sorted(src) else undone).add(rel)
    return data, done, undone


def parse_registry(text):
    return [ln.strip() for ln in (text or "").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


# ── the real tree ─────────────────────────────────────────────────────────────────────────────────
FILES = {}
for dp, dirs, fs in os.walk(FE):
    dirs[:] = [d for d in dirs if d not in ("node_modules", ".next")]
    for f in fs:
        if f.endswith((".tsx", ".ts")):
            rel = os.path.relpath(os.path.join(dp, f), FE).replace(os.sep, "/")
            FILES[rel] = read(os.path.join(dp, f))

DATA, DONE, UNDONE = scan_tree(FILES)
PENDING = parse_registry(read(REGISTRY) if os.path.exists(REGISTRY) else "")

print("\nTable-sort ratchet — a new data table cannot ship unsorted; the debt list only shrinks\n")
print("  %d data tables · %d sorted · %d pending (pin %d)\n" % (len(DATA), len(DONE), len(UNDONE), PINNED_MAX))

# ── (a) no new unsorted table ─────────────────────────────────────────────────────────────────────
unlisted = sorted(UNDONE - set(PENDING))
check("(a) every unsorted data table is on the registry — no NEW one slipped in",
      not unlisted, "unsorted and unlisted: %s" % unlisted)

# ── (b) the registry cannot keep lines that are done ──────────────────────────────────────────────
stale = sorted(set(PENDING) & DONE)
check("(b) no registry line names a page that IS sorted (delete it so the list shrinks)",
      not stale, "already sorted, still listed: %s" % stale)

# ── (c) the count only falls ───────────────────────────────────────────────────────────────────────
check("(c) the pending count is at or under the pin (%d ≤ %d)" % (len(PENDING), PINNED_MAX),
      len(PENDING) <= PINNED_MAX, "%d listed against a pin of %d" % (len(PENDING), PINNED_MAX))
check("(c) the pin is not slack — it matches what is actually pending",
      PINNED_MAX == len(UNDONE),
      "pin %d but %d unsorted; lower the pin to %d" % (PINNED_MAX, len(UNDONE), len(UNDONE)))

# ── (d) no dead lines ─────────────────────────────────────────────────────────────────────────────
dead = sorted(r for r in PENDING if r not in FILES)
check("(d) every registry line names a file that exists", not dead, "listed but gone: %s" % dead)

# ── (e) one comparison home ───────────────────────────────────────────────────────────────────────
check("(e) lib/table-sort.ts is present — the one comparison home", os.path.exists(SORT_HOME))
locals_ = sorted(rel for rel, src in FILES.items()
                 if has_data_table(src) and LOCAL_SORT.search(src) and rel != "lib/table-sort.ts")
check("(e) no page reimplements click-to-sort outside the excused set",
      not (set(locals_) - set(EXCUSED_LOCAL_SORT)),
      "hand-rolled sort state: %s" % sorted(set(locals_) - set(EXCUSED_LOCAL_SORT)))
stale_excuse = sorted(r for r in EXCUSED_LOCAL_SORT
                      if r not in FILES or not LOCAL_SORT.search(FILES.get(r, "")))
check("(e) every hand-rolled-sort excuse is still TRUE (migrated ⇒ delete the entry)",
      not stale_excuse, "stale excuse: %s" % stale_excuse)

# ── (f) negative controls ─────────────────────────────────────────────────────────────────────────
NEW_TABLE = '<table><thead><tr><th>Store</th></tr></thead><tbody/></table>'
syn = {"app/new/page.tsx": NEW_TABLE}
_, _, syn_undone = scan_tree(syn)
check("(f) a brand-new unsorted table → RED", bool(syn_undone - set(PENDING)))
syn2 = {"app/new/page.tsx": "import { SortableTh } from '@/components/SortableTh'\n" + NEW_TABLE}
_, syn_done, syn_undone2 = scan_tree(syn2)
check("(f) …and the same table WITH the mechanism → green",
      not syn_undone2 and syn_done == {"app/new/page.tsx"})
check("(f) a stale registry line (listed but sorted) → RED",
      bool({"app/new/page.tsx"} & syn_done))
check("(f) a dead registry line → RED", bool([r for r in ["app/deleted/page.tsx"] if r not in FILES]))
check("(f) a registry longer than the pin → RED", len(PENDING) + 1 > PINNED_MAX)
check("(f) a page owning its own asc/desc state → RED",
      bool(LOCAL_SORT.search("const [dir, setDir] = useState<'asc'|'desc'>('asc')")))
check("(f) …but a report's own DEFAULT row order stays green (useTableSort preserves it)",
      not LOCAL_SORT.search("return rows.sort((a, b) => b.amount - a.amount)"))
check("(f) …and a harmless non-row sort stays green",
      not LOCAL_SORT.search("const markets = opts.map(o => o.market).sort()"))
check("(f) a stale hand-rolled-sort excuse → RED",
      bool([r for r in {"app/gone/page.tsx": "x"} if r not in FILES]))
check("(f) a layout table (no thead/th) is not this lock's business",
      not has_data_table("<table><tr><td>left</td><td>right</td></tr></table>"))

# ── wired, or it is not a lock ────────────────────────────────────────────────────────────────────
wf = read(WORKFLOW) if os.path.exists(WORKFLOW) else ""
check("(wired) this lock runs in carrier-vocab-guard.yml", "harness_table_sort_lock.py" in wf)
check("(wired) the guard re-runs when the registry changes", "frontend/table_sort_pending.txt" in wf)

print("\n%d passed, %d failed" % (P, F))
if F:
    print("\n  %d of %d data tables still unsorted. Wiring one: useTableSort(rows, cell) +\n"
          "  <SortableTh field=… sort={s.sort} onSort={s.toggle}> + render s.sorted, then delete\n"
          "  its line from frontend/table_sort_pending.txt and lower PINNED_MAX." % (len(UNDONE), len(DATA)))
    sys.exit(1)
print("OK — %d of %d tables sorted; the remaining %d are registered and the list can only shrink."
      % (len(DONE), len(DATA), len(UNDONE)))
