"""Offline proof harness for A REPORTED FIGURE NAMES THE ROWS BEHIND IT (owner ask 2026-10-24).

Pure stdlib, no DB and no network — it drives the REAL functions in
`app/modules/core/report_drilldown.py` and the REAL cash-variance producer
(`app/modules/closing/cash_variance.py`), never reimplementations of either.

WHAT IS PROVEN
  A. `origin` — a source row reference carries its table, schema, keys and column; a blank key is
     DROPPED rather than served as an empty string; a table nobody named is `traceable: False`
     instead of a guess.
  B. `drill_row` — absence is never zero: `amount=None` is `present: False` / `measured: False`, and
     a row the figure deliberately excludes keeps `in_figure: False` instead of being dropped.
  C. `tie_out` STATES A DISAGREEMENT. It ties only when every counted row is measured AND the rows
     total the figure to the cent; it is **None** (never False) when a counted row has no number,
     because a sum missing a row was never a comparison; and it is False — in words — when the rows
     are all measured and genuinely total something else.
  D. `panel` — the sources list is the owner's question answered: the distinct tables, counted. The
     tie-out is computed over ALL the rows, before the cap, and `rows_total_count` always states the
     true count so a capped panel cannot present a shorter month as the whole of one. An origin that
     names no table is REPORTED under `untraceable_origins`, never tidied away.
  E. THE CASH VARIANCE DRILL, end to end on the report's own output. The figure is the group's own
     `net_variance`, and it ties to the cent. **Negative controls**: dropping one out-of-band day
     breaks the tie; mis-scoring a day breaks the tie; a day with no management count is carried as
     unmeasured and shown, never summed as $0.00; a day inside the band is shown and NOT counted.
     Every leg names the table it was read out of, with keys that address the row.
  F. THE DRILL NAMES NO DATABASE. Every table the producer can name is (1) created by a migration
     and (2) present in `frontend/src/lib/sourceLabels.ts`, so the operator reads a feed's plain
     name and never an identifier (§19.38, owner: "hide database names from the users").
  G. BUILD LOCKS so it cannot un-wire: the endpoint serves the drill through the one home and builds
     no panel dict of its own; the page renders the shared component and hand-rolls no panel; the
     one home holds no business figure of its own; and `UNMEASURED_REASONS` has not forked from
     `cash_variance`'s own vocabulary.
  H. Registration: the one home, the component, the index section and the coverage ledger all exist.

Run: `cd backend && python3 harness_report_drilldown.py`
"""
import glob
import os
import re
import sys

sys.path.insert(0, ".")

from app.modules.core import report_drilldown as rd
from app.modules.closing import cash_variance as cv

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


BAND = 5.00
ROOT = ".."
SRC_ONE_HOME = open("app/modules/core/report_drilldown.py", encoding="utf-8").read()
SRC_CV = open("app/modules/closing/cash_variance.py", encoding="utf-8").read()
SRC_ROUTER = open("app/modules/closing/router.py", encoding="utf-8").read()
PAGE = f"{ROOT}/frontend/src/app/(platform)/closing/cash-variance/page.tsx"
SRC_PAGE = open(PAGE, encoding="utf-8").read()
SRC_COMP = open(f"{ROOT}/frontend/src/components/DrillDownPanel.tsx", encoding="utf-8").read()
SRC_LABELS = open(f"{ROOT}/frontend/src/lib/sourceLabels.ts", encoding="utf-8").read()

# ── A. One source row reference. ───────────────────────────────────────────────────────────────
print("\n== A. origin(): which row, in which table, found by which keys ==")
o = rd.origin("daily_closing", schema="commcalc",
              keys={"closing_row_id": "abc", "store_code": "", "employee_name": None},
              column="t_cash", note="declared at closing")
check("A1 an origin carries table, schema and column",
      (o["table"], o["schema"], o["column"]) == ("daily_closing", "commcalc", "t_cash"), str(o))
check("A2 a blank or missing key is DROPPED, so a key that is present means something",
      o["keys"] == {"closing_row_id": "abc"}, str(o["keys"]))
check("A3 an origin that names a table is traceable", o["traceable"] is True)
u = rd.origin(None, schema="commcalc", keys={"x": 1})
check("A4 a table nobody named is NOT traceable — it is never guessed",
      u["table"] is None and u["traceable"] is False, str(u))
check("A5 no-keys collapses to None rather than an empty dict a screen would render as blank",
      rd.origin("cash_pickup")["keys"] is None)
check("A6 origins_of builds one origin per table with the SAME keys (the per-store-day shape)",
      [x["table"] for x in rd.origins_of([("a", {"k": 1}), ("b", {"k": 1})])] == ["a", "b"]
      and all(x["keys"] == {"k": 1} for x in rd.origins_of([("a", {"k": 1}), ("b", {"k": 1})])))

# ── B. One row behind a figure. ────────────────────────────────────────────────────────────────
print("\n== B. drill_row(): absence is never zero ==")
r_ok = rd.drill_row("2026-09-01", [o], amount=-100)
check("B1 a row with a figure is present and measured",
      (r_ok["amount"], r_ok["present"], r_ok["measured"]) == (-100.0, True, True), str(r_ok))
r_abs = rd.drill_row("2026-09-04", [], amount=None, reason="not_recorded")
check("B2 a row nobody recorded is present:False / measured:False and carries NO number",
      r_abs["amount"] is None and r_abs["present"] is False and r_abs["measured"] is False)
check("B3 and it carries the reason as a sentence a screen can show as-is",
      r_abs["reason_label"] == rd.UNMEASURED_REASONS["not_recorded"], str(r_abs["reason_label"]))
r_ctx = rd.drill_row("2026-09-03", [o], amount=2.0, in_figure=False)
check("B4 a row the figure EXCLUDES stays in the drill-down, flagged, rather than being dropped",
      r_ctx["in_figure"] is False and r_ctx["present"] is True)
check("B5 a row names its distinct source tables", r_ok["sources"] == ["daily_closing"])
check("B6 a caller holding a number it cannot trust can say so explicitly",
      rd.drill_row("x", [], amount=5, measured=False, reason="basis_mismatch")["measured"] is False)
check("B7 an unlabelled row is named, never rendered as a blank line",
      rd.drill_row(None, [])["label"] == "(unnamed)")

# ── C. The tie-out states a disagreement. ──────────────────────────────────────────────────────
print("\n== C. tie_out(): the report against its own evidence ==")
t = rd.tie_out(0.0, [rd.drill_row("a", [o], amount=-100), rd.drill_row("b", [o], amount=100)])
check("C1 all counted rows measured and totalling the figure ⇒ ties True",
      t["ties"] is True and t["difference"] == 0.0, str(t))
t2 = rd.tie_out(0.0, [rd.drill_row("a", [o], amount=-100), rd.drill_row("b", [o], amount=90)])
check("C2 all measured but a different total ⇒ ties FALSE, and the words SAY the report and its own "
      "evidence disagree",
      t2["ties"] is False and "disagree" in t2["words"], str(t2))
t3 = rd.tie_out(0.0, [rd.drill_row("a", [o], amount=-100), rd.drill_row("b", [], amount=None)])
check("C3 a counted row with no number ⇒ ties is None, NEVER False and never True: a sum missing a "
      "row was never a comparison",
      t3["ties"] is None and t3["unmeasured_rows"] == 1, str(t3))
t4 = rd.tie_out(-100.0, [rd.drill_row("a", [o], amount=-100),
                         rd.drill_row("c", [o], amount=2.0, in_figure=False)])
check("C4 a row shown for context does not enter the sum, and the panel SAYS how many are shown "
      "without being counted",
      t4["ties"] is True and t4["shown_not_counted"] == 1 and "not counted in it" in t4["words"],
      str(t4))
check("C5 an empty drill-down against a non-zero figure does not read as a tie",
      rd.tie_out(50.0, [])["ties"] is False)

# ── D. The panel. ──────────────────────────────────────────────────────────────────────────────
print("\n== D. panel(): where the data is coming from, in one read ==")
o2 = rd.origin("cash_pickup", schema="commcalc", keys={"store_code": "B-117"})
pn = rd.panel("Net variance", -100.0, [rd.drill_row("a", [o, o2], amount=-100),
                                       rd.drill_row("b", [o], amount=2.0, in_figure=False)])
check("D1 the sources list names the distinct tables and counts the rows that touched each",
      pn["sources"] == [{"table": "daily_closing", "rows": 2}, {"table": "cash_pickup", "rows": 1}],
      str(pn["sources"]))
check("D2 the panel states the TRUE row count whether or not every row fits",
      (pn["rows_total_count"], pn["capped"]) == (2, False))
many = [rd.drill_row(str(i), [o], amount=0) for i in range(rd.ROW_CAP + 7)]
pc = rd.panel("x", 0.0, many)
check("D3 a capped panel serves ROW_CAP rows and still reports the true total, so it cannot present "
      "a shorter month as the whole of one",
      (pc["rows_shown"], pc["rows_total_count"], pc["capped"]) == (rd.ROW_CAP, rd.ROW_CAP + 7, True),
      str((pc["rows_shown"], pc["rows_total_count"])))
check("D4 and the tie-out is over ALL the rows, before the cap — a tie-out over a truncated list "
      "would be a different claim",
      pc["tie_out"]["counted_rows"] == rd.ROW_CAP + 7)
pu = rd.panel("x", 0.0, [rd.drill_row("a", [rd.origin(None)], amount=0)])
check("D5 a figure that could not be traced to a source is REPORTED, never tidied away",
      pu["untraceable_origins"] == 1 and pu["sources"] == [], str(pu))
check("D6 an unknown unit degrades to money rather than to a format a screen cannot render",
      rd.panel("x", 1, [], unit="furlongs")["figure"]["unit"] == rd.UNIT_MONEY)
check("D7 the panel carries its SCHEMA so a stale renderer fails loudly",
      pn["schema"] == rd.SCHEMA)

# ── E. The cash variance drill, end to end on the report's own output. ─────────────────────────
print("\n== E. the cash variance drill-down, over the real report ==")


def _day(d, emp, store, counted, t_cash, pos, basis="off"):
    return cv.day_row(
        {"closing_row_id": f"row-{d}-{store}", "close_date": d, "store_code": store,
         "store_address": "1 Main St", "market": "LI", "employee_name": emp},
        received=cv.received_leg({}, {"actual_picked_amount": counted, "amount_basis": basis}
                                    if counted is not None else {}, {}),
        declared=cv.declared_leg({"t_cash": t_cash}),
        pos=cv.pos_leg({"b2b_cash": pos}), dollar_band=BAND)


ROWS = [_day("2026-09-01", "Abid", "B-117", 900, 1000, 1000),    # short 100
        _day("2026-09-02", "Abid", "B-117", 1250, 1000, 1000),   # over 250
        _day("2026-09-03", "Abid", "B-117", 1002, 1000, 1000),   # inside the band
        _day("2026-09-04", "Abid", "B-117", None, 1000, 1000)]   # no management count
EMPS = cv.by_employee(ROWS)
LABELS = {b["key"]: b["label"] for b in cv.basis_catalog("ePay")}
HEAD = cv.pair_catalog("RQ")[0]


def _drill(rows):
    return cv.drilldowns(rows, cv.by_employee(rows), "employee_name",
                         HEAD["key"], HEAD["label"], LABELS)["Abid"]


P = _drill(ROWS)
check("E1 the figure is the group's OWN net variance, read off totals() and not re-derived",
      P["figure"]["amount"] == EMPS[0]["pairs"][HEAD["key"]]["net_variance"] == 150.0,
      str(P["figure"]))
check("E2 and the rows behind it TIE to it, to the cent",
      P["tie_out"]["ties"] is True and P["tie_out"]["difference"] == 0.0, str(P["tie_out"]))
check("E3 every store-day is shown — the measured, the in-band and the unmeasurable",
      P["rows_total_count"] == 4 and [r["label"] for r in P["rows"]] ==
      ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"])
check("E4 only the out-of-band days are COUNTED in the figure",
      [r["in_figure"] for r in P["rows"]] == [True, True, False, False])
check("E5 the day with no management count carries NO number and says why — it is not summed as "
      "$0.00, which is the whole defect this report exists to prevent",
      P["rows"][3]["amount"] is None and P["rows"][3]["measured"] is False
      and P["rows"][3]["present"] is False)
check("E6 the day inside the band keeps its real variance and is shown for context",
      P["rows"][2]["amount"] == 2.0 and P["rows"][2]["in_figure"] is False)
check("E7 the panel NAMES THE TABLES the rows came out of — the owner's question",
      {s["table"] for s in P["sources"]} == {"daily_closing", "cash_pickup"}, str(P["sources"]))
check("E8 each leg names the table AND the keys that address its row",
      P["rows"][0]["fields"]["got"]["source"] == "cash_pickup"
      and P["rows"][0]["fields"]["expected"]["source"] == "daily_closing"
      and {o["table"]: o["keys"] for o in P["rows"][0]["origins"]}["daily_closing"]
      == {"closing_row_id": "row-2026-09-01-B-117"},
      str(P["rows"][0]["origins"]))
check("E9 a leg carries the basis IN THE TENANT'S OWN WORDS, so the screen spells no basis key",
      P["rows"][0]["fields"]["got"]["basis_label"] == LABELS[cv.BASIS_TOTAL])
check("E10 every pair's verdict is on every row, so the end-to-end check being unmeasurable is "
      "visible beside the envelope check being short",
      set(P["rows"][0]["fields"]["pairs"]) == set(cv.PAIR_KEYS))
check("E11 the figure's LABEL says it is the net of short and over — a zero here must read as "
      "'they cancelled out', never as 'clean'",
      "net of short and over" in P["figure"]["label"], P["figure"]["label"])
check("E12 the note carries the two GROSS counts beside it",
      "short" in (P["note"] or "") and "over" in (P["note"] or ""), str(P["note"]))

print("\n-- E negative controls: the tie-out has to be breakable to be worth anything --")
Z1 = cv.drilldowns([r for r in ROWS if r["close_date"] != "2026-09-02"], EMPS, "employee_name",
                   HEAD["key"], HEAD["label"], LABELS)["Abid"]
check("Z1 control: DROPPING an out-of-band day from the evidence breaks the tie against the "
      "unchanged figure",
      Z1["tie_out"]["ties"] is False and Z1["tie_out"]["difference"] != 0.0, str(Z1["tie_out"]))
_bad = [dict(r) for r in ROWS]
_bad[0] = dict(_bad[0])
_bad[0]["pairs"] = dict(_bad[0]["pairs"])
_bad[0]["pairs"][HEAD["key"]] = {**_bad[0]["pairs"][HEAD["key"]], "variance": -999.0}
Z2 = cv.drilldowns(_bad, EMPS, "employee_name", HEAD["key"], HEAD["label"], LABELS)["Abid"]
check("Z2 control: MIS-SCORING one day breaks the tie — the drill-down is a real check of the "
       "report against its evidence, not a tautology",
      Z2["tie_out"]["ties"] is False, str(Z2["tie_out"]))
_un = [dict(r) for r in ROWS]
_un[1] = dict(_un[1])
_un[1]["pairs"] = dict(_un[1]["pairs"])
_un[1]["pairs"][HEAD["key"]] = {**_un[1]["pairs"][HEAD["key"]], "measured": False,
                                "reason": "basis_unknown"}
Z3 = cv.drilldowns(_un, EMPS, "employee_name", HEAD["key"], HEAD["label"], LABELS)["Abid"]
check("Z3 control: a day TAMPERED to unmeasurable leaves the figure's evidence short, and the "
      "tie-out says the report and its evidence disagree rather than passing",
      Z3["tie_out"]["ties"] is False, str(Z3["tie_out"]))
Z3b = _drill(_un)
check("Z3b and with the rollup recomputed over the SAME tampered rows it ties again — which proves "
      "the property this report relies on: an unmeasurable day is never COUNTED in the figure, so "
      "the tie-out here can always be computed (the generic refusal is proven at C3)",
      Z3b["tie_out"]["ties"] is True
      and [r["in_figure"] for r in Z3b["rows"]] == [True, False, False, False],
      str(Z3b["tie_out"]))
Z4 = cv.drilldowns(ROWS, cv.by_store(ROWS), "store_code", HEAD["key"], HEAD["label"], LABELS)
check("Z4 control: the STORE rollup drills from the same rows and ties too — one mechanism, both "
      "grains, so a drill can never hold a different set of days than the row it opened from",
      list(Z4) == ["B-117"] and Z4["B-117"]["tie_out"]["ties"] is True
      and Z4["B-117"]["rows_total_count"] == 4)
Z5 = cv.drilldowns([], [], "employee_name", HEAD["key"], HEAD["label"], LABELS)
check("Z5 control: an empty report drills to an empty mapping, not to a panel asserting a tie",
      Z5 == {})

# ── F. The drill names no database. ────────────────────────────────────────────────────────────
print("\n== F. the operator reads a feed's plain name, never a table ==")
MIG = "".join(open(f, encoding="utf-8").read()
              for f in glob.glob(f"{ROOT}/database/migrations/*.sql"))
DRILL_TABLES = sorted(set(cv.LEG_ROW_KEYS) | set(cv.LEG_SOURCE_COLUMN)
                      | {t for ts in cv.LEG_EXTRA_TABLES.values() for t in ts})
_no_mig = [t for t in DRILL_TABLES if not re.search(rf"\b{t}\b", MIG)]
check("F1 every table a drill origin can name is CREATED BY A MIGRATION — a provenance claim about "
      "a relation that does not exist is worse than none",
      _no_mig == [], str(_no_mig))
_no_label = [t for t in DRILL_TABLES if not re.search(rf"^\s+{t}:", SRC_LABELS, re.M)]
check("F2 BUILD LOCK — and every one has a `sourceLabels.ts` entry, so the operator reads "
      "'management envelope count', never an identifier (§19.38)",
      _no_label == [], str(_no_label))
check("F3 the two maps agree: a table with row keys has a column and vice versa, so no leg can be "
      "traced to a row without naming the figure's column",
      sorted(cv.LEG_ROW_KEYS) == sorted(cv.LEG_SOURCE_COLUMN),
      str((sorted(cv.LEG_ROW_KEYS), sorted(cv.LEG_SOURCE_COLUMN))))
check("F4 every pair names a pair of legs that `day_row` actually builds",
      set(cv.PAIR_LEGS) == set(cv.PAIR_KEYS)
      and all(set(v) <= {"received", "declared", "pos"} for v in cv.PAIR_LEGS.values()))
check("F5 the component renders a source THROUGH sourceLabel and spells no table of its own",
      "sourceLabel(" in SRC_COMP
      and not re.search(r"'(raw_[a-z_]+|daily_closing|cash_pickup|envelope_count)'", SRC_COMP))

# ── G. Build locks, so it cannot un-wire. ──────────────────────────────────────────────────────
print("\n== G. build locks ==")
_EP = SRC_ROUTER.split('@router.get("/cash-variance")', 1)[1].split("\nclass ", 1)[0] \
    if '@router.get("/cash-variance")' in SRC_ROUTER else ""
check("G1 the endpoint serves the drill THROUGH the one home's producer",
      "_cash_variance.drilldowns(" in _EP and '"drilldown"' in _EP, _EP[:0] or "see router")
check("G2 BUILD LOCK — and builds no panel of its own: no second shape for the same question",
      "rd.panel(" not in _EP and "report_drilldown.panel(" not in _EP
      and '"tie_out"' not in _EP and '"rows_total_count"' not in _EP)
check("G3 BUILD LOCK — the producer dereferences the ONE home and defines no panel shape itself",
      "report_drilldown" in SRC_CV and "def panel(" not in SRC_CV and "def tie_out(" not in SRC_CV)
# A zero initialiser is not a figure; a NON-ZERO one would be. Same exception
# `harness_cash_variance.py` §F makes, so the two locks read the same way.
_HOME_FLOATS = [m for m in re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])",
                                      re.sub(r"#.*", "", SRC_ONE_HOME.split('"""', 2)[2]))
                if float(m) != 0.0]
check("G4 BUILD LOCK — the one home computes NO business figure: every amount it carries was "
      "handed to it, so it can never become a second opinion about money",
      _HOME_FLOATS == [] and "def compare(" not in SRC_ONE_HOME, str(_HOME_FLOATS))
check("G5 BUILD LOCK — the reason vocabulary has NOT forked from cash_variance's own words",
      all(rd.UNMEASURED_REASONS[k] == v for k, v in cv.UNMEASURED_REASONS.items()),
      str(sorted(set(cv.UNMEASURED_REASONS) - set(rd.UNMEASURED_REASONS))))
check("G6 BUILD LOCK — the page renders the SHARED component and hand-rolls no panel",
      "DrillDownPanel" in SRC_PAGE
      and "tie_out" not in SRC_PAGE and "rows_total_count" not in SRC_PAGE)
check("G7 BUILD LOCK — the page derives no drill of its own: it renders what the server served",
      "data?.drilldown?." in SRC_PAGE and "reduce(" not in SRC_PAGE.split("drilldown", 1)[1][:2000])
check("G8 RULE TWO — no carrier, tenant, product or vendor name in the one home or the component",
      not re.search(r"(?i)\b(boost|total wireless|vidapay|luxelink|cellfonz|verizon|t-?mobile|"
                    r"at&t|tracfone|rq|b2bsoft)\b", SRC_ONE_HOME + SRC_COMP))

# ── H. Registration. ───────────────────────────────────────────────────────────────────────────
print("\n== H. registration ==")
IDX = open(f"{ROOT}/docs/SYSTEM_DATA_FLOW_INDEX.md", encoding="utf-8").read()
check("H1 the one home exists where the index says it does",
      os.path.exists("app/modules/core/report_drilldown.py"))
check("H2 registered in the index (the mandatory owner directive 2026-09-01)",
      "core/report_drilldown.py" in IDX and "CORE-17" in IDX)
check("H3 the shared renderer is registered too — a server shape with no one renderer is half a fix",
      "DrillDownPanel.tsx" in IDX)
check("H4 the coverage ledger exists: the reports that still owe a drill-down are WRITTEN DOWN, so "
      "an excuse nobody wrote down cannot pass for an oversight",
      os.path.exists("harness_drilldown_pending.txt"))
check("H5 the module graph names this fact, so 'who else answers this question' is one call away",
      "report_drilldown" in open("app/modules/core/module_graph.py", encoding="utf-8").read())

print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
