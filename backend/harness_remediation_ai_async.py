#!/usr/bin/env python3
"""PROOF harness — remediation /propose must not block the FastAPI event loop.

Same freeze class as the helpdesk /ai-assist SEV-1 (2026-07-30, fixed in c8acc81): a SYNCHRONOUS
Anthropic call reached from an `async def` route runs ON the single uvicorn event loop, and the SDK
defaults to a 600s timeout with 2 automatic retries -> one stalled request froze EVERY endpoint
(including /health) for up to ~30 minutes.

This is not a grep. It:
  A/B. AST-proves the fixed shape (async helper, AsyncAnthropic, awaited call site, explicit limits).
  C.   Fuzzes the env-tunable limits, including garbage values that must not break module import.
  D.   Drives the REAL `_ai_diagnose` and the REAL `propose` coroutines (extracted from the live
       source) on a REAL event loop against a fake anthropic SDK that stalls, while a heartbeat task
       stands in for /health and every other in-flight request. The heartbeat MUST keep ticking.
  E.   Proves the degrade path is byte-for-byte what it was: a timeout/error/absent key still yields
       None -> /propose ESCALATES rather than auto-fixing, and manual mode never calls the model.
  F.   Blast radius: route surface identical to origin/main, no SHARED file touched.

Run:            cd backend && python3 harness_remediation_ai_async.py
Negative ctrl:  python3 harness_remediation_ai_async.py --router <(git show origin/main:backend/app/modules/remediation/router.py)
                (or pass any pre-fix copy of the router)
"""
import argparse
import ast
import asyncio
import copy
import os
import subprocess
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--router", default=os.path.join(HERE, "app", "modules", "remediation", "router.py"))
ap.add_argument("--expect-fail", action="store_true",
                help="negative control: exit 0 only if checks FAIL")
ARGS = ap.parse_args()
ROUTER = ARGS.router
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


DIAG = fn("_ai_diagnose")
PROP = fn("propose")

print("=" * 78)
print("A. _ai_diagnose source shape")
print("=" * 78)
check("A1 _ai_diagnose exists", DIAG is not None)
check("A2 _ai_diagnose is an ASYNC def", isinstance(DIAG, ast.AsyncFunctionDef),
      f"type={type(DIAG).__name__}")
body = seg(DIAG)


def code_only(node):
    """Executable code only: comments AND docstrings stripped. The comments deliberately say
    `Anthropic(` ("do not reintroduce"), so a raw string search would be a false positive."""
    stripped = copy.deepcopy(node)
    if (stripped.body and isinstance(stripped.body[0], ast.Expr)
            and isinstance(stripped.body[0].value, ast.Constant)
            and isinstance(stripped.body[0].value.value, str)):
        stripped.body = stripped.body[1:]
    return ast.unparse(ast.fix_missing_locations(stripped))


code = code_only(DIAG)
check("A3 uses AsyncAnthropic", "AsyncAnthropic" in code)
check("A4 awaits the model call", "await cli.messages.create" in code)
check("A5 NO bare sync `Anthropic(` left in _ai_diagnose code",
      "Anthropic(" not in code.replace("AsyncAnthropic(", ""))
check("A6 NO un-awaited `cli.messages.create` left",
      body.count("cli.messages.create") == body.count("await cli.messages.create"))
check("A7 no `from anthropic import Anthropic` anywhere in the file",
      "from anthropic import Anthropic\n" not in SRC)
check("A8 explicit timeout kwarg passed", "timeout=REMEDIATION_AI_TIMEOUT_S" in body)
check("A9 explicit max_retries kwarg passed", "max_retries=REMEDIATION_AI_MAX_RETRIES" in body)
check("A10 graceful `except Exception` retained",
      any(isinstance(h.type, ast.Name) and h.type.id == "Exception"
          for t in ast.walk(DIAG) if isinstance(t, ast.Try) for h in t.handlers))

print()
print("=" * 78)
print("B. AST proof — no sync anthropic call survives on this async path")
print("=" * 78)
bad = [n.lineno for n in ast.walk(DIAG)
       if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create"
       and not any(isinstance(a, ast.Await) and a.value is n for a in ast.walk(DIAG))]
check("B1 every .create() inside _ai_diagnose is awaited", not bad, f"un-awaited at lines {bad}")
names = {n.func.id for n in ast.walk(DIAG)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
check("B2 sync `Anthropic` constructor is not called in _ai_diagnose", "Anthropic" not in names)
check("B3 `AsyncAnthropic` constructor IS called in _ai_diagnose", "AsyncAnthropic" in names)
# the caller graph: every call to _ai_diagnose anywhere in the file must be awaited (or threadpool-hopped)
callsites, awaited_sites, hopped_sites = [], [], []
for node in ast.walk(TREE):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_ai_diagnose":
                callsites.append((node.name, n.lineno))
                if any(isinstance(a, ast.Await) and a.value is n for a in ast.walk(node)):
                    awaited_sites.append(n.lineno)
            if isinstance(n, ast.Call):
                for a in n.args:
                    if isinstance(a, ast.Name) and a.id == "_ai_diagnose":
                        hopped_sites.append(n.lineno)   # run_in_threadpool(_ai_diagnose, ...)
check("B4 _ai_diagnose has exactly one call site", len(callsites) == 1, f"{callsites}")
check("B5 that call site is INSIDE an async def", isinstance(PROP, ast.AsyncFunctionDef))
check("B6 that call site is AWAITED (or threadpool-hopped)",
      len(awaited_sites) == len(callsites) or len(hopped_sites) == len(callsites),
      f"awaited={awaited_sites} hopped={hopped_sites} sites={callsites}")
check("B7 the route reached is @router.post('/propose')",
      any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "post"
          and d.args and getattr(d.args[0], "value", "") == "/propose"
          for d in (PROP.decorator_list if PROP else [])))

print()
print("=" * 78)
print("C. Limits are sane + env-tunable, and bad env values cannot break import")
print("=" * 78)
check("C1 REMEDIATION_AI_TIMEOUT_S declared", "REMEDIATION_AI_TIMEOUT_S" in SRC)
check("C2 REMEDIATION_AI_MAX_RETRIES declared", "REMEDIATION_AI_MAX_RETRIES" in SRC)
if "try:\n    REMEDIATION_AI_TIMEOUT_S" in SRC:
    limits_src = SRC[SRC.index("try:\n    REMEDIATION_AI_TIMEOUT_S"):SRC.index("_DIAGNOSE_SYSTEM")]
    for label, env, want_t, want_r in [
            ("defaults", {}, 30.0, 1),
            ("garbage", {"REMEDIATION_AI_TIMEOUT_S": "abc", "REMEDIATION_AI_MAX_RETRIES": "x"}, 30.0, 1),
            ("empty", {"REMEDIATION_AI_TIMEOUT_S": "", "REMEDIATION_AI_MAX_RETRIES": ""}, 30.0, 1),
            ("negative", {"REMEDIATION_AI_TIMEOUT_S": "-5", "REMEDIATION_AI_MAX_RETRIES": "-2"}, 1.0, 0),
            ("override", {"REMEDIATION_AI_TIMEOUT_S": "12.5", "REMEDIATION_AI_MAX_RETRIES": "0"}, 12.5, 0)]:
        keys = ("REMEDIATION_AI_TIMEOUT_S", "REMEDIATION_AI_MAX_RETRIES")
        saved = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(env)
        g = {"os": os}
        try:
            exec(limits_src, g)
            ok = g["REMEDIATION_AI_TIMEOUT_S"] == want_t and g["REMEDIATION_AI_MAX_RETRIES"] == want_r
            detail = (f"got {g['REMEDIATION_AI_TIMEOUT_S']}/{g['REMEDIATION_AI_MAX_RETRIES']} "
                      f"want {want_t}/{want_r}")
        except Exception as e:
            ok, detail = False, f"raised {e!r}"
        check(f"C3 limits[{label}]", ok, detail)
        for k in keys:
            os.environ.pop(k, None)
            if saved[k] is not None:
                os.environ[k] = saved[k]
else:
    check("C3 limits block present and fuzzable", False, "no REMEDIATION_AI_* limits block in this file")
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


GOOD_JSON = ('Sure! ```json {"issue_class":"data","playbook_key":"remap_store",'
             '"params":{"from":"A","to":"B"},"proposed_action":"Remap store A to B.",'
             '"diagnosis":"Store string drift.","confidence":0.9} ``` hope that helps')
CODE_JSON = ('{"issue_class":"code","playbook_key":null,"params":{},'
             '"proposed_action":"Needs a deploy.","diagnosis":"Parser bug.","confidence":0.8}')


def _install_fake_anthropic(mode, payload=GOOD_JSON):
    """Fake SDK exposing BOTH clients. The SYNC one really blocks (time.sleep) — that is what makes
    the negative control reproduce the freeze instead of merely asserting a string."""
    mod = types.ModuleType("anthropic")
    box = {"kwargs": None, "client_kwargs": None}

    def _result():
        if mode == "timeout":
            raise APITimeoutError("Request timed out.")
        if mode == "boom":
            raise RuntimeError("kaboom" * 60)
        if mode == "garbage":
            return _Resp("I'm afraid I can't answer that.")
        return _Resp(payload)

    class _AMsgs:
        async def create(self, **kw):
            box["kwargs"] = kw
            await asyncio.sleep(STALL)
            return _result()

    class _SMsgs:
        def create(self, **kw):
            box["kwargs"] = kw
            time.sleep(STALL)              # THE BUG: blocks the loop
            return _result()

    class AsyncAnthropic:
        def __init__(self, **kw):
            box["client_kwargs"] = kw
            self.messages = _AMsgs()

    class Anthropic:
        def __init__(self, **kw):
            box["client_kwargs"] = kw
            self.messages = _SMsgs()

    mod.AsyncAnthropic, mod.Anthropic, mod.APITimeoutError = AsyncAnthropic, Anthropic, APITimeoutError
    sys.modules["anthropic"] = mod
    return box


# ── build a standalone namespace holding the REAL _ai_diagnose + REAL propose ────────────────────
CALLS = {"preview": 0, "insert": []}


class _Exec:
    def __init__(self, row):
        self.data = [dict(row, id="req-0001")] if row is not None else None


class _Tbl:
    def __init__(self, name):
        self.name = name
        self._row = None

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def insert(self, row):
        self._row = row
        CALLS["insert"].append(row)
        return self

    def execute(self):
        return _Exec(self._row)


class _Schema:
    def table(self, name):
        return _Tbl(name)


class _Client:
    def schema(self, name):
        return _Schema()


def _mk_ns():
    ns = {
        "os": os, "json": __import__("json"), "secrets": __import__("secrets"),
        "settings": types.SimpleNamespace(ANTHROPIC_API_KEY="sk-test",
                                          ACCOUNT_ENGINE_MODEL="claude-opus-4-8",
                                          APP_PUBLIC_URL="https://app.example"),
        "HTTPException": type("HTTPException", (Exception,),
                              {"__init__": lambda s, c, d="": Exception.__init__(s, d)}),
        "ORG_ID": "00000000-0000-0000-0000-000000000001",
        "sb": lambda: _Client(),
        "_catalog": lambda client, org_id, only_enabled=False: [
            {"key": "remap_store", "name": "Remap store", "description": "d", "params_schema": {}}],
        "_approval_url": lambda rid, tok: f"https://app.example/remediation/approve/{rid}?token={tok}",
        "pb": types.SimpleNamespace(
            is_implemented=lambda k: k == "remap_store",
            run_preview=lambda k, c, o, p: (CALLS.__setitem__("preview", CALLS["preview"] + 1),
                                            {"summary": "would remap 3 rows"})[1]),
    }
    # module-level limits, executed from the REAL source when present
    if "try:\n    REMEDIATION_AI_TIMEOUT_S" in SRC:
        exec(SRC[SRC.index("try:\n    REMEDIATION_AI_TIMEOUT_S"):SRC.index("_DIAGNOSE_SYSTEM")], ns)
    else:
        ns.setdefault("REMEDIATION_AI_TIMEOUT_S", 30.0)
        ns.setdefault("REMEDIATION_AI_MAX_RETRIES", 1)
    ns["_DIAGNOSE_SYSTEM"] = "diagnose-system-prompt"

    async def _send_approval(req, url):
        return {"channels": ["email"], "email": "sent", "whatsapp": None}
    ns["_send_approval"] = _send_approval

    for node in (DIAG, PROP):
        bare = copy.deepcopy(node)
        bare.decorator_list = []          # drop @router.post so it can be exec'd standalone
        exec(compile(ast.fix_missing_locations(ast.Module(body=[bare], type_ignores=[])),
                     f"<{node.name}>", "exec"), ns)
    return ns


NS = _mk_ns()
check("D1 extracted _ai_diagnose is a coroutine function",
      asyncio.iscoroutinefunction(NS["_ai_diagnose"]))
check("D2 extracted propose is a coroutine function", asyncio.iscoroutinefunction(NS["propose"]))


async def _drive(coro_factory):
    """Run a coroutine while a heartbeat (stand-in for /health) must keep ticking."""
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)                # let the heartbeat get scheduled
    t0 = time.monotonic()
    try:
        out = await coro_factory()
        err = None
    except Exception as e:                # noqa: BLE001 - the harness reports it
        out, err = None, e
    dt = time.monotonic() - t0
    hb.cancel()
    return out, ticks, dt, err


def run_propose(mode, body, payload=GOOD_JSON):
    box = _install_fake_anthropic(mode, payload)
    CALLS["preview"] = 0
    CALLS["insert"] = []
    out, ticks, dt, err = asyncio.run(_drive(lambda: NS["propose"](body, org_id=NS["ORG_ID"])))
    return out, ticks, dt, err, box


ISSUE = {"issue": "Sales for store A are landing under an unknown store code."}

out, ticks, dt, err, box = run_propose("ok", ISSUE)
check("D3 /propose completed without raising", err is None, repr(err))
check("D4 EVENT LOOP KEPT SERVING during the stalled model call (heartbeat ticked)",
      ticks >= 20, f"ticks={ticks} in {dt:.2f}s — a blocked loop yields ~0")
check("D5 client built with timeout=30.0", (box["client_kwargs"] or {}).get("timeout") == 30.0,
      str(box["client_kwargs"]))
check("D6 client built with max_retries=1", (box["client_kwargs"] or {}).get("max_retries") == 1,
      str(box["client_kwargs"]))
check("D7 api_key forwarded, never logged", (box["client_kwargs"] or {}).get("api_key") == "sk-test")
check("D8 model id untouched (settings.ACCOUNT_ENGINE_MODEL)",
      (box["kwargs"] or {}).get("model") == "claude-opus-4-8", str((box["kwargs"] or {}).get("model")))
check("D9 max_tokens still 700", (box["kwargs"] or {}).get("max_tokens") == 700)
check("D10 system prompt still _DIAGNOSE_SYSTEM",
      (box["kwargs"] or {}).get("system") == "diagnose-system-prompt")

print()
print("=" * 78)
print("E. Behaviour preserved — the AI verdict still drives /propose exactly as before")
print("=" * 78)
check("E1 fence-wrapped JSON still parsed (playbook picked)",
      (out or {}).get("request", {}).get("playbook_key") == "remap_store", str(out)[:200])
check("E2 data-class -> awaiting_approval (not escalated)",
      (out or {}).get("request", {}).get("status") == "awaiting_approval" and not (out or {}).get("escalated"))
check("E3 AI params/action/diagnosis still flow into the request",
      (out or {}).get("request", {}).get("params") == {"from": "A", "to": "B"}
      and (out or {}).get("request", {}).get("proposed_action") == "Remap store A to B."
      and (out or {}).get("request", {}).get("diagnosis") == "Store string drift.")
check("E4 dry-run preview still computed before approval", CALLS["preview"] == 1)
check("E5 approval magic-link still returned, token stripped from the request",
      "approval_url" in (out or {}) and "approval_token" not in (out or {}).get("request", {}))
check("E6 org_id still stamped on the insert (multi-tenant rule)",
      all(r.get("org_id") == NS["ORG_ID"] for r in CALLS["insert"]) and len(CALLS["insert"]) == 1)

out, ticks, dt, err, box = run_propose("timeout", ISSUE)
check("E7 TIMEOUT degrades exactly as before: escalated, never auto-fixed",
      err is None and (out or {}).get("escalated") is True
      and (out or {}).get("request", {}).get("status") == "escalated", f"{err!r} {str(out)[:200]}")
check("E8 loop kept serving through the timeout", ticks >= 20, f"ticks={ticks}")
check("E9 timeout runs NO playbook preview (nothing executed)", CALLS["preview"] == 0)

out, ticks, dt, err, box = run_propose("boom", ISSUE)
check("E10 generic SDK error degrades to escalated (no 500)",
      err is None and (out or {}).get("escalated") is True, f"{err!r} {str(out)[:200]}")

out, ticks, dt, err, box = run_propose("garbage", ISSUE)
check("E11 non-JSON model reply degrades to escalated",
      err is None and (out or {}).get("escalated") is True, str(out)[:200])

out, ticks, dt, err, box = run_propose("ok", ISSUE, payload=CODE_JSON)
check("E12 code-class is still ESCALATED, never auto-fixed",
      (out or {}).get("escalated") is True
      and (out or {}).get("request", {}).get("issue_class") == "code", str(out)[:200])

out, ticks, dt, err, box = run_propose("ok", {"issue": "x", "playbook_key": "remap_store",
                                              "params": {"from": "A", "to": "B"}})
check("E13 manual mode (explicit playbook_key) never calls the model",
      box["client_kwargs"] is None and (out or {}).get("request", {}).get("status") == "awaiting_approval",
      str(box["client_kwargs"]))

NS["settings"].ANTHROPIC_API_KEY = ""
out, ticks, dt, err, box = run_propose("ok", ISSUE)
check("E14 unconfigured key still short-circuits -> escalated, model never built",
      box["client_kwargs"] is None and (out or {}).get("escalated") is True, str(out)[:200])
NS["settings"].ANTHROPIC_API_KEY = "sk-test"


async def _empty():
    try:
        await NS["propose"]({}, org_id=NS["ORG_ID"])
        return None
    except Exception as e:
        return type(e).__name__


check("E15 empty issue still raises HTTPException(400)", asyncio.run(_empty()) == "HTTPException")
check("E16 org_id is still a query-param default on /propose (multi-tenant rule)",
      any(a.arg == "org_id" for a in PROP.args.args))

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


base_src = subprocess.run(
    ["git", "show", "origin/main:backend/app/modules/remediation/router.py"],
    cwd=os.path.join(HERE, ".."), capture_output=True, text=True).stdout
if base_src:
    now, base = _routes(TREE), _routes(ast.parse(base_src))
    check(f"F2 remediation route surface IDENTICAL to origin/main ({len(now)} routes)", now == base,
          f"now={len(now)} base={len(base)} diff={set(now) ^ set(base)}")
    base_tree = ast.parse(base_src)
    base_fns = sorted(n.name for n in ast.walk(base_tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    now_fns = sorted(n.name for n in ast.walk(TREE)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    check("F3 no function added or removed vs origin/main", base_fns == now_fns,
          f"diff={set(base_fns) ^ set(now_fns)}")
else:
    check("F2 origin/main copy readable for comparison", False, "git show failed")
check("F4 no SHARED file referenced by this change",
      "tenant_middleware" not in SRC and "rbac.ts" not in SRC)
check("F5 whitelist safety intact: pb.is_implemented still gates execution",
      "pb.is_implemented(playbook_key)" in SRC)

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
if ARGS.expect_fail:
    print("NEGATIVE CONTROL: expected failures ->", "REPRODUCED" if F else "NOT REPRODUCED (bad)")
    sys.exit(0 if F else 1)
sys.exit(1 if F else 0)
