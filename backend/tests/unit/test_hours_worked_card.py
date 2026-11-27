"""THE "HOURS WORKED" CARD SHOWS WHAT WAS CLOCKED, AND SAYS SO WHEN NOTHING WAS.

Owner Sanjot, 2026-11-27, verbatim: *"it shows hours worked as the same as the hours scheduled, it
should show the atual hours worked"*.

WHAT WAS WRONG. `GET /core/employee-dashboard` computed the rep's own "Hours Worked (this month)"
figure as, literally:

    ah = sum((float(s.get("actual_hours") or 0) or float(s.get("scheduled_hours") or 0)) for s in msh)

`storeops.timelog` was never queried, and `shifts.actual_hours` carries a figure on 7 of 7,740 live
rows (index §14y), so `actual == scheduled` on essentially every row for every tenant. The equality
the owner reported is arithmetically guaranteed, not a coincidence of one month. It was the last
live single-line spelling of the `actual_hours or scheduled_hours` defect §14y fixed in six modules
and §14y.1 in four more.

WHAT THIS FILE PINS — the pure fold only (`storeops/hours_worked_card.card`), DB-free:

  · a day a punch measured contributes MEASURED hours; a day nothing measured contributes to
    `unmeasured_hours` and NEVER to `actual_hours`;
  · nothing measured reads **"no punch recorded"** and never `0h` — a measured zero and an
    unmeasured day must not print the same (the §14s rule, owner 2026-10-30);
  · a refused period (the coverage interlock) publishes NO total at all and carries the refusal
    sentence instead — "we could not look" and "nobody clocked" never print the same;
  · the basis fold is the ONE home's (`payroll_hours_basis.fold_bases`), so this card and
    `GET /storeops/payroll` cannot disagree about the word "mixed";
  · EVERY TENANT: the same shifts under `schedule_fallback` and under `clocked_only` produce
    different cards, and neither is this card's own opinion.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.modules.storeops import hours_worked_card as hwc        # noqa: E402
from app.modules.storeops import payroll_hours_basis as hb       # noqa: E402


def _row(hours, basis, scheduled):
    return {"hours": hours, "basis": basis, "scheduled": scheduled}


# ── the owner's symptom, and that it is gone ─────────────────────────────────────────────────────
def test_a_punched_day_and_an_unpunched_day_are_not_added_together():
    """THE REGRESSION. Two 9h shifts, one clocked at 7.5h, one never clocked.

    The old expression answered 18.0 ("actual" == scheduled). The card answers 7.5 measured and
    names the other 9.0 as not clocked — the two are never summed into one "worked" figure.
    """
    rows = [_row(7.5, hb.CLOCKED, 9.0), _row(9.0, hb.SCHEDULE_OPT_IN, 9.0)]
    c = hwc.card(rows)
    assert c["actual_hours"] == 7.5
    assert c["unmeasured_hours"] == 9.0
    assert c["actual_hours"] != c["scheduled_hours"], "the defect is back"
    # The pay figure is still the one home's answer, kept SEPARATELY and labelled.
    assert c["paid_hours"] == 16.5
    assert c["state"] == hwc.PARTLY_MEASURED


def test_the_old_expression_is_reproduced_here_and_disagrees_with_the_card():
    """The control: the shipped defect, built on purpose, so "the card is right" is a comparison
    and not an assertion about itself."""
    shifts = [{"actual_hours": 0, "scheduled_hours": 9.0}, {"actual_hours": 0, "scheduled_hours": 9.0}]
    old = sum((float(s.get("actual_hours") or 0) or float(s.get("scheduled_hours") or 0))
              for s in shifts)
    assert old == 18.0, "the control no longer reproduces the defect"
    c = hwc.card([_row(9.0, hb.SCHEDULE_OPT_IN, 9.0), _row(9.0, hb.SCHEDULE_OPT_IN, 9.0)])
    # NOT 0.0 and NOT 18.0 — nothing measured these days, so the card declines to print a worked
    # figure at all and names the 18.0 as the schedule's. (This assertion originally read `== 0.0`
    # and failed, correctly: 0h is the other lie, per `test_a_measured_zero_...` below.)
    assert c["actual_hours"] is None and c["unmeasured_hours"] == 18.0
    assert c["actual_hours"] != old
    assert c["paid_hours"] == old, "a schedule_fallback org still OWES these hours — only the " \
                                   "claim that they were WORKED was wrong"


# ── nothing measured says so, and never prints 0h ────────────────────────────────────────────────
def test_nothing_measured_reads_no_punch_recorded_and_is_not_a_zero():
    c = hwc.card([_row(9.0, hb.SCHEDULE_OPT_IN, 9.0)])
    assert c["state"] == hwc.NOTHING_MEASURED
    assert c["actual_hours"] is None, "0h is the other lie — an unmeasured day is not a measured zero"
    assert "no punch recorded" in c["sentence"].lower()


def test_a_measured_zero_is_a_number_and_does_not_read_as_unmeasured():
    """§14s: the day WAS measured and the answer is zero. It must not borrow the unmeasured words."""
    c = hwc.card([_row(0.0, hb.MEASURED_ZERO, 9.0)])
    assert c["state"] == hwc.MEASURED
    assert c["actual_hours"] == 0.0, "a measured zero is a measurement"
    assert "no punch recorded" not in c["sentence"].lower()
    assert c["unmeasured_hours"] == 0.0


def test_no_shifts_at_all_is_its_own_state_and_not_nothing_measured():
    c = hwc.card([])
    assert c["state"] == hwc.NO_SHIFTS
    assert c["actual_hours"] is None and c["paid_hours"] is None


# ── a refused period publishes nothing ───────────────────────────────────────────────────────────
def test_a_refused_period_carries_the_refusal_and_no_total():
    refusal = "Only 49.0% of scheduled employee-days are measured; this org requires 85.0%."
    c = hwc.card([_row(7.5, hb.CLOCKED, 9.0)], refusal=refusal)
    assert c["state"] == hwc.REFUSED
    assert c["actual_hours"] is None and c["paid_hours"] is None and c["unmeasured_hours"] is None
    assert c["sentence"] == refusal
    assert c["refusal"] == refusal


def test_a_refusal_outranks_a_period_that_would_otherwise_look_finished():
    """Cellfonz's shape: every row this reader can see is clocked, but the ORG's coverage is below
    its floor. A card that showed the total would look like a finished figure."""
    rows = [_row(8.0, hb.CLOCKED, 8.0), _row(8.0, hb.CLOCKED, 8.0)]
    assert hwc.card(rows)["actual_hours"] == 16.0
    assert hwc.card(rows, refusal="below the floor")["actual_hours"] is None


# ── the basis words are the one home's, never this module's ──────────────────────────────────────
def test_the_dominant_basis_and_its_sentence_come_from_the_one_home():
    rows = [_row(7.5, hb.CLOCKED, 9.0), _row(9.0, hb.SCHEDULE_OPT_IN, 9.0)]
    c = hwc.card(rows)
    assert c["hours_basis"] == hb.SCHEDULE_OPT_IN       # 9.0 > 7.5 — the MOST hours
    assert c["hours_basis_reason"] == hb.basis_reason(hb.SCHEDULE_OPT_IN)
    assert c["hours_basis_mixed"] is True


def test_one_basis_only_is_not_called_mixed():
    c = hwc.card([_row(8.0, hb.CLOCKED, 8.0), _row(7.0, hb.CLOCKED, 8.0)])
    assert c["hours_basis"] == hb.CLOCKED and c["hours_basis_mixed"] is False


def test_an_unruled_basis_cannot_smuggle_itself_in_as_measured():
    """`basis_is_measurement` is the allowlist. A basis invented in 2027 is NOT a measurement here
    merely by appearing, and its hours never land in `actual_hours`."""
    c = hwc.card([_row(9.0, "invented_in_2027", 9.0)])
    assert c["actual_hours"] is None
    assert c["state"] == hwc.NOTHING_MEASURED


def test_a_non_paying_basis_contributes_nothing_to_the_pay_figure():
    """`punch_unusable` / `not_measured_unpaid` pay nothing — the clocked-only shape."""
    c = hwc.card([_row(0.0, hb.NOT_MEASURED_UNPAID, 9.0), _row(0.0, hb.PUNCH_UNUSABLE, 8.0)])
    assert c["paid_hours"] == 0.0
    assert c["unmeasured_hours"] == 17.0
    assert c["state"] == hwc.NOTHING_MEASURED


# ── EVERY TENANT: one shape, two declarations, two cards ─────────────────────────────────────────
def test_the_same_shifts_under_two_org_declarations_produce_different_cards():
    """The §COMMCALC-49.10 rule. This card holds no tenant's vocabulary and no mode of its own —
    the bases it is HANDED differ because the orgs declared differently, and the card follows.
    """
    fallback = hwc.card([_row(9.0, hb.SCHEDULE_OPT_IN, 9.0)])      # schedule_fallback org
    clocked = hwc.card([_row(0.0, hb.NOT_MEASURED_UNPAID, 9.0)])   # clocked_only org
    assert fallback["paid_hours"] == 9.0 and clocked["paid_hours"] == 0.0
    # Neither one claims the hours were WORKED, which is the owner's complaint.
    assert fallback["actual_hours"] is None and clocked["actual_hours"] is None
    assert fallback["unmeasured_hours"] == clocked["unmeasured_hours"] == 9.0


def test_the_sentence_names_both_sides_when_the_period_is_mixed():
    c = hwc.card([_row(31.5, hb.CLOCKED, 32.0), _row(18.0, hb.SCHEDULE_OPT_IN, 18.0)])
    assert "31.5" in c["sentence"] and "18" in c["sentence"]
    assert "measured" in c["sentence"] and "not clocked" in c["sentence"]


# ── shift counts, so the reader can see how many days are unaccounted ────────────────────────────
def test_the_shift_counts_are_split_the_same_way_the_hours_are():
    c = hwc.card([_row(8.0, hb.CLOCKED, 8.0), _row(9.0, hb.SCHEDULE_OPT_IN, 9.0),
                  _row(9.0, hb.SCHEDULE_OPT_IN, 9.0)])
    assert c["measured_shifts"] == 1 and c["unmeasured_shifts"] == 2
    assert c["shifts"] == 3
