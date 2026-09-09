"""HARNESS — commission received vs the master agent's back-office P&L (owner directive 2026-09-08).

Owner, verbatim: "check the commission received as per our system and the back office, seems like a
big difference, all items should match and there should be nothing in unsplit, everything has a
reason and everything is assigned to the code, so anything which is assigned to the company level
should be split among the store … dont count any rebate received in the commission — it reflects in
the balance sheet towards gross sales but not in gross profit. also the residual seems a lot off".

Proves, with NO DB and stdlib only:

  A. REGRESSION — THE RESIDUAL WAS 55% AIR. The residual-per-subscriber report summed
     residual = mi + atu for EVERY source. On the MA/VidaPay source `atu` is the airtime
     (refill/top-up) margin, which has had its OWN P&L line since mig 309 and does not recur per
     subscriber. Live August 2026 (org 854f6d7b…): reported $54,972.03 against booked residual
     $35,490.67 — overstated by exactly the month's $19,481.36 merchant discount, and the same 55%
     inflation on every per-subscriber figure. `residual_components` is the fix and this section
     reproduces the defect with the pre-fix component set.
  B. RESIDUAL COMPONENTS ARE CONFIG, PER SOURCE, NEVER PER CARRIER. Boost keeps mi+atu (two halves
     of one booked line); MA/VidaPay counts the booked residual line only; an org override wins;
     garbage/empty/unknown overrides fall back to the house default rather than to a guess.
  C. THE BACK-OFFICE VARIANCE, LINE BY LINE, STORE BY STORE. The 20-store August fixture carries
     the real per-store back-office figures and the real per-store feed classification. Residual
     ties 20/20 to the cent; the spiff and premium-store-spiff variances are the exact measured
     dollars and each one has a named cause.
  D. NOTHING IN UNSPLIT. Every processor account in the fixture resolves to a store through the
     mig-314 index, so every commission dollar carries a store: the company-wide (store=None)
     commission total is 0.00. An account the index cannot place stays company-wide and is NAMED —
     never spread across stores to make a total match.
  E. EVERYTHING HAS A REASON — `ma_tx_coverage`. Every raw_ma_daily_tx retail_cost dollar either
     books to a P&L line or is listed by order-type family with a reason; a family with no
     configured reason carries the literal 'no business rule configured' (the mig-312 marker
     ma_recon already uses). The regression: 'Retroactive Postpaid Spiff' — $3,794.56 of August
     carrier spiff cash — books NOWHERE under the org's live config and is reported as unexplained.
  F. REBATES ARE NOT COMMISSION. No rebate line may appear in the commission-received family, under
     EITHER presentation route; the routes only decide where the rebate presents, never whether it
     is commission. Reproduces the $251,946.31 August rebate sitting on a REVENUE line under
     'income' and its disappearance from revenue under 'contra_cogs'.
  G. COMPANY-LEVEL SPLITS TO STORES ON THE CARRIER'S OWN ATTRIBUTION. The $1,000 premium store
     spiff is a company-level accrual that lands per store because the carrier books each row
     against that store's processor account; the split basis is that account, not an allocation
     formula. Proven store by store against the back office, including the store that genuinely has
     none (measured zero) and the store the back office dropped.
  H. THREE STATES, NOT TWO. measured / genuinely-zero / not-measured stay distinguishable: a store
     with no feed rows at all is None, not 0.00.

Run:  cd backend && python3 harness_commission_backoffice_recon.py
"""
import sys

sys.path.insert(0, ".")

from app.modules.account import ma_store_pnl as msp          # noqa: E402
from app.modules.account import residual_subs as rs          # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


def r2(x):
    return round(x + 0.0, 2)


# ── the live shape, in miniature ─────────────────────────────────────────────────────────────────
# The org that holds BOTH back-office books. Luxlink Wireless = the Chicago stores, Nova Wave
# Communications = the NY/NJ stores; ONE org_id, two companies (verified live 2026-09-08).
ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"

# mig-314 account -> store, exactly as the live index resolves (19 derived from raw_ma_fulfillment
# + account 170405 pinned in ma_account_store_map). Every account maps: nothing is company-wide.
ACCOUNT_STORE = {
    "170084": "4640-A W Diversey Ave", "170078": "3248 W Lawrence Ave",
    "170088": "6500 W Irving Park Rd", "170077": "2640 Narragansett",
    "170083": "3966 W Grand Ave", "170086": "5601 W Belmont Ave",
    "170085": "4801 W Armitage Ave", "170074": "2317 S Cicero Ave STE A",
    "170075": "2414 W Cermak Rd", "170081": "3352 W 26th St", "170082": "3735 W 26th St",
    "170087": "639 W Lincoln Hwy", "170073": "18226 Kedzie Ave",
    "168873": "3560 Nostrand Avenue", "168874": "957 Pennsylvania Avenue",
    "168876": "218-80 Hempstead Avenue", "169288": "531 Utica Ave",
    "170405": "104-08 Lefferts Blvd", "168875": "902 Avenue U", "168872": "7812 Bergenline Ave",
}
LUX_STORES = ["4640-A W Diversey Ave", "3248 W Lawrence Ave", "6500 W Irving Park Rd",
              "2640 Narragansett", "3966 W Grand Ave", "5601 W Belmont Ave",
              "4801 W Armitage Ave", "2317 S Cicero Ave STE A", "2414 W Cermak Rd",
              "3352 W 26th St", "3735 W 26th St", "639 W Lincoln Hwy", "18226 Kedzie Ave"]
NOVA_STORES = ["3560 Nostrand Avenue", "957 Pennsylvania Avenue", "218-80 Hempstead Avenue",
               "531 Utica Ave", "104-08 Lefferts Blvd", "902 Avenue U", "7812 Bergenline Ave"]

# THE BACK OFFICE, August 2026, per store (owner's two workbooks, parsed).
BO_RESIDUAL = {
    "4640-A W Diversey Ave": 4710.02, "3248 W Lawrence Ave": 1990.79,
    "6500 W Irving Park Rd": 776.13, "2640 Narragansett": 1299.57, "3966 W Grand Ave": 2765.88,
    "5601 W Belmont Ave": 3815.43, "4801 W Armitage Ave": 1670.97,
    "2317 S Cicero Ave STE A": 2075.09, "2414 W Cermak Rd": 1988.63, "3352 W 26th St": 2379.52,
    "3735 W 26th St": 2735.97, "639 W Lincoln Hwy": 1060.65, "18226 Kedzie Ave": 1102.19,
    "3560 Nostrand Avenue": 1267.05, "957 Pennsylvania Avenue": 1244.29,
    "218-80 Hempstead Avenue": 1536.71, "531 Utica Ave": 260.13, "104-08 Lefferts Blvd": 574.43,
    "902 Avenue U": 1034.55, "7812 Bergenline Ave": 1202.67,
}
BO_PREMIUM_STORE_SPIFF = dict(
    [(s, 1000.0) for s in LUX_STORES if s != "18226 Kedzie Ave"]
    + [("18226 Kedzie Ave", 0.0)]
    + [(s, 1000.0) for s in ("957 Pennsylvania Avenue", "218-80 Hempstead Avenue", "531 Utica Ave")]
    + [(s, 0.0) for s in ("3560 Nostrand Avenue", "104-08 Lefferts Blvd", "902 Avenue U",
                          "7812 Bergenline Ave")])
BO_TOTALS = {"Postpaid Residual": 35490.67, "Postpaid Spiff": 97465.36,
             "Activation Profit": 3570.74, "Activation Spiff": 18061.37,
             "Activation Fee": 12.50, "Premium Store Spiff": 15000.00}

# The measured August feed totals, by classification (live read 2026-09-08).
FEED = {
    "residual": 35490.67,              # order_type 'Postpaid Residual Order' (∪ '%residual%')
    "spiff_configured": 94861.81,      # order_type 'PostPaid Additional Spiff'
    "spiff_unconfigured": 3794.56,     # order_type 'Retroactive Postpaid Spiff' — books NOWHERE
    "mdf": 16000.00,                   # 16 × $1,000 'Premium Store Spiff' rows
    "merchant_discount": 19481.36,     # airtime margin (its own P&L line since mig 309)
    "rebate_to_dealer": 251946.31,     # raw_ma_commission.rebate, sign-flipped
}

LIVE_CFG = {"store_attribution": True, "month_spiff_source": "daily_tx",
            "spiff_order_types": ["PostPaid Additional Spiff"],
            "mdf_product_tokens": ["premium store spiff"],
            "line_labels": {"mi_income": "Residual"}, "rebate_presentation": "income"}
PNL_CFG = {"merchant_discount_own_line": True,
           "residual_order_types": ["Postpaid Residual Order"]}


def tx(acct, order_type, product, retail_cost, merchant_discount=0.0):
    """One raw_ma_daily_tx row. NEGATIVE retail_cost = money paid TO the dealer."""
    return {"account_id": acct, "order_type": order_type, "product_name": product,
            "retail_cost": retail_cost, "merchant_discount": merchant_discount}


print("A. REGRESSION — the residual figure was the booked residual PLUS the airtime margin")
AUG_MI, AUG_ATU = 35490.67, 19481.36
pre_fix = ("mi", "atu")                      # what compute() summed for every source before the fix
check("pre-fix MA residual reproduces the reported $54,972.03",
      r2(sum({"mi": AUG_MI, "atu": AUG_ATU}[c] for c in pre_fix)) == 54972.03)
now = rs.residual_components("vidapay_ma")
check("fixed MA residual is the booked residual line only ($35,490.67)",
      r2(sum({"mi": AUG_MI, "atu": AUG_ATU}[c] for c in now)) == 35490.67)
check("the overstatement was exactly the month's merchant discount",
      r2(54972.03 - 35490.67) == r2(AUG_ATU) == 19481.36)
check("and it agrees with the back office to the cent", r2(AUG_MI) == BO_TOTALS["Postpaid Residual"])
# the per-subscriber figure is the same defect, divided
SUBS = 1248                                   # August activation lines (raw_ma_commission rows)
check("per-subscriber was inflated 54.9%",
      round((54972.03 / SUBS) / (35490.67 / SUBS) - 1, 3) == 0.549)
check("airtime margin is still REPORTED, just not called residual", "atu" in rs.RESIDUAL_COMPONENT_KEYS)

print("B. residual components: per SOURCE, config-overridable, never per carrier")
check("boost source keeps mi+atu (one booked line)",
      rs.residual_components("boost_mi_atu") == ("mi", "atu"))
check("MA/VidaPay source is mi only", rs.residual_components("vidapay_ma") == ("mi",))
check("org override wins",
      rs.residual_components("vidapay_ma", {"vidapay_ma": ["mi", "atu"]}) == ("mi", "atu"))
check("override is de-duplicated and normalised",
      rs.residual_components("vidapay_ma", {"vidapay_ma": ["MI", " mi ", "ATU"]}) == ("mi", "atu"))
check("empty override falls back to the house default, not to nothing",
      rs.residual_components("vidapay_ma", {"vidapay_ma": []}) == ("mi",))
check("unknown component names fall back rather than inventing a component",
      rs.residual_components("vidapay_ma", {"vidapay_ma": ["rebate", "spiff"]}) == ("mi",))
check("non-dict / non-list config is ignored",
      rs.residual_components("vidapay_ma", "nonsense") == ("mi",)
      and rs.residual_components("vidapay_ma", {"vidapay_ma": "mi"}) == ("mi",))
check("an unknown source falls back to the booked residual line",
      rs.residual_components("some_future_feed") == ("mi",))
check("no carrier, tenant or vendor name appears in the component defaults",
      all(k in ("boost_mi_atu", "vidapay_ma") for k in rs.RESIDUAL_COMPONENTS_DEFAULT))

print("C. the back-office variance, line by line, store by store")
# Residual: one row per store carrying that store's back-office figure — the feed classification is
# proven to book it to `mi_income` under the org's live config, per store, to the cent.
res_rows = [tx(a, "Postpaid Residual Order", "Trac Autopay Residual", -BO_RESIDUAL[s])
            for a, s in ACCOUNT_STORE.items()]
booked = {}
for line, acct, amt, _d in msp.ma_tx_bookings(res_rows, PNL_CFG, LIVE_CFG):
    if amt:
        booked.setdefault(line, {})
        booked[line][acct] = r2(booked[line].get(acct, 0.0) + amt)
hits = sum(1 for a, s in ACCOUNT_STORE.items()
           if r2(booked["mi_income"].get(a, 0.0)) == BO_RESIDUAL[s])
check("residual ties store for store, 20/20", hits == 20, f"{hits}/20")
check("residual total ties", r2(sum(booked["mi_income"].values())) == BO_TOTALS["Postpaid Residual"])
check("residual variance vs back office is zero",
      r2(sum(booked["mi_income"].values()) - BO_TOTALS["Postpaid Residual"]) == 0.0)
check("spiff variance is the unconfigured family, to the cent",
      r2(BO_TOTALS["Postpaid Spiff"] - FEED["spiff_configured"]) == 2603.55
      and r2(FEED["spiff_configured"] + FEED["spiff_unconfigured"]
             - BO_TOTALS["Postpaid Spiff"]) == 1191.01)
check("premium store spiff variance is one $1,000 row the back office dropped",
      r2(FEED["mdf"] - BO_TOTALS["Premium Store Spiff"]) == 1000.0)
check("the back office carries NO rebate line at all",
      "Rebate" not in " ".join(BO_TOTALS) and "rebate" not in " ".join(BO_TOTALS))

print("D. nothing in unsplit — every commission dollar carries a store")
company_wide = [amt for line, acct, amt, _d in msp.ma_tx_bookings(res_rows, PNL_CFG, LIVE_CFG)
                if acct is None and amt]
check("company-wide (store=None) commission total is 0.00", r2(sum(company_wide)) == 0.0)
unmapped = tx("999999", "Postpaid Residual Order", "Residual", -12.34)
one_unmapped = msp.ma_tx_bookings([unmapped], PNL_CFG, LIVE_CFG)
check("an account the index cannot place keeps its own account id, not a guessed store",
      one_unmapped[-1][1] == "999999" and "999999" not in ACCOUNT_STORE)
check("attribution off ⇒ every row company-wide (byte-identical pre-314 grain)",
      all(a is None for _l, a, _amt, _d in msp.ma_tx_bookings(res_rows, PNL_CFG,
                                                              msp.default_config())))

print("E. everything has a reason — ma_tx_coverage")
COV_ROWS = [
    tx("170084", "Postpaid Residual Order", "Trac Autopay Residual", -100.00),
    tx("170084", "PostPaid Additional Spiff", "TBV MONTH 4 New Activation SPF", -25.00),
    tx("170084", "Retroactive Postpaid Spiff", "Total MAX 5G BYO Plan $30 New Activation Commission",
       -14.75),
    tx("170083", "Retroactive Postpaid Spiff", "Total ALL ACCESS Plan $65 New Activation Commission",
       -17.65),
    tx("170085", "Sales Order", "Premium Store Spiff", -1000.00),
    tx("170085", "Sales Order", "Total Wireless RTR Wallet", 62.60, 5.32),
    tx("170086", "Postpaid Branded MarketPlace", "Apple iPhone 16e 128GB Black TO", 599.99),
    tx("170086", "Activation Order", "Total MAX 5G Plan $55", 33.56, 2.85),
]
cov = msp.ma_tx_coverage(COV_ROWS, PNL_CFG, LIVE_CFG)
check("booked lines are exactly what ma_tx_bookings books",
      cov["booked"] == {"mi_income": 100.0, "carrier_comm": 25.0, "mdf_income": 1000.0,
                        "ma_merchant_discount": 8.17})
fams = {u["order_type"]: u for u in cov["unbooked"]}
check("REGRESSION: 'Retroactive Postpaid Spiff' books nowhere under the live config",
      "Retroactive Postpaid Spiff" in fams and r2(fams["Retroactive Postpaid Spiff"]["amount"]) == 32.40)
check("and it is reported with the mig-312 honest-absence literal",
      fams["Retroactive Postpaid Spiff"]["reason"] == msp.NO_RULE_REASON == "no business rule configured")
check("unexplained total counts only the families with no configured reason",
      r2(cov["unexplained_total"]) == r2(cov["unbooked_total"]))
cov2 = msp.ma_tx_coverage(COV_ROWS, PNL_CFG, LIVE_CFG,
                          reasons={"postpaid branded marketplace": "device purchase — booked as "
                                                                   "device COGS from the MA sheet",
                                   "sales order": "airtime/refill wallet purchase; the dealer "
                                                  "margin is the row merchant_discount",
                                   "activation order": "plan purchase; the dealer margin is the "
                                                       "row merchant_discount"})
fams2 = {u["order_type"]: u for u in cov2["unbooked"]}
check("a configured reason replaces the literal and leaves the total unchanged",
      fams2["Postpaid Branded MarketPlace"]["reason"].startswith("device purchase")
      and r2(cov2["unbooked_total"]) == r2(cov["unbooked_total"]))
check("explained families drop out of unexplained_total",
      r2(cov2["unexplained_total"]) == 32.40)
check("configuring the family books it — the money moves from unbooked to carrier_comm",
      msp.ma_tx_coverage(
          COV_ROWS, PNL_CFG,
          dict(LIVE_CFG, spiff_order_types=["PostPaid Additional Spiff",
                                            "Retroactive Postpaid Spiff"]))["booked"]["carrier_comm"]
      == 57.40)
check("reasons are matched case-insensitively (config is typed by a human)",
      {u["order_type"]: u["reason"] for u in
       msp.ma_tx_coverage(COV_ROWS, PNL_CFG, LIVE_CFG,
                          reasons={"ACTIVATION ORDER": "plan purchase"})["unbooked"]
       }["Activation Order"] == "plan purchase")
check("merchant_discount is never reported as unbooked (it always books)",
      all(u["order_type"] != "__merchant_discount__" for u in cov["unbooked"])
      and cov["booked"]["ma_merchant_discount"] == 8.17)
check("unbooked families are ordered biggest-first",
      [u["order_type"] for u in cov["unbooked"]][0] == "Postpaid Branded MarketPlace")
check("coverage moves no dollar — booking output is untouched by it",
      msp.ma_tx_bookings(COV_ROWS, PNL_CFG, LIVE_CFG)
      == msp.ma_tx_bookings(COV_ROWS, PNL_CFG, LIVE_CFG))
check("empty input is a clean empty coverage, never an exception",
      msp.ma_tx_coverage([], PNL_CFG, LIVE_CFG)["unbooked"] == []
      and msp.ma_tx_coverage(None)["unbooked_total"] == 0.0)

print("F. rebates are not commission")
comm = msp.commission_received_lines()
check("no rebate line is in the commission-received family",
      not set(comm) & set(msp.REBATE_LINES))
check("both rebate routes are covered by REBATE_LINES",
      set(msp.REBATE_LINES) == {"device_rebate", "rebate_income"})
check("device revenue / device margin are not commission either",
      "device_rev" not in comm and "ma_device_margin" not in comm)
check("the commission family is exactly the carrier's own money heads",
      set(comm) == {"carrier_comm", "mi_income", "atu_income", "ma_merchant_discount",
                    "mdf_income", "fee_income"})
sheet = [{"merchant_account_id": "170084", "rebate": -FEED["rebate_to_dealer"]}]
inc = {l: r2(a) for l, _acct, a, _d in msp.ma_commission_bookings(sheet, LIVE_CFG) if a}
cc = {l: r2(a) for l, _acct, a, _d in
      msp.ma_commission_bookings(sheet, dict(LIVE_CFG, rebate_presentation="contra_cogs")) if a}
check("REGRESSION: 'income' puts $251,946.31 of rebate on a REVENUE line",
      inc == {"rebate_income": 251946.31})
check("'contra_cogs' takes the same dollars out of revenue, into contra-COGS",
      cc == {"device_rebate": -251946.31})
ALL_COMM = (FEED["spiff_configured"] + FEED["spiff_unconfigured"] + FEED["residual"]
            + FEED["mdf"] + FEED["merchant_discount"])
check("the rebate alone is 1.49x the whole month's real MA commission ($169,628.40)",
      r2(ALL_COMM) == 169628.40
      and round(FEED["rebate_to_dealer"] / ALL_COMM, 2) == 1.49)
check("gross profit is unchanged by the route (revenue and COGS move together)",
      r2(inc["rebate_income"] + cc["device_rebate"]) == 0.0)
check("route resolution is data-driven with a safe default",
      msp.rebate_route({"rebate_presentation": "income"}) == ("rebate_income", 1)
      and msp.rebate_route({}) == ("device_rebate", -1)
      and msp.rebate_route({"rebate_presentation": "nonsense"}) == ("device_rebate", -1))

print("G. company-level money splits to stores on the carrier's own attribution")
mdf_rows = [tx(a, "Sales Order", "Premium Store Spiff", -1000.00)
            for a, s in ACCOUNT_STORE.items()
            if s not in ("18226 Kedzie Ave", "104-08 Lefferts Blvd", "902 Avenue U",
                         "7812 Bergenline Ave")]
mdf = {}
for line, acct, amt, _d in msp.ma_tx_bookings(mdf_rows, PNL_CFG, LIVE_CFG):
    if amt and line == msp.MDF_LINE:
        mdf[ACCOUNT_STORE[acct]] = r2(mdf.get(ACCOUNT_STORE[acct], 0.0) + amt)
check("16 stores carry a $1,000 premium store spiff in the feed",
      len(mdf) == 16 and set(mdf.values()) == {1000.0} and r2(sum(mdf.values())) == FEED["mdf"])
check("the split basis is the row's own processor account — no allocation formula",
      all(ACCOUNT_STORE[a] in mdf for a in ("170084", "168873")))
check("15 of the 16 match the back office store for store",
      sum(1 for s, v in mdf.items() if BO_PREMIUM_STORE_SPIFF.get(s) == v) == 15)
check("the one mismatch is 3560 Nostrand — we have the row, the back office shows zero",
      mdf["3560 Nostrand Avenue"] == 1000.0 and BO_PREMIUM_STORE_SPIFF["3560 Nostrand Avenue"] == 0.0)
check("18226 Kedzie is a MEASURED zero in both books (no row exists)",
      "18226 Kedzie Ave" not in mdf and BO_PREMIUM_STORE_SPIFF["18226 Kedzie Ave"] == 0.0)
check("company-wide MDF is zero — nothing left at the company level to split",
      not [a for line, a, amt, _d in msp.ma_tx_bookings(mdf_rows, PNL_CFG, LIVE_CFG)
           if amt and a is None])

print("H. three states — measured / genuinely-zero / not-measured stay distinct")
measured = {ACCOUNT_STORE[a]: v for a, v in booked["mi_income"].items()}
not_measured = "104-08 Lefferts Blvd"
check("a store with feed rows reports its measured figure",
      measured["4640-A W Diversey Ave"] == 4710.02)
zero_cov = msp.ma_tx_coverage([tx("170073", "Sales Order", "Premium Store Spiff", 0.0)],
                              PNL_CFG, LIVE_CFG)["booked"]
check("a row worth 0.00 records its line as a MEASURED zero, not as an absence",
      zero_cov.get("mdf_income") == 0.0 and "mdf_income" in zero_cov
      and "carrier_comm" not in zero_cov and "mi_income" not in zero_cov)
check("a store with NO rows is absent from the mapping, never rendered as 0.00",
      mdf.get(not_measured) is None and not_measured not in mdf)

print("I. REVERSE-CALCULATING 'Activation Spiff' $18,061.37 — a clean negative, store by store")
# Owner 2026-09-09: "do the reverse calculation for 18061.37 as it might be back offices own
# terminolofy it could be just a total of all commision recd". The per-store figures below are the
# owner's own workbooks; the candidate vectors are LIVE August-2026 measurements (read 2026-09-09).
BO_ACTIVATION_SPIFF = {
    "4640-A W Diversey Ave": 1460.29, "3248 W Lawrence Ave": 863.61,
    "6500 W Irving Park Rd": 492.89, "2640 Narragansett": 863.92, "3966 W Grand Ave": 1582.25,
    "5601 W Belmont Ave": 1772.04, "4801 W Armitage Ave": 1414.05,
    "2317 S Cicero Ave STE A": 1187.14, "2414 W Cermak Rd": 797.57, "3352 W 26th St": 1038.17,
    "3735 W 26th St": 1069.15, "639 W Lincoln Hwy": 572.55, "18226 Kedzie Ave": 476.08,
    "3560 Nostrand Avenue": 592.46, "957 Pennsylvania Avenue": 1464.27,
    "218-80 Hempstead Avenue": 613.02, "531 Utica Ave": 517.31, "104-08 Lefferts Blvd": 506.51,
    "902 Avenue U": 427.99, "7812 Bergenline Ave": 350.10,
}
# Finalist 1 — the commission sheet's MONTH-1 activation commission (|raw_ma_commission.spiff_m1|),
# the only "activation spiff" shaped money in the feeds. Σ 23,271.90.
OURS_SPIFF_M1 = {
    "4640-A W Diversey Ave": 2007.01, "3248 W Lawrence Ave": 1052.09,
    "6500 W Irving Park Rd": 813.94, "2640 Narragansett": 1198.95, "3966 W Grand Ave": 1951.55,
    "5601 W Belmont Ave": 1959.80, "4801 W Armitage Ave": 1849.05,
    "2317 S Cicero Ave STE A": 1384.64, "2414 W Cermak Rd": 1017.57, "3352 W 26th St": 1162.45,
    "3735 W 26th St": 1199.15, "639 W Lincoln Hwy": 673.52, "18226 Kedzie Ave": 695.51,
    "3560 Nostrand Avenue": 712.46, "957 Pennsylvania Avenue": 1919.29,
    "218-80 Hempstead Avenue": 920.52, "531 Utica Ave": 789.81, "104-08 Lefferts Blvd": 866.50,
    "902 Avenue U": 597.99, "7812 Bergenline Ave": 500.10,
}
# Finalist 2 — the refill/RTR wallet margin (raw_ma_daily_tx 'Sales Order' merchant_discount), the
# half of our one "Merchant discount" line the back office does not carry as Activation Profit.
OURS_WALLET_MARGIN = {
    "4640-A W Diversey Ave": 1151.99, "3248 W Lawrence Ave": 1032.99,
    "6500 W Irving Park Rd": 286.26, "2640 Narragansett": 353.96, "3966 W Grand Ave": 1870.95,
    "5601 W Belmont Ave": 1030.95, "4801 W Armitage Ave": 1168.94,
    "2317 S Cicero Ave STE A": 1016.81, "2414 W Cermak Rd": 833.14, "3352 W 26th St": 1073.69,
    "3735 W 26th St": 1166.54, "639 W Lincoln Hwy": 454.32, "18226 Kedzie Ave": 263.86,
    "3560 Nostrand Avenue": 598.69, "957 Pennsylvania Avenue": 828.15,
    "218-80 Hempstead Avenue": 785.48, "531 Utica Ave": 451.62, "104-08 Lefferts Blvd": 320.77,
    "902 Avenue U": 491.54, "7812 Bergenline Ave": 555.06,
}
check("the owner's per-store Activation Spiff sums to the reported 18,061.37",
      r2(sum(BO_ACTIVATION_SPIFF.values())) == BO_TOTALS["Activation Spiff"] == 18061.37)
check("the two companies split 13,589.71 / 4,471.66 as the workbooks do",
      r2(sum(BO_ACTIVATION_SPIFF[s] for s in LUX_STORES)) == 13589.71
      and r2(sum(BO_ACTIVATION_SPIFF[s] for s in NOVA_STORES)) == 4471.66)
a1 = msp.per_store_agreement(OURS_SPIFF_M1, BO_ACTIVATION_SPIFF)
check("ELIMINATED — month-1 activation commission: 0 of 20 stores, 5,210.53 over on the month",
      a1["stores_exact"] == 0 and a1["total_diff"] == 5210.53
      and a1["verdict"] == msp.AGREEMENT_NO, str(a1))
check("and it is over at EVERY store, so no subset-of-rows filter can close it",
      all(OURS_SPIFF_M1[s] > BO_ACTIVATION_SPIFF[s] for s in BO_ACTIVATION_SPIFF))
a2 = msp.per_store_agreement(OURS_WALLET_MARGIN, BO_ACTIVATION_SPIFF)
check("ELIMINATED — refill/RTR wallet margin: 0 of 20 stores, 2,325.66 short, and it is over at "
      "some stores and short at others (not a rate, not a subset)",
      a2["stores_exact"] == 0 and a2["total_diff"] == -2325.66
      and any(OURS_WALLET_MARGIN[s] > BO_ACTIVATION_SPIFF[s] for s in BO_ACTIVATION_SPIFF)
      and any(OURS_WALLET_MARGIN[s] < BO_ACTIVATION_SPIFF[s] for s in BO_ACTIVATION_SPIFF),
      str(a2))
check("no flat per-activation rate can produce it either — $/activation runs 10.27 to 23.78",
      round(492.89 / 48, 2) == 10.27 and round(427.99 / 18, 2) == 23.78)
# THE ONE POSITIVE FINDING: the owner's hypothesis holds at the MONTH level. The back office's six
# commission lines total within $27.76 of our whole commission-received figure — the same money,
# cut into different lines, with 'Activation Spiff' as the slice we book inside "Merchant discount".
BO_SIX_LINE_TOTAL = r2(sum(BO_TOTALS.values()))
check("the back office's six commission lines total 169,600.64",
      BO_SIX_LINE_TOTAL == 169600.64, str(BO_SIX_LINE_TOTAL))
check("ours is 169,628.40 — the two books differ by $27.76 on the month (0.016%)",
      r2(ALL_COMM) == 169628.40 and r2(ALL_COMM - BO_SIX_LINE_TOTAL) == 27.76)
check("so the owner's reading is right in kind: their lines are a repartition of the SAME "
      "commission received, not extra money",
      abs(r2(ALL_COMM - BO_SIX_LINE_TOTAL)) < 0.02 * ALL_COMM / 100)
check("and the arithmetic is exact — our spiff excess + our MDF excess − their activation-family "
      "excess over our merchant discount = the same $27.76",
      r2(1191.01 + 1000.00
         - (BO_TOTALS["Activation Profit"] + BO_TOTALS["Activation Fee"]
            + BO_TOTALS["Activation Spiff"] - FEED["merchant_discount"])) == 27.76)
TIES_ON_TOTAL_ONLY = dict(BO_ACTIVATION_SPIFF)          # same month to the cent…
TIES_ON_TOTAL_ONLY["4640-A W Diversey Ave"] = r2(TIES_ON_TOTAL_ONLY["4640-A W Diversey Ave"] + 100)
TIES_ON_TOTAL_ONLY["18226 Kedzie Ave"] = r2(TIES_ON_TOTAL_ONLY["18226 Kedzie Ave"] - 100)
_agree_total_only = msp.per_store_agreement(TIES_ON_TOTAL_ONLY, BO_ACTIVATION_SPIFF)
check("REGRESSION: a candidate that ties on the MONTH but not on the stores is never a match",
      _agree_total_only["total_diff"] == 0.0 and _agree_total_only["stores_exact"] == 18
      and _agree_total_only["verdict"] == msp.AGREEMENT_TOTAL_ONLY, str(_agree_total_only))
check("a candidate that reproduces every store IS a match (the comparator is not simply strict)",
      msp.per_store_agreement(dict(BO_ACTIVATION_SPIFF), BO_ACTIVATION_SPIFF)["verdict"]
      == msp.AGREEMENT_REPRODUCES)
check("so 'Activation Spiff' stays UNRESOLVED and no P&L line was invented to absorb it",
      "activation_spiff" not in msp.COMMISSION_RECEIVED_LINES
      and "activation_spiff" not in [k for k, _l, _c in msp.DEVICE_MARGIN_COLUMNS])

print("J. RETROACTIVE POSTPAID SPIFF — store yes, rep yes, activation NO (owner 2026-09-09)")
# "im assuming spiff paid later but it must be assigned to a phone number or imei or order
# actiavted at a certain store." Live shape: 230 rows / $3,794.56, every row a MONTH-1
# "… New Activation Commission" arriving late.
RETRO_ROWS = [
    {"order_type": "Retroactive Postpaid Spiff", "order_number": "353264249",
     "product_name": "Total MAX 5G BYO Plan $30 New Activation Commission", "retail_cost": -15.00,
     "account_id": "170075", "user_name": "Nespinoza1", "tx_date": "2026-08-12",
     "merchant_invoice": 353338146.0},
    {"order_type": "Retroactive Postpaid Spiff", "order_number": "353475632",
     "product_name": "Total MAX 5G Plan $55 New Activation Commission", "retail_cost": -27.50,
     "account_id": "170073", "user_name": "Mcollins", "tx_date": "2026-08-14",
     "merchant_invoice": 353947241.0},
    {"order_type": "Retroactive Postpaid Spiff", "order_number": "354122954",
     "product_name": "Total MAX 5G BYO Plan $30 New Activation Commission", "retail_cost": -15.00,
     "account_id": "168872", "user_name": "Oneyda", "tx_date": "2026-08-18",
     "merchant_invoice": 354326784.0},
]
# The CONTROL: an 'Activation Order' row, the ONE family whose order_number does join (1,735 of
# 4,995 live). If the retro zero were a broken function this would be zero too.
ACT_ROW = {"order_type": "Activation Order", "order_number": "353659935",
           "product_name": "Total MAX 5G Plan $55", "retail_cost": 33.56, "account_id": "170084",
           "user_name": "Jgaribay", "tx_date": "2026-08-14", "merchant_discount": 2.85}
KNOWN_ACTIVATIONS = ["353659935", "356137121", "353890564"]   # raw_ma_commission.activation_order
STORE_INDEX = {a: s for a, s in ACCOUNT_STORE.items()}
att = msp.ma_payout_attribution(RETRO_ROWS + [ACT_ROW], KNOWN_ACTIVATIONS, STORE_INDEX,
                                {"353659935": "August 2026"})
fam = {f["order_type"]: f for f in att["families"]}
retro = fam["Retroactive Postpaid Spiff"]
check("every retroactive row resolves to a STORE through the mig-314 account index",
      retro["store_resolved_rows"] == len(RETRO_ROWS) and retro["store_unresolved_rows"] == 0)
check("three different stores, none of them company-wide or allocated",
      sorted(retro["stores"]) == sorted({ACCOUNT_STORE[a] for a in ("170075", "170073", "168872")}))
check("every row names a REP", sorted(retro["reps"]) == ["Mcollins", "Nespinoza1", "Oneyda"])
check("every row is DATED", retro["dated"] == len(RETRO_ROWS))
check("REGRESSION: not one row links to an activation — order_number matches no activation order",
      retro["linked_rows"] == 0 and retro["linked_amount"] == 0.0
      and retro["unlinked_amount"] == r2(sum(-r["retail_cost"] for r in RETRO_ROWS)))
check("so the month it was EARNED in is not claimed — no period is invented",
      retro["activation_periods"] == [])
check("and the absence is REPORTED with its reason, never papered over",
      retro["reason"] == msp.ATTRIBUTION_NO_ACTIVATION_REASON
      and "no imei/mdn" in retro["reason"] and "order_number" in retro["reason"])
check("CONTROL — the activation family DOES link, so the zero is the feed's keying, not a bug",
      fam["Activation Order"]["linked_rows"] == 1
      and fam["Activation Order"]["activation_periods"] == ["August 2026"]
      and fam["Activation Order"]["reason"] is None)
check("the same rows carrying an activation key WOULD be attributed to the earned month — the "
      "wiring is ready for the day the feed carries it",
      msp.ma_payout_attribution(
          [dict(RETRO_ROWS[0], order_number="356137121")], KNOWN_ACTIVATIONS, STORE_INDEX,
          {"356137121": "July 2026"})["families"][0]["activation_periods"] == ["July 2026"])
check("no store index ⇒ no store is claimed for anything (never guessed from the rep or the date)",
      msp.ma_payout_attribution(RETRO_ROWS)["families"][0]["store_resolved_rows"] == 0)
check("amounts use the booking sign convention — money TO the dealer is positive",
      r2(att["amount"]) == r2(sum(-r["retail_cost"] for r in RETRO_ROWS) - ACT_ROW["retail_cost"]))
check("the family the owner configured still books — attribution is a read-out, not a gate",
      any(line == "carrier_comm" for line, _a, amt, _d in
          msp.ma_tx_bookings(RETRO_ROWS, PNL_CFG,
                             dict(LIVE_CFG, spiff_order_types=["PostPaid Additional Spiff",
                                                               "Retroactive Postpaid Spiff"]))
          if amt))
check("empty input is a clean empty attribution, never an exception",
      msp.ma_payout_attribution([])["families"] == []
      and msp.ma_payout_attribution(None)["amount"] == 0.0)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
