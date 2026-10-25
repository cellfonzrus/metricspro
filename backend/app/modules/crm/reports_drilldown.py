"""A RATE HAS NO ROWS — the declaration for /crm/reports (§CORE-17).

Three tabs, three questions: how the funnel converts, where leads come from, and who is actually
working the pipeline. The audit's finding was the usual one — a manager reading "win rate 34%" or
"Dana: 212 touches" has no way to the records behind it. But this report brought a shape the earlier
batches did not have, and it is the reason this file says what it says.

**A RATE OR AN AVERAGE GETS NO PANEL OF ITS OWN.** `win_rate` is won ÷ closed. There is no set of
leads that ADDS UP to 34%, so a panel claiming to explain it would be a tie-out that cannot be
checked — the worst kind of reassurance, and exactly what `report_drilldown.tie_out` exists to refuse.
What a reader actually needs is the two COUNTS the rate divides, and both of those have rows. So the
rate's tile offers its numerator and its denominator instead, each with a real panel, and the rate
itself is left undrilled on purpose. The house already settled this shape once — the dashboard's
DM-verified tile serves "verified" and "filed" as two figures rather than one ratio (§CORE-17.3g,
harness §D8) — and this file is that ruling written down rather than re-derived.

**REPORTED, NOT PAPERED OVER — two live defects this report has today.**

1. `pipeline_core.funnel` SILENTLY DROPS a lead whose `stage_id` is not in the stage vocabulary
   (`if b is None: continue`), while `conversion_rates` counts every lead. So Σ(by_stage.count) can
   be LESS than the TOTAL tile on the same screen, and nothing on the screen says so. The panels make
   it visible rather than fixing it here: a lead in no known stage is shown in the per-stage
   population with `stage_unknown` as its reason, and the TOTAL panel counts it. The figures are
   unchanged — a figure is never moved to make a screen read better.
2. `report_activity` returns `{"rows": []}` on ANY exception from its read. An empty rep-activity
   report then reads as "nobody logged anything" when the truth may be that the read failed. That is
   the endpoint's own shape and not this declaration's to change; it is recorded here and in the
   index so the next person does not have to rediscover it.

THE STAGE AND SOURCE VOCABULARIES ARE PART OF THE PROVENANCE, not decoration. A lead's stage count
depends on `crm_stage` (a stage missing from it drops the lead, per defect 1) and its source name
comes from `crm_source`, so the rows that carry a stage or a source name say so.
"""
from app.modules.core import report_drilldown

#: One lead's address, and one logged touch's. Both tables key on their own id.
LEAD_KEYS = ("id",)
ACTIVITY_KEYS = ("id",)

#: Where a lead lives.
LEAD = ("crm_lead",)
#: The stage vocabulary, which DECIDES whether a lead reaches the funnel at all.
STAGE = ("crm_stage",)
#: The source vocabulary, which names where a lead came from.
SOURCE = ("crm_source",)
#: The disposition vocabulary, which names why a lead was lost.
DISPOSITION = ("crm_disposition",)
#: Where a logged touch lives.
ACTIVITY = ("crm_activity",)

#: Said on every tile that is a COUNT the rates divide, so a reader arriving from the rate knows
#: which half of it they are looking at.
_RATE_NOTE = ("Win rate is this figure over the closed leads, and close rate is the closed leads "
              "over the total. A percentage has no leads that add up to it, so the rates open to "
              "these counts instead of to a panel of their own.")


def _lead_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("customer_name") or r.get("phone") or r.get("id") or "(no name)",
        str(r.get("created_at") or "?")[:10],
    ))


def _lead_fields(stage_name, source_name, disposition_name):
    """A lead's own columns. The three vocabulary readers are passed IN, resolved once by the
    endpoint from the same `_vocab` call the report itself used — a second lookup here would be a
    second copy of the vocabulary."""
    def read(r):
        r = r or {}
        return {"state": r.get("status") or "open",
                "stage": stage_name(r.get("stage_id")) if stage_name else None,
                "source": source_name(r.get("source_id")) if source_name else None,
                "lost_reason": (disposition_name(r.get("disposition_id"))
                                if disposition_name and (r.get("status") == "lost") else None),
                "value": r.get("value_estimate"),
                "owner": r.get("owner_employee_id"),
                "store_code": r.get("store_code")}
    return read


def _in_stage_vocabulary(stage_name):
    def read(r):
        return bool(stage_name and stage_name((r or {}).get("stage_id")))
    return read


def _one_if(pred):
    def read(r):
        return 1 if pred(r or {}) else None
    return read


def _status(want):
    def read(r):
        r = r or {}
        return (r.get("status") or "open") == want
    return read


def _lead_tables(stage_name):
    """A lead's provenance. `crm_stage` rides along because the vocabulary is what decides whether
    this lead reaches the funnel at all — a lead whose stage is not in it is dropped by
    `pipeline_core.funnel` while still being counted in the totals."""
    def read(r):
        return LEAD + (STAGE if _in_stage_vocabulary(stage_name)(r) else ())
    return read


def _stage_reason(stage_name):
    def read(r):
        return None if _in_stage_vocabulary(stage_name)(r) else "stage_unknown"
    return read


def lead_catalogue(*, stage_name=None, source_name=None, disposition_name=None):
    common = {"row_label": _lead_label, "keys": LEAD_KEYS, "schema": "public",
              "fields": _lead_fields(stage_name, source_name, disposition_name),
              "tables": _lead_tables(stage_name), "unit": report_drilldown.UNIT_COUNT,
              "reason": _stage_reason(stage_name)}
    return (
        {"key": "total", "label": "Leads in this period", "total": "total",
         "value": _one_if(lambda _r: True),
         "note": ("Every lead the filter covers, whatever stage it is in — including one whose "
                  "stage is not in the stage list, which the funnel below drops. " + _RATE_NOTE),
         **common},
        {"key": "open", "label": "Leads still open", "total": "open",
         "value": _one_if(_status("open")), "in_figure": _status("open"), **common},
        {"key": "won", "label": "Leads won", "total": "won",
         "value": _one_if(_status("won")), "in_figure": _status("won"),
         "note": _RATE_NOTE, **common},
        {"key": "lost", "label": "Leads lost", "total": "lost",
         "value": _one_if(_status("lost")), "in_figure": _status("lost"),
         "note": ("Each row carries the reason recorded against it, which is what the \"why we "
                  "lose\" table counts. " + _RATE_NOTE),
         **dict(common, tables=(lambda r: LEAD + DISPOSITION
                                if (r or {}).get("disposition_id") else LEAD))},
    )


def lead_drilldowns(leads, totals, *, stage_name=None, source_name=None, disposition_name=None,
                    cap=None):
    return report_drilldown.tiles(
        lead_catalogue(stage_name=stage_name, source_name=source_name,
                       disposition_name=disposition_name),
        leads, totals, cap=cap or report_drilldown.ROW_CAP)


# ── One panel per funnel stage, and one per source ────────────────────────────────────────────────
# Both tables are the SAME leads grouped two ways, so they group with the report's own key functions
# rather than a restatement of them — a drill grouped one way beside a table grouped another is the
# grain defect §CORE-17 exists to stop. Same shape as `cash_variance.drilldowns` and
# `envelope_drilldown.by_employee_drilldowns`.

def _group_panel(label, figure_label, figure, group_rows, *, kind, stage_name=None,
                 source_name=None, disposition_name=None, note=None, cap=None):
    read = _lead_fields(stage_name, source_name, disposition_name)
    tables = _lead_tables(stage_name)
    reason = _stage_reason(stage_name)
    rows = [report_drilldown.drill_row(
        _lead_label(r),
        report_drilldown.origins_of([(t, {"id": (r or {}).get("id")}) for t in tables(r)],
                                    schema="public"),
        row_key={"id": (r or {}).get("id")},
        amount=1, in_figure=True, reason=reason(r), fields=read(r))
        for r in (group_rows or [])]
    return report_drilldown.panel(figure_label, figure, rows, subject=str(label), subject_kind=kind,
                                  unit=report_drilldown.UNIT_COUNT, note=note,
                                  cap=cap or report_drilldown.ROW_CAP)


def by_stage_drilldowns(leads, by_stage, *, stage_name=None, source_name=None,
                        disposition_name=None, cap=None):
    """One panel per funnel row, keyed on `stage_id` the way `pipeline_core.funnel` keys its buckets
    — so the panel holds exactly the leads that row counted, and a lead the funnel dropped appears in
    none of them (which is the defect, visible rather than hidden)."""
    buckets = {}
    for r in (leads or []):
        buckets.setdefault((r or {}).get("stage_id"), []).append(r)
    out = {}
    for row in (by_stage or []):
        name = str(row.get("stage") or "")
        grp = buckets.get(row.get("stage_id")) or []
        out[name] = _group_panel(
            name, "Leads in this stage", row.get("count"), grp, kind="stage",
            stage_name=stage_name, source_name=source_name, disposition_name=disposition_name,
            note=("The value beside this count is the sum of these leads' estimated values, and the "
                  "probability is the stage's own setting, not a measurement of them."), cap=cap)
    return out


def by_source_drilldowns(leads, by_source, *, stage_name=None, source_name=None,
                         disposition_name=None, cap=None):
    """One panel per source row, keyed on the source NAME the way `/crm/summary` keys its buckets
    (an unrecognised source is bucketed as "Unknown" there, so it is here too)."""
    buckets = {}
    for r in (leads or []):
        nm = (source_name((r or {}).get("source_id")) if source_name else None) or "Unknown"
        buckets.setdefault(nm, []).append(r)
    return {str(row.get("source") or ""): _group_panel(
        row.get("source"), "Leads from this source", row.get("leads"),
        buckets.get(str(row.get("source") or "")) or [], kind="source",
        stage_name=stage_name, source_name=source_name, disposition_name=disposition_name,
        note=("\"Won\" and \"won value\" on this line are the won leads among these; the "
              "conversion percentage is one over the other and has no leads of its own."), cap=cap)
        for row in (by_source or [])}


# ── One panel per rep on the activity tab ─────────────────────────────────────────────────────────

def _activity_label(r):
    r = r or {}
    return " · ".join(str(x) for x in (
        r.get("kind") or "note",
        str(r.get("created_at") or "?")[:16].replace("T", " "),
    ))


def _activity_fields(r):
    r = r or {}
    return {"kind": r.get("kind") or "note",
            "rep": r.get("actor_employee_id"),
            "lead": r.get("lead_id"),
            "logged_at": str(r.get("created_at") or "")[:19].replace("T", " ")}


def activity_drilldowns(rows, by_rep, *, cap=None):
    """One panel per rep line, keyed on `actor_employee_id` the way the endpoint keys its buckets
    (an unattributed touch is bucketed as "—" there, so it is here too).

    A touch names BOTH tables: the activity row it is, and the lead it was kept for — the endpoint
    drops a touch whose lead this caller cannot see, so the lead is part of why the figure is what it
    is, not merely context.
    """
    buckets = {}
    for r in (rows or []):
        buckets.setdefault((r or {}).get("actor_employee_id") or "—", []).append(r)
    out = {}
    for line in (by_rep or []):
        key = str(line.get("employee_id") or "")
        grp = buckets.get(key) or []
        drills = [report_drilldown.drill_row(
            _activity_label(r),
            report_drilldown.origins_of([(ACTIVITY[0], {"id": (r or {}).get("id")}),
                                         (LEAD[0], {"id": (r or {}).get("lead_id")})],
                                        schema="public"),
            row_key={"id": (r or {}).get("id")},
            amount=1, in_figure=True, fields=_activity_fields(r)) for r in grp]
        out[key] = report_drilldown.panel(
            "Touches logged by this person", line.get("total"), drills,
            subject=key, subject_kind="employee_id", unit=report_drilldown.UNIT_COUNT,
            note=("The per-kind columns beside this total are these same touches counted by kind, so "
                  "they add back up to it. A touch on a lead you cannot see is not counted here and "
                  "not shown — it is somebody else's pipeline, not a gap in this figure."),
            cap=cap or report_drilldown.ROW_CAP)
    return out
