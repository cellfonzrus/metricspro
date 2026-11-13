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

ONE LIVE DEFECT LEFT — REPORTED, REPRODUCED, NOT REPAIRED (the first was FIXED, see below)
─────────────────────────────────────────────────────────────────────
 1. **FIXED 2026-11-11 (§STOREOPS-07).** This read "`storeops.manual_hours` NEVER REACHES THIS
    REPORT'S FIGURE" for four months, and it was the defect the owner reported: *"i just fixed the
    hours in ny logistics , went in to check it still shows the old hours"*. By then there were
    **616** correction rows (51 house, 565 LuxeLink; ±4,441h gross), eight of them typed in the ten
    days before the report, and `GET /storeops/payroll` read none of them — on NY LOGISTICS, which
    holds punches and not one shift row in its pay period, there was nothing a manager could type
    that would move the figure at all.
    `GET /storeops/payroll` now folds a correction through
    `payroll_hours_basis.corrections_by_day`, so the relation IS named: a corrected day resolves
    `clocked_corrected`, which `BASIS_RELATIONS` below already maps. A correction that is
    legitimately NOT folded — the org declared `count_manual_hours = false`, or the day falls before
    its `corrections_effective_from` — carries the one home's own sentence for why, so an uncounted
    correction is never a blank. The provenance claim is now about a read that DID happen.
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
from app.modules.storeops import payroll_employer_cost as _empcost

#: THE TIME CLOCK — the one relation that records an hour somebody actually worked. DEREFERENCED from
#: the lineage registry, which is the one home for that fact (§CORE-17.3t re-keyed the last literal
#: copy of it); this module holds no copy.
TIME_CLOCK = _lineage.TIME_CLOCK
#: THE SCHEDULE — an INTENTION, and also the relation a manager's hours correction is typed onto
#: (`shifts.actual_hours`) on this read. Dereferenced, never spelled.
SCHEDULE = _lineage.SCHEDULE
#: HAND-ENTERED HOURS — the correction route a manager actually HAS (`storeops.manual_hours`, mig
#: 045). Named here from 2026-11-11, because from that date this report READS it (§STOREOPS-07); for
#: the four months before, naming it would have been a provenance claim about a read that did not
#: happen, and the docstring above says so. Dereferenced from the lineage registry, never spelled.
MANUAL_HOURS = _lineage.MANUAL_HOURS
#: THE EMPLOYEE RECORD — where a person's pay rate and their pay basis live. Named on every MONEY
#: row, because the gross cannot be read without it. A registry NAME constant as of this batch, with
#: its one pre-existing literal copy (`account/liabilities_drilldown.EMPLOYEES`) re-keyed in the SAME
#: commit — naming a fact is half a design fix and deleting every copy is the half that keeps getting
#: skipped here (§19.18).
EMPLOYEE_ROSTER = _lineage.EMPLOYEE_ROSTER
#: THE EMPLOYER TAX RATES AND WAGE BASES, and what earlier periods already consumed of those bases.
#: An employer-burden figure is not readable from a wage alone, so both are named on its rows.
#: Dereferenced from the lineage registry, never spelled.
PAYROLL_TAX_CONFIG = _lineage.PAYROLL_TAX_CONFIG
PAYROLL_TAX_LEDGER = _lineage.PAYROLL_TAX_LEDGER

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
    #: A CORRECTED DAY NAMES ALL THREE, and that is the honest provenance rather than a tidier
    #: single one. A correction is a DELTA (§STOREOPS-07), so the figure rests on whatever measured
    #: the day — the clock, or the schedule when nothing did — PLUS the correction itself, which
    #: lives in `manual_hours` or, in its older and now-unwritable spelling, in the shift row's own
    #: `actual_hours`. A reader asking why the figure is not what the clock said has to be sent to
    #: the corrections as well as to the clock. Same posture as CLOCKED_TRIMMED_TO_SCHEDULE below.
    _hb.CLOCKED_CORRECTED: (TIME_CLOCK, SCHEDULE, MANUAL_HOURS),
    #: TRIMMED TO THE ROSTERED START (§STOREOPS-03) answers out of BOTH relations, and that is the
    #: honest provenance rather than a tidier single one: the punch is where the hours came from, and
    #: the schedule is where the start they were cut back to came from. A reader asking why the figure
    #: is not what the clock said has to be sent to the roster as well as to the clock.
    _hb.CLOCKED_TRIMMED_TO_SCHEDULE: (TIME_CLOCK, SCHEDULE),
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
MONEY_KEYS = ("scheduled_pay", "actual_pay", "employer_tax", "employer_cost")
HOURS_KEYS = ("scheduled_hours", "actual_hours")
#: Every figure this family serves, in the order both screens print them.
SERVED_KEYS = ("scheduled_hours", "actual_hours", "scheduled_pay", "actual_pay",
               "employer_tax", "employer_cost")


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

_TAX_BASIS_NOTE = (
    " The RATES AND WAGE BASES ARE THIS COMPANY'S OWN, read from its payroll tax settings — this "
    "panel applies no rate of its own and is the SAME rule the Payroll Expenses page books from. The "
    "annual wage bases (Social Security, FUTA, state unemployment) are consumed by payroll runs that "
    "have been BOOKED: where none has been booked for an earlier period of this year, every base "
    "starts the period untouched, so FUTA and state unemployment here are an upper bound rather than "
    "a settlement. The sources list names the settings the rates came from and the ledger the caps "
    "were read from, so you can see which of the two you are looking at.")

_EMPLOYER_TAX_NOTE = _ROW_NOTE + (
    " The EMPLOYER's own payroll tax on what this period paid — Social Security, Medicare, FUTA and "
    "state unemployment, the company's cost and not a deduction from anybody's pay. It is computed "
    "on each person's ACTUAL pay beside it, so the two columns tie; the scheduled-pay equivalent "
    "travels on every row as well.") + _TAX_BASIS_NOTE

_EMPLOYER_COST_NOTE = _ROW_NOTE + (
    " WHAT THIS PAY PERIOD COST THE COMPANY: each person's actual pay PLUS the employer tax on it, "
    "which is the figure a labour cost is actually carried at. It is a sum of the two figures beside "
    "it and nothing else — no allocation, no overhead, no benefit and no payroll-expense item (those "
    "have their own page and their own P&L line, and adding them here would double-count them). A "
    "person shown without a pay figure is shown without a cost figure and is not counted.") + _TAX_BASIS_NOTE

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


#: DID THIS ROW'S PERIOD PRODUCE PAID HOURS — the ONE home's own row-level rule, bound BY IDENTITY.
#: It was folded here from the allowlist until 2026-11-12; `payroll_employer_cost` now needs the same
#: answer, so the fold moved into `payroll_hours_basis.row_is_paid` and both dereference it rather
#: than keeping a copy each (§19.18). A basis nobody has ruled on still reads as not payable.
_pays = _hb.row_is_paid


#: WHICH ROWS THE BURDEN WAS PUT ON — the producer's own predicate, bound by identity
#: (`payroll_employer_cost.carries_pay`). It is NOT `_pays`: a salaried person is paid by a period
#: allocation and carries no hours basis at all, so the hours question would blank a figure the
#: company really does owe tax on (measured: three LuxeLink employees, $16,607.15, October 2026).
#: Binding the producer's own predicate is what keeps this panel's rows and its figure one fold.
_burden_counts = _empcost.carries_pay


def _actual_hours(r):
    """PURE: the hours the report paid for — and NO FIGURE AT ALL for a person nothing measured and
    nothing entitles to the schedule (§CORE-17.3i). Read off the producer's own basis tally, never
    re-derived from the number beside it."""
    return (r or {}).get("actual_hours") if _pays(r) else None


def _actual_pay(r):
    """PURE: the money the report paid this person, blank where the report paid nothing — a $0.00
    there would be a wage claim about somebody nothing was measured for (§CORE-17.3i).

    THE MONEY QUESTION IS NOT THE HOURS QUESTION, and this read used to ask the hours one (a defect
    found 2026-11-12 while adding the burden panels, and fixed here rather than only for the new
    figures — the same question asked twice, answered two ways, is the defect the index rules
    forbid). A SALARIED person is paid by a period allocation and carries no hours basis at all, so
    `_pays` blanked a figure the report really does pay: measured live, this panel's evidence was
    SHORT of its own figure by $16,607.15 on LuxeLink and $3,860.68 on the house org for October
    2026, and the tie-out correctly said so on both — the report's total was never wrong, its
    evidence was. The producer's own predicate is bound instead."""
    return (r or {}).get("actual_pay") if _burden_counts(r, _empcost.ACTUAL_PAY) else None


def _employer_tax(r):
    """PURE: the employer tax the producer put on this row, blank wherever the producer put none —
    a person the report did not pay has no burden either, and a $0.00 would say the company employed
    them for nothing. The figure is the producer's; nothing is computed here."""
    return (r or {}).get("employer_tax") if _burden_counts(r, _empcost.ACTUAL_PAY) else None


def _employer_cost(r):
    """PURE: pay + employer tax, as the producer served it. Blank on exactly the rows above."""
    return (r or {}).get("employer_cost") if _burden_counts(r, _empcost.ACTUAL_PAY) else None


def _burden_relations(r):
    """PURE: an employer-burden figure's provenance — whatever answered for the hours, the employee
    record the rate came from, AND the two relations the tax itself was read out of: the company's
    own rates/wage bases, and what earlier booked runs consumed of those bases. A wage alone cannot
    produce this number, so naming only the wage's relations would be a false provenance.

    NOTHING AT ALL on a row with no figure (§CORE-17.3i)."""
    return (_pay_relations(r) + (PAYROLL_TAX_CONFIG, PAYROLL_TAX_LEDGER)) if _pays(r) else ()


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
        # THE OTHER BASIS, carried rather than hidden: the report prints scheduled pay beside actual,
        # so a reader comparing the two needs the burden on each. The producer's own figures.
        "employer tax on scheduled pay": r.get("employer_tax_scheduled"),
        "cost of scheduled pay": r.get("employer_cost_scheduled"),
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
            # THE EMPLOYER BURDEN (owner 2026-11-12, §STOREOPS-08). Two more figures over the SAME
            # person-and-pay-period row at the SAME grain — which is why they belong in this
            # catalogue rather than in a second one (§CORE-17.3l: the grain decides the family).
            dict(common, key="employer_tax", total="employer_tax", label="Payroll tax (employer)",
                 value=_employer_tax, column="actual_pay", tables=_burden_relations,
                 reason=_unmeasured_reason, unit=report_drilldown.UNIT_MONEY,
                 note=_EMPLOYER_TAX_NOTE),
            dict(common, key="employer_cost", total="employer_cost",
                 label="Cost of payroll to the company",
                 value=_employer_cost, column="actual_pay", tables=_burden_relations,
                 reason=_unmeasured_reason, unit=report_drilldown.UNIT_MONEY,
                 note=_EMPLOYER_COST_NOTE),
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
