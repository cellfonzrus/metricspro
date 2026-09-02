"""Proof harness — the on-demand statement engine (owner directive 2026-09-02). Stdlib only.

Proves app/modules/account/statement_engine.py's pure parts, and the assembly-level truths by
running the REAL engine._assemble (app.core.config is satisfied with a local BaseSettings stub —
no network, no DB, no pydantic):

  A. spec extension — bs_spec() = coa.BS_SPEC + the handset_payable auto_opt line; coa's spec is
     never mutated; period_as_of month-end/cap semantics.
  B. DEFAULT BYTE-IDENTITY — with the empty default config the extended spec assembles a Balance
     Sheet byte-identical to the coa.BS_SPEC assembly (the auto_opt line does not even render);
     with a booked payable the liability line appears and the subtotal moves by exactly that much.
  C. THE OWNER'S ROWS, END TO END — his three live August-2026 journal entries assembled through
     the FIXED scoping: consolidated carries all three; the Luxlink company scope shows the $250k
     contribution + a $210,000 'Loan' liability line; Nova Wave shows the $100k contribution. The
     old engine._journal_for_scope is run alongside to show it DROPS them (the defect, pinned).
  D. cash flow — operating/investing/financing classification (a journal 'Loan' is financing, the
     spec payables are operating, fixtures are investing), the indirect-method identity
     (NI − Δoperating assets + Δoperating liabilities + investing + financing == implied change),
     the manual-cash tie-out, and the first-period (no prior BS) flag.

Run:  cd backend && python3 harness_statement_engine.py
"""
import sys
import types

sys.path.insert(0, "app")

# app.core.config needs pydantic_settings; a class-attribute stub is enough for import (the
# harness never reads a setting) — same offline-proof trick the workforce harness uses for its
# app seams.
if "pydantic_settings" not in sys.modules:
    stub = types.ModuleType("pydantic_settings")

    class _BaseSettings:                                   # noqa: D401 — minimal import stub
        def __init__(self, **kw):
            pass

    stub.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = stub

from app.modules.account import coa, engine, balance_sheet, statement_engine as SE  # noqa: E402

FAIL = 0


def ok(name, cond, detail=None):
    global FAIL
    mark = "PASS" if cond else "FAIL"
    if not cond:
        FAIL += 1
    print(f"  [{mark}] {name}" + ("" if cond else f"  << {detail}"))


def empty_inputs(spec_keys):
    return {k: {"by_store": {}, "company_wide": 0.0, "detail": {}} for k in spec_keys}


ALL_KEYS = [k for k, *_ in coa.PL_SPEC] + [k for k, *_ in SE.bs_spec()]

# ── A. spec + as-of ────────────────────────────────────────────────────────────────────────────
print("A. spec extension + period as-of")
spec = SE.bs_spec()
# mig 938 added store_cash_on_hand to EXTRA_BS_SPEC (verified store cash) — the invariant is
# "coa.BS_SPEC followed by EXACTLY balance_sheet.EXTRA_BS_SPEC", not a frozen one-line list.
ok("bs_spec = coa.BS_SPEC + EXTRA_BS_SPEC (handset_payable, store_cash_on_hand)",
   [k for k, *_ in spec]
   == [k for k, *_ in coa.BS_SPEC] + ["handset_payable", "store_cash_on_hand"])
hp = next(row for row in spec if row[0] == "handset_payable")
ok("handset_payable is a liability, auto_opt (renders only when it carries value)",
   hp[2] == "liability" and hp[3] == "auto_opt", hp)
ok("coa.BS_SPEC itself is never mutated", not any(k == "handset_payable" for k, *_ in coa.BS_SPEC))
ok("closed month → its last day", SE.period_as_of("July 2026", today="2026-09-02") == "2026-07-31")
ok("open month capped at today", SE.period_as_of("September 2026", today="2026-09-02") == "2026-09-02")
ok("numeric spelling accepted", SE.period_as_of("2026-08", today="2026-09-02") == "2026-08-31")
ok("garbage period → None", SE.period_as_of("not-a-period") is None)

# ── B. default byte-identity / booked payable ──────────────────────────────────────────────────
print("B. default byte-identity + the booked handset payable")
inputs = empty_inputs(ALL_KEYS)
inputs["owed_vip"]["by_store"]["Store A"] = 500.0
base = engine._assemble(inputs, [], coa.BS_SPEC, coa.BS_LABEL, SE.BS_SECTIONS,
                        "consolidated", None, True)
ext = engine._assemble(inputs, [], SE.bs_spec(), SE.bs_label(), SE.BS_SECTIONS,
                       "consolidated", None, True)
ok("EMPTY handset_payable ⇒ extended assembly is byte-identical to the base spec", ext == base)

booked = empty_inputs(ALL_KEYS)
booked["owed_vip"]["by_store"]["Store A"] = 500.0
booked["handset_payable"]["by_store"]["Store A"] = 169013.57       # the live LuxeLink figure
booked["handset_payable"]["detail"]["Postpaid Branded MarketPlace"] = 169013.57
ext2 = engine._assemble(booked, [], SE.bs_spec(), SE.bs_label(), SE.BS_SECTIONS,
                        "consolidated", None, True)
liab = next(s for s in ext2["sections"] if s["type"] == "liability")
line = next((l for l in liab["lines"] if l["key"] == "handset_payable"), None)
ok("booked payable renders as its own liability line",
   line is not None and line["amount"] == 169013.57, line)
ok("liability subtotal moves by exactly the booked amount",
   liab["subtotal"] == 169513.57, liab["subtotal"])

# ── C. the owner's rows end to end ─────────────────────────────────────────────────────────────
print("C. owner's three live journal rows through the FIXED scoping")
COMPANIES = [{"id": "e0e28bd6", "name": "Luxlink Wireless"},
             {"id": "bccc049e", "name": "Nova Wave Communications"},
             {"id": "1e7ff323", "name": "Default Company"}]
matcher = balance_sheet.journal_company_matcher(COMPANIES)
J = [
    {"statement": "balance_sheet", "account_type": "equity", "account_line": "Owner capital / contributions",
     "amount": 250000.0, "company_id": None, "store_address": "Luxelink", "memo": None},
    {"statement": "balance_sheet", "account_type": "equity", "account_line": "Owner capital / contributions",
     "amount": 100000.0, "company_id": None, "store_address": "Novawave", "memo": None},
    {"statement": "balance_sheet", "account_type": "liability", "account_line": "Loan",
     "amount": 210000.0, "company_id": None, "store_address": "Luxelink", "memo": None},
]
base_inputs = empty_inputs(ALL_KEYS)


def bs_for(scope_key, stores):
    jscope = balance_sheet.journal_scope_entries(J, scope_key, stores, matcher)
    return engine._assemble(base_inputs, jscope, SE.bs_spec(), SE.bs_label(), SE.BS_SECTIONS,
                            scope_key, (None if scope_key == "consolidated" else stores),
                            scope_key == "consolidated")


def line_amt(bs, sec_type, label):
    sec = next(s for s in bs["sections"] if s["type"] == sec_type)
    ln = next((l for l in sec["lines"] if l["label"] == label), None)
    return ln["amount"] if ln else None


cons = bs_for("consolidated", None)
ok("consolidated Owner capital = $350,000 (both contributions)",
   line_amt(cons, "equity", "Owner capital / contributions") == 350000.0,
   line_amt(cons, "equity", "Owner capital / contributions"))
ok("consolidated shows the $210,000 Loan liability line",
   line_amt(cons, "liability", "Loan") == 210000.0)

lux = bs_for("company:e0e28bd6", set())
ok("Luxlink company scope: Owner capital $250,000",
   line_amt(lux, "equity", "Owner capital / contributions") == 250000.0,
   line_amt(lux, "equity", "Owner capital / contributions"))
ok("Luxlink company scope: Loan $210,000", line_amt(lux, "liability", "Loan") == 210000.0)

nova = bs_for("company:bccc049e", set())
ok("Nova Wave company scope: Owner capital $100,000",
   line_amt(nova, "equity", "Owner capital / contributions") == 100000.0)
ok("Nova Wave company scope: no Loan line", line_amt(nova, "liability", "Loan") is None)

# The DEFECT, pinned: the old scoping drops all three rows from both company scopes.
old_lux = engine._journal_for_scope(J, "company:e0e28bd6", set())
old_nova = engine._journal_for_scope(J, "company:bccc049e", set())
ok("OLD engine scoping loses every entry on both company scopes (the reported defect)",
   old_lux == [] and old_nova == [], (old_lux, old_nova))

# ── D. cash flow ───────────────────────────────────────────────────────────────────────────────
print("D. derived cash flow (indirect method)")
prior_inputs = empty_inputs(ALL_KEYS)
prior_inputs["inventory"]["by_store"]["Store A"] = 150000.0
prior_inputs["owed_vip"]["by_store"]["Store A"] = 20000.0
prior_j = [{"statement": "balance_sheet", "account_type": "asset", "account_line": "Cash / bank",
            "amount": 50000.0}]
prior_bs = engine._assemble(prior_inputs, prior_j, SE.bs_spec(), SE.bs_label(), SE.BS_SECTIONS,
                            "consolidated", None, True)

cur_inputs = empty_inputs(ALL_KEYS)
cur_inputs["inventory"]["by_store"]["Store A"] = 166020.16     # inventory grew (uses cash)
cur_inputs["owed_vip"]["by_store"]["Store A"] = 25000.0        # payable grew (frees cash)
cur_inputs["handset_payable"]["by_store"]["Store A"] = 10000.0  # new payable (frees cash)
cur_j = [
    {"statement": "balance_sheet", "account_type": "asset", "account_line": "Cash / bank", "amount": 55000.0},
    {"statement": "balance_sheet", "account_type": "liability", "account_line": "Loan", "amount": 210000.0},
    {"statement": "balance_sheet", "account_type": "equity", "account_line": "Owner capital / contributions",
     "amount": 350000.0},
    {"statement": "balance_sheet", "account_type": "asset", "account_line": "Fixtures / equipment",
     "amount": 30000.0},
]
cur_bs = engine._assemble(cur_inputs, cur_j, SE.bs_spec(), SE.bs_label(), SE.BS_SECTIONS,
                          "consolidated", None, True)
pl = {"net_income": -12000.0}
cf = SE.cash_flow(pl, cur_bs, prior_bs, "August 2026", "consolidated", "Consolidated")

sec = {s["type"]: s for s in cf["sections"]}
ok("comparative (prior BS present)", cf["comparative"] is True)
op = sec["operating"]
ok("operating opens with net income", op["lines"][0]["key"] == "net_income"
   and op["lines"][0]["amount"] == -12000.0)
inv_delta = round(166020.16 - 150000.0, 2)
ok("inventory growth is an operating USE of cash (−Δ)",
   any(l["key"] == "inventory" and l["amount"] == -inv_delta for l in op["lines"]),
   op["lines"])
ok("spec payables (owed_vip, handset_payable) are operating sources (+Δ)",
   any(l["key"] == "owed_vip" and l["amount"] == 5000.0 for l in op["lines"])
   and any(l["key"] == "handset_payable" and l["amount"] == 10000.0 for l in op["lines"]))
ok("the journal Loan is FINANCING, not working capital",
   any("Loan" in l["label"] and l["amount"] == 210000.0 for l in sec["financing"]["lines"])
   and not any(l["key"].startswith("je_loan") for l in op["lines"]))
ok("owner capital change is financing (+$350k)",
   any(l["key"] == "owner_capital" and l["amount"] == 350000.0 for l in sec["financing"]["lines"]))
ok("fixtures purchase is investing (−$30k)",
   sec["investing"]["subtotal"] == -30000.0, sec["investing"])

expected_implied = round((-12000.0 - inv_delta + 5000.0 + 10000.0)      # operating
                         + (-30000.0)                                    # investing
                         + (210000.0 + 350000.0), 2)                     # financing
ok("indirect-method identity: subtotals sum to implied_cash_change",
   cf["implied_cash_change"] == expected_implied
   and cf["implied_cash_change"] == round(sum(s["subtotal"] for s in cf["sections"]), 2),
   (cf["implied_cash_change"], expected_implied))
ok("manual-cash tie-out reported, not pretended",
   cf["cash_begin"] == 50000.0 and cf["cash_end"] == 55000.0
   and cf["cash_delta_reported"] == 5000.0
   and cf["tie_delta"] == round(expected_implied - 5000.0, 2) and cf["tied"] is False)

cf0 = SE.cash_flow(pl, cur_bs, None, "August 2026", "consolidated", "Consolidated")
ok("first computed period: comparative=false + the honest note",
   cf0["comparative"] is False and any("no prior balance sheet" in n for n in cf0["notes"]))
ok("retained earnings and cash are never adjustment lines",
   not any(l["key"] in ("retained", "cash") for s in cf["sections"] for l in s["lines"]))

print()
if FAIL:
    print(f"{FAIL} CHECK(S) FAILED")
    sys.exit(1)
print("harness_statement_engine: ALL CHECKS PASSED")
