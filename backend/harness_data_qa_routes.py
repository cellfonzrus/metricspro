#!/usr/bin/env python3
"""THE WIRING PROOF — every report the data assistant may run RESOLVES to a real, GET-able route on
the real app (index §52.1).

WHY THIS EXISTS, written down because it caught a live defect in its own pull request. The semantic
layer declares an endpoint path per question. Nothing in a pure, DB-free harness can tell a correct
path from a plausible one: the first version of the registry declared `/api/v1/sales-report`, which
reads perfectly and is wrong — `commcalc`'s router carries a `/commcalc` prefix, so the real path is
`/api/v1/commcalc/sales-report`. Every one of the nine questions was wrong the same way, and the
registry harness (165 checks) passed throughout, because a string that matches a pattern is not a
string that matches a route. Production would have answered every data question with "the report
could not be produced (404)".

So the registry's paths are checked against `app.openapi()`, which is the app's own account of what
it serves. That makes a mis-prefixed, renamed or deleted endpoint a BUILD failure rather than a
sentence an operator reads as "there is no data".

Three things are proven here and nowhere else:
  A  every registered question's path is a route the app actually serves
  B  every one of those routes supports GET — the assistant is read-only, proven against the app's
     own schema rather than against the registry's own promise
  C  the assistant's own two endpoints are mounted (a sub-router mounted into a router that already
     carries a prefix is how `/core/core/data-qa` happens, and it happened here too)

NEEDS THE BACKEND'S DEPENDENCIES (it imports the real app), so it runs in its own CI job with
`pip install -r requirements.txt` — the repo's existing pattern for an app-dependent proof. It
performs NO request, touches no database and calls no model: importing the app and reading its
schema is the whole test.

Run: python3 backend/harness_data_qa_routes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = []
CHECKS = 0


def ok(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(label)


from app.main import app                                      # noqa: E402
from app.modules.core import data_qa_registry as reg          # noqa: E402

SCHEMA = app.openapi()["paths"]

print(f"\n── the app serves {len(SCHEMA)} paths; the registry declares {len(reg.keys())} questions")

# ── §A every declared path is a real route ──────────────────────────────────────────────────────
for k in reg.keys():
    path = reg.question(k)["path"]
    ok(path in SCHEMA, f"A[{k}] '{path}' is a route the app actually serves")

# ── §B and every one of them is GET-able; the assistant is read-only ───────────────────────────
for k in reg.keys():
    path = reg.question(k)["path"]
    methods = {m.lower() for m in SCHEMA.get(path, {})}
    ok("get" in methods, f"B1[{k}] '{path}' supports GET")
    # The reader only ever issues GET (harness_data_qa_lock.py §B1). This is the other half: a path
    # whose ONLY verb is a mutation could never be read, and would be a declaration that lies.
    ok(methods != {"post"} and methods != {"delete"},
       f"B2[{k}] '{path}' is not a write-only route")

# ── §C the assistant's own endpoints are mounted, once, at the right depth ─────────────────────
ok("/api/v1/core/data-qa" in SCHEMA, "C1 POST /api/v1/core/data-qa is mounted")
ok("/api/v1/core/data-qa/status" in SCHEMA, "C2 GET /api/v1/core/data-qa/status is mounted")
ok("post" in {m.lower() for m in SCHEMA.get("/api/v1/core/data-qa", {})},
   "C3 asking a question is a POST")
ok("get" in {m.lower() for m in SCHEMA.get("/api/v1/core/data-qa/status", {})},
   "C4 the status read is a GET")
doubled = [p for p in SCHEMA if "core/core" in p or p.count("/data-qa") > 1]
ok(not doubled,
   f"C5 the sub-router is not mounted under a doubled prefix: {doubled}")

print(f"\n{'=' * 78}")
if FAILS:
    print(f"FAILED {len(FAILS)} of {CHECKS} checks:")
    for f in FAILS:
        print(f"  ✗ {f}")
    sys.exit(1)
print(f"OK — {CHECKS} checks passed (data-qa routes resolve on the real app)")
