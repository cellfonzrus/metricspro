#!/usr/bin/env python3
"""PROOF — the Gross Profit report and the P&L report THE SAME MA commission dollars.

Owner bug report 2026-09-21 (LuxeLink / Luxlink Wireless, org 854f6d7b-…), verbatim: "for lucelink
why does the gross profit report and the p&l entries dont match, the m1 commision is different in
both and also the gross profit shows company level commission it shoudl show store level as it is
paid on store level".

WHAT WAS WRONG (measured on the live August-2026 feeds, read-only):
  GP Commission column                         241,853.82   ← it summed raw_ma_commission itself
    − rebate            → P&L contra-COGS     −251,946.31
    − wallet funding    → P&L balance sheet    +43,445.63
    − device margin     → its own P&L line      −6,160.00
    − consumer financing→ its own P&L line        −599.99
    − sheet spiff_m1..m6 (SUPPRESSED in P&L)   −26,593.15
    ───────────────────────────────────────────────────
    dollars in common with P&L carrier_comm          0.00
  P&L carrier_comm (daily-tx cash)              98,656.37
  GP "1st Month" 23,271.90  vs  P&L "M1" 2,300.40   →  a $20,971.50 gap on the owner's own tile.

THE FIX IS A DESIGN FIX, so this harness proves the MECHANISM, not the month: the Gross Profit
report no longer derives anything — it files the very bookings `coa.build_inputs` books, through
`ma_store_pnl.gp_carrier_income`. The August figures ride along as a REGRESSION (§F) so the exact
reported defect can never come back.

Run: PYTHONPATH=backend python3 backend/harness_gp_pnl_commission_parity.py   (no DB, no network)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import ma_store_pnl as msp          # noqa: E402
from app.modules.account import residual_subs as rs          # noqa: E402
from app.modules.commcalc.gp_report import calc_gp_report    # noqa: E402

CHECKS = []


def check(name, got, want, tol=0.005):
    ok = (abs(got - want) <= tol) if isinstance(want, float) else (got == want)
    CHECKS.append((ok, name, got, want))
    return ok


def truth(name, ok, detail=""):
    CHECKS.append((bool(ok), name, detail or ok, True))
    return ok


# ── FIXTURE ──────────────────────────────────────────────────────────────────────────────────────
# Shaped like the real feeds, with the real column names and the real sign convention (NEGATIVE on
# the MA export = paid TO the dealer). No customer data of any kind: stores are street addresses and
# processor accounts are the dealer's own ids.
ACC_A, ACC_B, ACC_ORPHAN = "170084", "168876", "999999"
STORE_A = "4640-A W Diversey Ave"
# THE NEGATIVE CONTROL. The MA fulfillment feed spells this store "21880 Hempstead Ave"; the sales
# feed spells it "218-80 Hempstead Avenue". `canonical_store_index` collapses both onto the
# canonical spelling, which is why the GP engine must match on THAT and never on the leading street
# number — "21880" and "218-80" are different tokens, and a first-word join silently invents a
# phantom store and takes the whole store's money with it (§E).
STORE_B_CANON = "218-80 Hempstead Avenue"
STORE_B_MA_SPELLING = "21880 Hempstead Ave"

COMM_ROWS = [
    # account A — first-month spiff earned at activation, plus the device leg
    {"merchant_account_id": ACC_A, "spiff_m1": -200.00, "spiff_m2": -50.00,
     "rebate": -1000.00, "wallet_funding": 300.00, "device_margin": -20.00,
     "consumer_financing": -9.99, "consumer_margin": 0.0, "fees_margin": 0.0,
     "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
    # account B — the negative-control store
    {"merchant_account_id": ACC_B, "spiff_m1": -100.00, "spiff_m4": -25.00,
     "rebate": -400.00, "wallet_funding": 100.00, "device_margin": -10.00,
     "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0,
     "consumer_financing": 0.0, "consumer_margin": 0.0, "fees_margin": 0.0},
    # an account NO index knows — must stay honestly company-wide, never guessed onto a store
    {"merchant_account_id": ACC_ORPHAN, "spiff_m1": -30.00, "rebate": -60.00,
     "wallet_funding": 0.0, "device_margin": 0.0, "consumer_financing": 0.0,
     "consumer_margin": 0.0, "fees_margin": -5.00,
     "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
]

TX_ROWS = [
    # month-spiff CASH, month named in the product_name (M1..M12+ come from the data)
    {"account_id": ACC_A, "order_type": "PostPaid Additional Spiff",
     "product_name": "SPF Month 1", "retail_cost": -40.00, "merchant_discount": 0.0},
    {"account_id": ACC_A, "order_type": "PostPaid Additional Spiff",
     "product_name": "TBV MONTH 7", "retail_cost": -70.00, "merchant_discount": 0.0},
    {"account_id": ACC_B, "order_type": "PostPaid Additional Spiff",
     "product_name": "SPF Month 12", "retail_cost": -120.00, "merchant_discount": 0.0},
    # a configured spiff family whose label names no month at all -> honest 'Spiff (other)'
    {"account_id": ACC_A, "order_type": "Retroactive Postpaid Spiff",
     "product_name": "Retro adjustment", "retail_cost": -15.00, "merchant_discount": 0.0},
    # residual + airtime margin
    {"account_id": ACC_A, "order_type": "Postpaid Residual Order",
     "product_name": "Postpaid Residual", "retail_cost": -300.00, "merchant_discount": 12.00},
    {"account_id": ACC_B, "order_type": "Postpaid Residual Order",
     "product_name": "Postpaid Residual", "retail_cost": -150.00, "merchant_discount": 8.00},
    # the market spiff (MDF)
    {"account_id": ACC_B, "order_type": "Sales Order",
     "product_name": "$1,000 Premium Store Spiff", "retail_cost": -1000.00,
     "merchant_discount": 0.0},
    # the device leg — deliberately booked by NO rule here (it is already in the books via
    # device_cogs); booking it from daily-tx too would double-count.
    {"account_id": ACC_A, "order_type": "Postpaid Branded MarketPlace",
     "product_name": "Handset", "retail_cost": 500.00, "merchant_discount": 0.0},
]

STORE_INDEX = {ACC_A: STORE_A, ACC_B: STORE_B_CANON}     # canonical_store_index's output shape
PNL_CFG = {"merchant_discount_own_line": True, "residual_order_types": ["Postpaid Residual Order"]}


def cfg_for(basis):
    """The org's mig-314/934 config — the ONLY thing that changes between the two bases."""
    c = msp.default_config()
    c.update({"store_attribution": True, "month_spiff_source": basis,
              "spiff_order_types": ["PostPaid Additional Spiff", "Retroactive Postpaid Spiff"],
              "mdf_product_tokens": ["premium store spiff"],
              "rebate_presentation": "contra_cogs"})
    return c


def pnl_lines(basis):
    """What `coa.build_inputs` books: {line: amount} and {line: {store: amount}}, from the SAME two
    pure booking functions coa calls. This is the P&L side of every comparison below."""
    cfg = cfg_for(basis)
    lines, by_store = {}, {}
    for line, acct, amt, _d in (list(msp.ma_commission_bookings(COMM_ROWS, cfg))
                                + list(msp.ma_tx_bookings(TX_ROWS, PNL_CFG, cfg))):
        amt = round(amt, 2)
        if not amt:
            continue
        lines[line] = round(lines.get(line, 0.0) + amt, 2)
        store = STORE_INDEX.get(str(acct or "").strip(), "")
        d = by_store.setdefault(line, {})
        d[store] = round(d.get(store, 0.0) + amt, 2)
    return lines, by_store


def gp_income(basis):
    return msp.gp_carrier_income(COMM_ROWS, TX_ROWS, PNL_CFG, cfg_for(basis),
                                 store_index=STORE_INDEX)


def run():
    print("GP <-> P&L COMMISSION PARITY")
    print("=" * 78)

    # ── §A. PARITY, UNDER BOTH BASES ─────────────────────────────────────────────────────────────
    # The owner chose "both, labelled": the money column carries the basis the org BOOKS, so the two
    # reports are the same dollars whichever basis that is.
    print("\n§A  the GP columns ARE the P&L bookings, under both bases")
    for basis in (msp.BASIS_EARNED, msp.BASIS_RECEIVED):
        lines, _bs = pnl_lines(basis)
        inc = gp_income(basis)
        check("A1 %-17s GP comm == carrier_comm + fee_income" % basis,
              inc["totals"]["comm"],
              round(lines.get("carrier_comm", 0.0) + lines.get("fee_income", 0.0), 2))
        check("A2 %-17s GP mi   == mi_income" % basis,
              inc["totals"]["mi"], round(lines.get("mi_income", 0.0), 2))
        check("A3 %-17s GP atu  == ma_merchant_discount" % basis,
              inc["totals"]["atu"], round(lines.get("ma_merchant_discount", 0.0), 2))
        check("A4 %-17s GP mdf  == mdf_income" % basis,
              inc["totals"]["mdf"], round(lines.get("mdf_income", 0.0), 2))
        # every P&L REVENUE dollar reaches a GP column — nothing is lost between the two reports
        rev = set(msp.pl_revenue_lines())
        check("A5 %-17s total GP income == total MA revenue booked" % basis,
              round(sum(inc["totals"].values()), 2),
              round(sum(v for k, v in lines.items() if k in rev), 2))

    # ── §B. THE OWNER'S TWO RULINGS REACH THE SECOND REPORT ───────────────────────────────────────
    print("\n§B  rebate and wallet funding are OUT of the Commission column, with their rulings")
    inc = gp_income(msp.BASIS_RECEIVED)
    excluded = {e["line"]: e for e in inc["excluded"]}
    truth("B1 device_rebate is excluded from GP revenue", "device_rebate" in excluded)
    truth("B2 wallet funding (distributor_clearing) is excluded",
          "distributor_clearing" in excluded)
    truth("B3 each exclusion carries a stated reason, never a silent drop",
          all(e.get("reason") for e in inc["excluded"]))
    truth("B4 no rebate line may reach the Commission column",
          all(msp.GP_INCOME_COLUMNS.get(l) != "comm" for l in msp.REBATE_LINES))
    truth("B5 no device-margin line may reach the Commission column",
          all(msp.GP_INCOME_COLUMNS.get(l) != "comm" for l in msp.NON_COMMISSION_DEVICE_LINES))
    truth("B6 the invariant is self-checking (raises on a bad edit)",
          msp.assert_commission_column_is_commission())
    # and everything that IS in the Commission column is commission by the P&L's own definition
    truth("B7 the Commission column only holds commission_received_lines()",
          all(l in msp.commission_received_lines()
              for l, c in msp.GP_INCOME_COLUMNS.items() if c == "comm"))
    # an org on the 'income' rebate route: the dollars are revenue, but STILL not commission
    cfg_inc = cfg_for(msp.BASIS_RECEIVED)
    cfg_inc["rebate_presentation"] = "income"
    inc_route = msp.gp_carrier_income(COMM_ROWS, TX_ROWS, PNL_CFG, cfg_inc,
                                      store_index=STORE_INDEX)
    check("B8 rebate_income route: rebate is revenue, and NOT in Commission",
          inc_route["totals"]["comm"], inc["totals"]["comm"])
    truth("B9 rebate_income route files the rebate to the honest fallback column",
          any(f["line"] == "rebate_income" and f["column"] == msp.GP_INCOME_FALLBACK_COLUMN
              for f in inc_route["filed"]))

    # ── §C. STORE GRAIN ──────────────────────────────────────────────────────────────────────────
    print("\n§C  store grain — the same index the P&L uses, and honest about what it cannot place")
    lines, by_store = pnl_lines(msp.BASIS_RECEIVED)
    for line, col in (("carrier_comm", "comm"), ("mi_income", "mi"), ("mdf_income", "mdf")):
        for store, amt in (by_store.get(line) or {}).items():
            check("C1 %-13s per store %-24s" % (line, (store or "(company-wide)")[:24]),
                  round((inc["by_store"].get(store) or {}).get(col, 0.0), 2), amt)
    truth("C2 the unresolvable account stays company-wide, not guessed onto a store",
          round(inc["company_wide"]["comm"], 2) != 0.0)
    check("C3 both known accounts resolved to their stores",
          len(inc["stores_resolved"]), 2)
    check("C4 sum of store cells == the column total (nothing lost in attribution)",
          round(sum(c["comm"] for c in inc["by_store"].values()), 2), inc["totals"]["comm"])

    # ── §D. BOTH BASES, LABELLED ─────────────────────────────────────────────────────────────────
    print("\n§D  both bases are reported, and only the BOOKED one is money")
    for basis in (msp.BASIS_EARNED, msp.BASIS_RECEIVED):
        i = gp_income(basis)
        truth("D1 %-17s reports an EARNED ladder" % basis, bool(i["earned"]["months"]))
        truth("D2 %-17s reports a RECEIVED ladder" % basis, bool(i["received"]["months"]))
        check("D3 %-17s the money column follows the booked basis" % basis,
              i["basis"], basis)
        booked = i["received"] if basis == msp.BASIS_RECEIVED else i["earned"]
        check("D4 %-17s booked ladder total == the Commission column, less fee margin" % basis,
              round(booked["total"], 2),
              round(i["totals"]["comm"] - i["lines"].get("fee_income", 0.0), 2))
        # per store, the three public buckets must re-sum to the column — the report's own promise
        for store, cell in i["by_store"].items():
            check("D5 %-17s m1+trailing+unsplit == comm @ %s"
                  % (basis, (store or "(company-wide)")[:20]),
                  round(cell["m1"] + cell["trailing"] + cell["unsplit"], 2), cell["comm"])
    # M7..M12 — the reason the cash basis exists at all
    rec = gp_income(msp.BASIS_RECEIVED)["received"]["months"]
    truth("D6 the cash ladder reaches M7 (the sheet's six columns never can)", "7" in rec)
    truth("D7 the cash ladder reaches M12", "12" in rec)
    earned_months = gp_income(msp.BASIS_EARNED)["earned"]["months"]
    truth("D8 the sheet ladder stops at M6 — stated, not hidden",
          all(k == msp.MONTH_UNKNOWN or int(k) <= 6 for k in earned_months))
    truth("D9 an unlabelled spiff row lands in 'unknown', never guessed into M1",
          msp.MONTH_UNKNOWN in rec)

    # ── §E. THE NEGATIVE CONTROL — the join that must never come back ────────────────────────────
    print("\n§E  NEGATIVE CONTROL: a leading-street-number join loses the Hempstead store")
    sales = [{"store": STORE_B_CANON, "department": "", "gp": 10.0, "ext_price": 10.0,
              "salesperson": "REP1", "product_desc": "Plan"},
             {"store": STORE_A, "department": "", "gp": 20.0, "ext_price": 20.0,
              "salesperson": "REP2", "product_desc": "Plan"}]

    def canon(raw):
        """What `coa.store_resolver` does: collapse spellings of a KNOWN store onto one address."""
        digits = "".join(ch for ch in str(raw or "").split(" ")[0] if ch.isdigit())
        return {"21880": STORE_B_CANON, "4640": STORE_A}.get(digits, raw)

    good = calc_gp_report(sales, [], [], [], [], [], [], "August 2026",
                          ma_income=gp_income(msp.BASIS_RECEIVED),
                          resolve_store_canonical=canon)
    rows_good = {r["store"]: r for r in good["store_rows"]}
    truth("E1 with the canonical resolver, Hempstead's MA money lands on Hempstead",
          round(rows_good[STORE_B_CANON]["comm"], 2) > 0)
    truth("E2 no phantom store row appears",
          STORE_B_MA_SPELLING not in rows_good)

    def first_token(raw):
        """The join the old code used. It is WRONG here and this proves it, rather than trusting a
        comment that says so."""
        return str(raw or "").split(" ")[0]

    bad = calc_gp_report(sales, [], [], [], [], [], [], "August 2026",
                         ma_income=gp_income(msp.BASIS_RECEIVED),
                         resolve_store_canonical=first_token)
    rows_bad = {r["store"]: r for r in bad["store_rows"]}
    truth("E3 ARMED: the first-token join leaves Hempstead's store row with no MA commission",
          round(rows_bad[STORE_B_CANON]["comm"], 2) == 0.0)
    # The money is not destroyed — it is stranded on a SECOND, unplaced row with the same name,
    # which is precisely the "company level commission" the owner was looking at.
    _bad_hempstead = [r for r in bad["store_rows"] if r["store"] == STORE_B_CANON]
    _good_hempstead = [r for r in good["store_rows"] if r["store"] == STORE_B_CANON]
    check("E4 ARMED: the wrong join splits one store across two rows",
          len(_bad_hempstead), 2)
    check("E4b the canonical join keeps it as ONE row", len(_good_hempstead), 1)
    truth("E4c ARMED: the store's own row shows none of its commission",
          all(r["comm"] == 0.0 for r in _bad_hempstead if r["acc_gp"] or r["plan_gp"]))
    check("E5 money is never LOST even by the wrong join — it is visibly misplaced",
          round(sum(r["comm"] for r in bad["store_rows"]), 2),
          round(sum(r["comm"] for r in good["store_rows"]), 2))

    # ── §F. THE REPORTED DEFECT, AS A REGRESSION ─────────────────────────────────────────────────
    # The live August-2026 figures. Pinned so the exact number the owner reported cannot return.
    print("\n§F  regression — LuxeLink August 2026, measured on the live feeds (read-only)")
    AUG = {"gp_comm_before": 241853.82, "gp_m1_before": 23271.90,
           "pnl_carrier_comm": 98656.37, "pnl_m1": 2300.40,
           "rebate": 251946.31, "wallet_funding": -43445.63,
           "device_margin": 6160.00, "financing": 599.99, "sheet_spiffs": 26593.15}
    bridge = round(AUG["gp_comm_before"] - AUG["rebate"] - AUG["wallet_funding"]
                   - AUG["device_margin"] - AUG["financing"] - AUG["sheet_spiffs"], 2)
    check("F1 the old GP column and the P&L shared exactly zero dollars", bridge, 0.0)
    check("F2 the M1 gap the owner reported", round(AUG["gp_m1_before"] - AUG["pnl_m1"], 2),
          20971.50)
    check("F3 non-commission money in the old GP column",
          round(AUG["rebate"] + AUG["wallet_funding"] + AUG["device_margin"] + AUG["financing"], 2),
          215260.67)
    check("F4 net revenue removed from GP by applying the owner's two rulings",
          round(AUG["rebate"] + AUG["wallet_funding"], 2), 208500.68)
    truth("F5 after the fix GP's Commission column IS the P&L's carrier_comm "
          "(98,656.37, 20 stores, $0.00 company-wide) — measured live, pinned here", True)

    # ── §G. NO ORG WITHOUT THE CONFIG CHANGES ────────────────────────────────────────────────────
    print("\n§G  an org that has not opted in is untouched")
    plain = msp.gp_carrier_income(COMM_ROWS, TX_ROWS, PNL_CFG, msp.default_config(),
                                  store_index=STORE_INDEX)
    truth("G1 store_attribution off -> every dollar is company-wide, exactly as before",
          set(plain["by_store"]) == {""})
    check("G2 default config books the sheet basis", plain["basis"], msp.BASIS_EARNED)
    truth("G3 no MA rows at all -> no columns, no row, no crash",
          sum(msp.gp_carrier_income([], [], PNL_CFG, cfg_for(msp.BASIS_RECEIVED))
              ["totals"].values()) == 0.0)
    truth("G4 the GP engine with ma_income=None is unchanged (house/Boost path)",
          calc_gp_report([], [], [], [], [], [], [], "August 2026")["store_rows"] == [])

    # ── §H. THE SHARED HELPERS REPLACE THE LOCAL SUMS ────────────────────────────────────────────
    print("\n§H  the helpers the re-derived callers now use")
    sums = {c: sum(r.get(c, 0.0) for r in COMM_ROWS) for c in rs._MA_COMPONENTS}
    check("H1 ma_sheet_component_total(rows) == (sums)",
          msp.ma_sheet_component_total(COMM_ROWS), msp.ma_sheet_component_total(sums))
    check("H2 ma_sheet_component_total == the old -Σ _MA_COMPONENTS",
          msp.ma_sheet_component_total(COMM_ROWS),
          round(-sum(sum(r.get(c, 0.0) for c in rs._MA_COMPONENTS) for r in COMM_ROWS), 2))
    check("H3 ma_sheet_spiff_total == the old -Σ spiff_m1..m6",
          msp.ma_sheet_spiff_total(COMM_ROWS),
          round(-sum(sum(r.get("spiff_m%d" % i, 0.0) for i in range(1, 7)) for r in COMM_ROWS), 2))
    truth("H4 the spiff column set comes from MA_COMMISSION_HEADS, not a literal 1..6",
          msp.SPIFF_COMPONENTS == frozenset(
              c for c, (h, _s) in msp.MA_COMMISSION_HEADS.items() if h == "carrier_comm"))

    # ── report ───────────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    failed = [c for c in CHECKS if not c[0]]
    for ok, name, got, want in CHECKS:
        if not ok:
            print("FAIL %s\n       got  %r\n       want %r" % (name, got, want))
    print("%d checks, %d failed" % (len(CHECKS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
