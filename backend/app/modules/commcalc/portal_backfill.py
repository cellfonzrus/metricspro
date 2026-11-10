"""THE ONE HOME for "which span of days does this portal source still owe, and which has it answered".

THE CLASS THIS MODULE IS THE ONE HOME FOR (index §COMMCALC-58)
---------------------------------------------------------------
`portal_report_pull.date_range` answers exactly one question: the last N days, ending yesterday,
clamped to `MAX_WINDOW_DAYS` because a daily pull that re-fetches months is how a portal starts
rate-limiting. That clamp is right and stays. But it left the loop unable to be asked a DIFFERENT
question — "every day since the first of the year, once" — so a new connector that owed history had
nowhere to put it except a second pull loop with its own window arithmetic. That is the duplicate
defect the house rules forbid, and it would have arrived with the first tenant who said "catch me up".

So the general fact is separated from the recent-window fact and given one home: a source DECLARES
where its history starts (`data_source.history_from`), this module turns that into a list of windows
no wider than the loop's own ceiling, remembers which ones have been ANSWERED, and hands the pull the
oldest one still owed. When nothing is owed the pull keeps the loop's own recent window, unchanged —
this mechanism may never widen a pull that was not asked to be wide.

WHAT COUNTS AS ANSWERED, and why `empty` is one. A window is answered when the pull delivered rows
(`done`) or completed and the portal had nothing for those days (`empty`). A portal with nothing for
January will have nothing for January tomorrow either, so re-asking forever is how a backfill never
finishes. A window that FAILED is retried up to `MAX_ATTEMPTS`; after that it stops blocking the rest
of the backfill but stays visible as `stuck` — a gap in the data is reported as a gap, never skipped
silently and never counted as done.

STATE IS DERIVED FROM WHAT THE PULL DID, never asserted beside it (`state_of`). The loop's own rule is
that a pull which landed no rows is not delivered; this module reads that same answer rather than
keeping a second opinion about it.

DEGRADES TO TODAY'S BEHAVIOUR. Every database call here is guarded: until migration `1116` is applied
(or if the table is unreachable) nothing is owed, nothing is recorded, and every pull uses the recent
window exactly as it does now. Applying the migration takes effect with no restart.

PURE / IO SPLIT. `plan_windows`, `owed_windows`, `stuck_windows`, `choose`, `window_key`, `state_of`,
`progress` and `chunk_days` are pure and proved by `tests/unit/test_portal_backfill.py` +
`backend/harness_portal_backfill.py`. Only `owed`, `record` and `progress_for` touch a client.
"""
from datetime import date, timedelta

from app.modules.commcalc import portal_report_pull as prp

SCHEMA = "commcalc"
TABLE = "portal_pull_backlog"

# How many days one backfill window covers by default. Deliberately well below the loop's ceiling: a
# per-day report runs once per DAY inside the window, so a wide window is a long run against somebody
# else's portal. Per source via `data_source.history_chunk_days`.
DEFAULT_CHUNK_DAYS = 7

# How many times a failing window is re-asked before it stops blocking the backfill. It is then
# REPORTED (`stuck_windows`), never dropped.
MAX_ATTEMPTS = 4

# The report_key a window covers when it covers the source's whole declared report set — which is what
# the loop pulls in one pass. Spelled once; a future per-report backfill fits the same row shape.
ALL_REPORTS = "*"

ANSWERED = ("done", "empty")
STATES = ("done", "empty", "failed")


# ── pure: planning ───────────────────────────────────────────────────────────────────────────────
def _day(value):
    """A readable ISO date, or None. Never guesses: an unreadable declaration owes nothing, because a
    guessed start date is a silent decision about how far back we ask somebody else's portal."""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def window_width(value):
    """How wide one backfill window may be (`chunk_days` as declared). House default, clamped to the loop's OWN ceiling —
    dereferenced from `portal_report_pull.MAX_WINDOW_DAYS`, never a second copy of that number."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CHUNK_DAYS
    if n <= 0:
        return DEFAULT_CHUNK_DAYS
    return min(n, prp.MAX_WINDOW_DAYS)


def plan_windows(history_from, today=None, chunk_days=None):
    """The windows between the declared history start and YESTERDAY, oldest first, none wider than
    `chunk_days`. PURE. Empty when nothing is declared, when the declaration is unreadable, or when it
    is not in the past — all three mean "this source owes no history", which is the default posture."""
    start = _day(history_from)
    if start is None:
        return ()
    end = (_day(today) or date.today()) - timedelta(days=1)
    if end < start:
        return ()
    width = window_width(chunk_days)
    out, cur = [], start
    while cur <= end:
        last = min(cur + timedelta(days=width - 1), end)
        out.append((cur.isoformat(), last.isoformat()))
        cur = last + timedelta(days=1)
    return tuple(out)


def window_key(window):
    """ONE spelling of "which window is this". Read when planning and again when recording, and two
    spellings is how a recorded window never matches the planned one and a backfill repeats forever."""
    if isinstance(window, dict):
        a, b = window.get("window_from"), window.get("window_to")
    elif isinstance(window, (tuple, list)) and len(window) >= 2:
        a, b = window[0], window[1]
    else:
        return ""
    a, b = _day(a), _day(b)
    if a is None or b is None:
        return ""
    return "%s..%s" % (a.isoformat(), b.isoformat())


def _by_key(rows):
    out = {}
    for r in (rows or []):
        k = window_key(r)
        if k:
            out[k] = r
    return out


def _attempts(row):
    try:
        return int(row.get("attempts") or 0)
    except (TypeError, ValueError):
        return 0


def owed_windows(plan, rows):
    """The planned windows still owed, oldest first. A window is owed when it has no row, or its row
    says `failed` and it has attempts left. PURE."""
    seen = _by_key(rows)
    out = []
    for win in (plan or ()):
        row = seen.get(window_key(win))
        if row is None:
            out.append(win)
            continue
        if str(row.get("state") or "").strip().lower() in ANSWERED:
            continue
        if _attempts(row) < MAX_ATTEMPTS:
            out.append(win)
    return tuple(out)


def stuck_windows(plan, rows):
    """The planned windows that failed their last allowed attempt. Reported so a hole in the history
    is visible as a hole — this is the honest half of letting an exhausted window stop blocking."""
    seen = _by_key(rows)
    out = []
    for win in (plan or ()):
        row = seen.get(window_key(win))
        if row is None:
            continue
        if (str(row.get("state") or "").strip().lower() not in ANSWERED
                and _attempts(row) >= MAX_ATTEMPTS):
            out.append(win)
    return tuple(out)


def choose(owed, default_range):
    """The window this pull covers: the oldest owed one, else the loop's own recent range. PURE."""
    for win in (owed or ()):
        key = window_key(win)
        if key:
            return (key.split("..")[0], key.split("..")[1])
    return default_range


def state_of(result):
    """The outcome word for one pull, DERIVED from what the pull reported. A pull that landed rows is
    `done`; one that completed with none and said nothing went wrong is `empty`; anything else is
    `failed`. Never asserted beside the result — the loop already decides what delivered means."""
    if not isinstance(result, dict):
        return "failed"
    if result.get("ok") is False:
        return "failed"
    rows = result.get("rows_ingested") or 0
    try:
        rows = int(rows)
    except (TypeError, ValueError):
        rows = 0
    if result.get("delivered") and rows > 0:
        return "done"
    if str(result.get("error") or "").strip():
        return "failed"
    return "empty"


def progress(plan, rows):
    """What an operator reads: how many windows the history is, how many are answered, how many are
    owed, how many are stuck, and which window is next. PURE."""
    plan = tuple(plan or ())
    owed = owed_windows(plan, rows)
    stuck = stuck_windows(plan, rows)
    nxt = owed[0] if owed else None
    return {"windows": len(plan),
            "answered": max(0, len(plan) - len(owed) - len(stuck)),
            "owed": len(owed),
            "stuck": len(stuck),
            "next_window": window_key(nxt) if nxt else "",
            "stuck_windows": [window_key(w) for w in stuck],
            "complete": bool(plan) and not owed}


DECLARED_FIELDS = ("store_code", "history_from", "history_chunk_days")


def clean_declaration(row):
    """What a login DECLARED about its store and its history, cleaned for storage. PURE.

    One home because the save path and the pull read the same three columns, and because a cleared
    form field arrives as "" — which a DATE column rejects with a 500 rather than a refusal. An
    unreadable date is stored as NULL (the source then owes no history and says so) rather than
    guessed at. Only the keys actually sent are returned, so a PATCH stays a PATCH.
    """
    row = row if isinstance(row, dict) else {}
    out = {}
    if "store_code" in row:
        out["store_code"] = str(row.get("store_code") or "").strip() or None
    if "history_from" in row:
        d = _day(row.get("history_from"))
        out["history_from"] = d.isoformat() if d else None
    if "history_chunk_days" in row:
        raw = row.get("history_chunk_days")
        out["history_chunk_days"] = (None if str(raw or "").strip() == ""
                                     else window_width(raw))
    return out


# ── IO: the backlog table (guarded — an unapplied migration changes nothing) ─────────────────────
def _rows(client, org_id, source_id, report_key=ALL_REPORTS):
    """What this source has already answered, or **None when the backlog cannot be read**.

    The distinction is the whole mechanism. "No rows" means nothing has been answered yet and the
    backfill starts; "cannot be read" means there is NO MEMORY of what was answered, and a backfill
    without a memory re-pulls its first window every night forever while the daily update never
    happens. So an unreadable backlog is not treated as an empty one.
    """
    try:
        res = (client.schema(SCHEMA).table(TABLE)
               .select("window_from,window_to,state,attempts,rows_landed,last_error")
               .eq("org_id", org_id).eq("source_id", source_id)
               .eq("report_key", report_key).limit(2000).execute())
        return list(res.data or [])
    except Exception:
        return None


def plan_for(src_row, today=None):
    """The windows one SOURCE owes, from its own declaration. One reader of those two columns."""
    src_row = dict(src_row or {})
    return plan_windows(src_row.get("history_from"), today=today,
                        chunk_days=src_row.get("history_chunk_days"))


def window_for(client, org_id, src_row, default_range, today=None, report_key=ALL_REPORTS):
    """THE window this pull covers. Returns (from_iso, to_iso, is_backfill).

    Guarded end to end: with no declaration, no table, or nothing owed, this is the loop's own recent
    range and `is_backfill` is False — today's behaviour, byte for byte."""
    plan = plan_for(src_row, today=today)
    if not plan:
        return (default_range[0], default_range[1], False)
    rows = _rows(client, org_id, (src_row or {}).get("id"), report_key)
    if rows is None:                       # no memory ⇒ no backfill (see _rows)
        return (default_range[0], default_range[1], False)
    owed = owed_windows(plan, rows)
    if not owed:
        return (default_range[0], default_range[1], False)
    win = choose(owed, default_range)
    return (win[0], win[1], True)


def record(client, org_id, source_id, window, result, report_key=ALL_REPORTS, now_iso=None):
    """Record what happened to ONE backfill window. Best-effort: a backlog that cannot be written must
    never fail a pull that succeeded, so the failure here is reported by the next run owing the same
    window again rather than by a 500 over landed rows."""
    key = window_key(window)
    if not key:
        return {"recorded": False, "reason": "no window"}
    a, b = key.split("..")
    state = state_of(result)
    rows = 0
    try:
        rows = int((result or {}).get("rows_ingested") or 0)
    except (TypeError, ValueError):
        rows = 0
    prior = {window_key(r): r for r in _rows(client, org_id, source_id, report_key)}.get(key) or {}
    payload = {"org_id": org_id, "source_id": source_id, "report_key": report_key,
               "window_from": a, "window_to": b, "state": state,
               "attempts": _attempts(prior) + 1, "rows_landed": rows,
               "last_error": (str((result or {}).get("error") or "")[:400] or None)}
    if now_iso:
        payload["last_attempt_at"] = now_iso
    try:
        (client.schema(SCHEMA).table(TABLE)
         .upsert(payload, on_conflict="org_id,source_id,report_key,window_from,window_to")
         .execute())
        return {"recorded": True, "state": state, "window": key}
    except Exception as e:
        return {"recorded": False, "state": state, "window": key, "reason": str(e)[:160]}


def progress_for(client, org_id, src_row, today=None, report_key=ALL_REPORTS):
    """An operator-readable backfill position for one source, or None when it owes no history."""
    plan = plan_for(src_row, today=today)
    if not plan:
        return None
    rows = _rows(client, org_id, (src_row or {}).get("id"), report_key)
    if rows is None:
        # Declared history, no memory to track it with. SAID, not silently skipped — this is the one
        # sentence that explains why a backfill an operator asked for has not started.
        return {"windows": len(plan), "answered": 0, "owed": len(plan), "stuck": 0,
                "next_window": "", "stuck_windows": [], "complete": False, "blocked": True,
                "from": plan[0][0], "to": plan[-1][1],
                "note": ("This login is asked to pull history from %s, but the pull backlog is not "
                         "available, so the history has not started. Apply migration 1116; the daily "
                         "pull is unaffected." % plan[0][0])}
    out = progress(plan, rows)
    out["from"] = plan[0][0]
    out["to"] = plan[-1][1]
    out["blocked"] = False
    return out
