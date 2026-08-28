"""Pure per-employee attendance-pattern aggregation + POSITIVE coaching recommendations (Accountability
lens B). No DB / FastAPI. Consumes the per-shift exception rows from attendance_exceptions plus a
per-employee scheduled-shift count, and surfaces PATTERNS a manager should COACH on.

Framing is deliberate and matches the lesson from the Andon Labs experiment: this flags patterns for a
human conversation — a supportive check-in — and never proposes discipline, penalty, or termination. The
manager decides everything.
"""

DEFAULT_THRESHOLDS = {
    "late_rate_flag": 0.25,        # ≥25% of shifts late → worth a punctuality conversation
    "no_show_flag": 2,             # ≥2 unexcused no-shows in the window
    "left_early_rate_flag": 0.25,  # ≥25% of shifts left-early
    "min_shifts": 5,               # need a minimum sample before flagging a pattern
}


def _key(name, emp_id):
    return (str(name or emp_id or "").strip().upper())


def aggregate(exception_rows, shift_counts, thresholds=None):
    """exception_rows: the per-shift rows from compute_attendance_exceptions (each has exception_type,
    employee_name/employee_id, excused). shift_counts: {NAME_UPPER: total scheduled shifts}. Returns
    per-employee pattern rows (worst first) + coaching recommendations."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    emp = {}
    for r in exception_rows:
        k = _key(r.get("employee_name"), r.get("employee_id"))
        if not k:
            continue
        e = emp.setdefault(k, {"employee": r.get("employee_name") or k, "employee_id": r.get("employee_id"),
                               "late": 0, "no_show": 0, "left_early": 0, "excused": 0, "incidents": []})
        if r.get("excused"):
            e["excused"] += 1
            continue
        t = r.get("exception_type")
        # Use the is_late/is_left_early flags so a combined 'late_and_left_early' row counts toward
        # BOTH (the old exact-string match on 'late'/'left_early' silently dropped it from either).
        is_late = bool(r.get("is_late")) or t == "late"
        is_early = bool(r.get("is_left_early")) or t == "left_early"
        if is_late:
            e["late"] += 1
        if is_early:
            e["left_early"] += 1
        if t == "no_show":
            e["no_show"] += 1
        # Per-incident detail so the report/email can name the DATES an employee was late and the TIME
        # they actually clocked in (or clocked out early). Only late/left-early incidents are captured.
        if is_late or is_early:
            e["incidents"].append({
                "work_date": r.get("work_date"), "store_code": r.get("store_code"),
                "late": is_late, "left_early": is_early,
                "actual_clock_in": r.get("actual_clock_in"), "actual_clock_out": r.get("actual_clock_out"),
                "actual_clock_in_local": r.get("actual_clock_in_local"),
                "actual_clock_out_local": r.get("actual_clock_out_local"),
                "minutes_late": r.get("minutes_late") or 0, "minutes_early": r.get("minutes_early") or 0,
            })

    rows = []
    for k, e in emp.items():
        total = int(shift_counts.get(k, 0)) or (e["late"] + e["no_show"] + e["left_early"] + e["excused"])
        late_rate = (e["late"] / total) if total else 0.0
        le_rate = (e["left_early"] / total) if total else 0.0
        flags = []
        if total >= th["min_shifts"]:
            if late_rate >= th["late_rate_flag"]:
                flags.append("punctuality")
            if e["no_show"] >= th["no_show_flag"]:
                flags.append("attendance")
            if le_rate >= th["left_early_rate_flag"]:
                flags.append("early_departure")
        incidents = sorted(e["incidents"], key=lambda x: str(x.get("work_date") or ""))
        rows.append({"employee": e["employee"], "employee_id": e.get("employee_id"),
                     "total_shifts": total, "late": e["late"],
                     "no_show": e["no_show"], "left_early": e["left_early"], "excused": e["excused"],
                     "late_rate": round(late_rate, 2), "flags": flags, "incidents": incidents})
    rows.sort(key=lambda x: (-len(x["flags"]), -x["late_rate"], -x["no_show"]))

    recs = []
    for r in rows:
        if not r["flags"]:
            continue
        parts = []
        if "punctuality" in r["flags"]:
            parts.append(f"late on {r['late']} of {r['total_shifts']} shifts ({int(r['late_rate'] * 100)}%)")
        if "attendance" in r["flags"]:
            parts.append(f"{r['no_show']} unexcused no-show(s)")
        if "early_departure" in r["flags"]:
            parts.append(f"left early {r['left_early']} time(s)")
        recs.append({"employee": r["employee"], "flags": r["flags"],
                     "text": f"Have a supportive check-in with {r['employee']}: {', '.join(parts)}. "
                             f"Review commute/schedule fit and set a clear expectation — a coaching conversation, not a penalty."})
    return {"employees": rows, "recommendations": recs, "thresholds": th}


if __name__ == "__main__":
    exc = (
        [{"exception_type": "late", "employee_name": "Dana"}] * 6 +
        [{"exception_type": "no_show", "employee_name": "Dana"}] +
        [{"exception_type": "no_show", "employee_name": "Dana", "excused": True}] +
        [{"exception_type": "late", "employee_name": "Evan"}] * 1 +
        [{"exception_type": "no_show", "employee_name": "Finn"}] * 3
    )
    counts = {"DANA": 20, "EVAN": 20, "FINN": 20}
    res = aggregate(exc, counts)
    by = {r["employee"]: r for r in res["employees"]}
    assert by["Dana"]["late"] == 6 and by["Dana"]["late_rate"] == 0.3 and "punctuality" in by["Dana"]["flags"]
    assert by["Dana"]["excused"] == 1 and by["Dana"]["no_show"] == 1   # excused not counted as no_show
    assert by["Finn"]["no_show"] == 3 and "attendance" in by["Finn"]["flags"]
    assert by["Evan"]["flags"] == []                                    # 1/20 late → below threshold
    assert res["employees"][0]["employee"] in ("Dana", "Finn")          # flagged first
    assert any(rc["employee"] == "Dana" for rc in res["recommendations"])

    # combined late_and_left_early counts toward BOTH late and left_early (was dropped from either),
    # and per-incident dates/times are captured for the report/email.
    exc2 = [
        {"exception_type": "late_and_left_early", "is_late": True, "is_left_early": True, "employee_name": "Gia",
         "work_date": "2026-08-03", "actual_clock_in": "2026-08-03T13:20:00+00:00",
         "actual_clock_out": "2026-08-03T21:40:00+00:00", "minutes_late": 20, "minutes_early": 20},
        {"exception_type": "late", "is_late": True, "employee_name": "Gia",
         "work_date": "2026-08-01", "actual_clock_in": "2026-08-01T13:10:00+00:00", "minutes_late": 10},
    ]
    r2 = {x["employee"]: x for x in aggregate(exc2, {"GIA": 8})["employees"]}["Gia"]
    assert r2["late"] == 2 and r2["left_early"] == 1, (r2["late"], r2["left_early"])
    assert len(r2["incidents"]) == 2 and r2["incidents"][0]["work_date"] == "2026-08-01"  # sorted by date
    assert r2["incidents"][0]["minutes_late"] == 10 and r2["incidents"][1]["left_early"] is True
    print("accountability self-test OK:", [(r["employee"], r["flags"]) for r in res["employees"]])
