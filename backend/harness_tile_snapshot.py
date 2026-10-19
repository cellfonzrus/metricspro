"""LOCK — the figures a manager reads at the top of a report can be SENT, through the one sender, and
the sent copy cannot claim a change the screen refuses to claim (owner ask 2026-10-19, index §COMMCALC-19).

    python3 harness_tile_snapshot.py        (from backend/)

THE ASK, verbatim: *"on the sales comparison dashboard it shoudl give an option to send teh snapshot of
the top part to to the desired people"*.

WHAT WAS ACTUALLY MISSING. Sending already had ONE home — `frontend/src/lib/send-report.tsx` — with the
recipient picker over employees and saved contacts, free-typed addresses, the channel ladder, the
optional message and delivery through `POST /notify/send-file` into the Notify history. Nothing about
sending needed building. The gap was CONTENT: that button can only send an `ExportPayload`, and
`<ReportShell>` only ever handed it the TABLE. So every report in the house could mail its rows and no
report could mail the figures a manager actually reads.

THE CLASS, not the instance (CLAUDE.md, "a fix is a DESIGN fix or it is not a fix"): *a number on screen
that nobody can send is a number that gets retyped into an email.* The instance was one dashboard; the
fix is a SHARED converter, `frontend/src/lib/tile-snapshot.ts`, so the next report that wants this gets
the same columns, the same ordering and the same honesty rule for free. A snapshot block written inside
the Sales Comparison page would have been the sibling derivation the index rules forbid.

AND THE DEFECT THE ASK CAME WITH. The owner's own paste of the screen read **"Store expenses
$276,162.69 ± 0% was $276,162.69"**. Neither October nor September had entered expenses, so both
inherited the SAME carried sheet (August 2026 — measured live, that tenant's `store_expenses` ends there),
and the report divided that sheet by itself. "± 0%" reads as *spending held steady*; the truth was that
nobody had entered either month. A snapshot carries no small print and outlives the screen, so a lie
there travels further than a lie on the page. `sales_comparison.expense_change_measured` now withholds
the percent and names the reason, and that flag rides all the way into the mailed cell.

WHAT THIS LOCK FAILS THE BUILD FOR — the ways the design could quietly un-wire:
  §A ONE HOME FOR THE SHAPE — the converter exists, is pure, and the page DEREFERENCES it.
  §B NO SECOND CONVERTER — no other file builds a snapshot sheet of its own.
  §C ONE SENDER — the page adds no send path beside `SendReportButton`, and sends the SAME objects the
     tiles render from rather than a second read.
  §D THE HONESTY CHAIN IS UNBROKEN, link by link: the backend predicate → `expenses_meta.change_measured`
     → the tile's `measured` prop → the converter's "not measured" → the Note that says why.
  §E THE BEHAVIOUR IS PROVED, not asserted — `frontend/prove_tile_snapshot.mjs` drives the REAL module,
     and some workflow runs it, with path filters that cover every file it reads.
  §F REGISTERED in the index.
  §G NEGATIVE CONTROLS — every scan above is shown finding its target in a control string, so a scan
     that has stopped matching anything cannot sit here reading green.

Stdlib only, DB-free. The TypeScript is read as text here and EXECUTED by §E's node proof; this half is
the wiring lock, that half is the behaviour.
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import sales_comparison as sc  # noqa: E402

CONVERTER = "frontend/src/lib/tile-snapshot.ts"
PAGE = "frontend/src/app/(platform)/commcalc/sales-comparison/page.tsx"
SENDER = "frontend/src/lib/send-report.tsx"
PROOF = "frontend/prove_tile_snapshot.mjs"
PURE = "backend/app/modules/commcalc/sales_comparison.py"
WORKFLOW = ".github/workflows/carrier-vocab-guard.yml"
INDEX = "docs/SYSTEM_DATA_FLOW_INDEX.md"

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  PASS  %s" % name)
    else:
        _fail += 1
        print("  FAIL  %s %s" % (name, extra))


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def ts_code(text):
    """The TypeScript with its comments removed — a rule must live in CODE, not in prose about it."""
    out = re.sub(r"(?s)/\*.*?\*/", "", text)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", ln) for ln in out.splitlines())


conv, conv_code = read(CONVERTER), ts_code(read(CONVERTER))
page, page_code = read(PAGE), ts_code(read(PAGE))
proof = read(PROOF)
wf = read(WORKFLOW)
idx = read(INDEX)

print("\n§A ONE HOME FOR THE SHAPE")
check("A1 the converter exports the sheet builder",
      "export function tileSnapshotSheet" in conv_code)
check("A2 ...and the change label, so the two cannot drift apart",
      "export function snapshotChangeLabel" in conv_code)
check("A3 ...and names its own sheet once",
      len(re.findall(r"export const SNAPSHOT_SHEET_NAME", conv_code)) == 1)
check("A4 the tile shape is a declared type, so a caller cannot invent a field",
      "export type SnapshotTile" in conv_code
      and all(f in conv_code for f in ("changePct", "measured", "note", "money")))
check("A5 it is PURE — no React, no fetch, no clock",
      not re.search(r"from 'react'|useMemo|useState|\bfetch\s*\(|Date\.now|new Date", conv_code))
check("A6 it declares the column order itself (five columns, fixed here)",
      len(re.findall(r"header: '", conv_code)) == 5
      and conv_code.index("'Measure'") < conv_code.index("'This period'")
      < conv_code.index("'Compared period'") < conv_code.index("'Change'") < conv_code.index("'Note'"))
check("A7 the page DEREFERENCES it rather than shaping a sheet itself",
      "from '@/lib/tile-snapshot'" in page_code and "tileSnapshotSheet(" in page_code)
check("A8 ...and names no tenant, carrier or store (RULE TWO)",
      not re.search(r"(?i)\b(cellfonz|luxelink|novawave|boost|cricket|t-?mobile|at&t|verizon)\b",
                    conv_code))

print("\n§B NO SECOND CONVERTER")
src_dir = os.path.join(ROOT, "frontend", "src")
others = []
for base, _dirs, files in os.walk(src_dir):
    for fn in files:
        if not fn.endswith((".ts", ".tsx")):
            continue
        rel = os.path.relpath(os.path.join(base, fn), ROOT).replace(os.sep, "/")
        if rel == CONVERTER:
            continue
        body = ts_code(read(rel))
        # the column headers are the fingerprint of a snapshot sheet; a second file spelling them is a
        # second answer to "what shape is a sent tile row"
        if "'Compared period'" in body or '"Compared period"' in body:
            others.append(rel)
check("B1 no other frontend file spells the snapshot's columns", not others, others)
check("B2 the page builds its sheet ONLY through the shared converter",
      len(re.findall(r"sheets:\s*\[", page_code)) == len(re.findall(r"tileSnapshotSheet\(", page_code)),
      (re.findall(r"sheets:\s*\[[^\]]*\]", page_code)))
check("B3 the page declares no column of its own with the snapshot's headers (its TABLE columns are "
      "its own business; the SNAPSHOT's shape is not)",
      not re.search(r"header:\s*['\"](This period|Compared period|Measure)['\"]", page_code))

print("\n§C ONE SENDER")
check("C1 the page sends through the one home", "SendReportButton" in page_code
      and "from '@/lib/send-report'" in page_code)
check("C2 ...and spells no notify endpoint of its own",
      "notify/send" not in page_code and "send-file" not in page_code)
check("C3 the one home is what reaches /notify/send-file", "notify/send-file" in read(SENDER))
check("C4 the snapshot is built from the SAME payload objects the tiles render from — not a second read",
      "totals_by_category" in page_code.split("const snapshotTiles")[1].split("const snapshotPayload")[0]
      and "data.overall" in page_code.split("const snapshotTiles")[1].split("const snapshotPayload")[0])
check("C5 ...so the send block fires no request of its own",
      "api(" not in page_code.split("const snapshotTiles")[1].split("const Tile")[0])
check("C6 the table keeps its own Send — the snapshot button is additive, not a replacement",
      page_code.count("SendReportButton") >= 1 and "ReportShell" in page_code)

print("\n§D THE HONESTY CHAIN, LINK BY LINK")
# link 1 — the backend predicate, run for real
check("D1 the predicate exists and is pure", callable(sc.expense_change_measured))
check("D2 two carried copies of ONE sheet is not a measured change",
      sc.expense_change_measured("2026-08", "2026-08") is False)
check("D3 ...the same month spelled two ways is still one sheet (period_sort_key, not string ==)",
      sc.expense_change_measured("2026-08", "August 2026") is False)
check("D4 two real months ARE measured", sc.expense_change_measured("2026-08", "2026-07") is True)
check("D5 an entered month on either side is measured",
      sc.expense_change_measured(None, "2026-07") is True
      and sc.expense_change_measured("2026-08", None) is True
      and sc.expense_change_measured(None, None) is True)
pure = read(PURE)
check("D6 link 2 — the payload carries the flag, and the percent is WITHHELD rather than zeroed",
      '"change_measured": bool(_exp_change_measured)' in pure
      and 'if _exp_change_measured else None' in pure)
check("D7 ...and the per-store rows and the total carry it too",
      pure.count('cell["change_measured"] = False') == 1
      and pure.count('m["change_measured"] = False') == 1)
check("D8 link 3 — the tile is handed the flag",
      "measured={data.expenses_meta.change_measured !== false}" in page)
check("D9 link 4 — the SENT row is handed the flag as well, not only the screen",
      "measured," in page_code.split("const snapshotTiles")[1].split("const snapshotPayload")[0]
      or "measured:" in page_code.split("const snapshotTiles")[1].split("const snapshotPayload")[0])
check("D10 link 5 — the converter turns it into words, never a percent",
      "if (t.measured === false) return 'not measured'" in conv_code)
check("D11 ...and the reason travels in the Note, because a mailed sheet has no tooltip",
      re.search(r"(?s)note:.{0,240}carr", page_code) is not None
      and "r.note" in conv_code)
check("D12 the screen says it too, in words and not only in colour",
      "not measured" in page_code and "cannot be measured" in page)
check("D13 an absent figure is blank in the sent sheet, never a fabricated 0",
      "if (v == null || v === '') return ''" in conv_code)

print("\n§E THE BEHAVIOUR IS PROVED, AND THE PROOF IS RUN")
check("E1 the node proof exists and drives the REAL module",
      "src/lib/tile-snapshot.ts" in proof and "await import(" in proof)
check("E2 it calls through the imported module rather than copying it — no stub, no re-implementation",
      "pathToFileURL" in proof and "T.tileSnapshotSheet(" in proof
      and not re.search(r"(?m)^\s*(export\s+)?(function|const)\s+(tileSnapshotSheet|snapshotChangeLabel)\b",
                        proof))
check("E3 it proves all three states of the change label",
      "'not measured'" in proof and "'new'" in proof and "± 0%" in proof)
check("E4 it carries the owner's own figure as the regression", "276162.69" in proof)
check("E5 it carries negative controls", "negative controls" in proof)
check("E6 a workflow RUNS it", "node prove_tile_snapshot.mjs" in wf)
check("E7 ...in a job that has node", "setup-node" in wf)
for rel in (CONVERTER, PAGE, PROOF):
    check("E8 the path filter covers %s" % rel, '"%s"' % rel in wf or "'%s'" % rel in wf)
check("E9 this lock is run too", "harness_tile_snapshot.py" in wf)
check("E10 ...and is not parked on the debt list",
      "harness_tile_snapshot.py" not in read("backend/harness_unrun_pending.txt"))

print("\n§F REGISTERED")
check("F1 the section is in the index", "COMMCALC-19" in idx)
check("F2 the converter is named there", "tile-snapshot" in idx)
check("F3 ...and so is the sender it reuses", "send-report" in idx or "SendReportButton" in idx)

print("\n§G NEGATIVE CONTROLS")
check("neg-A a page that stopped dereferencing the converter would be RED",
      "tileSnapshotSheet(" not in page_code.replace("tileSnapshotSheet(", "handRolledSheet("))
check("neg-B the §B scanner finds a second converter when there is one",
      "'Compared period'" in ts_code("const columns = [{ header: 'Compared period' }]"))
check("neg-C the §C scanner finds a page-level notify call when there is one",
      "notify/send" in ts_code("await api('/api/v1/notify/send-file')"))
check("neg-D a predicate that compared raw strings would be RED",
      sc.expense_change_measured("2026-6", "2026-06") is False)
check("neg-E a comment claiming the rule would NOT satisfy the code scans",
      "tileSnapshotSheet" not in ts_code("// we call tileSnapshotSheet( here\n"))
check("neg-F a 'not measured' label dropped from the converter would be RED",
      "'not measured'" not in conv_code.replace("'not measured'", "''"))

print("\n%d passed, %d failed" % (_pass, _fail))
if _fail:
    sys.exit(1)
print("OK — the tiles a manager reads can be sent through the one sender, in one shared shape, and a "
      "change the report cannot measure is named as unmeasured in the mailed copy exactly as on screen.")
