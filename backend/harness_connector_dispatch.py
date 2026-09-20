"""DB-FREE PROOF — where a sweep RUNS, and what "late" means for the connector that is late.

OWNER DIRECTIVE 2026-09-20: *"fix the epay and vip connectors"*. They were two different problems, and
only one of them was a broken connector.

────────────────────────────────────────────────────────────────────────────────────────────────────
ePay — REAL, and the fix already existed for a caller that was superseded.

    last_status  "error"
    last_detail  "Sweep failed: Browser/portal sweeps do not run on the user-facing API service
                  (SERVICE_ROLE=api). Trigger this on the sweeps worker."
    last_run_at  2026-08-24      last_attempt_at  2026-09-20 20:05   <- attempting daily, failing daily

`/epay/sweep/run-due` HAS `require_browser_service()`, so on a split deploy the API service proxies it
to the sweeps worker (main.py's BrowserWorkProxy handler). Then `/connectors/run-due` — *"ONE pg_cron
entrypoint that fans out to every connector … replacing the per-vendor /{vendor}/sweep/run-due
crons"* — was written WITHOUT that guard, so the generic tick invoked the puller in-process and
`assert_browser_allowed()` raised deep inside the sweep. The per-vendor fix was applied; the caller
that replaced it was not wired to it. `/vip`, `/dlar` and `/b2b` run-due were unguarded too.

────────────────────────────────────────────────────────────────────────────────────────────────────
VIP — NOT BROKEN. It is WEEKLY.

    frequency    weekly        next_run_at  2026-09-25
    last_run_at  2026-09-18    last_status  "OK — 17 invoices, 55 lines, 175 devices … 34794 rows"

The scan applied one 30-hour window to every connector, so a healthy weekly sweep was reported STALLED
every day between its runs. `_connector_stale_hours_map` already made the window per-TENANT for this
exact reason — its own comment says *"a weekly distributor sweep is not late at 31h"* — but one value
per org cannot express two connectors on different cadences: a tenant with any weekly connector had to
accept the false alarm or widen the window for its daily ones and miss a real outage.

Run:  cd backend && python3 harness_connector_dispatch.py
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

ROUTER = "app/modules/commcalc/router.py"
PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def eq(name, got, want):
    ok(name, got == want, f"got={got!r} want={want!r}")


def _src(rel):
    return open(os.path.join(_HERE, rel), encoding="utf-8").read()


def _func_src(rel, name):
    text = _src(rel)
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"function {name!r} not found in {rel}")


RSRC = _src(ROUTER)
_ns = {}
for name in ("_CONNECTOR_CADENCE_HOURS", "_BROWSER_SWEEP_KINDS", "_SWEEP_BUILTINS"):
    for node in ast.parse(RSRC).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            _ns[name] = ast.literal_eval(node.value) if name != "_BROWSER_SWEEP_KINDS" else None
_ns["_CONNECTOR_CADENCE_HOURS"] = _ns["_CONNECTOR_CADENCE_HOURS"]
exec(_func_src(ROUTER, "_connector_stale_window"), _ns)
window = _ns["_connector_stale_window"]
BROWSER = set(eval(RSRC.split("_BROWSER_SWEEP_KINDS = frozenset(", 1)[1].split(")", 1)[0]))

print("\n§A  VIP — late is measured against the connector's OWN cadence")
eq("A1 the live VIP row: weekly ⇒ a 336h window, not 30h",
   window({"frequency": "weekly"}, 30), (336.0, "weekly"))
ok("A2 …so VIP at 54h since a successful run is NOT stale", 54 <= window({"frequency": "weekly"}, 30)[0])
ok("A3 …and a weekly connector that really has stopped IS still caught, at 2x its own period",
   400 > window({"frequency": "weekly"}, 30)[0])
eq("A4 daily ⇒ 48h (one cadence + one cadence of grace)",
   window({"frequency": "daily"}, 30), (48.0, "daily"))
eq("A5 hourly ⇒ 2h — a minute-by-minute feed is late fast", window({"frequency": "hourly"}, 30), (2.0, "hourly"))
eq("A6 monthly ⇒ 1488h", window({"frequency": "monthly"}, 30)[0], 1488.0)
ok("A7 a DAILY connector is still caught at 49h — the fix does not blanket-widen anything",
   49 > window({"frequency": "daily"}, 30)[0])

print("\n§B  …and an unknown cadence changes nothing")
eq("B1 no frequency at all (the portal data_source registry) keeps the ORG default",
   window({}, 30), (30.0, "unscheduled"))
eq("B2 …including a tenant that tuned it", window({}, 72), (72.0, "unscheduled"))
eq("B3 an unrecognised frequency slug also keeps the org default, never a guess",
   window({"frequency": "fortnightly-ish"}, 30), (30.0, "fortnightly-ish"))
eq("B4 a None row is survivable", window(None, 30), (30.0, "unscheduled"))
ok("B5 the scan reports WHICH schedule it judged against, so the number is never mysterious",
   "(schedule: {_freq})" in _func_src(ROUTER, "_scan_connector_health"))

print("\n§C  ePay — browser work is dispatched to the service that can do it")
# CORRECTED 2026-09-20, hours after it shipped: this first read {vip, dlar, epay, b2b} — every kind
# that pulls from a vendor PORTAL. "Logs into a portal" is not "launches Chromium": dlar, vip and b2b
# scrape over requests + BeautifulSoup and import playwright nowhere, so declaring them would have
# REFUSED connectors that work (dlar had imported successfully that morning; vip four days earlier).
# The derivation itself lives in harness_browser_sweep_kinds.py, which fails the build in EITHER
# direction; what is pinned here is that this dispatcher's set is the browser-only one.
eq("C1 only the sweep kind that actually launches Chromium is declared", BROWSER, {"epay"})
ok("C1b …and the HTTP scrapers are explicitly not in it",
   not ({"vip", "dlar", "b2b"} & BROWSER),
   "declaring a requests-based sweep would block a connector that runs fine on the API service")
ok("C2 …and every one of them has a puller (the sets cannot drift apart unnoticed)",
   BROWSER <= set(_ns["_SWEEP_BUILTINS"]), (BROWSER, set(_ns["_SWEEP_BUILTINS"])))
ok("C3 an EXTERNALLY registered kind is not assumed to need a browser",
   "register_sweep" in RSRC and "google_closing" not in BROWSER,
   "a third-party puller must never be blocked by a guess about what it launches")
DUE = _func_src(ROUTER, "connectors_run_due")
ok("C4 the generic dispatcher asks before invoking a browser puller in-process",
   "_BROWSER_SWEEP_KINDS and not _browser_allowed()" in DUE)
ok("C5 with a sweeps worker configured it forwards the WHOLE tick, reusing the existing proxy",
   "raise BrowserWorkProxy()" in DUE and "_browser_service_url()" in DUE)
ok("C6 with NOWHERE to forward it, the connector's own row says so — an attempt, never a run",
   "mark_run=True, success=False" in DUE and "BROWSER_SERVICE_URL is not set" in DUE)
ok("C7 …and next_run_at is NOT advanced by a refusal (the schedule was not satisfied)",
   DUE.index("blocked.append") < DUE.index("nra = cf.get('next_run_at')"))
# Slice the REFUSAL BLOCK itself — from the browser test to its `continue` — instead of guessing a
# character width. A fixed window silently measures the wrong thing the moment the block grows.
_blk = DUE.split("if kind in _BROWSER_SWEEP_KINDS", 1)[1]
_blk = _blk[:_blk.index("continue") + len("continue")]
ok("C8 the refusal ends in `continue`, so the non-browser sweeps in the same tick still run",
   _blk.rstrip().endswith("continue") and "dispatch[kind]" not in _blk,
   "the fan-out must not be lost because one connector needs a browser")
ok("C9 a blocked connector is named in the response, never silently skipped",
   '"blocked": blocked' in DUE)

print("\n§D  every scheduled entry point that can launch Chromium is guarded")
# Each handler is located by PARSING, not by slicing a guessed number of characters after its
# decorator — /data-sources/sweep/run-due carries a ~1,000-character docstring, so a fixed window
# reads the prose and concludes the guard is missing.
# GUARDED — these reach playwright, so on a SERVICE_ROLE=api service they must proxy, not run.
_HANDLER = {"/epay/sweep/run-due": "epay_sweep_run_due",
            "/data-sources/sweep/run-due": "data_sources_run_due"}
# UNGUARDED ON PURPOSE — requests + BeautifulSoup. A guard here is not a harmless extra: with no
# BROWSER_SERVICE_URL it 503s a sweep that would have worked, and with one it ships the tick to
# another service for no reason. Three of these were guarded for a few hours on 2026-09-20.
_NO_BROWSER = {"/vip/sweep/run-due": "vip_sweep_run_due",
               "/dlar/sweep/run-due": "dlar_sweep_run_due",
               "/b2b/sweep/run-due": "b2b_sweep_run_due",
               "the FTP run-due": "ftp_run_due", "the mailbox run-due": "email_run_due"}
for ep, fn in _HANDLER.items():
    body = _func_src(ROUTER, fn)
    ok(f"D1 {ep} calls require_browser_service()", "require_browser_service()" in body)
for ep, fn in _NO_BROWSER.items():
    body = _func_src(ROUTER, fn)
    ok(f"D1b {ep} does NOT — it never launches a browser", "require_browser_service()" not in body,
       "guarding it would refuse a sweep that runs fine on the API service")
ok("D2 the secret check still comes FIRST — an unauthenticated caller learns nothing about the deploy",
   all(_func_src(ROUTER, fn).index("verify_notify_secret")
       < _func_src(ROUTER, fn).index("require_browser_service()")
       for fn in _HANDLER.values()))
ok("D3 the guarded set is exactly the sweeps that can launch Chromium",
   all("require_browser_service()" in _func_src(ROUTER, fn) for fn in _HANDLER.values())
   and not any("require_browser_service()" in _func_src(ROUTER, fn)
               for fn in _NO_BROWSER.values()))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
