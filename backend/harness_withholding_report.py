"""Proof harness — COMMISSION WITHHOLDING report (owner ask 2026-10-06).

Stdlib-only, DB-free. Proves backend/app/modules/commcalc/withholding_report.py:
  1. withheld_findings folds every leg onto ONE finding per activation (a manager appeals an
     activation, not a ledger line), with first/last date, types and periods;
  2. the activation key prefers the DEVICE SERIAL, because the clawback leg carries no number
     (index §23s.8) — and a leg with neither identifier is NAMED, not dropped;
  3. recovery counts only commission that landed STRICTLY AFTER the last clawback leg, and the
     IMEI FENCE of the reused join refuses another subscriber's money on the same handset;
  4. an activation that could not be looked up is NEVER reported as unrecovered — the whole point;
  5. the ePay leg is CREDITS only and is never netted against the clawback;
  6. summarize keeps the unknowable out of the headline and counts `no_appeal` as a real bucket;
  7. the REGRESSION that reproduces the reported defect, end to end on the live shape.

Run:  python backend/harness_withholding_report.py   → all checks must print OK.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app.modules.commcalc import withholding_report as wr             # noqa: E402
from app.modules.marketing.event_sales import (                       # noqa: E402
    commission_event, index_commission_events)

FAILURES = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILURES.append(name)


DEC = {"Withholding": "Commission", "Recovery": "Commission", "DeviceCharge": "Equipment"}


def pd_row(**kw):
    """A payment-detail shaped row with sane defaults."""
    r = {"payment_type": "Withholding", "amount": -10.0, "imei": "", "mdn": "",
         "payment_date": "2026-04-10", "business_address": "1 Main St", "rep_username": "",
         "period": "April 2026"}
    r.update(kw)
    return r


# ── 1. folding: many legs, one finding ──────────────────────────────────────────────────────────
ROWS = [
    pd_row(imei="IMEI1", amount=-11.0, payment_date="2026-04-24", period="April 2026"),
    pd_row(imei="IMEI1", amount=-4.0, payment_date="2026-06-02", period="June 2026",
           payment_type="Recovery", rep_username="rep.a"),
    pd_row(imei="IMEI2", amount=-59.0, payment_date="2026-09-15", period="September 2026"),
    pd_row(imei="IMEI1", amount=240.0, payment_date="2026-05-01"),      # a CREDIT — not a clawback
    pd_row(imei="IMEI3", amount=-900.0, payment_type="DeviceCharge"),   # outside pay
    pd_row(imei="IMEI4", amount=-5.0, payment_type="Mystery"),          # undeclared
]
f = wr.withheld_findings(ROWS, DEC)
by = {x["key"]: x for x in f}
check("one finding per activation, not per ledger leg", len(f) == 2 and set(by) == {"IMEI1", "IMEI2"})
check("the legs are summed onto the finding", by["IMEI1"]["withheld"] == 15.0
      and by["IMEI1"]["legs"] == 2)
check("first and last clawback dates come from the legs, in order",
      by["IMEI1"]["first_withheld_on"] == "2026-04-24"
      and by["IMEI1"]["last_withheld_on"] == "2026-06-02")
check("every payment type involved is listed, sorted",
      by["IMEI1"]["types"] == ["Recovery", "Withholding"])
check("every period involved is listed", by["IMEI1"]["periods"] == ["April 2026", "June 2026"])
check("a later leg FILLS a blank rep without overwriting a known one",
      by["IMEI1"]["rep"] == "rep.a")
check("a CREDIT on the same activation never becomes a clawback", by["IMEI1"]["withheld"] == 15.0)
check("a debit outside the pay categories is not a finding", "IMEI3" not in by)
check("an UNDECLARED debit is not silently counted as a clawback", "IMEI4" not in by)
check("findings are ordered worst-money first", [x["key"] for x in f] == ["IMEI2", "IMEI1"])
check("the store resolver the caller passes is the one used",
      wr.withheld_findings([pd_row(imei="I", business_address="1 main st")], DEC,
                           store_of=lambda s: s.upper())[0]["store"] == "1 MAIN ST")

# ── 2. the activation key ───────────────────────────────────────────────────────────────────────
check("the device serial wins over the number (the clawback leg has no number)",
      wr.activation_key({"imei": "I9", "mdn": "5551234"}) == ("I9", wr.KEY_IMEI))
check("the number is used when there is no serial",
      wr.activation_key({"imei": "", "mdn": "5551234"}) == ("5551234", wr.KEY_MDN))
k, b = wr.activation_key(pd_row(imei="", mdn=""))
check("a leg with NEITHER identifier is named by type+date, never dropped",
      b == wr.KEY_NONE and k == "Withholding|2026-04-10")
check("a trailing '.0' on a serial is normalised away (the feed's float-ish export)",
      wr.activation_key({"imei": "352700326611885.0"})[0] == "352700326611885")
check("a keyless leg still produces a finding", len(wr.withheld_findings(
      [pd_row(imei="", mdn="", amount=-3.0)], DEC)) == 1)

# ── 3. recovery — STRICTLY after, and the fence ─────────────────────────────────────────────────
F1 = wr.withheld_findings([pd_row(imei="IMEI1", mdn="5550001", amount=-100.0,
                                  payment_date="2026-04-30")], DEC)
EV = [
    commission_event("epay", "before", imei="IMEI1", mdn="5550001", amount=70.0,
                     category="Commission", paid_on="2026-04-02"),      # BEFORE — not a recovery
    commission_event("epay", "after1", imei="IMEI1", mdn="5550001", amount=40.0,
                     category="Commission", paid_on="2026-06-11"),
    commission_event("epay", "after2", imei="IMEI1", mdn="5550001", amount=25.0,
                     category="Commission", paid_on="2026-07-03"),
    commission_event("epay", "sameday", imei="IMEI1", mdn="5550001", amount=9.0,
                     category="Commission", paid_on="2026-04-30"),      # same day — not after
    commission_event("epay", "rebate", imei="IMEI1", mdn="5550001", amount=500.0,
                     category="Re-imbursement", paid_on="2026-08-01"),  # not commission
    commission_event("epay", "undated", imei="IMEI1", mdn="5550001", amount=12.0,
                     category="Commission", paid_on=""),
]
r = wr.recovery(F1, index_commission_events(EV), as_of="2026-10-06", feeds_loaded=["epay"])
row = r["rows"][0]
check("only commission paid AFTER the last clawback leg counts as a recovery",
      row["recovered"] == 65.0 and row["events_after"] == 2)
check("money paid BEFORE the clawback is not a recovery of it", row["recovered"] == 65.0)
check("money paid ON the clawback date is not 'after'", row["recovered"] == 65.0)
check("a non-commission category is not a recovery of commission", row["recovered"] == 65.0)
check("an UNDATED payment is reported separately and never counted as a recovery",
      row["undated_paid"] == 12.0)
check("the months the money landed in are reported",
      row["recovered_months"] == {"2026-06": 40.0, "2026-07": 25.0})
check("a partial recovery says so, with the balance still out",
      row["recovery_state"] == wr.RECOVERED_PARTLY and row["still_out"] == 35.0)

full = wr.recovery(
    wr.withheld_findings([pd_row(imei="I", mdn="M", amount=-40.0, payment_date="2026-04-01")], DEC),
    index_commission_events([commission_event("epay", "u", imei="I", mdn="M", amount=40.0,
                                              category="Commission", paid_on="2026-05-01")]),
    as_of="2026-10-06", feeds_loaded=["epay"])["rows"][0]
check("a recovery for the full amount is RECOVERED with nothing still out",
      full["recovery_state"] == wr.RECOVERED and full["still_out"] == 0.0)

none = wr.recovery(
    wr.withheld_findings([pd_row(imei="I", mdn="M", amount=-40.0)], DEC),
    index_commission_events([commission_event("epay", "u", imei="I", mdn="M", amount=0.0,
                                              category="Commission", paid_on="2026-05-01")]),
    as_of="2026-10-06", feeds_loaded=["epay"])["rows"][0]
check("an activation the feed KNOWS but has not paid since is a MEASURED zero, not unknown",
      none["recovery_state"] == wr.NOT_RECOVERED and none["recovered"] == 0.0
      and none["still_out"] == 40.0)

# THE FENCE: the same handset, re-activated on a DIFFERENT number later. That later line's money is
# not this one's. The refusal lives in event_sales._line_event_uids and is exercised here, not
# re-implemented.
fenced = wr.recovery(
    wr.withheld_findings([pd_row(imei="SAMEIMEI", mdn="5550001", amount=-50.0,
                                 payment_date="2026-04-01")], DEC),
    index_commission_events([
        commission_event("epay", "other-sub", imei="SAMEIMEI", mdn="5559999", amount=300.0,
                         category="Commission", paid_on="2026-07-01")]),
    as_of="2026-10-06", feeds_loaded=["epay"])["rows"][0]
# The fence refuses the match outright, so NOTHING is reachable for this finding — and the honest
# state for "could not be looked up" is UNKNOWN, not a $0.00 recovery. Both halves matter: the money
# must not be credited here, AND the row must not be published as a measured loss.
check("THE IMEI FENCE: another subscriber's money on the same handset is refused",
      fenced["recovered"] is None and fenced["recovery_state"] == wr.RECOVERY_UNKNOWN)
check("THE IMEI FENCE: a refused match is reported as not-looked-up, never as a $0 recovery",
      "in no loaded commission feed" in fenced["recovery_reason"])
check("the fence does NOT fire when the clawback leg carries no number (serial is all there is)",
      wr.recovery(
          wr.withheld_findings([pd_row(imei="SAMEIMEI", mdn="", amount=-50.0,
                                       payment_date="2026-04-01")], DEC),
          index_commission_events([
              commission_event("epay", "by-serial", imei="SAMEIMEI", mdn="5559999", amount=50.0,
                               category="Commission", paid_on="2026-07-01")]),
          as_of="2026-10-06", feeds_loaded=["epay"])["rows"][0]["recovered"] == 50.0)

# ── 4. the unknowable is never a loss ───────────────────────────────────────────────────────────
absent = wr.recovery(wr.withheld_findings([pd_row(imei="GHOST", amount=-77.0)], DEC),
                     index_commission_events([]), as_of="2026-10-06", feeds_loaded=["epay"])["rows"][0]
check("an activation in NO loaded feed is unknown, not unrecovered",
      absent["recovery_state"] == wr.RECOVERY_UNKNOWN and absent["recovered"] is None
      and "in no loaded commission feed" in absent["recovery_reason"])
noload = wr.recovery(wr.withheld_findings([pd_row(imei="GHOST", amount=-77.0)], DEC),
                     index_commission_events([]), as_of="2026-10-06", feeds_loaded=[])["rows"][0]
check("with NO feed loaded at all the reason says so rather than blaming the activation",
      "no commission feed is loaded" in noload["recovery_reason"])
nokey = wr.recovery(wr.withheld_findings([pd_row(imei="", mdn="", amount=-3.0)], DEC),
                    index_commission_events([]), as_of="2026-10-06", feeds_loaded=["epay"])["rows"][0]
check("a finding with no identifier is unknown, with the honest reason",
      nokey["recovery_state"] == wr.RECOVERY_UNKNOWN
      and "neither a number nor a device serial" in nokey["recovery_reason"])
check("every recovery state carries a human note",
      set(wr.RECOVERY_NOTES) == {wr.RECOVERED, wr.RECOVERED_PARTLY, wr.NOT_RECOVERED,
                                 wr.RECOVERY_UNKNOWN}
      and all(len(v) > 40 for v in wr.RECOVERY_NOTES.values()))
check("the cutoff rule is stated in the payload", "STRICTLY AFTER" in r["cutoff_note"])

# one payment row may not be counted twice across two findings
twice = wr.recovery(
    wr.withheld_findings([pd_row(imei="A", mdn="M1", amount=-10.0, payment_date="2026-01-01"),
                          pd_row(imei="B", mdn="M1", amount=-10.0, payment_date="2026-01-01")], DEC),
    index_commission_events([commission_event("epay", "shared", imei="A", mdn="M1", amount=30.0,
                                              category="Commission", paid_on="2026-03-01")]),
    as_of="2026-10-06", feeds_loaded=["epay"])
check("one payment row is attributed to ONE finding, never double-counted",
      sum(x["recovered"] or 0 for x in twice["rows"]) == 30.0)

# ── 5. the ePay leg — credits only, never netted ────────────────────────────────────────────────
PAY_ROWS = [
    pd_row(imei="IMEI1", amount=-11.0),                                   # the clawback leg
    pd_row(imei="IMEI1", amount=240.0, payment_date="2026-05-01", payment_type="Recovery"),
    pd_row(imei="IMEI1", amount=60.0, payment_date="2026-07-01", payment_type="Recovery"),
    pd_row(imei="IMEI2", amount=-59.0),
]
FIND = wr.withheld_findings(PAY_ROWS, DEC)
e = wr.epay_leg(FIND, PAY_ROWS)
eby = {x["key"]: x for x in e["rows"]}
check("the parallel leg counts CREDITS only, and the clawback is not subtracted from it",
      eby["IMEI1"]["epay_paid"] == 300.0 and eby["IMEI1"]["epay_rows"] == 2
      and eby["IMEI1"]["epay_state"] == wr.EPAY_PAID)
check("the last payment date and the types paid are reported",
      eby["IMEI1"]["epay_last_paid_on"] == "2026-07-01"
      and eby["IMEI1"]["epay_types"] == ["Recovery"])
check("an activation with no payment row is a MEASURED zero, named as such",
      eby["IMEI2"]["epay_state"] == wr.EPAY_NONE and eby["IMEI2"]["epay_paid"] == 0.0)
check("with no feed loaded the leg is UNKNOWN, never $0.00 paid",
      wr.epay_leg(FIND, PAY_ROWS, feed_loaded=False)["rows"][0]["epay_state"] == wr.EPAY_UNKNOWN
      and wr.epay_leg(FIND, PAY_ROWS, feed_loaded=False)["rows"][0]["epay_paid"] is None)
check("a finding with no identifier cannot have its payment looked up, and says so",
      wr.epay_leg(wr.withheld_findings([pd_row(imei="", mdn="", amount=-1.0)], DEC),
                  PAY_ROWS)["rows"][0]["epay_state"] == wr.EPAY_UNKNOWN)
check("the payload states that the two legs are never summed",
      "never summed" in e["parallel_note"])
check("every ePay state carries a human note",
      set(wr.EPAY_NOTES) == {wr.EPAY_PAID, wr.EPAY_NONE, wr.EPAY_UNKNOWN}
      and all(len(v) > 40 for v in wr.EPAY_NOTES.values()))

# ── 6. summarize — the unknowable out of the headline, no_appeal a real bucket ───────────────────
ROWS6 = [
    {"withheld": 100.0, "recovered": 40.0, "still_out": 60.0, "undated_paid": 0.0,
     "recovery_state": wr.RECOVERED_PARTLY, "epay_state": wr.EPAY_PAID, "epay_paid": 500.0,
     "store": "S1", "rep": "r1", "appeal_status": "appeal_filed"},
    {"withheld": 77.0, "recovered": None, "still_out": 0.0, "undated_paid": 0.0,
     "recovery_state": wr.RECOVERY_UNKNOWN, "epay_state": wr.EPAY_UNKNOWN, "epay_paid": None,
     "store": "S1", "rep": "r2", "appeal_status": None},
    {"withheld": 20.0, "recovered": 0.0, "still_out": 20.0, "undated_paid": 5.0,
     "recovery_state": wr.NOT_RECOVERED, "epay_state": wr.EPAY_NONE, "epay_paid": 0.0,
     "store": "s1", "rep": "r1", "appeal_status": "written_off"},
]
c = wr.summarize(ROWS6)
check("the withheld total is every finding", c["withheld"] == 197.0 and c["findings"] == 3)
check("what is STILL OUT excludes the activation nobody could look up",
      c["still_out"] == 80.0 and c["unknown_withheld"] == 77.0 and c["unknown_findings"] == 1)
check("the unknowable is named in words, not left to subtraction",
      c["still_out_note"] and "$77.00" in c["still_out_note"])
check("the ePay total is its own figure, never netted against the clawback",
      c["epay_paid"] == 500.0)
check("the ePay unknown and measured-zero findings are counted separately",
      c["epay_unknown_findings"] == 1 and c["epay_none_findings"] == 1)
check("undated payments are carried as their own total", c["undated_paid"] == 5.0)
check("appeal buckets count findings, and no_appeal is a real bucket",
      c["by_appeal"]["appeal_filed"] == 1 and c["by_appeal"]["written_off"] == 1
      and c["by_appeal"]["no_appeal"] == 1 and c["by_appeal"]["appeal_won"] == 0)
check("every appeal state the ONE state machine knows has a bucket",
      set(c["by_appeal"]) == {"no_appeal"} | set(
          __import__("app.modules.commcalc.discrepancy_appeals",
                     fromlist=["x"]).APPEAL_STATES))
check("an unknown appeal spelling falls into no_appeal rather than inventing a bucket",
      wr.summarize([{"withheld": 1.0, "recovery_state": wr.NOT_RECOVERED, "recovered": 0.0,
                     "still_out": 1.0, "appeal_status": "banana"}])["by_appeal"]["no_appeal"] == 1)
check("stores and reps are counted case-insensitively", c["stores"] == 1 and c["reps"] == 2)
check("an appeal_of callable is honoured over the row field",
      wr.summarize(ROWS6, appeal_of=lambda r: "appeal_won")["by_appeal"]["appeal_won"] == 3)
check("empty input summarizes to zeros with no note, not a fabricated finding",
      wr.summarize([])["findings"] == 0 and wr.summarize([])["still_out_note"] is None)

# ── 7. THE REGRESSION, end to end on the live shape ─────────────────────────────────────────────
# Live 2026-10-06: 'Commission Withholding' declared as 'Commission', rows keyed by imei with an
# EMPTY mdn. The old detector saw none of this. The whole report must work from the serial alone.
LIVE = [
    {"payment_type": "Commission Withholding", "amount": -40.36, "imei": "352153372172297",
     "mdn": "", "payment_date": "2026-10-05", "business_address": "1710 W 4th St",
     "rep_username": "", "period": "October 2026"},
    {"payment_type": "Commission Withholding", "amount": -2.44, "imei": "352153372172297",
     "mdn": "", "payment_date": "2026-10-05", "business_address": "1710 W 4th St",
     "rep_username": "", "period": "October 2026"},
]
LIVE_DEC = {"Commission Withholding": "Commission"}
lf = wr.withheld_findings(LIVE, LIVE_DEC)
check("REGRESSION: the live rows produce one finding keyed by the DEVICE SERIAL",
      len(lf) == 1 and lf[0]["key_basis"] == wr.KEY_IMEI and lf[0]["withheld"] == 42.8)
lr = wr.recovery(lf, index_commission_events([
    commission_event("epay", "later", imei="352153372172297", amount=42.80,
                     category="Commission", paid_on="2026-11-14")]),
    as_of="2026-12-01", feeds_loaded=["epay"])["rows"][0]
check("REGRESSION: a number-less activation is still matched by serial and shows as recovered",
      lr["recovery_state"] == wr.RECOVERED and lr["recovered_months"] == {"2026-11": 42.8})
le = wr.epay_leg(lf, LIVE)["rows"][0]
check("REGRESSION: with only clawback legs in the feed, the paid leg is a measured zero",
      le["epay_state"] == wr.EPAY_NONE and le["epay_paid"] == 0.0)

print()
if FAILURES:
    print("%d FAILURE(S):" % len(FAILURES))
    for f in FAILURES:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
