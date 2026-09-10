"""Offline proof (no DB/network) for THE FLOWCHARTS TILE and the pay-column lie it uncovered.

OWNER DIRECTIVE 2026-09-10: "All of these will be in the training module under a tile called
flowcharts and named appropriately."

§A/§B/§C — the runbooks are IN the app, reachable, and cannot become an injection surface.
The obvious build is a `body_html` column rendered with dangerouslySetInnerHTML, which would also
make a runbook tenant-editable. That is stored XSS: any writer of a training row gets script
execution in every reader's authenticated session. So a runbook is TYPED CONTENT rendered by a
component, and §C fails the build if `dangerouslySetInnerHTML` ever appears on this path.

§D — THE $0.00 THAT WAS ALREADY LIVE. Researching the scheduling runbook turned up a real defect,
not a documentation gap: `/storeops/reports` reads the SAME pay-gated `GET /storeops/payroll` the
Payroll page reads, but had NO detection for withheld pay. The keys are deleted server-side, and
`lib/export.tsx`'s `money()` did `Number(n) || 0` — so a gated caller (every DM, since 2026-09-10)
saw `$0.00` in the table, the store rollup, the totals tiles AND the Excel/PDF export. "This person
earns nothing" is the exact lie strip-not-zero exists to prevent, and an export is the worst place
to tell it: the spreadsheet outlives the screen and carries no note.

Static on purpose: every value involved is a legal string and both pages render perfectly, so
neither tsc nor a build can see a report quietly paying everybody nothing (index §24).

Run: `cd backend && python3 harness_training_flowcharts.py`
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harnesslib import js_code_only   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


FE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "src")


def src(rel, code_only=True):
    with open(os.path.join(FE, rel), encoding="utf-8") as fh:
        raw = fh.read()
    return js_code_only(raw) if code_only else raw


# ══ A. THE LIBRARY ═══════════════════════════════════════════════════════════════════════════════
lib = src("lib/flowcharts.tsx")
check("A1 there is a flowchart registry", "export const FLOWCHARTS" in lib)
slugs = re.findall(r"slug:\s*'([a-z0-9-]+)'", lib)
check("A2 it holds the runbooks the owner asked for", set(slugs) >= {"daily-closing", "dm-verify", "store-cash"},
      slugs)
check("A3 every runbook is NAMED — a title, not a filename",
      len(re.findall(r"title:\s*'[^']+Runbook'", lib)) >= 3)
check("A4 ... and carries a one-line summary for the tile", lib.count("summary:") >= 3)
check("A5 ... and names the levels it is written for", lib.count("chain:") >= 3)
for tone in ("rep", "dm", "mm"):
    check(f"A6 the '{tone}' level tone is used", f"tone: '{tone}'" in lib)
check("A7 each runbook carries a diagram", lib.count("figure:") >= 3)
check("A8 ... with an aria-label, so the mechanism is available to a reader who cannot see it",
      lib.count("aria-label") >= 3)

# The diagrams must theme. A literal hex stroke would be invisible or garish on one of the two
# themes, and a training page nobody can read on dark mode is a training page nobody reads.
fig_hexes = re.findall(r'(?:stroke|fill)="(#[0-9a-fA-F]{3,6})"', lib)
check("A9 no diagram hard-codes a colour — strokes and fills come from tokens or currentColor",
      not fig_hexes, sorted(set(fig_hexes)))

# ══ B. REACHABLE ═════════════════════════════════════════════════════════════════════════════════
train = src("app/(platform)/training/page.tsx")
check("B1 the Training Center has a Flowcharts tab", "'flowcharts'" in train and "Flowcharts" in train)
check("B2 ... visible to EVERYONE, not only an admin — the people who must FOLLOW a procedure "
      "cannot be the ones locked out of reading it",
      re.search(r"canEdit\s*&&\s*\(\s*<div[^>]*>\s*\{\(\['tours', 'scripts'\]", train) is None
      and "canEdit\n          ? (['tours', 'flowcharts', 'scripts']" in train)
check("B3 the tab lists every registered runbook rather than a hand-kept copy",
      "FLOWCHARTS.map" in train)
check("B4 ... linking to the runbook route", "/training/flowcharts/${f.slug}" in train)
check("B5 a deep link opens the right tab, so a runbook's back-link lands where it says",
      "tab=flowcharts" in src("components/RunbookDoc.tsx") and "get('tab')" in train)

route = src("app/(platform)/training/flowcharts/[slug]/page.tsx")
check("B6 the runbook route resolves a slug", "flowchartBySlug" in route)
check("B7 an unknown slug renders a named miss with a way back, never a crash or a blank",
      "No flowchart here" in route and "FLOWCHARTS.map" in route)

# ══ C. NOT AN INJECTION SURFACE ══════════════════════════════════════════════════════════════════
doc = src("components/RunbookDoc.tsx")
for rel, body in (("components/RunbookDoc.tsx", doc), ("lib/flowcharts.tsx", lib),
                  ("app/(platform)/training/flowcharts/[slug]/page.tsx", route),
                  ("app/(platform)/training/page.tsx", train)):
    check(f"C1 {rel} never injects HTML", "dangerouslySetInnerHTML" not in body)
check("C2 runbook content is TYPED, so a new one is a data object and not a new page",
      "export type Runbook" in doc and "Runbook[]" in lib)
check("C3 the renderer's styles are scoped to the document, so a runbook cannot restyle the app",
      ".rb {" in doc)
check("C4 ... and its lane colours are declared for BOTH themes",
      'prefers-color-scheme: dark' in doc and '[data-theme="dark"] .rb' in doc)

# ══ D. THE $0.00 THE RESEARCH UNCOVERED ══════════════════════════════════════════════════════════
rep = src("app/(platform)/storeops/reports/page.tsx")
pay = src("app/(platform)/storeops/payroll/page.tsx")
exp = src("lib/export.tsx")

check("D1 the Hours & Payroll report now detects withheld pay at all",
      "const canSeePay" in rep)
check("D2 ... by ABSENCE OF THE KEY, the same test the Payroll page uses — not a second rule",
      "'pay_rate' in r" in rep and "'pay_rate' in (r as any)" in pay)
check("D3 the pay COLUMNS are dropped for a gated caller",
      "PAY_COLS" in rep and "empColsAll.filter" in rep and "storeColsAll.filter" in rep)
check("D4 ... every money column, in one vocabulary, so table and export cannot disagree",
      all(c in rep for c in ("'Pay $/hr'", "'Sched Pay'", "'Actual Pay'")))
check("D5 the money TILES are dropped too — a total of withheld values is still a fabricated number",
      "canSeePay && <Tile label=\"Scheduled Pay\"" in rep
      and "canSeePay && <Tile label=\"Actual Pay\"" in rep)

money_body = exp.split("const money")[1][:400]
check("D6 the shared exporter checks for a MISSING value BEFORE it formats — the old shape ran "
      "`Number(n) || 0` unconditionally, which is what turned a withheld figure into $0.00",
      "return ''" in money_body
      and money_body.index("return ''") < money_body.index("format("), money_body[:120])
check("D7 ... absent renders blank",
      re.search(r"if \(n == null \|\| n === ''\) return ''", exp) is not None)
check("D8 ... and a REAL zero still renders as money, because 0 is a fact",
      "format(Number(n) || 0)" in exp)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
