"""PROOF — THE THREE COMMISSION DISAGREEMENT REPORTS NAME THE ROWS BEHIND THEIR FIGURES.

DB-free, stdlib only, driving the REAL producers through the shared fold
(`core/report_drilldown.tiles` / `tile_panel` / `panel`): `carrier_vs_pay.rollup_by_rep` and
`earnings_columns`, `commission_received.build_breakout` with the real
`commission_legs.default_classifier()`, and `carrier_claim_value.claim_gap` for the gap a stored
discrepancy row carries. Batch 11 of the §CORE-17 audit: /commcalc/discrepancy,
/commcalc/carrier-vs-pay and /commcalc/commission-legs.

WHAT THIS BATCH IS ABOUT.

1. A DIFFERENCE IS DRILLABLE WHEN IT IS FOLDED PER ROW. §CORE-17.3h refuses a panel to a quotient and
   batch 10 extended that to `A_total − B_total`. Both differences here escape it, because the row
   carries its own difference and the total is Σ of those rows — so the tie-out is a real comparison
   that can disagree. §A, §B.

2. A FIGURE READ OUT OF A SNAPSHOT NAMES THE SNAPSHOT, and where the writer recorded a feed it names
   that too — per row, off the row's OWN stored evidence. The legacy writer records none, which is
   reported as the gap it is rather than filled with a guess. §A.

3. ABSENCE IS NEVER ZERO, THREE TIMES OVER. A commission claim nobody could price, a rep whose
   carrier statement was never loaded, and a month a feed never delivered are all SHOWN and not
   counted, each with its reason. None of them moves a figure by a cent, which is the point: the
   presentation changes, the number does not. §A3, §B2, §C4.

4. ONE DECLARATION, THREE CATALOGUES, LOCKED. The provenance map is keyed on values the platform
   STORED and is checked complete against the producer's own stream registry and against the prose
   the page prints, so it cannot un-wire. §Y.

5. THREE LIVE DEFECTS, REPRODUCED NOT HIDDEN. §Z6 reproduces the `TypeError` that 500s the Pay
   Discrepancy endpoint on an unsized claim; §Z7 pins that carrier-vs-pay's totals ignore its own
   market filter; §Z8 pins that the client reduce and the server fold agree to the cent, which is
   what makes defect 2 a presentation fix and not a figure moving.

  python3.11 backend/harness_batch11_drilldown.py
"""
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import carrier_claim_value as ccv                      # noqa: E402
from app.modules.commcalc import carrier_pay_drilldown as cpd                    # noqa: E402
from app.modules.commcalc import carrier_vs_pay as cvp                           # noqa: E402
from app.modules.commcalc import commission_legs as legs                         # noqa: E402
from app.modules.commcalc import commission_received as cr                       # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage                # noqa: E402
from app.modules.commcalc import ma_recon                                        # noqa: E402
from app.modules.core import report_drilldown as rd                              # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label
          + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECL = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "carrier_pay_drilldown.py")
ROUTER = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "router.py")
PAGES = {
    "/commcalc/discrepancy": "frontend/src/app/(platform)/commcalc/discrepancy/page.tsx",
    "/commcalc/carrier-vs-pay": "frontend/src/app/(platform)/commcalc/carrier-vs-pay/page.tsx",
    "/commcalc/commission-legs": "frontend/src/app/(platform)/commcalc/commission-legs/page.tsx",
}


def _read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


def _code_only(path):
    """The file's CODE, with comments and docstrings dropped. A build lock about what a module DOES
    must not fire on prose that merely describes it — the §CORE-17.3j narrowing of
    `harness_pl_drill_detail` §G5, reused here rather than re-derived. A file that will not tokenise
    is returned WHOLE, so the narrowing fails closed."""
    try:
        out = []
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                    out.append(tok.string)
        return " ".join(out)
    except Exception:
        return open(path, encoding="utf-8").read()


def _numbers(path):
    nums = []
    try:
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type == tokenize.NUMBER:
                    nums.append(tok.string)
    except Exception:
        return ["?"]
    return nums


def _row(panel, label):
    return next((r for r in (panel or {}).get("rows") or [] if r.get("label") == label), None)


def _tables(row):
    return sorted((row or {}).get("sources") or [])


def _tie(panel):
    return ((panel or {}).get("tie_out") or {}).get("ties")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A. PAY DISCREPANCY — the stored run's own rows, each naming where it came from.
#
# The fixture is the shape `discrepancy_results` really holds, with every gap computed by the REAL
# `carrier_claim_value.claim_gap` so an unsized claim is None here exactly as it is in the table.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. the Pay Discrepancy report opens to its commission lines ==")


def _drow(i, status, expected, received, source, store="S-1", evidence=None, comp="NAB"):
    return {
        "id": i, "status": status, "store": store, "rep_username": "ANA",
        "comp_type": comp, "bounty_month": 1, "imei": "IMEI-%d" % i, "mdn": "5550%d" % i,
        "expected_amount": expected, "received_amount": received,
        "gap": ccv.claim_gap(expected, received),
        "source": source, "evidence": evidence,
        "device_model": "handset", "customer_plan": "plan", "activation_date": "2026-09-0%d" % i,
    }


MA_EVIDENCE = {"b2b": {"trans_id": "T1", "source_table": lineage.MONTHLY_SALES}}
DROWS = [
    _drow(1, "open", 20.0, 5.0, ma_recon.SOURCE_MA, evidence=MA_EVIDENCE),   # gap 15.00
    _drow(2, "open", None, 0.0, "", comp="SOLD_NOT_IN_MI"),                  # gap NEVER SIZED
    _drow(3, "open", 10.0, 0.0, "", store="S-2"),                            # gap 10.00
    _drow(4, "info", 2.5, 2.5, "", comp="SIMCR"),                            # gap 0.00, measured
]

#: The row label the declaration builds for the claim nobody could price — named once so a check
#: cannot quietly test a row that is not there.
UNSIZED = "SOLD_NOT_IN_MI · month 1 · IMEI-2"

dpan = cpd.discrepancy_drilldowns(DROWS)
open_p = dpan.get("gap_open")
check("A1 the open tab's headline total is Σ(the recorded gaps) over exactly its own rows",
      cpd.status_total(DROWS, "open") == 25.0, cpd.status_total(DROWS, "open"))
check("A2 the panel's figure IS that total and the rows add up to it — a real tie, not a restatement",
      open_p and open_p["figure"]["amount"] == 25.0 and _tie(open_p) is True,
      (open_p or {}).get("tie_out"))
check("A3 ABSENCE IS NOT ZERO — the claim nobody could price is SHOWN, carries no figure, is not "
      "counted, and says why",
      (lambda r: r and r["amount"] is None and r["in_figure"] is False
       and r["measured"] is False and r["reason"] == "amount_not_recorded"
       and r["reason_label"])(_row(open_p, UNSIZED)),
      _row(open_p, UNSIZED))
check("A4 and the tie-out STILL ties, because an unsized claim is excluded rather than counted at "
      "$0.00 — the honest row changes the words, never the figure",
      (open_p["tie_out"]["shown_not_counted"] == 1
       and open_p["tie_out"]["counted_rows"] == 2), open_p["tie_out"])
check("A5 §CORE-17.3j — EVERY row names the SNAPSHOT it was read out of",
      all(lineage.DISCREPANCY_SNAPSHOT in _tables(r) for r in open_p["rows"]),
      [_tables(r) for r in open_p["rows"]])
check("A6 a row whose writer stamped itself names the feeds that writer reads unconditionally",
      _tables(_row(open_p, "NAB · month 1 · IMEI-1"))
      == sorted([lineage.DISCREPANCY_SNAPSHOT, lineage.MA_COMMISSION, lineage.MA_DAILY_TX,
                 lineage.MONTHLY_SALES]),
      _tables(_row(open_p, "NAB · month 1 · IMEI-1")))
check("A7 and the SOLD-side table comes from that row's OWN stored evidence, not from a constant",
      cpd.evidence_tables(DROWS[0]) == (lineage.MONTHLY_SALES,)
      and cpd.evidence_tables(DROWS[2]) == (),
      (cpd.evidence_tables(DROWS[0]), cpd.evidence_tables(DROWS[2])))
check("A8 a stored source_table the registry's union does not know is IGNORED — a stored string is "
      "not a licence to print an identifier on a manager's screen",
      cpd.evidence_tables({"evidence": {"b2b": {"source_table": "some_other_place"}}}) == (),
      cpd.evidence_tables({"evidence": {"b2b": {"source_table": "some_other_place"}}}))
check("A9 the legacy writer's rows name ONLY the snapshot — the gap is reported, never filled in",
      _tables(_row(open_p, "NAB · month 1 · IMEI-3")) == [lineage.DISCREPANCY_SNAPSHOT],
      _tables(_row(open_p, "NAB · month 1 · IMEI-3")))
check("A10 a row addresses the stored line by its own primary key, so a reader can find it again",
      all((r.get("row_key") or {}).get("id") for r in open_p["rows"]),
      [r.get("row_key") for r in open_p["rows"]])
check("A11 a tab with no rows gets NO panel rather than one asserting $0.00 behind nothing",
      "gap_pending" not in dpan and "gap_lagged" not in dpan and "gap_info" in dpan,
      sorted(dpan))
check("A12 a MEASURED zero is counted — the informational tab's $0.00 line is a real measurement, "
      "which is a different fact from the unpriced claim above",
      (lambda p: p["figure"]["amount"] == 0.0 and p["tie_out"]["counted_rows"] == 1
       and _tie(p) is True)(dpan["gap_info"]), dpan["gap_info"]["tie_out"])
check("A13 the panel answers 'where is this from' in one read — the distinct tables, counted",
      [s["table"] for s in open_p["sources"]][0] == lineage.DISCREPANCY_SNAPSHOT
      and {s["table"] for s in open_p["sources"]} == {
          lineage.DISCREPANCY_SNAPSHOT, lineage.MA_COMMISSION, lineage.MA_DAILY_TX,
          lineage.MONTHLY_SALES},
      open_p["sources"])

dstore = cpd.discrepancy_store_drilldowns(DROWS)
check("A14 the By-Store table's own gap opens too — one panel per (tab, store), named for the store",
      sorted(dstore["open"]) == ["S-1", "S-2"]
      and dstore["open"]["S-2"]["subject"] == "S-2", sorted(dstore.get("open") or {}))
check("A15 each store's panel ties against that store's own figure",
      dstore["open"]["S-2"]["figure"]["amount"] == 10.0 and _tie(dstore["open"]["S-2"]) is True
      and dstore["open"]["S-1"]["figure"]["amount"] == 15.0,
      [dstore["open"][s]["figure"] for s in sorted(dstore["open"])])
check("A16 the store panels PARTITION the tab's rows — a line is in exactly one of them, so the two "
      "families can never present the same line twice",
      sum(p["rows_total_count"] for p in dstore["open"].values()) == open_p["rows_total_count"]
      and (set().union(*[{(r["row_key"] or {})["id"] for r in p["rows"]}
                         for p in dstore["open"].values()])
           == {(r["row_key"] or {})["id"] for r in open_p["rows"]}),
      [(s, dstore["open"][s]["rows_total_count"]) for s in sorted(dstore["open"])])
check("A17 a row with no store at all is KEPT under a stated bucket rather than dropped — dropping "
      "it would silently shrink the figure",
      (lambda d: "Unknown" in d["open"] and d["open"]["Unknown"]["figure"]["amount"] == 4.0)(
          cpd.discrepancy_store_drilldowns(DROWS + [_drow(9, "open", 4.0, 0.0, "", store="")])),
      sorted(cpd.discrepancy_store_drilldowns(
          DROWS + [_drow(9, "open", 4.0, 0.0, "", store="")])["open"]))
check("A18 the PHANTOM tab is deliberately NOT claimed — it is a different endpoint over a different "
      "row shape, and a panel for it would open the wrong rows",
      "phantom" not in cpd.DISCREPANCY_STATUSES and "gap_phantom" not in dpan,
      cpd.DISCREPANCY_STATUSES)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B. CARRIER EARNED vs EMPLOYEE PAID — four tiles, folded by the REAL `rollup_by_rep`.
#
# Four reps, one per state the report can be in: paid and earning, measured at zero, no device key to
# look the statement up by, and an employee paid with no sold activation at all.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. Carrier Earned vs Employee Paid opens to its reps ==")

COLUMNS = cvp.earnings_columns(None, ("spiff_m1",))
RECON = [
    {"rep_username": "ANA", "store": "S-1", "status": "ok", "imei": "D1"},
    {"rep_username": "ANA", "store": "S-1", "status": "open", "imei": "D2"},
    {"rep_username": "BEN", "store": "S-2", "status": "ok", "imei": "D9"},
    {"rep_username": "CAM", "store": "S-3", "status": "ok", "imei": ""},
]
MA_INDEX = {"D1": {"spiff_m1": -30.0}, "D2": {"spiff_m1": -10.0}}
PAY = [
    {"epay_salesperson": "ANA", "total_payout": 25.0, "store": "S-1"},
    {"epay_salesperson": "DEE", "total_payout": 5.0, "store": "S-4"},
]
CVP_OUT = cvp.rollup_by_rep(RECON, MA_INDEX, PAY, columns=COLUMNS, payout_sign=-1,
                            statement_loaded=True, market_for=lambda s: "M-%s" % s)
cpan = cpd.carrier_vs_pay_drilldowns(CVP_OUT)

check("B1 every tile figure the report produced has a panel, and no panel exists for a figure it "
      "did not", sorted(cpan) == sorted(
          ["activations_in_statement", "activations_sold", "carrier_earned_reported",
           "difference_over_measured_reps", "employee_paid_total", "open_no_rule"]), sorted(cpan))
earn = cpan["carrier_earned_reported"]
check("B2 the earned figure counts ONLY the reps whose earnings were reported, and the rest stay in "
      "the panel as the context that makes it readable",
      earn["figure"]["amount"] == 40.0 and _tie(earn) is True
      and earn["tie_out"]["counted_rows"] == 1 and earn["tie_out"]["shown_not_counted"] == 3,
      earn["tie_out"])
check("B3 a MEASURED ZERO is shown WITH its $0.00 and left out of the total — the statement was "
      "loaded and pays nothing against this rep, which is a measurement",
      (lambda r: r["amount"] == 0.0 and r["measured"] is True and r["in_figure"] is False
       and r["reason"] is None)(_row(earn, "BEN")), _row(earn, "BEN"))
check("B4 a rep with no device key to look the statement up by carries NO figure and the reason is "
      "the PRODUCER'S OWN, rendered as a sentence from the one `UNMEASURED_REASONS` home",
      (lambda r: r["amount"] is None and r["reason"] == cvp.EARNED_REASON_NO_DEVICE_KEY
       and r["reason_label"] == rd.UNMEASURED_REASONS[cvp.EARNED_REASON_NO_DEVICE_KEY])(
          _row(earn, "CAM")), _row(earn, "CAM"))
check("B5 the feed is named per ROW off that row's own verdict — looked up in the statement names "
      "both statement tables",
      _tables(_row(earn, "ANA")) == sorted(cpd.STATEMENT_FEEDS), _tables(_row(earn, "ANA")))
check("B6 and a rep the carrier side was NEVER looked up for names NOTHING, which the panel reports "
      "as an origin it could not trace rather than inventing one",
      _tables(_row(earn, "DEE")) == [] and earn["untraceable_origins"] >= 1,
      (_tables(_row(earn, "DEE")), earn["untraceable_origins"]))
check("B7 a pre-attributed tenant's earned figure names the CALCULATED rep commissions — §CORE-17.3j: "
      "the snapshot the figure was read out of, not the feed the calculation read to write it",
      cpd.earned_tables({"carrier_earned_reason": cvp.EARNED_REASON_PREATTRIBUTED})
      == (cpd.REP_COMMISSIONS,),
      cpd.earned_tables({"carrier_earned_reason": cvp.EARNED_REASON_PREATTRIBUTED}))
paid = cpan["employee_paid_total"]
check("B8 the paid figure is the payroll side alone, over the reps the calculation has run for",
      paid["figure"]["amount"] == 30.0 and _tie(paid) is True
      and {s["table"] for s in paid["sources"]} == {cpd.REP_COMMISSIONS}, paid["tie_out"])
diff = cpan["difference_over_measured_reps"]
check("B9 A DIFFERENCE IS DRILLABLE HERE BECAUSE IT IS FOLDED PER ROW — Σ(the per-rep differences) "
      "against the report's own total is a comparison that can disagree (§CORE-17.3h, as extended)",
      diff["figure"]["amount"] == 15.0 and _tie(diff) is True
      and diff["tie_out"]["counted_rows"] == 1, diff["tie_out"])
check("B10 a rep missing either side is shown in the difference panel and not counted",
      all(_row(diff, n)["in_figure"] is False for n in ("BEN", "CAM", "DEE")),
      [(n, _row(diff, n)["in_figure"]) for n in ("BEN", "CAM", "DEE")])
check("B11 a difference row names BOTH ledgers it was read out of",
      _tables(_row(diff, "ANA")) == sorted(tuple(cpd.STATEMENT_FEEDS) + (cpd.REP_COMMISSIONS,)),
      _tables(_row(diff, "ANA")))
sold = cpan["activations_sold"]
check("B12 the activation COUNTS are counts, not money — the unit vocabulary is the one home's",
      sold["figure"]["unit"] == rd.UNIT_COUNT and _tie(sold) is True
      and sold["figure"]["amount"] == 4.0, sold["figure"])
check("B13 the sold count names the registry's OWN sales union, which is what the loader walks",
      _tables(_row(sold, "ANA")) == sorted(lineage.SALES_DISPLAY_SOURCES),
      _tables(_row(sold, "ANA")))
check("B14 the unpaid-with-no-rule count names both sides, because that verdict is read from the "
      "sold feed AND from the statement that does not carry the activation",
      _tables(_row(cpan["open_no_rule"], "ANA"))
      == sorted(set(lineage.SALES_DISPLAY_SOURCES) | set(cpd.STATEMENT_FEEDS)),
      _tables(_row(cpan["open_no_rule"], "ANA")))
check("B15 a rep row addresses itself by the key both ledgers agree on",
      all((r.get("row_key") or {}).get("rep_key") for r in earn["rows"]),
      [r.get("row_key") for r in earn["rows"]])
check("B16 the REP-COUNT figures get no panel of their own — they are cardinalities of the states "
      "every row already carries, and the panel shows those states",
      not any(k.startswith("carrier_earned_reps") or k == "difference_measured_reps"
              for k in cpan), sorted(cpan))
check("B17 a report with no reps at all drills to an empty mapping, never to a panel asserting a tie",
      cpd.carrier_vs_pay_drilldowns(
          cvp.rollup_by_rep([], {}, [], columns=COLUMNS, payout_sign=-1, statement_loaded=False))
      == {} or all(p["rows_total_count"] == 0 for p in cpd.carrier_vs_pay_drilldowns(
          cvp.rollup_by_rep([], {}, [], columns=COLUMNS, payout_sign=-1,
                            statement_loaded=False)).values()),
      sorted(cpd.carrier_vs_pay_drilldowns(
          cvp.rollup_by_rep([], {}, [], columns=COLUMNS, payout_sign=-1,
                            statement_loaded=False))))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C. COMMISSION RECEIVED OVER M1–M12 — the group tiles and the stream totals, folded by the REAL
# `build_breakout` over the REAL default leg classifier.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. the commission breakout opens to its (stream × month) cells ==")

PERIODS = ["August 2026", "September 2026"]
LABEL_ROWS = [
    {"source": "payment_detail", "period": "August 2026", "store_num": "S-1",
     "label": "New Activation Bounty - Month 1", "category": "Commission", "amount": 100.0, "n": 2},
    {"source": "payment_detail", "period": "September 2026", "store_num": "S-1",
     "label": "New Activation Bounty - Month 3", "category": "Commission", "amount": 50.0, "n": 1},
    # A label that states NO month — it lands in `unsplit`, never a guessed leg.
    {"source": "payment_detail", "period": "September 2026", "store_num": "S-1",
     "label": "Auto Top-Up reimbursement", "category": "Commission", "amount": 7.0, "n": 1},
]
MI_ROWS = [{"period": "September 2026", "salesforce_id": "sf-1", "leg_month": 1,
            "mi": 10.0, "atu": 4.0, "n": 1}]
CLS = legs.default_classifier()
BO = cr.build_breakout(PERIODS, CLS, label_rows=LABEL_ROWS, mi_rows=MI_ROWS,
                       period_key={p: p for p in PERIODS})
bpan = cpd.breakout_drilldowns(BO)

check("C1 every group tile's four figures open, and every stream's own total opens",
      {"group_commission_total", "group_commission_m1", "group_commission_m2_12",
       "group_commission_unsplit", "group_residual_total",
       "stream_comm_epay_total", "stream_mi_total", "stream_atu_total"} <= set(bpan),
      sorted(bpan))
gtot = bpan["group_commission_total"]
check("C2 the group total ties against the cells it was folded from — the identity `build_breakout` "
      "asserts into the payload, now checked against rows a reader can see",
      gtot["figure"]["amount"] == 157.0 and _tie(gtot) is True, gtot["tie_out"])
check("C3 the LEG splits tie too, each over the same cells with a different field",
      (bpan["group_commission_m1"]["figure"]["amount"] == 100.0
       and _tie(bpan["group_commission_m1"]) is True
       and bpan["group_commission_m2_12"]["figure"]["amount"] == 50.0
       and _tie(bpan["group_commission_m2_12"]) is True
       and bpan["group_commission_unsplit"]["figure"]["amount"] == 7.0
       and _tie(bpan["group_commission_unsplit"]) is True),
      [(k, bpan[k]["figure"]["amount"], _tie(bpan[k])) for k in
       ("group_commission_m1", "group_commission_m2_12", "group_commission_unsplit")])
rtot = bpan["group_residual_total"]
_absent = _row(rtot, "MI residual · August 2026")
check("C4 A MONTH A FEED NEVER DELIVERED IS SHOWN AND NOT COUNTED — an absent file is not a month "
      "that paid $0.00, and it says so",
      _absent and _absent["in_figure"] is False and _absent["reason"] == "not_recorded"
      and _absent["reason_label"], _absent)
check("C5 and saying so costs nothing: the residual total still ties, because an uncounted cell "
      "contributes zero either way",
      rtot["figure"]["amount"] == 14.0 and _tie(rtot) is True
      and rtot["tie_out"]["shown_not_counted"] == 2, rtot["tie_out"])
check("C6 each cell names the feed its STREAM was read out of, off the stored stream key",
      _tables(_row(gtot, "Commission received (ePay Payment Detail) · August 2026"))
      == [lineage.PAYMENT_DETAIL]
      and _tables(_row(rtot, "ATU residual · September 2026")) == [lineage.MI_RESIDUAL],
      [_tables(r) for r in rtot["rows"]])
check("C7 a stream that is one COLUMN of a shared feed says which column, on its own total panel",
      (bpan["stream_mi_total"]["rows"][0]["origins"][0]["column"] == "actual_mi_payout"
       and bpan["stream_atu_total"]["rows"][0]["origins"][0]["column"] == "actual_atu_payout"),
      [bpan[k]["rows"][0]["origins"][0]["column"] for k in ("stream_mi_total", "stream_atu_total")])
check("C8 a GROUP panel names no column, because the streams inside it were read out of different "
      "ones and naming one would be false",
      all((r["origins"][0] or {}).get("column") is None for r in rtot["rows"]),
      [r["origins"][0].get("column") for r in rtot["rows"]])
check("C9 a cell addresses itself by stream AND month — a stream key alone addressed every month "
      "there has ever been",
      all(set((r.get("row_key") or {})) == {"stream", "period"} for r in gtot["rows"]),
      [r.get("row_key") for r in gtot["rows"]])
check("C10 a stream total opens to its own months and ties",
      (bpan["stream_comm_epay_total"]["figure"]["amount"] == 157.0
       and _tie(bpan["stream_comm_epay_total"]) is True
       and bpan["stream_comm_epay_total"]["rows_total_count"] == 2),
      bpan["stream_comm_epay_total"]["tie_out"])
check("C11 a CROSS-CHECK stream that sits in no total still gets its own panel — a figure on the "
      "screen is a figure that opens",
      (lambda b: "stream_ma_residual_orders_total" in b
       and _tie(b["stream_ma_residual_orders_total"]) is True)(
          cpd.breakout_drilldowns(cr.build_breakout(
              PERIODS, CLS, tx_rows=[{"period": "September 2026", "airtime_all": 0.0,
                                      "residual_orders": 12.0, "n": 0, "n_residual": 3}],
              period_key={p: p for p in PERIODS}))),
      sorted(cpd.breakout_drilldowns(cr.build_breakout(
          PERIODS, CLS, tx_rows=[{"period": "September 2026", "airtime_all": 0.0,
                                  "residual_orders": 12.0, "n": 0, "n_residual": 3}],
          period_key={p: p for p in PERIODS}))))
check("C12 a reference stream is NOT in any group tile's rows, exactly as it is in no total",
      all(r["row_key"]["stream"] != "ma_residual_orders" for r in gtot["rows"]),
      [r["row_key"]["stream"] for r in gtot["rows"]])
check("C13 an empty window drills to an empty mapping rather than a panel asserting a tie",
      cpd.breakout_drilldowns(cr.build_breakout(PERIODS, CLS,
                                                period_key={p: p for p in PERIODS})) == {},
      cpd.breakout_drilldowns(cr.build_breakout(PERIODS, CLS,
                                                period_key={p: p for p in PERIODS})))
check("C14 the per-LEG CELLS in the table get no panel of their own — they are the stream's own leg "
      "total, which the group tiles and the stream total already open; deliberately not claimed",
      not any(re.match(r"^(stream|group)_.*_leg", k) for k in bpan), sorted(bpan))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §Y. LOCKED SO IT CANNOT UN-WIRE.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Y. build locks ==")

DECL_CODE = _code_only(DECL)
DECL_SRC = open(DECL, encoding="utf-8").read()

check("Y1 THE REASON VOCABULARY HAS NOT FORKED — the three keys this declaration emits ARE "
      "`carrier_vs_pay`'s own reason constants, and the one home renders a sentence for each",
      all(k in rd.UNMEASURED_REASONS and rd.UNMEASURED_REASONS[k]
          for k in (cvp.EARNED_REASON_NO_FEED, cvp.EARNED_REASON_NO_DEVICE_KEY,
                    cvp.EARNED_REASON_ABSENT)),
      [k for k in (cvp.EARNED_REASON_NO_FEED, cvp.EARNED_REASON_NO_DEVICE_KEY,
                   cvp.EARNED_REASON_ABSENT) if k not in rd.UNMEASURED_REASONS])
check("Y1b every reason this declaration can emit has a sentence — a bare key on a manager's screen "
      "is the database name the owner's standing instruction forbids",
      all(k in rd.UNMEASURED_REASONS
          for k in ("amount_not_recorded", "not_recorded", "not_computed")),
      [k for k in ("amount_not_recorded", "not_recorded", "not_computed")
       if k not in rd.UNMEASURED_REASONS])
check("Y2 THE PROVENANCE MAP IS COMPLETE against the producer's OWN stream registry, in both "
      "directions — a new money stream cannot ship without saying where it was read from",
      set(cpd.FEEDS_BY_STREAM) == set(cr.STREAMS) == set(cpd._stream_keys()),
      (sorted(set(cr.STREAMS) - set(cpd.FEEDS_BY_STREAM)),
       sorted(set(cpd.FEEDS_BY_STREAM) - set(cr.STREAMS))))
_bad_src = [k for k, (t, _c) in cpd.FEEDS_BY_STREAM.items()
            if t and t not in str(cr.STREAMS[k].get("source") or "")]
check("Y2b and the TABLE it names appears in that stream's own `source` sentence — the prose the page "
      "prints and this machine fact cannot diverge",
      _bad_src == [], _bad_src)
_bad_col = [k for k, (_t, c) in cpd.FEEDS_BY_STREAM.items()
            if c and c not in str(cr.STREAMS[k].get("source") or "")]
check("Y2c and so does the COLUMN, where a stream is one column of a shared feed",
      _bad_col == [], _bad_col)
check("Y3 the discrepancy writer map is keyed on the value the writing engine really stamps",
      set(lineage.DISCREPANCY_WRITER_FEEDS) == {ma_recon.SOURCE_MA},
      sorted(lineage.DISCREPANCY_WRITER_FEEDS))
check("Y3b an unknown writer resolves to NOTHING, so the drill names the snapshot alone rather than "
      "guessing a feed",
      lineage.discrepancy_writer_feeds("") == ()
      and lineage.discrepancy_writer_feeds("a_third_engine") == ()
      and lineage.discrepancy_writer_feeds(None) == (),
      lineage.discrepancy_writer_feeds("a_third_engine"))
check("Y4 THE DECLARATION BUILDS NO PANEL OF ITS OWN — every panel goes through the one home, and "
      "none of the panel shape's keys is spelled here",
      not any(("\"%s\"" % k) in DECL_CODE or ("'%s'" % k) in DECL_CODE
              for k in ("rows_total_count", "untraceable_origins", "tie_out", "rows_shown")),
      [k for k in ("rows_total_count", "untraceable_origins", "tie_out", "rows_shown")
       if ("\"%s\"" % k) in DECL_CODE or ("'%s'" % k) in DECL_CODE])
check("Y4b and it computes no rounding of its own — the ONE rounding is dereferenced, and no "
      "float coercion of a money value is written here",
      "report_drilldown.money" in DECL_SRC and "float(" not in DECL_CODE
      and "def _f" not in DECL_SRC,
      [t for t in ("float(", "def _f") if t in DECL_CODE])
_nums = sorted(set(_numbers(DECL)))
check("Y5 NO DOLLAR LITERAL in the declaration — it carries amounts handed to it and holds no "
      "threshold, rate or figure of its own",
      set(_nums) <= {"0", "1", "2"}, _nums)
_FORBIDDEN = ("vidapay", "t-cetra", "total wireless", "boost", "acima", "paygo",
              "dish", "epay", "luxelink", "cellfonz")
_hits = [w for w in _FORBIDDEN if w in DECL_CODE.lower()]
check("Y6 RULE TWO — no carrier, tenant, product or store name anywhere in the declaration's CODE",
      _hits == [], _hits)
check("Y7 the declaration is PURE — no DB client, no framework import, no clock",
      not any(s in DECL_SRC for s in ("from app.core.database", "import fastapi",
                                      "from fastapi", "datetime.now", "import requests")))
check("Y8 ONE MODULE, THREE CATALOGUES — the three reports are declared together and no sibling "
      "declaration file was created beside it",
      all(f in DECL_SRC for f in ("def discrepancy_catalogue", "def carrier_vs_pay_catalogue",
                                  "def breakout_drilldowns"))
      and not os.path.exists(os.path.join(
          ROOT, "backend/app/modules/commcalc/discrepancy_drilldown.py"))
      and not os.path.exists(os.path.join(
          ROOT, "backend/app/modules/commcalc/commission_legs_drilldown.py")))

ROUTER_CODE = open(ROUTER, encoding="utf-8").read()
check("Y9 THE ENDPOINTS BUILD NO PANEL — each one attaches the declaration's output and folds nothing",
      ROUTER_CODE.count("_cpdd.carrier_vs_pay_drilldowns") == 1
      and ROUTER_CODE.count("_cpdd.discrepancy_drilldowns") == 1
      and ROUTER_CODE.count("_cpdd.discrepancy_store_drilldowns") == 1
      and ROUTER_CODE.count("_cpdd.breakout_drilldowns") == 1
      and "report_drilldown.panel(" not in ROUTER_CODE,
      [k for k in ("carrier_vs_pay_drilldowns", "discrepancy_drilldowns",
                   "discrepancy_store_drilldowns", "breakout_drilldowns")
       if ROUTER_CODE.count("_cpdd." + k) != 1])
check("Y9b the discrepancy endpoint serves its per-tab total, so the screen's headline reads OFF the "
      "panel instead of re-reducing the rows in TypeScript (§CORE-17.3d)",
      "_cpdd.status_total" in ROUTER_CODE and '"status_totals"' in ROUTER_CODE)

SRC = {r: _read(p) for r, p in PAGES.items()}
for route, src in SRC.items():
    check("Y10 %s renders the ONE shared panel — writing the registry without wiring the caller is "
          "not a fix at all (§19.18)" % route,
          "DrillDownPanel" in src and "@/components/DrillDownPanel" in src)
    check("Y10b %s hand-rolls no panel of its own" % route,
          "untraceable_origins" not in src and "rows_total_count" not in src)
for route, src in SRC.items():
    check("Y11 §F5 — `drill-narrow` is NOT wired into %s: every filter that moves a figure there is "
          "a query parameter, so the server's own fold already covers what is on screen, and "
          "narrowing it would make the panel disagree with the tile" % route,
          "@/lib/drill-narrow" not in src and "narrowPanel(" not in src
          and "narrowPanels(" not in src)
check("Y12 the discrepancy screen's headline PREFERS the served figure and keeps its own reduce only "
      "as the fallback for a payload that carries none — one fold, with a named degradation",
      "clientTabTotal" in SRC["/commcalc/discrepancy"]
      and "figure?.amount" in SRC["/commcalc/discrepancy"]
      and "served ?? clientTabTotal" in SRC["/commcalc/discrepancy"])
check("Y13 every table these panels can name has a PLAIN NAME, so a database identifier can never "
      "reach a manager's screen (§19.38)",
      (lambda labels: [t for t in sorted(
          set(lineage.SALES_DISPLAY_SOURCES) | set(cpd.STATEMENT_FEEDS)
          | {cpd.REP_COMMISSIONS, lineage.DISCREPANCY_SNAPSHOT}
          | {t for t, _c in cpd.FEEDS_BY_STREAM.values() if t})
          if ("  %s:" % t) not in labels and ("\n  %s:" % t) not in labels] == [])(
          _read("frontend/src/lib/sourceLabels.ts")),
      [t for t in sorted(set(lineage.SALES_DISPLAY_SOURCES) | set(cpd.STATEMENT_FEEDS)
                         | {cpd.REP_COMMISSIONS, lineage.DISCREPANCY_SNAPSHOT}
                         | {t for t, _c in cpd.FEEDS_BY_STREAM.values() if t})
       if ("  %s:" % t) not in _read("frontend/src/lib/sourceLabels.ts")])
check("Y14 the carrier-vs-pay screen SAYS the strip covers every rep while the table is narrowed — "
      "the live defect is stated on the screen, not hidden behind a drill-down",
      "meta.filtered" in SRC["/commcalc/carrier-vs-pay"]
      and "reps_total" in SRC["/commcalc/carrier-vs-pay"])
check("Y15 the declaration's own header REPORTS the three live defects rather than leaving them for "
      "a reader to rediscover",
      DECL_SRC.count("LIVE DEFECTS") >= 1 and "TypeError" in DECL_SRC
      and "leaves its TOTALS unfiltered" in DECL_SRC)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §Z. NEGATIVE CONTROLS — a tie-out that cannot fail is not a proof.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Z. negative controls — each one BREAKS a tie-out, or reproduces a reported defect ==")


def _bump(rows, idx, field, delta):
    out = [dict(r) for r in rows]
    out[idx][field] = (out[idx][field] or 0) + delta
    return out


# Z1 — A ONE-CENT SUBTOTAL. The report's own total moved by a cent and the rows did not.
_z1 = cpd.discrepancy_drilldowns(DROWS)["gap_open"]
_z1 = rd.tile_panel("Total receivable from the carrier", 25.01, DROWS,
                    value="gap", row_label=cpd._discrepancy_label, keys=("id",),
                    tables=cpd.discrepancy_tables, schema="commcalc",
                    in_figure=cpd._gap_recorded, reason=cpd._gap_reason)
check("Z1 a ONE-CENT total breaks the discrepancy tie-out, in words",
      _tie(_z1) is False and "disagree" in _z1["tie_out"]["words"], _z1["tie_out"])

# Z2 — A DROPPED ROW. One commission line vanishes from the evidence.
_z2 = rd.tile_panel("Total receivable from the carrier", cpd.status_total(DROWS, "open"),
                    [r for r in DROWS if r["id"] != 3],
                    value="gap", row_label=cpd._discrepancy_label, keys=("id",),
                    tables=cpd.discrepancy_tables, schema="commcalc",
                    in_figure=cpd._gap_recorded, reason=cpd._gap_reason)
check("Z2 a DROPPED line breaks it too — the rows add up to less than the figure",
      _tie(_z2) is False and _z2["tie_out"]["difference"] == -10.0, _z2["tie_out"])

# Z3 — A FIGURE FOLDED IN AT $0.00 THAT NOBODY RECORDED. The careless declaration counts the
# unsized claim as nothing; the honest one does not. Both tie — and that is exactly why the
# DIFFERENCE between them has to be visible on the row, not in the total.
_z3 = rd.tile_panel("Total receivable from the carrier", cpd.status_total(DROWS, "open"),
                    DROWS, value=lambda r: rd.money(r.get("gap")),
                    row_label=cpd._discrepancy_label, keys=("id",),
                    tables=cpd.discrepancy_tables, schema="commcalc")
check("Z3 the CARELESS declaration (gap coerced to $0.00) counts a claim nobody could price, and the "
      "honest one shows it instead — the figure is identical, the row is not, and the row is the "
      "whole point",
      (_tie(_z3) is True and _z3["tie_out"]["counted_rows"] == 4
       and _row(_z3, "SOLD_NOT_IN_MI · month 1 · IMEI-2")["amount"] == 0.0
       and _z3["figure"]["amount"] == open_p["figure"]["amount"]
       and open_p["tie_out"]["counted_rows"] == 2),
      (_z3["tie_out"], open_p["tie_out"]))

# Z4 — A TAMPERED BREAKDOWN ENTRY. One (stream × month) cell is edited after the fold.
_z4rows = [dict(r) for r in cpd.cells(BO, groups=("commission",), in_total=True)]
_z4rows[0]["total"] = (_z4rows[0]["total"] or 0) + 1.0
_z4 = rd.tile_panel("Commission received — total", BO["groups"][0]["total"], _z4rows,
                    value="total", row_label=cpd._cell_label, keys=("stream", "period"),
                    tables=cpd.stream_tables, schema="commcalc",
                    in_figure=cpd._cell_recorded, reason=cpd._cell_reason)
check("Z4 a TAMPERED breakdown cell breaks the breakout tie-out",
      _tie(_z4) is False and _z4["tie_out"]["difference"] == 1.0, _z4["tie_out"])

# Z5 — A COUNTED ROW WITH NO NUMBER. `ties` must be None, never True and never False: a sum missing a
# row was never a comparison.
_z5rows = [dict(r) for r in DROWS if r["status"] == "open"]
_z5 = rd.tile_panel("Total receivable from the carrier", cpd.status_total(DROWS, "open"), _z5rows,
                    value="gap", row_label=cpd._discrepancy_label, keys=("id",),
                    tables=cpd.discrepancy_tables, schema="commcalc",
                    in_figure=lambda _r: True, reason=cpd._gap_reason)
check("Z5 a declaration that COUNTS the unpriced claim cannot claim a tie at all — `ties` is None, "
      "never False, because a sum missing a row was never a comparison",
      _tie(_z5) is None and "cannot be added up" in _z5["tie_out"]["words"], _z5["tie_out"])

# Z6 — THE REPORTED CRASH, REPRODUCED. `claim_gap` really does return None for a claim it cannot
# size, `discrepancy_engine` really does persist that with status 'open', and the endpoint's store
# rollup really does compare it to a threshold.
_unsized = ccv.claim_gap(None, 0.0)
_raised = False
try:
    _unsized > 0.50                                                      # noqa: B015
except TypeError:
    _raised = True
check("Z6 LIVE DEFECT 1, REPRODUCED — the real `claim_gap` returns None for an unsized claim, and "
      "`GET /commcalc/discrepancy/{period}`'s rollup compares exactly that to 0.50, which RAISES. "
      "Reported, not fixed: the repair has to decide whether such a row counts as flagged",
      _unsized is None and _raised, (_unsized, _raised))
check("Z6b and the drill-down survives it — the panel reads that None as 'never recorded' instead of "
      "comparing it, so the evidence is honest even on the payload that trips the endpoint",
      _row(open_p, "SOLD_NOT_IN_MI · month 1 · IMEI-2")["measured"] is False,
      _row(open_p, "SOLD_NOT_IN_MI · month 1 · IMEI-2"))

# Z7 — THE REPORTED FILTER DEFECT, PINNED. `rollup_by_rep` folds its totals over every rep, so a
# market selection cannot change them. If someone fixes that, this check goes red and the panels have
# to be re-pointed at the kept rows in the same breath.
_filtered_rows = [r for r in CVP_OUT["rows"] if r["market"] == "M-S-1"]
_refold = cvp.rollup_by_rep(
    [r for r in RECON if r["store"] == "S-1"], MA_INDEX,
    [p for p in PAY if p["store"] == "S-1"], columns=COLUMNS, payout_sign=-1,
    statement_loaded=True, market_for=lambda s: "M-%s" % s)
check("Z7 LIVE DEFECT 3, PINNED — the report's totals are folded over EVERY rep, so they differ from "
      "a fold over one market's reps. The panels are deliberately folded over the same (unfiltered) "
      "population as the figure they explain; if the endpoint starts narrowing its totals this check "
      "goes red and the panels must follow in the same PR",
      (CVP_OUT["totals"]["employee_paid_total"] == 30.0
       and _refold["totals"]["employee_paid_total"] == 25.0
       and len(_filtered_rows) < len(CVP_OUT["rows"])),
      (CVP_OUT["totals"]["employee_paid_total"], _refold["totals"]["employee_paid_total"]))

# Z8 — THE FIGURE DID NOT MOVE. The screen's old reduce coerces a null gap to zero; the server's fold
# skips it. Over every tab, the two agree to the cent — which is what makes defect 2 a presentation
# fix rather than a number changing under the owner.
_js_like = {s: round(sum((r["gap"] or 0) for r in DROWS if r["status"] == s), 2)
            for s in cpd.DISCREPANCY_STATUSES}
_served = {s: cpd.status_total(DROWS, s) for s in cpd.DISCREPANCY_STATUSES}
check("Z8 NO FIGURE MOVED — the screen's own reduce (a null gap coerced to 0, as JavaScript does) and "
      "the served fold (which skips it) agree on every tab, to the cent",
      _js_like == _served, (_js_like, _served))
check("Z8b and a tampered served total would NOT agree — so Z8 is a comparison that can fail",
      {**_served, "open": 25.01} != _js_like)

# Z9 — AN EMPTY REPORT. A drill-down over nothing must be an empty mapping, not a panel announcing
# that nothing ties.
check("Z9 an empty discrepancy run drills to an empty mapping rather than four panels asserting "
      "$0.00 behind nothing",
      cpd.discrepancy_drilldowns([]) == {} and cpd.discrepancy_store_drilldowns([]) == {},
      (cpd.discrepancy_drilldowns([]), cpd.discrepancy_store_drilldowns([])))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §I. REGISTRATION — the index rules are a build gate, not advice (CLAUDE.md).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. registration ==")

_IDX = _read("docs/SYSTEM_DATA_FLOW_INDEX.md")
for _f in ("carrier_pay_drilldown.py", "harness_batch11_drilldown.py"):
    check("I1(%s) registered in the index by its path" % _f, _f in _IDX)
check("I2 and the section this batch adds is there, so the next report does not re-derive WHERE a "
      "difference has to be subtracted for it to be drillable", "CORE-17.3k" in _IDX)
_MG = _read("backend/app/modules/core/module_graph.py")
check("I3 the declaration is a caller of the one home in the module graph, and the harness is one of "
      "its locks",
      "commcalc/carrier_pay_drilldown.py" in _MG and "harness_batch11_drilldown.py" in _MG)
_WF = _read(".github/workflows/carrier-vocab-guard.yml")
check("I4 the harness is locked into CI, with its own paths-filter block",
      "harness_batch11_drilldown.py" in _WF and _WF.count("harness_batch11_drilldown.py") >= 2)
_LEDGER = _read("backend/harness_drilldown_pending.txt")
for _r in PAGES:
    _ln = next((l for l in _LEDGER.splitlines() if l.split("\t")[0] == _r), "")
    check("I5(%s) the ledger reads `shared` and says what it refuses to claim — an excuse nobody "
          "wrote down is indistinguishable from an oversight" % _r,
          _ln.split("\t")[1:2] == ["shared"] and "REFUSES to claim" in _ln, _ln[:160])
check("I6 the ratchet was re-pinned DOWNWARD in the same PR, never upward",
      "OWES_MAX = 71" in _read("backend/harness_drilldown_coverage.py"))


print("\n" + "=" * 96)
print("PASS %d   FAIL %d" % (len(P), len(F)))
if F:
    print("\nFAILED:")
    for f in F:
        print("  - " + f)
sys.exit(1 if F else 0)
