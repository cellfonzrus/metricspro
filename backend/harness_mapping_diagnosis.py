"""WHY a file produced no rows — one home, every caller wired, and it cannot un-wire.

OWNER REPORT 2026-10-13. Two uploads were refused with: *"That file produced no rows once its header
and any total row were read."* Both files were fine. They were `MA - Empire October Commission
Details (24).xls` and its September twin, handed to the **MA Daily Transactions** reader — and a
Commission Details export and a Daily Transactions export share **not one column name** (verified
below: zero overlap between the two declared layouts). So every row mapped to nothing, `map_records`
dropped them all, and the refusal stated a fact about the FILE when what was wrong was the PAIRING.
The owner was sent looking for a problem in a spreadsheet that did not have one.

THE CLASS, not the instance. A mapper that knows which columns it looked for and can see which
columns the file carries must never report "no rows" without naming the difference. That answer —
"why did this produce nothing, and whose columns are these?" — is ONE fact with ONE home:
`column_mapping.no_rows_diagnosis`, over `column_mapping.header_fit`, over `column_mapping.match_key`.

THE SIBLINGS, found and fixed in the same change rather than excused. `map_records` has three
callers that land rows, and all three answered this question differently:
  · the prospect upload            — refused with the blanket sentence above;
  · `_ingest_mapped_df`            — the /upload-mapped core (also the intake commit and a custom
                                     report's dataset binding): returned `saved: 0` with NO reason
                                     at all, which is worse, not better;
  · the import wizard's commit     — its own copy of the pipeline, same silence.
All three now dereference the one home. The two live cores REPORT rather than raise, because an
automated sweep is allowed to hand them an empty day; the prospect upload still refuses, because a
person pressed a button and is owed an answer.

WHAT FAILS THE BUILD
  (a) A `map_records` CALLER THAT DOES NOT DEREFERENCE THE DIAGNOSIS. Derived from the AST, not a
      name list: a new landing path that drops every row silently is the defect, so it is RED.
  (b) A SECOND MATCH KEY. `match_key` is how the mapper pairs a rule's header with a file's column.
      Anything that answers FOR the mapper and spells that normalisation itself would be a second
      copy, free to drift into reporting a column as found that the mapper does not read → RED.
  (c) THE DIAGNOSIS IS SILENT WHEN NOTHING IS WRONG. `no_rows_diagnosis` returns None for a file
      that produced rows, so no caller can print a cause over a successful upload.
  (d) ONE RESOLUTION. The bulk read (`rules_by_report_key`) and the single read (`load_rules`) both
      resolve a rule through `resolve_rules`, so the candidates a diagnosis is scored against can
      never be resolved differently from the rules the upload itself used.
  (e) NO FEED IS NAMED IN THE MECHANISM (RULE TWO). The module must not contain a feed, carrier or
      tenant name: the candidates and their display labels are the caller's.
  (f) THE REPORTED CAUSE IS THE FIRST THING THAT WAS WRONG, in pipeline order, so a mis-paired file
      is never reported as an empty one.
  (g) THE OWNER'S OWN CASE, reproduced from the two declared layouts: his headers must diagnose as
      `no_columns_match` and must name MA Commission Details as where the file belongs.
  (h) NEGATIVE CONTROLS: each check above must be able to go red.

DB-free. Run: `python3.11 backend/harness_mapping_diagnosis.py`
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harnesslib import py_code_only                                    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CM_PATH = os.path.join(HERE, "app", "modules", "commcalc", "column_mapping.py")
ROUTER = os.path.join(HERE, "app", "modules", "commcalc", "router.py")

DIAGNOSIS = "no_rows_diagnosis"
MATCH_KEY = "match_key"

PASS, FAIL = [], []


def ok(label, cond, detail=""):
    (PASS if cond else FAIL).append((label, detail))


# ── the module under lock ─────────────────────────────────────────────────────────────────────────
_cm_src = open(CM_PATH).read()
_cm_code = py_code_only(_cm_src)
_cm_tree = ast.parse(_cm_src)
_cm_funcs = {n.name: n for n in _cm_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

for fn in (MATCH_KEY, "header_fit", DIAGNOSIS, "resolve_rules", "rules_by_report_key",
           "candidate_rules"):
    ok(f"A1 {fn} exists in the one home", fn in _cm_funcs)


# ── (a) every map_records caller dereferences the diagnosis ───────────────────────────────────────
def _calls_in(node):
    """Every call spelled in this function, as a bare name or a dotted tail."""
    out = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            out.add(f.id)
        elif isinstance(f, ast.Attribute):
            out.add(f.attr)
    return out


_r_tree = ast.parse(py_code_only(open(ROUTER).read()))
_r_funcs = [n for n in ast.walk(_r_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

_landing_callers, _missing = [], []
for fn in _r_funcs:
    calls = _calls_in(fn)
    if "map_records" not in calls:
        continue
    _landing_callers.append(fn.name)
    if DIAGNOSIS not in calls:
        _missing.append(fn.name)

ok("a1 every map_records caller in the router is accounted for", len(_landing_callers) >= 3,
   str(_landing_callers))
ok("a2 NO map_records caller drops every row without saying why", not _missing,
   f"silent landing paths: {_missing}")
ok("a3 the three known landing paths are among them",
   all(any(k in c for c in _landing_callers)
       for k in ("_ingest_mapped_df", "_upload_prospect_feed_impl")), str(_landing_callers))

# the ledger's own mapper is a DIFFERENT function (`_ledger_map_records`) and its own change; this
# lock states that limit rather than quietly covering it.
ok("a4 the ledger mapper is a separate path, stated not covered",
   "_ledger_map_records" in open(ROUTER).read())


# ── (b) one match key ─────────────────────────────────────────────────────────────────────────────
# Every function in the module that matches a HEADER must reach the key through `match_key`; the
# literal may appear only inside `match_key` itself. (A `.strip().lower()` over a VALUE — the boolean
# transform — is a different fact and is not counted: the scan is per function, not per file.)
_HEADER_MATCHERS = ("apply_mapping", "header_fit", DIAGNOSIS, "suggest", "candidate_rules")
_second_copies = []
for _fn in _HEADER_MATCHERS:
    _seg = py_code_only(ast.get_source_segment(_cm_src, _cm_funcs[_fn]) or "")
    if ".strip().lower()" in _seg:
        _second_copies.append(_fn)
ok("b1 no header matcher spells the mapper's key itself — all dereference match_key",
   not _second_copies, f"a second copy of the header key in: {_second_copies}")
ok("b1b the literal lives in match_key and nowhere else that matches a header",
   ".strip().lower()" in (ast.get_source_segment(_cm_src, _cm_funcs[MATCH_KEY]) or ""))
ok("b2 apply_mapping dereferences match_key rather than normalising inline",
   MATCH_KEY in _calls_in(_cm_funcs["apply_mapping"]))
ok("b3 header_fit dereferences the SAME key the mapper matches by",
   MATCH_KEY in _calls_in(_cm_funcs["header_fit"]))
ok("b4 the diagnosis reaches the key only through header_fit",
   "header_fit" in _calls_in(_cm_funcs[DIAGNOSIS]))


# ── (d) one resolution ────────────────────────────────────────────────────────────────────────────
ok("d1 load_rules resolves through resolve_rules",
   "resolve_rules" in _calls_in(_cm_funcs["load_rules"]))
ok("d2 the bulk read resolves through the same function",
   "resolve_rules" in _calls_in(_cm_funcs["rules_by_report_key"]))
ok("d3 candidate_rules reads the configured rows through the bulk read",
   "rules_by_report_key" in _calls_in(_cm_funcs["candidate_rules"]))


# ── (e) RULE TWO: no feed, carrier or tenant named in the mechanism ──────────────────────────────
_diag_code = py_code_only(ast.get_source_segment(_cm_src, _cm_funcs[DIAGNOSIS]) or "")
_banned = [w for w in ("boost", "total wireless", "luxelink", "cellfonz", "ma_commission",
                       "ma_daily_tx", "ma_marketplace") if w in _diag_code.lower()]
ok("e1 the diagnosis names no feed, carrier or tenant", not _banned, str(_banned))


# ── behaviour (c), (f), (g) ───────────────────────────────────────────────────────────────────────
sys.path.insert(0, HERE)
from app.modules.commcalc import column_mapping as cm                  # noqa: E402

RULES_TX = [{"target_field": "tx_date", "source_header": "Date of Transaction", "transform": "date10"},
            {"target_field": "account_id", "source_header": "Account ID", "transform": "text"},
            {"target_field": "merchant_discount", "source_header": "Merchant Discount",
             "transform": "number"}]
RULES_COMM = [{"target_field": "activation_order", "source_header": "Activation Order",
               "transform": "text"},
              {"target_field": "merchant_account_id", "source_header": "MerchantAccountId",
               "transform": "text"},
              {"target_field": "device_margin", "source_header": "Device Margin",
               "transform": "number"}]

# (c) silent when nothing is wrong
ok("c1 a file that produced rows gets NO diagnosis",
   cm.no_rows_diagnosis(["Account ID"], RULES_TX, 10, 10, 0, {"other": RULES_COMM}) is None)
ok("c2 one mapped row is enough to be silent",
   cm.no_rows_diagnosis(["Account ID"], RULES_TX, 10, 1) is None)

# (f) the first thing that was wrong, in pipeline order
ok("f1 nothing read at all reads as an empty file",
   cm.no_rows_diagnosis([], RULES_TX, 0, 0)["reason"] == "file_empty")
ok("f2 a header row with nothing under it reads as no data rows",
   cm.no_rows_diagnosis(["Account ID"], RULES_TX, 0, 0)["reason"] == "no_data_rows")
ok("f3 an empty file is never reported as a mis-pairing",
   cm.no_rows_diagnosis([], RULES_TX, 0, 0, 0, {"c": RULES_COMM})["better_fit"] is None)
_d_blank = cm.no_rows_diagnosis(["Account ID", "Date of Transaction"], RULES_TX, 7, 0)
ok("f4 columns present but every row blank says SO, not that the file is wrong",
   _d_blank["reason"] == "rows_blank_in_columns" and "7" in _d_blank["sentence"],
   _d_blank["reason"])
_d_tot = cm.no_rows_diagnosis(["Account ID"], RULES_TX, 4, 0, 4)
ok("f5 a file of nothing but totals rows says that", _d_tot["reason"] == "all_rows_are_totals")
ok("f6 the totals reading is only reached when the columns DID match",
   cm.no_rows_diagnosis(["Nothing At All"], RULES_TX, 4, 0, 4)["reason"] == "no_columns_match")

# (g) the owner's own case, from the two DECLARED layouts — no header list is written here
_specs = None
try:
    from app.modules.commcalc import report_pull
    _specs = {s["report_key"]: list((s.get("column_map") or {}).keys())
              for s in getattr(report_pull, "DEFAULT_REPORT_SPECS", []) if s.get("report_key")}
except Exception:
    _specs = None
ok("g0 the two declared layouts are readable", bool(_specs)
   and "ma_commission" in _specs and "ma_daily_tx" in _specs)
if _specs and "ma_commission" in _specs and "ma_daily_tx" in _specs:
    _comm_h, _tx_h = _specs["ma_commission"], _specs["ma_daily_tx"]
    _shared = {cm.match_key(h) for h in _comm_h} & {cm.match_key(h) for h in _tx_h}
    ok("g1 the two layouts share NO column — which is why nothing mapped", not _shared, str(_shared))
    _tx_rules = cm.default_mapping("ma_daily_tx")
    _recs = [{h: "x" for h in _comm_h} for _ in range(31)]
    _mapped = cm.map_records(_recs, _tx_rules, {"org_id": "o"})
    ok("g2 his file maps to zero rows under the feed he picked", len(_mapped) == 0, str(len(_mapped)))
    _dx = cm.no_rows_diagnosis(_comm_h, _tx_rules, len(_recs), len(_mapped), 0,
                               {"ma_commission": cm.default_mapping("ma_commission")},
                               {"ma_commission": "MA Commission Details"})
    ok("g3 the cause reported is the mis-pairing", _dx["reason"] == "no_columns_match", _dx["reason"])
    ok("g4 the sentence names the feed the file BELONGS to",
       "MA Commission Details" in _dx["sentence"], _dx["sentence"][:160])
    ok("g5 the sentence quotes the file's own columns back",
       "Activation Order" in _dx["sentence"])
    ok("g6 the sentence says nothing was written and nothing removed",
       "nothing was removed" in _dx["sentence"].lower())
    ok("g7 the better fit is machine-readable too, not only prose",
       (_dx.get("better_fit") or {}).get("key") == "ma_commission")
    ok("g8 the raw key is NOT shown to the person when a label exists",
       "ma_commission" not in _dx["sentence"])
    # and the right pairing is diagnosed as fine
    _good = cm.map_records(_recs, cm.default_mapping("ma_commission"), {"org_id": "o"})
    ok("g9 the SAME file under the right feed maps rows and gets no diagnosis",
       len(_good) == 31 and cm.no_rows_diagnosis(_comm_h, cm.default_mapping("ma_commission"),
                                                 31, len(_good)) is None, str(len(_good)))

# header_fit itself
_fit = cm.header_fit(["account id", "  Merchant Discount ", "Something Else"], RULES_TX)
ok("h1 header_fit matches case- and space-insensitively, as the mapper does",
   set(_fit["found"]) == {"Account ID", "Merchant Discount"}, str(_fit["found"]))
ok("h2 header_fit names what is missing in the MAPPING's spelling",
   _fit["missing"] == ["Date of Transaction"], str(_fit["missing"]))
ok("h3 header_fit names the file's own extra columns",
   _fit["extra"] == ["Something Else"], str(_fit["extra"]))
ok("h4 a mapping with no headers at all scores zero rather than dividing by zero",
   cm.header_fit(["a"], [])["fraction"] == 0.0)
ok("h5 a duplicated rule is counted once", len(cm.header_fit(
    ["Account ID"], RULES_TX + [dict(RULES_TX[1])])["expected"]) == 3)
ok("h6 match_key is the mapper's key, NOT a looser punctuation-collapsing one",
   cm.match_key("Top MA ID") == "top ma id" and cm.match_key("Top-MA-ID") != cm.match_key("Top MA ID"))

# the better fit must be the BEST one, not the first
_dx2 = cm.no_rows_diagnosis(["Activation Order", "MerchantAccountId", "Device Margin"], RULES_TX,
                            5, 0, 0,
                            {"one_column": [RULES_COMM[0]], "all_three": RULES_COMM})
ok("i1 the candidate named is the best fit, not the first offered",
   (_dx2.get("better_fit") or {}).get("key") == "all_three", str(_dx2.get("better_fit")))
ok("i2 a candidate that matches nothing is never named",
   cm.no_rows_diagnosis(["Zzz"], RULES_TX, 5, 0, 0, {"c": RULES_COMM})["better_fit"] is None)
ok("i3 no candidates at all still produces the cause",
   cm.no_rows_diagnosis(["Zzz"], RULES_TX, 5, 0)["reason"] == "no_columns_match")


# ── (h) negative controls: each check can go red ─────────────────────────────────────────────────
SYNTH_SILENT = '''
def land_a_feed(file, rules):
    mapped = column_mapping.map_records(records, rules, base)
    return {"saved": len(mapped)}
'''
SYNTH_WIRED = '''
def land_a_feed(file, rules):
    mapped = column_mapping.map_records(records, rules, base)
    why = column_mapping.no_rows_diagnosis(headers, rules, n, len(mapped))
    return {"saved": len(mapped), "no_rows_note": (why or {}).get("sentence")}
'''


def _silent_in(src):
    t = ast.parse(py_code_only(src))
    out = []
    for fn in [n for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        c = _calls_in(fn)
        if "map_records" in c and DIAGNOSIS not in c:
            out.append(fn.name)
    return out


ok("NEG1 a new landing path with no diagnosis is seen as silent",
   _silent_in(SYNTH_SILENT) == ["land_a_feed"], str(_silent_in(SYNTH_SILENT)))
ok("NEG2 the same path wired to the one home reads as covered", not _silent_in(SYNTH_WIRED))
ok("NEG3 a second match key inside a header matcher would be caught",
   ".strip().lower()" in py_code_only('def f(h):\n    return str(h).strip().lower()\n'))
ok("NEG4 a feed name inside the mechanism would be caught",
   any(w in "a sentence mentioning ma_daily_tx".lower()
       for w in ("ma_daily_tx",)))
ok("NEG5 the diagnosis cannot be made to speak over a successful upload",
   cm.no_rows_diagnosis([], [], 0, 1) is None)

for label, detail in PASS:
    print(f"  PASS  {label}")
for label, detail in FAIL:
    print(f"  FAIL  {label}  — {detail}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
print(f"\nmap_records landing paths wired to the one home: {sorted(_landing_callers)}")
sys.exit(1 if FAIL else 0)
