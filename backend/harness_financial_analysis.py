"""Proof harness — financial-analysis series assembly (roadmap Phase 3). Stdlib only.

Proves app/modules/account/analysis.py (pure) on fixture snapshot rows shaped exactly like
`commcalc.account_statements`:

  A. ONE MATH PATH — headline numbers (revenue/GP/NI, cash, assets) come from the snapshot
     payloads verbatim; section subtotals are read, never re-derived from raw sources.
  B. month axis — chronological, canonical month-name labels, trailing window respected; the SAME
     month stored under BOTH period spellings ('August 2026' + '2026-08') counts ONCE with the
     freshest computed_at winning (the finance-wide spelling duality).
  C. ratios — margin percentages carry None (a chart gap), never a fake 0, when revenue is 0.
  D. OPEX composition — per-month breakdown keys sum exactly to the P&L opex subtotal; the latest
     composition percentages sum to ~100.
  E. scope comparison series — per-company / per-store series align on the shared month axis with
     0-filled gaps; consolidated rows never leak into them.
  F. drift pin — analysis.CASH_KEYS == statement_engine.CF_CASH_KEYS (cash & equivalents must be
     the same pair of lines the Cash Flow statement calls cash).
  G. empty input — computed:false with the honest note, never a crash.

Run:  cd backend && python3 harness_financial_analysis.py
"""
import sys
import types

sys.path.insert(0, "app")

if "pydantic_settings" not in sys.modules:      # app.core.config import stub (statement_engine pin)
    stub = types.ModuleType("pydantic_settings")

    class _BaseSettings:
        def __init__(self, **kw):
            pass

    stub.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = stub

from app.modules.account import analysis  # noqa: E402
from app.modules.account import statement_engine as SE  # noqa: E402

FAIL = 0


def ok(name, cond, detail=None):
    global FAIL
    mark = "PASS" if cond else "FAIL"
    if not cond:
        FAIL += 1
    print(f"  [{mark}] {name}" + ("" if cond else f"  << {detail}"))


def pl_payload(rev_lines, cogs, opex_lines, other=0.0):
    rev = round(sum(a for _, a in rev_lines), 2)
    opex = round(sum(a for _, a in opex_lines), 2)
    gp = round(rev - cogs, 2)
    return {"statement_type": "pl",
            "sections": [
                {"type": "revenue", "subtotal": rev,
                 "lines": [{"key": k, "label": k, "amount": a} for k, a in rev_lines]},
                {"type": "cogs", "subtotal": cogs,
                 "lines": [{"key": "device_cost", "label": "Device cost", "amount": cogs}]},
                {"type": "opex", "subtotal": opex,
                 "lines": [{"key": k, "label": k.replace("_", " ").title(), "amount": a}
                           for k, a in opex_lines]},
                {"type": "other", "subtotal": other,
                 "lines": ([{"key": "je_taxes", "label": "Income taxes", "amount": other}]
                           if other else [])},
            ],
            "gross_profit": gp, "net_operating_income": round(gp - opex, 2),
            "net_income": round(gp - opex - other, 2)}


def bs_payload(cash, store_cash, inventory, assets, liab, equity):
    return {"statement_type": "balance_sheet",
            "sections": [
                {"type": "asset", "subtotal": assets,
                 "lines": [{"key": "cash", "label": "Cash / bank", "amount": cash},
                           {"key": "store_cash_on_hand", "label": "Cash on hand — stores",
                            "amount": store_cash},
                           {"key": "inventory", "label": "Inventory", "amount": inventory}]},
                {"type": "liability", "subtotal": liab,
                 "lines": [{"key": "handset_payable", "label": "Handset payables", "amount": liab}]},
                {"type": "equity", "subtotal": equity,
                 "lines": [{"key": "owner_capital", "label": "Owner capital", "amount": equity}]},
            ],
            "assets_total": assets, "liabilities_total": liab, "equity_total": equity}


def row(period, st, scope, payload, computed_at="2026-09-01T00:00:00+00:00", label=None):
    return {"period": period, "statement_type": st, "scope_key": scope,
            "scope_label": label or scope, "payload": payload, "computed_at": computed_at}


# ── fixtures: June/July/August 2026 ─────────────────────────────────────────────────────────────
ROWS = [
    # June — zero revenue month (ratio None case)
    row("June 2026", "pl", "consolidated",
        pl_payload([("carrier_comm", 0.0)], 0.0, [("wages", 1000.0)])),
    row("June 2026", "balance_sheet", "consolidated",
        bs_payload(5000.0, 0.0, 0.0, 5000.0, 0.0, 5000.0)),
    # July
    row("July 2026", "pl", "consolidated",
        pl_payload([("carrier_comm", 80000.0), ("accessory_rev", 20000.0)], 40000.0,
                   [("wages", 25000.0), ("rep_comm", 10000.0), ("store_opex", 5000.0)])),
    row("July 2026", "balance_sheet", "consolidated",
        bs_payload(10000.0, 2000.0, 150000.0, 162000.0, 100000.0, 62000.0)),
    # August under BOTH spellings — numeric spelling is STALE, month-name is fresher and must win
    row("2026-08", "pl", "consolidated",
        pl_payload([("carrier_comm", 1.0)], 0.0, [("wages", 1.0)]),
        computed_at="2026-08-15T00:00:00+00:00"),
    row("August 2026", "pl", "consolidated",
        pl_payload([("carrier_comm", 90000.0), ("accessory_rev", 30000.0)], 48000.0,
                   [("wages", 30000.0), ("rep_comm", 12000.0), ("store_opex", 6000.0)], other=2000.0),
        computed_at="2026-09-02T04:00:00+00:00"),
    row("August 2026", "balance_sheet", "consolidated",
        bs_payload(15000.0, 3000.0, 166000.0, 184000.0, 110000.0, 74000.0),
        computed_at="2026-09-02T04:00:00+00:00"),
    # company + store scopes (August only for company B / store 2 — gap-fill case)
    row("July 2026", "pl", "company:a", pl_payload([("carrier_comm", 60000.0)], 20000.0,
                                                   [("wages", 15000.0)]), label="Luxlink Wireless"),
    row("August 2026", "pl", "company:a", pl_payload([("carrier_comm", 70000.0)], 25000.0,
                                                     [("wages", 18000.0)]), label="Luxlink Wireless"),
    row("August 2026", "pl", "company:b", pl_payload([("carrier_comm", 50000.0)], 23000.0,
                                                     [("wages", 12000.0)]), label="Nova Wave"),
    row("July 2026", "pl", "store:1 Main St", pl_payload([("accessory_rev", 9000.0)], 3000.0,
                                                         [("wages", 2000.0)])),
    row("August 2026", "pl", "store:2 Oak Ave", pl_payload([("accessory_rev", 7000.0)], 2500.0,
                                                           [("wages", 1500.0)])),
]

out = analysis.assemble(ROWS, months=12)

print("A/B. month axis + spelling dedupe")
ok("computed with chronological canonical labels",
   out["computed"] and out["months"] == ["June 2026", "July 2026", "August 2026"], out["months"])
aug = out["monthly"][-1]
ok("duplicate-spelling month counts once, freshest computed_at wins",
   len(out["monthly"]) == 3 and aug["revenue"] == 120000.0, aug)
ok("trailing window respected", analysis.assemble(ROWS, months=2)["months"]
   == ["July 2026", "August 2026"])

print("A. one math path — payload verbatim")
jul = out["monthly"][1]
ok("July P&L headline from snapshot", (jul["revenue"], jul["gross_profit"], jul["net_income"])
   == (100000.0, 60000.0, 20000.0), jul)
ok("August NI includes 'other' section", aug["net_income"] == 22000.0, aug["net_income"])
ok("cash & equivalents = cash + store_cash_on_hand", (jul["cash"], aug["cash"])
   == (12000.0, 18000.0), (jul["cash"], aug["cash"]))
ok("BS totals verbatim", (aug["assets"], aug["liabilities"], aug["equity"], aug["inventory"])
   == (184000.0, 110000.0, 74000.0, 166000.0))

print("C. ratios")
jun = out["monthly"][0]
ok("zero-revenue month margins are None (chart gap), never fake 0",
   jun["gross_margin_pct"] is None and jun["net_margin_pct"] is None
   and jun["opex_ratio_pct"] is None, jun)
ok("July margins", (jul["gross_margin_pct"], jul["opex_ratio_pct"], jul["net_margin_pct"])
   == (60.0, 40.0, 20.0))

print("D. OPEX composition")
for m_row, pl_row in zip(out["expense_breakdown"], out["monthly"]):
    total = round(sum(v for k, v in m_row.items() if k != "period"), 2)
    ok(f"breakdown sums to opex subtotal ({m_row['period']})", total == pl_row["opex"],
       (total, pl_row["opex"]))
comp = out["expense_composition_latest"]
ok("latest composition sorted desc + pcts sum ~100",
   comp and comp[0]["key"] == "wages"
   and abs(sum(c["pct"] or 0 for c in comp) - 100.0) < 0.5, comp)
ok("expense line labels registered", out["expense_lines"].get("wages") == "Wages")

print("E. scope comparison series")
cos = {c["scope_key"]: c for c in out["companies"]}
ok("both companies present, labelled", set(cos) == {"company:a", "company:b"}
   and cos["company:a"]["label"] == "Luxlink Wireless")
ok("company series align on the shared axis with 0-filled gaps",
   [p["revenue"] for p in cos["company:b"]["series"]] == [0.0, 0.0, 50000.0],
   cos["company:b"]["series"])
ok("company:a August revenue", cos["company:a"]["series"][-1]["revenue"] == 70000.0)
sts = {s["scope_key"] for s in out["stores"]}
ok("stores series present; consolidated never leaks into scopes",
   sts == {"store:1 Main St", "store:2 Oak Ave"}
   and "consolidated" not in {c["scope_key"] for c in out["companies"]} | sts)

print("F. drift pin")
ok("analysis.CASH_KEYS == statement_engine.CF_CASH_KEYS",
   tuple(analysis.CASH_KEYS) == tuple(SE.CF_CASH_KEYS),
   (analysis.CASH_KEYS, SE.CF_CASH_KEYS))

print("G. empty input")
empty = analysis.assemble([], months=12)
ok("no rows -> computed:false + note", empty["computed"] is False and "note" in empty)
ok("garbage periods ignored", analysis.assemble(
    [row("not a period", "pl", "consolidated", pl_payload([("x", 1.0)], 0, []))])["computed"] is False)

print()
if FAIL:
    print(f"{FAIL} CHECK(S) FAILED")
    sys.exit(1)
print("harness_financial_analysis: ALL CHECKS PASSED")
