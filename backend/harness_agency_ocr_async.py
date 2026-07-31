#!/usr/bin/env python3
"""PROOF harness — SEV-1 2026-07-30 bug class, second site: commcalc/agency.py invoice OCR.

`agency_upload_ocr` (router.py, `async def`) calls `_agency._ocr_parse_transfer(...)` directly — no
threadpool hop — so the SYNC `anthropic.Anthropic` client that used to live there blocked the ONE
uvicorn event loop for the whole model call (SDK default 600s x 2 retries ≈ 30 min). This harness
proves the fix, mirroring backend/harness_ai_assist_async.py (the shipped ai-assist precedent):

  A  source shape                  B  AST: nothing sync survives
  C  env-tunable limits            D  LIVE: awaiting the async fn keeps the loop serving
  E  LIVE: sync bridge compat + hard wall (and the honest limitation it still has)
  F  byte-identity of everything that is NOT the client swap (prompt / model / parsing)
  G  blast radius

Run:  cd backend && python3 harness_agency_ocr_async.py
"""
import ast
import asyncio
import copy
import os
import subprocess
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
AGENCY = os.path.join(HERE, "app", "modules", "commcalc", "agency.py")
SRC = open(AGENCY, encoding="utf-8").read()
TREE = ast.parse(SRC, AGENCY)

P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {name}")
    else:
        F += 1
        print(f"  FAIL  {name}   {detail}")


def fn(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def seg(src, node):
    return ast.get_source_segment(src, node) or ""


BASE_SRC = subprocess.run(["git", "show", "origin/main:backend/app/modules/commcalc/agency.py"],
                          cwd=REPO, capture_output=True, text=True).stdout
BASE_TREE = ast.parse(BASE_SRC)

print("=" * 78)
print("A. Source shape — async implementation + sync bridge")
print("=" * 78)
aio = fn(TREE, "_ocr_parse_transfer_async")
brg = fn(TREE, "_ocr_parse_transfer")
check("A1 _ocr_parse_transfer_async exists", aio is not None)
check("A2 _ocr_parse_transfer_async IS an async def", isinstance(aio, ast.AsyncFunctionDef))
check("A3 _ocr_parse_transfer (sync bridge) still exists", brg is not None)
check("A4 bridge keeps the original signature (data, filename, mimetype)",
      [a.arg for a in brg.args.args] == ["data", "filename", "mimetype"],
      str([a.arg for a in brg.args.args]))
abody = seg(SRC, aio)
acode = "\n".join(ln.split("#", 1)[0] for ln in abody.splitlines())   # comments say "Anthropic(" on purpose
check("A5 uses AsyncAnthropic", "AsyncAnthropic" in acode)
check("A6 awaits the model call", "await cli.messages.create" in acode)
check("A7 NO bare sync `Anthropic(` left in the code",
      "Anthropic(" not in acode.replace("AsyncAnthropic(", ""))
check("A8 NO un-awaited `cli.messages.create` left",
      abody.count("cli.messages.create") == abody.count("await cli.messages.create"))
check("A9 no `from anthropic import Anthropic` anywhere in agency.py",
      "from anthropic import Anthropic\n" not in SRC)
check("A10 explicit timeout kwarg passed", "timeout=AGENCY_AI_TIMEOUT_S" in abody)
check("A11 explicit max_retries kwarg passed", "max_retries=AGENCY_AI_MAX_RETRIES" in abody)
check("A12 graceful `except Exception` retained in the async fn",
      any(isinstance(h.type, ast.Name) and h.type.id == "Exception"
          for t in ast.walk(aio) if isinstance(t, ast.Try) for h in t.handlers))
check("A13 graceful `except Exception` retained in the bridge",
      any(isinstance(h.type, ast.Name) and h.type.id == "Exception"
          for t in ast.walk(brg) if isinstance(t, ast.Try) for h in t.handlers))

print()
print("=" * 78)
print("B. AST proof — no sync anthropic call survives anywhere in agency.py")
print("=" * 78)
bad = []
for host in (aio, brg):
    for n in ast.walk(host):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create":
            if not any(isinstance(a, ast.Await) and a.value is n for a in ast.walk(host)):
                bad.append((host.name, n.lineno))
check("B1 every .create() on the OCR path is awaited", not bad, f"un-awaited: {bad}")
called = {n.func.id for n in ast.walk(TREE) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
check("B2 sync `Anthropic` constructor is never called in agency.py", "Anthropic" not in called)
check("B3 `AsyncAnthropic` constructor IS called", "AsyncAnthropic" in called)
brg_calls = [n for n in ast.walk(brg) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_ocr_parse_transfer_async"]
check("B4 bridge delegates to the async fn (no duplicated model code)",
      len(brg_calls) == 2 and "messages.create" not in seg(SRC, brg),
      f"delegating calls={len(brg_calls)}")

print()
print("=" * 78)
print("C. Limits are sane + env-tunable, and bad env values cannot break import")
print("=" * 78)
check("C1 AGENCY_AI_TIMEOUT_S declared", "AGENCY_AI_TIMEOUT_S" in SRC)
check("C2 AGENCY_AI_MAX_RETRIES declared", "AGENCY_AI_MAX_RETRIES" in SRC)
limits_src = SRC[SRC.index("try:\n    AGENCY_AI_TIMEOUT_S"):SRC.index("async def _ocr_parse_transfer_async")]
for label, env, want_t, want_r in [
        ("defaults", {}, 60.0, 1),
        ("garbage", {"AGENCY_AI_TIMEOUT_S": "abc", "AGENCY_AI_MAX_RETRIES": "x"}, 60.0, 1),
        ("empty", {"AGENCY_AI_TIMEOUT_S": "", "AGENCY_AI_MAX_RETRIES": ""}, 60.0, 1),
        ("negative", {"AGENCY_AI_TIMEOUT_S": "-5", "AGENCY_AI_MAX_RETRIES": "-2"}, 1.0, 0),
        ("override", {"AGENCY_AI_TIMEOUT_S": "12.5", "AGENCY_AI_MAX_RETRIES": "0"}, 12.5, 0)]:
    keys = ("AGENCY_AI_TIMEOUT_S", "AGENCY_AI_MAX_RETRIES")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ.update(env)
    g = {"os": os}
    try:
        exec(limits_src, g)
        ok = g["AGENCY_AI_TIMEOUT_S"] == want_t and g["AGENCY_AI_MAX_RETRIES"] == want_r
        detail = f"got {g['AGENCY_AI_TIMEOUT_S']}/{g['AGENCY_AI_MAX_RETRIES']}, want {want_t}/{want_r}"
        wall_ok = g["_AGENCY_AI_WALL_S"] == want_t * (1 + want_r) + 5
    except Exception as e:
        ok, wall_ok, detail = False, False, f"raised {e!r}"
    check(f"C3 limits[{label}]", ok and wall_ok, detail)
    for k in keys:
        os.environ.pop(k, None)
        if saved[k] is not None:
            os.environ[k] = saved[k]
check("C4 default worst case (125s) is under Railway's 300s cutoff", 60.0 * (1 + 1) + 5 <= 300)

print()
print("=" * 78)
print("D. LIVE — awaiting the async fn keeps the event loop serving")
print("=" * 78)
sys.path.insert(0, HERE)
from app.modules.commcalc import agency as A                     # noqa: E402

A.settings = types.SimpleNamespace(ANTHROPIC_API_KEY="sk-test",
                                   ACCOUNT_ENGINE_MODEL="claude-opus-4-8")
STALL = 0.60
GOOD_JSON = ('prose before {"lines":[{"equip_class_value":"device","product_desc":"A15","qty":2,'
             '"unit_cost":110.5},{"equip_class_value":"accessory","product_desc":"Case","qty":3,'
             '"unit_cost":4.25}]} trailing prose')


class _Blk:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, text):
        self.content = [_Blk(text)]


class APITimeoutError(Exception):
    pass


def _install_fake_anthropic(mode, stall=STALL):
    mod = types.ModuleType("anthropic")

    class _Msgs:
        def __init__(self, outer):
            self.o = outer

        async def create(self, **kw):
            AsyncAnthropic.seen = kw
            await asyncio.sleep(stall)
            if mode == "timeout":
                raise APITimeoutError("Request timed out.")
            if mode == "boom":
                raise RuntimeError("kaboom")
            return _Resp(GOOD_JSON)

    class AsyncAnthropic:
        last = None
        seen = None

        def __init__(self, **kw):
            AsyncAnthropic.last = kw
            self.messages = _Msgs(self)

    mod.AsyncAnthropic = AsyncAnthropic
    mod.APITimeoutError = APITimeoutError
    mod.Anthropic = None            # a sync-client regression would TypeError loudly
    sys.modules["anthropic"] = mod
    return AsyncAnthropic


async def _await_scenario(mode, stall=STALL):
    Cli = _install_fake_anthropic(mode, stall)
    ticks = 0

    async def heartbeat():                 # stands in for /health + every other request
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    t0 = time.monotonic()
    out = await A._ocr_parse_transfer_async(b"%PDF-1.4 fake", "inv.pdf", "application/pdf")
    dt = time.monotonic() - t0
    hb.cancel()
    return out, ticks, dt, Cli


out, ticks, dt, Cli = asyncio.run(_await_scenario("ok"))
check("D1 loop kept serving during the model call (heartbeat ticked)", ticks >= 20, f"ticks={ticks}")
check("D2 rows parsed out of the JSON envelope",
      out[0] == [{"equip_class_value": "device", "product_desc": "A15", "qty": 2, "unit_cost": 110.5},
                 {"equip_class_value": "accessory", "product_desc": "Case", "qty": 3, "unit_cost": 4.25}],
      str(out[0]))
check("D3 model + confidence unchanged", out[1] == "claude-opus-4-8" and out[2] == 0.9, str(out[1:]))
check("D4 client built with timeout=60.0", Cli.last.get("timeout") == 60.0, str(Cli.last))
check("D5 client built with max_retries=1", Cli.last.get("max_retries") == 1, str(Cli.last))
check("D6 api_key forwarded, never logged", Cli.last.get("api_key") == "sk-test")
check("D7 PDF sent as a base64 `document` block",
      Cli.seen["messages"][0]["content"][0]["type"] == "document"
      and Cli.seen["messages"][0]["content"][0]["source"]["media_type"] == "application/pdf"
      and Cli.seen["messages"][0]["content"][0]["source"]["type"] == "base64",
      str(Cli.seen["messages"][0]["content"][0])[:160])
check("D8 max_tokens still 1500", Cli.seen.get("max_tokens") == 1500, str(Cli.seen.get("max_tokens")))
check("D9 model resolved from ACCOUNT_ENGINE_MODEL", Cli.seen.get("model") == "claude-opus-4-8")

ImgCli = _install_fake_anthropic("ok", 0.01)
out_img = asyncio.run(A._ocr_parse_transfer_async(b"\x89PNG fake", "scan.png", "image/png"))
check("D10 non-PDF sent as an `image` block with media_type image/png",
      ImgCli.seen["messages"][0]["content"][0]["type"] == "image"
      and ImgCli.seen["messages"][0]["content"][0]["source"]["media_type"] == "image/png",
      str(ImgCli.seen["messages"][0]["content"][0])[:160])
check("D11 image path still returns rows", out_img[0] and out_img[2] == 0.9, str(out_img[1:]))

out, ticks, _, _ = asyncio.run(_await_scenario("timeout"))
check("D12 APITimeoutError degrades to ([], 'error', None) — same as before",
      out == ([], "error", None), str(out))
check("D13 loop kept serving through the timeout", ticks >= 20, f"ticks={ticks}")
out, _, _, _ = asyncio.run(_await_scenario("boom"))
check("D14 generic error degrades to ([], 'error', None)", out == ([], "error", None), str(out))

A.settings.ANTHROPIC_API_KEY = ""
check("D15 no API key still short-circuits to ([], 'deterministic', None) with no SDK touch",
      asyncio.run(A._ocr_parse_transfer_async(b"x", "a.pdf", "application/pdf")) == ([], "deterministic", None))
A.settings.ANTHROPIC_API_KEY = "sk-test"

print()
print("=" * 78)
print("E. LIVE — sync bridge: unchanged contract, and a HARD wall on the worst case")
print("=" * 78)
_install_fake_anthropic("ok", 0.05)
sync_out = A._ocr_parse_transfer(b"%PDF-1.4 fake", "inv.pdf", "application/pdf")
_install_fake_anthropic("ok", 0.05)
async_out = asyncio.run(A._ocr_parse_transfer_async(b"%PDF-1.4 fake", "inv.pdf", "application/pdf"))
check("E1 bridge OFF the loop returns exactly what awaiting returns", sync_out == async_out, str(sync_out))
_install_fake_anthropic("boom", 0.01)
check("E2 bridge degrades to ([], 'error', None) on failure",
      A._ocr_parse_transfer(b"x", "a.pdf", "application/pdf") == ([], "error", None))
A.settings.ANTHROPIC_API_KEY = ""
check("E3 bridge with no key still returns ([], 'deterministic', None)",
      A._ocr_parse_transfer(b"x", "a.pdf", "application/pdf") == ([], "deterministic", None))
A.settings.ANTHROPIC_API_KEY = "sk-test"


async def _bridge_on_loop(stall, wall=None):
    """Reproduces TODAY's router path: an `async def` handler calling the sync bridge."""
    _install_fake_anthropic("ok", stall)
    saved = A._AGENCY_AI_WALL_S
    if wall is not None:
        A._AGENCY_AI_WALL_S = wall
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)                      # let the heartbeat get going
    base = ticks
    t0 = time.monotonic()
    out = A._ocr_parse_transfer(b"%PDF-1.4 fake", "inv.pdf", "application/pdf")
    dt = time.monotonic() - t0
    hb.cancel()
    A._AGENCY_AI_WALL_S = saved
    return out, ticks - base, dt


out, ticks, dt = asyncio.run(_bridge_on_loop(0.20))
check("E4 bridge ON a running loop still returns the right rows (call-site compat kept)",
      out == async_out, str(out))
check("E5 DOCUMENTED LIMITATION: the bridge does NOT free the caller's loop "
      "(only the companion `await` at the router call site can)", ticks <= 3,
      f"ticks={ticks} — if this ever passes with many ticks, the bridge changed shape")
out, ticks, dt = asyncio.run(_bridge_on_loop(5.0, wall=0.30))
check("E6 hard wall fires: a hung model call is cut off at _AGENCY_AI_WALL_S, not 30 minutes",
      out == ([], "error", None) and dt < 1.5, f"out={out} dt={dt:.2f}s")
check("E7 wall path returns promptly (executor is NOT waited on)", dt < 1.5, f"dt={dt:.2f}s")

print()
print("=" * 78)
print("F. Byte-identity — everything that is NOT the client swap is unchanged")
print("=" * 78)


class _Norm(ast.NodeTransformer):
    """Undo exactly the three intended edits so the two bodies must be otherwise IDENTICAL."""

    def visit_Await(self, node):
        self.generic_visit(node)
        return node.value

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "AsyncAnthropic":
            node.func = ast.Name(id="Anthropic", ctx=ast.Load())
            node.keywords = [k for k in node.keywords if k.arg not in ("timeout", "max_retries")]
        return node

    def visit_ImportFrom(self, node):
        if node.module == "anthropic":
            node.names = [ast.alias(name="Anthropic", asname=None)]
        return node


def _stmts(node):
    b = list(node.body)
    if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0], "value", None), ast.Constant) \
            and isinstance(b[0].value.value, str):
        b = b[1:]                                  # docstrings legitimately differ
    return b


base_fn = fn(BASE_TREE, "_ocr_parse_transfer")
norm_new = [ast.dump(ast.fix_missing_locations(_Norm().visit(copy.deepcopy(s)))) for s in _stmts(aio)]
norm_base = [ast.dump(s) for s in _stmts(base_fn)]
check("F1 async body == origin/main body once the client swap is normalised away",
      norm_new == norm_base,
      "first divergence: " + next((f"{a[:150]} != {b[:150]}"
                                   for a, b in zip(norm_new, norm_base) if a != b), "length differs"))


def _prompt(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "prompt":
            return ast.literal_eval(n.value)     # implicit concat is folded into one Constant
    return None


check("F2 extraction prompt is byte-for-byte identical to origin/main",
      _prompt(aio) == _prompt(base_fn) and _prompt(aio) is not None)
check("F3 model fallback literal unchanged ('claude-3-5-sonnet-latest')",
      'getattr(settings, "ACCOUNT_ENGINE_MODEL", "claude-3-5-sonnet-latest")' in abody)
check("F4 confidence literal unchanged (0.9)", "0.9)" in abody)
check("F5 JSON envelope slicing unchanged",
      'text = text[text.find("{"): text.rfind("}") + 1]' in abody)

print()
print("=" * 78)
print("G. Blast radius")
print("=" * 78)
names_now = sorted(n.name for n in ast.walk(TREE) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
names_base = sorted(n.name for n in ast.walk(BASE_TREE) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
check("G1 exactly ONE new function (_ocr_parse_transfer_async), none removed/renamed",
      set(names_now) - set(names_base) == {"_ocr_parse_transfer_async"}
      and set(names_base) - set(names_now) == set(),
      f"+{set(names_now) - set(names_base)} -{set(names_base) - set(names_now)}")
changed = subprocess.run(["git", "diff", "--name-only", "origin/main", "--"], cwd=REPO,
                         capture_output=True, text=True).stdout.split()
check("G2 only agency.py + this package's two proof files are modified vs origin/main",
      set(changed) <= {"backend/app/modules/commcalc/agency.py",
                       "backend/harness_agency_ocr_async.py",
                       "backend/scratchpad/agency_ocr_async_asgi_smoke.py"},
      str(changed))
check("G3 router.py untouched (parallel carrier-income package owns it this cycle)",
      "backend/app/modules/commcalc/router.py" not in changed)
check("G4 whatif.py / custom_report.py untouched",
      not any(f.endswith(("whatif.py", "custom_report.py")) for f in changed))
check("G5 no SHARED file touched (core/**, main.py, client.ts, rbac.ts, layout.tsx)",
      not any(f.startswith("backend/app/core/") or f.endswith(("app/main.py", "lib/client.ts",
                                                               "lib/rbac.ts", "(platform)/layout.tsx"))
              for f in changed), str(changed))
def _imports(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out.add(n.module or "")
            out |= {f"{n.module}.{a.name}" for a in n.names}
    return out


imported = _imports(TREE)
want = (_imports(BASE_TREE) - {"anthropic.Anthropic"}
        | {"anthropic.AsyncAnthropic", "os", "asyncio", "concurrent.futures"})
tbls = {getattr(c.args[0], "value", "") for c in ast.walk(TREE)
        if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "table" and c.args}
check("G6a import surface == origin/main + {os, asyncio, concurrent.futures}, minus the sync client",
      imported == want, f"unexpected={sorted(imported ^ want)}")
check("G6b NOT money-touching: no calculator / commission_engine import, no rep_commissions table "
      f"(tables touched: {sorted(t for t in tbls if t)})",
      not any("calculator" in m or "commission_engine" in m for m in imported)
      and "rep_commissions" not in tbls)
check("G7 no migration / SQL added by this package",
      not any("migrations" in f or f.endswith(".sql") for f in changed), str(changed))
check("G8 `os` / `asyncio` / `concurrent.futures` imported once each at module top",
      SRC.count("\nimport os\n") == 1 and SRC.count("\nimport asyncio\n") == 1
      and SRC.count("\nimport concurrent.futures\n") == 1)

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
