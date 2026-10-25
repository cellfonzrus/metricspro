"""A FIGURE READ OUT OF A SNAPSHOT NAMES THE SNAPSHOT — the declaration for
/accounts/balance-sheet and /accounts/cash-flow (§CORE-17, batch 10).

OWNER ASK 2026-10-24, verbatim: *"update in index thta drilldown is needed for every report
geenerated from any data which comming from within the platforma as a defalut, assign an agent o
check all reports to check where the drill donw ins not there and sytart building it"*.

WHY ONE DECLARATION FOR TWO REPORTS. They share a GRAIN, not merely a module: both screens read a
stored `commcalc.account_statements` row for one (period, scope) and render `sections[].lines[]` with
a subtotal each, and the Cash Flow's own lines ARE Balance-Sheet LINE KEYS (`statement_engine.
cash_flow` labels each one "Change in <that line>"). One row shape, one provenance rule, two
catalogues — the `day_recon_drilldown` precedent (§CORE-17.3c), not two siblings that drift.

WHAT A FIGURE ON THESE SCREENS WAS ACTUALLY READ OUT OF, AND THE GAP THAT FOLLOWS
─────────────────────────────────────────────────────────────────────────────────
`GET /account/balance-sheet/{period}` and `GET /account/cash-flow/{period}` read ONE table:
`commcalc.account_statements`, the snapshot `statement_engine.compute_and_store` wrote. So every row
in every panel here names that table, keyed by the period, the scope and the line — which is the
honest answer to "where is this number from" and is where the trail STOPS:

  **A STORED STATEMENT DOES NOT RECORD WHICH FEED EACH LINE WAS READ OUT OF.** The compute knew (it
  had just read the asset ledger, the invoices, the borrowings), and it keeps the dollars and throws
  the provenance away. This declaration therefore names a FEED only where the booking code reads that
  line from it UNCONDITIONALLY (`LINE_FEEDS`), and for a line whose feed is chosen from per-org
  config AT COMPUTE TIME (`CONFIG_RESOLVED_LINES`) it names NO feed at all and says which basis
  decided it — because the snapshot does not record which basis answered, and naming one would be a
  provenance claim nothing checked. Closing the gap properly means the compute STORING the feed per
  line, which moves money-path code and is a PR of its own (index §CORE-17.3j).

A FEED IS NAMED ONLY WHERE SOMETHING WAS BOOKED. A line carrying no dollars at all had its feed read
and produced nothing, so it names only the snapshot — per ROW, never as a constant.

AND ONE FEED CANNOT BE NAMED AT ALL YET (`UNREGISTERED_FEED_LINES`). The chargeback reserve is read
from a table this repository's schema files only INDEX, never create, so the platform has no
registered plain name for it and inventing one would put a raw identifier on a manager's screen. That
line names no feed and SAYS WHY, in the reader's words — `why_no_feed()` answers the one question
over both registries, so a reader never has to tell an absence from a gap.

REPORTED, NOT PAPERED OVER — three live findings these two reports have today
────────────────────────────────────────────────────────────────────────────
1. **NO `coa.BS_SPEC` LINE CARRIES DRILL-DOWN DETAIL, SO THE SHEET'S OWN EXPANDER IS ALWAYS EMPTY.**
   `coa.add()` records a drill label only when its caller passes `detail_label`, and not one of the
   balance-sheet bookings does (inventory, owed-to-distributor, invoices unpaid, the chargeback
   reserve and both inter-store lines all call `add()` with three arguments). The screen renders an
   expander whenever `line.detail` is non-empty, so on the Balance Sheet it never opens — while the
   identical expander on the P&L is full. The figures are UNCHANGED here; such a line's panel says in
   words that the breakdown was never recorded (`detail_not_recorded`) and still names where the
   figure came from. Fixing it means passing a label at each booking, which changes stored payloads.
2. **A SCOPE LOSES THE DETAIL THE EXTRA LINES DO CARRY.** `statement_engine._book` records only
   `line["detail"]` (the org-wide roll-up) and never `detail_by_store`, while `engine._scoped` builds
   a non-consolidated scope's detail exclusively from `detail_by_store`. So the handset-payable and
   sales-tax lines — the two that DO record a breakdown — show it on Consolidated and show nothing on
   any company, store or filtered read. That is the §4e defect (owner report 2026-10-07) returning on
   the lines added after it was fixed for `coa.add`.
3. **A MANUAL JOURNAL ENTRY FOLDED ONTO A COMPUTED LINE ADDS DOLLARS AND NO EVIDENCE.**
   `engine._assemble` matches a journal entry to a spec line BY LABEL and adds its amount to that
   line without touching its detail. Where that happens the line's panel comes back `ties: False` in
   words — the report disagreeing with its own evidence, which is the outcome this mechanism exists
   to show rather than round away.

THE VOCABULARIES ARE DEREFERENCED, NEVER RE-SPELLED. The line keys, labels, sections and kinds are
read off the statement payload the endpoint is already serving; the spec vocabulary comes from
`coa.BS_SPEC` / `statement_engine.bs_spec()` through the payload itself. Nothing here re-derives a
line, a label or a dollar.

💰 MOVES NO MONEY. Read-only presentation of a stored snapshot. PURE: no I/O, no framework import,
no clock, no org / carrier / tenant / product name (RULE TWO).
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3j.
"""
from app.modules.core import report_drilldown

#: THE table both reports read. Every row in every panel here names it, because that is where the
#: figure was read out of — the endpoints select one `account_statements` row and render its payload.
SNAPSHOT = "account_statements"

#: The schema that table lives in.
SCHEMA = "commcalc"

#: Where a MANUAL line's dollars are entered — `coa.journal_rows` is the only thing that books a
#: `manual` line, and `engine._assemble` appends an unmatched entry as one.
JOURNAL = "journal_entries"

#: WHICH FEED A BALANCE-SHEET LINE'S BOOKINGS ARE READ OUT OF, where the booking code reads that line
#: from it UNCONDITIONALLY. Keyed on the spec line key, so a line renamed in `coa.BS_SPEC` loses its
#: claim here rather than carrying a stale one. A line NOT in this map and not manual or computed
#: names only the snapshot — see this module's docstring for why a guess would be worse.
LINE_FEEDS = {
    # coa.build_inputs: the on-inventory ledger rows' owed column, plus the pending PayGo batches.
    "owed_vip": ("asset_ledger", "vip_paygo_payments"),
    # coa.build_inputs: every distributor invoice whose status is not paid or voided.
    "vip_ap": ("vip_invoices",),
    # coa.build_inputs: one borrowing row is both a receivable for the lender and a payable for the
    # borrower, so both lines read the same table.
    "inter_store_recv": ("store_borrowings",),
    "inter_store_pay": ("store_borrowings",),
    # ma_store_pnl: wallet funding off either marketplace feed — a settlement, never revenue.
    "distributor_clearing": ("raw_ma_commission", "raw_ma_daily_tx"),
    # statement_engine: the tax the sales lines collected, read through the Tax Collected page's own
    # union of the two sales feeds.
    "sales_tax_payable": ("raw_sales", "daily_sales_feed"),
}

#: A LINE WHOSE FEED THE CODE READS AND THIS HOME CANNOT NAME YET, with the reason. The chargeback
#: reserve is booked from `commcalc.chargeback_items`, which `coa` reads and migrations 030/036 index
#: — but NO migration in this repository CREATES it, so it is not a REGISTERED table, and a
#: `sourceLabels.ts` entry for an unregistered table fails the infra lock
#: (`harness_carrier_vocab_guard` IW3). Naming it would therefore either break the build or put a raw
#: table name on a manager's screen. REPORTED rather than papered over (index §CORE-17.3j): the
#: remedy is a migration that creates or formally registers the table, which is a schema change and
#: not something a drill-down PR does.
UNREGISTERED_FEED_LINES = {
    # The SENTENCE A READER SEES, so it says what is true of their screen and nothing about the
    # platform's internals — the setup-internals lock (harness_carrier_vocab_guard §SETUP) is right
    # to refuse those words in copy, and the engineering reason is in the comment above.
    "chargeback_res": ("the place this line is read from has no plain name in the platform yet, so "
                       "naming it here would show a database name instead of a report"),
}

#: THE LINES WHOSE FEED IS CHOSEN FROM PER-ORG CONFIG AT COMPUTE TIME, with what it chose between —
#: in the reader's words, because the snapshot does not record which basis answered. These lines name
#: NO feed: a provenance claim nothing checked reads like rigour and is a lie.
CONFIG_RESOLVED_LINES = {
    "inventory": ("the inventory basis — the emailed aging report, the per-device ledger or the "
                  "asset landing, whichever this org is configured for"),
    "handset_payable": ("the distributor-payable basis — the marketplace due-date window or the "
                        "asset ledger's open balance, whichever this org is configured for"),
    "store_cash_on_hand": ("the store-cash basis — every store-day's declared cash or only the "
                           "district-manager-verified ones, whichever this org is configured for"),
}

#: Said on a line whose breakdown the snapshot never recorded (finding 1 above).
NO_DETAIL_NOTE = ("The compute stored this line's dollars without storing what they were summed "
                  "from, so there is no breakdown to open — the figure's source is named above it.")

#: Said on every Balance-Sheet section panel.
SECTION_NOTE = ("These are the lines this subtotal is the sum of, exactly as the stored statement "
                "holds them. A line's own breakdown opens from the line.")

#: Said on the imbalance panel, where one of the two rows is counted NEGATIVE.
IMBALANCE_NOTE = ("Assets less liabilities and equity. The second row is counted negative, which is "
                  "what makes these two rows add up to the imbalance above them.")

#: Said on every Cash Flow panel, because a derived statement is not a measured one.
CASH_FLOW_NOTE = ("Each row is the change in one balance-sheet line between this period's stored "
                  "statement and the prior period's — a derived figure, not one anybody recorded.")


def line_tables(key, kind, amount):
    """PURE: the tables ONE statement line was read out of — per line, never a constant.

    The snapshot is always named: it is where the endpoint read the figure. A feed is added only when
    the booking code reads this line from it unconditionally AND the line carries dollars, because a
    line at $0.00 had its feed read and nothing came back — claiming it answered would be the
    provenance lie this mechanism exists to refuse.
    """
    out = [SNAPSHOT]
    if not report_drilldown.money(amount):
        return tuple(out)
    if kind == "manual" or str(key or "").startswith("je_"):
        out.append(JOURNAL)
    elif kind == "computed":
        # Retained earnings is the accumulated net income of the PRIOR P&L snapshots — the same
        # table, read again under a different statement type, which the origin's keys say.
        pass
    else:
        out.extend(LINE_FEEDS.get(key) or ())
    return tuple(out)


def _origins(period, scope_key, statement_type, key, *, tables, column="payload"):
    keys = {"period": period, "scope_key": scope_key, "statement_type": statement_type,
            "line": key}
    return report_drilldown.origins_of([(t, keys) for t in tables], schema=SCHEMA, column=column)


def why_no_feed(key):
    """PURE: why a line names no feed — the basis that chooses it at compute time, or the fact that
    its feed is a table the platform cannot name yet. One question, one answer, so a reader never has
    to guess whether an unnamed feed is an absence or a gap."""
    return CONFIG_RESOLVED_LINES.get(key) or UNREGISTERED_FEED_LINES.get(key)


def _line_fields(section_name, line, *, inside=None):
    line = line or {}
    return {"section": section_name,
            "kind": line.get("kind") or "auto",
            "inside": inside,
            "why_no_feed": why_no_feed(line.get("key"))}


def _detail_rows(line, section_name, *, period, scope_key, statement_type):
    """The rows behind ONE line: the breakdown the compute recorded, or ONE row saying it never
    recorded any. The second case is finding 1 in this module's docstring — shown, uncounted as a
    measured figure, and never served as $0.00."""
    line = line or {}
    key, label = line.get("key"), line.get("label") or line.get("key")
    kind = line.get("kind") or "auto"
    tables = line_tables(key, kind, line.get("amount"))
    detail = line.get("detail") or {}
    if not detail:
        return [report_drilldown.drill_row(
            label, _origins(period, scope_key, statement_type, key, tables=tables),
            row_key={"line": key}, amount=None, in_figure=True, reason="detail_not_recorded",
            fields=_line_fields(section_name, line))]
    return [report_drilldown.drill_row(
        str(dl), _origins(period, scope_key, statement_type, key, tables=tables),
        row_key={"line": key, "entry": str(dl)}, amount=dv, in_figure=True,
        fields=_line_fields(section_name, line, inside=label))
        for dl, dv in detail.items()]


def _section_rows(section, *, period, scope_key, statement_type):
    name = (section or {}).get("name") or (section or {}).get("type")
    return [report_drilldown.drill_row(
        ln.get("label") or ln.get("key"),
        _origins(period, scope_key, statement_type, ln.get("key"),
                 tables=line_tables(ln.get("key"), ln.get("kind") or "auto", ln.get("amount"))),
        row_key={"line": ln.get("key")}, amount=ln.get("amount"), in_figure=True,
        fields=_line_fields(name, ln))
        for ln in ((section or {}).get("lines") or [])]


def _l_plus_e(statement):
    """The figure the screen prints as "Liabilities + Equity": the two stored subtotals added, the
    one way this mechanism rounds. Not a fold of rows — the rows ARE those two subtotals."""
    st = statement or {}
    return report_drilldown.money(st.get("liabilities_total")) + report_drilldown.money(
        st.get("equity_total"))


def _addr(statement):
    s = statement or {}
    return s.get("period"), s.get("scope_key")


# ── THE BALANCE SHEET ─────────────────────────────────────────────────────────────────────────────

def balance_sheet_drilldowns(statement, *, cap=None):
    """PURE: a panel per figure on the Balance Sheet — one per section subtotal, one per line, the
    Liabilities + Equity total and the imbalance.

    Every figure is the STATEMENT'S OWN, dereferenced: a subtotal is the stored `subtotal`, a line is
    the stored `amount`, and the two totals are the stored `liabilities_total` / `equity_total` /
    `imbalance`. Nothing is re-folded, so a tile and its panel cannot be two arithmetics — and where
    the stored subtotal disagrees with its own lines the tie-out says so in words.
    """
    st = statement or {}
    period, scope = _addr(st)
    cap = cap or report_drilldown.ROW_CAP
    out = {}
    for sec in (st.get("sections") or []):
        stype = sec.get("type")
        if not stype:
            continue
        rows = _section_rows(sec, period=period, scope_key=scope, statement_type="balance_sheet")
        out["section_%s" % stype] = report_drilldown.panel(
            "Total %s" % (sec.get("name") or stype), sec.get("subtotal"), rows,
            subject=sec.get("name"), subject_kind="section", note=SECTION_NOTE, cap=cap)
        for ln in (sec.get("lines") or []):
            if not ln.get("key"):
                continue
            out["line_%s_%s" % (stype, ln["key"])] = report_drilldown.panel(
                ln.get("label") or ln["key"], ln.get("amount"),
                _detail_rows(ln, sec.get("name") or stype, period=period, scope_key=scope,
                             statement_type="balance_sheet"),
                subject=sec.get("name"), subject_kind="section",
                note=(NO_DETAIL_NOTE if not (ln.get("detail") or {}) else None), cap=cap)
    # The screen prints Liabilities + Equity as one number; it is the two stored subtotals and
    # nothing else, so those are its rows.
    if st.get("liabilities_total") is not None and st.get("equity_total") is not None:
        rows = [report_drilldown.drill_row(
            lbl, _origins(period, scope, "balance_sheet", k, tables=(SNAPSHOT,)),
            row_key={"line": k}, amount=amt, in_figure=True,
            fields={"section": lbl, "kind": "subtotal", "inside": None, "why_no_feed": None})
            for k, lbl, amt in (("liabilities_total", "Total Liabilities",
                                 st.get("liabilities_total")),
                                ("equity_total", "Total Equity", st.get("equity_total")))]
        out["liabilities_plus_equity"] = report_drilldown.panel(
            "Liabilities + Equity", _l_plus_e(st), rows, note=SECTION_NOTE, cap=cap)
    if st.get("imbalance") is not None:
        rows = [report_drilldown.drill_row(
            "Total Assets", _origins(period, scope, "balance_sheet", "assets_total",
                                     tables=(SNAPSHOT,)),
            row_key={"line": "assets_total"}, amount=st.get("assets_total"), in_figure=True,
            fields={"section": "Assets", "kind": "subtotal", "inside": None,
                    "why_no_feed": None}),
            report_drilldown.drill_row(
            "Liabilities + Equity (counted negative)",
            _origins(period, scope, "balance_sheet", "liabilities_plus_equity",
                     tables=(SNAPSHOT,)),
            row_key={"line": "liabilities_plus_equity"},
            amount=-_l_plus_e(st), in_figure=True,
            fields={"section": "Liabilities + Equity", "kind": "subtotal", "inside": None,
                    "why_no_feed": None})]
        out["imbalance"] = report_drilldown.panel(
            "Assets − (Liabilities + Equity)", st.get("imbalance"), rows, note=IMBALANCE_NOTE,
            cap=cap)
    return out


# ── THE CASH FLOW ─────────────────────────────────────────────────────────────────────────────────

def cash_flow_drilldowns(statement, *, cap=None):
    """PURE: a panel per section subtotal on the Cash Flow, plus one for the implied change in cash.

    A cash-flow LINE gets no panel: it is already the leaf — one balance-sheet line's change between
    two stored statements, with nothing underneath it on this report. The beginning and ending cash
    balances get none either, for the reason the screen now states: they are the Balance Sheet's own
    cash lines, which this payload does not carry, so the honest answer is the Balance Sheet rather
    than a panel of rows this report never had. And the tie-out delta is implied MINUS reported — two
    figures, one of which has a real panel — so it offers that one instead of a panel that would
    explain a subtraction (§CORE-17.3h, a quotient or a difference offers its sides).
    """
    st = statement or {}
    period, scope = _addr(st)
    cap = cap or report_drilldown.ROW_CAP
    out, subtotals = {}, []
    for sec in (st.get("sections") or []):
        stype = sec.get("type")
        if not stype:
            continue
        name = sec.get("name") or stype
        rows = [report_drilldown.drill_row(
            ln.get("label") or ln.get("key"),
            _origins(period, scope, "cash_flow", ln.get("key"), tables=(SNAPSHOT,)),
            row_key={"line": ln.get("key")}, amount=ln.get("amount"), in_figure=True,
            fields={"section": name, "kind": ("net income" if ln.get("key") == "net_income"
                                              else "change"), "inside": None,
                    "why_no_feed": None})
            for ln in (sec.get("lines") or [])]
        out[stype] = report_drilldown.panel(
            "Net cash — %s" % name.lower(), sec.get("subtotal"), rows, subject=name,
            subject_kind="section", note=CASH_FLOW_NOTE, cap=cap)
        subtotals.append((stype, name, sec.get("subtotal")))
    if st.get("implied_cash_change") is not None and subtotals:
        rows = [report_drilldown.drill_row(
            "Net cash — %s" % name.lower(),
            _origins(period, scope, "cash_flow", stype, tables=(SNAPSHOT,)),
            row_key={"line": stype}, amount=amt, in_figure=True,
            fields={"section": name, "kind": "subtotal", "inside": None, "why_no_feed": None})
            for stype, name, amt in subtotals]
        out["implied_cash_change"] = report_drilldown.panel(
            "Implied change in cash", st.get("implied_cash_change"), rows,
            note=("The three section subtotals and nothing else. The REPORTED change beside it on "
                  "screen is the balance sheet's own cash lines, which this statement does not "
                  "carry — that is why only this half of the tie-out opens."), cap=cap)
    return out
