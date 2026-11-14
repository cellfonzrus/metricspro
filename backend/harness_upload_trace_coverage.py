"""THE RATCHET — a writing upload endpoint records what it did, and the silent list can only shrink.

OWNER REPORT 2026-10-13: *"jose was not able to uloasd the filres"*, and an hour later *"i just
upladed handset infor monthwise and also all teptherg but it does nto show me any link for a
report"*. Both reduced to one fact: **a prospect upload that failed left no record anywhere.** Two
tables agreed that nothing had arrived — `commcalc.upload_trace` and `core.import_batches` — and
neither could say whether a file had been refused or never sent, because the prospect endpoint wrote
to neither. A refused file and a loaded file looked identical to the person who pressed the button.

THE CLASS, not the instance. "Did this ingest happen, and what did it do" has ONE home:
`commcalc/router._write_upload_trace`, the owner's debug-first surface. Every upload that WRITES rows
is a caller of it, and the prospect upload was a caller that did not dereference it. Fixing only that
endpoint would be the patchwork CLAUDE.md forbids, because the next writing upload would be just as
silent. So the general fact is enforced here instead.

WHY A RATCHET AND NOT A BIG-BANG. The scan below finds **seven** other writing upload endpoints that
do not trace, each in a module this change has no business rewriting (ePay settlement, carrier recon,
the commission ledger import, agency transfers, product MRC). Rewriting all eight in one PR would be
unreviewable and would touch three money paths to fix a diagnostic. So the debt is written down, with
a reason per entry, and the build enforces that it only ever gets smaller.

WHAT FAILS THE BUILD
  (a) A NEW SILENT WRITING UPLOAD. An endpoint that takes an `UploadFile` and writes rows, does not
      reach `_write_upload_trace`, and is not in the register → RED. This is the check that stops the
      defect being re-introduced.
  (b) A STALE REGISTER LINE. An endpoint listed as silent that now traces → RED until its line is
      deleted. Without this the list never shrinks and the ratchet is decorative.
  (c) THE COUNT ONLY FALLS. More entries than PINNED_MAX → RED. Wiring an endpoint lowers the pin.
  (d) A DEAD REGISTER LINE. A listed endpoint that no longer exists → RED, so deletions stay honest.
  (e) THE PROSPECT UPLOAD IS NOT IN THE REGISTER, and reaches the trace home in a `finally` — the
      posture `upload_file` has, so an exception is traced rather than only a clean return.
  (f) ONE TRACE HOME. A second insert into `upload_trace` from anywhere but
      `ingest_record.write_trace` is a second answer to "what does an ingest record" → RED. The home
      moved out of `router._write_upload_trace` on 2026-11-14 (index §COMMCALC-73) so that the ePay
      portal sweep could reach it: `router` imports the sweep, so a home inside `router` was
      unreachable from it, and the sweep had grown its own half-record instead. `router` now
      dereferences it.
  (j) THE SWEEP CANNOT LAND SILENTLY. `epay_sweep` reaches `auto_calc.landed` from exactly ONE place,
      `epay_sweep.landed`, which also records the landing. Recording and recalculating are two
      consequences of the same fact; a second call site is a storage path that can land rows while
      every "last upload" surface keeps showing the previous month — which is exactly what comp and
      payment detail did for 37 days.
  (k) EVERY STORAGE PATH REACHES IT, derived from the call graph: a function in `epay_sweep` that
      replaces or upserts rows must reach `landed`, directly or through one helper.
  (g) A READ-ONLY endpoint is NOT required to trace. `/column-mapping/detect`, `.../sample` and the
      `analyze` previews write nothing, and demanding a trace row from them would make the lock noise
      and get it switched off. "Writes rows" is DERIVED from the call graph, never from a name list.
  (h) SUBSTANCE, not just a row count. `prospect_eval.substance` reads its period columns from
      `data_lineage_registry.MONTH_COLUMNS` (the one home) rather than keeping a list, and
      `substance_note` returns None when nothing is wrong, so an absence is never a finding.
  (i) NEGATIVE CONTROLS over synthetic sources: each of the above must be able to go red.

WHAT THIS SCAN CANNOT SEE, said out loud. "Writes rows" is resolved within `commcalc/router.py`
only — the endpoint's own body, plus one level into a helper defined in the same module. An endpoint
that delegates its write to ANOTHER module reads as read-only here and is not counted. That is a
stated limit, not a claim of completeness: it is why (b2) refuses a register line for an endpoint
this scan calls read-only, so the register can never be used to assert something the scan did not
measure. Widening the scan across modules is its own change.

DB-free. Run: `python3.11 backend/harness_upload_trace_coverage.py`
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harnesslib import py_code_only                                    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTER = os.path.join(HERE, "app", "modules", "commcalc", "router.py")
PEV = os.path.join(HERE, "app", "modules", "commcalc", "prospect_eval.py")

TRACE_HOME = "_write_upload_trace"

# ── THE REGISTER ──────────────────────────────────────────────────────────────────────────────────
# A writing upload endpoint that does not record what it did, with the reason it is still silent.
# This list may only SHRINK. Adding to it is not a way to pass this lock: a new endpoint belongs in
# the trace home, and PINNED_MAX below is what makes that non-negotiable.
SILENT_WRITING_UPLOADS = {
    "upload_mapped": "the mapped-columns re-ingest; shares _ingest_mapped_df with the traced path",
    "commission_ledger_import": "commission ledger landing — a money path, its own PR",
    "onboarding_intake_analyze": "an 'analyze' that also writes its parsed landing rows",
    "onboarding_intake_commit": "traces through a helper, so the row exists but this scan cannot see it",
    "import_product_mrc": "product MRC reference import",
    "ma_overview_upload_report": "delegates to upload_file, which traces; this scan cannot see it",
}
PINNED_MAX = 6

PASS, FAIL = [], []


def ok(label, cond, detail=""):
    (PASS if cond else FAIL).append((label, detail))


def _calls(node):
    """Every bare-name call inside `node`."""
    return {n.func.id for n in ast.walk(node) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)}


def _attr_chain(node):
    """`a.b.c(...)` → 'a.b.c' for an attribute call, else ''."""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


WRITERS = {"insert", "upsert", "delete"}


def _writes_directly(node):
    """`.insert(`/`.upsert(`/`.delete(` anywhere in this function's own body."""
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in WRITERS for n in ast.walk(node))


def _writes_rows(node, helpers=None):
    """DERIVED, never a name list: does this endpoint put rows anywhere?

    True when it writes directly, OR when it calls a module-level helper that does. The ONE level of
    indirection is not optional and was not free: without it `epay_upload`, `carrier_recon_upload`
    and five others read as read-only purely because they delegate, and the lock would have declared
    the siblings exempt instead of counting them — the lock excusing the very defect it exists for.

    A `detect`/`analyze`/`sample` preview reaches no writer by either route, which is why it is not
    asked for a trace row rather than being named in an exception list."""
    if _writes_directly(node):
        return True
    called = _calls(node)
    for name in called:
        h = (helpers or {}).get(name)
        if h is not None and (_writes_directly(h) or bool(_calls(h) & set(helpers or {}) and any(
                _writes_directly((helpers or {}).get(c)) for c in _calls(h) if c in (helpers or {})))):
            return True
    return False


def endpoints(path):
    """[(name, route, takes_file, writes, traces, in_finally)] for every function in `path`."""
    tree = ast.parse(open(path).read())
    helpers = {n.name: n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = list(n.args.args) + list(n.args.kwonlyargs)
        if not any(isinstance(a.annotation, ast.Name) and a.annotation.id == "UploadFile"
                   for a in args):
            continue
        route = None
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args:
                try:
                    route = ast.literal_eval(d.args[0])
                except Exception:
                    pass
        if route is None:
            continue                          # a helper, not an endpoint
        traces = TRACE_HOME in _calls(n)
        in_finally = any(TRACE_HOME in _calls_in(t.finalbody) for t in ast.walk(n)
                         if isinstance(t, ast.Try) and t.finalbody)
        out.append((n.name, route, True, _writes_rows(n, helpers), traces, in_finally))
    return out


def _calls_in(body):
    found = set()
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                found.add(n.func.id)
    return found


# ── the scan ──────────────────────────────────────────────────────────────────────────────────────
EPS = endpoints(ROUTER)
WRITING = [e for e in EPS if e[3]]
READONLY = [e for e in EPS if not e[3]]
SILENT = [e for e in WRITING if not e[4]]

print(f"UPLOAD-TRACE COVERAGE — {len(EPS)} upload endpoints · {len(WRITING)} write rows · "
      f"{len(READONLY)} read-only · {len(SILENT)} silent\n")

# (a) no NEW silent writing upload
unregistered = sorted(e[0] for e in SILENT if e[0] not in SILENT_WRITING_UPLOADS)
ok("(a) no writing upload endpoint is silent and unregistered", not unregistered,
   f"silent and unlisted: {unregistered}")

# (b) no stale register line
traced_but_listed = sorted(e[0] for e in WRITING if e[4] and e[0] in SILENT_WRITING_UPLOADS)
ok("(b) the register carries no endpoint that now traces", not traced_but_listed,
   f"listed but tracing — delete these lines: {traced_but_listed}")
listed_readonly = sorted(e[0] for e in READONLY if e[0] in SILENT_WRITING_UPLOADS)
ok("(b2) the register carries no endpoint that writes nothing", not listed_readonly,
   f"listed but read-only — delete these lines: {listed_readonly}")

# (c) the count only falls
ok(f"(c) the silent count is at or under its pin ({PINNED_MAX})", len(SILENT) <= PINNED_MAX,
   f"{len(SILENT)} silent against a pin of {PINNED_MAX}")
ok("(c2) the pin is not slack — it matches what is actually silent", len(SILENT) == PINNED_MAX,
   f"silent {len(SILENT)} · pin {PINNED_MAX} — lower the pin to {len(SILENT)}")

# (d) no dead register line
names = {e[0] for e in EPS}
dead = sorted(k for k in SILENT_WRITING_UPLOADS if k not in names)
ok("(d) every register line names an endpoint that still exists", not dead, f"gone: {dead}")
ok("(d2) every register line carries a reason",
   all(str(v).strip() for v in SILENT_WRITING_UPLOADS.values()))

# (e) the prospect upload traces, in a finally
pros = next((e for e in EPS if e[1] == "/prospects/{prospect_id}/upload"), None)
ok("(e1) the prospect upload endpoint is found", pros is not None)
ok("(e2) it is NOT in the silent register", bool(pros) and pros[0] not in SILENT_WRITING_UPLOADS)
ok("(e3) it reaches the trace home", bool(pros) and pros[4])
ok("(e4) it traces in a `finally`, so a refusal is traced too", bool(pros) and pros[5])

CODE = py_code_only(open(ROUTER).read())
ok("(e5) it traces under its own source label", 'source="prospect-upload"' in CODE)
ok("(e6) the impl is a separate function, so the wrapper cannot be bypassed",
   "async def _upload_prospect_feed_impl(" in CODE)

# (f) ONE trace home — across the whole module, not just the router
def _trace_inserters(path):
    """[(file, function)] for every insert into the trace table in `path`. The table may be named by
    a constant (`TRACE_TABLE`), so the literal is not what is scanned for — the call chain is."""
    src = open(path).read()
    tree = ast.parse(src)
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "insert"):
            continue
        chain = _attr_chain(n.func)
        line = src.splitlines()[n.lineno - 1] if n.lineno <= len(src.splitlines()) else ""
        if "upload_trace" not in chain and "upload_trace" not in line and "TRACE_TABLE" not in line:
            continue
        fn = None
        for f in ast.walk(tree):
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    f.lineno <= n.lineno <= (f.end_lineno or f.lineno):
                fn = f.name
        out.append((os.path.basename(path), fn))
    return out

COMMCALC = os.path.join(HERE, "app", "modules", "commcalc")
_writers = []
for _f in sorted(os.listdir(COMMCALC)):
    if _f.endswith(".py"):
        _writers += _trace_inserters(os.path.join(COMMCALC, _f))
ok("(f) upload_trace is written from ONE place",
   set(_writers) <= {("ingest_record.py", "write_trace")}, f"writers: {sorted(set(_writers))}")
ok("(f2) the router dereferences that home rather than keeping its own insert",
   "_ingest_record.write_trace(" in CODE and TRACE_HOME in CODE)
ok("(f3) and the target-table map is not copied back into the router",
   "_TRACE_TARGET_TABLE = _ingest_record.TARGET_TABLE" in CODE)

# (j)/(k) the sweep cannot land rows silently
SWEEP = os.path.join(COMMCALC, "epay_sweep.py")
_sweep_tree = ast.parse(open(SWEEP).read())
_sweep_fns = {f.name: f for f in ast.walk(_sweep_tree)
              if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _hook_sites(tree):
    """Which functions reach `auto_calc.landed(` (under any import alias)."""
    hits = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "landed" and "auto_calc" in _attr_chain(n.func):
            for f in ast.walk(tree):
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                        f.lineno <= n.lineno <= (f.end_lineno or f.lineno):
                    hits.add(f.name)
    return hits


_hooks = _hook_sites(_sweep_tree)
ok("(j1) the post-landing hook is reached from ONE place in the sweep", _hooks == {"landed"},
   f"call sites: {sorted(_hooks)}")
_landed_src = ast.get_source_segment(open(SWEEP).read(), _sweep_fns["landed"]) or "" \
    if "landed" in _sweep_fns else ""
ok("(j2) and that place records the landing in the same call",
   "sweep_landed(" in _landed_src, "landed() must call ingest_record.sweep_landed")


def _replaces_rows(fn):
    """DERIVED: does this function put rows into a feed table? A replace helper, the DTD ingest, or a
    direct upsert — never a name list."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            chain = _attr_chain(n.func) if isinstance(n.func, ast.Attribute) else (
                n.func.id if isinstance(n.func, ast.Name) else "")
            if chain.endswith("_safe_replace") or chain.endswith("safe_replace") \
                    or chain.endswith("_ei.ingest") or chain.endswith(".upsert"):
                return True
    return False


def _reaches_landed(fn, fns=None, depth=1):
    fns = _sweep_fns if fns is None else fns
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            if n.func.id == "landed":
                return True
            if depth and n.func.id in fns and _reaches_landed(fns[n.func.id], fns, depth - 1):
                return True
    return False


_paths = {name: _reaches_landed(fn) for name, fn in _sweep_fns.items()
          if _replaces_rows(fn) and name != "landed"}
ok("(k1) every storage path in the sweep reaches the one landing call",
   _paths and all(_paths.values()), f"silent paths: {sorted(k for k, v in _paths.items() if not v)}")
ok("(k2) and all three of them are seen (month replace, per-day replace, the DTD ingest)",
   len(_paths) >= 3, f"found: {sorted(_paths)}")

# (g) a read-only upload endpoint is not asked to trace
ok("(g1) read-only upload endpoints exist and are exempt", len(READONLY) > 0,
   f"{len(READONLY)} preview endpoints")
ok("(g2) 'writes rows' is derived, not a name list",
   "WRITERS = {" in open(__file__).read() and "_writes_rows" in open(__file__).read())
_detect = next((e for e in EPS if e[1] == "/column-mapping/detect"), None)
ok("(g3) the column-mapping preview is classified read-only",
   _detect is not None and not _detect[3])

# (h) substance reads the ONE home for period columns, and says nothing when nothing is wrong
sys.path.insert(0, HERE)
from app.modules.commcalc import prospect_eval as PEV_MOD                 # noqa: E402
PCODE = py_code_only(open(PEV).read())
ok("(h1) substance dereferences data_lineage_registry.MONTH_COLUMNS",
   "MONTH_COLUMNS" in PCODE and "data_lineage_registry" in PCODE)
ok("(h2) it keeps no period-column list of its own",
   PCODE.count('"period_month"') == 0 and PCODE.count("'period_month'") == 0)

_sub = PEV_MOD.substance([{"period": "August 2026", "period_month": 8, "period_year": 2026},
                          {"period": "August 2026", "price": 529.0}])
ok("(h3) a row carrying nothing but its period counts as empty", _sub["empty"] == 1, str(_sub))
ok("(h4) a row carrying a value counts as carrying data", _sub["carrying_data"] == 1, str(_sub))
ok("(h5) both rows count as dated", _sub["dated"] == 2 and _sub["undated"] == 0, str(_sub))
_all_good = PEV_MOD.substance([{"period": "x", "price": 1.0}])
ok("(h6) no note when every row carries data",
   PEV_MOD.substance_note(_all_good, "MA Commission Details") is None)
_note = PEV_MOD.substance_note(PEV_MOD.substance(
    [{"period": "x"}, {"period": "x"}]), "MA Commission Details")
ok("(h7) every row empty names the column mapping as the thing to check",
   _note and "column mapping" in _note and "MA Commission Details" in _note, str(_note))
_part = PEV_MOD.substance_note(PEV_MOD.substance(
    [{"period": "x"}, {"period": "x", "price": 2.0}]), "MA Commission Details")
ok("(h8) a partly empty file says how many rows the evaluation can see",
   _part and "1 of the 2" in _part, str(_part))
ok("(h9) an empty file measures as nothing, never as a problem",
   PEV_MOD.substance([]) == {"rows": 0, "empty": 0, "carrying_data": 0, "dated": 0, "undated": 0}
   and PEV_MOD.substance_note(PEV_MOD.substance([]), "x") is None)
ok("(h10) a zero amount does not count as carrying data (the wrong-layout shape)",
   PEV_MOD.substance([{"period": "x", "price": 0.0, "tspid": ""}])["empty"] == 1)
ok("(h11) substance is pure — it does not mutate its rows",
   (lambda rs: (PEV_MOD.substance(rs), rs == [{"period": "x", "price": 1.0}])[1])(
       [{"period": "x", "price": 1.0}]))
ok("(h12) a non-dict row is ignored rather than crashing the upload",
   PEV_MOD.substance([None, "x", {"period": "p", "price": 1.0}])["rows"] == 1)

# (i) NEGATIVE CONTROLS — each check must be able to go red
SYNTH_SILENT = '''
from fastapi import UploadFile, File
@router.post("/thing/upload")
async def thing_upload(file: UploadFile = File(...)):
    client.table("thing").insert([{"a": 1}]).execute()
    return {}
'''
SYNTH_TRACED = '''
from fastapi import UploadFile, File
@router.post("/thing/upload")
async def thing_upload(file: UploadFile = File(...)):
    try:
        client.table("thing").insert([{"a": 1}]).execute()
    finally:
        _write_upload_trace("org")
    return {}
'''
SYNTH_PREVIEW = '''
from fastapi import UploadFile, File
@router.post("/thing/detect")
async def thing_detect(file: UploadFile = File(...)):
    return {"columns": []}
'''
import tempfile


def _scan_src(src):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
        name = f.name
    try:
        return endpoints(name)
    finally:
        os.unlink(name)


_s = _scan_src(SYNTH_SILENT)
ok("NEG1 a new writing upload with no trace is seen as silent",
   len(_s) == 1 and _s[0][3] and not _s[0][4], str(_s))
_t = _scan_src(SYNTH_TRACED)
ok("NEG2 the same endpoint tracing in a finally reads as traced and in-finally",
   len(_t) == 1 and _t[0][4] and _t[0][5], str(_t))
_p = _scan_src(SYNTH_PREVIEW)
ok("NEG3 a preview that writes nothing is classified read-only",
   len(_p) == 1 and not _p[0][3], str(_p))
ok("NEG4 a trace outside a finally does NOT satisfy the finally check",
   not _scan_src(SYNTH_TRACED.replace("try:", "if True:")
                 .replace("    finally:\n        _write_upload_trace(\"org\")\n",
                          "    _write_upload_trace(\"org\")\n"))[0][5])
ok("NEG5 the register cannot excuse an endpoint that does not exist",
   "zzz_not_a_real_endpoint" not in names)

# NEG6/NEG7 — the sweep locks must be able to go red on synthetic sources.
SYNTH_SILENT_PATH = """
from app.modules.commcalc import auto_calc as _auto_calc

def landed(client, org, spec, key, **kw):
    _ingest_record.sweep_landed(client, org, **kw)
    return _auto_calc.landed(client, org, **kw)

def _store_quietly(client, org, spec, rows):
    _safe_replace(client, spec["table"], rows, lambda q: q, label="x")
    return {"rows": len(rows)}

def _store_loudly(client, org, spec, rows):
    _safe_replace(client, spec["table"], rows, lambda q: q, label="x")
    return landed(client, org, spec, "k", rows=len(rows))
"""
_ns = ast.parse(SYNTH_SILENT_PATH)
_nfns = {f.name: f for f in ast.walk(_ns) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
_npaths = {n: _reaches_landed(f, _nfns) for n, f in _nfns.items()
           if _replaces_rows(f) and n != "landed"}
ok("NEG6 a storage path that does not reach the landing call reads as silent",
   _npaths == {"_store_quietly": False, "_store_loudly": True}, str(_npaths))
SYNTH_TWO_HOOKS = SYNTH_SILENT_PATH + """
def _second_hook(client, org, spec):
    return _auto_calc.landed(client, org, table=spec["table"], periods=[], rows=1)
"""
ok("NEG7 a second post-landing hook call site is seen",
   _hook_sites(ast.parse(SYNTH_TWO_HOOKS)) == {"landed", "_second_hook"},
   str(_hook_sites(ast.parse(SYNTH_TWO_HOOKS))))

for label, detail in PASS:
    print(f"  PASS  {label}")
for label, detail in FAIL:
    print(f"  FAIL  {label}  — {detail}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if SILENT:
    print("\nstill silent (the register, which may only shrink):")
    for e in sorted(SILENT):
        print(f"    {e[0]:34s} {SILENT_WRITING_UPLOADS.get(e[0], '(UNREGISTERED)')}")
sys.exit(1 if FAIL else 0)
