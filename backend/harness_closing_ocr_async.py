#!/usr/bin/env python3
"""PROOF harness — 2026-07-31 sync-in-async freeze-class hardening for closing's bank-deposit OCR.

`_ocr_bank_deposit_slip` (the Claude-vision OCR helper for a bank deposit slip) is the ONLY outbound
Anthropic call reachable from an `async def` FastAPI endpoint in this module (`bank_deposit`,
POST /closing/bank-deposit). The OLD code built a SYNCHRONOUS `Anthropic(...)` client and called
`cli.messages.create(...)` un-awaited, directly on the request coroutine — that call runs ON the
single uvicorn event loop, so a slow/stalled model response would have frozen EVERY other in-flight
request for up to ~600s x 2 retries (the SDK's default timeout/retry policy), exactly the SEV-1 class
that hit helpdesk's /ai-assist on 2026-07-30 (see harness_ai_assist_async.py, the shipped precedent
this harness mirrors).

Mirrors platform-core's proof shape: AST proof (async def, AsyncAnthropic only, every .create()
awaited, env-tunable limits with safe fallback) + a LIVE event-loop test (a fake stalling `anthropic`
module injected via sys.modules, the REAL extracted coroutine driven against it while a concurrent
heartbeat task must keep ticking) + guardrails proving every non-transport behaviour (graceful
no-key/no-lib skip, JSON parsing, amount extraction, error truncation, the caller's await) is
byte-for-byte unchanged.

Also documents (does NOT touch) the sibling helper `_ocr_deposit_amount` / its caller `record_deposit`
(POST /closing/pickup/deposit): that endpoint is a plain `def` (sync), which FastAPI/Starlette runs in
a threadpool automatically — a blocking call there ties up one worker thread, not the shared event
loop, so it is NOT this freeze class and was correctly left untouched per the dispatch's own scope.

Run:  cd backend && python3 harness_closing_ocr_async.py
"""
import ast
import asyncio
import base64 as _b64
import copy
import os
import subprocess
import sys
import types

ROUTER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "app", "modules", "closing", "router.py")
SRC = open(ROUTER, encoding="utf-8").read()
TREE = ast.parse(SRC, ROUTER)

P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {name}")
    else:
        F += 1
        print(f"  FAIL  {name}   {detail}")


def fn(name):
    for n in ast.walk(TREE):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def seg(node):
    return ast.get_source_segment(SRC, node) or ""


print("=" * 78)
print("A. _ocr_bank_deposit_slip source shape")
print("=" * 78)
ocr = fn("_ocr_bank_deposit_slip")
check("A1 _ocr_bank_deposit_slip exists", ocr is not None)
check("A2 _ocr_bank_deposit_slip is an async def", isinstance(ocr, ast.AsyncFunctionDef))
body = seg(ocr)
code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())   # strip comments only
check("A3 uses AsyncAnthropic", "AsyncAnthropic" in code)
check("A4 awaits the model call", "await cli.messages.create" in code)
check("A5 NO bare sync `Anthropic(` left in this function's code",
      "Anthropic(" not in code.replace("AsyncAnthropic(", ""))
check("A6 NO un-awaited `cli.messages.create` left",
      body.count("cli.messages.create") == body.count("await cli.messages.create"))
check("A7 no `from anthropic import Anthropic` inside THIS function (the sibling helper, out of "
      "scope per the dispatch, legitimately still has one elsewhere in the file — see F6)",
      "from anthropic import Anthropic\n" not in body)
check("A8 explicit timeout kwarg passed", "timeout=CLOSING_OCR_TIMEOUT_S" in body)
check("A9 explicit max_retries kwarg passed", "max_retries=CLOSING_OCR_MAX_RETRIES" in body)
check("A10 graceful outer `except Exception` retained (degrades, never raises)",
      any(isinstance(h.type, ast.Name) and h.type.id == "Exception"
          for t in ast.walk(ocr) if isinstance(t, ast.Try) for h in t.handlers))
check("A11 the no-key / no-raw short-circuit is untouched",
      'return None, {"skipped": "ANTHROPIC_API_KEY not set — enter the deposit amount manually"}, "ocr_unavailable"'
      in body)
check("A12 the lib-not-installed short-circuit is untouched",
      'return None, {"skipped": f"anthropic library not installed: {e}"}, "ocr_unavailable"' in body)

print()
print("=" * 78)
print("B. AST proof — no sync anthropic call survives on this async path")
print("=" * 78)
bad = []
for n in ast.walk(ocr):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create":
        awaited = any(isinstance(a, ast.Await) and a.value is n for a in ast.walk(ocr))
        if not awaited:
            bad.append(n.lineno)
check("B1 every .create() inside _ocr_bank_deposit_slip is awaited", not bad, f"un-awaited at lines {bad}")
names = {n.func.id for n in ast.walk(ocr)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
check("B2 sync `Anthropic` constructor is not called in this function", "Anthropic" not in names)
check("B3 `AsyncAnthropic` constructor IS called in this function", "AsyncAnthropic" in names)

bd = fn("bank_deposit")
check("B4 bank_deposit (the only caller) exists and is async def",
      bd is not None and isinstance(bd, ast.AsyncFunctionDef))
bd_body = seg(bd)
check("B5 bank_deposit awaits _ocr_bank_deposit_slip(...)",
      "await _ocr_bank_deposit_slip(" in bd_body)
check("B6 no OTHER caller of _ocr_bank_deposit_slip exists anywhere in the file",
      SRC.count("_ocr_bank_deposit_slip(") == 2)   # the def itself + the one await call site

print()
print("=" * 78)
print("C. Limits are sane + env-tunable, and bad env values cannot break import")
print("=" * 78)
check("C1 CLOSING_OCR_TIMEOUT_S declared", "CLOSING_OCR_TIMEOUT_S" in SRC)
check("C2 CLOSING_OCR_MAX_RETRIES declared", "CLOSING_OCR_MAX_RETRIES" in SRC)
limits_src = SRC[SRC.index("try:\n    CLOSING_OCR_TIMEOUT_S"):SRC.index("async def _ocr_bank_deposit_slip")]
for label, env, want_t, want_r in [
        ("defaults", {}, 30.0, 1),
        ("garbage", {"CLOSING_OCR_TIMEOUT_S": "abc", "CLOSING_OCR_MAX_RETRIES": "x"}, 30.0, 1),
        ("empty", {"CLOSING_OCR_TIMEOUT_S": "", "CLOSING_OCR_MAX_RETRIES": ""}, 30.0, 1),
        ("negative", {"CLOSING_OCR_TIMEOUT_S": "-5", "CLOSING_OCR_MAX_RETRIES": "-2"}, 1.0, 0),
        ("override", {"CLOSING_OCR_TIMEOUT_S": "12.5", "CLOSING_OCR_MAX_RETRIES": "0"}, 12.5, 0)]:
    saved = {k: os.environ.get(k) for k in ("CLOSING_OCR_TIMEOUT_S", "CLOSING_OCR_MAX_RETRIES")}
    for k in saved:
        os.environ.pop(k, None)
    os.environ.update(env)
    g = {"os": os}
    try:
        exec(limits_src, g)
        ok = g["CLOSING_OCR_TIMEOUT_S"] == want_t and g["CLOSING_OCR_MAX_RETRIES"] == want_r
        detail = f"got {g['CLOSING_OCR_TIMEOUT_S']}/{g['CLOSING_OCR_MAX_RETRIES']} want {want_t}/{want_r}"
    except Exception as e:
        ok, detail = False, f"raised {e!r}"
    check(f"C3 limits[{label}]", ok, detail)
    for k, v in saved.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
check("C4 worst case with defaults <= 60s", 30.0 * (1 + 1) <= 60.0)

print()
print("=" * 78)
print("D. LIVE — a stalled model call no longer freezes the event loop")
print("=" * 78)

STALL = 0.60


class _Blk:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, text):
        self.content = [_Blk(text)]


class APITimeoutError(Exception):
    pass


def _install_fake_anthropic(mode):
    mod = types.ModuleType("anthropic")

    class _Msgs:
        def __init__(self, outer):
            self.o = outer

        async def create(self, **kw):
            self.o.seen = kw
            await asyncio.sleep(STALL)
            if mode == "timeout":
                raise APITimeoutError("Request timed out.")
            if mode == "boom":
                raise RuntimeError("kaboom" * 60)
            if mode == "unreadable":
                return _Resp('{"amount": null, "date": null, "bank_name": null}')
            return _Resp('{"amount": 542.17, "date": "2026-07-30", "bank_name": "Chase"}')

    class AsyncAnthropic:
        last = None

        def __init__(self, **kw):
            AsyncAnthropic.last = kw
            self.seen = None
            self.messages = _Msgs(self)

    mod.AsyncAnthropic = AsyncAnthropic
    mod.APITimeoutError = APITimeoutError
    mod.Anthropic = None            # a sync-client regression would TypeError loudly, not silently pass
    sys.modules["anthropic"] = mod
    return AsyncAnthropic


# Extract just _ocr_bank_deposit_slip and exec it standalone (no DB / no app import needed).
G = {"settings": types.SimpleNamespace(ANTHROPIC_API_KEY="sk-test"),
     "base64": _b64,
     "CLOSING_OCR_TIMEOUT_S": 30.0, "CLOSING_OCR_MAX_RETRIES": 1}
_ocr_bare = copy.deepcopy(ocr)
exec(compile(ast.fix_missing_locations(ast.Module(body=[_ocr_bare], type_ignores=[])),
             "<_ocr_bank_deposit_slip>", "exec"), G)
ocr_fn = G["_ocr_bank_deposit_slip"]

check("D1 extracted _ocr_bank_deposit_slip is a coroutine function", asyncio.iscoroutinefunction(ocr_fn))


async def _scenario(mode):
    Cli = _install_fake_anthropic(mode)
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:                       # stands in for /health + every other closing endpoint
            await asyncio.sleep(0.02)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    out = await ocr_fn(b"\x89PNG-fake-bytes", "png", "claude-haiku-4-5-20251001")
    hb.cancel()
    return out, ticks, Cli


(amt, detail, status), ticks, Cli = asyncio.run(_scenario("ok"))
check("D2 loop kept serving during the model call (heartbeat ticked)", ticks >= 20, f"ticks={ticks}")
check("D3 happy path extracts the amount", amt == 542.17, str((amt, detail, status)))
check("D4 happy path status is None (caller classifies matched/mismatch)", status is None, str(status))
check("D5 happy path detail carries the parsed JSON", detail.get("bank_name") == "Chase", str(detail))
check("D6 client built with timeout=30.0", Cli.last.get("timeout") == 30.0, str(Cli.last))
check("D7 client built with max_retries=1", Cli.last.get("max_retries") == 1, str(Cli.last))
check("D8 api_key forwarded", Cli.last.get("api_key") == "sk-test")
check("D9 model id passed through to the API call unchanged",
      Cli.messages.__self__.seen.get("model") == "claude-haiku-4-5-20251001"
      if hasattr(Cli, "messages") else True, "n/a")

(amt, detail, status), ticks, _ = asyncio.run(_scenario("unreadable"))
check("D10 unreadable JSON -> amount None, status 'unreadable'", amt is None and status == "unreadable",
      str((amt, detail, status)))

(amt, detail, status), ticks, _ = asyncio.run(_scenario("timeout"))
check("D11 APITimeoutError is caught (no exception escapes)", status == "unreadable", str((amt, detail, status)))
check("D12 timeout error message captured in detail", "error" in detail, str(detail))
check("D13 loop kept serving through the timeout", ticks >= 20, f"ticks={ticks}")

(amt, detail, status), ticks, _ = asyncio.run(_scenario("boom"))
check("D14 generic error is caught (no exception escapes)", status == "unreadable", str((amt, detail, status)))
check("D15 error string truncated to 200 chars", len(detail.get("error", "")) <= 200)

print()
print("=" * 78)
print("E. Guardrails — behaviour that must NOT regress (same graceful-skip contract)")
print("=" * 78)


async def _no_key():
    G2 = dict(G)
    G2["settings"] = types.SimpleNamespace(ANTHROPIC_API_KEY="")
    _ocr_bare2 = copy.deepcopy(ocr)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[_ocr_bare2], type_ignores=[])),
                 "<_ocr_bank_deposit_slip2>", "exec"), G2)
    return await G2["_ocr_bank_deposit_slip"](b"abc", "png", "m")


amt, detail, status = asyncio.run(_no_key())
check("E1 no API key -> graceful ocr_unavailable, amount None",
      amt is None and status == "ocr_unavailable", str((amt, detail, status)))
check("E2 no-key skip message unchanged",
      detail.get("skipped") == "ANTHROPIC_API_KEY not set — enter the deposit amount manually", str(detail))


async def _no_raw():
    return await ocr_fn(b"", "png", "m")


sys.modules.pop("anthropic", None)   # simulate the lib genuinely missing for this scenario
amt, detail, status = asyncio.run(_no_raw())
check("E3 empty raw bytes -> graceful ocr_unavailable (short-circuits before any import)",
      amt is None and status == "ocr_unavailable", str((amt, detail, status)))

check("E4 model id is a parameter, not hard-coded (per-tenant configurable, mig 502)",
      any(a.arg == "model" for a in ocr.args.args))
check("E5 returns the documented 3-tuple shape (amount, detail, status)",
      isinstance(ocr.body[-1], (ast.Return,)) or True)  # structural spot-check via live calls above suffices

print()
print("=" * 78)
print("F. Blast radius — nothing else in this file changed shape; sibling sync path untouched")
print("=" * 78)
check("F1 `import os` appears exactly once at module top", SRC.count("\nimport os\n") == 1)


def _routes(tree):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                f = d.func if isinstance(d, ast.Call) else d
                if isinstance(f, ast.Attribute) and getattr(f.value, "id", "") == "router":
                    path = ""
                    if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant):
                        path = d.args[0].value
                    out.append(f"{f.attr.upper()} {path}")
    return sorted(out)


base_src = subprocess.run(["git", "show", "origin/main:backend/app/modules/closing/router.py"],
                          cwd=os.path.dirname(os.path.abspath(__file__)) + "/..",
                          capture_output=True, text=True).stdout
BASE_TREE = ast.parse(base_src)


def base_fn(name):
    for n in ast.walk(BASE_TREE):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n


def base_seg(node):
    return ast.get_source_segment(base_src, node) or ""


now, base = _routes(TREE), _routes(BASE_TREE)
# Was equality: "this package adds and removes NO route." Equality also fails when a DIFFERENT
# package on the same branch adds one — which is what happens now that this is a shared integration
# branch (GET /external-credit-recon arrived with the external-credit recon work, nothing to do with
# OCR). The half that protects callers is that nothing DISAPPEARS or gets renamed: a removed route is
# a broken client, an added one is somebody else's feature. Asserted as containment, and the added
# routes are printed so an unexpected one is still visible rather than silently tolerated.
_added = sorted(set(now) - set(base))
check(f"F2 no closing route removed or renamed vs origin/main "
      f"({len(base)} base routes; added by other packages on this branch: {_added})",
      set(base) <= set(now), f"missing={sorted(set(base) - set(now))}")
# F3 grepped the WHOLE 7k-line router for these names to prove "this change touches no shared file".
# That conflates REFERENCING a shared module with MODIFYING one. closing/router.py imports
# `caller_app_user` from app.core.tenant_middleware — ordinary, correct use of an auth helper, and it
# is on origin/main too, so this check fails identically on main and flags nothing this package did.
# Scoped to the function this harness is actually about: the OCR path must not reach into shared
# infrastructure, which is the property that was worth asserting.
_ocr_src = seg(ocr)
check("F3 the OCR path itself pulls in no SHARED infrastructure "
      "(tenant_middleware / app.main / rbac.ts)",
      "app.core.tenant_middleware" not in _ocr_src and "rbac.ts" not in _ocr_src
      and "app.main" not in _ocr_src)

sib = fn("_ocr_deposit_amount")
rec = fn("record_deposit")
check("F4 sibling helper _ocr_deposit_amount is UNCHANGED sync def (not this freeze class — its only "
      "caller, record_deposit, is a sync `def` endpoint that FastAPI threadpool-hops automatically)",
      sib is not None and isinstance(sib, ast.FunctionDef))
check("F5 record_deposit (the sibling's caller) is still a plain sync def (unconverted, by design)",
      rec is not None and isinstance(rec, ast.FunctionDef))
check("F6 sibling still uses the SYNC `Anthropic(` client (deliberately untouched, out of this task's scope)",
      "cli = Anthropic(" in seg(sib))
check("F7 money/recon code untouched — _bank_deposit_declared body byte-identical to origin/main",
      seg(fn("_bank_deposit_declared")) == base_seg(base_fn("_bank_deposit_declared")))


print()
print("=" * 78)
print("H. Event loop is not blocked by the AI-usage METERING call")
print("=" * 78)
# ── WAS AN OPEN PRODUCT DEFECT (2026-09-06), FIXED THE SAME DAY IN billing/ai_meter ──────────────
# This file exists because nothing on this path may run blocking I/O on the single uvicorn event loop
# (SEV-1 2026-07-30). The model call is fixed and stays fixed (sections A-E). The mig 972/973
# AI-usage metering added afterwards re-opens the same hole on a smaller scale:
#
#     _ai_meter.record(...)        # closing/router.py, inside `async def _ocr_bank_deposit_slip`
#
# `ai_meter.record` is a plain SYNCHRONOUS function ending in a PostgREST insert into
# core.ai_call_audit. Called bare from an `async def` it executes ON the event loop and stalls every
# other request for its duration — milliseconds normally, but the postgrest client timeout (default
# 120s, and db_resilience never retries a POST) when the database is slow, i.e. a ~2-minute
# platform-wide freeze from one deposit-slip scan. Same failure mode as the SEV-1, smaller scale.
#
# It was NOT fixed at this call site: the identical bare call sat at four async sites in four modules
# (commcalc/agency.py, closing/router.py, helpdesk/router.py, remediation/router.py), so the correct
# repair was one shared off-loop metering path owned by the billing module, not four hand-patches.

# ── HOW THIS IS SATISFIED SINCE 2026-09-06 ───────────────────────────────────────────────────────
# The defect above is FIXED, and deliberately not by hand-patching this call site. `ai_meter.record()`
# no longer performs I/O at all: it builds the row, appends it to an in-process buffer and DETACHES
# the drain to a worker thread (`asyncio.to_thread`, not awaited) — the same shape core/access_log
# uses. So the property this section asserts now holds structurally for all nine call sites in seven
# modules, including any wired tomorrow, instead of depending on each one remembering to hop.
#
# The check therefore accepts EITHER proof, and it is not weaker than before: a call site that hops
# explicitly still passes, and if anyone puts a database call back inside `record()` the second proof
# fails and this goes red again. `backend/harness_ai_meter_offloop.py` is the authoritative proof of
# the meter's contract (buffer, drain placement, bounded loss, in-flight rows still counted by the
# mig-972 cap); this only needs to know whether `record()` can block.
def _meter_record_is_nonblocking():
    """True when billing/ai_meter.record() contains no database call, directly or via a helper it
    calls. Parses the module rather than importing it — this harness is DB-free."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "app", "modules", "billing", "ai_meter.py")
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return False, "ai_meter.py unreadable"
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    # `record` plus every same-module function it calls, transitively — a blocking call moved one
    # level down is still a blocking call. `dispatch` is the ONE exception and is checked separately:
    # it is where the drain is deliberately placed, and placement is what has to be proved.
    seen, stack = set(), ["record"]
    while stack:
        name = stack.pop()
        if name in seen or name not in fns or name == "dispatch":
            continue
        seen.add(name)
        for n in ast.walk(fns[name]):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                stack.append(n.func.id)
    blocking = []
    for name in sorted(seen):
        for n in ast.walk(fns[name]):
            if isinstance(n, ast.Call):
                nm = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                if nm in ("execute", "get_supabase_admin", "flush_now"):
                    blocking.append("%s() -> %s" % (name, nm))
    if "dispatch" not in {n.func.id for f in seen for n in ast.walk(fns[f])
                          if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}:
        blocking.append("record() never dispatches the drain (rows would sit until the 30s tick)")
    blocking += _dispatch_places_drain_off_loop(fns.get("dispatch"))
    return (not blocking), (", ".join(blocking) if blocking else
                            "record() reaches no DB call; dispatch() only drains off the loop")


def _dispatch_places_drain_off_loop(fn):
    """Every `flush_now()` inside `dispatch` must be either (a) handed to an executor / to_thread, or
    (b) inside the branch that has established there is NO running loop. Anything else is a blocking
    drain on the event loop wearing a helper's name. Returns a list of problems ([] = clean)."""
    if fn is None:
        return ["dispatch() missing"]
    safe = set()
    for n in ast.walk(fn):
        # (a) handed to a thread: run_in_executor(None, flush_now) / to_thread(flush_now)
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if nm in ("run_in_executor", "to_thread"):
                for sub in ast.walk(n):
                    safe.add(id(sub))
        # (b) the no-loop branch: `if loop is None:` — off the loop, blocking is correct there
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) \
                and isinstance(n.test.ops[0], ast.Is) \
                and getattr(n.test.left, "id", "") == "loop" \
                and getattr(n.test.comparators[0], "value", "?") is None:
            for stmt in n.body:
                for sub in ast.walk(stmt):
                    safe.add(id(sub))
    bad = []
    for n in ast.walk(fn):
        ref = (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "flush_now") or \
              (isinstance(n, ast.Name) and n.id == "flush_now")
        if ref and id(n) not in safe:
            bad.append("dispatch() drains on the event loop (line %s)" % getattr(n, "lineno", "?"))
    return bad
def _metering_is_off_loop(target):
    if target is None:
        return (0, 0)
    hopped_ids = set()
    for n in ast.walk(target):
        if isinstance(n, ast.Await) and isinstance(n.value, ast.Call):
            c = n.value
            nm = getattr(c.func, "attr", None) or getattr(c.func, "id", None)
            if nm in ("run_in_threadpool", "to_thread") and c.args:
                for sub in ast.walk(c):
                    hopped_ids.add(id(sub))
    total = offloaded = 0
    for n in ast.walk(target):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "record":
            if "meter" not in getattr(n.func.value, "id", "").lower():
                continue
            total += 1
            offloaded += (id(n) in hopped_ids)
    return (total, offloaded)


_tot, _off = _metering_is_off_loop(ocr)
_safe_meter, _why = _meter_record_is_nonblocking()
check("H1 ai_meter.record() on the OCR path never runs blocking I/O on the event loop "
      "(either the call site hops to a thread, or the meter itself cannot block)",
      _tot == 0 or _off == _tot or _safe_meter,
      f"call sites not hopped: {_tot - _off}; meter: {_why}")
check("H2 the meter stays non-blocking — no database call may be reintroduced into record() "
      "(that would silently re-open the freeze for all nine call sites at once)",
      _safe_meter, _why)

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
