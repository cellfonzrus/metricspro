"""DID THE CARRIER PAY WHAT WE COMPUTED, AND OUT OF WHICH LEGS — the declaration for the three
commission disagreement reports (§CORE-17, batch 11).

  /commcalc/discrepancy      Pay Discrepancy — per commission LINE: what we expected against what
                             the carrier paid, and the gap.
  /commcalc/carrier-vs-pay   Carrier Earned vs Employee Paid — per REP: dealer revenue beside the
                             payroll expense, never summed.
  /commcalc/commission-legs  Commission received over M1–M12 — per money STREAM × month: which leg
                             of a subscriber's life the money arrived for.

ONE MODULE, THREE CATALOGUES, AND WHY THAT IS NOT A SIBLING PAIR
────────────────────────────────────────────────────────────────
The batch-10 precedent (`account/statement_drilldown.py`) put TWO reports in one declaration because
they share a ROW GRAIN. These three do not: a discrepancy row is a commission LINE, a carrier-vs-pay
row is a REP, a breakout row is a money STREAM in a MONTH. Three row shapes is three catalogues, and
pretending otherwise would make one declaration lie about two of its reports.

What they DO share is the thing a drill-down exists for, so it lives here once and all three
dereference it:

  · **where a carrier-money figure came from** — `FEEDS_BY_STREAM` and
    `data_lineage_registry.discrepancy_writer_feeds`, both keyed on a value the platform STORED, so a
    new stream or a third discrepancy writer is a ROW plus its derivation and never a branch on a
    carrier, tenant or product name (RULE TWO);
  · **"one side of this was never reported, which is not the same as zero"** — the reason KEYS are
    `carrier_vs_pay.EARNED_REASON_*` themselves, imported rather than re-spelled, so the two
    vocabularies cannot drift (locked: harness §Y1);
  · **a figure read out of a SNAPSHOT names the snapshot** (§CORE-17.3j) — the Pay Discrepancy report
    reads `discrepancy_results`, a table two engines write into, so that is what its rows name.

Putting them in three files would be three answers to "where does carrier-vs-ours evidence come
from", which is the duplicate defect `CLAUDE.md` forbids arriving one report at a time.

A DIFFERENCE IS NOT A SUM — EXCEPT WHEN IT REALLY IS ONE
────────────────────────────────────────────────────────
The §CORE-17.3h ruling (no panel for a quotient) was extended in batch 10 to a figure computed as
A_total − B_total: no set of records adds up to it, so a tie-out against it is a check that cannot
fail. BOTH differences on these reports escape that, and for the same reason the 3-way recon's net
figures did (§CORE-17.3e): they are folded PER ROW and then added up. A discrepancy row carries its
own `gap`; a carrier-vs-pay row carries its own `difference`. Σ(those rows) against the report's total
is a real comparison that can disagree, so both are drillable. The report's PERCENTAGES and rep COUNTS
are not declared here, and §CORE-17.3k says why.

THREE LIVE DEFECTS THESE REPORTS HAVE TODAY — REPORTED, NOT PAPERED OVER, AND NO FIGURE MOVED
─────────────────────────────────────────────────────────────────────────────────────────────
1. **`GET /commcalc/discrepancy/{period}` RAISES on a row whose gap was never sized.**
   `carrier_claim_value.claim_gap` returns **None** when the expected side could not be measured
   (which is right: a gap is a subtraction), `discrepancy_engine` persists that NULL, and the
   endpoint's store rollup then evaluates `r["gap"] > 0.50` on it — a `TypeError` in Python 3, so the
   whole report 500s for any period holding one. `SOLD_NOT_IN_MI` with no parseable MRC is exactly
   such a row and it is written with `status='open'`, which is the branch that compares. Not fixed
   here: the repair has to decide whether an unsized open claim counts toward `total_flagged`, which
   is a decision about money. Harness §Z6 reproduces it against the real producer.
2. **The same report counts an unsized claim as $0.00 on the screen.** `tabTotal` is
   `rows.reduce((s, r) => s + r.gap, 0)` and `null` coerces to 0 in JavaScript, so a claim nobody
   could size is folded in as nothing. The panels here show those rows WITHOUT a figure and do not
   count them (`amount_not_recorded`), and because a null contributes zero to a sum either way **the
   figure is identical to the cent** — the presentation is what changes.
3. **`GET /commcalc/carrier-vs-pay/{period}` narrows its ROWS under a market or store filter and
   leaves its TOTALS unfiltered.** `rollup_by_rep` folds the totals over every rep and the endpoint
   then filters `out["rows"]`, reporting what it dropped in `meta.filtered` — which the page does not
   show. So the four tiles describe every rep in the month while the table beneath them describes the
   selection. The panels here are folded over the UNFILTERED rows, because that is the population the
   figure they explain was folded over, and a panel that explained a tile with a different population
   would be proof of the wrong number (§CORE-17.3b). The page now says so out loud instead. Not fixed
   here: folding the totals over the kept rows MOVES a figure the owner reads, so it is a PR of its
   own. Harness §Z7 pins the defect.

💰 MOVES NO MONEY. Read-only presentation of figures the producers already computed. Books nothing,
pays nothing, writes nothing, computes no business figure of its own. PURE: no I/O, no framework
import, no clock, no org / carrier / tenant / product / store name (RULE TWO).
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3k.
"""
from app.modules.commcalc import carrier_vs_pay as _cvp
from app.modules.commcalc import commission_received as _cr
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.core import report_drilldown

#: The snapshot the Pay Discrepancy report is READ OUT OF. Dereferenced, never spelled here.
DISCREPANCY_SNAPSHOT = _lineage.DISCREPANCY_SNAPSHOT

#: Where the employee side of every pay comparison is read from, as stored.
REP_COMMISSIONS = "rep_commissions"

#: The carrier STATEMENT pair `ma_recon.load_ma_paid_rows` reads for every period, unconditionally —
#: so a rep whose activations were looked up in it names both, and a rep who was never looked up
#: names neither.
STATEMENT_FEEDS = (_lineage.MA_COMMISSION, _lineage.MA_DAILY_TX)

#: The sold universe `ma_recon.load_sold_sales` walks — the registry's own display union, which is
#: what that loader iterates. Not a list of tables written down twice.
SOLD_FEEDS = tuple(_lineage.SALES_DISPLAY_SOURCES)

#: WHICH TABLE EACH MONEY STREAM ON THE BREAKOUT WAS READ OUT OF, and which column held it where the
#: stream is one column of a shared feed. Keyed on `commission_received.STREAMS`' own stream keys, so
#: a new stream cannot ship without an entry (locked: harness §Y2 fails the build on a stream with no
#: row here, on a row naming a stream that does not exist, and on a table that does not appear in that
#: stream's own `source` sentence — the prose the page prints and this fact cannot diverge).
FEEDS_BY_STREAM = {
    "comm_epay": (_lineage.PAYMENT_DETAIL, None),
    "comm_ma": (_lineage.MA_COMMISSION, None),
    "comp_comm": (_lineage.COMP_REPORT, None),
    "mi": (_lineage.MI_RESIDUAL, "actual_mi_payout"),
    "atu": (_lineage.MI_RESIDUAL, "actual_atu_payout"),
    "ma_airtime": (_lineage.MA_DAILY_TX, "merchant_discount"),
    "ma_activation_margins": (_lineage.MA_COMMISSION, None),
    "ma_residual_orders": (_lineage.MA_DAILY_TX, "retail_cost"),
}

#: The four report tabs the Pay Discrepancy screen folds a total for, in its own order. These are the
#: `status` values `discrepancy_engine` writes; 'ok' is deliberately absent, because the report does
#: not show a tab for rows with nothing to chase. 'phantom' is absent too: it is a different endpoint
#: over a different row shape and is NOT claimed here (§CORE-17.3k).
DISCREPANCY_STATUSES = ("open", "pending", "lagged", "info")

#: What each tab's figure MEANS, in the words the screen already uses — so a panel header cannot drift
#: from the tile it opened from.
_STATUS_FIGURE = {
    "open": "Total receivable from the carrier",
    "pending": "Pending — too recent to reconcile",
    "lagged": "Lagged — awaiting prior-period data",
    "info": "Informational total",
}

_DISCREPANCY_NOTE = (
    "One row per commission line this month: what the plan says was earned, what the carrier's own "
    "feed paid against it, and the difference. Every row is read out of the stored discrepancy run — "
    "press Run Detection to rebuild it. A line nobody could price is shown WITHOUT a figure and is "
    "not counted, because a claim that could not be sized is not a claim worth $0.00.")

_EARNED_NOTE = (
    "One row per rep. The figure counts only the reps whose carrier earnings were actually reported: "
    "a rep the statement was never loaded for is shown without a figure, and a rep the statement WAS "
    "looked up for and pays nothing against is shown at a measured $0.00 and left out of the total.")

_PAID_NOTE = (
    "One row per rep, as the commission calculation stored it. This is a payroll EXPENSE and is "
    "never added to, or netted against, the carrier revenue beside it.")

_DIFFERENCE_NOTE = (
    "Earned minus paid, per rep, and only where BOTH sides were measured — the figure is the sum of "
    "those per-rep differences, not one total subtracted from another. A rep missing either side is "
    "shown here and deliberately not counted.")

_BREAKOUT_NOTE = (
    "One row per money stream per month. Every figure on this report is a finer split of money the "
    "company already received, so these rows re-partition it rather than adding any. A month in "
    "which a feed delivered nothing is shown and not counted — it is an absent file, not a zero.")


# ── WHERE A DISCREPANCY ROW CAME FROM ─────────────────────────────────────────────────────────────

def writer_feeds(row):
    """The feeds the engine that WROTE this row reads for every row it writes, off the row's own
    stored `source` value (`data_lineage_registry.discrepancy_writer_feeds` is the one home). An
    unrecognised writer resolves to nothing and the row names only the snapshot."""
    return tuple(_lineage.discrepancy_writer_feeds((row or {}).get("source")))


def evidence_tables(row):
    """The SOLD-side table this row's own stored evidence names, or `()`.

    `ma_recon._evidence_for` records `evidence.b2b.source_table` — which of the sales feeds the
    activation was found in — and that is a stored fact, so it is read rather than guessed. A value the
    registry's display union does not know is IGNORED: a stored string is not a licence to put an
    arbitrary identifier on a manager's screen. The legacy engine records no evidence at all and this
    returns nothing for its rows, which §CORE-17.3k reports as the gap it is."""
    ev = (row or {}).get("evidence")
    if not isinstance(ev, dict):
        return ()
    b2b = ev.get("b2b")
    table = str((b2b or {}).get("source_table") or "").strip() if isinstance(b2b, dict) else ""
    return (table,) if table in SOLD_FEEDS else ()


def discrepancy_tables(row):
    """Every table ONE discrepancy row was built from: the snapshot it was read out of, the feeds its
    writer reads unconditionally, and the sold-side feed its own evidence names. Ordered so the
    snapshot comes first, because that is where the figure literally was."""
    out = [DISCREPANCY_SNAPSHOT]
    for t in writer_feeds(row) + evidence_tables(row):
        if t and t not in out:
            out.append(t)
    return tuple(out)


def _discrepancy_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("comp_type") or "(no type)",
        ("month %s" % r.get("bounty_month")) if r.get("bounty_month") is not None else "",
        r.get("imei") or r.get("mdn") or "(no device)",
    ) if str(x or "").strip())


def _discrepancy_fields(r):
    r = r or {}
    return {
        "store": r.get("store"),
        "rep": r.get("rep_username"),
        "expected": r.get("expected_amount"),
        "received": r.get("received_amount"),
        "detail": " · ".join(str(x) for x in (
            r.get("device_model"), r.get("customer_plan"), r.get("activation_date"),
            r.get("rule_reason") or r.get("notes"),
        ) if str(x or "").strip()) or None,
    }


def _gap_recorded(r):
    """A gap exists only where the expected side was measured. `claim_gap` already refuses to invent
    one, so this reads its answer rather than re-deciding it."""
    return (r or {}).get("gap") is not None


def _gap_reason(r):
    return None if _gap_recorded(r) else "amount_not_recorded"


def status_total(rows, status):
    """Σ(the recorded gaps) over the rows in one tab — the figure the screen's headline shows.

    It is the SAME number the page's own reduce produces, to the cent: a row whose gap was never
    recorded contributes nothing to a sum whether it is skipped (here) or coerced to zero (there). The
    difference is only that the panel can then say which rows those were instead of folding them in as
    $0.00."""
    want = str(status or "").strip()
    return round(sum(report_drilldown.money(r.get("gap")) for r in (rows or [])
                     if r and str(r.get("status") or "").strip() == want
                     and r.get("gap") is not None), 2)


def _of_status(rows, status):
    want = str(status or "").strip()
    return [r for r in (rows or []) if r and str(r.get("status") or "").strip() == want]


def discrepancy_catalogue():
    """One figure per report tab. The rows are the stored run's own rows for that tab and the total is
    `status_total` over exactly them, so the tie-out compares the screen's headline against the lines
    it is made of."""
    common = {"value": "gap", "row_label": _discrepancy_label, "keys": ("id",),
              "fields": _discrepancy_fields, "tables": discrepancy_tables, "schema": "commcalc",
              "in_figure": _gap_recorded, "reason": _gap_reason, "note": _DISCREPANCY_NOTE}
    return tuple(
        dict(common, key="gap_%s" % s, label=_STATUS_FIGURE[s], status=s)
        for s in DISCREPANCY_STATUSES
    )


def discrepancy_drilldowns(rows, *, cap=None):
    """PURE: one panel per report tab, over the stored run's rows the endpoint already serves.

    A tab with no rows at all gets NO panel rather than one asserting $0.00 behind nothing, which is
    `tiles()`'s own rule applied to a row list per figure."""
    out = {}
    for spec in discrepancy_catalogue():
        spec = dict(spec)
        key, label, status = spec.pop("key"), spec.pop("label"), spec.pop("status")
        mine = _of_status(rows, status)
        if not mine:
            continue
        out[key] = report_drilldown.tile_panel(
            label, status_total(rows, status), mine,
            cap=cap or report_drilldown.ROW_CAP, **spec)
    return out


def discrepancy_store_drilldowns(rows, *, cap=None):
    """PURE: the By-Store table's own figures — one panel per (tab, store), because that gap is a
    reported figure a district manager acts on and it had no rows behind it either.

    The rows are a PARTITION of the tab's panel, so a line appears in exactly one of them and the two
    families can never present the same line twice."""
    out = {}
    for status in DISCREPANCY_STATUSES:
        mine = _of_status(rows, status)
        if not mine:
            continue
        by_store = {}
        for r in mine:
            by_store.setdefault(str(r.get("store") or "").strip() or "Unknown", []).append(r)
        panels = {}
        for store, srows in by_store.items():
            total = round(sum(report_drilldown.money(r.get("gap")) for r in srows
                              if r.get("gap") is not None), 2)
            panels[store] = report_drilldown.tile_panel(
                _STATUS_FIGURE[status], total, srows,
                value="gap", row_label=_discrepancy_label, keys=("id",),
                fields=_discrepancy_fields, tables=discrepancy_tables, schema="commcalc",
                in_figure=_gap_recorded, reason=_gap_reason, note=_DISCREPANCY_NOTE,
                subject=store, subject_kind="store", cap=cap or report_drilldown.ROW_CAP)
        out[status] = panels
    return out


# ── CARRIER EARNED vs EMPLOYEE PAID ───────────────────────────────────────────────────────────────

def earned_tables(r):
    """WHICH FEED THIS REP'S EARNED FIGURE WAS READ OUT OF, decided by the row's OWN reason — the
    producer's stored verdict, not a second judgement about the same question.

    · pre-attributed  the processor payment feed was already aggregated per rep BY THE CALCULATION, so
                      the figure was read out of `rep_commissions`. §CORE-17.3j: the snapshot is what
                      is named, not the feed the calculation read to write it.
    · looked up       the statement pair was loaded and these activations were looked up in it —
                      whether or not it paid anything against them. Both tables are named because
                      `load_ma_paid_rows` reads both for every period.
    · never looked up no statement for the period, no device key to look one up by, or no sold
                      activation at all. Nothing was read, so nothing is named and the row reports an
                      origin that cannot be traced.
    """
    r = r or {}
    reason = str(r.get("carrier_earned_reason") or "").strip()
    if reason == _cvp.EARNED_REASON_PREATTRIBUTED:
        return (REP_COMMISSIONS,)
    if reason == _cvp.EARNED_REASON_PAID:
        return STATEMENT_FEEDS
    if reason == _cvp.EARNED_REASON_ABSENT and int(r.get("activations_sold") or 0) > 0:
        return STATEMENT_FEEDS
    return ()


def _earned_reason(r):
    """The reason a row carries NO earned figure — the producer's own key, so the sentence the reader
    sees comes from the one `UNMEASURED_REASONS` home rather than a fourth spelling of it. A rep whose
    figure IS reported or measured carries no reason at all."""
    r = r or {}
    if r.get("carrier_earned") is not None:
        return None
    return str(r.get("carrier_earned_reason") or "").strip() or None


def _earned_in_figure(r):
    return str((r or {}).get("carrier_earned_state") or "") == _cvp.EARNED_STATE_REPORTED


def _paid_in_figure(r):
    return (r or {}).get("employee_paid") is not None


def _paid_reason(r):
    return None if _paid_in_figure(r) else "not_computed"


def _difference_in_figure(r):
    return str((r or {}).get("difference_state") or "") == _cvp.DIFFERENCE_STATE_COMPUTED


def _difference_reason(r):
    return None if _difference_in_figure(r) else _earned_reason(r) or "not_computed"


def difference_tables(r):
    """A per-rep difference was read out of BOTH sides, so it names both — or, where one side was
    never read, only the side that was."""
    out = list(earned_tables(r))
    if REP_COMMISSIONS not in out:
        out.append(REP_COMMISSIONS)
    return tuple(out)


def _rep_label(r):
    r = r or {}
    return str(r.get("rep") or r.get("rep_key") or "(unnamed rep)")


def _rep_fields(r):
    r = r or {}
    return {"store": r.get("store"), "market": r.get("market"),
            "detail": " · ".join(str(x) for x in (
                r.get("carrier_earned_state"),
                "%s sold" % r.get("activations_sold"),
                "%s in the carrier feed" % r.get("activations_in_statement"),
            ) if str(x or "").strip())}


def _counted(r):
    return True


def carrier_vs_pay_catalogue():
    """Every figure on the tile strip, declared. `total` names the key in the report's OWN `totals`
    block, so a figure the report did not produce has no panel instead of a panel asserting one."""
    rep_common = {"row_label": _rep_label, "keys": ("rep_key",), "fields": _rep_fields,
                  "schema": "commcalc"}
    count_common = dict(rep_common, unit=report_drilldown.UNIT_COUNT, in_figure=_counted,
                        tables=lambda _r: SOLD_FEEDS)
    return (
        {"key": "carrier_earned_reported", "label": "Carrier earned (dealer revenue)",
         "total": "carrier_earned_reported", "value": "carrier_earned",
         "tables": earned_tables, "in_figure": _earned_in_figure, "reason": _earned_reason,
         "note": _EARNED_NOTE, **rep_common},
        {"key": "employee_paid_total", "label": "Employee paid (payroll expense)",
         "total": "employee_paid_total", "value": "employee_paid",
         "tables": lambda _r: (REP_COMMISSIONS,), "in_figure": _paid_in_figure,
         "reason": _paid_reason, "note": _PAID_NOTE, **rep_common},
        {"key": "difference_over_measured_reps", "label": "Earned minus paid",
         "total": "difference_over_measured_reps", "value": "difference",
         "tables": difference_tables, "in_figure": _difference_in_figure,
         "reason": _difference_reason, "note": _DIFFERENCE_NOTE, **rep_common},
        {"key": "activations_sold", "label": "Activations sold",
         "total": "activations_sold", "value": "activations_sold",
         "note": "One row per rep, counting the activations they rang out this month.",
         **count_common},
        {"key": "activations_in_statement", "label": "Activations found in the carrier feed",
         "total": "activations_in_statement", "value": "activations_in_statement",
         "tables": lambda _r: STATEMENT_FEEDS,
         "note": ("One row per rep, counting the activations whose device was found in the loaded "
                  "carrier statement."),
         **{k: v for k, v in count_common.items() if k != "tables"}},
        {"key": "open_no_rule", "label": "Unpaid with no business rule",
         "total": "open_no_rule", "value": "open_no_rule",
         # Both sides: the verdict "unpaid, and nothing explains it" is read from the sold feed AND
         # from the statement that does not carry the activation.
         "tables": lambda _r: SOLD_FEEDS + STATEMENT_FEEDS,
         "note": ("One row per rep, counting the activations the carrier has not paid and no "
                  "business rule explains."),
         **{k: v for k, v in count_common.items() if k != "tables"}},
    )


def carrier_vs_pay_drilldowns(out, *, cap=None):
    """PURE: one panel per tile, over `rollup_by_rep`'s OWN rows and totals.

    THE ROWS MUST BE THE UNFILTERED ONES. The totals this ties against are folded over every rep
    (defect 3 in this module's header), so a panel folded over a market selection would disagree with
    the tile by construction and look like proof of the wrong number. The caller therefore hands the
    rows in BEFORE narrowing, and the page says in words that the tiles cover the whole month.
    """
    rows = (out or {}).get("rows") or []
    totals = (out or {}).get("totals") or {}
    return report_drilldown.tiles(carrier_vs_pay_catalogue(), rows, totals,
                                  cap=cap or report_drilldown.ROW_CAP)


# ── COMMISSION RECEIVED OVER M1–M12 ───────────────────────────────────────────────────────────────

def stream_tables(r):
    """The feed one breakout cell was read out of, off the cell's own stream key."""
    t = FEEDS_BY_STREAM.get(str((r or {}).get("stream") or "").strip())
    return (t[0],) if t and t[0] else ()


def stream_column(key):
    """The column that held a stream's money, where the stream is one column of a shared feed."""
    t = FEEDS_BY_STREAM.get(str(key or "").strip())
    return t[1] if t else None


def cells(out, *, groups=None, in_total=None):
    """Every (stream × month) CELL the breakout produced, flattened — the grain every figure on that
    report is folded over.

    `build_breakout` already computed each cell; this names them, it does not re-add them. A cell
    carries its stream's identity so a row can say which feed and which month it is, and the stream's
    `lines` count so a month a feed never delivered can be told apart from a month it delivered
    nothing for."""
    out = out or {}
    want_groups = set(groups) if groups else None
    rows = []
    for s in out.get("streams") or []:
        if want_groups is not None and s.get("group") not in want_groups:
            continue
        if in_total is not None and bool(s.get("in_total")) is not bool(in_total):
            continue
        for period, cell in (s.get("periods") or {}).items():
            cell = cell or {}
            rows.append({
                "stream": s.get("key"), "stream_label": s.get("label"), "group": s.get("group"),
                "period": period, "lines": cell.get("lines") or 0,
                "total": cell.get("total"), "m1": cell.get("m1"),
                "m2_12": cell.get("m2_12"), "unsplit": cell.get("unsplit"),
                "legs": cell.get("legs") or {},
            })
    return rows


def _cell_label(r):
    r = r or {}
    return "%s · %s" % (r.get("stream_label") or r.get("stream") or "(unnamed)",
                        r.get("period") or "(no month)")


def _cell_fields(r):
    r = r or {}
    return {"detail": "%s line(s)" % (r.get("lines") or 0),
            "m1": r.get("m1"), "m2_12": r.get("m2_12"), "unsplit": r.get("unsplit")}


def _cell_recorded(r):
    """Did this feed deliver anything for this month AT ALL? A month with no file is not a month that
    paid $0.00, so such a cell is shown and not counted. Its contribution is zero either way, which
    is what makes saying so free of any change to the figure."""
    r = r or {}
    return bool(r.get("lines")) or bool(r.get("legs")) or report_drilldown.money(r.get("total")) != 0


def _cell_reason(r):
    return None if _cell_recorded(r) else "not_recorded"


#: The four figures each group tile shows, and the cell field each is Σ of.
GROUP_FIGURES = (
    ("total", "total", "%s — total"),
    ("m1", "m1", "%s — 1st month"),
    ("m2_12", "m2_12", "%s — M2–M12"),
    ("unsplit", "unsplit", "%s — unsplit"),
)


def breakout_drilldowns(out, *, cap=None):
    """PURE: a panel per group-tile figure and a panel per stream total, over the breakout's own cells.

    The group tiles are Σ(cells of that group's counted streams), so their tie-out is the identity
    `build_breakout` already asserts into `identity` — checked here against the cells a reader can
    see rather than only in the payload. A stream's own total opens to its months, including the
    cross-check streams that sit in no total: a figure on the screen is a figure that opens.
    """
    out = out or {}
    cap = cap or report_drilldown.ROW_CAP
    panels = {}
    common = {"row_label": _cell_label, "keys": ("stream", "period"), "fields": _cell_fields,
              "tables": stream_tables, "schema": "commcalc", "in_figure": _cell_recorded,
              "reason": _cell_reason, "note": _BREAKOUT_NOTE}
    for g in out.get("groups") or []:
        key, label = g.get("group"), g.get("label") or g.get("group")
        rows = cells(out, groups=(key,), in_total=True)
        if not rows:
            continue
        for suffix, field, title in GROUP_FIGURES:
            amount = g.get(field)
            if amount is None:
                continue
            panels["group_%s_%s" % (key, suffix)] = report_drilldown.tile_panel(
                title % label, amount, rows, value=field, subject=label,
                subject_kind="money group", cap=cap, **common)
    for s in out.get("streams") or []:
        key = s.get("key")
        if s.get("total") is None:
            continue
        rows = [r for r in cells(out) if r.get("stream") == key]
        if not rows:
            continue
        panels["stream_%s_total" % key] = report_drilldown.tile_panel(
            "%s — total" % (s.get("label") or key), s.get("total"), rows, value="total",
            subject=s.get("label"), subject_kind="money stream",
            column=stream_column(key), cap=cap, **common)
    return panels


def _stream_keys():
    """The producer's own stream keys, dereferenced — so the lock that checks `FEEDS_BY_STREAM` is
    complete reads the registry rather than a list written down twice."""
    return tuple(_cr.STREAMS)
