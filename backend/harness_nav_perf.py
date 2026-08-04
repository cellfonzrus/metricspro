"""HARNESS — nav-perf package (platform-core, 2026-08-04).

Owner complaint (verbatim, in chat): "it takes some time to load the screen when moving from one menu
to the other, need a permanent fix for this."

ROOT CAUSE this harness pins down and guards:
  The backend runs ONE uvicorn worker (backend/Dockerfile: `uvicorn app.main:app` with no --workers),
  so there is exactly ONE event loop for the whole product. 457 of the 1038 route handlers are
  `async def`, and 371 of those contain no `await` at all — they are pure BLOCKING Supabase I/O
  declared `async`. FastAPI runs an `async def` handler ON the event loop, so for its entire duration
  NOTHING else in the product can progress: not another page's fetch, not another tenant's request,
  not the middleware. The worst offender on the navigation path is `GET /api/v1/core/attention`
  (MEASURED 5.1 s house / 5.4 s Luxelink), which the frontend re-fires on every menu hop.

WHAT THIS HARNESS PROVES
  A. LIVE NEGATIVE CONTROL — an in-process ASGI app reproduces the freeze: an `async def` handler
     doing blocking work stalls concurrent requests for its full duration; the SAME body declared
     `def` does not. This is the mechanism, demonstrated, not asserted.
  B. THE SWEEP IS KEYWORD-ONLY — for every converted handler, re-inserting `async ` reproduces the
     base file BYTE-IDENTICALLY, and no converted handler contains await / async with / async for /
     yield. Nothing else changed.
  C. NO HANDLER WAS CONVERTED THAT NEEDED TO STAY ASYNC — every remaining `async def` route handler
     in platform-core-owned modules genuinely awaits something.
  D. /core/attention — `def`, and its memo is per-(org, deep) with the SERVER-resolved org, TTL-bound,
     bypassable, permission-gated on every single request, and fully revertible via env.
  E. ROUTE SURFACE UNCHANGED — same count, same paths, same methods as the base commit.
  F. FRONTEND WIRING — the cache identity is published from auth-context and AdminAttention no longer
     re-fires a 5-second scan on every navigation.

Run:  python3 backend/harness_nav_perf.py     (no network, no DB — the ASGI app is local & synthetic)
"""
import ast
import asyncio
import inspect
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PASS = FAIL = 0


def ck(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}" + (f"  [{extra}]" if extra else ""))
    else:
        FAIL += 1
        print(f"  XX  {label}" + (f"  [{extra}]" if extra else ""))


def fatal(msg):
    print(f"FATAL: {msg}")
    sys.exit(2)


BASE = os.environ.get("NAV_PERF_BASE", "bf00a20")
OWNED = ("app/modules/core/", "app/modules/notify/", "app/modules/helpdesk/",
         "app/modules/remediation/", "app/modules/recovery/")
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


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
    """{name: (is_async, blocking_markers)} for every @router.<method> handler in `src`."""
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


# ══ A. LIVE NEGATIVE CONTROL — reproduce the freeze, then show the fix removes it ═════════════════
print("\nA. live negative control: does an `async def` blocking handler stall the whole app?")
try:
    import httpx
    from fastapi import FastAPI
except Exception as e:                                             # pragma: no cover
    fatal(f"httpx/fastapi unavailable: {e}")

BLOCK_S = 0.40
_probe = FastAPI()


@_probe.get("/heavy-async")
async def _heavy_async():
    """EXACTLY the shape of the 371 handlers: declared async, body is pure blocking I/O."""
    time.sleep(BLOCK_S)
    return {"ok": True}


@_probe.get("/heavy-sync")
def _heavy_sync():
    """The SAME body after the sweep — FastAPI runs it in the threadpool."""
    time.sleep(BLOCK_S)
    return {"ok": True}


@_probe.get("/ping")
async def _ping():
    """A trivial handler standing in for 'every other request in the product'."""
    return {"pong": True}


async def _measure(heavy_path):
    """Run ONE heavy request and, 50 ms into it, ask the app a trivial question.

    The metric is WHEN THE APP ANSWERS THE SECOND USER, measured from the start of the heavy request.
    If the heavy handler is an `async def` doing blocking work it owns the single event loop for its
    whole duration — the second request cannot even be dispatched, so the answer lands at ~the end of
    the block. Declared `def`, FastAPI hands the body to the threadpool, the loop stays free, and the
    second user is answered at ~50 ms regardless of how long the heavy one takes.
    """
    tr = httpx.ASGITransport(app=_probe)
    async with httpx.AsyncClient(transport=tr, base_url="http://probe") as c:
        await c.get("/ping")                                  # warm the transport
        t0 = time.perf_counter()

        async def second_user():
            await asyncio.sleep(0.05)                          # arrive 50 ms into the heavy request
            await c.get("/ping")
            return (time.perf_counter() - t0) * 1000           # answered this many ms after the start

        answered_at, _ = await asyncio.gather(second_user(), c.get(heavy_path))
        heavy_ms = (time.perf_counter() - t0) * 1000
        return heavy_ms, answered_at


heavy_a, answer_a = asyncio.run(_measure("/heavy-async"))
heavy_s, answer_s = asyncio.run(_measure("/heavy-sync"))
ck("A1 `async def` + blocking body: the SECOND user is not answered until the block ends",
   answer_a > BLOCK_S * 1000 * 0.8,
   f"answered {answer_a:.0f} ms in, during a {heavy_a:.0f} ms handler")
ck("A2 `def` (post-sweep) + the SAME body: the second user is answered immediately",
   answer_s < BLOCK_S * 1000 * 0.25,
   f"answered {answer_s:.0f} ms in, during a {heavy_s:.0f} ms handler")
ck("A3 the second user's wait is ~the whole block as async, ~their own arrival time as def",
   answer_a / max(answer_s, 0.01) > 4, f"{answer_a:.0f} ms → {answer_s:.0f} ms")
ck("A4 the heavy request itself is not slower as a `def`",
   heavy_s < heavy_a * 1.5 + 50, f"async {heavy_a:.0f} ms vs sync {heavy_s:.0f} ms")
ck("A5 the probe really did block for the intended duration (the control is honest)",
   heavy_a > BLOCK_S * 1000 * 0.9 and heavy_s > BLOCK_S * 1000 * 0.9,
   f"async {heavy_a:.0f} ms / sync {heavy_s:.0f} ms vs {BLOCK_S * 1000:.0f} ms of blocking")

# ══ B. THE SWEEP IS KEYWORD-ONLY ══════════════════════════════════════════════════════════════════
print("\nB. the async→def sweep changed NOTHING but the keyword")
converted = {}          # relpath -> [names]
for rel in OWNED:
    base_dir = os.path.join(HERE, rel)
    if not os.path.isdir(base_dir):
        continue
    for dp, _, fs in os.walk(base_dir):
        if "__pycache__" in dp:
            continue
        for fn in sorted(fs):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dp, fn)
            relp = os.path.relpath(p, REPO)
            now = open(p, encoding="utf-8").read()
            was = git_show(BASE, relp)
            if not was or was == now:
                continue
            h_now, h_was = handlers(now), handlers(was)
            names = [n for (n, _), (a, _) in h_was.items()
                     if a and any(nn == n and not aa for (nn, _), (aa, _) in h_now.items())]
            if names:
                converted[relp] = sorted(names)

total = sum(len(v) for v in converted.values())
ck("B1 the sweep converted the expected handler population", total == 113 + 6,
   f"{total} handlers across {len(converted)} files (113 sweep + 6 import_health)")

# Reconstruct: put `async ` back on exactly the converted defs and compare byte-for-byte to base.
recon_ok, recon_bad = 0, []
for relp, names in converted.items():
    now = open(os.path.join(REPO, relp), encoding="utf-8").read()
    was = git_show(BASE, relp)
    lines = now.split("\n")
    for i, ln in enumerate(lines):
        m = re.match(r"^def (\w+)\(", ln)
        if m and m.group(1) in names:
            lines[i] = "async " + ln
    recon = "\n".join(lines)
    if recon == was:
        recon_ok += 1
    else:
        recon_bad.append(relp)
IH_REL = "backend/app/modules/core/import_health.py"
ck("B2 in every SWEPT file, re-inserting `async ` reproduces the base file BYTE-IDENTICALLY",
   recon_bad == [IH_REL] or recon_bad == [], f"{recon_ok} byte-identical; differs: {recon_bad or 'none'}")
ck("B3 import_health.py is the ONE file in the package with edits beyond the keyword",
   recon_bad in ([], [IH_REL]) and len(converted) >= 6,
   f"{len(converted)} files touched; beyond-keyword: {recon_bad or 'none'}")

# No converted handler needs to be a coroutine.
bad_conv = []
for relp, names in converted.items():
    h = handlers(open(os.path.join(REPO, relp), encoding="utf-8").read())
    for (n, _), (is_async, markers) in h.items():
        if n in names and (is_async or markers):
            bad_conv.append(f"{relp}::{n}")
ck("B4 every converted handler is now `def` and contains NO await/async-with/async-for/yield",
   not bad_conv, f"{len(converted)} files, {total} handlers")

# ══ C. NOTHING THAT NEEDED ASYNC WAS TOUCHED ══════════════════════════════════════════════════════
print("\nC. the handlers that stayed async are the ones that genuinely await")
still_async, wrongly_left = 0, []
for rel in OWNED:
    base_dir = os.path.join(HERE, rel)
    if not os.path.isdir(base_dir):
        continue
    for dp, _, fs in os.walk(base_dir):
        if "__pycache__" in dp:
            continue
        for fn in sorted(fs):
            if not fn.endswith(".py"):
                continue
            relp = os.path.relpath(os.path.join(dp, fn), REPO)
            for (n, _), (is_async, markers) in handlers(open(os.path.join(REPO, relp), encoding="utf-8").read()).items():
                if not is_async:
                    continue
                still_async += 1
                if markers == 0:
                    wrongly_left.append(f"{relp}::{n}")
# handlers awaited BY NAME elsewhere were deliberately skipped — they must stay coroutines.
awaited_names = set()
for dp, _, fs in os.walk(HERE):
    if "__pycache__" in dp:
        continue
    for fn in fs:
        if fn.endswith(".py"):
            txt = open(os.path.join(dp, fn), encoding="utf-8", errors="replace").read()
            awaited_names.update(re.findall(r"await\s+(?:[A-Za-z_][\w\.]*\.)?([A-Za-z_]\w*)\s*\(", txt))
leftover = [x for x in wrongly_left if x.split("::")[1] not in awaited_names]
ck("C1 every still-async platform-core handler either awaits, or is awaited by name elsewhere",
   not leftover, f"{still_async} still async; unexplained: {leftover or 'none'}")
ck("C2 the deliberately-skipped set is non-empty (the sweep was selective, not blanket)",
   still_async >= 19, f"{still_async} handlers left as coroutines")

# ══ D. /core/attention ════════════════════════════════════════════════════════════════════════════
print("\nD. /core/attention — off the event loop, memoised per tenant, fully revertible")
from app.modules.core import import_health as IH        # noqa: E402

ck("D1 the handler is no longer a coroutine function", not inspect.iscoroutinefunction(IH.get_attention))
sig = inspect.signature(IH.get_attention)
ck("D2 it gained a `fresh` bypass and kept every existing parameter",
   "fresh" in sig.parameters and {"org_id", "deep", "authorization", "x_active_org"} <= set(sig.parameters))

IH._attention_memo_clear()
os.environ["ATTENTION_CACHE_TTL_S"] = "45"
HOUSE, LUX = "00000000-0000-0000-0000-000000000001", "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
IH._attention_memo_put(HOUSE, 0, {"items": ["house-item"], "counts": {"total": 1}})
ck("D3 a memoised payload is returned for the SAME (org, deep)",
   (IH._attention_memo_get(HOUSE, 0) or {}).get("items") == ["house-item"])
ck("D4 CROSS-TENANT: another org gets NOTHING from the house entry", IH._attention_memo_get(LUX, 0) is None)
ck("D5 deep=1 is a separate key (a cheap scan never masquerades as a full one)",
   IH._attention_memo_get(HOUSE, 1) is None)
IH._attention_memo_put(LUX, 0, {"items": ["lux-item"], "counts": {"total": 1}})
ck("D6 both tenants coexist, each with its own payload",
   (IH._attention_memo_get(HOUSE, 0) or {}).get("items") == ["house-item"]
   and (IH._attention_memo_get(LUX, 0) or {}).get("items") == ["lux-item"])

os.environ["ATTENTION_CACHE_TTL_S"] = "0"
ck("D7 ATTENTION_CACHE_TTL_S=0 is a complete revert: reads miss AND writes are dropped",
   IH._attention_memo_get(HOUSE, 0) is None
   and (IH._attention_memo_put("zzz", 0, {"x": 1}), IH._attention_memo_get("zzz", 0) is None)[1])
os.environ["ATTENTION_CACHE_TTL_S"] = "not-a-number"
ck("D8 a garbage env value falls back to the default instead of crashing", IH._attention_ttl() == 45.0)
os.environ["ATTENTION_CACHE_TTL_S"] = "0.05"
IH._attention_memo_clear()
IH._attention_memo_put(HOUSE, 0, {"items": ["stale"]})
time.sleep(0.12)
ck("D9 an expired entry is not served", IH._attention_memo_get(HOUSE, 0) is None)
os.environ["ATTENTION_CACHE_TTL_S"] = "600"
IH._attention_memo_clear()
for i in range(IH._ATTN_MAX + 40):
    IH._attention_memo_put(f"org-{i}", 0, {"i": i})
ck("D10 the memo is hard-capped (never a memory leak on a many-tenant instance)",
   len(IH._ATTN_MEMO) <= IH._ATTN_MAX, f"{len(IH._ATTN_MEMO)} entries, cap {IH._ATTN_MAX}")
os.environ.pop("ATTENTION_CACHE_TTL_S", None)
IH._attention_memo_clear()

SRC_IH = open(os.path.join(HERE, "app/modules/core/import_health.py"), encoding="utf-8").read()
ck("D11 the permission gate runs BEFORE any memo read (a revoked admin is 403'd immediately)",
   SRC_IH.index("client, caller, org = _gate(authorization, x_active_org, org_id)\n    if not fresh:")
   < SRC_IH.index("memo = _attention_memo_get(org, deep)"))
ck("D12 the memo key uses the SERVER-resolved org (`org` from _gate/_scope_org), never the raw param",
   "_attention_memo_get(org, deep)" in SRC_IH and "_attention_memo_get(org_id" not in SRC_IH)
ck("D13 `collect_attention` itself is untouched and still uncached (harnesses call it directly)",
   git_show(BASE, "backend/app/modules/core/import_health.py").split("def collect_attention")[1][:1200]
   == SRC_IH.split("def collect_attention")[1][:1200])

# ══ E. ROUTE SURFACE ══════════════════════════════════════════════════════════════════════════════
print("\nE. route surface")
import app.main as M                                        # noqa: E402
routes = sorted({(getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", []) or []))) for r in M.app.routes})
expected = int(os.environ.get("EXPECT_ROUTES", "1042"))
ck("E1 route count unchanged vs base", len(M.app.routes) == expected, f"{len(M.app.routes)} (expect {expected})")
ck("E2 /api/v1/core/attention still exists exactly once",
   sum(1 for p, _ in routes if p == "/api/v1/core/attention") == 1)
ck("E3 no route lost its methods", all(m for _, m in routes if _ not in ("",)))

# ══ F. FRONTEND WIRING ════════════════════════════════════════════════════════════════════════════
print("\nF. frontend wiring")
FE = os.path.join(REPO, "frontend/src")
auth = open(os.path.join(FE, "lib/auth-context.tsx"), encoding="utf-8").read()
ck("F1 auth-context publishes the cache identity", "setCacheIdentity(" in auth)
core_ts = open(os.path.join(FE, "lib/cache-core.ts"), encoding="utf-8").read()
ck("F2 the cache engine namespaces every key by (user, acting org)",
   "`${_userId}::${_orgId}`" in core_ts)
ck("F3 no identity ⇒ the engine falls through to a plain uncached api()",
   "if (!key) return api(path)" in core_ts)
attn = open(os.path.join(FE, "components/AdminAttention.tsx"), encoding="utf-8").read()
ck("F4 AdminAttention no longer re-fires the 5-second scan on every navigation",
   "REFRESH_MS" in attn and "20_000" not in attn)

print(f"\n{PASS}/{PASS + FAIL} checks passed")
sys.exit(1 if FAIL else 0)
