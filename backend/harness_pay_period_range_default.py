"""Differential proof for the payroll range picker's DEFAULT + prev/next stepping (2026-07-25 owner
follow-up: "the date range by default should be as defined in the payroll time period").

The frontend (storeops/reports/page.tsx, storeops/payroll/page.tsx, via
../lib/pay-period.ts) NEVER reimplements the tenant pay-period boundary math
(work_week_start_dow anchoring, biweekly_anchor snapping, payday resolution) — core/** is a shared
file no module agent may edit (AGENT_CONTRACT §1), and duplicating server logic client-side is
exactly the drift the contract warns about. Instead it:
  1. takes the DEFAULT range verbatim from `GET /api/v1/core/tenant-settings`'s `preview[0]`, which
     IS `pay_period_for(settings, today)` (that's what `_next_periods()` computes as its first
     element) — proof group A below locks in that equivalence.
  2. steps prev/next by a FIXED period length (7 or 14 days) without ever calling pay_period_for
     again — safe ONLY because pay_period_for's periods are contiguous on a fixed grid once anchored
     (the biweekly_anchor snap only ever runs relative to `ref`, and every subsequent period boundary
     falls exactly `length` days from the last) — proof group B verifies that contiguity guarantee
     holds for a spread of configs (weekly/biweekly, every work_week_start_dow, multiple biweekly
     anchors) and reference dates (including exact boundary days and a year-crossing case), so the
     client's "shift by length" is PROVABLY equivalent to calling pay_period_for again at the
     stepped reference date.

This harness imports (never edits) `app.modules.core.router`'s real, unmodified
pay_period_for/_pp_settings/_next_periods — the exact functions GET /tenant-settings calls.
Run: `python3 harness_pay_period_range_default.py` from backend/.
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

from app.modules.core.router import pay_period_for, _pp_settings, _next_periods  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


def cfg(**kw):
    return _pp_settings(kw)


CONFIGS = {
    "weekly-mon (Boost default)":     cfg(pay_period_type="weekly", work_week_start_dow=0),
    "weekly-thu (luxelink-style)":    cfg(pay_period_type="weekly", work_week_start_dow=3),
    "biweekly-mon no-anchor":         cfg(pay_period_type="biweekly", work_week_start_dow=0),
    "biweekly-mon anchor 2026-01-05": cfg(pay_period_type="biweekly", work_week_start_dow=0, biweekly_anchor="2026-01-05"),
    "biweekly-thu anchor 2026-01-01": cfg(pay_period_type="biweekly", work_week_start_dow=3, biweekly_anchor="2026-01-01"),
}

# ══ Group A: default range == pay_period_for(today) — the exact identity /tenant-settings' preview[0]
#             (what the frontend takes as its default) is built from. Locks in that GET /tenant-settings
#             can never silently drift from "today's real pay period" without this proof catching it.
today = date.today()
for name, s in CONFIGS.items():
    direct = pay_period_for(s, today)
    preview0 = _next_periods(s, 1)[0]
    check(f"A: default range == pay_period_for(today) [{name}]", direct == preview0, (direct, preview0))

# ══ Group B: contiguity — client-side "shift by fixed length" reproduces calling pay_period_for again
#             at the stepped reference date, for EVERY config across a spread of reference dates
#             (ordinary mid-period days, exact period-start/-end boundary days, and a year-crossing case).
REF_DATES = [
    date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 19),   # near a biweekly anchor
    date(2026, 3, 15), date(2026, 6, 30), date(2026, 7, 25),  # "today" per the dispatch
    date(2026, 12, 28), date(2027, 1, 4),                     # year-crossing
]

for name, s in CONFIGS.items():
    length = 14 if s.get("pay_period_type") == "biweekly" else 7
    for ref in REF_DATES:
        p0 = pay_period_for(s, ref)
        p0_start = date.fromisoformat(p0["start"])
        p0_end = date.fromisoformat(p0["end"])

        # forward: client math (fixed +length days) vs. the real backend re-queried at the next ref
        client_fwd = {"start": (p0_start + timedelta(days=length)).isoformat(),
                      "end": (p0_end + timedelta(days=length)).isoformat()}
        real_fwd = pay_period_for(s, p0_end + timedelta(days=1))
        check(f"B-fwd: step(+1) == pay_period_for(next ref) [{name} @ {ref}]",
              client_fwd["start"] == real_fwd["start"] and client_fwd["end"] == real_fwd["end"],
              (client_fwd, real_fwd))

        # backward: client math (fixed -length days) vs. the real backend re-queried at the prior ref
        client_bwd = {"start": (p0_start - timedelta(days=length)).isoformat(),
                      "end": (p0_end - timedelta(days=length)).isoformat()}
        real_bwd = pay_period_for(s, p0_start - timedelta(days=length))
        check(f"B-bwd: step(-1) == pay_period_for(prior ref) [{name} @ {ref}]",
              client_bwd["start"] == real_bwd["start"] and client_bwd["end"] == real_bwd["end"],
              (client_bwd, real_bwd))

        # sanity: period really is `length` days end-to-end (guards the length constant itself)
        check(f"B-len: period spans exactly {length} days [{name} @ {ref}]",
              (p0_end - p0_start).days == length - 1, p0)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
