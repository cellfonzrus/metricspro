#!/usr/bin/env python3
"""Harness: finance sync-in-async freeze-class fix (agent/finance/sync-in-async).

Proves the three sites that could block the uvicorn event loop are fixed, AND — the part that
matters most for a money module — that NOT ONE computational line changed: every function in the
touched files other than the three transport sites is AST-identical to origin/main, so the P&L,
Balance Sheet and VIP recon figures are byte-identical by construction.

    python3 harness_finance_sync_in_async.py

Exit 0 = all green.
"""
import ast
import os
import subprocess
import sys

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
REPO = os.path.dirname(BACKEND)
BASE = "origin/main"

MOD = "app/modules"
ENGINE = f"{MOD}/account/engine.py"
RECON = f"{MOD}/account/recon.py"
AROUTER = f"{MOD}/account/router.py"
PCOSTS = f"{MOD}/billing/platform_costs.py"
BROUTER = f"{MOD}/billing/router.py"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def read_now(rel):
    with open(os.path.join(BACKEND, rel), encoding="utf-8") as fh:
        return fh.read()


def read_base(rel):
    return subprocess.run(["git", "-C", REPO, "show", f"{BASE}:backend/{rel}"],
                          capture_output=True, text=True, check=True).stdout


def funcs(src):
    """{qualname: normalized-AST-dump} for every function, docstrings/comments stripped."""
    out = {}

    def walk(node, prefix=""):
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{prefix}{child.name}"
                body = list(child.body)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)):
                    body = body[1:]                      # drop docstring
                clone = ast.Module(body=body, type_ignores=[])
                out[q] = ast.dump(clone)
                walk(child, prefix=f"{q}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix=f"{prefix}{child.name}.")

    walk(ast.parse(src))
    return out


# ═══════════════════════════════════════════════════════════════════════════════════════════
print("\n[1] No blocking client remains on the touched paths")
# ═══════════════════════════════════════════════════════════════════════════════════════════
eng, rec, pc = read_now(ENGINE), read_now(RECON), read_now(PCOSTS)


def client_calls(src, *names):
    """Every constructor Call matching `names`, as (dotted_name, {kwargs}). AST-based so prose in a
    docstring or comment can never satisfy — or falsely fail — a check."""
    found = []
    for n in ast.walk(ast.parse(src)):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        dotted = (f"{f.value.id}.{f.attr}" if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                  else f.attr if isinstance(f, ast.Attribute)
                  else f.id if isinstance(f, ast.Name) else None)
        if dotted in names:
            found.append((dotted, {k.arg for k in n.keywords}))
    return found


check("platform_costs: no sync `httpx.Client(` constructed (AST)",
      not client_calls(pc, "httpx.Client", "Client"))
check("platform_costs: constructs httpx.AsyncClient (AST)",
      [kw for name, kw in client_calls(pc, "httpx.AsyncClient") if "timeout" in kw])
check("platform_costs: paginating GET is awaited", "await c.get(url, headers=headers, params=params)" in pc)

for label, src in (("engine", eng), ("recon", rec)):
    # The sync Anthropic client is ALLOWED here (its callers are threadpool-hopped) but it MUST be
    # capped — an uncapped client is the 600s x 2 = ~30-minute hang.
    calls = client_calls(src, "Anthropic", "AsyncAnthropic")
    check(f"{label}: exactly one Anthropic client constructed (AST)", len(calls) == 1, str(calls))
    check(f"{label}: that client passes timeout AND max_retries (AST)",
          all({"timeout", "max_retries"} <= kw for _, kw in calls), str(calls))
    check(f"{label}: limits come from the shared ai_limits module",
          "timeout=ACCOUNT_AI_TIMEOUT_S" in src and "max_retries=ACCOUNT_AI_MAX_RETRIES" in src)

# ═══════════════════════════════════════════════════════════════════════════════════════════
print("\n[2] Every async endpoint that reaches a blocking site hops off the loop / awaits")
# ═══════════════════════════════════════════════════════════════════════════════════════════
ar, br = read_now(AROUTER), read_now(BROUTER)

check("router: run_in_threadpool imported", "from fastapi.concurrency import run_in_threadpool" in ar)


def hops_to_threadpool(src, endpoint, callee):
    """True when EVERY reference to `callee` inside async endpoint `endpoint` is handed to an
    AWAITED run_in_threadpool — i.e. the blocking work provably cannot run on the event loop.

    Deliberately AST-based and module-agnostic. The earlier form matched the exact source line
    `await run_in_threadpool(engine.compute_and_store, ...)`, so when 386a196d (2026-09-02,
    balance-sheet truths) re-pointed /compute at statement_engine.compute_and_store — same threadpool
    hop, same SEV-1 protection — the literal stopped matching and the harness reported the freeze-class
    fix as missing. Which module supplies compute_and_store is not the safety property; staying off
    the loop is. This also catches what the literal could not: a SECOND, un-hopped call added later.
    """
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == endpoint), None)
    if fn is None:
        return False
    hopped, total = 0, 0
    for n in ast.walk(fn):
        if isinstance(n, ast.Attribute) and n.attr == callee:
            total += 1
    for n in ast.walk(fn):
        if not isinstance(n, ast.Await):
            continue
        c = n.value
        if not (isinstance(c, ast.Call) and getattr(c.func, "id", getattr(c.func, "attr", None))
                == "run_in_threadpool" and c.args):
            continue
        if isinstance(c.args[0], ast.Attribute) and c.args[0].attr == callee:
            hopped += 1
    return total > 0 and hopped == total


check("/compute hops to threadpool (every compute_and_store call is awaited off-loop)",
      hops_to_threadpool(ar, "compute", "compute_and_store"))
check("/run-due hops to threadpool", "await run_in_threadpool(autocompute.recompute_due, sb()," in ar)
check("/recon hops to threadpool", "await run_in_threadpool(recon.reconcile, sb(), org_id, period," in ar)
check("billing: fetch_cost is awaited", "await _pc.fetch_cost(r)" in br)

# no bare (unhopped, unawaited) call left anywhere
for needle, where in ((" engine.compute_and_store(sb()", AROUTER),
                      (" autocompute.recompute_due(sb()", AROUTER),
                      (" recon.reconcile(sb()", AROUTER),
                      (" _pc.fetch_cost(", BROUTER)):
    src = ar if where == AROUTER else br
    bare = [ln.strip() for ln in src.splitlines()
            if needle.strip() in ln and "run_in_threadpool" not in ln and "await" not in ln]
    check(f"no bare sync call `{needle.strip()}` left in {os.path.basename(where)}", not bare, str(bare[:2]))

# ═══════════════════════════════════════════════════════════════════════════════════════════
print("\n[3] Coroutine wiring is real (import-level, no network)")
# ═══════════════════════════════════════════════════════════════════════════════════════════
import inspect  # noqa: E402
from app.modules.billing import platform_costs as pcmod  # noqa: E402
from app.modules.account import ai_limits  # noqa: E402

check("platform_costs.fetch_cost is a coroutine function", inspect.iscoroutinefunction(pcmod.fetch_cost))
check("platform_costs._anthropic_cost is a coroutine function",
      inspect.iscoroutinefunction(pcmod._anthropic_cost))
check("ACCOUNT_AI_TIMEOUT_S sane", 1.0 <= ai_limits.ACCOUNT_AI_TIMEOUT_S <= 600,
      str(ai_limits.ACCOUNT_AI_TIMEOUT_S))
check("ACCOUNT_AI_MAX_RETRIES sane", 0 <= ai_limits.ACCOUNT_AI_MAX_RETRIES <= 5,
      str(ai_limits.ACCOUNT_AI_MAX_RETRIES))
check("worst case well under the SDK default (600s x 3 = 1800s)",
      ai_limits.ACCOUNT_AI_TIMEOUT_S * (1 + ai_limits.ACCOUNT_AI_MAX_RETRIES) < 600)

# env override + garbage-value fallback
import importlib  # noqa: E402
os.environ["ACCOUNT_AI_TIMEOUT_S"] = "12.5"
os.environ["ACCOUNT_AI_MAX_RETRIES"] = "not-a-number"
_re = importlib.reload(ai_limits)
check("ACCOUNT_AI_TIMEOUT_S honours the env var", _re.ACCOUNT_AI_TIMEOUT_S == 12.5)
check("garbage ACCOUNT_AI_MAX_RETRIES falls back (no import crash)", _re.ACCOUNT_AI_MAX_RETRIES == 1)
os.environ.pop("ACCOUNT_AI_TIMEOUT_S"); os.environ.pop("ACCOUNT_AI_MAX_RETRIES")
importlib.reload(ai_limits)

# ═══════════════════════════════════════════════════════════════════════════════════════════
print("\n[4] fetch_cost dispatch/fallback behaviour unchanged (awaited, no network)")
# ═══════════════════════════════════════════════════════════════════════════════════════════
import asyncio  # noqa: E402

cases = [
    ({"provider": "railway", "flat_monthly_cost": 20}, {"cost": 20.0, "status": "manual"}),
    ({"provider": "vercel"}, {"cost": None, "status": "unconfigured"}),
    ({"provider": "anthropic", "credential": "", "flat_monthly_cost": 5}, {"cost": 5.0, "status": "manual"}),
    ({"provider": "", "flat_monthly_cost": 0}, {"cost": 0.0, "status": "manual"}),
]
for conn, want in cases:
    got = asyncio.run(pcmod.fetch_cost(conn))
    ok = all(got.get(k) == v for k, v in want.items())
    check(f"fetch_cost({conn.get('provider') or '(none)'}) -> {want}", ok, str(got))

# live path: a bad credential must NOT raise, and must fall back to the flat figure
got = asyncio.run(pcmod.fetch_cost({"provider": "anthropic", "credential": "sk-ant-bogus",
                                    "flat_monthly_cost": 42}))
check("fetch_cost(anthropic, bad cred) falls back to flat figure without raising",
      got.get("cost") == 42.0 and got.get("status") == "manual", str(got))

# ═══════════════════════════════════════════════════════════════════════════════════════════
print("\n[5] MONEY PROOF — every non-transport function is AST-identical to origin/main")
# ═══════════════════════════════════════════════════════════════════════════════════════════
# Sites we deliberately changed. Everything else must match origin/main exactly.
EXPECTED_DELTA = {
    ENGINE: {"_narrate"},
    RECON: {"_missed_days"},
    AROUTER: {"compute", "run_due", "get_recon"},
    PCOSTS: {"_anthropic_cost", "fetch_cost"},
    BROUTER: {"refresh_platform_costs"},
}

for rel, allowed in EXPECTED_DELTA.items():
    base_f, now_f = funcs(read_base(rel)), funcs(read_now(rel))
    check(f"{os.path.basename(rel)}: no function added/removed", set(base_f) == set(now_f),
          f"+{sorted(set(now_f) - set(base_f))} -{sorted(set(base_f) - set(now_f))}")
    changed = {q for q in set(base_f) & set(now_f) if base_f[q] != now_f[q]}
    # SUBSET, not equality. The money property this guards is "no COMPUTATIONAL function drifted
    # from main" — nothing outside the transport sites. Equality additionally required each listed
    # transport function to still DIFFER from main, which quietly became false as this package
    # merged (main now has the fix, so the delta is legitimately empty) — reporting a failure at the
    # exact moment the fix is fully landed. The presence of the fix is proven directly, and far more
    # strongly, by sections [1] and [2] above; this check's job is the "nothing else moved" half.
    check(f"{os.path.basename(rel)}: no function outside {sorted(allowed)} changed",
          changed <= allowed, f"also changed: {sorted(changed - allowed)}")

# The deterministic money modules must be untouched outright.
for rel in (f"{MOD}/account/coa.py", f"{MOD}/account/autocompute.py",
            f"{MOD}/account/statement_filter.py"):
    check(f"{os.path.basename(rel)}: byte-identical to origin/main", read_base(rel) == read_now(rel))

# And within _narrate / _missed_days, the PROMPT and the response PARSING must be unchanged —
# only the client construction moved.
b_eng, b_rec = read_base(ENGINE), read_base(RECON)
for label, base_src, now_src, parse in (
        ("engine._narrate", b_eng, eng,
         '''text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()'''),
        ("recon._missed_days", b_rec, rec,
         '''text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")''')):
    check(f"{label}: response parsing unchanged", parse in base_src and parse in now_src)

for frag in ('model=settings.ACCOUNT_ENGINE_MODEL,\n            max_tokens=1200,',
             'thinking={"type": "adaptive"},'):
    check(f"engine: model/tokens/thinking unchanged ({frag.splitlines()[0][:38]}…)",
          frag in b_eng and frag in eng)
for frag in ('model=settings.ACCOUNT_ENGINE_MODEL, max_tokens=2000,',
             'thinking={"type": "adaptive"}, output_config={"effort": "medium"},'):
    check(f"recon: model/tokens/thinking unchanged ({frag[:38]}…)", frag in b_rec and frag in rec)

# Deterministic no-API-key fallback text must be identical (that is what ships today).
fallback = '(Set ANTHROPIC_API_KEY for the full narrative.)'
check("engine: no-API-key deterministic narrative unchanged", fallback in b_eng and fallback in eng)

# ═══════════════════════════════════════════════════════════════════════════════════════════
print("\n[6] Threadpool hop preserves return values AND exceptions")
# ═══════════════════════════════════════════════════════════════════════════════════════════
from fastapi.concurrency import run_in_threadpool  # noqa: E402


def _sync_ok(a, b, *, kw=0):
    return {"sum": a + b + kw}


def _sync_boom(*_a, **_k):
    raise ValueError("boom")


async def _drive():
    r = await run_in_threadpool(_sync_ok, 1, 2, kw=3)
    caught = None
    try:
        await run_in_threadpool(_sync_boom, 1)
    except ValueError as e:
        caught = str(e)
    # the hop must not be running on the loop's own thread
    import threading
    loop_thread = threading.current_thread().name
    worker = await run_in_threadpool(lambda: threading.current_thread().name)
    return r, caught, loop_thread, worker

res, caught, loop_thread, worker = asyncio.run(_drive())
check("threadpool returns the same value (kwargs included)", res == {"sum": 6}, str(res))
check("threadpool re-raises the original exception", caught == "boom", str(caught))
check("threadpool really runs off the event-loop thread", worker != loop_thread, f"{loop_thread} vs {worker}")

# ═══════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 78}\n  {len(passed)} passed, {len(failed)} failed")
if failed:
    for f in failed:
        print(f"    FAILED: {f}")
print("=" * 78)
sys.exit(1 if failed else 0)
