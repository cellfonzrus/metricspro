"""READ EVERY ROW — the ONE home for "give me all of this feed", with no hard-coded ceiling.

THE CLASS, NOT THE INSTANCE (owner directive 2026-09-20). The instance was "Boost's July commission
is half what the carrier paid". The class is:

    **a read with a literal row ceiling silently becomes a read of PART of the data the day the
    data outgrows the number somebody typed.**

Measured live 2026-10-06, house org: `commcalc.raw_payment_detail` holds **82,999** rows for July
2026. The commission engine's own input loader read it with `.limit(50000)`, so the pay run saw 60%
of the month and computed $60,994.46 of carrier commission where the feed holds $123,700.62 — and
12 of 122 rep logins disappeared entirely. The ceiling was hard-coded in six places with three
different values (50,000 twice, 60,000 three times), not one of them derived from anything, and the
loader wrapped the read in `except: return []` so crossing it looked exactly like an empty month.

`raw_mi` is the next one over: 46,047 rows in September and 45,280 in October, growing ~4,000 a
month. It crosses 50,000 within weeks.

WHAT THIS IS. One paged read loop. `read_all` keeps asking for the next page until a short page says
there are no more, so the row count is a property of the DATA, never of a literal. It replaces the
~20 private copies of the same `range(start, start + page - 1)` loop that already existed across
`asset/`, `account/` and `commcalc/` — each correct on its own, none of them shared, which is the
§19.18 defect in the shape of its own cure.

WHAT IT REFUSES TO DO. There is no `limit=` parameter and no default cap. A caller that genuinely
wants the top N rows is asking a different question and should order and slice explicitly; a caller
that wants "the data" gets the data. `harness_feed_read_lock.py` fails the build if a pay-path read
re-introduces a literal row ceiling.

NO DB AND NO NETWORK IN THIS MODULE. The caller hands in a factory that returns a fresh query
builder, so a proof harness drives the same loop over a fake table.
"""
from __future__ import annotations

# PostgREST serves at most 1,000 rows per request on the hosted tier, so this is the PAGE SIZE —
# how much comes back per round trip — and never a limit on the total. Verified live 2026-10-06:
# an explicit `limit=200000` really does return all 73,678 `raw_comp_report` rows, so the ceiling
# that broke July was ours, not the platform's.
PAGE = 1000

# A page loop must terminate even if a backend keeps serving full pages forever. At PAGE=1000 this
# allows 20 million rows — four orders of magnitude above anything this platform holds — so it can
# only ever fire on a bug, and when it fires it RAISES rather than returning a short answer.
MAX_PAGES = 20_000


class IncompleteRead(RuntimeError):
    """A paged read could not be completed. Raised, never swallowed: a partial feed read must not
    be mistaken for a smaller feed. This is the whole point of the module — the failure mode that
    cost July $62,700 was a read that failed quietly and looked like less data."""


def read_all(make_query, page: int = PAGE):
    """Every row `make_query()` matches, as one list, oldest page first.

    `make_query()` must return a FRESH query builder each call — postgrest builders carry the range
    they were last given, so reusing one silently re-requests the same window forever.

    Raises `IncompleteRead` rather than returning a partial list, so no caller can mistake a failed
    read for an empty feed.
    """
    page = max(1, int(page or PAGE))
    out: list = []
    start = 0
    for _ in range(MAX_PAGES):
        try:
            resp = make_query().range(start, start + page - 1).execute()
        except Exception as e:  # noqa: BLE001 - re-raised as IncompleteRead, never swallowed
            raise IncompleteRead(
                f"paged read failed at offset {start} after {len(out)} rows: {e}") from e
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < page:
            return out
        start += page
    raise IncompleteRead(
        f"paged read exceeded {MAX_PAGES} pages ({len(out)} rows) — refusing to return a partial feed")


def table_reader(client, schema: str, table: str, select: str = "*"):
    """A `make_query` factory for a Supabase table: `read_all(table_reader(...))`.

    `apply` is an optional callable that adds the filters (`lambda q: q.eq('org_id', org).in_(...)`).
    Kept here so a caller never has to re-type the schema/table/select triple beside its own copy of
    the page loop.
    """
    def _factory(apply=None):
        def _make():
            q = client.schema(schema).table(table).select(select)
            return apply(q) if apply else q
        return _make
    return _factory
