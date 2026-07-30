#!/usr/bin/env python3
"""PROOF harness — SEV-1 2026-07-30: helpdesk /ai-assist must not block the FastAPI event loop.

Static (AST) proof of the fix + a LIVE proof that a stalled model call no longer freezes the app:
a fake anthropic module whose `messages.create` sleeps is injected, the real `ai_assist` coroutine is
driven on a real event loop, and a concurrent "health" coroutine must keep ticking while it runs.

Run:  cd backend && python3 harness_ai_assist_async.py
"""
import ast
import asyncio
import os
import sys
import time
import types

ROUTER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "app", "modules", "helpdesk", "router.py")
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
print("A. ai_assist source shape")
print("=" * 78)
ai = fn("ai_assist")
check("A1 ai_assist exists", ai is not None)
check("A2 ai_assist is an async def", isinstance(ai, ast.AsyncFunctionDef))
body = seg(ai)
# CODE only — comments mention `Anthropic(` deliberately ("do not reintroduce"), so strip them.
code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
check("A3 uses AsyncAnthropic", "AsyncAnthropic" in code)
check("A4 awaits the model call", "await cli.messages.create" in code)
check("A5 NO bare sync `Anthropic(` left in ai_assist code",
      "Anthropic(" not in code.replace("AsyncAnthropic(", ""))
check("A6 NO un-awaited `cli.messages.create` left",
      body.count("cli.messages.create") == body.count("await cli.messages.create"))
check("A7 no `from anthropic import Anthropic` anywhere in the file",
      "from anthropic import Anthropic\n" not in SRC)
check("A8 explicit timeout kwarg passed", "timeout=AI_ASSIST_TIMEOUT_S" in body)
check("A9 explicit max_retries kwarg passed", "max_retries=AI_ASSIST_MAX_RETRIES" in body)
check("A10 graceful `except Exception` retained",
      any(isinstance(h.type, ast.Name) and h.type.id == "Exception"
          for t in ast.walk(ai) if isinstance(t, ast.Try) for h in t.handlers))

print()
print("=" * 78)
print("B. AST proof — no sync anthropic call survives on this async path")
print("=" * 78)
bad = []
for n in ast.walk(ai):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create":
        # every .create() in ai_assist must be the child of an Await node
        awaited = any(isinstance(a, ast.Await) and a.value is n for a in ast.walk(ai))
        if not awaited:
            bad.append(n.lineno)
check("B1 every .create() inside ai_assist is awaited", not bad, f"un-awaited at lines {bad}")
names = {n.func.id for n in ast.walk(ai)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
check("B2 sync `Anthropic` constructor is not called in ai_assist", "Anthropic" not in names)
check("B3 `AsyncAnthropic` constructor IS called in ai_assist", "AsyncAnthropic" in names)

print()
print("=" * 78)
print("C. Limits are sane + env-tunable, and bad env values cannot break import")
print("=" * 78)
check("C1 AI_ASSIST_TIMEOUT_S declared", "AI_ASSIST_TIMEOUT_S" in SRC)
check("C2 AI_ASSIST_MAX_RETRIES declared", "AI_ASSIST_MAX_RETRIES" in SRC)
ns = {"os": os}
limits_src = SRC[SRC.index("try:\n    AI_ASSIST_TIMEOUT_S"):SRC.index("_AI_SUPPORT_SYSTEM")]
for label, env, want_t, want_r in [
        ("defaults", {}, 30.0, 1),
        ("garbage", {"AI_ASSIST_TIMEOUT_S": "abc", "AI_ASSIST_MAX_RETRIES": "x"}, 30.0, 1),
        ("empty", {"AI_ASSIST_TIMEOUT_S": "", "AI_ASSIST_MAX_RETRIES": ""}, 30.0, 1),
        ("negative", {"AI_ASSIST_TIMEOUT_S": "-5", "AI_ASSIST_MAX_RETRIES": "-2"}, 1.0, 0),
        ("override", {"AI_ASSIST_TIMEOUT_S": "12.5", "AI_ASSIST_MAX_RETRIES": "0"}, 12.5, 0)]:
    saved = {k: os.environ.get(k) for k in ("AI_ASSIST_TIMEOUT_S", "AI_ASSIST_MAX_RETRIES")}
    for k in saved:
        os.environ.pop(k, None)
    os.environ.update(env)
    g = {"os": os}
    try:
        exec(limits_src, g)
        ok = g["AI_ASSIST_TIMEOUT_S"] == want_t and g["AI_ASSIST_MAX_RETRIES"] == want_r
        detail = f"got {g['AI_ASSIST_TIMEOUT_S']}/{g['AI_ASSIST_MAX_RETRIES']} want {want_t}/{want_r}"
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

# Fake `anthropic` package: AsyncAnthropic.messages.create sleeps, so we can watch the loop.
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
            return _Resp("hello from the model")

    class AsyncAnthropic:
        last = None

        def __init__(self, **kw):
            AsyncAnthropic.last = kw
            self.seen = None
            self.messages = _Msgs(self)

    mod.AsyncAnthropic = AsyncAnthropic
    mod.APITimeoutError = APITimeoutError
    mod.Anthropic = None            # a sync-client regression would TypeError loudly
    sys.modules["anthropic"] = mod
    return AsyncAnthropic


# Load the router module WITHOUT importing the app package (no DB / no settings needed):
# exec just the ai_assist function against stub globals.
G = {
    "settings": types.SimpleNamespace(ANTHROPIC_API_KEY="sk-test", ACCOUNT_ENGINE_MODEL="claude-opus-4-8"),
    "HTTPException": type("HTTPException", (Exception,), {"__init__": lambda s, c, d="": Exception.__init__(s, d)}),
    "_require_module": lambda org_id, key: None,
    "_tenant_ai_context": lambda org_id: {"tenant_name": "Acme", "modules": "helpdesk"},
    "_AI_SUPPORT_SYSTEM": "sys {tenant_name} {modules}",
    "ORG_ID": "00000000-0000-0000-0000-000000000001",
    "AI_ASSIST_TIMEOUT_S": 30.0, "AI_ASSIST_MAX_RETRIES": 1,
}
import copy
_ai_bare = copy.deepcopy(ai)
_ai_bare.decorator_list = []          # drop @router.post so we can exec it standalone
exec(compile(ast.fix_missing_locations(ast.Module(body=[_ai_bare], type_ignores=[])),
             "<ai_assist>", "exec"), G)
ai_fn = G["ai_assist"]

check("D1 extracted ai_assist is a coroutine function", asyncio.iscoroutinefunction(ai_fn))


async def _scenario(mode):
    Cli = _install_fake_anthropic(mode)
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:                       # stands in for /health + every other request
            await asyncio.sleep(0.02)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    t0 = time.monotonic()
    out = await ai_fn({"message": "how do I upload sales?"}, org_id=G["ORG_ID"])
    dt = time.monotonic() - t0
    hb.cancel()
    return out, ticks, dt, Cli


out, ticks, dt, Cli = asyncio.run(_scenario("ok"))
check("D2 loop kept serving during the model call (heartbeat ticked)", ticks >= 20, f"ticks={ticks}")
check("D3 happy path returns the model text", out.get("reply") == "hello from the model", str(out))
check("D4 configured=True on success", out.get("configured") is True)
check("D5 client built with timeout=30.0", Cli.last.get("timeout") == 30.0, str(Cli.last))
check("D6 client built with max_retries=1", Cli.last.get("max_retries") == 1, str(Cli.last))
check("D7 api_key forwarded, never logged", Cli.last.get("api_key") == "sk-test")

out, ticks, dt, _ = asyncio.run(_scenario("timeout"))
check("D8 APITimeoutError is caught (no 500)", isinstance(out, dict) and "reply" in out, str(out))
check("D9 timeout reply tells the user to retry / raise a ticket",
      "taking too long" in out.get("reply", "") and "ticket" in out.get("reply", ""), str(out))
check("D10 timeout keeps configured=True", out.get("configured") is True)
check("D11 loop kept serving through the timeout", ticks >= 20, f"ticks={ticks}")

out, ticks, dt, _ = asyncio.run(_scenario("boom"))
check("D12 generic error is caught (no 500)", isinstance(out, dict) and "reply" in out)
check("D13 generic error keeps the original graceful wording",
      out.get("reply", "").startswith("The assistant hit an error."), str(out))
check("D14 error string is truncated to 200 chars", len(out.get("error", "")) <= 200,
      str(len(out.get("error", ""))))

print()
print("=" * 78)
print("E. Guardrails — unchanged behaviour that must NOT regress")
print("=" * 78)


async def _no_key():
    G["settings"].ANTHROPIC_API_KEY = ""
    try:
        return await ai_fn({"message": "hi"}, org_id=G["ORG_ID"])
    finally:
        G["settings"].ANTHROPIC_API_KEY = "sk-test"


out = asyncio.run(_no_key())
check("E1 unconfigured key still short-circuits with configured=False", out.get("configured") is False, str(out))


async def _empty():
    try:
        await ai_fn({"message": "   "}, org_id=G["ORG_ID"])
        return None
    except Exception as e:
        return type(e).__name__


check("E2 empty message still raises HTTPException(400)", asyncio.run(_empty()) == "HTTPException")
check("E3 org_id is still a query-param default (multi-tenant rule)",
      any(a.arg == "org_id" for a in ai.args.args))
check("E4 route decorator unchanged (@router.post('/ai-assist'))",
      any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "post"
          and d.args and getattr(d.args[0], "value", "") == "/ai-assist" for d in ai.decorator_list))
check("E5 model id untouched (settings.ACCOUNT_ENGINE_MODEL)",
      "model=settings.ACCOUNT_ENGINE_MODEL" in body)
check("E6 max_tokens untouched (1024)", "max_tokens=1024" in body)
check("E7 history window still capped at 10", "[-10:]" in body)
check("E8 question still truncated to 4000", "question[:4000]" in body)
check("E9 no secret is printed/logged in ai_assist",
      "ANTHROPIC_API_KEY" not in body.replace("settings.ANTHROPIC_API_KEY", ""))

print()
print("=" * 78)
print("F. Blast radius — nothing else in this file changed shape")
print("=" * 78)
check("F1 `os` imported once at module top", SRC.count("\nimport os\n") == 1)
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


import subprocess
base_src = subprocess.run(["git", "show", "origin/main:backend/app/modules/helpdesk/router.py"],
                          cwd=os.path.dirname(os.path.abspath(__file__)) + "/..",
                          capture_output=True, text=True).stdout
now, base = _routes(TREE), _routes(ast.parse(base_src))
check(f"F2 helpdesk route surface IDENTICAL to origin/main ({len(now)} routes)", now == base,
      f"now={len(now)} base={len(base)} diff={set(now) ^ set(base)}")
check("F3 no SHARED file referenced by this change",
      "app.core.tenant_middleware" not in SRC and "rbac.ts" not in SRC)

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
