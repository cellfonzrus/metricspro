"""Proof harness for the SERVICE_ROLE browser guard (app/core/service_role.py).

Run from backend/:  python harness_service_role.py

Proves two things:
  (A) The guard's truth table is OPT-IN blocking — browsers are ALLOWED by default and only
      BLOCKED when SERVICE_ROLE is explicitly api/web (case-insensitive). Unset / sweeps / worker /
      all / empty / anything-else → allowed, require/assert do NOT raise.
  (B) Every real Playwright/Chromium launch site in the production sweep modules has an
      assert_browser_allowed()/require_browser_service() guard directly above it — so no code path
      can spawn Chromium on the API service.
"""
import importlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_failures = []


def check(cond, label):
    print(("  PASS " if cond else "  FAIL ") + label)
    if not cond:
        _failures.append(label)


def _reload_with_role(role):
    """Set (or unset) SERVICE_ROLE, then reimport the module so service_role() re-reads env."""
    if role is None:
        os.environ.pop("SERVICE_ROLE", None)
    else:
        os.environ["SERVICE_ROLE"] = role
    import app.core.service_role as sr
    importlib.reload(sr)
    return sr


# ── (A) truth table ──────────────────────────────────────────────────────────────────────────────
print("[A] SERVICE_ROLE truth table (opt-IN blocking — default ALLOWS browsers)")

# HTTPException is what require_browser_service raises; grab the class for isinstance checks.
from fastapi import HTTPException

ALLOWED_ROLES = [None, "sweeps", "worker", "all", "", "  ", "SWEEPS", "cron", "background"]
for role in ALLOWED_ROLES:
    sr = _reload_with_role(role)
    shown = "<unset>" if role is None else repr(role)
    check(sr.browser_allowed() is True, f"browser_allowed() True when SERVICE_ROLE={shown}")
    ok = True
    try:
        sr.require_browser_service()   # must NOT raise
        sr.assert_browser_allowed()    # must NOT raise
    except Exception as e:
        ok = False
        print("       unexpected raise:", repr(e))
    check(ok, f"require/assert do NOT raise when SERVICE_ROLE={shown}")

BLOCKED_ROLES = ["api", "API", "Api", "web", "WEB", " api ", "api\t"]
for role in BLOCKED_ROLES:
    sr = _reload_with_role(role)
    check(sr.browser_allowed() is False, f"browser_allowed() False when SERVICE_ROLE={role!r}")
    # require_browser_service → HTTPException 503 with the guidance detail
    raised = None
    try:
        sr.require_browser_service()
    except HTTPException as e:
        raised = e
    except Exception as e:
        raised = e
    check(isinstance(raised, HTTPException) and raised.status_code == 503,
          f"require_browser_service() raises HTTPException(503) when SERVICE_ROLE={role!r}")
    check(isinstance(raised, HTTPException) and "sweeps worker" in str(raised.detail),
          f"503 detail carries the guidance message when SERVICE_ROLE={role!r}")
    # assert_browser_allowed → RuntimeError with the SAME message
    rt = None
    try:
        sr.assert_browser_allowed()
    except RuntimeError as e:
        rt = e
    except Exception as e:
        rt = e
    check(isinstance(rt, RuntimeError) and "sweeps worker" in str(rt),
          f"assert_browser_allowed() raises RuntimeError with guidance when SERVICE_ROLE={role!r}")

# banner sanity
sr = _reload_with_role("api")
check("blocked" in sr.role_banner() and "api" in sr.role_banner(), "role_banner() reads 'api ... blocked'")
sr = _reload_with_role(None)
check("allowed" in sr.role_banner() and "unset" in sr.role_banner(), "role_banner() reads '<unset> ... allowed'")

# ── (B) every launch site is guarded ───────────────────────────────────────────────────────────────
print("\n[B] Every Playwright/Chromium launch site in production sweep modules is guarded")

LAUNCH_FILES = [
    "app/modules/commcalc/vidapay_sweep.py",
    "app/modules/commcalc/epay_sweep.py",
    "app/modules/commcalc/live_login.py",
]
GUARD_RE = re.compile(r"assert_browser_allowed\(|require_browser_service\(")
# A "launch" is a real browser spawn: a `with (sync|async)_playwright() as ...` context entry or a
# chromium.launch( call. Import lines, comments and docstrings that merely mention sync_playwright()
# in prose are NOT launches and are excluded.
LAUNCH_RE = re.compile(r"(with\s+(sync|async)_playwright\(\)|chromium\.launch\()")
IMPORT_RE = re.compile(r"^\s*(from|import)\s")
COMMENT_RE = re.compile(r"^\s*#")
WINDOW = 8   # a guard must appear within this many lines above the launch site

total_sites = 0
for rel in LAUNCH_FILES:
    path = os.path.join(HERE, rel)
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines):
        if IMPORT_RE.match(line) or COMMENT_RE.match(line):
            continue
        if not LAUNCH_RE.search(line):
            continue
        total_sites += 1
        lo = max(0, i - WINDOW)
        window = "".join(lines[lo:i + 1])
        guarded = bool(GUARD_RE.search(window))
        check(guarded, f"{rel}:{i+1} guarded ({line.strip()[:60]})")

check(total_sites >= 9, f"found and checked all launch sites (n={total_sites})")

# Note: harness_*.py and scratchpad/*.py proof scripts also contain sync_playwright(), but they are
# standalone test scaffolding that never runs in a Railway deploy and shadow Playwright with fakes —
# they are intentionally NOT part of the production guard surface.

print("\n" + ("ALL PASS" if not _failures else f"{len(_failures)} FAILURE(S): " + "; ".join(_failures)))
sys.exit(1 if _failures else 0)
