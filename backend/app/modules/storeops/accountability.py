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
        e = emp.setdefault(k, {"employee": r.get("employee_name") or k, "late": 0, "no_show": 0,
                               "left_early": 0, "excused": 0})
        if r.get("excused"):
            e["excused"] += 1
            continue
        t = r.get("exception_type")
        if t == "late":
            e["late"] += 1
        elif t == "no_show":
            e["no_show"] += 1
        elif t == "left_early":
            e["left_early"] += 1

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
        rows.append({"employee": e["employee"], "total_shifts": total, "late": e["late"],
                     "no_show": e["no_show"], "left_early": e["left_early"], "excused": e["excused"],
                     "late_rate": round(late_rate, 2), "flags": flags})
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
    print("accountability self-test OK:", [(r["employee"], r["flags"]) for r in res["employees"]])
