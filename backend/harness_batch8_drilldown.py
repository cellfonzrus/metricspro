"""PROOF — the CRM reports and the Helpdesk dashboard name their evidence, and a RATE names nothing.

DB-free, stdlib only, driving the real `crm/reports_drilldown` and `helpdesk/dashboard_drilldown`
through the shared fold (`core/report_drilldown.tiles`). Batch 8 of the §CORE-17 audit:
/crm/reports and /helpdesk/dashboard.

WHAT THIS BATCH IS ABOUT.

1. A RATE OR AN AVERAGE GETS NO PANEL OF ITS OWN. `win_rate` is won ÷ closed; `close_rate` is
   closed ÷ total; `avg_resolution_hours` is a mean over the resolved tickets. There is no set of
   records that ADDS UP to a quotient, so a panel behind one could only be a tie-out that cannot
   fail — the worst kind of reassurance. Each of the three offers the COUNTS it divides instead,
   and the declarations serve no panel under those keys at all. §A5, §A6, §E4, §E5, §Z1, §Z6.

   The house settled this shape once already — the dashboard's DM-verified tile serves "verified"
   and "filed" rather than one ratio (§CORE-17.3g, batch 7 §D8). This batch is that ruling applied,
   not re-derived, and §CORE-17.3h writes it down so the next report does not re-derive it either.

2. THE AVERAGE'S DENOMINATOR WAS NEVER ON THE SCREEN. "Avg resolution (hrs)" is computed from the
   tickets that carry BOTH timestamps, so on a period with four tickets of which one is resolved it
   is a measurement of one and reads as a measurement of four. The honest figure behind it is that
   denominator, so `measured` is served, shown on the tile, and its panel holds the unresolved
   tickets uncounted with `not_resolved` as their reason. §E2, §E3, §E5.

3. REPORTED, NOT PAPERED OVER — `pipeline_core.funnel` SILENTLY DROPS a lead whose `stage_id` is not
   in the stage vocabulary (`if b is None: continue`), while `conversion_rates` counts every lead. So
   Σ(by_stage.count) can be LESS than the TOTAL tile on the same screen. §C reproduces it from the
   REAL `funnel` and `conversion_rates`, the panels make it visible (the lead is in the TOTAL panel
   with `stage_unknown`, and in none of the per-stage panels), the screen states the gap in words,
   and the figures are UNCHANGED. A figure is never moved to make a screen read better. §C, §H5.

4. AND `report_activity` RETURNS `{"rows": []}` ON ANY EXCEPTION, so a failed read reads as "nobody
   logged anything". That is the endpoint's own shape, not this declaration's to change; it is
   recorded in the declaration and in the index so nobody has to rediscover it. §G6.

5. THE VOCABULARY IS PROVENANCE, NOT DECORATION. A lead's stage count depends on `crm_stage` and a
   ticket's stage on `ticket_statuses` — an entry missing from either CHANGES the figure (the lead is
   dropped; the ticket falls back to "open"). So those tables are named on the rows they actually
   answered for and left off the rows they did not, rather than claimed on every row. §B, §F.

  python3 backend/harness_batch8_drilldown.py
"""
import os
import re
import sys
import tokenize
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.crm import pipeline_core as pc                                 # noqa: E402
from app.modules.crm import reports_drilldown as crd                            # noqa: E402
from app.modules.helpdesk import dashboard_drilldown as hdd                      # noqa: E402
from app.modules.core import report_drilldown as rd                             # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_only(path):
    """A file's CODE, with strings and comments stripped — a section number in a docstring is prose,
    a float in an expression is a threshold, and only the second is what §G forbids."""
    out = []
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                out.append(tok.string)
    return " ".join(out)


def _body(fn_name, src):
    i = src.index(fn_name)
    j = src.find("\ndef ", i + len(fn_name))
    k = src.find("\n@router.", i + len(fn_name))
    ends = [x for x in (j, k) if x != -1]
    return src[i:(min(ends) if ends else len(src))]


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE CRM FIXTURE. Four leads, and the fourth is the whole point: its stage is not in the stage
# vocabulary, so `funnel` drops it while `conversion_rates` counts it.
STAGES = [{"id": "s1", "name": "New", "sort_order": 1, "probability": 10},
          {"id": "s2", "name": "Won", "sort_order": 2, "probability": 100, "is_won": True}]
SOURCES = [{"id": "src1", "name": "Walk-in"}]
DISPOSITIONS = [{"id": "d1", "name": "Went elsewhere"}]
LEADS = [
    {"id": "l1", "customer_name": "Ann", "created_at": "2026-10-01T10:00:00+00:00",
     "status": "open", "stage_id": "s1", "source_id": "src1", "value_estimate": 100.0,
     "owner_employee_id": "e1", "store_code": "B-1"},
    {"id": "l2", "customer_name": "Bob", "created_at": "2026-10-02T10:00:00+00:00",
     "status": "open", "stage_id": "s1", "source_id": None, "value_estimate": 200.0,
     "owner_employee_id": "e1", "store_code": "B-1"},
    {"id": "l3", "customer_name": "Cy", "created_at": "2026-10-03T10:00:00+00:00",
     "status": "won", "stage_id": "s2", "source_id": "src1", "value_estimate": 300.0,
     "owner_employee_id": "e2", "store_code": "B-2"},
    # LOST, with a reason recorded — and in a stage that is NOT in the vocabulary.
    {"id": "l4", "customer_name": "Dee", "created_at": "2026-10-04T10:00:00+00:00",
     "status": "lost", "stage_id": "s-gone", "source_id": None, "disposition_id": "d1",
     "value_estimate": 400.0, "owner_employee_id": "e2", "store_code": "B-2"},
]
_ST = {s["id"]: s for s in STAGES}
_SRC = {s["id"]: s for s in SOURCES}
_DISP = {d["id"]: d for d in DISPOSITIONS}
READERS = {"stage_name": (lambda i: (_ST.get(i) or {}).get("name")),
           "source_name": (lambda i: (_SRC.get(i) or {}).get("name")),
           "disposition_name": (lambda i: (_DISP.get(i) or {}).get("name"))}

TOTALS = pc.conversion_rates(LEADS)
BY_STAGE = pc.funnel(LEADS, STAGES)
LEAD_DRILL = crd.lead_drilldowns(LEADS, TOTALS, **READERS)


def _row(panel, lead_id):
    for r in panel["rows"]:
        if (r.get("row_key") or {}).get("id") == lead_id:
            return r
    return None


print("== A. the CRM's five conversion figures ==")
check("A1 the report's own fold is what the panels are tied against — `conversion_rates`, driven "
      "here, not a restatement of it",
      (TOTALS["total"], TOTALS["won"], TOTALS["lost"], TOTALS["open"]) == (4, 1, 1, 2), TOTALS)
for _k, _n in (("total", 4), ("open", 2), ("won", 1), ("lost", 1)):
    check("A2(%s) its panel ties to the figure exactly" % _k,
          LEAD_DRILL[_k]["tie_out"]["ties"] is True
          and LEAD_DRILL[_k]["figure"]["amount"] == _n,
          (LEAD_DRILL[_k]["tie_out"], LEAD_DRILL[_k]["figure"]))
check("A3 and every one of them is a COUNT of leads, not money — a lead's estimated value is a "
      "column on the row, never the figure",
      all(LEAD_DRILL[k]["figure"]["unit"] == rd.UNIT_COUNT
          for k in ("total", "open", "won", "lost")))
check("A4 every panel's rows carry the lead's natural key, so a screen could narrow them",
      all(set((r.get("row_key") or {}).keys()) == set(crd.LEAD_KEYS)
          for k in ("total", "open", "won", "lost") for r in LEAD_DRILL[k]["rows"]))
check("A5 WIN RATE AND CLOSE RATE GET NO PANEL — a percentage has no leads that add up to it, so a "
      "tie-out against one would be a check that cannot fail",
      "win_rate" not in LEAD_DRILL and "close_rate" not in LEAD_DRILL,
      sorted(LEAD_DRILL))
check("A6 and they are absent from the DECLARATION, not merely dropped by the fold — the catalogue "
      "never offers a quotient a key",
      not any(t["key"] in ("win_rate", "close_rate") for t in crd.lead_catalogue(**READERS)))
check("A7 the counts the rates divide say so on their own panels, so a reader arriving from the "
      "rate knows which half of it they are looking at",
      all("percentage has no leads" in (LEAD_DRILL[k]["note"] or "")
          for k in ("total", "won", "lost")))

print("\n== B. provenance, per row, never a constant ==")
check("B1 a lead whose stage IS in the vocabulary names the stage list, because that list is what "
      "lets it reach the funnel at all",
      _row(LEAD_DRILL["total"], "l1")["sources"] == ["crm_lead", "crm_stage"],
      _row(LEAD_DRILL["total"], "l1")["sources"])
check("B2 a lead whose stage is NOT in it names only the lead table — a provenance claimed where no "
      "vocabulary answered is a lie that reads like rigour",
      _row(LEAD_DRILL["total"], "l4")["sources"] == ["crm_lead"],
      _row(LEAD_DRILL["total"], "l4")["sources"])
check("B3 a LOST lead with a reason recorded names the disposition list, which is what the \"why we "
      "lose\" table counts",
      _row(LEAD_DRILL["lost"], "l4")["sources"] == ["crm_disposition", "crm_lead"],
      _row(LEAD_DRILL["lost"], "l4")["sources"])
check("B4 and that is read PER ROW: a lost panel built over a lead with no reason recorded names no "
      "disposition list",
      crd.lead_drilldowns([dict(LEADS[3], disposition_id=None)], {"lost": 1},
                          **READERS)["lost"]["rows"][0]["sources"] == ["crm_lead"])
check("B5 every row's origins address the lead by its key, so a reader can go and look at it",
      all(o.get("keys", {}).get("id") for r in LEAD_DRILL["total"]["rows"] for o in r["origins"]))

print("\n== C. the funnel defect, reproduced and made visible ==")
_funnel_sum = sum(r["count"] for r in BY_STAGE)
check("C1 Σ(by_stage) is LESS than the TOTAL tile on the same screen — `funnel` drops a lead whose "
      "stage is not in the vocabulary while `conversion_rates` counts it",
      _funnel_sum == 3 and TOTALS["total"] == 4,
      (_funnel_sum, TOTALS["total"]))
check("C2 the dropped lead IS counted in the total panel, and carries the reason why the funnel "
      "below will not show it",
      _row(LEAD_DRILL["total"], "l4")["in_figure"] is True
      and _row(LEAD_DRILL["total"], "l4")["reason"] == "stage_unknown")
check("C3 the reason is one the shared vocabulary of reasons knows, so the panel renders words "
      "rather than a code",
      "stage_unknown" in rd.UNMEASURED_REASONS
      and _row(LEAD_DRILL["total"], "l4")["reason_label"])
_stage_panels = crd.by_stage_drilldowns(LEADS, BY_STAGE, **READERS)
check("C4 one panel per funnel row, keyed on the stage NAME the row carries",
      sorted(_stage_panels) == ["New", "Won"], sorted(_stage_panels))
for _nm, _n in (("New", 2), ("Won", 1)):
    check("C5(%s) and it holds exactly the leads that row counted" % _nm,
          _stage_panels[_nm]["figure"]["amount"] == _n
          and _stage_panels[_nm]["tie_out"]["ties"] is True,
          _stage_panels[_nm]["tie_out"])
check("C6 the dropped lead appears in NONE of them — the panels are keyed on `stage_id` exactly as "
      "`funnel` keys its buckets, so they cannot disagree with it about which leads it counted",
      not any((r.get("row_key") or {}).get("id") == "l4"
              for p in _stage_panels.values() for r in p["rows"]))

print("\n== D. the other two groupings are keyed as their own endpoints key them ==")
_BY_SOURCE = [{"source": "Walk-in", "leads": 2, "won": 1}, {"source": "Unknown", "leads": 2, "won": 0}]
_src_panels = crd.by_source_drilldowns(LEADS, _BY_SOURCE, **READERS)
check("D1 one panel per source line, including \"Unknown\" — which is where an unrecognised source "
      "lands in `/crm/summary`, so it lands there here too rather than being dropped",
      sorted(_src_panels) == ["Unknown", "Walk-in"], sorted(_src_panels))
check("D2 and each holds that line's own leads",
      all(_src_panels[k]["tie_out"]["ties"] is True for k in _src_panels),
      {k: _src_panels[k]["tie_out"] for k in _src_panels})
ACTS = [
    {"id": "a1", "kind": "call", "actor_employee_id": "e1", "lead_id": "l1",
     "created_at": "2026-10-01T11:00:00+00:00"},
    {"id": "a2", "kind": "sms", "actor_employee_id": "e1", "lead_id": "l2",
     "created_at": "2026-10-02T11:00:00+00:00"},
    # nobody is recorded against this one
    {"id": "a3", "kind": "note", "actor_employee_id": None, "lead_id": "l3",
     "created_at": "2026-10-03T11:00:00+00:00"},
]
_BY_REP = [{"employee_id": "e1", "total": 2, "call": 1, "sms": 1},
           {"employee_id": "—", "total": 1, "note": 1}]
_rep_panels = crd.activity_drilldowns(ACTS, _BY_REP)
check("D3 one panel per rep line, keyed on `actor_employee_id` — including the \"—\" bucket the "
      "endpoint puts an unattributed touch in",
      sorted(_rep_panels) == sorted(["e1", "—"]), sorted(_rep_panels))
check("D4 each ties to that rep's own total",
      all(_rep_panels[k]["tie_out"]["ties"] is True for k in _rep_panels),
      {k: _rep_panels[k]["tie_out"] for k in _rep_panels})
check("D5 a touch names BOTH the activity row it is and the lead it was kept for — the endpoint "
      "drops a touch whose lead this caller cannot see, so the lead is part of why the figure is "
      "what it is, not context",
      all(r["sources"] == ["crm_activity", "crm_lead"]
          for p in _rep_panels.values() for r in p["rows"]),
      [r["sources"] for p in _rep_panels.values() for r in p["rows"]])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE HELPDESK FIXTURE. Four tickets: one resolved, one open, one pending, and one whose status is
# not in the status list at all.
ST = {"st1": {"stage": "open", "label": "New"},
      "st2": {"stage": "pending", "label": "Waiting on customer"},
      "st3": {"stage": "done", "label": "Closed"}}
TICKETS = [
    {"id": "t1", "subject": "Printer", "status_id": "st1",
     "created_at": "2026-10-01T09:00:00+00:00", "resolved_at": None},
    {"id": "t2", "subject": "Login", "status_id": "st2",
     "created_at": "2026-10-02T09:00:00+00:00", "resolved_at": None},
    {"id": "t3", "subject": "Tablet", "status_id": "st3",
     "created_at": "2026-10-03T09:00:00+00:00", "resolved_at": "2026-10-03T13:00:00+00:00"},
    # UNMAPPED STATUS — falls back to "open" on the screen, and must say so.
    {"id": "t4", "subject": "Mystery", "status_id": "st-gone",
     "created_at": "2026-10-04T09:00:00+00:00", "resolved_at": None},
]


def _stage_of(t):
    return (ST.get((t or {}).get("status_id")) or {}).get("stage") or "open"


def _status_label(sid):
    return (ST.get(sid) or {}).get("label")


FIG = hdd.totals(TICKETS, stage_of=_stage_of)
TD = hdd.drilldowns(TICKETS, FIG, stage_of=_stage_of, status_label=_status_label)


def _replicate_dashboard(tickets):
    """The endpoint's OWN loop, copied here and driven over the same tickets, so §E1 compares two
    folds rather than asserting one against itself. If the endpoint's definition of a stage, an open
    ticket or a measurable duration ever moves, this and `hdd.totals` part company and §E1 fails."""
    by_stage = {"open": 0, "pending": 0, "done": 0}
    open_count, res = 0, []
    for t in tickets:
        stage = (ST.get(t.get("status_id")) or {}).get("stage") or "open"
        by_stage[stage] = by_stage.get(stage, 0) + 1
        if t.get("resolved_at") and t.get("created_at"):
            d = (datetime.fromisoformat(t["resolved_at"].replace("Z", "+00:00"))
                 - datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")))
            res.append(d.total_seconds() / 3600)
        if stage in ("open", "pending"):
            open_count += 1
    return by_stage, open_count, res


print("\n== E. the helpdesk dashboard, where the headline figure is a mean ==")
_by_stage, _open, _res = _replicate_dashboard(TICKETS)
check("E1 the declaration's fold and the endpoint's own loop agree on every figure — two folds that "
      "must agree, checked, beat one fold nobody can check",
      (FIG["total"], FIG["open"]) == (len(TICKETS), _open)
      and all(FIG["stage_%s" % s] == _by_stage[s] for s in hdd.STAGES),
      (FIG, _by_stage, _open))
check("E2 `measured` is the AVERAGE'S DENOMINATOR, and it is 1 of 4 here — the figure the tile has "
      "never shown and the reason it could read as a measurement of all of them",
      (FIG["measured"], FIG["total"]) == (1, 4) and len(_res) == FIG["measured"],
      (FIG["measured"], len(_res)))
for _k in ("total", "open", "measured", "stage_open", "stage_pending", "stage_done"):
    check("E3(%s) its panel ties to the figure exactly" % _k,
          TD[_k]["tie_out"]["ties"] is True, TD[_k]["tie_out"])
check("E4 AN AVERAGE GETS NO PANEL — `avg_resolution_hours` has no key in the fold and none in the "
      "catalogue, for the same reason a percentage has none",
      "avg_resolution_hours" not in TD
      and not any(t["key"] == "avg_resolution_hours"
                  for t in hdd.catalogue(stage_of=_stage_of, status_label=_status_label)),
      sorted(TD))
_unres = [r for r in TD["measured"]["rows"] if (r.get("row_key") or {}).get("id") in ("t1", "t2", "t4")]
check("E5 the unresolved tickets are SHOWN in the average's panel and NOT counted, each saying it "
      "contributes no duration — not folded in at zero hours, which would drag the mean toward a "
      "number nobody measured",
      len(_unres) == 3 and all(r["in_figure"] is False for r in _unres)
      and all(r["reason"] == "not_resolved" for r in _unres),
      [(r["label"], r["in_figure"], r["reason"]) for r in _unres])
check("E6 and the panel says in words that the figure above it is a mean over these rows only",
      "no tickets that add up to it" in (TD["measured"]["note"] or ""))

print("\n== F. the status vocabulary decides the answer, so it is named where it answered ==")
_t3 = [r for r in TD["total"]["rows"] if (r.get("row_key") or {}).get("id") == "t3"][0]
check("F1 a ticket whose status IS in the list names it",
      _t3["sources"] == ["ticket_statuses", "tickets"], _t3["sources"])
_t4 = [r for r in TD["total"]["rows"] if (r.get("row_key") or {}).get("id") == "t4"][0]
check("F2 the UNMAPPED one names only the ticket table, and carries a reason — rather than the "
      "screen silently calling it open",
      _t4["sources"] == ["tickets"] and _t4["reason"] == "not_recorded",
      (_t4["sources"], _t4["reason"]))
check("F3 and it IS counted as open, because that is what the endpoint does — the panel reports the "
      "fallback, it does not change the figure",
      _t4["in_figure"] is True
      and any((r.get("row_key") or {}).get("id") == "t4" and r["in_figure"] is True
              for r in TD["open"]["rows"])
      and not any((r.get("row_key") or {}).get("id") == "t4" and r["in_figure"] is True
                  for r in TD["stage_done"]["rows"]),
      [(r.get("row_key"), r["in_figure"]) for r in TD["open"]["rows"]])
check("F4 its row says the status is unmapped as a FIELD too, so the screen's Status column can "
      "print that instead of an empty cell",
      _t4["fields"]["status_mapped"] is False and _t4["fields"]["stage"] == "open")
check("F5 the Open tile is the endpoint's own definition of open — the open and pending stages, "
      "read off one tuple so a stage added there cannot leave this counting the wrong tickets",
      hdd.OPEN_STAGES == ("open", "pending")
      and FIG["open"] == FIG["stage_open"] + FIG["stage_pending"])

print("\n== Z. negative controls — the checks above can fail ==")
_z1 = crd.lead_drilldowns(LEADS, dict(TOTALS, won=2), **READERS)
check("Z1 a WON count inflated by one is caught by its panel",
      _z1["won"]["tie_out"]["ties"] is False)
_z2 = crd.lead_drilldowns(LEADS, TOTALS, stage_name=(lambda _i: "Anything"),
                          source_name=READERS["source_name"],
                          disposition_name=READERS["disposition_name"])
check("Z2 a stage reader that answers for EVERY id — the shape a hard-coded vocabulary would have — "
      "makes the dropped lead's provenance claim a stage list that never answered",
      _row(_z2["total"], "l4")["sources"] == ["crm_lead", "crm_stage"]
      and _row(_z2["total"], "l4")["reason"] is None)
_z3 = crd.by_stage_drilldowns(LEADS, [dict(BY_STAGE[0], count=3)], **READERS)
check("Z3 a funnel row claiming three leads where two were bucketed is caught",
      _z3["New"]["tie_out"]["ties"] is False)
_z4 = crd.activity_drilldowns(ACTS, [{"employee_id": "e1", "total": 3}])
check("Z4 a rep line claiming a touch that is not in their bucket is caught",
      _z4["e1"]["tie_out"]["ties"] is False)
_z5 = hdd.drilldowns(TICKETS, dict(FIG, measured=4), stage_of=_stage_of, status_label=_status_label)
check("Z5 and claiming the average measures all four tickets is caught — which is the whole point "
      "of serving the denominator",
      _z5["measured"]["tie_out"]["ties"] is False)
_z6 = hdd.drilldowns([dict(TICKETS[0], resolved_at="2026-10-01T10:00:00+00:00")], {"measured": 1},
                     stage_of=_stage_of, status_label=_status_label)
check("Z6 a ticket resolved in ZERO measurable hours is still a MEASURED one and is counted — "
      "measurability is read off the two timestamps, never off whether the duration is truthy",
      _z6["measured"]["rows"][0]["in_figure"] is True
      and _z6["measured"]["rows"][0]["measured"] is True)
_z7 = hdd.drilldowns(TICKETS, FIG, stage_of=_stage_of, status_label=(lambda _s: "Something"))
check("Z7 a status reader that answers for every id makes the unmapped ticket claim a status list "
      "that never answered — so §F2 is a real check, not a tautology",
      [r for r in _z7["total"]["rows"] if (r.get("row_key") or {}).get("id") == "t4"][0]["sources"]
      == ["ticket_statuses", "tickets"])

print("\n== G. build locks — the mechanism cannot un-wire ==")
for _mod, _path in (("reports_drilldown", "app/modules/crm/reports_drilldown.py"),
                    ("dashboard_drilldown", "app/modules/helpdesk/dashboard_drilldown.py")):
    _p = os.path.join(ROOT, "backend", _path)
    _src = open(_p, encoding="utf-8").read()
    _c = _code_only(_p)
    check("G1(%s) the declaration BUILDS no panel of its own — one shape, one renderer" % _mod,
          not any(k in _src for k in ('"tie_out"', '"rows_total_count"', '"untraceable_origins"',
                                      '"rows_shown"', "def panel", "def tie_out")))
    check("G2(%s) it holds no rate, threshold or dollar of its own — the quotients live in "
          "`pipeline_core.conversion_rates` and in the endpoint, and a copy here could drift" % _mod,
          not re.findall(r"\d+\.\d+", _c), re.findall(r"\d+\.\d+", _c))
    check("G3(%s) RULE TWO — no carrier, tenant or product name in it" % _mod,
          not re.search(r"(?i)\b(boost|total\s*wireless|luxelink|cellfonz|vzone|metro|verizon)\b",
                        _src))
    check("G4(%s) and it reads NOTHING from the database — every vocabulary is passed IN, so the "
          "names on a drill row and the names in the report come from one lookup" % _mod,
          not any(k in _c for k in ("client", "execute", "schema(", "supabase", "db(")))
_crm_rt = _read("backend", "app", "modules", "crm", "router.py")
_conv = _body("def report_conversion(", _crm_rt)
check("G5 the conversion endpoint folds its totals ONCE and serves THAT dict both to the screen and "
      "to the panels, so a figure and its drill-down cannot disagree",
      _conv.count("_totals = core.conversion_rates(") == 1
      and '"totals": _totals,' in _conv
      and "lead_drilldowns(leads, _totals," in _conv)
check("G6 and the per-stage panels are built from the SAME `by_stage` the screen shows, not a "
      "second funnel call",
      _conv.count("core.funnel(") == 1 and "by_stage_drilldowns(\n                leads, by_stage," in _conv)
check("G7 the known `report_activity` shape — any exception becomes an empty report — is written "
      "down in the declaration, so a reader of an empty rep-activity table knows the other reading",
      'return {"rows": []}' in _body("def report_activity(", _crm_rt)
      and "ANY exception" in open(os.path.join(ROOT, "backend", "app", "modules", "crm",
                                               "reports_drilldown.py"), encoding="utf-8").read())
_hd_rt = _read("backend", "app", "modules", "helpdesk", "router.py")
_dash_ep = _body("def dashboard(", _hd_rt)
check("G8 the helpdesk endpoint hands the declaration the status map it ALREADY read (`st`), not a "
      "second read of the vocabulary",
      _dash_ep.count("_maps(org_id)") == 1 and "st.get(" in _dash_ep
      and "_dashboard_drilldown.totals(tickets, stage_of=_stage_of)" in _dash_ep)
check("G9 and it serves `measured` to the screen, because the average's denominator belongs on the "
      "tile and not only in the panel",
      '"measured": _figures["measured"],' in _dash_ep)
check("G10 the three reasons these panels use are in the shared vocabulary of reasons, so no screen "
      "renders a raw code",
      all(k in rd.UNMEASURED_REASONS for k in ("stage_unknown", "not_resolved", "not_recorded")))

print("\n== H. the screens offer what the server served, and nothing else ==")
_crm_page = _read("frontend", "src", "app", "(platform)", "crm", "reports", "page.tsx")
_hd_page = _read("frontend", "src", "app", "(platform)", "helpdesk", "dashboard", "page.tsx")
check("H1 both screens render the ONE shared panel and use the ONE offer rule",
      all("from '@/components/DrillDownPanel'" in p and "useDrillTile(" in p
          for p in (_crm_page, _hd_page)))
check("H2 the CRM's WIN RATE and CLOSE RATE tiles offer the COUNTS they divide and never themselves",
      "dl('won', 'won', 'lost', 'lost')" in _crm_page
      and "dl('won', 'won', 'lost', 'lost', 'total', 'all leads')" in _crm_page
      and "dl('win_rate" not in _crm_page and "dl('close_rate" not in _crm_page)
check("H3 and the helpdesk's AVG tile offers the tickets it MEASURES, with the count on the tile "
      "itself so the mean is not read as a measurement of all of them",
      "dl('measured'," in _hd_page and "dl('avg_resolution" not in _hd_page
      and "over ${d.measured} of ${d.total} resolved" in _hd_page)
check("H4 neither screen narrows its panels, and neither should: every filter on both is a query "
      "parameter, so the server folded exactly the records on screen",
      all("drill-narrow" not in p and "narrowPanels" not in p for p in (_crm_page, _hd_page)))
check("H5 the CRM screen STATES the funnel shortfall in words rather than leaving a reader to "
      "notice that the stages add up to less than the total",
      "const funnelShort" in _crm_page and "not in your stage list" in _crm_page)
check("H6 each table row's link comes from the grouping that tab's own endpoint keyed, picked by "
      "tab rather than one map used for all three",
      all(k in _crm_page for k in ("drilldown_by_stage", "drilldown_by_source", "drilldown_by_rep"))
      and "rowKeyOfLine" in _crm_page)
check("H7 and the aging card says it is keyed on NOW, not on the period",
      "age as of now, not the period" in _hd_page)

print("\n== I. registration ==")
_IDX = _read("docs", "SYSTEM_DATA_FLOW_INDEX.md")
for _f in ("reports_drilldown.py", "dashboard_drilldown.py", "harness_batch8_drilldown.py"):
    check("I1(%s) registered in the index" % _f, _f in _IDX)
check("I2 and the rule this batch settled — a rate or an average gets no panel — is written in the "
      "index, so the next report does not re-derive it",
      "CORE-17.3h" in _IDX)
_mg = _read("backend", "app", "modules", "core", "module_graph.py")
check("I3 both new declarations are callers of the one home in the module graph",
      "crm/reports_drilldown.py" in _mg or "reports_drilldown.py" in _mg)
check("I4 the harness is locked into CI",
      "harness_batch8_drilldown.py" in _read(".github", "workflows", "carrier-vocab-guard.yml"))
_ledger = _read("backend", "harness_drilldown_pending.txt")
for _r in ("/crm/reports", "/helpdesk/dashboard"):
    check("I5(%s) reads `shared` in the coverage ledger" % _r,
          re.search(r"^%s\tshared\t\S" % re.escape(_r), _ledger, re.M) is not None)

print("\n%d/%d checks passed" % (len(P), len(P) + len(F)))
if F:
    print("FAILED:")
    for x in F:
        print("  -", x)
    sys.exit(1)
