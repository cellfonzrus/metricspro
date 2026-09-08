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

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
