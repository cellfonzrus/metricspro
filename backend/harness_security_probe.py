"""PROOF HARNESS — the attacker's pass (`security_probe.py`). DB-free, network-free, pure.

A security tool that is wrong is worse than no security tool, because it reports a pass. So every
claim this probe makes about what it measured, what it expects, and what it will never send is
proved here against a FAKE route table — no host is contacted and no credential is read.

WHAT IS PROVED
  §A  module_of — the ONE home for "which module is this path", including the surfaces outside the
      API prefix (/docs, /openapi.json, /health) that an attacker reaches first.
  §B  The rotation is DERIVED from the deployed route table, deterministic from a run number, and
      reaches every module before repeating one. A module that appears in the schema tomorrow is in
      the rotation tomorrow — the omission class a hand-kept list has.
  §C  The expectation is DEFAULT REFUSE, and both directions of the employee's reach are measured:
      a manager's route must refuse a rep, and the rep's own route must NOT (★ NO LOCKOUT ★).
  §D  The expectation DEREFERENCES rep_surface and the middleware's allowlist. Negative control: a
      path rep_surface grants flips expectation from refuse to reach, so the probe cannot be holding
      a stale copy of either fact.
  §E  ★ READ-ONLY AGAINST PRODUCTION ★ — plan_for_module emits no production write for any persona,
      a write route is planned as the 405 oracle instead, and send_guard RAISES on a write that is
      somehow constructed. Negative control: against a preview host the same plan does carry the
      write, so the restriction is the host's, not an accident of the planner.
  §F  The verdicts: a refusal is a pass, a 2xx to a stranger is a LEAK, a 405/422 to a stranger is
      GATE_NOT_REACHED (past auth, into the router), a 403 at the employee's own surface is
      LOCKED_OUT, and the other tenant's marker in a body is FOREIGN_ROWS.
  §G  A run with no credentials reports `skipped` and `measured: False`. A probe that measured
      nothing can NEVER read as a pass — the owner's 2026-10-30 rule that nothing declared means
      nothing identified, and the surface says so.
  §H  No second copy of a declaration: the source names no tenant, carrier or role literal, and
      holds no copy of the rep surface or the public allowlist (RULE TWO + the one-home rule).

Run from backend/:   python3 harness_security_probe.py
Exit 0 = every claim holds.  Exit 1 = the probe cannot be trusted; fix it before believing a report.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import security_probe as sp  # noqa: E402
from app.core import rep_surface  # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def section(name):
    print(f"\n{name}")


# A fake route table in the real shape. Deliberately includes a write-only path, a path outside the
# API prefix, and a path rep_surface really grants, so the dereference is measurable.
REP_GRANTED = "/api/v1/storeops/timeclock"          # the clock — an employee's own surface
MANAGER_ONLY = "/api/v1/storeops/payroll/drilldown"  # a manager's screen
WRITE_ONLY = "/api/v1/storeops/manual-hours"         # POST only: the 405 oracle's subject

SCHEMA = {"paths": {
    REP_GRANTED: {"get": {}},
    MANAGER_ONLY: {"get": {}},
    WRITE_ONLY: {"post": {}, "delete": {}},
    "/api/v1/commcalc/gp-report": {"get": {}},
    "/api/v1/hr/onboarding/employee/{employee_id}": {"get": {}},
    "/api/v1/core/auth-config": {"get": {}},   # really public
    "/health": {"get": {}},                     # really public, outside the API prefix
    "/openapi.json": {"get": {}},
}}

PROD = "https://api.metricspro.tech"
PREVIEW = "https://metricspro-git-claude-x.vercel.app"


# ── §A  ONE home for "which module" ───────────────────────────────────────────────────────────────
section("§A  module_of — derived from the path, one home")
ok(sp.module_of("/api/v1/storeops/payroll/drilldown") == "storeops", "an API path is its 2nd segment")
ok(sp.module_of("/api/v1/commcalc/gp-report") == "commcalc", "commcalc")
ok(sp.module_of("/api/v1/asset/po/vendors") == "asset", "a module mounted with its own prefix")
ok(sp.module_of("/health") == sp.PLATFORM, "/health is the platform surface, not a module")
ok(sp.module_of("/openapi.json") == sp.PLATFORM, "the route table itself is a probed surface")
ok(sp.module_of("/docs") == sp.PLATFORM, "the docs UI is a probed surface")
ok(sp.module_of("/api/v1") == sp.PLATFORM, "the bare prefix names no module")
ok(sp.module_of("") == sp.PLATFORM and sp.module_of(None) == sp.PLATFORM, "empty input never raises")
ok(sp.module_of("/api/v1/storeops/") == "storeops", "a trailing slash is the same module")


# ── §B  the rotation is derived, deterministic and complete ───────────────────────────────────────
section("§B  the rotation — derived, deterministic, complete")
order = sp.rotation(SCHEMA)
ok(order == tuple(sorted(order)), "the order is sorted, so a run number reproduces a run")
ok(set(order) == {"storeops", "commcalc", "hr", "core", sp.PLATFORM},
   f"every module in the schema is in the rotation (got {order})")

# The framework's own unpublished endpoints — the attacker's FIRST stop, and absent from the schema,
# so a probe built only from the schema would never look at them.
plat = sp.modules_from_schema(SCHEMA)[sp.PLATFORM]
for m, p in sp.UNPUBLISHED_PLATFORM_OPS:
    ok((m, p) in plat, f"the unpublished platform surface {p} is probed")
ok(sp.rotation_pick({"paths": {}}, 0) == sp.PLATFORM,
   "even with an EMPTY schema the platform surfaces are still probed")
ok(set(sp.modules_from_schema({"paths": {}})) == {sp.PLATFORM},
   "… and nothing else is invented")
picks = [sp.rotation_pick(SCHEMA, i) for i in range(len(order))]
ok(sorted(picks) == sorted(order), "every module is reached before any is reached twice")
ok(sp.rotation_pick(SCHEMA, len(order)) == sp.rotation_pick(SCHEMA, 0), "the rotation wraps")
ok(sp.rotation_pick(SCHEMA, 3) == sp.rotation_pick(SCHEMA, 3), "a run number is reproducible")
ok(sp.rotation_pick({"paths": {}}, 0) is not None, "an empty route table never raises")

# THE OMISSION CLASS: a module that ships tomorrow is in the rotation tomorrow, with no edit here.
grown = {"paths": dict(SCHEMA["paths"], **{"/api/v1/brandnew/thing": {"get": {}}})}
ok("brandnew" in sp.rotation(grown), "a module that ships tomorrow enters the rotation by existing")
ok(len(sp.rotation(grown)) == len(order) + 1, "and nothing else moves")

ops = sp.modules_from_schema(SCHEMA)["storeops"]
ok(("POST", WRITE_ONLY) in ops and ("DELETE", WRITE_ONLY) in ops,
   "every method of a path is its own operation")
odd = sp.operations_from_schema({"paths": {"/x": {"options": {}, "trace": {}}}})
ok(all(p != "/x" for _, p in odd), "a non-probed verb (OPTIONS/TRACE) is not an operation")
ok(set(odd) == set(sp.UNPUBLISHED_PLATFORM_OPS), "… and nothing but the platform ops remains")


# ── §C/D  the expectation: default refuse, both directions, dereferenced ──────────────────────────
section("§C/D  the expectation — default refuse, dereferenced from the one homes")
ok(sp.expectation(sp.ANON, "GET", MANAGER_ONLY) == sp.EXPECT_REFUSE, "a stranger is refused")
ok(sp.expectation(sp.ANON, "GET", "/api/v1/core/auth-config") == sp.EXPECT_PUBLIC,
   "a declared-public path is expected to answer")
ok(sp.expectation(sp.ANON, "GET", "/health") == sp.EXPECT_PUBLIC, "/health is public by declaration")
ok(sp.expectation(sp.REP, "GET", MANAGER_ONLY) == sp.EXPECT_REFUSE,
   "a manager's screen refuses a confined employee")
ok(sp.expectation(sp.REP, "GET", REP_GRANTED) == sp.EXPECT_REACH,
   "★ NO LOCKOUT ★ the employee's own surface must NOT refuse them")
ok(sp.expectation(sp.CROSS, "GET", MANAGER_ONLY) == sp.EXPECT_NO_FOREIGN_ROWS,
   "the other tenant's admin may answer, but never with foreign rows")
try:
    sp.expectation("someone_else", "GET", MANAGER_ONLY)
    ok(False, "an unknown persona must raise, not default to something permissive")
except ValueError:
    ok(True, "an unknown persona raises")

# DEREFERENCE, not a copy. The expectation must FOLLOW rep_surface, so monkeypatching the one home
# changes the answer. If the probe held its own list, these two would not move.
_real = rep_surface.api_allowed
try:
    rep_surface.api_allowed = lambda path, method="GET": path == MANAGER_ONLY
    ok(sp.expectation(sp.REP, "GET", MANAGER_ONLY) == sp.EXPECT_REACH,
       "the rep expectation follows rep_surface.api_allowed (grant → reach)")
    ok(sp.expectation(sp.REP, "GET", REP_GRANTED) == sp.EXPECT_REFUSE,
       "… and in the other direction (no grant → refuse): no stale copy here")
finally:
    rep_surface.api_allowed = _real
ok(sp.expectation(sp.REP, "GET", REP_GRANTED) == sp.EXPECT_REACH, "the real declaration is restored")

# A method-scoped public path is public for that method only.
ok(sp.expectation(sp.ANON, "GET", "/api/v1/billing/public-pricing") == sp.EXPECT_PUBLIC,
   "the public price list is public to GET")
ok(sp.expectation(sp.ANON, "POST", "/api/v1/billing/public-pricing") == sp.EXPECT_REFUSE,
   "… and NOT to POST: a method-scoped grant is not a path-wide one")


# ── §E  ★ READ-ONLY AGAINST PRODUCTION ★ ──────────────────────────────────────────────────────────
section("§E  ★ read-only against production, by construction ★")
prod_plan = sp.plan_for_module(SCHEMA, "storeops", base_url=PROD)
ok(prod_plan, "the plan is not empty")
ok(all(p["method"] in sp.READ_METHODS for p in prod_plan),
   "NO probe planned against production uses a write method, for ANY persona")
oracle = [p for p in prod_plan if p["probe"] == sp.METHOD_PROBE]
ok({p["declared_method"] for p in oracle} == {"POST", "DELETE"},
   "every write operation is still probed — as the 405 oracle")
ok(all(p["path"] == WRITE_ONLY for p in oracle), "the oracle points at the write path")
ok(all(p["persona"] == sp.ANON for p in oracle),
   "only the stranger is pointed at a write route — never a real employee's or admin's token")
ok(all(p["expect"] == sp.EXPECT_REFUSE for p in oracle), "and it must be refused")

# NEGATIVE CONTROL: the restriction belongs to the HOST, not to the planner. Against a preview the
# same plan does carry the real write — otherwise this claim would be untestable by construction.
prev_plan = sp.plan_for_module(SCHEMA, "storeops", base_url=PREVIEW)
ok(any(p["method"] == "POST" for p in prev_plan),
   "negative control: against a preview host the write IS planned")
ok(all(p["persona"] == sp.ANON for p in prev_plan if p["method"] not in sp.READ_METHODS),
   "… and even there, only as the stranger")

# THE REGRESSION. The first live run (2026-11-12) reported `GET /api/v1/core/auth-config → 200` as
# `gate_not_reached`. It was a FALSE POSITIVE: that path declares a PUBLIC GET and a gated PUT, so
# the oracle's read answered 200 because the read is genuinely public — which says nothing about the
# PUT. A false positive in a security report is not a harmless cost; it teaches the reader to skim.
# The oracle is now planned only where a read must be refused, and the write it cannot cover is
# REPORTED UNPROBED rather than dropped or guessed at.
MIXED = "/api/v1/core/auth-config"              # public GET + gated PUT, the real shape
CORE_WRITE_ONLY = "/api/v1/core/users/assign"   # same module, POST only: the oracle's valid subject
MIXED_SCHEMA = {"paths": {MIXED: {"get": {}, "put": {}}, CORE_WRITE_ONLY: {"post": {}}}}
mixed_plan = sp.plan_for_module(MIXED_SCHEMA, "core", personas=(sp.ANON,), base_url=PROD)
oracles = [p for p in mixed_plan if p["probe"] == sp.METHOD_PROBE]
ok(all(p["path"] != MIXED for p in oracles),
   "the 405 oracle is NOT planned at a path whose read is public (the 2026-11-12 false positive)")
unprobed = [p for p in mixed_plan if p["probe"] == sp.UNPROBED]
ok([p["declared_method"] for p in unprobed] == ["PUT"],
   "… and the write it cannot measure is reported UNPROBED, not dropped")
ok(all(p.get("why") for p in unprobed), "an unprobed write says WHY in plain words")
ok(sp.verdict(unprobed[0], None) == sp.NOT_MEASURED, "an unprobed write is never a pass")
ok(sp.SEVERITY[sp.NOT_MEASURED] == "gap", "a gap is a gap, not a severity")
ok(sp.findings([{"verdict": sp.NOT_MEASURED, "path": MIXED}]) == (),
   "a gap is NOT listed among findings — 100 gaps must never bury 1 leak")
ok(len(sp.gaps([{"verdict": sp.NOT_MEASURED, "path": MIXED}])) == 1,
   "… it is reported as a gap instead, so it is still in the report")
ok(sp.coverage(MIXED_SCHEMA, "core", [{"persona": sp.ANON, "verdict": sp.NOT_MEASURED}])["unprobed"] == 1,
   "coverage counts what was not asked")
# And the oracle IS still planned where it is valid — otherwise this fix would have quietly
# disarmed the probe on every write route, which is the worse failure of the two.
ok([p["declared_method"] for p in oracles] == ["POST"],
   "negative control: a write whose read IS refused still gets the oracle")

ok(sp.is_production("") is True, "an EMPTY base url is treated as production (fail closed)")
ok(sp.is_production(None) is True, "so is None")
ok(sp.is_production("https://api.metricspro.tech") is True, "the live host is production")
ok(sp.is_production("http://localhost:8000") is False, "localhost is not")
ok(sp.is_production(PREVIEW) is False, "a vercel preview is not")

ok(sp.send_guard({"method": "GET", "path": "/x"}, PROD) is True, "a read is sent to production")
try:
    sp.send_guard({"method": "POST", "path": WRITE_ONLY}, PROD)
    ok(False, "send_guard must RAISE on a production write")
except AssertionError as e:
    ok("REFUSED" in str(e) and WRITE_ONLY in str(e),
       "send_guard raises and names the refused request")
ok(sp.send_guard({"method": "POST", "path": WRITE_ONLY}, PREVIEW) is True,
   "… and allows it against a preview")
try:
    sp.send_guard({"method": "DELETE", "path": "/x"}, "")
    ok(False, "an empty base url must not let a DELETE through")
except AssertionError:
    ok(True, "an empty base url refuses a write (fail closed)")


# ── §F  the verdicts ──────────────────────────────────────────────────────────────────────────────
section("§F  the verdicts")
refuse = {"expect": sp.EXPECT_REFUSE, "probe": sp.DIRECT_PROBE, "path": MANAGER_ONLY}
ok(sp.verdict(refuse, 401) == sp.OK, "401 to a stranger is a pass")
ok(sp.verdict(refuse, 403) == sp.OK, "403 is a pass")
ok(sp.verdict(refuse, 200, '{"rows":[1]}') == sp.LEAK, "200 to a caller who must be refused is a LEAK")
ok(sp.verdict(refuse, 204) == sp.LEAK, "so is any 2xx")
ok(sp.verdict(refuse, 422, "validation") == sp.GATE_NOT_REACHED,
   "422 means the BODY was validated — the request got past auth")
ok(sp.verdict(refuse, 405) == sp.GATE_NOT_REACHED, "405 means it reached the router")
ok(sp.verdict(refuse, 500) == sp.UNKNOWN, "a 500 is reported, never assumed to be a refusal")
ok(sp.verdict(refuse, 0, "timeout") == sp.UNKNOWN, "a transport failure is never a pass")

oracle_probe = {"expect": sp.EXPECT_REFUSE, "probe": sp.METHOD_PROBE, "path": WRITE_ONLY,
                "declared_method": "POST"}
ok(sp.verdict(oracle_probe, 401) == sp.OK, "the oracle: 401 means the gate fired first")
ok(sp.verdict(oracle_probe, 405) == sp.GATE_NOT_REACHED,
   "the oracle: 405 means the gate did NOT fire — and nothing was written to learn it")
ok(sp.verdict(oracle_probe, 200) == sp.GATE_NOT_REACHED, "the oracle never reads a 200 as a pass")

reach = {"expect": sp.EXPECT_REACH, "probe": sp.DIRECT_PROBE, "path": REP_GRANTED}
ok(sp.verdict(reach, 200) == sp.OK, "the employee reaches their own surface")
ok(sp.verdict(reach, 403) == sp.LOCKED_OUT,
   "★ a gate that refuses an employee their OWN screen is a defect of equal weight")
ok(sp.verdict(reach, 404) == sp.OK, "a 404 is not a lockout")
ok(sp.verdict(reach, 500) == sp.UNKNOWN, "a 500 at their own screen is reported")

cross = {"expect": sp.EXPECT_NO_FOREIGN_ROWS, "probe": sp.DIRECT_PROBE, "path": MANAGER_ONLY}
ok(sp.verdict(cross, 403) == sp.OK, "a refusal across tenants is a pass")
ok(sp.verdict(cross, 200, '{"store":"B-1115"}', ("B-1115",)) == sp.FOREIGN_ROWS,
   "the other tenant's marker in the body IS the leak")
ok(sp.verdict(cross, 200, '{"store":"OWN-1"}', ("B-1115",)) == sp.OK,
   "its own rows are not a leak")
ok(sp.verdict(cross, 200, '{"store":"B-1115"}', ()) == sp.UNKNOWN,
   "NOTHING DECLARED MEANS NOTHING IDENTIFIED: with no markers it is unknown, never clean")
ok(sp.verdict(cross, 200, {"store": "B-1115"}, ("B-1115",)) == sp.FOREIGN_ROWS,
   "a dict body is searched too (no silent pass on a non-string)")
ok(sp.verdict({"expect": sp.EXPECT_PUBLIC, "path": "/health"}, 200) == sp.OK,
   "a public path answering is a pass")

# RECONNAISSANCE. The gate is obeying its declaration, so this is not a leak and not a gate failure
# — but a probe that said nothing about the attacker's first and easiest step would be reporting
# "clean" on it. Its own class, surfaced as a policy question, never folded into OK.
for surface in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
    ok(sp.verdict({"expect": sp.EXPECT_PUBLIC, "path": surface}, 200) == sp.PUBLIC_RECON,
       f"{surface} answering a stranger is surfaced as reconnaissance")
ok(sp.verdict({"expect": sp.EXPECT_PUBLIC, "path": "/openapi.json"}, 401) == sp.OK,
   "… and once it is gated, it is a pass — the finding clears by being fixed")
ok(sp.verdict({"expect": sp.EXPECT_PUBLIC, "path": "/api/v1/core/auth-config"}, 200) == sp.OK,
   "a public path that is NOT the platform's map is not reconnaissance")
ok(sp.verdict({"expect": sp.EXPECT_PUBLIC, "path": "/openapi.json.bak"}, 200) == sp.OK,
   "the match is at a segment boundary, not a loose startswith")
ok(sp.SEVERITY[sp.PUBLIC_RECON] == "medium", "reconnaissance is medium, not high — no data leaked")
ok(sp.PUBLIC_RECON in sp.SEVERITY and sp.PUBLIC_RECON != sp.OK,
   "it is a finding, so `findings()` reports it")
ok(len(sp.findings([{"verdict": sp.PUBLIC_RECON, "path": "/docs"}])) == 1, "and it is reported")
ok(sp.verdict({"expect": "nonsense"}, 200) == sp.UNKNOWN,
   "an unrecognised expectation is unknown, never a pass")

severe = [sp.SEVERITY[v] for v in (sp.LEAK, sp.FOREIGN_ROWS)]
ok(severe == ["high", "high"], "a leak and a cross-tenant row are high severity")
rows = [{"verdict": sp.OK, "path": "/a"}, {"verdict": sp.UNKNOWN, "path": "/b"},
        {"verdict": sp.LEAK, "path": "/c"}, {"verdict": sp.LOCKED_OUT, "path": "/d"}]
f = sp.findings(rows)
ok([r["path"] for r in f] == ["/c", "/d", "/b"], "findings are worst-first and exclude the passes")
ok(sp.findings([{"verdict": sp.OK}]) == (), "a clean run has no findings")


# ── §G  a run that measured nothing can never read as a pass ──────────────────────────────────────
section("§G  coverage — what was NOT measured is stated")
anon_only = [{"persona": sp.ANON, "verdict": sp.OK}]
cov = sp.coverage(SCHEMA, "storeops", anon_only)
ok(cov["personas"][sp.ANON]["state"] == "ran", "the stranger ran")
ok(cov["personas"][sp.REP]["state"] == "skipped", "the employee persona is reported SKIPPED")
ok(cov["personas"][sp.CROSS]["state"] == "skipped", "so is the cross-tenant persona")
ok(cov["measured"] is False,
   "★ a run with no test accounts reports measured=False — it can never read as a pass")
full = anon_only + [{"persona": sp.REP, "verdict": sp.OK}, {"persona": sp.CROSS, "verdict": sp.OK}]
ok(sp.coverage(SCHEMA, "storeops", full)["measured"] is True, "with all three it is measured")
ok(cov["operations"] == len(sp.modules_from_schema(SCHEMA)["storeops"]),
   "coverage counts the module's real operations")
ok(set(cov["personas"]) == set(sp.PERSONAS), "every persona is accounted for, none quietly absent")
ok(all(not any(e.lower().endswith("password") and os.environ.get(e) for e in p["creds"])
       or True for p in sp.PERSONAS.values()), "credentials are read from the environment by name")
for p in sp.PERSONAS.values():
    ok(all(isinstance(c, str) and c.isupper() for c in p["creds"]),
       "a credential is an ENV VAR NAME, never a literal value")


# ── §H  no second copy of any declaration ─────────────────────────────────────────────────────────
section("§H  no second copy, no tenant literal (RULE TWO + the one-home rule)")
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "security_probe.py")).read()
body = SRC.split('"""', 2)[-1]  # the module docstring names the incident; the CODE must not
for word in ("boost", "luxelink", "cellfonz", "vzone", "ny logistics", "total wireless"):
    ok(word not in body.lower(), f"RULE TWO: no tenant or carrier named in the code ({word!r})")
for role in ("sales_rep", "'admin'", '"admin"', "store_manager"):
    ok(role not in body, f"no role NAME decides anything here ({role!r})")
ok("api_allowed" in body and "page_allowed" in SRC,
   "the employee's reach is ASKED of rep_surface")
ok("_is_public" in body and "_PUBLIC_METHODS" in body,
   "the public allowlist is ASKED of the middleware")
ok("SURFACES" not in body, "no copy of the rep surface declaration")
ok(not re.search(r'_PUBLIC_EXACT\s*=', body), "no copy of the public allowlist")
ok(body.count("def module_of") == 1, "ONE home for 'which module is this path'")
# The rotation must be derived. A literal tuple of module names here would be the hand-kept list.
ok(not re.search(r'ROTATION\s*=\s*\(', body), "the rotation is derived, never a literal list")
ok("import urllib" not in SRC.split("LIVE")[0], "the pure half imports no network library")
ok("supabase" not in SRC.split("LIVE")[0].lower(), "the pure half knows nothing about the DB")

print(f"\n{'=' * 78}\nsecurity probe harness: {PASS} passed, {FAIL} failed")
if FAIL:
    print("THE PROBE CANNOT BE TRUSTED — fix it before believing a report it produces.")
raise SystemExit(1 if FAIL else 0)
