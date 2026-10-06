"""WHICH INSTALMENT MONTH IS THIS ROW IN — the ONE home, answered from the ROW'S OWN anchor.

THE CLASS, NOT THE INSTANCE (index §19.51). The instance was "the residual installment engine pays
every long-standing subscriber the SIXTH month of a six-month curve, every month, forever". The
class is the same one §19.46 fixed for a feed row's month:

    **a row's position in a schedule is a property of THE ROW, never of the window the reader
    happened to pull.**

`installment_engine.compute_installments` derived a subscriber's activation month as the LOWEST
period index it found inside its own lookback window (`max_n = min(12, max(num_months))` months
back). For anybody present in all of those months that yields `month_index == max_n` — and it yields
it again next month, and the month after, because the window slides with the pay period. Measured
live 2026-10-06 (house org, read-only, September 2026, the real engine replayed): 16,757 of 26,981
paid rows / **$119,887.50 of $177,462.50** sat at `month_index == 6`, and against the rows' OWN
`mi_activation_date` the derived month DISAGREED on **11,780 of 15,337** anchored subscribers
(**$57,992.50**), late by 1 to 13+ months.

The sibling path answers the SAME question and was measured CORRECT:
`sale_installment_engine.compute_sale_installments` iterates the periods a sale could have come from
and reads each period's own rows, so its anchor is the row's own origin month — verified live, every
cohort carrying forward exactly once (284 -> 284 -> 284, 89 -> 89 -> 89, 312 -> 312). It was right by
construction, not by accident, and its arithmetic is the arithmetic here. So this module is the
FACTORED fact, not a second derivation: both engines now compute `month_index` with `month_span`, and
both clamp with `in_schedule` / `horizon`.

WHAT AN ANSWER CARRIES. Never a bare integer. `resolve_month` returns the month index **and the
BASIS it was derived from** — the §19.49 / §19.50 shape, where a verdict that could not be measured
says so instead of guessing:

    BASIS_ACTIVATION     the row's own activation date (an anchor, the real answer)
    BASIS_ORIGIN_PERIOD  the row's own origin month (the sale-path anchor; equally real)
    BASIS_WINDOW_EDGE    NO anchor on the row -- the lookback floor, which is a GUESS

A `BASIS_WINDOW_EDGE` answer is a row the engine could not place, and the §19.48 rule applies to it:
it is REPORTED, with its count and its dollars, never silently folded in as though it were known.
`BasisTally` is what the caller reports it with.

NO DB, NO CLOCK, NO NETWORK, NO VOCABULARY. Pure stdlib. Every carrier-, tenant- or product-specific
fact (which column holds the activation date, how many months a schedule runs, what the ceiling is)
is handed in by the caller from its own config rows -- RULE TWO. This module knows only arithmetic.
"""
from __future__ import annotations

import datetime as _dt

# ── THE BASES — how a month index was arrived at ─────────────────────────────────────────────────
# Three values, and the first two are the only ones that mean "I know". Spelled as constants so no
# caller writes the string and no reader has to guess which spellings exist (the §19.50 VERDICT_*
# precedent).
BASIS_ACTIVATION = "activation_date"
BASIS_ORIGIN_PERIOD = "origin_period"
BASIS_WINDOW_EDGE = "window_edge"

#: The bases that come from the ROW. Anything else is a fallback the caller must report.
ANCHORED_BASES = (BASIS_ACTIVATION, BASIS_ORIGIN_PERIOD)

#: Human wording for an operator notice / a payload note. Display only; moves no figure.
BASIS_LABELS = {
    BASIS_ACTIVATION: "the subscriber's own activation date",
    BASIS_ORIGIN_PERIOD: "the row's own origin month",
    BASIS_WINDOW_EDGE: "the oldest month in the lookback window (no activation date on the row)",
}


def is_anchored(basis) -> bool:
    """True when the month index came from the row itself rather than from the reader's window."""
    return str(basis or "") in ANCHORED_BASES


# ── THE SCALE — one monotonic month index, shared by every caller ────────────────────────────────
def period_index(year, month):
    """Monotonic month index for a (year, month) pair, or None when either is missing/out of range.

    `year * 12 + (month - 1)`. This is THE scale: `month_span` only ever subtracts two of these, so
    an index built any other way (a different epoch, a 1-based month) would silently shift every
    schedule by a month. `installment_engine._period_index` dereferences this rather than keeping
    its own copy of the expression.
    """
    try:
        y, m = int(year), int(month)
    except (TypeError, ValueError):
        return None
    if not y or not 1 <= m <= 12:
        return None
    return y * 12 + (m - 1)


def index_of_date(value):
    """Month index of a date, or None when the value is absent or unreadable.

    Accepts a `date` / `datetime`, or a string whose first 10 characters are an ISO `YYYY-MM-DD`
    (what a feed row carries). Deliberately strict: a value this cannot read returns None, which
    becomes a REPORTED `BASIS_WINDOW_EDGE` row rather than a plausible-looking month. There is no
    "today" in this module, so nothing here can drift with the clock.
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return period_index(value.year, value.month)
    if isinstance(value, _dt.date):
        return period_index(value.year, value.month)
    s = str(value).strip()
    if len(s) < 7:
        return None
    try:
        return period_index(int(s[0:4]), int(s[5:7]))
    except (TypeError, ValueError):
        return None


# ── THE ARITHMETIC — shared with the sale-installment path ───────────────────────────────────────
def month_span(anchor_index, pay_index):
    """Which month of the curve `pay_index` is, for something anchored at `anchor_index`: 1-based.

    `(pay_index - anchor_index) + 1`. The anchor month itself is month 1. Returns None when either
    index is missing, and a value < 1 when the pay month precedes the anchor (the caller skips those
    — a payout cannot land before the thing that earned it).

    THE shared fact: `sale_installment_engine` computed exactly this inline as
    `(pay_idx - s_idx) + 1` and now dereferences it, so the two engines cannot drift on what "month
    3" means.
    """
    if anchor_index is None or pay_index is None:
        return None
    return (int(pay_index) - int(anchor_index)) + 1


def horizon(num_months_values, max_months):
    """How many months back a reader must look to cover the deepest schedule, clamped at `max_months`.

    The caller passes its own schedules' `num_months` (config rows, RULE TWO) and its own ceiling.
    Empty / unusable input yields 1 — read the pay month only — never 0, which would read nothing.
    """
    try:
        cap = int(max_months)
    except (TypeError, ValueError):
        cap = 1
    cap = max(1, cap)
    deepest = 1
    for v in num_months_values or ():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > deepest:
            deepest = n
    return max(1, min(cap, deepest))


def in_schedule(month_index, num_months) -> bool:
    """True when `month_index` is a month this schedule actually has a line for: 1 <= i <= num_months.

    A month past the end of the curve is NOT payable — which is the whole point of anchoring on the
    row: a subscriber who activated 14 months ago is past a 6-month curve and must fall out, where
    the window-edge derivation kept re-presenting them as month 6.
    """
    if month_index is None:
        return False
    try:
        i, n = int(month_index), int(num_months or 0)
    except (TypeError, ValueError):
        return False
    return 1 <= i <= n


# ── THE ANSWER — month index plus the basis it rests on ──────────────────────────────────────────
def resolve_month(pay_index, activation_date=None, origin_index=None, window_floor_index=None):
    """Which instalment month `pay_index` is for one row, and HOW that was decided.

    Returns `{"month_index", "anchor_index", "basis", "anchored"}`. `month_index` may be None (no
    anchor at all was available) or < 1 (the pay month precedes the anchor); the caller decides what
    to do with those, and `in_schedule` is the gate.

    Precedence, strongest anchor first — both of the first two are facts ABOUT THE ROW:
      1. `activation_date` — the row's own date (`BASIS_ACTIVATION`).
      2. `origin_index`    — the row's own origin month, already resolved to an index by a caller
         that reads its rows per-period (`BASIS_ORIGIN_PERIOD`; the sale path's shape).
      3. `window_floor_index` — the oldest month the reader pulled. A GUESS, flagged
         `BASIS_WINDOW_EDGE`, and only ever reached when the row carries no anchor of its own.

    The fallback exists because a feed row can genuinely lack its date, and refusing to place such a
    row at all would silently drop money that the carrier is paying. It is the honest option only
    because it is LABELLED: `anchored` is False, and `BasisTally` turns that into a reported count
    and a reported dollar figure.
    """
    act_idx = index_of_date(activation_date)
    if act_idx is not None:
        basis, anchor = BASIS_ACTIVATION, act_idx
    elif origin_index is not None:
        basis, anchor = BASIS_ORIGIN_PERIOD, int(origin_index)
    elif window_floor_index is not None:
        basis, anchor = BASIS_WINDOW_EDGE, int(window_floor_index)
    else:
        return {"month_index": None, "anchor_index": None,
                "basis": BASIS_WINDOW_EDGE, "anchored": False}
    return {"month_index": month_span(anchor, pay_index), "anchor_index": anchor,
            "basis": basis, "anchored": is_anchored(basis)}


class BasisTally:
    """Counts and dollars per basis — so "I could not place this one" is REPORTED, not absorbed.

    §19.48's rule, applied to the month index instead of to a payment type: a row the engine placed
    on a guess is named in the result with its count and its amount, so an operator can see how much
    of a payout rests on an anchor the data does not actually carry.
    """

    def __init__(self):
        self.rows = {}
        self.amounts = {}

    def add(self, basis, amount=0.0):
        """Record one resolved row. `amount` is whatever money the caller attached to it."""
        b = str(basis or BASIS_WINDOW_EDGE)
        self.rows[b] = self.rows.get(b, 0) + 1
        try:
            amt = float(amount or 0.0)
        except (TypeError, ValueError):
            amt = 0.0
        self.amounts[b] = round(self.amounts.get(b, 0.0) + amt, 2)
        return b

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())

    @property
    def unanchored_rows(self) -> int:
        """Rows placed on the window edge — the ones nobody should read as known."""
        return sum(n for b, n in self.rows.items() if not is_anchored(b))

    @property
    def unanchored_amount(self) -> float:
        return round(sum(a for b, a in self.amounts.items() if not is_anchored(b)), 2)

    def as_dict(self) -> dict:
        """The reportable shape: per-basis rows and amounts, plus the unanchored summary."""
        return {
            "rows_by_basis": dict(sorted(self.rows.items())),
            "amount_by_basis": {k: round(v, 2) for k, v in sorted(self.amounts.items())},
            "rows": self.total_rows,
            "unanchored_rows": self.unanchored_rows,
            "unanchored_amount": self.unanchored_amount,
        }

    def note(self):
        """One sentence naming what was placed on a guess, or None when everything was anchored."""
        n = self.unanchored_rows
        if not n:
            return None
        return (f"{n:,} installment row(s) (${self.unanchored_amount:,.2f}) were placed by "
                f"{BASIS_LABELS[BASIS_WINDOW_EDGE]} — their month of life is NOT proven by the data.")
