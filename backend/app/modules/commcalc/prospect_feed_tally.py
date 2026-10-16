"""HOW MUCH HAS THIS PROSPECT GOT — the ONE home for the per-feed, per-month row tally.

OWNER REPORT 2026-10-16: *"Store Takeover Evaluation takes along time to load , and existing
evaluation is not in the pull up list"*. One cause, both complaints.

THE CLASS, NOT THE INSTANCE (owner directive 2026-09-20). The instance was "the takeover list is
slow". The class is one sentence:

    **a cheap primary answer must never be withheld until an expensive decoration beside it is
    ready.**

`GET /commcalc/prospects` answers "which stores am I evaluating" — two rows, one indexed query,
milliseconds. It then DECORATED each row with "and how many rows has each feed got in each month",
and it computed that decoration by SHIPPING EVERY FEED ROW TO PYTHON AND COUNTING THEM.

Measured live 2026-10-16, acting org: `commcalc.prospect_feed_row` holds **202,865** rows
(137,374 + 65,491 across two prospects). At the platform's 1,000-row page that is **203 HTTP round
trips**, timed from a container at **0.289 s/page — about 59 seconds**, and it grows with every
upload. The list the owner was looking for was therefore EMPTY for a minute while a count he had not
asked for was assembled, and `gunicorn --timeout 120` with two workers means the same read both
occupies half the platform's capacity and is on a path to being killed outright as the feed grows.

WHAT THIS MODULE IS. The tally's one home, in the shape BOTH paths must produce:

    {prospect_id: {period: {feed_key: n}}}

- `from_grouped` builds it from counts the DATABASE grouped (one round trip, the fast path).
- `from_rows` builds it from the feed rows themselves (the fallback, for a database that does not
  yet carry the grouping function).

They are two readings of one fact, so they go through ONE accumulator (`_add`) and are asserted
equal on equivalent input by `harness_prospect_feed_tally.py`. A second shape builder beside this
one is the divergence the index rules forbid.

UNKNOWN IS NOT ZERO. `periods_of({})` is `[]` for a prospect with nothing uploaded, which is a FACT;
a prospect whose tally has not been read yet is absent from the mapping entirely, and the screen
must say so rather than print "nothing yet" over a feed it simply has not counted. `missing_tally`
names that difference so no caller has to re-derive it, and the harness holds it: reporting an
uncounted prospect as empty is the quiet wrongness this platform treats as a defect.

NO DB AND NO NETWORK IN THIS MODULE. The caller hands in rows or grouped counts, so the proof
harness drives the same accumulator with no database at all.
"""
from __future__ import annotations

# The keys the tally is cut on, named once. `period` is carried as the empty string when a row has
# no month stamped, never dropped: a row that landed with no month is still a row somebody uploaded.
PROSPECT = "prospect_id"
FEED = "feed_key"
PERIOD = "period"
COUNT = "n"


def _add(acc: dict, prospect, feed, period, n):
    """THE accumulator. Both paths reach the shape through here, so they cannot drift."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return
    if n <= 0:
        return
    p = str(prospect or "").strip()
    f = str(feed or "").strip()
    if not p or not f:
        return
    per = str(period or "")
    by_period = acc.setdefault(p, {}).setdefault(per, {})
    by_period[f] = by_period.get(f, 0) + n


def from_grouped(grouped) -> dict:
    """The tally from counts the DATABASE grouped — the fast path.

    Each entry carries the three keys and a count; anything else in the row is ignored, so the
    function survives a grouping that grows a column.
    """
    acc: dict = {}
    for r in grouped or []:
        if not isinstance(r, dict):
            continue
        _add(acc, r.get(PROSPECT), r.get(FEED), r.get(PERIOD), r.get(COUNT))
    return acc


def from_rows(rows) -> dict:
    """The tally from the feed rows themselves — the fallback, one row at a time, each worth one."""
    acc: dict = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        _add(acc, r.get(PROSPECT), r.get(FEED), r.get(PERIOD), 1)
    return acc


def periods_of(by_period) -> list:
    """The months a prospect has, newest first — the order the month picker and the table both want.

    Derived from the tally rather than stored beside it, so a month cannot appear in one and not the
    other. Sorted as text, which is the platform's own month spelling and sorts newest-first in
    reverse for '2026-08'; a differently spelled month still sorts stably rather than at random.
    """
    return sorted((by_period or {}).keys(), reverse=True)


def missing_tally(prospect_ids, tally) -> list:
    """The prospects whose tally is NOT KNOWN, as opposed to known-and-empty.

    A caller prints "nothing uploaded yet" only for a prospect absent from this list. This is the
    one place the difference is decided, because getting it wrong prints a confident zero over data
    that exists — see the module docstring.
    """
    have = set(tally or {})
    return [str(p) for p in (prospect_ids or []) if str(p) not in have]


def by_feed(by_period) -> dict:
    """`{feed_key: [periods]}` — the same tally read the other way round.

    `_pev.default_period` asks "which months has each feed got rows for", and the only way the
    evaluation endpoint had to answer it was to READ EVERY ROW THE PROSPECT HAS and look at two
    columns of each: 137,374 rows and ~137 round trips for one prospect, measured 2026-10-16, purely
    to decide which month to open on. That is the same defect as the list's, in the endpoint next
    door — so it dereferences this instead, and the fact has one home for both.

    A period appears once per feed it has rows for, and a month with a zero count cannot appear at
    all because `_add` never records one.
    """
    out: dict = {}
    for period, feeds in (by_period or {}).items():
        for feed, n in (feeds or {}).items():
            if (n or 0) > 0:
                out.setdefault(str(feed), []).append(period)
    return {f: sorted(ps, reverse=True) for f, ps in out.items()}
