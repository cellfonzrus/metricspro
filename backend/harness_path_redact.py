#!/usr/bin/env python3
"""CREDENTIALS IN REQUEST PATHS — proof for app/core/path_redact.py. DB-free, stdlib only.

WHAT THIS DEFENDS (owner question, 2026-09-20: *"why does our url have secrets — should all of those
be masked?"*). The URLs are right: `/r/{token}` and `/onboard/{token}` are capability URLs for flows
with nobody to authenticate yet. What was wrong is that `core.access_log` wrote one row per request
carrying `path[:400]` and `query[:400]`, with none of the twelve token-bearing endpoints skipped — so
a live token, and on the onboarding document routes the `?value=<date-of-birth>` gate beside it, were
stored in plaintext for the life of the log.

§A  the pure redaction — what is masked, and what is deliberately KEPT
§B  the backstop — 404s and odd shapes under a capability prefix are still caught
§C  fail-closed — garbage never raises, and never returns the thing it was meant to hide
§D  the query — dropped on a capability route, untouched everywhere else
§E  THE LOCK — every logging call site still dereferences the module, and a newly added
    credential-shaped path parameter fails the build until it is classified
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.core import path_redact as R            # noqa: E402

PASS = FAIL = 0
HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app")


def ok(cond, msg, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s%s" % (msg, (" " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  ✗ %s%s" % (msg, (" " + extra) if extra else ""))


def eq(got, want, msg):
    ok(got == want, msg, "" if got == want else "\n        got  %r\n        want %r" % (got, want))


# The real token-bearing templates, spelled as the app mounts them. Fixtures, not a second home: §E
# proves the live call sites read the app's own route table rather than any list written here.
ROUTES = [
    "/api/v1/referral/redeem/{token}",
    "/api/v1/hr/public/onboarding/{token}",
    "/api/v1/hr/public/onboarding/{token}/upload",
    "/api/v1/hr/public/onboarding/{token}/sign",
    "/api/v1/hr/public/onboarding/{token}/intake",
    "/api/v1/hr/public/onboarding/{token}/task/{task_id}/document/{file_id}",
    # non-sensitive neighbours — these must come through completely untouched
    "/api/v1/commcalc/rep-commissions/{period}",
    "/api/v1/hr/employees/{employee_id}/onboarding",
    "/api/v1/commcalc/commission-buckets/{key}",
    "/health",
]
TOK = "IQhJ8nJ2nQ3x_Kq0vUo1bZ7t"          # shaped like secrets.token_urlsafe(24); not a real token


def main():
    rules = R.compile_rules(ROUTES)

    print("§A  THE PURE REDACTION — the credential goes, the audit value stays")
    eq(R.redact("/api/v1/referral/redeem/" + TOK, rules),
       "/api/v1/referral/redeem/{token}",
       "the referral capability token is masked")
    eq(R.redact("/api/v1/hr/public/onboarding/" + TOK, rules),
       "/api/v1/hr/public/onboarding/{token}",
       "the onboarding token is masked")
    eq(R.redact("/api/v1/hr/public/onboarding/%s/upload" % TOK, rules),
       "/api/v1/hr/public/onboarding/{token}/upload",
       "...and on every sub-route of it")
    # THE POINT OF THE DESIGN: only the secret goes. The ids are why the log is worth keeping.
    eq(R.redact("/api/v1/hr/public/onboarding/%s/task/9f3c/document/aa12" % TOK, rules),
       "/api/v1/hr/public/onboarding/{token}/task/9f3c/document/aa12",
       "task_id and file_id SURVIVE — you can still see who touched which file")
    eq(R.redact("/api/v1/commcalc/rep-commissions/2026-07", rules),
       "/api/v1/commcalc/rep-commissions/2026-07",
       "a route with no credential is returned byte-identical")
    eq(R.redact("/api/v1/commcalc/commission-buckets/tablet", rules),
       "/api/v1/commcalc/commission-buckets/tablet",
       "`{key}` is a business identifier and is NOT masked (it would cost audit value for nothing)")
    eq(R.redact("/api/v1/hr/employees/8f3c-44/onboarding", rules),
       "/api/v1/hr/employees/8f3c-44/onboarding",
       "an employee id on an AUTHENTICATED route is left alone — it is not a credential")
    eq(R.redact("/health", rules), "/health", "an unparameterised route is untouched")
    ok(TOK not in R.redact("/api/v1/referral/redeem/" + TOK, rules),
       "the token value appears NOWHERE in the redacted output")

    print("\n§B  THE BACKSTOP — what the precise layer cannot match is still caught")
    eq(R.redact("/api/v1/hr/public/onboarding/%s/" % TOK, rules),
       "/api/v1/hr/public/onboarding/{token}/",
       "a trailing slash does not slip a token through")
    eq(R.redact("/api/v1/hr/public/onboarding/%s/nope/deeper" % TOK, rules),
       "/api/v1/hr/public/onboarding/{token}/nope/deeper",
       "a 404 probe under the prefix — no route matches, the token is masked anyway")
    ok(TOK not in R.redact("/api/v1/hr/public/onboarding/%s/nope/deeper" % TOK, rules),
       "...and the stolen token is not sitting in the 404's log row")
    eq(R.redact("/api/v1/referral/redeem/%s/extra" % TOK, rules),
       "/api/v1/referral/redeem/{token}/extra",
       "an extra segment on the referral route is covered by the same prefix")
    eq(R.redact("/api/v1/hr/public/onboarding/", rules),
       "/api/v1/hr/public/onboarding/",
       "the bare prefix with no token after it is left as-is (nothing to mask)")

    print("\n§C  FAIL CLOSED — a redaction fault never raises, and never leaks")
    for bad in (None, "", 7, [], {}, object()):
        try:
            out = R.redact(bad, rules)
            ok(isinstance(out, str), "redact(%r) returns a string instead of raising" % (bad,))
        except Exception as e:
            ok(False, "redact(%r) raised %s" % (bad, type(e).__name__))
    ok(R.redact("/api/v1/referral/redeem/" + TOK, None) is not None,
       "rules=None still returns something rather than raising")
    ok(R.redact("/api/v1/referral/redeem/" + TOK, R.EMPTY_RULES)
       == "/api/v1/referral/redeem/" + TOK,
       "EMPTY_RULES is inert — this module masks credentials, it does not scrub logs at large")
    # The fallback is DERIVED (the last real compile), never a hardcoded path list.
    ok(R._FALLBACK_RULES is not None and R._FALLBACK_RULES[1],
       "the fallback rule set is populated from a real route table, not written by hand")
    eq(R.redact_for_app(None, "/api/v1/hr/public/onboarding/" + TOK),
       "/api/v1/hr/public/onboarding/{token}",
       "a call site that cannot reach the app object STILL redacts, via that fallback")

    print("\n§D  THE QUERY — the second factor travels there, so it goes too")
    ok(R.is_sensitive_path("/api/v1/hr/public/onboarding/%s/task/9f/document/aa" % TOK, rules),
       "a capability route is recognised as sensitive")
    eq(R.redact_query("/api/v1/hr/public/onboarding/%s/task/9f/document/aa" % TOK,
                      "value=1990-05-14", rules),
       R.QUERY_MASK,
       "the ?value=<date-of-birth> gate is NOT stored beside the token it unlocks")
    ok("1990-05-14" not in R.redact_query(
        "/api/v1/hr/public/onboarding/%s/task/9f/document/aa" % TOK, "value=1990-05-14", rules),
       "...the date of birth appears nowhere in what is stored")
    eq(R.redact_query("/api/v1/commcalc/rep-commissions/2026-07", "org_id=abc&store=12", rules),
       "org_id=abc&store=12",
       "an ordinary route keeps its query exactly as before — no behaviour change")
    eq(R.redact_query("/api/v1/commcalc/rep-commissions/2026-07", "", rules), "",
       "an empty query stays empty rather than becoming a mask")
    ok(R.redact_query("/x", object(), rules) == R.QUERY_MASK,
       "an unusable query fails CLOSED (masked), never open")

    print("\n§E  THE LOCK — a call site that stops dereferencing this module fails the build")
    al = open(os.path.join(APP, "core", "access_log.py"), encoding="utf-8").read()
    mn = open(os.path.join(APP, "main.py"), encoding="utf-8").read()

    ok("path_redact" in al, "access_log imports the redactor")
    ok(re.search(r'"path":\s*_pr\.redact_for_app\(', al),
       "access_log writes the REDACTED path, not scope['path']")
    ok(re.search(r'"query":\s*\(?_pr\.redact_query_for_app\(', al),
       "...and the redacted query")
    ok(not re.search(r'"path":\s*path\[', al),
       "the raw-path write is GONE from access_log (this is the line that leaked)")

    ok("path_redact" in mn, "main.py's crash logger imports the redactor")
    # Bound the function by PARSING, not by scanning for the next "\ndef " — the handlers that follow
    # it are `async def` behind decorators, so a text slice ran on to the BrowserWorkProxy forward at
    # the bottom of the file and reported its (correct, non-logging) request.url.path as a violation.
    import ast as _ast
    _lines = mn.splitlines()
    err = ""
    for _n in _ast.walk(_ast.parse(mn)):
        if isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and _n.name == "_log_system_error":
            err = "\n".join(_lines[_n.lineno - 1:(getattr(_n, "end_lineno", None) or _n.lineno)])
            break
    ok(bool(err), "_log_system_error was located by parsing main.py")
    ok("safe_path" in err, "_log_system_error computes a redacted path")
    # Everything AFTER the line that computes safe_path: the computation itself must read
    # request.url.path (that is its input) — what must not survive is any LOGGING line still using it.
    after = err.split("safe_path =", 1)[1].split("\n", 1)[1]
    ok("request.url.path" not in after,
       "...and no logging line after it still reads request.url.path",
       "stderr and failure_log must both get the masked form")
    # The proxy forward MUST keep the real path — it is forwarding the request, not logging it.
    ok("url = base + request.url.path" in mn,
       "the BrowserWorkProxy forward still uses the real path (it is a forward, not a log)")

    print("\n§E2 a newly added credential-shaped path parameter must be classified")
    # Scan every route literal in the app for parameter names that LOOK like a credential. Anything
    # matching that is not in SENSITIVE_PARAMS fails here — so the day someone writes `{reset_token}`,
    # the build stops until it is either masked or consciously excluded.
    cred = re.compile(r"token|secret|password|signature|nonce|api_?key|bearer|credential", re.I)
    found, offenders = set(), set()
    for root, _dirs, files in os.walk(APP):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            src = open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read()
            for lit in re.findall(r'@(?:router|app)\.(?:get|post|put|patch|delete)\(\s*"([^"]+)"', src):
                for name in R.params_in(lit):
                    found.add(name)
                    if cred.search(name) and name not in R.SENSITIVE_PARAMS:
                        offenders.add((name, lit))
    ok(bool(found), "route parameters were actually scanned", "%d distinct names" % len(found))
    ok("token" in found, "the live {token} routes are visible to this scan")
    ok(not offenders,
       "every credential-shaped path parameter is in SENSITIVE_PARAMS",
       "UNCLASSIFIED: %s" % sorted(offenders) if offenders else "")
    # And the converse: nothing in the sensitive set is a business identifier we still want to read.
    for benign in ("key", "code_id", "report_key", "store_code", "period", "employee_id"):
        ok(benign not in R.SENSITIVE_PARAMS,
           "%r stays readable in logs — masking it would cost audit value for no gain" % benign)

    print("\n\u00a7F  AGAINST REAL FASTAPI OBJECTS — the section fixtures could not replace")
    # THIS IS THE ONE THAT CAUGHT THE REAL BUG. Every assertion above passed while the live wiring
    # redacted NOTHING: `app.routes` is not a flat list of endpoints here — including a router leaves a
    # single `_IncludedRouter` container holding the real APIRouter on `.original_router` and the mount
    # prefix on `.include_context.prefix`. The first `rules_for_app` read only the top level, found 31
    # entries instead of 1596, compiled ZERO sensitive routes, and shipped a redactor that redacted
    # nothing. Strings cannot catch that; only real router objects can.
    try:
        from fastapi import FastAPI, APIRouter
    except Exception:
        ok(False, "fastapi is importable so this section can run",
           "SKIPPED — without it the _IncludedRouter regression is UNGUARDED")
    else:
        inner = APIRouter()

        @inner.get("/public/onboarding/{token}/task/{task_id}")
        def _h(token: str, task_id: str):
            return {}

        @inner.get("/employees/{employee_id}")
        def _h2(employee_id: str):
            return {}

        outer = APIRouter(prefix="/hr")
        outer.include_router(inner)          # nested include — depth must be walked, not just one level
        fake = FastAPI()
        fake.include_router(outer, prefix="/api/v1")

        tmpl = R.route_templates(fake)
        ok(len(tmpl) >= 2,
           "included-router containers are WALKED to their leaf routes",
           "found %d templates" % len(tmpl))
        ok(any(t.endswith("/api/v1/hr/public/onboarding/{token}/task/{task_id}") for t in tmpl),
           "...and a nested route carries its full mounted prefix", str(sorted(tmpl)))
        live = "/api/v1/hr/public/onboarding/%s/task/9f3c" % TOK
        eq(R.redact_for_app(fake, live),
           "/api/v1/hr/public/onboarding/{token}/task/9f3c",
           "redaction works through the REAL app object, not just a template list")
        ok(TOK not in R.redact_for_app(fake, live),
           "the token does not survive the real-object path")
        eq(R.redact_query_for_app(fake, live, "value=1990-05-14"), R.QUERY_MASK,
           "the gate value is dropped through the real app object too")
        eq(R.redact_for_app(fake, "/api/v1/hr/employees/8f3c"),
           "/api/v1/hr/employees/8f3c",
           "a non-credential route is still untouched end to end")

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
