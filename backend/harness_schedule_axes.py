"""DB-free proof for the ONE scheduled-hours fact: both axes, one derivation (index §STOREOPS-01).

Owner report 2026-10-12, verbatim: *"in the ups store schduleing teh one employee hajra sohail is
appearing on the store side and her other name is appearing on teh other employee side ... now the
schesuelomnt eh companyh side shows 204 hours and employee side show 164 fix from teh back end and
keep one hajra schdule is the same for both"*.

WHAT WAS MEASURED (live, read-only, 2026-10-12, NY LOGISTICS / UPS centre 7710):
  · ONE roster row — `E274`, `Hajra Sohail`. There is NO second employee record; the premise of two
    records is wrong, and §C below pins that as a fact rather than a reading.
  · 26 shift rows under TWO spellings — `Hajra Cheema` (21) and `Hajra Sohail` (5) — and EVERY ONE
    of them already carrying `employee_id = 'E274'`. The identity was never in doubt; the
    denormalized `employee_name` copy was.
  · The week of Mon 2026-10-12, in the state it was in at 19:26 UTC (after the five `Hajra Sohail`
    rows were added, before any were deleted): **208.0h by store, 164.0h by employee**. The
    employee figure is the owner's 164 to the hour. The store figure reconstructs to 208.0 against
    his "204"; `storeops.shifts` carries no `updated_at`, so a shift edited between 19:15 and 19:26
    leaves no trace and the exact store figure at his instant is not recoverable. The COMPOSITION of
    the gap is certain either way: it is exactly the `Hajra Cheema` rows, which §B proves.

THE CLASS. `shifts.employee_name` is a COPY of `employees.name` that `PATCH /storeops/employees/{id}`
does not maintain. The store axis grouped on `store_code` (a column) and kept every row; the employee
axis was an INNER JOIN of that copy against the roster's `name`. So renaming an employee orphaned
every shift they already had — counted by store, silently dropped by employee — and the next rename
for the next tenant would do it again.

THE FIX PROVEN HERE. `schedule_hours.scheduled_axes` builds both axes in one row walk over one
canonical employee key from `payroll_identity.build_shift_attributor`, and its contract is the
INVARIANT `sum(by_store) == sum(by_employee) == total` — a row that names nobody rides a visible
`__unattributed__` bucket instead of being dropped.

§E is a NEGATIVE CONTROL: it reproduces the old loss on the same fixture, so none of this can go
vacuous (the trap [[vacuous-check-trap]] names).
"""
import sys
sys.path.insert(0, ".")
from app.modules.storeops import schedule_hours as sh            # noqa: E402
from app.modules.storeops import payroll_identity as pi          # noqa: E402

PASS = 0
FAIL = []


def check(label, got, want):
    global PASS
    if got == want:
        PASS += 1
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


# ════════════════════════════════════════════════════════════════════════════════════════════════
# THE LIVE FIXTURE — the week of Mon 2026-10-12 at store 7710, exactly as measured.
# ════════════════════════════════════════════════════════════════════════════════════════════════
ROSTER_7710 = [
    {"id": 270, "employee_id": "E270", "name": "ss"},
    {"id": 271, "employee_id": "E271", "name": "Manan Ali"},
    {"id": 272, "employee_id": "E272", "name": "Ahmed"},
    {"id": 273, "employee_id": "E273", "name": "Mohammad Ansari"},
    {"id": 274, "employee_id": "E274", "name": "Hajra Sohail"},
    {"id": 275, "employee_id": "E275", "name": "Aiman"},
    {"id": 276, "employee_id": "E276", "name": "Zohaib Ali"},
]

#: (shift_id, date, stored employee_name, employee_id, start, end) — the live rows.
_LIVE_WEEK = [
    (9437, "2026-10-12", "Aiman", "E275", "12:00", "19:00"),
    (9436, "2026-10-12", "Hajra Cheema", "E274", "09:00", "17:00"),
    (9492, "2026-10-12", "Hajra Sohail", "E274", "09:00", "19:00"),
    (9438, "2026-10-12", "Manan Ali", "E271", "16:00", "19:00"),
    (9439, "2026-10-12", "Zohaib Ali", "E276", "10:00", "19:00"),
    (9441, "2026-10-13", "Aiman", "E275", "12:00", "18:00"),
    (9440, "2026-10-13", "Hajra Cheema", "E274", "09:00", "19:00"),
    (9493, "2026-10-13", "Hajra Sohail", "E274", "09:00", "19:00"),
    (9442, "2026-10-13", "Manan Ali", "E271", "16:00", "19:00"),
    (9443, "2026-10-13", "Zohaib Ali", "E276", "10:00", "16:00"),
    (9445, "2026-10-14", "Aiman", "E275", "12:00", "18:00"),
    (9444, "2026-10-14", "Hajra Cheema", "E274", "09:00", "19:00"),
    (9494, "2026-10-14", "Hajra Sohail", "E274", "09:00", "19:00"),
    (9446, "2026-10-14", "Manan Ali", "E271", "16:00", "19:00"),
    (9447, "2026-10-14", "Zohaib Ali", "E276", "10:00", "16:00"),
    (9451, "2026-10-15", "Ahmed", "E272", "10:00", "18:00"),
    (9449, "2026-10-15", "Aiman", "E275", "12:00", "19:00"),
    (9448, "2026-10-15", "Hajra Cheema", "E274", "09:00", "17:00"),
    (9495, "2026-10-15", "Hajra Sohail", "E274", "09:00", "19:00"),
    (9450, "2026-10-15", "Manan Ali", "E271", "16:00", "19:00"),
    (9456, "2026-10-16", "Aiman", "E275", "12:00", "18:00"),
    (9452, "2026-10-16", "Hajra Cheema", "E274", "09:00", "17:00"),
    (9496, "2026-10-16", "Hajra Sohail", "E274", "09:00", "17:00"),
    (9453, "2026-10-16", "Manan Ali", "E271", "16:00", "19:00"),
    (9454, "2026-10-16", "Mohammad Ansari", "E273", "09:00", "14:30"),
    (9455, "2026-10-16", "Zohaib Ali", "E276", "14:30", "19:00"),
    (9457, "2026-10-17", "Ahmed", "E272", "10:00", "18:00"),
    (9458, "2026-10-17", "Aiman", "E275", "10:00", "18:00"),
    (9459, "2026-10-18", "Ahmed", "E272", "10:00", "17:00"),
    (9460, "2026-10-18", "Mohammad Ansari", "E273", "10:00", "17:00"),
]

WEEK_7710 = [{"id": i, "shift_date": d, "employee_name": n, "employee_id": e,
              "store_code": "7710", "start_time": st, "end_time": en, "is_deleted": False}
             for (i, d, n, e, st, en) in _LIVE_WEEK]


def old_employee_axis_total(shifts, employees):
    """THE OLD DERIVATION, kept here on purpose as the negative control: the employee axis as an
    INNER JOIN of `shifts.employee_name` against the roster's `name`, which is what both the grid
    footer and the export did before §STOREOPS-01."""
    names = {str(e.get("name") or "") for e in employees}
    return round(sum(sh._shift_h(s) for s in shifts
                     if not s.get("is_deleted") and str(s.get("employee_name") or "") in names), 2)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the shift attributor — precedence, and what refuses to guess")
attr = pi.build_shift_attributor(ROSTER_7710)
a = attr("E274", "Hajra Cheema")
check("A1 a stale name does not change who the row is about", (a["key"], a["name"]), ("E274", "Hajra Sohail"))
check("A2 the basis is the id, not the name", a["basis"], pi.MATCH_EMPLOYEE_ID)
check("A3 it binds", a["binds"], True)
check("A4 the row's own spelling is carried, not discarded", a["shift_name"], "Hajra Cheema")
b = attr("", "Hajra Sohail")
check("A5 no id, one roster name → binds on our own copy", (b["key"], b["basis"]),
      ("E274", pi.MATCH_SHIFT_NAME))
c = attr("", "Nobody Here")
check("A6 a name nobody has names NOBODY, not the first hit", (c["key"], c["binds"]),
      (pi.UNATTRIBUTED_KEY, False))
check("A7 and it says why", c["basis"], pi.MATCH_UNMATCHED)
dup = pi.build_shift_attributor(ROSTER_7710 + [{"id": 290, "employee_id": "E290", "name": "Hajra Sohail"}])
amb = dup("", "Hajra Sohail")
check("A8 two employees, one name → ambiguous, never merged", (amb["key"], amb["basis"]),
      (pi.UNATTRIBUTED_KEY, pi.MATCH_NAME_AMBIGUOUS))
check("A9 an id still resolves even when the name is ambiguous", dup("E274", "Hajra Sohail")["key"], "E274")
# the numeric-pk path and its ambiguity guard (business_id_alias_map's own rule, re-proven here
# because the shift attributor is now a second caller of it)
check("A10 a numeric pk aliases to the business id", attr("274", "whatever")["key"], "E274")
clash = pi.build_shift_attributor([{"id": 274, "employee_id": "E274", "name": "A"},
                                   {"id": 999, "employee_id": "274", "name": "B"}])
check("A11 a numeric that is someone else's business id is NOT aliased",
      clash("274", "")["key"], "274")
# accent folding — `str.isalnum()` is True for 'í', the defect fold_accents exists for
acc = pi.build_shift_attributor([{"id": 1, "employee_id": "E1", "name": "Leslie Martinez"}])
check("A12 'Martínez, LESLIEE' is not a different person from 'Leslie Martinez'",
      acc("", "Martinez, Leslie")["key"], "E1")
check("A13 accents fold", acc("", "Martínez, Leslie")["key"], "E1")

# ════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE REGRESSION — the live 208-vs-164 week")
axes = sh.scheduled_axes(WEEK_7710, ROSTER_7710)
check("B1 store axis = the measured 208.0h", axes["by_store"], {"7710": 208.0})
check("B2 the OLD employee axis reproduces the owner's 164.0h",
      old_employee_axis_total(WEEK_7710, ROSTER_7710), 164.0)
check("B3 the gap was exactly 44.0h", round(208.0 - 164.0, 2), 44.0)
check("B4 the employee axis now totals 208.0h too",
      axes["invariant"]["employee_total"], 208.0)
check("B5 and the invariant SAYS they agree", axes["invariant"]["agrees"], True)
check("B6 total is the one number", axes["total"], 208.0)
e274 = [r for r in axes["by_employee"] if r["key"] == "E274"]
check("B7 there is exactly ONE Hajra bucket", len(e274), 1)
check("B8 she holds all 92.0h (44 under one spelling + 48 under the other)", e274[0]["hours"], 92.0)
check("B9 labelled with the ROSTER name", e274[0]["name"], "Hajra Sohail")
check("B10 both spellings are on the record", sorted(e274[0]["stored_names"]),
      ["Hajra Cheema", "Hajra Sohail"])
check("B11 nothing is unattributed in this week", axes["unattributed"]["hours"], 0.0)
div = axes["name_divergence"]
check("B12 the divergence is REPORTED, with its weight",
      [(d["roster_name"], d["stored_name"], d["rows"], d["hours"]) for d in div],
      [("Hajra Sohail", "Hajra Cheema", 5, 44.0)])
check("B13 every other employee is unaffected",
      {r["name"]: r["hours"] for r in axes["by_employee"] if r["key"] != "E274"},
      {"Aiman": 40.0, "Zohaib Ali": 25.5, "Ahmed": 23.0, "Manan Ali": 15.0,
       "Mohammad Ansari": 12.5})
check("B14 the per-employee hours still sum to the store total",
      round(sum(r["hours"] for r in axes["by_employee"]), 2), 208.0)

# ════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE PREMISE, CHECKED — one employee record, not two")
check("C1 only one roster row answers to either spelling",
      sorted({attr("", n)["key"] for n in ("Hajra Sohail", "Hajra Cheema")} - {pi.UNATTRIBUTED_KEY}),
      ["E274"])
check("C2 every live Hajra shift already carried E274",
      sorted({r[3] for r in _LIVE_WEEK if "Hajra" in r[2]}), ["E274"])
check("C3 so the hours were never split by IDENTITY, only by the name copy",
      round(sum(sh._shift_h(s) for s in WEEK_7710 if s["employee_id"] == "E274"), 2), 92.0)

# ════════════════════════════════════════════════════════════════════════════════════════════════
section("D. the invariant holds whatever the rows look like")
CASES = {
    "D1 nobody on the roster at all": ([
        {"employee_id": "E1", "employee_name": "A", "store_code": "S1", "shift_date": "2026-10-12",
         "start_time": "09:00", "end_time": "17:00"}], []),
    "D2 an overnight shift": ([
        {"employee_id": "E274", "employee_name": "Hajra Cheema", "store_code": "S1",
         "shift_date": "2026-10-12", "start_time": "18:00", "end_time": "00:30"}], ROSTER_7710),
    "D3 a zero-length row": ([
        {"employee_id": "E274", "employee_name": "x", "store_code": "S1",
         "shift_date": "2026-10-12", "start_time": "10:00", "end_time": "10:00"}], ROSTER_7710),
    "D4 a soft-deleted row": ([
        {"employee_id": "E274", "employee_name": "x", "store_code": "S1", "is_deleted": True,
         "shift_date": "2026-10-12", "start_time": "09:00", "end_time": "17:00"}], ROSTER_7710),
    "D5 no store code": ([
        {"employee_id": "E274", "employee_name": "x", "store_code": None,
         "shift_date": "2026-10-12", "start_time": "09:00", "end_time": "17:00"}], ROSTER_7710),
    "D6 one person across two stores": ([
        {"employee_id": "E274", "employee_name": "Hajra Cheema", "store_code": "S1",
         "shift_date": "2026-10-12", "start_time": "09:00", "end_time": "13:00"},
        {"employee_id": "E274", "employee_name": "Hajra Sohail", "store_code": "S2",
         "shift_date": "2026-10-12", "start_time": "14:00", "end_time": "19:00"}], ROSTER_7710),
    "D7 nobody matches anything": ([
        {"employee_id": "ZZZ", "employee_name": "Ghost", "store_code": "S1",
         "shift_date": "2026-10-12", "start_time": "09:00", "end_time": "17:00"}], ROSTER_7710),
    "D8 the live week": (WEEK_7710, ROSTER_7710),
    "D9 no rows at all": ([], ROSTER_7710),
}
for label, (rows, emps) in CASES.items():
    ax = sh.scheduled_axes(rows, emps)
    check(f"{label} — store total == employee total",
          (ax["invariant"]["agrees"], ax["invariant"]["store_total"]),
          (True, ax["invariant"]["employee_total"]))
    check(f"{label} — total is the store total", ax["total"], ax["invariant"]["store_total"])

ax1 = sh.scheduled_axes(*CASES["D1 nobody on the roster at all"])
check("D10 an empty roster puts the hours in the VISIBLE bucket, not in a hole",
      (ax1["unattributed"]["hours"], ax1["total"]), (8.0, 8.0))
ax7 = sh.scheduled_axes(*CASES["D7 nobody matches anything"])
check("D11 the unattributed bucket carries its reason",
      ax7["unattributed"]["rows"][0]["basis"], pi.MATCH_UNMATCHED)
check("D12 the unattributed bucket is LAST", ax7["by_employee"][-1]["key"], pi.UNATTRIBUTED_KEY)
ax6 = sh.scheduled_axes(*CASES["D6 one person across two stores"])
check("D13 one bucket, two stores inside it",
      (len(ax6["by_employee"]), ax6["by_employee"][0]["by_store"]),
      (1, {"S1": 4.0, "S2": 5.0}))

# ════════════════════════════════════════════════════════════════════════════════════════════════
section("E. NEGATIVE CONTROL — the old derivation still loses the hours")
# If this ever passes, the fixture has stopped reproducing the defect and §B proves nothing.
check("E1 the old name-join drops the 44h", old_employee_axis_total(WEEK_7710, ROSTER_7710) < 208.0, True)
check("E2 it drops EXACTLY the Hajra Cheema rows",
      round(208.0 - old_employee_axis_total(WEEK_7710, ROSTER_7710), 2),
      round(sum(sh._shift_h(s) for s in WEEK_7710 if s["employee_name"] == "Hajra Cheema"), 2))
check("E3 the old derivation's own invariant is BROKEN (that is the defect)",
      abs(208.0 - old_employee_axis_total(WEEK_7710, ROSTER_7710)) < 0.01, False)

section("F. NO-OP CONTROL — a roster whose names all agree is byte-identical")
clean = [dict(s, employee_name={"E270": "ss", "E271": "Manan Ali", "E272": "Ahmed",
                                "E273": "Mohammad Ansari", "E274": "Hajra Sohail",
                                "E275": "Aiman", "E276": "Zohaib Ali"}[s["employee_id"]])
         for s in WEEK_7710]
cax = sh.scheduled_axes(clean, ROSTER_7710)
check("F1 old and new agree when no name has drifted",
      old_employee_axis_total(clean, ROSTER_7710), cax["invariant"]["employee_total"])
check("F2 and nothing is reported as diverging", cax["name_divergence"], [])
check("F3 the total is unchanged by the fix", cax["total"], 208.0)

# ════════════════════════════════════════════════════════════════════════════════════════════════
section("G. the trend report reads the same identity")
tr = sh.hours_trend(WEEK_7710, anchor="2026-10-18", employees=ROSTER_7710, weeks=2, months=2)
wk = tr["weeks"][-1]
check("G1 by_employee is labelled with the roster name, once", wk["by_employee"].get("Hajra Sohail"), 92.0)
check("G2 the stale spelling is NOT a second row", "Hajra Cheema" in wk["by_employee"], False)
check("G3 the week total is the store total", wk["total"], 208.0)
check("G4 the report carries the identity evidence block",
      (tr["identity"]["invariant"]["agrees"], len(tr["identity"]["name_divergence"])), (True, 1))
check("G5 by_employee sums to by_store", round(sum(wk["by_employee"].values()), 2),
      round(sum(wk["by_store"].values()), 2))
# two different people with the same roster name must not merge into one display row
two_same = [{"id": 1, "employee_id": "E1", "name": "Sam Lee"}, {"id": 2, "employee_id": "E2", "name": "Sam Lee"}]
rows_same = [{"employee_id": "E1", "employee_name": "Sam Lee", "store_code": "S1",
              "shift_date": "2026-10-12", "start_time": "09:00", "end_time": "17:00"},
             {"employee_id": "E2", "employee_name": "Sam Lee", "store_code": "S1",
              "shift_date": "2026-10-12", "start_time": "09:00", "end_time": "13:00"}]
tr2 = sh.hours_trend(rows_same, anchor="2026-10-18", employees=two_same, weeks=2, months=2)
check("G6 two people, one name → two labelled rows, never one merged row",
      sorted(tr2["weeks"][-1]["by_employee"].items()), [("Sam Lee (E1)", 8.0), ("Sam Lee (E2)", 4.0)])
check("G7 and the axis still sums right",
      round(sum(tr2["weeks"][-1]["by_employee"].values()), 2), 12.0)

# ════════════════════════════════════════════════════════════════════════════════════════════════
section("H. name_divergences counts only what it is given")
dv = pi.name_divergences(
    [{"employee_id": "E274", "employee_name": "Hajra Cheema", "hours": 8},
     {"employee_id": "E274", "employee_name": "Hajra Cheema", "hours": 10},
     {"employee_id": "E274", "employee_name": "Hajra Sohail", "hours": 10},
     {"employee_id": "ZZZ", "employee_name": "Ghost", "hours": 5}],
    attr)
check("H1 one entry per (person, stale spelling)", len(dv), 1)
check("H2 with the rows and hours behind it", (dv[0]["rows"], dv[0]["hours"]), (2, 18.0))
check("H3 an unattributable row is not a 'divergence' — it is a different question",
      [d["stored_name"] for d in dv], ["Hajra Cheema"])
check("H4 a reordered spelling is NOT a divergence ('Sohail, Hajra')",
      pi.name_divergences([{"employee_id": "E274", "employee_name": "Sohail, Hajra", "hours": 8}], attr), [])

print(f"\n{'='*60}")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(f"FAIL: {f}")
print(f"\n{PASS} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
