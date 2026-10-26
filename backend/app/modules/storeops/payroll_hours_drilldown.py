"""WHERE DID THIS FIGURE COME FROM — the declaration for the two screens that fold ONE PAY PERIOD of
the payroll report: hours, and the pay those hours produce (§CORE-17, batch 24).

  /storeops/payroll   Payroll Report — Scheduled Hours / Actual Hours / Scheduled Pay / Actual Pay.
  /storeops/reports   StoreOps Reports — Hours & Payroll — the same four tiles, plus a By-Store view
                      that regroups the SAME rows in the browser.

ONE FAMILY, AND THE REASON IS THE GRAIN, NOT THE SUBJECT (§CORE-17.3l)
──────────────────────────────────────────────────────────────────────
Both screens read the SAME endpoint (`GET /storeops/payroll`), over the SAME pay period resolved by
the SAME resolver, and fold the SAME merged per-employee rows into the SAME four figures. There is
one row shape — ONE PERSON over ONE pay period, at the store they worked the most of it — and the
figures differ only by UNIT (hours, money) and by WHICH RELATION ANSWERED for the hours. Two
catalogues over that would be the sibling derivation the index rules forbid, so there is one.

WHAT WAS WORKED NAMES THE TIME CLOCK, NEVER THE SCHEDULE — AND THIS REPORT PAYS FOR BOTH
────────────────────────────────────────────────────────────────────────────────────────
Batch 20 settled, for every report after it, that output per hour worked names the closed punch and
nothing else: the schedule records an INTENTION. This report is the place that ruling actually
bites, because `Actual Hours` here is NOT "hours worked" on every row. `payroll_hours_basis.py` (the
ONE home, §14y) decides per (person, day) which evidence answered, and its own measurement is the
reason: `shifts.actual_hours` carries a figure on **7 of 7,740** rows, so before that module existed
payroll's `actual_hours or scheduled_hours` fell through to the schedule on effectively every row —
and nobody was told.

So this family does not paper that over and it does not re-derive it either. Every drill row names
the relation the PRODUCER'S OWN basis word says answered for it:

  · a punched day                 → the time clock
  · a day a manager corrected     → the schedule row the correction was typed onto (see defect 2)
  · a day paid from the schedule  → the schedule, and the row SAYS the figure is an intention
  · a day nothing measured        → NO relation at all, NO figure, and NOT COUNTED (§CORE-17.3i)

A reader therefore sees, in the panel's own `sources` list, how much of the figure above rests on a
measurement and how much on a plan — which is the only honest way to print one number over a period
whose punch coverage is partial (see THIN COVERAGE below).

ABSENCE IS NEVER ZERO (§CORE-17.3i). A person for whom nothing was measured and who is not ticked to
be paid from the schedule carries the producer's own `not_measured_unpaid`, is shown WITHOUT a
figure, names no relation, and is not counted. A `$0.00` or an `0.0 h` there would tell somebody they
worked and produced nothing, which in a payroll context is an accusation with nothing behind it. Such
a row contributes exactly 0 to the report's own total, so the tie-out still holds to the cent.

THIN PUNCH COVERAGE IS SURFACED, NEVER SMOOTHED (CLAUDE.md: a live-data defect is REPORTED)
──────────────────────────────────────────────────────────────────────────────────────────
House punch coverage is partial and was measured, read-only, on production: roughly 54% (July), 52%
(August), 66% (September) and 62% (October), and **June 2026 has no punches at all**. A figure folded
over such a month is not wrong — it is the figure the report prints and people are paid by — but a
panel must let the reader see how little of it rests on a measurement. Three things do that here, and
none of them is arithmetic of this module's own:

  1. each row carries the producer's `hours_basis` word and the producer's own sentence for it;
  2. each row carries the producer's `hours_by_basis` tally VERBATIM, so a mixed period shows its
     own split instead of one word standing in for a fortnight;
  3. the panel's `sources` counts the rows per relation, so "the schedule · 24 row(s)" beside "our
     own time clock · 11 row(s)" is readable without opening anything.

The Cellfonz October salary figure was ruled NOT MEASURABLE at 49% coverage, and the note on every
hours panel in this family says in words that a period can be in that state.

PAY IS RBAC'd, AND THIS DRILL IS GATED THE SAME WAY (agent rule 4)
──────────────────────────────────────────────────────────────────
Pay-per-hour, gross pay and salary are hidden below market manager, per-org configurable
(`pay_visibility`, mig 434). `GET /storeops/payroll` STRIPS those keys server-side for a gated
caller, so this family's money panels are **not served at all** to one — never served as $0.00, never
served over stripped rows. `MONEY_KEYS` names the two figures that are withheld, the endpoint asks
the same `pay_visibility` the report asks, and the harness fails the build if a gated caller is
served either panel. The hours panels stay, because a gated caller still corrects hours.

WHAT GETS NO PANEL HERE, AND WHICH RULING SAYS SO
─────────────────────────────────────────────────
  · **`Employees`** on the StoreOps Reports strip — a cardinality, not Σ(a field). The panel's own
    header already states how many rows are behind it, and the house settled this twice before
    ("Reps Calculated", the Daily Closing `days` tile).
  · **`Net (Actual − Sched)`** per row and the `▲/▼` cue — a DIFFERENCE of two figures that each
    drill (§CORE-17.3k, as batch 10 extended §CORE-17.3h). Both halves are offered instead.
  · **`Pay Rate`** — a leaf: one column of one employee record, and a rate besides (§CORE-17.3h).
    It is named as the PROVENANCE of the money figures instead, which is where it belongs.
  · **THE BY-STORE VIEW'S ROWS** on `/storeops/reports` — the page regroups the very rows these
    panels are folded over, in the browser, under a store key those rows already carry. The narrowed
    tile panel answers that question exactly (every drill row carries its own store in `row_key`), so
    a second per-store catalogue would be two paths to one answer.
  · **THE PTO ACCRUAL AND CHARGEBACK PANELS** below the payroll table — different subjects at a
    different grain (a month-keyed ledger, not this pay period), with their own producers. Left to
    their own family rather than stretched into this one, and the page already says in words that
    they are keyed to the calendar month.
  · **A FIGURE OVER ROWS NONE OF WHICH WERE COUNTED**, and a zero over them: refused by the one
    home's own `served_tiles` rule, dereferenced here and not copied.

TWO LIVE DEFECTS — REPORTED, REPRODUCED, NOT REPAIRED, NO FIGURE MOVED
─────────────────────────────────────────────────────────────────────
 1. **`storeops.manual_hours` NEVER REACHES THIS REPORT'S FIGURE.** 569 hand-entered correction rows
    exist (37 house, 532 Luxelink) and `GET /storeops/payroll` deliberately does not read that table
    (§14y records it; the router says so in its own comment). So a correction a manager typed on the
    Time Clock screen is invisible to the Payroll Report, and this family therefore NEVER names that
    relation: naming it would be a provenance claim about a read that did not happen. The panel names
    what answered, and this docstring names what did not.
 2. **A MANAGER'S CORRECTION IS INDISTINGUISHABLE FROM THE SCHEDULE IT WAS TYPED OVER.** On this
    read a `clocked_corrected` day is a `shifts.actual_hours` edit — the same relation a
    `schedule_opt_in` day comes out of — so the two bases name ONE table and are told apart only by
    the basis word travelling on the row. That is stated rather than hidden: the row's `fields` carry
    the basis and its sentence, and the relation named is the one the figure was really read from.

💰 MOVES NO MONEY. Read-only presentation of figures the endpoint already produced. Books nothing,
pays nothing, writes nothing, and computes no business figure of its own: `period_totals` folds
exactly the four sums both screens already fold in the browser, so the tile and its evidence become
ONE fold (§CORE-17.3d) instead of two that can drift.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / product / store name
(RULE TWO), and no dollar literal, rate or threshold of its own.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §CORE-17.3x.
"""
from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.core import report_drilldown
from app.modules.storeops import payroll_hours_basis as _hb

#: THE TIME CLOCK — the one relation that records an hour somebody actually worked. DEREFERENCED from
#: the lineage registry, which is the one home for that fact (§CORE-17.3t re-keyed the last literal
#: copy of it); this module holds no copy.
TIME_CLOCK = _lineage.TIME_CLOCK
#: THE SCHEDULE — an INTENTION, and also the relation a manager's hours correction is typed onto
#: (`shifts.actual_hours`) on this read. Dereferenced, never spelled.
SCHEDULE = _lineage.SCHEDULE
#: THE EMPLOYEE RECORD — where a person's pay rate and their pay basis live. Named on every MONEY
#: row, because the gross cannot be read without it. A registry NAME constant as of this batch, with
#: its one pre-existing literal copy (`account/liabilities_drilldown.EMPLOYEES`) re-keyed in the SAME
#: commit — naming a fact is half a design fix and deleting every copy is the half that keeps getting
#: skipped here (§19.18).
EMPLOYEE_ROSTER = _lineage.EMPLOYEE_ROSTER

SCHEMA = "storeops"

#: WHICH FIGURES CAN BE EXPLAINED AT ALL — the one home's own rule, bound BY IDENTITY. This module
#: holds no copy of it (§CORE-17.3u).
_served = report_drilldown.served_tiles

#: WHICH RELATION ANSWERED FOR A DAY'S HOURS, KEYED ON THE PRODUCER'S OWN BASIS WORD. The keys are
#: `payroll_hours_basis`'s own constants, not strings spelled here, so a basis renamed in the one home
#: moves this map with it instead of silently falling through to "nothing nameable". Every basis the
#: one home declares has an entry and the harness fails the build when one does not — a basis nobody
#: has given a provenance to must be a build failure, not a blank on a payroll screen.
#:
#: THE TWO EMPTY TUPLES ARE THE POINT, not an omission. Nothing measured and nothing payable means
#: the figure was read out of NO relation, which `report_drilldown` reports as an untraceable origin
#: rather than tidying away (§CORE-17.3i).
BASIS_RELATIONS = {
    _hb.CLOCKED: (TIME_CLOCK,),
    _hb.MEASURED_ZERO: (TIME_CLOCK,),
    _hb.CLOCKED_RECONCILED: (TIME_CLOCK,),
    _hb.CLOCKED_OVERRIDE: (TIME_CLOCK,),
    _hb.CLOCKED_CORRECTED: (SCHEDULE,),
    _hb.SCHEDULE_OPT_IN: (SCHEDULE,),
    _hb.NOT_MEASURED_UNPAID: (),
    _hb.PUNCH_UNUSABLE: (),
}

#: THE BASES WHOSE FIGURE MAY BECOME PAID HOURS — the one home's own allowlist, dereferenced. A row
#: with no hours on any of these is shown WITHOUT a figure; deciding that here would be a second
#: copy of a rule whose whole design is that a basis nobody has ruled on reads as NOT payable.
PAYING_BASES = _hb.PAYING_BASES
#: THE BASES THAT ARE A MEASUREMENT — used for the per-row `measured` word a reader sees, never for
#: whether the figure counts. Also the one home's own tuple.
MEASURED_BASES = _hb.MEASURED_BASES

#: THE FIGURES A PAY-GATED CALLER IS NOT SERVED AT ALL. Not zeroed, not blanked — absent, exactly as
#: `pay_visibility.strip_pay` treats the columns they come from.
MONEY_KEYS = ("scheduled_pay", "actual_pay")
HOURS_KEYS = ("scheduled_hours", "actual_hours")
#: Every figure this family serves, in the order both screens print them.
SERVED_KEYS = ("scheduled_hours", "actual_hours", "scheduled_pay", "actual_pay")


# ── NOTES — in the words the screens already use, and the coverage warning on every hours panel ──

_ROW_NOTE = (
    "One row per person for this pay period, merged exactly as the report merges them, at the store "
    "they worked the most of it — and only the people you are allowed to see, because the panel is "
    "folded after the same scope the report applies.")

_BASIS_NOTE = (
    " Each row names the relation that actually answered for its hours: the time clock where a punch "
    "was closed, the schedule where the period was paid from the plan instead, and NOTHING where "
    "nothing was measured. The basis word on every row is the payroll engine's own, not a reading of "
    "the number beside it.")

_COVERAGE_NOTE = (
    " READ THE SOURCES LIST BEFORE TRUSTING THIS AS HOURS WORKED. Punch coverage across this company "
    "is partial — measured on production at roughly 54% in July, 52% in August, 66% in September and "
    "62% in October, with no punches at all before July — so on a thin month most of this figure is "
    "the SCHEDULE, which records what somebody was meant to work rather than what they did. A month "
    "at that level of coverage has already been ruled not measurable once, and this panel's sources "
    "list is how you tell which kind of month you are looking at.")

_SCHEDULED_HOURS_NOTE = _ROW_NOTE + (
    " These are SCHEDULED hours: what the schedule says was planned, read out of the schedule alone. "
    "The schedule records an INTENTION — these are not a measurement of anything worked, and the "
    "Actual Hours figure beside them is the one that names a punch.")

_ACTUAL_HOURS_NOTE = _ROW_NOTE + _BASIS_NOTE + _COVERAGE_NOTE + (
    " A person for whom nothing was measured and who is not paid from the schedule is shown here "
    "without a figure and is not counted — they contribute nothing to the total above, and a 0.0 "
    "would read as 'this person worked and produced nothing'. Where the lunch break is deducted "
    "automatically, the hours above are already NET of it and each row carries the deduction as its "
    "own line rather than folded silently in.")

_SCHEDULED_PAY_NOTE = _ROW_NOTE + (
    " Scheduled hours at each person's own recorded rate, so every row names both the schedule and "
    "the employee record the rate was read from. A salaried person's row is not hours multiplied by "
    "a rate at all — it carries their pay basis, and the report's own salary note where it has one.")

_ACTUAL_PAY_NOTE = _ROW_NOTE + _BASIS_NOTE + _COVERAGE_NOTE + (
    " Each row names both the relation its hours came from AND the employee record its rate came "
    "from, because the money cannot be read without the two together. A salaried person's figure is "
    "a period allocation rather than hours times a rate, and their row says so.")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  READERS. Accessors and names only — nothing here adds, judges or decides.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def _column(field):
    """PURE: a reader for one stored field of one payroll row."""
    def _read(r):
        return (r or {}).get(field)
    return _read


def _basis_tally(r):
    """PURE: the producer's own `hours_by_basis` map with the zero entries dropped — the SAME filter
    the router applies before it picks a row's dominant basis, so this module and that one agree
    about which bases a period actually has. Nothing is summed, renamed or re-attributed."""
    return {k: v for k, v in ((r or {}).get("hours_by_basis") or {}).items() if v}


def _bases(r):
    """PURE: the bases that carried hours for this person this period."""
    return tuple(_basis_tally(r).keys())


def _pays(r):
    """PURE: did ANY basis that may become paid hours carry hours for this person? The allowlist is
    the one home's, so a basis nobody has ruled on reads as not payable here exactly as it does
    there."""
    return any(b in PAYING_BASES for b in _bases(r))


def _actual_hours(r):
    """PURE: the hours the report paid for — and NO FIGURE AT ALL for a person nothing measured and
    nothing entitles to the schedule (§CORE-17.3i). Read off the producer's own basis tally, never
    re-derived from the number beside it."""
    return (r or {}).get("actual_hours") if _pays(r) else None


def _actual_pay(r):
    """PURE: the money those hours produced, blank on exactly the rows the hours are blank on — a
    person with no measured hours has no measured pay either, and a $0.00 would be a wage claim."""
    return (r or {}).get("actual_pay") if _pays(r) else None


#: WHICH "WE CANNOT MEASURE THIS" ANSWER A ROW REPORTS WHEN IT HAS MORE THAN ONE, strongest first.
#: AN UNUSABLE PUNCH AND NOTHING AT ALL ARE DIFFERENT FACTS and the distinction survives here: one
#: says a punch EXISTS and the company's own rule refuses to count it, which is a thing to go and fix,
#: and the other says nobody clocked at all. The router drops a zero-hour basis before it picks a
#: row's dominant one — correctly, because it is naming where the HOURS came from — so this reads the
#: engine's tally UNFILTERED for the reason, which is the only place that word survives.
REASON_PRECEDENCE = (_hb.PUNCH_UNUSABLE, _hb.NOT_MEASURED_UNPAID)


def _unmeasured_reason(r):
    """PURE: WHY a row carries no figure, in the PRODUCER'S OWN basis word — never a sentence
    composed here. A row that recorded an unusable punch says so; a row with no basis at all reports
    the engine's word for "nothing was measured and nothing is owed from the plan"."""
    if _pays(r):
        return None
    recorded = set(((r or {}).get("hours_by_basis") or {}).keys())
    for b in REASON_PRECEDENCE:
        if b in recorded:
            return b
    return _hb.NOT_MEASURED_UNPAID


def _hours_relations(r):
    """PURE: WHICH RELATIONS ANSWERED FOR THIS PERSON'S HOURS, per row, because three people on one
    tile can have three different answers — and a person nothing measured has none.

    Read through `BASIS_RELATIONS`, keyed on the producer's own basis words. A basis the map does not
    know contributes NOTHING rather than a guess: the harness fails the build if the one home grows a
    basis this map has no entry for, so the blank can only ever mean "measured nothing", never
    "nobody taught the panel about this basis"."""
    out = []
    for b in _bases(r):
        for t in BASIS_RELATIONS.get(b, ()):
            if t not in out:
                out.append(t)
    return tuple(out)


def _pay_relations(r):
    """PURE: the money's provenance — whichever relation answered for the hours, plus the employee
    record the rate (or the salary basis) was read out of.

    NOTHING AT ALL on a row with no figure: §CORE-17.3i is that such a row names no relation, and
    naming the employee record alone would claim a provenance for a number that does not exist."""
    return (_hours_relations(r) + (EMPLOYEE_ROSTER,)) if _pays(r) else ()


def _schedule_relations(_r):
    """PURE: the schedule, and only the schedule. A scheduled figure is read out of the plan whatever
    the clock did."""
    return (SCHEDULE,)


def _scheduled_pay_relations(_r):
    return (SCHEDULE, EMPLOYEE_ROSTER)


def _hours_column(r):
    """PURE: WHICH COLUMN the hours were read out of, per row. A punched day's hours come off the
    punch's own column and a planned day's off the schedule's, and saying one for both would name the
    wrong column on half the rows."""
    if not _pays(r):
        return None          # no figure, so no column held one
    return "hours" if any(b in MEASURED_BASES for b in _bases(r)) else "scheduled_hours"


def _label(r):
    """PURE: the person as both screens name them, with the store they are folded under."""
    r = r or {}
    name = str(r.get("name") or "").strip()
    store = str(r.get("store") or "").strip()
    if name and store:
        return "%s · %s" % (name, store)
    return name or store or None


def _fields(r):
    """PURE: what else a reader needs beside one person's row — every value the producer's own, and
    the basis sentence the producer composed so the screen and the API cannot disagree."""
    r = r or {}
    return {
        "store": r.get("store") or None,
        "shift count": r.get("shifts"),
        "hours basis": r.get("hours_basis") or None,
        "why these hours": r.get("hours_basis_reason") or None,
        "mixed basis": r.get("hours_basis_mixed") or None,
        "hours by basis": _basis_tally(r) or None,
        "lunch deducted": r.get("lunch_deduction_hours") or None,
        "pay basis": r.get("pay_basis") or None,
        "salary note": r.get("salary_note") or None,
        "prorated period": r.get("salary_prorated") or None,
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE ONE FOLD. The totals both screens print, folded ONCE, here.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def period_totals(rows):
    """THE FOUR FIGURES BOTH SCREENS PRINT, folded in ONE place.

    `GET /storeops/payroll` returns a bare list and folds no strip of its own, so each screen reduced
    the four sums itself — two copies of one arithmetic, in TypeScript, which is exactly the shape
    §CORE-17.3d exists to collapse. This is that one fold: Σ over the rows the caller was served, of
    the four fields the screens sum, and nothing else. No rule, no rate, no threshold, no judgement —
    a tile then reads its number OFF its narrowed panel and the two cannot drift.

    IT SUMS WHAT THE REPORT SUMS, INCLUDING THE ROWS A PANEL SHOWS WITHOUT A FIGURE. Such a row
    carries 0 in the payload, so it adds nothing here and is not counted there, and the tie-out still
    holds to the cent — which is what makes the honest blank and the correct total compatible rather
    than a choice.

    `closing/three_way_drilldown.totals` is the precedent: where a screen folds its own strip and no
    producer does, the one home for that strip is the declaration, and it computes sums only.
    """
    rows = [r for r in (rows or ()) if r]
    out = {}
    for k in SERVED_KEYS:
        # Rounded through the ONE home's own rounding, never a second spelling of it. Rounding per
        # row cannot drift from each screen's raw reduce here, because the producer has already
        # rounded every one of these four fields to cents before serving them — and where anything
        # ever did drift, the shared `agreesToTheCent` gate costs the reader the drill LINK and never
        # the figure, which is the whole point of reading the tile off its own panel.
        out[k] = report_drilldown.money(sum(report_drilldown.money(r.get(k)) for r in rows))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE CATALOGUE — one person-and-pay-period row, two units, three provenances.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

def catalogue(*, can_see_pay=True):
    """PURE: the panels this family serves over ONE pay period's payroll rows.

    The two MONEY panels are absent entirely for a pay-gated caller — the rows they would be folded
    over have had their money keys deleted server-side, and a panel over stripped rows would publish
    $0.00 as a measurement of somebody's pay.
    """
    common = {"row_label": _label, "keys": ("employee_id", "store"), "fields": _fields,
              "schema": SCHEMA}
    out = [
        dict(common, key="scheduled_hours", total="scheduled_hours", label="Scheduled hours",
             value=_column("scheduled_hours"), column="scheduled_hours",
             tables=_schedule_relations, unit=report_drilldown.UNIT_HOURS,
             note=_SCHEDULED_HOURS_NOTE),
        dict(common, key="actual_hours", total="actual_hours", label="Hours this payroll paid for",
             value=_actual_hours, column=_hours_column, tables=_hours_relations,
             reason=_unmeasured_reason, unit=report_drilldown.UNIT_HOURS,
             note=_ACTUAL_HOURS_NOTE),
    ]
    if can_see_pay:
        out += [
            dict(common, key="scheduled_pay", total="scheduled_pay", label="Scheduled pay",
                 value=_column("scheduled_pay"), column="scheduled_pay",
                 tables=_scheduled_pay_relations, unit=report_drilldown.UNIT_MONEY,
                 note=_SCHEDULED_PAY_NOTE),
            dict(common, key="actual_pay", total="actual_pay", label="Actual pay",
                 value=_actual_pay, column="actual_pay", tables=_pay_relations,
                 reason=_unmeasured_reason, unit=report_drilldown.UNIT_MONEY,
                 note=_ACTUAL_PAY_NOTE),
        ]
    return tuple(out)


def payroll_drilldowns(rows, *, can_see_pay=True, cap=None):
    """PURE: every panel for ONE pay period of the payroll report.

    `rows` are the rows the endpoint served THAT CALLER — already scoped, already merged, already
    stripped if they may not see pay — and they are never modified here. A period with no rows drills
    to nothing rather than to panels asserting a tie against zero.
    """
    rows = [r for r in (rows or ()) if r]
    if not rows:
        return {}
    totals = period_totals(rows)
    keys = SERVED_KEYS if can_see_pay else HOURS_KEYS
    return dict(_served(catalogue(can_see_pay=can_see_pay), rows,
                        {k: v for k, v in totals.items() if k in keys},
                        cap or report_drilldown.ROW_CAP))
