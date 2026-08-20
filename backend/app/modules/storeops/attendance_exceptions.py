"""Attendance Exceptions engine (mod-people, owner directive 2026-08-06, verbatim):
"time clock should show who were scheduled and didn't clock in and also if somebody else clocked in
instead of the scheduled".

Joins storeops.shifts (the SCHEDULE) against storeops.timelog (the PUNCHES) for the same
(org_id, work_date) window and classifies every shift/punch into one of:

  no_show            a scheduled shift where the SAME employee never punched (at any store) during the
                      shift's window, the shift's start + grace has already passed, and no one else
                      covered it either. THE HEADLINE ASK.
  covered_by_other   a scheduled shift where the SAME employee never punched, but >=1 OTHER employee's
                      punch overlaps the shift's window (`coverers`, plural — every match is listed,
                      never just the first). THE NAMED ASK ("if somebody else clocked in instead").
  unscheduled        a punch with no shift for that employee AT THAT STORE that work_date — the mirror
                      image of covered_by_other: the coverer's OWN punch is "unscheduled" from their own
                      point of view even though it just covered someone else's shift. Falls out of the
                      SAME join for free.
  late               the employee DID show up, but their first clock-in that day is more than
                      `late_grace_min` minutes after the shift's start.
  left_early         the employee DID show up and (fully) clocked out, but their last CLOSED clock-out
                      that day is more than `early_leave_grace_min` minutes before the shift's end. A
                      still-open punch is never judged left-early (they haven't left yet).

A shift that's fully covered, on time, and left on time contributes NOTHING to the output — this module
returns ONLY the gaps, per the owner's framing ("surface the GAPS between the schedule and reality").

EXCUSED (a label, not a 6th class): when a no_show/covered_by_other shift's employee has an
APPROVED storeops.time_off_requests row covering that work_date, the row still carries its real
`exception_type` (no_show / covered_by_other) plus `excused=True` + `excused_reason` (the time-off's
`type`/`notes`) so a filter can show/hide it without losing WHICH kind of gap it was. Tenant config
`timeoff_mode` decides the ONLY other behavior: 'label' (default — emit the row, excused=True) or
'suppress' (never emit an excused row at all). "A schedule that wasn't cleaned up after an approved
request is not an attendance problem" (owner correctness note) — this is the mechanism for that.

CORRECTNESS RULES THIS MODULE ENFORCES (read before changing ANY of these):

  1. TIMEZONE. Every timestamp comparison happens in AWARE UTC. A shift's wall-clock start/end
     ('HH:MM' + the shift's own business-local `shift_date') is combined into aware UTC via the
     CALLER-supplied `tz` (the router passes `_biz_tz_for(org_id)` — the exact same per-tenant
     timezone `_fmt_time`/`_biz_dt_utc` already use). storeops.timelog's clock_in/clock_out are already
     stored UTC (see router.py's BUSINESS_TZ notes) and are parsed as-is. Nothing here ever calls a bare
     `.astimezone()` with no tz argument, and nothing here is JS, so `new Date("YYYY-MM-DD")` is not
     reachable from this file (the frontend must use `parseLocalDate`, not this module).
  2. MULTI-SESSION / OPEN PUNCH. A day's coverage for one employee is the UNION of every punch that
     day, not a single row: a still-OPEN punch (no clock_out) is judged as if it stretches to `now_utc`
     (still here = not absent), and when several closed pairs exist the same day (a real lunch
     re-clock-in), the EARLIEST clock_in and the LATEST closed clock_out across all of them are what
     "late"/"left_early" are judged against (see harness's multi-session-same-day case).
  3. DON'T FLAG THE FUTURE. A shift with no matching punch is only eligible to become no_show /
     covered_by_other once `now_utc >= shift_start + noshow_grace_min`. Before that, the shift is
     entirely omitted (no row at all, not even a placeholder) — a shift later today, or tomorrow, is
     simply not evaluated yet.
  4. STORE MATCHING. Punches carry their OWN `store_code`, which may differ from the shift's
     `store_code` (the whole point of the multi-store clock-in system: home ∪ scheduled ∪ floater).
     - Self-coverage (did the SCHEDULED employee show up at all) matches at ANY store — a floater who
       legitimately clocked in somewhere else that overlaps the shift window is presence, not absence.
       `same_store=False` is reported on the row rather than the fact being silently dropped.
     - Coverage-by-other ALSO searches every store (not just the shift's own) for the identical
       "don't silently drop a cross-store match" reason — the majority of real covers ARE same-store,
       but a cross-store cover is reported (`same_store=False` per coverer) rather than lost.
     - Unscheduled matches SAME store only (`employee/store/work_date`, per spec) — this is what makes
       a coverer's own punch read as "unscheduled" even though they already have a shift elsewhere that
       day; two independently-true facts, not a contradiction.
  5. NEGATIVE/UNPARSEABLE INPUT NEVER CRASHES. A shift with no parseable start_time is skipped (can't
     classify it) rather than raising. An overnight shift (end_time earlier than start_time, e.g.
     22:00-06:00) has its end rolled to the next calendar day rather than treated as zero/negative length.

PURE (no DB, no network): `compute_attendance_exceptions(shifts, punches, time_off_rows, config, now_utc,
tz)` operates on already-fetched rows the caller provides, exactly like lunch_deduction.py's
`compute_lunch_deduction_from_rows` / salary_owed.py's engine functions. router.py owns every DB read,
org scoping, RBAC span filtering, and id-canonicalization (numeric-vs-business employee_id — see
payroll_identity.business_id_alias_map, applied by the caller BEFORE rows reach this module, exactly as
`_canonical_shift_employee_id` already does for shift writes) before calling in.

DEGRADE (AGENT_CONTRACT §5): unlike lunch_deduction's HARD off-switch (that feature changes pay, this
one only reads and reports), attendance exceptions is safe to run with CODE DEFAULTS the moment
migration 421 hasn't been applied yet — `get_tenant_attendance_config` try/excepts the tenant-config
read and falls back to `DEFAULT_CONFIG` on ANY failure (missing column, missing row, missing table),
never a 500, never a hard "unavailable" gate on the report itself.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DEFAULT_CONFIG = {
    "late_grace_min": 10,          # minutes after shift start before a clock-in counts as LATE
    "early_leave_grace_min": 10,   # minutes before shift end a clock-out counts as LEFT_EARLY
    "noshow_grace_min": 30,        # minutes after shift start before an un-punched shift becomes NO_SHOW
    "coverage_overlap_min": 15,    # tolerance padding a punch window must fall within to "cover" a shift
    "timeoff_mode": "label",       # 'label' (show EXCUSED, default) | 'suppress' (never emit those rows)
}

_TIMEOFF_MODES = ("label", "suppress")


def _parse_iso_utc(s):
    """Parse a stored ISO timestamp, always returning a TZ-AWARE UTC datetime (naive -> assumed UTC —
    a real storeops.timelog clock_in/clock_out is always tz-aware over PostgREST; this normalization
    only protects a hand-built fixture/future caller, same convention as lunch_deduction._parse_dt)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _fmt_local(dt, tz):
    """A UTC datetime → a friendly 'h:MM AM/PM' string in the STORE's zone, for display. The row also
    keeps the raw UTC ISO; a client that shows this pre-formatted string can't re-introduce a zone bug."""
    if not dt:
        return None
    try:
        return dt.astimezone(tz).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return None


def _parse_hhmm_local(date_str, hhmm, tz):
    """Combine a 'YYYY-MM-DD' shift_date + 'HH:MM' business-local wall time into an aware UTC datetime,
    or None on any parse failure — mirrors router.py's `_biz_dt_utc`, but takes an already-resolved
    `tz` (tzinfo) instead of an org_id, so this module never touches the database itself."""
    if not date_str or not hhmm:
        return None
    try:
        parts = str(hhmm).strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        naive = datetime.fromisoformat(str(date_str)[:10] + f"T{h:02d}:{m:02d}:00")
        return naive.replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        return None


def _norm_store(x):
    return str(x or "").strip().upper()


def _overlaps(a_start, a_end, b_start, b_end):
    """Half-open interval overlap test. Any missing bound => no overlap (never a crash)."""
    if not (a_start and a_end and b_start and b_end):
        return False
    return a_start < b_end and b_start < a_end


def resolve_config(overrides):
    """Merge a (possibly partial / possibly None) overrides dict onto DEFAULT_CONFIG, clamping every
    grace/overlap minute value to a sane non-negative int and `timeoff_mode` to a known value — so a
    corrupt/partial config row can never produce a negative grace window or an unrecognized mode."""
    cfg = dict(DEFAULT_CONFIG)
    overrides = overrides or {}
    for k in ("late_grace_min", "early_leave_grace_min", "noshow_grace_min", "coverage_overlap_min"):
        v = overrides.get(k)
        if v is not None:
            try:
                cfg[k] = max(0, int(v))
            except (TypeError, ValueError):
                pass
    mode = overrides.get("timeoff_mode")
    if mode and str(mode).strip().lower() in _TIMEOFF_MODES:
        cfg["timeoff_mode"] = str(mode).strip().lower()
    return cfg


def get_tenant_attendance_config(org_id, sb_client):
    """(config, available). `available=False` only signals "migration 421 hasn't run / columns
    missing" for the settings-page UI — `config` is ALWAYS a fully usable dict (DEFAULT_CONFIG when
    unavailable), unlike lunch_deduction's hard gate: this feature is read-only/reporting, never money,
    so it runs on sane defaults immediately rather than refusing to report anything pre-migration.

    Availability is judged by COLUMN PRESENCE on the fetched row (not just 'the select didn't raise') —
    same reasoning as lunch_deduction.get_tenant_lunch_config: a schemaless in-memory fake client (the
    harness's FakeClient) never raises for an unknown key, so a presence check is what correctly models
    "migration not yet run" against both a real Postgres AND the offline harness fixtures."""
    try:
        rows = (sb_client.table("tenants").select(
            "attendance_late_grace_min,attendance_early_leave_grace_min,attendance_noshow_grace_min,"
            "attendance_coverage_overlap_min,attendance_timeoff_mode")
            .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return resolve_config(None), False
    if not rows or "attendance_late_grace_min" not in rows[0]:
        return resolve_config(None), False
    t = rows[0]
    overrides = {
        "late_grace_min": t.get("attendance_late_grace_min"),
        "early_leave_grace_min": t.get("attendance_early_leave_grace_min"),
        "noshow_grace_min": t.get("attendance_noshow_grace_min"),
        "coverage_overlap_min": t.get("attendance_coverage_overlap_min"),
        "timeoff_mode": t.get("attendance_timeoff_mode"),
    }
    return resolve_config(overrides), True


def _excused_reason(time_off_rows_for_emp, work_date):
    """The first APPROVED time-off row covering `work_date`, or None. Caller pre-groups rows per
    employee (see `compute_attendance_exceptions`) so this stays a plain date-range scan."""
    for t in time_off_rows_for_emp or []:
        if str(t.get("status") or "").lower() != "approved":
            continue
        sd, ed = str(t.get("start_date") or "")[:10], str(t.get("end_date") or "")[:10]
        if sd and ed and sd <= work_date <= ed:
            label = str(t.get("type") or "").strip() or "Approved time off"
            note = str(t.get("notes") or "").strip()
            return f"{label} — {note}" if note else label
    return None


def compute_attendance_exceptions(shifts, punches, time_off_rows, config, now_utc, tz, store_tz=None):
    """PURE (no DB). shifts/punches/time_off_rows are already-fetched, already-org-scoped,
    already-id-canonicalized (business employee_id) rows for the SAME (org, date-range) window.

    `store_tz` (optional {store_code: tzinfo}) resolves each shift's LOCAL start/end in that STORE's
    OWN zone — so a Chicago (Central) store's "09:45" is 09:45 Central, not 09:45 in the tenant's
    default zone. Without it (or for a store not in the map) it falls back to `tz`, the tenant zone —
    byte-identical to the old single-zone behaviour for a single-timezone tenant. This is the same
    per-store zone the time clock itself uses (migration 851); the accountability path had never
    adopted it, so cross-timezone stores were evaluated + displayed in the wrong zone.

    shifts row keys used:    id, employee_id, employee_name, store_code, shift_date ('YYYY-MM-DD'),
                              start_time ('HH:MM'), end_time ('HH:MM'), is_deleted
    punches row keys used:   id, employee_id, employee_name, store_code, work_date ('YYYY-MM-DD'),
                              clock_in (ISO), clock_out (ISO or None)
    time_off_rows keys used: employee_id, start_date, end_date, status, type, notes

    Returns a flat list of exception dicts (JSON-safe plain values only) — see the module docstring
    for the exact shape per `exception_type`. Deliberately does NOT emit a row for a normal on-time,
    fully-covered shift or a normally-scheduled punch — only gaps."""
    cfg = resolve_config(config)
    late_grace = timedelta(minutes=cfg["late_grace_min"])
    early_grace = timedelta(minutes=cfg["early_leave_grace_min"])
    noshow_grace = timedelta(minutes=cfg["noshow_grace_min"])
    overlap_pad = timedelta(minutes=cfg["coverage_overlap_min"])
    timeoff_mode = cfg["timeoff_mode"]

    # ── index punches ────────────────────────────────────────────────────────────────────────────
    punches_by_emp_day = defaultdict(list)
    punches_by_day = defaultdict(list)
    for p in punches or []:
        eid = p.get("employee_id")
        wd = str(p.get("work_date") or "")[:10]
        if not eid or not wd:
            continue
        rec = dict(p)
        rec["_ci"] = _parse_iso_utc(p.get("clock_in"))
        rec["_co"] = _parse_iso_utc(p.get("clock_out"))
        rec["_open"] = rec["_co"] is None
        if rec["_ci"] is None:
            continue   # can't place an unparseable punch on the timeline — skip, never crash
        punches_by_emp_day[(str(eid), wd)].append(rec)
        punches_by_day[wd].append(rec)

    # ── index approved time off per employee ────────────────────────────────────────────────────
    timeoff_by_emp = defaultdict(list)
    for t in time_off_rows or []:
        eid = t.get("employee_id")
        if eid:
            timeoff_by_emp[str(eid)].append(t)

    out = []

    # ── pass 1: every shift -> no_show / covered_by_other / late / left_early (or nothing) ────────
    shifts_by_emp_day = defaultdict(list)   # for pass 2's unscheduled join
    for s in shifts or []:
        if s.get("is_deleted"):
            continue
        eid = str(s.get("employee_id") or "")
        wd = str(s.get("shift_date") or "")[:10]
        if not eid or not wd:
            continue
        store = s.get("store_code")
        stz = (store_tz or {}).get(store) or tz   # the STORE's own zone; tenant zone is the fallback
        shift_start = _parse_hhmm_local(wd, s.get("start_time"), stz)
        shift_end = _parse_hhmm_local(wd, s.get("end_time"), stz)
        if shift_end and shift_start and shift_end <= shift_start:
            shift_end = shift_end + timedelta(days=1)   # overnight shift crosses midnight
        shifts_by_emp_day[(eid, wd)].append((shift_start, shift_end, store))
        if shift_start is None:
            continue   # nothing to classify a shift against with no parseable start

        window_start = shift_start - overlap_pad
        window_end = (shift_end or shift_start) + overlap_pad

        # -- self coverage: this employee's OWN punches that day, at ANY store (rule 4) --
        own = punches_by_emp_day.get((eid, wd), [])
        own_matches = [p for p in own if _overlaps(p["_ci"], p["_co"] or now_utc, window_start, window_end)]

        if own_matches:
            first_in = min(p["_ci"] for p in own_matches)
            still_open = any(p["_open"] for p in own_matches)
            last_out = None if still_open else max(p["_co"] for p in own_matches)
            worked_store = next((p.get("store_code") for p in own_matches if p["_ci"] == first_in), own_matches[0].get("store_code"))
            same_store = _norm_store(worked_store) == _norm_store(store)
            is_late = bool(shift_start and first_in > shift_start + late_grace)
            is_early = bool(shift_end and last_out and last_out < shift_end - early_grace)
            if is_late or is_early:
                out.append({
                    "exception_type": "late_and_left_early" if (is_late and is_early) else ("late" if is_late else "left_early"),
                    "is_late": is_late, "is_left_early": is_early,
                    "shift_id": s.get("id"), "employee_id": eid, "employee_name": s.get("employee_name"),
                    "store_code": store, "worked_store_code": worked_store, "same_store": same_store,
                    "work_date": wd, "shift_start": s.get("start_time"), "shift_end": s.get("end_time"),
                    "actual_clock_in": first_in.isoformat() if first_in else None,
                    "actual_clock_out": last_out.isoformat() if last_out else None,
                    # Pre-formatted in the STORE's zone so the review/email show the real local time.
                    "actual_clock_in_local": _fmt_local(first_in, stz),
                    "actual_clock_out_local": _fmt_local(last_out, stz),
                    "minutes_late": max(0, round((first_in - shift_start).total_seconds() / 60)) if is_late else 0,
                    "minutes_early": max(0, round((shift_end - last_out).total_seconds() / 60)) if is_early else 0,
                    "excused": False, "excused_reason": None,
                })
            continue   # present (whether flagged late/early or not) -> never a no_show/covered_by_other

        # -- absent so far: don't flag the future (rule 3) --
        if now_utc < shift_start + noshow_grace:
            continue

        reason = _excused_reason(timeoff_by_emp.get(eid), wd)
        excused = reason is not None
        if excused and timeoff_mode == "suppress":
            continue

        # -- coverage by someone else: any OTHER employee's punch overlapping this window, any store,
        #    cross-store cover reported (not dropped) via `same_store` (rule 4) --
        coverers = [p for p in punches_by_day.get(wd, [])
                    if str(p.get("employee_id")) != eid and _overlaps(p["_ci"], p["_co"] or now_utc, window_start, window_end)]

        row = {
            "shift_id": s.get("id"), "employee_id": eid, "employee_name": s.get("employee_name"),
            "store_code": store, "work_date": wd,
            "shift_start": s.get("start_time"), "shift_end": s.get("end_time"),
            "excused": excused, "excused_reason": reason,
        }
        if coverers:
            row["exception_type"] = "covered_by_other"
            row["coverers"] = [{
                "employee_id": c.get("employee_id"), "employee_name": c.get("employee_name"),
                "store_code": c.get("store_code"), "same_store": _norm_store(c.get("store_code")) == _norm_store(store),
                "clock_in": c["_ci"].isoformat() if c["_ci"] else None,
                "clock_out": c["_co"].isoformat() if c["_co"] else None,
                "open": c["_open"],
            } for c in sorted(coverers, key=lambda c: c["_ci"] or now_utc)]
        else:
            row["exception_type"] = "no_show"
        out.append(row)

    # ── pass 2: every punch -> unscheduled when no SAME-STORE shift for that employee/day overlaps it
    #    (rule 4 — the mirror image of covered_by_other, "the same join from the other side") ──────
    for p in punches or []:
        eid = str(p.get("employee_id") or "")
        wd = str(p.get("work_date") or "")[:10]
        if not eid or not wd:
            continue
        p_ci = _parse_iso_utc(p.get("clock_in"))
        if p_ci is None:
            continue
        p_co = _parse_iso_utc(p.get("clock_out")) or now_utc
        pstore = p.get("store_code")
        candidates = shifts_by_emp_day.get((eid, wd), [])
        matched = any(
            st is not None and _norm_store(sc) == _norm_store(pstore)
            and _overlaps(p_ci, p_co, st - overlap_pad, (en or st) + overlap_pad)
            for st, en, sc in candidates
        )
        if matched:
            continue
        out.append({
            "exception_type": "unscheduled",
            "punch_id": p.get("id"), "employee_id": eid, "employee_name": p.get("employee_name"),
            "store_code": pstore, "work_date": wd,
            "clock_in": p.get("clock_in"), "clock_out": p.get("clock_out"), "open": p.get("clock_out") is None,
            "excused": False, "excused_reason": None,
        })

    return out
