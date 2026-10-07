"""Lock — every chart on the Finance Trends hub carries a plain-language caption, from ONE home.

Owner request 2026-10-07: *"how to read the Trends report in finance, explain under each graph what
that means for a lay man"*.

THE CLASS, NOT THE INSTANCE (CLAUDE.md "a fix is a DESIGN fix"). The instance was "the four Trends
charts have no explanation". The class is **a chart renders a number without telling the reader what
moving up or down means**, and the design fix is that the caption is a component with one definition
(`frontend/src/components/ChartNote.tsx`) which every chart card dereferences — plus this lock, so
the fifth chart added to the hub cannot ship without one and an existing caption cannot be quietly
deleted or re-styled inline.

SIBLINGS CHECKED IN THE SAME CHANGE. The other chart surfaces that answer "read this graph" are
`commcalc/comp-trend` and `accounts/analysis`. They are EXCUSED here, not forgotten: `comp-trend` is
a commission-agent-owned comparison screen and `accounts/analysis` sits behind the same
`account_trends` grant but was not what the owner asked about. §D below pins the scope so the
exclusion is a declared fact rather than an oversight, and the component is already shared so either
can be wired without a second implementation.

NOT THE `.pg-note` GATE. `globals.css` hides `.pg-note` from everyone but a Master admin who has
turned help on (`lib/help-context.tsx`). These captions are for the market managers who read the
report and can never flip that switch, so §A2 proves the caption is NOT gated.

  A. THE HOME — ChartNote exists, renders its caption unconditionally, and carries no copy of its own.
  B. EVERY CHART IS WIRED — each `<TrendChart>` card on the Trends hub has exactly one `<ChartNote>`,
     and the page declares no second caption implementation.
  C. THE CAPTIONS EARN THEIR PLACE — each one says what the lines ARE and what a move MEANS, and the
     two captions whose number is conditional (carried expenses, the uncomputed snapshot) say so, so
     the lock fails if a caption is reduced to a label.
  D. RULE TWO + SCOPE — no carrier/tenant/product name in the component or the captions; the sibling
     chart pages are named, so adding one to the lock is a one-line change rather than a rediscovery.

Stdlib only. No DB, no network, no imports from `app`.

Run:  cd backend && python3 harness_trends_chart_notes.py
"""
import io
import os
import re
import sys

PASS = FAIL = 0


def ok(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


HERE = os.path.dirname(os.path.abspath(__file__))
FE = os.path.join(os.path.dirname(HERE), "frontend", "src")
NOTE_PATH = os.path.join(FE, "components", "ChartNote.tsx")
HUB_PATH = os.path.join(FE, "app", "(platform)", "accounts", "trends", "page.tsx")

# The chart surfaces that answer "read this graph". Only the Trends hub is LOCKED today; the others
# are declared so the next agent extends this tuple instead of re-deriving the question (§D2).
LOCKED_CHART_PAGES = (HUB_PATH,)
SIBLING_CHART_PAGES = (
    os.path.join(FE, "app", "(platform)", "commcalc", "comp-trend", "page.tsx"),
    os.path.join(FE, "app", "(platform)", "accounts", "analysis", "page.tsx"),
)


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def code_only(src):
    """`src` with // line comments and /* */ blocks stripped — a lock must read the CODE, never a
    comment that happens to mention the symbol it is looking for."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in src.splitlines())


def jsx_blocks(src, tag):
    """Every `<Tag>…</Tag>` body in `src`, non-nested (ChartNote never nests)."""
    return re.findall(r"<%s>(.*?)</%s>" % (tag, tag), src, flags=re.S)


print("── A. the home ──")
ok("A1 ChartNote.tsx exists and default-exports one component",
   os.path.exists(NOTE_PATH))
note = read(NOTE_PATH) if os.path.exists(NOTE_PATH) else ""
note_code = code_only(note)
ok("A1b exactly one ChartNote definition in the tree",
   len(note_code.split("export default function ChartNote")) == 2)
# The whole point of not reusing `.pg-note`: the caption must render for every reader.
ok("A2 the caption is NOT behind the master-admin help gate",
   "pg-note" not in note_code and "useHelp" not in note_code and "help-context" not in note_code)
ok("A2b and it is not conditionally returned away",
   "return null" not in note_code)
ok("A3 the component carries no caption copy of its own (every word comes from the page)",
   "children" in note_code and not re.search(r">[A-Za-z][^<>{}]{25,}<", note_code))


print("\n── B. every chart on the Trends hub is wired ──")
hub = read(HUB_PATH)
hub_code = code_only(hub)
ok("B1 the hub imports the one home",
   "from '@/components/ChartNote'" in hub_code)
charts = hub_code.count("<TrendChart")
notes = hub_code.count("<ChartNote>")
ok(f"B2 one caption per chart ({charts} charts, {notes} captions)",
   charts > 0 and charts == notes)
# A card is `<div className="card" style={card}>` … each must hold both a chart and its caption.
cards = re.findall(r'<div className="card" style=\{card\}>(.*?)\n          </div>', hub_code, flags=re.S)
ok(f"B3 every chart CARD holds its own caption ({len(cards)} cards)",
   len(cards) == charts and all(("<TrendChart" in c) == ("<ChartNote>" in c) for c in cards))
# No second implementation: a caption typed as a bare styled <p>/<span> inside the chart grid is the
# drift this lock exists to stop.
grid = hub_code[hub_code.index("gridTemplateColumns"):] if "gridTemplateColumns" in hub_code else ""
ok("B4 no second caption implementation in the chart grid",
   not re.search(r"<p\b", grid) and "pg-note" not in grid)
ok("B5 the captions sit OUTSIDE the PNG capture refs (the export is unchanged)",
   all(blk.index("</div>") < blk.index("<ChartNote>")
       for blk in (c for c in cards if "<ChartNote>" in c)))


print("\n── C. the captions earn their place ──")
caps = jsx_blocks(hub_code, "ChartNote")
ok(f"C1 {len(caps)} captions, each a real sentence rather than a label",
   len(caps) == charts and all(len(c.strip()) >= 120 for c in caps))
# "What does a move MEAN" is the whole ask — a caption that only names the series fails here.
DIRECTION = re.compile(r"(?i)\b(rising|falls?|widening|converging|up|down|more|less)\b")
ok("C2 every caption says what a MOVE means, not just what the line is",
   all(DIRECTION.search(c) for c in caps))
# The two numbers that are conditional must say so — these are the live traps a lay reader walks into.
ok("C3 the expenses caption warns that an un-entered month inherits the previous one",
   any("inherits" in c and "flat" in c for c in caps))
ok("C4 the profit caption warns that months can be uncomputed, and names the report it belongs to",
   any("computing" in c and "Gross Profit" in c for c in caps))


print("\n── D. RULE TWO + declared scope ──")
ok("D1 no carrier / tenant / product name in the component or any caption",
   not re.search(r"(?i)\b(boost|luxelink|nova\s*wave|cellfonz|vzone|t-?mobile|verizon|total\s+wireless|vidapay)\b",
                 note_code + "".join(caps)))
ok("D2 every locked chart page exists",
   all(os.path.exists(p) for p in LOCKED_CHART_PAGES))
ok("D3 the declared sibling chart pages still exist (extend this lock, do not re-derive the question)",
   all(os.path.exists(p) for p in SIBLING_CHART_PAGES))

print()
print(f"harness_trends_chart_notes: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
print("ALL CHECKS PASSED")
