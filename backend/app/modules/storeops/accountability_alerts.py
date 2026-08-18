"""Accountability — morning lateness alerts (owner directive 2026-08-18).

PURE email planning + HTML builders (no DB / FastAPI). The router feeds this the per-employee lateness
for the CURRENT PAY PERIOD (from accountability.aggregate) plus the resolved org hierarchy per store
(_managers_above_dm), and this decides WHO gets WHICH email:

  • MORNING SUMMARY  → every manager ABOVE the DM. One digest per manager listing, per store they
    oversee, each employee late this pay period with the DATES + the TIME they clocked in and how many
    times they've been late this pay period. Fires every morning (10:30 tenant-local, gated + deduped
    by the caller).
  • CAP (corrective action plan) → the IMMEDIATE DM, for every employee late THAT DAY. States how many
    times the employee has been late this pay period (owner: "Count this pay period") and gives the DM
    a short plan to communicate to the employee.

Framing matches the accountability lens: supportive coaching + a documented plan, never a threat.
"""

# ── tiny HTML helpers (no external template engine; matches the app's plain-HTML email style) ──────
def _esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fmt_time(iso):
    """'2026-08-03T13:20:00+00:00' -> '1:20 PM'. Best-effort; returns '' when unparseable/None. The
    stored clock times are already business-local ISO strings (per BUSINESS_TZ at punch time)."""
    if not iso:
        return ""
    try:
        from datetime import datetime
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return t.strftime("%-I:%M %p")
    except Exception:
        try:
            return str(iso)[11:16]
        except Exception:
            return ""


def _incident_rows_html(incidents):
    """A small table of a person's late/left-early incidents: date · clocked in · minutes late · left
    early. Only late/left-early incidents are passed in."""
    trs = []
    for it in incidents:
        d = _esc(it.get("work_date"))
        ci = _fmt_time(it.get("actual_clock_in"))
        co = _fmt_time(it.get("actual_clock_out"))
        late = f"{int(it.get('minutes_late') or 0)} min late" if it.get("late") else ""
        early = f"left {int(it.get('minutes_early') or 0)} min early" if it.get("left_early") else ""
        detail = " · ".join([x for x in (late, early) if x])
        clocked = f"in {ci}" if ci else ""
        if co and it.get("left_early"):
            clocked = (clocked + f", out {co}").strip(", ")
        trs.append(
            f"<tr><td style='padding:3px 10px 3px 0'>{d}</td>"
            f"<td style='padding:3px 10px 3px 0'>{_esc(clocked)}</td>"
            f"<td style='padding:3px 0;color:#b45309'>{_esc(detail)}</td></tr>")
    return ("<table style='font-size:13px;border-collapse:collapse;margin:4px 0 2px'>"
            "<tr style='color:#6b7280;text-align:left'><th style='padding:0 10px 4px 0'>Date</th>"
            "<th style='padding:0 10px 4px 0'>Clocked</th><th style='padding:0 4px 4px 0'></th></tr>"
            + "".join(trs) + "</table>")


def build_manager_summary(slot, period_label):
    """slot = {"name","email","stores": {store_code: [record,...]}}. record = {employee, late_count,
    incidents, ...}. Returns {"subject","html"} — the morning digest for one above-DM manager."""
    name = slot.get("name") or "there"
    blocks = []
    total_people = 0
    for store in sorted(slot.get("stores", {})):
        recs = slot["stores"][store]
        # one line per employee (dedupe by employee within a store)
        seen = {}
        for r in recs:
            seen[str(r.get("employee") or r.get("employee_id"))] = r
        people = sorted(seen.values(), key=lambda r: -(r.get("late_count") or 0))
        total_people += len(people)
        rows = []
        for r in people:
            rows.append(
                f"<div style='margin:10px 0 2px'><b>{_esc(r.get('employee'))}</b> — late "
                f"<b>{int(r.get('late_count') or 0)}×</b> this pay period</div>"
                + _incident_rows_html(r.get("incidents") or []))
        blocks.append(f"<h3 style='margin:16px 0 2px;font-size:15px'>Store {_esc(store)}</h3>"
                      + "".join(rows))
    html = (
        f"<p>Good morning {_esc(name)},</p>"
        f"<p>Lateness across the stores you oversee, this pay period ({_esc(period_label)}):</p>"
        + "".join(blocks) +
        "<p style='color:#6b7280;font-size:12px;margin-top:16px'>Automated accountability report — "
        "surfaced for coaching conversations, not penalties. Excused time off is never counted.</p>")
    subject = f"Lateness report — {total_people} employee(s) · {period_label}"
    return {"subject": subject, "html": html}


def build_cap_email(dm_name, rec, period_label):
    """A corrective-action-plan email for the DM about ONE employee late today. rec carries the
    employee, their pay-period late_count, and today's incident. Returns {"subject","html"}."""
    emp = rec.get("employee")
    n = int(rec.get("late_count") or 0)
    today_ci = _fmt_time((rec.get("today_incident") or {}).get("actual_clock_in"))
    today_min = int((rec.get("today_incident") or {}).get("minutes_late") or 0)
    today_line = (f"clocked in at <b>{_esc(today_ci)}</b>" + (f" ({today_min} min late)" if today_min else "")) \
        if today_ci else "clocked in late"
    html = (
        f"<p>Hi {_esc(dm_name or 'there')},</p>"
        f"<p><b>{_esc(emp)}</b> was late again today — {today_line}. That is "
        f"<b>{n} time(s)</b> late this pay period ({_esc(period_label)}).</p>"
        "<p>Please have a corrective-action conversation with them and communicate this plan:</p>"
        "<ol style='font-size:14px'>"
        f"<li>Acknowledge the pattern together: {_esc(emp)} has been late {n} time(s) this pay period.</li>"
        "<li>Ask what's driving it (commute, schedule fit, childcare) and problem-solve it.</li>"
        "<li>Set a clear, written expectation: on the clock by shift start, every shift.</li>"
        "<li>Agree a check-in date and confirm they understand the accountability step if it continues.</li>"
        "</ol>"
        + _incident_rows_html(rec.get("incidents") or []) +
        "<p style='color:#6b7280;font-size:12px;margin-top:14px'>This is a coaching + documentation "
        "step. Keep it supportive; you decide any formal action.</p>")
    subject = f"Corrective action needed — {emp} late {n}× this pay period"
    return {"subject": subject, "html": html}


def plan_emails(late_records, hierarchy_by_store, today, period_label):
    """Decide the emails to send. Inputs are already resolved by the caller:
      late_records: [{store_code, employee, employee_id, late_count, incidents,
                      late_today (bool), today_incident}]  — for the CURRENT pay period.
      hierarchy_by_store: {store_code: {"dm":[{name,email,...}], "above":[{name,email,...}]}}
    Returns {"summaries": [...], "caps": [...]} where each item is
      {"kind","to","to_name","subject","html","dedupe_key"}. Managers with no email are skipped."""
    by_store = {}
    for r in late_records:
        by_store.setdefault(r.get("store_code"), []).append(r)

    mgr = {}   # lower(email) -> {"name","email","stores": {store: [rec]}}
    caps = []
    for store, recs in by_store.items():
        h = hierarchy_by_store.get(store) or {"dm": [], "above": []}
        for m in h.get("above", []):
            em = (m.get("email") or "").strip()
            if not em:
                continue
            slot = mgr.setdefault(em.lower(), {"name": m.get("name") or em, "email": em, "stores": {}})
            slot["stores"].setdefault(store, []).extend(recs)
        dms = [d for d in h.get("dm", []) if (d.get("email") or "").strip()]
        for r in recs:
            if not r.get("late_today"):
                continue
            for d in dms:
                built = build_cap_email(d.get("name"), r, period_label)
                empkey = str(r.get("employee_id") or r.get("employee") or "").strip().upper()
                caps.append({"kind": "cap", "to": d["email"].strip(), "to_name": d.get("name"),
                             "subject": built["subject"], "html": built["html"],
                             "dedupe_key": f"lateness_cap|{today}|{d['email'].strip().lower()}|{empkey}"})

    summaries = []
    for slot in mgr.values():
        built = build_manager_summary(slot, period_label)
        summaries.append({"kind": "manager_summary", "to": slot["email"], "to_name": slot["name"],
                          "subject": built["subject"], "html": built["html"],
                          "dedupe_key": f"lateness_am|{today}|{slot['email'].lower()}"})
    return {"summaries": summaries, "caps": caps}


if __name__ == "__main__":
    recs = [
        {"store_code": "S1", "employee": "Dana", "employee_id": "E1", "late_count": 4,
         "late_today": True,
         "today_incident": {"actual_clock_in": "2026-08-18T13:12:00+00:00", "minutes_late": 12},
         "incidents": [
             {"work_date": "2026-08-04", "actual_clock_in": "2026-08-04T13:05:00+00:00", "minutes_late": 5, "late": True},
             {"work_date": "2026-08-18", "actual_clock_in": "2026-08-18T13:12:00+00:00", "minutes_late": 12, "late": True}]},
        {"store_code": "S1", "employee": "Evan", "employee_id": "E2", "late_count": 1, "late_today": False,
         "today_incident": None, "incidents": [
             {"work_date": "2026-08-02", "actual_clock_in": "2026-08-02T13:03:00+00:00", "minutes_late": 3, "late": True}]},
    ]
    hier = {"S1": {"dm": [{"name": "Dee DM", "email": "dee@x.com"}],
                   "above": [{"name": "Rita Regional", "email": "rita@x.com"},
                             {"name": "Vic VP", "email": "vic@x.com"}]}}
    plan = plan_emails(recs, hier, "2026-08-18", "Aug 15–28, 2026")
    assert len(plan["summaries"]) == 2, plan["summaries"]           # rita + vic (above the DM)
    assert {s["to"] for s in plan["summaries"]} == {"rita@x.com", "vic@x.com"}
    assert len(plan["caps"]) == 1 and plan["caps"][0]["to"] == "dee@x.com"   # only Dana late TODAY
    assert "4 time(s)" in plan["caps"][0]["html"] and "Dana" in plan["caps"][0]["subject"]
    assert plan["caps"][0]["dedupe_key"] == "lateness_cap|2026-08-18|dee@x.com|E1"
    assert all("Dana" in s["html"] for s in plan["summaries"])       # summary lists the late employee
    print("accountability_alerts self-test OK — summaries:",
          [(s["to"], s["subject"]) for s in plan["summaries"]], "caps:", [c["to"] for c in plan["caps"]])
