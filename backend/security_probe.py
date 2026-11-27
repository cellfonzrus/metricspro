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


# ── 1b. THE ACCOUNTS — the ONE home for "who may this run sign in as" ────────────────────────────
# Sanjot's 2026-11-23 directive: two sales reps from three tenants. That shape is DECLARED in the
# environment, never in code, so a fourth tenant or a third rep needs no edit here (RULE TWO: no
# tenant name, no count, no role literal below).
#
# WHY TWO REPS IN ONE TENANT. One rep per tenant can only ask the Manan question — does a confined
# employee reach a manager's URL. It cannot ask the SECOND leak's question (§STOREOPS-07.65): can
# rep A read rep B's hours? That needs a victim in the same company, so a tenant's accounts are kept
# as a LIST and never collapsed to one.
#
# WHY A ROW HOLDS NO PASSWORD. Every row is serialised into the report. A row therefore carries the
# NAME of the secret that holds the password and never the password, and `probe_accounts` is the only
# code that resolves a name to a value. §J of the harness fails the build if a credential or an email
# can be found in a plan.
ACCOUNTS_ENV = "PROBE_ACCOUNTS"          # a JSON list: tenant, email, password_env, markers
LEGACY_REP = ("PROBE_REP_EMAIL", "PROBE_REP_PASSWORD", "PROBE_FOREIGN_MARKERS")


def _account_row(kind, tenant, email, password_env, markers, seq):
    return {"label": f"{kind}:{tenant}#{seq}", "kind": kind, "tenant": str(tenant),
            "email": str(email), "password_env": str(password_env),
            "markers": tuple(str(m).strip() for m in (markers or ()) if str(m).strip())}


def probe_accounts(env=None):
    """Every signed-in identity this run may use. DEFAULT NONE — an absent, malformed or
    half-configured declaration yields NO account, so the run reports the persona unmeasured instead
    of guessing. Never raises: a broken secret must not stop the stranger's pass from running."""
    env = os.environ if env is None else env
    rows, seq = [], {}
    raw = str(env.get(ACCOUNTS_ENV, "") or "").strip()
    declared = []
    if raw:
        try:
            parsed = json.loads(raw)
            declared = parsed if isinstance(parsed, list) else []
        except Exception:
            declared = []          # a half-read declaration is no declaration
    for item in declared:
        if not isinstance(item, dict):
            continue
        tenant, email = str(item.get("tenant", "") or "").strip(), str(item.get("email", "") or "").strip()
        pw_env = str(item.get("password_env", "") or "").strip()
        # The secret must EXIST. An account whose password is not configured is dropped, because a
        # sign-in that cannot happen must appear as a gap and never as a persona that "ran".
        if not (tenant and email and pw_env and env.get(pw_env)):
            continue
        seq[tenant] = seq.get(tenant, 0) + 1
        rows.append(_account_row(REP, tenant, email, pw_env, item.get("markers"), seq[tenant]))
    if not rows:
        # The original single-rep declaration, read by this SAME home so there is no second reader.
        e_env, p_env, m_env = LEGACY_REP
        if env.get(e_env) and env.get(p_env):
            rows.append(_account_row(REP, "legacy", env[e_env], p_env,
                                     str(env.get(m_env, "")).split("|"), 1))
    return tuple(rows)


def foreign_markers(accounts, label):
    """What a marker in THIS account's answer would prove. DERIVED from the other tenants' own
    declarations — an account's own tenant marker is never foreign, or every honest answer would
    read as a leak. Empty means nothing was declared, which `verdict` reports as `unknown`, never
    as clean (owner 2026-10-30: nothing declared means nothing identified)."""
    mine = next((a["tenant"] for a in accounts if a["label"] == label), None)
    out = []
    for a in accounts:
        if a["tenant"] == mine:
            continue
        out.extend(m for m in a["markers"] if m not in out)
    return tuple(out)


def kind_of(persona):
    """Which of the three questions this persona label asks. A label is `<kind>:<tenant>#<n>`, so a
    hundred accounts still dispatch on three kinds and no caller grows a per-account branch."""
    p = str(persona or "")
    head = p.split(":", 1)[0]
    return head if head in PERSONAS else p


# ── 2. WHAT A CORRECTLY-GATED PLATFORM MUST ANSWER ────────────────────────────────────────────────
def _is_public(method, path):
    """Dereferenced from the middleware's OWN answer, never copied. `public_surface_allows` is the
    one home for "declared public for this path AND this method" — the same single call the
    middleware's dispatch makes, so this probe cannot drift from what the platform actually does.
    (Until 2026-11-23 this function re-combined `_is_public` and `_PUBLIC_METHODS` itself: a second
    copy of the answer, which is what the house rule against copies exists to prevent.)"""
    return tm.public_surface_allows(path, str(method).upper())


def expectation(persona, method, path):
    """What this persona must be told. DEFAULT REFUSE — the same default as the gate itself.
    Dispatches on the persona's KIND, so a second rep in the same tenant is held to the identical
    declared standard as the first and no account can acquire a standard of its own."""
    m = str(method).upper()
    if _is_public(m, path):
        return EXPECT_PUBLIC
    persona = kind_of(persona)
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


# A persona can be stopped by a door that is NOT the gate under test. Two-factor enforcement is the
# live one: the middleware answers `2fa_required` before any module gate is consulted, so the probe
# never reaches the question it came to ask. Read as a refusal that is a measurement, a tenant turning
# 2FA on would silently convert every module to "no leak found" — the exact shape of the accident this
# tool exists to prevent. It is therefore a GAP. No tenant has such a policy configured today, which
# is why this is a lock and not a fix.
BLINDED_MARKS = ("2fa_required", "two-factor authentication required")


def blinded_verdict(body=""):
    """NOT_MEASURED when the answer proves a different door stopped us; None when it does not."""
    text = (body if isinstance(body, str) else json.dumps(body, default=str)).lower()
    return NOT_MEASURED if any(mark in text for mark in BLINDED_MARKS) else None


# ── ★ AND THE SAME RULE FOR THE OTHER WAY AN ANSWER CAN BE UNJUDGEABLE ★ ─────────────────────────
# `blinded_verdict` catches a DIFFERENT DOOR having stopped us. This catches the other half: an
# expectation whose verdict turns on WHOSE ROWS came back cannot be judged when no marker names
# another tenant's content. Found 2026-11-27 (§CORE-53) while wiring the first real rep accounts:
# `EXPECT_NO_FOREIGN_ROWS` refused to judge such a body and `EXPECT_REACH` fell through to OK, so
# one branch honoured this module's own stated rule — "an empty marker set is never read as clean,
# because nothing declared means nothing identified" (owner 2026-10-30) — and the other did not.
# That is the one-branch-checks-and-another-does-not shape `_foreign_in` was made to close, in the
# EMPTY-SET direction, and it had no symptom because it only bites when exactly ONE tenant is
# declared — the state the platform enters the moment the first tenant's accounts exist and before
# the second's do.
#
# IT IS ASKED PER-BRANCH, NOT UP FRONT, AND THAT PLACEMENT IS THE POINT. A LOCKOUT is a fact about
# the DOOR and a refusal is a fact about the GATE; both are judgeable with no markers whatsoever.
# Refusing to judge them would trade one blind spot for another, and ★ NO LOCKOUT ★ is a defect of
# equal weight to a leak. So each branch answers its own door question FIRST and asks this only
# before it would read a body.
MARKER_DEPENDENT = frozenset({EXPECT_REACH, EXPECT_NO_FOREIGN_ROWS})


def judgeable_verdict(expect, foreign_markers=()):
    """NOT_MEASURED when this expectation's verdict turns on markers and none were declared; None
    when the answer can be judged. PURE. ONE home, so a branch cannot keep its own rule."""
    if expect in MARKER_DEPENDENT and not tuple(m for m in (foreign_markers or ()) if str(m).strip()):
        return NOT_MEASURED
    return None


def _foreign_in(text, markers):
    """Is another tenant's content in this body? ONE home, so every expectation that can receive a
    body asks the same question the same way — a second copy of this loop is how one branch ends up
    checking and another not (which is exactly what happened before 2026-11-23)."""
    return any(m and str(m) in text for m in (markers or ()))


def verdict(probe, status, body="", foreign_markers=()):
    """What the answer means. Pure: no network, no clock, no state.

    `foreign_markers` are strings belonging to the OTHER tenant (a store code, a company name) whose
    appearance in a body is the leak itself. An empty marker set is never read as "clean" — it is
    reported as `unknown`, because nothing declared means nothing identified (owner 2026-10-30)."""
    expect = probe.get("expect")
    code = int(status or 0)
    text = body if isinstance(body, str) else json.dumps(body, default=str)

    # Asked FIRST, before any expectation: if a different door stopped the probe, nothing about the
    # gate under test was measured, whatever the status code happens to be.
    blinded = blinded_verdict(text)
    if blinded:
        return blinded

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
        # ★ THE BLIND SPOT THIS BRANCH USED TO HAVE (closed 2026-11-23, run 327). Any non-2xx on a
        # declared-public path returned OK, so a public surface that refused EVERY caller was
        # reported as a pass. That is how `/api/v1/pos/public/w9/{token}` — the link a wholesale
        # customer is emailed to return their Form W-9 — answered `401 {"detail":"sign in to perform
        # this action"}` on production through 325 clean runs: the `pos` router's own router-level
        # gate refused it before the handler, and this probe called that fine. NO LOCKOUT is a defect
        # of equal weight to a leak (the agent doc says so and `LOCKED_OUT` already existed); it was
        # simply never reachable for an anonymous caller.
        #
        # Which 401s are correct is asked of the DECLARATION, not guessed from the path: a public
        # path that verifies a caller of its own may refuse a stranger; one that authenticates by a
        # capability it is handed (a link token, an HMAC, a cron secret) must not, because an
        # anonymous caller IS its intended caller.
        if (code in REFUSAL_CODES
                and not tm.public_path_verifies_its_own_caller(probe.get("path"))
                and not _discloses_shape(probe.get("path"))):
            return LOCKED_OUT
        # `_discloses_shape` is exempt on purpose: gating /openapi.json, /docs and /redoc is the
        # very change this probe reports PUBLIC_RECON to ask for, so the day the owner makes it the
        # finding must CLEAR — not turn into a lockout accusing the fix.
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
        # ★ THE EMPLOYEE'S OWN SURFACE IS WHERE A CROSS-TENANT LEAK HIDES BEST ★ — found by the
        # integration test on 2026-11-23. This branch used to return OK for any answer under 500,
        # so a schedule or time-clock screen a rep is SUPPOSED to reach could hand back another
        # tenant's rows and the probe would call it clean. Whose rows came back is a separate
        # question from whether the door opened, and every answered body is now asked both.
        # The door opened. Whose rows came out is a SEPARATE question, and it cannot be asked at
        # all without a marker — so it is reported as the gap it is rather than as a clean answer.
        blind = judgeable_verdict(expect, foreign_markers)
        if blind:
            return blind
        leak = _foreign_in(text, foreign_markers)
        if leak:
            return FOREIGN_ROWS
        return OK if code < 500 else UNKNOWN

    if expect == EXPECT_NO_FOREIGN_ROWS:
        if code in REFUSAL_CODES:
            return OK                       # the gate answered; no marker was needed to judge that
        blind = judgeable_verdict(expect, foreign_markers)
        if blind:
            return blind                    # was `UNKNOWN` — a thing we could not ASK is a GAP, and
        return FOREIGN_ROWS if _foreign_in(text, foreign_markers) else OK

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


def coverage(schema, module, results, accounts=()):
    """What this run actually measured, and what it did NOT — stated, never implied. A persona with
    no credential is reported as `skipped`, so a run with no test accounts can never read as a pass.

    Every declared account is named SEPARATELY. Two reps in one tenant are two measurements, and
    rolling them into one "rep: ran" would hide the case where only one of them could sign in."""
    ops = modules_from_schema(schema).get(module, ())
    seen = {(r.get("persona")) for r in results}
    # The three kinds are always listed, present or not; each declared account is listed as well, so
    # a reader can see WHICH identity was blind rather than only that something was.
    names = list(PERSONAS) + [a["label"] for a in accounts if a["label"] not in PERSONAS]
    per = {}
    for persona in names:
        rows = [r for r in results if r.get("persona") == persona]
        sent = [r for r in rows if r.get("verdict") != NOT_MEASURED]
        ran = persona in seen
        if persona == REP and accounts:
            # With accounts declared, the bare kind is satisfied by its accounts rather than by a
            # persona of its own — otherwise a fully measured multi-tenant run could never read
            # as measured.
            ran = ran or any(a["label"] in seen for a in accounts if a["kind"] == REP)
        per[persona] = {"probes": len(sent), "unprobed": len(rows) - len(sent),
                        "state": "ran" if ran else "skipped"}
    return {"module": module, "operations": len(ops), "personas": per,
            "accounts": tuple(a["label"] for a in accounts),
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


def sign_in(account):
    """A real sign-in, the way the browser does it — Supabase Auth, email + password → JWT. Takes an
    ACCOUNT ROW and resolves its password from the named secret here, at the last possible moment, so
    the value never travels through the plan or the report. Returns None (never raises, never
    guesses) when the credential is not configured, and the run reports that persona as `skipped`."""
    if isinstance(account, str):      # a bare persona kind: the original single-identity form
        creds = PERSONAS.get(account, {}).get("creds") or ()
        if not creds:
            return None
        email, password = os.environ.get(creds[0]), os.environ.get(creds[1])
    else:
        email, password = account.get("email"), os.environ.get(account.get("password_env", ""))
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


def _markers_for(accounts, persona):
    """The foreign set for one persona. The stranger carries none (it is refused before any body),
    the admin persona falls back to the original single declaration."""
    if persona == ANON:
        return ()
    if persona == CROSS:
        return tuple(m for m in str(os.environ.get(LEGACY_REP[2], "")).split("|") if m.strip())
    return foreign_markers(accounts, persona)


def run(base_url, module, run_index=None, workers=4):
    import concurrent.futures as cf
    schema = fetch_schema(base_url)
    if module is None:
        module = rotation_pick(schema, run_index or 0)
    tokens = {ANON: ""}
    accounts = probe_accounts()
    for account in accounts:
        tok = sign_in(account)
        if tok:
            tokens[account["label"]] = tok
    # The cross-tenant ADMIN persona is a different question from a rep of another tenant, and only
    # its own credential can ask it. Reps never stand in for it.
    cross_tok = sign_in(CROSS)
    if cross_tok:
        tokens[CROSS] = cross_tok
    plan = [p for p in plan_for_module(schema, module, personas=tuple(tokens), base_url=base_url)]

    def one(p):
        if p.get("probe") == UNPROBED:
            # Nothing is sent. The row exists so the gap is in the report rather than in nobody's head.
            return {**p, "status": None, "verdict": NOT_MEASURED,
                    "severity": SEVERITY[NOT_MEASURED], "body": ""}
        send_guard(p, base_url)
        headers = {"Authorization": f"Bearer {tokens[p['persona']]}"} if tokens.get(p["persona"]) else {}
        status, body = _http(p["method"], base_url.rstrip("/") + p["path"], headers)
        # EVERY signed-in answer is checked for another tenant's content, not only the admin's: a
        # rep of tenant A receiving tenant B's store code is the same leak wearing a smaller hat.
        v = verdict(p, status, body, foreign_markers=_markers_for(accounts, p["persona"]))
        return {**p, "status": status, "verdict": v, "severity": SEVERITY.get(v, "low"),
                "body": body[:300] if v != OK else ""}

    results = []
    with cf.ThreadPoolExecutor(workers) as ex:
        results.extend(ex.map(one, plan))
    cov = coverage(schema, module, results, accounts=accounts)
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
