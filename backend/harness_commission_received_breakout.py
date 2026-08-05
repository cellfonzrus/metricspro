"""HARNESS — Commission Received breakout (owner directive 2026-08-05).

    "we need to see what we made in M1 and other months and how much is on ATU and how much is on
     residual."

WHAT THIS PROVES (each assertion is one of the failure modes that has actually bitten this module):

  A. SUM IDENTITY, per stream and per period — every ladder re-sums to its own row total. This is the
     discipline whose ABSENCE let the 2026-08-05 M1 misclassification live for a day: m1+trailing+
     unsplit held the whole time because both readings iterated the same column list, so the identity
     alone is not enough — see (C).
  B. RECONCILIATION TO THE REAL GP REPORT. The breakout is driven from the SAME fixtures as
     `gp_report.calc_gp_report`, and the commission / MI / ATU rows are asserted EQUAL to that
     function's own `totals`. If a future edit makes the breakout show money the GP report doesn't,
     this fails.
  C. LEG-CLASSIFIER vs LEDGER AGREEMENT — the invariant the m1 fix introduced: for all twelve
     raw_ma_commission columns, `commission_legs.ma_field_leg` must agree with
     `ledger_ma_sync.DEFAULT_COMPONENTS[…]['payment_month']`. Re-asserted here so this package cannot
     regress it.
  D. THE M1 RULING HOLDS — M1 == spiff_m1 alone; the six activation-order margins land in Unsplit and
     are NAMED. (Owner ruling 2026-08-05, binding.)
  E. THE ePay/VidaPay DOUBLE-COUNT GATE — a month that has ePay Payment Detail does NOT also take the
     VidaPay commission rows on top.
  F. THE VIDAPAY RESIDUAL DIVERGENCE is reported, not reconciled: airtime_all (the GP basis),
     residual_orders (the ma-overview-recon / What-If basis) and the OVERLAP between them are three
     distinct figures, and the residual-orders row is in NO total.
  G. MI/ATU per-leg split — migration 274's `commission_leg_mi_rollup` shape, wired for the first
     time; `mi_split_by_activation=false` forces everything honest-unsplit.
  H. RULE FIVE filters really filter, and company-wide carrier money is excluded (not silently kept)
     when a store/market filter is active.
  I. NOTHING MOVES A PAYOUT — the module has no writes and no calc imports.

Run: python3 harness_commission_received_breakout.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_received as CR
from app.modules.commcalc import commission_legs as LEGS
from app.modules.commcalc import ledger_ma_sync as LMS
from app.modules.commcalc.gp_report import calc_gp_report

OK = FAIL = 0
FAILURES = []


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} {extra}".strip())
        print(f"  FAIL  {name} {extra}")


def close(a, b, tol=0.011):
    return abs(round(float(a), 2) - round(float(b), 2)) <= tol


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────────
PERIODS = ["April 2026", "May 2026", "June 2026"]
PKEY = {}
for _p in PERIODS:
    y, mname = _p.split()[1], _p.split()[0]
    mnum = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
            "October", "November", "December"].index(mname) + 1
    PKEY[_p] = _p
    PKEY[f"{y}-{mnum:02d}"] = _p

MA_COMPONENTS = ["device_margin", "consumer_margin", "consumer_financing", "rebate",
                 "wallet_funding", "fees_margin",
                 "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6"]

# VidaPay commission — the export posts money paid TO the dealer as NEGATIVE.
MA_ROWS = [
    {"period": "June 2026", "n": 998,
     "device_margin": -4700.0, "consumer_margin": 0.0, "consumer_financing": -1200.0,
     "rebate": -60000.0, "wallet_funding": -25000.0, "fees_margin": -5100.0,
     "spiff_m1": -28000.0, "spiff_m2": -9000.0, "spiff_m3": -6000.0,
     "spiff_m4": -3000.0, "spiff_m5": -1500.0, "spiff_m6": -900.0},
    {"period": "2026-05", "n": 900,
     "device_margin": -3900.0, "consumer_margin": 0.0, "consumer_financing": -800.0,
     "rebate": -52000.0, "wallet_funding": -21000.0, "fees_margin": -4400.0,
     "spiff_m1": -24000.0, "spiff_m2": -8000.0, "spiff_m3": -5000.0,
     "spiff_m4": -2500.0, "spiff_m5": -1200.0, "spiff_m6": -700.0},
]
MARGIN_COLS = ["rebate", "device_margin", "consumer_margin", "consumer_financing",
               "wallet_funding", "fees_margin"]

# VidaPay daily transactions (mig 278 rollup shape). residual_orders arrives sign-APPLIED.
TX_ROWS = [
    {"period": "June 2026", "airtime_all": 14000.0, "airtime_residual_orders": 3100.0,
     "residual_orders": 41000.0, "n": 52000, "n_residual": 9000},
    {"period": "2026-05", "airtime_all": 12500.0, "airtime_residual_orders": 2800.0,
     "residual_orders": 38000.0, "n": 48000, "n_residual": 8500},
]

# ePay Payment Detail + Comprehensive Comp (mig 274 rollup shape)
LABEL_ROWS = [
    {"source": "payment_detail", "period": "April 2026", "store_num": "100",
     "label": "New Activation Bounty - Month 1", "category": "Commission", "amount": 5000.0, "n": 50},
    {"source": "payment_detail", "period": "April 2026", "store_num": "100",
     "label": "New Activation Bounty - Month 2", "category": "Commission", "amount": 2000.0, "n": 40},
    {"source": "payment_detail", "period": "April 2026", "store_num": "200",
     "label": "New Activation Bounty - Month 3", "category": "Commission", "amount": 1500.0, "n": 30},
    {"source": "payment_detail", "period": "April 2026", "store_num": "200",
     "label": "Boost Auto Top-Up", "category": "Commission", "amount": 700.0, "n": 12},
    # not Commission category -> must be ignored entirely
    {"source": "payment_detail", "period": "April 2026", "store_num": "100",
     "label": "2026 SIM card reimbursement", "category": "Re-imbursement", "amount": 9999.0, "n": 5},
    {"source": "comp_report", "period": "April 2026", "store_num": "100",
     "label": "Device Upgrade Bounty - Month 1", "category": "", "amount": 800.0, "n": 9},
    {"source": "comp_report", "period": "April 2026", "store_num": "100",
     "label": "2026 SIM card reimbursement", "category": "", "amount": 400.0, "n": 3},
]

# ePay MI/ATU (mig 274 commission_leg_mi_rollup shape)
MI_ROWS = [
    {"period": "April 2026", "salesforce_id": "SF1", "leg_month": 1, "mi": 900.0, "atu": 300.0, "n": 120},
    {"period": "April 2026", "salesforce_id": "SF1", "leg_month": 4, "mi": 2400.0, "atu": 800.0, "n": 500},
    {"period": "April 2026", "salesforce_id": "SF2", "leg_month": 2, "mi": 1100.0, "atu": 250.0, "n": 210},
    {"period": "April 2026", "salesforce_id": "SF2", "leg_month": None, "mi": 150.0, "atu": 40.0, "n": 22},
]

STORE_IDX = {"100": {"store": "100 Main St", "store_code": "S100", "market": "NY"},
             "200": {"store": "200 Oak Ave", "store_code": "S200", "market": "NJ"}}
SFID_IDX = {"SF1": STORE_IDX["100"], "SF2": STORE_IDX["200"]}


def passes(num, markets=(), stores=()):
    ent = STORE_IDX.get(num)
    if not markets and not stores:
        return True
    if not ent:
        return False
    if markets and (ent.get("market") or "").upper() not in markets:
        return False
    if stores and not any(s in (ent.get("store") or "") or s == ent.get("store_code") for s in stores):
        return False
    return True


def passes_sfid(sf, markets=(), stores=()):
    ent = SFID_IDX.get(sf)
    if not markets and not stores:
        return True
    if not ent:
        return False
    if markets and (ent.get("market") or "").upper() not in markets:
        return False
    if stores and not any(s in (ent.get("store") or "") or s == ent.get("store_code") for s in stores):
        return False
    return True


def comp_is_commission(label):
    ct = str(label or "").lower()
    return not ("reimbursement" in ct or "rebate" in ct or "mdf" in ct)


def build(legcls=None, markets=(), stores=(), skip=(), **kw):
    legcls = legcls or LEGS.default_classifier()
    return CR.build_breakout(
        PERIODS, legcls,
        label_rows=kw.get("label_rows", LABEL_ROWS),
        ma_rows=kw.get("ma_rows", MA_ROWS),
        mi_rows=kw.get("mi_rows", MI_ROWS),
        tx_rows=kw.get("tx_rows", TX_ROWS),
        components=MA_COMPONENTS, period_key=PKEY,
        passes=lambda n: passes(n, markets, stores),
        passes_sfid=lambda s: passes_sfid(s, markets, stores),
        comp_is_commission=comp_is_commission,
        skip_periods=skip)


def stream(out, key):
    return next((s for s in out["streams"] if s["key"] == key), None)


print("=" * 100)
print("HARNESS — Commission Received breakout (M1 · M2…M6 · ATU · MI/residual)")
print("=" * 100)

# ═══ A. SUM IDENTITY ══════════════════════════════════════════════════════════════════════════════
print("\nA. SUM IDENTITY — every ladder re-sums to its own row total, per period and overall")
out = build()
chk("A1 identity_ok", out["identity_ok"], str(out["identity"])[:200])
for s in out["streams"]:
    ssum = round(sum(s["legs"].values()), 2)
    chk(f"A2 {s['key']} legs re-sum to stream total", close(ssum, s["total"]),
        f"legs={ssum} total={s['total']}")
    chk(f"A3 {s['key']} m1+m2_12+unsplit == total",
        close(s["m1"] + s["m2_12"] + s["unsplit"], s["total"]))
    psum = round(sum(v["total"] for v in s["periods"].values()), 2)
    chk(f"A4 {s['key']} periods re-sum to stream total", close(psum, s["total"]))
for g in out["groups"]:
    gsum = round(sum(st["total"] for st in out["streams"]
                     if st["group"] == g["group"] and st["in_total"]), 2)
    chk(f"A5 group {g['group']} total", close(gsum, g["total"]))
    chk(f"A6 group {g['group']} legs re-sum", close(sum(g["legs"].values()), g["total"]))

# ═══ B. RECONCILIATION TO THE REAL GP REPORT ══════════════════════════════════════════════════════
print("\nB. RECONCILIATION — the REAL gp_report.calc_gp_report, same fixtures, same figures")

# B-i: an MA-fed (Total/VidaPay) org — GP books MA commission into `comm` and merchant_discount into `atu`.
legcls = LEGS.default_classifier()
ma_sums = {c: sum(r.get(c, 0.0) for r in MA_ROWS if PKEY.get(r["period"]) == "June 2026")
           for c in MA_COMPONENTS}
ma_comm = round(-sum(ma_sums[c] for c in MA_COMPONENTS), 2)
gp_ma = calc_gp_report(
    sales=[], pay_detail=[], mi_rows=[], rep_commissions=[], expenses=[], catalog=[],
    store_mapping=[], period="June 2026",
    ma_income={"comm": ma_comm, "atu": 14000.0, "components": ma_sums,
               "component_list": list(MA_COMPONENTS)},
    leg_classify=legcls)
gt = gp_ma["totals"]
out_ma = build(ma_rows=[r for r in MA_ROWS if PKEY.get(r["period"]) == "June 2026"],
               tx_rows=[r for r in TX_ROWS if PKEY.get(r["period"]) == "June 2026"],
               label_rows=[], mi_rows=[])
s_comm = stream(out_ma, "comm_ma")
s_air = stream(out_ma, "ma_airtime")
chk("B1 VidaPay commission total == GP Commission column", close(s_comm["total"], gt["comm"]),
    f"{s_comm['total']} vs {gt['comm']}")
chk("B2 VidaPay M1 == GP comm_m1", close(s_comm["m1"], gt["comm_m1"]),
    f"{s_comm['m1']} vs {gt['comm_m1']}")
chk("B3 VidaPay M2–M12 == GP comm_m2_12", close(s_comm["m2_12"], gt["comm_m2_12"]))
chk("B4 VidaPay Unsplit == GP comm_unsplit", close(s_comm["unsplit"], gt["comm_unsplit"]))
chk("B5 airtime row == GP ATU column", close(s_air["total"], gt["atu"]),
    f"{s_air['total']} vs {gt['atu']}")
# the per-leg ladder must equal the GP card's own ladder for the same money
gp_ladder = (gp_ma.get("commission_legs") or {}).get("ladder", {}).get("comm", {})
for k, v in gp_ladder.items():
    kk = "unsplit" if k in (None, "unknown", "unsplit") else str(k)
    chk(f"B6 ladder rung {kk} matches GP", close(s_comm["legs"].get(kk, 0.0), v),
        f"{s_comm['legs'].get(kk)} vs {v}")

# B-ii: an ePay (Boost) org — GP books raw_mi into `mi`/`atu` and payment detail into `comm`.
gp_mi_rows = [
    {"salesforce_id": "SF1", "actual_mi_payout": 900.0, "actual_atu_payout": 300.0,
     "mi_activation_date": "2026-04-05"},
    {"salesforce_id": "SF1", "actual_mi_payout": 2400.0, "actual_atu_payout": 800.0,
     "mi_activation_date": "2026-01-11"},
    {"salesforce_id": "SF2", "actual_mi_payout": 1100.0, "actual_atu_payout": 250.0,
     "mi_activation_date": "2026-03-02"},
    {"salesforce_id": "SF2", "actual_mi_payout": 150.0, "actual_atu_payout": 40.0,
     "mi_activation_date": ""},
]
gp_pd = [{"business_address": "100 Main St", "payment_type": "New Activation Bounty - Month 1",
          "amount": 5000.0, "category": "Commission"},
         {"business_address": "100 Main St", "payment_type": "New Activation Bounty - Month 2",
          "amount": 2000.0, "category": "Commission"},
         {"business_address": "200 Oak Ave", "payment_type": "New Activation Bounty - Month 3",
          "amount": 1500.0, "category": "Commission"},
         {"business_address": "200 Oak Ave", "payment_type": "Boost Auto Top-Up",
          "amount": 700.0, "category": "Commission"},
         {"business_address": "100 Main St", "payment_type": "2026 SIM card reimbursement",
          "amount": 9999.0, "category": "Re-imbursement"}]
gp_sm = [{"store_address": "100 Main St", "salesforce_id": "SF1", "market": "NY", "store_code": "S100"},
         {"store_address": "200 Oak Ave", "salesforce_id": "SF2", "market": "NJ", "store_code": "S200"}]
gp_epay = calc_gp_report(sales=[], pay_detail=gp_pd, mi_rows=gp_mi_rows, rep_commissions=[],
                         expenses=[], catalog=[], store_mapping=gp_sm, period="April 2026",
                         comp_rows=[{"business_address": "100 Main St",
                                     "compensation_type": "Device Upgrade Bounty - Month 1",
                                     "payment_amount": 800.0},
                                    {"business_address": "100 Main St",
                                     "compensation_type": "2026 SIM card reimbursement",
                                     "payment_amount": 400.0}],
                         leg_classify=legcls)
et = gp_epay["totals"]
out_ep = build(ma_rows=[], tx_rows=[])
s_ep = stream(out_ep, "comm_epay")
s_mi = stream(out_ep, "mi")
s_atu = stream(out_ep, "atu")
s_cc = stream(out_ep, "comp_comm")
chk("B7 ePay commission total == GP Commission column", close(s_ep["total"], et["comm"]),
    f"{s_ep['total']} vs {et['comm']}")
chk("B8 ePay M1 == GP comm_m1", close(s_ep["m1"], et["comm_m1"]))
chk("B9 ePay M2–M12 == GP comm_m2_12", close(s_ep["m2_12"], et["comm_m2_12"]))
chk("B10 ePay Unsplit == GP comm_unsplit", close(s_ep["unsplit"], et["comm_unsplit"]))
chk("B11 MI total == GP MI column", close(s_mi["total"], et["mi"]), f"{s_mi['total']} vs {et['mi']}")
chk("B12 MI M1 == GP mi_m1", close(s_mi["m1"], et["mi_m1"]), f"{s_mi['m1']} vs {et['mi_m1']}")
chk("B13 MI M2–M12 == GP mi_m2_12", close(s_mi["m2_12"], et["mi_m2_12"]))
chk("B14 MI Unsplit == GP mi_unsplit", close(s_mi["unsplit"], et["mi_unsplit"]))
chk("B15 ATU total == GP ATU column", close(s_atu["total"], et["atu"]), f"{s_atu['total']} vs {et['atu']}")
chk("B16 ATU M1 == GP atu_m1", close(s_atu["m1"], et["atu_m1"]))
chk("B17 ATU M2–M12 == GP atu_m2_12", close(s_atu["m2_12"], et["atu_m2_12"]))
chk("B18 ATU Unsplit == GP atu_unsplit", close(s_atu["unsplit"], et["atu_unsplit"]))
chk("B19 Comp Comm total == GP comp_comm column", close(s_cc["total"], et["comp_comm"]),
    f"{s_cc['total']} vs {et['comp_comm']}")
chk("B20 Comp Comm M1 == GP comp_comm_m1", close(s_cc["m1"], et["comp_comm_m1"]))
chk("B21 Comp Comm is NOT in the Commission group total",
    all(g["total"] != s_cc["total"] or g["group"] != CR.G_COMMISSION for g in out_ep["groups"])
    or next(g for g in out_ep["groups"] if g["group"] == CR.G_COMMISSION)["total"] == s_ep["total"])
# the GP report's own per-source ladder must match ours, rung for rung
for src_key, mine in (("mi", s_mi), ("atu", s_atu), ("comm", s_ep), ("comp_comm", s_cc)):
    for k, v in ((gp_epay.get("commission_legs") or {}).get("ladder", {}).get(src_key, {}) or {}).items():
        kk = "unsplit" if k in (None, "unknown", "unsplit") else str(k)
        chk(f"B22 {src_key} rung {kk} matches GP", close(mine["legs"].get(kk, 0.0), v),
            f"{mine['legs'].get(kk)} vs {v}")

# ═══ C. LEG CLASSIFIER vs LEDGER (the invariant the M1 fix introduced) ════════════════════════════
print("\nC. LEG CLASSIFIER vs LEDGER — payment_month agreement on all twelve MA columns")
for comp in LMS.DEFAULT_COMPONENTS["ma_commission"]:
    col, pm = comp["col"], comp["payment_month"]
    bucket, leg = LEGS.ma_field_leg(col)
    if pm is None:
        chk(f"C1 {col} -> unsplit (ledger says no payment month)", bucket == LEGS.UNSPLIT,
            f"got {bucket}/{leg}")
    elif pm == 1:
        chk(f"C2 {col} -> m1 leg 1", bucket == LEGS.M1 and leg == 1, f"got {bucket}/{leg}")
    else:
        chk(f"C3 {col} -> trailing leg {pm}", bucket == LEGS.TRAILING and leg == pm,
            f"got {bucket}/{leg}")
# and the breakout's own ladder must place each column on the ledger's month
ma_only = build(label_rows=[], mi_rows=[], tx_rows=[])
sc_ma = stream(ma_only, "comm_ma")
for comp in LMS.DEFAULT_COMPONENTS["ma_commission"]:
    col, pm = comp["col"], comp["payment_month"]
    raw = -sum(r.get(col, 0.0) for r in MA_ROWS)
    if pm is not None and round(raw, 2):
        chk(f"C4 breakout rung M{pm} contains {col}", close(sc_ma["legs"].get(str(pm), 0.0), raw),
            f"rung={sc_ma['legs'].get(str(pm))} col={round(raw, 2)}")

# ═══ D. THE OWNER'S M1 RULING ═════════════════════════════════════════════════════════════════════
print("\nD. M1 == spiff_m1 alone; the six margins land in Unsplit and are NAMED")
m1_expected = round(-sum(r.get("spiff_m1", 0.0) for r in MA_ROWS), 2)
margins = round(-sum(r.get(c, 0.0) for r in MA_ROWS for c in MARGIN_COLS), 2)
chk("D1 M1 == Σ spiff_m1", close(sc_ma["m1"], m1_expected), f"{sc_ma['m1']} vs {m1_expected}")
chk("D2 Unsplit == Σ the six margins", close(sc_ma["unsplit"], margins),
    f"{sc_ma['unsplit']} vs {margins}")
named = set((sc_ma.get("meta") or {}).get("unsplit_fields") or [])
chk("D3 the six margin columns are NAMED on the row", named == set(MARGIN_COLS), str(sorted(named)))
for n in range(2, 7):
    exp = round(-sum(r.get(f"spiff_m{n}", 0.0) for r in MA_ROWS), 2)
    chk(f"D4 M{n} is its OWN line == Σ spiff_m{n}", close(sc_ma["legs"].get(str(n), 0.0), exp),
        f"{sc_ma['legs'].get(str(n))} vs {exp}")
chk("D5 leg_columns exposes M1..M6 individually",
    [c for c in ma_only["leg_columns"] if c <= 6] == [1, 2, 3, 4, 5, 6], str(ma_only["leg_columns"]))
# an org that PUTS the margins back in M1 gets them back (RULE TWO — still config)
cfg_back = dict(LEGS.DEFAULT_CFG); cfg_back["ma_m1_fields"] = list(MARGIN_COLS)
back = build(legcls=LEGS.LegClassifier(cfg_back), label_rows=[], mi_rows=[], tx_rows=[])
sb_ma = stream(back, "comm_ma")
chk("D6 config puts the margins back into M1", close(sb_ma["m1"], m1_expected + margins))
chk("D7 …and the TOTAL is unchanged either way", close(sb_ma["total"], sc_ma["total"]))

# ═══ E. THE ePay / VidaPay DOUBLE-COUNT GATE ══════════════════════════════════════════════════════
print("\nE. an ePay month does NOT also take the VidaPay commission rows on top")
ma_june = [dict(r, period="April 2026") for r in MA_ROWS[:1]]
with_gate = build(ma_rows=ma_june, skip=("April 2026",))
without = build(ma_rows=ma_june, skip=())
chk("E1 gated: no VidaPay commission row for the ePay month",
    (stream(with_gate, "comm_ma") or {"periods": {}}).get("periods", {}).get("April 2026",
                                                                             {"total": 0})["total"] == 0
    if stream(with_gate, "comm_ma") else True)
chk("E2 ungated would have added it (the gate is doing real work)",
    stream(without, "comm_ma") is not None
    and stream(without, "comm_ma")["periods"]["April 2026"]["total"] != 0)

# ═══ F. THE VIDAPAY RESIDUAL DIVERGENCE ═══════════════════════════════════════════════════════════
print("\nF. VidaPay residual divergence is REPORTED, never silently reconciled")
s_air_all = stream(out, "ma_airtime")
s_res = stream(out, "ma_residual_orders")
chk("F1 airtime row == Σ airtime_all", close(s_air_all["total"], sum(r["airtime_all"] for r in TX_ROWS)))
chk("F2 residual-orders row == Σ residual_orders",
    close(s_res["total"], sum(r["residual_orders"] for r in TX_ROWS)))
chk("F3 residual-orders row is a REFERENCE row, in NO total", s_res["in_total"] is False)
chk("F4 residual orders are NOT inside the Residual & airtime group total",
    close(next(g for g in out["groups"] if g["group"] == CR.G_RESIDUAL)["total"],
          round(sum(st["total"] for st in out["streams"]
                    if st["group"] == CR.G_RESIDUAL and st["in_total"]), 2)))
chk("F5 the overlap between the two definitions is named",
    close((s_air_all.get("meta") or {}).get("airtime_on_residual_orders", 0),
          sum(r["airtime_residual_orders"] for r in TX_ROWS)))
chk("F6 divergence is stated in plain English", "Postpaid Residual Orders" in out["divergence_note"]
    and "left out of every total" in out["divergence_note"])
chk("F7 airtime + residual orders both land wholly in unsplit (no guessed month)",
    close(s_air_all["unsplit"], s_air_all["total"]) and close(s_res["unsplit"], s_res["total"]))

# ═══ G. MI/ATU per-leg split ══════════════════════════════════════════════════════════════════════
print("\nG. MI and ATU split by month-of-life; unparseable activation dates stay honest-unsplit")
chk("G1 MI M1 rung", close(s_mi["legs"].get("1", 0), 900.0))
chk("G2 MI M4 rung", close(s_mi["legs"].get("4", 0), 2400.0))
chk("G3 MI M2 rung", close(s_mi["legs"].get("2", 0), 1100.0))
chk("G4 MI unsplit rung (no activation date)", close(s_mi["legs"].get("unsplit", 0), 150.0))
chk("G5 ATU M1 rung", close(s_atu["legs"].get("1", 0), 300.0))
chk("G6 ATU unsplit rung", close(s_atu["legs"].get("unsplit", 0), 40.0))
cfg_off = dict(LEGS.DEFAULT_CFG); cfg_off["mi_split_by_activation"] = False
off = build(legcls=LEGS.LegClassifier(cfg_off))
o_mi, o_atu = stream(off, "mi"), stream(off, "atu")
chk("G7 split OFF -> ALL MI unsplit", close(o_mi["unsplit"], o_mi["total"]) and o_mi["total"] != 0)
chk("G8 split OFF -> ALL ATU unsplit", close(o_atu["unsplit"], o_atu["total"]))
chk("G9 split OFF changes no total", close(o_mi["total"], s_mi["total"])
    and close(o_atu["total"], s_atu["total"]))

# ═══ H. RULE FIVE filters ═════════════════════════════════════════════════════════════════════════
print("\nH. RULE FIVE — store/market filters really filter, company-wide money is excluded")
ny = build(markets={"NY"})
ny_comm = stream(ny, "comm_epay")
chk("H1 NY-only commission == the two 100-Main-St labels", close(ny_comm["total"], 7000.0),
    str(ny_comm["total"]))
chk("H2 NY-only MI == SF1 rows only", close(stream(ny, "mi")["total"], 3300.0))
chk("H3 NY-only ATU == SF1 rows only", close(stream(ny, "atu")["total"], 1100.0))
s200 = build(stores=["S200"])
chk("H4 store S200 commission == the two 200-Oak-Ave labels",
    close(stream(s200, "comm_epay")["total"], 2200.0))
chk("H5 unfiltered commission == every label", close(stream(out, "comm_epay")["total"], 9200.0))
chk("H6 filtered totals are <= unfiltered (a filter never adds money)",
    stream(ny, "comm_epay")["total"] <= stream(out, "comm_epay")["total"])
# the caller (router) is what drops company-wide MA money under a filter; prove the shape honours it
noma = build(markets={"NY"}, ma_rows=[], tx_rows=[])
chk("H7 with MA excluded there is no VidaPay row at all", stream(noma, "comm_ma") is None
    and stream(noma, "ma_airtime") is None)

# ═══ I. NOTHING MOVES A PAYOUT ════════════════════════════════════════════════════════════════════
print("\nI. money safety — the module cannot write, pay or recompute")
srcpath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "app/modules/commcalc/commission_received.py")
srctext = open(srcpath, encoding="utf-8").read()
for forbidden in ("insert(", "update(", "upsert(", "delete(", "rep_commissions", "_run_calculation",
                  "calc_rep_commissions", "commission_engine", "plan_pay_gate", "get_supabase"):
    chk(f"I1 no '{forbidden}' in commission_received.py", forbidden not in srctext)
chk("I2 no DB client import", "from app.core.database" not in srctext)
chk("I3 pure — imports nothing from the app at module level",
    all(not l.startswith(("import app", "from app")) for l in srctext.splitlines()))

# ═══ J. degenerate inputs never 500 ═══════════════════════════════════════════════════════════════
print("\nJ. degenerate inputs")
empty = CR.build_breakout([], LEGS.default_classifier())
chk("J1 no periods -> empty, identity ok", empty["streams"] == [] and empty["identity_ok"])
nulls = build(label_rows=[{"source": "payment_detail", "period": "nope", "store_num": "",
                           "label": None, "category": "Commission", "amount": None, "n": None}],
              ma_rows=[{"period": None}], mi_rows=[{"period": "", "leg_month": "x"}],
              tx_rows=[{"period": "zzz"}])
chk("J2 unknown periods / null amounts are dropped, not crashed", nulls["identity_ok"])
weird = build(mi_rows=[{"period": "June 2026", "salesforce_id": "SF1", "leg_month": -3,
                        "mi": 10.0, "atu": 5.0, "n": 1}], label_rows=[], ma_rows=[], tx_rows=[])
chk("J3 a negative leg month is honest-unsplit, never a guessed rung",
    close(stream(weird, "mi")["legs"].get("unsplit", 0), 10.0))

print("\n" + "=" * 100)
print(f"RESULT: {OK} passed, {FAIL} failed")
if FAILURES:
    print("\nFAILURES:")
    for f in FAILURES:
        print("  -", f)
print("=" * 100)
sys.exit(1 if FAIL else 0)
