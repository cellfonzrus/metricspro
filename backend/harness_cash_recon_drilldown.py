"""PROOF — the eleven cash tiles name their rows, and the shared tile fold is honest (§CORE-17).

DB-free, stdlib only, driving the REAL functions:
  · `core/report_drilldown.tile_panel` / `.tiles` — the shared fold every tile drill-down rides;
  · `closing/cash_recon_drilldown.catalogue` / `.drilldowns` — this report's declaration.

WHAT THIS IS GUARDING AGAINST, stated so the checks are not mistaken for ceremony.

A tile drill-down is a claim: *these are the rows that figure was summed from*. The claim is cheap to
make and easy to get wrong — the wrong field, the wrong predicate, a short/over bucket that counts the
rows the tile excluded — and a WRONG drill-down is worse than none, because it looks like proof. So
the fold ties the rows back against the report's own total and every check below either proves a tie
or proves a planted error BREAKS one (sections Z).

The second thing it guards is the §19.18 failure: a mechanism built and the callers left unwired. The
build locks (section G) fail if the endpoint folds a panel of its own, if this report recomputes a
figure instead of reading the one the response serves, or if a money tile on the screen has no
declaration — which is how the screen reached eleven undrillable tiles in the first place.

  python3 backend/harness_cash_recon_drilldown.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.closing import cash_recon_drilldown as crd          # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage    # noqa: E402
from app.modules.core import report_drilldown as rd                  # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOL = 1.0
PROC = lineage.billpay_processor_feed("processor:epay")
SALES = lineage.LIVE_SALES_FEED


def day(code, name, dd, **kw):
    """One store-day exactly as `GET /closing/cash-recon-management` serves it."""
    r = {"store_code": code, "store_name": name, "market": "LI", "reps": ["Abid"], "day": dd,
         "cash_declared": 0.0, "credit_declared": 0.0,
         "epay_cash_declared": 0.0, "epay_credit_declared": 0.0,
         "cash_pickup": 0.0, "billpay_pickup": 0.0,
         "cash_picked_actual": None, "cash_picked_variance": None, "cash_picked_comparable": None,
         "mgmt_counted": None, "mgmt_variance": None,
         "pos_billpay": None, "sales_billpay": None, "sales_billpay_card": None,
         "billpay_status": "no_pos_data", "three_way_status": "declared_only"}
    r.update(kw)
    return r


#: THREE STORE-DAYS WITH A DIFFERENT SHAPE EACH, because the interesting behaviour is in the gaps:
#: one clean day, one short on both counts, and one where nobody counted anything.
ROWS = [
    day("B-1115", "1 Main St", "2026-09-01", cash_declared=1000.0, credit_declared=400.0,
        epay_cash_declared=60.0, epay_credit_declared=15.0, cash_pickup=940.0, billpay_pickup=60.0,
        cash_picked_actual=940.0, cash_picked_variance=0.0, cash_picked_comparable=True,
        mgmt_counted=940.0, mgmt_variance=0.0, pos_billpay=75.0, sales_billpay=75.0, sales_billpay_card=50.0,
        billpay_status="ok", three_way_status="ok"),
    day("B-1598", "2 Oak Ave", "2026-09-02", cash_declared=800.0, credit_declared=250.0,
        epay_cash_declared=40.0, epay_credit_declared=0.0, cash_pickup=760.0, billpay_pickup=40.0,
        cash_picked_actual=700.0, cash_picked_variance=-60.0, cash_picked_comparable=True,
        mgmt_counted=690.0, mgmt_variance=-70.0, pos_billpay=95.0, sales_billpay=40.0,
        sales_billpay_card=25.0,
        billpay_status="mismatch", three_way_status="mismatch"),
    day("B-60TH", "3 Pine Rd", "2026-09-03", cash_declared=500.0, credit_declared=100.0,
        epay_cash_declared=0.0, epay_credit_declared=0.0, cash_pickup=500.0, billpay_pickup=0.0,
        pos_billpay=0.0, sales_billpay=0.0, billpay_status="ok", three_way_status="ok"),
]


def totals_of(rows, tol=TOL):
    """The endpoint's own fold, replicated here ONCE so the harness compares the declaration against
    the real arithmetic rather than against a number this file chose. If the endpoint's fold and this
    one ever diverge, the tie-out checks below are what notices."""
    short_m = [r for r in rows if r["mgmt_variance"] is not None and r["mgmt_variance"] < -abs(tol)]
    over_m = [r for r in rows if r["mgmt_variance"] is not None and r["mgmt_variance"] > abs(tol)]
    short_d = [r for r in rows if r["cash_picked_variance"] is not None and r["cash_picked_variance"] < -abs(tol)]
    over_d = [r for r in rows if r["cash_picked_variance"] is not None and r["cash_picked_variance"] > abs(tol)]
    return {
        "cash_declared": round(sum(r["cash_declared"] for r in rows), 2),
        "credit_declared": round(sum(r["credit_declared"] for r in rows), 2),
        "epay_declared": round(sum(r["epay_cash_declared"] + r["epay_credit_declared"] for r in rows), 2),
        "cash_pickup": round(sum(r["cash_pickup"] for r in rows), 2),
        "billpay_pickup": round(sum(r["billpay_pickup"] for r in rows), 2),
        "pos_billpay": round(sum(r["pos_billpay"] or 0.0 for r in rows), 2),
        "sales_billpay": round(sum(r["sales_billpay"] or 0.0 for r in rows), 2),
        "sales_billpay_card": round(sum(r["sales_billpay_card"] or 0.0 for r in rows), 2),
        "mgmt_counted": round(sum(r["mgmt_counted"] or 0.0 for r in rows), 2),
        "cash_picked_actual": round(sum(r["cash_picked_actual"] or 0.0 for r in rows), 2),
        "mgmt_short_amount": round(sum(r["mgmt_variance"] for r in short_m), 2),
        "mgmt_over_amount": round(sum(r["mgmt_variance"] for r in over_m), 2),
        "dm_short_amount": round(sum(r["cash_picked_variance"] for r in short_d), 2),
        "dm_over_amount": round(sum(r["cash_picked_variance"] for r in over_d), 2),
        "mismatched_store_days": sum(1 for r in rows if r["billpay_status"] == "mismatch"),
        "three_way_mismatched": sum(1 for r in rows if r["three_way_status"] == "mismatch"),
    }


def drill(rows=None, totals=None, tol=TOL):
    rows = ROWS if rows is None else rows
    return crd.drilldowns(rows, totals if totals is not None else totals_of(rows, tol), tol,
                          processor_table=PROC, sales_table=SALES)


# ═══ A. the shared fold ═══════════════════════════════════════════════════════════════════════
print("\n== A. the shared tile fold (core/report_drilldown) ==")
_rows = [{"k": "a", "v": 10.0}, {"k": "b", "v": None}, {"k": "c", "v": 5.0}]
_p = rd.tile_panel("T", 15.0, _rows, value="v", tables=("daily_closing",), row_label="k", keys=("k",))
check("A1 a tile's rows are folded from the field the report declares", _p["tie_out"]["ties"] is True)
check("A2 a row with no number is NOT counted as $0.00 — it is shown, uncounted",
      _p["rows"][1]["present"] is False and _p["rows"][1]["in_figure"] is False)
check("A3 and the panel says how many rows it shows without counting them",
      _p["tie_out"]["shown_not_counted"] == 1, _p["tie_out"])
check("A4 the provenance is rolled up per table, with a row count",
      _p["sources"] == [{"table": "daily_closing", "rows": 3}], _p["sources"])
check("A5 the keys that address the row are carried, so a reader can find it again",
      _p["rows"][0]["origins"][0]["keys"] == {"k": "a"}, _p["rows"][0]["origins"])
_pc = rd.tile_panel("C", 1, _rows, value=(lambda r: 1 if r["k"] == "a" else None),
                    tables=("daily_closing",), row_label="k", unit=rd.UNIT_COUNT)
check("A6 a COUNT tile ties on its own unit, not as dollars",
      _pc["figure"]["unit"] == rd.UNIT_COUNT and _pc["tie_out"]["ties"] is True)
_ps = rd.tile_panel("S", 10.0, _rows, value="v", tables=("daily_closing",), row_label="k",
                    in_figure=(lambda r: r["k"] == "a"))
check("A7 a tile that counts a SUBSET ties against that subset and keeps the rest as context",
      _ps["tie_out"]["ties"] is True and _ps["tie_out"]["counted_rows"] == 1
      and _ps["tie_out"]["shown_not_counted"] == 2, _ps["tie_out"])
_pt = rd.tiles(({"key": "x", "label": "X", "total": "missing", "value": "v",
                 "tables": ("daily_closing",)},), _rows, {"other": 1.0})
check("A8 a tile whose figure the report never produced is ABSENT, not a panel asserting $0.00",
      _pt == {}, _pt)
_pn = rd.tiles(({"key": "x", "label": "X", "total": "t", "value": "v",
                 "tables": ("daily_closing",)},), _rows, {"t": None})
check("A9 and a total served as None is absent too — a blank is not a figure", _pn == {}, _pn)
def _code_only(path):
    """A file's CODE, with every string literal and comment removed. A dollar figure in a docstring
    is prose; a float in an expression is a rate, and only the second is what these locks forbid."""
    import io
    import tokenize
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            out.append(tok.string)
    return " ".join(out)


_fold_code = _code_only("app/modules/core/report_drilldown.py")
check("A10 the fold holds no dollar, rate or threshold of its own — it sums what a report declared "
      "and judges none of it",
      not [m for m in re.findall(r"\d+\.\d+", _fold_code) if float(m) != 0.0],
      re.findall(r"\d+\.\d+", _fold_code))

# ═══ B. every tile on the screen is declared ══════════════════════════════════════════════════
print("\n== B. every money tile on the screen has a declaration ==")
D = drill()
CAT = {t["key"] for t in crd.catalogue(TOL, processor_table=PROC, sales_table=SALES)}
check("B1 the catalogue declares all sixteen figures the eleven tiles show", len(CAT) == 16, sorted(CAT))
check("B2 and every one of them produced a panel", set(D) == CAT, sorted(set(CAT) - set(D)))
_ep = os.path.join(ROOT, "backend", "app", "modules", "closing", "router.py")
_src = open(_ep, encoding="utf-8").read()
_blk = _src[_src.index("def cash_recon_management"):_src.index('\n@router.get("/deposit-accountability")')]
_tiles_declared = set(re.findall(r'"total": "([a-z_]+)"', open(
    "app/modules/closing/cash_recon_drilldown.py", encoding="utf-8").read()))
# Parse the endpoint's OWN `_totals` literal rather than guessing at its spellings: it mixes
# `round(sum(...))`, `sum(...)` and a bare name, and a regex that saw only two of the three made this
# check lie in the reassuring direction.
_tblk = _blk[_blk.index("_totals = {"):]
_tblk = _tblk[:_tblk.index("\n    # EVERY TILE NAMES")]
_totals_served = set(re.findall(r'"([a-z_]+)":', _tblk))
_money_totals = {k for k in _totals_served if not k.endswith("_days")}
check("B3 BUILD GATE — every MONEY total the endpoint serves is declared, so the screen cannot grow "
      "a twelfth undrillable tile (which is how it grew to eleven)",
      _money_totals <= _tiles_declared, sorted(_money_totals - _tiles_declared))
check("B4 and no declaration names a total the endpoint stopped serving — a tile declared against "
      "a figure nobody produces is a dead link, not a drill-down",
      _tiles_declared <= _totals_served, sorted(_tiles_declared - _totals_served))

# ═══ C. the rows really are the rows ══════════════════════════════════════════════════════════
print("\n== C. the panels tie out against the report's own totals ==")
_bad = {k: v["tie_out"] for k, v in D.items() if v["tie_out"]["ties"] is not True}
check("C1 EVERY tile's rows add up to the figure the report shows, to the cent", _bad == {}, _bad)
check("C2 the declared-cash drill names the rep's sheet AND the verification that can override it",
      [s["table"] for s in D["cash_declared"]["sources"]] == ["daily_closing", "daily_closing_verification"],
      D["cash_declared"]["sources"])
check("C3 the management-count drill names the envelope count",
      "envelope_count" in {s["table"] for s in D["mgmt_counted"]["sources"]})
check("C4 the pickup drills name the pickup envelope, not the closing sheet",
      {s["table"] for s in D["cash_pickup"]["sources"]} == {"cash_pickup"}
      and {s["table"] for s in D["billpay_pickup"]["sources"]} == {"billpay_pickup"})
check("C5 the processor leg names the feed the LINEAGE REGISTRY resolved — never a literal here",
      {s["table"] for s in D["pos_billpay"]["sources"]} == {PROC} and PROC == "raw_epay_daily_tx")
check("C6 and the sales leg names the live sales feed from that same registry",
      {s["table"] for s in D["sales_billpay"]["sources"]} == {SALES})
check("C7 the short bucket counts ONLY the short store-days, and shows the others as context",
      D["mgmt_short_amount"]["tie_out"]["counted_rows"] == 1
      and D["mgmt_short_amount"]["tie_out"]["shown_not_counted"] == 2,
      D["mgmt_short_amount"]["tie_out"])
check("C8 the over bucket counts none of them on this data, and says so rather than showing nothing",
      D["mgmt_over_amount"]["tie_out"]["counted_rows"] == 0
      and D["mgmt_over_amount"]["rows_total_count"] == 3)
check("C9 a mismatch tile is a COUNT of store-days, and ties as a count",
      D["mismatched_store_days"]["figure"]["unit"] == rd.UNIT_COUNT
      and D["mismatched_store_days"]["tie_out"]["ties"] is True)
check("C10 every row carries the store, market and people a manager needs to act",
      all(set(("store_name", "market", "reps", "day")) <= set(r["fields"] or {})
          for r in D["cash_declared"]["rows"]))
check("C11 the figure's label is prose a manager reads, never a payload key",
      all("_" not in v["figure"]["label"] for v in D.values()),
      [v["figure"]["label"] for v in D.values() if "_" in v["figure"]["label"]])

# ═══ D. absence is reported, never zeroed ═════════════════════════════════════════════════════
print("\n== D. a day nobody counted is not a day that tied ==")
_u = [r for r in D["mgmt_counted"]["rows"] if r["label"].startswith("3 Pine")][0]
check("D1 a store-day nobody counted has NO number and is not counted in the figure",
      _u["present"] is False and _u["in_figure"] is False, _u)
check("D2 and it carries the reason in the shared vocabulary, so the screen explains the blank",
      _u["reason"] == "not_recorded" and _u["reason_label"], _u)
_scored = crd.drilldowns([day("B-9", "9 Elm", "2026-09-04", mgmt_counted=100.0, mgmt_variance=None)],
                         {"mgmt_counted": 100.0, "mgmt_short_amount": 0.0}, TOL,
                         processor_table=PROC, sales_table=SALES)
_r = _scored["mgmt_short_amount"]["rows"][0]
check("D3 a count NOBODY SCORED reads as not-computed — never as a $0.00 variance that tied",
      _r["present"] is False and _r["reason"] == "not_computed", _r)
_nc = crd.drilldowns([day("B-8", "8 Ash", "2026-09-05", cash_picked_actual=100.0,
                          cash_picked_variance=None, cash_picked_comparable=False)],
                     {"dm_short_amount": 0.0}, TOL, processor_table=PROC, sales_table=SALES)
check("D4 a pickup whose basis refuses the comparison says the two figures count different cash",
      _nc["dm_short_amount"]["rows"][0]["reason"] == "basis_mismatch")
_nofeed = crd.drilldowns(ROWS, {k: v for k, v in totals_of(ROWS).items() if k != "pos_billpay"},
                         TOL, processor_table=None, sales_table=SALES)
check("D5 a feed that never resolved for the range yields NO panel, so the screen offers no link "
      "into an empty one", "pos_billpay" not in _nofeed)
_untraceable = rd.tile_panel("T", 10.0, [{"k": "a", "v": 10.0}], value="v", tables=(None,), row_label="k")
check("D6 an origin that names no table is REPORTED as untraceable, never tidied away",
      _untraceable["untraceable_origins"] == 1, _untraceable)

# ═══ Z. negative controls — the tie-out can really go red ═════════════════════════════════════
print("\n== Z. negative controls ==")
_t = totals_of(ROWS)
_t_bad = dict(_t, cash_declared=round(_t["cash_declared"] + 0.01, 2))
check("Z1 a total moved by ONE CENT breaks its tie-out — the check is real, not a tautology",
      drill(ROWS, _t_bad)["cash_declared"]["tie_out"]["ties"] is False)
_rows_missing = ROWS[:2]
check("Z2 a store-day dropped from the evidence leaves the rows short of the unchanged figure",
      drill(_rows_missing, _t)["cash_declared"]["tie_out"]["ties"] is False)
_rows_blank = [dict(ROWS[0], cash_declared=None)] + ROWS[1:]
_z3 = drill(_rows_blank, _t)["cash_declared"]["tie_out"]
check("Z3 a row tampered to UNMEASURED makes the tie-out un-checkable (None), never a pass",
      _z3["ties"] is None and _z3["unmeasured_rows"] == 0 or _z3["ties"] is False, _z3)
_z4 = drill(ROWS, dict(_t, mgmt_short_amount=round(_t["mgmt_short_amount"] - 10.0, 2)))
check("Z4 a short bucket whose total does not match its own short days disagrees in words",
      _z4["mgmt_short_amount"]["tie_out"]["ties"] is False
      and "disagree" in _z4["mgmt_short_amount"]["tie_out"]["words"])
_z5 = crd.drilldowns([], totals_of([]), TOL, processor_table=PROC, sales_table=SALES)
check("Z5 an empty range drills to panels with no rows, not panels claiming a tie over nothing",
      all(v["rows_total_count"] == 0 for v in _z5.values()) and _z5)
_wrong = dict(ROWS[1], mgmt_variance=+70.0)
_z6 = crd.drilldowns([ROWS[0], _wrong, ROWS[2]], _t, TOL, processor_table=PROC, sales_table=SALES)
check("Z6 a variance whose SIGN was flipped moves out of the short bucket and breaks it",
      _z6["mgmt_short_amount"]["tie_out"]["ties"] is False, _z6["mgmt_short_amount"]["tie_out"])

# ═══ E. build locks ═══════════════════════════════════════════════════════════════════════════
print("\n== E. build locks — the mechanism cannot un-wire ==")
check("E1 the endpoint builds NO panel of its own — it dereferences the declaration",
      "_cash_recon_drilldown.drilldowns(" in _blk and "report_drilldown.panel(" not in _blk)
check("E2 and it folds the panels from the SAME rows and totals it serves, so a tile and its "
      "drill-down cannot disagree",
      re.search(r"_cash_recon_drilldown\.drilldowns\(\s*\n?\s*out,\s*_totals,", _blk) is not None)
_crd_src = open("app/modules/closing/cash_recon_drilldown.py", encoding="utf-8").read()
check("E3 the declaration BUILDS no panel of its own — one shape, one renderer. (It passes a "
      "`schema` kwarg to the shared fold, which is declaring, not building; the keys below are ones "
      "only a builder would spell.)",
      not any(k in _crd_src for k in ('"rows_total_count"', '"tie_out"', '"untraceable_origins"',
                                      '"rows_shown"', "def panel", "def tie_out")))
check("E4 the declaration names NO feed table literally — the lineage registry is dereferenced",
      not re.search(r'"raw_[a-z_]+"', _crd_src) and "billpay_processor_feed" not in _crd_src,
      re.findall(r'"raw_[a-z_]+"', _crd_src))
check("E5 the endpoint dereferences the registry for both feed legs",
      "_lineage.billpay_processor_feed(" in _blk and "_lineage.LIVE_SALES_FEED" in _blk)
check("E6 RULE TWO — no carrier, tenant or product name in either new file",
      not re.search(r"(?i)\b(boost|total\s*wireless|luxelink|cellfonz|vzone|metro|at&t|verizon)\b",
                    _crd_src))
check("E7 the declaration holds no tolerance, rate or threshold of its own — the band is the "
      "screen's own parameter",
      not [m for m in re.findall(r"\d+\.\d+", _crd_src) if float(m) not in (0.0, 1.0)],
      re.findall(r"\d+\.\d+", _crd_src))
_page = os.path.join(ROOT, "frontend", "src", "app", "(platform)", "closing",
                     "cash-recon-management", "page.tsx")
_pg = open(_page, encoding="utf-8").read()
check("E8 the page renders the SHARED panel and hand-rolls none of it",
      "DrillDownPanel" in _pg and "tie_out" not in _pg)
_drill_home = open(os.path.join(ROOT, "frontend", "src", "components",
                                "DrillDownPanel.tsx"), encoding="utf-8").read()
check("E9 and it offers a tile's link only when the server sent that panel — DEREFERENCED from the "
      "shared offer rule (§CORE-17.3c), never a second copy of the loop in this page",
      "useDrillTile(drills)" in _pg and "const dl = drill.dl" in _pg
      and "if (p && (!gate || gate(pairs[i], p)))" in _drill_home
      and "const dl = (...pairs: string[]) => {" not in _pg)
_labels = open(os.path.join(ROOT, "frontend", "src", "lib", "sourceLabels.ts"), encoding="utf-8").read()
_named = {s["table"] for v in D.values() for s in v["sources"]}
_unlabelled = sorted(t for t in _named if ("  %s:" % t) not in _labels)
check("E10 every table these drills can name has a PLAIN-WORDS label — a manager never sees a "
      "table name (§19.38, owner: 'hide database names from the users')",
      _unlabelled == [], _unlabelled)
_migs = " ".join(sorted(os.listdir(os.path.join(ROOT, "database", "migrations"))))
_mig_src = ""
for fn in os.listdir(os.path.join(ROOT, "database", "migrations")):
    if fn.endswith(".sql"):
        _mig_src += open(os.path.join(ROOT, "database", "migrations", fn),
                         encoding="utf-8", errors="ignore").read()
_uncreated = sorted(t for t in _named if t not in _mig_src)
check("E11 and every one of them is a table a migration actually creates — a drill cannot name a "
      "relation that does not exist", _uncreated == [], _uncreated)

print("\n== F. registration ==")
_idx = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("F1 the declaration is registered in the index (owner directive 2026-09-01)",
      "cash_recon_drilldown" in _idx)
check("F2 so is this harness", "harness_cash_recon_drilldown.py" in _idx)
check("F3 and the shared tile fold is named there too", "tile_panel" in _idx)
_ledger = open("harness_drilldown_pending.txt", encoding="utf-8").read()
check("F4 the coverage ledger no longer says this report owes a drill-down",
      re.search(r"^/closing/cash-recon-management\tshared", _ledger, re.M) is not None)

print("\n%d/%d checks passed" % (len(P), len(P) + len(F)))
if F:
    print("FAILED:")
    for f in F:
        print("  -", f)
    sys.exit(1)
