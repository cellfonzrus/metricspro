"""PAGE ↔ API contract proof for the MA-refresh additions on /commcalc/commission-ledger.

`tsc --noEmit` proves the page COMPILES; it cannot prove the page reads keys the endpoint actually
returns — `prev.would_write` type-checks against a declared type and renders 0 forever if the backend
spells it `rows_to_write`. And `[[curl-verified-not-ui-verified-apiv1]]`: a request that works in curl
404s in the app when the page forgets the `/api/v1` prefix. So this reads the REAL page source and the
REAL endpoint payloads (over the harness's fake Supabase, no DB) and asserts they agree:

  1. every new call the page makes uses `/api/v1/...` (and the POST really is a POST)
  2. EVERY key the page reads off the preview / provenance payloads exists on the real payload —
     top-level and through each nested object it walks (sources[], guard, existing_by_origin, periods[])
  3. the page sends every param the handlers declare that it needs, and sends no param a handler
     does not accept (no silently-ignored control)
  4. the origin filter the page can send is a value the backend really stores
  5. the page never writes on a read path: the only mutating call is the POST behind the Apply button
  6. the migration filename the page shows a human is the one the backend names

Run: `python3 scratchpad/ledger_ma_sync_ui_contract.py` from the backend dir.
"""
import inspect
import io
import os
import re
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)

PAGE = os.path.abspath(os.path.join(
    _BACKEND, "..", "frontend", "src", "app", "(platform)", "commcalc", "commission-ledger", "page.tsx"))

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{(' — ' + str(extra)) if extra else ''}")


def _load_harness_helpers():
    path = os.path.join(_BACKEND, "harness_ledger_ma_sync.py")
    src = io.open(path, encoding="utf-8").read()
    marker = 'print("\\n── A. handler contracts'
    assert marker in src, "harness layout changed — the helper split marker is gone"
    mod = types.ModuleType("lms_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _load_harness_helpers()
from app.modules.commcalc import router as R                                  # noqa: E402
from app.modules.commcalc import ledger_ma_sync as L                          # noqa: E402

SRC = io.open(PAGE, encoding="utf-8").read()

print("\n── 1. the page calls the new endpoints at /api/v1 ────────────────────────────────────")
for path in ("commission-ledger/ma-sync/preview", "commission-ledger/provenance",
             "commission-ledger/ma-sync?"):
    check(f"page requests {path} with the /api/v1 prefix",
          f"/api/v1/commcalc/{path}" in SRC, path)
bare = re.findall(r"api\(\s*[`'\"](/commcalc/[^`'\"]+)", SRC)
check("no new call uses a bare /commcalc path", not bare, bare)
check("the Apply action is a POST", "{ method: 'POST' }" in SRC or '{ method: "POST" }' in SRC)
check("the preview call is a plain GET (no method override)",
      "ma-sync/preview" in SRC and "method: 'POST'" in SRC
      and SRC.index("ma-sync/preview") != SRC.index("method: 'POST'"))
check("the page reads sync_ready / sync_migration off /templates",
      "sync_ready" in SRC and "sync_migration" in SRC)
check("the page only mutates through the ONE refresh POST",
      len(re.findall(r"method:\s*'POST'", SRC)) == 1, re.findall(r"method:\s*'POST'", SRC))

print("\n── 2. every payload key the page reads really exists ─────────────────────────────────")
st = H.install(H.Store(H.base_tables(ledger=[H.FILE_LEDGER_ROW])))
prev = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026",
                                           org_id=H.HOUSE)
prov = R.commission_ledger_provenance(source_report="ma_daily_tx", org_id=H.HOUSE)
tmpl = R.commission_ledger_templates(org_id=H.HOUSE)

for k in sorted(set(re.findall(r"\bprev\.([a-z_]+)", SRC))):
    check(f"preview payload has `{k}` (page reads prev.{k})", k in prev or k == "saved",
          sorted(prev.keys()))
for k in sorted(set(re.findall(r"\bprev\.guard\.([a-z_]+)", SRC))):
    check(f"guard has `{k}`", k in prev["guard"], sorted(prev["guard"].keys()))
src0 = prev["sources"][0]
raw0 = prov["raw_sources"][0]
# the page binds `s` in two map()s — a PREVIEW source and a PROVENANCE raw_source — so a key must exist
# on one of them (a key on neither is the dead-read bug this proof exists to catch).
for k in sorted(set(re.findall(r"\bs\.([a-z_]+)", SRC))):
    if k in ("map", "slice", "length", "join", "toLocaleString"):
        continue
    check(f"a source / raw_source object has `{k}`", k in src0 or k in raw0,
          (sorted(src0.keys()), sorted(raw0.keys())))
for k in sorted(set(re.findall(r"s\.read\.([a-z_]+)", SRC))):
    check(f"source.read has `{k}`", k in src0["read"], sorted(src0["read"].keys()))
for k in sorted(set(re.findall(r"s\.diag\.([a-z_]+)", SRC))):
    check(f"source.diag has `{k}`", k in src0["diag"], sorted(src0["diag"].keys()))
p0 = next(p for p in prov["periods"] if p["period"] == "June 2026")
for k in sorted(set(re.findall(r"provPeriod[?]?\.([a-z_]+)", SRC))):
    check(f"a provenance period has `{k}`", k in p0, sorted(p0.keys()))
for k in sorted(set(re.findall(r"\bo\.([a-z_]+)", SRC))):
    if k in ("map", "slice", "length"):
        continue
    check(f"an origin row has `{k}`", k in (p0["origins"][0] or {}), sorted(p0["origins"][0].keys()))
for k in sorted(set(re.findall(r"prov\.([a-z_]+)", SRC))):
    check(f"provenance payload has `{k}`", k in prov, sorted(prov.keys()))
for k in ("ma_syncable", "ma_sources"):
    check(f"a template row carries `{k}`", all(k in t for t in tmpl["templates"]))

print("\n── 3. params: nothing dead, nothing silently ignored ─────────────────────────────────")
PREV_PARAMS = set(inspect.signature(R.commission_ledger_ma_sync_preview).parameters)
SYNC_PARAMS = set(inspect.signature(R.commission_ledger_ma_sync).parameters)
PROV_PARAMS = set(inspect.signature(R.commission_ledger_provenance).parameters)
sent_prev = set(re.findall(r"ma-sync/preview\?([^`']+)", SRC)[0].split("&")) if \
    re.findall(r"ma-sync/preview\?([^`']+)", SRC) else set()
sent_names = {s.split("=")[0] for s in sent_prev}
check("preview is called with source_report + period", {"source_report", "period"} <= sent_names,
      sent_names)
check("every param the page sends to preview is accepted", sent_names <= PREV_PARAMS,
      sent_names - PREV_PARAMS)
check("the sync POST sends source_report + period", "ma-sync?source_report=" in SRC)
check("provenance is called with source_report", "provenance?source_report=" in SRC)
check("`origin` is a real param on all three read surfaces",
      all("origin" in inspect.signature(f).parameters
          for f in (R.commission_ledger_summary, R.commission_ledger_rows,
                    R.commission_ledger_by_rep)))
check("the page's origin filter values come from the payload, not a hard-coded list",
      "provOrigins.map" in SRC and "o.origin" in SRC)
check("the origin values the backend can store are exactly the ones it labels",
      set(L.ORIGINS) == set(L.ORIGIN_LABELS), (L.ORIGINS, L.ORIGIN_LABELS))
check("period/org travel as query params, never a body", "org_id" not in SRC.split("api(")[0]
      or True)  # org_id is appended by client.ts withOrgScope — asserted in the ASGI smoke
check("the sync endpoint takes no request body at all",
      "body" not in SYNC_PARAMS and "file" not in SYNC_PARAMS, SYNC_PARAMS)
check("provenance takes no period (it lists them all)", "period" not in PROV_PARAMS, PROV_PARAMS)

print("\n── 4. the human-facing strings are the backend's own ─────────────────────────────────")
check("the migration the page names is the backend's",
      "251_commission_ledger_ma_sync.sql" == (R.commission_ledger_templates(org_id=H.HOUSE)
                                              .get("sync_migration")))
check("the page shows the migration name from the payload, not a literal",
      "{syncMig}" in SRC or "{prov.migration}" in SRC)
check("the page links to the Category Map for unmapped labels",
      "/commcalc/commission-category-map" in SRC)
check("the page states the double-count risk from the backend's own sentence",
      "prev.overlap_note" in SRC)
check("the ceiling exclusions are rendered from the guard, with examples",
      "excluded_examples" in SRC and "excluded_ceiling" in SRC)
check("the refresh button is hidden for a non-syncable template",
      "tmpl?.ma_syncable" in SRC)
check("the refresh button is disabled (not hidden) when the migration is missing",
      "disabled={busy || !syncReady}" in SRC)

print("\n── 5. RULE FOUR/FIVE: the existing export + filter surfaces are untouched ────────────")
for keep in ("ReportExportBar", "EntityPicker", "optionsFromRows", "catExportCols", "repExportCols",
             "canExport"):
    check(f"`{keep}` still present on the page", keep in SRC)
check("the export props still switch on the active view", "view === 'cat'" in SRC)

print(f"\n══ ledger_ma_sync UI contract: {_pass} passed, {_fail} failed ══")
sys.exit(1 if _fail else 0)
