"""PROOF — the two closing WORK QUEUES name their evidence, including the rows that have none.

DB-free, stdlib only, driving the real `closing/chain_drilldown` and `closing/attempts_drilldown`
through the shared fold (`core/report_drilldown.tiles` / `panel`), over rows built by the REAL
producers (`deposit_accountability.stage_chain`, `submit_refusal.is_real_try`). Batch 9 of the
§CORE-17 audit: /closing/closing-pickup-status and /closing/management.

WHAT THIS BATCH IS ABOUT.

1. THE ROWS THESE REPORTS EXIST FOR WERE READ OUT OF NOTHING. Every earlier batch drilled from a
   figure to the rows behind it. A store-day nobody filed anything for has no closing sheet, no
   turned-away submit, no pickup envelope and no count — it is on the report because the ROSTER
   expected that store to file. So it names no table, which the shared fold reports as a stated,
   COUNTABLE untraceable origin, and it carries `not_filed` plus a field saying in words that the
   roster is the only thing that put it there. Naming a table would be a provenance claim nothing
   checked; leaving the row out would hide the report's own subject. §A5, §B3, §Z2.

2. A STORE-DAY NOBODY FILED IS NOT WORTH $0.00. It contributes 0.00 to declared / collected /
   still-in-store today, so "Cash still in stores $0" reads as "no money is waiting" when the drawer
   was never counted. Those rows are shown in all three cash panels and NOT counted, with the reason
   stated — and the tie-out still holds EXACTLY, because what they contributed was zero. §C.

3. REPORTED, NOT PAPERED OVER: the "No cash to collect" tile counts store-days nobody filed.
   `cash_pickup_state` reaches `nothing_to_collect` when there are no declared envelopes and nothing
   was collected, and an unfiled store-day has no declared envelopes. The figure is UNCHANGED and the
   panel says which of its rows are the unfiled ones. §C5, §C6.

4. A TURNED-AWAY SUBMIT IS NOT A RECOUNT. `submit_refusal.is_real_try` is the one rule for what
   counts as a try: a refused submit stored no closing, so counting it would read as a rep who
   recounted three times when their photo failed three times. Those rows stay in the panel, uncounted,
   with the refusal's own submitter-facing sentence dereferenced from `submit_refusal.message_for` —
   this declaration spells no refusal's meaning of its own. §D, §E, §Z4.

5. ONLY THE ACCEPTED TRY NAMES THE CLOSING ROW THAT STANDS. A try is a record of what somebody
   typed; only the one the gate accepted produced a `daily_closing` row. §E3, §E4, §Z5.

  python3 backend/harness_batch9_drilldown.py
"""
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.closing import attempts_drilldown as atd                       # noqa: E402
from app.modules.closing import chain_drilldown as chd                          # noqa: E402
from app.modules.closing import deposit_accountability as da                    # noqa: E402
from app.modules.closing import submit_refusal as sref                          # noqa: E402
from app.modules.closing import unfinished_day as ufd                           # noqa: E402
from app.modules.core import report_drilldown as rd                             # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_only(path):
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
# THE CHAIN FIXTURE, built by the REAL `stage_chain`. Four store-days on one date:
#   B-1  filed, DM verified, cash declared $500 and collected
#   B-2  filed, cash declared $300 and nobody collected it
#   B-3  NOTHING AT ALL — the spine put it here, and no table answered for it
#   B-4  a submit the gate turned away, so a person came in and no closing exists
DAY = "2026-10-01"
SPINE = [(c, DAY) for c in ("B-1", "B-2", "B-3", "B-4")]
CLOSINGS = [
    {"store_code": "B-1", "close_date": DAY, "employee_name": "Abid", "id": "c1",
     "submitted_at": "2026-10-01T22:00:00+00:00"},
    {"store_code": "B-2", "close_date": DAY, "employee_name": "Bea", "id": "c2",
     "submitted_at": "2026-10-01T21:00:00+00:00"},
]
VERS = [{"store_code": "B-1", "close_date": DAY, "verified": True, "verified_by": "DM",
         "verified_at": "2026-10-02T09:00:00+00:00"}]
COUNTS = [{"store_code": "B-1", "close_date": DAY, "counted_by": "Mgr",
           "counted_at": "2026-10-02T10:00:00+00:00", "status": "match"}]
ATTEMPTS = [{"store_code": "B-4", "close_date": DAY, "employee_name": "Dee", "attempt_no": 1,
             "refused": True, "refusal_code": "envelope_photo_required",
             "created_at": "2026-10-01T20:00:00+00:00"}]
DECLARED = {("B-1", DAY): [{"employee_name": "Abid", "cash": 500.0}],
            ("B-2", DAY): [{"employee_name": "Bea", "cash": 300.0}]}
DAY_ROWS = [{"store_code": "B-1", "day": DAY, "envelopes": [
    {"kind": "cash", "employee_name": "Abid", "state": "undisposed", "amount": 500.0,
     "picked_up_at": "2026-10-02T08:00:00+00:00", "picked_up_by": "DM"}]}]

ROWS, SUMMARY = da.stage_chain(SPINE, CLOSINGS, VERS, DAY_ROWS, COUNTS,
                               attempt_rows=ATTEMPTS, declared_envelopes=DECLARED)
for _r in ROWS:
    _r["store_name"] = _r["store_code"]
FIG = chd.figures(SUMMARY)
CD = chd.drilldowns(ROWS, SUMMARY)


def _row(panel, code):
    for r in panel["rows"]:
        if (r.get("row_key") or {}).get("store_code") == code:
            return r
    return None


print("== A. the report's own figures, and a tile for every one of them ==")
check("A1 the fixture is the real chain's output — four store-days, two filed, one with nothing at "
      "all and one where a submit was turned away",
      (FIG["store_days"], FIG["closing_finished"]) == (4, 2)
      and FIG["closing_%s" % ufd.TURNED_AWAY] == 1, FIG)
check("A2 the figures are the endpoint's OWN summary flattened, not a second fold — every count "
      "here equals the count `chain_summary` reported",
      all(FIG["closing_%s" % s["key"]] == s["store_days"] for s in SUMMARY["closing_states"])
      and all(FIG["pickup_%s" % s["key"]] == s["store_days"] for s in SUMMARY["pickup_states"])
      and FIG["cash_awaiting"] == SUMMARY["cash_awaiting"])
check("A3 and every state in BOTH vocabularies has a tile, dereferenced from its own home, so a "
      "state renamed there moves the tile rather than leaving one counting a dead key",
      all(("closing_%s" % s) in CD for s in ufd.STATES)
      and all(("pickup_%s" % s) in CD for s in da.PICKUP_STATES),
      sorted(CD))
for _k in sorted(CD):
    check("A4(%s) its panel ties to the figure exactly" % _k,
          CD[_k]["tie_out"]["ties"] is True, CD[_k]["tie_out"])
check("A5 THE STORE-DAY WITH NO RECORD AT ALL is counted, and its origin is reported as one that "
      "cannot be traced rather than invented",
      _row(CD["store_days"], "B-3")["in_figure"] is True
      and _row(CD["store_days"], "B-3")["sources"] == []
      and CD["store_days"]["untraceable_origins"] >= 1,
      (_row(CD["store_days"], "B-3"), CD["store_days"]["untraceable_origins"]))
check("A6 and the row says IN WORDS why it is on the report, so an empty evidence cell is never "
      "left for the reader to interpret",
      "roster" in (_row(CD["store_days"], "B-3")["fields"] or {}).get("evidence", ""),
      _row(CD["store_days"], "B-3")["fields"])

print("\n== B. provenance, per row, from zero tables to five ==")
check("B1 the fully-handled store-day names every stage that actually answered for it",
      _row(CD["store_days"], "B-1")["sources"]
      == ["cash_pickup", "daily_closing", "daily_closing_verification", "envelope_count"],
      _row(CD["store_days"], "B-1")["sources"])
check("B2 a filed store-day nobody verified or collected names the closing sheet ALONE — a "
      "provenance claimed where no authority was exercised is a lie that reads like rigour",
      _row(CD["store_days"], "B-2")["sources"] == ["daily_closing"],
      _row(CD["store_days"], "B-2")["sources"])
check("B3 the store-day with nothing names NOTHING",
      _row(CD["store_days"], "B-3")["sources"] == [])
check("B4 a store-day whose only event was a TURNED-AWAY submit names the attempt log and NOT the "
      "closing sheet — somebody came in, and no closing exists",
      _row(CD["store_days"], "B-4")["sources"] == ["closing_attempt"],
      _row(CD["store_days"], "B-4")["sources"])
check("B5 every row's origins address the store-day by its own key, so a reader can find it again",
      all(set((r.get("row_key") or {}).keys()) == set(chd.ROW_KEYS)
          for r in CD["store_days"]["rows"]))
check("B6 every table these panels name has a plain-language label on the client, so no database "
      "name can reach a manager's screen (index §19.38)",
      all(("  %s:" % t) in _read("frontend", "src", "lib", "sourceLabels.ts")
          or ("\n  %s:" % t) in _read("frontend", "src", "lib", "sourceLabels.ts")
          for t in (chd.CLOSING, chd.ATTEMPT, chd.VERIFICATION, chd.PICKUP, chd.COUNT)))

print("\n== C. the three cash figures refuse to count an unfiled store-day at zero ==")
for _k, _amt in (("cash_declared", 800.0), ("cash_picked", 500.0), ("cash_awaiting", 300.0)):
    check("C1(%s) the figure is the chain's own and the panel ties to it" % _k,
          CD[_k]["figure"]["amount"] == _amt and CD[_k]["tie_out"]["ties"] is True,
          (CD[_k]["figure"], CD[_k]["tie_out"]))
    check("C2(%s) the unfiled store-days are SHOWN and NOT counted, each saying the drawer was "
          "never counted rather than contributing $0.00" % _k,
          all(_row(CD[_k], c)["in_figure"] is False and _row(CD[_k], c)["reason"] == "not_filed"
              for c in ("B-3", "B-4")),
          [(c, _row(CD[_k], c)["in_figure"], _row(CD[_k], c)["reason"]) for c in ("B-3", "B-4")])
check("C3 the reason is one the shared vocabulary knows, so the panel prints a sentence and not a "
      "code", "not_filed" in rd.UNMEASURED_REASONS
      and _row(CD["cash_awaiting"], "B-3")["reason_label"])
check("C4 the cash figures are MONEY and the store-day counts are COUNTS — a count of store-days is "
      "never rendered as dollars",
      CD["cash_awaiting"]["figure"]["unit"] == rd.UNIT_MONEY
      and CD["store_days"]["figure"]["unit"] == rd.UNIT_COUNT)
_nothing = CD["pickup_%s" % da.NOTHING_TO_COLLECT]
check("C5 REPORTED, NOT PAPERED OVER: the \"no cash to collect\" tile really does count store-days "
      "nobody filed — reproduced from the real `cash_pickup_state`, and the figure is left alone",
      _nothing["figure"]["amount"] == 2
      and all(_row(_nothing, c)["in_figure"] is True for c in ("B-3", "B-4")),
      _nothing["figure"])
check("C6 and the tile's panel says so in words, so a reader is not left to assume the drawer was "
      "checked and found empty",
      "nobody told us what was in the drawer" in (_nothing["note"] or ""))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE ATTEMPTS FIXTURE. One group, three submits: a recount, the accepted one, and one the gate
# turned away.
GROUPS = [{"close_date": DAY, "store_code": "B-1", "store_address": "1 A St",
           "employee_name": "Abid", "attempts": 2, "row_id": "dc1", "tries": [
               {"attempt_no": 1, "entered_cash": 500, "blocked": True, "accepted": False,
                "refused": False, "created_at": "2026-10-01T21:00:00+00:00"},
               {"attempt_no": 2, "entered_cash": 480, "accepted": True, "refused": False,
                "created_at": "2026-10-01T21:10:00+00:00"},
               {"attempt_no": 3, "refused": True, "refusal_code": "envelope_photo_required",
                "created_at": "2026-10-01T21:20:00+00:00"}]},
          # A group whose closing row could not be matched: it must name no closing row at all.
          {"close_date": DAY, "store_code": "B-9", "store_address": "9 Z St",
           "employee_name": "Zed", "attempts": 1, "row_id": None, "tries": [
               {"attempt_no": 1, "entered_cash": 100, "accepted": True, "refused": False,
                "created_at": "2026-10-01T19:00:00+00:00"}]}]
AD = atd.group_drilldowns(GROUPS)
GK = "%s|B-1|Abid" % DAY


def _try(panel, n):
    for r in panel["rows"]:
        if (r.get("row_key") or {}).get("attempt_no") == n:
            return r
    return None


print("\n== D. one panel per attempt group, keyed as the screen keys its rows ==")
check("D1 the panel keys are the screen's own row keys, field for field",
      sorted(AD) == sorted([GK, "%s|B-9|Zed" % DAY]) and atd.GROUP_KEYS ==
      ("close_date", "store_code", "employee_name"), sorted(AD))
check("D2 the screen builds that same key from the same three fields, so a manager's click and the "
      "server's fold address one group",
      "const key = `${g.close_date}|${g.store_code}|${g.employee_name}`"
      in _read("frontend", "src", "app", "(platform)", "closing", "management", "page.tsx"))
check("D3 the panel's figure is the badge's own attempt count, and it ties to the tries",
      AD[GK]["figure"]["amount"] == 2 and AD[GK]["tie_out"]["ties"] is True, AD[GK]["tie_out"])
check("D4 all three submits are SHOWN — the turned-away one included, because it is the evidence "
      "that a person came in and could not file",
      AD[GK]["rows_total_count"] == 3 and AD[GK]["tie_out"]["shown_not_counted"] == 1,
      (AD[GK]["rows_total_count"], AD[GK]["tie_out"]))

print("\n== E. a turned-away submit is not a recount, and only the accepted try names the closing ==")
check("E1 the refused submit is not counted, and says why",
      _try(AD[GK], 3)["in_figure"] is False and _try(AD[GK], 3)["reason"] == "submit_refused"
      and "submit_refused" in rd.UNMEASURED_REASONS)
check("E2 and the WORDS come from the refusal registry, dereferenced — this declaration spells no "
      "refusal's meaning of its own",
      (_try(AD[GK], 3)["fields"] or {}).get("why")
      == sref.message_for("envelope_photo_required"))
check("E3 only the ACCEPTED try names the closing row that stands",
      _try(AD[GK], 2)["sources"] == ["closing_attempt", "daily_closing"]
      and _try(AD[GK], 1)["sources"] == ["closing_attempt"]
      and _try(AD[GK], 3)["sources"] == ["closing_attempt"],
      [(n, _try(AD[GK], n)["sources"]) for n in (1, 2, 3)])
check("E4 and a group whose closing row could NOT be matched names no closing row at all, rather "
      "than pointing at one nobody found",
      AD["%s|B-9|Zed" % DAY]["rows"][0]["sources"] == ["closing_attempt"],
      AD["%s|B-9|Zed" % DAY]["rows"][0]["sources"])
check("E5 what counts as a try is read through `submit_refusal.is_real_try`, the one home, so the "
      "panel and the badge cannot disagree",
      "is_real_try" in _code_only(os.path.join(ROOT, "backend", "app", "modules", "closing",
                                               "attempts_drilldown.py")))

print("\n== Z. negative controls — the checks above can fail ==")
_z1 = chd.drilldowns(ROWS, dict(SUMMARY, cash_awaiting=999.0))
check("Z1 a cash figure inflated past its store-days is caught",
      _z1["cash_awaiting"]["tie_out"]["ties"] is False)
_z2_rows = [dict(r) for r in ROWS]
for _r in _z2_rows:
    _r["stages"] = [dict(st, rows=1) if st["key"] == "closing" else st for st in _r["stages"]]
_z2 = chd.drilldowns(_z2_rows, SUMMARY)
check("Z2 a declaration that treated every store-day as filed would claim the closing sheet for a "
      "store-day that filed nothing, and report NOTHING untraceable — so §A5 is a real check",
      _row(_z2["store_days"], "B-3")["sources"] == ["daily_closing"]
      and _z2["store_days"]["untraceable_origins"] == 0)
check("Z3 and it would also fold the unfiled store-days into the cash figures at $0.00, which the "
      "figures' own rows then claim as measured",
      _row(_z2["cash_declared"], "B-3")["in_figure"] is True
      and _row(_z2["cash_declared"], "B-3")["reason"] is None)
_z4 = atd.group_drilldowns([dict(GROUPS[0], attempts=3)])
check("Z4 an attempt count that counts the turned-away submit is caught — which is the whole point "
      "of the figure", _z4[GK]["tie_out"]["ties"] is False)
_z5 = atd.group_drilldowns([dict(GROUPS[0], tries=[
    dict(GROUPS[0]["tries"][0], accepted=True)] + GROUPS[0]["tries"][1:], attempts=2)])
check("Z5 a RECOUNT marked accepted would name the closing row too, so §E3 is not a tautology",
      _try(_z5[GK], 1)["sources"] == ["closing_attempt", "daily_closing"])
_z6 = chd.drilldowns([dict(ROWS[0], cash_declared=0, cash_picked=0, cash_awaiting=0)],
                     {"cash_declared": 0})
check("Z6 a FILED store-day that declared nothing is a measured zero and IS counted — filed-ness is "
      "read off the closing stage, never off whether the money is truthy",
      _z6["cash_declared"]["rows"][0]["in_figure"] is True
      and _z6["cash_declared"]["rows"][0]["measured"] is True)

print("\n== G. build locks — the mechanism cannot un-wire ==")
for _mod, _path in (("chain_drilldown", "app/modules/closing/chain_drilldown.py"),
                    ("attempts_drilldown", "app/modules/closing/attempts_drilldown.py")):
    _p = os.path.join(ROOT, "backend", _path)
    _src = open(_p, encoding="utf-8").read()
    _c = _code_only(_p)
    check("G1(%s) the declaration BUILDS no panel of its own — one shape, one renderer" % _mod,
          not any(k in _src for k in ('"tie_out"', '"rows_total_count"', '"untraceable_origins"',
                                      '"rows_shown"', "def tie_out")))
    check("G2(%s) it holds no tolerance, threshold or dollar of its own" % _mod,
          not re.findall(r"\d+\.\d+", _c), re.findall(r"\d+\.\d+", _c))
    check("G3(%s) RULE TWO — no carrier, tenant or product name in it" % _mod,
          not re.search(r"(?i)\b(boost|total\s*wireless|luxelink|cellfonz|vzone|metro|verizon)\b",
                        _src))
    check("G4(%s) and it reads NOTHING from the database" % _mod,
          not any(k in _c for k in ("client", "execute", "schema(", "supabase", "db(")))
check("G5 the chain declaration spells NO state key or state label of its own — both vocabularies "
      "are dereferenced from their own homes",
      not re.search(r'"(not_started|awaiting_correction|turned_away|finished|not_picked_up|"'
                    r'|partly_picked_up|picked_up|nothing_to_collect)"',
                    _code_only(os.path.join(ROOT, "backend", "app", "modules", "closing",
                                            "chain_drilldown.py")))
      and "_ufd.STATES" in _read("backend", "app", "modules", "closing", "chain_drilldown.py")
      and "_da.PICKUP_STATES" in _read("backend", "app", "modules", "closing",
                                       "chain_drilldown.py"))
check("G6 a stage cell is read BY KEY, never by position, so a chain that gains a stage cannot "
      "silently point these figures at another one",
      "if (st or {}).get(\"key\") == key" in _read("backend", "app", "modules", "closing",
                                                   "chain_drilldown.py"))
_rt = _read("backend", "app", "modules", "closing", "router.py")
_chain_ep = _body("def accountability_chain(", _rt)
check("G7 the chain endpoint folds its summary ONCE and serves THAT summary both to the screen and "
      "to the panels, so a tile and its drill-down cannot be two arithmetics",
      _chain_ep.count("summary = _da.chain_summary(rows)") == 1
      and '"summary": summary,' in _chain_ep
      and "_chain_drilldown.drilldowns(rows, summary)" in _chain_ep)
_att_ep = _body("def closing_attempts(", _rt)
check("G8 the attempts endpoint serves the panels over the SAME group list it serves to the screen",
      '"groups": out,' in _att_ep
      and "_attempts_drilldown.group_drilldowns(out)" in _att_ep)

print("\n== H. the screens offer what the server served, and nothing else ==")
_pick_page = _read("frontend", "src", "app", "(platform)", "closing", "_lib",
                   "ClosingPickupStatus.tsx")
_mgmt_page = _read("frontend", "src", "app", "(platform)", "closing", "management", "page.tsx")
check("H1 both screens render the ONE shared panel through the ONE offer rule",
      all("from '@/components/DrillDownPanel'" in p and "useDrillTile(" in p
          for p in (_pick_page, _mgmt_page)))
check("H2 a state tile asks for its panel by the state's OWN key, so a renamed state moves the tile "
      "and its drill together rather than leaving a dead link",
      "dl(`closing_${s.key}`" in _pick_page and "dl(`pickup_${s.key}`" in _pick_page)
check("H3 the cash tile offers all three cash figures — still in store, collected and declared",
      "dl('cash_awaiting', 'still in store', 'cash_picked', 'collected', 'cash_declared', 'declared')"
      in _pick_page)
check("H4 the pickup-status panel shows the EVIDENCE column, which is the one this report needs: a "
      "store-day read out of nothing says so instead of rendering a blank line",
      "header: 'Evidence'" in _pick_page and "fields?.evidence" in _pick_page)
check("H5 neither screen narrows its panels, and neither should: every filter on both is a query "
      "parameter, so the server folded exactly the rows on screen",
      all("drill-narrow" not in p and "narrowPanels" not in p for p in (_pick_page, _mgmt_page)))
check("H6 the attempts panel's Outcome column shows the turned-away submits, and its Why column the "
      "server's own words",
      "'turned away'" in _mgmt_page and "fields?.why" in _mgmt_page)
check("H7 and the drill link on a group card does not trigger the card's own expander",
      "onClick={e => e.stopPropagation()}" in _mgmt_page)

print("\n== I. registration ==")
_IDX = _read("docs", "SYSTEM_DATA_FLOW_INDEX.md")
for _f in ("chain_drilldown.py", "attempts_drilldown.py", "harness_batch9_drilldown.py"):
    check("I1(%s) registered in the index" % _f, _f in _IDX)
check("I2 and the finding this batch records — a report whose most important rows have no evidence "
      "at all — is written in the index", "CORE-17.3i" in _IDX)
_mg = _read("backend", "app", "modules", "core", "module_graph.py")
check("I3 both new declarations are callers of the one home in the module graph",
      "chain_drilldown.py" in _mg and "attempts_drilldown.py" in _mg)
check("I4 the harness is locked into CI",
      "harness_batch9_drilldown.py" in _read(".github", "workflows", "carrier-vocab-guard.yml"))
_ledger = _read("backend", "harness_drilldown_pending.txt")
for _r in ("/closing/closing-pickup-status", "/closing/management"):
    check("I5(%s) reads `shared` in the coverage ledger" % _r,
          re.search(r"^%s\tshared\t\S" % re.escape(_r), _ledger, re.M) is not None)

print("\n%d/%d checks passed" % (len(P), len(P) + len(F)))
if F:
    print("FAILED:")
    for x in F:
        print("  -", x)
    sys.exit(1)
