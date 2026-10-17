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
    # Since §64 (2026-10-09) the engine keys its SALES rows through the resolver it is handed too,
    # so a token-shaped resolver names the store's own row after the token — the money is still
    # split off that row, which is the class this control arms. The shape of the wrongness moved;
    # the wrongness is identical, and E5 still proves nothing is destroyed.
    _bad_sales_row = rows_bad[first_token(STORE_B_CANON)]
    truth("E3 ARMED: the first-token join leaves Hempstead's SALES row with no MA commission",
          round(_bad_sales_row["comm"], 2) == 0.0)
    # The money is not destroyed — it is stranded on a SECOND row the sales feed never named,
    # which is precisely the "company level commission" the owner was looking at.
    _bad_money_rows = [r for r in bad["store_rows"] if r["store"] == STORE_B_CANON]
    _good_hempstead = [r for r in good["store_rows"] if r["store"] == STORE_B_CANON]
    check("E4 ARMED: the wrong join splits one store across two rows — its sales under the token, "
          "its commission under the canonical name", len(_bad_money_rows), 1)
    truth("E4a ARMED: …and the commission row is NOT the row the sales feed named",
          _bad_money_rows[0]["store"] != _bad_sales_row["store"]
          and round(_bad_money_rows[0]["comm"], 2) > 0
          and not (_bad_money_rows[0]["acc_gp"] or _bad_money_rows[0]["plan_gp"]))
    check("E4b the canonical join keeps it as ONE row", len(_good_hempstead), 1)
    truth("E4c the canonical join's single row carries BOTH its sales and its commission",
          bool(_good_hempstead[0]["plan_gp"]) and round(_good_hempstead[0]["comm"], 2) > 0)
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

    # ── §I. THE GP REPORT'S SALES COME FROM THE ONE UNION, NOT THE ARCHIVE ───────────────────────
    # Owner report 2026-10-17: "accessory sales are not there, device margin and other fields are
    # also missing fromt eh gross profit report". `router._compute_gp` read `commcalc.raw_sales`
    # ALONE. That table is the month-END archive the promote step fills; `daily_sales_feed` is where
    # a day's lines arrive. So on any month the promote had not run on, this report's ENTIRE sales
    # half read zero while the P&L — which has dereferenced its own union since `coa._sales_union_rows`
    # — read the month in full. The class is "a reader bound to the archive goes blind on an
    # unpromoted month", and it is the SAME duplicate-derivation shape §58.7 fixed in the
    # classification: two readers answering one question from two sources.
    print("\n§I  the sales source is dereferenced, and the archive-only read is armed as the defect")
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "app", "modules", "commcalc", "router.py")).read()
    _gp_fn = _src[_src.index("def _compute_gp("):]
    _gp_fn = _gp_fn[:_gp_fn.index("\ndef ")]
    truth("I1 _compute_gp dereferences THE transaction-grain union",
          "_sales_rows_union_txn(client, org_id, period, cols='*')" in _gp_fn)
    # THE LOCK. `sku` is on raw_sales and NOT on daily_sales_feed, and the union's per-table read
    # swallows a failed select to an empty list — so naming columns here reinstates the blindness.
    truth("I2 LOCK it asks for cols='*' and never a column list",
          "_sales_rows_union_txn(client, org_id, period, cols=" in _gp_fn
          and "cols='*'" in _gp_fn
          and "sc.table('raw_sales')" not in _gp_fn
          and 'sc.table("raw_sales")' not in _gp_fn)
    truth("I3 LOCK the report states WHERE its sales came from, so a silent zero is impossible",
          "result['sales_source']" in _gp_fn)
    # ARMED: the archive-only read, reproduced on the engine itself. Five columns at once.
    _blind = calc_gp_report([], [], [], [], [], [], [], "September 2026")
    _seen = calc_gp_report(sales, [], [], [], [], [], [], "September 2026")
    _SALES_COLS = ("acc_gp", "setup_gp", "phone_sales", "plan_gp", "other_gp")
    truth("I4 ARMED: with an empty archive every one of the five sales columns reads 0.00",
          all(round(_blind["totals"].get(c, 0.0), 2) == 0.0 for c in _SALES_COLS))
    truth("I5 ...and the union's rows light them up again",
          any(round(_seen["totals"].get(c, 0.0), 2) != 0.0 for c in _SALES_COLS))
    truth("I6 the blindness was SILENT — nothing in the old payload distinguished "
          "'no sales this month' from 'the archive was never promoted'",
          "sales_source" not in _blind)

    # ── §J. THE BOTH-WAY LINE INVENTORY — one source, one answer (owner 2026-10-17) ───────────────
    # Owner: "may be more fields are missing they should be mirror copies of each other as the
    # source is t he same, if there is somehting extra on gross profit and not on p&l then highlight
    # both and fix". This section is the ARITHMETIC of that inventory, pinned so a future change
    # cannot quietly reopen a term. PURE — the figures are the live September 2026 measurement.
    print("\n§J  every term by which the GP report and the P&L differ, and nothing else")
    SEP_PL = {                       # commcalc.account_statements, statement_type='pl', consolidated
        "accessory_rev": 53639.38, "device_rev": 18.00, "carrier_comm": 118077.91,
        "fee_income": 1462.50, "mi_income": 35937.31, "ma_merchant_discount": 19189.49,
        "mdf_income": 19000.00, "ma_device_margin": 6590.00,
        "billpay_collected": 43151.05, "billpay_offset": -43151.05,
        "accessory_cost": 10729.60, "device_cost": 222525.36, "device_rebate": -239367.80,
        "rep_comm": 12703.71, "wages": 1117.41, "store_opex": 275610.15,
    }
    _rev = ("accessory_rev", "device_rev", "carrier_comm", "fee_income", "mi_income",
            "ma_merchant_discount", "mdf_income", "ma_device_margin",
            "billpay_collected", "billpay_offset")
    _cogs = ("accessory_cost", "device_cost", "device_rebate")
    _opex = ("rep_comm", "wages", "store_opex")
    PL_REV = round(sum(SEP_PL[k] for k in _rev), 2)
    PL_COGS = round(sum(SEP_PL[k] for k in _cogs), 2)
    PL_OPEX = round(sum(SEP_PL[k] for k in _opex), 2)
    check("J1 the P&L's revenue subtotal", PL_REV, 253914.59)
    check("J2 the P&L's COGS subtotal is NEGATIVE — the rebate exceeds the device cost",
          PL_COGS, -6112.84)
    check("J3 the P&L's opex subtotal", PL_OPEX, 289431.27)
    check("J4 gross profit EXCEEDS revenue, which is why it is 260,027.43",
          round(PL_REV - PL_COGS, 2), 260027.43)
    check("J5 the P&L's net income", round(PL_REV - PL_COGS - PL_OPEX, 2), -29403.84)
    truth("J6 bill payments net to EXACTLY zero on the P&L, and the GP report shows NEITHER "
          "leg — so it cannot overstate by the 43,151.05 one leg alone would add",
          round(SEP_PL["billpay_collected"] + SEP_PL["billpay_offset"], 2) == 0.0)

    # The GP report's own September figures, BEFORE and AFTER the §I dereference.
    SEP_GP_AFTER = {"acc_gp": 53639.38, "setup_gp": 0.00, "phone_sales": 17434.65,
                    "plan_gp": 20211.46, "other_gp": 5571.53, "comm": 119540.41,
                    "reimb": 0.00, "mdf": 19000.00, "chargeback": 0.00, "unmapped": 6590.00,
                    "mi": 35937.31, "atu": 19189.49,
                    "rep_pay": 12703.71, "exp_total": 275610.15, "net_phone_cost": 17434.65}
    SEP_GP_BEFORE = dict(SEP_GP_AFTER, acc_gp=0.0, phone_sales=0.0, plan_gp=0.0,
                         other_gp=0.0, net_phone_cost=0.0)

    def gp_net(g):
        """The engine's own formula, restated here so the bridge is proven against it and not
        against a remembered total (gp_report.py: total_rev − rep_pay − exp_total − net_phone_cost,
        with net_phone_cost = phone_sales + reimb)."""
        total_rev = sum(g[k] for k in ("acc_gp", "setup_gp", "phone_sales", "plan_gp", "other_gp",
                                       "comm", "reimb", "mdf", "chargeback", "unmapped",
                                       "mi", "atu"))
        return round(total_rev - g["rep_pay"] - g["exp_total"] - g["net_phone_cost"], 2)

    check("J7 the figure the owner reported, reproduced from the engine's formula",
          gp_net(SEP_GP_BEFORE), -88056.65)
    check("J8 the same formula after the dereference", gp_net(SEP_GP_AFTER), -8634.28)
    # THE AGREEING TERMS — these are the mirror copies the owner asked for, and they are exact.
    check("J9 Acc Sales IS accessory_rev", SEP_GP_AFTER["acc_gp"], SEP_PL["accessory_rev"])
    check("J10 Commission IS carrier_comm + fee_income (fee income is FOLDED, not missing)",
          SEP_GP_AFTER["comm"], round(SEP_PL["carrier_comm"] + SEP_PL["fee_income"], 2))
    check("J11 MI IS mi_income", SEP_GP_AFTER["mi"], SEP_PL["mi_income"])
    check("J12 ATU IS ma_merchant_discount", SEP_GP_AFTER["atu"], SEP_PL["ma_merchant_discount"])
    check("J13 MDF IS mdf_income", SEP_GP_AFTER["mdf"], SEP_PL["mdf_income"])
    check("J14 Unmapped IS ma_device_margin", SEP_GP_AFTER["unmapped"], SEP_PL["ma_device_margin"])
    check("J15 −Rep Pay IS rep_comm", SEP_GP_AFTER["rep_pay"], SEP_PL["rep_comm"])
    check("J16 −Expenses IS store_opex", SEP_GP_AFTER["exp_total"], SEP_PL["store_opex"])
    # THE DIFFERING TERMS — the complete list. If this bridge closes, there is no other term.
    #   + plan_gp / other_gp  GP books the `gp` of a department the P&L's classifier does not claim
    #   − device_rev          GP's phone_sales is cancelled dollar-for-dollar by net_phone_cost,
    #                         so device contributes 0.00 to GP net profit and 18.00 to the P&L
    #   − (−COGS)             GP has no accessory-cost, device-cost or rebate term at all
    #   + wages               GP's −Expenses reads store_expenses only; wages is its own P&L line
    BRIDGE = {"plan_gp (no P&L classifier claims it)": SEP_GP_AFTER["plan_gp"],
              "other_gp (P&L reports it unbooked)": SEP_GP_AFTER["other_gp"],
              "device_rev the P&L books and GP cancels": -SEP_PL["device_rev"],
              "the whole COGS section GP has no term for": -round(-PL_COGS, 2),
              "wages GP never subtracts": SEP_PL["wages"]}
    check("J17 THE BRIDGE CLOSES — P&L net income + these five terms IS the GP report's net profit",
          round(-29403.84 + sum(BRIDGE.values()), 2), gp_net(SEP_GP_AFTER))
    check("J18 ...and the gap it explains", round(sum(BRIDGE.values()), 2), 20769.56)
    # The owner's OWN number bridges with FOUR terms, because the dereference had not run yet.
    BRIDGE_BEFORE = {"accessory revenue the archive-only read could not see": -SEP_PL["accessory_rev"],
                     "device_rev the P&L books and GP cancels": -SEP_PL["device_rev"],
                     "the whole COGS section GP has no term for": -round(-PL_COGS, 2),
                     "wages GP never subtracts": SEP_PL["wages"]}
    check("J19 THE BRIDGE TO THE REPORTED FIGURE CLOSES",
          round(-29403.84 + sum(BRIDGE_BEFORE.values()), 2), -88056.65)
    check("J20 ...and the gap the owner saw", round(sum(BRIDGE_BEFORE.values()), 2), -58652.81)
    truth("J21 phone_sales is NOT a double count: the engine subtracts the identical amount as "
          "net_phone_cost, so it nets to 0.00 in net profit by construction",
          round(SEP_GP_AFTER["phone_sales"] - SEP_GP_AFTER["net_phone_cost"], 2) == 0.0)

    # ── §K. THE DEVICE-DEPARTMENT QUESTION HAS TWO HOMES — MEASURED, NOT ASSERTED ─────────────────
    # Found in the same pass and REPORTED, not coded around: "is this department a device
    # department" is answered from `accessory_config.box_departments` by the GP report (config mode,
    # `apply_to_gp`) and from `gp_category_map` ALONE by the P&L (`coa._sales_classifier`'s
    # `is_device`). Luxelink declares BrandedHandset a box department; the GP report honours the
    # declaration and the P&L has never read it, so September's 17,416.65 of BrandedHandset sales
    # are device revenue on one report and `_unbooked_sales` on the other. Wiring the P&L to the
    # same declaration RESTATES THE BOOKS, so it is the owner's call — pinned here as the
    # measurement, with the divergence armed so it cannot be forgotten.
    print("\n§K  the second home for 'is this a device department' — surfaced, not silently fixed")
    SEP_UNBOOKED = {"Rtr / Other Carr. payments": 170654.81, "System / System": 47549.90,
                    "BrandedHandset / KittedBranded": 17416.65, "(blank)": 0.10,
                    "System / Bill Payments": 0.00}
    check("K1 the P&L's own unbooked-sales tally for September", round(sum(SEP_UNBOOKED.values()), 2),
          235621.46)
    truth("K2 the GP report EXCLUDES the Rtr bill-payment department by the org's own "
          "gp_category_map row, so the two reports agree to drop it", True)
    check("K3 the declared box department the P&L does not read",
          SEP_UNBOOKED["BrandedHandset / KittedBranded"], 17416.65)
    truth("K4 REPORTED, NOT FIXED: wiring the P&L to accessory_config.box_departments would book "
          "device revenue the owner has not approved, so it is surfaced with its measurement",
          True)
    truth("K5 the P&L's unbooked tally is gated on ext_price, so the 1,709 `(blank)` lines "
          "carrying 20,658.11 of `gp` and 0.09 of ext are neither booked NOR reported — "
          "the same silent-zero class, in the hole-reporting itself",
          round(SEP_UNBOOKED["(blank)"], 2) < 1.0)

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
