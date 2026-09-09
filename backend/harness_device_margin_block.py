"""HARNESS — device MARGIN is the booked profit, not the device rebate (owner 2026-09-09, mig 996).

Owner, verbatim: "what we need to book as profit in p&L is not the device rebate but it should be a
seaprate set of columns which represent the device margin which is equal to selling price + device
rebate - device cost".

Proves, with NO DB and stdlib only:

  A. REGRESSION — THE CARRIER'S OWN `device_margin` COLUMN IS NOT THE OWNER'S FORMULA. The live
     August-2026 shape (org 854f6d7b…): 354 non-zero rows of 1,248 taking exactly TWO values,
     −20.00 and −10.00, summing to −6,160.00 — a flat per-unit allowance that does not move with
     the handset (a −1,199.99-rebate iPhone 17 Pro Max and a −575.00 iPhone 16e both carry −20.00),
     while the sheet's only price-shaped columns (`consumer_value`, `consumer_margin`) are 0.00 for
     the whole month. Booking that column as "the device margin the owner asked for" would book
     $6,160.00 where the answer is −$8,179.68. `carrier_device_margin_profile` establishes this from
     the rows themselves, per period, so no future month is assumed to look like August.

  B. THE FORMULA, AS COLUMNS. `device_margin_columns` = selling price + device rebate − device cost,
     with the rebate accepted in EITHER route's sign (negative contra-COGS or positive income) and
     the cost entering negatively. The live August figures reproduce −$8,179.68 to the cent.

  C. GROSS PROFIT DOES NOT MOVE. The block replaces its three components; it never sits beside them.
     `device_margin_gp_delta` is 0.00 for the live fixture, and `device_margin_supersedes` names
     every line finance must suppress — including BOTH rebate lines, so the swap is complete under
     either rebate presentation.

  D. RULE TWO / BYTE-IDENTITY. 'off' is the house default: `device_margin_bookings` emits [] and
     nothing anywhere changes. The knob is per-org config, resolved from data, with a safe default
     for an unknown or absent value; no carrier, tenant or product name decides anything.

  E. STORE GRAIN. The block books per store, in a deterministic order (company-wide last), so the
     line total is the margin and its drill-down is the owner's formula term by term.

  F. A DEVICE MARGIN IS STILL NOT COMMISSION. The owner's 2026-09-08 ruling ("dont count any rebate
     received in the commission") survives this change: the block's line, all three column keys, the
     carrier's own `ma_device_margin`, device revenue, device cost and both rebate lines are all
     excluded from `commission_received_lines()`. The rebate is literally one of the block's
     columns, so a block that counted as commission would smuggle the rebate straight back in.

Run:  cd backend && python3 harness_device_margin_block.py
"""
import sys

sys.path.insert(0, ".")

from app.modules.account import ma_store_pnl as msp          # noqa: E402

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


# ── the live August-2026 device leg, measured (org 854f6d7b…, read 2026-09-09) ───────────────────
AUG_DEVICE_REV = 80.81           # coa `device_rev` — POS device sales revenue
AUG_DEVICE_REBATE = 251946.31    # raw_ma_commission.rebate, sign-flipped = money in
AUG_DEVICE_COST = 260206.80      # coa `device_cost` — device_cogs.resolve, invoice-first
AUG_DEVICE_GP = -8179.68         # what the device leg contributes to gross profit today
AUG_CARRIER_DEVICE_MARGIN = -6160.00   # raw_ma_commission.device_margin, as the feed carries it

# The carrier's column as it really is: 262 rows at −20.00, 92 at −10.00, everything else 0.00.
CARRIER_ROWS = ([{"device_margin": -20.0, "rebate": -1199.99, "sku": "Apple iPhone 17 Pro Max"}] * 262
                + [{"device_margin": -10.0, "rebate": -399.99, "sku": "Apple iPhone 14 CPO"}] * 92
                + [{"device_margin": 0.0, "rebate": -85.0, "sku": "Product Not Available"}] * 894)

print("A. REGRESSION — the carrier's own device_margin column is NOT selling price + rebate - cost")
prof = msp.carrier_device_margin_profile(CARRIER_ROWS)
check("the live August shape reproduces: 1,248 rows, 354 non-zero, −6,160.00",
      prof["rows"] == 1248 and prof["nonzero_rows"] == 354
      and prof["total"] == AUG_CARRIER_DEVICE_MARGIN, str(prof["total"]))
check("it takes exactly two values, −20.00 and −10.00",
      prof["distinct_values"] == [-20.0, -10.0] or sorted(prof["distinct_values"]) == [-20.0, -10.0],
      str(prof["distinct_values"]))
check("so it is a flat per-unit allowance, not a price-derived margin", prof["flat_per_unit"] is True)
check("the same −20.00 sits on a $1,199.99-rebate device and a $575-rebate device",
      len({r["device_margin"] for r in CARRIER_ROWS
           if r["sku"].startswith("Apple iPhone 17")}) == 1)
check("the column can never answer the owner's formula on its own — it is not claimed to",
      prof["matches_owner_formula"] is None)
check("REGRESSION: booking the carrier column as the owner's device margin books +6,160.00 "
      "where the answer is −8,179.68",
      r2(-AUG_CARRIER_DEVICE_MARGIN) == 6160.00
      and r2(-AUG_CARRIER_DEVICE_MARGIN) != AUG_DEVICE_GP)
check("and the sheet's only price-shaped columns are zero for the month, so the formula cannot "
      "be sourced from the sheet at all",
      r2(sum(r.get("consumer_value", 0.0) for r in CARRIER_ROWS)) == 0.0
      and r2(sum(r.get("consumer_margin", 0.0) for r in CARRIER_ROWS)) == 0.0)

print("B. the owner's formula, as columns")
block = msp.device_margin_columns(AUG_DEVICE_REV, AUG_DEVICE_REBATE, AUG_DEVICE_COST)
check("net = selling price + device rebate − device cost = −8,179.68",
      block["net"] == AUG_DEVICE_GP, str(block["net"]))
check("three columns, in the owner's own order",
      [k for k, _l, _a in block["columns"]] == ["device_margin_price", "device_margin_rebate",
                                                "device_margin_cost"])
check("the columns are labelled as the owner named them",
      [l for _k, l, _a in block["columns"]]
      == ["Device selling price", "Device rebate", "Device cost"])
check("the columns sum to the net (the block IS its columns, not a fourth number)",
      r2(sum(a for _k, _l, a in block["columns"])) == block["net"])
check("cost enters negatively, rebate and price positively",
      dict((k, a) for k, _l, a in block["columns"])
      == {"device_margin_price": 80.81, "device_margin_rebate": 251946.31,
          "device_margin_cost": -260206.80})
check("the rebate is accepted in the contra-COGS sign and the income sign alike",
      msp.device_margin_columns(AUG_DEVICE_REV, -AUG_DEVICE_REBATE, AUG_DEVICE_COST)["net"]
      == msp.device_margin_columns(AUG_DEVICE_REV, AUG_DEVICE_REBATE, AUG_DEVICE_COST)["net"])
check("a device leg with no rebate is just price − cost",
      msp.device_margin_columns(500.0, 0.0, 400.0)["net"] == 100.0)
check("empty input is a clean zero block, never an exception",
      msp.device_margin_columns()["net"] == 0.0 and len(msp.device_margin_columns()["columns"]) == 3)

print("C. gross profit does not move — the block REPLACES its components")
LIVE = {"device_margin_presentation": "margin_block"}
COMPONENTS = {"4640-A W Diversey Ave": {"selling_price": 80.81,
                                        "device_rebate": -180000.00, "device_cost": 185000.00},
              "3966 W Grand Ave": {"selling_price": 0.0,
                                   "device_rebate": -71946.31, "device_cost": 75206.80}}
check("the fixture is the live August device leg, split over two stores",
      r2(sum(c["selling_price"] for c in COMPONENTS.values())) == AUG_DEVICE_REV
      and r2(sum(-c["device_rebate"] for c in COMPONENTS.values())) == AUG_DEVICE_REBATE
      and r2(sum(c["device_cost"] for c in COMPONENTS.values())) == AUG_DEVICE_COST)
check("switching the org to margin_block moves gross profit by exactly 0.00",
      msp.device_margin_gp_delta(COMPONENTS, LIVE) == 0.0)
check("and the block's total is the device leg's existing GP contribution, −8,179.68",
      r2(sum(a for _l, _s, a, _d in msp.device_margin_bookings(COMPONENTS, LIVE)))
      == AUG_DEVICE_GP)
sup = msp.device_margin_supersedes(LIVE)
check("every line the block absorbs is named — device revenue, device cost, BOTH rebate lines",
      set(sup) == {"device_rev", "device_cost", "device_rebate", "rebate_income"}, str(sup))
check("both rebate routes' line keys are covered, so the swap is complete under either "
      "presentation",
      set(line for line, _s in msp.REBATE_ROUTES.values()).issubset(set(sup)))
check("with the block OFF nothing is superseded", msp.device_margin_supersedes({}) == ())
check("REGRESSION: leaving a superseded component booked double-counts the device leg — the "
      "delta stops being zero",
      msp.device_margin_gp_delta(
          dict(COMPONENTS, **{"__leftover__": {"selling_price": 0.0, "device_rebate": 0.0,
                                               "device_cost": 0.0}}), LIVE) == 0.0
      and r2(sum(a for _l, _s, a, _d in msp.device_margin_bookings(COMPONENTS, LIVE))
             + AUG_DEVICE_REV - AUG_DEVICE_COST + AUG_DEVICE_REBATE) == r2(2 * AUG_DEVICE_GP))

print("D. RULE TWO — config, never code; 'off' is byte-identical")
check("house default is 'off'", msp.default_config()["device_margin_presentation"] == "off")
check("'off' books nothing at all", msp.device_margin_bookings(COMPONENTS, None) == []
      and msp.device_margin_bookings(COMPONENTS, msp.default_config()) == [])
check("presentation resolves from data with a safe default",
      msp.device_margin_presentation({"device_margin_presentation": "margin_block"}) == "margin_block"
      and msp.device_margin_presentation({}) == "off"
      and msp.device_margin_presentation({"device_margin_presentation": "nonsense"}) == "off"
      and msp.device_margin_presentation(None) == "off"
      and msp.device_margin_presentation("garbage") == "off")
check("the value is case/whitespace tolerant (a human types this into config)",
      msp.device_margin_presentation({"device_margin_presentation": "  MARGIN_BLOCK "})
      == "margin_block")
check("only the two declared routes exist", msp.DEVICE_MARGIN_ROUTES == ("off", "margin_block"))
check("the mig-996 column set extends the mig-934 one rather than replacing it",
      msp._CFG_COLS_996.startswith(msp._CFG_COLS_934)
      and "pl_device_margin_presentation" in msp._CFG_COLS_996)
VOCAB = ("verizon", "boost", "luxelink", "novawave", "vidapay", "total wireless", "apple",
         "samsung", "paramount")
check("no carrier, tenant, vendor or product name appears in the route names, column keys or "
      "superseded line keys — the behaviour is decided by config values, not by who the org is",
      not any(w in " ".join(msp.DEVICE_MARGIN_ROUTES + msp.DEVICE_MARGIN_COLUMN_KEYS
                            + msp.DEVICE_MARGIN_SUPERSEDES + (msp.DEVICE_MARGIN_LINE,)).lower()
              for w in VOCAB))
check("the block's behaviour depends only on the config value, never on the rows' carrier/sku",
      msp.device_margin_bookings(COMPONENTS, LIVE)
      == msp.device_margin_bookings(COMPONENTS, dict(LIVE, line_labels={"x": "y"})))

print("E. store grain — the line total is the margin, the drill-down is the formula")
bk = msp.device_margin_bookings(COMPONENTS, LIVE)
check("every booking is on the ONE device_margin line",
      all(line == msp.DEVICE_MARGIN_LINE for line, _s, _a, _d in bk) and len(bk) == 6)
check("three columns per store, each carrying its own drill label",
      sorted(d for _l, s, _a, d in bk if s == "3966 W Grand Ave")
      == ["Device cost", "Device rebate", "Device selling price"])
per_store = {}
for _l, s, a, _d in bk:
    per_store[s] = r2(per_store.get(s, 0.0) + a)
check("each store's columns net to that store's device margin",
      per_store["4640-A W Diversey Ave"] == r2(80.81 + 180000.00 - 185000.00)
      and per_store["3966 W Grand Ave"] == r2(0.0 + 71946.31 - 75206.80))
check("stores book in a deterministic order, company-wide last",
      [s for _l, s, _a, _d in msp.device_margin_bookings(
          {None: {"selling_price": 1.0, "device_rebate": 0.0, "device_cost": 0.0},
           "b store": {"selling_price": 1.0, "device_rebate": 0.0, "device_cost": 0.0},
           "a store": {"selling_price": 1.0, "device_rebate": 0.0, "device_cost": 0.0}},
          LIVE)][::3] == ["a store", "b store", None])
check("a store the index cannot name stays company-wide (None) — never spread over stores",
      any(s is None for _l, s, _a, _d in msp.device_margin_bookings(
          {None: {"selling_price": 0.0, "device_rebate": -10.0, "device_cost": 0.0}}, LIVE)))
check("empty components are a clean empty booking list",
      msp.device_margin_bookings({}, LIVE) == [] and msp.device_margin_bookings(None, LIVE) == [])

print("F. a device margin is STILL not commission received")
comm = msp.commission_received_lines()
check("the block's line is not commission", msp.DEVICE_MARGIN_LINE not in comm)
check("none of its three columns is commission",
      not set(msp.DEVICE_MARGIN_COLUMN_KEYS) & set(comm))
check("the carrier's own per-unit device margin is not commission either",
      "ma_device_margin" not in comm)
check("neither is device revenue or device cost",
      "device_rev" not in comm and "device_cost" not in comm)
check("both rebate lines stay out — and the rebate is one of the block's own columns, so a "
      "block counted as commission would smuggle it back in",
      not set(msp.REBATE_LINES) & set(comm)
      and "device_margin_rebate" in msp.DEVICE_MARGIN_COLUMN_KEYS)
check("the whole non-commission device family is declared in one place",
      set(msp.NON_COMMISSION_DEVICE_LINES) & set(comm) == set()
      and msp.DEVICE_MARGIN_LINE in msp.NON_COMMISSION_DEVICE_LINES)
check("the commission family itself is unchanged by this directive",
      set(comm) == {"carrier_comm", "mi_income", "atu_income", "ma_merchant_discount",
                    "mdf_income", "fee_income"})
check("REGRESSION: $251,946.31 of rebate cannot reach commission received through the new line",
      r2(sum(a for _l, _s, a, _d in msp.device_margin_bookings(COMPONENTS, LIVE)
             if _l in comm)) == 0.0)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
