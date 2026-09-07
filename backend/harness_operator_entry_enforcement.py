#!/usr/bin/env python3
"""PROOF HARNESS — MANDATORY TENANT-ENTRY SESSIONS.

Migration 985. DB-free, stdlib only, no fastapi, no network: it imports the PURE decision layer
(`app/modules/core/operator.py`) and reads `tenant_middleware.py` / `operator_api.py` as TEXT to
prove the wiring is present and shaped the way the argument below assumes.

WHAT IT HAS TO PROVE
────────────────────
Migration 980 added an audited way into a tenant and left the unaudited one working beside it. This
makes the record a PRECONDITION — which is access-cutting, so every state has to be re-proved.

    §A  REQUIREMENT OFF     — byte-identical to today, for EVERY input combination.
    §B  HOME TENANT         — the escape hatch: your own company is never gated, in ANY state,
                              including one where the entry ledger itself cannot be read.
    §C  EXEMPT PREFIXES     — the console (where a session is opened and the switch turned off),
                              identity/bootstrap, the tenant directory.
    §D  IT ACTUALLY BITES   — a foreign tenant with no / expired / ended / other-org session is
                              REFUSED, and refused rather than silently rewritten.
    §E  BROKEN LEDGER       — unreadable entry log: foreign tenant refused, home tenant unaffected.
    §F  THE REFUSALS        — turning the requirement on is refused when nobody holds `tenant.enter`.
    §G  A's ESCAPE HATCH    — B must not close the door A left open (mig 984 + 985 together).
    §H  WIRING + RULE TWO   — the middleware branch, the kill switch, no person/tenant literals.
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.core import operator as OP  # noqa: E402

PASS = FAIL = 0
NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
HERE = os.path.dirname(os.path.abspath(__file__))

HOME = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"     # a company the operator IS a member of
FOREIGN = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"  # a customer's company
OTHER = "cccccccc-3333-4333-8333-cccccccccccc"    # a third company


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, ("— " + extra) if extra else ""))


def section(t):
    print("\n" + "─" * 96 + "\n%s\n" % t + "─" * 96)


def iso(d):
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sess(org, *, expires=None, ended=None):
    return {"id": "s1", "actor_auth_id": "u1", "org_id": org,
            "started_at": iso(NOW - timedelta(minutes=5)),
            "expires_at": expires if expires is not None else iso(NOW + timedelta(minutes=25)),
            "ended_at": ended}


ON = {"require_entry_session": True}
OFF = {"require_entry_session": False}

PATHS = ["/api/v1/hr/employees", "/api/v1/commcalc/exec-mtd", "/api/v1/closing/submissions",
         "/api/v1/crm/customers", "/api/v1/asset/inventory", "/api/v1/core/access-log",
         "/api/v1/billing/plans", "/api/v1/core/ip-block"]


def d(path, org, *, policy=ON, member_orgs=(HOME,), session=None, failed=False):
    return OP.entry_requirement_decision(policy=policy, path=path, requested_org=org,
                                         member_orgs=member_orgs, session=session,
                                         session_lookup_failed=failed, now=NOW)


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§A  REQUIREMENT OFF — deploying this changes who can reach what by exactly NOTHING")
# ══════════════════════════════════════════════════════════════════════════════════════════════
off_policies = [None, {}, "garbage", 42, [], OFF,
                {"require_entry_session": "maybe"},        # unparseable ⇒ default False
                {"require_entry_session": None},
                {"require_entry_session": 1},              # 1 is not a bool ⇒ default False
                {"legacy_membership_flag_honored": False, "enforce_scoped_roles": True}]
denied = required = combos = 0
for pol in off_policies:
    for path in PATHS:
        for org in (None, "", HOME, FOREIGN, OTHER, "not-a-uuid"):
            for s in (None, sess(FOREIGN), sess(FOREIGN, ended=iso(NOW))):
                for failed in (False, True):
                    combos += 1
                    v = d(path, org, policy=pol, session=s, failed=failed)
                    if not v["allowed"]:
                        denied += 1
                    if v["required"]:
                        required += 1
check("A1 with the switch off NOTHING is ever refused (%d combinations swept)" % combos,
      denied == 0, "denied=%d" % denied)
check("A2 …and the decision never reports itself as requiring anything", required == 0,
      "required=%d" % required)
check("A3 the shipped DEFAULT is off", OP.POLICY_DEFAULTS["require_entry_session"] is False)
check("A4 an ABSENT policy row (pre-980, half-applied, unreadable) is off",
      OP.effective_policy(None)["require_entry_session"] is False)
check("A5 a policy row that predates the flag entirely reads off",
      OP.effective_policy({"entry_min_minutes": 5})["require_entry_session"] is False)


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§B  THE ESCAPE HATCH — your OWN company is never gated, in any state")
# ══════════════════════════════════════════════════════════════════════════════════════════════
states = [("no session", None, False), ("expired session", sess(HOME, expires=iso(NOW - timedelta(minutes=1))), False),
          ("ended session", sess(HOME, ended=iso(NOW)), False),
          ("a session for a DIFFERENT company", sess(FOREIGN), False),
          ("THE ENTRY LEDGER IS UNREADABLE", None, True)]
for label, s, failed in states:
    bad = [p for p in PATHS if not d(p, HOME, session=s, failed=failed)["allowed"]]
    check("B1 home tenant reachable on every path — %s" % label, not bad, str(bad[:2]))
check("B2 …and the home-tenant answer never claims the requirement applied",
      d("/api/v1/hr/employees", HOME, failed=True)["code"] == "home_tenant")
check("B3 a login with SEVERAL memberships keeps all of them",
      all(d("/api/v1/hr/employees", o, member_orgs=(HOME, OTHER))["allowed"] for o in (HOME, OTHER)))
check("B4 naming NO tenant at all is never gated (the operator acting as themselves)",
      d("/api/v1/hr/employees", None)["code"] == "no_target"
      and d("/api/v1/hr/employees", "")["allowed"])
check("B5 …even with an unreadable ledger", d("/api/v1/hr/employees", None, failed=True)["allowed"])


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§C  EXEMPT PREFIXES — the surfaces the requirement must never be able to close")
# ══════════════════════════════════════════════════════════════════════════════════════════════
HATCHES = [
    ("/api/v1/core/operator/enter", "opening the entry session the requirement demands"),
    ("/api/v1/core/operator/exit", "closing it again"),
    ("/api/v1/core/operator/policy", "switching the requirement back OFF"),
    ("/api/v1/core/operator/me", "the console's identity read"),
    ("/api/v1/core/me", "who am I"),
    ("/api/v1/core/my-tenants", "which companies may I act as"),
    ("/api/v1/core/bootstrap", "session bootstrap"),
    ("/api/v1/core/tenants", "the directory a company is chosen FROM"),
    ("/api/v1/core/platform-notice", "the tenant-facing status banner"),
]
for p, why in HATCHES:
    check("C1 %s is exempt (%s)" % (p, why),
          OP.is_entry_exempt(p) and d(p, FOREIGN)["allowed"] and d(p, FOREIGN, failed=True)["allowed"])
check("C2 exemption is segment-aware — /core/operator-archive is NOT exempt",
      not OP.is_entry_exempt("/api/v1/core/operator-archive"))
check("C3 …and /core/tenants-export is NOT exempt",
      not OP.is_entry_exempt("/api/v1/core/tenants-export"))
check("C4 a tenant-DATA path is never exempt",
      not any(OP.is_entry_exempt(p) for p in
              ("/api/v1/hr/employees", "/api/v1/commcalc/exec-mtd", "/api/v1/closing/submissions")))


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§D  IT ACTUALLY BITES — a foreign company without an open session is REFUSED")
# ══════════════════════════════════════════════════════════════════════════════════════════════
CASES = [
    ("no session at all", None),
    ("an EXPIRED session", sess(FOREIGN, expires=iso(NOW - timedelta(seconds=1)))),
    ("a session ended by the operator", sess(FOREIGN, ended=iso(NOW - timedelta(minutes=1)))),
    ("a session for a DIFFERENT company", sess(OTHER)),
    ("a session with an unreadable expiry (no time-box is no session)", sess(FOREIGN, expires="junk")),
    ("a session with no expiry at all", sess(FOREIGN, expires=None) | {"expires_at": None}),
]
for label, s in CASES:
    v = d("/api/v1/hr/employees", FOREIGN, session=s)
    check("D1 foreign company refused — %s" % label,
          v["required"] and not v["allowed"] and v["code"] == "entry_session_required")
    check("D2 …and the refusal tells the operator what to do about it",
          "operator console" in (v["message"] or "").lower())
v = d("/api/v1/hr/employees", FOREIGN, session=sess(FOREIGN))
check("D3 an OPEN, unexpired session for THAT company is allowed",
      v["required"] and v["allowed"] and v["code"] == "ok")
check("D4 …and the decision reports the org it judged, so a log can show it",
      v["org_id"] == FOREIGN)
check("D5 a session that expires one second from now still works (no early cut-off)",
      d("/api/v1/hr/employees", FOREIGN,
        session=sess(FOREIGN, expires=iso(NOW + timedelta(seconds=1))))["allowed"])
check("D6 the refusal is a REFUSAL, not a rewrite — no other org is ever proposed",
      set(d("/api/v1/hr/employees", FOREIGN).keys())
      == {"required", "allowed", "code", "message", "org_id"}
      and d("/api/v1/hr/employees", FOREIGN)["org_id"] == FOREIGN)
check("D7 the middleware and the module agree on the code the client keys on",
      OP.ENTRY_REFUSAL_CODE == "operator_entry_session_required")


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§E  THE ENTRY LEDGER ITSELF IS BROKEN")
# ══════════════════════════════════════════════════════════════════════════════════════════════
v = d("/api/v1/hr/employees", FOREIGN, failed=True)
check("E1 an unreadable entry log refuses a FOREIGN company — an unrecorded entry is the one thing "
      "this setting exists to prevent", not v["allowed"] and v["code"] == "entry_ledger_unreadable")
check("E2 …and says so honestly rather than blaming the operator",
      "could not be read" in (v["message"] or ""))
check("E3 …while the operator's OWN company is explicitly unaffected",
      "own company is unaffected" in (v["message"] or "")
      and d("/api/v1/hr/employees", HOME, failed=True)["allowed"])
check("E4 …and the console stays reachable, so the switch can be turned back off",
      d("/api/v1/core/operator/policy", FOREIGN, failed=True)["allowed"])


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§F  THE REFUSAL — turning the requirement on when nobody could satisfy it")
# ══════════════════════════════════════════════════════════════════════════════════════════════
OWNER_ROW = {"auth_id": "u1", "email": "op-a@example.test", "operator_role": "owner",
             "is_active": True, "expires_at": None}
BILLING_ROW = {"auth_id": "u2", "email": "op-c@example.test", "operator_role": "billing",
               "is_active": True, "expires_at": None}
r = OP.policy_change_decision(current_policy={"legacy_membership_flag_honored": False},
                              requested={"require_entry_session": True},
                              active_registry_operators=1, active_rows=[BILLING_ROW])
check("F1 refused when no active operator holds `tenant.enter`",
      not r["allowed"] and r["code"] == "would_block_entry")
check("F2 …and the refusal reassures that the home tenant is unaffected either way",
      "home tenant is never affected" in r["message"])
r = OP.policy_change_decision(current_policy={"legacy_membership_flag_honored": False},
                              requested={"require_entry_session": True},
                              active_registry_operators=2, active_rows=[OWNER_ROW, BILLING_ROW])
check("F3 allowed once somebody holds it", r["allowed"] and r["policy"]["require_entry_session"])
r = OP.policy_change_decision(current_policy={}, requested={"require_entry_session": True},
                              active_registry_operators=0, active_rows=[])
check("F4 with the legacy flag still honored the flip is never refused (legacy carries every "
      "capability, including tenant.enter)", r["allowed"])
r = OP.policy_change_decision(current_policy=ON, requested={"require_entry_session": False},
                              active_registry_operators=0, active_rows=[])
check("F5 turning the requirement OFF is NEVER refused — the way back is always open",
      r["allowed"] and r["policy"]["require_entry_session"] is False)
check("F6 `support` and `owner` hold tenant.enter; `billing` and `readonly` do not",
      OP.CAP_TENANT_ENTER in OP.OPERATOR_ROLES["support"]
      and OP.CAP_TENANT_ENTER in OP.OPERATOR_ROLES["owner"]
      and OP.CAP_TENANT_ENTER not in OP.OPERATOR_ROLES["billing"]
      and OP.CAP_TENANT_ENTER not in OP.OPERATOR_ROLES["readonly"])


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§G  BOTH HALVES TOGETHER — B must not close the door A left open")
# ══════════════════════════════════════════════════════════════════════════════════════════════
BOTH = {"require_entry_session": True, "enforce_scoped_roles": True,
        "legacy_membership_flag_honored": False}
OWNER = {"auth_id": "u1", "email": "op-a@example.test", "operator_role": "owner",
         "is_active": True, "expires_at": None}
# The worst realistic state: both switches on, the cutover done, and the entry ledger unreadable.
for p in ("/api/v1/core/operator/policy", "/api/v1/core/operator/enforcement",
          "/api/v1/core/operator/me", "/api/v1/core/me"):
    entry_ok = d(p, FOREIGN, policy=BOTH, failed=True)["allowed"]
    scope_ok = OP.endpoint_decision(path=p, method="POST", legacy_super_admin=False,
                                    operator_row=OWNER, policy=BOTH, now=NOW)["allowed"]
    check("G1 %s survives BOTH enforcements + the cutover + a broken entry ledger" % p,
          entry_ok and scope_ok)
check("G2 the owner still reaches their OWN company's data with both switches on",
      d("/api/v1/hr/employees", HOME, policy=BOTH, failed=True)["allowed"]
      and OP.endpoint_decision(path="/api/v1/hr/employees", method="GET", legacy_super_admin=False,
                               operator_row=OWNER, policy=BOTH, now=NOW)["allowed"])
check("G3 the two exempt lists both contain the console — neither can seal the other in",
      OP.is_entry_exempt("/api/v1/core/operator/policy")
      and OP.is_enforcement_exempt("/api/v1/core/operator/policy"))
check("G4 an entry session still confers ONLY the acting-org switch — never `impersonate`",
      tuple(OP.ENTRY_GRANTS) == ("acting_org",)
      and not any("impersonate" in c for c in OP.ALL_CAPABILITIES))
check("G5 the impersonation prefix is untouched by BOTH enforcements",
      OP.is_enforcement_exempt("/api/v1/impersonation/start")
      and OP.endpoint_capability("/api/v1/impersonation/start", "POST") is None)
check("G6 entering a tenant is a CAPABILITY, so mig 984 can withhold it from a role that should "
      "not have it", OP.CAP_TENANT_ENTER in OP.ALL_CAPABILITIES)


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§H  WIRING + RULE TWO")
# ══════════════════════════════════════════════════════════════════════════════════════════════
SRC = {n: open(os.path.join(HERE, n)).read() for n in (
    "app/core/tenant_middleware.py", "app/modules/core/operator_api.py",
    "app/modules/core/operator.py")}
MIG = open(os.path.join(HERE, "..", "database", "migrations",
                        "985_mandatory_entry_sessions.sql")).read()
mw = SRC["app/core/tenant_middleware.py"]

m = re.search(r"\n        if super_admin:\n(.*?)\n            return await self\.app\(scope, receive, send\)",
              mw, re.S)
branch = m.group(1) if m else ""
check("H1 the requirement is applied inside the SUPER-ADMIN branch, where x-active-org is honored",
      "_entry_verdict" in branch and "_reject_entry_session" in branch)
check("H2 …BEFORE the acting org is published, so a refused request never stamps a tenant",
      branch.index("_entry_verdict") < branch.index("_set_acting"))
check("H3 the refusal is a 403 that keeps the session alive (not a 401)",
      re.search(r'async def _reject_entry_session.*?"status": 403', mw, re.S) is not None)
_rej = re.search(r"^async def _reject_entry_session\(.*?(?=^\S)", mw, re.S | re.M)
check("H4 …and never rewrites the caller onto some other tenant",
      bool(_rej) and "_set_acting" not in _rej.group(0) and "_ACTING_ORG" not in _rej.group(0))
check("H5 there is an environment kill switch that needs no database",
      "OPERATOR_ENTRY_ENFORCE" in mw and "_entry_enforce" in mw)
check("H6 the verdict helper can never raise into the request path",
      re.search(r"def _entry_verdict.*?except Exception:\s*\n\s*return None", mw, re.S) is not None)
check("H7 the policy is read once and cached, not on every request",
      "_ENTRY_TTL" in mw and "_entry_cache" in mw)
check("H8 the SESSION is only looked up when a foreign tenant is genuinely claimed",
      re.search(r'if not d\["required"\]:\s*\n\s*return None\s*\n\s*row, failed = _entry_session_row',
                mw, re.S) is not None)
check("H9 a MISS is cached briefly, so opening a session is not swallowed by a TTL",
      "fresh = _ENTRY_TTL if" in mw and "2.0" in mw)
check("H10 opening or closing a session busts the cache in-process",
      "_bust_entry_cache" in SRC["app/modules/core/operator_api.py"])
check("H11 the decision layer is PURE — the middleware supplies the row, it does not fetch one",
      "get_supabase" not in re.search(r"def entry_requirement_decision.*?(?=\n\ndef |\Z)",
                                      SRC["app/modules/core/operator.py"], re.S).group(0))


def executable_text(src):
    import io
    import tokenize
    return " ".join(t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
                    if t.type not in (tokenize.COMMENT, tokenize.STRING)).lower()


FORBIDDEN = ("sanjot", "cellfonz", "00000000-0000-0000-0000-000000000001",
             "854f6d7b-6590-4e4d-88ab-646f560d4f4c", "luxelink")
for n in SRC:
    hits = [t for t in FORBIDDEN if t in executable_text(SRC[n])]
    check("H12 RULE TWO: no person/tenant/org literal in %s's executable path" % n, not hits,
          str(hits))
mig_code = "\n".join(l for l in MIG.lower().split("\n") if not l.lstrip().startswith("--"))
check("H13 RULE TWO: migration 985 names no tenant, person or org id",
      not any(t in mig_code for t in FORBIDDEN))
check("H14 the access-cutting seed ships COMMENTED OUT",
      "-- UPDATE core.platform_operator_policy SET require_entry_session = TRUE" in MIG
      and not re.search(r"^\s*UPDATE core\.platform_operator_policy SET require_entry_session = TRUE",
                        MIG, re.M))
check("H15 the migration carries a -- REVERT: note and names the env kill switch",
      "-- REVERT:" in MIG and "OPERATOR_ENTRY_ENFORCE=0" in MIG)
check("H16 985 is additive: it touches storeops.app_users not at all, and creates no table",
      "app_users" not in mig_code and "create table" not in mig_code)
check("H17 985 adds the index the per-request lookup actually needs",
      "operator_entry_current_idx" in MIG
      and "(actor_auth_id, org_id, started_at DESC)" in MIG)


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 96)
print("  %d passed, %d failed" % (PASS, FAIL))
print("═" * 96)
sys.exit(1 if FAIL else 0)
