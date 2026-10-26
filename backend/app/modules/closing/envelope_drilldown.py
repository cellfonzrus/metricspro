"""THREE PEOPLE COUNT ONE ENVELOPE — the declaration for the Management Envelope Receipt (§CORE-17).

Five tiles sit above this report — envelopes, counted, short, over, chargebacks — and every one of
them is a statement about cash a named rep handed over. A short envelope can become a CHARGEBACK
against that rep's pay, so "this employee is $180 short across the month" is the figure a person
loses money to. Until now none of the five opened to the envelopes behind it.

ONE ROW IS ONE ENVELOPE, and an envelope is one rep's day: `closing_row_id`, the daily_closing row's
own id, which is also the id the count POST and the chargeback link both use. Keyed on that rather
than on (store, day, rep) because two of the three people who touch an envelope address it by the id
and only the reader addresses it by the name.

THREE TABLES, AND WHICH FIGURE CAME FROM WHICH. This is the whole reason the report needed a
drill-down: the numbers on one line were recorded by three different people in three different
places, and the screen showed them side by side as if they were one measurement.

  · `daily_closing`  — what the REP DECLARED. Present on every row, because a row IS a closing row.
  · `envelope_count` — what MANAGEMENT COUNTED, and therefore the variance, the status and the
                       comment. Absent when nobody has counted yet, which is not a $0.00 variance.
  · `ops_chargeback` — the PENALTY, when a short envelope was assigned to the rep. The amount and
                       the decision live there and nowhere else, reached through the count row's
                       `chargeback_id`.

So the money tiles name two tables and the chargeback tiles name three, rather than every tile
naming all three and leaving the reader to find a figure in a table that never held it.

AN UNCOUNTED ENVELOPE IS NOT A CLEAN ONE. It is shown in every panel — it is a real envelope, and
`envelopes` counts it — but it is deliberately NOT counted in short / over / match or in either
dollar figure, with `not_counted` as its stated reason. The alternative, folding it in at $0.00,
would read as "management counted this and it was exactly right", which is the opposite of the
truth and is exactly the reading the receipt exists to prevent. `by_employee` inherits this through
`envelope_report.totals`, which is why its `uncounted` column exists.

THE BASIS IS NOT RE-DECIDED HERE. Which cash a line is about (whole drawer / store cash /
bill-payment cash) is normalised once by `envelope_report.normalize_envelope_basis` at the endpoint,
and the stored count carries the basis it was SCORED on (mig 1036) — the two differ exactly when
management reads the receipt on one basis and the count on file was taken on another. This
declaration reports both as plain words it is given, and spells no basis key: the vocabulary has one
home in `envelope_report.basis_options`, and a second copy here would be the divergence the house
rules forbid.
"""
from app.modules.closing import envelope_report as _envelope_report
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.core import report_drilldown

#: One envelope's address — the closing row's own id (see the module docstring).
ROW_KEYS = ("closing_row_id",)

#: What the rep declared. Every row has one, because a row IS one of these.
DECLARED = ("daily_closing",)
#: What management counted. Consulted for every row of a counted figure, found only where somebody
#: has counted — the absence is the `not_counted` reason, not a zero.
COUNTED = ("envelope_count",)
#: Where a chargeback's amount and decision live. Reached through the count row's `chargeback_id`.
#: DEREFERENCED from the one feed registry (§CORE-17.3q) — this was the SECOND literal copy of that
#: relation name across the drill-down declarations, and a copy is a future divergence.
CHARGEBACK = (_lineage.OPS_CHARGEBACK,)

#: The statuses a counted envelope can land on. Read off `envelope_report.STATUSES` so a status
#: added there cannot leave a tile here counting the wrong rows.
STATUSES = tuple(_envelope_report.STATUSES)


def _label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("store_address") or r.get("store_code") or "(no store)",
        str(r.get("close_date") or "?")[:10],
        r.get("employee_name") or "(no name)",
    ))


def _fields(basis_label):
    """A row's own columns. `basis_label` turns a basis KEY into this tenant's own wording — passed
    in by the endpoint from `envelope_report.basis_options`, never spelled here."""
    def read(r):
        r = r or {}
        cb = r.get("counted_basis") or None
        return {"store_code": r.get("store_code"),
                "employee_name": r.get("employee_name"),
                "state": r.get("status") or "uncounted",
                "declared": r.get("declared_cash"),
                "counted": r.get("counted_amount"),
                # WHICH CASH the stored count was scored on, and whether the row recorded it at all
                # (a count taken before mig 1036 did not). Shown because a variance scored on another
                # basis is a different measurement, not a different rounding.
                "counted_on": (basis_label(cb.get("basis")) if cb and basis_label else
                               (cb.get("basis") if cb else None)),
                "counted_on_recorded": (bool(cb.get("recorded")) if cb else None),
                "counted_by": r.get("counted_by_name") or r.get("counted_by"),
                "chargeback": r.get("chargeback_status")}
    return read


def _counted(r):
    return bool((r or {}).get("counted"))


def _reason(r):
    """Why a row carries no variance. Only ever one answer here: nobody has counted the envelope, so
    there is nothing to compare the declaration against."""
    return None if _counted(r) else "not_counted"


def _one_if(pred):
    """A COUNT tile's contribution: one envelope, when the predicate holds, and no number at all when
    it does not — so the rows a count excludes stay in the panel as context with their reason."""
    def read(r):
        return 1 if pred(r or {}) else None
    return read


def _variance_when(want):
    """A DOLLAR tile's contribution. Short is served as a POSITIVE figure because the tile is headed
    "short" and `envelope_report.totals` folds it that way — the sign lives in one place, there."""
    def read(r):
        r = r or {}
        if r.get("status") != want or r.get("variance") is None:
            return None
        v = float(r.get("variance") or 0)
        return -v if want == "short" else v
    return read


def _chargeback(r):
    return bool((r or {}).get("chargeback_id"))


def _charged_tables(r):
    """What a chargeback figure's row was actually read out of: the closing sheet and the management
    count always, and the chargeback row only where one exists."""
    return DECLARED + COUNTED + (CHARGEBACK if _chargeback(r) else ())


#: Said once, on every tile whose figure is a variance: a count scored on another basis is a
#: different measurement. The screen has a column for it; a reader opening the panel gets the
#: sentence.
_BASIS_NOTE = ("A row's variance was scored on the basis its count was taken on, which is shown on "
               "the row. Where that differs from the basis you are reading the receipt on, the two "
               "figures are measuring different cash.")
#: Said once, on every tile that excludes an uncounted envelope.
_UNCOUNTED_NOTE = ("An envelope nobody has counted yet is shown here but not counted in this "
                   "figure. Folding it in at $0.00 would read as a count that came out exactly "
                   "right.")


def catalogue(*, basis_label=None):
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "commcalc",
              "fields": _fields(basis_label), "reason": _reason}
    # The three provenance shapes, named once each.
    declared_only = dict(common, tables=DECLARED)
    counted_side = dict(common, tables=DECLARED + COUNTED)
    # PER ROW, not a constant. A short envelope nobody assigned to anyone is shown in the chargeback
    # panels as the money nobody has charged — and it must not name the chargeback table, because
    # there is no chargeback row to read. A provenance claimed where nothing was read is a lie that
    # reads like rigour; caught by `harness_batch7_drilldown.py` §B4 before it shipped.
    charged = dict(common, tables=_charged_tables)
    out = [
        {"key": "envelopes", "label": "Envelopes filed", "total": "envelopes",
         "unit": report_drilldown.UNIT_COUNT, "value": _one_if(lambda _r: True),
         "note": ("One per rep-day closing filed in this range. Whether anybody has counted it is "
                  "the next tile, not this one."),
         **declared_only},
        {"key": "counted", "label": "Envelopes management has counted", "total": "counted",
         "unit": report_drilldown.UNIT_COUNT, "value": _one_if(_counted),
         "in_figure": _counted, **counted_side},
    ]
    for st in STATUSES:
        out.append({"key": st, "label": f"Envelopes counted {st}", "total": st,
                    "unit": report_drilldown.UNIT_COUNT,
                    "value": _one_if(lambda r, _st=st: r.get("status") == _st),
                    "in_figure": (lambda r, _st=st: (r or {}).get("status") == _st),
                    "note": _UNCOUNTED_NOTE, **counted_side})
    out += [
        {"key": "short_total", "label": "Cash missing from counted envelopes", "total": "short_total",
         "value": _variance_when("short"),
         "in_figure": (lambda r: (r or {}).get("status") == "short"),
         "note": _BASIS_NOTE + " " + _UNCOUNTED_NOTE, **counted_side},
        {"key": "over_total", "label": "Cash over in counted envelopes", "total": "over_total",
         "value": _variance_when("over"),
         "in_figure": (lambda r: (r or {}).get("status") == "over"),
         "note": _BASIS_NOTE + " " + _UNCOUNTED_NOTE, **counted_side},
        {"key": "chargebacks", "label": "Short envelopes assigned to a rep", "total": "chargebacks",
         "unit": report_drilldown.UNIT_COUNT, "value": _one_if(_chargeback),
         "in_figure": _chargeback,
         "note": ("A chargeback is created from a short envelope when the box on that line is "
                  "ticked. A short envelope with no chargeback is shown here, uncharged."),
         **charged},
        {"key": "chargeback_total", "label": "Money assigned to reps for short envelopes",
         "total": "chargeback_total",
         "value": (lambda r: (r or {}).get("chargeback_amount") if _chargeback(r or {}) else None),
         "in_figure": _chargeback,
         "note": ("The chargeback's own amount, from the chargeback row — not the variance. A waived "
                  "chargeback still shows its amount here, because this figure counts what was "
                  "assigned, and the decision is on the row."),
         **charged},
    ]
    return tuple(out)


def drilldowns(rows, totals, *, basis_label=None, cap=None):
    return report_drilldown.tiles(catalogue(basis_label=basis_label), rows, totals,
                                 cap=cap or report_drilldown.ROW_CAP)


# ── The BY EMPLOYEE rollup: one panel per line ────────────────────────────────────────────────────
# The owner's ask that started §CORE-17 was about an employee-level cash variance — "cash variance at
# employee should have the drill down from where the data is coming from" — and this report's second
# view is the other surface of that shape: one line per person, summed over their envelopes. A line
# that says somebody is $180 short has to open to the envelopes that say so.
#
# THE SHAPE IS NOT NEW. `cash_variance.drilldowns` / `group_drilldown` already serve one panel per
# rollup row, keyed by the subject the rollup is keyed on, for exactly this reason. This is that
# precedent applied to this report's rows, not a second mechanism: the figure is the line's OWN
# `net_variance`, read off the `envelope_report.totals` call `by_employee` already made.

def _emp_key(r):
    """The rollup's OWN key. `envelope_report.by_employee` groups on the trimmed employee name and
    calls an unnamed rep '(unnamed)', so this reads that rule rather than restating it — a drill-down
    grouped one way beside a table grouped another is the grain defect §CORE-17 exists to stop."""
    return ((r or {}).get("employee_name") or "").strip() or "(unnamed)"


def employee_panel(group, group_rows, *, basis_label=None, cap=None):
    """PURE: the drill-down for ONE line of the By employee view.

    The figure is the line's `net_variance` — over MINUS short — and the label SAYS SO, because
    `envelope_report.by_employee`'s own docstring warns about this figure: a rep $50 short one day and
    $50 over the next nets to zero while having twice handed over the wrong cash. A net of $0.00 must
    read as "they cancelled out", never as "clean", so the note carries the gross counts beside it.
    """
    g = group or {}
    read = _fields(basis_label)
    rows = []
    for r in (group_rows or []):
        st = (r or {}).get("status")
        v = (r or {}).get("variance")
        rows.append(report_drilldown.drill_row(
            _label(r),
            report_drilldown.origins_of(
                [(t, {"closing_row_id": (r or {}).get("closing_row_id")})
                 for t in (DECLARED + COUNTED + (CHARGEBACK if _chargeback(r) else ()))],
                schema="commcalc"),
            row_key={"closing_row_id": (r or {}).get("closing_row_id")},
            # The row's contribution to the NET: over counts up, short counts down, and a day inside
            # neither is shown without being counted.
            amount=(float(v) if st in ("short", "over") and v is not None else None),
            in_figure=(st in ("short", "over") and v is not None),
            reason=_reason(r), fields=read(r)))
    return report_drilldown.panel(
        "Cash variance — net of short and over", g.get("net_variance"), rows,
        subject=str(g.get("employee_name") or ""), subject_kind="employee_name",
        unit=report_drilldown.UNIT_MONEY, cap=cap or report_drilldown.ROW_CAP,
        note=(f"{g.get('short', 0)} short and {g.get('over', 0)} over of {g.get('counted', 0)} "
              f"counted envelope(s); {g.get('uncounted', 0)} envelope(s) nobody has counted, which is "
              f"not the same as a count that came out right."))


def by_employee_drilldowns(rows, groups, *, basis_label=None, cap=None):
    """PURE: one panel per By-employee line, keyed by the employee name the line is keyed on."""
    buckets = {}
    for r in (rows or []):
        buckets.setdefault(_emp_key(r), []).append(r)
    return {str(g.get("employee_name") or ""): employee_panel(
                g, buckets.get(str(g.get("employee_name") or "")) or [],
                basis_label=basis_label, cap=cap)
            for g in (groups or [])}
