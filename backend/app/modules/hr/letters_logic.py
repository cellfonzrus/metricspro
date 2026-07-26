"""hr.letters_logic — PURE logic for the HR Letters / Template-Library system.

Deliberately dependency-free (no Supabase, no FastAPI) so every rule here is unit-provable without a
DB: lateness detection (multi-session-safe), strike-tier escalation, merge-field rendering, and the
small numeric clamps used by the per-tenant config knobs. `letters.py` (the FastAPI router) imports
these and supplies the I/O (fetch shifts/punches, persist, send email).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - py<3.9 fallback never hit in this codebase
    ZoneInfo = None  # type: ignore


# ── config clamps (RULE TWO — every threshold is tenant-configurable with a sane default) ────────
def clamp_int(raw, lo, hi, default):
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def grace_minutes_from_config(cfg: dict) -> int:
    """Default 5, clamped 0..60 (a whole-shift 'grace' would defeat the point of the check)."""
    return clamp_int((cfg or {}).get("grace_minutes"), 0, 60, 5)


def strike_window_days_from_config(cfg: dict) -> int:
    """Default 90, clamped 1..365 (a rolling lookback window for counting strikes)."""
    return clamp_int((cfg or {}).get("strike_window_days"), 1, 365, 90)


# ── lateness detection (multi-session safe — NEVER assume one punch per day) ──────────────────────
def _parse_iso(v):
    if not v:
        return None
    s = str(v).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def earliest_punch(punches: list) -> datetime | None:
    """The earliest clock_in among ANY number of punch rows for the day (multi-session safe — a
    lunch-break second session must never be mistaken for 'the' punch; only the FIRST one of the day
    determines lateness, exactly as the owner's fixture specifies)."""
    earliest = None
    for p in punches or []:
        dt = _parse_iso((p or {}).get("clock_in"))
        if dt is None:
            continue
        if earliest is None or dt < earliest:
            earliest = dt
    return earliest


def compute_lateness(scheduled_start: str, punches: list, grace_minutes: int, work_date: date,
                     tzname: str = "America/New_York") -> dict | None:
    """Late = the EARLIEST clock-in of the day is after scheduled_start + grace. Only days with a
    scheduled shift are ever evaluated (the caller only calls this for employees who had one).
    Returns None when: no scheduled_start, no punches at all (a no-show is a different problem, not
    'late'), or the earliest punch is within grace. Otherwise {"first_punch_at": iso str (tz-aware),
    "minutes_late": int (>=1)}."""
    if not scheduled_start or not punches:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", str(scheduled_start).strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    tz = None
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(tzname or "America/New_York")
        except Exception:
            tz = None
    sched_dt = datetime(work_date.year, work_date.month, work_date.day, hh, mm, tzinfo=tz)
    grace_dt = sched_dt + timedelta(minutes=int(grace_minutes or 0))
    earliest = earliest_punch(punches)
    if earliest is None:
        return None
    # Normalize both to comparable tz-awareness: if the punch timestamp is tz-naive (shouldn't happen —
    # storeops writes tz-aware timestamptz — but never crash on it), assume it's already business-local.
    if earliest.tzinfo is None and sched_dt.tzinfo is not None:
        earliest = earliest.replace(tzinfo=sched_dt.tzinfo)
    elif earliest.tzinfo is not None and sched_dt.tzinfo is None:
        sched_dt = sched_dt.replace(tzinfo=earliest.tzinfo)
        grace_dt = grace_dt.replace(tzinfo=earliest.tzinfo)
    if earliest <= grace_dt:
        return None
    minutes_late = int((earliest - sched_dt).total_seconds() // 60)
    return {"first_punch_at": earliest.isoformat(), "minutes_late": max(minutes_late, 1)}


# ── strike-tier escalation ────────────────────────────────────────────────────────────────────────
# Only 3 templates exist for late_clockin (tiers 1/3/5 — categories v1, owner directive). 1st/2nd
# occurrence = the tier-1 "standard notice" template; 3rd/4th = the tier-3 escalated letter citing
# suspension-without-pay; 5th onward = the tier-5 letter citing termination basis. Every late day still
# gets SOME letter (per the owner: "create an automatic email to the employees who clock in late") —
# it's the WORDING/tier that escalates at the 3rd and 5th strike, not whether a letter fires at all.
def tier_for_strike_count(strike_number: int) -> int:
    n = int(strike_number or 0)
    if n >= 5:
        return 5
    if n >= 3:
        return 3
    return 1


# ── merge-field rendering ─────────────────────────────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render_template(text: str, merge: dict) -> str:
    """Replace every {{token}} with its merge value (blank if absent/None — never leaves a raw
    {{token}} in a sent letter, and never raises on a template referencing an unknown field)."""
    merge = merge or {}

    def _sub(m):
        v = merge.get(m.group(1))
        return "" if v is None else str(v)
    return _TOKEN_RE.sub(_sub, text or "")


def tokens_in(text: str) -> list:
    """Every {{token}} name referenced in a template body/subject (for an admin-facing 'used fields'
    hint and for detecting an unknown/typo'd token)."""
    out, seen = [], set()
    for m in _TOKEN_RE.finditer(text or ""):
        k = m.group(1)
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out
