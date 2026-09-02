"""HARNESS — MA → P&L store attribution + line labels (owner spec 2026-09-02, mig 314).

"it shows company wide vida commission, it should show store wise commission for all M1 thru M12,
also it should say Residual on Total side and Mi on boost side, … mdf should capture the market
spiff of $1000/$500 per store …, rebates and phone cost are not being captured per store, none of
these are hard coded". Proves, with NO DB and stdlib only:

  A. BYTE-IDENTITY (tx): with `default_config()` ma_tx_bookings emits exactly the (line, amount)
     sequence residual_subs.ma_tx_pnl_bookings (mig 309) emits — every org that hasn't opted in
     keeps today's books to the penny, in the same add() order (identical incremental rounding).
  B. BYTE-IDENTITY (sheet): with defaults ma_commission_bookings reproduces coa's old inline
     component loop (re-implemented here from the pre-314 code) — heads, signs, wallet_funding
     clearing treatment, detail labels, ordering.
  C. STORE ATTRIBUTION: pl_ma_store_attribution=true carries the row's account id; blank/missing
     accounts and wallet_funding stay company-wide (None). Unmapped ≠ guessed.
  D. PRECEDENCE: a retail_cost books AT MOST ONCE — residual beats MDF beats month-spiff; the MDF
     match books to `mdf_income` with the product_name as drill label; merchant_discount always
     books regardless (different money).
  E. MONTH SPIFFS (cash basis): source='daily_tx' books configured order_type rows to carrier_comm
     with 'M<n>' detail via THE shared commission_ledger.parse_payment_month ('TBV MONTH 4' → M4,
     'M1 Proration' → M1, no token → 'Spiff (other)'), and SUPPRESSES the sheet's spiff_m1..m6 in
     ma_commission_bookings (no dollar can book at both the activation and the cash month) while
     rebate/margins/financing still book. source='commission_sheet' books none of the tx rows.
  F. ACCOUNT→STORE INDEX: derived from fulfillment (tspid, business_address); an ambiguous tspid
     (two addresses) is DROPPED (company-wide, honest); the ma_account_store_map override wins and
     can pin accounts fulfillment never names.
  G. LINE LABELS: apply_line_labels renames only known line keys ("Residual" on the Total side);
     unknown keys / empty values are ignored — a typo cannot invent a P&L line.
  H. MONEY GUARD: the tx path still reads ONLY merchant_discount + retail_cost as money
     (assert_money_columns still bites on identifiers like merchant_invoice).
  I. ADAPTIVE CONFIG: a malformed/missing config row (pre-mig-314) resolves to the defaults.
  J. LUXELINK-SHAPED END-TO-END: a mini fixture mirroring the Aug-2026 evidence books residual /
     MDF / month spiffs / rebate per store with the exact expected sums.

Run:  cd backend && python3 harness_ma_store_pnl.py
"""
import sys

sys.path.insert(0, ".")

from app.modules.account import ma_store_pnl as msp  # noqa: E402
from app.modules.account import residual_subs as rs  # noqa: E402
from app.modules.commcalc.calculator import safe_float  # noqa: E402

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


TX_ROWS = [
    # label-family residual
    {"product_name": "Trac Autopay Residual", "order_type": "Postpaid Residual Order",
     "retail_cost": -4.10, "merchant_discount": 0, "account_id": "170084"},
    # order-type residual, product lacks the word
    {"product_name": "Recurring line credit", "order_type": "Postpaid Residual Order",
     "retail_cost": -2.50, "merchant_discount": 0, "account_id": "170083"},
    # airtime row: merchant discount only
    {"product_name": "Total ALL ACCESS Plan $65", "order_type": "Activation Order",
     "retail_cost": 65.0, "merchant_discount": 5.53, "account_id": "170084"},
    # MDF market spiff (the $1,000-per-store rows)
    {"product_name": "Premium Store Spiff", "order_type": "Sales Order",
     "retail_cost": -1000.0, "merchant_discount": 0, "account_id": "170085"},
    # month-spiff cash rows
    {"product_name": "TBV MONTH 4 New Activation SPF", "order_type": "PostPaid Additional Spiff",
     "retail_cost": -25.0, "merchant_discount": 0, "account_id": "170084"},
    {"product_name": "New Activation Commission - M1 Proration",
     "order_type": "PostPaid Additional Spiff",
     "retail_cost": -3.75, "merchant_discount": 0, "account_id": "170083"},
    {"product_name": "Total Wireless Edge Upgrade Processing Fee",
     "order_type": "PostPaid Additional Spiff",
     "retail_cost": -10.0, "merchant_discount": 0, "account_id": "170083"},
    # blank account
    {"product_name": "Residual", "order_type": "Postpaid Residual Order",
     "retail_cost": -1.00, "merchant_discount": 0, "account_id": ""},
]

LUX_CFG = {"store_attribution": True, "month_spiff_source": "daily_tx",
           "spiff_order_types": ["PostPaid Additional Spiff"],
           "mdf_product_tokens": ["premium store spiff"],
           "line_labels": {"mi_income": "Residual"}}

print("A. byte-identity with mig-309 ma_tx_pnl_bookings under defaults")
for pnl_cfg in (None, rs.default_ma_pnl_config(),
                {"merchant_discount_own_line": False, "residual_order_types": []}):
    old = rs.ma_tx_pnl_bookings(TX_ROWS, pnl_cfg)
    new = [(l, a) for (l, _acct, a, _d) in msp.ma_tx_bookings(TX_ROWS, pnl_cfg, None)]
    check(f"identical (line, amount) sequence (cfg={pnl_cfg and sorted(pnl_cfg)!r})", old == new,
          f"{old[:4]} vs {new[:4]}")
new_default = msp.ma_tx_bookings(TX_ROWS, None, msp.default_config())
check("default config: every account is None (company-wide)",
      all(acct is None for (_l, acct, _a, _d) in new_default))
check("default config: no mdf_income / no carrier_comm rows",
      not any(l in ("mdf_income", "carrier_comm") for (l, *_r) in new_default))

print("B. byte-identity with coa's old inline raw_ma_commission loop under defaults")
COMM_ROWS = [
    {"merchant_account_id": "168874", "rebate": -500.0, "device_margin": -10.0,
     "consumer_margin": 0, "consumer_financing": -1.5, "wallet_funding": 250.0,
     "fees_margin": -2.0, "spiff_m1": -20.01, "spiff_m2": 0, "spiff_m3": 0,
     "spiff_m4": -5.0, "spiff_m5": 0, "spiff_m6": 0},
    {"merchant_account_id": "170405", "rebate": -179.99, "device_margin": 0,
     "consumer_margin": -1.0, "consumer_financing": 0, "wallet_funding": -30.0,
     "fees_margin": 0, "spiff_m1": -4.36, "spiff_m2": -2.0, "spiff_m3": 0,
     "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0},
]
_OLD_HEAD = {"rebate": ("device_rebate", -1), "device_margin": ("ma_device_margin", 1),
             "fees_margin": ("fee_income", 1), "consumer_financing": ("financing_income", 1),
             "consumer_margin": ("ma_device_margin", 1),
             "spiff_m1": ("carrier_comm", 1), "spiff_m2": ("carrier_comm", 1),
             "spiff_m3": ("carrier_comm", 1), "spiff_m4": ("carrier_comm", 1),
             "spiff_m5": ("carrier_comm", 1), "spiff_m6": ("carrier_comm", 1)}
_OLD_DETAIL = {"carrier_comm": "SPIFF / bounty",
               "device_rebate": "Device purchase rebates (Distributor/MA)"}


def _old_inline(rows):  # the pre-314 coa loop, verbatim semantics
    out = []
    for r in rows:
        for c in rs._MA_COMPONENTS:
            if c == "wallet_funding":
                out.append(("distributor_clearing", safe_float(r.get(c)), None))
                continue
            hs = _OLD_HEAD.get(c)
            if not hs:
                continue
            head, sign = hs
            out.append((head, sign * -safe_float(r.get(c)), _OLD_DETAIL.get(head)))
    return out


old = _old_inline(COMM_ROWS)
new = [(l, a, d) for (l, _acct, a, d) in msp.ma_commission_bookings(COMM_ROWS, None)]
check("identical (line, amount, detail) sequence", old == new, f"{old[:3]} vs {new[:3]}")
check("head map moved verbatim", msp.MA_COMMISSION_HEADS == _OLD_HEAD)
check("defaults: all accounts None", all(
    acct is None for (_l, acct, _a, _d) in msp.ma_commission_bookings(COMM_ROWS, None)))

print("C. store attribution carries the account id; blanks/wallet stay company-wide")
attr = msp.ma_tx_bookings(TX_ROWS, None, LUX_CFG)
check("residual row carries its account",
      ("mi_income", "170084", 4.10, None) in [(l, a, amt, d) for (l, a, amt, d) in attr])
check("blank account row books company-wide (None)",
      any(l == "mi_income" and a is None and amt == 1.00 for (l, a, amt, d) in attr))
cattr = msp.ma_commission_bookings(COMM_ROWS, dict(LUX_CFG))
check("sheet rebate carries its account",
      any(l == "device_rebate" and a == "168874" and amt == -500.0 for (l, a, amt, d) in cattr))
check("wallet_funding stays company-wide even under attribution",
      all(a is None for (l, a, amt, d) in cattr if l == "distributor_clearing"))

print("D. precedence — retail_cost books at most once; MDF books with product drill label")
mdf = [(l, a, amt, d) for (l, a, amt, d) in attr if l == "mdf_income"]
check("MDF row books once, $1000, per store, labelled",
      mdf == [("mdf_income", "170085", 1000.0, "Premium Store Spiff")], repr(mdf))
check("MDF row books to NO other retail_cost line",
      not any(amt == 1000.0 and l in ("mi_income", "carrier_comm") for (l, a, amt, d) in attr))
trick = [{"product_name": "Premium Store Spiff Residual", "order_type": "Sales Order",
          "retail_cost": -7.0, "merchant_discount": 0, "account_id": "1"}]
tb = msp.ma_tx_bookings(trick, None, LUX_CFG)
check("a row matching residual AND an MDF token books residual ONLY (precedence)",
      [(l, amt) for (l, _a, amt, _d) in tb if amt] == [("mi_income", 7.0)], repr(tb))

print("E. month spiffs — cash-basis source + sheet suppression")
spiffs = [(d, amt) for (l, _a, amt, d) in attr if l == "carrier_comm"]
check("TBV MONTH 4 -> M4 / M1 Proration -> M1 / no token -> Spiff (other)",
      spiffs == [("M4", 25.0), ("M1", 3.75), ("Spiff (other)", 10.0)], repr(spiffs))
check("source=commission_sheet books NO tx spiff rows",
      not any(l == "carrier_comm" for (l, *_r) in
              msp.ma_tx_bookings(TX_ROWS, None, dict(LUX_CFG, month_spiff_source="commission_sheet"))))
check("daily_tx suppresses the sheet's spiff_m1..m6",
      not any(l == "carrier_comm" for (l, *_r) in cattr))
check("…but rebate / margins / financing / clearing still book",
      {l for (l, *_r) in cattr} == {"device_rebate", "ma_device_margin", "financing_income",
                                    "fee_income", "distributor_clearing"})
check("explicit empty spiff_order_types books nothing even under daily_tx",
      not any(l == "carrier_comm" for (l, *_r) in
              msp.ma_tx_bookings(TX_ROWS, None, dict(LUX_CFG, spiff_order_types=[]))))

print("F. account→store index")
FUL = [{"tspid": "170084", "business_address": "4640a W Diversey Ave"},
       {"tspid": "170084", "business_address": "4640A W DIVERSEY AVE"},   # same, case drift
       {"tspid": "168874", "business_address": "957 Pennsylvania Ave"},
       {"tspid": "999", "business_address": "1 First St"},
       {"tspid": "999", "business_address": "2 Second St"},               # ambiguous -> dropped
       {"tspid": "", "business_address": "3 Third St"},
       {"tspid": "170085", "business_address": ""}]
idx = msp.account_store_index(FUL)
check("derived map keeps first spelling, case-insensitive dedup",
      idx.get("170084") == "4640a W Diversey Ave" and idx.get("168874") == "957 Pennsylvania Ave")
check("ambiguous tspid dropped (honest company-wide)", "999" not in idx)
check("blank tspid/address ignored", "" not in idx and "170085" not in idx)
idx2 = msp.account_store_index(FUL, [{"account_id": "999", "store_address": "1 First St"},
                                     {"account_id": "170405", "store_address": "531 Utica Ave"}])
check("override pins the ambiguous account and adds the unmapped one",
      idx2.get("999") == "1 First St" and idx2.get("170405") == "531 Utica Ave")

print("G. line labels")
lines = {"mi_income": {"label": "MI residual income"}, "wages": {}}
msp.apply_line_labels(lines, {"mi_income": "Residual", "nope": "X", "wages": "  "})
check("known key relabelled", lines["mi_income"]["label"] == "Residual")
check("unknown key cannot invent a line", "nope" not in lines)
check("empty label ignored", "label" not in lines["wages"])
check("no labels config is a no-op", msp.apply_line_labels(lines, None) is lines)

print("H. money guard")
try:
    rs.assert_money_columns(["merchant_invoice"], "harness")
    check("assert_money_columns still bites", False)
except ValueError:
    check("assert_money_columns still bites", True)
check("tx money columns unchanged (merchant_discount, retail_cost only)",
      list(rs._MA_PNL_MONEY_COLUMNS) == ["merchant_discount", "retail_cost"])

print("I. adaptive config resolution")


class _Stub:
    def __init__(self, rows=None, raise_=False):
        self._rows, self._raise = rows, raise_

    def schema(self, s):
        return self

    def table(self, t):
        return self

    def select(self, s):
        if self._raise:
            raise RuntimeError("no such column")
        return self

    def eq(self, k, v):
        return self

    def limit(self, n):
        return self

    def execute(self):
        from types import SimpleNamespace
        return SimpleNamespace(data=self._rows)


check("pre-314 (missing columns) -> defaults",
      msp.load_config(_Stub(raise_=True), "org") == msp.default_config())
check("no row -> defaults", msp.load_config(_Stub(rows=[]), "org") == msp.default_config())
bad = msp.load_config(_Stub(rows=[{"pl_ma_store_attribution": "yes",
                                   "pl_ma_month_spiff_source": "weird",
                                   "pl_ma_spiff_order_types": "not-a-list",
                                   "pl_mdf_product_tokens": 7,
                                   "pl_line_labels": ["not", "a", "dict"]}]), "org")
check("malformed values keep the defaults", bad == msp.default_config())
good = msp.load_config(_Stub(rows=[{"pl_ma_store_attribution": True,
                                    "pl_ma_month_spiff_source": "daily_tx",
                                    "pl_ma_spiff_order_types": [" PostPaid Additional Spiff "],
                                    "pl_mdf_product_tokens": ["premium store spiff", " "],
                                    "pl_line_labels": {"mi_income": "Residual", "": "x"}}]), "org")
check("valid values resolve (trimmed)",
      good["store_attribution"] is True and good["month_spiff_source"] == "daily_tx"
      and good["spiff_order_types"] == ["PostPaid Additional Spiff"]
      and good["mdf_product_tokens"] == ["premium store spiff"]
      and good["line_labels"] == {"mi_income": "Residual"})

print("J. luxelink-shaped end-to-end aggregation")
agg = {}
for l, acct, amt, d in (msp.ma_tx_bookings(TX_ROWS, None, LUX_CFG)
                        + msp.ma_commission_bookings(COMM_ROWS, dict(LUX_CFG))):
    if amt:
        agg.setdefault(l, {}).setdefault(acct, 0.0)
        agg[l][acct] = round(agg[l][acct] + amt, 2)
check("residual per store", agg["mi_income"] == {"170084": 4.10, "170083": 2.50, None: 1.00})
check("merchant discount per store", agg["ma_merchant_discount"] == {"170084": 5.53})
check("MDF per store", agg["mdf_income"] == {"170085": 1000.0})
check("month spiffs per store (M1-M12 detail proven in E)",
      agg["carrier_comm"] == {"170084": 25.0, "170083": 13.75})
check("rebate per store (contra-COGS, negative)",
      agg["device_rebate"] == {"168874": -500.0, "170405": -179.99})

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
