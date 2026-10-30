"""BUILD GATE — a report cannot ship without the STANDARD FILTERS and the SEND BAR (§CORE-27).

OWNER ASK 2026-10-30, verbatim: *"Zero Sales does not have send notification bar, please note this
also in teh indsex all reports must have the satandard filters and the send bar by default"*.

"BY DEFAULT" IS A RATCHET, NOT A SENTENCE IN A DOC — the same lesson §CORE-17 already paid for. The
instance he reported was Zero Sales. The CLASS is *a report whose figures nobody can mail gets
retyped into an email, and a report nobody can narrow gets read at the wrong scope*. Written only in
the index, the rule is re-broken by the next report nobody remembered to check; so the rule is a
CHECK, and the measurement that came with the ask found, over the 128 report routes the §CORE-17
ledger names, 27 with no way to send and 71 narrowed by controls of their own rather than by the
standard bar.

HOW THIS GATE AVOIDS BEING A SECOND OPINION. It declares nothing of its own:

  * WHICH ROUTES ARE REPORTS is read from `harness_drilldown_pending.txt`, the ledger §CORE-17
    already maintains. A screen that ledger calls `not_report` is not a report here either. One fact,
    one home — a second list of "what is a report" would drift from the first within a month.
  * WHETHER A PAGE HAS EACH BAR is read from the PAGE SOURCE, not from a declaration, because both
    answers are one shared component and a page either renders it or does not. There is nothing for
    an author to judge, so there is nothing for an author to mis-declare.

THE TWO PINS MAY ONLY SHRINK. A new report with no send bar raises `SEND_OWES_MAX` and fails here;
wiring one lowers it and the author re-pins DOWNWARD. That pin is the only thing that makes "by
default" mean anything (`harness_drilldown_coverage.OWES_MAX` is the house precedent).

Run: `cd backend && python3 harness_report_toolbar_coverage.py`
"""
import os
import re
import sys

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


ROOT = ".."
DRILL_LEDGER = "harness_drilldown_pending.txt"

#: THE RATCHETS, measured 2026-10-30 over the 128 report routes the §CORE-17 ledger names. THEY MAY
#: ONLY GO DOWN. Lower each in the same PR that wires one more report; a change that RAISES either is
#: a report shipped without the toolbar, which is what this file exists to refuse.
#: 2026-10-30: Zero Sales took the send count from 28 to 27 (the owner's reported instance).
SEND_OWES_MAX = 27
FILTER_OWES_MAX = 71

#: Routes whose answer lives in a shared component rather than in a `page.tsx`. Same overrides the
#: drill-down gate names, for the same reason: a 20-line wrapper can never answer.
PAGE_OVERRIDE = {
    "/closing/closing-pickup-status": "frontend/src/app/(platform)/closing/_lib/ClosingPickupStatus.tsx",
    "/closing/verify": "frontend/src/components/DailyClosingVerify.tsx",
}

#: THE ONE HOME FOR EACH BAR, and what counts as having it.
#:   send   — `<ReportShell>` (its toolbar), `<ReportExportBar>` (the same toolbar beside a page's own
#:            table), or `<SendReportButton>` reached directly. All three end in the one sender.
#:   filter — `<ReportShell>` (its built-in quick filters) or `<StandardFilterBar>`.
#: A page that narrows with controls of its own (a store picker, a period context) is NOT counted as
#: having the standard filters: the owner asked for the standard ones, and a bespoke bar is the
#: per-report copy the house rules forbid. Those routes read `owes`, honestly.
SEND_PROVIDERS = ("ReportShell", "ReportExportBar", "SendReportButton")
FILTER_PROVIDERS = ("ReportShell", "StandardFilterBar")


def _page(route):
    if route in PAGE_OVERRIDE:
        return f"{ROOT}/{PAGE_OVERRIDE[route]}"
    return f"{ROOT}/frontend/src/app/(platform){route}/page.tsx"


#: COMMENTS ARE NOT CODE, and this cost a mutation test to find (2026-10-30). The first draft of
#: this gate matched `<ReportExportBar[\s/>]` against the raw file, and the Zero Sales page's own
#: explanatory comment contains the words `<ReportExportBar>` — so deleting the real element left the
#: gate green. That is the SAME vacuity the element-not-the-identifier rule exists to close, one layer
#: in: a page can satisfy a source check by TALKING about the component. Every source this gate reads
#: therefore has its comments stripped first, and the mutation (element deleted → B1 and D2 red) is
#: confirmed rather than assumed.
_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_JSX = re.compile(r"\{\s*/\*.*?\*/\s*\}", re.S)
_LINE = re.compile(r"^\s*//.*$", re.M)


def _strip_comments(src):
    return _LINE.sub("", _BLOCK.sub("", _JSX.sub("", src)))


def _src(route):
    p = _page(route)
    return _strip_comments(open(p, encoding="utf-8").read()) if os.path.exists(p) else ""


def _renders(src, names):
    """THE ELEMENT, NOT THE IDENTIFIER. `"ReportExportBar" in src` is satisfied by the IMPORT ALONE,
    so a page whose render was deleted while its import stayed would pass — the un-wiring hole every
    one of these locks exists to close (the §CORE-17 D1 lesson, in the same words)."""
    return next((n for n in names if re.search(r"<" + n + r"[\s/>]", src)), None)


# ── The population comes from the drill-down ledger. ───────────────────────────────────────────
print("\n== A. the population is READ, not re-declared ==")
check("A1 the §CORE-17 ledger exists — this gate has no population of its own",
      os.path.exists(DRILL_LEDGER))
declared = {}
for ln in open(DRILL_LEDGER, encoding="utf-8"):
    ln = ln.rstrip("\n")
    if not ln.strip() or ln.lstrip().startswith("#"):
        continue
    parts = ln.split("\t")
    declared[parts[0].strip()] = (parts[1].strip() if len(parts) > 1 else "")
reports = sorted(r for r, st in declared.items() if st != "not_report")
check("A2 the ledger parsed and names a plausible estate of reports", len(reports) > 80, str(len(reports)))
check("A3 a screen the §CORE-17 ledger calls `not_report` is not a report here either — one fact, "
      "one home, rather than two lists of what a report is",
      len(declared) > len(reports), f"{len(declared)} declared, {len(reports)} reports")
unreadable = [r for r in reports if not _src(r)]
check("A4 every report route resolves to a file this gate can read — a claim about a page nobody "
      "can find is not a claim",
      unreadable == [], str(unreadable))

# ── The measurement. ───────────────────────────────────────────────────────────────────────────
print("\n== B. the ratchets ==")
send_owes, filter_owes, have_send, have_filter = [], [], {}, {}
for r in reports:
    s = _src(r)
    have_send[r] = _renders(s, SEND_PROVIDERS)
    have_filter[r] = _renders(s, FILTER_PROVIDERS)
    if not have_send[r]:
        send_owes.append(r)
    if not have_filter[r]:
        filter_owes.append(r)

check(f"B1 THE SEND RATCHET — {len(send_owes)} report route(s) cannot be sent, against a pinned "
      f"ceiling of {SEND_OWES_MAX}. This number MAY ONLY SHRINK: re-pin it DOWNWARD when one is "
      f"wired, never upward",
      len(send_owes) <= SEND_OWES_MAX, f"{len(send_owes)} > {SEND_OWES_MAX}: {send_owes}")
check(f"B2 THE FILTER RATCHET — {len(filter_owes)} report route(s) have no standard filters, "
      f"against a pinned ceiling of {FILTER_OWES_MAX}. Shrink only",
      len(filter_owes) <= FILTER_OWES_MAX, f"{len(filter_owes)} > {FILTER_OWES_MAX}: {filter_owes}")
check("B3 neither pin is stale by more than a batch — when it drifts below, re-pin it so the gate "
      "keeps biting",
      len(send_owes) >= SEND_OWES_MAX - 5 and len(filter_owes) >= FILTER_OWES_MAX - 5,
      f"send={len(send_owes)}/{SEND_OWES_MAX} filter={len(filter_owes)}/{FILTER_OWES_MAX}")

# ── The shared components really are the one home. ─────────────────────────────────────────────
print("\n== C. what the gate counts really is the one home ==")
SHELL = open(f"{ROOT}/frontend/src/components/ReportShell.tsx", encoding="utf-8").read()
BAR = open(f"{ROOT}/frontend/src/components/ReportExportBar.tsx", encoding="utf-8").read()
check("C1 `<ReportShell>` really carries the sender — if it stops, every report counted through it "
      "silently loses its send bar and this gate would still read green",
      "<SendReportButton" in SHELL)
check("C2 and so does `<ReportExportBar>`", "<SendReportButton" in BAR)
check("C3 `<ReportShell>` really carries filters of its own, which is why a page using it is not "
      "asked for `<StandardFilterBar>` as well",
      "Add filter" in SHELL)
check("C4 the standard filter bar is still where this gate looks for it",
      os.path.exists(f"{ROOT}/frontend/src/components/StandardFilterBar.tsx"))

# ── The reported instance, and its honesty rule. ───────────────────────────────────────────────
print("\n== D. Zero Sales — the instance the owner reported ==")
ZS = "/commcalc/zero-sales"
zs_src = _src(ZS)
check("D1 Zero Sales is in the population at all", ZS in reports)
check("D2 it renders the shared send bar", _renders(zs_src, SEND_PROVIDERS) == "ReportExportBar")
check("D3 and the shared standard filters, which it already had",
      _renders(zs_src, FILTER_PROVIDERS) == "StandardFilterBar")
check("D4 what it sends comes from the shared converter, not from a payload built in the page",
      "zeroSalesSheets(data)" in zs_src)
CONV = f"{ROOT}/frontend/src/lib/zero-sales-export.ts"
check("D5 the converter exists where the index says it does", os.path.exists(CONV))
conv = open(CONV, encoding="utf-8").read() if os.path.exists(CONV) else ""
check("D6 it reuses the shared tile-snapshot sheet rather than inventing a second snapshot shape",
      "tileSnapshotSheet" in conv)
check("D7 THE HONESTY RULE TRAVELS — a day no feed landed for exports BLANK, never 0. A mailed 0 "
      "carries none of the screen's colour or banner to question it",
      "MEASURED.has" in conv and "return ''" in conv)
# ONE HOME FOR THE STATE WORDS. The page used to spell `word: 'measured zero'` in its own table and
# the export would have spelled it again — two copies of what a square means, which is exactly the
# §19.18 shape. The page now dereferences the converter's map.
check("D8 the five state words have ONE home and the page dereferences it — no second copy of what "
      "a square means",
      "ZS_STATE_WORD" in conv and "zsStateWord(k)" in zs_src
      and not re.search(r"word:\s*'", zs_src),
      "the page still spells a state word of its own")

print("\n== E. registration ==")
IDX = open(f"{ROOT}/docs/SYSTEM_DATA_FLOW_INDEX.md", encoding="utf-8").read()
check("E1 the gate is registered in the index", "harness_report_toolbar_coverage.py" in IDX)
check("E2 and so is the rule it enforces", "CORE-27" in IDX)
check("E3 and the converter", "zero-sales-export" in IDX)

print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
print(f"\nreports={len(reports)}  send_owes={len(send_owes)}  filter_owes={len(filter_owes)}  "
      f"both={len([r for r in reports if have_send[r] and have_filter[r]])}")
print("\nSTILL OWES A SEND BAR:")
for r in send_owes:
    print(f"  {r}")
print("\nSTILL OWES THE STANDARD FILTERS:")
for r in filter_owes:
    print(f"  {r}")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
