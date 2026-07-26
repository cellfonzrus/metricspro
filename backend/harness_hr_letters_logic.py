"""Pure-logic proof (no DB, no FastAPI) for app.modules.hr.letters_logic — the lateness/tier/render
rules the owner's fixtures are stated against verbatim:
  "lateness fixture (scheduled 9:00, punches 9:03 w/ grace 5 -> not late; 9:07 -> late; multi-session
  day uses earliest); strike escalation fixture (3rd + 5th pick correct templates)."
Run: `python3 harness_hr_letters_logic.py` from backend/.
"""
import sys
from datetime import date

sys.path.insert(0, ".")

from app.modules.hr.letters_logic import (  # noqa: E402
    clamp_int, compute_lateness, grace_minutes_from_config, render_template,
    strike_window_days_from_config, tier_for_strike_count, tokens_in,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


WD = date(2026, 7, 20)
TZ = "America/New_York"

# ── the owner's exact fixture ──────────────────────────────────────────────────────────────────
r = compute_lateness("09:00", [{"clock_in": "2026-07-20T09:03:00-04:00"}], 5, WD, TZ)
check("scheduled 9:00, punch 9:03, grace 5 -> NOT late", r is None, r)

r = compute_lateness("09:00", [{"clock_in": "2026-07-20T09:07:00-04:00"}], 5, WD, TZ)
check("scheduled 9:00, punch 9:07, grace 5 -> LATE", r is not None and r["minutes_late"] == 7, r)

# multi-session day: the SECOND session's punch (afternoon, after a lunch break) must never be
# mistaken for "the" punch — only the EARLIEST of the day determines lateness.
punches = [{"clock_in": "2026-07-20T13:05:00-04:00"}, {"clock_in": "2026-07-20T09:07:00-04:00"}]
r = compute_lateness("09:00", punches, 5, WD, TZ)
check("multi-session day uses the EARLIEST punch (not the 2nd/afternoon session)",
     r is not None and r["minutes_late"] == 7 and r["first_punch_at"].startswith("2026-07-20T09:07"), r)

# grace boundary is inclusive (<=)
r = compute_lateness("09:00", [{"clock_in": "2026-07-20T09:05:00-04:00"}], 5, WD, TZ)
check("exactly at the grace boundary (9:05, grace 5) -> NOT late", r is None, r)

# no scheduled shift -> never evaluated
check("no scheduled_start -> None (only scheduled days count)",
     compute_lateness("", [{"clock_in": "2026-07-20T09:07:00-04:00"}], 5, WD, TZ) is None)
check("blank/whitespace scheduled_start -> None", compute_lateness("   ", [{"clock_in": "2026-07-20T09:07:00-04:00"}], 5, WD, TZ) is None)

# a no-show (scheduled, but zero punches) is a DIFFERENT problem, not "late" — never flagged here
check("scheduled shift + ZERO punches (a no-show) -> None (not 'late')", compute_lateness("09:00", [], 5, WD, TZ) is None)

# a punch missing/garbage clock_in is ignored, not a crash
r = compute_lateness("09:00", [{"clock_in": None}, {"clock_in": "garbage"}, {"clock_in": "2026-07-20T09:07:00-04:00"}], 5, WD, TZ)
check("garbage/missing punch timestamps are ignored, real one still used", r is not None and r["minutes_late"] == 7)

# grace 0 -> even 1 minute late counts
r = compute_lateness("09:00", [{"clock_in": "2026-07-20T09:01:00-04:00"}], 0, WD, TZ)
check("grace=0, 1 minute late -> LATE (minutes_late=1)", r is not None and r["minutes_late"] == 1, r)

# ── strike-tier escalation — "3rd + 5th pick correct templates" ───────────────────────────────
check("occurrence #1 -> tier 1 (standard notice)", tier_for_strike_count(1) == 1)
check("occurrence #2 -> tier 1 (standard notice, '1st/2nd = standard notice')", tier_for_strike_count(2) == 1)
check("occurrence #3 -> tier 3 (escalated / suspension-without-pay language)", tier_for_strike_count(3) == 3)
check("occurrence #4 -> tier 3 (still escalated, not yet final)", tier_for_strike_count(4) == 3)
check("occurrence #5 -> tier 5 (termination-basis language)", tier_for_strike_count(5) == 5)
check("occurrence #12 (well past 5) -> still tier 5 (no tier beyond 5 exists)", tier_for_strike_count(12) == 5)
check("occurrence #0 / garbage -> tier 1 (never crashes, never escalates on nothing)", tier_for_strike_count(0) == 1)

# ── config knob clamps (RULE TWO — every threshold configurable with a sane default) ──────────
check("grace default = 5 when unset", grace_minutes_from_config({}) == 5)
check("grace clamps above 60 down to 60", grace_minutes_from_config({"grace_minutes": 999}) == 60)
check("grace clamps negative up to 0", grace_minutes_from_config({"grace_minutes": -5}) == 0)
check("grace tolerates garbage -> default", grace_minutes_from_config({"grace_minutes": "abc"}) == 5)
check("strike window default = 90 when unset", strike_window_days_from_config({}) == 90)
check("strike window clamps above 365 down to 365", strike_window_days_from_config({"strike_window_days": 9999}) == 365)
check("strike window clamps below 1 up to 1", strike_window_days_from_config({"strike_window_days": 0}) == 1)
check("clamp_int is the shared primitive both use", clamp_int("40", 0, 60, 5) == 40)

# ── merge-field rendering ──────────────────────────────────────────────────────────────────────
check("renders known tokens", render_template("Hi {{name}}, owe {{amt}}.", {"name": "Sam", "amt": "$5"}) == "Hi Sam, owe $5.")
check("unknown token -> blank, never raises, never leaves a raw {{token}}",
     render_template("Hi {{name}} {{missing}}", {"name": "Sam"}) == "Hi Sam ")
check("None value -> blank", render_template("{{x}}", {"x": None}) == "")
check("no tokens at all -> passthrough", render_template("plain text", {}) == "plain text")
check("None body -> empty string, never raises", render_template(None, {"a": 1}) == "")
check("tokens_in finds every distinct token once", tokens_in("{{a}} {{b}} {{a}}") == ["a", "b"])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("\nFAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
