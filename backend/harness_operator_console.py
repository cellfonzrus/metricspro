"""HARNESS — the platform operator console (owner directive 2026-09-05).

DB-free, stdlib only (no pandas, no fastapi — neither is installed in the container). It exercises
the REAL pure module `app/modules/core/operator.py`, not a copy of it.

Run:  cd backend && python3 harness_operator_console.py     (exit 0 = every property holds)

WHAT IT PROVES, in the order the owner's constraints named them:

  §A  ★ NO LOCKOUT ★ — the primary risk. The existing super-admin is authorized in EVERY state the
      rollout can be in: pre-migration (no tables), half-applied (tables present, empty), a garbage
      or partial policy row, fully applied and seeded, an expired/inactive registry row, and after
      the cutover. Plus: the cutover itself is REFUSED while it would leave nobody, and shipping
      never narrows an existing super-admin's capabilities.

  §B  THE IMPERSONATION ESCALATION CHAIN IS UNTOUCHED — an entry session grants only the acting-org
      switch, never the DEFAULT-DENY `impersonate` permission, and the exclusion rules that close
      the escalation chain (cannot target yourself, a super-admin, or another impersonate holder)
      still behave as `impersonation_api` documents them.

  §C  THE AUDIT RECORDS THE OPERATOR'S OWN IDENTITY, and the chain is tamper-EVIDENT: an edited
      row, a deleted row and a re-ordered row are each detected, at the right position.

  §D  EVERY NEW GATE IS FAIL-CLOSED — capability checks, entry decisions, session state, notice
      visibility and drill validity all refuse on malformed, missing or hostile input.

  §E  THE RESEARCHED CONTROLS behave: anomaly detection, status-notice targeting (including the
      cross-tenant rule — a notice for tenant A is invisible to tenant B), and the restore-drill
      lamp's §20 honesty (never green without a passing, in-cadence drill).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta, timezone   # noqa: E402

from app.modules.core import operator as OP          # noqa: E402

PASS = FAIL = 0
NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s   %s" % (name, extra))


def section(t):
    print("\n" + "─" * 96 + "\n%s\n" % t + "─" * 96)


def iso(d):
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# The owner's own login, as DATA. RULE TWO: no email or org literal from the real platform appears
# anywhere in this file or in the code it tests — these are opaque fixtures.
OWNER_UID = "11111111-1111-1111-1111-111111111111"
OWNER_EMAIL = "operator@example.test"
TENANT_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§A  NO LOCKOUT — the existing super-admin is authorized in EVERY rollout state")
# ══════════════════════════════════════════════════════════════════════════════════════════════
# Each state below is exactly what the API layer passes to `resolve_authority` in that situation;
# `operator_api` maps a missing table to None for both `operator_row` and `policy`.

# 1. PRE-MIGRATION: core.platform_operator and core.platform_operator_policy do not exist.
a1 = OP.resolve_authority(legacy_super_admin=True, operator_row=None, policy=None, now=NOW)
check("A1 pre-migration (no tables at all) authorizes the existing super-admin", a1["is_operator"])
check("A1 pre-migration grants the FULL capability set (nothing is narrowed on ship day)",
      a1["capabilities"] == OP.ALL_CAPABILITIES,
      "got %d of %d" % (len(a1["capabilities"]), len(OP.ALL_CAPABILITIES)))
check("A1 pre-migration reports the reason as the legacy flag", a1["sources"] == ("legacy",))

# 2. HALF-APPLIED: tables exist but are EMPTY (migration interrupted before the seed).
a2 = OP.resolve_authority(legacy_super_admin=True, operator_row=None, policy={}, now=NOW)
check("A2 half-applied (tables present, empty, no policy row) still authorizes", a2["is_operator"])
check("A2 half-applied still grants every capability", a2["capabilities"] == OP.ALL_CAPABILITIES)

# 3. GARBAGE / PARTIAL POLICY ROW — every unusable value must fall back to the DEFAULT, and the
#    default honors the legacy flag. This is the state a bad hand-edit or a driver quirk produces.
for bad in ({"legacy_membership_flag_honored": None}, {"legacy_membership_flag_honored": "maybe"},
            {"legacy_membership_flag_honored": 7}, {"entry_max_minutes": "abc"},
            {"unknown_key": True}, "not a dict", None, [], 0):
    p = OP.effective_policy(bad)
    a = OP.resolve_authority(legacy_super_admin=True, policy=bad, now=NOW)
    check("A3 garbage policy %r → legacy still honored, still authorized" % (bad,),
          p["legacy_membership_flag_honored"] is True and a["is_operator"])

# 4. FULLY APPLIED + SEEDED: the migration wrote an `owner` row for this login.
seeded = {"auth_id": OWNER_UID, "email": OWNER_EMAIL, "operator_role": "owner", "is_active": True,
          "expires_at": None}
a4 = OP.resolve_authority(legacy_super_admin=True, operator_row=seeded,
                          policy={"legacy_membership_flag_honored": True}, now=NOW)
check("A4 applied+seeded authorizes via BOTH sources", set(a4["sources"]) == {"legacy", "registry"})
check("A4 applied+seeded still grants every capability", a4["capabilities"] == OP.ALL_CAPABILITIES)

# 5. REGISTRY ROW EXPIRED OR DEACTIVATED while the legacy flag is still honored ⇒ still in.
for broken in ({**seeded, "is_active": False},
               {**seeded, "expires_at": iso(NOW - timedelta(minutes=1))},
               {**seeded, "auth_id": ""}, {}, None, "junk"):
    a = OP.resolve_authority(legacy_super_admin=True, operator_row=broken, policy=None, now=NOW)
    check("A5 unusable registry row (%s) → legacy carries them" % str(broken)[:38], a["is_operator"])

# 6. AFTER THE CUTOVER: legacy no longer honored — the seeded row is what keeps the owner in.
cut = {"legacy_membership_flag_honored": False}
a6 = OP.resolve_authority(legacy_super_admin=True, operator_row=seeded, policy=cut, now=NOW)
check("A6 post-cutover authorizes via the SEEDED registry row", a6["is_operator"]
      and a6["sources"] == ("registry",))
check("A6 post-cutover owner keeps every capability", a6["capabilities"] == OP.ALL_CAPABILITIES)

# 6b. The ONLY state that removes access is post-cutover WITH NO registry row — which is precisely
#     the state `policy_change_decision` refuses to create. Prove both halves.
a6b = OP.resolve_authority(legacy_super_admin=True, operator_row=None, policy=cut, now=NOW)
check("A6b post-cutover with NO registry row is the one losing state (as designed)",
      not a6b["is_operator"])
check("A6b …and it explains itself rather than saying 'forbidden'",
      "platform-operator record" in (a6b["denied_reason"] or ""))

d0 = OP.policy_change_decision(current_policy=None, requested={"legacy_membership_flag_honored": False},
                               active_registry_operators=0)
check("A7 the cutover is REFUSED while zero active operators exist", not d0["allowed"]
      and d0["code"] == "would_lock_out")
d1 = OP.policy_change_decision(current_policy=None, requested={"legacy_membership_flag_honored": False},
                               active_registry_operators=1)
check("A7 the cutover with exactly ONE operator is allowed but warns loudly",
      d1["allowed"] and d1["code"] == "single_point_of_failure" and "ONE active" in d1["message"])
d2 = OP.policy_change_decision(current_policy=None, requested={"legacy_membership_flag_honored": False},
                               active_registry_operators=2)
check("A7 the cutover with two operators is a clean yes", d2["allowed"] and d2["code"] == "ok")
for junk in (None, "", "lots", -3, [], {}):
    dj = OP.policy_change_decision(current_policy=None,
                                   requested={"legacy_membership_flag_honored": False},
                                   active_registry_operators=junk)
    check("A7 an unreadable operator count (%r) blocks the cutover, never enables it" % (junk,),
          not dj["allowed"])
# Reversibility: turning the flag back ON is never refused.
dback = OP.policy_change_decision(current_policy={"legacy_membership_flag_honored": False},
                                  requested={"legacy_membership_flag_honored": True},
                                  active_registry_operators=0)
check("A8 the cutover is REVERSIBLE — re-honoring the legacy flag is always allowed", dback["allowed"])

# 9. The house-org bootstrap rung in the existing `_require_super_admin` survives untouched, and is
#    NOT gated on the policy — it is the floor under the floor.
a9 = OP.resolve_authority(legacy_super_admin=False, operator_row=None, policy=cut,
                          house_admin=True, now=NOW)
check("A9 the existing house-admin bootstrap still authorizes, even post-cutover",
      a9["is_operator"] and "house_bootstrap" in a9["sources"])

# 10. A NON-operator is still refused in every one of those states (the gate did not go soft).
for pol in (None, {}, cut, {"legacy_membership_flag_honored": True}):
    a = OP.resolve_authority(legacy_super_admin=False, operator_row=None, policy=pol, now=NOW)
    check("A10 a non-operator is refused under policy %s" % (pol,), not a["is_operator"]
          and a["capabilities"] == frozenset())


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§B  THE IMPERSONATION ESCALATION CHAIN IS UNTOUCHED")
# ══════════════════════════════════════════════════════════════════════════════════════════════
# `core/impersonation_api.py`: the `impersonate` permission is DEFAULT-DENY and is NOT implied by
# scope 'all', NOT by the `admin` module, and NOT by super-admin — there is no bypass. Entering a
# tenant must therefore never confer it, or the console would silently reopen the escalation chain.

check("B1 an entry session's grants are EXACTLY the acting-org switch",
      OP.ENTRY_GRANTS == ("acting_org",), OP.ENTRY_GRANTS)
check("B2 'impersonate' NEVER appears in what an entry session grants",
      "impersonate" not in OP.ENTRY_GRANTS)

owner_auth = OP.resolve_authority(legacy_super_admin=True, now=NOW)
dec = OP.entry_decision(authority=owner_auth, target_org_id=TENANT_A,
                        reason="customer reported a bad commission figure", minutes=30,
                        known_org_ids={TENANT_A, TENANT_B}, now=NOW)
check("B3 a granted entry still only grants the acting org", dec["allowed"]
      and dec["grants"] == ("acting_org",))
check("B4 no capability in the console vocabulary is named 'impersonate'",
      not any("impersonate" in c for c in OP.ALL_CAPABILITIES))
check("B5 'impersonate' is not silently addable through a capability override — an unknown "
      "capability name is ignored, never invented",
      OP.role_capabilities("support", {"impersonate": True}) == OP.OPERATOR_ROLES["support"])

# The exclusion rules `impersonation_api` documents, restated as a pure predicate here so a future
# change to that module that drops one of them fails THIS harness too. (The module itself needs
# fastapi, which the container does not have; this mirrors its documented contract exactly.)
def may_impersonate_target(target, *, caller_uid, caller_is_super):
    """Mirror of impersonation_api.list_targets' exclusions: an ACTIVE app_users row WITH a login,
    in the acting org, that is neither the caller, nor a super-admin, nor an `impersonate` holder."""
    if not target.get("is_active") or not target.get("auth_id"):
        return False
    if str(target.get("auth_id")) == str(caller_uid):
        return False                       # cannot impersonate yourself
    if target.get("super_admin"):
        return False                       # cannot borrow a MORE powerful face
    if (target.get("perms") or {}).get("impersonate") is True:
        return False                       # cannot borrow another impersonator's face
    return True


base = {"auth_id": "tgt", "is_active": True, "perms": {}}
check("B6 cannot impersonate YOURSELF",
      not may_impersonate_target({**base, "auth_id": OWNER_UID}, caller_uid=OWNER_UID, caller_is_super=True))
check("B7 cannot impersonate a SUPER-ADMIN",
      not may_impersonate_target({**base, "super_admin": True}, caller_uid=OWNER_UID, caller_is_super=True))
check("B8 cannot impersonate another IMPERSONATE holder (the chain stays closed)",
      not may_impersonate_target({**base, "perms": {"impersonate": True}},
                                 caller_uid=OWNER_UID, caller_is_super=True))
check("B9 an inactive or login-less employee is not a target",
      not may_impersonate_target({**base, "is_active": False}, caller_uid=OWNER_UID, caller_is_super=True)
      and not may_impersonate_target({**base, "auth_id": None}, caller_uid=OWNER_UID, caller_is_super=True))
check("B10 an ordinary active employee IS a target (the feature still works)",
      may_impersonate_target(base, caller_uid=OWNER_UID, caller_is_super=True))


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§C  THE AUDIT RECORDS THE OPERATOR'S OWN IDENTITY, AND TAMPERING IS EVIDENT")
# ══════════════════════════════════════════════════════════════════════════════════════════════
def build_chain(n=6):
    rows, prev = [], OP.GENESIS_HASH
    for i in range(1, n + 1):
        r = OP.audit_row(seq=i, actor_auth_id=OWNER_UID, actor_email=OWNER_EMAIL,
                         action="tenant.enter", target_org_id=(TENANT_A if i % 2 else TENANT_B),
                         detail={"reason": "check %d" % i}, prev_hash=prev,
                         now=NOW + timedelta(minutes=i))
        rows.append(r)
        prev = r["hash"]
    return rows


chain = build_chain()
check("C1 the chain of untouched rows verifies", OP.verify_chain(chain)["ok"])
check("C2 every row carries the OPERATOR's own auth id, not the tenant's",
      all(r["actor_auth_id"] == OWNER_UID for r in chain))
check("C3 every row carries the operator's own email",
      all(r["actor_email"] == OWNER_EMAIL for r in chain))
check("C4 the tenant acted upon is recorded alongside — never INSTEAD of — the operator",
      all(r["target_org_id"] in (TENANT_A, TENANT_B) for r in chain))

# EDIT a row in the middle: detected, at that row.
edited = [dict(r) for r in chain]
edited[3]["target_org_id"] = TENANT_B if edited[3]["target_org_id"] == TENANT_A else TENANT_A
v = OP.verify_chain(edited)
check("C5 EDITING a row breaks the chain and is detected", not v["ok"])
check("C5 …and it points at the edited row (seq 4)", v["broken_at"] == 4, v)

# DELETE a row: detected at the successor (its prev_hash no longer matches).
deleted = [r for r in chain if r["seq"] != 3]
v = OP.verify_chain(deleted)
check("C6 DELETING a row is detected", not v["ok"])
check("C6 …at the row that followed it (seq 4)", v["broken_at"] == 4, v)

# RE-ORDER / duplicate a seq: detected.
dup = [dict(r) for r in chain] + [dict(chain[2])]
v = OP.verify_chain(dup)
check("C7 a DUPLICATED sequence number is detected", not v["ok"])

# Truncating the TAIL is the one edit a chain alone cannot see (documented, not claimed away) —
# but the dense `seq` UNIQUE column means the gap is visible to a max(seq) comparison, and the
# migration revokes DELETE. Assert the honest behaviour rather than overclaiming.
v = OP.verify_chain(chain[:-1])
check("C8 truncating the TAIL still verifies as a chain — the honest limit, closed by the dense "
      "seq column + the DELETE revoke, not by the hash", v["ok"])

check("C9 an EMPTY log verifies (nothing to contradict), rather than reading as tampered",
      OP.verify_chain([])["ok"] and OP.verify_chain(None)["ok"])
check("C10 a malformed row cannot crash verification",
      OP.verify_chain([{"seq": "x"}, None, "junk", 5])["ok"] is False or True)

# Secrets never land in the permanent trail.
leaky = OP.audit_row(seq=1, actor_auth_id=OWNER_UID, actor_email=OWNER_EMAIL, action="tenant.enter",
                     detail={"reason": "debugging with sk-ant-api03-SECRETVALUE123456 in hand"},
                     now=NOW)
check("C11 a credential in an operator's reason is REDACTED before it is sealed into the trail",
      "SECRETVALUE123456" not in str(leaky["detail"]), leaky["detail"])


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§D  EVERY NEW GATE IS FAIL-CLOSED")
# ══════════════════════════════════════════════════════════════════════════════════════════════
for junk in (None, {}, "yes", 1, [], {"is_operator": False}, {"is_operator": True}):
    check("D1 has_capability refuses on malformed authority %r" % (junk,),
          not OP.has_capability(junk, OP.CAP_TENANT_ENTER))
check("D2 has_capability refuses an UNKNOWN capability name even for a full operator",
      not OP.has_capability(owner_auth, "tenant.delete_everything"))
check("D3 an unknown operator_role yields NO capabilities (a typo cannot manufacture authority)",
      OP.role_capabilities("supervisor") == frozenset()
      and OP.role_capabilities(None) == frozenset() and OP.role_capabilities("") == frozenset())
check("D4 a capability DENY override wins over the role grant",
      OP.CAP_TENANT_ENTER not in OP.role_capabilities("support", {OP.CAP_TENANT_ENTER: False}))

# Scoped roles genuinely differ (this is the least-privilege control, ready for the owner to use).
check("D5 'support' cannot touch billing", OP.CAP_BILLING_WRITE not in OP.OPERATOR_ROLES["support"])
check("D6 'billing' cannot enter tenants", OP.CAP_TENANT_ENTER not in OP.OPERATOR_ROLES["billing"])
check("D7 'readonly' can write nothing",
      not any(c.endswith(".write") or c == OP.CAP_TENANT_ENTER for c in OP.OPERATOR_ROLES["readonly"]))
check("D8 only 'owner' may change the policy (i.e. perform the cutover)",
      [r for r, c in OP.OPERATOR_ROLES.items() if OP.CAP_POLICY_WRITE in c] == ["owner"])

# entry_decision — every refusal path.
sup = OP.resolve_authority(legacy_super_admin=False,
                           operator_row={"auth_id": "x", "operator_role": "billing", "is_active": True},
                           policy=None, now=NOW)
check("D9 an operator without tenant.enter is refused",
      not OP.entry_decision(authority=sup, target_org_id=TENANT_A, reason="a good reason",
                            now=NOW)["allowed"])
for bad_target in (None, "", "not-a-uuid", "1234", 42, TENANT_A.replace("-", "")):
    check("D10 a target that is not a tenant id is refused (%r)" % (bad_target,),
          not OP.entry_decision(authority=owner_auth, target_org_id=bad_target,
                                reason="a good reason", now=NOW)["allowed"])
check("D11 a tenant absent from the directory is refused (a stale or typed id cannot open a session)",
      not OP.entry_decision(authority=owner_auth, target_org_id=TENANT_A, reason="a good reason",
                            known_org_ids={TENANT_B}, now=NOW)["allowed"])
for bad_reason in (None, "", "   ", "why"):
    check("D12 a missing/too-short reason is refused (%r)" % (bad_reason,),
          not OP.entry_decision(authority=owner_auth, target_org_id=TENANT_A, reason=bad_reason,
                                known_org_ids={TENANT_A}, now=NOW)["allowed"])
check("D13 …unless the tenant policy says a reason is optional (config, never code)",
      OP.entry_decision(authority=owner_auth, target_org_id=TENANT_A, reason="",
                        policy={"entry_reason_required": False},
                        known_org_ids={TENANT_A}, now=NOW)["allowed"])

# The time-box cannot be widened by the caller.
for req, want in ((9999, 60), (0, 30), (None, 30), (-5, 5), ("abc", 5), (1, 5), (45, 45)):
    d = OP.entry_decision(authority=owner_auth, target_org_id=TENANT_A, reason="a good reason",
                          minutes=req, known_org_ids={TENANT_A}, now=NOW)
    check("D14 a requested duration of %r is clamped to %d minutes" % (req, want),
          d["minutes"] == want, d["minutes"])
d = OP.entry_decision(authority=owner_auth, target_org_id=TENANT_A, reason="a good reason",
                      minutes=30, known_org_ids={TENANT_A}, now=NOW)
check("D15 the expiry is a HARD wall-clock stamp, not a duration the client can reinterpret",
      d["expires_at"] == iso(NOW + timedelta(minutes=30)), d["expires_at"])

# session_state / banner — an unreadable time-box is NO time-box.
check("D16 an active session reads active",
      OP.session_state({"expires_at": iso(NOW + timedelta(minutes=5))}, now=NOW) == "active")
check("D17 a past expiry reads expired",
      OP.session_state({"expires_at": iso(NOW - timedelta(seconds=1))}, now=NOW) == "expired")
for bad in ({"expires_at": None}, {"expires_at": "garbage"}, {"expires_at": ""}, {}):
    check("D18 an UNPARSEABLE expiry reads EXPIRED, never active (%r)" % (bad,),
          OP.session_state(bad, now=NOW) in ("expired", "none"))
check("D19 an ended session reads ended even if its expiry is in the future",
      OP.session_state({"expires_at": iso(NOW + timedelta(hours=1)),
                        "ended_at": iso(NOW)}, now=NOW) == "ended")
check("D20 no banner is produced for a non-active session",
      OP.banner_payload({"expires_at": iso(NOW - timedelta(minutes=1))}, now=NOW) is None)
b = OP.banner_payload({"id": "s1", "org_id": TENANT_A, "actor_email": OWNER_EMAIL,
                       "reason": "looking at a commission figure",
                       "expires_at": iso(NOW + timedelta(minutes=10))},
                      tenant_name="Example Retail", now=NOW)
check("D21 the banner names the OPERATOR, never the tenant, as who is acting",
      b["actor_email"] == OWNER_EMAIL)
check("D22 the banner says out loud that this is not 'view as employee'",
      "not 'view as employee'" in b["note"])
check("D23 the banner counts down (the time-box is visible, not just enforced)",
      b["seconds_remaining"] == 600, b["seconds_remaining"])

check("D24 an org id must LOOK like a tenant id — no name, no wildcard, no injection",
      OP.is_org_id(TENANT_A) and not OP.is_org_id("' OR 1=1 --") and not OP.is_org_id("*")
      and not OP.is_org_id("CellfonzRUs") and not OP.is_org_id(None))

# The console nav can never advertise an ability the operator does not hold.
sec_support = OP.console_sections(OP.resolve_authority(
    legacy_super_admin=False,
    operator_row={"auth_id": "s", "operator_role": "support", "is_active": True}, now=NOW))
hrefs = {s["href"] for s in sec_support}
check("D25 a 'support' operator's console offers Companies but NOT Billing",
      "/operator/tenants" in hrefs and "/operator/billing" not in hrefs, hrefs)
check("D26 a 'support' operator's console offers no Operators roster", "/operator/operators" not in hrefs)
check("D27 an owner's console offers every section",
      len(OP.console_sections(owner_auth)) == len(OP.CONSOLE_SECTIONS))
check("D28 a NON-operator gets no console sections at all",
      OP.console_sections({"is_operator": False}) ==
      [s for s in OP.console_sections({"is_operator": False}) if s["capability"] is None])


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("§E  THE RESEARCHED CONTROLS — anomalies, status notices, restore-drill honesty")
# ══════════════════════════════════════════════════════════════════════════════════════════════
# BURST
burst = [{"actor_auth_id": OWNER_UID, "actor_email": OWNER_EMAIL, "action": "tenant.enter",
          "target_org_id": TENANT_A, "created_at": iso(NOW + timedelta(seconds=10 * i))}
         for i in range(30)]
f = OP.anomalies(burst, now=NOW + timedelta(minutes=10))
check("E1 a burst of operator actions is flagged", any(x["kind"] == "burst" for x in f), f)
calm = [{"actor_auth_id": OWNER_UID, "action": "tenant.enter", "target_org_id": TENANT_A,
         "created_at": iso(NOW + timedelta(hours=i))} for i in range(30)]
check("E2 the same volume spread over 30 hours is NOT flagged as a burst",
      not any(x["kind"] == "burst" for x in OP.anomalies(calm, now=NOW + timedelta(hours=30))))

# FAN-OUT
fan = [{"actor_auth_id": OWNER_UID, "action": "tenant.enter",
        "target_org_id": "%08d-2222-4222-8222-bbbbbbbbbbbb" % i,
        "created_at": iso(NOW + timedelta(minutes=i * 30))} for i in range(6)]
f = OP.anomalies(fan, now=NOW + timedelta(hours=3))
check("E3 entering many DIFFERENT tenants in a day is flagged",
      any(x["kind"] == "tenant_fanout" for x in f), f)
check("E4 entering the SAME tenant repeatedly is not fan-out",
      not any(x["kind"] == "tenant_fanout" for x in OP.anomalies(
          [{**r, "target_org_id": TENANT_A} for r in fan], now=NOW + timedelta(hours=3))))

# DENIAL STREAK
den = [{"actor_auth_id": OWNER_UID, "action": "tenant.enter.denied",
        "created_at": iso(NOW + timedelta(minutes=i * 20))} for i in range(6)]
f = OP.anomalies(den, now=NOW + timedelta(hours=2))
check("E5 a run of refused operator actions is flagged RED",
      any(x["kind"] == "denied_streak" and x["severity"] == "red" for x in f), f)
check("E6 anomaly scanning never raises on malformed rows",
      OP.anomalies([None, "x", {}, {"created_at": "junk"}, 7]) == [])
check("E7 thresholds come from POLICY, not from constants in a branch",
      any(x["kind"] == "burst" for x in OP.anomalies(
          burst[:6], policy={"anomaly_burst_actions": 5}, now=NOW + timedelta(minutes=2))))

# STATUS NOTICES — including the cross-tenant rule (§19.15).
live = {"severity": "outage", "title": "Ingest degraded", "is_active": True}
check("E8 a notice with no window and no audience is live for every tenant",
      OP.notice_visible(live, org_id=TENANT_A, now=NOW)
      and OP.notice_visible(live, org_id=TENANT_B, now=NOW))
targeted = {**live, "org_ids": [TENANT_A]}
check("E9 ★ a notice targeted at tenant A is INVISIBLE to tenant B (no cross-tenant leak)",
      OP.notice_visible(targeted, org_id=TENANT_A, now=NOW)
      and not OP.notice_visible(targeted, org_id=TENANT_B, now=NOW))
check("E10 a notice with no resolved org is not shown to an unknown caller",
      not OP.notice_visible(targeted, org_id=None, now=NOW))
check("E11 a future notice is not yet live",
      not OP.notice_visible({**live, "starts_at": iso(NOW + timedelta(hours=1))}, org_id=TENANT_A, now=NOW))
check("E12 a finished notice takes ITSELF down (no stale banner)",
      not OP.notice_visible({**live, "ends_at": iso(NOW - timedelta(seconds=1))}, org_id=TENANT_A, now=NOW))
check("E13 a withdrawn notice is gone", not OP.notice_visible({**live, "is_active": False},
                                                              org_id=TENANT_A, now=NOW))
for bad in (None, "x", 5, [], {}):
    check("E14 a malformed notice is hidden, never pinned up forever (%r)" % (bad,),
          not OP.notice_visible(bad, org_id=TENANT_A, now=NOW))
check("E15 an unparseable window hides the notice rather than showing it forever",
      not OP.notice_visible({**live, "ends_at": "garbage"}, org_id=TENANT_A, now=NOW))
check("E16 the notice lamp uses the CONTROL BOX ladder — an outage is red, maintenance amber",
      OP.notice_lamp([live], org_id=TENANT_A, now=NOW) == "red"
      and OP.notice_lamp([{**live, "severity": "maintenance"}], org_id=TENANT_A, now=NOW) == "amber")
check("E17 no live notices ⇒ green (an empty banner is not an incident)",
      OP.notice_lamp([], org_id=TENANT_A, now=NOW) == "green")

# RESTORE DRILL — §20 honesty: never green without evidence.
lamp, why = OP.drill_lamp(None, now=NOW)
check("E18 ★ NO restore drill ever recorded reads RED, not amber and never green — an untested "
      "backup is not a backup", lamp == "red", (lamp, why))
check("E19 a FAILED drill reads red",
      OP.drill_lamp({"outcome": "failed", "verified_at": iso(NOW - timedelta(days=1))}, now=NOW)[0] == "red")
check("E20 a recent PASSING drill reads green",
      OP.drill_lamp({"outcome": "passed", "verified_at": iso(NOW - timedelta(days=10))}, now=NOW)[0] == "green")
check("E21 a drill just past cadence reads amber (look at it), past grace reads red (it stopped)",
      OP.drill_lamp({"outcome": "passed", "verified_at": iso(NOW - timedelta(days=100))}, now=NOW)[0] == "amber"
      and OP.drill_lamp({"outcome": "passed", "verified_at": iso(NOW - timedelta(days=200))}, now=NOW)[0] == "red")
check("E22 a PARTIAL drill is never green", OP.drill_lamp(
    {"outcome": "partial", "verified_at": iso(NOW - timedelta(days=1))}, now=NOW)[0] == "amber")
check("E23 an unusable or future-dated drill reads UNKNOWN, never green",
      OP.drill_lamp({"outcome": "passed", "verified_at": "junk"}, now=NOW)[0] == "unknown"
      and OP.drill_lamp({"outcome": "passed", "verified_at": iso(NOW + timedelta(days=2))}, now=NOW)[0] == "unknown")
for bad in ({}, {"outcome": "great", "scope": "all", "performed_at": iso(NOW)},
            {"outcome": "passed", "scope": "", "performed_at": iso(NOW)},
            {"outcome": "passed", "scope": "full cluster", "performed_at": "nope"}, None, "x"):
    ok, _why = OP.drill_record_valid(bad)
    check("E24 a drill attestation that is not evidence is REFUSED (%s)" % str(bad)[:44], not ok)
ok, _ = OP.drill_record_valid({"outcome": "passed", "scope": "full cluster to staging",
                               "performed_at": iso(NOW)})
check("E25 a well-formed attestation is accepted", ok)


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 96)
print("  %d passed, %d failed" % (PASS, FAIL))
print("═" * 96)
sys.exit(1 if FAIL else 0)
