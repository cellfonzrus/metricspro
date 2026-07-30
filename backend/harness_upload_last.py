"""Endpoint harness for GET /api/v1/commcalc/upload/last — drives the REAL router handler against an
in-memory FAKE Supabase client (no network, no DB). What this proves:

  MULTI-TENANT (AGENT_CONTRACT RULE ONE)
  • org_id is a QUERY PARAM on the handler signature (never a constant / Form field / body)
  • EVERY read the handler issues carries `.eq('org_id', …)` — asserted by recording each query
  • a second tenant's ingests are never returned, in either direction (house ↮ tenant)

  HONESTY (the reason this endpoint exists)
  • `last_at` is the newest ingest that actually LANDED rows — a NEWER refused/zero-row attempt never
    becomes "last upload"; it surfaces separately as `latest_attempt`
  • when the newest attempt DID land rows, `latest_attempt` is null (no phantom warning)
  • a report that never ingested anything is returned EXPLICITLY with `last_at: null` when asked for,
    instead of being silently absent

  COVERAGE / CORRECTNESS
  • both journals are folded (upload_trace mig-202 AND the older upload_log) and the newer wins
    regardless of which table it came from, across mixed 'Z' / '+00:00' timestamp spellings
  • per-period + per-day counts become `period` / `span` / `days`
  • a report older than the recent window is still found via the targeted per-key lookup, including the
    second `.gt('rows_saved', 0)` pass when its newest row saved nothing
  • a missing upload_trace (mig 202 unrun) or upload_log degrades to a hint — never a 500 — and the
    other journal still answers

Run: `python3 harness_upload_last.py` from the backend dir.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import router as R

_pass = 0
_fail = 0
QUERY_LOG = []


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Minimal in-memory stand-in for the supabase-py query builder (only the verbs this handler uses).
# Unlike the imei harness this one implements ORDER + LIMIT for real, because the window-vs-targeted
# -lookup behaviour is exactly what needs proving.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _MissingTable(Exception):
    pass


class _Q:
    def __init__(self, store, schema, table):
        self._store, self._schema, self._table = store, schema, table
        self._eq, self._in, self._gt = {}, {}, {}
        self._order = None
        self._limit = None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self._eq[k] = v
        return self

    def in_(self, k, v):
        self._in[k] = list(v)
        return self

    def gt(self, k, v):
        self._gt[k] = v
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        QUERY_LOG.append({"table": self._table, "schema": self._schema, "eq": dict(self._eq),
                          "in": dict(self._in), "gt": dict(self._gt), "order": self._order,
                          "limit": self._limit})
        key = f"{self._schema}.{self._table}"
        if key not in self._store:
            raise _MissingTable(f"relation {key} does not exist")
        rows = []
        for r in self._store[key]:
            ok = True
            for k, v in self._eq.items():
                if str(r.get(k)) != str(v):
                    ok = False
            for k, v in self._in.items():
                if str(r.get(k)) not in {str(x) for x in v}:
                    ok = False
            for k, v in self._gt.items():
                if r.get(k) is None or not (float(r.get(k)) > float(v)):
                    ok = False
            if ok:
                rows.append(dict(r))
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: str(r.get(col) or ""), reverse=bool(desc))
        if self._limit is not None:
            rows = rows[:self._limit]
        return type("Res", (), {"data": rows})()


class _Schema:
    def __init__(self, store, schema):
        self._store, self._schema = store, schema

    def table(self, t):
        return _Q(self._store, self._schema, t)


class FakeClient:
    def __init__(self, store):
        self._store = store

    def schema(self, s):
        return _Schema(self._store, s)


def install(store):
    QUERY_LOG.clear()
    R.sb = lambda: FakeClient(store)      # noqa: E731


HOUSE = "00000000-0000-0000-0000-000000000001"
TEN = "00000000-0000-0000-0000-0000000000aa"


def trace(**kw):
    base = {"id": "t1", "org_id": HOUSE, "created_at": "2026-07-20T10:00:00+00:00",
            "upload_type": "sales", "source": "manual", "status": "ok", "skipped": None,
            "rows_saved": 100, "rows_in": 100, "filename": "f.xlsx", "target_table": "raw_sales",
            "periods": None, "date_counts": None, "note": None}
    base.update(kw)
    return base


def log(**kw):
    base = {"id": "l1", "org_id": HOUSE, "uploaded_at": "2026-07-20T10:00:00+00:00",
            "file_type": "sales", "period": "July 2026", "rows_saved": 100, "filename": "f.xlsx"}
    base.update(kw)
    return base


def call(org_id=HOUSE, **kw):
    return R.upload_last_by_report(org_id=org_id, **kw)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 1. handler contract: read-only, org_id is a QUERY PARAM ──────────────────────────")
import inspect
sig = inspect.signature(R.upload_last_by_report)
check("org_id is a parameter defaulting to ORG_ID (query param, not a constant/body)",
      "org_id" in sig.parameters and sig.parameters["org_id"].default == R.ORG_ID)
check("no request body / Form parameter exists on the handler",
      not any(p.name in ("body", "request", "file") for p in sig.parameters.values()))
_routes = [r for r in R.router.routes if getattr(r, "path", "") == "/commcalc/upload/last"]
check("registered exactly once, read-only (GET)",
      len(_routes) == 1 and set(getattr(_routes[0], "methods", [])) == {"GET"})
_src = inspect.getsource(R.upload_last_by_report)
check("handler issues no write verb (insert/update/upsert/delete)",
      not any(v in _src for v in (".insert(", ".update(", ".upsert(", ".delete(")))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 2. multi-tenant: every read org-scoped, no cross-tenant bleed ─────────────────────")
store = {
    "commcalc.upload_trace": [
        trace(id="h1", org_id=HOUSE, upload_type="sales", created_at="2026-07-20T10:00:00+00:00", rows_saved=4000),
        trace(id="a1", org_id=TEN, upload_type="sales", created_at="2026-07-28T10:00:00+00:00", rows_saved=9999),
        trace(id="a2", org_id=TEN, upload_type="daily_sales", created_at="2026-07-28T11:00:00+00:00", rows_saved=77),
    ],
    "commcalc.upload_log": [
        log(id="hl1", org_id=HOUSE, file_type="sales", uploaded_at="2026-07-19T10:00:00+00:00", rows_saved=3999),
        log(id="al1", org_id=TEN, file_type="mi_report", uploaded_at="2026-07-27T10:00:00+00:00", rows_saved=12),
    ],
}
install(store)
house = call(HOUSE, types="sales,daily_sales,mi_report")
check("every executed query carries .eq('org_id', <caller>)",
      len(QUERY_LOG) > 0 and all(q["eq"].get("org_id") == HOUSE for q in QUERY_LOG))
check("house sees only its own sales ingest (4,000 rows, not the tenant's 9,999)",
      house["reports"]["sales"]["rows_saved"] == 4000)
check("house does NOT see the tenant-only daily_sales ingest",
      house["reports"]["daily_sales"]["last_at"] is None)
check("house does NOT see the tenant-only mi_report upload_log row",
      house["reports"]["mi_report"]["last_at"] is None)
install(store)
ten = call(TEN, types="sales,daily_sales,mi_report")
check("tenant sees only its own sales ingest (9,999 rows)",
      ten["reports"]["sales"]["rows_saved"] == 9999)
check("tenant sees its own daily_sales + mi_report",
      ten["reports"]["daily_sales"]["rows_saved"] == 77 and ten["reports"]["mi_report"]["rows_saved"] == 12)
check("echoed org_id is the caller's", ten["org_id"] == TEN and house["org_id"] == HOUSE)
check("every tenant query carries the TENANT org_id",
      all(q["eq"].get("org_id") == TEN for q in QUERY_LOG))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 3. honesty: a newer REFUSED attempt never becomes 'last upload' ───────────────────")
store = {
    "commcalc.upload_trace": [
        trace(id="g1", upload_type="daily_sales", created_at="2026-07-14T09:00:00+00:00",
              rows_saved=4533, status="ok", source="email_sweep",
              periods={"July 2026": 4533},
              date_counts={"2026-07-01": 300, "2026-07-13": 233, "2026-07-14": 4000}),
        trace(id="g2", upload_type="daily_sales", created_at="2026-07-14T15:00:00+00:00",
              rows_saved=0, status="skipped", skipped="price_guard", source="email_sweep"),
    ],
    "commcalc.upload_log": [],
}
install(store)
r = call(HOUSE, types="daily_sales")["reports"]["daily_sales"]
check("last_at is the ingest that LANDED rows (09:00), not the 15:00 refusal",
      r["last_at"].startswith("2026-07-14T09:00") and r["rows_saved"] == 4533)
check("the refusal is surfaced separately as latest_attempt",
      (r["latest_attempt"] or {}).get("skipped") == "price_guard"
      and r["latest_attempt"]["rows_saved"] == 0
      and r["latest_attempt"]["at"].startswith("2026-07-14T15:00"))
check("source_label humanizes the sweep source", r["source_label"] == "email feed")
check("per-day counts become a span + day count",
      r["span"] == ["2026-07-01", "2026-07-14"] and r["days"] == 3)
check("a single period label is reported", r["period"] == "July 2026")

store["commcalc.upload_trace"].append(
    trace(id="g3", upload_type="daily_sales", created_at="2026-07-14T18:00:00+00:00", rows_saved=51,
          status="ok", source="email_sweep", periods={"July 2026": 51}))
install(store)
r = call(HOUSE, types="daily_sales")["reports"]["daily_sales"]
check("a NEWER successful ingest wins and clears latest_attempt",
      r["last_at"].startswith("2026-07-14T18:00") and r["rows_saved"] == 51 and r["latest_attempt"] is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 4. both journals folded; newer wins across tables + timestamp spellings ───────────")
store = {
    "commcalc.upload_trace": [trace(id="t", upload_type="x_report", created_at="2026-07-25T12:00:00Z", rows_saved=9)],
    "commcalc.upload_log": [log(id="l", file_type="x_report", uploaded_at="2026-07-26T12:00:00+00:00",
                                rows_saved=14, period="2026-07-26")],
}
install(store)
r = call(HOUSE, types="x_report")["reports"]["x_report"]
check("the newer upload_log row wins over the older trace row ('Z' vs '+00:00')",
      r["rows_saved"] == 14 and r["origin"] == "upload_log")
check("upload_log's period text is reported when the trace has no periods JSON", r["period"] == "2026-07-26")
check("upload_log rows report NO source rather than guessing 'manual'", r["source"] is None)
store["commcalc.upload_log"][0]["uploaded_at"] = "2026-07-24T12:00:00+00:00"
install(store)
r = call(HOUSE, types="x_report")["reports"]["x_report"]
check("flip the dates and the trace row wins", r["rows_saved"] == 9 and r["origin"] == "upload_trace")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 5. a report older than the recent window is still found (targeted lookup) ─────────")
noise = [trace(id=f"n{i}", upload_type="daily_sales", created_at=f"2026-07-28T{i:02d}:00:00+00:00",
               rows_saved=10) for i in range(24)]
old_monthly = trace(id="old", upload_type="comp_report", created_at="2026-06-02T08:00:00+00:00",
                    rows_saved=812, periods={"May 2026": 812})
store = {"commcalc.upload_trace": noise + [old_monthly], "commcalc.upload_log": []}
install(store)
narrow = call(HOUSE, types="comp_report", limit=5)
check("the monthly report outside the 5-row window is still found by the targeted lookup",
      narrow["reports"]["comp_report"]["rows_saved"] == 812)
check("the targeted lookup is per-key and org-scoped",
      any(q["eq"].get("upload_type") == "comp_report" and q["eq"].get("org_id") == HOUSE for q in QUERY_LOG))
install(store)
unasked = call(HOUSE, limit=5)
check("without `types` no targeted lookup runs (the window alone answers)",
      "comp_report" not in unasked["reports"]
      and not any(q["eq"].get("upload_type") for q in QUERY_LOG))

# newest row outside the window saved NOTHING → the second .gt('rows_saved',0) pass finds the landed one
store["commcalc.upload_trace"].append(
    trace(id="old2", upload_type="comp_report", created_at="2026-06-03T08:00:00+00:00",
          rows_saved=0, status="error", skipped=None))
install(store)
r = call(HOUSE, types="comp_report", limit=5)["reports"]["comp_report"]
check("targeted lookup falls back to the newest row that DID land rows",
      r["rows_saved"] == 812 and r["last_at"].startswith("2026-06-02"))
check("and still reports the newer empty attempt", (r["latest_attempt"] or {}).get("rows_saved") == 0)
check("the fallback query filters .gt('rows_saved', 0)",
      any(q["gt"].get("rows_saved") == 0 for q in QUERY_LOG))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 6. never-uploaded reports are explicit, not silently missing ──────────────────────")
install({"commcalc.upload_trace": [], "commcalc.upload_log": []})
res = call(HOUSE, types="sales,catalog")
check("both asked-for keys are present with last_at = null",
      set(res["reports"]) == {"sales", "catalog"}
      and all(v["last_at"] is None and v["rows_saved"] is None for v in res["reports"].values()))
check("ok stays true on an empty org (never a 500)", res["ok"] is True and res["hint"] is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 7. graceful degradation when a journal table is missing ───────────────────────────")
install({"commcalc.upload_log": [log(file_type="sales", rows_saved=42)]})   # no upload_trace (mig 202 unrun)
res = call(HOUSE, types="sales")
check("mig-202-less org still answers from upload_log", res["reports"]["sales"]["rows_saved"] == 42)
check("the missing trace table is named in the hint, not raised",
      res["ok"] is True and "migration 202" in (res["hint"] or ""))
check("sources lists only the journal that answered", res["sources"] == ["upload_log"])

install({"commcalc.upload_trace": [trace(upload_type="sales", rows_saved=7)]})   # no upload_log
res = call(HOUSE, types="sales")
check("upload_log-less org still answers from upload_trace", res["reports"]["sales"]["rows_saved"] == 7)
check("the missing log table is named in the hint", "007_upload_log.sql" in (res["hint"] or ""))

install({})   # neither table exists
res = call(HOUSE, types="sales")
check("neither table → ok, an explicit null record, and both hints (never a 500)",
      res["ok"] is True and res["reports"]["sales"]["last_at"] is None
      and "migration 202" in res["hint"] and "007_upload_log.sql" in res["hint"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 8. multi-period file + MA report keys ─────────────────────────────────────────────")
store = {"commcalc.upload_trace": [
    trace(id="m1", upload_type="ma_commission", created_at="2026-07-27T10:00:00+00:00", rows_saved=3100,
          source="manual", periods={"May 2026": 1000, "June 2026": 2100},
          date_counts={"2026-05-02": 1000, "2026-06-11": 2100}),
], "commcalc.upload_log": []}
install(store)
r = call(HOUSE, types="ma_commission")["reports"]["ma_commission"]
check("a multi-month historical load reports every period, not a single label",
      r["period"] is None and r["periods"] == {"May 2026": 1000, "June 2026": 2100})
check("its day span is still reported", r["span"] == ["2026-05-02", "2026-06-11"] and r["days"] == 2)
check("manual source is labelled", r["source_label"] == "manual upload")

print("\n══════════════════════════════════════════════════════════════════════════════════════")
print(f"  {_pass} passed, {_fail} failed")
print("══════════════════════════════════════════════════════════════════════════════════════")
sys.exit(1 if _fail else 0)
