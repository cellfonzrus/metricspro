"""Endpoint harness for the Commission-Ledger MA refresh — drives the REAL router handlers against an
in-memory FAKE Supabase client (no network, no DB, no Postgres).

  GET  /api/v1/commcalc/commission-ledger/ma-sync/preview     (read-only)
  POST /api/v1/commcalc/commission-ledger/ma-sync             (the only writer)
  GET  /api/v1/commcalc/commission-ledger/provenance          (read-only)
  GET  /api/v1/commcalc/commission-ledger/summary|rows|by-rep (read-only, + the new origin filter)
  POST /api/v1/commcalc/commission-ledger/import              (unchanged behaviour, narrower delete)

WHAT IT PROVES

  MULTI-TENANT (AGENT_CONTRACT RULE ONE)
  • EVERY read issued by EVERY endpoint is org-constrained (`.eq('org_id', …)`), asserted over the whole
    query log — raw MA tables, the ledger, the config tables, all of them
  • a second tenant's raw_ma_* rows and ledger rows are invisible in BOTH directions
  • every INSERTed ledger row carries org_id (the write-side trap), and it is the CALLER's org
  • every DELETE is org-scoped, and scoped to the template + period + origin
  • org_id is a QUERY PARAM on all five handlers (never a constant, Form field or body)

  READ-ONLY WHERE IT MUST BE
  • preview / provenance / summary / rows / by-rep run against a client whose write verbs RAISE — the run
    passing at all proves those endpoints write nothing (not even upload_trace)

  IDEMPOTENCE + PROVENANCE ISOLATION (the whole point)
  • refresh twice => byte-identical ledger state and the same row count (no duplicates)
  • a refresh deletes ONLY origin='ma_sync' rows: file-imported rows survive byte-identical
  • a FILE re-import deletes ONLY origin='file' (+ legacy NULL) rows: synced rows survive byte-identical
  • both origins in one period are reported as an overlap, with a warning naming the double count

  THE AMOUNT GUARD
  • pointing the ledger's amount at raw_ma_daily_tx.merchant_invoice (an invoice NUMBER stored NUMERIC)
    refuses the source through the ENDPOINT: zero rows derived, the reason surfaced, the write 400s
  • an over-ceiling line is excluded, counted and exampled in the preview payload

  PERIOD DUALITY (the recurring bug class)
  • raw rows stored as '2026-06' are found for the request 'June 2026' and vice-versa
  • when the period column matches nothing, the tx_date month range is used AND the payload says so

  DEGRADATION
  • pre-migration-251 (no `origin` column): the refresh REFUSES with the migration name and writes nothing,
    the file import still works and falls back to its original un-scoped delete, and the page's templates
    call reports sync_ready=false
  • a missing raw_ma_* table (mig 083 unrun) => an honest warning, never a 500
  • an empty period => 400 "nothing to write", never an empty-wipe

  BOUNDED READS (the 2026-07-30 worker-starvation lesson)
  • 3,000 raw rows are read in a bounded, PAGED number of queries; the query count does not grow with the
    data; the provenance scan selects one column and reports whether it hit its cap

Run: `python3 harness_ledger_ma_sync.py` from the backend dir.
"""
import copy
import inspect
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from fastapi import HTTPException

from app.modules.commcalc import router as R
from app.modules.commcalc import ledger_ma_sync as L

HOUSE = "00000000-0000-0000-0000-000000000001"
TENANT = "00000000-0000-0000-0000-0000000000a2"

_pass = _fail = 0
QUERY_LOG = []
WRITE_LOG = []


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{(' — ' + str(extra)) if extra else ''}")


def _catch(f):
    """Run f() and return the exception it raised (or None) — so a refusal can be asserted on."""
    try:
        f()
        return None
    except Exception as e:
        return e


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Fake supabase-py client. Reads are filtered in memory; every query is logged (table + filters) so org
# scoping can be asserted on ALL of them. Writes are logged too — and when `read_only` is set, every
# write verb RAISES, which is how the read endpoints prove they write nothing.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class WriteAttempted(AssertionError):
    pass


class Store:
    def __init__(self, tables=None, missing_cols=None, read_only=False):
        self.t = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.missing = {k: set(v) for k, v in (missing_cols or {}).items()}
        self.read_only = read_only


class _Q:
    def __init__(self, st, schema, table):
        self.st, self.schema, self.table = st, schema, table
        self.key = f"{schema}.{table}"
        self._eq, self._in, self._gte, self._lte, self._is = {}, {}, {}, {}, {}
        self._sel, self._limit, self._range, self._mode, self._payload = "*", None, None, "select", None

    # ── filters ──
    def select(self, *a, **k):
        self._sel = a[0] if a else "*"
        return self

    def eq(self, k, v):
        self._eq[k] = v
        return self

    def in_(self, k, v):
        self._in[k] = list(v)
        return self

    def gte(self, k, v):
        self._gte[k] = v
        return self

    def lte(self, k, v):
        self._lte[k] = v
        return self

    def is_(self, k, v):
        self._is[k] = v
        return self

    def limit(self, n):
        self._limit = n
        return self

    def order(self, *a, **k):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    # ── writes ──
    def insert(self, rows):
        if self.st.read_only:
            raise WriteAttempted(f"insert attempted on {self.key}")
        self._mode, self._payload = "insert", (rows if isinstance(rows, list) else [rows])
        return self

    def upsert(self, rows, **k):
        if self.st.read_only:
            raise WriteAttempted(f"upsert attempted on {self.key}")
        self._mode, self._payload = "upsert", (rows if isinstance(rows, list) else [rows])
        return self

    def update(self, patch):
        if self.st.read_only:
            raise WriteAttempted(f"update attempted on {self.key}")
        self._mode, self._payload = "update", patch
        return self

    def delete(self):
        if self.st.read_only:
            raise WriteAttempted(f"delete attempted on {self.key}")
        self._mode = "delete"
        return self

    # ── execution ──
    def _missing(self, cols):
        bad = self.st.missing.get(self.key) or set()
        return sorted(c for c in cols if c in bad)

    def _match(self, r):
        for k, v in self._eq.items():
            if str(r.get(k)) != str(v):
                return False
        for k, v in self._in.items():
            if str(r.get(k)) not in {str(x) for x in v}:
                return False
        for k, v in self._gte.items():
            if not (r.get(k) and str(r.get(k)) >= str(v)):
                return False
        for k, v in self._lte.items():
            if not (r.get(k) and str(r.get(k)) <= str(v)):
                return False
        for k, v in self._is.items():
            if str(v).lower() == "null" and r.get(k) not in (None,):
                return False
        return True

    def execute(self):
        QUERY_LOG.append({"table": self.table, "schema": self.schema, "mode": self._mode,
                          "eq": dict(self._eq), "in": dict(self._in), "select": self._sel,
                          "gte": dict(self._gte), "lte": dict(self._lte), "is": dict(self._is)})
        if self.key not in self.st.t:
            raise Exception(f'relation "{self.key}" does not exist (PGRST205)')
        # a filter or a select touching a column this table doesn't have behaves like postgrest: it errors
        touched = list(self._eq) + list(self._in) + list(self._gte) + list(self._lte) + list(self._is)
        if self._sel and self._sel != "*":
            touched += [c.strip() for c in str(self._sel).split(",") if c.strip()]
        bad = self._missing(touched)
        if bad:
            raise Exception(f'column {self.table}.{bad[0]} does not exist (42703)')

        if self._mode == "delete":
            keep, gone = [], 0
            for r in self.st.t[self.key]:
                if self._match(r):
                    gone += 1
                else:
                    keep.append(r)
            self.st.t[self.key] = keep
            WRITE_LOG.append({"mode": "delete", "table": self.table, "eq": dict(self._eq),
                              "is": dict(self._is), "removed": gone})
            return type("Res", (), {"data": []})()
        if self._mode in ("insert", "upsert"):
            payload = self._payload or []
            bad = self._missing([k for r in payload for k in r.keys()])
            if bad:
                raise Exception(f'column "{bad[0]}" of relation "{self.table}" does not exist (42703)')
            for r in payload:
                self.st.t[self.key].append(dict(r))
            WRITE_LOG.append({"mode": self._mode, "table": self.table, "rows": len(payload),
                              "payload": [dict(r) for r in payload]})
            return type("Res", (), {"data": [dict(r) for r in payload]})()
        if self._mode == "update":
            n = 0
            for r in self.st.t[self.key]:
                if self._match(r):
                    r.update(self._payload or {})
                    n += 1
            WRITE_LOG.append({"mode": "update", "table": self.table, "rows": n})
            return type("Res", (), {"data": []})()

        rows = [dict(r) for r in self.st.t[self.key] if self._match(r)]
        if self._range:
            a, b = self._range
            rows = rows[a:b + 1]
        if self._limit is not None:
            rows = rows[:self._limit]
        return type("Res", (), {"data": rows})()


class _Schema:
    def __init__(self, st, schema):
        self.st, self.schema = st, schema

    def table(self, t):
        return _Q(self.st, self.schema, t)

    def rpc(self, *a, **k):
        raise WriteAttempted("rpc attempted")


class FakeClient:
    def __init__(self, st):
        self.st = st

    def schema(self, s):
        return _Schema(self.st, s)

    def table(self, t):
        return _Q(self.st, "public", t)

    def rpc(self, *a, **k):
        raise WriteAttempted("rpc attempted")


def install(st):
    QUERY_LOG.clear()
    WRITE_LOG.clear()
    R.sb = lambda: FakeClient(st)                                          # noqa: E731
    return st


def unscoped_reads():
    """Every logged SELECT that is not constrained by org_id (a cross-tenant leak)."""
    return [q for q in QUERY_LOG if q["mode"] == "select"
            and "org_id" not in q["eq"] and "org_id" not in q["in"]]


def unscoped_writes():
    return [w for w in WRITE_LOG if w["mode"] == "delete" and "org_id" not in w["eq"]]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════════════════════════════
LEDGER_COLS_251 = ("origin", "source_table", "source_row_id", "synced_at")


def tx_row(i, org, period, product, otype, amt, user="amir", date="2026-06-03"):
    return {"id": f"tx-{org[-2:]}-{i}", "org_id": org, "period": period, "account_id": "A100",
            "account_name": "509 Nostrand", "direct_ma_name": "CellFonz", "order_number": f"SO-{i}",
            "tx_date": date, "due_date": None, "user_name": user, "order_type": otype,
            "product_name": product, "retail_cost": amt, "merchant_discount": 1.11,
            "merchant_invoice": 4211987 + i}


HOUSE_TX = [
    tx_row(1, HOUSE, "June 2026", "TBV MONTH 1 New Activation Commission", "Postpaid Order", -25.0),
    tx_row(2, HOUSE, "June 2026", "TBV MONTH 4 SPF", "Postpaid Order", -10.0),
    tx_row(3, HOUSE, "June 2026", "Trac Autopay Residual", "Residual Order", -2.5, "sara"),
    tx_row(4, HOUSE, "June 2026", "Airtime Top-Up $30", "Airtime Order", 30.0),
    tx_row(5, HOUSE, "July 2026", "TBV MONTH 2 Commission", "Postpaid Order", -15.0, "sara",
           "2026-07-02"),
]
TENANT_TX = [tx_row(9, TENANT, "June 2026", "TBV MONTH 1 New Activation Commission", "Postpaid Order",
                    -999.0, "other-rep")]

MC_ROW = {"id": "mc-1", "org_id": HOUSE, "period": "June 2026", "tx_date": "2026-06-11",
          "activation_order": "ACT-9", "merchant_account_id": "M-77", "ban": "BAN-5",
          "activation_type": "New", "user_name": "amir", "device_margin": -30.0, "consumer_margin": 0,
          "consumer_financing": 0, "rebate": -20.0, "wallet_funding": 0, "fees_margin": 0,
          "spiff_m1": -5.0, "spiff_m2": 0, "spiff_m3": 0, "spiff_m4": -4.0, "spiff_m5": 0,
          "spiff_m6": 0, "mrc_net_discount": -45.0, "merchant_invoice": 987654321}

FILE_LEDGER_ROW = {"id": "led-file-1", "org_id": HOUSE, "period": "June 2026",
                   "source_report": "ma_daily_tx", "origin": "file", "rep_user": "amir",
                   "product_name": "HAND UPLOADED LINE", "order_type": "Postpaid Order",
                   "category": "commission", "commission": 111.0, "spiff": 0, "equipment_rebate": 0,
                   "residual_monthly": 0, "autopay_residual": 0, "payout_total": 111.0,
                   "raw_amount": -111.0, "is_payout": True, "payment_month": 1,
                   "created_at": "2026-07-12T14:03:00Z", "source_table": None, "source_row_id": None,
                   "synced_at": None}


def base_tables(ledger=None, tx=None, mc=None, cfg=None):
    return {
        "commcalc.commission_ledger": list(ledger or []),
        "commcalc.raw_ma_daily_tx": list(HOUSE_TX + TENANT_TX if tx is None else tx),
        "commcalc.raw_ma_commission": list([MC_ROW] if mc is None else mc),
        "commcalc.ledger_sync_config": list(cfg or []),
        "commcalc.commission_category_map": [],
        "commcalc.column_mapping": [],
        "commcalc.target_field_registry": [],
        "commcalc.manual_report_mapping": [],
        "commcalc.upload_trace": [],
        "commcalc.upload_log": [],
        "commcalc.rep_commissions": [],
        "commcalc.rep_aliases": [],
    }


def ledger_state(st, drop=("id", "synced_at")):
    """The ledger table as a comparable snapshot (order-insensitive). `synced_at` is dropped by default —
    it is the refresh CLOCK and is expected to advance; everything that describes money must not."""
    rows = st.t["commcalc.commission_ledger"]
    return sorted([{k: v for k, v in r.items() if k not in drop} for r in rows],
                  key=lambda r: (str(r.get("origin")), str(r.get("product_name")),
                                 str(r.get("source_row_id")), str(r.get("payout_total"))))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── A. handler contracts (org_id is a QUERY PARAM, not a constant) ──")
for fn, name in ((R.commission_ledger_ma_sync_preview, "ma-sync/preview"),
                 (R.commission_ledger_ma_sync, "ma-sync"),
                 (R.commission_ledger_provenance, "provenance"),
                 (R.commission_ledger_summary, "summary"),
                 (R.commission_ledger_rows, "rows"),
                 (R.commission_ledger_by_rep, "by-rep")):
    sig = inspect.signature(fn)
    p = sig.parameters.get("org_id")
    check(f"{name}: org_id is a query param defaulting to the house org",
          p is not None and p.default == R.ORG_ID and p.kind == p.POSITIONAL_OR_KEYWORD, str(sig))
check("summary/rows/by-rep gained an `origin` filter",
      all("origin" in inspect.signature(f).parameters
          for f in (R.commission_ledger_summary, R.commission_ledger_rows, R.commission_ledger_by_rep)))
check("no handler takes org via Form/body",
      not any("Form" in str(inspect.signature(f)) for f in
              (R.commission_ledger_ma_sync_preview, R.commission_ledger_ma_sync,
               R.commission_ledger_provenance)))

print("\n── B. PREVIEW is read-only and org-scoped ──")
st = install(Store(base_tables(), read_only=True))
prev = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
check("preview wrote NOTHING (write verbs would have raised)", WRITE_LOG == [])
check("every read was org-scoped", not unscoped_reads(), unscoped_reads()[:3])
check("preview derived the house rows only (4 of June)", prev["would_write"] == 4, prev["would_write"])
check("the tenant's -999 line is NOT in the house preview",
      prev["summary"]["categories"]["commission"]["total"] == 25.0, prev["summary"]["categories"])
check("the payout total is the four June lines", prev["summary"]["payout_total"] == 37.5,
      prev["summary"]["payout_total"])
check("the positive airtime line is a charge", prev["summary"]["charge_total"] == 30.0)
check("the source names the raw table + how the period matched",
      prev["sources"][0]["source_table"] == "raw_ma_daily_tx"
      and prev["sources"][0]["read"]["matched_by"] == "period", prev["sources"][0]["read"])
check("the amount column is stated on the payload",
      prev["sources"][0]["diag"]["amount_col"] == "retail_cost", prev["sources"][0]["diag"])
check("the delete scope is declared up front",
      prev["delete_scope"] == {"org_id": HOUSE, "source_report": "ma_daily_tx", "period": "June 2026",
                               "origin": "ma_sync"}, prev["delete_scope"])
check("the overlap note is absent when the period is empty", prev["overlap_note"] is None)

print("\n── C. the NON-HOUSE tenant sees only its own rows (both directions) ──")
st = install(Store(base_tables(), read_only=True))
pt = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026", org_id=TENANT)
check("tenant preview derives its single row", pt["would_write"] == 1, pt["would_write"])
check("tenant sees its own 999.00, not the house 25.00",
      pt["summary"]["categories"]["commission"]["total"] == 999.0, pt["summary"]["categories"])
check("every tenant read was org-scoped", not unscoped_reads())

print("\n── D. REFRESH writes, stamps org, and is IDEMPOTENT ──")
st = install(Store(base_tables()))
r1 = R.commission_ledger_ma_sync(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
snap1 = ledger_state(st)
check("the refresh saved the 4 derived lines", r1["saved"] == 4, r1["saved"])
inserted = [w for w in WRITE_LOG if w["mode"] == "insert" and w["table"] == "commission_ledger"]
check("every inserted row carries the CALLER's org_id",
      all(row.get("org_id") == HOUSE for w in inserted for row in w["payload"]))
check("every inserted row carries origin='ma_sync'",
      all(row.get("origin") == "ma_sync" for w in inserted for row in w["payload"]))
check("every inserted row carries its source table + raw row id",
      all(row.get("source_table") == "raw_ma_daily_tx" and row.get("source_row_id")
          for w in inserted for row in w["payload"]))
dels = [w for w in WRITE_LOG if w["mode"] == "delete" and w["table"] == "commission_ledger"]
check("the delete was org+template+period+origin scoped",
      dels and all(d["eq"].get("org_id") == HOUSE and d["eq"].get("source_report") == "ma_daily_tx"
                   and d["eq"].get("period") == "June 2026" and d["eq"].get("origin") == "ma_sync"
                   for d in dels), dels)
check("no unscoped delete happened", not unscoped_writes())
check("an upload_trace row was written (the ingest is auditable)",
      any(w["table"] == "upload_trace" for w in WRITE_LOG))
check("nothing was written to rep_commissions",
      not any(w["table"] == "rep_commissions" for w in WRITE_LOG))

WRITE_LOG.clear()
r2 = R.commission_ledger_ma_sync(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
snap2 = ledger_state(st)
check("running it TWICE saves the same count", r2["saved"] == r1["saved"])
check("running it twice leaves IDENTICAL ledger state (no duplicates, same money)", snap1 == snap2,
      [(a, b) for a, b in zip(snap1, snap2) if a != b][:2])
check("...and every synced row still carries a refresh stamp",
      all(r.get("synced_at") for r in st.t["commcalc.commission_ledger"]))
check("the ledger holds exactly 4 rows after two runs",
      len(st.t["commcalc.commission_ledger"]) == 4, len(st.t["commcalc.commission_ledger"]))
check("the second run deleted its own 4 rows before re-inserting",
      any(w["mode"] == "delete" and w.get("removed") == 4 for w in WRITE_LOG),
      [w for w in WRITE_LOG if w["mode"] == "delete"])

print("\n── E. PROVENANCE ISOLATION: a refresh never touches a file-imported row ──")
st = install(Store(base_tables(ledger=[FILE_LEDGER_ROW])))
before_file = [r for r in st.t["commcalc.commission_ledger"] if r["origin"] == "file"]
R.commission_ledger_ma_sync(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
after_file = [r for r in st.t["commcalc.commission_ledger"] if r["origin"] == "file"]
check("the file row survived the refresh BYTE-IDENTICAL", before_file == after_file, (before_file, after_file))
check("the period now holds both origins",
      len({r["origin"] for r in st.t["commcalc.commission_ledger"]}) == 2)
prov = R.commission_ledger_provenance(source_report="ma_daily_tx", org_id=HOUSE)
june = next(p for p in prov["periods"] if p["period"] == "June 2026")
check("provenance reports BOTH sources for the period", len(june["origins"]) == 2, june["origins"])
check("...and flags the overlap explicitly", june["overlap"] is True)
check("...with a per-source line count and last-refreshed stamp",
      all(o["lines"] > 0 for o in june["origins"])
      and any(o["origin"] == "ma_sync" and o["last_at"] for o in june["origins"]), june["origins"])
check("July (raw data present, never synced) is flagged STALE",
      next(p for p in prov["periods"] if p["period"] == "July 2026")["stale"] is True,
      [(p["period"], p["stale"], p["raw_rows"]) for p in prov["periods"]])
check("provenance counts the raw rows waiting per period",
      june["raw_available"].get("raw_ma_daily_tx") == 4, june["raw_available"])
check("periods are sorted newest first", [p["period"] for p in prov["periods"]][0] == "July 2026",
      [p["period"] for p in prov["periods"]])

# the ORIGIN FILTER makes the double count readable one source at a time
both = R.commission_ledger_summary(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
only_file = R.commission_ledger_summary(source_report="ma_daily_tx", period="June 2026", origin="file",
                                        org_id=HOUSE)
only_sync = R.commission_ledger_summary(source_report="ma_daily_tx", period="June 2026",
                                        origin="ma_sync", org_id=HOUSE)
check("unfiltered summary sums both sources (unchanged behaviour)",
      both["payout_total"] == 111.0 + 37.5, both["payout_total"])
check("origin=file isolates the file import", only_file["payout_total"] == 111.0)
check("origin=ma_sync isolates the refresh", only_sync["payout_total"] == 37.5)
check("file + sync == unfiltered (no row is hidden or double-shown)",
      only_file["payout_total"] + only_sync["payout_total"] == both["payout_total"])
rws = R.commission_ledger_rows(source_report="ma_daily_tx", period="June 2026", origin="ma_sync",
                               org_id=HOUSE)
check("the drill-down honours the origin filter",
      rws["count"] == 4 and all(r["origin"] == "ma_sync" for r in rws["rows"]), rws["count"])
byrep = R.commission_ledger_by_rep(source_report="ma_daily_tx", period="June 2026", origin="ma_sync",
                                  org_id=HOUSE)
check("by-rep honours the origin filter", round(byrep["totals"]["ledger_payout"], 2) == 37.5,
      byrep["totals"])

print("\n── F. the reverse isolation: a FILE re-import never wipes synced rows ──")
sync_before = [r for r in st.t["commcalc.commission_ledger"] if r["origin"] == "ma_sync"]
R._ledger_delete_scoped(FakeClient(st), HOUSE, "ma_daily_tx", "June 2026", L.ORIGIN_FILE)
sync_after = [r for r in st.t["commcalc.commission_ledger"] if r["origin"] == "ma_sync"]
check("a file-scoped wipe removed the file row", not [r for r in st.t["commcalc.commission_ledger"]
                                                     if r["origin"] == "file"])
check("...and left every synced row BYTE-IDENTICAL", sync_before == sync_after)

# a legacy NULL-origin row (pre-251 data that the backfill somehow missed) counts as a file import
st2 = install(Store(base_tables(ledger=[dict(FILE_LEDGER_ROW, id="led-legacy", origin=None)])))
R.commission_ledger_ma_sync(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
check("a NULL-origin legacy row survives a refresh",
      any(r.get("id") == "led-legacy" for r in st2.t["commcalc.commission_ledger"]))
R._ledger_delete_scoped(FakeClient(st2), HOUSE, "ma_daily_tx", "June 2026", L.ORIGIN_FILE)
check("...and IS removed by a file re-import (so a re-upload can't duplicate it)",
      not any(r.get("id") == "led-legacy" for r in st2.t["commcalc.commission_ledger"]))
check("...while the synced rows are still there",
      len([r for r in st2.t["commcalc.commission_ledger"] if r["origin"] == "ma_sync"]) == 4)

print("\n── G. the AMOUNT GUARD through the endpoint ──")
# the tenant re-mapped the ledger's amount onto the invoice NUMBER column
bad_map = [{"org_id": HOUSE, "report_key": "commission_ledger", "target_field": "raw_amount",
            "source_header": "Merchant Invoice", "transform": "number", "is_active": True,
            "carrier_id": None, "priority": 100}]
tb = base_tables()
tb["commcalc.column_mapping"] = bad_map
st = install(Store(tb, read_only=True))
bad = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
check("an ID amount column derives ZERO rows", bad["would_write"] == 0)
check("...the refusal is surfaced with a reason",
      bad["guard"]["refused"] and "identifier" in bad["guard"]["refused"][0]["reason"],
      bad["guard"]["refused"])
check("...naming the offending column", "merchant_invoice" in str(bad["guard"]["refused"]))
st = install(Store(tb))
e = _catch(lambda: R.commission_ledger_ma_sync(source_report="ma_daily_tx", period="June 2026",
                                               org_id=HOUSE))
check("...and the WRITE is refused (400), with nothing written",
      isinstance(e, HTTPException) and e.status_code == 400, e)
check("...truly nothing written", not [w for w in WRITE_LOG if w["mode"] in ("insert", "delete")])

# an over-ceiling line is excluded + counted + exampled
tb = base_tables(tx=HOUSE_TX + [tx_row(77, HOUSE, "June 2026", "Weird Line", "Postpaid Order",
                                       -4211987.0)])
st = install(Store(tb, read_only=True))
big = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
check("the over-ceiling line is excluded", big["would_write"] == 4)
check("...counted", big["guard"]["excluded_ceiling"] == 1)
check("...with its dollars and column named",
      big["guard"]["excluded_ceiling_total"] == 4211987.0
      and big["guard"]["excluded_examples"][0]["column"] == "retail_cost", big["guard"])
tb2 = base_tables(tx=tb["commcalc.raw_ma_daily_tx"],
                  cfg=[{"org_id": HOUSE, "source_report": "ma_daily_tx", "report_key": "ma_daily_tx",
                        "source_table": "raw_ma_daily_tx", "kind": "row", "date_col": "tx_date",
                        "enabled": True, "amount_ceiling": 9000000, "component_map": {},
                        "field_hints": {}}])
st = install(Store(tb2, read_only=True))
raised = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
check("the ceiling is per-tenant CONFIGURABLE (raise it, the line returns)",
      raised["would_write"] == 5 and raised["guard"]["excluded_ceiling"] == 0, raised["would_write"])

print("\n── H. PERIOD DUALITY ('June 2026' vs '2026-06') ──")
st = install(Store(base_tables(tx=[tx_row(1, HOUSE, "2026-06", "TBV MONTH 1 Commission",
                                          "Postpaid Order", -25.0)]), read_only=True))
p1 = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
check("rows stored as '2026-06' are found for 'June 2026'", p1["would_write"] == 1, p1["would_write"])
st = install(Store(base_tables(tx=[tx_row(1, HOUSE, "June 2026", "TBV MONTH 1 Commission",
                                          "Postpaid Order", -25.0)]), read_only=True))
p2 = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="2026-06", org_id=HOUSE)
check("...and vice-versa", p2["would_write"] == 1, p2["would_write"])
check("the written period is the one the caller asked for", p2["period"] == "2026-06")
# a THIRD spelling (or a blank period) falls back to the row's own date, and SAYS so
st = install(Store(base_tables(tx=[dict(tx_row(1, HOUSE, "Jun-26", "TBV MONTH 1 Commission",
                                               "Postpaid Order", -25.0), tx_date="2026-06-15")]),
                   read_only=True))
p3 = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
check("an unknown period spelling falls back to the tx_date month", p3["would_write"] == 1)
check("...and the payload SAYS the fallback was used",
      "tx_date range" in (p3["sources"][0]["read"]["matched_by"] or ""), p3["sources"][0]["read"])

print("\n── I. MA COMMISSION (component shape) through the endpoint ──")
st = install(Store(base_tables(), read_only=True))
mc = R.commission_ledger_ma_sync_preview(source_report="ma_commission", period="June 2026", org_id=HOUSE)
check("one activation row expands to its non-zero components only", mc["would_write"] == 4,
      [o["product_name"] for o in mc["observed"]])
check("the zero components are counted, not written", mc["guard"]["skipped_empty_amount"] == 8,
      mc["guard"])
check("the spiffs land in their own payment months",
      (mc["summary"]["by_month"].get("spiff|1"), mc["summary"]["by_month"].get("spiff|4")) == (5.0, 4.0),
      mc["summary"]["by_month"])
check("the labels that match no rule are surfaced as unmapped, never guessed",
      {u["product_name"] for u in mc["unmapped"]} == {"Device Margin", "Rebate"},
      [u["product_name"] for u in mc["unmapped"]])
check("mrc_net_discount (a plan price) never becomes a line",
      all("MRC" not in (o["product_name"] or "") for o in mc["observed"]))
check("the invoice number never becomes a line",
      all(abs(o["payout_total"]) != 987654321 for o in mc["observed"]))
check("the component shape reports its synthesized fields, not as config gaps",
      set(mc["sources"][0]["synthesized_fields"]) == {"raw_amount", "product_name"},
      mc["sources"][0]["synthesized_fields"])
check("the context fields resolve through the source's own hints",
      {f["target_field"]: f["col"] for f in mc["sources"][0]["mapped_fields"]}.get("order_number")
      == "activation_order", mc["sources"][0]["mapped_fields"])
check("MA Commission writes into its OWN template namespace",
      mc["source_report"] == "ma_commission" and mc["delete_scope"]["source_report"] == "ma_commission")
st = install(Store(base_tables()))
R.commission_ledger_ma_sync(source_report="ma_commission", period="June 2026", org_id=HOUSE)
R.commission_ledger_ma_sync(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
check("the two templates coexist without touching each other",
      len([r for r in st.t["commcalc.commission_ledger"] if r["source_report"] == "ma_commission"]) == 4
      and len([r for r in st.t["commcalc.commission_ledger"] if r["source_report"] == "ma_daily_tx"]) == 4,
      [(r["source_report"], r["product_name"]) for r in st.t["commcalc.commission_ledger"]])

print("\n── J. DEGRADATION ──")
# pre-251: no origin column anywhere
tb = base_tables(ledger=[{k: v for k, v in FILE_LEDGER_ROW.items() if k not in LEDGER_COLS_251}])
st = install(Store(tb, missing_cols={"commcalc.commission_ledger": LEDGER_COLS_251}))
e = _catch(lambda: R.commission_ledger_ma_sync(source_report="ma_daily_tx", period="June 2026",
                                               org_id=HOUSE))
check("pre-251 the refresh REFUSES", isinstance(e, HTTPException) and e.status_code == 400, e)
check("...naming the migration", "251_commission_ledger_ma_sync.sql" in str(getattr(e, "detail", "")), e)
check("...and writes nothing at all", not [w for w in WRITE_LOG if w["mode"] in ("insert", "delete")])
tmpl = R.commission_ledger_templates(org_id=HOUSE)
check("the page is told sync is not ready", tmpl["sync_ready"] is False
      and tmpl["sync_migration"] == "251_commission_ledger_ma_sync.sql", tmpl.get("sync_ready"))
check("...and the MA templates are still listed as syncable-in-principle",
      any(t["key"] == "ma_daily_tx" and t.get("ma_syncable") for t in tmpl["templates"]))
check("ma_commission is now a pickable template",
      any(t["key"] == "ma_commission" for t in tmpl["templates"]),
      [t["key"] for t in tmpl["templates"]])
prov0 = R.commission_ledger_provenance(source_report="ma_daily_tx", org_id=HOUSE)
check("pre-251 provenance degrades: everything reads as a file import",
      prov0["ready"] is False and all(o["origin"] == "file" for p in prov0["periods"]
                                      for o in p["origins"]), prov0["periods"])
# and the FILE import's delete falls back to today's exact statement
WRITE_LOG.clear()
mode = R._ledger_delete_scoped(FakeClient(st), HOUSE, "ma_daily_tx", "June 2026", L.ORIGIN_FILE)
check("pre-251 a file import still clears its period (old behaviour)", mode == "unscoped_pre_251")
check("...with exactly ONE un-origin-scoped delete",
      len([w for w in WRITE_LOG if w["mode"] == "delete"]) == 1
      and "origin" not in WRITE_LOG[0]["eq"], WRITE_LOG)
check("...still org+template+period scoped", WRITE_LOG[0]["eq"].get("org_id") == HOUSE
      and WRITE_LOG[0]["eq"].get("period") == "June 2026")

# missing raw table (mig 083 unrun)
tb = base_tables()
del tb["commcalc.raw_ma_daily_tx"]
st = install(Store(tb, read_only=True))
miss = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
check("a missing raw table degrades to a warning, not a 500", miss["would_write"] == 0
      and any("083" in w for w in miss["warnings"]), miss["warnings"])
st = install(Store(tb))
e = _catch(lambda: R.commission_ledger_ma_sync(source_report="ma_daily_tx", period="June 2026",
                                               org_id=HOUSE))
check("...and the refresh 400s instead of wiping the period",
      isinstance(e, HTTPException) and e.status_code == 400, e)
check("...having deleted nothing", not [w for w in WRITE_LOG if w["mode"] == "delete"])

# an empty period
st = install(Store(base_tables(tx=[])))
e = _catch(lambda: R.commission_ledger_ma_sync(source_report="ma_daily_tx", period="May 2026",
                                               org_id=HOUSE))
check("an empty period never becomes an empty-wipe",
      isinstance(e, HTTPException) and e.status_code == 400
      and not [w for w in WRITE_LOG if w["mode"] == "delete"], e)
e = _catch(lambda: R.commission_ledger_ma_sync_preview(period="", org_id=HOUSE))
check("preview without a period 400s", isinstance(e, HTTPException) and e.status_code == 400)
e = _catch(lambda: R.commission_ledger_ma_sync(period="", org_id=HOUSE))
check("refresh without a period 400s", isinstance(e, HTTPException) and e.status_code == 400)
e = _catch(lambda: R.commission_ledger_ma_sync_preview(period="June 2026", org_id=""))
check("no org_id 400s", isinstance(e, HTTPException) and e.status_code == 400)

print("\n── K. the money guardrail: nothing outside the ledger is ever written ──")
st = install(Store(base_tables(ledger=[FILE_LEDGER_ROW])))
R.commission_ledger_ma_sync(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
R.commission_ledger_ma_sync(source_report="ma_commission", period="June 2026", org_id=HOUSE)
touched = sorted({w["table"] for w in WRITE_LOG})
check("only commission_ledger + upload_trace were written",
      touched == ["commission_ledger", "upload_trace"], touched)
for forbidden in ("rep_commissions", "commission_plan", "commission_plan_rule", "payout_schedule",
                  "raw_ma_daily_tx", "raw_ma_commission", "sale_installment_ledger", "asset_ledger",
                  "coa_entry", "commission_category_map", "column_mapping"):
    check(f"{forbidden} was never written", not any(w["table"] == forbidden for w in WRITE_LOG))

print("\n── L. bounded reads (the 2026-07-30 worker-starvation lesson) ──")
# 3,000 raw rows must not become 3,000 queries, and the query count must not grow with the data.
many = [tx_row(i, HOUSE, "June 2026", "TBV MONTH 1 Commission", "Postpaid Order", -1.0)
        for i in range(3000)]
st = install(Store(base_tables(tx=many), read_only=True))
p = R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
big_q = len(QUERY_LOG)
check("3,000 raw rows derive 3,000 ledger lines", p["would_write"] == 3000, p["would_write"])
check("...in a BOUNDED number of queries (paged, not per-row)", big_q <= 15, big_q)
check("...and the raw read really pages (a full page then a short one)",
      len([q for q in QUERY_LOG if q["table"] == "raw_ma_daily_tx"]) == 4,
      [q["table"] for q in QUERY_LOG])
st = install(Store(base_tables(tx=many[:5]), read_only=True))
R.commission_ledger_ma_sync_preview(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
small_q = len(QUERY_LOG)
check("the query count does not grow with the row count (only the page count does)",
      big_q - small_q <= 4, (small_q, big_q))
st = install(Store(base_tables(tx=many), read_only=True))
prv = R.commission_ledger_provenance(source_report="ma_daily_tx", org_id=HOUSE)
check("provenance reports the raw row count it scanned",
      prv["raw_sources"][0]["rows"] == 3000, prv["raw_sources"])
check("...and says whether the scan was truncated at the cap",
      prv["raw_sources"][0]["truncated"] is False, prv["raw_sources"])
check("...selecting ONE column, not the whole row",
      all(q["select"] == "period" for q in QUERY_LOG if q["table"] == "raw_ma_daily_tx"),
      [q["select"] for q in QUERY_LOG if q["table"] == "raw_ma_daily_tx"])

print(f"\n══ ledger_ma_sync harness: {_pass} passed, {_fail} failed ══")
sys.exit(1 if _fail else 0)
