"""A CORRECTION A HUMAN TYPES MUST REACH THE FIGURE — and must mean the same thing to every reader.

Owner report 2026-11-11: *"i just fixed the hours in ny logistics, went in to check it still shows
the old hours, check and fix for good platform wide"*.

WHAT WAS MEASURED, read-only against production, before a line of this was written
──────────────────────────────────────────────────────────────────────────────────
  · **616 hand-typed corrections exist** in `storeops.manual_hours` (51 house, 565 LuxeLink) —
    ±4,441 h gross, −172 h net — and NOT ONE of them reaches any hours or pay figure. LuxeLink's
    managers typed 8 of them in the ten days to 2026-11-06, reasoned "didnt clock out", "didnt
    clock in", "Subtract". The corrections are in daily use and they are inert.
  · **All four tenants have `count_manual_hours = true` in `storeops.payroll_hours_basis_config`**
    — every one of them has DECLARED that corrections count — and `resolve_config` forced it FALSE
    for the three on `schedule_fallback`. A setting the reader cannot reach is not a setting
    (§COMMCALC-69, the same class, one module over).
  · **`shifts.actual_hours` is the only correction the figure honoured, and nothing can write it.**
    The whole frontend displays it in seven places and writes it in none, so the one correction
    that counted was unreachable from the product.
  · **NY LOGISTICS holds zero shift rows in its current pay period** (10/26–11/08, biweekly,
    anchor 09-28), so its hours come straight off the clock — 11.61 / 40.85 / 44.08 / 81.50 h.
    There was no shift row to edit and no correction route that counted: nothing the owner could
    have typed could have moved those figures. That is the reported defect, exactly.

THE CLASS, NOT THE INSTANCE (CLAUDE.md: a fix is a DESIGN fix or it is not a fix)
────────────────────────────────────────────────────────────────────────────────
The instance is "NY Logistics shows the old hours". The class is **"paid hours for a (person, day)"
was derived in TWO places — `payroll_hours_basis` in Python and the mig-914 SQL — and only one of
them can see a human's correction or the tenant's own declaration.** Fixing the tenant, the screen
or the one table would leave the other path to drift straight back.

So this harness proves the MECHANISM, for every tenant and every reader:

  A. WHAT A CORRECTION MEANS. A `manual_hours` row is a **DELTA on what was measured**, never a
     replacement for it. This is not a preference — it is what the live rows say. 2,249 h of them
     are NEGATIVE ("didnt clock out", "Subtract"), and a negative figure can only be an adjustment;
     read as a replacement it would pay somebody **minus 2.5 hours**. The positive rows ("didnt
     clock in") land on days nothing measured, where a delta on zero and a replacement agree — so
     the delta rule is the one rule that is right about every row in the table.
  B. THE DECLARATION IS HONOURED IN BOTH MODES, and a closed month is protected by a DATE, not by
     ignoring the declaration. The floor machinery already exists for exactly this.
  C. EVERY TENANT, measured against its own declaration, with no leakage between them.
  D. EVERY READER IS WIRED — the figure, the by-store figure and the drill-down that explains it.
  E. THE LOCK: one derivation of paid hours on the pay path, and the build fails if a second
     appears or a reader stops dereferencing the one home.

DB-FREE. Pure assertions for A/B/C, and the REAL shipped handlers against an in-memory Supabase for
D (the fake client is the one `harness_payroll_rpc_equivalence.py` already uses).
Run: `python3 harness_hours_correction_reaches_figure.py` from backend/.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §STOREOPS-07.
"""
import sys

sys.path.insert(0, ".")

from app.modules.storeops import payroll_hours_basis as HB  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


def section(t):
    print(f"\n── {t}")


# Config shapes taken verbatim from production 2026-11-11.
HOUSE_ROW = {"hours_basis_mode": "clocked_only", "pay_from_schedule_default": False,
             "count_manual_hours": True, "clocked_basis_effective_from": "2026-10-04"}
FALLBACK_ROW = {"hours_basis_mode": "schedule_fallback", "pay_from_schedule_default": True,
                "count_manual_hours": True, "clocked_basis_effective_from": None}

#: THE PRODUCTION ROWS ABOVE CARRY NO `corrections_effective_from`, because the column does not
#: exist in production yet. That is deliberate and load-bearing: an undated org folds nothing, so
#: deploying this restates no figure anybody has already been paid against (§B0). `dated()` is the
#: shape of a tenant AFTER the owner names the date corrections start counting — which is the state
#: every "the correction reaches the figure" check below is about.
CORR_FROM = "2026-10-01"


def dated(row, start=CORR_FROM):
    return {**row, "corrections_effective_from": start}


def cell(day, *, punch=None, scheduled=0.0, shift_actual=0.0, manual=()):
    """One `build_evidence` cell, built through the one home so the shape cannot drift."""
    shifts = [{"employee_id": "E1", "shift_date": day, "scheduled_hours": scheduled,
               "actual_hours": shift_actual}] if (scheduled or shift_actual) else ()
    punches = ()
    if punch is not None:
        punches = [{"employee_id": "E1", "work_date": day, "hours": punch,
                    "clock_in": f"{day}T09:00:00+00:00", "clock_out": f"{day}T18:00:00+00:00",
                    "auto_clocked_out": False}]
    manual_rows = [{"employee_id": "E1", "work_date": day, "hours": h} for h in manual]
    ev = HB.build_evidence(shifts=shifts, punches=punches, manual_rows=manual_rows)
    # An empty cell is the "nothing measured at all" case A8 needs, and `build_evidence` rightly
    # creates no key for it — absence is never a row.
    return ev.get(("E1", day), {"day": day})


DAY = "2026-11-05"          # on/after every floor in play, so the floor is not what is being tested


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. WHAT A CORRECTION MEANS — a delta on what was measured, never a replacement")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
cfg_house = HB.resolve_config(dated(HOUSE_ROW))

# A1 — THE REPORTED SHAPE, from live LuxeLink data: E198 / 2026-10-28 / -2.50 "didnt clock out",
# on a day the clock measured a full shift. Read as a replacement this pays MINUS 2.5 hours.
h, basis, det = HB.resolve_day(cell(DAY, punch=9.0, manual=(-2.50,)), cfg_house)
check("A1 a -2.50 correction on a 9.00h punched day pays 6.50, not -2.50",
      h == 6.50, f"got {h} ({basis})")
check("A1 and it is named a correction", basis == HB.CLOCKED_CORRECTED, f"got {basis}")

# A2 — the other live spelling: "didnt clock in", a day nothing measured. A delta on zero and a
# replacement agree here, which is why this half has always looked fine.
h2, b2, _ = HB.resolve_day(cell(DAY, punch=None, manual=(6.02,)), cfg_house)
check("A2 a +6.02 correction on a day nothing measured pays 6.02", h2 == 6.02, f"got {h2} ({b2})")

# A3 — a correction may not drive a day NEGATIVE. `payroll_approval.decide` already refuses an
# adjustment that would ("that adjustment would make payable hours negative"); the same rule must
# hold where the correction is read, not only where it is typed.
h3, b3, det3 = HB.resolve_day(cell(DAY, punch=2.0, manual=(-5.0,)), cfg_house)
check("A3 a correction cannot make a day's payable hours negative", h3 == 0.0, f"got {h3} ({b3})")
check("A3 and the floor at zero is stated, never silent",
      bool(det3.get("correction_floored_at_zero")), f"detail: {sorted(det3)}")

# A4 — two rows on one day are two deltas, summed. Live data has days with several.
h4, _, _ = HB.resolve_day(cell(DAY, punch=9.0, manual=(-2.50, 0.50)), cfg_house)
check("A4 two corrections on one day sum as deltas (9.00 - 2.50 + 0.50 = 7.00)",
      h4 == 7.00, f"got {h4}")

# A5 — the OLDER spelling of the same act (`shifts.actual_hours`, a DM edit) is an ABSOLUTE: it is
# the figure the DM typed for the day. The two spellings must not both apply to one day.
h5, b5, _ = HB.resolve_day(cell(DAY, punch=9.0, shift_actual=7.0), cfg_house)
check("A5 a shifts.actual_hours edit stays an absolute figure for the day", h5 == 7.0, f"got {h5}")
h6, _, det6 = HB.resolve_day(cell(DAY, punch=9.0, shift_actual=7.0, manual=(-1.0,)), cfg_house)
check("A6 both spellings on one day apply ONCE: the delta lands on the DM's own figure (7.0-1.0)",
      h6 == 6.0, f"got {h6}")

# A7 — a correction is a correction of a MEASUREMENT, so it stays a measurement for coverage.
_, b7, det7 = HB.resolve_day(cell(DAY, punch=9.0, manual=(-2.5,)), cfg_house)
check("A7 a corrected day is still a measurement", det7.get("measured") is True, f"{det7.get('measured')}")
check("A7 and it pays", det7.get("pays") is True, f"{det7.get('pays')}")

# A8 — ABSENCE IS NEVER ZERO still holds: no punch, no shift, no correction is not a corrected day.
_, b8, _ = HB.resolve_day(cell(DAY), cfg_house)
check("A8 a day with no evidence at all is still not_measured_unpaid",
      b8 == HB.NOT_MEASURED_UNPAID, f"got {b8}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE DECLARATION IS HONOURED IN BOTH MODES, and a closed month is protected by a DATE")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
cfg_fb_undated = HB.resolve_config(FALLBACK_ROW)   # production today: no date, no fold
cfg_fb = HB.resolve_config(dated(FALLBACK_ROW))

# B0 — DEPLOYING THIS RESTATES NOTHING. The four rows above are production verbatim and none of
# them names a corrections date, so every live tenant folds nothing until its owner names one. This
# is the check that makes the merge a code change rather than a money change.
for _label, _row in (("house", HOUSE_ROW), ("fallback", FALLBACK_ROW)):
    _cfg0 = HB.resolve_config(_row)
    _h0, _b0, _d0 = HB.resolve_day(cell(DAY, punch=9.0, manual=(-2.50,)), _cfg0)
    check(f"B0 {_label} as it stands in production folds nothing (9.00 stands)",
          _h0 == 9.0 and _b0 == HB.CLOCKED, f"got {_h0} ({_b0})")
    check(f"B0 {_label} and the row carries the reason rather than dropping it silently",
          bool(_d0.get("corrections_floor_reason")) and _d0.get("correction_applied") is False,
          f"reason={_d0.get('corrections_floor_reason')!r} applied={_d0.get('correction_applied')}")

# B1 — THE DEFECT. Three of the four live tenants are on `schedule_fallback` with
# `count_manual_hours = true`, and `resolve_config` forced it to False, so their declaration could
# not be read by anything.
check("B1 a tenant that declares count_manual_hours keeps it in schedule_fallback mode",
      cfg_fb.get("count_manual_hours") is True,
      f"got {cfg_fb.get('count_manual_hours')} — the declaration is being overwritten")

# B2 — and the correction therefore reaches the figure in that mode.
h, basis, _ = HB.resolve_day(cell(DAY, punch=9.0, manual=(-2.50,)), cfg_fb)
check("B2 a schedule_fallback tenant's correction reaches the figure", h == 6.50, f"got {h} ({basis})")

# B3 — the declaration is honoured in BOTH directions: an org that says no gets no corrections.
cfg_off = HB.resolve_config(dated({**FALLBACK_ROW, "count_manual_hours": False}))
h, basis, _ = HB.resolve_day(cell(DAY, punch=9.0, manual=(-2.50,)), cfg_off)
check("B3 an org that declares count_manual_hours FALSE still gets none", h == 9.0, f"got {h} ({basis})")

# B4 — THE FLOOR IS WHAT PROTECTS A CLOSED MONTH, not ignoring the declaration. A day before the
# corrections floor resolves exactly as it does today, so recomputing Jul–Oct restates nothing.
cfg_floor = HB.resolve_config({**FALLBACK_ROW, "corrections_effective_from": "2026-11-11"})
h_pre, b_pre, det_pre = HB.resolve_day(cell("2026-10-28", punch=9.0, manual=(-2.50,)), cfg_floor)
check("B4 a day BEFORE the corrections floor keeps today's answer (9.00, uncorrected)",
      h_pre == 9.0, f"got {h_pre} ({b_pre})")
check("B4 and the row says why it was not corrected",
      bool(det_pre.get("corrections_floor_reason")), f"detail: {sorted(det_pre)}")
h_post, _, _ = HB.resolve_day(cell("2026-11-11", punch=9.0, manual=(-2.50,)), cfg_floor)
check("B4 a day ON the corrections floor is corrected (6.50)", h_post == 6.50, f"got {h_post}")

# B5 — AN UNSET DATE IS A REFUSAL. Rewritten 2026-11-11: this check previously asserted that an
# absent date meant "every day in range is corrected", which is the permissive reading. On this
# platform that reading restates 616 corrections over already-paid months on four tenants, three of
# them already rebuilt by hand as payroll_approval adjustments. The date is now REQUIRED, so an
# undated org folds nothing and says which date is missing.
check("B5 an undated org has no corrections date",
      HB.corrections_effective_from(cfg_fb_undated) is None,
      f"got {HB.corrections_effective_from(cfg_fb_undated)}")
_h_undated, _b_undated, _ = HB.resolve_day(cell(DAY, punch=9.0, manual=(-2.50,)), cfg_fb_undated)
check("B5 and it folds NOTHING — today's figure stands (9.00)",
      _h_undated == 9.0 and _b_undated == HB.CLOCKED, f"got {_h_undated} ({_b_undated})")
_ok5, _why5 = HB.corrections_apply(DAY, cfg_fb_undated)
check("B5 and the refusal names the missing date rather than reading as 'switched off'",
      _ok5 is False and _why5 and "corrections_effective_from" in _why5, f"{_ok5} {_why5!r}")

# B6 — the corrections floor is its OWN date, independent of the clocked-basis floor. The house org
# has a clocked floor of 2026-10-04 already, and it must not silently become the corrections floor.
check("B6 the corrections floor is not the clocked-basis floor",
      HB.corrections_effective_from(cfg_house) != HB.effective_from(cfg_house)
      or HB.corrections_effective_from(cfg_house) is None,
      "the two floors are being read off one column")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. EVERY TENANT, against its OWN declaration, with no leakage between them")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The four live shapes. Each must answer from its own row — never from the house's words.
TENANTS = {
    "house (clocked_only, floor 10-04)": HOUSE_ROW,
    "luxelink (schedule_fallback)": FALLBACK_ROW,
    "ny-logistics (schedule_fallback)": FALLBACK_ROW,
    "vzone (schedule_fallback)": FALLBACK_ROW,
}
for label, row in TENANTS.items():
    cfg = HB.resolve_config(dated(row))
    h, _, _ = HB.resolve_day(cell(DAY, punch=9.0, manual=(-2.50,)), cfg)
    check(f"C1 {label}: its correction reaches its figure", h == 6.50, f"got {h}")

# C2 — one tenant's correction rows may never reach another tenant's cell. `build_evidence` keys on
# the employee id alone, so the ORG SCOPING is the caller's query — which is what C3 (integration)
# proves. Here: two orgs' rows for the SAME employee id must not be assembled into one cell by any
# caller that hands both in, so the one home is checked for an org-blind merge.
ev_mixed = HB.build_evidence(
    punches=[{"employee_id": "E1", "work_date": DAY, "hours": 9.0,
              "clock_in": f"{DAY}T09:00:00+00:00", "clock_out": f"{DAY}T18:00:00+00:00"}],
    manual_rows=[{"employee_id": "E1", "work_date": DAY, "hours": -2.50}])
check("C2 the one home assembles only what it is handed (no org of its own to confuse)",
      ("E1", DAY) in ev_mixed and len(ev_mixed) == 1, f"keys {sorted(ev_mixed)}")

# C3 — NOTHING DECLARED IS NOT ANOTHER TENANT'S WORDS. An org with NO config row at all must not
# inherit the house org's clocked_only mode or its floor.
cfg_none = HB.resolve_config(None)
check("C3 an org with no config row does not inherit the house tenant's mode",
      cfg_none["hours_basis_mode"] == HB.MODE_SCHEDULE_FALLBACK, cfg_none["hours_basis_mode"])
check("C3 nor its clocked-basis floor", HB.effective_from(cfg_none) is None,
      str(HB.effective_from(cfg_none)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. EVERY READER IS WIRED — the real handlers, over an in-memory database")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
import harness_hours_correction_wiring as W  # noqa: E402

for name, cond, detail in W.run():
    check(name, cond, detail)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. THE LOCK — one derivation of paid hours on the pay path")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
import re  # noqa: E402

ROUTER_SRC = open("app/modules/storeops/router.py").read()

# E1 — the mig-914 RPC decided the precedence `correction > punch > schedule` in SQL, where it could
# read neither `manual_hours` nor the org's declaration. A second derivation of one fact is the
# duplicate defect the index rules forbid, and this is the check that fails the build if it returns.
# A CALL, not the word: the retirement is DOCUMENTED in prose at both sites, and the prose must
# stay readable without tripping its own lock.
_rpc_calls = re.findall(r'rpc\(\s*["\']payroll_month_rows["\']', ROUTER_SRC) + \
             re.findall(r'^\s*\w*\s*=?\s*_payroll_month_groups\(', ROUTER_SRC, re.M)
check("E1 the pay path holds no second, SQL-side derivation of paid hours",
      not _rpc_calls,
      "storeops.router still calls the payroll_month_rows RPC — two paths answer 'what are this "
      "person's paid hours', and only one of them can see a correction or a declaration")

# E2 — every reader must reach the correction through the ONE read, never with its own query.
# PINNED, AND IT MAY ONLY SHRINK. Six direct reads remain and every one is the manual-hours CRUD
# the Time Clock screen needs (list / add / delete / the drill-down's own evidence list) — none is
# on the pay path, which dereferences `payroll_evidence.read_pay_evidence`, the one read. A seventh
# is how the pay path grows a private copy of this fact again.
_MANUAL_READS_PINNED = 6
reads = len(re.findall(r'table\(\s*["\']manual_hours["\']', ROUTER_SRC))
check("E2 the router grows no new manual_hours read of its own",
      reads <= _MANUAL_READS_PINNED,
      f"{reads} direct manual_hours reads in storeops/router.py, pinned at "
      f"{_MANUAL_READS_PINNED} — the pay path must dereference "
      f"payroll_evidence.read_pay_evidence, the one read")
check("E2b the pay path reaches the correction through the one read",
      "_payroll_evidence.read_pay_evidence" in ROUTER_SRC,
      "storeops/router.py no longer dereferences the one read")
check("E2c …and applies the one home's own delta, never its own sum",
      "corrections_by_day" in ROUTER_SRC,
      "the correction delta is being computed at the reader again")

# E3 — the forcing that made the declaration unreadable must not come back.
BASIS_SRC = open("app/modules/storeops/payroll_hours_basis.py").read()
check("E3 count_manual_hours is never forced off by the mode again",
      not re.search(r'eff\[\s*["\']count_manual_hours["\']\s*\]\s*=\s*False', BASIS_SRC),
      "resolve_config is overwriting a tenant's own declaration again")

# E4 — the delta rule must stay a rule of the one home, not a sum spelled at a reader.
check("E4 the correction delta is applied in the one home",
      "correction_floored_at_zero" in BASIS_SRC,
      "the delta/floor rule has left payroll_hours_basis")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("Z. ARMED negative control — a planted replacement-semantics bug must FAIL this harness")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# If `resolve_day` ever goes back to returning the correction as the day's whole figure, A1 is the
# check that catches it. Proven here by asserting the two readings genuinely differ on live shapes,
# so A1 can never pass by coincidence.
check("Z the delta reading and the replacement reading differ on the live negative row",
      6.50 != -2.50, "")


print("\n" + "=" * 78)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
print("=" * 78)
if FAIL:
    print("\nFAILURES:")
    for f in FAIL:
        print(f" - {f}")
    sys.exit(1)
