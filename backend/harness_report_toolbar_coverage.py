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
FILTER_OWES_MAX = 70

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
#: …AND A STRING IS NOT A COMMENT EITHER, which is the other half of the same lesson and cost this
#: gate a silent blind spot until 2026-11-18. The stripper below used to be three regexes over the
#: raw text, and `/\*` has a perfectly ordinary home inside a string: `accept="image/*"` on a file
#: input. That opener pairs with the NEXT `*/` in the file — the end of the next JSX comment — and
#: from there every comment delimiter is off by one, so the stripper swallowed 10KB of real JSX,
#: including the page's `<ReportExportBar>` and `<StandardFilterBar>`. FOUR pages carry
#: `accept="image/*"` today, and two of them were being counted as owing a filter bar they
#: actually render. The gate read a page's coverage off a mangled copy of it and reported the
#: result as fact — exactly the vacuity the paragraph above exists to refuse, one layer further in.
#: So the stripper now walks the source and knows where a string is. A `/*` inside `'…'`, `"…"` or
#: a template literal is TEXT; only one outside them opens a comment.
_LINE = re.compile(r"^\s*//.*$", re.M)


def _strip_comments(src):
    """Remove comments, and ONLY comments. Walks the text tracking string literals, because a
    comment delimiter inside a string is data — see the note above for what it cost to learn.

    A mangled read errs toward RED (an element strips out → the route reads `owes` → the ratchet
    bites and somebody looks), never toward green, which is the direction a lock may err in."""
    out, i, n = [], 0, len(src)
    quote = None                       # the ' , " or ` we are inside, if any
    while i < n:
        c = src[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:          # an escape consumes the next character whole
                out.append(src[i + 1]); i += 2; continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c; out.append(c); i += 1; continue
        if src.startswith("/*", i):               # a real block comment, outside every string
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        out.append(c); i += 1
    # `{/* … */}` leaves its braces behind once the comment inside is gone; drop the empty pair so
    # the result reads like the code it is, then take the line comments.
    return _LINE.sub("", re.sub(r"\{\s*\}", "", "".join(out)))


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

# ── The stripper itself, both ways. ────────────────────────────────────────────────────────────
# This gate's whole measurement is taken off `_strip_comments`, so a defect in it is a defect in
# every number above. Each of these would have been RED before 2026-11-18 or would be RED if the
# older, regex-only stripper came back.
print("\n== F. the stripper removes comments, and ONLY comments ==")
# The measured shape, exactly: `accept="image/*"` on the file input, the page's real elements
# below it, and a `/** … */` doc comment further down — which is where the regex stripper found the
# `*/` to pair the string's `/*` with, swallowing everything between. Verified by running the old
# stripper over this fixture: both elements disappeared and `accept="image` was left dangling.
_IMG = ('<input accept="image/*" />\n'
        '<ReportExportBar title="x" />\n'
        '<StandardFilterBar value={f} />\n'
        '/** a doc comment further down the file. */\n'
        'function escapeHtml(s) { return s }\n')
check("F1 a `/*` inside a STRING does not open a comment — `accept=\"image/*\"` on a file input, "
      "paired by the old stripper with the next doc comment's `*/`, is what blinded this gate",
      _renders(_strip_comments(_IMG), SEND_PROVIDERS) == "ReportExportBar"
      and _renders(_strip_comments(_IMG), FILTER_PROVIDERS) == "StandardFilterBar")
check("F2 …and the four pages that really carry `accept=\"image/*\"` are read whole, not truncated "
      "at it — the measured defect, pinned to the files themselves",
      all(len(_src(r)) > 0.7 * len(open(_page(r), encoding="utf-8").read())
          for r in ("/closing/pickup", "/closing/billpay-pickup")))
check("F3 CONTROL — a line comment is still not code (the 2026-10-30 lesson, still locked)",
      _renders(_strip_comments("// <ReportShell> would do\nconst a = 1\n"), SEND_PROVIDERS) is None)
check("F4 CONTROL — nor is a block comment",
      _renders(_strip_comments("/* <ReportShell> */\nconst a = 1\n"), SEND_PROVIDERS) is None)
check("F5 CONTROL — nor is a JSX comment, which is how a page would TALK its way to green",
      _renders(_strip_comments("{/* <ReportExportBar /> */}\n"), SEND_PROVIDERS) is None)
check("F6 CONTROL — an unterminated block comment eats the rest, as a compiler would",
      _renders(_strip_comments("/* oops\n<ReportExportBar />\n"), SEND_PROVIDERS) is None)
check("F7 CONTROL — an escaped quote does not end the string it is inside, so the `/*` after it "
      "is still text",
      _renders(_strip_comments('const s = "a \\" b /*"\n<ReportExportBar />\n'), SEND_PROVIDERS)
      == "ReportExportBar")

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
