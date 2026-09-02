"""Proof harness — DM-verified store cash on the Balance Sheet (owner 2026-09-02, item 4;
mig 938).

Proves, stdlib-only and DB-free:
  A. config: house default 'off'; resolve order; spec line present as auto_opt/store.
  B. store_cash_cells (PURE): the 'verified' basis counts ONLY DM-verified store-days as
     collected (unverified dollars EXCLUDED and reported in meta — never silently dropped); ALL
     outflows subtract regardless (cash that left is gone); 'all' basis == the operational
     cash-position math; 'off' books nothing; as-of cutoff; negative balances preserved (a real
     signal, never clamped); zero-balance stores dropped (auto_opt line stays empty-clean).
  C. cash flow: `store_cash_on_hand` is CASH — excluded from operating deltas, summed into
     cash_begin/cash_end next to the manual bank line; a pre-938 payload (no such line) is
     byte-identical; the deposit lifecycle ties out (store cash down + bank cash up ⇒ reported
     cash delta unchanged).

Run: python3 backend/harness_verified_cash_bs.py
"""
import sys
import os
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

# statement_engine imports coa → app.core config chain; stub the app config like the sibling
# statement-engine harness does NOT need to — balance_sheet + the cash_flow function are reachable
# with only calculator.safe_float. Import the pure pieces directly.
from app.modules.account import balance_sheet  # noqa: E402
from app.modules.account.balance_sheet import store_cash_cells, load_bs_config  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


print("A. spec + config defaults")
spec = {k: (sec, kind, grain) for k, _lbl, sec, kind, grain in balance_sheet.EXTRA_BS_SPEC}
check("store_cash_on_hand line: asset / auto_opt / store grain",
      spec.get("store_cash_on_hand") == ("asset", "auto_opt", "store"))
check("house default basis 'off'", balance_sheet.default_bs_config()["cash_on_hand_basis"] == "off")
check("bases registry", balance_sheet.CASH_ON_HAND_BASES == ("off", "verified", "all"))


class _FailingClient:
    def schema(self, *_a, **_k):
        raise RuntimeError("no db in harness")


check("pre-938 DB (read fails) degrades to 'off'",
      load_bs_config(_FailingClient(), "org")["cash_on_hand_basis"] == "off")

print("B. store_cash_cells")
decl = {"S1": {"2026-08-13": 500.0, "2026-08-14": 300.0, "2026-08-20": 999.0},
        "S2": {"2026-08-13": 200.0}}
taken = {"S1": {"2026-08-14": 100.0}, "S2": {"2026-08-15": 200.0}}
vkeys = {("S1", "2026-08-13"), ("S1", "2026-08-14")}

cells, meta = store_cash_cells(decl, taken, vkeys, "verified", "2026-08-31")
check("verified basis: only verified days count as collected",
      cells.get("S1") == 700.0, str(cells))
check("unverified declared reported, not booked",
      meta["unverified_declared"] == 999.0 + 200.0 and meta["unverified_days"] == 2)
check("outflows subtract regardless of verification (negative preserved)",
      cells.get("S2") == -200.0)
check("meta total ties to cells", meta["total"] == round(sum(cells.values()), 2))

cells_all, meta_all = store_cash_cells(decl, taken, set(), "all", "2026-08-31")
check("'all' basis = operational math (decl − taken, every day)",
      cells_all.get("S1") == 1699.0 and "S2" not in cells_all)
check("zero-balance store dropped (S2: 200 − 200 = 0)", "S2" not in cells_all)

cells_cut, _m = store_cash_cells(decl, taken, set(), "all", "2026-08-13")
check("as-of cutoff excludes later movement", cells_cut.get("S1") == 500.0 and cells_cut.get("S2") == 200.0)

off_cells, off_meta = store_cash_cells(decl, taken, vkeys, "off", "2026-08-31")
check("'off' books nothing (byte-identical default)", off_cells == {})
check("no as_of books nothing", store_cash_cells(decl, taken, vkeys, "verified", None)[0] == {})

print("C. cash flow — store_cash_on_hand is CASH")
# The cash_flow function lives in statement_engine, which imports coa (needs the app config chain
# at import). Stub the settings module chain minimally so the PURE function is importable.
try:
    from app.modules.account import statement_engine
except Exception:
    # Provide a minimal app.core.config stub if the environment lacks one, then retry.
    cfgmod = types.ModuleType("app.core.config")
    cfgmod.settings = types.SimpleNamespace(ANTHROPIC_API_KEY="", ACCOUNT_ENGINE_MODEL="")
    sys.modules.setdefault("app.core.config", cfgmod)
    from app.modules.account import statement_engine

check("CF_CASH_KEYS carries both cash lines",
      statement_engine.CF_CASH_KEYS == ("cash", "store_cash_on_hand"))
check("both excluded from operating deltas",
      set(statement_engine.CF_CASH_KEYS) <= statement_engine.CF_EXCLUDED)


def _bs(cash, store_cash, inventory):
    lines = [{"key": "cash", "label": "Cash / bank", "amount": cash, "kind": "manual"},
             {"key": "inventory", "label": "Inventory", "amount": inventory, "kind": "auto"}]
    if store_cash is not None:
        lines.append({"key": "store_cash_on_hand", "label": "Cash on hand — stores (undeposited)",
                      "amount": store_cash, "kind": "auto_opt"})
    return {"sections": [{"name": "Assets", "type": "asset", "lines": lines, "subtotal": 0}]}


pl = {"net_income": 100.0}
# Deposit lifecycle: $400 moves store cash → bank. Total cash unchanged; the CF must agree.
cf = statement_engine.cash_flow(pl, _bs(1400.0, 100.0, 50.0), _bs(1000.0, 500.0, 50.0),
                                "September 2026", "consolidated", "Consolidated")
check("deposit (store→bank) leaves reported cash delta at 0",
      cf["cash_begin"] == 1500.0 and cf["cash_end"] == 1500.0 and cf["cash_delta_reported"] == 0.0)
check("store-cash delta is NOT an operating adjustment",
      all("store_cash_on_hand" != l.get("key") for s in cf["sections"] for l in s["lines"]))
check("operating carries net income + inventory delta only",
      any(l["key"] == "net_income" for l in cf["sections"][0]["lines"]))

# Pre-938 payload (no store_cash line at all) — byte-identical cash math.
cf_old = statement_engine.cash_flow(pl, _bs(1400.0, None, 50.0), _bs(1000.0, None, 50.0),
                                    "September 2026", "consolidated", "Consolidated")
check("pre-938 payload: cash math unchanged (missing key sums as 0)",
      cf_old["cash_begin"] == 1000.0 and cf_old["cash_end"] == 1400.0
      and cf_old["cash_delta_reported"] == 400.0)

# Verified collection lifecycle: store cash up $250 (verified collections), bank untouched —
# reported cash delta = +250, and the implied side sees it only through net income/operating.
cf2 = statement_engine.cash_flow(pl, _bs(1000.0, 250.0, 50.0), _bs(1000.0, 0.0, 50.0),
                                 "September 2026", "consolidated", "Consolidated")
check("verified collections raise cash & equivalents", cf2["cash_delta_reported"] == 250.0)

print()
if FAILS:
    print(f"❌ {len(FAILS)} failure(s): {FAILS}")
    sys.exit(1)
print("✅ harness_verified_cash_bs: ALL PASS")
