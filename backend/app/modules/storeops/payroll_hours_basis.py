"""THE ONE HOME for "which hours is this employee paid for on this day, and on what BASIS?"

OWNER ASK 2026-10-10 (verbatim): *"for the payroll menu need to turn off the payroll automatically
being calculated as per schedule, it is only based on actual hours clocked in unless a button is
checked for each employee separately against that employee name."*

THE CLASS, NOT THE INSTANCE
---------------------------
The instance is "payroll is calculated as per schedule". The class is:

    **paid hours were decided by `actual_hours or scheduled_hours` in six places at once, over a
      column effectively NOTHING writes — so the `or` fell through to the schedule on ~100% of
      rows, and no reader was ever told that is what happened.**

MEASURED, production, read-only, 2026-10-10 (Supabase REST; re-runnable):

  · `storeops.shifts` holds **7,740** rows. **7** carry `actual_hours > 0** — all on four April days,
    and in every one of those seven `actual_hours == scheduled_hours == 9`, i.e. even they look like
    copies of the schedule rather than measurements. Nothing in this codebase writes that column
    except `PATCH /storeops/shifts` (a DM edit); the clock does not, the force-clockout sweep does
    not, no backfill does. It is, in practice, a DEAD column that payroll reads.
  · The punches are NOT missing. `storeops.timelog` holds **3,663** rows, **3,555** with
    `hours > 0`, and it is live today (open punches stamped 2026-10-10). 40 rows have
    `clock_out IS NULL`; 290 are `auto_clocked_out`.
  · The CORRECTIONS are not missing either. `storeops.manual_hours` holds **569** rows (37 house,
    532 Luxelink) — and `GET /storeops/payroll` deliberately does not read it (see
    `router.py` ~:1929: it is REPORTED as "manual hours not in payroll"). So the second half of the
    same class: the correction never reaches the number either.

So "the clock is unused" was never the problem. The measurement never reached the column payroll
reads. That is ONE fact with SIX homes, and this module is the one home it now has.

WHAT THIS MODULE ANSWERS, AND HOW IT ANSWERS IT
-----------------------------------------------
`resolve_day` takes the evidence for ONE (employee, business date) and returns
`(hours, basis, detail)` — **never the number alone**. A caller that wants hours must take the
basis with it, which is the whole point: the pre-fix `or` produced a number that could not say
whether it was measured.

THE BASIS VOCABULARY (and what each one means for the money)

  `clocked`              a CLOSED punch that the org counts as evidence. The hours are the punch's.
  `clocked_corrected`    a human correction — a `manual_hours` row, or a `shifts.actual_hours > 0`
                         DM edit. A correction always outranks a raw punch (the pay path's
                         long-standing rule: a DM who fixed a forgotten punch must not be
                         overwritten by the partial punch they fixed).
  `measured_zero`        a measurement exists for this day and it says ZERO (the store was closed,
                         the person clocked straight back out, a DM zeroed it). ZERO IS THE TRUTH
                         and it NEVER falls back to the schedule.
  `schedule_opt_in`      nothing was measured, AND this employee is ticked to be paid from the
                         schedule (the owner's per-employee button), or the whole org still is.
                         The hours are the SCHEDULE'S and the basis says so out loud.
  `not_measured_unpaid`  nothing was measured and there is no opt-in. **0 hours, and SAID.** This is
                         the state the pre-fix `or` could not express: it silently paid the schedule.
  `punch_unusable`       a punch exists but it is OPEN, or it was AUTO-CLOCKED-OUT, and the org does
                         not count such a punch as evidence. It is NOT a measurement and it is NOT
                         silently paid: the day falls through to the opt-in / unpaid answer and the
                         unusable punch is REPORTED. `clockin_evidence.py` documents a punch left
                         open five days that auto-closed for 122.19 hours — paying that because a
                         row happened to carry a number is exactly the defect this basis prevents.

THE RULE IS AN ALLOWLIST, deliberately (the `carrier_dollar_class.component_is_evidence` pattern,
index §66). `basis_pays_hours` names the bases whose figure may become paid hours; everything else —
**including a basis nobody has ruled on yet** — reads as NOT payable. A denylist would default the
other way, and defaulting the other way is this defect: a new basis added later could silently
start paying the schedule again.

PRECEDENCE, stated once, for every caller
    correction  >  usable closed punch  >  measured zero  >  schedule opt-in  >  unpaid

CONFIG, NEVER CODE (RULE TWO)
-----------------------------
Not one tenant, store, employee or carrier name appears here. Every fork is a row:
`storeops.payroll_hours_basis_config` per org over the house defaults in `DEFAULT_CONFIG`, plus the
per-employee `storeops.employees.pay_from_schedule` tick box (mig 1070).

**THE DEFAULT PROTECTS THE PAYROLL.** `hours_basis_mode` ships as `MODE_SCHEDULE_FALLBACK` and
`pay_from_schedule_default` ships TRUE, so every existing tenant behaves **byte-identically to
today** until somebody turns it on. That is not timidity, it is arithmetic — measured on production
2026-10-10, flipping to clocked-only right now would move paid hours like this:

  | tenant   | month   | today's paid h | clocked-only h |   delta |
  |----------|---------|---------------:|---------------:|--------:|
  | Cellfonz | 2026-06 |        4,830.0 |            0.0 | -100.0% |
  | Cellfonz | 2026-07 |        6,274.7 |        5,066.5 |  -19.3% |
  | Cellfonz | 2026-08 |        5,244.6 |        3,212.8 |  -38.7% |
  | Cellfonz | 2026-09 |        5,523.0 |        3,500.1 |  -36.6% |
  | Luxelink | 2026-07 |        8,265.7 |        4,218.8 |  -49.0% |
  | Luxelink | 2026-08 |        7,138.8 |        4,768.5 |  -33.2% |
  | Luxelink | 2026-09 |        5,907.1 |        4,561.8 |  -22.8% |

PARTIAL COVERAGE IS THE COMMON CASE, NOT THE EDGE CASE — which is why the interlock below trips on
a COVERAGE THRESHOLD and not merely on zero punches. Per-day punch coverage of scheduled days runs
72–85% (Cellfonz) and 68–96% (Luxelink); June 2026 and every month before July 2026 have NO punches
at all. `coverage` + `interlock` refuse to compute a clocked-only period whose measured share is
below `min_punch_coverage_pct` and REPORT it as a data defect — they never pay the shortfall as
zero, and they never quietly reinstate the schedule.

PURE. No DB, no network, no clock, no app imports. Proof + lock:
`backend/harness_payroll_hours_basis.py`, `backend/harness_payroll_hours_basis_lock.py`.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §14y.
"""
from __future__ import annotations

# ── THE BASIS VOCABULARY ─────────────────────────────────────────────────────────────────────────
CLOCKED = "clocked"
CLOCKED_CORRECTED = "clocked_corrected"
MEASURED_ZERO = "measured_zero"
SCHEDULE_OPT_IN = "schedule_opt_in"
NOT_MEASURED_UNPAID = "not_measured_unpaid"
PUNCH_UNUSABLE = "punch_unusable"
#: TWO CLOCKS MEASURED THIS DAY AND THEY DISAGREE, and the org's declared rule chose between them.
#: Owner ruling 2026-10-10, verbatim: *"if they are clocking out at earlier time from the elevate
#: then it means they are spending time in store to make extra hours and they ahve no more work to
#: do, use that as the time used to create payroll and mention on the report why you are doing so."*
CLOCKED_RECONCILED = "clocked_reconciled"
#: A HUMAN OVERRODE THE RULE for this day, with a reason. Out-ranks everything: somebody genuinely
#: does closing duties, a cash count, or waits for a DM after the POS is shut, and those are payable
#: hours. The rule is the default; this is the labour-law escape hatch, and it is audited.
CLOCKED_OVERRIDE = "clocked_override"

ALL_BASES = (CLOCKED, CLOCKED_CORRECTED, CLOCKED_RECONCILED, CLOCKED_OVERRIDE, MEASURED_ZERO,
             SCHEDULE_OPT_IN, NOT_MEASURED_UNPAID, PUNCH_UNUSABLE)

#: THE ALLOWLIST. A basis whose hours figure may become PAID hours. An unknown or missing basis is
#: absent from it on purpose — see the module docstring.
PAYING_BASES = (CLOCKED, CLOCKED_CORRECTED, CLOCKED_RECONCILED, CLOCKED_OVERRIDE, MEASURED_ZERO,
                SCHEDULE_OPT_IN)
#: DERIVED, never re-spelled: the complement cannot drift out of step with the allowlist.
NON_PAYING_BASES = tuple(b for b in ALL_BASES if b not in PAYING_BASES)
#: The bases that are a MEASUREMENT of the day (the question `salary_expense`'s three states ask).
MEASURED_BASES = (CLOCKED, CLOCKED_CORRECTED, CLOCKED_RECONCILED, CLOCKED_OVERRIDE, MEASURED_ZERO)

BASIS_REASONS = {
    CLOCKED: "A closed time-clock punch this org counts as evidence. These are the clocked hours.",
    CLOCKED_CORRECTED: "A human correction (a manual-hours row, or a DM edit of the shift's actual "
                       "hours). A correction always outranks the raw punch it corrects.",
    CLOCKED_RECONCILED: "Two clock records measured this day and they disagree by more than this "
                        "org's tolerance, so this org's declared rule chose between them. The row "
                        "carries the rule's own sentence saying which end of the shift was taken "
                        "from which record, and why.",
    CLOCKED_OVERRIDE: "A manager overrode the reconciliation for this day, with a reason, and an "
                      "override always out-ranks the rule — closing duties, a cash count or waiting "
                      "for a DM after the POS is shut are payable hours the clock cannot see.",
    MEASURED_ZERO: "A measurement exists for this day and it says zero. Zero is the truth here; it "
                   "never falls back to the schedule.",
    SCHEDULE_OPT_IN: "Nothing was measured, and this employee (or this org) is ticked to be paid "
                     "from the schedule. These are SCHEDULED hours, not clocked hours.",
    NOT_MEASURED_UNPAID: "Nothing was measured and there is no schedule opt-in for this employee, "
                         "so no hours are payable for this day. This is reported, not paid from "
                         "the schedule.",
    PUNCH_UNUSABLE: "A punch exists but it is open or it was auto-clocked-out, and this org does "
                    "not count such a punch as a measurement. It is reported rather than paid.",
}

#: Why a punch was not usable. Part of the detail, never a default.
PUNCH_OPEN = "open_punch"
PUNCH_AUTO = "auto_clocked_out"

# ── MODES ────────────────────────────────────────────────────────────────────────────────────────
#: Today's behaviour, for every tenant, until somebody changes a row: an unmeasured day falls back
#: to the schedule. The owner's "turn it off" lands as MODE_CLOCKED_ONLY, per org / per employee.
MODE_SCHEDULE_FALLBACK = "schedule_fallback"
MODE_CLOCKED_ONLY = "clocked_only"
MODES = (MODE_SCHEDULE_FALLBACK, MODE_CLOCKED_ONLY)

# ── WHICH OF TWO COMPETING CLOCK RECORDS IS THE PAID ONE (owner ruling 2026-10-10) ───────────────
# The class is **"which of two clock records is the paid one, and on what basis"**, and it is a
# DECLARED RULE, never an `if` in a handler. RULE TWO: no platform is named here — the two records
# are "ours" (the kiosk, `storeops.timelog`) and "the feed" (whatever report an org has wired), and
# which org runs which product is data.
#
#: THE HOUSE DEFAULT, and why it is not the owner's literal words. He named the clock-OUT: *"if they
#: are clocking out at earlier time from the elevate … they have no more work to do, use that as the
#: time used to create payroll."* But pay is a DURATION, and taking the earlier-clock-out record's
#: WHOLE duration also imports that record's clock-IN — which on the measured day is EARLIER on 3 of
#: 16 paired rows, so `EARLIER_CLOCK_OUT` pays MORE than today on those rows (worst case 9.17h
#: instead of 8.94h: the feed's in is 09:44 against the kiosk's 09:58, same clock-out). That is the
#: opposite of the intent. `NARROWEST_WINDOW` applies his own reasoning at BOTH ends — the later
#: start and the earlier end — so it can never pay more than either record and is not gameable at
#: one end. Measured on 2026-10-09, 16 paired rows: today 146.42h / $2,156.45; EARLIER_CLOCK_OUT
#: 143.63h / $2,113.60; NARROWEST_WINDOW 142.98h / $2,102.63.
RULE_NARROWEST_WINDOW = "narrowest_window"
#: The owner's literal ruling, kept selectable so one config row switches to it.
RULE_EARLIER_CLOCK_OUT = "earlier_clock_out"
#: Today: our own kiosk record decides and the feed is only a check.
RULE_OURS_ONLY = "ours_only"
CLOCK_SOURCE_RULES = (RULE_NARROWEST_WINDOW, RULE_EARLIER_CLOCK_OUT, RULE_OURS_ONLY)

CLOCK_RULE_REASONS = {
    RULE_NARROWEST_WINDOW:
        "This org pays the narrowest window the two clock records agree on — the LATER clock-in and "
        "the EARLIER clock-out. Time on the kiosk before the point-of-sale clock started, or after "
        "it stopped, is treated as time with no work left to do.",
    RULE_EARLIER_CLOCK_OUT:
        "This org pays the record whose clock-out is EARLIER, whole. Note that this also takes that "
        "record's clock-in, which can be the earlier of the two and therefore pay MORE than the "
        "other record does.",
    RULE_OURS_ONLY:
        "This org pays its own time-clock record and treats the clock report as a check only.",
}

# ── CONFIG (RULE TWO — house defaults; storeops.payroll_hours_basis_config rows override) ────────
HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
MIGRATION = "1070_payroll_clocked_hours_basis.sql"

DEFAULT_CONFIG = {
    # The fork the owner asked for. The house default is TODAY'S behaviour, deliberately.
    "hours_basis_mode": MODE_SCHEDULE_FALLBACK,
    # The org-level value of the per-employee tick box, for an employee whose own column is unset —
    # so a tenant can be migrated wholesale instead of ticking hundreds of boxes.
    "pay_from_schedule_default": True,
    # Is an AUTO-CLOCKED-OUT punch a measurement? NO in clocked-only mode: the sweep's number is the
    # scheduled end or a 122-hour runaway (`clockin_evidence.py` documents a punch left open five
    # days that auto-closed for 122.19h), not an observation of when somebody left.
    # ⚠ IN SCHEDULE-FALLBACK MODE IT IS FORCED TRUE, because that is what happens TODAY —
    # `router._punch_counts_for_pay` asks only for `clock_out` + `hours`, so the 290 live
    # auto-clocked-out punches on this estate (116 in Cellfonz September 2026 alone) are ALREADY
    # being paid. That is a REPORTED defect, not one this change fixes behind the owner's back: the
    # default mode must move nobody's pay, and switching clocked-only on is where it stops.
    "count_auto_clockout_punches": False,
    # Is an OPEN punch (no clock_out) a measurement? Never in the house default — there is no span.
    "count_open_punches": False,
    # Do `manual_hours` rows count as corrections? TRUE in clocked-only mode, where they are the
    # correction mechanism. In schedule-fallback mode this is forced FALSE by `resolve_config`,
    # because folding 569 unread rows into pay would move money on apply — the one thing the
    # default must not do.
    "count_manual_hours": True,
    # WHICH OF TWO COMPETING CLOCK RECORDS IS PAID (owner ruling 2026-10-10). Engages only when the
    # clock feed is switched on for the org, so the shipped default moves nothing.
    "clock_source_rule": RULE_NARROWEST_WINDOW,
    # Below this, the two records are treated as AGREEING and today's figure stands. 13 of the 16
    # paired rows on the measured day differ by under 0.3h and that is GRAIN, not padding: the feed
    # stores whole minutes and the kiosk stores seconds. A rule that bites on 29 seconds rounds
    # against the employee for no reason; two minutes is the house default and the report states it.
    "clock_agreement_tolerance_hours": 0.034,
    # Does the feed decide pay at all? FALSE ships: until an org turns its clock feed on, the feed is
    # a check and `clock_source_rule` never engages.
    "clock_feed_is_pay_source": False,
    # The interlock. A clocked-only period whose measured share of scheduled employee-days is below
    # this is REFUSED and reported as a data defect, never paid as a shortfall.
    "min_punch_coverage_pct": 85.0,
    # Whether the interlock refuses (True) or merely warns. Refusing is the house default.
    "interlock_refuses": True,
}
_CONFIG_FIELDS = tuple(DEFAULT_CONFIG.keys())
_BOOL_FIELDS = ("pay_from_schedule_default", "count_auto_clockout_punches",
                "count_open_punches", "count_manual_hours", "interlock_refuses",
                "clock_feed_is_pay_source")


def resolve_config(org_row=None):
    """Merge a (possibly partial / absent) per-org config row over the house defaults.

    Same shape and posture as `salary_expense.resolve_config` and `payroll_expenses
    .resolve_tax_config` — an org with no row, or a database where mig 1070 has not run, behaves
    exactly as the house default, which is exactly as today.

    THE ONE THING THIS FUNCTION DECIDES RATHER THAN MERGES: `count_manual_hours` is forced FALSE in
    `MODE_SCHEDULE_FALLBACK`. `GET /storeops/payroll` has never read `storeops.manual_hours`
    (router.py ~:1929 reports those rows as deliberately NOT in payroll), so honouring the flag in
    the default mode would fold 569 live correction rows into pay the moment this code deployed.
    The default mode must be byte-identical to today; the corrections become payable when the org
    turns clocked-only ON, which is the decision the owner makes, not one this module makes for him.
    """
    eff = dict(DEFAULT_CONFIG)
    for f in _CONFIG_FIELDS:
        v = (org_row or {}).get(f)
        if v is not None:
            eff[f] = v
    mode = str(eff["hours_basis_mode"] or "").strip()
    eff["hours_basis_mode"] = mode if mode in MODES else MODE_SCHEDULE_FALLBACK
    for f in _BOOL_FIELDS:
        eff[f] = bool(eff[f])
    if (eff["hours_basis_mode"] == MODE_SCHEDULE_FALLBACK
            and (org_row or {}).get("count_auto_clockout_punches") is None):
        # Today's behaviour, preserved exactly — see the note on the default above. An org that says
        # otherwise in its own row is honoured either way.
        eff["count_auto_clockout_punches"] = True
    rule = str(eff["clock_source_rule"] or "").strip()
    eff["clock_source_rule"] = rule if rule in CLOCK_SOURCE_RULES else RULE_NARROWEST_WINDOW
    try:
        eff["clock_agreement_tolerance_hours"] = max(0.0, float(eff["clock_agreement_tolerance_hours"]))
    except (TypeError, ValueError):
        eff["clock_agreement_tolerance_hours"] = DEFAULT_CONFIG["clock_agreement_tolerance_hours"]
    try:
        pct = float(eff["min_punch_coverage_pct"])
    except (TypeError, ValueError):
        pct = DEFAULT_CONFIG["min_punch_coverage_pct"]
    eff["min_punch_coverage_pct"] = max(0.0, min(100.0, pct))
    if eff["hours_basis_mode"] == MODE_SCHEDULE_FALLBACK:
        eff["count_manual_hours"] = False
    return eff


def basis_pays_hours(basis):
    """PURE: may the hours figure reached on this `basis` become PAID hours? Answered from the basis
    ALONE.

    THE QUESTION, NOT THE POLICY. This function knows nothing about who is asking or what they do
    with a False, and it must not — a pay run stops, a coverage banner counts, an hours-approval
    board shows the row greyed with its reason. If a caller ever needs to pass its intent in here,
    that is the signal the question and the policy have been folded together again.

    An unknown or missing basis is NOT paying: see the allowlist note in the module docstring.
    """
    return basis in PAYING_BASES


def basis_is_measurement(basis):
    """PURE: is this basis a MEASUREMENT of the day (as opposed to the schedule, or nothing)?

    This is the question `salary_expense`'s three states ask, which is why that module now imports
    its state constants from here instead of spelling a second copy of them.
    """
    return basis in MEASURED_BASES


def basis_reason(basis):
    """PURE: the plain sentence a reader is shown next to an hours figure. An unruled basis says so
    rather than rendering blank — a basis nobody can explain is a defect, not a label."""
    return BASIS_REASONS.get(basis, f"This hours figure's basis ({basis!r}) has no ruling in "
                                    f"payroll_hours_basis — it is treated as not payable.")


# ── EVIDENCE ─────────────────────────────────────────────────────────────────────────────────────
def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _d(v):
    return str(v or "")[:10]


def punch_usability(punch, cfg):
    """PURE: `(is_evidence, reason_or_None)` for ONE `storeops.timelog` row.

    A punch is evidence iff it is CLOSED (`clock_out` set AND `hours` not null) and — unless the org
    says otherwise — was not auto-clocked-out. `auto_clocked_out` is a NULLABLE column: absent /
    NULL means "not auto", matching every other nullable-flag reading in this codebase.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    if not (punch or {}).get("clock_out") or (punch or {}).get("hours") is None:
        return (True, None) if cfg.get("count_open_punches") else (False, PUNCH_OPEN)
    if (punch or {}).get("auto_clocked_out") is True and not cfg.get("count_auto_clockout_punches"):
        return False, PUNCH_AUTO
    return True, None


def build_evidence(shifts=None, punches=None, manual_rows=None, feed_punches=None,
                   overrides=None):
    """PURE: the per-(raw employee id, business date) evidence index every resolver reads.

    `shifts`      storeops.shifts rows (`employee_id`, `shift_date`, `scheduled_hours`,
                  `actual_hours`). Soft-deleted rows must already be filtered by the caller's
                  query, as every payroll query does.
    `punches`     storeops.timelog rows (`employee_id`, `work_date`, `hours`, `clock_out`,
                  `auto_clocked_out`). A caller that holds no punches passes none — the resolver
                  then answers from the shift rows alone, which is what `account/coa` and
                  `hr/router` have, and reproduces their arithmetic exactly.
    `manual_rows` storeops.manual_hours rows (`employee_id`, `work_date`, `hours`). A correction may
                  be NEGATIVE (there are negative rows live), so presence — never a `> 0` test — is
                  what makes it a measurement.

    Keys are the RAW `employee_id` on each row, the same grain `router._punch_driven_day_maps`
    already used, so the existing identity-merge behaviour is untouched. A caller whose rows are
    ALREADY narrowed to one employee (`salary_owed`) may leave the id off entirely; those rows key
    on `None` and still group by day, because dropping them would silently lose a day's evidence.
    Only a row with no BUSINESS DATE is dropped — a day we cannot name cannot be compared.

    `feed_punches`  the CLOCK REPORT's rows for the same days (`storeops/clock_feed.normalize`'s
                    `punches`, already store-bound, type-allowlisted and converted to UTC), keyed on
                    `employee_id` once `payroll_identity`'s matcher has BOUND them. They are a
                    competing clock record, and `reconcile_clock_records` decides between them and
                    ours under this org's declared rule (owner ruling 2026-10-10). They are used
                    only when `clock_feed_is_pay_source` is on for the org.
    `overrides`     per-(employee, day) manager overrides: `{"hours", "reason", "by"}`. An override
                    out-ranks everything, including the rule — closing duties and a cash count after
                    the point-of-sale clock is shut are payable hours no clock can see.
    """
    ev = {}

    def cell(eid, day):
        return ev.setdefault((eid, day), {"scheduled": 0.0, "shift_actual": 0.0,
                                          "shift_actual_rows": 0, "punch": 0.0, "punch_rows": 0,
                                          "manual": 0.0, "manual_rows": 0, "unusable": []})

    for s in shifts or ():
        eid = (s or {}).get("employee_id")
        day = _d((s or {}).get("shift_date"))
        if not day:
            continue
        c = cell(eid, day)
        c["scheduled"] += _f(s.get("scheduled_hours"))
        a = _f(s.get("actual_hours"))
        if a > 0:
            c["shift_actual"] += a
            c["shift_actual_rows"] += 1
    for t in punches or ():
        eid = (t or {}).get("employee_id")
        day = _d((t or {}).get("work_date"))
        if not day:
            continue
        c = cell(eid, day)
        c.setdefault("_raw_punches", []).append(t)
    for r in manual_rows or ():
        eid = (r or {}).get("employee_id")
        day = _d((r or {}).get("work_date"))
        if not day:
            continue
        c = cell(eid, day)
        c["manual"] += _f(r.get("hours"))
        c["manual_rows"] += 1
    for r in feed_punches or ():
        eid = (r or {}).get("employee_id")
        day = _d((r or {}).get("business_date") or (r or {}).get("work_date"))
        if not day:
            continue
        c = cell(eid, day)
        c.setdefault("_feed_punches", []).append(r)
    for r in overrides or ():
        day = _d((r or {}).get("work_date") or (r or {}).get("business_date"))
        if not day:
            continue
        c = cell((r or {}).get("employee_id"), day)
        c["_override"] = r
    return ev


def _apply_punch_policy(cell, cfg):
    """Fold the cell's raw punch rows into (hours, rows, unusable[]) under this org's policy. Kept
    out of `build_evidence` so the SAME evidence index can be resolved under two configs — which is
    how the harness proves a mode change moves nothing but the mode."""
    hours = 0.0
    rows = 0
    unusable = []
    for t in cell.get("_raw_punches") or ():
        ok, why = punch_usability(t, cfg)
        if ok:
            hours += _f(t.get("hours"))
            rows += 1
        else:
            unusable.append(why)
    return hours, rows, unusable


def auto_closed_punches(cell):
    """PURE: how many of this day's punches the KIOSK closed by itself, regardless of policy.

    DISTINCT FROM `punch_usability`, and that is the whole point. Whether an auto-closed punch is
    PAID is a policy question (`count_auto_clockout_punches`, and today's answer is yes — 290 live
    punches). Whether one EXISTS is a fact about the day, and the clock-variance board needs the
    fact even when the policy is paying it: *"an auto-closed kiosk punch is not evidence of
    anything ... it must ALSO be flagged for the DM rather than quietly trimmed"* (owner ruling
    2026-10-10). Reading it off `unusable` would have hidden it on exactly the orgs where it is
    being paid, which is the only place it matters.
    """
    cell = cell if isinstance(cell, dict) else {}
    return sum(1 for t in (cell.get("_raw_punches") or ())
               if str((t or {}).get("auto_clocked_out") or "").strip().lower()
               in ("1", "true", "t", "yes", "y") or (t or {}).get("auto_clocked_out") is True)


# ── WHICH OF TWO COMPETING CLOCK RECORDS IS PAID (owner ruling 2026-10-10) ───────────────────────
def _iso_dt(v):
    """PURE: an aware datetime from an ISO string / datetime, or None. Both records are stored UTC
    (`storeops.timelog` natively; the feed converted through the store's own zone by
    `storeops/clock_feed.to_utc`), so the comparison below never touches a zone."""
    from datetime import datetime, timezone
    if hasattr(v, "tzinfo"):
        try:
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    s = str(v or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def reconcile_clock_records(ours, feed, cfg):
    """PURE: `(hours, basis, reason)` for ONE day on which two clock records both measured.

    `ours` / `feed` are each `{"clock_in", "clock_out", "hours"}` — ours is this platform's own kiosk
    punch, `feed` is the clock report's row for the same (employee, store, day). Either may be None.

    THE RULE IS DECLARED, NOT CODED. `cfg["clock_source_rule"]` picks it and every outcome travels
    with the SENTENCE that explains it — the owner asked for exactly that: *"mention on the report
    why you are doing so"*. The reason is part of the return value, not a label a screen adds later,
    so no caller can show the number without the explanation.

    THE TOLERANCE. Under `clock_agreement_tolerance_hours` the two records are treated as AGREEING
    and OUR figure stands untouched. The feed stores whole minutes and the kiosk stores seconds, so
    13 of 16 paired rows on the measured day differ only by grain; a rule that bit on 29 seconds
    would round against the employee for nothing. It bites on the real cases (1.96h, 2.96h).

    AN AUTO-CLOSED PUNCH LOSES AUTOMATICALLY and is still FLAGGED: its clock-out is the sweep's
    number, not an observation, so it can never win the comparison — but trimming it quietly would
    hide a time-clock failure the DM has to see.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    rule = cfg["clock_source_rule"]
    tol = float(cfg["clock_agreement_tolerance_hours"])
    o_h = None if ours is None else _f((ours or {}).get("hours"))
    f_h = None if feed is None else _f((feed or {}).get("hours"))
    auto = bool((ours or {}).get("auto_clocked_out"))

    if feed is None or f_h is None:
        return o_h, None, ("Only this platform's own time clock measured this day; there is no "
                           "clock-report row to compare it with.")
    if ours is None or o_h is None:
        return f_h, CLOCKED_RECONCILED, ("Only the clock report measured this day; this platform's "
                                         "own time clock has nothing for it.")
    if rule == RULE_OURS_ONLY:
        return o_h, None, CLOCK_RULE_REASONS[RULE_OURS_ONLY]
    if abs(o_h - f_h) <= tol and not auto:
        return o_h, None, (f"The two clock records agree to within this org's {tol:.3f}h tolerance "
                           f"({o_h:.2f}h here against {f_h:.2f}h on the report), which is clock "
                           f"grain and not padding, so this platform's own figure stands.")

    o_in, o_out = _iso_dt((ours or {}).get("clock_in")), _iso_dt((ours or {}).get("clock_out"))
    f_in, f_out = _iso_dt((feed or {}).get("clock_in")), _iso_dt((feed or {}).get("clock_out"))

    if rule == RULE_EARLIER_CLOCK_OUT:
        # The owner's literal ruling. Kept selectable; it is NOT the house default because taking a
        # record's whole duration also takes its clock-IN, which can pay MORE than the other record.
        if auto or (o_out and f_out and f_out < o_out):
            return f_h, CLOCKED_RECONCILED, (
                f"The clock report's clock-out is the earlier of the two"
                + (" (this platform's own punch was closed automatically by the kiosk sweep, so its "
                   "clock-out is not an observation of when anybody left)" if auto else "")
                + f", so this org pays the report's {f_h:.2f}h rather than the {o_h:.2f}h on the "
                  f"kiosk: once the point-of-sale clock has stopped there is no more work to do, and "
                  f"the time after it is not paid.")
        return o_h, None, (f"This platform's own clock-out is the earlier of the two, so its "
                           f"{o_h:.2f}h stands against the report's {f_h:.2f}h.")

    # RULE_NARROWEST_WINDOW — the house default. The later start and the earlier end.
    start = max([d for d in (o_in, f_in) if d], default=None)
    end = min([d for d in (o_out, f_out) if d], default=None)
    if start is None or end is None or end < start:
        # No usable window. Fall back to the SMALLER of the two measured durations rather than
        # inventing a span: it is the one figure neither record contradicts.
        h = min(o_h, f_h)
        return h, CLOCKED_RECONCILED, (
            f"The two clock records disagree ({o_h:.2f}h here against {f_h:.2f}h on the report) and "
            f"their timestamps do not form a usable common window, so this org pays the smaller of "
            f"the two measured durations, {h:.2f}h — the only figure neither record contradicts.")
    h = round((end - start).total_seconds() / 3600.0, 2)
    who_start = "the clock report" if (f_in and start == f_in and f_in != o_in) else "this platform's own clock"
    who_end = "the clock report" if (f_out and end == f_out and f_out != o_out) else "this platform's own clock"
    return h, CLOCKED_RECONCILED, (
        f"The two clock records disagree by {abs(o_h - f_h):.2f}h, beyond this org's {tol:.3f}h "
        f"tolerance, so this org pays the narrowest window they agree on: {h:.2f}h, starting at the "
        f"later clock-in ({who_start}'s) and ending at the earlier clock-out ({who_end}'s)"
        + (" — and this platform's own punch was closed automatically by the kiosk sweep, so its "
           "clock-out was never an observation of when anybody left" if auto else "")
        + f". The kiosk alone reads {o_h:.2f}h and the report alone reads {f_h:.2f}h; the time "
          f"outside the common window is treated as time with no work left to do.")


# ── THE RESOLUTION ───────────────────────────────────────────────────────────────────────────────
def pay_from_schedule(employee_row, cfg):
    """PURE: is THIS employee paid from the schedule when nothing was measured?

    The owner's button, resolved in one place: the employee's own
    `storeops.employees.pay_from_schedule` when it is set, else the org's
    `pay_from_schedule_default`. A NULL / absent column (a pre-1070 database) falls to the org
    default, so an un-applied migration is byte-identical to today rather than silently unpaying
    everybody — which is the direction a migration gap must always fail in on a pay path.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    v = (employee_row or {}).get("pay_from_schedule")
    if v is None:
        return bool(cfg.get("pay_from_schedule_default"))
    return bool(v)


def resolve_day(cell, cfg, employee_row=None):
    """PURE: `(hours, basis, detail)` for ONE (employee, business date).

    `cell` is one value out of `build_evidence`; `{}` / None means "no shift, no punch, no
    correction", which resolves to NOT_MEASURED_UNPAID with 0 hours — absence is never a zero
    measurement.

    PRECEDENCE: manager override > correction > the two clock records under this org's declared
    rule > a usable closed punch alone > measured zero > schedule opt-in > unpaid.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    cell = cell if isinstance(cell, dict) else {}
    scheduled = _f(cell.get("scheduled"))
    punch_h, punch_rows, unusable = _apply_punch_policy(cell, cfg)
    feed_rows = list(cell.get("_feed_punches") or ())
    feed_h = round(sum(_f(r.get("hours")) for r in feed_rows), 4) if feed_rows else None
    detail = {"scheduled_hours": round(scheduled, 2),
              # The FACT, not the policy: see `auto_closed_punches`. The variance board reads this.
              "auto_closed_punches": auto_closed_punches(cell),
              "feed_hours": feed_h, "feed_rows": len(feed_rows),
              "punch_hours": round(punch_h, 2), "punch_rows": punch_rows,
              "unusable_punches": list(unusable),
              "manual_rows": int(cell.get("manual_rows") or 0),
              "shift_actual_rows": int(cell.get("shift_actual_rows") or 0),
              "mode": cfg["hours_basis_mode"]}

    def out(hours, basis):
        detail["basis_reason"] = basis_reason(basis)
        detail["pays"] = basis_pays_hours(basis)
        detail["measured"] = basis_is_measurement(basis)
        return round(float(hours), 2), basis, detail

    # 0. A MANAGER OVERRIDE OUT-RANKS EVERYTHING, including the declared rule. Somebody genuinely
    #    does closing duties, a cash count, or waits for a DM after the point-of-sale clock is shut,
    #    and those are payable hours no clock record can see. The rule is the default; this is the
    #    escape hatch, it carries a reason, and `payroll_change_log` audits it.
    ov = cell.get("_override")
    if isinstance(ov, dict) and ov.get("hours") is not None:
        detail["override_reason"] = str(ov.get("reason") or "").strip() or None
        detail["override_by"] = ov.get("by")
        detail["rule_reason"] = (
            "A manager set this day's payable hours by hand"
            + (f": {detail['override_reason']}" if detail["override_reason"] else "")
            + ". An override always out-ranks the clock-record rule.")
        return out(_f(ov.get("hours")), CLOCKED_OVERRIDE)

    # 1. A HUMAN CORRECTION. `manual_hours` presence is the measurement (a correction may be
    #    negative), and a DM's `shifts.actual_hours > 0` edit is the older spelling of the same act.
    if cfg.get("count_manual_hours") and int(cell.get("manual_rows") or 0) > 0:
        return out(_f(cell.get("manual")), CLOCKED_CORRECTED)
    if _f(cell.get("shift_actual")) > 0:
        return out(_f(cell.get("shift_actual")), CLOCKED_CORRECTED)

    # 2. THE TWO CLOCK RECORDS. When the clock report is a PAY SOURCE for this org, the declared
    #    rule decides between it and our own punch — and the sentence explaining the choice travels
    #    with the number (owner ruling 2026-10-10: "mention on the report why you are doing so").
    if cfg.get("clock_feed_is_pay_source") and (feed_rows or punch_rows > 0):
        ours = None
        if punch_rows > 0:
            usable = [t for t in (cell.get("_raw_punches") or ()) if punch_usability(t, cfg)[0]]
            if usable:
                first = min(usable, key=lambda t: str(t.get("clock_in") or ""))
                last = max(usable, key=lambda t: str(t.get("clock_out") or ""))
                ours = {"clock_in": first.get("clock_in"), "clock_out": last.get("clock_out"),
                        "hours": punch_h,
                        "auto_clocked_out": any(t.get("auto_clocked_out") is True for t in usable)}
        feed = None
        if feed_rows:
            f_first = min(feed_rows, key=lambda r: str(r.get("clock_in") or ""))
            f_last = max(feed_rows, key=lambda r: str(r.get("clock_out") or ""))
            feed = {"clock_in": f_first.get("clock_in"), "clock_out": f_last.get("clock_out"),
                    "hours": feed_h}
        hours, basis, reason = reconcile_clock_records(ours, feed, cfg)
        detail["rule_reason"] = reason
        detail["clock_source_rule"] = cfg["clock_source_rule"]
        detail["ours_hours"] = None if ours is None else round(_f(ours.get("hours")), 2)
        if hours is not None:
            if basis is None:
                basis = CLOCKED if _f(hours) > 0 else MEASURED_ZERO
            elif _f(hours) == 0:
                basis = MEASURED_ZERO
            return out(hours, basis)

    # 3. A USABLE CLOSED PUNCH on its own. Zero usable hours off a usable punch is a measured zero.
    if punch_rows > 0:
        return out(punch_h, CLOCKED if punch_h > 0 else MEASURED_ZERO)

    # 4. A punch existed but this org does not count it. NOT a measurement, and not silently paid.
    if unusable:
        if pay_from_schedule(employee_row, cfg) and scheduled > 0:
            # Reported AND paid from the schedule, because that is what the opt-in means — but the
            # unusable punch travels in `detail` so it is never invisible.
            return out(scheduled, SCHEDULE_OPT_IN)
        return out(0.0, PUNCH_UNUSABLE)

    # 5. Nothing measured. The owner's button decides, per employee, with an org default.
    if pay_from_schedule(employee_row, cfg):
        return out(scheduled, SCHEDULE_OPT_IN)
    return out(0.0, NOT_MEASURED_UNPAID)


def resolve_shift_row(shift, cfg, employee_row=None, cell=None):
    """PURE: `(hours_this_shift_row_contributes, basis, detail)`.

    THE ONE REPLACEMENT for `hrs = actual_hours or scheduled_hours`. A caller holding only shift
    rows (`account/coa.derive_wage_cells`, `hr/router`, `pto_accrual`) passes no `cell` and gets
    that expression's exact arithmetic back, now carrying its basis: `actual_hours > 0` reads
    `clocked_corrected`, everything else reads `schedule_opt_in` under the shipped default.

    A caller that DOES hold punches passes the day's `cell` from `build_evidence`, and the shift row
    then contributes 0 for a day the punch already measured — the punch is counted at the store the
    person actually stood in, which is `router._shift_actual_contribution`'s long-standing rule and
    is preserved here rather than restated there.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    if cell is None:
        cell = {"scheduled": _f((shift or {}).get("scheduled_hours")),
                "shift_actual": max(0.0, _f((shift or {}).get("actual_hours"))),
                "shift_actual_rows": 1 if _f((shift or {}).get("actual_hours")) > 0 else 0}
        return resolve_day(cell, cfg, employee_row)
    hours, basis, detail = resolve_day(cell, cfg, employee_row)
    if basis in (CLOCKED, MEASURED_ZERO):
        # The punch is this day's answer and is counted from the punch row itself. This shift row
        # adds nothing — but it still reports the basis, so the reader is never shown a bare 0.
        d = dict(detail)
        d["shift_contributes_zero_because"] = "a punch measured this day and is counted at its own store"
        return 0.0, basis, d
    if basis == CLOCKED_CORRECTED:
        # This ROW's own correction, never the day's sum — a day with two corrected shift rows must
        # contribute each row once, which is `router._shift_actual_contribution`'s `return act`.
        return _f((shift or {}).get("actual_hours")), basis, detail
    # SCHEDULE_OPT_IN / NOT_MEASURED_UNPAID / PUNCH_UNUSABLE: this row's own scheduled hours.
    if basis == SCHEDULE_OPT_IN:
        return _f((shift or {}).get("scheduled_hours")), basis, detail
    return 0.0, basis, detail


def punch_counts_for_pay(punch, cell, cfg):
    """PURE: does THIS closed punch contribute its hours to pay?

    It counts iff it is usable AND no correction covers its day (a correction always wins). This is
    `router._punch_counts_for_pay`'s rule, moved here so the precedence is stated once.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    ok, _why = punch_usability(punch, cfg)
    if not ok:
        return False
    cell = cell if isinstance(cell, dict) else {}
    if cfg.get("count_manual_hours") and int(cell.get("manual_rows") or 0) > 0:
        return False
    return not _f(cell.get("shift_actual")) > 0


# ── THE MEASUREMENT QUESTION, asked on its own ───────────────────────────────────────────────────
#: "Nothing measured this day" — the verdict `salary_expense`'s three-state rollup asks for. It is a
#: MEASUREMENT verdict, not a pay BASIS: the pay basis for an unmeasured day is `schedule_opt_in` or
#: `not_measured_unpaid` depending on the org's and the employee's config, and that decision belongs
#: to `resolve_day`, not to this function.
MEASUREMENT_NONE = "not_measured"


def measurement_state(cell, cfg):
    """PURE: `(state, measured_hours)` for one (employee, day) — the MEASUREMENT only.

    `state` is `CLOCKED`, `CLOCKED_CORRECTED`, `MEASURED_ZERO` or `MEASUREMENT_NONE`, and the
    precedence is `resolve_day`'s own, because it IS `resolve_day` — this function is the same
    resolution asked without the pay question, so the two can never disagree about whether a day was
    measured. `storeops/salary_expense` (the three-state salary→expenses rollup, §14s) and
    `marketing/router`'s event payroll both dereference this instead of each deciding again.
    """
    cfg = dict(cfg if isinstance(cfg, dict) else resolve_config(None))
    # A HUMAN CORRECTION IS ALWAYS A MEASUREMENT, whatever the pay mode says. `resolve_config`
    # forces `count_manual_hours` off in schedule-fallback mode so that applying this change moves
    # nobody's PAY — but "was this day measured" is a different question from "is this correction
    # paid", and answering the first one with the second one's switch would make a corrected day
    # look unmeasured and send the salary rollup back to the schedule.
    cfg["count_manual_hours"] = True
    hours, basis, _detail = resolve_day(cell, cfg, None)
    if basis_is_measurement(basis):
        return basis, round(float(hours), 2)
    return MEASUREMENT_NONE, 0.0


# ── COVERAGE + THE INTERLOCK ─────────────────────────────────────────────────────────────────────
def coverage(evidence, cfg, employee_rows=None):
    """PURE: how much of this period was actually MEASURED, per store-agnostic employee-day grain.

    Returns `{scheduled_days, measured_days, measured_pct, unmeasured_days, unusable_punch_days,
    bases}`. `scheduled_days` counts employee-days that carry a schedule — the denominator a reader
    means by "did everybody's day get clocked", and the one the owner's Monday correction list works
    against. A day with a punch but no schedule still counts as measured and is reported in `bases`.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    emp_by_id = {(e or {}).get("employee_id"): e for e in (employee_rows or ()) if (e or {}).get("employee_id")}
    sched_days = 0
    measured_sched_days = 0
    unmeasured = []
    unusable_days = []
    bases = {}
    for (eid, day), cell in sorted((evidence or {}).items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        if not isinstance(cell, dict):
            continue
        _h, basis, detail = resolve_day(cell, cfg, emp_by_id.get(eid))
        bases[basis] = bases.get(basis, 0) + 1
        has_schedule = _f(cell.get("scheduled")) > 0
        if has_schedule:
            sched_days += 1
            if basis_is_measurement(basis):
                measured_sched_days += 1
            else:
                unmeasured.append({"employee_id": eid, "date": day, "basis": basis,
                                   "scheduled_hours": detail["scheduled_hours"]})
        if detail["unusable_punches"]:
            unusable_days.append({"employee_id": eid, "date": day,
                                  "reasons": detail["unusable_punches"]})
    pct = (measured_sched_days / sched_days * 100.0) if sched_days else 0.0
    return {"scheduled_days": sched_days, "measured_days": measured_sched_days,
            "measured_pct": round(pct, 2), "unmeasured_days": unmeasured,
            "unusable_punch_days": unusable_days, "bases": bases}


def interlock(cov, cfg):
    """PURE: may a payroll run compute on a CLOCKED-ONLY basis over this period's evidence?

    Returns `{ok, refused, reason, measured_pct, threshold, mode}`.

    THE POINT. Clocked-only over a period nobody clocked does not produce a small payroll, it
    produces a WRONG one — and on this estate partial coverage is the common case, not the edge
    case (72–96% in the months that have punches at all, 0% in every month before July 2026). So
    the test is a declared COVERAGE THRESHOLD (`min_punch_coverage_pct`, config, not a literal),
    not "are there any punches". Below it, the period is REPORTED as a data defect and the run
    stops: CLAUDE.md — a defect found in live data is reported, never fixed by writing code that
    hides it. Paying the shortfall as zero would be exactly that code.

    In MODE_SCHEDULE_FALLBACK the interlock never refuses — there is nothing to protect, because an
    unmeasured day is paid from the schedule by design in that mode.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    cov = cov if isinstance(cov, dict) else {}
    mode = cfg["hours_basis_mode"]
    pct = float(cov.get("measured_pct") or 0.0)
    thr = float(cfg["min_punch_coverage_pct"])
    base = {"measured_pct": pct, "threshold": thr, "mode": mode,
            "scheduled_days": int(cov.get("scheduled_days") or 0),
            "measured_days": int(cov.get("measured_days") or 0)}
    if mode != MODE_CLOCKED_ONLY:
        return {**base, "ok": True, "refused": False,
                "reason": "This org still pays an unmeasured day from the schedule, so there is no "
                          "clocked-only basis to protect."}
    if int(cov.get("scheduled_days") or 0) == 0:
        return {**base, "ok": True, "refused": False,
                "reason": "No scheduled employee-days in this period — nothing to measure."}
    if pct >= thr:
        return {**base, "ok": True, "refused": False,
                "reason": f"{pct:.2f}% of scheduled employee-days were measured, at or above the "
                          f"{thr:.2f}% this org requires."}
    reason = (f"Only {pct:.2f}% of this period's {base['scheduled_days']} scheduled employee-days "
              f"carry a usable measurement — below the {thr:.2f}% this org requires for a "
              f"clocked-only payroll run. {len(cov.get('unmeasured_days') or [])} employee-days "
              f"have no punch and no correction. Running now would pay those days nothing; that is "
              f"a missing-clock-data defect to fix, not a payroll figure. The uncorrected days are "
              f"listed rather than paid as zero.")
    return {**base, "ok": not bool(cfg["interlock_refuses"]),
            "refused": bool(cfg["interlock_refuses"]), "reason": reason}


# ── THE AUDIT ROW ────────────────────────────────────────────────────────────────────────────────
#: `storeops.payroll_change_log.entry_point` for a flip of the per-employee tick box, so the owner's
#: "button checked against that employee name" is audited by the log that already audits pay edits.
CHANGE_ENTRY_POINT = "pay_from_schedule_change"
CHANGE_FIELD = "pay_from_schedule"


# ── THE ONE IMPURE FUNCTION (the `carrier_dollar_class.load_config` convention) ──────────────────
#: Cached per org for the life of the process: a config row is read on every payroll surface and
#: effectively never changes at runtime. `invalidate_config` is called by the writer.
_CONFIG_CACHE = {}
CONFIG_TABLE = "payroll_hours_basis_config"


def load_config(client, org_id, *, use_cache=True):
    """The per-org row merged over the house defaults. THE one reader of `CONFIG_TABLE`.

    Every failure mode — no row, no table (mig 1070 not applied), an unreachable database — returns
    the house defaults, which are byte-identical to today's behaviour. A pay path must never change
    its arithmetic because a config read failed, and it must never fail CLOSED into paying nobody.
    `client` is already schema-scoped to `storeops` by the caller (`storeops.router.sb()`).
    """
    key = str(org_id)
    if use_cache and key in _CONFIG_CACHE:
        return _CONFIG_CACHE[key]
    row = None
    try:
        rows = (client.table(CONFIG_TABLE).select("*").eq("org_id", org_id).limit(1).execute().data) or []
        row = rows[0] if rows else None
    except Exception:
        row = None
    cfg = resolve_config(row)
    cfg["config_row_present"] = row is not None
    if use_cache:
        _CONFIG_CACHE[key] = cfg
    return cfg


def invalidate_config(org_id=None):
    """Drop the cached config for one org (or all of them). Called by the config writer."""
    if org_id is None:
        _CONFIG_CACHE.clear()
    else:
        _CONFIG_CACHE.pop(str(org_id), None)
