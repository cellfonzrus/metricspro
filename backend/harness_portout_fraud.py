"""PROOF HARNESS — the daily port-out fraud report (owner directive 2026-09-28, index §19.32).

Run:  cd backend && python3 harness_portout_fraud.py

DB-free, stdlib only, no network, no money. Every fixture value is SYNTHETIC: the mobile numbers are
555-prefixed non-routable test numbers and the names are invented. Real subscriber identifiers are
never copied into this repo — this report is ABOUT subscriber lines, so that rule bites here.

Sections:
  A. CONFIG IS CONFIG (RULE TWO) — window, accessory floor and watched classes are per-org rows over
     house defaults; a junk value falls back to house rather than emptying or flooding the report.
  B. THE THREE DATING BASES — explicit deactivation, residual transfer-out, snapshot transition.
  C. ⚠ THE REGRESSION: a port-out date BEFORE our sale (the live -17-day row) is UNDECIDABLE, never
     flagged. This is the defect the report would have shipped with, and the negative control proves
     the check can go red.
  D. UNDECIDABLE IS A STATE, NOT A DROP — every non-verdict carries a reason with prose, and the
     headline prints what it could not see beside what it flagged.
  E. THE ACCESSORY COLUMN IS AN ATTRIBUTION, NOT A TRIGGER — and an unmeasured invoice is never $0.00.
  F. ONE DERIVATION: the lookup is marketing/event_sales.line_feed_state, shared with the event
     retention report, and the retention report's own behaviour is unchanged by the extraction.
  G. PORT-OUT IS A NAMED STATUS — an involuntary suspend is not a port-out.
  H. THE SECOND-PAYMENT TRIGGER IS DECLARED UNCONFIGURED rather than silently aliased to 30 days.
"""
import sys
import os
import io
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import portout_fraud as PF          # noqa: E402
from app.modules.marketing import event_sales as ES           # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  \033[92mPASS\033[0m " if cond else "\033[91m  FAIL\033[0m ") + name +
          (("  — " + detail) if (detail and not cond) else ""))


def section(t):
    print("\n" + "=" * 98 + "\n" + t + "\n" + "=" * 98)


# ── SYNTHETIC FIXTURES ────────────────────────────────────────────────────────────────────────────
# 555-01xx numbers are reserved-for-fiction and route nowhere. No live value appears in this file.
def sub(phone, status, act=None, deact=None, xfer=None, serial=""):
    return {"phone_number": phone, "device_serial": serial, "subscriber_status": status,
            "mi_activation_date": act, "mi_deactivation_date": deact,
            "residual_transfer_out_date": xfer}


def snap(rows, loaded=True):
    idx = {"mdn": {}, "serial": {}}
    for r in rows:
        if r.get("phone_number"):
            idx["mdn"][str(r["phone_number"])] = r
        if r.get("device_serial"):
            idx["serial"][str(r["device_serial"])] = r
    return {"index": idx, "loaded": loaded}


def line(mdn, day, tid="T1", store="STORE-A", rep="Pat Quinn"):
    return {"mdn": mdn, "serial_1": "", "trans_date": day, "trans_id": tid, "store": store,
            "salesperson": rep, "line_class": "port", "product_desc": "Handset"}


R = PF.resolve_rules(None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. CONFIG IS CONFIG (RULE TWO)")
check("house default window is the owner's 30 days", R["window_days"] == 30)
check("house default accessory floor is the owner's $50", R["accessory_floor"] == 50.0)
check("house watches port-in", R["watch_classes"] == ["port"])
check("second-payment boundary is UNSET at house level", R["second_payment_days"] is None)

t = PF.resolve_rules({"window_days": 45, "accessory_floor": 75.0, "watch_classes": ["port", "activation"]})
check("a tenant row overrides the window", t["window_days"] == 45)
check("a tenant row overrides the accessory floor", t["accessory_floor"] == 75.0)
check("a tenant row overrides the watched classes", t["watch_classes"] == ["port", "activation"])
check("no literal 30/50 survives a tenant override (config, never code)",
      t["window_days"] != R["window_days"] and t["accessory_floor"] != R["accessory_floor"])

for bad in ({"window_days": 0}, {"window_days": -5}, {"window_days": "x"}, {"window_days": None}):
    check("a window of %r falls back to house, never to zero" % bad["window_days"],
          PF.resolve_rules(bad)["window_days"] == 30)
check("a negative accessory floor falls back to house",
      PF.resolve_rules({"accessory_floor": -1})["accessory_floor"] == 50.0)
check("an accessory floor of 0 IS honoured (a tenant may want only free-of-accessory rows)",
      PF.resolve_rules({"accessory_floor": 0})["accessory_floor"] == 0.0)
check("an empty watch_classes list falls back to house rather than watching nothing",
      PF.resolve_rules({"watch_classes": []})["watch_classes"] == ["port"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE THREE DATING BASES")
S = {"2026-08": snap([sub("5550100", "ACTIVE"),
                      sub("5550101", "ACTIVE"),
                      sub("5550102", "ACTIVE"),
                      sub("5550103", "ACTIVE")]),
     "2026-09": snap([sub("5550100", "PORTED-OUT", deact="2026-08-20"),
                      sub("5550101", "PORTED-OUT", xfer="2026-08-22"),
                      sub("5550102", "PORTED-OUT"),
                      sub("5550103", "PORTED-OUT", deact="2026-11-30")])}

v = PF.evaluate(line("5550100", "2026-08-05"), S, R, latest_key="2026-09")
check("explicit deactivation date dates the port-out exactly",
      v["basis"] == PF.BASIS_DEACTIVATION and v["days"] == 15)
check("15 days is inside the 30-day window -> FLAGGED", v["state"] == PF.STATE_FLAGGED)

v = PF.evaluate(line("5550101", "2026-08-05"), S, R, latest_key="2026-09")
check("residual transfer-out date is the second exact basis",
      v["basis"] == PF.BASIS_TRANSFER_OUT and v["days"] == 17 and v["state"] == PF.STATE_FLAGGED)

v = PF.evaluate(line("5550103", "2026-08-05"), S, R, latest_key="2026-09")
check("a port-out 117 days later is CLEARED, not flagged",
      v["state"] == PF.STATE_CLEARED and v["days"] == 117)

# snapshot basis: no date at all, bounded by the month it first reads PORTED-OUT
v = PF.evaluate(line("5550102", "2026-09-20"), S, R, latest_key="2026-09", first_ported_key="2026-09")
check("with no date, the snapshot month BOUNDS the port-out", v["basis"] == PF.BASIS_SNAPSHOT)
check("a bound entirely inside the window flags", v["state"] == PF.STATE_FLAGGED)
check("a bounded row reports the window, not a fabricated day",
      v["days"] is None and v["ported_between"] == ["2026-09-01", "2026-09-30"])

v = PF.evaluate(line("5550102", "2026-08-20"), S, R, latest_key="2026-09", first_ported_key="2026-09")
check("a bound spanning day 12 to day 41 STRADDLES the boundary -> undecidable, not a coin toss",
      v["state"] == PF.STATE_UNDECIDABLE and v["reason"] == PF.REASON_STRADDLES)

v = PF.evaluate(line("5550102", "2026-06-01"), S, R, latest_key="2026-09", first_ported_key="2026-09")
check("a bound entirely outside the window clears", v["state"] == PF.STATE_CLEARED)

v = PF.evaluate(line("5550102", "2026-09-20"), S, R, latest_key="2026-09", first_ported_key=None)
check("PORTED-OUT with no date and no bound is undecidable with its own reason",
      v["state"] == PF.STATE_UNDECIDABLE and v["reason"] == PF.REASON_NO_PORTOUT_DATE)
check("that reason carries prose a manager can act on", len(PF.reason_note(PF.REASON_NO_PORTOUT_DATE)) > 80)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. ⚠ THE REGRESSION — a port-out BEFORE our sale is NEVER flagged (the live -17-day row)")
# Reproduces the shape found on live house data 2026-09-28: a port-in whose matched feed row carries a
# port-out date 17 days BEFORE the sale. A bare `days <= 30` test flags it as the report's worst case.
S2 = {"2026-09": snap([sub("5550199", "PORTED-OUT", deact="2026-08-19")])}
v = PF.evaluate(line("5550199", "2026-09-05"), S2, R, latest_key="2026-09")
check("REGRESSION: a -17-day lifespan is UNDECIDABLE, not flagged",
      v["state"] == PF.STATE_UNDECIDABLE, "got %r" % v["state"])
check("REGRESSION: and it names the recycled-number reason",
      v["reason"] == PF.REASON_BEFORE_ACTIVATION)
check("REGRESSION: the negative day count is still REPORTED, not hidden", v["days"] == -17)
check("the reason explains recycled numbers rather than asserting fraud",
      "recycled" in PF.reason_note(PF.REASON_BEFORE_ACTIVATION).lower())

# NEGATIVE CONTROL: the naive rule the report must not have.
naive = [d for d in (-17, 2, 15, 45) if d <= R["window_days"]]
check("NEGATIVE CONTROL: a bare `days <= window` test WOULD flag the -17 row (so the check is real)",
      -17 in naive)

v = PF.evaluate(line("5550199", "2026-08-19"), S2, R, latest_key="2026-09")
check("a same-day port-out (0 days) is also undecidable, not a zero-day 'worst case'",
      v["state"] == PF.STATE_UNDECIDABLE and v["reason"] == PF.REASON_BEFORE_ACTIVATION)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C2. ⚠ THE REGRESSION: a port-out in an EARLIER month, gone from the latest snapshot")
# Found by the live smoke test 2026-09-28, not by reading: asking only the LATEST snapshot put 323
# house lines into `dropped_out_of_the_feed` — a line that ported out in April and is absent from
# September reads as an ABSENCE, and the port-out the report exists to find is swallowed by it.
S4 = {"2026-04": snap([sub("5550400", "ACTIVE")]),
      "2026-05": snap([sub("5550400", "PORTED-OUT", deact="2026-05-02")]),
      "2026-09": snap([])}                       # gone from the latest month entirely
L4 = line("5550400", "2026-04-20", tid="C1")

v = PF.evaluate(L4, S4, R, latest_key="2026-09")
check("asking only the LATEST month loses the port-out to an absence (the defect)",
      v["state"] == PF.STATE_UNDECIDABLE and v["reason"] == ES.REASON_DROPPED,
      "got %r/%r" % (v["state"], v["reason"]))
v = PF.evaluate(L4, S4, R, latest_key="2026-09", first_ported_key="2026-05")
check("REGRESSION: asked in the month it READS PORTED-OUT, the 12-day port-out is FLAGGED",
      v["state"] == PF.STATE_FLAGGED and v["days"] == 12, "got %r/%r" % (v["state"], v["days"]))
r4 = PF.report([L4], S4, R, latest_key="2026-09", accessory_by_invoice={"C1": 0.0},
               first_ported_by_line={"5550400": "2026-05"})
check("REGRESSION: and the whole report finds it, not just the bare evaluator",
      r4["summary"]["flagged"] == 1 and r4["summary"]["undecidable"] == 0)
check("a line that NEVER reads ported-out still falls back to the latest month",
      PF.evaluate(line("5550100", "2026-08-05"), S, R,
                  latest_key="2026-09", first_ported_key=None)["state"] == PF.STATE_FLAGGED)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. UNDECIDABLE IS A STATE, AND THE HEADLINE CONFESSES IT")
lines = [line("5550100", "2026-08-05", tid="A1"),                      # flagged, 15d
         line("5550101", "2026-08-05", tid="A2"),                      # flagged, 17d
         line("5550103", "2026-08-05", tid="A3"),                      # cleared
         line("5550102", "2026-08-20", tid="A4"),                      # straddles -> undecidable
         line("", "2026-08-05", tid="A5"),                             # no mdn -> undecidable
         line("5559999", "2026-08-05", tid="A6"),                      # absent from feed
         {**line("5550100", "", tid="A7")}]                            # no sale date
rep = PF.report(lines, S, R, latest_key="2026-09",
                accessory_by_invoice={"A1": 0.0, "A2": 120.0, "A3": 10.0, "A4": 5.0},
                first_ported_by_line={"5550102": "2026-09"})
s = rep["summary"]
check("every line lands in exactly one state (nothing is dropped)",
      s["flagged"] + s["cleared"] + s["retained"] + s["undecidable"] == len(lines))
check("the headline carries the undecidable count beside the flagged one",
      s["flagged"] == 2 and s["undecidable"] == 4, repr(s))
check("the headline carries a coverage percentage", s["coverage_pct"] == 42.9)
check("undecidable is broken down BY REASON, so the gap is actionable",
      set(s["undecidable_by_reason"]) == {PF.REASON_STRADDLES, PF.REASON_NO_MDN,
                                          "absent_from_loaded_feed", PF.REASON_NO_SALE_DATE},
      repr(s["undecidable_by_reason"]))
check("every reason in the payload carries its prose",
      all(rep["undecidable_reasons"][k] for k in rep["undecidable_reasons"]))
check("every row that is not a verdict carries a reason",
      all(r["reason"] for r in rep["rows"] if r["state"] == PF.STATE_UNDECIDABLE))
check("no flagged row carries a reason (a finding is not an excuse)",
      all(not r["reason"] for r in rep["rows"] if r["state"] == PF.STATE_FLAGGED))
check("`decidable` is False on exactly the undecidable rows — the §19.31 vocabulary, not a fifth one",
      all((r["state"] == PF.STATE_UNDECIDABLE) == (not r["decidable"]) for r in rep["rows"]))
check("the report is URGENT when anything is flagged", s["urgent"] is True)
check("and not urgent when nothing is", PF.report([], S, R)["summary"]["urgent"] is False)
check("an empty report reports coverage None, never 100%", PF.report([], S, R)["summary"]["coverage_pct"] is None)
check("flagged rows roll up by store and by rep for the manager's message",
      s["flagged_by_store"] == {"STORE-A": 2} and s["flagged_by_rep"] == {"Pat Quinn": 2})

# not-yet-elapsed
rep2 = PF.report([line("5550100", "2026-09-28", tid="B1")], S, R, latest_key="2026-09",
                 as_of="2026-09-29")
check("a line sold yesterday is NOT a clean line — it is not-yet-elapsed",
      rep2["summary"]["undecidable"] == 1 and rep2["rows"][0]["reason"] == PF.REASON_NOT_ELAPSED)
check("and it is not counted as cleared", rep2["summary"]["cleared"] == 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. THE ACCESSORY COLUMN — attribution, not trigger; unknown, not $0.00")
check("below the floor leans rep-driven", PF.attribution(0.0, R) == PF.ATTRIB_REP_DRIVEN)
check("$49.99 is below a $50 floor", PF.attribution(49.99, R) == PF.ATTRIB_REP_DRIVEN)
check("exactly at the floor is NOT below it", PF.attribution(50.0, R) == PF.ATTRIB_CUSTOMER_DRIVEN)
check("above the floor leans customer-driven", PF.attribution(120.0, R) == PF.ATTRIB_CUSTOMER_DRIVEN)
check("an UNMEASURED invoice is unknown, never 0.00 (0.00 is this column's most accusing value)",
      PF.attribution(None, R) == PF.ATTRIB_UNKNOWN)
check("the floor moves with config, so $50 is not a branch",
      PF.attribution(60.0, PF.resolve_rules({"accessory_floor": 75})) == PF.ATTRIB_REP_DRIVEN)
flagged = {r["trans_id"]: r for r in rep["flagged"]}
check("a flagged row with $0 accessories is still FLAGGED (the accessory is not the trigger)",
      flagged["A1"]["state"] == PF.STATE_FLAGGED and flagged["A1"]["attribution"] == PF.ATTRIB_REP_DRIVEN)
check("a flagged row with $120 accessories is EQUALLY flagged",
      flagged["A2"]["state"] == PF.STATE_FLAGGED and flagged["A2"]["attribution"] == PF.ATTRIB_CUSTOMER_DRIVEN)
check("the accessory floor never moves a row's state",
      PF.evaluate(line("5550100", "2026-08-05"), S, PF.resolve_rules({"accessory_floor": 9999}),
                  latest_key="2026-09", accessory_total=0.0)["state"] == PF.STATE_FLAGGED)
check("the summary counts flagged rows under the floor separately for the message's ordering",
      s["flagged_below_accessory_floor"] == 1)
check("an invoice with no accessory entry reads unknown, not zero",
      PF.report([line("5550100", "2026-08-05", tid="ZZ")], S, R, latest_key="2026-09",
                accessory_by_invoice={})["rows"][0]["attribution"] == PF.ATTRIB_UNKNOWN)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. ONE DERIVATION — the lookup is the retention report's, shared not copied")
check("the fraud module dereferences event_sales.line_feed_state", PF.line_feed_state is ES.line_feed_state)
check("and inherits its three-state vocabulary verbatim", PF.STATE_UNMATCHED == ES.STATE_UNMATCHED)
check("and its unmatched reasons verbatim (no second spelling)",
      PF.REASON_NO_MDN == ES.REASON_NO_MDN and PF.UNMATCHED_REASONS is ES.UNMATCHED_REASONS)
check("a lookup reason's prose comes from the retention home, not a local copy",
      PF.reason_note(ES.REASON_ABSENT) == ES.UNMATCHED_REASONS[ES.REASON_ABSENT])
# The extraction must not have changed the retention report's own answers.
ev = ES.evaluate_line({"mdn": "5550100", "serial_1": "", "trans_date": "2026-08-05"},
                      None, S, {"2026-09": "September 2026"}, latest_key="2026-09",
                      earlier_keys=["2026-08", "2026-09"])
check("retention: a matched ported-out line is CHURNED, exactly as before the extraction",
      ev["state"] == ES.STATE_CHURNED and ev["status"] == "PORTED-OUT")
check("retention: the matched branch still carries the feed's dates",
      "mi_deactivation_date" in ev and ev["mi_deactivation_date"] == "2026-08-20")
ev = ES.evaluate_line({"mdn": "5559999", "serial_1": "", "trans_date": "2026-08-05"},
                      None, S, {}, latest_key="2026-09", earlier_keys=["2026-08", "2026-09"])
check("retention: an unmatched line is still unmatched-with-a-reason, never churn",
      ev["state"] == ES.STATE_UNMATCHED and ev["reason"] == ES.REASON_ABSENT)
check("retention: the unmatched branch carries NO feed dates (shape unchanged)",
      "mi_deactivation_date" not in ev)
ev = ES.evaluate_line({"mdn": "5550100", "serial_1": "", "trans_date": "2026-08-05"},
                      None, {"2026-09": snap([], loaded=False)}, {}, latest_key="2026-09")
check("retention: an unloaded snapshot is still an ingest gap, not a churn",
      ev["state"] == ES.STATE_UNMATCHED and ev["reason"] == ES.REASON_FEED_NOT_LOADED)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. PORT-OUT IS A NAMED STATUS — 'not active' is not good enough")
check("PORTED-OUT is a port-out", PF.is_ported_out("PORTED-OUT") is True)
check("lower/underscore spellings are the same status",
      PF.is_ported_out("ported_out") and PF.is_ported_out("Ported Out"))
check("INVOLUNTARY-SUSPENDED is NOT a port-out", PF.is_ported_out("INVOLUNTARY-SUSPENDED") is False)
check("INACTIVE is NOT a port-out", PF.is_ported_out("INACTIVE") is False)
check("ACTIVE is not a port-out", PF.is_ported_out("ACTIVE") is False)
check("blank is not a port-out", PF.is_ported_out("") is False)
S3 = {"2026-09": snap([sub("5550300", "INVOLUNTARY-SUSPENDED", deact="2026-09-10")])}
v = PF.evaluate(line("5550300", "2026-09-01"), S3, R, latest_key="2026-09")
check("a suspended line carrying a deactivation date is RETAINED, not flagged",
      v["state"] == PF.STATE_RETAINED, "got %r" % v["state"])
check("NEGATIVE CONTROL: written as `not is_active_status` it WOULD have flagged that suspend",
      not ES.is_active_status("INVOLUNTARY-SUSPENDED"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. THE SECOND-PAYMENT TRIGGER IS DECLARED, NOT ALIASED")
b = PF.basis_note(R)
check("the payload states the second-payment trigger is not evaluated",
      "does NOT" in b["second_payment"] and "second payment" in b["second_payment"].lower())
check("it says so without pretending 30 days stands in for it",
      "stand-in" in b["second_payment"])
check("the basis note states the activation date is OURS", "transaction" in b["activation_date"].lower())
check("the basis note states a port-out we cannot date is undecidable",
      "UNDECIDABLE" in b["port_out_date"])
check("the basis note states the accessory column is not a trigger",
      "NOT a trigger" in b["accessory"])
check("the basis note refuses to read as a verdict about a person",
      "accuses no person" in b["not_a_verdict"])
check("the basis note names the derivation it reuses", "line_feed_state" in b["reuses"])

# ── THE RECIPIENT RESOLVER READS THE SCHEMA THE TABLES ARE ACTUALLY IN ───────────────────────────
# THE DEFECT (owner found it 2026-09-28, before the daily send was ever switched on). The first draft
# of `notify.router._role_scope_recipients` read `roles` and `app_users` through `sb()`, which is
# `.schema("notify")`. Those tables live in `storeops` (mig 003 / 015 — the PostgREST-EXPOSED schema;
# `core` deliberately is not). So every call raised, the blanket `except` swallowed it, and the
# function returned NO RECIPIENTS with no error. Applying the daily send would have created a job
# that mailed nobody, for ever, silently, on the one report whose whole point is urgency.
#
# A source check, not a behavioural one, on purpose: reproducing it behaviourally needs a live
# PostgREST that knows which schemas exist, which is the one thing a DB-free harness cannot have —
# and that is exactly why CI could not catch it. What IS checkable without a database is that the
# resolver names the right schema and does not go back through the notify-scoped helper.
_NR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "app", "modules", "notify", "router.py")
_nr_src = io.open(_NR, encoding="utf-8").read() if os.path.isfile(_NR) else ""
_fn = ""
_m = re.search(r"def _role_scope_recipients\(.*?(?=\ndef |\Z)", _nr_src, re.S)
if _m:
    _fn = _m.group(0)
check("the recipient resolver exists", bool(_fn))
check("it reads roles/app_users from the storeops schema", 'schema("storeops")' in _fn)
check("it does NOT read them through sb(), which is the notify schema  <- the defect",
      'sb().table("roles")' not in _fn and 'sb().table("app_users")' not in _fn)
check("a resolution failure is LOGGED, never swallowed in silence",
      "_log.exception(" in _fn or "_log.warning(" in _fn)
check("resolving to nobody is itself reported", "resolves to nobody" in _fn)
check("it still never raises (an urgent report with explicit recipients must not be silenced)",
      "except Exception:" in _fn and "return [], []" in _fn)

print("\n" + "=" * 98)
print("RESULT: %d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("   FAILED: " + f)
print("=" * 98)
sys.exit(1 if FAIL else 0)
