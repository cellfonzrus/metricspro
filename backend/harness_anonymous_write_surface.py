#!/usr/bin/env python3
"""PROOF + LOCK — what an ANONYMOUS caller may write, and under whose name (security review 2026-10-28).

Two findings, one shape: a surface that an unauthenticated caller can reach, acting on a fact it
asserted rather than one the server established.

§A — THE CRASH LOG WAS FILED UNDER THE CALLER'S CLAIM.
`app/main.py::_log_system_error` wrote its `core.failure_log` row with

    org_id = request.query_params.get("org_id") or "<house org>"

i.e. the UNVERIFIED `?org_id=` the caller typed. So anyone able to provoke a 500 could write rows —
each carrying a truncated traceback — into any tenant's Failure Logs page.

This was already fixed ONE FUNCTION AWAY. `tenant_middleware._log_identity_outage` files under
`_PLATFORM_ORG_ID` and spells out the reason in its own comment: *"attributing them to the
CALLER-DECLARED org_id would let an unauthenticated caller spam another tenant's Failure Logs page."*
The crash handler was the sibling that was never brought in line — exactly the failure mode CLAUDE.md
names ("one of them fixed and the other not is the same defect wearing a hat"). This section locks
BOTH, and fails the build if a third logging call site is added that reads the query string for its
org.

§B — THE LOCKOUT COUNTS A CLAIM NOBODY VERIFIED.
Sign-in goes directly to Supabase, so the backend never sees whether a password was right; the
frontend posts `{email, success:false}` to the anonymous `/api/v1/core/auth/login-record`, and
`login_guard` counts it. Five posts put any email a stranger knows into the soft-locked state and
repeating it keeps it there, so the login SCREEN refuses a named person indefinitely. (Nothing
server-side refuses the sign-in itself — `login_guard.check` is only read by the pre-login probe the
frontend consults — so this is a denial of service against the screen, not a true account lock. Said
plainly because the severity matters.)

The real fix is for the backend to observe the sign-in, which is an architecture decision for the
owner. What ships here is a COST INCREASE, and it is labelled as one: a dedicated per-IP cap on that
one endpoint. It only bites once `TRUSTED_PROXY_HOPS` is set, because the limiter keys on the client
address — which is why the `client_ip` one-home ships in the same PR. This section proves the cap
exists, is distinct from the general auth tier, and is low enough to matter.

DB-free: every decision under test is pure, and §A reads source text.
Run: python3 backend/harness_anonymous_write_surface.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  -> {detail!r}" if detail is not None else ""))


def read(rel):
    return open(os.path.join(HERE, *rel.split("/")), encoding="utf-8").read()


def code_only(src):
    """`src` with `#` comments stripped, so a check cannot be fooled by PROSE that quotes the very
    line it forbids — the fix's own comment names the old expression, and the first version of this
    harness went red on it. (Same lesson as `encryption_backfill`'s AST scan: a module explaining
    itself is not a call site.) Crude but sufficient: these files carry no `#` inside a string that
    matters to the patterns below."""
    out = []
    for line in src.splitlines():
        i = line.find("#")
        out.append(line if i < 0 else line[:i])
    return "\n".join(out)


print("A. the crash log is filed under the PLATFORM's org, never the caller's claim")
main_src = read("app/main.py")
crash_with_prose = main_src.split("def _log_system_error(", 1)[1].split("\n# HSTS", 1)[0]
crash = code_only(crash_with_prose)
check("_log_system_error no longer reads org_id out of the query string",
      'query_params.get("org_id")' not in crash)
check("…and nowhere else in main.py does either",
      'query_params.get("org_id")' not in code_only(main_src))
check("the old expression survives only as PROSE, so the reason stays readable",
      'query_params.get("org_id")' in crash_with_prose)
check("_log_system_error dereferences _PLATFORM_ORG_ID (the same fact the middleware uses)",
      "_PLATFORM_ORG_ID" in crash)
check("the row's org_id IS that fact", re.search(r'"org_id":\s*org_id', crash) is not None)
check("org_id is assigned from it, not from a request value",
      re.search(r"org_id\s*=\s*_PLATFORM_ORG_ID", crash) is not None)
check("the acting tenant is still recorded, for triage, in detail",
      '"acting_org"' in crash)
check("the acting tenant read is wrapped (a contextvar fault must not replace the real error)",
      re.search(r"try:\s*\n\s*_acting = acting_org\(\)\s*\n\s*except Exception:", crash) is not None)

tm_src = read("app/core/tenant_middleware.py")
outage = tm_src.split("def _log_identity_outage(", 1)[1].split("\nclass ", 1)[0]
check("the sibling that was already right stays right (_log_identity_outage)",
      '"org_id": _PLATFORM_ORG_ID' in outage)
check("_PLATFORM_ORG_ID is env-overridable, not a pinned literal, in its one home",
      re.search(r'_PLATFORM_ORG_ID\s*=\s*os\.environ\.get\(\s*"PLATFORM_ORG_ID"', tm_src) is not None)

print("A2. lock — no logging call site may take its org from the query string")
offenders = []
for root, _dirs, files in os.walk(os.path.join(HERE, "app")):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        rel = os.path.relpath(os.path.join(root, fn), HERE).replace(os.sep, "/")
        body = code_only(open(os.path.join(root, fn), encoding="utf-8", errors="replace").read())
        for m in re.finditer(r'query_params\.get\(\s*[\'"]org_id[\'"]\s*\)', body):
            # Only a write into one of the audit/diagnostic tables is the defect; a HANDLER reading
            # its own org_id param is normal and is what the middleware rewrites.
            window = body[max(0, m.start() - 1500):m.start() + 1500]
            if re.search(r'table\(\s*[\'"](failure_log|access_log|log|auth_event|impersonation_action)[\'"]', window):
                offenders.append(f"{rel}:{body[:m.start()].count(chr(10)) + 1}")
check("no audit/diagnostic write takes its org_id from the query string", offenders == [], offenders)

print("B. the client-asserted login outcome has its own, much stricter cap")
from app.core import rate_limit as RL   # noqa: E402

check("/auth/login-record is classified as its own tier",
      RL._is_login_record_path("/api/v1/core/auth/login-record") is True)
check("the pre-login PROBE is not in that tier (it writes nothing)",
      RL._is_login_record_path("/api/v1/core/auth/login-precheck") is False)
check("it is NOT swallowed by the password-reset tier",
      RL._is_reset_path("/api/v1/core/auth/login-record") is False)
check("it is matched at a segment boundary, not by a loose startswith",
      RL._is_login_record_path("/api/v1/core/auth/login-recorder") is False)
lim, win = RL._login_record_limit()
check("the cap is per hour, not per minute", win == 3600, (lim, win))
check("the cap is below what the general auth tier would allow in an hour",
      lim < RL._limits()[1] * 60, (lim, RL._limits()[1]))
check("the cap is above an honest human's worst hour (mistyped passwords + reloads)",
      lim >= 8, lim)
check("the cap is env-tunable without a deploy",
      "RATE_LIMIT_LOGIN_RECORD_PER_HOUR" in read("app/core/rate_limit.py"))
os.environ["RATE_LIMIT_LOGIN_RECORD_PER_HOUR"] = "4"
try:
    check("…and the env value is honoured", RL._login_record_limit()[0] == 4)
finally:
    os.environ.pop("RATE_LIMIT_LOGIN_RECORD_PER_HOUR", None)

print("B2. the cap actually refuses, on its own key")
rl = RL.RateLimiter()
t = 1000.0
lim, win = RL._login_record_limit()
for i in range(lim):
    allowed, _ = rl.check("loginrec:1.2.3.4", lim, win, t + i)
    check(f"post {i + 1} of {lim} allowed", allowed is True)
allowed, retry = rl.check("loginrec:1.2.3.4", lim, win, t + lim)
check("the next post in the window is refused with a retry-after", allowed is False and retry >= 1,
      (allowed, retry))
check("a DIFFERENT address is unaffected (the key is per-IP)",
      rl.check("loginrec:5.6.7.8", lim, win, t + lim)[0] is True)
check("the general tier is a separate bucket, so the cap cannot throttle ordinary traffic",
      rl.check("gen:1.2.3.4", RL._limits()[0], 60, t + lim)[0] is True)

print("B3. the limiter keys on the ONE-HOME client address, not a re-derived one")
rl_src = read("app/core/rate_limit.py")
check("rate_limit reads the client address from app.core.client_ip",
      "client_ip" in rl_src and "from_scope" in rl_src)
check("rate_limit no longer imports the old helper from tenant_middleware",
      "_client_ip_from" not in code_only(rl_src))
check("the access log reads the same one home",
      "client_ip" in read("app/core/access_log.py"))
check("the cap's own comment says plainly that it is a cost increase, not the fix",
      "NOT THE FIX" in rl_src or "not the fix" in rl_src)

print("N. negative controls")
check("control: the PRE-FIX crash-log line really did read the caller's org",
      'query_params.get("org_id")'
      in 'org_id = request.query_params.get("org_id") or "0000"')
check("control: §A2's detector fires on a failure_log write that reads the query string",
      bool(re.search(r'query_params\.get\(\s*[\'"]org_id[\'"]\s*\)',
                     'o = request.query_params.get("org_id")\n'
                     'get_supabase().schema("core").table("failure_log").insert({})')))
check("control: §B2 exercised a real refusal (the cap is a positive number)", lim > 0, lim)
check("control: the login-record endpoint really is on the public allowlist (so the cap is the "
      "only thing in front of it)",
      "/api/v1/core/auth/login-record" in tm_src)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
