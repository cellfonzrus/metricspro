"""THE ATTACKER'S PASS — one module per run, from outside the platform, read-only.

OWNER (2026-11-12, verbatim): *"create a testng agent who deciatedly is checking the data leakage,
unwanted access to modules even if theya re gated like the case of manan and we discovered by
accident, c annot have an accident of this sort, put it on schdule and one module at a time from the
front end as though somebnbody was trying to hack in to the system"*

THE INSTANCE (§CORE-33, 2026-11-04): NY Logistics' Manan walked past the clock-in screen and read
adjustment hours. It was found BY ACCIDENT. Every control that closed it — `rep_surface`,
`RepSurfaceMiddleware`, `harness_rep_surface_lock` — is a statement made INSIDE the code about what
the code intends. None of them asks the deployed platform, from outside, with a real low-privilege
token, what it actually answers.

THE CLASS: **a gate proved in-process is a claim about the code, not a measurement of the running
system.** Every existing access control here is proved by a harness that imports the handler and
calls it with a fake client. That catches a wrong RULE. It cannot catch a rule that is right and
NOT REACHED — a middleware that never fired for this path, an allowlist entry that over-matches, a
route mounted outside the gate, a deploy that shipped `REP_CONFINE=0`. The Manan defect was exactly
that shape: the rule lived in the browser and the API had no audience of its own.

SO: this module probes the DEPLOYED platform as an unprivileged outsider, one module per run, and
reports what it is actually told. It proves nothing about intent and everything about behaviour.

WHAT IS NOT DUPLICATED (CLAUDE.md duplicate-check gate). The index was searched for an existing
mechanism answering "is this surface reachable by someone who should not reach it":
  · `harness_privesc_rbac_gates.py`  — in-process, fake client, RBAC/user-management handlers.
  · `harness_rep_surface.py` / `_lock` — in-process, the DECLARATION and the middleware's rule.
  · `harness_cross_tenant_isolation.py` — in-process, org-scoping of the sales/pay basis.
  · `harness_org_scope_guard.py`      — static text scan for a missing `.eq('org_id', …)`.
  · `.github/workflows/security.yml`  — SAST / dependency / secret scanning. No live access probe.
Every one of them is a statement made inside the process. NONE of them sends a request to the
deployed host. This module is the missing OUTSIDE measurement, and it REUSES rather than restates
their declarations: the question "may a confined employee reach this path" is asked of
`app.core.rep_surface.api_allowed` and `page_allowed`, and "is this path public" is asked of
`app.core.tenant_middleware._is_public` / `public_methods_for`. There is no second copy of either
fact here, and `harness_security_probe.py` fails the build if one appears.

ONE MODULE PER RUN, and the module list is DERIVED from the deployed route table (its own
`/openapi.json`), never hand-listed — a module that ships tomorrow enters the rotation by existing,
so the rotation cannot silently omit the newest surface. That is the whole point of a rotation that
is a list: a hand-kept one would have to be remembered.

★ READ-ONLY AGAINST PRODUCTION, BY CONSTRUCTION ★
This probe never sends a write to a production host. Not "tries not to" — `plan_for_module` cannot
emit one, and `send_guard` raises if one is ever constructed. A write route is still probed, with a
side-effect-free oracle:

    An unauthenticated GET to a POST-only path must be refused by the MIDDLEWARE (401), which runs
    BEFORE route matching. A `405 Method Not Allowed` means the request reached the ROUTER — i.e.
    the auth gate did not fire for that path. So 405 is the finding, and nothing was ever written.

Pure logic lives above the `LIVE` divider and has no network, no DB and no imports of the app's
clients; `harness_security_probe.py` proves it. Run the live pass from backend/:

    python3 security_probe.py --module storeops            # one named module
    python3 security_probe.py --rotate 7                   # the rotation's 7th run
    python3 security_probe.py --rotate 7 --out findings.json

Index §CORE-42.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import rep_surface  # noqa: E402  — the ONE home for an employee's reach
from app.core import tenant_middleware as tm  # noqa: E402  — the ONE home for "this path is public"

# ── THE PERSONAS ──────────────────────────────────────────────────────────────────────────────────
# Three outsiders, each the shape of a real attacker. `token_env` names the ENVIRONMENT VARIABLE a
# credential is read from — a password is never an argument, never a literal here, and never logged.
ANON = "anon"
REP = "rep"
CROSS = "cross_tenant"

PERSONAS = {
    ANON: {
        "what": "nobody — no Authorization header at all",
        "asks": "does the platform answer a stranger",
        "creds": (),
    },
    REP: {
        "what": "a real signed-in individual contributor (the Manan shape) in tenant A",
        "asks": "does a confined employee reach a manager's module by typing its URL",
        "creds": ("PROBE_REP_EMAIL", "PROBE_REP_PASSWORD"),
    },
    CROSS: {
        "what": "a real signed-in ADMIN of tenant B, carrying tenant A's identifiers",
        "asks": "does one tenant's request return another tenant's rows",
        "creds": ("PROBE_CROSS_EMAIL", "PROBE_CROSS_PASSWORD"),
    },
}

READ_METHODS = frozenset({"GET", "HEAD"})

# What a correctly-gated platform must answer. These are the only four verdicts a plan asks for.
EXPECT_REFUSE = "refuse"          # 401 or 403, and no body of rows
EXPECT_PUBLIC = "public"          # declared public — any answer is fine, it is meant to be open
EXPECT_REACH = "reach"            # the employee's OWN surface: must NOT be 403 (★ NO LOCKOUT ★)
EXPECT_NO_FOREIGN_ROWS = "no_foreign_rows"  # may answer, but never with the other tenant's content
EXPECT_UNPROBED = "unprobed"      # this run cannot measure it — SAID, never dropped

REFUSAL_CODES = frozenset({401, 403})


# ── 1. WHICH MODULE DOES A PATH BELONG TO ─────────────────────────────────────────────────────────
# ONE home. The rotation, the report and the plan all ask this, so "which module is this" can never
# be answered two ways. A path outside the API prefix (/health, /docs, /openapi.json) belongs to
# `platform` — it is still a surface an attacker reaches first, so it is still in the rotation.
PLATFORM = "platform"
API_PREFIX = "/api/v1"


def module_of(path):
    """The module key a path belongs to, derived from the path itself — never a hand-kept map."""
    p = "/" + str(path or "").strip("/")
    if not (p == API_PREFIX or p.startswith(API_PREFIX + "/")):
        return PLATFORM
    rest = p[len(API_PREFIX):].strip("/")
    if not rest:
        return PLATFORM
    return rest.split("/")[0]


# The endpoints the framework MOUNTS BUT DOES NOT PUBLISH in its own schema, so they cannot be
# derived from it — and they are the first thing an attacker opens, because they hand over the map.
# This is the one place a surface is named rather than derived, and the reason is stated here rather
# than left to be rediscovered. It is a PLATFORM fact (a framework's own routes), never a module
# list and never a tenant's vocabulary, so naming it does not re-create the omission class §B guards:
# a MODULE still enters the rotation by existing. The harness pins that each one stays probed.
UNPUBLISHED_PLATFORM_OPS = (("GET", "/openapi.json"), ("GET", "/docs"), ("GET", "/redoc"))


def operations_from_schema(schema):
    """Every (METHOD, path) the deployed app publishes, sorted. The attacker's own map: this is read
    from the host's `/openapi.json`, so it is what the RUNNING build serves, not what this checkout
    says it would serve."""
    out = list(UNPUBLISHED_PLATFORM_OPS)
    for path, ops in (schema or {}).get("paths", {}).items():
        for method in ops or {}:
            m = str(method).upper()
            if m in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
                out.append((m, path))
    return tuple(sorted(set(out), key=lambda t: (t[1], t[0])))


def modules_from_schema(schema):
    """The rotation's universe: every module the deployed app exposes, with its operations."""
    by_mod = {}
    for method, path in operations_from_schema(schema):
        by_mod.setdefault(module_of(path), []).append((method, path))
    return {k: tuple(v) for k, v in sorted(by_mod.items())}


def rotation(schema):
    """The rotation order. Sorted by module key so it is DETERMINISTIC and reproducible from a run
    number alone — the schedule carries no state, and a run can always be repeated."""
    return tuple(modules_from_schema(schema).keys())


def rotation_pick(schema, run_index):
    """Which module this run probes. Every module is reached before any is reached twice."""
    order = rotation(schema)
    if not order:
        return None
    return order[int(run_index) % len(order)]


# ── 2. WHAT A CORRECTLY-GATED PLATFORM MUST ANSWER ────────────────────────────────────────────────
def _is_public(method, path):
    """Dereferenced from the middleware's OWN allowlist, never copied. `public_methods_for` scopes a
    path that is public for one method only, exactly as the middleware scopes it."""
    if not tm._is_public(path):
        return False
    allowed = tm._PUBLIC_METHODS.get(path)
    if allowed is None:
        return True
    return str(method).upper() in {str(m).upper() for m in allowed}


def expectation(persona, method, path):
    """What this persona must be told. DEFAULT REFUSE — the same default as the gate itself."""
    m = str(method).upper()
    if _is_public(m, path):
        return EXPECT_PUBLIC
    if persona == ANON:
        return EXPECT_REFUSE
    if persona == REP:
        # Asked of rep_surface itself. Both directions matter: a manager's route must refuse, and
        # the employee's own route must NOT — a gate that locks reps out of their own screens is a
        # defect of equal weight (★ NO LOCKOUT ★, index §22).
        return EXPECT_REACH if rep_surface.api_allowed(path, m) else EXPECT_REFUSE
    if persona == CROSS:
        return EXPECT_NO_FOREIGN_ROWS
    raise ValueError(f"unknown persona {persona!r}")


# ── 3. THE PLAN — and why it can never carry a production write ───────────────────────────────────
def is_production(base_url):
    """A host is production unless it declares itself otherwise. DEFAULT: production — so a
    mis-typed or empty base URL is treated as the live system and gets the read-only rules, never
    the permissive ones."""
    u = str(base_url or "").lower()
    if not u:
        return True
    return not any(mark in u for mark in ("localhost", "127.0.0.1", "0.0.0.0",
                                          "-git-", ".vercel.app", "preview", "staging", "test."))


METHOD_PROBE = "method"   # the 405 oracle: a read sent at a write-only path
DIRECT_PROBE = "direct"   # the method the operation actually declares
UNPROBED = "unprobed"     # planned, counted, NOT sent — a gap this run states out loud


def plan_for_module(schema, module, personas=(ANON, REP, CROSS), base_url=""):
    """Every probe this run will send, as data — so the plan can be inspected and proved before a
    single request leaves. A WRITE operation is never planned as a write against production: it is
    planned as the 405 oracle, which reaches the same gate and writes nothing."""
    ops = modules_from_schema(schema).get(module, ())
    prod = is_production(base_url)
    # THE ORACLE IS NOT ALWAYS VALID, and this is the half that was missing on 2026-11-12: a path
    # may carry BOTH a readable method and a gated write (`GET /core/auth-config` is declared public;
    # `PUT` on the same path flips the tenant's login enforcement). A GET there answers 200 because
    # the GET is genuinely public — which says NOTHING about the PUT, and read as the oracle it is a
    # false positive. A false positive in a security report is not a harmless cost: it trains the
    # reader to skim. So the oracle is planned only where a refusal is what a read must get, and
    # every write it cannot cover is REPORTED UNPROBED rather than dropped.
    readable = {p for m, p in ops if m in READ_METHODS}
    plan = []
    for persona in personas:
        for method, path in ops:
            if method in READ_METHODS:
                plan.append({"persona": persona, "method": method, "path": path,
                             "probe": DIRECT_PROBE, "expect": expectation(persona, method, path)})
                continue
            # A write. Only the stranger is ever pointed at one — a probe must not act with a real
            # employee's or admin's authority, even against a preview host.
            if persona != ANON:
                continue
            if prod:
                if path in readable or expectation(ANON, "GET", path) != EXPECT_REFUSE:
                    plan.append({"persona": persona, "method": method, "path": path,
                                 "probe": UNPROBED, "declared_method": method,
                                 "expect": EXPECT_UNPROBED,
                                 "why": "a read of this path is not expected to be refused, so the "
                                        "405 oracle cannot measure this write; it needs a preview host"})
                else:
                    plan.append({"persona": persona, "method": "GET", "path": path,
                                 "probe": METHOD_PROBE, "declared_method": method,
                                 "expect": expectation(persona, method, path)})
            else:
                plan.append({"persona": persona, "method": method, "path": path,
                             "probe": DIRECT_PROBE, "expect": expectation(persona, method, path)})
    return tuple(plan)


def send_guard(probe, base_url):
    """THE LOCK. Raises rather than sending a write to a production host. `plan_for_module` already
    cannot emit one; this refuses it a second time at the moment of sending, because the whole value
    of this tool rests on the owner being able to trust that it only looks."""
    m = str(probe.get("method", "")).upper()
    if m in READ_METHODS:
        return True
    if is_production(base_url):
        raise AssertionError(
            f"REFUSED: {m} {probe.get('path')} is a write and {base_url or '<empty>'} is production. "
            "This probe is read-only against production by construction (see the 405 oracle).")
    return True


# ── 4. THE VERDICT ────────────────────────────────────────────────────────────────────────────────
OK = "ok"
LEAK = "leak"                    # answered a caller who must have been refused
GATE_NOT_REACHED = "gate_not_reached"   # the request got past auth to the router / validator
LOCKED_OUT = "locked_out"        # refused a caller whose own surface this is
FOREIGN_ROWS = "foreign_rows"    # one tenant's request carried another tenant's content
UNKNOWN = "unknown"              # neither refused nor plainly answered — reported, never assumed ok
PUBLIC_RECON = "public_recon"    # declared public AND hands a stranger the platform's own map
NOT_MEASURED = "not_measured"    # this run could not ask — a gap, stated, never a pass

SEVERITY = {LEAK: "high", FOREIGN_ROWS: "high", GATE_NOT_REACHED: "medium",
            LOCKED_OUT: "medium", PUBLIC_RECON: "medium", UNKNOWN: "low",
            NOT_MEASURED: "gap", OK: "none"}


def _discloses_shape(path):
    """Does this surface hand over the platform's OWN MAP — every route, every field name, every
    request shape? That is reconnaissance: it does not leak a tenant's data, it tells an attacker
    exactly where to aim. The three are the framework's own, so they are named with the fact above
    (`UNPUBLISHED_PLATFORM_OPS`) rather than guessed at from a path pattern."""
    p = "/" + str(path or "").strip("/")
    return any(p == d or p.startswith(d + "/") for _, d in
               ((m, pp) for m, pp in UNPUBLISHED_PLATFORM_OPS))


def verdict(probe, status, body="", foreign_markers=()):
    """What the answer means. Pure: no network, no clock, no state.

    `foreign_markers` are strings belonging to the OTHER tenant (a store code, a company name) whose
    appearance in a body is the leak itself. An empty marker set is never read as "clean" — it is
    reported as `unknown`, because nothing declared means nothing identified (owner 2026-10-30)."""
    expect = probe.get("expect")
    code = int(status or 0)
    text = body if isinstance(body, str) else json.dumps(body, default=str)

    if expect == EXPECT_UNPROBED:
        # A gap, reported as a gap. It is deliberately NOT `ok`: a write this run could not measure
        # must appear in the report, or the next reader will believe the module was covered.
        return NOT_MEASURED

    if expect == EXPECT_PUBLIC:
        # A declared-public surface is not a gate failure — the gate is doing what it was told. But
        # one that hands a stranger the PLATFORM'S OWN MAP is a policy question somebody must answer,
        # and a probe that stayed silent about it would be reporting "clean" on the attacker's first
        # and easiest step. So it is surfaced as its own class, never folded into OK and never
        # mislabelled a leak.
        if 200 <= code < 300 and _discloses_shape(probe.get("path")):
            return PUBLIC_RECON
        return OK

    if expect == EXPECT_REFUSE:
        if code in REFUSAL_CODES:
            return OK
        # The 405 oracle. Reaching the ROUTER means the gate that should have refused never fired.
        if probe.get("probe") == METHOD_PROBE:
            return OK if code in REFUSAL_CODES else GATE_NOT_REACHED
        if code in (404, 405, 422):
            # Past auth, refused by routing or validation. Not data — but the gate did not answer.
            return GATE_NOT_REACHED
        if 200 <= code < 300:
            return LEAK
        return UNKNOWN

    if expect == EXPECT_REACH:
        if code == 403:
            return LOCKED_OUT
        return OK if code < 500 else UNKNOWN

    if expect == EXPECT_NO_FOREIGN_ROWS:
        if code in REFUSAL_CODES:
            return OK
        if not foreign_markers:
            return UNKNOWN
        for marker in foreign_markers:
            if marker and str(marker) in text:
                return FOREIGN_ROWS
        return OK

    return UNKNOWN


def findings(results):
    """Only what is wrong, worst first — a report nobody reads is a control nobody has. A GAP is not
    a finding and is not listed here: it is counted by `coverage` instead, so a hundred unmeasurable
    writes can never bury one real leak. Both are reported; they are not reported as the same thing."""
    bad = [r for r in results if r.get("verdict") not in (OK, NOT_MEASURED)]
    order = {"high": 0, "medium": 1, "low": 2, "gap": 3, "none": 4}
    return tuple(sorted(bad, key=lambda r: (order.get(SEVERITY.get(r.get("verdict"), "low"), 4),
                                            str(r.get("path")))))


def gaps(results):
    """What this run could not ask, as data — the honest other half of a clean report."""
    return tuple(sorted((r for r in results if r.get("verdict") == NOT_MEASURED),
                        key=lambda r: (str(r.get("path")), str(r.get("declared_method")))))


def coverage(schema, module, results):
    """What this run actually measured, and what it did NOT — stated, never implied. A persona with
    no credential is reported as `skipped`, so a run with no test accounts can never read as a pass."""
    ops = modules_from_schema(schema).get(module, ())
    seen = {(r.get("persona")) for r in results}
    per = {}
    for persona in PERSONAS:
        rows = [r for r in results if r.get("persona") == persona]
        sent = [r for r in rows if r.get("verdict") != NOT_MEASURED]
        per[persona] = {"probes": len(sent), "unprobed": len(rows) - len(sent),
                        "state": "ran" if persona in seen else "skipped"}
    return {"module": module, "operations": len(ops), "personas": per,
            "unprobed": len(gaps(results)),
            "measured": all(v["state"] == "ran" for v in per.values())}


# ────────────────────────────────────────── LIVE ──────────────────────────────────────────────────
# Nothing below this line is imported by the harness. Network only.

def _http(method, url, headers=None, timeout=25, limit=4000):
    """One request. `limit` CAPS how much of a body is read — deliberately small for a probe, so a
    report of a leak never itself becomes a copy of the leaked rows. `limit=None` reads in full and
    is used for the route table only, which is the app's own public schema and holds no tenant data.
    (The cap was a real defect on 2026-11-12: the 2.2 MB schema came back truncated and unparseable.)"""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, method=str(method).upper(),
                                 headers={"User-Agent": "metricspro-security-probe", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read() if limit is None else r.read(limit)
            return r.status, raw.decode("utf8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(1000).decode("utf8", "replace")
    except Exception as e:  # a transport failure is a fact about the run, never a pass
        return 0, f"{type(e).__name__}: {e}"


def fetch_schema(base_url):
    status, body = _http("GET", base_url.rstrip("/") + "/openapi.json", timeout=90, limit=None)
    if status != 200:
        raise SystemExit(f"cannot read the route table from {base_url}: HTTP {status}")
    return json.loads(body)


def sign_in(persona):
    """A real sign-in, the way the browser does it — Supabase Auth, email + password → JWT. Returns
    None (never raises, never guesses) when the credential is not configured, and the run reports
    that persona as `skipped`."""
    email_env, pw_env = PERSONAS[persona]["creds"][:2] if PERSONAS[persona]["creds"] else (None, None)
    if not email_env:
        return None
    email, password = os.environ.get(email_env), os.environ.get(pw_env)
    sb_url, sb_key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_ANON_KEY")
    if not (email and password and sb_url and sb_key):
        return None
    import urllib.error
    import urllib.request
    body = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(
        sb_url.rstrip("/") + "/auth/v1/token?grant_type=password", data=body, method="POST",
        headers={"apikey": sb_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())["access_token"]
    except Exception:
        return None


def run(base_url, module, run_index=None, workers=4):
    import concurrent.futures as cf
    schema = fetch_schema(base_url)
    if module is None:
        module = rotation_pick(schema, run_index or 0)
    tokens = {ANON: ""}
    for persona in (REP, CROSS):
        tok = sign_in(persona)
        if tok:
            tokens[persona] = tok
    markers = tuple(m for m in str(os.environ.get("PROBE_FOREIGN_MARKERS", "")).split("|") if m.strip())
    plan = [p for p in plan_for_module(schema, module, personas=tuple(tokens), base_url=base_url)]

    def one(p):
        if p.get("probe") == UNPROBED:
            # Nothing is sent. The row exists so the gap is in the report rather than in nobody's head.
            return {**p, "status": None, "verdict": NOT_MEASURED,
                    "severity": SEVERITY[NOT_MEASURED], "body": ""}
        send_guard(p, base_url)
        headers = {"Authorization": f"Bearer {tokens[p['persona']]}"} if tokens.get(p["persona"]) else {}
        status, body = _http(p["method"], base_url.rstrip("/") + p["path"], headers)
        v = verdict(p, status, body, foreign_markers=markers if p["persona"] == CROSS else ())
        return {**p, "status": status, "verdict": v, "severity": SEVERITY.get(v, "low"),
                "body": body[:300] if v != OK else ""}

    results = []
    with cf.ThreadPoolExecutor(workers) as ex:
        results.extend(ex.map(one, plan))
    cov = coverage(schema, module, results)
    return {"base_url": base_url, "module": module, "run_index": run_index, "coverage": cov,
            "findings": findings(results), "gaps": gaps(results),
            "probes": len(results) - cov["unprobed"]}


def main(argv):
    base = os.environ.get("PROBE_BASE_URL", "https://api.metricspro.tech")
    module = run_index = out = None
    for i, a in enumerate(argv):
        if a == "--module":
            module = argv[i + 1]
        elif a == "--rotate":
            run_index = int(argv[i + 1])
        elif a == "--base":
            base = argv[i + 1]
        elif a == "--out":
            out = argv[i + 1]
        elif a == "--list":
            print("\n".join(rotation(fetch_schema(base))))
            return 0
    report = run(base, module, run_index)
    text = json.dumps(report, indent=2, default=str)
    if out:
        with open(out, "w") as fh:
            fh.write(text)
    cov = report["coverage"]
    print(f"module {report['module']}: {report['probes']} probes, "
          f"{len(report['findings'])} finding(s), {cov['unprobed']} unprobed, "
          f"measured={cov['measured']}")
    for persona, st in cov["personas"].items():
        print(f"  {persona}: {st['state']} ({st['probes']} probes, {st['unprobed']} unprobed)")
    for f in report["findings"]:
        print(f"  [{f['severity']}] {f['verdict']}: {f['persona']} {f['method']} {f['path']} "
              f"→ HTTP {f['status']}")
    # A finding is reported, never thrown: the schedule must keep running and the report is the
    # deliverable. The caller decides what fails a build.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
