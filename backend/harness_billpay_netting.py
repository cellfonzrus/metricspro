"""PROOF: the pickup envelope nets out the bill-pay cash that is collected on the other screen.

OWNER DIRECTIVE 2026-09-08
    "on the cash pick up it shows the full amount but it should only show the store cash amount to be
     picked up, as the epay amount is being declared and picked up on a different menu — this is
     duplicating the total cash."
and, asked which figure to net by:
    "it should be the total cash minus the epay cash, NOT as declared by the employee but as
     CALCULATED BY THE POS."

THE OVERLAP WAS REAL. The closing form's cash field is, per the owner's own 2026-09-02 directive,
"Total cash in store including Bill Payments" — `t_cash` is the whole drawer and `epay_on_cash` is a
SUBSET of it. Cash Pickup collected the whole drawer while the bill-pay page separately offered the
bill-pay cash for collection: the same physical dollars on two screens.

WHY NOT THE DECLARED FIGURE — measured live, house org, August 2026, 539 closings:

    sum t_cash                                     $209,583.23
    sum epay_on_cash (declared bill-pay cash)      $183,156.03
    rows where declared bill-pay > total cash      147 / 539  (27.3%)
      … 30-odd of them with t_cash $0.00 and $687–$891 of declared bill-pay cash

A subset cannot exceed its whole, so the declaration cannot be trusted as the amount. The POS figure
can — it is computed from the sales transactions, not typed by the person holding the cash. Live
Aug 1–7 the POS sales leg reports $91,232.22 of bill-pay CASH (and $12,873.94 on card).

WHAT THIS PINS
  A. the POS figure decides the AMOUNT; the declaration only decides the SPLIT between reps;
  B. an envelope never nets below zero, and cash that cannot be netted is REPORTED, not dropped;
  C. three states — netted / no-POS-figure / switched-off — never a fabricated zero;
  D. the 147-row "declared more bill-pay than total cash" condition is flagged per envelope;
  E. the arithmetic ties: gross − netted == net, summed and per row.

PURE: stdlib only, no DB, no network.  Run: `cd backend && python3 harness_billpay_netting.py`
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app.modules.closing import billpay_netting as BN      # noqa: E402


def row(k, t_cash, declared=0.0):
    return {"key": k, "t_cash": t_cash, "epay_on_cash": declared}


print("\n== A. the POS figure is the AMOUNT; the declaration only splits it ==")
r = BN.net_store_day([row("a", 1000.0, 600.0)], pos_billpay_cash=400.0)
check("A1 one rep: the POS figure is what comes out, not their $600 claim",
      r["rows"]["a"]["billpay_netted"] == 400.0 and r["rows"]["a"]["net"] == 600.0, r["rows"]["a"])
r = BN.net_store_day([row("a", 1000.0, 300.0), row("b", 1000.0, 100.0)], pos_billpay_cash=400.0)
check("A2 two reps: the POS $400 splits 3:1 on their own declarations",
      r["rows"]["a"]["billpay_netted"] == 300.0 and r["rows"]["b"]["billpay_netted"] == 100.0, r["rows"])
check("A3 and the split always sums to the POS figure",
      r["netted_total"] == 400.0 and r["unallocated"] == 0.0, r)
r = BN.net_store_day([row("a", 300.0), row("b", 100.0)], pos_billpay_cash=200.0)
check("A4 nobody declared anything -> split by the cash they hold (3:1)",
      r["rows"]["a"]["billpay_netted"] == 150.0 and r["rows"]["b"]["billpay_netted"] == 50.0, r["rows"])
r = BN.net_store_day([row("a", 0.0), row("b", 0.0)], pos_billpay_cash=200.0)
check("A5 no cash and no declarations -> nothing can be netted, and it is reported",
      r["netted_total"] == 0.0 and r["unallocated"] == 200.0, r)
check("A6 a bigger declaration never pulls out more than the POS says",
      BN.net_store_day([row("a", 5000.0, 4000.0)], pos_billpay_cash=100.0)["netted_total"] == 100.0)


print("\n== B. an envelope never goes negative, and the remainder is never dropped ==")
r = BN.net_store_day([row("a", 50.0, 900.0)], pos_billpay_cash=900.0)
check("B1 the POS exceeds the whole envelope -> netted to zero, not negative",
      r["rows"]["a"]["net"] == 0.0 and r["rows"]["a"]["billpay_netted"] == 50.0, r["rows"]["a"])
check("B2 and the $850 the POS says passed through is REPORTED, not silently lost",
      r["unallocated"] == 850.0, r)
r = BN.net_store_day([row("a", 50.0, 900.0), row("b", 1000.0, 100.0)], pos_billpay_cash=600.0)
check("B3 a rep capped at their own cash spills the rest to a rep with headroom",
      r["rows"]["a"]["net"] == 0.0 and r["rows"]["a"]["billpay_netted"] == 50.0
      and r["rows"]["b"]["billpay_netted"] == 550.0, r["rows"])
check("B4 the store-day total still nets exactly the POS figure",
      r["netted_total"] == 600.0 and r["unallocated"] == 0.0, r)
check("B5 spill can never push another envelope negative either",
      all(v["net"] >= 0 for v in
          BN.net_store_day([row("a", 10.0, 900.0), row("b", 20.0, 5.0)],
                           pos_billpay_cash=900.0)["rows"].values()))
r = BN.net_store_day([row("a", 10.0, 900.0), row("b", 20.0, 5.0)], pos_billpay_cash=900.0)
check("B6 with only $30 in the store, $870 is reported unallocated",
      r["netted_total"] == 30.0 and r["unallocated"] == 870.0, r)


print("\n== C. THREE STATES — a missing POS figure is never a zero ==")
r = BN.net_store_day([row("a", 500.0, 200.0)], pos_billpay_cash=None)
check("C1 no POS figure -> basis 'none' and NOTHING netted", r["basis"] == "none"
      and r["rows"]["a"]["net"] == 500.0 and r["rows"]["a"]["billpay_netted"] == 0.0, r)
check("C2 and the DM is TOLD the envelope is un-netted, not shown a reconciled-looking number",
      "No POS bill-pay figure" in (BN.envelope_note(r, "a") or ""), BN.envelope_note(r, "a"))
r = BN.net_store_day([row("a", 500.0, 200.0)], pos_billpay_cash=200.0, enabled=False)
check("C3 netting switched off -> basis 'off', the full envelope, no note",
      r["basis"] == "off" and r["rows"]["a"]["net"] == 500.0 and BN.envelope_note(r, "a") is None, r)
r = BN.net_store_day([row("a", 500.0, 200.0)], pos_billpay_cash=0.0)
check("C4 a REAL zero from the POS is basis 'pos' — different from 'none'",
      r["basis"] == "pos" and r["rows"]["a"]["net"] == 500.0, r)
check("C5 which is the whole point: 'no data' and 'no bill payments' read differently",
      BN.net_store_day([row("a", 500.0)], pos_billpay_cash=None)["basis"] != r["basis"])
r = BN.net_store_day([row("a", 500.0, 200.0)], pos_billpay_cash=120.0)
check("C6 a netted envelope says what came out and why",
      "$120.00" in (BN.envelope_note(r, "a") or "")
      and "bill-pay screen" in (BN.envelope_note(r, "a") or ""), BN.envelope_note(r, "a"))
r = BN.net_store_day([row("a", 50.0, 900.0)], pos_billpay_cash=900.0)
check("C7 and an unallocated remainder is said out loud in the same sentence",
      "$850.00 more bill-pay cash" in (BN.envelope_note(r, "a") or ""), BN.envelope_note(r, "a"))


print("\n== D. the 27.3% of live rows that declare more bill-pay than they hold ==")
# Real August rows, verbatim from the live read.
LIVE = [("2026-08-04 B-117  Rohit", 225.0, 1465.0),
        ("2026-08-17 B-6011 Anthony Castella", 0.0, 891.0),
        ("2026-08-03 B-1800 Anjali Sharma", 0.0, 877.0),
        ("2026-08-01 B-117  Rohit", 544.0, 1338.0)]
for label, tc, dec in LIVE:
    rr = BN.net_store_day([row(label, tc, dec)], pos_billpay_cash=None)
    check(f"D[{label}] flagged: declared ${dec:,.0f} bill-pay against ${tc:,.0f} of cash",
          rr["rows"][label]["declared_exceeds_cash"] is True)
ok = BN.net_store_day([row("x", 1000.0, 300.0)], pos_billpay_cash=None)
check("D5 a sane row is NOT flagged", ok["rows"]["x"]["declared_exceeds_cash"] is False)
check("D6 equal declared and total is not a violation",
      BN.net_store_day([row("x", 300.0, 300.0)],
                       pos_billpay_cash=None)["rows"]["x"]["declared_exceeds_cash"] is False)
check("D7 the flag is independent of netting — it shows even when netting is OFF",
      BN.net_store_day([row("x", 10.0, 900.0)], pos_billpay_cash=None,
                       enabled=False)["rows"]["x"]["declared_exceeds_cash"] is True)


print("\n== E. the arithmetic ties, every time ==")
CASES = [
    ([row("a", 1000.0, 600.0), row("b", 500.0, 100.0)], 400.0),
    ([row("a", 0.0, 891.0)], 500.0),
    ([row("a", 225.0, 1465.0), row("b", 300.0, 0.0)], 700.0),
    ([row("a", 12.34, 5.67), row("b", 89.01, 45.0), row("c", 3.0, 0.0)], 33.33),
    ([row("a", 100.0, 50.0)], 0.0),
]
for i, (rows_, pos) in enumerate(CASES, 1):
    res = BN.net_store_day(rows_, pos_billpay_cash=pos)
    per_row = all(abs(v["gross"] - v["billpay_netted"] - v["net"]) < 0.005 for v in res["rows"].values())
    tot = sum(v["billpay_netted"] for v in res["rows"].values())
    check(f"E{i}a gross - netted == net on every envelope", per_row, res["rows"])
    check(f"E{i}b the rows' netted amounts sum to netted_total",
          abs(tot - res["netted_total"]) < 0.005, (tot, res["netted_total"]))
    check(f"E{i}c netted_total + unallocated == the POS figure",
          abs(res["netted_total"] + res["unallocated"] - pos) < 0.02,
          (res["netted_total"], res["unallocated"], pos))
    check(f"E{i}d nothing is ever netted below zero",
          all(v["net"] >= -0.005 and v["billpay_netted"] >= -0.005 for v in res["rows"].values()))
check("E6 an empty store-day is inert, not an error",
      BN.net_store_day([], pos_billpay_cash=100.0)["netted_total"] == 0.0)
check("E7 a negative POS figure is clamped, never treated as a credit to the envelope",
      BN.net_store_day([row("a", 100.0)], pos_billpay_cash=-50.0)["rows"]["a"]["net"] == 100.0)


print("\n== F. RULE TWO — no carrier / tenant / processor literal in the rule ==")
_src = open("app/modules/closing/billpay_netting.py").read()
import io as _io, tokenize as _tok                                          # noqa: E402
_code = " ".join(t.string for t in _tok.generate_tokens(_io.StringIO(_src).readline)
                 if t.type not in (_tok.COMMENT, _tok.STRING)).lower()
check("F0 the scan really did drop the prose (the docstring's own evidence is not in it)",
      "owner" in _src.lower() and "owner" not in _code)
for banned in ("epay", "boost", "vidapay", "b2bsoft", "cellfonz", "luxelink", "metro", "verizon"):
    check(f"F {banned!r} appears nowhere in the rule itself", banned not in _code)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
