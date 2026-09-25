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
# every spelling of a month name a person types: the full name, its 3-letter abbreviation, 'sept'
_MONTH_LOOKUP = {m.lower(): i for i, m in enumerate(_MONTHS) if m}
_MONTH_LOOKUP.update({m[:3].lower(): i for i, m in enumerate(_MONTHS) if m})
_MONTH_LOOKUP["sept"] = 9


def parse_period(period: str):
    """'June 2026' -> (6, 2026). Also accepts the numeric 'YYYY-MM' form.

    Returns (month, year); (0, 0) when unparseable. Robust across BOTH spellings — unlike the
    month-name-only `commcalc.calculator.parse_period`, the numeric form is parsed correctly
    (that variant returned month=1 for '2026-06'). Behaviour is byte-identical to the previous
    `coa.parse_period` (which this replaces as the finance-wide canonical parser).

    Since 2026-09-22 (index §30.15) an ABBREVIATED month name ('Aug 2026', 'aug 2026', 'Sept 2026')
    and a one-digit month ('2026-8') parse too — a spelling the platform accepted at a landing must
    be one it can read back. Everything that parsed before parses to the same answer."""
    p = (period or "").strip().lower()
    if "-" in p and p.split("-")[0].isdigit():
        y, m = p.split("-")[:2]
        try:
            return int(m), int(y)
        except ValueError:
            return 0, 0
    parts = p.split()
    mo = _MONTH_LOOKUP.get(parts[0], 0) if parts else 0
    yr = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return mo, yr


def canonical_period(period):
    """THE ONE spelling of a month-period the platform STORES: the month-NAME form ('August 2026' —
    the first entry `period_keys` lists). A string that is not a month-period (or is blank) comes
    back stripped, unchanged: this never invents a period. Every ledger landing canonicalises through
    this (commission_ledger.canonical_period dereferences it), so 'Aug 2026' / 'aug 2026' / '2026-08'
    typed at an upload all land as 'August 2026' and every reader finds them (index §30.15)."""
    raw = str(period or "").strip()
    pm, py = parse_period(raw)
    if 1 <= pm <= 12 and py:
        return f"{_MONTHS[pm]} {py}"
    return raw


def is_canonical_period(period):
    """True when `period` is spelled exactly the way canonical_period would store it (or is not a
    month-period at all). A stored spelling that is NOT canonical is an ORPHAN no period_keys()
    reader finds — the class of the 'aug 2026' copies (index §30.15)."""
    raw = str(period or "").strip()
    return canonical_period(raw) == raw


def period_keys(period):
    """Every period-string spelling a `.in_("period", …)` filter must match for `period`:
    the month-name form ('June 2026' — the CANONICAL spelling, listed FIRST), the zero-padded
    numeric form ('2026-06'), PLUS the literal input string when it is neither.

    This ONLY EVER ADDS the missing spelling — the caller's literal input is always kept, so a
    query that already matched a set of rows never loses one; it can only pick up the same month
    written under the other spelling (the whole point of the fix). The ORDER is deterministic since
    2026-09-22 (canonical first) so `period_keys(p)[0]` is the spelling a landing stores; as the
    value of an IN clause the order is irrelevant. Superset of the previous per-file constructions:
      • coa.build_inputs  {period} | {month-name}          → adds numeric
      • recon._period_keys {period} | {month-name}          → adds numeric
      • residual._recent_labels {month-name, numeric}        → identical (per month)
    """
    raw = str(period or "").strip() if period is not None else period
    pm, py = parse_period(raw or "")
    if 1 <= pm <= 12 and py:
        keys = [f"{_MONTHS[pm]} {py}", f"{py}-{pm:02d}"]
        for lit in (period, raw):
            if lit not in keys:
                keys.append(lit)
        return keys
    return [period] if period == raw else [period, raw]


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


def month_range(period_from, period_to=None, max_months=None):
    """THE ONE month enumeration: every month from `period_from` to `period_to` INCLUSIVE, oldest first,
    each in the canonical stored spelling ('July 2026'). Either spelling is accepted on the way in (the
    `parse_period` rules); a missing `period_to` is the single month `period_from`.

    Raises ValueError — never guesses — on an unparseable bound, a reversed range, or more than
    `max_months` months (None = no cap). Callers that loop a per-month computation over a range
    (the Rep Incentive month range, the discrepancy appeals period filter) enumerate through this, so
    no two ranges can disagree about which months a window holds. PURE."""
    pf = str(period_from or "").strip()
    pt = str(period_to or "").strip() or pf
    m0, y0 = parse_period(pf)
    m1, y1 = parse_period(pt)
    if not (1 <= m0 <= 12 and y0):
        raise ValueError(f"not a month period: {period_from!r} (use YYYY-MM)")
    if not (1 <= m1 <= 12 and y1):
        raise ValueError(f"not a month period: {period_to!r} (use YYYY-MM)")
    n = (y1 - y0) * 12 + (m1 - m0) + 1
    if n < 1:
        raise ValueError("the from-month is after the to-month")
    if max_months is not None and n > max_months:
        raise ValueError(f"month range too long: {n} months (max {max_months})")
    out = []
    for i in range(n):
        yy, mm = y0 + (m0 - 1 + i) // 12, (m0 - 1 + i) % 12 + 1
        out.append(f"{_MONTHS[mm]} {yy}")
    return out
