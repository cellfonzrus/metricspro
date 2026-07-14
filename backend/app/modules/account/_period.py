"""Canonical period handling for the finance module (account / payables / billing).

The recurring finance bug is the period-spelling duality: uploads across the app store the same
month under two spellings — the daily-sales / ePay path writes the month-NAME form ("June 2026")
while compute is invoked with the numeric form ("2026-06"). A Supabase filter that queries only
ONE spelling silently returns no rows → the retail P&L came out $0 (fixed once in `coa.build_inputs`,
commit 458d5ec). `coa`, `recon`, and `residual_subs` each re-implemented this — three independent
copies plus two divergent `parse_period`s — so every new filter risked forgetting the other spelling.

This is the single source of truth. Every period-filtered query in the finance tree routes its
`.in_("period", …)` value through `period_keys()`, and every finance module parses a period string
through `parse_period()`. It is deliberately module-local (NOT in `core/**`) so it unblocks the
finance tree without a shared-file escalation.
"""

_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def parse_period(period: str):
    """'June 2026' -> (6, 2026). Also accepts the numeric 'YYYY-MM' form.

    Returns (month, year); (0, 0) when unparseable. Robust across BOTH spellings — unlike the
    month-name-only `commcalc.calculator.parse_period`, the numeric form is parsed correctly
    (that variant returned month=1 for '2026-06'). Behaviour is byte-identical to the previous
    `coa.parse_period` (which this replaces as the finance-wide canonical parser)."""
    months = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
              "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
    p = (period or "").strip().lower()
    if "-" in p and p.split("-")[0].isdigit():
        y, m = p.split("-")[:2]
        return int(m), int(y)
    parts = p.split()
    mo = months.get(parts[0], 0) if parts else 0
    yr = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return mo, yr


def period_keys(period):
    """Every period-string spelling a `.in_("period", …)` filter must match for `period`:
    the literal input string, PLUS the month-name form ('June 2026'), PLUS the zero-padded
    numeric form ('2026-06').

    This ONLY EVER ADDS the missing spelling — the caller's literal input is always kept, so a
    query that already matched a set of rows never loses one; it can only pick up the same month
    written under the other spelling (the whole point of the fix). Order-insensitive: the value
    feeds an IN clause. Superset of the previous per-file constructions:
      • coa.build_inputs  {period} | {month-name}          → adds numeric
      • recon._period_keys {period} | {month-name}          → adds numeric
      • residual._recent_labels {month-name, numeric}        → identical (per month)
    """
    keys = {period}
    pm, py = parse_period(period)
    if 1 <= pm <= 12 and py:
        keys.add(f"{_MONTHS[pm]} {py}")
        keys.add(f"{py}-{pm:02d}")
    return list(keys)


def recent_period_keys(latest_y, latest_m, n):
    """The last `n` months ending at (latest_y, latest_m), flattened to every spelling via
    `period_keys` — for a bounded multi-month `.in_("period", …)` sweep (residual_subs' fallback
    aggregation). Byte-identical set to the previous `_recent_labels` (month-name + 'YYYY-MM'
    per month); order is irrelevant to an IN clause."""
    out, y, m = [], latest_y, latest_m
    for _ in range(max(1, n)):
        out.extend(period_keys(f"{y}-{m:02d}"))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out
