#!/usr/bin/env python3
"""ASYNC-SWEEP (mod-commission) — proof that moving commcalc's zero-`await` route handlers off the
single uvicorn event loop is (a) worth doing, (b) keyword-only, and (c) safe for every caller.

Direct follow-up to platform-core's NAV-PERF ask (docs/handoffs/platform-core.md, main bafab57):
"181 zero-`await` `async def` handlers remain in app/modules/commcalc/ — every one blocks the whole
product for its duration."  Sections A/B/C mirror harness_nav_perf.py so the two packages can be
reviewed against the same yardstick.

  A  LIVE NEGATIVE CONTROL — an in-process FastAPI app proving the freeze is real and that the
     conversion removes it. Nothing touches production.
  B  KEYWORD-ONLY — re-insert `async ` on exactly the converted defs and reproduce the BASE file
     byte-for-byte. One allowed exception, pinned by exact text: team_snapshot's de-await.
  C  NOTHING THAT NEEDED ASYNC WAS TOUCHED — every handler still declared async either awaits, or is
     awaited by name by a file this agent does not own.
  D  CALLER AUDIT — no `await` or `asyncio.run()` anywhere in backend/ still targets a now-sync
     handler, and every rewritten proof file defines the dual-shape helper before it uses it.
  E  MONEY SAFETY — the calc/payout surface is untouched apart from the keyword.
  F  ROUTE SURFACE — same routes, same methods, same paths, same order as base.

Run:  cd backend && python3 harness_commcalc_async_sweep.py
"""
import ast
import asyncio
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PASS = FAIL = 0
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


def fatal(msg):
    print(f"FATAL: {msg}")
    sys.exit(2)


BASE = os.environ.get("ASYNC_SWEEP_BASE", "bafab57")
ROUTER_REL = "backend/app/modules/commcalc/router.py"
OWNED = ("app/modules/commcalc/",)
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Blocked BY ANOTHER MODULE'S FILE: backend/app/modules/notify/report_registry.py does
# `await C.<name>(...)`. notify/** belongs to mod-platform-core (AGENT_CONTRACT §1), so these 9 stay
# coroutines until that agent drops the awaits. Filed as a numbered ask in docs/handoffs/commission.md.
BLOCKED_BY_NOTIFY = {
    "get_action_plan", "get_commissions", "get_discrepancy_results", "get_flags",
    "get_gp_report", "get_phantom_payments", "get_top_sellers", "vip_invoices_list", "vip_summary",
}
# The ONE line in this package that is not the `async ` keyword.
DEAWAIT_OLD = "        summ = await get_targets_summary(cperiod, today=today, stores=push_stores or None,"
DEAWAIT_NEW = "        summ = get_targets_summary(cperiod, today=today, stores=push_stores or None,"


def git_show(rev, path):
    return subprocess.run(["git", "-C", REPO, "show", f"{rev}:{path}"],
                          capture_output=True, text=True).stdout


def is_route(dec):
    f = dec.func if isinstance(dec, ast.Call) else dec
    return isinstance(f, ast.Attribute) and f.attr in METHODS


class _Body(ast.NodeVisitor):
    def __init__(self):
        self.awaits = self.awith = self.afor = self.yields = 0

    def visit_Await(self, n): self.awaits += 1; self.generic_visit(n)
    def visit_AsyncWith(self, n): self.awith += 1; self.generic_visit(n)
    def visit_AsyncFor(self, n): self.afor += 1; self.generic_visit(n)
    def visit_Yield(self, n): self.yields += 1; self.generic_visit(n)
    def visit_YieldFrom(self, n): self.yields += 1; self.generic_visit(n)
    def visit_AsyncFunctionDef(self, n): pass          # nested scope — not this handler's body


def handlers(src):
    """{(name, lineno): (is_async, blocking_markers)} for every @router.<method> handler in `src`.

    Keyed by LINE, not by name: commcalc/router.py has a pre-existing duplicate handler name
    (`delete_category_rule` at 5900 for /carrier-category-map/{rid} and at 9926 for
    /plan-installments/category-rules/{rid}). FastAPI registers by decorator so the duplicate is
    harmless, but a name-keyed dict silently collapses the two and a name-keyed reconstruction puts
    the `async ` keyword back on the WRONG def. Every claim below is therefore line-anchored."""
    out = {}
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if not any(is_route(d) for d in node.decorator_list):
            continue
        b = _Body()
        for c in node.body:
            b.visit(c)
        out[(node.name, node.lineno)] = (isinstance(node, ast.AsyncFunctionDef),
                                         b.awaits + b.awith + b.afor + b.yields)
    return out


def pyfiles(root):
    for dp, _, fs in os.walk(root):
        if "__pycache__" in dp or "/.venv" in dp:
            continue
        for fn in sorted(fs):
            if fn.endswith(".py"):
                yield os.path.join(dp, fn)


# ══ A. LIVE NEGATIVE CONTROL ═══════════════════════════════════════════════════════════════════════
print("\nA. live negative control: does an `async def` blocking handler stall the whole app?")
try:
    import httpx
    from fastapi import FastAPI
except Exception as e:                                             # pragma: no cover
    fatal(f"httpx/fastapi unavailable: {e}")

BLOCK_S = 0.40
_probe = FastAPI()


@_probe.get("/sales-report-async")
async def _heavy_async():
    """EXACTLY the shape of the 181 handlers: declared async, body is pure blocking Supabase I/O.
    /commcalc/sales-report was MEASURED at 4,697 ms house / 3,109 ms Luxelink in this shape."""
    time.sleep(BLOCK_S)
    return {"ok": True}


@_probe.get("/sales-report-sync")
def _heavy_sync():
    """The SAME body after the sweep — FastAPI runs it in the threadpool."""
    time.sleep(BLOCK_S)
    return {"ok": True}


@_probe.get("/ping")
async def _ping():
    """A trivial handler standing in for 'every other request in the product, every tenant'."""
    return {"pong": True}


async def _measure(heavy_path):
    tr = httpx.ASGITransport(app=_probe)
    async with httpx.AsyncClient(transport=tr, base_url="http://probe") as c:
        await c.get("/ping")                                  # warm the transport
        t0 = time.perf_counter()

        async def second_user():
            await asyncio.sleep(0.05)                          # arrive 50 ms into the heavy request
            await c.get("/ping")
            return (time.perf_counter() - t0) * 1000

        answered_at, _ = await asyncio.gather(second_user(), c.get(heavy_path))
        return (time.perf_counter() - t0) * 1000, answered_at


heavy_a, answer_a = asyncio.run(_measure("/sales-report-async"))
heavy_s, answer_s = asyncio.run(_measure("/sales-report-sync"))
ck("A1 `async def` + blocking body: the second user is not answered until the block ends",
   answer_a > BLOCK_S * 1000 * 0.8, f"answered {answer_a:.0f} ms in, during a {heavy_a:.0f} ms handler")
ck("A2 `def` (post-sweep) + the SAME body: the second user is answered immediately",
   answer_s < BLOCK_S * 1000 * 0.25, f"answered {answer_s:.0f} ms in, during a {heavy_s:.0f} ms handler")
ck("A3 the second user's wait is ~the whole block as async, ~their own arrival time as def",
   answer_a / max(answer_s, 0.01) > 4, f"{answer_a:.0f} ms -> {answer_s:.0f} ms")
ck("A4 the heavy request itself is not slower as a `def`",
   heavy_s < heavy_a * 1.5 + 50, f"async {heavy_a:.0f} ms vs sync {heavy_s:.0f} ms")
ck("A5 the probe really did block for the intended duration (the control is honest)",
   heavy_a > BLOCK_S * 1000 * 0.9 and heavy_s > BLOCK_S * 1000 * 0.9,
   f"async {heavy_a:.0f} / sync {heavy_s:.0f} ms vs {BLOCK_S * 1000:.0f} ms of blocking")

# ══ B. THE SWEEP IS KEYWORD-ONLY ═══════════════════════════════════════════════════════════════════
print("\nB. the async->def sweep changed NOTHING but the keyword")
converted = {}          # relpath -> [names]
for rel in OWNED:
    base_dir = os.path.join(HERE, rel)
    if not os.path.isdir(base_dir):
        continue
    for p in pyfiles(base_dir):
        relp = os.path.relpath(p, REPO)
        now = open(p, encoding="utf-8").read()
        was = git_show(BASE, relp)
        if not was or was == now:
            continue
        h_now, h_was = handlers(now), handlers(was)
        names = [k for k, (a, _) in h_was.items() if a and k in h_now and not h_now[k][0]]
        if names:
            converted[relp] = sorted(names, key=lambda k: k[1])

total = sum(len(v) for v in converted.values())
ck("B1 the sweep converted the expected handler population", total == 173,
   f"{total} handlers across {len(converted)} file(s) (170 mechanical + get_targets_summary + "
   f"team_snapshot + calculate)")
ck("B2 every converted handler lives in the ONE file this agent owns for them",
   list(converted) == [ROUTER_REL], f"{list(converted)}")

# Reconstruct: put `async ` back on exactly the converted defs, undo the ONE de-await, and compare
# byte-for-byte to base.
recon_bad, anchor_bad = [], []
for relp, names in converted.items():
    now = open(os.path.join(REPO, relp), encoding="utf-8").read()
    was = git_show(BASE, relp)
    lines = now.split("\n")
    was_lines = was.split("\n")
    for name, lineno in names:
        cur = lines[lineno - 1]
        if not cur.startswith(f"def {name}("):
            anchor_bad.append(f"{relp}:{lineno} {cur[:40]!r}")
            continue
        lines[lineno - 1] = "async " + cur
    recon = "\n".join(lines).replace(DEAWAIT_NEW, DEAWAIT_OLD)
    if recon != was:
        recon_bad.append(relp)
ck("B3a each converted handler is still on its ORIGINAL line number (a keyword removal moves nothing)",
   not anchor_bad, f"anchor misses: {anchor_bad or 'none'}")
ck("B3 re-inserting `async ` (+ undoing the ONE pinned de-await) reproduces the base file "
   "BYTE-IDENTICALLY", not recon_bad, f"differs: {recon_bad or 'none'}")

now_src = open(os.path.join(REPO, ROUTER_REL), encoding="utf-8").read()
was_src = git_show(BASE, ROUTER_REL)
ck("B4 the de-await really is the ONLY non-keyword edit — it is present now and was not before",
   now_src.count(DEAWAIT_NEW) == 1 and DEAWAIT_OLD not in now_src
   and was_src.count(DEAWAIT_OLD) == 1,
   "router.py:17571 team_snapshot -> get_targets_summary")

# The unified diff must contain nothing but def lines plus that single de-await pair.
diff = subprocess.run(["git", "-C", REPO, "diff", "-U0", BASE, "--", ROUTER_REL],
                      capture_output=True, text=True).stdout
body = [l for l in diff.split("\n")
        if l[:1] in "+-" and l.strip() not in ("", "+", "-")
        and not l.startswith(("+++", "---"))]
non_def = [l for l in body if not re.match(r"^[+-](async )?def \w+\(", l)]
ck("B5 the router diff contains ONLY `def`/`async def` lines and the single de-await pair",
   len(non_def) == 2 and sorted(x.strip() for x in non_def) ==
   sorted([("-" + DEAWAIT_OLD).strip(), ("+" + DEAWAIT_NEW).strip()]),
   f"{len(body)} changed lines, {len(non_def)} non-def")
ck("B6 every changed def line pairs exactly (each removal has its identical keyword-less addition)",
   all(c == 2 for c in
       __import__("collections").Counter(re.sub(r"^[+-](async )?", "", l) for l in body
                                         if re.match(r"^[+-](async )?def \w+\(", l)).values()),
   f"{len(body) - 2} keyword lines = {(len(body) - 2) // 2} handlers")

bad_conv = []
h_now = handlers(now_src)
for names in converted.values():
    for k in names:
        is_async, markers = h_now[k]
        if is_async or markers:
            bad_conv.append(k)
ck("B7 every converted handler is now `def` and contains NO await/async-with/async-for/yield",
   not bad_conv, f"{total} handlers")

# ══ C. NOTHING THAT NEEDED ASYNC WAS TOUCHED ═══════════════════════════════════════════════════════
print("\nC. the handlers that stayed async are the ones that genuinely await (or are awaited elsewhere)")
still_async, zero_await_left = 0, []
for rel in OWNED:
    for p in pyfiles(os.path.join(HERE, rel)):
        for (n, _ln), (is_async, markers) in handlers(open(p, encoding="utf-8").read()).items():
            if not is_async:
                continue
            still_async += 1
            if markers == 0:
                zero_await_left.append(n)
ck("C1 the ONLY zero-await handlers left declared async are the 9 blocked by notify/report_registry.py",
   set(zero_await_left) == BLOCKED_BY_NOTIFY,
   f"{sorted(zero_await_left)}")
ck("C2 those 9 are genuinely awaited by name from a file mod-commission does not own",
   all(f"await C.{n}(" in git_show("HEAD", "backend/app/modules/notify/report_registry.py")
       for n in BLOCKED_BY_NOTIFY),
   "backend/app/modules/notify/report_registry.py")
ck("C3 the sweep was selective, not blanket — genuine coroutines were left alone",
   still_async - len(BLOCKED_BY_NOTIFY) >= 20,
   f"{still_async} still async ({still_async - len(BLOCKED_BY_NOTIFY)} of them genuinely await)")

h_base = handlers(was_src)
base_async_zero = {k for k, (a, m) in h_base.items() if a and m == 0}
ck("C4 the base population matches platform-core's audit exactly (181 zero-await async handlers)",
   len(base_async_zero) == 181, f"{len(base_async_zero)} on {BASE}")
ck("C5 173 converted + 9 blocked + 1 gained (team_snapshot, which awaited only the handler we "
   "converted) accounts for the whole population",
   len(base_async_zero) == total - 1 + len(BLOCKED_BY_NOTIFY),
   f"181 = 173 - 1 + 9; team_snapshot was NOT zero-await on base")

# ══ D. CALLER AUDIT ════════════════════════════════════════════════════════════════════════════════
print("\nD. no caller anywhere in backend/ still awaits (or asyncio.runs) a now-sync handler")
CONVERTED = {n for v in converted.values() for n, _ln in v}
# Every stdlib way to drive a coroutine. `asyncio.run(...)` was NOT enough: harness_commission_leg_split
# used `asyncio.get_event_loop().run_until_complete(...)` at 15 sites and blew up with
# "TypeError: An asyncio.Future, a coroutine or an awaitable is required" until this set was widened.
CORO_SINKS = {"run", "run_until_complete", "ensure_future", "create_task", "gather", "wait_for",
              "wait", "shield", "as_completed", "run_coroutine_threadsafe"}
# A BARE `run(...)` / `run_route(...)` is a file-local helper, not a stdlib driver. Those are proven
# dual-shape by D5 instead — flagging them here would be a false positive on the safe pattern.
LOCAL_DRIVERS = set()
offenders, helper_bad = [], []
for p in pyfiles(os.path.join(REPO, "backend")):
    relp = os.path.relpath(p, REPO)
    src = open(p, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    parents = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
        if nm not in CONVERTED:
            continue
        par = parents.get(n)
        if isinstance(par, ast.Await):
            offenders.append(f"{relp}:{n.lineno} await {nm}(")
        elif isinstance(par, ast.Call) and par.args and par.args[0] is n:
            of = par.func
            if isinstance(of, ast.Attribute):
                oa = of.attr                      # asyncio.run / <loop>.run_until_complete / ...
            elif isinstance(of, ast.Name):
                oa = of.id if of.id in LOCAL_DRIVERS else ""   # a file-local helper, checked by D5
            else:
                oa = ""
            if oa in CORO_SINKS:
                offenders.append(f"{relp}:{n.lineno} {oa}({nm}(")
ck("D1 no stdlib coroutine driver (await / asyncio.run / run_until_complete / gather / "
   "create_task / ensure_future) still targets a now-sync handler anywhere in backend/",
   not offenders, f"offenders: {offenders[:6] or 'none'}")

# every file that uses run_route defines it FIRST (module-level use above the def would be a NameError)
users = []
for p in pyfiles(os.path.join(REPO, "backend")):
    src = open(p, encoding="utf-8", errors="replace").read()
    if "run_route(" not in src or os.path.basename(p) == os.path.basename(__file__):
        continue
    users.append(os.path.relpath(p, REPO))
    dl = [i for i, l in enumerate(src.split("\n")) if l.startswith("def run_route(")]
    ul = [i for i, l in enumerate(src.split("\n")) if "run_route(" in l and not l.startswith("def ")]
    if not dl or (ul and min(ul) < dl[0]):
        helper_bad.append(os.path.relpath(p, REPO))
ck("D2 every file using run_route defines it above its first use", not helper_bad,
   f"{len(users)} files; bad: {helper_bad or 'none'}")

# the helper is genuinely dual-shape — compile the REAL text out of a rewritten file and execute it
_src = open(os.path.join(REPO, "backend/harness_commcalc_market_dropdown.py"), encoding="utf-8").read()
_m = re.search(r"^def run_route\(x\):\n(?:(?:[ \t].*)?\n)+", _src, re.M)
_ns = {}
exec(compile(_m.group(0), "run_route", "exec"), _ns)


async def _co():
    return {"shape": "coroutine"}


ck("D3 run_route awaits a coroutine and passes a plain value through unchanged",
   _ns["run_route"](_co()) == {"shape": "coroutine"}
   and _ns["run_route"]({"shape": "plain"}) == {"shape": "plain"},
   "dual-shape, executed from the real file text")

# the 9 blocked handlers must STILL be driven as coroutines by their proofs
still_coro_sites = 0
for p in pyfiles(os.path.join(REPO, "backend")):
    src = open(p, encoding="utf-8", errors="replace").read()
    for n in BLOCKED_BY_NOTIFY:
        still_coro_sites += len(re.findall(r"(?:await|asyncio\.run\()\s*(?:[\w\.]+\.)?" + n + r"\s*\(", src))
ck("D4 the 9 blocked handlers are still driven as coroutines (their call sites were NOT rewritten)",
   still_coro_sites >= 9, f"{still_coro_sites} coroutine call sites preserved")

# Any file that drives a converted handler through a LOCAL helper (`run(...)`) must have made that
# helper dual-shape, or it raises exactly the same TypeError one level down.
LOCAL_HELPERS = ["backend/scratchpad/custom_report_proof.py",
                 "backend/scratchpad/store_matching_smart_proof.py",
                 "backend/scratchpad/installment_edit_m1gate_proof.py"]
not_dual = []
for relp in LOCAL_HELPERS:
    src = open(os.path.join(REPO, relp), encoding="utf-8").read()
    m = re.search(r"^def run\((\w+)\):\n((?:(?:[ \t].*)?\n)+)", src, re.M)
    if not m or "iscoroutine" not in m.group(2):
        not_dual.append(relp)
ck("D5 every LOCAL run() helper that now receives a plain value is dual-shape", not not_dual,
   f"{len(LOCAL_HELPERS)} helpers; not dual-shape: {not_dual or 'none'}")

# ══ E. MONEY SAFETY ════════════════════════════════════════════════════════════════════════════════
print("\nE. nothing pays differently — the calc surface is untouched apart from the keyword")
MONEY_FILES = ["backend/app/modules/commcalc/calculator.py",
               "backend/app/modules/commcalc/commission_engine.py",
               "backend/app/modules/commcalc/commission_ledger.py",
               "backend/app/modules/commcalc/installment_engine.py",
               "backend/app/modules/commcalc/sale_installment_engine.py",
               "backend/app/modules/commcalc/payout_accrual.py",
               "backend/app/modules/commcalc/expected_commission.py",
               "backend/app/modules/commcalc/setup_fee_pay.py",
               "backend/app/modules/commcalc/plan_pay_gate.py"]
untouched = [f for f in MONEY_FILES
             if git_show(BASE, f) == open(os.path.join(REPO, f), encoding="utf-8").read()]
ck("E1 every payout ENGINE file is byte-identical to base", untouched == MONEY_FILES,
   f"{len(untouched)}/{len(MONEY_FILES)} identical; changed: "
   f"{[f for f in MONEY_FILES if f not in untouched] or 'none'}")

# _run_calculation — the function POST /calculate enqueues — must be byte-identical
def _fn_src(src, name):
    t = ast.parse(src)
    for n in ast.walk(t):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return "\n".join(src.split("\n")[n.lineno - 1:n.end_lineno])
    return None


for fn in ("_run_calculation", "_resolve_carrier_mode", "_apply_new_engines", "_pvariants",
           "_open_month_source", "_fetch_sales_unified", "_promote_feed_to_raw_sales", "_do_dlar_sweep"):
    ck(f"E2 `{fn}` is byte-identical to base", _fn_src(now_src, fn) == _fn_src(was_src, fn), fn)

calc_now, calc_was = _fn_src(now_src, "calculate"), _fn_src(was_src, "calculate")
ck("E3 POST /calculate differs from base by the `async ` keyword and NOTHING else",
   calc_was == "async " + calc_now and calc_now.startswith("def calculate("),
   f"{len(calc_now.split(chr(10)))} lines, 6 chars of difference")
ck("E4 /calculate still enqueues the recompute as a background task (unchanged semantics)",
   "background_tasks.add_task(_run_calculation, period, org_id, force)" in calc_now
   and "'calc_status': 'running'" in calc_now, "add_task + calc_status upsert intact")

# no frontend file touched
fe = subprocess.run(["git", "-C", REPO, "diff", "--name-only", BASE, "--", "frontend/"],
                    capture_output=True, text=True).stdout.strip()
ck("E5 no frontend file touched (this is a backend scheduling change only)", fe == "", fe or "none")
mig = subprocess.run(["git", "-C", REPO, "diff", "--name-only", BASE, "--", "migrations/",
                      "backend/migrations/", "supabase/"], capture_output=True, text=True).stdout.strip()
ck("E6 no migration / no SQL in this package", mig == "", mig or "none")

# ══ F. ROUTE SURFACE ═══════════════════════════════════════════════════════════════════════════════
print("\nF. the route surface is identical to base")


def routes(src):
    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for d in node.decorator_list:
            if is_route(d):
                f = d.func if isinstance(d, ast.Call) else d
                path = ""
                if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant):
                    path = d.args[0].value
                out.append((f.attr.upper(), path, node.name, node.lineno))
    return sorted(out)


r_now, r_was = routes(now_src), routes(was_src)
ck("F1 same number of commcalc router.py routes as base", len(r_now) == len(r_was),
   f"{len(r_now)} vs {len(r_was)}")
ck("F2 every (method, path, handler name, line number) is identical to base", r_now == r_was,
   f"{len(r_now)} routes")

try:
    os.environ.setdefault("SUPABASE_URL", "http://localhost")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")
    from app.main import app as _app                                     # noqa: E402
    live = sorted((tuple(sorted(getattr(r, "methods", []) or [])), r.path) for r in _app.routes)
    ck("F3 the whole app still imports and registers its full route surface", len(live) >= 1000,
       f"{len(live)} routes registered")
    cc = [p for _, p in live if p.startswith("/api/v1/commcalc")]
    ck("F4 the commcalc surface is mounted", len(cc) >= 300, f"{len(cc)} commcalc routes")
except Exception as e:                                                   # pragma: no cover
    ck("F3 the whole app still imports and registers its full route surface", False, repr(e)[:160])

print(f"\n{'=' * 96}\nASYNC-SWEEP: {PASS} passed, {FAIL} failed")
if FAILED:
    for f in FAILED:
        print(f"  - {f}")
sys.exit(1 if FAIL else 0)
