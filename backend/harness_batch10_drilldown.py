"""PROOF — THE THREE FINANCE REPORTS NAME THE ROWS BEHIND THEIR FIGURES, AND WHERE THEY STOP.

DB-free, stdlib only, driving the REAL producers through the shared fold
(`core/report_drilldown.tiles` / `tile_panel` / `panel`): `statement_filter.aggregate` (the
balance-sheet assembler), `statement_engine.cash_flow` (the cash-flow producer),
`liabilities_due.payables_due_in_window` / `attribute_stores` / `aggregate_payroll` /
`rent_due_rows` / `insurance_due_rows` / `sum_known` and `balance_sheet.handset_payable_bookings`,
with the REAL `payroll_tax_estimate.compute_pay`. Batch 10 of the §CORE-17 audit:
/accounts/balance-sheet, /accounts/cash-flow and /accounts/liabilities-due.

WHAT THIS BATCH IS ABOUT.

1. A FIGURE READ OUT OF A SNAPSHOT NAMES THE SNAPSHOT, AND THE TRAIL STOPS THERE. Both statement
   screens read one `account_statements` row. The compute knew which feed each line came from and
   kept only the dollars, so a feed is named ONLY where the booking code reads that line from it
   unconditionally, and a line whose feed is chosen from per-org config at compute time names NO
   feed and says which basis decided it. §B.

2. NO BALANCE-SHEET LINE RECORDS A BREAKDOWN, SO THE SHEET'S OWN EXPANDER NEVER OPENS. Every
   balance-sheet booking in `coa` calls `add()` without a drill label. Such a line's panel says so,
   in the shared vocabulary of reasons, and REFUSES to claim a tie — it never serves the missing
   breakdown as $0.00. §C.

3. A DIFFERENCE AND A LEAF GET NO PANEL. The cash-flow tie-out delta is implied minus reported, and
   the reported half is the balance sheet's own cash lines, which this statement does not carry; a
   cash-flow LINE is already the leaf. All four are absent from the declaration rather than served
   as a tie-out that cannot fail (§CORE-17.3h, extended from a quotient to a difference). §D3.

4. SIX TILES COMPOSED FROM FOUR DERIVATIONS, OPENED. The distributor tiles name the feed the
   RESOLVED basis read; the payroll tiles open to one row per person with the hours table that
   actually answered for them; a store whose rent is not set is shown WITHOUT a figure and not
   counted, because the report counts those separately. §E.

  python3 backend/harness_batch10_drilldown.py
"""
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import balance_sheet as bsx                            # noqa: E402
from app.modules.account import coa                                             # noqa: E402
from app.modules.account import liabilities_drilldown as ldd                     # noqa: E402
from app.modules.account import liabilities_due as ld                            # noqa: E402
from app.modules.account import statement_drilldown as sd                        # noqa: E402
from app.modules.account import statement_engine as se                           # noqa: E402
from app.modules.account import statement_filter as sf                           # noqa: E402
from app.modules.core import report_drilldown as rd                              # noqa: E402
from app.modules.storeops import payroll_tax_estimate as pte                     # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label
          + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _code_only(path):
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                out.append(tok.string)
    return " ".join(out)


def _body(fn_name, src):
    i = src.index(fn_name)
    ends = [x for x in (src.find("\ndef ", i + len(fn_name)),
                        src.find("\n@router.", i + len(fn_name))) if x != -1]
    return src[i:(min(ends) if ends else len(src))]


def _row(panel, label):
    return next((r for r in (panel or {}).get("rows") or [] if r.get("label") == label), None)


def _line(panel, key):
    return next((r for r in (panel or {}).get("rows") or []
                 if (r.get("row_key") or {}).get("line") == key), None)


def _tables(row):
    return sorted((row or {}).get("sources") or [])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE BALANCE-SHEET FIXTURE, assembled by the REAL `statement_filter.aggregate` out of two per-store
# snapshots — the same function the filtered read uses, so the payload under test is one the platform
# really produces (subtotals, totals and the imbalance included).
#   · cash             manual, dollars           → the journal
#   · inventory        config-resolved basis     → NO feed, and it says which basis chose it
#   · chargeback_res   unconditional feed, $0.00 → the feed produced nothing, so it is not claimed
#   · owed_vip         unconditional feed        → the ledger AND the PayGo batches
#   · handset_payable  config-resolved, WITH a breakdown the compute recorded
#   · owner_capital    manual
PERIOD, SCOPE = "September 2026", "consolidated"


def _ln(k, label, amount, kind="auto", detail=None):
    return {"key": k, "label": label, "amount": amount, "kind": kind, "detail": dict(detail or {})}


STORE_PAYLOAD = {
    "statement_type": "balance_sheet",
    "sections": [
        {"name": "Assets", "type": "asset", "lines": [
            _ln("cash", "Cash / bank", 100.0, "manual"),
            _ln("inventory", "Inventory — on-hand device value", 400.0),
        ], "subtotal": 500.0},
        {"name": "Liabilities", "type": "liability", "lines": [
            _ln("owed_vip", "Owed to Distributor (device financing)", 200.0),
            _ln("chargeback_res", "Chargeback reserve", 0.0),
            _ln("handset_payable", "Handset payables (devices due to distributor)", 50.0,
                "auto_opt", {"Activation": 30.0, "Upgrade": 20.0}),
        ], "subtotal": 250.0},
        {"name": "Equity", "type": "equity", "lines": [
            _ln("owner_capital", "Owner capital / contributions", 150.0, "manual"),
        ], "subtotal": 150.0},
    ],
}


def bs_statement(payloads=None):
    st = sf.aggregate(list(payloads or [STORE_PAYLOAD, STORE_PAYLOAD]), "balance_sheet",
                      structure=STORE_PAYLOAD)
    st["period"], st["scope_key"] = PERIOD, SCOPE
    return st


BS = bs_statement()
BSD = sd.balance_sheet_drilldowns(BS)


def _sec(st, stype):
    return next(s for s in st["sections"] if s["type"] == stype)


def _amt(st, stype, key):
    return next(l["amount"] for l in _sec(st, stype)["lines"] if l["key"] == key)


print("== A. every summarised figure on the Balance Sheet opens, and ties ==")
check("A1 the figures are the STATEMENT'S OWN, dereferenced — a section panel's figure is the stored "
      "subtotal and a line panel's is the stored amount, never a second fold of the same rows",
      all(BSD["section_%s" % s["type"]]["figure"]["amount"] == s["subtotal"]
          for s in BS["sections"])
      and BSD["line_asset_cash"]["figure"]["amount"] == _amt(BS, "asset", "cash"),
      [(s["type"], s["subtotal"], BSD["section_%s" % s["type"]]["figure"]["amount"])
       for s in BS["sections"]])
_missing = [(s["type"], l["key"]) for s in BS["sections"] for l in s["lines"]
            if "line_%s_%s" % (s["type"], l["key"]) not in BSD]
check("A2 NOTHING SUMMARISED IS LEFT UNDRILLABLE — every section subtotal and every line on the "
      "sheet has a panel, which is what the audit found this report had none of",
      _missing == [] and all("section_%s" % s["type"] in BSD for s in BS["sections"]), _missing)
check("A3 every section panel's rows are its own lines and add up to the subtotal exactly",
      all(BSD["section_%s" % s["type"]]["tie_out"]["ties"] is True for s in BS["sections"]),
      [(s["type"], BSD["section_%s" % s["type"]]["tie_out"]["words"]) for s in BS["sections"]])
check("A4 Liabilities + Equity is the two stored subtotals and nothing else",
      BSD["liabilities_plus_equity"]["tie_out"]["ties"] is True
      and sorted(_tables(r) and r["label"] for r in BSD["liabilities_plus_equity"]["rows"])
      == ["Total Equity", "Total Liabilities"],
      BSD["liabilities_plus_equity"]["tie_out"]["words"])
check("A5 the imbalance opens to the two sides it is the difference of, the second counted NEGATIVE "
      "— so the two rows really do add up to the gap the screen shows",
      BSD["imbalance"]["figure"]["amount"] == BS["imbalance"]
      and BSD["imbalance"]["tie_out"]["ties"] is True
      and _row(BSD["imbalance"], "Liabilities + Equity (counted negative)")["amount"] < 0,
      BSD["imbalance"]["tie_out"]["words"])
check("A6 every row carries the row key that addresses the line it explains, so a screen could "
      "narrow a panel to its own selection (§CORE-17.3d)",
      all(r.get("row_key", {}).get("line")
          for k, p in BSD.items() for r in p["rows"]), )
check("A7 a period that was never computed drills to NOTHING rather than a panel asserting a tie "
      "over a statement nobody produced",
      sd.balance_sheet_drilldowns(None) == {} and sd.balance_sheet_drilldowns({}) == {})

print("\n== B. provenance, per row, never a constant ==")
check("B1 EVERY row names the snapshot, because that is where the endpoint read the figure — both "
      "these screens read one stored statement row and nothing else",
      all(sd.SNAPSHOT in _tables(r) for p in BSD.values() for r in p["rows"]))
check("B2 a line the booking code reads from a feed UNCONDITIONALLY names that feed — the asset "
      "ledger and the PayGo batches both book what is owed to the distributor",
      _tables(_line(BSD["section_liability"], "owed_vip"))
      == ["account_statements", "asset_ledger", "vip_paygo_payments"])
_zero = bs_statement()
for _l in _sec(_zero, "liability")["lines"]:
    if _l["key"] == "owed_vip":
        _l["amount"] = 0.0
check("B3 the SAME line at $0.00 names only the snapshot — the feed was read and produced nothing, "
      "and claiming it answered would be a provenance lie that reads like rigour",
      _tables(_line(sd.balance_sheet_drilldowns(_zero)["section_liability"], "owed_vip"))
      == ["account_statements"])
check("B4 a MANUAL line names the journal it was entered in, not a feed",
      _tables(_line(BSD["section_asset"], "cash")) == ["account_statements", "journal_entries"])
check("B5 a line whose feed is chosen from per-org CONFIG at compute time names NO feed, and says "
      "which basis chose it — the snapshot does not record which one answered",
      _tables(_line(BSD["section_asset"], "inventory")) == ["account_statements"]
      and "inventory basis" in str(_line(BSD["section_asset"], "inventory")["fields"]
                                   ["why_no_feed"]))
check("B5b and a line whose feed is a table NO MIGRATION IN THIS REPOSITORY CREATES names no feed "
      "either, with that stated as the reason — the chargeback reserve really is read from one, and "
      "a `sourceLabels` entry for an unregistered table fails the infra lock, so the alternative to "
      "saying so is a raw table name on a manager's screen",
      set(sd.UNREGISTERED_FEED_LINES) == {"chargeback_res"}
      and "has no plain name in the platform yet"
      in str(_line(BSD["section_liability"], "chargeback_res")["fields"]["why_no_feed"])
      and "migration" not in str(_line(BSD["section_liability"], "chargeback_res")["fields"])
      and _tables(_line(BSD["section_liability"], "chargeback_res")) == ["account_statements"])
check("B5c ONE question, ONE answer: why a line names no feed is read from one function over both "
      "registries, so a reader never has to tell an absence from a gap",
      sd.why_no_feed("inventory") and sd.why_no_feed("chargeback_res")
      and sd.why_no_feed("owed_vip") is None)
_spec_keys = {k for k, *_ in se.bs_spec()}
_claimed = set(sd.LINE_FEEDS) | set(sd.CONFIG_RESOLVED_LINES) | set(sd.UNREGISTERED_FEED_LINES)
check("B6 every line key the three registries claim is a REAL spec line — they dereference the spec "
      "rather than carrying a stale copy of it",
      _claimed <= _spec_keys, sorted(_claimed - _spec_keys))
check("B7 and the three are disjoint: a line is read from a known feed, or chosen by a basis, or "
      "read from a table nobody can name — never two of those at once",
      len(set(sd.LINE_FEEDS)) + len(set(sd.CONFIG_RESOLVED_LINES))
      + len(set(sd.UNREGISTERED_FEED_LINES)) == len(_claimed))

print("\n== C. the three live findings, reproduced and reported rather than papered over ==")
_coa_src = _read("backend", "app", "modules", "account", "coa.py")
_bs_adds = {}
for _k, *_rest in coa.BS_SPEC:
    _bs_adds[_k] = re.findall(r"add\(\"%s\",[^\n]*" % re.escape(_k), _coa_src)
_with_label = {k: v for k, v in _bs_adds.items() if any("detail_label" in c for c in v)}
check("C1 FINDING 1, at the source: not ONE balance-sheet booking in `coa` passes a drill label, so "
      "no `BS_SPEC` line carries a breakdown and the sheet's own expander can never open",
      any(_bs_adds.values()) and _with_label == {}, _with_label)
_cash = BSD["line_asset_cash"]
check("C2 such a line still opens, and its one row says the breakdown was NEVER RECORDED — it is "
      "not served as $0.00, which would be a different fact",
      len(_cash["rows"]) == 1 and _cash["rows"][0]["amount"] is None
      and _cash["rows"][0]["measured"] is False
      and _cash["rows"][0]["reason"] == "detail_not_recorded",
      _cash["rows"][0])
check("C3 and the tie-out REFUSES to claim a tie — `None`, never False: a sum missing its only row "
      "was never a comparison",
      _cash["tie_out"]["ties"] is None and _cash["figure"]["amount"] == _amt(BS, "asset", "cash"),
      _cash["tie_out"])
check("C4 the reason is in the shared vocabulary, so the screen prints a sentence and not a code",
      rd.UNMEASURED_REASONS.get("detail_not_recorded", "").startswith("the stored statement"))
_hp = BSD["line_liability_handset_payable"]
check("C5 a line that DOES record a breakdown drills to its entries and ties exactly",
      _hp["tie_out"]["ties"] is True and _hp["rows_total_count"] == 2
      and _row(_hp, "Activation")["amount"] == 60.0, _hp["tie_out"]["words"])
_se_src = _read("backend", "app", "modules", "account", "statement_engine.py")
_eng_src = _read("backend", "app", "modules", "account", "engine.py")
check("C6 FINDING 2, at the source: `statement_engine._book` records only the org-wide `detail` and "
      "never `detail_by_store`, while `engine._scoped` builds a scope's detail ONLY from "
      "`detail_by_store` — so the two lines that DO record a breakdown lose it on every company, "
      "store and filtered read",
      'line["detail"][detail]' in _body("def _book(", _se_src)
      and "detail_by_store" not in _body("def _book(", _se_src)
      and 'line.get("detail_by_store")' in _body("def _scoped(", _eng_src))
_tampered = bs_statement()
for _l in _sec(_tampered, "liability")["lines"]:
    if _l["key"] == "handset_payable":
        _l["amount"] = round(_l["amount"] + 75.0, 2)      # a journal entry folded on by label
_hp2 = sd.balance_sheet_drilldowns(_tampered)["line_liability_handset_payable"]
check("C7 FINDING 3: a manual entry folded onto a computed line by label adds dollars and no "
      "evidence, and the panel SAYS the report and its own evidence disagree",
      _hp2["tie_out"]["ties"] is False and "disagree" in _hp2["tie_out"]["words"],
      _hp2["tie_out"]["words"])
check("C8 and `engine._assemble` really does fold it that way — matched BY LABEL, amount only",
      'match["amount"] = _round(match["amount"] + amt)' in _eng_src
      and 'ln["label"] == label' in _eng_src)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE CASH FLOW, produced by the REAL `statement_engine.cash_flow` over two balance sheets.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
BS_PRIOR = {"sections": [
    {"type": "asset", "lines": [_ln("cash", "Cash / bank", 100.0, "manual"),
                                _ln("inventory", "Inventory", 400.0)]},
    {"type": "liability", "lines": [_ln("owed_vip", "Owed to Distributor", 200.0)]},
    {"type": "equity", "lines": [_ln("owner_capital", "Owner capital", 50.0, "manual")]}]}
BS_CUR = {"sections": [
    {"type": "asset", "lines": [_ln("cash", "Cash / bank", 300.0, "manual"),
                                _ln("inventory", "Inventory", 500.0),
                                _ln("fixtures", "Fixtures / equipment", 70.0, "manual")]},
    {"type": "liability", "lines": [_ln("owed_vip", "Owed to Distributor", 260.0),
                                    _ln("je_loan", "Loan", 500.0, "manual")]},
    {"type": "equity", "lines": [_ln("owner_capital", "Owner capital", 90.0, "manual")]}]}
CF = se.cash_flow({"net_income": 120.0}, BS_CUR, BS_PRIOR, PERIOD, SCOPE, "Consolidated")
CFD = sd.cash_flow_drilldowns(CF)

print("\n== D. the Cash Flow, over the real producer ==")
check("D1 every section subtotal has a panel, and its own lines add up to it exactly",
      all(CFD[s["type"]]["tie_out"]["ties"] is True and
          CFD[s["type"]]["figure"]["amount"] == s["subtotal"] for s in CF["sections"]),
      [(s["type"], CFD[s["type"]]["tie_out"]["words"]) for s in CF["sections"]])
check("D2 the implied change in cash opens to the three subtotals and ties",
      CFD["implied_cash_change"]["figure"]["amount"] == CF["implied_cash_change"]
      and CFD["implied_cash_change"]["tie_out"]["ties"] is True
      and CFD["implied_cash_change"]["rows_total_count"] == 3,
      CFD["implied_cash_change"]["tie_out"]["words"])
check("D3 A DIFFERENCE AND A LEAF GET NO PANEL — the tie-out delta, the reported change and the two "
      "cash balances are ABSENT from the fold, not served as a check that cannot fail, and a "
      "cash-flow LINE has no panel of its own because it is already the leaf",
      not ({"tie_delta", "cash_delta_reported", "cash_begin", "cash_end"} & set(CFD))
      and not any(k.startswith("line_") for k in CFD), sorted(CFD))
check("D4 every row names the snapshot and carries its line key",
      all(_tables(r) == ["account_statements"] and (r.get("row_key") or {}).get("line")
          for p in CFD.values() for r in p["rows"]))
check("D5 net income rides in operating as a row of its own, marked as what it is rather than as a "
      "change in a balance-sheet line",
      _row(CFD["operating"], "Net income")["fields"]["kind"] == "net income"
      and _row(CFD["operating"], "Change in Inventory")["fields"]["kind"] == "change")
_first = se.cash_flow({"net_income": 120.0}, BS_CUR, None, PERIOD, SCOPE, "Consolidated")
_firstd = sd.cash_flow_drilldowns(_first)
check("D6 the FIRST computed period — no prior balance sheet — still drills, and every panel still "
      "ties against the deltas that are the full current balances",
      _first["comparative"] is False
      and all(p["tie_out"]["ties"] is True for p in _firstd.values()),
      {k: v["tie_out"]["words"] for k, v in _firstd.items()})
check("D7 the panel says in words why only this half of the tie-out opens",
      "does not" in (CFD["implied_cash_change"]["note"] or ""))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE LIABILITIES PAYLOAD, built by the REAL producers the endpoint composes it from.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
WK_START, WK_END, TODAY = "2026-10-01", "2026-10-07", "2026-10-05"
FAMS = ["activation"]
TX = [
    # inside the window, mapped to a store
    {"account_id": "A1", "order_type": "Activation", "tx_date": "2026-09-20",
     "due_date": "2026-10-02", "retail_cost": 400.0},
    # inside the window, account NOT mapped to a store → the company-wide remainder
    {"account_id": "A9", "order_type": "Activation", "tx_date": "2026-09-21",
     "due_date": "2026-10-06", "retail_cost": 100.0},
    # still open but due later — outstanding, not due this week
    {"account_id": "A1", "order_type": "Activation", "tx_date": "2026-09-22",
     "due_date": "2026-11-15", "retail_cost": 250.0},
]
ACCT_STORE = {"A1": "B-1"}
_out_bookings, _out_meta = bsx.handset_payable_bookings(TX, FAMS, TODAY)
_due_rows, _due_meta = ld.payables_due_in_window(TX, FAMS, WK_START, WK_END)
_o_by, _o_cw = ld.attribute_stores([{"account_id": a, "amount": amt, "order_type": d}
                                    for (a, amt, d) in _out_bookings], ACCT_STORE)
_d_by, _d_cw = ld.attribute_stores(_due_rows, ACCT_STORE)

PRAW = [
    {"employee_id": "e1", "name": "Abid", "store": "B-1", "pay_rate": 20.0, "total_hours": 40.0,
     "clocked_hours": 40.0, "manual_hours": 0.0, "basis": "clocked", "settings": {}},
    {"employee_id": "e2", "name": "Bea", "store": "B-2", "pay_rate": 18.0, "total_hours": 12.0,
     "clocked_hours": 0.0, "manual_hours": 12.0, "basis": "clocked", "settings": {}},
    # no punch and no hand-entered hours at all: `payroll_raw` fell back to the SCHEDULE
    {"employee_id": "e3", "name": "Cy", "store": "B-1", "pay_rate": 16.0, "total_hours": 8.0,
     "clocked_hours": 0.0, "manual_hours": 0.0, "basis": "scheduled", "settings": {}},
]
AGG = ld.aggregate_payroll(PRAW, pte.compute_pay)          # the REAL tax twin
LEASES = [
    {"store_code": "B-1", "current_rent": 3000.0, "rent_due": None, "insurance_premium": 120.0,
     "insurance_premium_due": "2026-10-05", "insurance_premium_frequency": "annual"},
    # a store whose rent is NOT set — shown, never counted, never $0.00
    {"store_code": "B-9", "current_rent": None, "rent_due": None},
]
_rent_rows = ld.rent_due_rows(LEASES, None, WK_START, WK_END)
_ins_rows = ld.insurance_due_rows(LEASES, WK_START, WK_END)
_rent_total, _rent_unknown = ld.sum_known(_rent_rows)
_ins_total, _ = ld.sum_known(_ins_rows)


def liabilities_payload(*, basis="marketplace_due", pay_allowed=True, lease_allowed=True):
    return {
        "as_of": TODAY, "week": {"start": WK_START, "end": WK_END},
        "distributor": {
            "configured": basis != "off", "basis": basis,
            "outstanding": ({"total": _out_meta["total"], "rows": _out_meta["rows"],
                             "as_of": TODAY, "company_wide": _o_cw,
                             "by_store": [{"store": s, "market": "BK", "amount": v}
                                          for s, v in sorted(_o_by.items())]}
                            if basis != "off" else None),
            "due_this_week": ({"total": _due_meta["total"], "rows": _due_rows,
                               "company_wide": _d_cw,
                               "by_store": [{"store": s, "market": "BK", "amount": v}
                                            for s, v in sorted(_d_by.items())]}
                              if basis != "off" else None)},
        "payroll": ({"allowed": True, "due": [dict(AGG, start="2026-09-14", end="2026-09-27",
                                                   payday="2026-10-02")],
                     "current": dict(AGG, start="2026-09-28", end="2026-10-11",
                                     payday="2026-10-16")}
                    if pay_allowed else {"allowed": False, "note": "restricted"}),
        "rents": ({"allowed": True, "rows": _rent_rows, "total": _rent_total,
                   "unknown": _rent_unknown} if lease_allowed else {"allowed": False}),
        "insurance": ({"allowed": True, "rows": _ins_rows, "total": _ins_total}
                      if lease_allowed else {"allowed": False}),
    }


LP = liabilities_payload()
LDD = ldd.drilldowns(LP)

print("\n== E. the liabilities composition, opened ==")
check("E1 all six tiles have a panel, and every one of them ties against the figure the endpoint "
      "serves — four composed derivations, each now openable",
      sorted(LDD) == ["distributor_due_week", "distributor_outstanding", "insurance_due",
                      "payroll_due_gross", "payroll_due_tax", "rents_due"]
      and all(p["tie_out"]["ties"] is True for p in LDD.values()),
      {k: v["tie_out"]["words"] for k, v in LDD.items() if v["tie_out"]["ties"] is not True})
check("E2 the company-wide remainder is a ROW, not a figure folded into the total with nothing to "
      "open — an unmapped account is where this tile quietly loses its explanation",
      _row(LDD["distributor_outstanding"], ldd._COMPANY_WIDE_LABEL)["amount"] == _o_cw
      and _o_cw > 0)
check("E3 the feed named is the one the RESOLVED basis read, keyed on the stored config value",
      [s["table"] for s in LDD["distributor_outstanding"]["sources"]] == ["raw_ma_daily_tx"]
      and [s["table"] for s in ldd.drilldowns(liabilities_payload(basis="asset_ledger"))
           ["distributor_outstanding"]["sources"]] == ["asset_ledger"])
_unknown = ldd.drilldowns(liabilities_payload(basis="some_future_basis"))
check("E4 a basis nothing knows a feed for names NO table and is REPORTED as an untraceable origin "
      "with a reason — a wrong provenance claim is worse than an admitted gap",
      _unknown["distributor_outstanding"]["sources"] == []
      and _unknown["distributor_outstanding"]["untraceable_origins"] == 2
      and _unknown["distributor_outstanding"]["rows"][0]["reason"] == "feed_unresolved"
      and _unknown["distributor_outstanding"]["tie_out"]["ties"] is True)
check("E5 payroll opens to ONE ROW PER PERSON, out of the fold that produced the total — Σ the rows "
      "is the gross_total `aggregate_payroll` served, over the REAL tax calculator",
      LDD["payroll_due_gross"]["rows_total_count"] == 3
      and LDD["payroll_due_gross"]["figure"]["amount"] == AGG["gross_total"]
      and LDD["payroll_due_gross"]["tie_out"]["ties"] is True,
      (AGG["gross_total"], LDD["payroll_due_gross"]["tie_out"]["words"]))
check("E6 the tax tile opens to the same people and ties to the tax total, naming the withholding "
      "setup that decides the withheld half",
      LDD["payroll_due_tax"]["figure"]["amount"] == AGG["tax"]["total"]
      and LDD["payroll_due_tax"]["tie_out"]["ties"] is True
      and "payroll_settings" in _tables(LDD["payroll_due_tax"]["rows"][0]),
      LDD["payroll_due_tax"]["tie_out"]["words"])
check("E7 THE HOURS TABLE IS READ PER PERSON, not claimed for all of them: the one who clocked in "
      "names the time clock, the one whose hours were typed names the hand-entered hours, and the "
      "one with no punch at all names the SCHEDULE — because that is what answered for them",
      _tables(_row(LDD["payroll_due_gross"], "Abid · payday 2026-10-02")) == ["employees", "timelog"]
      and _tables(_row(LDD["payroll_due_gross"], "Bea · payday 2026-10-02"))
      == ["employees", "manual_hours"]
      and _tables(_row(LDD["payroll_due_gross"], "Cy · payday 2026-10-02"))
      == ["employees", "shifts"],
      [(r["label"], _tables(r)) for r in LDD["payroll_due_gross"]["rows"]])
_norent = next(r for r in LDD["rents_due"]["rows"] if "B-9" in r["label"])
check("E8 a store whose rent is NOT SET is shown WITHOUT a figure and not counted — which is exactly "
      "what the report does (`sum_known` excludes it and counts it separately), and the tie-out "
      "still holds exactly",
      _norent["amount"] is None and _norent["in_figure"] is False
      and _norent["reason"] == "amount_not_recorded"
      and LDD["rents_due"]["tie_out"]["ties"] is True
      and LDD["rents_due"]["tie_out"]["shown_not_counted"] == 1
      and _rent_unknown == 1, _norent)
_gated = ldd.drilldowns(liabilities_payload(pay_allowed=False, lease_allowed=False))
check("E9 FAIL CLOSED HERE TOO — a section the server's gate refused carries no total, so it gets "
      "NO panel rather than a panel asserting $0.00 behind a figure nobody is allowed to see",
      sorted(_gated) == ["distributor_due_week", "distributor_outstanding"], sorted(_gated))
_off = ldd.drilldowns(liabilities_payload(basis="off"))
check("E10 and a basis that is OFF offers no distributor panels, because the endpoint produced no "
      "figure for them", _off.get("distributor_outstanding") is None)
check("E11 every row carries its row key, which is what lets the screen's own client-side store / "
      "market filter narrow the panel the tile reads its number off (§CORE-17.3d)",
      all((r.get("row_key") or {}) or r["label"] == ldd._COMPANY_WIDE_LABEL
          for p in LDD.values() for r in p["rows"]))

print("\n== F. the drill names no database ==")
MIG = " ".join(open(f, encoding="utf-8", errors="ignore").read()
               for f in __import__("glob").glob(os.path.join(ROOT, "database/migrations/*.sql")))
SRC_LABELS = _read("frontend", "src", "lib", "sourceLabels.ts")
TABLES = sorted({sd.SNAPSHOT, sd.JOURNAL, ldd.EMPLOYEES, ldd.TIMELOG, ldd.MANUAL_HOURS,
                 ldd.SHIFTS, ldd.PAYROLL_SETTINGS, ldd.LEASE}
                | {t for ts in sd.LINE_FEEDS.values() for t in ts}
                | {t for ts in ldd.PAYABLE_BASIS_FEEDS.values() for t in ts})
_no_mig = [t for t in TABLES if not re.search(r"\b%s\b" % t, MIG)]
check("F1 every table these two declarations can name is created by a migration", _no_mig == [],
      _no_mig)
_no_label = [t for t in TABLES if not re.search(r"^\s+%s:" % t, SRC_LABELS, re.M)]
check("F2 BUILD LOCK — and every one has a `sourceLabels.ts` entry, so the operator reads a feed's "
      "plain name and never a table (owner: hide database names from the users)",
      _no_label == [], _no_label)
check("F3 and no label of them is a carrier, tenant or hosting name (RULE TWO reaches the copy too)",
      not re.search(r"(?i)(boost|total\s*wireless|luxelink|cellfonz|verizon|metro|supabase)",
                    "\n".join(re.findall(r"^\s+\w+: '([^']+)',", SRC_LABELS, re.M))))

print("\n== G. build locks — the mechanism cannot un-wire ==")
for _mod, _path in (("statement_drilldown", "app/modules/account/statement_drilldown.py"),
                    ("liabilities_drilldown", "app/modules/account/liabilities_drilldown.py")):
    _p = os.path.join(ROOT, "backend", _path)
    _src = open(_p, encoding="utf-8").read()
    _c = _code_only(_p)
    check("G1(%s) the declaration BUILDS no panel of its own — one shape, one renderer" % _mod,
          not any(k in _src for k in ('"tie_out"', '"rows_total_count"', '"untraceable_origins"',
                                      '"rows_shown"', "def panel(", "def tie_out(")))
    check("G2(%s) it holds no rate, threshold or dollar of its own — every amount it carries was "
          "handed to it" % _mod,
          not re.findall(r"\d+\.\d+", _c), re.findall(r"\d+\.\d+", _c))
    check("G3(%s) RULE TWO — no carrier, tenant or product name in it" % _mod,
          not re.search(r"(?i)\b(boost|total\s*wireless|luxelink|cellfonz|vzone|metro|verizon)\b",
                        _src))
    check("G4(%s) and it reads NOTHING from the database — the payload and every vocabulary are "
          "passed IN, so a panel cannot hold a different answer than the screen" % _mod,
          not any(k in _c for k in ("client", "execute", "schema(", "supabase", "db(")))
_acct_rt = _read("backend", "app", "modules", "account", "router.py")
_attach = _body("def _with_statement_drilldown(", _acct_rt)
check("G5 both statement endpoints attach their panels in ONE place, folded from the `statement` "
      "THEY ARE SERVING — so a drill-down cannot exist on the unfiltered read and go missing on the "
      "filtered one",
      'st = (resp or {}).get("statement")' in _attach
      and _acct_rt.count("_with_statement_drilldown(") == 4
      and "_with_statement_drilldown(\n            _filtered_read(" in _acct_rt)
_liab = _body("def _liabilities_due_impl(", _acct_rt)
check("G6 the liabilities endpoint hands the declaration the payload IT has just assembled, never a "
      "second read of anything",
      "_ldd.drilldowns(out)" in _liab and "liabilities_drilldown as _ldd" in _liab)
_ld_src = _read("backend", "app", "modules", "account", "liabilities_due.py")
_apay = _body("def aggregate_payroll(", _ld_src)
check("G7 ONE FOLD: `aggregate_payroll`'s per-person rows come out of the SAME loop that produces "
      "the totals — the drill-down behind those two tiles is the arithmetic they are, not a "
      "restatement of it",
      _apay.count("for r in raw_rows or []") == 1 and _apay.count("rows.append(") == 1
      and _apay.count("compute_pay(") == 1)
check("G8 every reason these declarations can emit is in the shared vocabulary, so no screen renders "
      "a raw code",
      all(k in rd.UNMEASURED_REASONS for k in ("detail_not_recorded", "amount_not_recorded",
                                               "feed_unresolved")))
check("G9 the per-person pay cells are not served twice — they travel inside the panels a reader "
      "opens, and are dropped from the section dicts",
      '_p.pop("rows", None)' in _liab)

print("\n== H. the screens offer what the server served, and nothing else ==")
_bs_page = _read("frontend", "src", "app", "(platform)", "accounts", "balance-sheet", "page.tsx")
_cf_page = _read("frontend", "src", "app", "(platform)", "accounts", "cash-flow", "page.tsx")
_ld_page = _read("frontend", "src", "app", "(platform)", "accounts", "liabilities-due", "page.tsx")
check("H1 all three screens render the ONE shared panel through the ONE offer rule",
      all("from '@/components/DrillDownPanel'" in p and "useDrillTile(" in p
          and "<DrillDownPanel" in p for p in (_bs_page, _cf_page, _ld_page)))
check("H2 the Balance Sheet offers a link on every section total, every line, the Liabilities + "
      "Equity row and the imbalance banner",
      all(k in _bs_page for k in ("drill.dl(`section_${s.type}`", "drill.dl(`line_${s.type}_${l.key}`",
                                  "drill.dl('liabilities_plus_equity'", "drill.dl('imbalance'")))
check("H3 the two STATEMENT screens do not narrow, and should not: their store/market filter is "
      "server-side, so the panels are already about the statement on screen",
      all("drill-narrow" not in p and "narrowPanels" not in p for p in (_bs_page, _cf_page)))
check("H4 the LIABILITIES screen does narrow — every filter on it is client-side — and each tile "
      "reads its number OFF its own narrowed panel, which closes the defect where the distributor "
      "tiles kept the unfiltered total over a filtered table",
      "narrowPanels(data?.drilldown || {}" in _ld_page
      and "p?.figure?.amount != null ? p.figure.amount" in _ld_page
      and _ld_page.count("tileAmt('") >= 6)
check("H5 the cash-flow screen says where the beginning / ending cash comes from, since those two "
      "figures deliberately have no panel here",
      "this statement does not carry" in _cf_page and "/accounts/balance-sheet" in _cf_page)
_bs_fields = {"section", "kind", "inside", "why_no_feed"}
check("H6 each screen's extra columns are read off the `fields` its own declaration serves",
      all("r.fields?.%s" % f in _bs_page for f in _bs_fields)
      and all("r.fields?.%s" % f in _cf_page for f in ("section", "kind"))
      and all("r.fields?.%s" % f in _ld_page for f in ("store", "market", "detail")))
check("H7 and the served rows really carry those keys, so a column cannot read a field nobody sends",
      set(BSD["section_asset"]["rows"][0]["fields"]) == _bs_fields
      and {"store", "market", "detail"} == set(LDD["rents_due"]["rows"][0]["fields"]))
check("H8 no screen hand-rolls a panel of its own",
      not any(k in p for p in (_bs_page, _cf_page, _ld_page)
              for k in ("tie_out:", "untraceable_origins:", "rows_total_count:")))

print("\n== I. registration ==")
_IDX = _read("docs", "SYSTEM_DATA_FLOW_INDEX.md")
for _f in ("statement_drilldown.py", "liabilities_drilldown.py", "harness_batch10_drilldown.py"):
    check("I1(%s) registered in the index" % _f, _f in _IDX)
check("I2 and the section this batch adds is there, so the next finance report does not re-derive "
      "what a snapshot can and cannot name", "CORE-17.3j" in _IDX)
_mg = _read("backend", "app", "modules", "core", "module_graph.py")
check("I3 both declarations are callers of the one home in the module graph",
      "account/statement_drilldown.py" in _mg and "account/liabilities_drilldown.py" in _mg)
check("I4 the harness is locked into CI",
      "harness_batch10_drilldown.py" in _read(".github", "workflows", "carrier-vocab-guard.yml"))
_ledger = _read("backend", "harness_drilldown_pending.txt")
for _r in ("/accounts/balance-sheet", "/accounts/cash-flow", "/accounts/liabilities-due"):
    check("I5(%s) reads `shared` in the coverage ledger, with a written note" % _r,
          re.search(r"^%s\tshared\t\S" % re.escape(_r), _ledger, re.M) is not None)
_cov = _read("backend", "harness_drilldown_coverage.py")
_owes = int(re.search(r"^OWES_MAX = (\d+)", _cov, re.M).group(1))
check("I6 the ratchet was re-pinned DOWNWARD by the three this batch built",
      _owes == 74, _owes)

print("\n== Z. negative controls — every check above can fail ==")
_z = bs_statement()
_sec(_z, "asset")["subtotal"] = round(_sec(_z, "asset")["subtotal"] + 0.01, 2)
check("Z1 a section subtotal a cent away from its own lines is CAUGHT, in words",
      sd.balance_sheet_drilldowns(_z)["section_asset"]["tie_out"]["ties"] is False)
_z2 = bs_statement()
_sec(_z2, "asset")["lines"] = [l for l in _sec(_z2, "asset")["lines"] if l["key"] != "inventory"]
check("Z2 a line dropped from the sheet while the subtotal keeps its dollars is CAUGHT",
      sd.balance_sheet_drilldowns(_z2)["section_asset"]["tie_out"]["ties"] is False)
_z3 = bs_statement()
for _l in _sec(_z3, "liability")["lines"]:
    if _l["key"] == "handset_payable":
        _l["detail"]["Activation"] = 1.0
check("Z3 a tampered breakdown entry breaks its own line's tie-out",
      sd.balance_sheet_drilldowns(_z3)["line_liability_handset_payable"]["tie_out"]["ties"] is False)
_z4 = bs_statement()
_z4["assets_total"] = round(_z4["assets_total"] + 10.0, 2)
check("Z4 an engine that disagrees with its OWN totals is caught by the imbalance panel — the two "
      "sides no longer add up to the gap it reports",
      sd.balance_sheet_drilldowns(_z4)["imbalance"]["tie_out"]["ties"] is False)
check("Z5 and the provenance rule really is per row: naming a line's feed REGARDLESS of whether it "
      "booked anything — the constant a careless declaration would serve — differs from what we "
      "serve for the $0.00 line",
      sd.line_tables("owed_vip", "auto", 0.0) == ("account_statements",)
      and sd.line_tables("owed_vip", "auto", 5.0)
      == ("account_statements", "asset_ledger", "vip_paygo_payments"))
_z6 = liabilities_payload()
_z6["payroll"]["due"][0]["rows"] = _z6["payroll"]["due"][0]["rows"][:-1]
check("Z6 a person dropped from the payroll rows while the tile keeps its total is CAUGHT",
      ldd.drilldowns(_z6)["payroll_due_gross"]["tie_out"]["ties"] is False)
_z7 = liabilities_payload()
_z7["rents"]["rows"] = [dict(r, amount=0.0) if r["amount"] is None else r
                        for r in _z7["rents"]["rows"]]
_z7p = ldd.drilldowns(_z7)["rents_due"]
check("Z7 folding the unrecorded rent in AT $0.00 changes the claim rather than the total — the row "
      "becomes a counted measurement of nothing, which is the fact this mechanism refuses to assert",
      _z7p["tie_out"]["shown_not_counted"] == 0
      and next(r for r in _z7p["rows"] if "B-9" in r["label"])["measured"] is True
      and _z7p["figure"]["amount"] == LDD["rents_due"]["figure"]["amount"])
_z8 = liabilities_payload()
_z8["distributor"]["by_store"] = None
_z8["distributor"]["outstanding"]["total"] = round(
    _z8["distributor"]["outstanding"]["total"] + 50.0, 2)
check("Z8 a distributor total that does not match its own per-store rows plus the company-wide "
      "remainder is CAUGHT",
      ldd.drilldowns(_z8)["distributor_outstanding"]["tie_out"]["ties"] is False)
_z9 = se.cash_flow({"net_income": 999.0}, BS_CUR, BS_PRIOR, PERIOD, SCOPE, "Consolidated")
_z9["sections"][0]["subtotal"] = 0.0
check("Z9 a cash-flow subtotal that is not the sum of its own lines is CAUGHT",
      sd.cash_flow_drilldowns(_z9)["operating"]["tie_out"]["ties"] is False)

print("\n%d/%d checks passed" % (len(P), len(P) + len(F)))
if F:
    print("FAILED:")
    for x in F:
        print("  -", x)
    sys.exit(1)
