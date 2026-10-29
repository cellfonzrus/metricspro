"""A STATUS LINE THAT MUST FIT A COLUMN KEEPS ITS ENDS — the one home.

Found 2026-10-27, from the owner's own report (*"Is all the data being ingested properly for kpi,
Ismail reported that data was off"*). The DLAR sweep's §COMMCALC-16 diagnostic was working: its
2026-10-26 run DID detect that the carrier had renamed the store report's unit-count fields, and the
line it wrote named them. But what the operator could read ended mid-sentence:

    ⚠ PARTIAL — October 2026: 28 stores written as of 2026-10-24 … ⚠ COLUMN STOPPED ARRIVING:
    raw_dlar_store.gross_adds (0 of 28 rows), … ⚠ PORTAL FIELD CHANGED: store (DLAR):
    'gross_activation_quantity' NOT SENT, … store (DLAR): 'upgrades' NOT SENT,          <-- 600 chars

`router._sweep_set_status` stored `detail[:600]`, and the part the whole mechanism exists to deliver —
**FIELDS THE PORTAL SENDS THAT NOTHING READS**, which carries the renamed field's NEW spelling — is
written LAST, so it is exactly what a head truncation throws away. The detection worked and the
finding was destroyed on the way to the column.

THE CLASS, NOT THE INSTANCE. `detail[:600]` is not DLAR's; it is every portal sweep's, in one shared
writer. And the rule is general:

> **A length cap on a human-read status must not decide WHICH part of it survives. Keep both ends and
> elide the middle, and say how much was dropped.**

A head truncation silently privileges whatever happens to be written first, which is usually the
restatement of what already went fine. Keeping both ends means a growing middle (one row per changed
column, one per store) can never push the conclusion out, and the elision marker tells the reader a
number is missing rather than letting a cut sentence read as the end of the story.

PURE: stdlib only, no DB, no network. Proof: `backend/harness_status_detail.py`.
"""

# What the elision says. It carries the COUNT, because "…" alone cannot be told from a sentence that
# happens to trail off — which is how the 2026-10-26 line read.
MARK = " …[%d chars elided]… "


def fit(detail, limit=600, keep_tail=None):
    """A status line bounded to `limit` characters that KEEPS ITS ENDS. PURE.

    Returns `detail` unchanged when it already fits (so nothing about a healthy line changes). When it
    does not, the head and the tail are both kept with the middle replaced by `MARK`, which names how
    many characters were dropped.

    `keep_tail` is how much of the END to protect; the default gives the tail a little under half the
    budget, which is enough for the conclusion of a sweep line and still leaves the opening readable.
    A `limit` too small for both ends plus the marker degrades to the plain head cut — it cannot do
    better, and it must never raise inside a status writer whose whole job is to record what happened.

    `None` and non-strings come back as `''` / `str(...)`: a status writer is the last place that may
    throw."""
    s = "" if detail is None else str(detail)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return s
    if limit <= 0:
        return ""
    if len(s) <= limit:
        return s
    tail = keep_tail if keep_tail is not None else max(1, (limit * 4) // 9)
    try:
        tail = int(tail)
    except (TypeError, ValueError):
        tail = max(1, (limit * 4) // 9)
    dropped = len(s) - limit
    mark = MARK % dropped
    # grow the marker's own length into the budget, then re-measure: the marker's digits change the
    # arithmetic, so compute it once and settle on the result rather than iterating.
    room = limit - len(mark)
    if room < 2 or tail >= room:
        return s[:limit]
    head = room - tail
    out = s[:head] + mark + s[len(s) - tail:]
    # the marker's digit count can shift by one as `dropped` changes; never exceed the cap
    return out[:limit] if len(out) > limit else out
