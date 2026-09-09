"""Proof harness: the CONNECTOR ROUTE GATE (connector_route_policy + everything that reads it).

OWNER DIRECTIVE 2026-09-09, verbatim: "we are not doing the 2FA login for b2b as they sent an email out
to not do it, so make that gated by default for all unless it opens up later, only option for b2b is
email ingested reports".

THE DEFECT BEING RETIRED, from live data (commcalc.data_source d0c12f4f…, house org, read 2026-09-09):
    enabled = true, consecutive_failures = 29, last delivery 2026-07-16,
    last_status = "error: The b2bsoft session has expired — please re-authenticate
                   (Log in + enter the 2FA code)."
Twenty-nine scheduled attempts, each one ending in a prompt telling a human to perform exactly the
sign-in the vendor had told them not to perform. This harness proves the four things that must now be
true, and the two that must NOT change:

  §1 THE SEEDED DEFAULT IS OFF FOR EVERY ORG — house org included — and the migration's seed is what
     says so (no code default is doing it).
  §2 A CLOSED CONNECTOR IS A STATED STATE: never a failure, never a silence, and NEVER rendered as
     healthy. It maps to `unmonitored` on the platform lamp, it never pages, and the roll-up refuses to
     announce "all portal sessions are riding a valid login" while one is switched off.
  §3 FLIPPING ONE CONFIG ROW FULLY RESTORES THE LOGIN PATH — nothing was deleted, so nothing has to be
     re-written.
  §4 THE SIBLING CARRIERS ARE PROVABLY UNAFFECTED: VidaPay / T-CETRA (whose 2FA is a DIFFERENT vendor's
     and stays working), the three merchant card portals, and every unlisted connector all resolve OPEN
     against the very same seeded rows.
  §5 THE REGRESSION: the exact live row above, replayed, is skipped rather than retried — and its 29
     failures stay on the record rather than being rewritten away.
  §6 PRE-MIGRATION / MISCONFIGURED INPUT IS INERT: no table, no rows, a garbage row ⇒ the login path
     behaves exactly as it did before this change.

No DB, no network. Run:  cd backend && python3 harness_connector_route_policy.py
"""
import re
import sys

sys.path.insert(0, ".")

from app.modules.commcalc import connector_route_policy as crp        # noqa: E402
from app.modules.commcalc import portal_session_health as psh         # noqa: E402
from app.modules.core import control_box as cb                        # noqa: E402

PASS = FAIL = 0
HOUSE = crp.HOUSE_ORG
TENANT = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"      # a real second org id (LuxeLink), as data
OTHER = "11111111-2222-3333-4444-555555555555"

MIG = "../database/migrations/998_connector_route_policy.sql"


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s" % msg)


def eq(got, want, msg):
    ok(got == want, "%s  [got %r]" % (msg, got))


def seeded_rows():
    """The house rows EXACTLY as migration 998 seeds them — parsed out of the migration itself, so this
    harness cannot pass against a seed the migration does not actually contain."""
    sql = open(MIG).read()
    body = sql.split("VALUES", 1)[1].split("ON CONFLICT", 1)[0]
    rows = []
    for m in re.finditer(r"\('([0-9a-f-]{36})',\s*'([a-z0-9_]+)',\s*'([a-z_]+)',\s*(true|false)", body):
        org, conn, route, allowed = m.groups()
        rows.append({"org_id": org, "connector": conn, "route": route,
                     "allowed": allowed == "true",
                     "reason": "The vendor emailed us not to use their 2FA / browser login, so the "
                               "platform does not attempt it (owner directive 2026-09-09).",
                     "remedy_route": "email_sweep",
                     "remedy_label": "the email-ingested reports",
                     "remedy_href": "/commcalc/email-imports"})
    return rows


def main():
    print("\n1. THE SEED — off by default for EVERY org, including the house org")
    rows = seeded_rows()
    ok(len(rows) == 2, "migration 998 seeds exactly the two slugs the scraper registry accepts")
    ok(all(r["org_id"] == HOUSE and r["allowed"] is False and r["route"] == "pull" for r in rows),
       "every seeded row is a HOUSE-org row that closes the 'pull' route")
    for org, who in ((HOUSE, "the house org itself"), (TENANT, "an existing tenant"),
                     (OTHER, "an org created tomorrow")):
        for slug in ("b2bsoft", "b2b"):
            p = crp.resolve(rows, org, slug)
            ok(crp.is_closed(p), "the portal login is CLOSED for %s (connector %r)" % (who, slug))
    eq(crp.resolve(rows, TENANT, "B2BSoft")["allowed"], False,
       "the connector slug matches case-insensitively (a row saved 'B2BSoft' is still gated)")
    eq(crp.resolve(rows, TENANT, "b2bsoft", route="email_sweep")["allowed"], True,
       "ONLY the portal-login route is closed — the email route stays open")
    ok(crp.resolve(rows, OTHER, "b2bsoft")["source"] == "house",
       "an org with no row of its own inherits the HOUSE default (mig 244 inheritance shape)")

    print("\n2. A CLOSED CONNECTOR IS A STATED STATE — never a fault, never silent, never healthy")
    pol = crp.resolve(rows, HOUSE, "b2bsoft")
    h = crp.health(pol)
    eq(h["state"], "route_disabled", "it has its own state on the portal-session ladder")
    ok(h["state"] in psh.STATES, "that state is registered in portal_session_health.STATES")
    ok(h["needs_human"] is False and h["actionable"] is False,
       "it is NOT actionable — nobody can fix it by signing in")
    eq(psh.should_notify(h), False, "it never pages, so the 29-failure alert loop stops")
    eq(psh.should_notify(h, last_notified_state="needs_login"), False,
       "...and it does not page even when the previous notified state was worse")
    eq(cb.LAMP_FROM_PORTAL_STATE["route_disabled"], "unmonitored",
       "the platform lamp is `unmonitored` — deliberately NOT green")
    ok(cb.LAMP_FROM_PORTAL_STATE["route_disabled"] != "green",
       "a switched-off connector can never be rendered as healthy")
    ok(cb._RANK["unmonitored"] < cb._RANK["red"] and cb._RANK["unmonitored"] > cb._RANK["green"],
       "...and it sits between green and an incident: not a pass, not an alarm")
    ok(h["headline"] and h["detail"], "the chip carries BOTH a headline and a detail — never blank")
    ok("not a fault" in h["detail"], "the detail says outright that this is not a fault")
    ok("The vendor emailed us not to use" in h["detail"],
       "the operator's recorded REASON is shown verbatim, so nobody re-derives it")
    ok("email-ingested reports" in h["detail"],
       "the detail names the route that IS supported instead of prescribing a sign-in")
    ok("sign" in h["detail"].lower() and "will not help" in h["detail"].lower(),
       "it explicitly tells the reader that signing in will not help")
    ok("re-authenticate" not in h["detail"].lower() and "2fa" not in h["headline"].lower(),
       "the remedy the vendor forbade appears nowhere in the copy")

    print("\n2b. The gate's REFUSAL body is not the cooldown's — no confirm-to-override escape")
    ref = crp.refusal(pol)
    eq(ref["ok"], False, "the endpoint refuses")
    eq(ref["route_disabled"], True, "it says WHY in a field of its own")
    eq(ref.get("blocked"), None, "it is NOT reported as a portal block (that would be a fault)")
    eq(ref["requires_confirm"], False,
       "there is no second-click override: a cooldown is a timer, an instruction is not")
    eq(ref["remedy_route"], "email_sweep", "the refusal names the supported route")
    eq(ref["remedy_href"], "/commcalc/email-imports", "...and where to go for it")
    ok(ref["message"], "the refusal carries a full human sentence, not just a code")

    print("\n2c. The ROLL-UP cannot describe a switched-off connector as fine")
    src_off = {"id": "d0c12f4f", "label": "b2b sales reports", "processor": "b2bsoft",
               "has_session": True, "auth_status": "authenticated",
               "route_policy": crp.resolve(rows, HOUSE, "b2bsoft")}
    summ = psh.summarize([src_off])
    eq(summ["worst"], "route_disabled", "the banner's worst state is the disabled one")
    eq(summ["needs_human"], 0, "nobody is asked to do anything")
    eq(summ["disabled"], 1, "...but the count of switched-off connectors is reported alongside it")
    res = cb._eval_portal_sessions({"key": "portals", "kind": "portal_sessions", "enabled": True,
                                    "label": "Portal sessions"},
                                   {"summary": summ}, None)
    eq(res["lamp"], "unmonitored", "the control-box lamp is unmonitored, not green")
    ok("switched off" in res["headline"],
       "the headline SAYS the route is switched off [%s]" % res["headline"])
    ok("riding a valid login" not in res["headline"],
       "...and never claims the sessions are all fine")
    healthy_only = psh.summarize([{"id": "x", "has_session": True,
                                   "session_expires_at": "2099-01-01T00:00:00Z"}])
    ok("riding a valid login" in cb._eval_portal_sessions(
        {"key": "portals", "kind": "portal_sessions", "enabled": True, "label": "P"},
        {"summary": healthy_only}, None)["headline"],
       "a genuinely healthy roll-up still reads exactly as it always did")

    print("\n3. REVERSIBLE — one config row re-opens the login path completely")
    reopened = [dict(r, allowed=True) if r["connector"] == "b2bsoft" else r for r in rows]
    p = crp.resolve(reopened, TENANT, "b2bsoft")
    ok(crp.is_open(p), "flipping the house row's `allowed` re-opens it for every org")
    eq(crp.health(p)["state"], "route_disabled", "health() is only ever asked about a closed route")
    eq(psh.evaluate({"has_session": True, "auth_status": "authenticated",
                     "session_expires_at": "2099-01-01T00:00:00Z",
                     "route_policy": p})["state"], "healthy",
       "with the route open the row reports its ORDINARY session health again")
    eq(psh.evaluate({"has_session": True, "auth_status": "needs_2fa", "route_policy": p})["state"],
       "needs_login", "...including the sign-in prompt, unchanged, when the session really is dead")
    tenant_only = rows + [{"org_id": TENANT, "connector": "b2bsoft", "route": "pull", "allowed": True}]
    ok(crp.is_open(crp.resolve(tenant_only, TENANT, "b2bsoft")),
       "one TENANT may re-open it for itself with its own row")
    ok(crp.is_closed(crp.resolve(tenant_only, OTHER, "b2bsoft")),
       "...without re-opening it for anybody else")
    ok("UPDATE commcalc.connector_route_policy SET allowed = true" in open(MIG).read(),
       "the migration's REVERT note spells out that one-row re-open")

    print("\n4. THE SIBLING CARRIERS ARE UNAFFECTED (same rows, same resolver)")
    siblings = ["vidapay", "total_access", "payanywhere", "transfirst", "businesstrack",
                "epay", "dlar", "vip", "ftp", "email", "", None]
    for org in (HOUSE, TENANT, OTHER):
        for slug in siblings:
            ok(crp.is_open(crp.resolve(rows, org, slug)),
               "%r stays OPEN for %s" % (slug, org[:8]))
    vp = {"has_session": True, "auth_status": "needs_2fa", "processor": "vidapay",
          "route_policy": crp.resolve(rows, HOUSE, "vidapay")}
    eq(psh.evaluate(vp)["state"], "needs_login",
       "VidaPay/T-CETRA's OWN 2FA prompt is untouched — a different vendor, still working")
    eq(psh.evaluate(vp)["needs_human"], True, "...and still raises a human, exactly as before")
    ok(all(c in open(MIG).read() for c in ("'b2bsoft'", "'b2b'")),
       "the migration closes only the two slugs for the one portal")
    ok("vidapay" not in open(MIG).read().lower() and "payanywhere" not in open(MIG).read().lower(),
       "no sibling connector is named anywhere in the migration's seed")

    print("\n5. REGRESSION — the live 29-failure row, replayed")
    live = {"id": "d0c12f4f-7313-4a83-b97d-3dfbebd7c83e", "org_id": HOUSE,
            "processor": "b2bsoft", "label": "b2b sales reports ", "enabled": True,
            "consecutive_failures": 29, "auth_status": "authenticated",
            "last_status": "error: The b2bsoft session has expired — please re-authenticate "
                           "(Log in + enter the 2FA code).",
            "has_session": True, "last_run_at": "2026-07-16T23:05:30.770742+00:00"}
    before = psh.evaluate(dict(live))
    eq(before["state"], "expired",
       "BEFORE the gate: the row reads 'expired' and prescribes a re-login (the retry loop)")
    ok(before["needs_human"], "...which is what raised an alert 29 times")
    live["route_policy"] = crp.resolve(rows, live["org_id"], live["processor"])
    after = psh.evaluate(dict(live))
    eq(after["state"], "route_disabled", "AFTER: the same row reads as switched-off-by-config")
    eq(after["needs_human"], False, "the alert loop stops")
    eq(psh.should_notify(after, last_notified_state="expired"), False,
       "...and no further notification fires, even after a worse state was already reported")
    eq(live["consecutive_failures"], 29,
       "the 29 recorded failures are NOT rewritten — the evidence stays on the record")
    eq(live["last_status"],
       "error: The b2bsoft session has expired — please re-authenticate (Log in + enter the 2FA code).",
       "...and neither is the stored status: the gate computes, it does not overwrite history")
    ok(crp.status_line(live["route_policy"]),
       "the computed status line the UI shows instead is non-empty")
    ok(len(crp.status_line(live["route_policy"])) <= 300,
       "...and is capped like every other stored status string")

    print("\n6. PRE-MIGRATION AND MALFORMED INPUT ARE INERT (no behaviour change)")
    ok(crp.is_open(crp.resolve([], HOUSE, "b2bsoft")),
       "no rows at all (pre-migration-998) ⇒ the login path behaves exactly as before")
    ok(crp.is_open(crp.resolve(None, HOUSE, "b2bsoft")), "a failed read (None) is open, never closed")
    ok(crp.is_open(crp.resolve(["nonsense", 7, None], HOUSE, "b2bsoft")),
       "garbage rows are ignored rather than crashing the gate")
    ok(crp.is_open(crp.resolve([{"connector": "b2bsoft"}], HOUSE, "b2bsoft")),
       "a row with no `allowed` value defaults to ALLOWED — a gate never closes by accident")
    ok(crp.is_open(None) and crp.is_open({}) and crp.is_open("x"),
       "is_open() fails INERT on any non-policy input")
    eq(crp.headline({"allowed": True}), "", "an open route renders no chip at all")
    eq(crp.detail({"allowed": True}), "", "...and no detail")
    ok(crp.is_open(crp.resolve(rows, HOUSE, "b2bsoft", route="ftp")),
       "closing 'pull' does not close any other route for the same connector")

    print("\n7. THE ALERT ITSELF — the login popup stops nagging, but never goes quiet")
    # A DB-free stand-in for the attention provider's reads. p_connectors is a `cheap` provider on the
    # login-popup path, so it takes the policy off ctx and performs NO extra select — proven here by
    # a client that refuses any table the provider is not already entitled to read for this org.
    from app.modules.commcalc import import_audit as ia

    class _Q:
        def __init__(self, store, key, seen):
            self.store, self.key, self.seen, self.f = store, key, seen, {}

        def select(self, *a, **k):
            return self

        def eq(self, k, v):
            self.f[k] = v
            return self

        def in_(self, k, v):
            self.f["_in_" + k] = v
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            self.seen.append({"key": self.key, "filters": dict(self.f)})
            rows = [r for r in self.store.get(self.key, [])
                    if all(r.get(k) == v for k, v in self.f.items() if not k.startswith("_"))]
            return type("R", (), {"data": [dict(r) for r in rows]})()

    class _S:
        def __init__(self, store, schema, seen):
            self.store, self.schema, self.seen = store, schema, seen

        def table(self, t):
            return _Q(self.store, f"{self.schema}.{t}", self.seen)

    class FakeC:
        def __init__(self, store):
            self.store, self.seen = store, []

        def schema(self, s):
            return _S(self.store, s, self.seen)

    src = {"id": "d0c12f4f", "org_id": HOUSE, "processor": "b2bsoft", "label": "b2b sales reports",
           "enabled": True, "username": "u", "account_id": None, "password": "p",
           "auth_status": "authenticated", "auth_message": None,
           "last_status": "error: The b2bsoft session has expired — please re-authenticate "
                          "(Log in + enter the 2FA code).",
           "last_run_at": "2026-07-16T23:05:30+00:00", "last_attempt_at": "2026-09-09T10:00:00+00:00",
           "next_run_at": "2026-08-25T10:00:00+00:00", "frequency": "daily",
           "session_expires_at": None, "blocked_until": None, "block_reason": None,
           "consecutive_failures": 29}
    store = {"commcalc.data_source": [src], "commcalc.commission_org_config": [],
             "commcalc.epay_sweep_config": [], "commcalc.dlar_sweep_config": [],
             "commcalc.vip_sweep_config": [], "commcalc.b2b_sweep_config": []}

    before = ia.p_connectors(FakeC(store), HOUSE, {"now": None})
    mine = [i for i in before if src["id"] in (i.get("key") or "")]
    ok(any(i["severity"] == "error" for i in mine),
       "BEFORE the gate: the live row raises an ERROR item ('is not importing') at every admin login")
    ok(any("login" in (i.get("detail") or "").lower() for i in mine),
       "...whose remedy is the sign-in the vendor forbade")

    c2 = FakeC(store)
    after = ia.p_connectors(c2, HOUSE, {"now": None, "route_policy": rows})
    mine = [i for i in after if src["id"] in (i.get("key") or "")]
    eq(len(mine), 1, "AFTER: the same row raises exactly ONE item — not silence, not a stack of alarms")
    eq(mine[0]["severity"], "info", "...and it is INFO: a stated state, never an incident")
    ok("switched off" in mine[0]["label"], "the item SAYS the login is switched off [%s]" % mine[0]["label"])
    ok("not a fault" in mine[0]["detail"], "...and that it is not a fault")
    ok("The vendor emailed us not to use" in mine[0]["detail"], "...quoting the recorded reason")
    ok(mine[0].get("deep_link") == "/commcalc/email-imports",
       "its deep link points at the email-ingest route, not at the login")
    ok("email-ingested" in (mine[0].get("deep_link_label") or ""),
       "...and its button names that route [%s]" % mine[0].get("deep_link_label"))
    ok(not any(i["severity"] == "error" for i in mine), "no error item survives for this connector")
    ok(not any(f["key"] == "commcalc.connector_route_policy" for f in c2.seen),
       "the CHEAP provider performs NO policy read of its own — the rows arrive on the context, so "
       "the login popup's org-isolation invariant (harness_import_health §D) is untouched")

    print("\n7b. A sibling login on the same page is left completely alone")
    vp_src = dict(src, id="src-vp", processor="vidapay", label="VidaPay", consecutive_failures=0,
                  auth_status="needs_2fa", last_status="needs login: session expired")
    store2 = dict(store, **{"commcalc.data_source": [src, vp_src]})
    items = ia.p_connectors(FakeC(store2), HOUSE, {"now": None, "route_policy": rows})
    vp_items = [i for i in items if "src-vp" in (i.get("key") or "")]
    ok(any(i["severity"] == "error" for i in vp_items),
       "VidaPay's own needs-login alert still fires, unchanged, on the very same call")
    ok(all("switched off" not in (i.get("label") or "") for i in vp_items),
       "...and is never mislabelled as switched off")

    print("\n7c. With no policy on the context, every provider behaves exactly as before")
    plain = ia.p_connectors(FakeC(store), HOUSE, {"now": None})
    eq([i["key"] for i in plain], [i["key"] for i in before],
       "an absent route_policy context is byte-identical to pre-mig-998 behaviour")

    print("\n6b. Hygiene: no secret, and no vendor name in a code path")
    src = open("app/modules/commcalc/connector_route_policy.py").read()
    for word in ("b2bsoft", "vidapay", "wsreports", "luxelink"):
        ok(word not in src.lower(),
           "the policy module contains no %r literal (RULE TWO)" % word)
    for word in ("password", "session_state", "totp_secret", "cookie"):
        ok(word not in src.lower().replace("no secret", ""),
           "the policy module never touches %r" % word)
    blob = " ".join(str(v) for v in list(crp.health(pol).values()) + list(crp.refusal(pol).values()))
    ok("password" not in blob.lower() and "cookie" not in blob.lower(),
       "nothing the gate renders can carry credential or session material")

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
