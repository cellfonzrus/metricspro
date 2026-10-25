"""AN AVERAGE IS NOT A SUM — the declaration for the Helpdesk dashboard (§CORE-17).

Four tiles and two breakdowns over one table, and the interesting figure is the one that is not a
fold at all.

**"AVG RESOLUTION (HRS)" IS A MEAN OVER A SUBSET, AND THE TILE NEVER SAID WHICH.** It is computed
only from tickets that have BOTH a `created_at` and a `resolved_at`, so on a month with 200 tickets
of which 12 are resolved, the tile is a measurement of 12 and reads as a measurement of 200. There
is no set of tickets that ADDS UP to "4.2 hours", so this figure gets NO panel of its own — a
tie-out against it could only ever be a check that cannot fail. What it gets instead is a panel for
its DENOMINATOR, "tickets this average measures", and the unresolved tickets appear in it, uncounted,
with `not_resolved` as their reason. That is the §CORE-17 ruling for a rate or an average, written
once in `crm/reports_drilldown.py` and applied here: **offer the counts it divides, never the
quotient.**

TWO TABLES, AND THE SECOND ONE DECIDES THE ANSWER. A ticket's lifecycle stage is not on the ticket —
the ticket carries a `status_id` and `ticket_statuses` maps it to a stage. So the Open and
Resolved/Closed tiles, and the whole "By lifecycle stage" breakdown, depend on that table, and an
unmapped `status_id` silently reads as "open" (`st.get(...) or {}).get("stage") or "open"`). A row
whose status is unmapped therefore says so.

THE AGING BREAKDOWN IS KEYED ON NOW, NOT ON THE PERIOD. "Open-ticket aging" buckets an open ticket
by how old it is at the moment the page loaded, while every other figure on the screen is keyed on
`created_at` inside the chosen range. Both are correct and they are not the same question, which is
why the aging rows say when they were measured rather than leaving a reader to assume they match.
"""
from app.modules.core import report_drilldown

#: One ticket's address.
ROW_KEYS = ("id",)

#: Where a ticket lives.
TICKET = ("tickets",)
#: The status vocabulary, which is what turns a `status_id` into a lifecycle stage. It DECIDES the
#: stage figures, so it is provenance, not decoration.
STATUS = ("ticket_statuses",)

#: The stages `dashboard` folds into. Read as a tuple so a stage added to the endpoint cannot leave
#: a tile here counting the wrong tickets.
STAGES = ("open", "pending", "done")
#: The stages the Open tile counts — the endpoint's own definition of an open ticket.
OPEN_STAGES = ("open", "pending")


def _label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("subject") or r.get("id") or "(no subject)",
        str(r.get("created_at") or "?")[:10],
    ))


def _fields(stage_of, status_label):
    """A ticket's own columns. Both readers are passed IN, resolved from the same `_maps` call the
    endpoint already made — a second read of the vocabulary here would be a second copy of it."""
    def read(r):
        r = r or {}
        return {"stage": stage_of(r) if stage_of else None,
                "status": status_label(r.get("status_id")) if status_label else None,
                "status_mapped": bool(status_label and status_label(r.get("status_id"))),
                "opened": str(r.get("created_at") or "")[:19].replace("T", " "),
                "resolved": (str(r.get("resolved_at") or "")[:19].replace("T", " ")
                             if r.get("resolved_at") else None),
                "hours": r.get("_resolution_hours")}
    return read


def _resolved(r):
    """Finished, and therefore measurable: the endpoint needs BOTH timestamps before a ticket can
    contribute a duration, so this reads the same pair."""
    r = r or {}
    return bool(r.get("resolved_at") and r.get("created_at"))


def _tables(status_label):
    """A ticket's provenance. `ticket_statuses` rides along where the status actually resolved,
    because that table is what decides the stage — and where it did NOT resolve, it is left off, so
    a reader can see which rows fell back to "open" rather than being told so."""
    def read(r):
        mapped = bool(status_label and status_label((r or {}).get("status_id")))
        return TICKET + (STATUS if mapped else ())
    return read


def _unmapped_reason(status_label):
    def read(r):
        return None if (status_label and status_label((r or {}).get("status_id"))) else "not_recorded"
    return read


def _measured_reason():
    """Why a ticket contributes nothing to the AVERAGE — and for this figure there is exactly one
    answer: it is not finished. Whether its status is in the status list has no bearing on a
    duration, so the generic unmapped-status reason is NOT inherited here; it would explain the
    wrong thing on the one panel a reader opens to understand the mean.

    Caught by `harness_batch8_drilldown.py` §E5, which found the unfinished tickets shown, uncounted
    and unexplained under the shared reason, which is the gap this panel exists to close rather than
    reproduce."""
    def read(_r):
        return None if _resolved(_r) else "not_resolved"
    return read


def _one_if(pred):
    def read(r):
        return 1 if pred(r or {}) else None
    return read


_AVG_NOTE = ("The average resolution time on this screen is a mean over these tickets only — the "
             "unresolved ones below contribute no duration at all. An average has no tickets that "
             "add up to it, so it opens to this count rather than to a panel of its own.")


def catalogue(*, stage_of=None, status_label=None):
    common = {"row_label": _label, "keys": ROW_KEYS, "schema": "public",
              "fields": _fields(stage_of, status_label), "tables": _tables(status_label),
              "unit": report_drilldown.UNIT_COUNT, "reason": _unmapped_reason(status_label)}
    out = [
        {"key": "total", "label": "Tickets raised in this period", "total": "total",
         "value": _one_if(lambda _r: True),
         "note": ("Keyed on when the ticket was raised, which is what the period filter covers. The "
                  "aging breakdown is keyed on now instead, so the two answer different questions."),
         **common},
        {"key": "open", "label": "Tickets still needing someone", "total": "open",
         "value": _one_if(lambda r: (stage_of(r) if stage_of else "open") in OPEN_STAGES),
         "in_figure": (lambda r: (stage_of(r) if stage_of else "open") in OPEN_STAGES),
         "note": ("A ticket whose status is not in the status list falls back to \"open\" on this "
                  "screen, so it is counted here. Those rows name no status list and say so."),
         **common},
        # THE DENOMINATOR OF THE AVERAGE, which is the honest thing to serve for it.
        {"key": "measured", "label": "Tickets the average resolution time measures",
         "total": "measured", "value": _one_if(_resolved), "in_figure": _resolved,
         "note": _AVG_NOTE, **dict(common, reason=_measured_reason())},
    ]
    for st in STAGES:
        out.append({"key": "stage_%s" % st, "label": "Tickets at the %s stage" % st,
                    "total": "stage_%s" % st,
                    "value": _one_if(lambda r, _s=st: (stage_of(r) if stage_of else "open") == _s),
                    "in_figure": (lambda r, _s=st: (stage_of(r) if stage_of else "open") == _s),
                    **common})
    return tuple(out)


def totals(tickets, *, stage_of=None):
    """PURE: the figures the dashboard's tiles and its stage breakdown show, folded from the SAME
    tickets the panels are built from — and `measured`, the average's denominator, which the screen
    has never shown and which is the only honest thing to put behind that tile.

    IT REPLACES NOTHING. The endpoint keeps its own `total` / `open` / `by_stage` / `avg_resolution_
    hours`; this exists so `tiles()` has a figure to tie each panel against, and so the harness can
    assert the two folds agree. Two folds that must agree, checked, beat one fold nobody can check.
    """
    rows = tickets or []
    out = {"total": len(rows), "open": 0, "measured": 0}
    for st in STAGES:
        out["stage_%s" % st] = 0
    for r in rows:
        st = (stage_of(r) if stage_of else "open") or "open"
        if ("stage_%s" % st) in out:
            out["stage_%s" % st] += 1
        if st in OPEN_STAGES:
            out["open"] += 1
        if _resolved(r):
            out["measured"] += 1
    return out


def drilldowns(tickets, figures, *, stage_of=None, status_label=None, cap=None):
    return report_drilldown.tiles(catalogue(stage_of=stage_of, status_label=status_label),
                                  tickets, figures, cap=cap or report_drilldown.ROW_CAP)
