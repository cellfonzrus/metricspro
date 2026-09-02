"""Harness for the schedule↔sales rep-name join in the Daily Targets engine (2026-09-02).

Root cause it guards: LuxeLink's POS exports reps as 'Last, First' ('Antunez, Diana') while its
StoreOps schedule says 'First Last' ('Diana Antunez'). The engine's plain upper-case compare matched
ZERO of the org's reps, so every per-employee daily target multiplied by rep_share = 0 and the
employee panel rendered silent zeros — while the house org, whose two systems happen to agree on
spelling, worked. Drives the REAL targets_engine functions (pure, no I/O). What it proves:

  NAME IDENTITY (name_key)
  • 'Antunez, Diana' ≡ 'Diana Antunez' ≡ 'DIANA  ANTUNEZ' — comma style, word order, case and
    double spaces never split a person
  • 'Kellie, Mark' ≡ 'Kellie Mark' (the inversion a naive comma-flip gets wrong)
  • different people stay different; empty/None keys are falsy

  THE JOIN, CROSS-FORMAT (real engine functions)
  • scope_hours_by_day finds a rep's scheduled hours when the rep is asked for by the POS spelling
  • scope_actuals_by_day / scope_conversion find a rep's sales when asked by the schedule spelling
  • reps_in_scope lists a schedule+POS split identity ONCE, displaying the schedule spelling
  • store_code matching is NOT token-sorted — '26th 3352' still ≠ '3352 26th'

  THE PRORATION INPUTS
  • with cross-format names, store hours and rep hours are both > 0 → rep_share is a real fraction
    (two reps at equal hours → 0.5 each), and compute_scope emits a non-empty calendar whose per-rep
    base sums to the rep's prorated monthly — the exact numbers the employee panel shows

Run: `python3 harness_targets_rep_name_join.py` from the backend dir.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import targets_engine as T   # noqa: E402

_pass = 0
_fail = 0
FAILED = []


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        FAILED.append(name)
        print(f"  FAIL  {name}")


def run():
    print("── 1. name_key: one person, many spellings ─────────────────────────────────────")
    check("'Antunez, Diana' ≡ 'Diana Antunez'", T.name_key('Antunez, Diana') == T.name_key('Diana Antunez'))
    check("case + double spaces ignored", T.name_key('DIANA  ANTUNEZ') == T.name_key('Diana Antunez'))
    check("'Kellie, Mark' ≡ 'Kellie Mark' (naive comma-flip would miss this)",
          T.name_key('Kellie, Mark') == T.name_key('Kellie Mark'))
    check("'Islam Khan, Ariful' ≡ 'Ariful Islam Khan'",
          T.name_key('Islam Khan, Ariful') == T.name_key('Ariful Islam Khan'))
    check("different people stay different",
          T.name_key('Jacobo, Liset') != T.name_key('Jacobo, Vanessa'))
    check("empty and None key to falsy", not T.name_key('') and not T.name_key(None))

    print("── 2. the join, cross-format, on the real engine functions ─────────────────────")
    shifts = [
        {'store_code': 'kedzie', 'employee_name': 'Diana Antunez', 'shift_date': '2026-09-01',
         'scheduled_hours': 8.0},
        {'store_code': 'kedzie', 'employee_name': 'Alejandro Galarza', 'shift_date': '2026-09-01',
         'scheduled_hours': 8.0},
        {'store_code': 'kedzie', 'employee_name': 'Diana Antunez', 'shift_date': '2026-09-02',
         'scheduled_hours': 4.0},
        {'store_code': 'Cicero', 'employee_name': 'Diana Antunez', 'shift_date': '2026-09-01',
         'scheduled_hours': 6.0},  # other store — must not leak into kedzie's scope
    ]
    actuals = [
        {'store_code': 'KEDZIE', 'rep_name': 'Antunez, Diana', 'trans_date': '2026-09-01',
         'prem_count': 2, 'byod_count': 1, 'upg_count': 0, 'acc_gp': 50.0,
         'setup_fee': 0, 'box_count': 3, 'billpay_count': 10},
        {'store_code': 'KEDZIE', 'rep_name': 'Galarza, Alejandro', 'trans_date': '2026-09-01',
         'prem_count': 1, 'byod_count': 0, 'upg_count': 1, 'acc_gp': 20.0,
         'setup_fee': 0, 'box_count': 1, 'billpay_count': 5},
    ]
    hrs = T.scope_hours_by_day(shifts, 'kedzie', 'Antunez, Diana')   # POS spelling asks for hours
    check("scheduled hours found via the POS spelling",
          hrs == {date(2026, 9, 1): 8.0, date(2026, 9, 2): 4.0})
    acts = T.scope_actuals_by_day(actuals, 'kedzie', 'Diana Antunez')  # schedule spelling asks for sales
    check("sales found via the schedule spelling",
          date(2026, 9, 1) in acts and acts[date(2026, 9, 1)]['prem'] == 2
          and acts[date(2026, 9, 1)]['byod'] == 1)
    conv = T.scope_conversion(actuals, 'kedzie', 'Diana Antunez')
    check("conversion joins cross-format too", conv['boxes'] == 3 and conv['billpays'] == 10)
    reps = T.reps_in_scope(shifts, actuals, 'kedzie')
    check("split identity listed ONCE, schedule spelling shown",
          reps.count('Diana Antunez') == 1 and 'Antunez, Diana' not in reps
          and 'Alejandro Galarza' in reps and len(reps) == 2)
    check("store codes are NOT token-sorted ('26th 3352' ≠ '3352 26th')",
          T.scope_hours_by_day([{'store_code': '3352 26th', 'employee_name': 'A B',
                                 'shift_date': '2026-09-01', 'scheduled_hours': 8.0}],
                               '26th 3352', None) == {})

    print("── 3. proration inputs: rep_share becomes a real fraction, calendar non-empty ──")
    store_hours = T.scope_hours_by_day(shifts, 'kedzie', None)
    rep_hours = T.scope_hours_by_day(shifts, 'kedzie', 'Antunez, Diana')
    sh, rh = sum(store_hours.values()), sum(rep_hours.values())
    check("store hours and rep hours both > 0 (was rep=0 → share=0 before)", sh == 20.0 and rh == 12.0)
    rep_share = rh / sh
    monthly = {c: round(100.0 * rep_share, 2) for c in T.CATEGORIES}
    result = T.compute_scope(monthly, rep_hours, acts, date(2026, 9, 1),
                             round_counts=False, month_end=date(2026, 9, 2))
    check("calendar renders the rep's scheduled days (not empty)",
          [d['date'] for d in result['calendar']] == ['2026-09-01', '2026-09-02'])
    base_sum = sum(d['cats']['activations']['base'] for d in result['calendar'])
    check("rep's daily bases sum to the prorated monthly (100 × 12/20 = 60)",
          abs(base_sum - 60.0) < 0.01)

    print(f"\n{_pass} passed, {_fail} failed")
    if FAILED:
        print("FAILED:", *FAILED, sep="\n  - ")
        sys.exit(1)


if __name__ == "__main__":
    run()
