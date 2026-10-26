"""A REPORTED FIGURE NAMES THE ROWS BEHIND IT, AND EVERY ROW NAMES WHERE IT CAME FROM.

OWNER ASK 2026-10-24, verbatim (OWNER-QUOTE-BEGIN):
  "cash variance at employee should have the drill down from wheter the data is cming from , update
   in index thta drilldown is needed for every report geenerated from any data which comming from
   within the platforma as a defalut , assign an agent o check all reports to check where the drill
   donw ins not there and sytart building it"
(OWNER-QUOTE-END)

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix or it is not a fix")
──────────────────────────────────────────────────────────────────────────────────
The instance is "the Cash Variance report shows a per-employee shortage with no way to see which
store-days and which recorded figures produced it". The class is:

  **A REPORT BUILT FROM PLATFORM DATA ASSERTS A NUMBER AND KEEPS ITS EVIDENCE TO ITSELF.** The
  operator is asked to act on a figure — chase a rep, dispute a carrier, sign a deposit — with no way
  to reach the rows it was summed from, and no way to learn which table, feed or upload those rows
  came from. Every such figure is an assertion the platform cannot be made to defend.

It is the same shape as §66 (`component_is_evidence`) and §COMMCALC-09 (`payout_slice_evidence`) one
layer up: those two ask *is this value evidence of anything*; this one asks *and what is the evidence*.

ONE FACT, ONE HOME, DEREFERENCED. Before this module there were as many answers as reports: the P&L
accumulates `detail` labels inside `account/coa.py` (§4e) and ties them out in TypeScript
(`scopeFinancials.lineTieOut`); the commission explain re-computes from the plan engine
(`commcalc/commission_drilldown.py`); and a `"source"` key appears on 30-odd payloads across the
backend meaning, variously, a table name, a screen name, an upload route and a free sentence. A new
report therefore invented its own shape — or, far more often, served no evidence at all.

WHAT THIS MODULE IS. The shape of a drill-down, once, as four pure functions:

  · `origin(table, …)`  — ONE source row reference: which table, in which schema, which keys address
    the row, which column held the figure. The user never sees any of those words: the screen renders
    the table through `frontend/src/lib/sourceLabels.ts::sourceLabel`, the one home for a feed's
    plain name (§19.38, "hide database names from the users").
  · `drill_row(…)`      — one row behind a figure: its label, its amount, its origins, and whether
    the figure counts it.
  · `tie_out(…)`        — Σ(the rows this figure counts) against the figure, which STATES a
    disagreement instead of hiding it.
  · `panel(…)`          — the three together, plus `sources`: the distinct places the rows came
    from, counted. That list is the owner's question — *where is the data coming from* — answered in
    one read.

ABSENCE IS NEVER ZERO, AND IT IS NEVER A TIE. A drill row whose figure was never recorded carries
`measured: False` and a reason, and `tie_out` refuses to assert `ties` at all when any counted row is
unmeasured — `ties: None`, with a sentence. A panel that summed a blank as $0.00 and then announced
that it tied would be the absence-is-zero defect wearing a proof.

THE VOCABULARY IS NOT NEW. `measured` / `reason` / `reason_label` are `closing/cash_variance.py`'s
own words, and `unit` is `closing/metric_catalog.figure_unit`'s. A fourth spelling of "we could not
measure this" is exactly the divergence the index rules forbid.

ROWS SHOWN BUT NOT COUNTED. A figure rarely counts every row behind it — the Cash Variance shortage
total deliberately excludes days inside the tolerance band. Those days still belong in the drill-down
(they are the context that makes the figure readable) so each row declares `in_figure`, the tie-out
sums only the counted ones, and the panel says in words how many are shown without being counted. A
drill-down that silently dropped them would read as a shorter month than the one the report measured.

LOCKED SO IT CANNOT UN-WIRE (CLAUDE.md). `backend/harness_report_drilldown.py` fails the build when
a drill origin names a table no migration creates, when that table has no `sourceLabel` entry (the
operator would be shown a database name), when a producer builds a panel dict by hand instead of
through these functions, and when a tie-out claims a tie over an unmeasured row.

💰 MOVES NO MONEY. Read-only presentation of figures other modules computed. Books nothing, pays
nothing, writes nothing, and deliberately computes no business figure of its own: every amount it
carries was handed to it.

PURE: no I/O, no framework import, no clock, no org/carrier/tenant/product name (RULE TWO).
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.
"""
from __future__ import annotations

#: Bump when the SHAPE of a panel changes, so a stale renderer fails loudly rather than drawing a
#: half-panel. Served on every panel.
SCHEMA = 1

#: How many rows a panel serves before it says it was capped. A drill-down is read by a human, and a
#: payload nobody can scroll is a denial of service dressed as transparency. A caller needing every
#: row exports the report — `rows_total_count` always states the true count, so the screen can say
#: "showing 400 of 1,812" instead of quietly answering a different question.
ROW_CAP = 400

#: Which kind of number the figure is, so a screen formats it without guessing. Deliberately the same
#: words as `closing/metric_catalog.figure_unit` rather than a second spelling of the same question.
UNIT_MONEY = "money"
UNIT_COUNT = "count"
UNIT_HOURS = "hours"
UNITS = (UNIT_MONEY, UNIT_COUNT, UNIT_HOURS)

#: Why a drill row carries no number. Each is a sentence a screen may show as-is, and none of them is
#: a figure — "we could not measure this" must never render as money. The first three are
#: `closing/cash_variance.UNMEASURED_REASONS` verbatim (one vocabulary, not two); the last two are
#: the shapes a drill-down meets that a pairwise comparison does not.
UNMEASURED_REASONS = {
    "leg_absent": "one of the two figures was never recorded",
    "basis_unknown": "one of the two figures does not say which cash it counted",
    "basis_mismatch": "the two figures count different cash, so the difference is a definition, "
                      "not a variance",
    "not_recorded": "nothing was ever recorded here, which is not the same as nothing happened",
    "not_computed": "no run has computed this yet, so the blank is a missing calculation",
    # The shapes the batch-3 and batch-5 reports brought with them. A reason with no entry here still
    # travels on the row, but the screen prints the bare key instead of a sentence — so every reason a
    # declaration can emit is spelled out, in the words the reader needs rather than the field name.
    "no_processor_data": "the processor's feed reported nothing for this store-day, which is not the "
                         "same as it settling nothing",
    "no_declared_data": "no closing row was filed for this store-day, so there is nothing to compare "
                        "the other side against",
    "dm_merged": "the district manager corrected the combined total without stating the split, so "
                 "attributing part of it to this one figure would be a guess",
    "not_collected": "this envelope has not been collected yet, so nothing has been counted",
    "no_sales_feed_match": "the sales feed was not loaded for this day, or this person could not be "
                           "matched to it, so no comparison was made at all",
    "not_counted": "management has not counted this envelope yet, so there is no variance to "
                   "measure — the figure is light by an unknown amount, not by nothing",
    "stage_unknown": "this record's stage is not in the stage list, so the per-stage report drops it "
                     "while the total still counts it",
    "not_resolved": "this one is not finished yet, so it contributes no duration — an average over "
                    "the finished ones is not an average over all of them",
    "not_filed": "nobody filed a closing for this store-day, so there is no declared figure at all — "
                 "the drawer was never counted, which is not the same as it being empty",
    "submit_refused": "this submit was turned away before anything was stored, so it is not a "
                      "recount — it is a person who came in and could not file",
    # The shapes the batch-10 finance reports brought with them (§CORE-17.3j).
    "detail_not_recorded": "the stored statement kept this line's dollars without keeping what they "
                           "were summed from, so the breakdown was never written down — the figure "
                           "is real, its row-level evidence was not recorded",
    "amount_not_recorded": "no amount is recorded for this one, so it is shown without a figure and "
                           "not counted — the report counts these separately, because something "
                           "nobody recorded is not something worth $0.00",
    "feed_unresolved": "this figure's own basis is not one we know a feed for, so where it was read "
                       "out of cannot be named — an unnamed source is reported rather than guessed",
    # The shapes the batch-11 commission reports brought with them (§CORE-17.3k). These three KEYS
    # are `commcalc/carrier_vs_pay.EARNED_REASON_NO_FEED / _NO_DEVICE_KEY / _ABSENT` themselves — the
    # producer's own vocabulary, not a fourth spelling of it, and `harness_batch11_drilldown.py` §Y1
    # fails the build if the two drift apart. The sentences here are the reader-facing half; the
    # payload's own longer explanation stays in that module's `EARNED_REASONS`.
    "carrier_statement_not_loaded": "no carrier statement is loaded for this month, so nothing about "
                                    "this side is known — a missing upload is reported, never "
                                    "absorbed into the figure as $0.00",
    "no_device_key_on_sale_row": "these activations carry no device serial, so there is no key to "
                                 "look the carrier statement up by — nothing is known about them, "
                                 "which is not the same as their having earned nothing",
    "absent_from_the_loaded_statement": "the carrier side was never looked up for this one, so it "
                                        "carries no figure at all rather than a zero somebody could "
                                        "mistake for a measurement",
    # The shapes the batch-13 TARGETS reports brought with them (§CORE-17.3m). A target is a
    # DECLARED number rather than a measurement, so "nobody set one" is its own absence — and a day
    # that has not happened yet is a third thing again, neither a zero nor a missing record.
    "target_not_set": "nobody has set this target, so there is nothing to measure against — an "
                      "unset target is not a target of zero, and a store without one is not a "
                      "store failing it",
    "day_not_yet_reached": "this day has not happened yet, so there is nothing it could have "
                           "achieved — it is shown because the month is not over, not because "
                           "nothing was sold",
    "no_dm_in_org_tree": "the org chart does not say which district manager owns this store, so "
                         "this figure cannot be attributed to any of them — it is unattributable "
                         "rather than zero, and assigning the store in the org chart is what puts "
                         "it on somebody's card",
}


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _s(v):
    s = str(v).strip() if v is not None else ""
    return s or None


def money(v):
    """PURE: a figure rounded to cents THE ONE WAY this mechanism rounds.

    Public because a declaration sometimes has to ask whether a figure handed to it is zero, or add
    two figures the report already served (the Liabilities + Equity total is the two stored
    subtotals). Spelling that arithmetic in the declaration would be a second copy of this rounding
    — and a declaration that writes `0.0` to say "zero" has acquired a dollar literal of its own,
    which is exactly what the build locks on these declarations refuse."""
    return _f(v)


# ── ONE source row reference. ──────────────────────────────────────────────────────────────────
def origin(table, *, schema=None, keys=None, column=None, note=None):
    """PURE: where ONE figure on a drill row came from — the table, its schema, the keys that address
    the row, and the column the figure was read out of.

    `table` is the stored relation's real name and is NEVER rendered as itself: the screen passes it
    through `sourceLabel()` so the operator reads "daily closing sheet", not `daily_closing`. The
    harness fails the build if a table named here has no entry there, because the alternative is a
    database name on a manager's screen.

    `keys` is how a human (or a support query) finds the row again — `{"closing_row_id": "…"}` or the
    natural key `{"store_code": "B-1115", "close_date": "2026-09-14", "employee_name": "…"}`. Blank
    values are dropped rather than served as empty strings, so a key that is present means something.

    A table nobody named resolves to None, which `panel` reports as an origin that cannot be traced
    rather than inventing one. Guessing a table here would be a provenance claim nothing checked.
    """
    return {
        "table": _s(table),
        "schema": _s(schema),
        "keys": {k: v for k, v in (keys or {}).items() if v not in (None, "")} or None,
        "column": _s(column),
        "note": _s(note),
        "traceable": bool(_s(table)),
    }


# ── One row behind a figure. ───────────────────────────────────────────────────────────────────
def drill_row(label, origins, *, amount=None, in_figure=True, measured=None, reason=None,
              fields=None, subject=None, row_key=None):
    """PURE: one of the rows a figure was built from.

    `amount=None` means this row has no number — nobody recorded one — and `measured` defaults to
    whether a number is present, so a caller cannot accidentally serve a blank as a measured $0.00.
    A caller that HAS a number but knows it cannot be trusted passes `measured=False` with a reason.

    `in_figure=False` keeps the row in the drill-down without letting it into the tie-out. That is
    for rows the figure deliberately excludes — a day inside a tolerance band, a line a rule held.
    They are shown because they are the context that makes the figure readable.

    `fields` is whatever else the screen should show on this row (a basis, a verdict, a store), as a
    plain dict. Nothing in it is interpreted here: this module presents evidence, it does not judge it.

    `row_key` is the natural key that addresses the REPORT ROW this drill row explains — the same dict
    the origins are keyed by. It exists so a screen that filters its own rows can line the two up and
    narrow the panel to the same selection its tile is folded over (§CORE-17.3d). Without it the only
    honest options on such a report were to refuse the drill or to show a panel about a population the
    screen is not showing.
    """
    ors = [o for o in (origins or []) if o]
    has = amount is not None
    return {
        "label": _s(label) or "(unnamed)",
        "subject": _s(subject),
        "amount": None if not has else _f(amount),
        "present": has,
        "in_figure": bool(in_figure),
        "measured": bool(has if measured is None else measured),
        "reason": _s(reason),
        "reason_label": UNMEASURED_REASONS.get(_s(reason) or "", None),
        "origins": ors,
        "sources": sorted({o["table"] for o in ors if o.get("table")}),
        "fields": dict(fields or {}) or None,
        "row_key": ({k: v for k, v in (row_key or {}).items() if v not in (None, "")} or None),
    }


# ── Σ(the rows the figure counts), against the figure. ─────────────────────────────────────────
def tie_out(amount, rows):
    """PURE: does the drill-down add up to the figure it explains?

    IT STATES A DISAGREEMENT RATHER THAN HIDING ONE. `ties` is True only when every counted row is
    measured AND the rows total the figure to the cent. It is **None**, never False, when any counted
    row is unmeasured: a sum that is missing a row cannot disagree with the figure, because it was
    never a comparison. False means the rows were all measured and the total is genuinely different —
    the case that says the report and its own evidence disagree, which is a defect to fix, not to
    round away.

    `shown_not_counted` is how many rows the drill-down shows that the figure deliberately excludes,
    so a screen can say so instead of leaving the reader to subtract and wonder.
    """
    counted = [r for r in (rows or []) if r.get("in_figure")]
    unmeasured = [r for r in counted if not r.get("measured")]
    total = round(sum(_f(r.get("amount")) for r in counted if r.get("present")), 2)
    figure = _f(amount)
    diff = round(total - figure, 2)
    if unmeasured:
        ties, words = None, (
            f"{len(unmeasured)} of the {len(counted)} row(s) this figure counts has no recorded "
            f"number, so the rows cannot be added up to check the figure")
    elif diff == 0:
        ties, words = True, f"the {len(counted)} row(s) behind this figure add up to it exactly"
    else:
        ties, words = False, (
            f"the {len(counted)} row(s) behind this figure add up to {_money(total)}, which is "
            f"{_money(abs(diff))} {'more' if diff > 0 else 'less'} than the {_money(figure)} the "
            f"report shows — the report and its own evidence disagree")
    shown_not_counted = len([r for r in (rows or []) if not r.get("in_figure")])
    if shown_not_counted:
        words += (f"; {shown_not_counted} further row(s) are shown for context and are deliberately "
                  f"not counted in it")
    return {
        "figure": figure, "rows_total": total, "difference": diff, "ties": ties,
        "counted_rows": len(counted), "unmeasured_rows": len(unmeasured),
        "shown_not_counted": shown_not_counted, "words": words,
    }


# ── The panel a screen renders. ────────────────────────────────────────────────────────────────
def panel(figure_label, amount, rows, *, subject=None, subject_kind=None, unit=UNIT_MONEY,
          note=None, cap=ROW_CAP):
    """PURE: the drill-down for ONE figure — what the figure is, the rows behind it, whether they add
    up, and WHERE THEY CAME FROM.

    `sources` is the owner's question answered in one read: the distinct tables the rows were built
    from, each with how many of the rows touched it. A screen renders each through `sourceLabel()`,
    so the reader sees "daily closing sheet · 31 row(s)" and never a table name. An origin that names
    no table is counted under `untraceable_origins` rather than dropped — a provenance gap is
    REPORTED, never tidied away (CLAUDE.md).

    THE ROWS ARE CAPPED AND THE TRUE COUNT IS ALWAYS SERVED. `rows_total_count` is the real number of
    rows behind the figure whether or not they all fit, so a capped panel says "showing 400 of 1,812"
    instead of quietly presenting a shorter month as the whole of one. The tie-out is computed over
    ALL the rows, before the cap, because a tie-out over a truncated list would be a different claim.
    """
    allrows = [r for r in (rows or []) if r]
    counts, untraceable = {}, 0
    for r in allrows:
        for o in r.get("origins") or []:
            t = o.get("table")
            if t:
                counts[t] = counts.get(t, 0) + 1
            else:
                untraceable += 1
    limit = int(cap or ROW_CAP)
    shown = allrows[:limit] if limit > 0 else allrows
    return {
        "schema": SCHEMA,
        "figure": {"label": _s(figure_label) or "(unnamed figure)", "amount": _f(amount),
                   "unit": _s(unit) if _s(unit) in UNITS else UNIT_MONEY},
        "subject": _s(subject), "subject_kind": _s(subject_kind),
        "rows": shown,
        "rows_shown": len(shown), "rows_total_count": len(allrows),
        "capped": len(shown) < len(allrows),
        # Over ALL the rows, never over the shown slice — see the docstring.
        "tie_out": tie_out(amount, allrows),
        "sources": [{"table": t, "rows": n}
                    for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "untraceable_origins": untraceable,
        "note": _s(note),
    }


def _money(n):
    return f"${_f(n):,.2f}"


#: The tables a drill-down may name, as the shape of the answer rather than a list: a producer passes
#: the table it actually read. This tuple is only the SCHEMA vocabulary, so a caller cannot invent a
#: fourth place for stored data to live without the harness noticing.
SCHEMAS = ("commcalc", "storeops", "closing", "account", "asset", "pos", "core", "public")


def origins_of(table_keys, *, schema=None, column=None, note=None):
    """PURE: convenience for the common case — several tables addressed by the SAME keys, which is
    what a per-store-day or per-invoice drill row almost always has. `table_keys` is
    `[(table, {keys}), …]`; a table paired with no keys still produces an origin, because naming the
    table is itself most of the answer.
    """
    return [origin(t, schema=schema, keys=k, column=column, note=note) for t, k in (table_keys or [])]


# ── THE TILE SHAPE — Σ(one field) over rows the report already has. ────────────────────────────
#
# WHY THIS IS HERE AND NOT IN EACH REPORT. The audit behind §CORE-17 found 93 report routes owing a
# drill-down, and the overwhelming majority owe the SAME one: a tile shows a total, the report
# already holds the per-row figures that total was folded from, and there is no way from one to the
# other. Ninety-two per-report folds would be ninety-two answers to "which rows is this?" — the
# sibling-derivation defect the index rules forbid, arriving one report at a time instead of all at
# once. So the fold lives here, once, and a report declares only what is particular to it: which
# field holds the figure, which tables it came out of, and which rows the tile counts.
#
# A REPORT DECLARES, IT DOES NOT COMPUTE. Everything a caller passes is an accessor or a name. This
# module still sums nothing a report has not already summed, judges nothing, and holds no rate,
# threshold or dollar of its own — `tie_out` then checks the report's own total against the rows it
# named, which is the whole point: a declaration that does not add up says so.


def _reader(spec, default=None):
    """A field name, a tuple of field names, or a callable — resolved to a callable over one row.

    A report's rows are plain dicts off a payload, so naming the field is the honest common case and
    a lambda is the escape hatch. A tuple reads several fields, which is how a row's natural key and
    its human label are both usually spelled.
    """
    if spec is None:
        return (lambda _r: default)
    if callable(spec):
        return spec
    if isinstance(spec, (tuple, list)):
        names = tuple(spec)
        return (lambda r: tuple((r or {}).get(n) for n in names))
    return (lambda r: (r or {}).get(spec))


def tile_panel(figure_label, amount, rows, *, value, tables, row_label=None, keys=(),
               column=None, schema=None, unit=UNIT_MONEY, in_figure=None, measured=None,
               reason=None, fields=None, subject=None, subject_kind=None, note=None,
               cap=ROW_CAP):
    """PURE: the drill-down for a TILE — a figure that is Σ(one field) over the report's own rows.

    `value` reads the row's contribution; **None means the row has no number**, and such a row is
    shown with its reason rather than counted as nothing. `tables` names where that figure was read
    from — a tuple, or a callable when a row's provenance genuinely differs row to row (one feed
    present for this store-day, absent for that one). `keys` are the field names that address the
    row in those tables, so a reader can find it again.

    `figure_label` NAMES THE TILE and `row_label` names each row, and they are deliberately two
    words rather than one: in a catalogue both live in the same dict, and when they shared the name
    `label` one silently overwrote the other — every tile on the first report to use this served the
    row accessor as its own title. Caught by `harness_cash_recon_drilldown.py` §C11 before it
    shipped; the two names are what stop it recurring.

    `in_figure` is the predicate for **which rows the tile counts**, and defaults to "the row has a
    number". A tile that counts a subset — the short days, the mismatched days — passes its own
    predicate, and the rows it excludes stay in the panel as the context that makes the figure
    readable. That is the difference between a drill-down and a filtered list.

    THE TIE-OUT IS NOT A FORMALITY HERE. A report whose tile sums a field one way and declares it
    another will come back `ties: False`, in words, on the operator's screen. That is the intended
    outcome: it is the report disagreeing with its own evidence, and it is better seen than averaged
    away.
    """
    read_v = _reader(value)
    read_l = _reader(row_label if row_label is not None else "label", default=None)
    read_k = _reader(keys, default=None) if keys else (lambda _r: None)
    read_t = tables if callable(tables) else (lambda _r: tables)
    read_f = _reader(fields, default=None) if fields is not None else (lambda _r: None)
    read_m = measured if callable(measured) else _reader(measured, default=None)
    read_rs = reason if callable(reason) else _reader(reason, default=None)
    read_in = in_figure if callable(in_figure) else None
    key_names = tuple(keys) if isinstance(keys, (tuple, list)) else ((keys,) if keys else ())

    out = []
    for r in (rows or []):
        if not r:
            continue
        v = read_v(r)
        kv = read_k(r)
        if key_names and not isinstance(kv, dict):
            kv = dict(zip(key_names, kv if isinstance(kv, tuple) else (kv,)))
        elif not isinstance(kv, dict):
            kv = None
        lab = read_l(r)
        if isinstance(lab, tuple):
            lab = " · ".join(str(x) for x in lab if x not in (None, ""))
        fl = read_f(r)
        if isinstance(fl, tuple) and fields is not None and not callable(fields):
            fl = dict(zip(tuple(fields), fl))
        # A row whose tables did not RESOLVE still states a provenance, as one that cannot be
        # traced. The declaration shape for a config-resolved feed is `tuple(t for t in (x,) if x)`,
        # so an unregistered processor / an empty sales read collapses to `()` — and an empty origin
        # list reported nothing untraceable at all, which is a silent blank wearing the clothes of a
        # clean answer. Fixed HERE rather than in each catalogue, because every report that names a
        # config-resolved feed has the same shape (cash recon's processor and sales legs included).
        _tables = tuple(read_t(r) or ()) or (None,)
        out.append(drill_row(
            lab, origins_of([(t, kv) for t in _tables], schema=schema, column=column),
            row_key=kv,
            amount=v,
            in_figure=(read_in(r) if read_in else v is not None),
            measured=read_m(r), reason=read_rs(r),
            fields=(fl if isinstance(fl, dict) else None),
        ))
    return panel(figure_label, amount, out, subject=subject, subject_kind=subject_kind,
                 unit=unit, note=note, cap=cap)


def tiles(catalogue, rows, totals, *, schema=None, cap=ROW_CAP):
    """PURE: every tile on a report, from ONE declaration table.

    `catalogue` is a tuple of dicts — one per tile — each naming the tile's `key` (what the screen
    asks for), its `label` (what the reader sees), the `total` key its figure lives under, and
    whatever else `tile_panel` takes — including `row_label`, which names the ROWS, not the tile.
    A report therefore adds a tile by adding a ROW, not by writing another fold, and the harness can
    read the catalogue to check every tile on the screen has one.

    A tile whose total key is absent from `totals` is SKIPPED rather than served with a zero: a
    figure the report did not produce has no rows behind it, and inventing $0.00 would assert one.
    """
    out = {}
    for t in (catalogue or ()):
        spec = dict(t)
        key = _s(spec.pop("key", None))
        total_key = spec.pop("total", None)
        if not key or total_key not in (totals or {}):
            continue
        amount = (totals or {}).get(total_key)
        if amount is None:
            continue
        spec.setdefault("schema", schema)
        spec.setdefault("cap", cap)
        label = spec.pop("label", None)
        out[key] = tile_panel(label if label is not None else key, amount, rows, **spec)
    return out
