"""A COLUMN THAT DRAWS ITS OWN CELL IS DRAWN — every table taking an `ExportColumn` honours `render`.

OWNER REPORT 2026-10-15, verbatim: *"it did not let is enter"* — the two seller-declared cells of
index §COMMCALC-03.26 could not be typed into. The box was never on the screen to type into.

THE CLASS, and it is §19.18's again: *one question, two answers, one of them silently wrong.* Two
components take the same column definition. `components/DataGrid.tsx` honoured
`ExportColumn.render` — the declared way a column draws its own cell — and `components/
ReportShell.tsx` ignored it and printed `get` regardless. Nothing failed while that was true: the
column was accepted, the callback was never called, and the page showed a figure where its author
had put a control. THREE shipped columns were dead that way, all on one page, for as long as they
had existed:

  * the takeover evaluations list's **Open** button — with the page copy directly above it reading
    "Press Open on any row". The Prospect dropdown was the only way into an evaluation.
  * its **Compare with our store** picker (shipped 2026-10-14, PR #476).
  * the two **seller-declared cells** (shipped 2026-10-14, PR #481), which is the one the owner hit.

THE FIX IS THE MECHANISM, not the cells: `ReportShell` now has ONE `bodyCell(c, r)` home for what a
body cell shows, dereferenced by both of its row paths, and all three columns light up together.
This lock is the part that stops it un-wiring.

WHAT THIS LOCK HOLDS
  (a) EVERY table component that takes an `ExportColumn` and draws body cells honours `render`.
      The subject set is DERIVED — a component is in scope because it draws a `<td` whose content it
      reads off a handed column (`c.get(`), never because it is named — and the derived set is then
      asserted against a NAMED roster, each member carrying its reason. Never a lower bound on the
      count: that is the weak form §CORE-10's gate B bans, where a subject dropping out of the scan
      does not fail its assertion but stops having one. So a new table joins the roster deliberately
      and one that leaves says so.
  (b) `ReportShell` has exactly ONE body-cell home and both row paths (plain and grouped) go
      through it. A second inline `isMoney(c) ? money(c.get(r))` body cell is the copy that caused
      this, so it fails the build.
  (c) `render` STAYS DISPLAY-ONLY. The export payload, the sort/filter/group keys and the subtotals
      read `get`, so a downloaded file carries the figure the control was bound to rather than a
      control nobody can open. `lib/export.tsx` must never call a column's `render`.
  (d) A COLUMN'S `render` REACHES A TABLE THAT HONOURS IT. Every page declaring `render:` on a
      column is checked — also derived — so no page can hand one to a component outside (a).
  (e) NEGATIVE CONTROLS over synthetic sources: each of the above must be able to go red.

WHY NOT A TYPE. `render?:` is optional by design (most columns want none), so no compiler can tell
"this component may be handed a column with a render" from "this component honours one". The
distinction is behavioural, which is why it needs a behavioural check.

Stdlib only, DB-free. Runs in .github/workflows/carrier-vocab-guard.yml.

  python3 backend/harness_column_render_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")
EXPORT_HOME = os.path.join(FE, "lib", "export.tsx")
SHELL = os.path.join(FE, "components", "ReportShell.tsx")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

# WHAT PUTS A FILE IN SCOPE, and the reasoning is the whole lock. A file is a GENERIC COLUMN
# RENDERER when it draws a table cell whose content it reads off a column object it was handed —
# `c.get(r)` inside a `<td`. That is precisely the shape that can silently drop a renderer, and it
# catches a bespoke page table (the CRM's) as surely as a shared component. It deliberately does NOT
# catch a page that hand-writes `<td>{r.day}</td>` per field while keeping an ExportColumn list for
# its export (closing/accountability): such a page reads no column accessor, so there is nothing
# for it to honour or drop.
GENERIC_CELL = re.compile(r"\b(c|col|column)\.get\(")
MENTIONS_COLUMN = re.compile(r"\bExportColumn\b")
# How a file honours one. Either spelling of the property access counts.
HONOURS = re.compile(r"\b(c|col|column)\.render\b|\?\.render\b")

# THE ONE EXCUSED MEMBER, named with its reason so the excuse cannot rot: lib/export.tsx builds the
# PDF and Print HTML, which is a FILE, and rule (c) is that a file carries the figure rather than a
# control nobody can open. It must therefore NOT honour `render`, and (c) asserts exactly that — so
# excusing it here removes nothing from the build's reach.
EXPORT_RENDERS_FILES = "lib/export.tsx"
# A column declaring that it draws its own cell.
DECLARES = re.compile(r"\brender:\s*\(")
# The copy that caused this: a body cell formatted inline from `get` with no renderer consulted.
INLINE_BODY = re.compile(r"\{isMoney\(c\)\s*\?\s*money\(c\.get\(r\)\)\s*:\s*str\(c\.get\(r\)\)\}")

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


def draws_body_cells(src):
    """A TABLE component emits body cells. A page that merely defines columns does not own a `<td`
    of its own for them, and a type file owns none at all."""
    return "<td" in src


def takes_columns(src):
    """DERIVED membership: the file reads a handed column's own accessor."""
    return bool(MENTIONS_COLUMN.search(src)) and bool(GENERIC_CELL.search(src))


def table_components(files):
    """{rel: src} -> (tables, honouring, ignoring). Pure over a dict so the controls can run it
    against a synthetic tree. A table is a file that renders a column's accessor into a body cell;
    the one excused member is dropped by NAME, and (c) holds it to the opposite rule."""
    tables, honour, ignore = set(), set(), set()
    for rel, src in sorted(files.items()):
        if rel == EXPORT_RENDERS_FILES:
            continue
        if not (takes_columns(src) and draws_body_cells(src)):
            continue
        tables.add(rel)
        (honour if HONOURS.search(src) else ignore).add(rel)
    return tables, honour, ignore


def declaring_files(files):
    """Pages that give a column its own renderer."""
    return {rel for rel, src in files.items() if DECLARES.search(src)}


# ── the real tree ─────────────────────────────────────────────────────────────────────────────────
FILES = {}
for dp, dirs, fs in os.walk(FE):
    dirs[:] = [d for d in dirs if d not in ("node_modules", ".next")]
    for fn in fs:
        if fn.endswith((".tsx", ".ts")):
            full = os.path.join(dp, fn)
            FILES[os.path.relpath(full, FE).replace(os.sep, "/")] = read(full)

TABLES, HONOUR, IGNORE = table_components(FILES)
DECLARERS = declaring_files(FILES)

print("ReportShell / DataGrid and friends: %d table component(s) take an ExportColumn, "
      "%d honour `render`" % (len(TABLES), len(HONOUR)))

# ── (a) every table component honours a column's renderer ─────────────────────────────────────────
# THE ROSTER, NAMED — never a bare `len(TABLES) >= 2`. A lower bound on a derived set is the weak
# form §CORE-10's gate B exists to ban: a subject that drops out of the scan does not fail its
# assertion, it stops having one. So the set is declared by name with each member's reason, and a
# member that leaves OR appears fails the build — a new table joins this roster deliberately.
EXPECTED_TABLES = {
    "components/ReportShell.tsx":
        "the shared report table — every report page's grid, and the one that dropped `render`",
    "components/DataGrid.tsx":
        "the other shared table (TanStack-backed), which honoured it all along",
    "app/(platform)/crm/reports/page.tsx":
        "the CRM's own bespoke table over the shared column type — wired 2026-10-15 in the same "
        "change, before it had cost anybody a control",
}
check("(a) the roster of tables is exactly what is declared — a subject that leaves names itself",
      TABLES == set(EXPECTED_TABLES),
      {"left": sorted(set(EXPECTED_TABLES) - TABLES), "appeared": sorted(TABLES - set(EXPECTED_TABLES))})
check("(a) EVERY table component taking an ExportColumn honours `render`", not IGNORE, sorted(IGNORE))
for _rel, _why in sorted(EXPECTED_TABLES.items()):
    check("(a) %s honours it — %s" % (_rel, _why), _rel in HONOUR)

# ── (b) ONE body-cell home in ReportShell, and both row paths on it ───────────────────────────────
shell = read(SHELL)
check("(b) ReportShell declares exactly one body-cell home", shell.count("const bodyCell =") == 1,
      shell.count("const bodyCell ="))
check("(b) both of its row paths dereference that home (plain rows and grouped rows)",
      shell.count("bodyCell(c, r)") == 2, shell.count("bodyCell(c, r)"))
check("(b) no body cell is formatted inline from `get` any more — that copy WAS the defect",
      not INLINE_BODY.search(shell), INLINE_BODY.findall(shell))
check("(b) the home itself consults the renderer first", "c.render ? c.render(r)" in shell)

# ── (c) `render` stays display-only — an export carries the figure, never a control ───────────────
exp = read(EXPORT_HOME)
check("(c) lib/export.tsx declares `render` as the column's own cell", "render?:" in exp)
check("(c) …and never calls it: an exported file carries `get`, not a control",
      not HONOURS.search(exp), HONOURS.findall(exp))
check("(c) ReportShell's export payload still reads `get`", "c.get(" in shell)
check("(c) …and its subtotals do too — a renderer is not a number",
      "subtotal(" in shell and "c.render" in shell.split("const bodyCell =")[1].split("\n")[0])

# ── (d) a declared renderer reaches a component that honours it ───────────────────────────────────
# Every page that declares one must name a table that is in HONOUR. Derived both ways: the page's
# table is found by the component it mentions, so a page handed to a third unwired table is caught
# by (a) and a page handed to something that is not a table at all is caught here.
TABLE_NAMES = {r.rsplit("/", 1)[-1].rsplit(".", 1)[0] for r in HONOUR}
orphans = sorted(rel for rel in DECLARERS
                 if rel not in TABLES and not any(n in FILES[rel] for n in TABLE_NAMES))
check("(d) at least one page declares a column renderer — this check is not vacuous",
      bool(DECLARERS - TABLES), sorted(DECLARERS))
check("(d) every page declaring one hands it to a component that honours it", not orphans, orphans)

# ── (e) negative controls ─────────────────────────────────────────────────────────────────────────
_IMP = "import { type ExportColumn } from '@/lib/export'\n"
_IGNORES = _IMP + "const x = (c: ExportColumn, r: any) => <td>{c.get(r)}</td>"
_HONOURS = _IMP + "const x = (c: ExportColumn, r: any) => <td>{c.render ? c.render(r) : c.get(r)}</td>"
_PAGE = _IMP + "const cols: ExportColumn[] = [{ header: 'Open', get: () => '', render: (r) => <button/> }]"

check("(e) a table component that ignores `render` → RED",
      bool(table_components({"components/T.tsx": _IGNORES})[2]))
check("(e) …and one that honours it → green",
      not table_components({"components/T.tsx": _HONOURS})[2])
check("(e) a page that only DEFINES columns is not a table component",
      not table_components({"app/p/page.tsx": _PAGE})[0])
check("(e) a type-only file is not a table component either",
      not table_components({"lib/types.ts": "export type ExportColumn = { render?: () => any }"})[0])
check("(e) a page that hand-writes a <td> per field is NOT in scope — it reads no column accessor",
      not table_components({"app/p/page.tsx": _IMP + "const c: ExportColumn[] = []\n<td>{r.day}</td>"})[0])
check("(e) the export home is excused BY NAME, and only it",
      not table_components({EXPORT_RENDERS_FILES: _IGNORES})[0]
      and bool(table_components({"lib/other-export.tsx": _IGNORES})[0]))
check("(e) a column declaring a renderer is seen", bool(declaring_files({"app/p/page.tsx": _PAGE})))
check("(e) a column with no renderer is not", not declaring_files({"app/p/page.tsx": _IMP + "[{ get: () => 1 }]"}))
check("(e) the inline-body-cell pattern is detectable — the regression itself",
      bool(INLINE_BODY.search("<td>{isMoney(c) ? money(c.get(r)) : str(c.get(r))}</td>")))
check("(e) …and the fixed form is not flagged", not INLINE_BODY.search("<td>{bodyCell(c, r)}</td>"))
check("(e) a page handed to no known table → RED",
      bool([r for r in {"app/p/page.tsx"} if not any(n in _PAGE for n in {"ReportShell", "DataGrid"})]))

# ── the three columns this was reported through — the regression, by name ──────────────────────────
TAKEOVER = "app/(platform)/commcalc/store-takeover/page.tsx"
tk = FILES.get(TAKEOVER, "")
check("(regression) the takeover page still declares its three renderers",
      len(DECLARES.findall(tk)) >= 4, len(DECLARES.findall(tk)))
check("(regression) the Open button is one of them", "render: (r: ProspectRow)" in tk)
check("(regression) the compare-with-our-store picker is another", "Compare with our store" in tk)
check("(regression) the two declared cells are the others",
      tk.count("render: (r: StoreRow) => cell(r,") == 2,
      tk.count("render: (r: StoreRow) => cell(r,"))
check("(regression) …and the page's only table is one that now honours them",
      "ReportShell" in tk and not any(r == TAKEOVER for r in IGNORE))

# ── wired, or it is not a lock ────────────────────────────────────────────────────────────────────
wf = read(WORKFLOW) if os.path.exists(WORKFLOW) else ""
check("(wired) this lock runs in carrier-vocab-guard.yml", "harness_column_render_lock.py" in wf)

print("\n%d passed, %d failed" % (P, F))
if F:
    print("\n  A column's `render` is how an interactive cell reaches the screen. A table that takes\n"
          "  an ExportColumn and draws its own <td> must consult it: `c.render ? c.render(r) : …`.\n"
          "  Keep every other read — export, sort, filter, group, subtotal — on `get`.")
    sys.exit(1)
print("OK — %d table component(s) take an ExportColumn and all %d honour `render`; "
      "%d page(s) declare one and each reaches one." % (len(TABLES), len(HONOUR), len(DECLARERS)))
