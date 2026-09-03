"""Proof harness — Commission Discrepancy appeal state machine (mig 946, owner directive 2026-09-03).

Stdlib-only, DB-free. Proves backend/app/modules/commcalc/discrepancy_appeals.py:
  1. the transition truth table (every allowed edge passes, every other edge raises),
  2. apply_appeal's patch builder (who/when stamps; clear = full NULL reset; note clamp;
     it never emits a money field),
  3. period_range_variants (spelling expansion, defaults, reversed/over-long ranges),
  4. summarize_appeals (buckets, open totals, the literal 'no business rule configured' count).

Run:  python backend/harness_discrepancy_appeals.py   → all checks must print OK.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app.modules.commcalc.discrepancy_appeals import (   # noqa: E402
    APPEAL_STATES, ALLOWED_TRANSITIONS, MAX_NOTE_LEN, MAX_RANGE_MONTHS,
    normalize_state, validate_transition, apply_appeal, allowed_next,
    parse_month, month_spellings, period_range_variants, summarize_appeals)

FAILURES = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILURES.append(name)


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
        return False
    except ValueError:
        return True


# ── 1. transition truth table — exhaustive over (state ∪ '') × (state ∪ '') ──────────────────────
ALL = ("",) + APPEAL_STATES
for cur in ALL:
    for nxt in ALL:
        legal = nxt != cur and nxt in ALLOWED_TRANSITIONS[cur]
        if legal:
            check(f"transition {cur or 'Ø'} -> {nxt or 'Ø'} allowed",
                  validate_transition(cur, nxt) == (cur, nxt))
        else:
            check(f"transition {cur or 'Ø'} -> {nxt or 'Ø'} rejected",
                  raises(validate_transition, cur, nxt))

check("NULL/None/'null' normalize to ''",
      normalize_state(None) == "" == normalize_state("null") == normalize_state("  "))
check("unknown state raises", raises(normalize_state, "escalated"))
check("case-insensitive state accepted", normalize_state("Appeal_Filed") == "appeal_filed")
check("allowed_next drives buttons from a NULL row",
      allowed_next(None) == ["appeal_filed", "written_off"])
check("allowed_next from filed", set(allowed_next("appeal_filed"))
      == {"appeal_won", "appeal_denied", "written_off", ""})

# ── 2. apply_appeal patch builder ─────────────────────────────────────────────────────────────────
p = apply_appeal(None, "appeal_filed", "  chasing MA about June  ", "uid-123", "2026-09-03T10:00:00Z")
check("file: status + trimmed note + who + when",
      p == {"appeal_status": "appeal_filed", "appeal_note": "chasing MA about June",
            "appealed_by": "uid-123", "appealed_at": "2026-09-03T10:00:00Z"})
check("patch touches ONLY appeal fields (never money/status)",
      set(p) == {"appeal_status", "appeal_note", "appealed_by", "appealed_at"})
p2 = apply_appeal("appeal_filed", "appeal_won", "", None, "2026-09-04T10:00:00Z")
check("won: empty note -> None, blank actor -> 'web'",
      p2["appeal_note"] is None and p2["appealed_by"] == "web" and p2["appeal_status"] == "appeal_won")
p3 = apply_appeal("appeal_won", "", "ignored", "uid", "now")
check("clear: full NULL reset of all four fields",
      p3 == {"appeal_status": None, "appeal_note": None, "appealed_by": None, "appealed_at": None})
check("note clamped to MAX_NOTE_LEN",
      len(apply_appeal("", "written_off", "x" * 9000, "u", "t")["appeal_note"]) == MAX_NOTE_LEN)
check("invalid transition raises through apply_appeal", raises(apply_appeal, "", "appeal_won", "", "u", "t"))

# ── 3. period range expansion ─────────────────────────────────────────────────────────────────────
check("parse '2026-04'", parse_month("2026-04") == (2026, 4))
check("parse 'April 2026'", parse_month("April 2026") == (2026, 4))
check("strict: junk month raises (never leniently January)", raises(parse_month, "wat"))
check("spellings", month_spellings(2026, 6) == ["2026-06", "June 2026"])
check("single month = both spellings",
      period_range_variants("2026-06", "") == ["2026-06", "June 2026"])
check("range spans the year boundary",
      period_range_variants("2025-11", "2026-01")
      == ["2025-11", "November 2025", "2025-12", "December 2025", "2026-01", "January 2026"])
check("missing FROM defaults to TO", period_range_variants("", "2026-02") == ["2026-02", "February 2026"])
check("reversed range raises", raises(period_range_variants, "2026-05", "2026-04"))
check("over-long range raises", raises(period_range_variants, "2020-01", "2026-01"))
check("empty both raises", raises(period_range_variants, "", ""))
check(f"max range honored ({MAX_RANGE_MONTHS} months passes)",
      len(period_range_variants("2024-01", "2026-12")) == MAX_RANGE_MONTHS * 2)

# ── 4. summary bucketing ──────────────────────────────────────────────────────────────────────────
rows = [
    {"status": "open", "gap": 25.5, "appeal_status": None, "notes": "no business rule configured"},
    {"status": "open", "gap": 10.0, "appeal_status": "appeal_filed", "notes": ""},
    {"status": "lagged", "gap": 5.25, "appeal_status": "appeal_filed",
     "rule_reason": "BYOD SIM kits carry no MA payout"},
    {"status": "open", "gap": "7.25", "appeal_status": "appeal_won", "notes": None},
    {"status": "info", "gap": None, "appeal_status": "written_off", "notes": ""},
]
s = summarize_appeals(rows)
check("total rows", s["total_rows"] == 5)
check("open bucket = open-status rows only", s["open_count"] == 3 and s["open_gap"] == 42.75)
check("appeal buckets keyed with 'none' for NULL",
      s["by_appeal"]["none"] == {"count": 1, "gap": 25.5}
      and s["by_appeal"]["appeal_filed"] == {"count": 2, "gap": 15.25}
      and s["by_appeal"]["appeal_won"]["count"] == 1
      and s["by_appeal"]["written_off"] == {"count": 1, "gap": 0.0})
check("no-rule count is the LITERAL marker only (evidence-first, never inferred)",
      s["no_rule_count"] == 1)
check("string gap coerced", s["by_appeal"]["appeal_won"]["gap"] == 7.25)
check("empty input summarizes cleanly",
      summarize_appeals([]) == {"by_appeal": {}, "open_count": 0, "open_gap": 0.0,
                                "no_rule_count": 0, "total_rows": 0})

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print("  - " + f)
    sys.exit(1)
print(f"ALL CHECKS PASSED ({sum(1 for _ in ALL) ** 2} transition edges + unit checks)")
