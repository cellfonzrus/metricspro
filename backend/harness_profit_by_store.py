"""Proof harness — THE finance profit producer (`statement_engine.profit_by_store`). Stdlib only.

WHY IT EXISTS. Owner report 2026-11-17, verbatim: *"The profit is not correct it should be same as
in the finance dashboard"*. The Weekly Performance Review quoted the Gross Profit report's
`net_profit` (`total_rev − rep_pay − exp_total − net_phone_cost`, over store-attributed lines only)
as "profit". Measured live for October 2026:

    house org 00000000-0000-0000-0000-000000000001   GP Σ net_profit  +340,102.98
                                                     P&L net income    −26,543.19   (Δ 366,646.17)
    LuxeLink  854f6d7b-6590-4e4d-88ab-646f560d4f4c   GP Σ net_profit   +31,122.43
                                                     P&L net income       +576.53   (Δ  30,545.90)

Two formulas answering "what did we earn" is the duplicate-derivation defect; the fix is ONE
producer in the finance module that every other report QUOTES.

WHAT IS PROVEN HERE (pure folds + the REAL `engine._assemble`, no DB, no network):
  A. `pl_figures` READS an assembled payload and never re-derives it — an absent figure is None,
     never 0.
  B. `company_wide_pl_cells` — the dollars no store scope can carry, signed into net income by
     SECTION; `manual`/`computed` lines (zeroed by `_assemble`) contribute nothing.
  C. `unstored_journal_pl_cells` — store-grain entries excluded, company/tenant grain reported,
     a non-P&L section's effect is None WITH A REASON.
  D. END TO END over the REAL `engine._assemble`: the producer's per-store net income is the P&L's
     own net income for that scope, and Σ store rows + the company-wide effect == consolidated.
  E. An UNEXPLAINED residual is reported as unexplained (`reconciles: false`) — never absorbed.
  F. Store CODE: unread identity ⇒ None with a reason; unclaimed store key ⇒ None with a reason;
     an ambiguous identity is flagged on the row AND listed in coverage.
  G. A store with no money has NO ROW (it is not a zero) — `_scopes` gives it no scope.
  H. THE REGRESSION: the GP-shaped formula and net income differ on the same inputs, so a report
     that derives its own profit cannot agree; quoting the producer is the only way it can.

Run:  cd backend && python3 harness_profit_by_store.py
"""
import sys
import types

sys.path.insert(0, "app")

# app.core.config needs pydantic_settings; a class-attribute stub is enough for import (this
# harness never reads a setting) — the same offline-proof trick harness_statement_engine.py uses.
if "pydantic_settings" not in sys.modules:
    stub = types.ModuleType("pydantic_settings")

    class _BaseSettings:                                   # noqa: D401 — minimal import stub
        def __init__(self, **kw):
            pass

    stub.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = stub

from app.modules.account import coa, engine, balance_sheet, statement_engine as SE  # noqa: E402

FAIL = 0


def check(name, cond, detail=None):
    global FAIL
    if not cond:
        FAIL += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  << {detail}"))


def cell(by_store=None, company_wide=0.0, detail=None, detail_by_store=None, label=None):
    c = {"by_store": dict(by_store or {}), "company_wide": company_wide,
         "detail": dict(detail or {}), "detail_by_store": dict(detail_by_store or {})}
    if label:
        c["label"] = label
    return c


def empty_inputs(spec):
    return {k: cell() for k, *_ in spec}


SPEC = coa.pl_spec_for(None)
LABELS = coa.pl_label_for(None)
SECTION_OF = {k: sec for k, _l, sec, *_ in SPEC}


def first_key(section, kind=None):
    for k, _l, sec, knd, _g in SPEC:
        if sec == section and (kind is None or knd == kind) and knd not in ("manual", "computed"):
            return k
    raise AssertionError(f"no spec line for section {section}")


REV = first_key("revenue")
COGS = first_key("cogs")
OPEX = first_key("opex")

# ── A. pl_figures reads, never derives ─────────────────────────────────────────────────────────
print("A. pl_figures — reads the payload, an unmeasured figure is None")
payload = {"sections": [{"type": "revenue", "subtotal": 1000.0},
                        {"type": "cogs", "subtotal": 400.0},
                        {"type": "opex", "subtotal": 250.0},
                        {"type": "other", "subtotal": 0.0}],
           "gross_profit": 600.0, "net_operating_income": 350.0, "net_income": 350.0}
f = SE.pl_figures(payload)
check("section subtotals + totals read verbatim",
      (f["revenue"], f["cogs"], f["opex"], f["other"]) == (1000.0, 400.0, 250.0, 0.0)
      and (f["gross_profit"], f["net_income"]) == (600.0, 350.0), f)
# A payload that CONTRADICTS the identity is still reported as the payload states it — this fold
# states no accounting rule of its own.
odd = dict(payload, net_income=-99.0)
check("no figure is recomputed (a payload's own net_income is returned)",
      SE.pl_figures(odd)["net_income"] == -99.0)
bare = SE.pl_figures({"sections": [{"type": "revenue", "subtotal": 5.0}]})
check("unmeasured figures are None, never 0",
      bare["net_income"] is None and bare["gross_profit"] is None and bare["cogs"] is None, bare)
check("empty payload measures nothing", all(v is None for v in SE.pl_figures({}).values()))

# ── B. company-wide cells ──────────────────────────────────────────────────────────────────────
print("\nB. company_wide_pl_cells — the dollars no store scope can carry, signed by section")
inp = empty_inputs(SPEC)
inp[REV] = cell(company_wide=1200.0)
inp[COGS] = cell(company_wide=500.0)
inp[OPEX] = cell(company_wide=0.54, label="Gross Payroll")
cw = SE.company_wide_pl_cells(inp, SPEC)
by_key = {c["key"]: c for c in cw}
check("every non-zero company-wide line is reported", set(by_key) == {REV, COGS, OPEX}, by_key)
check("revenue adds to net income, COGS and opex subtract",
      by_key[REV]["net_income_effect"] == 1200.0
      and by_key[COGS]["net_income_effect"] == -500.0
      and by_key[OPEX]["net_income_effect"] == -0.54, by_key)
check("the data's own label override wins over the spec label",
      by_key[OPEX]["label"] == "Gross Payroll")
inp2 = empty_inputs(SPEC)
inp2[REV] = cell(company_wide=0.0)
check("a zero company-wide line is not a row", SE.company_wide_pl_cells(inp2, SPEC) == [])
man = [k for k, _l, sec, knd, _g in SPEC if knd in ("manual", "computed") and sec in SE.PL_SECTION_SIGN]
if man:
    inp3 = empty_inputs(SPEC)
    inp3[man[0]] = cell(company_wide=9999.0)
    check("a manual/computed line contributes nothing (as _assemble zeroes it)",
          SE.company_wide_pl_cells(inp3, SPEC) == [], man[0])
else:
    print("  [SKIP] no manual/computed P&L spec line in this chart")

# ── C. unstored journal cells ──────────────────────────────────────────────────────────────────
print("\nC. unstored_journal_pl_cells — what consolidated counts and no store scope does")
journal = [
    {"statement": "pl", "account_type": "opex", "account_line": "Legal fees",
     "amount": 2500.0, "store_address": ""},                                  # tenant grain
    {"statement": "pl", "account_type": "revenue", "account_line": "Settlement",
     "amount": 1000.0, "company_id": "co-1"},                                 # company grain
    {"statement": "pl", "account_type": "opex", "account_line": "Rent",
     "amount": 800.0, "store_address": "1 Main St"},                          # STORE grain
    {"statement": "balance_sheet", "account_type": "asset", "account_line": "Cash",
     "amount": 50.0},                                                         # not a P&L entry
    {"statement": "pl", "account_type": "mystery", "account_line": "Unknown bucket",
     "amount": 42.0},                                                         # unknown section
]
je = SE.unstored_journal_pl_cells(journal, None, None)
lines = {c["line"]: c for c in je}
check("store-grain and non-P&L entries are not in this list",
      "Rent" not in lines and "Cash" not in lines, sorted(lines))
check("tenant + company grain reported with their grain",
      lines["Legal fees"]["grain"] == "tenant" and lines["Settlement"]["grain"] == "company"
      and lines["Settlement"]["company_id"] == "co-1", lines)
check("signs follow the section (opex −, revenue +)",
      lines["Legal fees"]["net_income_effect"] == -2500.0
      and lines["Settlement"]["net_income_effect"] == 1000.0)
check("an entry whose section is not a P&L section is None WITH a reason",
      lines["Unknown bucket"]["net_income_effect"] is None
      and "not a P&L section" in (lines["Unknown bucket"]["reason"] or ""), lines["Unknown bucket"])

# ── D. end to end over the REAL engine._assemble ───────────────────────────────────────────────
print("\nD. END TO END — the producer's figures ARE the P&L's, and the rows explain consolidated")
S1, S2 = "100 Main St", "200 Oak Ave"
inputs = empty_inputs(SPEC)
inputs[REV] = cell({S1: 70000.0, S2: 30000.0}, company_wide=1200.0)
inputs[COGS] = cell({S1: 40000.0, S2: 20000.0})
inputs[OPEX] = cell({S1: 25000.0, S2: 15000.0}, company_wide=0.54)
journal_d = [{"statement": "pl", "account_type": "opex", "account_line": "Legal fees",
              "amount": 2500.0, "store_address": ""}]


def assemble(scope_key, stores, include_cw):
    jscope, _m = balance_sheet.journal_grain_entries(journal_d, scope_key, stores, None, None)
    return engine._assemble(inputs, jscope, SPEC, LABELS, SE.PL_SECTIONS,
                            scope_key, stores, include_cw)


cons_pl = assemble("consolidated", None, True)
store_pls = [(f"store:{s}", s, assemble(f"store:{s}", {s}, False)) for s in (S1, S2)]
identity = {S1: {"store": S1, "store_code": "B-100", "market": "North", "salesforce_id": "sf1",
                 "codes": ["B-100"], "ambiguous": False, "is_active": True},
            S2: {"store": S2, "store_code": "B-200", "market": "South", "salesforce_id": "",
                 "codes": ["B-200", "B-201"], "ambiguous": True, "is_active": True}}
out = SE.fold_profit_by_store("October 2026", "org-1", cons_pl, store_pls, [],
                              SE.company_wide_pl_cells(inputs, SPEC),
                              SE.unstored_journal_pl_cells(journal_d, None, None),
                              identity=identity, scope_counts={"scopes_total": 3})
rows = {r["store"]: r for r in out["stores"]}
check("one row per store scope, keyed as the BOOKS key them", set(rows) == {S1, S2}, sorted(rows))
check("each row's net income IS that scope's assembled P&L net income",
      all(rows[label]["net_income"] == pl["net_income"] for _k, label, pl in store_pls),
      {s: rows[s]["net_income"] for s in rows})
check("revenue and gross profit come off the same payload",
      rows[S1]["revenue"] == 70000.0 and rows[S1]["gross_profit"] == 30000.0
      and rows[S1]["net_income"] == 5000.0, rows[S1])
check("consolidated figures are the consolidated payload's",
      out["consolidated"]["net_income"] == cons_pl["net_income"]
      and out["consolidated"]["revenue"] == cons_pl["sections"][0]["subtotal"], out["consolidated"])
rec = out["reconciliation"]
check("Σ store rows + company-wide effect + unstored journal effect == consolidated",
      rec["reconciles"] is True and rec["unexplained"] == 0.0
      and rec["residual"] == rec["explained"], rec)
check("the residual is EXPLAINED line by line, not asserted",
      rec["company_wide_net_income_effect"] == round(1200.0 - 0.54, 2)
      and rec["unstored_journal_net_income_effect"] == -2500.0
      and len(rec["company_wide_lines"]) == 2 and len(rec["unstored_journal_entries"]) == 1, rec)
check("`why` names both reasons in words",
      any("NO store" in w for w in rec["why"]) and any("above store grain" in w for w in rec["why"]),
      rec["why"])
check("the basis sentence says what the number is AND what it is not",
      "net income" in out["basis"].lower() and "net_profit" in out["basis"]
      and "statement(" in out["basis"])

# ── E. an unexplained residual is reported, never absorbed ─────────────────────────────────────
print("\nE. an unexplained residual is REPORTED")
bad = SE.fold_profit_by_store("October 2026", "org-1", cons_pl, store_pls, [],
                              [],   # the company-wide dollars withheld from the explanation
                              SE.unstored_journal_pl_cells(journal_d, None, None),
                              identity=identity)
check("reconciles false and the gap is named",
      bad["reconciliation"]["reconciles"] is False
      and bad["reconciliation"]["unexplained"] == round(1200.0 - 0.54, 2)
      and any("NOT explained" in w for w in bad["reconciliation"]["why"]), bad["reconciliation"])

# ── F. the store CODE a caller joins on ────────────────────────────────────────────────────────
print("\nF. store identity — a code nobody measured is None WITH a reason")
check("a resolved row carries code + market + the declared codes",
      rows[S1]["store_code"] == "B-100" and rows[S1]["market"] == "North"
      and rows[S1]["store_code_reason"] is None, rows[S1])
check("an ambiguous identity is flagged on the row and listed in coverage",
      rows[S2]["identity_ambiguous"] is True and rows[S2]["store_codes"] == ["B-200", "B-201"]
      and out["coverage"]["stores_with_ambiguous_identity"] == [S2], rows[S2])
unread = SE.fold_profit_by_store("October 2026", "org-1", cons_pl, store_pls, [], [], [],
                                 identity=None, identity_reason="store identity unavailable: boom")
check("identity not read ⇒ every code None with the read's own reason",
      all(r["store_code"] is None and "boom" in r["store_code_reason"] for r in unread["stores"])
      and unread["coverage"]["store_identity_read"] is False, unread["coverage"])
partial = SE.fold_profit_by_store("October 2026", "org-1", cons_pl, store_pls, [], [], [],
                                  identity={S1: identity[S1]})
p2 = {r["store"]: r for r in partial["stores"]}
check("a store key no store_mapping row claims ⇒ None with that reason, and coverage names it",
      p2[S2]["store_code"] is None and "no store_mapping row" in p2[S2]["store_code_reason"]
      and partial["coverage"]["stores_with_no_store_code"] == [S2], p2[S2])

# ── G. a store with no money has no row — it is not a zero ─────────────────────────────────────
print("\nG. coverage states itself — a store with no money has NO row")
scopes, all_stores = SE._scopes(inputs, [], lambda s: None, journal_d, None, None)
check("_scopes names only the stores the lines place money at",
      all_stores == {S1, S2}
      and {k for k, *_ in scopes} == {"consolidated", f"store:{S1}", f"store:{S2}"}, scopes)
check("coverage counts the scopes and says a missing store is not a zero",
      out["coverage"]["store_scopes"] == 2 and out["coverage"]["scopes_total"] == 3
      and "not a zero" in out["coverage"]["note"], out["coverage"])

# ── H. THE REGRESSION: a second derivation cannot agree ────────────────────────────────────────
print("\nH. REGRESSION — the GP-shaped formula is a DIFFERENT question (the reported defect)")
# The Gross Profit report's shape over the SAME store-attributed dollars: revenue − rep pay −
# expenses − phone cost, with NO company-wide dollars and NO journal entries. It omits whole
# sections of the chart, so it cannot be net income — pinned here so nobody "fixes" the gap by
# tuning a second formula again.
gp_shaped = round(sum(inputs[REV]["by_store"].values())
                  - sum(inputs[COGS]["by_store"].values()), 2)          # no opex, no company-wide
check("the GP-shaped number and net income differ on the SAME inputs",
      gp_shaped != out["consolidated"]["net_income"],
      (gp_shaped, out["consolidated"]["net_income"]))
check("Σ store rows alone is NOT the company's profit either (company-wide dollars exist)",
      rec["stores_net_income_total"] != out["consolidated"]["net_income"]
      and rec["residual"] != 0.0, rec)
check("and the payload lets the caller SEE why those two differ",
      abs(rec["residual"] - rec["explained"]) < 0.01)

print()
if FAIL:
    print(f"{FAIL} CHECK(S) FAILED")
    sys.exit(1)
print("harness_profit_by_store: ALL CHECKS PASSED")
