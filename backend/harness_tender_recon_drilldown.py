"""PROOF — the X-report-vs-closing tiles name their stores, and REFUSE to when they cannot.

DB-free, stdlib only, driving the real `commcalc/tender_recon_drilldown` declaration through the
shared fold (`core/report_drilldown.tiles`).

THE AUDIT RANKED THIS REPORT SECOND of the 93 because its own sibling already drills the same
question: `/closing/tender-recon-3way` has `/closing/tender-drilldown` and this one had nothing. One
question with two answers, one carrying evidence and one not, is the duplicate defect.

AND IT IS ONLY PARTLY CLOSED HERE, WHICH IS THE POINT OF SECTION G. This page recomputes its tiles in
TypeScript over the FILTERED rows while the panels are folded server-side over the whole range, so
under a store or market filter the tile and the panel describe different populations. A panel
explaining a figure the screen is not showing would look like proof of the wrong number. So the page
offers a drill only while the two agree to the cent, states the reason when they do not, and this
route DELIBERATELY STILL READS `owes` in the coverage ledger — §G asserts all three, so nobody can
mistake the partial for the finished thing. The real remedy (one fold serving both sides, i.e. this
report's standard filters moving server-side) is recorded in the index, not quietly skipped.

  python3 backend/harness_tender_recon_drilldown.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import tender_recon_drilldown as trd    # noqa: E402
from app.modules.core import report_drilldown as rd               # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOL = 1.0


def store(name, pos_cash, clo_cash, pos_card, clo_card, **kw):
    r = {"store": name,
         "pos_cash": pos_cash, "closing_cash": clo_cash,
         "cash_variance": round(pos_cash - clo_cash, 2),
         "pos_card": pos_card, "closing_card": clo_card,
         "card_variance": round(pos_card - clo_card, 2),
         "pos_other": 0.0, "in_pos": True, "in_closing": True}
    r["match"] = abs(r["cash_variance"]) <= TOL and abs(r["card_variance"]) <= TOL
    r.update(kw)
    return r


#: One store that ties, one short on cash, and one the closing sheet never covered — the shapes the
#: report exists to tell apart.
ROWS = [
    store("1 Main St", 1000.0, 1000.0, 400.0, 400.0),
    store("2 Oak Ave", 800.0, 740.0, 250.0, 250.0),
    store("3 Pine Rd", 500.0, 0.0, 100.0, 0.0, in_closing=False),
]


def totals_of(rows):
    s = lambda k: round(sum(r[k] for r in rows), 2)          # noqa: E731
    return {"pos_cash": s("pos_cash"), "closing_cash": s("closing_cash"),
            "pos_card": s("pos_card"), "closing_card": s("closing_card"),
            "cash_variance": round(s("pos_cash") - s("closing_cash"), 2),
            "card_variance": round(s("pos_card") - s("closing_card"), 2),
            "stores": len(rows), "mismatches": sum(1 for r in rows if not r["match"])}


D = trd.drilldowns(ROWS, totals_of(ROWS))

print("\n== A. every tile is declared and ties ==")
CAT = {t["key"] for t in trd.catalogue()}
check("A1 all seven figures the five tiles show are declared", len(CAT) == 7, sorted(CAT))
check("A2 and every one produced a panel", set(D) == CAT, sorted(CAT ^ set(D)))
_bad = {k: v["tie_out"] for k, v in D.items() if v["tie_out"]["ties"] is not True}
check("A3 every panel's stores add up to the figure the report shows, to the cent", _bad == {}, _bad)
check("A4 THE VARIANCE TILE TIES TOO — Σ(POS) − Σ(closing) over a store set is Σ(POS − closing) over "
      "the same set, and if the endpoint ever stops folding it that way this check is what notices",
      D["cash_variance"]["tie_out"]["ties"] is True and D["cash_variance"]["figure"]["amount"] == 560.0,
      D["cash_variance"]["tie_out"])
check("A5 a row is a STORE, and the key says so — a reader who expected days would misread the count",
      all(r["origins"][0]["keys"] == {"store": r["label"]} for r in D["pos_cash"]["rows"]))

print("\n== B. both sides of the comparison are named ==")
check("B1 the POS legs name the X-report and nothing else",
      {s["table"] for s in D["pos_cash"]["sources"]} == {"pos_tender_summary"}
      and {s["table"] for s in D["pos_card"]["sources"]} == {"pos_tender_summary"})
check("B2 the closing legs name the rep's sheet AND the DM verification that can override it — "
      "naming only the sheet would credit a corrected figure to the row that was corrected",
      {s["table"] for s in D["closing_cash"]["sources"]}
      == {"daily_closing", "daily_closing_verification"}, D["closing_cash"]["sources"])
check("B3 a variance names all three, because it is a claim about both sides",
      {s["table"] for s in D["cash_variance"]["sources"]}
      == {"pos_tender_summary", "daily_closing", "daily_closing_verification"})
check("B4 a store present on only ONE side is still a row — a missing side IS the finding",
      any(r["fields"].get("in_closing") is False for r in D["cash_variance"]["rows"]))

print("\n== C. the mismatch tile counts mismatches ==")
check("C1 it is a COUNT, and ties as one",
      D["mismatches"]["figure"]["unit"] == rd.UNIT_COUNT
      and D["mismatches"]["tie_out"]["ties"] is True)
check("C2 it counts only the stores outside the band, and keeps the rest as context",
      D["mismatches"]["tie_out"]["counted_rows"] == 2
      and D["mismatches"]["tie_out"]["shown_not_counted"] == 1, D["mismatches"]["tie_out"])
check("C3 a matching store carries NO number — 'this store is not one of them' is not a "
      "measurement of it",
      [r for r in D["mismatches"]["rows"] if r["label"] == "1 Main St"][0]["present"] is False)

print("\n== Z. negative controls ==")
_t = totals_of(ROWS)
check("Z1 a total moved by one cent breaks its tie-out",
      trd.drilldowns(ROWS, dict(_t, pos_cash=round(_t["pos_cash"] + 0.01, 2)))
      ["pos_cash"]["tie_out"]["ties"] is False)
check("Z2 a store dropped from the evidence leaves the rows short of the unchanged figure",
      trd.drilldowns(ROWS[:2], _t)["pos_cash"]["tie_out"]["ties"] is False)
check("Z3 a variance tile whose total contradicts its own rows disagrees IN WORDS",
      "disagree" in trd.drilldowns(ROWS, dict(_t, cash_variance=0.0))
      ["cash_variance"]["tie_out"]["words"])
_flipped = [dict(ROWS[1], match=True)] + [ROWS[0], ROWS[2]]
check("Z4 a store wrongly marked matching leaves the mismatch count short",
      trd.drilldowns(_flipped, _t)["mismatches"]["tie_out"]["ties"] is False)
check("Z5 an empty range drills to panels with no rows, not panels claiming a tie over nothing",
      all(v["rows_total_count"] == 0 for v in trd.drilldowns([], totals_of([])).values()))

print("\n== E. build locks ==")
def _code_only(path):
    """A file's CODE, with strings and comments stripped. A section number in a docstring is prose;
    a float in an expression is a rate, and only the second is what E2 forbids."""
    import tokenize
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                out.append(tok.string)
    return " ".join(out)


_src = open("app/modules/commcalc/tender_recon_drilldown.py", encoding="utf-8").read()
_code = _code_only("app/modules/commcalc/tender_recon_drilldown.py")
check("E1 the declaration BUILDS no panel of its own — one shape, one renderer",
      not any(k in _src for k in ('"tie_out"', '"rows_total_count"', "def panel", "def tie_out")))
check("E2 it holds no tolerance, rate or dollar of its own — the band is the screen's parameter",
      not re.findall(r"\d+\.\d+", _code), re.findall(r"\d+\.\d+", _code))
check("E3 RULE TWO — no carrier, tenant or product name in it",
      not re.search(r"(?i)\b(boost|total\s*wireless|luxelink|cellfonz|vzone|metro|verizon)\b", _src))
_rt = open(os.path.join(ROOT, "backend", "app", "modules", "commcalc", "router.py"),
           encoding="utf-8").read()
_blk = _rt[_rt.index('@router.get("/x-tender-recon")'):]
_blk = _blk[:_blk.index('@router.get("/carrier-category-map")')]
check("E4 the endpoint builds no panel of its own",
      "_tender_recon_drilldown.drilldowns(" in _blk and "report_drilldown." not in _blk)
check("E5 and it folds from the SAME rows and totals it serves, so a tile and its drill cannot "
      "disagree about the unfiltered range",
      "_tender_recon_drilldown.drilldowns(rows_out, _totals)" in _blk
      and '"totals": _totals,' in _blk)
_labels = open(os.path.join(ROOT, "frontend", "src", "lib", "sourceLabels.ts"), encoding="utf-8").read()
_named = {s["table"] for v in D.values() for s in v["sources"]}
check("E6 every table these drills can name reads as plain words, never as a table name",
      all(("  %s:" % t) in _labels for t in _named),
      sorted(t for t in _named if ("  %s:" % t) not in _labels))

print("\n== G. the partial is DECLARED a partial ==")
_pg = open(os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing", "tender-recon",
                        "page.tsx"), encoding="utf-8").read()
check("G1 the page offers a figure's drill ONLY while the tile and the panel agree to the cent — a "
      "panel explaining a figure the screen is not showing would look like proof of the wrong number",
      "const agrees = (k: string)" in _pg and "Math.round(" in _pg and "if (agrees(pairs[i]))" in _pg)
check("G2 and when a filter makes them disagree the REASON IS STATED on screen, not hidden",
      "narrowed" in _pg and "would be about a different" in _pg)
_ledger = open("harness_drilldown_pending.txt", encoding="utf-8").read()
check("G3 so this route deliberately STILL READS `owes` in the coverage ledger — a partial drill is "
      "not a drill, and the count does not move for it",
      re.search(r"^/closing/tender-recon\towes", _ledger, re.M) is not None)
_idx = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("G4 and the real remedy is WRITTEN DOWN rather than left to be re-derived (§CORE-17.3b)",
      "CORE-17.3b" in _idx and "tender_recon_drilldown" in _idx)

print("\n== F. registration ==")
check("F1 the declaration is registered in the index", "tender_recon_drilldown" in _idx)
check("F2 so is this harness", "harness_tender_recon_drilldown.py" in _idx)

print("\n%d/%d checks passed" % (len(P), len(P) + len(F)))
if F:
    print("FAILED:")
    for f in F:
        print("  -", f)
    sys.exit(1)
