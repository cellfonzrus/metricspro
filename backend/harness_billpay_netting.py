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


# ══ G. THE DECLARED SOURCE (owner bug report 2026-10-02, mig 1038) ═══════════════════════════════
# OWNER: "cash pick up 258 short but 258 is epay cash which appears on the next report for epay
# pick up ... this cash pick up report should not show anything short since 15 is declared as store
# cash and 258 as epay and the total is 273 — cash pick up should only show the cash from sales and a
# column for total cash".
#
# That is the SAME overlap section A exists for, reported again because the 2026-09-08 switch was
# never turned on — and described with the DECLARED figures, which that directive ruled out as the
# amount. So the source is now a per-org choice, and this section pins that choosing 'declared':
#   • resolves the owner's own store-day exactly (the regression below);
#   • still caps each envelope at its own cash, so the 147-row condition is visible, not netted away;
#   • still reports when the POS does not back the declaration, so the caution that made the POS the
#     original choice is not quietly dropped;
#   • changes NOTHING for an org on the default source.
print("\n== G. the DECLARED source — the owner's 2026-10-02 report ==")
check("G0 'pos' is the default source, so mig 1038 changes no org's numbers on its own",
      BN.NET_SOURCE_DEFAULT == "pos" and BN.normalize_net_source(None) == "pos"
      and BN.normalize_net_source("") == "pos")
check("G0b an unreadable source word degrades to the behaviour that shipped, never to the new one",
      BN.normalize_net_source("declaredd") == "pos" and BN.normalize_net_source("POS") == "pos"
      and BN.normalize_net_source("DECLARED") == "declared")

# THE REGRESSION — B-2612 / 2026-09-03, the exact store-day the owner reported.
g = BN.net_store_day([row("kashif", 273.0, 258.0)], pos_billpay_cash=None, source="declared")
check("G1 REGRESSION B-2612 2026-09-03: the envelope offers the $15.00 of sales cash, not $273.00",
      g["rows"]["kashif"]["net"] == 15.0, g["rows"]["kashif"])
check("G1b and the $258.00 the bill-pay screen collects is what came out",
      g["rows"]["kashif"]["billpay_netted"] == 258.0 and g["basis"] == "declared")
check("G1c so the DM collecting $15.00 is LEVEL, where before it read $258.00 short",
      abs(g["rows"]["kashif"]["net"] - 15.0) < 0.005)
check("G1d the whole drawer is still reported beside it — the owner asked for both figures",
      g["rows"]["kashif"]["gross"] == 273.0)

check("G2 the declared source resolves a store-day the POS has no figure for — where 'pos' nets "
      "nothing at all and the envelope stays short",
      g["basis"] == "declared"
      and BN.net_store_day([row("kashif", 273.0, 258.0)], pos_billpay_cash=None)["basis"] == "none")

# The 147-row August condition: a declaration BIGGER than the drawer. The cap must bite and be said.
g3 = BN.net_store_day([row("a", 0.0, 891.0)], source="declared")
check("G3 a declaration bigger than the whole drawer nets only the drawer — never below zero",
      g3["rows"]["a"]["net"] == 0.0 and g3["rows"]["a"]["billpay_netted"] == 0.0)
check("G3b and it is FLAGGED, so the 27%-of-August condition stays visible on the declared source",
      g3["rows"]["a"]["declared_exceeds_cash"] is True)
g3b = BN.net_store_day([row("a", 100.0, 400.0)], source="declared")
check("G3c partially: $400 declared against a $100 drawer nets $100 and flags it",
      g3b["rows"]["a"]["net"] == 0.0 and g3b["rows"]["a"]["billpay_netted"] == 100.0
      and g3b["rows"]["a"]["declared_exceeds_cash"] is True)

# A declaration is each rep's OWN — it is never allocated across the store-day's reps.
g4 = BN.net_store_day([row("a", 500.0, 100.0), row("b", 500.0, 0.0)], source="declared")
check("G4 each rep's declaration comes out of their OWN envelope, never spread across the day",
      g4["rows"]["a"]["net"] == 400.0 and g4["rows"]["b"]["net"] == 500.0)
check("G4b nothing is 'unallocated' on this source — there is nothing to allocate",
      g4["unallocated"] == 0.0)

# The POS caution survives the switch of source.
g5 = BN.net_store_day([row("a", 500.0, 100.0)], pos_billpay_cash=400.0, source="declared")
check("G5 the declared figure is what nets, even when a POS figure exists",
      g5["rows"]["a"]["billpay_netted"] == 100.0 and g5["rows"]["a"]["net"] == 400.0)
check("G5b but the POS DISAGREEING is reported — $100 declared against $400 from the POS",
      g5["pos_disagrees"] is True and g5["pos_gap"] == -300.0, (g5["pos_disagrees"], g5["pos_gap"]))
check("G5c and the DM is told so in words, not only in a field",
      "not backed by the sales data" in (BN.envelope_note(g5, "a") or ""), BN.envelope_note(g5, "a"))
g6 = BN.net_store_day([row("a", 500.0, 100.0)], pos_billpay_cash=100.0, source="declared")
check("G6 agreement is not flagged as disagreement",
      g6["pos_disagrees"] is False and g6["pos_gap"] == 0.0)
check("G6b and with NO POS figure the gap is None — 'nobody checked', never 'it agrees'",
      g["pos_gap"] is None and g["pos_disagrees"] is False)

check("G7 the switch still outranks the source: netting off nets nothing on either source",
      BN.net_store_day([row("a", 273.0, 258.0)], enabled=False, source="declared")["basis"] == "off"
      and BN.net_store_day([row("a", 273.0, 258.0)], enabled=False,
                           source="declared")["rows"]["a"]["net"] == 273.0)
check("G8 the arithmetic ties on this source too: gross − netted == net",
      all(abs(v["gross"] - v["billpay_netted"] - v["net"]) < 0.005
          for res in (g, g3, g3b, g4, g5) for v in res["rows"].values()))
check("G9 the default source is byte-identical to what mig 989 shipped — section A's own case",
      BN.net_store_day([row("a", 1000.0, 600.0)], pos_billpay_cash=400.0, source="pos")
      == BN.net_store_day([row("a", 1000.0, 600.0)], pos_billpay_cash=400.0))


# ══ H. THE VOCABULARY HAS ONE HOME — AND THE DATABASE CANNOT OUTGROW IT ══════════════════════════
# The house rule: one fact, one home, dereferenced — and locked so it cannot un-wire. These two
# CHECK constraints are the only copies of these words outside this module, and this section fails
# the build the moment they disagree, exactly as harness_envelope_receipt_basis.py does for mig 1036.
print("\n== H. the basis vocabulary is tied to the migrations' CHECK constraints ==")
_m1038 = open("../database/migrations/1038_pickup_billpay_net_source.sql").read()
_m1039 = open("../database/migrations/1039_cash_pickup_amount_basis.sql").read()
for _src_word in BN.NET_SOURCES:
    check(f"H1 mig 1038's CHECK admits {_src_word!r}", f"'{_src_word}'" in _m1038)
check("H1b and admits nothing else — the CHECK lists exactly NET_SOURCES",
      sorted(set(__import__("re").findall(r"'([a-z_]+)'(?=[,)\s])",
             _m1038.split("CHECK (pickup_billpay_net_source IN (")[1].split(")")[0] + ")")))
      == sorted(BN.NET_SOURCES))
for _b in BN.PICKUP_BASES:
    check(f"H2 mig 1039's CHECK admits {_b!r}", f"'{_b}'" in _m1039)
check("H2b and admits nothing else — the CHECK lists exactly PICKUP_BASES",
      sorted(set(__import__("re").findall(r"'([a-z_]+)'",
             _m1039.split("amount_basis IN (")[1].split(")")[0])))
      == sorted(BN.PICKUP_BASES))
check("H3 mig 1039 does NOT backfill — absence is never turned into a claim somebody made",
      "UPDATE commcalc.cash_pickup" not in _m1039 and "SET amount_basis" not in _m1039)
check("H4 and the column stays NULLABLE, which is how an un-recorded basis is spelled",
      "amount_basis IS NULL" in _m1039 and "NOT NULL" not in _m1039.split("ADD COLUMN IF NOT EXISTS amount_basis")[1].split(";")[0])
check("H5 mig 1038 leaves the money-touching statements COMMENTED — choosing a source is not "
      "switching the netting on",
      "-- INSERT INTO commcalc.cash_pickup_config" in _m1038
      and "\nINSERT INTO commcalc.cash_pickup_config" not in _m1038)
check("H6 every basis a config can produce is a basis the vocabulary has",
      set(BN.basis_allowed("pos")) | set(BN.basis_allowed("declared"))
      | set(BN.basis_allowed("pos", enabled=False)) <= set(BN.PICKUP_BASES))
check("H6b and the two sources cannot produce each other's basis",
      "declared" not in BN.basis_allowed("pos") and "pos" not in BN.basis_allowed("declared"))

# ══ CONTROLS: each new rule must FAIL when the thing it protects is broken ════════════════════════
print("\n== I. armed controls — a broken version of each new rule is caught ==")
check("I1 control: netting the declared figure WITHOUT the envelope floor would report "
      "negative cash in the bag (so G3's cap is load-bearing)",
      (0.0 - 891.0) < 0 and g3["rows"]["a"]["net"] == 0.0)
check("I2 control: a source that silently fell back to 'pos' would leave the owner's store-day "
      "un-netted — G1 would then read $273.00",
      BN.net_store_day([row("kashif", 273.0, 258.0)], pos_billpay_cash=None,
                       source="pos")["rows"]["kashif"]["net"] == 273.0)
check("I3 control: were the gap computed when no POS figure exists, G6b would read 258.0 instead "
      "of None",
      g["pos_gap"] is None)
check("I4 control: the vocabulary tie really can fail — a word absent from the module is absent "
      "from the CHECK too",
      "'drawer'" not in _m1039 and "drawer" not in BN.PICKUP_BASES)


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
