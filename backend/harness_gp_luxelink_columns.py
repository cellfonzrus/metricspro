"""Proof harness — GP-report luxelink columns package (agent/commission/gp-luxelink-columns).

Runs the REAL calc_gp_report + the REAL router._compute_gp / _accessory_config_uncached / _is_accessory
against a fake Supabase client (filter-honoring, same convention as harness_core_bootstrap.py) — no DB,
no network. Run from backend/:  python3 harness_gp_luxelink_columns.py

Proves (owner directive 2026-07-29 "go"):
  LEGACY BYTE-IDENTICAL (house/Boost):
    1. config_classify=None + ma_income=None → department-only buckets exactly as before
       (Android-XP → Phone Sales, Ondigo → Acc GP, blank → Plan GP, unknown → Other).
    2. An org with ePay payment rows NEVER gets the MA fallback row, even when MA tables hold rows.
    3. accessory_config without the mig-250 column (or absent row) resolves apply_to_gp=False →
       _compute_gp passes config_classify=None.
  CONFIG MODE (apply_to_gp=true — the luxelink case):
    4. Accessory lines classify by CATEGORY through the org's Sales-Report rule: dept BrandedHandset
       splits — category HandsetBranded → Acc GP, category KittedBranded → Phone Sales (box dept).
    5. gp_category_map department overrides still win AFTER accessory (BrandedHandset→other override
       reroutes the phone line but can NOT swallow the accessory line).
    6. Blank dept → Plan GP; mapped dept (Rtr→plan) → Plan GP; unmapped named dept → Other.
    7. Rep rows use the same per-line rule.
    8. Transparency map shows BrandedHandset under BOTH accessory and device buckets.
  MA CARRIER-INCOME FALLBACK (ePay-less org):
    9. raw_payment_detail EMPTY → Commission column = sign-flipped Σ raw_ma_commission components
       (356.14) on ONE company-wide row; ATU = Σ merchant_discount (120.25); totals include both.
   10. ma_income=None → NO company-wide row (list lengths + totals unchanged).
   11. End-to-end _compute_gp for the luxelink-like org: buckets + MA row + totals all correct.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")

from app.modules.commcalc.gp_report import calc_gp_report  # noqa: E402
import app.modules.commcalc.router as rt  # noqa: E402
from app.modules.account.residual_subs import _MA_COMPONENTS  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def close(a, b):
    return abs(float(a) - float(b)) < 0.005


# ── fakes (filter-honoring, harness_core_bootstrap convention) ─────────────────────────────────
class FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def in_(self, col, vals):
        vals = set(vals)
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def execute(self):
        return SimpleNamespace(data=list(self._rows))

    def __getattr__(self, _name):          # select/order/... → chain
        return lambda *a, **k: self


class FakeClient:
    def __init__(self, tables):
        self.tables = tables

    def schema(self, _name):
        return self

    def table(self, name):
        return FakeQuery(self.tables.get(name, []))


LUX = "854f6d7b-0000-0000-0000-00000000lux0"
HOUSE = "00000000-0000-0000-0000-000000000001"
PERIOD = "2026-07"


def lux_sales():
    mk = lambda dept, cat, ext, gp: {"org_id": LUX, "period": PERIOD, "store": "123 Main St",
                                     "department": dept, "category": cat, "ext_price": ext, "gp": gp,
                                     "product_desc": "x", "salesperson": "Amy", "voided": "", "trans_type": "Sale"}
    return [
        mk("BrandedHandset", "KittedBranded", 100.0, 5.0),    # phone (box dept, non-accessory category)
        mk("BrandedHandset", "HandsetBranded", 25.0, 10.0),   # accessory BY CATEGORY (same dept!)
        mk("Handset", "Accessories", 15.0, 7.0),              # accessory by category
        mk("Handset", "SimMarketplace", 30.0, 2.0),           # named, unmapped, not box → other
        mk("Rtr", "", 40.0, 3.0),                             # gp_category_map: Rtr → plan
        mk("", "", 10.0, 4.0),                                # blank dept → plan
    ]


def lux_tables(gp_acc_basis="sales"):
    return {
        "raw_sales": lux_sales(),
        "raw_payment_detail": [],                             # ePay-less → MA fallback fires
        "raw_mi": [], "rep_commissions": [], "store_expenses": [], "raw_catalog": [],
        "store_mapping": [], "payment_categories": [], "raw_comp_report": [],
        "gp_category_map": [{"org_id": LUX, "department": "Rtr", "category": "plan"}],
        "accessory_config": [{"org_id": LUX, "departments": [], "categories":
                              ["HandsetBranded", "Accessories", "Accessory"], "product_keywords": [],
                              "acima_tenders": [], "box_departments": ["BrandedHandset"],
                              "apply_to_gp": True, "catalog_classify_enabled": False,
                              "gp_acc_basis": gp_acc_basis}],
        "raw_ma_commission": [
            {"org_id": LUX, "period": PERIOD, "device_margin": -200.14, "consumer_margin": -100.0},
            {"org_id": LUX, "period": PERIOD, "rebate": -56.0},
        ],
        "raw_ma_daily_tx": [
            {"org_id": LUX, "period": PERIOD, "merchant_discount": 100.25},
            {"org_id": LUX, "period": PERIOD, "merchant_discount": 20.0},
        ],
    }


def house_tables(gp_acc_basis=None):
    mk = lambda dept, ext, gp: {"org_id": HOUSE, "period": PERIOD, "store": "9 Elm St",
                                "department": dept, "category": "", "ext_price": ext, "gp": gp,
                                "product_desc": "x", "salesperson": "Bob", "voided": "", "trans_type": "Sale"}
    return {
        "raw_sales": [mk("Android - XP", 200.0, 8.0), mk("Ondigo", 20.0, 12.0),
                      mk("", 5.0, 6.0), mk("Weird", 9.0, 1.0)],
        "raw_payment_detail": [{"org_id": HOUSE, "period": PERIOD, "business_address": "9 Elm St",
                                "amount": 500.0, "payment_type": "Commission Payment"}],
        "payment_categories": [{"org_id": HOUSE, "description": "Commission Payment", "category": "Commission"}],
        "raw_mi": [], "rep_commissions": [], "store_expenses": [], "raw_catalog": [],
        "store_mapping": [], "raw_comp_report": [], "gp_category_map": [],
        # No row → resolver defaults (apply_to_gp False, and the HOUSE DEFAULT acc basis 'sales').
        # A row is supplied only when a test needs to pin the basis explicitly.
        "accessory_config": ([] if gp_acc_basis is None else
                             [{"org_id": HOUSE, "departments": ["ondigo"], "categories": [],
                               "product_keywords": [], "acima_tenders": [], "box_departments": [],
                               "apply_to_gp": False, "catalog_classify_enabled": False,
                               "gp_acc_basis": gp_acc_basis}]),
        # MA rows exist for the org — the gate (pay_detail non-empty) must still suppress the fallback:
        "raw_ma_commission": [{"org_id": HOUSE, "period": PERIOD, "device_margin": -999.0}],
        "raw_ma_daily_tx": [{"org_id": HOUSE, "period": PERIOD, "merchant_discount": 999.0}],
    }


# Bypass the TTL cache so each wire() resolves fresh (the REAL uncached resolver still runs, incl. the
# mig-250 apply_to_gp defensive read).
rt._accessory_config = lambda client, org_id: rt._accessory_config_uncached(client, org_id)

# ── 1+2+3: house/Boost legacy path through the REAL _compute_gp ────────────────────────────────
res_h = rt._compute_gp(FakeClient(house_tables()), HOUSE, PERIOD)
row_h = next((r for r in res_h["store_rows"] if r["store"] == "9 Elm St"), {})
check("1a. legacy buckets: Android-XP → Phone Sales", close(row_h.get("phone_sales"), 200.0))
# OWNER DIRECTIVE 2026-09-02 (commit 8ce5570d, mig 932): "Acc Gp should show the price at which the
# accessories were sold not the Gross profit as they are not entered correct … renamed to Acc Sales".
# The accessory bucket's basis became per-org config `accessory_config.gp_acc_basis` with HOUSE
# DEFAULT 'sales' — Σ ext_price (20.0 here) instead of Σ gp (12.0). This assertion pinned the old
# single answer, so it failed on the org default even though the report is correct. Both bases are
# now asserted, which is what actually protects the money: the config SELECTS the basis, and neither
# branch has drifted. (RULE TWO: this is a config column with a house default, not a tenant branch.)
check("1b. accessory basis 'sales' (house default) → Acc Sales = Σ ext_price 20.0",
      close(row_h.get("acc_gp"), 20.0), row_h.get("acc_gp"))
_row_h_gp = next((r for r in rt._compute_gp(FakeClient(house_tables("gp")), HOUSE, PERIOD)["store_rows"]
                  if r["store"] == "9 Elm St"), {})
check("1b2. accessory basis 'gp' (legacy) → Acc GP = Σ gp 12.0",
      close(_row_h_gp.get("acc_gp"), 12.0), _row_h_gp.get("acc_gp"))
check("1c. legacy buckets: blank → Plan GP, Weird → Other",
      close(row_h.get("plan_gp"), 6.0) and close(row_h.get("other_gp"), 1.0))
check("1d. ePay Commission category → Commission column", close(row_h.get("comm"), 500.0))
check("2. ePay org NEVER gets the MA company-wide row (gate = pay_detail non-empty)",
      not any("VidaPay" in str(r.get("store")) for r in res_h["store_rows"])
      and close(res_h["totals"]["comm"], 500.0))
acfg_h = rt._accessory_config_uncached(FakeClient(house_tables()), HOUSE)
check("3. absent accessory_config row → apply_to_gp False (config_classify None path)",
      acfg_h.get("apply_to_gp") is False and sorted(acfg_h["departments"]) == ["ondigo"])

# ── 4-9, 11: luxelink-like org end-to-end through the REAL _compute_gp ─────────────────────────
res_l = rt._compute_gp(FakeClient(lux_tables()), LUX, PERIOD)
row_l = next((r for r in res_l["store_rows"] if r["store"] == "123 Main St"), {})
check("4a. accessory BY CATEGORY: HandsetBranded + Accessories → Acc Sales 40 (basis 'sales')",
      close(row_l.get("acc_gp"), 40.0), row_l.get("acc_gp"))
_res_l_gp = rt._compute_gp(FakeClient(lux_tables("gp")), LUX, PERIOD)
_row_l_gp = next((r for r in _res_l_gp["store_rows"] if r["store"] == "123 Main St"), {})
check("4a2. same lines on basis 'gp' → Acc GP 17 (the legacy answer is intact under config)",
      close(_row_l_gp.get("acc_gp"), 17.0), _row_l_gp.get("acc_gp"))
check("4b. same dept, phone category → Phone Sales 100 (box dept)", close(row_l.get("phone_sales"), 100.0))
check("6a. Rtr→plan map + blank dept → Plan GP 7", close(row_l.get("plan_gp"), 7.0))
check("6b. SimMarketplace line (named, unmapped, not box) → Other 2", close(row_l.get("other_gp"), 2.0))
ma_row = next((r for r in res_l["store_rows"] if "VidaPay" in str(r.get("store"))), None)
check("9a. ePay-less → ONE company-wide MA row", ma_row is not None
      and sum(1 for r in res_l["store_rows"] if "VidaPay" in str(r.get("store"))) == 1)
check("9b. commission received (sign-flipped Σ components) → Commission column 356.14",
      ma_row is not None and close(ma_row.get("comm"), 356.14))
check("9c. airtime margin → ATU column 120.25", ma_row is not None and close(ma_row.get("atu"), 120.25))
check("9d. totals include the MA row", close(res_l["totals"]["comm"], 356.14)
      and close(res_l["totals"]["atu"], 120.25) and close(res_l["totals"]["acc_gp"], 40.0),
      res_l["totals"])
check("11. MA row books clean (no phantom store fields; net = comm+atu)",
      ma_row is not None and ma_row.get("store_code") == "" and close(ma_row.get("net_profit"), 476.39))
rep_amy = next((r for r in res_l["rep_rows"] if r["rep"] == "Amy"), {})
check("7. rep rows use the same per-line rule (Amy acc 40 / phones 100 / plan 7)",
      close(rep_amy.get("acc_gp"), 40.0) and close(rep_amy.get("phone_sales"), 100.0)
      and close(rep_amy.get("plan_gp"), 7.0), rep_amy)
_amy_gp = next((r for r in _res_l_gp["rep_rows"] if r["rep"] == "Amy"), {})
check("7b. rep rows follow the SAME configured basis as the store rows (basis 'gp' → Amy acc 17)",
      close(_amy_gp.get("acc_gp"), 17.0), _amy_gp.get("acc_gp"))
bc = res_l.get("bucket_composition") or {}
check("8. transparency: BrandedHandset appears under BOTH accessory and device",
      any(x["department"] == "BrandedHandset" for x in bc.get("accessory", []))
      and any(x["department"] == "BrandedHandset" for x in bc.get("device", [])))

# ── 5: override precedence (pure calc_gp_report — accessory wins, then the dept override) ──────
sales5 = lux_sales()
cc = {"is_accessory": lambda r: str(r.get("category") or "").lower() in
                                 ("handsetbranded", "accessories", "accessory"),
      "box_departments": {"BrandedHandset"}}
res5 = calc_gp_report(sales5, [], [], [], [], [], [], PERIOD,
                      gp_category_map=[{"department": "BrandedHandset", "category": "other"},
                                       {"department": "Rtr", "category": "plan"}],
                      config_classify=cc)
row5 = next((r for r in res5["store_rows"] if r["store"] == "123 Main St"), {})
check("5a. accessory beats the dept override (HandsetBranded line stays Acc GP)",
      close(row5.get("acc_gp"), 17.0))
check("5b. dept override beats box (KittedBranded phone line rerouted to Other, not device)",
      close(row5.get("phone_sales"), 0.0) and close(row5.get("other_gp"), 5.0 + 2.0))

# ── 10: no ma_income → no synthetic row (pure) ─────────────────────────────────────────────────
res10 = calc_gp_report(lux_sales(), [], [], [], [], [], [], PERIOD, config_classify=cc)
check("10. ma_income=None → no company-wide row",
      not any("VidaPay" in str(r.get("store")) for r in res10["store_rows"]))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
