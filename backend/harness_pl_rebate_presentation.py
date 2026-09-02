"""HARNESS — P&L rebate presentation (owner report 2026-09-02, mig 934).

"rebate is coming in negative, it should be a positive number as it is coming in."

Proves, with NO DB and stdlib only:

  A. HOUSE DEFAULT BYTE-IDENTITY: with default/absent/malformed config, ma_commission_bookings
     routes the rebate component exactly as pre-934 — ('device_rebate', −1), i.e. the contra-COGS
     ruling K1 — and every OTHER component's routing is untouched by the new config in BOTH modes.
  B. INCOME MODE: rebate_presentation='income' books the SAME rebate dollars POSITIVE on
     `rebate_income` (feed negative = money to the dealer ⇒ +abs), with the same store
     attribution and the same drill-down detail label.
  C. INVARIANCE: for any row set, gross profit and net income (revenue − cogs, per coa.PL_SPEC
     sections) are IDENTICAL under both presentations — only section subtotals move.
  D. ONE ROUTE FOR BOTH SOURCES: rebate_route() is the single resolver the MA-sheet path and the
     activation-rebate-ledger path share; ledger arithmetic (sign × positive ledger amount)
     reproduces the pre-934 `-amount` under the default and +amount under 'income'.
  E. ADAPTIVE CONFIG: load_config survives a live DB that does not yet have the mig-934 column —
     the select falls back to the mig-314 column set WITHOUT dropping the mig-314 values (the
     regression that would silently un-seed a live org), and only then to defaults; an unknown
     presentation value keeps the default.
  F. ZERO-LINE SUPPRESSION: engine._assemble drops a spec line marked suppress_zero ONLY when it
     carries no amount and no detail; dollars or drill-down always render; unmarked lines are
     byte-identical (a zero 'auto' line still renders).
  G. SPEC: `rebate_income` exists in coa.PL_SPEC as an auto_opt REVENUE line (materialises only
     where it carries value ⇒ every non-opted org's payload is byte-identical).

Run:  cd backend && python3 harness_pl_rebate_presentation.py
"""
import sys
import types

sys.path.insert(0, ".")

# app.core.config needs pydantic_settings; a class-attribute stub is enough for import (the
# harness never reads a setting) — same offline-proof trick harness_statement_engine uses.
if "pydantic_settings" not in sys.modules:
    stub = types.ModuleType("pydantic_settings")

    class _BaseSettings:                                   # noqa: D401 — minimal import stub
        def __init__(self, **kw):
            pass

    stub.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = stub

from app.modules.account import ma_store_pnl as msp  # noqa: E402
from app.modules.account import coa, engine  # noqa: E402

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


# Luxelink-shaped MA commission sheet rows (feed sign: NEGATIVE = paid TO the dealer).
SHEET_ROWS = [
    {"merchant_account_id": "170084", "rebate": -429.01, "device_margin": -20.0,
     "spiff_m1": -9.20, "spiff_m2": -41.25, "wallet_funding": 18.39},
    {"merchant_account_id": "168874", "rebate": -500.00, "fees_margin": -12.50},
    {"merchant_account_id": "170405", "rebate": -75.00},           # unmapped account
    {"merchant_account_id": "170073", "consumer_financing": -8.00},  # no rebate at all
]

CFG_INCOME = dict(msp.default_config(), rebate_presentation="income",
                  store_attribution=True)
CFG_CONTRA = dict(msp.default_config(), store_attribution=True)

PL_SECTION = {k: sec for k, _lbl, sec, *_ in coa.PL_SPEC}


def net_income_proxy(bookings):
    """revenue − cogs − opex over P&L bookings (coa.PL_SPEC sections; BS lines ignored)."""
    total = 0.0
    for line, _acct, amt, _d in bookings:
        sec = PL_SECTION.get(line)
        if sec == "revenue":
            total += amt
        elif sec in ("cogs", "opex", "other"):
            total -= amt
    return round(total, 2)


def by_line(bookings):
    """Non-zero aggregation per (line, account) — coa.add() drops zero amounts the same way."""
    agg = {}
    for line, acct, amt, _d in bookings:
        if not amt:
            continue
        agg.setdefault(line, {})
        agg[line][acct] = round(agg[line].get(acct, 0.0) + amt, 2)
    return agg


print("A. house default byte-identity")
d = msp.ma_commission_bookings(SHEET_ROWS, CFG_CONTRA)
check("rebate books on device_rebate, negative (K1)",
      by_line(d)["device_rebate"] == {"170084": -429.01, "168874": -500.0, "170405": -75.0})
check("rebate_route defaults to contra",
      msp.rebate_route(msp.default_config()) == ("device_rebate", -1)
      and msp.rebate_route(None) == ("device_rebate", -1)
      and msp.rebate_route({"rebate_presentation": "bogus"}) == ("device_rebate", -1))
check("device_rebate detail label unchanged",
      any(dl == "Device purchase rebates (Distributor/MA)"
          for (ln, _a, _amt, dl) in d if ln == "device_rebate"))

print("B. income mode")
i = msp.ma_commission_bookings(SHEET_ROWS, CFG_INCOME)
check("rebate books on rebate_income, positive, same stores",
      by_line(i)["rebate_income"] == {"170084": 429.01, "168874": 500.0, "170405": 75.0})
check("no device_rebate bookings in income mode", "device_rebate" not in by_line(i))
check("income detail label mirrors the contra one",
      any(dl == "Device purchase rebates (Distributor/MA)"
          for (ln, _a, _amt, dl) in i if ln == "rebate_income"))
_strip = lambda bk: [(ln, a, amt, dl) for (ln, a, amt, dl) in bk
                     if ln not in ("device_rebate", "rebate_income")]
check("every non-rebate booking byte-identical across modes", _strip(d) == _strip(i))

print("C. gross-profit / net-income invariance")
check("net income identical under both presentations",
      net_income_proxy(d) == net_income_proxy(i),
      f"{net_income_proxy(d)} vs {net_income_proxy(i)}")
rev_d = round(sum(a for (ln, _x, a, _dl) in d if PL_SECTION.get(ln) == "revenue"), 2)
rev_i = round(sum(a for (ln, _x, a, _dl) in i if PL_SECTION.get(ln) == "revenue"), 2)
cogs_d = round(sum(a for (ln, _x, a, _dl) in d if PL_SECTION.get(ln) == "cogs"), 2)
cogs_i = round(sum(a for (ln, _x, a, _dl) in i if PL_SECTION.get(ln) == "cogs"), 2)
check("revenue and COGS move by the SAME amount (+1,004.01)",
      round(rev_i - rev_d, 2) == 1004.01 and round(cogs_d - cogs_i, 2) == -1004.01,
      f"rev Δ{round(rev_i - rev_d, 2)} cogs Δ{round(cogs_d - cogs_i, 2)}")

print("D. one route for both sources (activation-rebate ledger arithmetic)")
for cfg, want in ((CFG_CONTRA, -818.0), (CFG_INCOME, 818.0)):
    ln, sign = msp.rebate_route(cfg)
    check(f"ledger 818.00 books {want} on {ln}", round(sign * 818.0, 2) == want)

print("E. adaptive config (live DB without the mig-934 column)")


class _Sel:
    def __init__(self, rows, fail_cols):
        self._rows, self._fail = rows, fail_cols

    def schema(self, _s):
        return self

    def table(self, _t):
        return self

    def select(self, cols):
        if self._fail(cols):
            raise RuntimeError("42703 column does not exist")
        return self

    def eq(self, *_a):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        return types.SimpleNamespace(data=self._rows)


ROW_314 = {"pl_ma_store_attribution": True, "pl_ma_month_spiff_source": "daily_tx",
           "pl_ma_spiff_order_types": ["PostPaid Additional Spiff"],
           "pl_mdf_product_tokens": ["premium store spiff"],
           "pl_line_labels": {"mi_income": "Residual"}}
cfg = msp.load_config(_Sel([ROW_314], lambda c: "pl_rebate_presentation" in c), "org")
check("pre-934 DB keeps ALL mig-314 seeds (store attribution, tokens, labels)",
      cfg["store_attribution"] is True and cfg["month_spiff_source"] == "daily_tx"
      and cfg["mdf_product_tokens"] == ["premium store spiff"]
      and cfg["line_labels"] == {"mi_income": "Residual"}
      and cfg["rebate_presentation"] == "contra_cogs")
cfg = msp.load_config(_Sel([dict(ROW_314, pl_rebate_presentation="income")],
                           lambda c: False), "org")
check("post-934 DB resolves income", cfg["rebate_presentation"] == "income")
cfg = msp.load_config(_Sel([dict(ROW_314, pl_rebate_presentation="INCOME ")],
                           lambda c: False), "org")
check("value is trimmed + case-folded", cfg["rebate_presentation"] == "income")
cfg = msp.load_config(_Sel([dict(ROW_314, pl_rebate_presentation="sideways")],
                           lambda c: False), "org")
check("unknown value keeps the default", cfg["rebate_presentation"] == "contra_cogs")
cfg = msp.load_config(_Sel([], lambda c: True), "org")
check("no readable column set at all → full defaults", cfg == msp.default_config())

print("F. engine._assemble suppress_zero passthrough")
PL_SECTIONS = [("Revenue", "revenue"), ("Cost of Goods Sold", "cogs"),
               ("Operating Expenses", "opex"), ("Other", "other")]


def blank_inputs():
    return {k: {"by_store": {}, "company_wide": 0.0, "detail": {}}
            for k, *_ in coa.PL_SPEC + coa.BS_SPEC}


def pl_keys(inputs):
    pl = engine._assemble(inputs, [], coa.PL_SPEC, coa.PL_LABEL, PL_SECTIONS,
                          "consolidated", None, True)
    return {ln["key"] for sec in pl["sections"] for ln in sec["lines"]}, pl


inp = blank_inputs()
keys, _ = pl_keys(inp)
check("unmarked zero 'auto' line still renders (byte-identity)", "device_rebate" in keys)
check("auto_opt rebate_income absent when empty", "rebate_income" not in keys)
inp = blank_inputs()
inp["device_rebate"]["suppress_zero"] = True
keys, _ = pl_keys(inp)
check("suppress_zero drops the empty contra line", "device_rebate" not in keys)
inp = blank_inputs()
inp["device_rebate"]["suppress_zero"] = True
inp["device_rebate"]["company_wide"] = -75.0
keys, _ = pl_keys(inp)
check("suppress_zero NEVER hides dollars", "device_rebate" in keys)
inp = blank_inputs()
inp["rebate_income"]["by_store"] = {"104-08 Lefferts Blvd": 429.01}
inp["device_rebate"]["suppress_zero"] = True
keys, pl = pl_keys(inp)
rev = next(s for s in pl["sections"] if s["type"] == "revenue")
check("income mode end-shape: positive revenue line, no contra line",
      "rebate_income" in keys and "device_rebate" not in keys
      and rev["subtotal"] == 429.01 and pl["net_income"] == 429.01)

print("G. spec")
spec = {k: (sec, kind) for k, _l, sec, kind, _g in coa.PL_SPEC}
check("rebate_income is an auto_opt revenue line",
      spec.get("rebate_income") == ("revenue", "auto_opt"))
check("device_rebate stays an auto cogs line (K1 default untouched)",
      spec.get("device_rebate") == ("cogs", "auto"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
