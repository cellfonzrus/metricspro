"""CLOCK-IN SELF-CHECK — assemble every trace an employee's clock-in attempt would have left.

Owner 2026-08-11: _"create a self check module for the failed clockin attempts to verify if the employee
is lying or actual issue was there."_

WHAT THIS ANSWERS, AND WHAT IT REFUSES TO ANSWER
────────────────────────────────────────────────
It answers: *what does the system actually hold for this person, on this day?* — the punch, the failed
attempts, the schedule, whether they were provably at the store doing other work, and whether the kiosk
was working for everybody else at that store at that hour.

It does NOT return a verdict on honesty, and that is deliberate, not squeamishness. **A clock-in that
fails on the DEVICE never reaches the server and therefore leaves no row anywhere in this database** —
the app not opening, the login failing, a dead network, a camera permission denied, a phone left at home.
`core.failure_log` only records attempts that got far enough to be REFUSED by the server (a face mismatch
or a location/schedule block). So:

    NO RECORD  ≠  DID NOT TRY.        NO RECORD  =  the system has nothing to say.

A module that scored "no record" as "lying" would be a confident random-number generator pointed at
people's pay. Each day is therefore classified by what the EVIDENCE does — `supports` the claim,
`weakens` it, or is `inconclusive` — and the caller (a human) decides.

THE ONE PIECE OF REAL DISCRIMINATING EVIDENCE
─────────────────────────────────────────────
Whether OTHER people clocked in successfully at the SAME store on the SAME day. If nobody could, a
claim of "app issues" is corroborated by the store's own data. If eleven colleagues clocked in either
side of the disputed hour, the claim is weakened — still not disproven (their phone, their face, their
enrolment), but weakened, and that is an honest thing to say.

Second-order but strong: the employee was demonstrably AT WORK — they rang sales or filed the daily
closing — while the clock holds nothing. That is positive proof of presence with a missing punch, which
is the single most common real outcome and is worth separating from "no trace at all".

MEASURED WHILE BUILDING THIS (Luxelink, 2026-07-23 → 08-05):
  • Janet Garibay — ONE punch, 07/23, by MANAGER OVERRIDE, left open 5 days and auto-closed by the kiosk
    ("stale punch ... review hours") for 122.19 h. No logged attempts, no punches at all after 07/28.
  • The failure log DOES name people — 27 face_mismatch rows across 8+ employees in that window, several
    reading "best 0%" — so the absence of Janet's name is a real absence, not an unlogged category.
  • Overrides are routine here, which is itself the finding: an employee who can never self-clock has a
    standing friction the payroll board silently absorbs.

PURE: `analyze()` takes already-loaded rows and returns the report. No DB, no clock — the caller passes
`today`. That is what makes the classification testable (harness_clockin_evidence.py) rather than
inspected by eye.
"""
from datetime import date, timedelta

# The failure_log categories that mean "a clock-in was attempted and the SERVER refused it".
# Anything the device never sent is, by construction, absent — see the module docstring.
CLOCKIN_FAILURE_CATEGORIES = ("face_mismatch", "clock_in_location")

# A punch whose device carries this marker was not self-service: a manager had to let them in.
OVERRIDE_MARKERS = ("override",)

VERDICTS = {
    "clocked_in": "Clocked in normally.",
    "clocked_in_override": "Clocked in, but only via a manager override — self-service did not work.",
    "attempted_failed": "Tried to clock in and the system refused it.",
    "worked_without_clocking": "No punch, but they were provably at work that day.",
    "no_record_scheduled": "Scheduled, but the system holds no punch, no attempt and no other activity.",
    "no_record_unscheduled": "Not scheduled and no activity — nothing to explain.",
    "not_yet": "This day has not happened yet.",
}

# How each verdict bears on a claim of "the app would not let me clock in".
CLAIM_EFFECT = {
    "clocked_in": "neutral",
    "clocked_in_override": "supports",
    "attempted_failed": "supports",
    "worked_without_clocking": "supports",
    "no_record_scheduled": "inconclusive",
    "no_record_unscheduled": "neutral",
    "not_yet": "neutral",
}


def _s(v):
    return "" if v is None else str(v).strip()


def _day(v):
    """First 10 chars of any date-ish value as an ISO day string."""
    return _s(v)[:10]


def _store_key(v):
    """Normalize a store code for COMPARISON only (never for display).

    The same store is spelled several ways in this data — 'CERMARK' / 'Cermark', '3352 26TH' /
    '3352 26th Chicago' — the same twin problem that put 29 closings on a phantom store code. Left
    unnormalized, the peer lookup silently found zero colleagues and every disputed day reported
    'nobody at this store clocked in', i.e. it FABRICATED corroboration. Lower-cased and stripped to
    alphanumerics, the common variants collapse; a spelling that still differs is handled honestly by
    `store_variants` in the report rather than by guessing."""
    return "".join(c for c in _s(v).lower() if c.isalnum())


def _is_override(punch):
    d = _s(punch.get("device")).lower()
    n = _s(punch.get("notes")).lower()
    return any(m in d or m in n for m in OVERRIDE_MARKERS)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def daterange(start, end):
    d, out = start, []
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def analyze(employee_name, start, end, punches, failures, shifts,
            peer_punches=None, activity=None, strikes=None, today=None):
    """Build the day-by-day evidence report. PURE.

    punches   : storeops.timelog rows for THIS employee in range
    failures  : core.failure_log rows naming THIS employee (any category; filtered here)
    shifts    : storeops.shifts rows for THIS employee in range
    peer_punches: timelog rows for EVERY employee at the stores in question — the kiosk-health signal.
                  Rows belonging to this employee are ignored when counting peers.
    activity  : [{work_date, kind, detail}] independent proof of presence (sales rung, closing filed)
    strikes   : storeops.late_clockin_strike rows (a late-strike is itself proof a punch landed)
    today     : the caller's today. Days AFTER it have not happened yet, so they carry no evidence
                either way and are marked `not_yet`. Without this, a schedule that runs past today
                produced "scheduled, and the system holds nothing" for days nobody could possibly have
                worked — and, where no colleague had clocked in yet either, scored them as SUPPORTING
                a complaint about a shift in the future. None = do not classify any day as future.
    """
    by_day_punch, by_day_fail, by_day_shift = {}, {}, {}
    for p in punches or []:
        by_day_punch.setdefault(_day(p.get("work_date") or p.get("clock_in")), []).append(p)
    for f in failures or []:
        if _s(f.get("category")) in CLOCKIN_FAILURE_CATEGORIES:
            by_day_fail.setdefault(_day(f.get("created_at")), []).append(f)
    for s in shifts or []:
        if s.get("is_deleted"):
            continue
        by_day_shift.setdefault(_day(s.get("shift_date")), []).append(s)

    by_day_act = {}
    for a in activity or []:
        by_day_act.setdefault(_day(a.get("work_date")), []).append(a)
    by_day_strike = {}
    for s in strikes or []:
        by_day_strike.setdefault(_day(s.get("work_date")), []).append(s)

    # Peer clock-ins per (day, store) — only rows that are NOT this employee's, and only SELF-SERVICE
    # ones. A store where everyone needed an override is not evidence that the kiosk worked.
    #
    # `peer_punches is None` means the caller DID NOT LOAD peer data; an empty LIST means it loaded it
    # and nobody clocked in. Collapsing the two would let a caller who simply omitted the argument
    # produce "nobody at this store could clock in" — the module committing the exact error it exists
    # to prevent, and in the direction that fabricates corroboration. Caught by harness C1.
    peer_data_known = peer_punches is not None
    peers = {}
    for p in peer_punches or []:
        if _s(p.get("employee_name")).lower() == _s(employee_name).lower():
            continue
        if _is_override(p):
            continue
        key = (_day(p.get("work_date") or p.get("clock_in")), _store_key(p.get("store_code")))
        peers.setdefault(key, set()).add(_s(p.get("employee_name")))

    days = []
    for d in daterange(start, end):
        iso = d.isoformat()
        ps, fs = by_day_punch.get(iso, []), by_day_fail.get(iso, [])
        sh, act = by_day_shift.get(iso, []), by_day_act.get(iso, [])
        strk = by_day_strike.get(iso, [])
        # A shift row with no hours and no times is a placeholder, not a schedule (it is what makes a
        # day look "covered" to payroll while carrying nothing) — it must not read as "was scheduled".
        real_shift = [s for s in sh if (_num(s.get("scheduled_hours")) or 0) > 0 or _s(s.get("start_time"))]

        stores = {_store_key(s.get("store_code")) for s in sh if _s(s.get("store_code"))}
        stores |= {_store_key(p.get("store_code")) for p in ps if _s(p.get("store_code"))}
        peer_names = set()
        for st in (stores or {""}):
            peer_names |= peers.get((iso, st), set())

        if today is not None and d > today and not (ps or fs or act or strk):
            verdict = "not_yet"
        elif ps:
            verdict = "clocked_in_override" if all(_is_override(p) for p in ps) else "clocked_in"
        elif fs:
            verdict = "attempted_failed"
        elif act or strk:
            verdict = "worked_without_clocking"
        elif real_shift:
            verdict = "no_record_scheduled"
        else:
            verdict = "no_record_unscheduled"

        effect = CLAIM_EFFECT[verdict]
        notes = []
        # Kiosk health only MEANS anything on a day with no punch — otherwise it is noise.
        if verdict in ("attempted_failed", "no_record_scheduled", "worked_without_clocking"):  # never 'not_yet''
            if peer_names:
                notes.append(f"{len(peer_names)} other employee(s) clocked in normally at this store "
                             f"that day — the kiosk was working for them.")
                if verdict == "no_record_scheduled":
                    effect = "weakens"
            elif peer_data_known and stores:
                notes.append("Nobody at this store clocked in successfully that day — consistent with "
                             "a device or app problem rather than one person's.")
                effect = "supports"
            elif stores:
                notes.append("Clock-ins by colleagues at this store were not checked, so nothing here "
                             "says whether the kiosk was working.")
        for p in ps:
            if _is_override(p):
                notes.append(f"Punch required a manager override ({_s(p.get('device')) or 'override'}).")
            if _s(p.get("notes")).lower().count("stale punch"):
                notes.append("This punch was left open and auto-closed later — its hours are not a "
                             "measure of time worked.")
            fm = _num(p.get("face_match_pct"))
            if fm is not None and fm < 100:
                notes.append(f"Face matched at {fm:g}%.")
        for f in fs:
            notes.append(f"Refused: {_s(f.get('message')) or _s(f.get('category'))}")
        for a in act:
            notes.append(f"Proof of presence: {_s(a.get('kind'))}{' — ' + _s(a.get('detail')) if a.get('detail') else ''}")

        days.append({
            "date": iso,
            "verdict": verdict,
            "verdict_label": VERDICTS[verdict],
            "claim_effect": effect,           # supports | weakens | inconclusive | neutral
            "punches": len(ps),
            "override_punch": bool(ps) and all(_is_override(p) for p in ps),
            "failed_attempts": len(fs),
            "scheduled": bool(real_shift),
            "scheduled_hours": round(sum(_num(s.get("scheduled_hours")) or 0 for s in real_shift), 2),
            "other_activity": len(act) + len(strk),
            "peers_clocked_in": len(peer_names),
            "stores": sorted(x for x in stores if x),
            "notes": notes,
        })

    counts = {}
    for d in days:
        counts[d["verdict"]] = counts.get(d["verdict"], 0) + 1
    effects = {}
    for d in days:
        effects[d["claim_effect"]] = effects.get(d["claim_effect"], 0) + 1

    # The raw spellings this employee's own rows carry. More than one means the store-code twins are in
    # play for them, which the reader should know before trusting any per-store count.
    variants = sorted({_s(r.get("store_code")) for r in list(punches or []) + list(shifts or [])
                       if _s(r.get("store_code"))})

    return {
        "employee_name": employee_name,
        "start": start.isoformat(), "end": end.isoformat(),
        "store_variants": variants,
        "days": days,
        "counts": counts,
        "claim_effects": effects,
        "summary": _summary(employee_name, days, counts, effects),
        # Stated on every response, not buried in a doc: the reader must not read silence as proof.
        "limits": [
            "A clock-in that fails on the phone or kiosk BEFORE reaching the server leaves no record "
            "anywhere — app not opening, no network, a failed login or a denied camera. Days marked "
            "'no record' are therefore not evidence that the employee did not try.",
            "Only attempts the server actively refused are logged (face mismatch, location/schedule "
            "block).",
            "This report assembles evidence. It does not decide whether anyone is telling the truth.",
        ],
    }


def _summary(name, days, counts, effects):
    """One paragraph a manager can act on, built only from what was counted. PURE."""
    if not days:
        return "No days in range."
    n = len(days)
    parts = [f"{name}: {n} day(s) reviewed."]
    clocked = counts.get("clocked_in", 0) + counts.get("clocked_in_override", 0)
    parts.append(f"{clocked} with a punch"
                 + (f" ({counts['clocked_in_override']} needed a manager override)"
                    if counts.get("clocked_in_override") else "") + ".")
    if counts.get("attempted_failed"):
        parts.append(f"{counts['attempted_failed']} day(s) show a REFUSED clock-in attempt — "
                     f"the system itself recorded the failure.")
    if counts.get("worked_without_clocking"):
        parts.append(f"{counts['worked_without_clocking']} day(s) have no punch but independent proof "
                     f"they were working.")
    if counts.get("no_record_scheduled"):
        parts.append(f"{counts['no_record_scheduled']} scheduled day(s) hold nothing at all.")
    if effects.get("supports"):
        parts.append(f"{effects['supports']} day(s) SUPPORT a report of clock-in trouble.")
    if effects.get("weakens"):
        parts.append(f"{effects['weakens']} day(s) WEAKEN it — colleagues clocked in fine at the same "
                     f"store the same day.")
    return " ".join(parts)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# LOADERS + ENDPOINT — everything above this line is pure and is where the decisions live.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from fastapi import APIRouter, Header, HTTPException   # noqa: E402

from app.core.database import get_supabase             # noqa: E402

router = APIRouter()

ORG_ID = "00000000-0000-0000-0000-000000000001"


def sb():
    return get_supabase()


def _employee(org_id, employee_id="", name=""):
    """Resolve the subject. Either key works — the board passes employee_id, a human passes a name."""
    try:
        q = sb().table("employees").select("employee_id,name,home_store").eq("org_id", org_id)
        q = q.eq("employee_id", employee_id) if employee_id else q.ilike("name", name)
        rows = q.limit(1).execute().data or []
        return rows[0] if rows else None
    except Exception:
        return None


def gather(org_id, employee_id, employee_name, start, end):
    """Load every source `analyze` reads. Each read degrades to [] on its own — a missing optional
    source must narrow the evidence, never 500 the page. `peer_punches` is passed as a LIST (possibly
    empty) only when its read SUCCEEDED, so an unavailable read stays distinguishable from a store
    where genuinely nobody clocked in (see analyze's peer_data_known)."""
    lo, hi = start.isoformat(), end.isoformat()
    client = sb()

    def _try(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    punches = _try(lambda: (client.table("timelog").select("*").eq("org_id", org_id)
                            .eq("employee_name", employee_name)
                            .gte("work_date", lo).lte("work_date", hi)
                            .order("clock_in").execute().data) or [], []) or []
    shifts = _try(lambda: (client.table("shifts").select("*").eq("org_id", org_id)
                           .eq("employee_name", employee_name)
                           .gte("shift_date", lo).lte("shift_date", hi)
                           .execute().data) or [], []) or []
    strikes = _try(lambda: (client.table("late_clockin_strike").select("*").eq("org_id", org_id)
                            .eq("employee_name", employee_name)
                            .gte("work_date", lo).lte("work_date", hi)
                            .execute().data) or [], []) or []
    # The failure log is the only record of an attempt the SERVER refused. core schema, name-keyed.
    failures = _try(lambda: (client.schema("core").table("failure_log").select("*")
                             .eq("org_id", org_id).eq("employee_name", employee_name)
                             .gte("created_at", lo).lte("created_at", (end + timedelta(days=1)).isoformat())
                             .execute().data) or [], []) or []

    # Peer clock-ins for kiosk health. Deliberately NOT filtered by store_code in the query: the same
    # store is spelled several ways ('3352 26TH' vs '3352 26th Chicago'), so an `in_(store_code)` filter
    # returned zero peers and the report then announced "nobody at this store clocked in" — inventing
    # corroboration out of a spelling difference. The whole range is fetched (bounded by the 92-day cap)
    # and `analyze` matches on the normalized store key.
    peer_punches = _try(lambda: (client.table("timelog")
                                 .select("employee_name,store_code,work_date,clock_in,device,notes")
                                 .eq("org_id", org_id)
                                 .gte("work_date", lo).lte("work_date", hi)
                                 .limit(20000).execute().data) or [], None)

    # Independent proof of presence. SALES ARE THE STRONGEST SIGNAL AND THE REASON THIS EXISTS:
    # measured 2026-08-11, four Luxelink employees (Chavez, Lopez, Navarrete, Perez) rang 27 days of
    # sales inside 07/23-08/05 while holding ZERO punches and ZERO shifts — completely invisible to
    # payroll. Without this loader the check would have called those days "no record" and quietly
    # implied nobody worked them.
    #
    # ⚠️ NAME VOCABULARY: raw_sales stores "Last, First" ("Lopez, Zuleicka") while storeops stores
    # "First Last" ("Zuliecka Lopez") — AND the two spellings of that surname differ. A plain equality
    # join finds nothing, so the match runs through commission_engine._canon_person, the SAME
    # canonicalizer the commission side already uses for this exact problem, and is compared per row
    # rather than filtered in the query.
    activity = []
    try:
        from app.modules.commcalc.commission_engine import _canon_person as _canon
    except Exception:
        def _canon(s):
            return " ".join(_s(s).lower().split())
    want = _canon(employee_name)
    for row in _try(lambda: (client.schema("commcalc").table("raw_sales")
                             .select("salesperson,trans_date,store")
                             .eq("org_id", org_id)
                             .gte("trans_date", lo).lte("trans_date", hi)
                             .limit(200000).execute().data) or [], []) or []:
        if _canon(row.get("salesperson")) != want:
            continue
        activity.append({"work_date": row.get("trans_date"), "kind": "rang sales",
                         "detail": _s(row.get("store"))})

    for row in _try(lambda: (client.schema("commcalc").table("daily_closing")
                             .select("close_date,employee_name,store_code")
                             .eq("org_id", org_id).eq("employee_name", employee_name)
                             .gte("close_date", lo).lte("close_date", hi)
                             .execute().data) or [], []) or []:
        activity.append({"work_date": row.get("close_date"), "kind": "filed the daily closing",
                         "detail": _s(row.get("store_code"))})

    return punches, failures, shifts, peer_punches, activity, strikes


@router.get("/clock-in-check")
def clock_in_check(employee_id: str = "", name: str = "", start: str = "", end: str = "",
                   authorization: str = Header(default=""), org_id: str = ORG_ID):
    """SELF CHECK for a disputed clock-in: every trace the system holds for one employee over a range.

    Returns a day-by-day report with, per day, whether the evidence SUPPORTS or WEAKENS a report of
    clock-in trouble — and, prominently, what the system cannot see at all. It does not judge honesty;
    see this module's docstring for why that is a design decision and not a limitation to be removed.
    """
    if not (employee_id or name):
        raise HTTPException(400, "employee_id or name is required")
    try:
        s = date.fromisoformat(_s(start)[:10]) if start else None
        e = date.fromisoformat(_s(end)[:10]) if end else None
    except ValueError:
        raise HTTPException(400, "start/end must be ISO dates (YYYY-MM-DD)")
    if not (s and e):
        # Default to the period the payroll board is approving, so a dispute raised on that screen
        # opens here on the SAME dates rather than a differently-guessed window.
        from app.modules.storeops.payroll_approval import previous_pay_period
        s, e, _payday = previous_pay_period(org_id)
    if e < s:
        raise HTTPException(400, "end is before start")
    if (e - s).days > 92:
        raise HTTPException(400, "range is limited to 92 days")

    emp = _employee(org_id, employee_id, name)
    subject = (emp or {}).get("name") or (name or employee_id)
    punches, failures, shifts, peers, activity, strikes = gather(
        org_id, (emp or {}).get("employee_id") or employee_id, subject, s, e)
    out = analyze(subject, s, e, punches, failures, shifts,
                  peer_punches=peers, activity=activity, strikes=strikes, today=date.today())
    out["employee_id"] = (emp or {}).get("employee_id") or employee_id
    out["resolved"] = bool(emp)
    if not emp:
        out["limits"] = ["No employee record matched, so only rows carrying this exact name were "
                         "found. Check the spelling before drawing any conclusion."] + out["limits"]
    return out
