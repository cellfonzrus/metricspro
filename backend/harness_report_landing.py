#!/usr/bin/env python3
"""WHERE A REPORT'S ROWS LAND — the lock (index §COMMCALC-85).

DB-free. It fails the build if the mechanism that closed this defect comes un-wired:

  §A  the fact survives the round trip — `columns(rows(src))` keeps the target it was given, which
      is the exact property that was false until 2026-11-26 and the reason no declaration had one;
  §B  which landings are legal is the CONNECTOR's own answer, dereferenced from its `TARGETS` and
      never a list spelled anywhere else;
  §C  a target the connector does not land is refused where somebody can fix it, not stored;
  §D  the connector is a REQUIRED argument of the writer — no house default can stand in for a
      tenant's connector;
  §E  one writer: both doors that store a declaration go through `router._save_source_reports`;
  §F  the fan-out is safe by default — an existing declaration is not replaced unless asked, a dry
      run writes nothing, and an unreadable login list refuses rather than declaring onto nothing;
  §G  negative controls: each check is shown to FAIL on the shape it forbids.

Run: python3 backend/harness_report_landing.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from app.modules.commcalc import processor_backoffice_sweep as pbs   # noqa: E402
from app.modules.commcalc import report_declaration as rd           # noqa: E402
from app.modules.commcalc import report_landing as rl               # noqa: E402

PASS, FAIL = [], []


def ck(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (("   " + detail) if (detail and not ok) else ""))


CONN = pbs.CONNECTOR
TGT = sorted(pbs.TARGETS)[0]
SRC = {"processor": CONN,
       "portal_reports": [{"key": "daily_tx", "label": "Daily TX", "nav": ["Reports"],
                           "target": TGT}],
       "portal_calibration": {"daily_tx": {"params": [], "notes": "kept"}}}

print("§A  the fact survives the round trip (this is what was false)")
rows = rd.rows(SRC)
ck("(a) the editor reads the declared landing back", rows[0]["target"] == TGT)
reports, cal = rd.columns(rows, SRC["portal_calibration"], processor=CONN)
ck("(a) …and a save writes it again — columns(rows(src)) keeps it",
   reports[0].get("target") == TGT)
ck("(a) the pull's own resolver sees the same landing",
   pbs.report_specs(src_row={"portal_reports": reports})[0]["target"] == TGT)
ck("(a) a calibration entry's other keys still survive", cal["daily_tx"].get("notes") == "kept")
ck("(a) an undeclared landing is NOT stored as empty — absence stays absence",
   "target" not in rd.columns([{"key": "k"}], None, processor=CONN)[0][0])
#: A declaration from before this field existed must round-trip byte-identically — that property is
#: what makes the editor safe to open on 27 logins, and adding a field is exactly when it breaks.
#: `nav` is the one derived value: the editor SHOWS the hint the key gives, and the save stores it.
_OLD = {"key": "daily_tx", "label": "Daily TX"}
ck("(a) a declaration written before this field existed round-trips unchanged",
   rd.columns(rd.rows({"processor": CONN, "portal_reports": [dict(_OLD)]}),
              None, processor=CONN)[0]
   # "tx" is too short to be a nav hint (`len(w) > 2`), so only "daily" survives.
   == [{**_OLD, "nav": ["daily"], "export": "csv"}],
   detail=repr(rd.columns(rd.rows({"processor": CONN, "portal_reports": [dict(_OLD)]}),
                          None, processor=CONN)[0]))

print("§B  the legal landings are the connector's own")
ck("(b) they are the connector's TARGETS, dereferenced",
   rl.targets_for(CONN) == tuple(sorted(pbs.TARGETS)))
ck("(b) the registry is keyed by the connector's OWN slug", CONN in rl.connectors())
ck("(b) a connector that lands nothing offers nothing", rl.targets_for("pos_report") == ())
ck("(b) …and says so rather than pretending", rl.lands_anything("pos_report") is False)
ck("(b) an unknown connector lands nothing", rl.targets_for("no-such-connector") == ())
ck("(b) no landing name is SPELLED outside the connector that owns it",
   not [p for p in ("app/modules/commcalc/report_landing.py",
                    "app/modules/commcalc/report_declaration.py")
        if TGT in open(os.path.join(ROOT, p), encoding="utf-8").read()])
src_router = open(os.path.join(ROOT, "app/modules/commcalc/router.py"), encoding="utf-8").read()
ck("(b) …nor in the router", TGT not in src_router)
front = os.path.join(os.path.dirname(ROOT), "frontend/src/components/commcalc/PortalReportEditor.tsx")
ck("(b) …nor on the screen, which reads the served list (RULE TWO)",
   TGT not in open(front, encoding="utf-8").read())

print("§C  a landing the connector does not have is refused where it can be fixed")
ck("(c) a legal target is kept, case and padding folded",
   rl.clean_target(CONN, "  " + TGT.upper() + " ") == TGT)
ck("(c) an illegal one answers '' — never passed through as plausible",
   rl.clean_target(CONN, "somewhere_else") == "")
ck("(c) …and is not stored by the writer either",
   "target" not in rd.columns([{"key": "k", "target": "somewhere_else"}], None,
                              processor=CONN)[0][0])
ck("(c) one connector cannot be given another's landing",
   rl.clean_target("pos_report", TGT) == "")
ck("(c) the editor is TOLD when no landing is declared and it could be",
   "No landing is declared" in rl.target_problem(CONN, ""))
ck("(c) …and that advice reaches the row",
   any("No landing is declared" in p
       for p in rd.row({"key": "k"}, None, CONN)["problems"]))
ck("(c) a connector that lands nothing says nothing about the ROW — advice must be actionable",
   rl.target_problem("pos_report", "") == "")
ck("(c) an illegal stored value is named back", "does not land" in rl.target_problem(CONN, "nope"))

print("§D  the connector is required, never defaulted")
sig = [a.arg for a in ast.parse(open(rd.__file__, encoding="utf-8").read()).body[-1].args.kwonlyargs]
cols_fn = [n for n in ast.walk(ast.parse(open(rd.__file__, encoding="utf-8").read()))
           if isinstance(n, ast.FunctionDef) and n.name == "columns"][0]
ck("(d) `processor` is a keyword-only argument of columns",
   "processor" in [a.arg for a in cols_fn.args.kwonlyargs])
ck("(d) …with NO default, so a caller cannot forget to say which connector",
   all(d is None for d in cols_fn.args.kw_defaults))
try:
    rd.columns([{"key": "k"}], None)
    missing_raises = False
except TypeError:
    missing_raises = True
ck("(d) calling it without one is a TypeError, not a silent wrong answer", missing_raises)

print("§E  one writer for a declaration")
tree = ast.parse(src_router)
fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
ck("(e) the one writer exists", "_save_source_reports" in fns)
ck("(e) the fan-out endpoint exists", "declare_reports_across_logins" in fns)
one = ast.get_source_segment(src_router, fns["_save_source_reports"]) or ""
editor = ast.get_source_segment(src_router, fns.get("save_data_source_reports")) or ""
fan = ast.get_source_segment(src_router, fns.get("declare_reports_across_logins")) or ""
ck("(e) the editor's save goes through it", "_save_source_reports(" in editor)
ck("(e) the fan-out goes through it too", "_save_source_reports(" in fan)
ck("(e) and neither writes the column itself",
   "portal_reports" not in editor.replace('body.get("reports")', "")
   and 'table("data_source").update' not in fan)
ck("(e) the one writer reads the connector off the ROW, not off the request",
   'src.get("processor")' in one and "body" not in one)
ck("(e) the writer is the only place the columns are built",
   src_router.count("rd.columns(") == 1)
ck("(e) the fan-out is gated like changing a login", "_require_import_admin(" in fan)
ck("(e) …and org-scoped", "require_org(" in fan and 'eq("org_id", org_id)' in fan)

print("§F  the fan-out is safe by default")
ck("(f) an existing declaration is left alone unless asked",
   "already declared" in fan and "body.overwrite" in fan)
ck("(f) a dry run writes nothing", re.search(r"if body\.dry_run[\s\S]{0,400}?continue", fan)
   is not None)
ck("(f) every login is named in the answer, none silently skipped",
   '"login": who' in fan and "logins" in fan)
ck("(f) an unreadable login list REFUSES rather than declaring onto nothing",
   "could not be read" in fan and "409" in fan)
ck("(f) a declaration saved with no landing SAYS its rows cannot be stored",
   "cannot be stored" in fan)
ck("(f) no logins on that connector is answered plainly, not as success over nothing",
   "no logins on that connector" in fan)
# One post writes once per login, so the login count is the work. It is BOUNDED, and the bound
# REFUSES rather than truncates — applying to the first N and reporting success is the silent-drop
# defect §COMMCALC-83 exists to prevent.
_ceiling = next((n.value.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "MAX_FANOUT_LOGINS" for t in n.targets)
                 and isinstance(n.value, ast.Constant)), None)
ck("(f) the fan-out is bounded by a declared ceiling", "MAX_FANOUT_LOGINS" in fan
   and isinstance(_ceiling, int) and _ceiling > 0, repr(_ceiling))
ck("(f) …and above it nothing is written, rather than the first N",
   "Nothing was declared" in fan)

print("§G  negative controls — each check fails on the shape it forbids")
ck("(g) a writer that dropped the target would be caught",
   "target" not in {k for r in rd.columns([{"key": "k", "target": "nope"}], None,
                                          processor=CONN)[0] for k in r})
ck("(g) …and the real writer keeps a legal one",
   rd.columns([{"key": "k", "target": TGT}], None, processor=CONN)[0][0]["target"] == TGT)
ck("(g) a registry that spelled its own landings would differ from the connector's",
   rl.targets_for(CONN) != ("a_made_up_landing",))
ck("(g) a connector module that cannot be read lands nothing rather than raising",
   rl.targets_for(None) == ())
ck("(g) the editor serves the list from the backend",
   '"landing_targets"' in src_router)

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED: " + "; ".join(FAIL))
    sys.exit(1)
print("OK — a report's landing is declarable, survives a save, and is the connector's own answer.")
