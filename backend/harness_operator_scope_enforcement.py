#!/usr/bin/env python3
"""PROOF HARNESS — SCOPED OPERATOR ROLES, ENFORCED ON THE PRE-EXISTING SUPER-ADMIN ENDPOINTS.

Migration 984. DB-free, stdlib only, no fastapi, no network: it imports the PURE decision layer
(`app/modules/core/operator.py`) and reads the two I/O files as TEXT to prove the wiring is present.

WHAT IT HAS TO PROVE — one thing above all others
─────────────────────────────────────────────────
Migration 980 could only ever ADD authority (`authority = legacy flag ∪ registry row`), so it had a
safety net by construction. THIS change NARROWS, so the net has to be rebuilt deliberately. Every
section below exists to answer: "in this rollout state, can the owner still administer the platform?"

    §A  ENFORCEMENT OFF          — byte-identical to today, for EVERY input combination.
    §B  ENFORCEMENT ON, seeded   — the owner keeps every surface.
    §C  EXPIRED / INACTIVE ROW   — still in, via the legacy flag.
    §D  REGISTRY EMPTY           — still in, via the legacy flag.
    §E  HALF-APPLIED 984         — a policy row with no `enforce_scoped_roles` column ⇒ OFF ⇒ today.
    §F  THE ONE LOSING STATE     — post-cutover + empty registry — and the refusal that forbids it.
    §G  ESCAPE HATCHES           — the console, identity/bootstrap and impersonation are never gated.
    §H  IT ACTUALLY BITES        — a narrow role really is refused, or none of this was worth doing.
    §I  ROUTE RESOLUTION         — longest prefix, verb specificity, segment safety, bad config.
    §J  RULE TWO + the wiring    — no person/tenant literal in the authorization path; the gate is
                                   still ONE gate and still has its unchanged pre-984 branch.
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


# The real, mapped super-admin surfaces this platform has today (every one of them is a route that
# calls `_require_super_admin`). Used as the sweep set everywhere below.
SURFACES = [
    ("PUT", "/api/v1/core/auth-config"),
    ("GET", "/api/v1/core/access-log"),
    ("GET", "/api/v1/core/ip-block"),
    ("POST", "/api/v1/core/ip-block"),
    ("POST", "/api/v1/core/ip-block/remove"),
    ("POST", "/api/v1/core/sessions/revoke"),
    ("GET", "/api/v1/core/export-event"),
    ("GET", "/api/v1/core/tenants"),
    ("POST", "/api/v1/core/tenants"),
    ("PATCH", "/api/v1/core/tenants/854f6d7b-6590-4e4d-88ab-646f560d4f4c"),
    ("POST", "/api/v1/core/tenants/854f6d7b-6590-4e4d-88ab-646f560d4f4c/sync"),
    ("GET", "/api/v1/core/super-admins"),
    ("POST", "/api/v1/core/super-admins"),
    ("DELETE", "/api/v1/core/super-admins"),
    ("POST", "/api/v1/core/reinstate-login"),
    ("POST", "/api/v1/core/users/reset-password"),
    ("GET", "/api/v1/core/control-box"),
    ("POST", "/api/v1/core/control-box/run"),
    ("GET", "/api/v1/fix-pipeline/requests"),
    ("POST", "/api/v1/fix-pipeline/requests"),
    ("GET", "/api/v1/billing/plans"),
    ("POST", "/api/v1/billing/pricing/settings"),
    ("GET", "/api/v1/billing/ai-usage"),
    ("PUT", "/api/v1/billing/module-pricing"),
]

OWNER_ROW = {"auth_id": "11111111-1111-1111-1111-111111111111", "email": "op-a@example.test",
             "operator_role": "owner", "is_active": True, "expires_at": None}
SUPPORT_ROW = {"auth_id": "22222222-2222-2222-2222-222222222222", "email": "op-b@example.test",
               "operator_role": "support", "is_active": True, "expires_at": None}
BILLING_ROW = {"auth_id": "33333333-3333-3333-3333-333333333333", "email": "op-c@example.test",
               "operator_role": "billing", "is_active": True, "expires_at": None}
EXPIRED_ROW = dict(OWNER_ROW, auth_id="44444444-4444-4444-4444-444444444444",
                   email="op-d@example.test", expires_at=iso(NOW - timedelta(hours=1)))
INACTIVE_ROW = dict(OWNER_ROW, is_active=False)

POLICY_OFF = {"legacy_membership_flag_honored": True, "enforce_scoped_roles": False}
POLICY_ON = {"legacy_membership_flag_honored": True, "enforce_scoped_roles": True}
POLICY_ON_CUTOVER = {"legacy_membership_flag_honored": False, "enforce_scoped_roles": True}


def decide(path, method, *, legacy=False, row=None, policy=None, house=False, route_map=None):
    return OP.endpoint_decision(path=path, method=method, legacy_super_admin=legacy,
                                operator_row=row, policy=policy, house_admin=house,
                                route_map=route_map, now=NOW)


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§A  ENFORCEMENT OFF — deploying this code changes who can do what by exactly NOTHING")
# ══════════════════════════════════════════════════════════════════════════════════════════════
# The strongest form of the claim: sweep EVERY surface × EVERY caller shape × every policy that
# leaves the switch off, and assert the decision layer can never produce a denial.
off_policies = [
    None, {}, "garbage", 17, [],
    POLICY_OFF,
    {"legacy_membership_flag_honored": False},                       # post-cutover, enforcement off
    {"enforce_scoped_roles": "not-a-bool"},                          # unparseable ⇒ default False
    {"enforce_scoped_roles": None},                                  # explicit null ⇒ default False
    {"enforce_scoped_roles": 0},                                     # 0 is not a bool ⇒ default
    {"legacy_membership_flag_honored": True, "entry_min_minutes": "x"},
]
callers = [
    ("legacy super-admin", dict(legacy=True, row=None)),
    ("legacy + owner row", dict(legacy=True, row=OWNER_ROW)),
    ("registry-only support", dict(legacy=False, row=SUPPORT_ROW)),
    ("registry-only billing", dict(legacy=False, row=BILLING_ROW)),
    ("expired owner row", dict(legacy=False, row=EXPIRED_ROW)),
    ("nobody at all", dict(legacy=False, row=None)),
    ("house-admin bootstrap", dict(legacy=False, row=None, house=True)),
]
denials = enforced = 0
combos = 0
for pol in off_policies:
    for label, kw in callers:
        for m, p in SURFACES:
            combos += 1
            d = decide(p, m, policy=pol, **kw)
            if not d["allowed"]:
                denials += 1
            if d["enforced"]:
                enforced += 1
check("A1 with the switch off NO combination is ever denied (%d combinations swept)" % combos,
      denials == 0, "denials=%d" % denials)
check("A2 with the switch off the layer reports itself as NOT enforcing, always",
      enforced == 0, "enforced=%d" % enforced)
check("A3 the off-verdict carries no capability, so nothing downstream can key on one",
      all(decide(p, m, policy=None, legacy=True)["capability"] is None for m, p in SURFACES))
check("A4 the shipped DEFAULT is off", OP.POLICY_DEFAULTS["enforce_scoped_roles"] is False)
check("A5 an ABSENT policy row (pre-migration / half-applied 980) is off",
      OP.effective_policy(None)["enforce_scoped_roles"] is False)


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§B  ENFORCEMENT ON, correctly-seeded owner — the owner keeps EVERY surface")
# ══════════════════════════════════════════════════════════════════════════════════════════════
lost = [(m, p) for m, p in SURFACES if not decide(p, m, legacy=True, row=OWNER_ROW,
                                                  policy=POLICY_ON)["allowed"]]
check("B1 owner row + legacy flag reaches every mapped surface", not lost, str(lost[:3]))
lost = [(m, p) for m, p in SURFACES if not decide(p, m, legacy=False, row=OWNER_ROW,
                                                  policy=POLICY_ON_CUTOVER)["allowed"]]
check("B2 POST-CUTOVER, the seeded owner row ALONE reaches every mapped surface", not lost,
      str(lost[:3]))
check("B3 `owner` is every capability by definition — enforcement cannot bite it",
      OP.OPERATOR_ROLES["owner"] == OP.ALL_CAPABILITIES)
lost = [(m, p) for m, p in SURFACES if not decide(p, m, legacy=True, row=None,
                                                  policy=POLICY_ON)["allowed"]]
check("B4 a legacy super-admin with NO registry row keeps every surface while the flag is honored",
      not lost, str(lost[:3]))
lost = [(m, p) for m, p in SURFACES if not decide(p, m, legacy=False, row=None, house=True,
                                                  policy=POLICY_ON_CUTOVER)["allowed"]]
check("B5 the house-admin BOOTSTRAP rung survives enforcement AND the cutover together", not lost,
      str(lost[:3]))
check("B6 a capability the code does not know cannot be conjured by an override row",
      decide("/api/v1/core/ip-block", "POST", legacy=False, row=SUPPORT_ROW, policy=POLICY_ON,
             route_map=[{"method": "*", "route_prefix": "/api/v1/core/ip-block",
                         "capability": "invented.cap"}])["capability"] == OP.CAP_SECURITY)


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§C  ENFORCEMENT ON with an EXPIRED or DEACTIVATED operator row")
# ══════════════════════════════════════════════════════════════════════════════════════════════
for label, row in (("expired", EXPIRED_ROW), ("deactivated", INACTIVE_ROW),
                   ("malformed", {"operator_role": "owner"}), ("junk", "not-a-row")):
    lost = [(m, p) for m, p in SURFACES
            if not decide(p, m, legacy=True, row=row, policy=POLICY_ON)["allowed"]]
    check("C1 %s registry row + legacy flag still reaches every surface" % label, not lost,
          str(lost[:2]))
d = decide("/api/v1/core/ip-block", "POST", legacy=False, row=EXPIRED_ROW, policy=POLICY_ON_CUTOVER)
check("C2 an expired row after the cutover is correctly REFUSED (the time-box is real)",
      not d["allowed"] and d["code"] == "not_operator")
check("C3 …and the refusal says WHY, in words a human can act on",
      "not a platform operator" in (d["message"] or ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§D  ENFORCEMENT ON with the registry EMPTY")
# ══════════════════════════════════════════════════════════════════════════════════════════════
lost = [(m, p) for m, p in SURFACES
        if not decide(p, m, legacy=True, row=None, policy=POLICY_ON)["allowed"]]
check("D1 empty registry + legacy honored ⇒ the owner keeps every surface", not lost, str(lost[:2]))
check("D2 an empty registry holds zero policy.write and zero tenant.enter",
      OP.capability_holders([], OP.CAP_POLICY_WRITE) == 0
      and OP.capability_holders([], OP.CAP_TENANT_ENTER) == 0)
check("D3 …and holders are counted from ACTIVE rows only, never from bodies",
      OP.capability_holders([EXPIRED_ROW, INACTIVE_ROW, OWNER_ROW], OP.CAP_POLICY_WRITE, now=NOW) == 1)
check("D4 a support/billing registry does NOT count as a policy.write holder",
      OP.capability_holders([SUPPORT_ROW, BILLING_ROW], OP.CAP_POLICY_WRITE, now=NOW) == 0)


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§E  HALF-APPLIED MIGRATION 984 — the column is not there yet")
# ══════════════════════════════════════════════════════════════════════════════════════════════
# A policy row written by migration 980 has every 980 key and NO `enforce_scoped_roles`.
ROW_980 = {"legacy_membership_flag_honored": True, "require_entry_session": False,
           "entry_reason_required": True, "entry_min_minutes": 5, "entry_max_minutes": 60,
           "entry_default_minutes": 30, "anomaly_burst_actions": 25, "anomaly_burst_minutes": 10,
           "anomaly_fanout_tenants": 5, "anomaly_denied_streak": 5}
check("E1 a pre-984 policy row reads as NOT enforcing",
      OP.effective_policy(ROW_980)["enforce_scoped_roles"] is False)
lost = [(m, p) for m, p in SURFACES
        if decide(p, m, legacy=False, row=SUPPORT_ROW, policy=ROW_980)["enforced"]]
check("E2 …so even a narrow role is unaffected while 984 is unapplied", not lost, str(lost[:2]))
check("E3 the 980 keys still survive the 984 column being added",
      OP.effective_policy(ROW_980)["entry_max_minutes"] == 60
      and OP.effective_policy(ROW_980)["legacy_membership_flag_honored"] is True)
# The reverse half-application: 984 applied, 980's row missing entirely.
check("E4 984 applied with NO policy row at all is still off",
      OP.effective_policy(None)["enforce_scoped_roles"] is False
      and decide("/api/v1/core/ip-block", "POST", legacy=True, policy=None)["allowed"])


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§F  THE ONE LOSING STATE — and the refusal that makes it unreachable")
# ══════════════════════════════════════════════════════════════════════════════════════════════
d = decide("/api/v1/core/ip-block", "POST", legacy=True, row=None, policy=POLICY_ON_CUTOVER)
check("F1 post-cutover + empty registry IS a lockout — stated, not hidden", not d["allowed"])
r = OP.policy_change_decision(current_policy={"legacy_membership_flag_honored": False},
                              requested={"enforce_scoped_roles": True},
                              active_registry_operators=0, active_rows=[])
check("F2 …and enabling enforcement into that state is REFUSED",
      not r["allowed"] and r["code"] == "would_lock_out_policy")
r = OP.policy_change_decision(current_policy={"legacy_membership_flag_honored": False},
                              requested={"enforce_scoped_roles": True},
                              active_registry_operators=2, active_rows=[SUPPORT_ROW, BILLING_ROW])
check("F3 …refused even with TWO operators, when neither of them holds policy.write",
      not r["allowed"] and r["code"] == "would_lock_out_policy")
r = OP.policy_change_decision(current_policy={"legacy_membership_flag_honored": False},
                              requested={"enforce_scoped_roles": True},
                              active_registry_operators=1, active_rows=[OWNER_ROW])
check("F4 exactly ONE policy.write holder is allowed but WARNED, loudly",
      r["allowed"] and r["code"] == "single_point_of_failure" and "OPERATOR_ENFORCE" not in r["message"])
check("F5 …and the warning names the rollback path", "984" in r["message"])
r = OP.policy_change_decision(current_policy={}, requested={"enforce_scoped_roles": True},
                              active_registry_operators=0, active_rows=[])
check("F6 with the legacy flag still honored, enabling enforcement is never refused (the owner "
      "cannot be locked out by it)", r["allowed"] and r["code"] == "ok")
r = OP.policy_change_decision(current_policy=POLICY_ON, requested={"enforce_scoped_roles": False},
                              active_registry_operators=0, active_rows=[])
check("F7 turning enforcement OFF is NEVER refused — the way back is always open",
      r["allowed"] and r["policy"]["enforce_scoped_roles"] is False)
r = OP.policy_change_decision(current_policy=POLICY_ON_CUTOVER,
                              requested={"legacy_membership_flag_honored": True},
                              active_registry_operators=0, active_rows=[])
check("F8 re-honoring the legacy flag is never refused either",
      r["allowed"] and r["policy"]["legacy_membership_flag_honored"] is True)
r = OP.policy_change_decision(current_policy={},
                              requested={"legacy_membership_flag_honored": False,
                                         "enforce_scoped_roles": True},
                              active_registry_operators=0, active_rows=[])
check("F9 a SINGLE request that flips both switches at once is judged on the RESULT, and refused",
      not r["allowed"] and r["code"] == "would_lock_out")
r = OP.policy_change_decision(current_policy={},
                              requested={"legacy_membership_flag_honored": False,
                                         "enforce_scoped_roles": True},
                              active_registry_operators=1, active_rows=[OWNER_ROW])
check("F10 …and permitted when an owner row would survive it",
      r["allowed"] and r["policy"]["enforce_scoped_roles"] is True)
check("F11 the 980 cutover refusal is untouched by 984",
      not OP.policy_change_decision(current_policy={},
                                    requested={"legacy_membership_flag_honored": False},
                                    active_registry_operators=0)["allowed"])


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§G  ESCAPE HATCHES — the surfaces enforcement must never be able to close")
# ══════════════════════════════════════════════════════════════════════════════════════════════
HATCHES = [
    ("POST", "/api/v1/core/operator/policy", "the control that turns enforcement back OFF"),
    ("GET", "/api/v1/core/operator/me", "the console's own identity read"),
    ("GET", "/api/v1/core/operator/enforcement", "the owner preview"),
    ("POST", "/api/v1/core/operator/enter", "opening an entry session"),
    ("GET", "/api/v1/core/me", "who am I"),
    ("GET", "/api/v1/core/my-tenants", "which tenants may I act as"),
    ("POST", "/api/v1/core/bootstrap", "session bootstrap"),
]
for m, p, why in HATCHES:
    ok = OP.endpoint_capability(p, m) is None and OP.is_enforcement_exempt(p)
    check("G1 %s is structurally exempt (%s)" % (p, why), ok)
    # …and exempt for EVERY caller shape, including one with no capabilities at all.
    check("G2 %s answers for a caller with an empty role, under enforcement" % p,
          decide(p, m, legacy=False, row=dict(SUPPORT_ROW, operator_role="typo"),
                 policy=POLICY_ON)["allowed"])
check("G3 impersonation is exempt: `impersonate` must stay default-deny under its OWN gate",
      OP.is_enforcement_exempt("/api/v1/impersonation/start")
      and OP.endpoint_capability("/api/v1/impersonation/start", "POST") is None)
check("G4 no operator capability is named `impersonate`, nor implies it",
      not any("impersonate" in c for c in OP.ALL_CAPABILITIES))
check("G5 an entry session still grants ONLY the acting-org switch",
      tuple(OP.ENTRY_GRANTS) == ("acting_org",))
check("G6 no route in the house map points at the impersonation prefix",
      not any(p.startswith("/api/v1/impersonation") for _m, p, _c in OP.ROUTE_CAPABILITIES))
check("G7 a config override CANNOT gate an exempt prefix",
      decide("/api/v1/core/operator/policy", "POST", legacy=False, row=SUPPORT_ROW,
             policy=POLICY_ON,
             route_map=[("*", "/api/v1/core/operator", OP.CAP_POLICY_WRITE)])["allowed"])
check("G8 …not even the impersonation prefix",
      decide("/api/v1/impersonation/start", "POST", legacy=False, row=SUPPORT_ROW, policy=POLICY_ON,
             route_map=[("*", "/api/v1/impersonation", OP.CAP_TENANT_ENTER)])["allowed"])
check("G9 an UNKNOWN route is not gated — narrowing is only ever explicit",
      decide("/api/v1/hr/employees", "GET", legacy=False, row=SUPPORT_ROW,
             policy=POLICY_ON)["code"] == "unmapped")
check("G10 an EMPTY path (the middleware never saw the request) is not gated",
      OP.endpoint_capability("", "GET") is None and OP.endpoint_capability(None, "GET") is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§H  IT ACTUALLY BITES — otherwise none of this was worth shipping")
# ══════════════════════════════════════════════════════════════════════════════════════════════
BITES = [
    ("support", SUPPORT_ROW, "POST", "/api/v1/core/ip-block", OP.CAP_SECURITY),
    ("support", SUPPORT_ROW, "POST", "/api/v1/core/super-admins", OP.CAP_OPERATOR_WRITE),
    ("support", SUPPORT_ROW, "GET", "/api/v1/billing/plans", OP.CAP_BILLING_READ),
    ("support", SUPPORT_ROW, "POST", "/api/v1/core/tenants", OP.CAP_TENANT_LIFECYCLE),
    ("billing", BILLING_ROW, "POST", "/api/v1/core/sessions/revoke", OP.CAP_SECURITY),
    ("billing", BILLING_ROW, "POST", "/api/v1/core/control-box/run", OP.CAP_PLATFORM_REPAIR),
    ("readonly", dict(OWNER_ROW, operator_role="readonly"), "PUT", "/api/v1/core/auth-config",
     OP.CAP_SECURITY),
    ("engineering", dict(OWNER_ROW, operator_role="engineering"), "PUT",
     "/api/v1/billing/module-pricing", OP.CAP_BILLING_WRITE),
]
for role, row, m, p, cap in BITES:
    d = decide(p, m, legacy=False, row=row, policy=POLICY_ON)
    check("H1 `%s` is refused %s %s (needs %s)" % (role, m, p, cap),
          not d["allowed"] and d["code"] == "missing_capability" and d["capability"] == cap)
    check("H2 …and the refusal names the role and the missing capability",
          role in (d["message"] or "") and cap in (d["message"] or ""))
d = decide("/api/v1/core/tenants", "GET", legacy=False, row=SUPPORT_ROW, policy=POLICY_ON)
check("H3 `support` CAN still read the tenant directory (reading ≠ changing)",
      d["allowed"] and d["capability"] == OP.CAP_TENANT_READ)
d = decide("/api/v1/core/access-log", "GET", legacy=False, row=SUPPORT_ROW, policy=POLICY_ON)
check("H4 `support` can still read the access log", d["allowed"])
d = decide("/api/v1/billing/plans", "GET", legacy=False, row=BILLING_ROW, policy=POLICY_ON)
check("H5 `billing` CAN read billing", d["allowed"] and d["capability"] == OP.CAP_BILLING_READ)
check("H6 a per-row capability GRANT widens exactly one operator, and no one else",
      decide("/api/v1/core/ip-block", "POST", legacy=False,
             row=dict(SUPPORT_ROW, capabilities={OP.CAP_SECURITY: True}),
             policy=POLICY_ON)["allowed"]
      and not decide("/api/v1/core/ip-block", "POST", legacy=False, row=SUPPORT_ROW,
                     policy=POLICY_ON)["allowed"])
check("H7 a per-row DENY narrows an owner (deny wins, as it does for settings areas)",
      not decide("/api/v1/core/ip-block", "POST", legacy=False,
                 row=dict(OWNER_ROW, capabilities={OP.CAP_SECURITY: False}),
                 policy=POLICY_ON)["allowed"])
check("H8 …but a per-row deny CANNOT be used to lock the platform's last owner out of the console",
      OP.is_enforcement_exempt("/api/v1/core/operator/policy"))

# The owner preview is what makes turning this on an informed act rather than a leap.
prev = OP.enforcement_preview([OWNER_ROW, SUPPORT_ROW, BILLING_ROW, EXPIRED_ROW],
                              policy=POLICY_OFF, now=NOW)
by_email = {o["email"]: o for o in prev["operators"]}
check("H9 the preview marks the owner as full-reach and losing nothing",
      by_email["op-a@example.test"]["full_reach"] and not by_email["op-a@example.test"]["would_lose"])
check("H10 the preview lists exactly what `support` would lose",
      "/api/v1/core/ip-block" in by_email["op-b@example.test"]["would_lose"]
      and "/api/v1/core/tenants" in by_email["op-b@example.test"]["would_keep"])
check("H11 an EXPIRED owner row previews as inactive, with no capabilities left",
      by_email["op-d@example.test"]["active"] is False
      and by_email["op-d@example.test"]["capabilities"] == []
      and by_email["op-d@example.test"]["full_reach"] is False)
check("H12 the preview counts policy.write holders — the number the refusal keys on",
      prev["policy_write_holders"] == 1)
check("H13 the preview publishes the exempt list, so the escape hatch is visible in the UI",
      "/api/v1/core/operator" in prev["exempt_prefixes"])


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§I  ROUTE RESOLUTION — longest prefix, verb specificity, segment safety, bad config")
# ══════════════════════════════════════════════════════════════════════════════════════════════
check("I1 GET /core/tenants is a READ, POST is a LIFECYCLE act",
      OP.endpoint_capability("/api/v1/core/tenants", "GET") == OP.CAP_TENANT_READ
      and OP.endpoint_capability("/api/v1/core/tenants", "POST") == OP.CAP_TENANT_LIFECYCLE)
check("I2 a child path inherits its parent's mapping",
      OP.endpoint_capability("/api/v1/core/tenants/abc/sync", "POST") == OP.CAP_TENANT_LIFECYCLE)
check("I3 a trailing slash resolves the same",
      OP.endpoint_capability("/api/v1/core/tenants/", "GET") == OP.CAP_TENANT_READ)
check("I4 a query string is ignored",
      OP.endpoint_capability("/api/v1/core/access-log?limit=5", "GET") == OP.CAP_AUDIT_READ)
check("I5 prefix matching is SEGMENT-aware — /billing never swallows /billing-export",
      OP.endpoint_capability("/api/v1/billing-export/run", "POST") is None)
check("I6 …nor /core/tenants /core/tenants-archive",
      OP.endpoint_capability("/api/v1/core/tenants-archive", "GET") is None)
check("I7 a longer prefix outranks a shorter one",
      OP.endpoint_capability("/api/v1/core/ip-block/remove", "POST") == OP.CAP_SECURITY)
check("I8 a relative or malformed path resolves to nothing (never to a capability)",
      all(OP.endpoint_capability(x, "GET") is None
          for x in ("api/v1/core/tenants", "", None, 42, "   ", "://x")))
BAD_CONFIG = [None, "row", 7, (), ("GET",), {"route_prefix": "/api/v1/core/tenants"},
              {"route_prefix": "/api/v1/core/tenants", "capability": "nope"},
              {"method": "GET", "route_prefix": "", "capability": OP.CAP_SECURITY},
              ("GET", "/api/v1/core/tenants", None)]
check("I9 every malformed override row is ignored, and none of them changes the answer",
      OP.endpoint_capability("/api/v1/core/tenants", "GET", route_map=BAD_CONFIG)
      == OP.CAP_TENANT_READ)
check("I10 a VALID override row re-points a surface without a deploy",
      OP.endpoint_capability("/api/v1/core/access-log", "GET",
                             route_map=[("GET", "/api/v1/core/access-log", OP.CAP_SECURITY)])
      == OP.CAP_SECURITY)
check("I11 a config row can gate a surface the house map does not know",
      OP.endpoint_capability("/api/v1/payables/invoices", "POST",
                             route_map=[("*", "/api/v1/payables", OP.CAP_BILLING_WRITE)])
      == OP.CAP_BILLING_WRITE)
check("I12 every capability in the house map is a real capability",
      all(c in OP.ALL_CAPABILITIES for _m, _p, c in OP.ROUTE_CAPABILITIES))
check("I13 every prefix in the house map is an absolute /api/v1 path",
      all(p.startswith("/api/v1/") for _m, p, _c in OP.ROUTE_CAPABILITIES))
check("I14 `platform.repair` exists and belongs to owner + engineering only",
      sorted(r for r, c in OP.OPERATOR_ROLES.items() if OP.CAP_PLATFORM_REPAIR in c)
      == ["engineering", "owner"])


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§J  RULE TWO + THE WIRING — one gate, still with its unchanged branch")
# ══════════════════════════════════════════════════════════════════════════════════════════════
SRC = {name: open(os.path.join(HERE, name)).read() for name in (
    "app/modules/core/router.py", "app/modules/core/operator_api.py",
    "app/modules/core/operator.py", "app/core/tenant_middleware.py")}
MIG = open(os.path.join(HERE, "..", "database", "migrations",
                        "984_operator_scope_enforcement.sql")).read()

gate = SRC["app/modules/core/router.py"]
m = re.search(r"def _require_super_admin\(.*?\n(?=\n@|\ndef )", gate, re.S)
body = m.group(0) if m else ""
check("J1 `_require_super_admin` still exists and is still the gate", bool(body))
check("J2 …it consults the layer rather than re-deciding", "scoped_role_verdict" in body)
check("J3 …and keeps its UNCHANGED pre-984 branch when the layer stands down",
      "if verdict is None:" in body and 'raise HTTPException(403, "super-admin only")' in body)
check("J4 …the layer can never be the reason the gate breaks (it is wrapped)",
      "except Exception:" in body and "verdict = None" in body)
check("J5 there is still exactly ONE definition of the super-admin gate",
      len(re.findall(r"^def _require_super_admin\(", gate, re.M)) == 1)

api = SRC["app/modules/core/operator_api.py"]
check("J6 the I/O layer has an environment kill switch that needs no database",
      "OPERATOR_ENFORCE" in api and "_enforce_env_on" in api)
check("J7 …and returns None (= today) on ANY failure",
      re.search(r"def scoped_role_verdict.*?except Exception:\s*\n\s*return None", api, re.S)
      is not None)
check("J8 …and stands down when the middleware never saw the route",
      "if not path:" in api and "return None" in api)
check("J9 the policy write tolerates a half-applied 984 (column absent ⇒ retry without it)",
      "_POLICY_COLUMNS_984" in api and "_persist_policy" in api)
check("J10 the preview endpoint is READ-gated, not write-gated",
      "def enforcement_state" in api and "CAP_OPERATOR_READ" in api)

mw = SRC["app/core/tenant_middleware.py"]
check("J11 the route contextvar is published before any early return, for every http request",
      re.search(r'if scope\.get\("type"\) != "http":\s*\n\s*return await self\.app.*?'
                r'_set_request_route\(', mw, re.S) is not None)
check("J12 …and `current_route` degrades to (None, \"\") outside a request",
      'return v if v else (None, "")' in mw)

# RULE TWO — no person, tenant or org literal anywhere in the authorization path. Comments and
# docstrings are documentation (the owner's directive is quoted verbatim in one); what must be clean
# is the EXECUTABLE text, so the source is tokenized and every COMMENT and STRING token dropped.
FORBIDDEN = ("sanjot", "cellfonz", "00000000-0000-0000-0000-000000000001",
             "854f6d7b-6590-4e4d-88ab-646f560d4f4c", "luxelink")


def executable_text(src):
    import io
    import tokenize
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out).lower()


for name in ("app/modules/core/operator.py", "app/modules/core/operator_api.py",
             "app/modules/core/router.py", "app/core/tenant_middleware.py"):
    hits = [t for t in FORBIDDEN if t in executable_text(SRC[name])]
    check("J13 RULE TWO: no person/tenant/org literal in %s's executable path" % name, not hits,
          str(hits))
mig_low = MIG.lower()
mig_code = "\n".join(l for l in mig_low.split("\n") if not l.lstrip().startswith("--"))
check("J14 RULE TWO: migration 984 names no tenant, person or org id",
      not any(t for t in FORBIDDEN if t in mig_code))
check("J15 the access-narrowing seed ships COMMENTED OUT",
      "-- UPDATE core.platform_operator_policy SET enforce_scoped_roles = TRUE" in MIG
      and not re.search(r"^\s*UPDATE core\.platform_operator_policy SET enforce_scoped_roles = TRUE",
                        MIG, re.M))
check("J16 the new column defaults FALSE",
      "enforce_scoped_roles BOOLEAN NOT NULL DEFAULT FALSE" in MIG)
check("J17 the migration carries a -- REVERT: note", "-- REVERT:" in MIG)
check("J18 the migration is additive: it touches storeops.app_users not at all",
      "app_users" not in mig_code)
check("J19 the override table has NO org_id column — platform authority belongs to no tenant",
      re.search(r"CREATE TABLE IF NOT EXISTS core\.operator_route_capability.*?\);", MIG, re.S)
      is not None
      and "org_id" not in re.search(r"CREATE TABLE IF NOT EXISTS core\.operator_route_capability.*?\);",
                                    MIG, re.S).group(0))
check("J20 the override table is locked down like every other 980 table",
      "ENABLE ROW LEVEL SECURITY" in MIG and "REVOKE ALL ON core.operator_route_capability FROM anon"
      in MIG)


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 96)
print("  %d passed, %d failed" % (PASS, FAIL))
print("═" * 96)
sys.exit(1 if FAIL else 0)
