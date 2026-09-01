"""HARNESS — MA TX joins the multi-month payout formula (mig 308, Phase A; owner spec 2026-09-01).

For Total (plan-mode) tenants the VidaPay MA Daily Tx export (commcalc.raw_ma_daily_tx) becomes a
multi-month evidence source: the 'Activation Order' row's retail_cost IS the M1 MRC, and the
'TBV MONTH n …' product wording proves months 2..16 paid. A B2B sale reaches its MA TX rows through
a TWO-HOP join (raw_sales.serial_1 ↔ raw_ma_commission.imei|sim, digit-normalized →
raw_ma_commission.activation_order ↔ raw_ma_daily_tx.order_number).

Everything proven here is PURE — no database, no pandas, no network.

  python3 backend/harness_ma_tx_multimonth.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import sale_installment_engine as eng          # noqa: E402
from app.modules.commcalc import commission_ledger as cl                 # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


CFG = {"gate_source": "ma_tx", "ma_tx_activation_order_type": "Activation Order",
       "ma_max_month": 16, "ma_min_amount": 0.01, "ma_payout_sign": -1,
       "ma_month_field_prefix": "spiff_m", "ma_month1_extra_fields": ["rebate", "device_margin"]}

print("── A. 'MONTH n' parsing is the Commission Ledger's parser, REUSED — never a second regex ──")
check("engine re-exports commission_ledger.parse_payment_month (same function object)",
      eng.parse_payment_month is cl.parse_payment_month)
check("'TBV MONTH 5 New Activation SPF' → 5", cl.parse_payment_month("TBV MONTH 5 New Activation SPF") == 5)
check("'TBV MONTH 2 New Activation Commission' → 2",
      cl.parse_payment_month("TBV MONTH 2 New Activation Commission") == 2)
check("month 16 parses (the widened horizon)",
      cl.parse_payment_month("TBV MONTH 16 New Activation Commission") == 16)
check("a plain product name ('Total STARTER Plan $40') carries no month → None",
      cl.parse_payment_month("Total STARTER Plan $40") is None)
check("empty / None → None", cl.parse_payment_month("") is None and cl.parse_payment_month(None) is None)

print("── B. the TWO-HOP index builders (sale serial → ma_commission → activation_order → MA TX) ──")
# Realistic fixture: device A activates on order AO-1 (with an Excel-float IMEI in the MA file and a
# base+adjustment spiff pair that must NET); device B activates on order AO-2.
MA_ROWS = [
    {"imei": "355163568356973.0", "sim": "89014103279", "activation_order": "AO-1",
     "spiff_m2": -5.0, "spiff_m3": -5.0, "merchant_account_id": "MID1"},
    {"imei": "355163568356973", "activation_order": "AO-1", "spiff_m2": 5.0},   # adjustment: nets m2 → 0
    {"imei": "990001112223334", "activation_order": "AO-2", "spiff_m2": -7.0},
    {"imei": None, "sim": "", "activation_order": "AO-ORPHAN"},                 # no device key → skipped
    {"imei": "111112222233333", "activation_order": None},                      # no order → skipped
]
TX_ROWS = [
    {"order_number": "AO-1", "order_type": "Activation Order",
     "product_name": "Total STARTER Plan $40", "retail_cost": 40.0, "account_id": "ACCT9"},
    {"order_number": "AO-1", "order_type": "Commission",
     "product_name": "TBV MONTH 2 New Activation Commission", "retail_cost": -12.0, "account_id": "ACCT9"},
    {"order_number": "AO-1", "order_type": "SPF",
     "product_name": "TBV MONTH 5 New Activation SPF", "retail_cost": -3.0},
    {"order_number": "AO-1", "order_type": "Commission",
     "product_name": "TBV MONTH 16 New Activation Commission", "retail_cost": -2.0},
    # AO-2 (the OTHER device's activation) pays month 3 — must never gate device A.
    {"order_number": "AO-2", "order_type": "Activation Order",
     "product_name": "Total 5G+ Plan $60", "retail_cost": 60.0, "account_id": "ACCT7"},
    {"order_number": "AO-2", "order_type": "Commission",
     "product_name": "TBV MONTH 3 New Activation Commission", "retail_cost": -9.0, "account_id": "ACCT7"},
    {"order_number": None, "product_name": "TBV MONTH 2 stray"},                # no order → skipped
]
link = eng.build_ma_link_index(MA_ROWS)
tx = eng.build_ma_tx_index(TX_ROWS, CFG)
IDX = {"link": link, "tx": tx}
check("Excel-float IMEI digit-normalizes onto one key ('…973.0' + '…973' → one entry, one order)",
      link.get("355163568356973") == ["AO-1"], link.get("355163568356973"))
check("the SIM key links to the same order (either serial column joins)",
      link.get("89014103279") == ["AO-1"])
check("device B links to its own order only", link.get("990001112223334") == ["AO-2"])
check("row with no device key contributes nothing", "AO-ORPHAN" not in {o for v in link.values() for o in v})
check("tx index groups by order_number; month nets summed per order",
      tx["AO-1"]["months"] == {2: -12.0, 5: -3.0, 16: -2.0}, tx["AO-1"]["months"])
check("the Activation Order row is captured with its retail_cost (the MRC donor)",
      tx["AO-1"]["activation"]["retail_cost"] == 40.0)
check("order_number is NOT unique in the feed — one order carries activation + month rows, no raise",
      tx["AO-1"]["activation"]["count"] == 1 and len(tx) == 2)
check("missing links degrade: unknown serial → no orders, evidence (False, no_ma_tx_link), never a raise",
      eng.ma_tx_mrc_for("000000000000000", IDX) is None
      and eng.ma_tx_month_evidence("000000000000000", 2, IDX, CFG)
      == (False, {"matched": False, "reason": "no_ma_tx_link"}))
check("None/empty serial degrades to None too",
      eng.ma_tx_mrc_for(None, IDX) is None and eng.ma_tx_mrc_for("", IDX) is None)
check("builders never raise on junk rows",
      eng.build_ma_link_index(None) == {} and eng.build_ma_tx_index(None, None) == {}
      and eng.build_ma_tx_index([{}], {}) == {})

print("── C. M1 MRC from the Activation Order row's retail_cost; ladder fallback when unlinked ──")
hit = eng.ma_tx_mrc_for("355163568356973", IDX)
check("'Total STARTER Plan $40' activation row, retail_cost 40 → MRC 40.0",
      hit is not None and hit["mrc"] == 40.0, hit)
check("the hit carries order_number + account_id provenance for the ledger row",
      hit["order_number"] == "AO-1" and hit["account_id"] == "ACCT9", hit)
amount, mrc, src = eng._line_amount({"product_desc": "phone line"},
                                    {"payout_kind": "pct_mrc", "mrc_pct": 0.05},
                                    {}, None, mrc_override=(hit["mrc"], "ma_tx_activation"))
check("pct_mrc pays 5% of the MA TX MRC and stamps mrc_source='ma_tx_activation'",
      amount == 2.0 and mrc == 40.0 and src == "ma_tx_activation", (amount, mrc, src))
check("'ma_tx_activation' is an allowed installment_mrc_basis (existing values still allowed)",
      set(eng._MRC_BASIS_VALUES) == {"plan_line", "trigger_line", "ma_tx_activation"})
# The fall-through: an UNLINKED activation must still resolve through the existing plan-line ladder,
# never zero out. Same call the engine makes when ma_tx_mrc_for returns None.
matcher = eng._norm_plan_matcher(eng.DEFAULT_PLAN_LINE_MATCHER)
rk, mv, ms = eng._mrc_candidate({"product_desc": "Total ALL ACCESS Plan $65", "mdn": "5551234"},
                                {}, None, matcher)
check("unlinked chain falls through to the existing ladder (plan-line bare $ → 65.0, 'prefill')",
      (rk, mv, ms) == (2, 65.0, "prefill"), (rk, mv, ms))
neg = eng.build_ma_tx_index([{"order_number": "AO-N", "order_type": "Activation Order",
                              "product_name": "Total STARTER Plan $40", "retail_cost": -40.0}], CFG)
check("an export that signs the activation row negative still yields MRC 40.0 (abs — no direction on a charge)",
      eng.ma_tx_mrc_for("12345", {"link": {"12345": ["AO-N"]}, "tx": neg})["mrc"] == 40.0)
zero = eng.build_ma_tx_index([{"order_number": "AO-Z", "order_type": "Activation Order",
                               "product_name": "Freebie", "retail_cost": 0}], CFG)
check("an activation row with $0 retail_cost does NOT donate an MRC (falls through, never a $0 chain)",
      eng.ma_tx_mrc_for("12345", {"link": {"12345": ["AO-Z"]}, "tx": zero}) is None)
alt = eng.build_ma_tx_index(TX_ROWS, {"ma_tx_activation_order_type": "  aCtIvAtIoN oRdEr "})
check("the M1 order-type string is CONFIG (trimmed, case-insensitive) — never a hardcoded literal",
      alt["AO-1"]["activation"] is not None)
none_type = eng.build_ma_tx_index(TX_ROWS, {"ma_tx_activation_order_type": "Retailer Order"})
check("a different configured order type finds no activation rows in this feed",
      none_type["AO-1"]["activation"] is None)

print("── D. the paid gate — UNION of ma_commission spiffs and MA TX 'MONTH n' evidence ──")
SPIFF_IDX = eng._ma_gate_index(MA_ROWS)
SALE_A = {"serial_1": "355163568356973"}
SALE_B = {"serial_1": "990001112223334"}
met, ev = eng.ma_tx_month_evidence("355163568356973", 2, IDX, CFG)
check("month 2 gated by the MA TX 'MONTH 2' row (net -12 × sign -1 ≥ 0.01)",
      met and ev["reason"] == "paid" and ev["evidence"]["month_net"] == -12.0, ev)
check("ma_payout_sign respected: sign +1 flips the same net to a NON-payout",
      eng.ma_tx_month_evidence("355163568356973", 2, IDX, {**CFG, "ma_payout_sign": 1})[0] is False)
tiny = eng.build_ma_tx_index([{"order_number": "AO-1", "product_name": "TBV MONTH 2 x",
                               "retail_cost": -3.0}], CFG)
m, e2 = eng.ma_tx_month_evidence("355163568356973", 2, {"link": link, "tx": tiny},
                                 {**CFG, "ma_min_amount": 5.0})
check("ma_min_amount respected: a -$3 month-2 net does not clear a $5 minimum (no_month_payout)",
      m is False and e2["reason"] == "no_month_payout", e2)
check("…and clears a $2 minimum (same evidence, config decides)",
      eng.ma_tx_month_evidence("355163568356973", 2, {"link": link, "tx": tiny},
                               {**CFG, "ma_min_amount": 2.0})[0] is True)
check("ma_min_amount <= 0 clamps to the 0.01 code default (0 is NOT a no-minimum sentinel)",
      eng.ma_tx_month_evidence("355163568356973", 3, IDX, {**CFG, "ma_min_amount": 0})[0] is False)
claw = eng.build_ma_tx_index([{"order_number": "AO-1", "product_name": "TBV MONTH 2 base",
                               "retail_cost": -12.0},
                              {"order_number": "AO-1", "product_name": "TBV MONTH 2 reversal",
                               "retail_cost": 20.0}], CFG)
m, e2 = eng.ma_tx_month_evidence("355163568356973", 2, {"link": link, "tx": claw}, CFG)
check("base + over-reversal NETS to a charge → held as 'net_clawback', never paid",
      m is False and e2["reason"] == "net_clawback" and e2["evidence"]["month_net"] == 8.0, e2)
check("month 5 gates via the spiff-less MA TX row alone (no spiff_m5 anywhere)",
      eng._gate_met_ma_tx(SALE_A, SPIFF_IDX, IDX, 5, CFG)[0] is True)
# month ≤ 6 via the ma_commission spiff column ONLY (device B has no MA TX month-2 row):
m, e2 = eng._gate_met_ma_tx(SALE_B, SPIFF_IDX, IDX, 2, CFG)
check("month ≤6 still gates via the raw_ma_commission spiff column (union half (i), reused unchanged)",
      m is True and e2["evidence"]["ma_commission"].get("spiff_m2") == -7.0, e2)
check("device A's NETTED spiff (base −5 + adj +5 = 0) alone does NOT gate month 2…",
      eng._gate_met_ma(SALE_A, SPIFF_IDX, 2, CFG)[0] is False)
check("…but the union still pays month 2 from the MA TX evidence (either half suffices)",
      eng._gate_met_ma_tx(SALE_A, SPIFF_IDX, IDX, 2, CFG)[0] is True)
for n in (7, 16):
    m, e2 = eng._gate_met_ma_tx(SALE_A, SPIFF_IDX, IDX, n, CFG)
    want = (n == 16)  # fixture pays month 16 (and nothing in 7)
    check(f"month {n} is gate-able ONLY via MA TX (no spiff_m{n} column exists) → {'paid' if want else 'held'}",
          m is want, (n, m, e2))
m, e2 = eng._gate_met_ma_tx(SALE_A, SPIFF_IDX, IDX, 16, {**CFG, "ma_max_month": 6})
check("ma_max_month honored: with the default 6 the month-16 evidence is beyond the horizon",
      m is False and e2["reason"] == "month_beyond_ma_columns" and e2.get("max_month") == 6, e2)
m, e2 = eng.ma_tx_month_evidence("355163568356973", 3, IDX, CFG)
check("a 'MONTH 3' row on the WRONG serial's activation (AO-2) does NOT gate device A via MA TX",
      m is False and e2["reason"] == "no_month_payout" and e2["evidence"]["month_net"] == 0.0, e2)
check("…and device B IS gated by its own month-3 row",
      eng.ma_tx_month_evidence("990001112223334", 3, IDX, CFG)[0] is True)
m, e2 = eng.ma_tx_month_evidence("355163568356973", 1, IDX, CFG)
check("M1: the EXISTENCE of the linked Activation Order row itself is month-1 evidence",
      m is True and e2["evidence"]["activation_order_seen"] is True, e2)
only_m = eng.build_ma_tx_index([{"order_number": "AO-1", "product_name": "TBV MONTH 1 Proration",
                                 "retail_cost": -4.0}], CFG)
check("M1 also gates from a directed 'MONTH 1' row when no activation row is present",
      eng.ma_tx_month_evidence("355163568356973", 1, {"link": link, "tx": only_m}, CFG)[0] is True)
m, e2 = eng._gate_met_ma_tx({"serial_1": "777"}, SPIFF_IDX, IDX, 2, CFG)
check("a device in NEITHER statement reads honest 'no_ma_record'",
      m is False and e2["reason"] == "no_ma_record" and e2["matched"] is False, e2)
check("gate evidence carries order/account provenance for the ledger row",
      eng._gate_met_ma_tx(SALE_A, SPIFF_IDX, IDX, 2, CFG)[1].get("order_number") == "AO-1")

print("── E. horizon: 16 months is the schema ceiling; the horizon itself stays config ──")
check("MAX_SCHEDULE_MONTHS == 16 (matches the mig-308 CHECK)", eng.MAX_SCHEDULE_MONTHS == 16)
_compute_src = inspect.getsource(eng.compute_sale_installments)
check("compute no longer hardcodes min(12, …) — the clamp is MAX_SCHEDULE_MONTHS",
      "min(12," not in _compute_src and "MAX_SCHEDULE_MONTHS" in _compute_src)
check("gate config defaults carry the CONFIG home of the M1 order-type string (both modes)",
      all(c.get("ma_tx_activation_order_type") == "Activation Order"
          for c in eng._GATE_CFG_DEFAULTS.values())
      and "ma_tx_activation_order_type" in eng._GATE_CFG_KEYS)
check("plan-mode default gate_source is STILL 'ma_commission' — 'ma_tx' is an explicit config opt-in",
      eng._GATE_CFG_DEFAULTS["plan"]["gate_source"] == "ma_commission")
cfgd = eng._resolve_gate_cfg([{"carrier_id": "c1", "gate_source": "ma_tx",
                               "ma_max_month": 16, "ma_tx_activation_order_type": "Retailer Activation"}],
                             [], "c1", "plan")
check("a config row resolves gate_source='ma_tx' + ma_max_month=16 + a tenant order-type string",
      cfgd["gate_source"] == "ma_tx" and cfgd["ma_max_month"] == 16
      and cfgd["ma_tx_activation_order_type"] == "Retailer Activation", cfgd)

print("── F. money guard — merchant_invoice is an IDENTIFIER and is never summed by the new paths ──")
check("the MA TX select list excludes merchant_invoice (and everything not needed)",
      "merchant_invoice" not in eng._MA_TX_SELECT_COLS
      and set(eng._MA_TX_SELECT_COLS) == {"order_number", "order_type", "product_name",
                                          "retail_cost", "account_id"})
check("the ONLY MA TX money column the formula reads is retail_cost",
      eng._MA_TX_MONEY_COLS == ("retail_cost",))
_new_src = "".join(inspect.getsource(f) for f in (
    eng.build_ma_link_index, eng.build_ma_tx_index, eng.ma_tx_mrc_for,
    eng.ma_tx_month_evidence, eng._gate_met_ma_tx, eng._read_ma_tx))
check("no new code path READS merchant_invoice (no quoted column reference anywhere in them)",
      '"merchant_invoice"' not in _new_src and "'merchant_invoice'" not in _new_src)
_money_reads = [t for t in ("retail_cost", "merchant_discount", "merchant_invoice")
                if f'"{t}"' in _new_src or f"'{t}'" in _new_src]
check("the only money column the new paths reference is retail_cost", _money_reads == ["retail_cost"],
      _money_reads)
check("_persist keeps a NARROWER fallback tier: mig-308 columns can never cost the mig-258 audit trail",
      '("extended", cols + extra)' in inspect.getsource(eng._persist)
      and '("base", cols)' in inspect.getsource(eng._persist))

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
