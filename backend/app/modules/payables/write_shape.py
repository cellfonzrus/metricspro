"""ONE home for *"can this relation actually accept the row this build is about to write?"* — asked
BEFORE anything is deleted.

Index §PAYABLES-01. Lock: `backend/harness_payable_rebuild_preflight.py`.

WHY THIS EXISTS, measured rather than imagined. On 2026-10-27, `engine.build_ledger` was run against
production for the house org. `_build_one_carrier` deletes a carrier's rows and then inserts the
rebuilt ones, so it deleted all 11,000 Boost/VIP rows and then failed to insert a single one:

    PGRST204  Could not find the 'rebate_basis' column of 'device_payable_ledger' in the schema cache

`build_ledger` catches per carrier so one bad config cannot abort the rest, so the call RETURNED
NORMALLY — `{"written": 0, "per_carrier": [{"error": "..."}]}`, HTTP 200 — describing a table it had
just emptied. The rows came back only because a snapshot had been taken first. Pressing
*↻ Rebuild ledger* in the UI does exactly the same thing, with no snapshot.

THE CLASS, named so the fix is not about one column: **a rebuild must never delete what it has not
established it can replace.** The instance was "the code is ahead of the production schema" (1093
and 1094 were skipped while 1095, 1098 and 1099 went in — migrations are applied by hand here, so a
landed number is no evidence the ones below it landed). The class is wider: any write the relation
refuses — a dropped column, a renamed one, a revoked grant — turns delete+insert into delete.

THE CHECK CAN ONLY FAIL WHERE THERE IS SOMETHING TO LOSE, which is what makes it safe to apply
everywhere. The live column set is read from a sample row of the relation itself. When the relation
is EMPTY there is no sample — and equally nothing to destroy — so the build proceeds exactly as
before. The guard costs one `select` per carrier and changes no arithmetic.

WHY A SAMPLE ROW AND NOT THE CATALOGUE. This backend reaches the database only through PostgREST
(there is no direct SQL path — see the house conventions on migration grants), and
`information_schema` is not exposed through it. A sample row's KEYS are the relation's columns:
PostgREST returns every column of the row, NULL ones included, so the key set is the column set and
not merely "the columns this row filled in".

NO CARRIER, TENANT, PRODUCT OR COLUMN NAME APPEARS HERE (RULE TWO). The caller passes the row it
intends to write and the relation it intends to write to; this module compares the two and says no.
"""


class WriteShapeRefused(Exception):
    """The relation cannot accept the row this build produces, so nothing was deleted.

    Carries the relation and the missing columns so a surface can tell a reader WHICH declaration is
    absent — which is the difference between "the rebuild is broken" and "two migrations have not
    been applied yet".
    """

    def __init__(self, relation, missing, label=""):
        self.relation = relation
        self.missing = list(missing)
        self.label = label or ""
        who = (" for %s" % self.label) if self.label else ""
        super().__init__(
            "refused to rebuild%s: %s cannot accept %d column(s) this build writes (%s). Nothing "
            "was deleted. The database is behind the code — apply the migration that adds them, "
            "then rebuild." % (who, relation, len(self.missing), ", ".join(self.missing)))


def live_columns(client, relation, schema="commcalc"):
    """The relation's live column set, or None when it cannot be established.

    None is deliberately NOT an empty set: "I could not look" and "it has no columns" must not read
    the same, because the first means *proceed, there is nothing to lose* and the second would mean
    *refuse everything*. An empty relation and an unreadable one both answer None.
    """
    try:
        rows = client.schema(schema).table(relation).select("*").limit(1).execute().data or []
    except Exception:                                    # pragma: no cover - I/O guard
        return None
    if not rows or not isinstance(rows[0], dict):
        return None
    return set(rows[0].keys())


def missing_columns(row, live):
    """PURE: the keys of `row` the relation has no column for. Empty when `live` is None (unknown),
    because an unknown column set can refuse nothing — see `live_columns`."""
    if not live:
        return []
    return sorted(set(row or {}) - set(live))


def refuse_if_unwritable(client, relation, sample_row, schema="commcalc", label=""):
    """Raise `WriteShapeRefused` when `relation` cannot accept `sample_row`. Returns the live column
    set (or None when it could not be established).

    CALL THIS BEFORE THE DELETE, never after: its whole purpose is that the refusal arrives while the
    old rows are still there. Every row of one build carries the same keys, so one sample answers for
    the batch.
    """
    live = live_columns(client, relation, schema)
    missing = missing_columns(sample_row, live)
    if missing:
        raise WriteShapeRefused(relation, missing, label)
    return live
