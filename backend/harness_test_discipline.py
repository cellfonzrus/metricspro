"""LOCK — the test framework cannot quietly un-wire itself (project directive 2026-10-28).

OWNER DIRECTIVE 2026-10-28, verbatim: *"We need to define all development we do to follow the
following / Unit testing / Integration testing / Test driven development"*.

THE CLASS THIS EXISTS TO PREVENT. This repo has 597 `harness_*.py` proof programs and, before this
change, **no unit-test runner, no integration-test runner, and no CI job anywhere that runs a
test** — 330 of those harnesses are run by nothing (`harness_unrun_pending.txt`), and so were all 12
frontend proofs. (The frontend's typecheck and lint ratchet were already gated, inside
`carrier-vocab-guard.yml`; it was tests that had no job.) The general fact behind that is the one
the house already named in §19.25: *a proof that nothing executes is not a gate, it is a file.* A test framework is the same shape and fails the same way, only faster — a
`pytest.ini` whose `testpaths` drifts, a conftest whose network guard is deleted "to debug one
thing", a CI job that installs the runner but never calls it. Each of those leaves the suite green
and meaningless, and nobody finds out until production is wrong.

So the framework ships with the check that fails the build if any load-bearing piece comes undone.

WHAT IT PROVES
  A  THE RUNNER IS CONFIGURED — pytest.ini names the suite and runs under --strict-markers, and the
     test-only dependencies are pinned in requirements-dev.txt.
  B  PRODUCTION IS UNREACHABLE FROM A TEST, and this is proved by RUNNING the guard, not by reading
     it: conftest's blocker is imported and given a real non-loopback address. There is no staging
     database here — every environment points at the same live Supabase — so this is the single
     most important assertion in the file.
  C  THE CREDENTIALS CONFTEST BLANKS ARE EXACTLY THE DECLARED SET. Compared BOTH WAYS (§CORE-10): a
     variable that leaves the blanking list names itself here instead of silently going live.
  D  BOTH SUITES EXIST AND BOTH ARE RUN BY CI — the declared jobs in .github/workflows/tests.yml,
     the declared npm scripts, and the frontend's own fetch blocker.
  E  ONE HOME FOR THE DATABASE DOUBLE — a second in-memory Supabase stub anywhere under tests/ is
     the duplicate defect the house rules forbid, wearing a test's hat.
  F  RULE TWO — no tenant, carrier or company name anywhere in the framework.

Stdlib only, no DB, no network:  cd backend && python3 harness_test_discipline.py
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "tests.yml")
FRONTEND = os.path.join(ROOT, "frontend")

# ── DECLARED SUBJECT SETS (§CORE-10: derived sets are compared with these BOTH WAYS) ─────────────

# Every credential tests/conftest.py blanks before the app is imported. A name leaving this set is a
# credential a test could suddenly use for real.
BLANKED_CREDENTIALS = {
    "SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "RESEND_API_KEY", "NOTIFY_RUN_SECRET",
    "FIX_PIPELINE_SECRET", "HELPDESK_EXPORT_SECRET", "WHATSAPP_ACCESS_TOKEN",
    "ANTHROPIC_API_KEY", "DATABASE_URL", "DATABASE_WRITE_URL",
}

# The two suites. Unit = pure logic. Integration = the real app over the double.
SUITES = {"unit", "integration"}

# Commands .github/workflows/tests.yml must actually run. A job that installs a runner and never
# calls it is the "green and meaningless" shape this lock exists for.
REQUIRED_CI_COMMANDS = {
    "python -m pytest tests -m unit",
    "python -m pytest tests -m integration",
    "npm run test",
    "npm run test:proofs",
    "python harness_test_discipline.py",
}

# npm scripts the frontend must expose, so a developer runs locally what CI runs.
REQUIRED_NPM_SCRIPTS = {"test", "test:watch", "test:proofs"}

# RULE TWO — no tenant / carrier / company name may appear in the framework files.
FORBIDDEN_NAMES = ("boost", "luxelink", "cellfonz", "vzone", "verizon", "t-mobile", "metro")

FRAMEWORK_FILES = [
    "backend/pytest.ini",
    "backend/requirements-dev.txt",
    "backend/tests/conftest.py",
    "backend/tests/fakes/supabase.py",
    "frontend/vitest.config.ts",
    "frontend/tests/setup.ts",
    "frontend/tools/run-proofs.mjs",
]

_FAIL: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label + (("\n        " + detail) if (detail and not ok) else ""))
    if not ok:
        _FAIL.append(label)


def eq(derived, declared, label: str) -> None:
    """Compare a derived set with the declared one BOTH WAYS, naming what differs."""
    derived, declared = set(derived), set(declared)
    missing = sorted(declared - derived)
    extra = sorted(derived - declared)
    check(label, not missing and not extra,
          (("not found in the code: %s. " % ", ".join(missing)) if missing else "")
          + (("found but not declared here: %s" % ", ".join(extra)) if extra else ""))


def read(rel: str) -> str:
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ── A. the runner is configured ──────────────────────────────────────────────────────────────────
def check_runner_configured() -> None:
    ini = read("backend/pytest.ini")
    check("A1 backend/pytest.ini exists", bool(ini))
    check("A2 pytest.ini points at the tests/ suite", bool(re.search(r"^testpaths\s*=\s*tests\s*$", ini, re.M)))
    check("A3 pytest.ini runs under --strict-markers, so a typo'd marker is an error not a silent skip",
          "--strict-markers" in ini)
    markers_block = re.search(r"^markers\s*=\s*$(.*?)^\w", ini, re.M | re.S)
    eq(set(re.findall(r"^\s+(\w+):", markers_block.group(1), re.M)) if markers_block else set(),
       SUITES, "A4 pytest.ini declares exactly the two suites")

    dev = read("backend/requirements-dev.txt")
    check("A5 requirements-dev.txt pins pytest", bool(re.search(r"^pytest>=", dev, re.M)))
    check("A6 requirements-dev.txt builds on the real requirements, so tests run the shipped stack",
          "-r requirements.txt" in dev)


# ── B. production is unreachable from a test — PROVED BY RUNNING THE GUARD ───────────────────────
def check_network_guard_actually_blocks() -> None:
    conf = read("backend/tests/conftest.py")
    check("B1 conftest replaces socket.socket.connect", "socket.socket.connect = _guarded_connect" in conf)
    check("B2 conftest replaces socket.create_connection",
          "socket.create_connection = _guarded_create_connection" in conf)

    # Imported lazily: this harness is stdlib-only at module level by house rule, and the import
    # pulls pytest in. The CI job that runs this harness installs requirements-dev.txt.
    sys.path.insert(0, HERE)
    try:
        from tests.conftest import OutboundNetworkBlocked, _guarded_create_connection
    except ImportError as exc:                                    # pragma: no cover - CI config error
        check("B3 conftest imports", False, "cannot import tests.conftest: %r" % (exc,))
        return

    blocked = False
    try:
        _guarded_create_connection(("etxdalernqqtwjcrtcuj.supabase.co", 443), 1)
    except OutboundNetworkBlocked:
        blocked = True
    except Exception as exc:                                      # pragma: no cover
        check("B3 a connection to production raises OutboundNetworkBlocked", False,
              "it raised %r instead — the guard did not fire" % (exc,))
        return
    check("B3 a connection to production is REFUSED by the guard (run, not read)", blocked,
          "the guard let a non-loopback address through — a test can reach live Supabase")

    passed_through = False
    try:
        _guarded_create_connection(("127.0.0.1", 1), 0.01)
    except OutboundNetworkBlocked:
        pass
    except OSError:
        passed_through = True                                     # refused by the OS, not the guard
    check("B4 loopback still reaches the real socket, so TestClient and local fixtures work",
          passed_through, "the guard blocked 127.0.0.1 — it would block the test suite itself")


# ── C. the blanked credentials are exactly the declared set ──────────────────────────────────────
def check_credentials_blanked() -> None:
    conf = read("backend/tests/conftest.py")
    block = re.search(r'for _var in \((.*?)\):', conf, re.S)
    derived = set(re.findall(r'"([A-Z_]+)"', block.group(1))) if block else set()
    eq(derived, BLANKED_CREDENTIALS, "C1 conftest blanks exactly the declared credentials")


# ── D. both suites exist and CI runs them ────────────────────────────────────────────────────────
def check_suites_and_ci() -> None:
    derived_suites = {d for d in SUITES | {"unit", "integration"}
                      if os.path.isdir(os.path.join(HERE, "tests", d))}
    eq(derived_suites, SUITES, "D1 both test suites exist on disk")
    for suite in sorted(SUITES):
        files = [f for f in os.listdir(os.path.join(HERE, "tests", suite)) if f.startswith("test_")]
        check("D2 tests/%s holds at least one test module" % suite, bool(files),
              "an empty suite is a green gate that proves nothing")

    # A test module at tests/ root carries NEITHER marker, so neither CI job runs it — a test file
    # nobody executes, which is the very class this lock exists for. Found live on 2026-10-28: the
    # repo's one pre-existing pytest file sat exactly there.
    stray = sorted(f for f in os.listdir(os.path.join(HERE, "tests")) if f.startswith("test_"))
    check("D2b no test module sits outside the two suites, where no CI job would run it",
          not stray, "unmarked and therefore unrun: " + ", ".join(stray))

    wf = read(".github/workflows/tests.yml")
    check("D3 .github/workflows/tests.yml exists", bool(wf))
    # The pipefail lock's rule, restated here so this workflow cannot be the one that forgets it.
    check("D4 the test workflow declares `shell: bash` at top level (pipefail)",
          bool(re.search(r"^defaults:\s*$", wf, re.M)) and "shell: bash" in wf)
    missing = sorted(c for c in REQUIRED_CI_COMMANDS if c not in wf)
    check("D5 the test workflow RUNS every declared command, it does not merely install them",
          not missing, "not run by tests.yml: " + ", ".join(missing))

    pkg = read("frontend/package.json")
    derived_scripts = {s for s in REQUIRED_NPM_SCRIPTS if '"%s":' % s in pkg}
    eq(derived_scripts, REQUIRED_NPM_SCRIPTS, "D6 the frontend exposes exactly the declared test scripts")

    vc = read("frontend/vitest.config.ts")
    check("D7 vitest loads the shared setup file", "./tests/setup.ts" in vc)
    setup = read("frontend/tests/setup.ts")
    check("D8 the frontend setup refuses a real fetch, the mirror of the backend socket guard",
          "OutboundNetworkBlocked" in setup and "stubGlobal('fetch'" in setup)


# ── E. one home for the database double ──────────────────────────────────────────────────────────
def check_one_double() -> None:
    homes = []
    for dirpath, _dirs, files in os.walk(os.path.join(HERE, "tests")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
            if re.search(r"^class \w*(Fake|Stub|Mock)\w*(Supabase|Postgrest|Client)\b", src, re.M):
                homes.append(os.path.relpath(path, ROOT))
    eq(homes, {"backend/tests/fakes/supabase.py"},
       "E1 the in-memory database double has exactly ONE home")

    double = read("backend/tests/fakes/supabase.py")
    check("E2 the double REFUSES an unfiltered delete (the shape that emptied 11,000 live rows)",
          "OutboundWriteRefused" in double and "delete on %s" not in double
          and 'would empty the table' in double)


# ── F. RULE TWO ──────────────────────────────────────────────────────────────────────────────────
def check_rule_two() -> None:
    offenders = []
    for rel in FRAMEWORK_FILES:
        low = read(rel).lower()
        for word in FORBIDDEN_NAMES:
            if word in low:
                offenders.append("%s mentions %r" % (rel, word))
    check("F1 RULE TWO — no tenant or carrier name in the test framework", not offenders,
          "; ".join(offenders))
    eq([os.path.basename(p) for p in FRAMEWORK_FILES if read(p)],
       [os.path.basename(p) for p in FRAMEWORK_FILES],
       "F2 every declared framework file exists")


def main() -> int:
    print(__doc__.strip().splitlines()[0])
    print("-" * 96)
    check_runner_configured()
    check_network_guard_actually_blocks()
    check_credentials_blanked()
    check_suites_and_ci()
    check_one_double()
    check_rule_two()
    print("-" * 96)
    if _FAIL:
        print("RED — %d check(s) failed:" % len(_FAIL))
        for f in _FAIL:
            print("  · " + f)
        return 1
    print("OK — the test framework is wired, the suites exist, CI runs them, and no test can "
          "reach production.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
