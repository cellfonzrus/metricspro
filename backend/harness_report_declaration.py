"""PROOF + LOCK: a tenant's declared reports, as one editable thing (index §COMMCALC-48).

WHAT THIS PROVES, DB-free and browser-free. The mapping between the two stored columns the pull reads
and the rows an operator edits: both declared shapes read, the round trip, the derived per-day flag,
the advice, the filter vocabulary, and a capture folded in without overwriting a human's correction.

WHAT THIS LOCKS, and why each lock is the point.

  • THE ROUND TRIP IS THE IDENTITY. `columns(rows(src))` must reproduce what the pull reads, so a
    tenant who opens the editor and saves without touching anything has changed nothing. An editor
    that quietly rewrites a working declaration into its own dialect is worse than no editor, and this
    is the check that would catch it.
  • THE EDITOR'S SHAPE IS NEVER STORED. It is derived in both directions. A third stored spelling of
    one fact is how a declaration and its own filter came to be derived two different ways
    (§COMMCALC-39), and that defect made fourteen declared reports resolve to none.
  • ONE RULE FOR "WHICH REPORT IS THIS". `pos_report_sweep.spec_key`, dereferenced. A second rule here
    is that same defect in the one place a human types the key.
  • THE FILTER VOCABULARY IS THE PULL'S. `portal_report_pull.PARAM_KINDS` / `DATE_TOKENS`,
    dereferenced — an editor offering a kind the pull cannot apply offers a filter that is ignored.
  • A SAVE NEVER DROPS WHAT IT DOES NOT UNDERSTAND. Other keys of a calibration entry, and entries for
    keys the editor never showed, are folded through untouched.
  • A CAPTURE SUGGESTS, IT DOES NOT DECIDE. A selector a human edited survives a capture.

Run:  cd backend && python3 harness_report_declaration.py        Exit 0 = all green.
"""
import io
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import report_declaration as rd      # noqa: E402
from app.modules.commcalc import pos_report_sweep as sweep      # noqa: E402
from app.modules.commcalc import portal_report_pull as prp      # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s" % msg)


def eq(got, want, msg):
    ok(got == want, msg if got == want else "%s (got %r, want %r)" % (msg, got, want))


def read(rel):
    with open(os.path.join(_ROOT, rel)) as fh:
        return fh.read()


def strip_text(src):
    out, prev = [], None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            t, s = tok.type, tok.string
            if t == tokenize.COMMENT:
                continue
            if t == tokenize.STRING and prev in (None, tokenize.INDENT, tokenize.DEDENT,
                                                 tokenize.NEWLINE, tokenize.NL):
                prev = t
                continue
            out.append(s)
            prev = t
    except tokenize.TokenError:
        return src
    return " ".join(out)


# A source declared the way a tenant's really is: one report declared in full, one as a bare key, and
# a calibration entry carrying something this editor does not edit.
SRC = {
    "portal_reports": [
        {"key": "Item Sales", "label": "Item Sales", "nav": "Reporting > POS > Item Sales",
         "export": "csv"},
        "tender_cash",
    ],
    "portal_calibration": {
        "item_sales": {
            "params": [{"selector": "#from", "kind": "fill", "value": "{date}", "label": "From"},
                       {"selector": "#to", "kind": "fill", "value": "{date}", "label": "To"},
                       {"selector": "#detail", "kind": "check", "value": None, "label": "Itemized"}],
            "notes": "captured 2026-10-29",
        },
        "login": {"fields": {"username": "#email"}, "press": "#continue"},
    },
}


print("\n== §A  BOTH DECLARED SHAPES READ ==")
rows = rd.rows(SRC)
eq(len(rows), 2, "both declared reports become rows")
eq(rows[0]["key"], "item_sales", "the object shape's key is canonical (spec_key, not a second rule)")
eq(rows[0]["label"], "Item Sales", "its label is kept as the tenant wrote it")
eq(rows[0]["nav"], ["Reporting", "POS", "Item Sales"],
   "a menu path typed with arrows becomes the hint words")
eq(rows[1]["key"], "tender_cash", "the bare-string shape reads too")
eq(rows[1]["nav"], ["tender", "cash"],
   "…and shows the hint its declaration actually gives the navigation, rather than hiding it")
eq([r["key"] for r in rows], [sweep.spec_key(i) for i in SRC["portal_reports"]],
   "every key is the one pos_report_sweep will key its spec by")
eq(len(rd.rows({"portal_reports": {"key": "one"}})), 1, "a single object declaration is accepted")
eq(rd.rows({}), [], "a source with nothing declared has no rows (not an error)")
eq(rd.rows(None), [], "…nor does no source at all")


print("\n== §B  THE ROUND TRIP IS THE IDENTITY ==")
reports, cal = rd.columns(rows, SRC["portal_calibration"])
again = rd.rows({"portal_reports": reports, "portal_calibration": cal})
eq(again, rows, "columns(rows(src)) reproduces the same rows — opening and saving changes nothing")
eq([r["key"] for r in reports], ["item_sales", "tender_cash"], "declared order is kept")
eq(sweep.report_specs(None, reports, src_row={"portal_reports": reports})[0]["key"], "item_sales",
   "what is saved is what the PULL resolves — the same resolver, not a parallel reading")
eq(len(sweep.report_specs(None, reports, src_row={"portal_reports": reports})), 2,
   "…and it resolves both of them (the §COMMCALC-39 defect would resolve none)")
eq(prp.report_params({"portal_calibration": cal}, "item_sales"),
   tuple({"selector": p["selector"], "kind": p["kind"], "value": p["value"], "label": p["label"]}
         for p in rows[0]["params"]),
   "the saved filters are exactly what portal_report_pull reads back")
eq(prp.is_per_day({"portal_calibration": cal}, "item_sales"), True,
   "…and the pull still derives per-day from them")


print("\n== §C  A SAVE NEVER DROPS WHAT IT DOES NOT UNDERSTAND ==")
eq(cal["item_sales"].get("notes"), "captured 2026-10-29",
   "another key of a calibration entry survives the save")
eq(cal.get("login"), SRC["portal_calibration"]["login"],
   "a calibration entry for a key the editor never showed survives it (the §COMMCALC-46 login "
   "declaration lives there)")
_, cal2 = rd.columns([r for r in rows if r["key"] != "tender_cash"], SRC["portal_calibration"])
eq("tender_cash" in cal2, False, "removing a report does remove it from the declaration")
eq(cal2.get("login"), SRC["portal_calibration"]["login"], "…without touching the rest")


print("\n== §D  PER-DAY IS DERIVED, NEVER A SECOND SWITCH ==")
eq(rows[0]["per_day"], True, "a report whose filters mention the day IS a per-day report")
eq(rows[1]["per_day"], False, "…and one with no filters is not")
lying = dict(rows[0]); lying["per_day"] = False
_, cal3 = rd.columns([lying], None)
eq(rd.rows({"portal_reports": [{"key": "item_sales"}], "portal_calibration": cal3})[0]["per_day"],
   True, "a row claiming otherwise cannot save that claim — the filters decide")
eq("per_day" in rd.columns([rows[0]], None)[0][0], False, "per_day is never stored")
eq("problems" in rd.columns([rows[0]], None)[0][0], False, "nor is the advice")


print("\n== §E  THE FILTER VOCABULARY IS THE PULL'S ==")
eq(tuple(rd.PARAM_KINDS), tuple(prp.PARAM_KINDS), "the kinds are portal_report_pull's, dereferenced")
eq(tuple(rd.DATE_TOKENS), tuple(prp.DATE_TOKENS), "so are the date tokens")
eq(rd.clean_params([{"selector": "#x", "kind": "fill", "value": "1"},
                    {"selector": "#y", "kind": "teleport", "value": "2"},
                    {"selector": "", "kind": "fill", "value": "3"},
                    "not a dict"]),
   [{"selector": "#x", "kind": "fill", "value": "1", "label": "#x"}],
   "a kind the pull cannot apply, a step naming no control and a non-dict are DROPPED, not saved as "
   "filters that would be silently ignored")
eq(rd.clean_params([{"selector": "#d", "kind": "fill", "value": "{date}"}])[0]["value"], "{date}",
   "a date token is kept verbatim — substituting one here would freeze the report on today")
eq(len(rd.clean_params([{"selector": "#%d" % i, "kind": "fill", "value": "v"}
                        for i in range(rd.MAX_PARAMS + 10)])), rd.MAX_PARAMS,
   "the filters per report are bounded")
eq(len(rd.rows({"portal_reports": ["r%d" % i for i in range(rd.MAX_ROWS + 10)]})), rd.MAX_ROWS,
   "…and so are the reports per source")


print("\n== §F  ADVICE, NOT A REFUSAL ==")
bare = rd.rows({"portal_reports": ["store_total"]})[0]
ok("no filters are declared" in " ".join(bare["problems"]),
   "a report with no filters is told so in the operator's words")
eq(bare["key"], "store_total", "…and is still saved and still shown")
dated = rd.row({"key": "x", "nav": ["X"]}, {"params": [{"selector": "#a", "kind": "fill",
                                                        "value": "2026-10-01"}]})
ok(any("same page" in p for p in dated["problems"]),
   "a filter with a literal date instead of a token is flagged — every run would export one day")
ok(rd.row({"key": "y", "nav": ["Y"]},
          {"params": [{"selector": "#a", "kind": "fill", "value": "{date}"}]})["problems"] == [],
   "a report declared properly has nothing to say about it")
eq(rd.row("", None), None, "a row with no key is not a row")
eq(rd.row(17, None), None, "…and neither is a number")


print("\n== §G  A CAPTURE SUGGESTS, IT DOES NOT DECIDE ==")
human = rd.rows(SRC)
# The human corrected #from by hand; the capture read the page and suggests a different selector for
# the same labelled control, plus one genuinely new control.
sug = {"params": [{"selector": "#ctl00_txtFrom", "kind": "fill", "value": "{date}", "label": "From"},
                  {"selector": "#store", "kind": "select", "value": "All", "label": "Store"}]}
merged = rd.merge_suggestion(human, "Item Sales", sug)
sels = [p["selector"] for p in merged[0]["params"]]
ok("#from" in sels, "the selector the human edited survives the capture")
ok("#ctl00_txtFrom" not in sels, "…and the capture's rival for the same labelled control is not added")
ok("#store" in sels, "a control the row did not have yet IS added")
eq(merged[1], human[1], "another report's row is untouched")
eq(human[0]["params"][0]["selector"], "#from", "nothing is mutated in place")
eq(rd.merge_suggestion(human, "no_such_report", sug), human,
   "a suggestion for a report that is not declared changes nothing")
eq(rd.merge_suggestion(None, "item_sales", sug), [], "…and neither does one with no rows")


print("\n== §H  THE LOCK ==")
src_rd = read("backend/app/modules/commcalc/report_declaration.py")
exe_rd = strip_text(src_rd)
src_router = read("backend/app/modules/commcalc/router.py")

ok("pos_report_sweep" in src_rd and "spec_key" in src_rd, "the key rule is dereferenced")
ok("def spec_key" not in exe_rd, "…and not redefined here")
ok("portal_report_pull" in src_rd, "the filter vocabulary is dereferenced")
ok(re.search(r"PARAM_KINDS\s*=\s*_prp\.PARAM_KINDS", src_rd) is not None,
   "…by reference, so a kind added to the pull is offerable without a second edit")
ok(re.search(r"DATE_TOKENS\s*=\s*_prp\.DATE_TOKENS", src_rd) is not None, "…as are the date tokens")
ok("report_columns" in src_rd, "word comparison is report_columns.norm (§COMMCALC-38)")
ok("def norm(" not in exe_rd, "…not a normalizer of its own")
for fn in ("rows", "columns", "row", "clean_params", "merge_suggestion", "row_problems"):
    eq(len(re.findall(r"def %s\s*\(" % fn, src_rd)), 1, "%s is defined once" % fn)
ok('"/data-sources/{sid}/reports"' in src_router, "the router exposes the editable declaration")
ok(re.search(r'@router\.put\("/data-sources/\{sid\}/reports"\)', src_router) is not None,
   "…and a save for it")
ok("report_declaration" in src_router, "the router dereferences this home")
ok(re.search(r"def (data_source_reports|save_data_source_reports)[\s\S]{0,2000}?rd\.columns\(", src_router)
   is not None, "the save builds the columns HERE, not in the endpoint")
ok(".eq(\"org_id\", org_id)" in src_router, "the endpoints are org-scoped (the house rule)")
ok(not re.search(r"\b(ups|vidapay|b2bsoft|boost|total|luxelink|cellfonz|item sales|tender cash)\b",
                 exe_rd, re.I),
   "the home names no portal, tenant or report — the fourteen a tenant wants are rows in their "
   "database (RULE TWO)")


print("\n== §I  NEGATIVE CONTROLS — the shipped rules, armed ==")


def armed(label, fn):
    try:
        ok(fn() is True, "ARMED: %s" % label)
    except Exception as e:
        ok(False, "control blew up (%s): %r" % (label, e))


armed("two rows claiming one key collapse to one, not two reports",
      lambda: len(rd.rows({"portal_reports": [{"key": "a b"}, {"key": "a_b"}]})) == 1)
armed("…and the same on save",
      lambda: len(rd.columns([{"key": "a b"}, {"key": "a_b"}])[0]) == 1)
armed("an unknown export falls back rather than saving an export nothing can press",
      lambda: rd.columns([{"key": "k", "export": "telepathy"}])[0][0]["export"] == "csv")
armed("a declared export the pull knows is kept",
      lambda: rd.columns([{"key": "k", "export": "excel"}])[0][0]["export"] == "excel")
armed("an empty line_map is not written (it would read as a declaration)",
      lambda: "line_map" not in rd.columns([{"key": "k", "line_map": {}}])[0][0])
armed("a declared line_map IS written",
      lambda: rd.columns([{"key": "k", "line_map": {"x": "y"}}])[0][0]["line_map"] == {"x": "y"})
armed("the editor's own fields never reach the stored report",
      lambda: set(rd.columns([rows[0]])[0][0]) <= {"key", "label", "nav", "export", "line_map"})
armed("saving no rows clears the reports but keeps the other calibration entries",
      lambda: rd.columns([], SRC["portal_calibration"]) == ([], {
          "item_sales": SRC["portal_calibration"]["item_sales"],
          "login": SRC["portal_calibration"]["login"]}))


print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
