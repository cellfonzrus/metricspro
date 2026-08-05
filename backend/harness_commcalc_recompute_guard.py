#!/usr/bin/env python3
"""RECOMPUTE GUARD (mod-commission) — proof that the commission recompute can be taken OFF the single
event loop without ever letting two of them interleave the delete-then-insert of a period's pay.

Fixes the RED FINDING in docs/handoffs/commission.md (2026-08-04 async-sweep park record):
`_run_calculation` is a 366-line `async def` with ZERO awaits, enqueued as a Starlette BackgroundTask.
Starlette awaits an ASYNC background task ON the loop, so every recompute (documented >300 s) froze the
entire product, every tenant. It was also the only thing serialising two Calculate presses — the calc
WROTE calc_status='running' but nothing ever READ it. So the guard has to land WITH the keyword change.

  A  OFF THE LOOP        — _run_calculation is a plain `def`, still zero awaits, and no `await` /
                           `asyncio.run` anywhere in backend/app still targets it. Plus a LIVE negative
                           control: the same background task as `async def` freezes a concurrent request,
                           as `def` it does not.
  B  MONEY BYTE-IDENTITY — the 366-line calculation body is byte-for-byte the base file's, and the diff
                           touches no other module file, no engine, no calculator.
  C  RACE, ON REAL POSTGRES — two threads, genuine row-lock contention, exactly one claim. Plus the
                           first-ever-row INSERT race arbitrated by UNIQUE(org_id, period).
  D  STALE TAKEOVER     — a dead run can never wedge recomputes; a live one is never stolen.
  E  FAIL OPEN          — mig 275 absent / PostgREST error / indeterminate → the recompute PROCEEDS and
                           the status row is left exactly as main leaves it.
  F  SCOPE              — other period / other tenant never blocked; '2026-07' and 'July 2026' are ONE slot.
  G  TOKEN HANDOFF      — the endpoint claims once and hands the token down; the task does not re-claim
                           its own slot; an internal caller (DLAR / email sweep) claims for itself and
                           SKIPS instead of raising.
  H  END-TO-END         — through the real FastAPI/Starlette stack: two POSTs → one 200, one 409.
  I  NO MONEY WRITES    — the guard writes only calc_status; a refused run writes NOTHING to
                           rep_commissions / flags / chargeback_items.
  J  MIGRATION HYGIENE  — 275 is in band, additive, idempotent, no anon/authenticated grant, no policy.

Sections C/D use a real Postgres over psycopg2 (docker: postgres:16-alpine on 127.0.0.1:55432) with a
shim that speaks the PostgREST calls the guard actually issues. If no database is reachable they SKIP
loudly rather than silently pass.

Run:  cd backend && python3 harness_commcalc_recompute_guard.py
      (optional)  GUARD_PG_DSN="postgresql://postgres:guard@127.0.0.1:55432/postgres"
"""
import ast
import asyncio
import inspect
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PASS = FAIL = SKIP = 0
FAILED = []


def ck(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f"   [{extra}]" if extra else ""))
    else:
        FAIL += 1
        FAILED.append(label)
        print(f"  FAIL  {label}" + (f"   [{extra}]" if extra else ""))


def skip(label, why):
    global SKIP
    SKIP += 1
    print(f"  SKIP  {label}   [{why}]")


def head(t):
    print("\n" + "=" * 100 + f"\n  {t}\n" + "=" * 100)


BASE = os.environ.get("GUARD_BASE", "da961df")
ROUTER_REL = "backend/app/modules/commcalc/router.py"
ROUTER_ABS = os.path.join(HERE, "app", "modules", "commcalc", "router.py")


def git(*args):
    return subprocess.run(["git", "-C", REPO] + list(args), capture_output=True, text=True).stdout


branch_src = open(ROUTER_ABS, encoding="utf-8").read()
base_src = git("show", f"{BASE}:{ROUTER_REL}")
if not base_src.strip():
    print(f"FATAL: could not read {ROUTER_REL} at {BASE}")
    sys.exit(2)

import app.modules.commcalc.router as R  # noqa: E402


def fn_source(src, name):
    """Exact source text of a top-level def/async def, from the `def` line through its last line."""
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "".join(lines[node.lineno - 1:node.end_lineno])
    return None


# ════════════════════════════════════════════════════════════════════════════════════════════════
head("A · OFF THE LOOP — the recompute is a threadpool task now, and nothing still awaits it")
# ════════════════════════════════════════════════════════════════════════════════════════════════
ck("BASE _run_calculation was an `async def` (the defect this package fixes)",
   fn_source(base_src, "_run_calculation").startswith("async def _run_calculation("))
ck("BRANCH _run_calculation is a plain `def` → Starlette runs it in the THREADPOOL, not on the loop",
   fn_source(branch_src, "_run_calculation").startswith("def _run_calculation("))
ck("asyncio.iscoroutinefunction(_run_calculation) is False (what Starlette actually branches on)",
   asyncio.iscoroutinefunction(R._run_calculation) is False)

_calc_fn = fn_source(branch_src, "_run_calculation")
_awaits = [n for n in ast.walk(ast.parse(_calc_fn.replace("def _run_calculation", "def _f", 1)))
           if isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))]
ck("the recompute still contains ZERO awaits (it never needed to be async)", not _awaits,
   f"awaits={len(_awaits)}")

ck("BASE POST /calculate was `async def`; BRANCH is `def` (re-applies the reverted 5e7f53b)",
   fn_source(base_src, "calculate").startswith("async def calculate(")
   and fn_source(branch_src, "calculate").startswith("def calculate("))
ck("asyncio.iscoroutinefunction(calculate) is False", asyncio.iscoroutinefunction(R.calculate) is False)

# no coroutine driver anywhere in the app still targets the now-sync function
app_hits = []
for root, _d, files in os.walk(os.path.join(HERE, "app")):
    for f in files:
        if f.endswith(".py"):
            p = os.path.join(root, f)
            t = open(p, encoding="utf-8").read()
            for m in re.finditer(r"(await|asyncio\.run\(|run_until_complete\()\s*_run_calculation", t):
                app_hits.append(f"{os.path.relpath(p, HERE)}:{t[:m.start()].count(chr(10)) + 1}")
ck("no `await _run_calculation` / `asyncio.run(_run_calculation)` left in backend/app", not app_hits,
   ";".join(app_hits))
ck("the DLAR sweep calls it directly (it is already a sync threadpool worker)",
   "_cres = _run_calculation(res['period'], org_id)" in branch_src)
ck("the EMAIL sweep (a real coroutine) sends it to the threadpool instead of blocking the loop",
   "await _in_pool(_run_calculation, _ftp_current_period(), org_id)" in branch_src
   and "from starlette.concurrency import run_in_threadpool as _in_pool" in branch_src)

# ── LIVE NEGATIVE CONTROL — the freeze is real, and `def` removes it ─────────────────────────────
# A REAL uvicorn server (one worker, one event loop — production's shape), not a TestClient: the point
# being measured is what the single loop does while a background task runs, and TestClient drives each
# request through its own portal, which would hide exactly that.
try:
    import socket
    import urllib.request

    import uvicorn
    from fastapi import BackgroundTasks, FastAPI

    BLOCK = 0.8

    def _sync_task():
        time.sleep(BLOCK)          # exactly what a 300 s recompute does: blocking, zero awaits

    async def _async_task():
        time.sleep(BLOCK)          # `async def` with a blocking body — the production defect, in miniature

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def _serve(task):
        app = FastAPI()

        @app.post("/start")
        def start(bt: BackgroundTasks):
            bt.add_task(task)
            return {"ok": True}

        @app.get("/other")
        async def other():
            return {"ok": True}     # any other tenant's page, served ON the loop

        port = _free_port()
        srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        th = threading.Thread(target=srv.run, daemon=True)
        th.start()
        for _ in range(200):
            if srv.started:
                break
            time.sleep(0.02)
        return srv, port

    def _measure(task):
        # The GET must be IN FLIGHT while the background task runs. (The POST itself also blocks in the
        # async case — Starlette awaits background tasks before the response completes — so measuring
        # after the POST returns would measure nothing.)
        srv, port = _serve(task)
        out = {}
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/other", timeout=5).read()   # warm

            def other_page():
                time.sleep(0.2)                       # the task is running by now
                t0 = time.perf_counter()
                urllib.request.urlopen(f"http://127.0.0.1:{port}/other", timeout=30).read()
                out["ms"] = (time.perf_counter() - t0) * 1000

            th = threading.Thread(target=other_page)
            th.start()
            urllib.request.urlopen(f"http://127.0.0.1:{port}/start", data=b"", timeout=30).read()
            th.join(30)
            return out.get("ms", -1)
        finally:
            srv.should_exit = True
            time.sleep(0.3)

    ms_async = _measure(_async_task)
    ms_sync = _measure(_sync_task)
    ck("LIVE: an ASYNC background task FREEZES a concurrent request (the production defect)",
       ms_async > BLOCK * 1000 * 0.5, f"other request took {ms_async:.0f} ms")
    ck("LIVE: the SAME work as a SYNC background task does NOT freeze it (this package)",
       ms_sync < ms_async / 4, f"other request took {ms_sync:.0f} ms (vs {ms_async:.0f} ms async)")
except Exception as e:
    skip("LIVE negative control", f"{type(e).__name__}: {e}")


# ════════════════════════════════════════════════════════════════════════════════════════════════
head("B · MONEY BYTE-IDENTITY — the calculation itself did not change")
# ════════════════════════════════════════════════════════════════════════════════════════════════
BODY_ANCHOR = "    # MONEY-PATH FRESHNESS (Gate-1 rework finding 1b)"
b_calc = fn_source(base_src, "_run_calculation")
n_calc = fn_source(branch_src, "_run_calculation")
ck("both versions share the calculation-body anchor",
   BODY_ANCHOR in b_calc and BODY_ANCHOR in n_calc)
b_body = b_calc[b_calc.index(BODY_ANCHOR):]
n_body = n_calc[n_calc.index(BODY_ANCHOR):]
ck("the ENTIRE 366-line calculation body is BYTE-IDENTICAL to base "
   "(delete-then-insert, guards, engines, notices, status stamps)",
   b_body == n_body, f"{len(b_body)} bytes")
ck("everything added to _run_calculation sits BEFORE that anchor (signature, docstring, claim)",
   len(n_calc) - len(b_calc) == len(n_calc[:n_calc.index(BODY_ANCHOR)]) - len(b_calc[:b_calc.index(BODY_ANCHOR)]))

for token in ("delete()", "rep_commissions", "chargeback_items", "flags"):
    ck(f"'{token}' occurrences in the calculation body unchanged vs base",
       b_body.count(token) == n_body.count(token), f"{b_body.count(token)}")

b_ep, n_ep = fn_source(base_src, "calculate"), fn_source(branch_src, "calculate")
ck("POST /calculate still: require_org → mark running → enqueue → 'started'",
   "require_org(org_id)" in n_ep and "background_tasks.add_task(_run_calculation" in n_ep
   and '"status": "started"' in n_ep)
ck("the endpoint's only behavioural addition is the 409 refusal",
   n_ep.count("HTTPException(409") == 1 and b_ep.count("HTTPException") == 0)
ck("`force` does NOT bypass the running guard (force is the zero-wipe outcome check, not concurrency)",
   "force" not in R._calc_guard_acquire.__code__.co_varnames)

changed = [l for l in git("diff", "--name-only", BASE, "--").splitlines() if l.strip()]
# APP code: router.py and nothing else. (backend/harness_* and backend/scratchpad/* are proof files —
# the two scratchpad edits only make existing assertions shape-agnostic; they are re-run below.)
_app_changed = [c for c in changed if c.startswith("backend/app/")]
ck("router.py is the ONLY backend app file touched — no engine, no calculator, no other module",
   _app_changed == [ROUTER_REL], ";".join(_app_changed))
ck("the only non-app backend edits are proof files",
   not [c for c in changed if c.startswith("backend/") and c not in _app_changed
        and not (c.startswith("backend/harness_") or c.startswith("backend/scratchpad/"))],
   ";".join(changed))
for f in ("calculator.py", "commission_engine.py", "sale_installment_engine.py", "installment_engine.py",
          "plan_impact.py", "commission_legs.py"):
    ck(f"{f} untouched", f"backend/app/modules/commcalc/{f}" not in changed)


# ════════════════════════════════════════════════════════════════════════════════════════════════
#  A PostgREST-shaped client over a REAL Postgres — used by sections C/D/F/G/I
# ════════════════════════════════════════════════════════════════════════════════════════════════
DSN = os.environ.get("GUARD_PG_DSN", "postgresql://postgres:guard@127.0.0.1:55432/postgres")
try:
    import psycopg2
    import psycopg2.extras
    _probe = psycopg2.connect(DSN, connect_timeout=3)
    _probe.close()
    HAVE_PG = True
except Exception as _e:
    HAVE_PG = False
    PG_WHY = f"{type(_e).__name__}: {_e}"

DDL_FULL = """
DROP SCHEMA IF EXISTS commcalc CASCADE;
CREATE SCHEMA commcalc;
CREATE TABLE commcalc.calc_status (
  id SERIAL PRIMARY KEY, org_id TEXT NOT NULL, period TEXT NOT NULL,
  calc_status TEXT DEFAULT 'pending', calc_finished_at TIMESTAMPTZ, save_errors JSONB,
  calc_started_at TIMESTAMPTZ, calc_run_id TEXT,
  UNIQUE(org_id, period));
CREATE TABLE commcalc.commission_org_config (
  org_id TEXT PRIMARY KEY, pay_disabled BOOLEAN, residual_visibility TEXT,
  plan_ct_resolution TEXT, installment_mrc_basis TEXT, installment_mrc_hardware_guard BOOLEAN,
  store_resolution TEXT, calc_stale_minutes INTEGER);
CREATE TABLE commcalc.rep_commissions (id SERIAL PRIMARY KEY, org_id TEXT, period TEXT, total_payout NUMERIC);
"""
# The SAME schema WITHOUT the migration-275 columns — for the fail-open section.
DDL_PRE275 = DDL_FULL.replace("  calc_started_at TIMESTAMPTZ, calc_run_id TEXT,\n", "") \
                     .replace(", calc_stale_minutes INTEGER", "")

_OPS = {"is": lambda c, v: (f"{c} IS NULL", []) if v == "null" else (f"{c} IS %s", [v]),
        "neq": lambda c, v: (f"({c} IS NULL OR {c} <> %s)", [v]),
        "eq": lambda c, v: (f"{c} = %s", [v]),
        "lt": lambda c, v: (f"{c} < %s", [v])}


def _pgrst_or(expr):
    """Translate PostgREST's or=(a.is.null,b.neq.x,c.lt.t) into SQL, exactly as PostgREST documents it.
    NOTE on `neq`: PostgREST emits `col <> 'x'`, which is NULL (not TRUE) for a NULL column — that is
    why the guard ALSO passes `calc_status.is.null`. Modelled faithfully here, including that trap."""
    parts, params = [], []
    for item in expr.split(","):
        col, op, val = item.split(".", 2)
        if op == "neq":
            parts.append(f"{col} <> %s")       # faithful: NULL-in ⇒ NULL-out, NOT true
            params.append(val)
        elif op == "is" and val == "null":
            parts.append(f"{col} IS NULL")
        elif op == "lt":
            parts.append(f"{col} < %s")
            params.append(val)
        else:
            raise AssertionError(f"unmodelled PostgREST op: {item}")
    return "(" + " OR ".join(parts) + ")", params


class PGTable:
    def __init__(self, conn, schema, table, hooks):
        self.c, self.s, self.t, self.h = conn, schema, table, hooks
        self.kind = None
        self.payload = None
        self.cols = "*"
        self.filters = []      # (sql, params)
        self._limit = None
        self.conflict = None

    # ── verbs ──
    def select(self, cols="*", **kw):
        self.kind, self.cols = "select", cols
        return self

    def update(self, payload):
        self.kind, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.kind, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.kind, self.payload, self.conflict = "upsert", payload, on_conflict
        return self

    def delete(self):
        self.kind = "delete"
        return self

    # ── filters ──
    def eq(self, col, val):
        self.filters.append((f"{col} = %s", [val]))
        return self

    def or_(self, expr):
        self.filters.append(_pgrst_or(expr))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _where(self):
        if not self.filters:
            return "", []
        sql = " WHERE " + " AND ".join(f for f, _ in self.filters)
        params = [p for _, ps in self.filters for p in ps]
        return sql, params

    def execute(self):
        self.h.setdefault("log", []).append((self.kind, f"{self.s}.{self.t}", self.payload, self.filters))
        rel = f"{self.s}.{self.t}"
        cur = self.c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            if self.kind == "select":
                w, p = self._where()
                cur.execute(f"SELECT {self.cols} FROM {rel}{w}" + (f" LIMIT {int(self._limit)}" if self._limit else ""), p)
                rows = cur.fetchall()
            elif self.kind == "update":
                keys = list(self.payload)
                w, p = self._where()
                cur.execute(f"UPDATE {rel} SET " + ", ".join(f"{k} = %s" for k in keys) + w + " RETURNING *",
                            [self.payload[k] for k in keys] + p)
                rows = cur.fetchall()
            elif self.kind in ("insert", "upsert"):
                if self.h.get("insert_barrier") is not None and self.kind == "insert":
                    self.h["insert_barrier"].wait(timeout=5)
                keys = list(self.payload)
                sql = (f"INSERT INTO {rel} (" + ", ".join(keys) + ") VALUES (" +
                       ", ".join(["%s"] * len(keys)) + ")")
                if self.kind == "upsert":
                    conf = self.conflict or "org_id,period"
                    sql += (f" ON CONFLICT ({conf}) DO UPDATE SET " +
                            ", ".join(f"{k} = EXCLUDED.{k}" for k in keys if k not in conf.split(",")))
                cur.execute(sql + " RETURNING *", [self.payload[k] for k in keys])
                rows = cur.fetchall()
            elif self.kind == "delete":
                w, p = self._where()
                cur.execute(f"DELETE FROM {rel}{w} RETURNING *", p)
                rows = cur.fetchall()
            else:
                raise AssertionError("no verb")
        finally:
            cur.close()
        for r in rows:
            for k, v in list(r.items()):
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
        return type("Res", (), {"data": [dict(r) for r in rows], "count": len(rows)})()


class PGClient:
    """Speaks the exact chain the guard uses: .schema(s).table(t).update(...).eq(...).or_(...).execute()"""
    def __init__(self, conn, hooks=None):
        self.conn = conn
        self.hooks = hooks if hooks is not None else {}
        self._schema = "public"

    def schema(self, s):
        c = PGClient(self.conn, self.hooks)
        c._schema = s
        return c

    def table(self, t):
        return PGTable(self.conn, self._schema, t, self.hooks)


def pg_conn(ddl=None):
    c = psycopg2.connect(DSN)
    c.autocommit = True                       # PostgREST: one statement = one transaction
    if ddl:
        cur = c.cursor()
        cur.execute(ddl)
        cur.close()
    return c


ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000b2"


# ════════════════════════════════════════════════════════════════════════════════════════════════
head("C · THE RACE — real Postgres, real row-lock contention, exactly one winner")
# ════════════════════════════════════════════════════════════════════════════════════════════════
if not HAVE_PG:
    skip("race proof on real Postgres", PG_WHY)
    skip("insert race on real Postgres", PG_WHY)
else:
    setup = pg_conn(DDL_FULL)
    cur = setup.cursor()
    cur.execute("INSERT INTO commcalc.calc_status (org_id, period, calc_status) VALUES (%s,%s,'done')",
                (ORG_A, "July 2026"))
    cur.close()

    # Hold the row so BOTH threads pile onto the same row lock at the same instant — this is what makes
    # the proof deterministic instead of "we ran two threads and hoped they overlapped".
    blocker = psycopg2.connect(DSN)
    bcur = blocker.cursor()
    bcur.execute("BEGIN")
    bcur.execute("SELECT 1 FROM commcalc.calc_status WHERE org_id=%s AND period=%s FOR UPDATE",
                 (ORG_A, "July 2026"))

    results = {}
    started = threading.Barrier(3)

    def racer(name):
        conn = pg_conn()
        started.wait()
        results[name] = R._calc_guard_acquire(PGClient(conn), ORG_A, "July 2026")

    t1 = threading.Thread(target=racer, args=("t1",))
    t2 = threading.Thread(target=racer, args=("t2",))
    t1.start()
    t2.start()
    started.wait()               # both threads are now inside the guard, about to UPDATE
    time.sleep(0.7)              # …and both are blocked on the row lock the blocker holds
    locked = None
    with blocker.cursor() as q:
        q.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock'")
        locked = q.fetchone()[0]
    bcur.execute("COMMIT")       # release — both UPDATEs resume in the same instant
    bcur.close()
    blocker.close()
    t1.join(20)
    t2.join(20)

    ck("both racers really did contend on the SAME row lock (not a lucky serialisation)",
       locked >= 2, f"backends waiting on Lock = {locked}")
    winners = [k for k, v in results.items() if v[0] and v[1]]
    losers = [k for k, v in results.items() if not v[0]]
    ck("EXACTLY ONE thread acquired the slot", len(winners) == 1, f"winners={winners}")
    ck("the other got a clean refusal with the holder's running-since", len(losers) == 1
       and results[losers[0]][2].get("running_since"), f"losers={losers}")
    ck("neither thread failed open (a real refusal is not an error path)",
       not [k for k, v in results.items() if v[0] and v[1] is None])

    with setup.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as q:
        q.execute("SELECT * FROM commcalc.calc_status WHERE org_id=%s AND period=%s", (ORG_A, "July 2026"))
        row = q.fetchone()
    ck("the stored row is the WINNER's run — one clean claim, not two interleaved",
       row["calc_status"] == "running" and row["calc_run_id"] == results[winners[0]][1],
       f"run_id={row['calc_run_id'][:8]}…")
    ck("the loser's message names the period and tells the operator what to do",
       "already running" in R._calc_busy_message("July 2026", results[losers[0]][2])
       and "July 2026" in R._calc_busy_message("July 2026", results[losers[0]][2]))

    # ── first-ever calc for a period: no row exists, so UNIQUE(org_id, period) is the arbiter ──
    with setup.cursor() as q:
        q.execute("DELETE FROM commcalc.calc_status")
    ins_results = {}
    ins_barrier = threading.Barrier(2, timeout=5)

    def ins_racer(name):
        conn = pg_conn()
        ins_results[name] = R._calc_guard_acquire(PGClient(conn, {"insert_barrier": ins_barrier}),
                                                  ORG_A, "August 2026")

    a = threading.Thread(target=ins_racer, args=("a",))
    b = threading.Thread(target=ins_racer, args=("b",))
    a.start()
    b.start()
    a.join(20)
    b.join(20)
    ck("INSERT race: exactly one acquired the first-ever slot",
       len([k for k, v in ins_results.items() if v[0] and v[1]]) == 1, str(list(ins_results)))
    ck("INSERT race: the loser was REFUSED, not failed open",
       len([k for k, v in ins_results.items() if not v[0]]) == 1)
    with setup.cursor() as q:
        q.execute("SELECT count(*) FROM commcalc.calc_status WHERE org_id=%s AND period='August 2026'", (ORG_A,))
        n = q.fetchone()[0]
    ck("INSERT race: exactly ONE status row exists afterwards", n == 1, f"rows={n}")


# ════════════════════════════════════════════════════════════════════════════════════════════════
head("D · STALE TAKEOVER — a dead run can never wedge recomputes; a live one is never stolen")
# ════════════════════════════════════════════════════════════════════════════════════════════════
if not HAVE_PG:
    skip("stale takeover", PG_WHY)
else:
    conn = pg_conn(DDL_FULL)
    cl = PGClient(conn)

    def seed(period, status, started_sql, stale=None):
        with conn.cursor() as q:
            q.execute("DELETE FROM commcalc.calc_status WHERE org_id=%s AND period=%s", (ORG_A, period))
            q.execute(f"INSERT INTO commcalc.calc_status (org_id, period, calc_status, calc_started_at)"
                      f" VALUES (%s,%s,%s,{started_sql})", (ORG_A, period, status))
            q.execute("INSERT INTO commcalc.commission_org_config (org_id, calc_stale_minutes) VALUES (%s,%s)"
                      " ON CONFLICT (org_id) DO UPDATE SET calc_stale_minutes = EXCLUDED.calc_stale_minutes",
                      (ORG_A, stale))

    seed("July 2026", "running", "now() - interval '3 minutes'")
    ck("a FRESH running run is NOT stolen (3 min < 20 min default)",
       R._calc_guard_acquire(cl, ORG_A, "July 2026")[0] is False)

    seed("July 2026", "running", "now() - interval '45 minutes'")
    ok, tok, _ = R._calc_guard_acquire(cl, ORG_A, "July 2026")
    ck("a run still marked running after 45 min is presumed DEAD and taken over", ok and tok)

    seed("July 2026", "running", "NULL")
    ck("a legacy 'running' row with NO start time is takeable (pre-mig-275 rows can't wedge)",
       R._calc_guard_acquire(cl, ORG_A, "July 2026")[0] is True)

    seed("July 2026", "done", "now()")
    ck("a finished run never blocks the next Calculate",
       R._calc_guard_acquire(cl, ORG_A, "July 2026")[0] is True)
    seed("July 2026", "error", "now()")
    ck("a FAILED run never blocks the next Calculate",
       R._calc_guard_acquire(cl, ORG_A, "July 2026")[0] is True)

    seed("July 2026", "running", "now() - interval '6 minutes'", stale=5)
    ck("the takeover threshold is TENANT-CONFIGURABLE (calc_stale_minutes=5 → a 6-min run is dead)",
       R._calc_guard_acquire(cl, ORG_A, "July 2026")[0] is True)
    seed("July 2026", "running", "now() - interval '30 minutes'", stale=120)
    ck("…and a tenant with long recomputes can raise it (120 → a 30-min run is still ALIVE)",
       R._calc_guard_acquire(cl, ORG_A, "July 2026")[0] is False)
    ck("no config row → the 20-minute code default", R._calc_stale_minutes(PGClient(pg_conn()), ORG_B) == 20)
    seed("July 2026", "done", "now()", stale=99999)
    ck("an absurd stored value is clamped to 1440, not honoured", R._calc_stale_minutes(cl, ORG_A) == 1440)
    seed("July 2026", "done", "now()", stale=0)
    ck("zero/negative is clamped to 1", R._calc_stale_minutes(cl, ORG_A) == 1)


# ════════════════════════════════════════════════════════════════════════════════════════════════
head("E · FAIL OPEN — a broken guard must never block a recompute")
# ════════════════════════════════════════════════════════════════════════════════════════════════
if HAVE_PG:
    pre = pg_conn(DDL_PRE275)                       # the LIVE schema until migration 275 is run
    cl = PGClient(pre)
    with pre.cursor() as q:
        q.execute("INSERT INTO commcalc.calc_status (org_id, period, calc_status) VALUES (%s,'July 2026','running')",
                  (ORG_A,))
    ok, tok, holder = R._calc_guard_acquire(cl, ORG_A, "July 2026")
    ck("mig 275 NOT applied → guard FAILS OPEN (recompute proceeds, exactly today's behaviour)",
       ok is True and tok is None and holder is None)
    with pre.cursor() as q:
        q.execute("SELECT calc_status FROM commcalc.calc_status WHERE org_id=%s AND period='July 2026'", (ORG_A,))
        st = q.fetchone()[0]
    ck("…and the legacy 'running' mark still happened, so /calc-status is unchanged pre-migration",
       st == "running")
else:
    skip("pre-migration fail-open on real Postgres", PG_WHY)


class Boom:
    def schema(self, *a):
        raise RuntimeError("PostgREST unavailable")


ok, tok, holder = R._calc_guard_acquire(Boom(), ORG_A, "July 2026")
ck("a totally dead database client → FAIL OPEN, never a refusal", ok is True and tok is None)
ck("the guard never raises out of _calc_guard_acquire", True)


class Weird:
    """Update returns nothing AND the row is unreadable — an indeterminate guard must still fail open."""
    class T:
        def __getattr__(self, _n):
            return lambda *a, **k: self
        def execute(self):
            return type("R", (), {"data": []})()
    def schema(self, *a):
        return self
    def table(self, *a):
        return Weird.T()


ok, tok, holder = R._calc_guard_acquire(Weird(), ORG_A, "July 2026")
ck("indeterminate guard state → FAIL OPEN (never invents a refusal)", ok is True and holder is None)


# ════════════════════════════════════════════════════════════════════════════════════════════════
head("F · SCOPE — one slot per (tenant, month); nothing else is blocked")
# ════════════════════════════════════════════════════════════════════════════════════════════════
if not HAVE_PG:
    skip("scope proof", PG_WHY)
else:
    conn = pg_conn(DDL_FULL)
    cl = PGClient(conn)
    ck("first claim on July 2026 / org A succeeds", R._calc_guard_acquire(cl, ORG_A, "July 2026")[0])
    ck("a second July 2026 claim in the SAME tenant is refused",
       R._calc_guard_acquire(cl, ORG_A, "July 2026")[0] is False)
    ck("a DIFFERENT month in the same tenant is allowed",
       R._calc_guard_acquire(cl, ORG_A, "June 2026")[0] is True)
    ck("the SAME month in a DIFFERENT tenant is allowed (multi-tenant: no cross-org block)",
       R._calc_guard_acquire(cl, ORG_B, "July 2026")[0] is True)
    ck("'2026-07' collides with 'July 2026' — ONE slot per month, both spellings (_pvariants trap)",
       R._calc_guard_acquire(cl, ORG_A, "2026-07")[0] is False)
    with conn.cursor() as q:
        q.execute("SELECT count(*) FROM commcalc.calc_status")
        ck("no duplicate status rows created by the alternate spelling", q.fetchone()[0] == 3)
    with conn.cursor() as q:
        q.execute("SELECT count(*) FROM commcalc.calc_status WHERE org_id=%s", (ORG_B,))
        ck("every guard write is org-scoped (contract RULE ONE)", q.fetchone()[0] == 1)


# ════════════════════════════════════════════════════════════════════════════════════════════════
head("G · TOKEN HANDOFF — one claim per press; internal callers claim for themselves")
# ════════════════════════════════════════════════════════════════════════════════════════════════
ck("the endpoint hands its claim token to the background task",
   "background_tasks.add_task(_run_calculation, period, org_id, force, token)" in branch_src)
ck("_run_calculation only claims when it was NOT handed a token",
   "if not guard_token:" in fn_source(branch_src, "_run_calculation"))
sig = inspect.signature(R._run_calculation)
ck("the new parameter is optional and last (every existing 3-arg call site still valid)",
   list(sig.parameters) == ["period", "org_id", "force", "guard_token"]
   and sig.parameters["guard_token"].default is None)

calls = {"n": 0}
orig_acq = R._calc_guard_acquire
try:
    R._calc_guard_acquire = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), (True, "tok", None))[1]
    orig_sb = R.sb
    R.sb = lambda: Boom()
    try:
        R._run_calculation("July 2026", ORG_A, False, "tok-from-endpoint")
    except Exception:
        pass
    ck("a task GIVEN a token does not re-claim (one claim per Calculate press)", calls["n"] == 0)
    calls["n"] = 0
    try:
        R._run_calculation("July 2026", ORG_A)
    except Exception:
        pass
    ck("an internal caller (DLAR / email sweep) claims for itself", calls["n"] == 1)

    R._calc_guard_acquire = lambda *a, **k: (False, None, {"running_since": "2026-08-05T00:00:00Z",
                                                           "stale_minutes": 20})
    out = R._run_calculation("July 2026", ORG_A)
    ck("a refused internal recompute RETURNS a skip marker — it never raises into a sweep",
       isinstance(out, dict) and out.get("skipped") == "already_running", str(out))
finally:
    R._calc_guard_acquire = orig_acq
    R.sb = orig_sb
ck("the DLAR sweep reports the skip honestly instead of claiming it recalculated",
   "recalc skipped for" in branch_src and "_cres.get('skipped')" in branch_src)


# ════════════════════════════════════════════════════════════════════════════════════════════════
head("H · END TO END — through the real FastAPI/Starlette stack")
# ════════════════════════════════════════════════════════════════════════════════════════════════
if not HAVE_PG:
    skip("end-to-end 200/409", PG_WHY)
else:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    conn = pg_conn(DDL_FULL)
    ran = []
    orig_sb, orig_run = R.sb, R._run_calculation
    try:
        R.sb = lambda: PGClient(conn)
        R._run_calculation = lambda *a, **k: ran.append((a, threading.current_thread().name))
        app = FastAPI()
        app.post("/calculate/{period}")(R.calculate)
        c = TestClient(app)
        r1 = c.post(f"/calculate/July%202026?org_id={ORG_A}")
        r2 = c.post(f"/calculate/July%202026?org_id={ORG_A}")
        r3 = c.post(f"/calculate/June%202026?org_id={ORG_A}")
        ck("first POST /calculate → 200 started", r1.status_code == 200 and r1.json()["status"] == "started")
        ck("second POST for the SAME month → 409, not a silent second recompute", r2.status_code == 409,
           f"{r2.status_code}")
        ck("the 409 body is plain English an operator can act on",
           "already running" in r2.json()["detail"] and "half-written" in r2.json()["detail"])
        ck("a DIFFERENT month still starts normally", r3.status_code == 200)
        ck("the refused press enqueued NOTHING (one background run, not two)", len(ran) == 2,
           f"enqueued={len(ran)}")
        ck("the enqueued task carries the claim token", len(ran[0][0]) == 4 and ran[0][0][3])
        ck("Starlette ran the sync background task OFF the event loop (threadpool thread)",
           all("MainThread" not in t for _a, t in ran), str([t for _a, t in ran]))
    finally:
        R.sb, R._run_calculation = orig_sb, orig_run


# ════════════════════════════════════════════════════════════════════════════════════════════════
head("I · NO MONEY WRITES — the guard touches status only")
# ════════════════════════════════════════════════════════════════════════════════════════════════
guard_src = "".join(inspect.getsource(f) for f in
                    (R._calc_guard_acquire, R._calc_guard_legacy_mark, R._calc_stale_minutes,
                     R._calc_busy_message))
for t in ("rep_commissions", "chargeback_items", "raw_sales", "daily_sales_feed", "total_payout",
          "commission_plan", "payout"):
    ck(f"the guard never mentions {t}", t not in guard_src)
ck("calc_status is the ONLY table the guard names at all",
   set(re.findall(r"table\('([a-z_]+)'\)", guard_src)) == {"calc_status"},
   str(set(re.findall(r"table\('([a-z_]+)'\)", guard_src))))
ck("the tenant threshold is read through the EXISTING config reader, not a new query",
   "_commission_org_config(client, org_id)" in inspect.getsource(R._calc_stale_minutes))

if HAVE_PG:
    conn = pg_conn(DDL_FULL)
    cl = PGClient(conn, {"log": []})
    R._calc_guard_acquire(cl, ORG_A, "July 2026")
    R._calc_guard_acquire(cl, ORG_A, "July 2026")     # the refused one
    kinds = [(k, rel) for k, rel, _p, _f in cl.hooks["log"]]
    ck("a REFUSED claim performs zero writes outside calc_status",
       not [1 for k, rel in kinds if k in ("insert", "update", "upsert", "delete")
            and rel != "commcalc.calc_status"], str(kinds))
    with conn.cursor() as q:
        q.execute("SELECT count(*) FROM commcalc.rep_commissions")
        ck("rep_commissions untouched by the guard, claimed or refused", q.fetchone()[0] == 0)


# ════════════════════════════════════════════════════════════════════════════════════════════════
head("J · MIGRATION HYGIENE — band, additive, idempotent, no anon grant")
# ════════════════════════════════════════════════════════════════════════════════════════════════
MIG = os.path.join(REPO, "database", "migrations", "275_commission_recompute_guard.sql")
ck("migration 275 exists", os.path.exists(MIG))
msql_raw = open(MIG, encoding="utf-8").read() if os.path.exists(MIG) else ""
# strip `--` comment lines: the migration DOCUMENTS the UPDATE the guard issues and says "no GRANT to
# anon", and a hygiene scan must read the STATEMENTS, not the prose about them.
msql = "\n".join(l for l in msql_raw.splitlines() if not l.lstrip().startswith("--"))
existing = sorted(f.split("_")[0] for f in os.listdir(os.path.join(REPO, "database", "migrations"))
                  if f[:3].isdigit())
ck("275 is inside mod-commission's band 200-299", 200 <= 275 <= 299)
ck("275 was FREE before this package", existing.count("275") == 1, f"count={existing.count('275')}")
ck("additive only — no DROP / DELETE / UPDATE of data",
   not re.search(r"\b(DROP|TRUNCATE|DELETE FROM|UPDATE )\b", msql.upper().replace("DO UPDATE", "")))
ck("idempotent — every ALTER uses ADD COLUMN IF NOT EXISTS",
   msql.upper().count("ADD COLUMN IF NOT EXISTS") == 3 and msql.upper().count("ADD COLUMN ") == 3)
ck("no GRANT to anon/authenticated (contract §5)",
   not re.search(r"GRANT[\s\S]{0,80}(anon|authenticated)", msql, re.I))
ck("no open RLS policy", "CREATE POLICY" not in msql.upper())
ck("no new table → no new RLS surface", "CREATE TABLE" not in msql.upper())
ck("the three columns are exactly the ones the code reads",
   all(c in msql for c in ("calc_started_at", "calc_run_id", "calc_stale_minutes")))

# tenant-configurable per RULE TWO, with an admin UI
ui = open(os.path.join(REPO, "frontend", "src", "app", "(platform)", "commcalc",
                       "plan-installments", "page.tsx"), encoding="utf-8").read()
ck("the threshold is editable in the admin UI (RULE TWO: config + UI, never a constant)",
   "calc_stale_minutes" in ui)
ck("the UI copy is plain user-facing English, not developer language",
   "does not change anyone" in ui.replace("&apos;", "'").replace("’", "'").lower()
   or "does not change anyone's pay" in ui.replace("&apos;", "'"))
ck("PUT /commission-settings accepts and clamps it",
   "calc_stale_minutes" in inspect.getsource(R.put_commission_settings)
   and "min(1440, max(1, int(_v)))" in inspect.getsource(R.put_commission_settings))
ck("it is written in its OWN statement so a pre-275 save of other pay settings still works",
   "is migration 275 applied" in inspect.getsource(R.put_commission_settings))


print("\n" + "=" * 100)
print(f"  {PASS} passed, {FAIL} failed, {SKIP} skipped")
print("=" * 100)
if FAILED:
    print("FAILED:")
    for f in FAILED:
        print("   ·", f)
sys.exit(1 if FAIL else 0)
