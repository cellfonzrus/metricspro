"""A REP'S TARGETS ARE THEIR OWN, AND THE SCREEN NAMED "MY" OFFERS NOBODY ELSE.

OWNER, 2026-11-27: *"under my targets it should only shpow the name of the person whoc is logged in
not all reps , that needs to chnage across teh paltform"*.

WHAT HE WAS LOOKING AT. `/commcalc/targets/my` has carried its own confession since before logins
existed: *"Until logins are enabled, pick your store and name below. This view will be locked to you
automatically once sign-in is live."* Sign-in went live; nobody came back to close it. So the screen
still asked every viewer to choose a name out of the whole store's roster.

THE DEFECT UNDER THE COSMETIC ONE, and it is why this is an access test rather than a UI tweak.
`GET /targets/{period}/calendar` took `rep` as a plain query parameter and applied NO person filter
of any kind — the only guard in the handler is `scope_keyset`, which bounds the STORE. It also
published `reps`: every name that worked or sold at that store. A confined employee is granted read
on `/commcalc/targets` (`rep_surface`, the action-plan surface), so one rep could name a colleague
and read their target, their pace, their achieved figures and their shortfall.

THE RULE THAT ALREADY SAID SO. `rbac.SELF_OWN_STORE_SURFACES` lists this page FIRST and states the
paired rule in its own comment: *"a surface listed here shows STORE-LEVEL data. If it also carries a
per-employee pay / commission / compensation / PII column, that column is filtered to the caller's
own employee_id … Adding a surface to this list without that filter is a payroll leak between
colleagues."* The declaration was right; the wiring was never done. That is §19.18 — a registry
written and its callers left unwired — for the fourth time in this repo, which is why the lock below
is an AST lock and not a comment.

THE CLASS: **"may this door open" and "whose rows come out of it" are two questions, and a surface a
rep is SUPPOSED to reach can only be judged by the second.** Named already in
`probe-own-surface-foreign-rows`; this is the same shape, one subsystem over.

TWO TENANTS throughout, with the same role NAMES and different declarations, because a feature is
for every tenant (CLAUDE.md 2026-10-30) and a one-tenant answer would otherwise pass.

★ NO LOCKOUT ★ is asserted as hard as the refusal: a filter that hides a rep from their OWN figures
is a worse outage than the leak it closes, and the accent/middle-initial spellings are exactly where
that happens. See `test_a_rep_whose_name_is_spelled_differently_*`.
"""
import pytest

from app.modules.commcalc import targets_engine

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
PERIOD = "November 2026"

UID = {"repA": "uid-rep-a", "mgrA": "uid-mgr-a", "admA": "uid-adm-a", "repC": "uid-rep-c",
       "repB": "uid-rep-b", "mgrB": "uid-mgr-b"}
HDR = {k: f"Bearer {v}" for k, v in UID.items()}
_BY_TOKEN = {v: UID[k] for k, v in HDR.items()}

# The rep's roster spelling, and the three spellings the POS/schedule feeds actually write for them.
# Each is the SAME person and must resolve to them; `OTHER` is a colleague and must never resolve.
ME = "Diana Antunez"
ME_POS = "Antunez, Diana"              # the comma form — the 2026-09-02 finding
ME_ACCENT = "Díana Antúnez"            # accents: `token_key` alone would lock her out of her own page
OTHER = "Marco Reyes"


def _people(org, suffix, store):
    return {
        "app_users": [
            {"id": f"au-rep-{suffix}", "org_id": org, "auth_id": UID[f"rep{suffix}"],
             "role": "sales_rep", "employee_id": f"E100{suffix}", "is_active": True},
            {"id": f"au-mgr-{suffix}", "org_id": org, "auth_id": UID[f"mgr{suffix}"],
             "role": "store_manager", "employee_id": f"E200{suffix}", "store_code": store,
             "is_active": True},
        ],
        "employees": [
            {"id": f"1{suffix}", "org_id": org, "employee_id": f"E100{suffix}", "name": ME,
             "home_store": store, "is_active": True},
            {"id": f"2{suffix}", "org_id": org, "employee_id": f"E101{suffix}", "name": OTHER,
             "home_store": store, "is_active": True},
            {"id": f"3{suffix}", "org_id": org, "employee_id": f"E200{suffix}", "name": "The Manager",
             "home_store": store, "is_active": True},
        ],
        "roles": [
            # Tenant A declares the individual contributor through `scope`, tenant B through
            # `people_visibility` over a store scope. Both shapes exist live, so both are tested.
            {"id": f"r1{suffix}", "org_id": org, "name": "sales_rep",
             "permissions": ({"scope": "self"} if suffix == "A"
                             else {"scope": "store", "people_visibility": "self"})},
            {"id": f"r2{suffix}", "org_id": org, "name": "store_manager",
             "permissions": {"scope": "store", "people_visibility": "span"}},
            {"id": f"r3{suffix}", "org_id": org, "name": "admin",
             "permissions": {"scope": "all", "people_visibility": "all"}},
        ],
    }


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """RBAC on, two tenants, the REAL targets handler reading the double.

    `_fetch_shifts` / `_fetch_actuals` are stubbed because what they return is not under test — the
    question is which of their rows reach the caller. Everything that DECIDES that (the role row,
    the app_user, `scope_keyset`, the people keyset) is the real code reading real seeded rows."""
    from app.modules.commcalc import router as cr

    # `storeops.router.sb()` is the storeops-SCHEMA client (`sb().table("app_config")`), while
    # `commcalc.router.sb()` is the default-schema one that names its schema at each call. Patching
    # both to the same thing makes RBAC read as OFF and every filter unrestricted — which is how the
    # first run of this file passed the fix and failed the tests.
    monkeypatch.setattr("app.modules.storeops.router.sb",
                        lambda: fake_supabase.schema("storeops"), raising=False)
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: fake_supabase, raising=False)
    for mod in ("app.modules.storeops.router", "app.modules.commcalc.router"):
        monkeypatch.setattr(f"{mod}.get_supabase", lambda: fake_supabase, raising=False)
    monkeypatch.setattr("app.modules.core.router._uid_from_token",
                        lambda a: _BY_TOKEN.get((a or "").strip()))
    monkeypatch.setattr(
        "app.core.tenant_middleware.caller_app_user_http",
        lambda uid, cols="*": next(
            (u for u in (fake_supabase.schema("storeops").table("app_users").select("*")
                         .execute().data or []) if u.get("auth_id") == uid), None))

    # Both colleagues worked, and both sold, at the same store — the shape the owner is looking at.
    # KEYED BY ORG, because the real `_fetch_shifts`/`_fetch_actuals` are org-scoped reads and a stub
    # that ignored `org_id` would hand one tenant's rows to the other and make a cross-tenant
    # assertion here meaningless (it did, on the first run of this file).
    shifts, actuals = {ORG_A: [], ORG_B: []}, {ORG_A: [], ORG_B: []}
    for org, store in ((ORG_A, "S1"), (ORG_B, "T1")):
        for name in (ME, OTHER):
            shifts[org].append({"store_code": store, "employee_name": name,
                                "shift_date": "2026-11-03", "hours": 8.0})
        # The POS writes her the other way round, and once with accents.
        for name in (ME_POS, ME_ACCENT, OTHER):
            actuals[org].append({"store_code": store, "rep_name": name, "sale_date": "2026-11-03",
                                 "activations": 1, "upgrades": 0, "accessories_amount": 10.0})
    monkeypatch.setattr(cr, "_fetch_shifts",
                        lambda client, start, end, org_id, *a, **k: list(shifts.get(org_id, [])),
                        raising=False)
    monkeypatch.setattr(cr, "_fetch_actuals",
                        lambda client, org_id, *a, **k: list(actuals.get(org_id, [])),
                        raising=False)

    # Nobody here manages an org unit — the tree resolves a manager for 6 of 29 house stores and 0
    # of 20 Luxelink stores, so "no subtree" is the live majority shape and the right default here.
    fake_supabase.seed_rpc("storeops.org_span_for_manager", lambda params: [])
    fake_supabase.seed("storeops.app_config", [{"id": 1, "rbac_enabled": True}])
    a, b = _people(ORG_A, "A", "S1"), _people(ORG_B, "B", "T1")
    # The admin exists in tenant A only: an unlinked super-admin is the live shape (3 of 140 logins).
    a["app_users"].append({"id": "au-adm-a", "org_id": ORG_A, "auth_id": UID["admA"],
                           "role": "admin", "employee_id": None, "is_active": True})
    # repC is a real, linked, NAMED rep whom nobody has placed in a store: no login pin and no
    # roster `home_store`. That is the shape the empty-keyset hole lived on.
    a["app_users"].append({"id": "au-rep-c", "org_id": ORG_A, "auth_id": UID["repC"],
                           "role": "sales_rep", "employee_id": "E300A", "is_active": True})
    a["employees"].append({"id": "4A", "org_id": ORG_A, "employee_id": "E300A",
                           "name": "Unplaced Rep", "home_store": None, "is_active": True})
    for table in ("app_users", "employees", "roles"):
        fake_supabase.seed(f"storeops.{table}", a[table] + b[table])
    fake_supabase.seed("storeops.stores", [
        {"id": "s1", "org_id": ORG_A, "store_code": "S1", "address": "1 A St", "is_active": True},
        {"id": "t1", "org_id": ORG_B, "store_code": "T1", "address": "1 B St", "is_active": True}])
    fake_supabase.seed("storeops.shifts", [])
    fake_supabase.seed("storeops.timelog", [])
    fake_supabase.seed("commcalc.targets", [
        # The column names are DEREFERENCED from the engine, not spelled here: a target seeded under
        # the wrong spelling sums to zero, the store is dropped from the response, and every
        # assertion below would pass against an empty payload (it did, on the first run).
        {"id": "tgA", "org_id": ORG_A, "period": PERIOD, "store_code": "S1",
         **{targets_engine.BUILTIN_TARGET_COLUMN[c]: v for c, v in
            (("activations", 100), ("upgrades", 50), ("accessories", 5000))}},
        {"id": "tgB", "org_id": ORG_B, "period": PERIOD, "store_code": "T1",
         **{targets_engine.BUILTIN_TARGET_COLUMN[c]: v for c, v in
            (("activations", 100), ("upgrades", 50), ("accessories", 5000))}}])
    return fake_supabase


def _cal(client, who, org, store, **qs):
    q = "".join(f"&{k}={v}" for k, v in qs.items())
    return client.get(f"/api/v1/commcalc/targets/{PERIOD}/calendar"
                      f"?store_code={store}&org_id={org}{q}",
                      headers={"Authorization": HDR[who]})


# ── THE PICKER: WHOSE NAMES COME BACK ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("who,org,store", [("repA", ORG_A, "S1"), ("repB", ORG_B, "T1")])
def test_a_rep_is_offered_only_their_own_name(api_client, wired, who, org, store):
    """THE OWNER'S REPORT. The dropdown was built from this list."""
    r = _cal(api_client, who, org, store, scope="store")
    assert r.status_code == 200, r.text
    reps = r.json()["reps"]
    assert OTHER not in reps, f"a colleague is still offered: {reps}"
    assert len(reps) == 1, f"exactly one name — the viewer's own: {reps}"


@pytest.mark.parametrize("who,org,store", [("mgrA", ORG_A, "S1"), ("mgrB", ORG_B, "T1")])
def test_the_manager_of_that_store_is_still_offered_their_team(api_client, wired, who, org, store):
    """The narrowing must not reach a viewer whose job is the whole store."""
    reps = _cal(api_client, who, org, store, scope="store").json()["reps"]
    assert OTHER in reps, f"the manager lost their own team: {reps}"


def test_an_unrestricted_admin_still_sees_everyone(api_client, wired):
    reps = _cal(api_client, "admA", ORG_A, "S1", scope="store").json()["reps"]
    assert OTHER in reps and len(reps) > 1, reps


# ── THE LEAK: NAMING A COLLEAGUE DIRECTLY ────────────────────────────────────────────────────────
@pytest.mark.parametrize("who,org,store", [("repA", ORG_A, "S1"), ("repB", ORG_B, "T1")])
def test_a_rep_cannot_read_a_colleagues_figures_by_naming_them(api_client, wired, who, org, store):
    """THE REGRESSION. Not hiding the name from a dropdown — REFUSING the request. A hidden option
    in a browser is not a gate; the owner's screen is one `curl` away from this parameter."""
    r = _cal(api_client, who, org, store, scope="rep", rep=OTHER.replace(" ", "%20"))
    assert r.status_code == 403, f"a colleague's targets came back: {r.status_code} {r.text[:400]}"


@pytest.mark.parametrize("who,org,store", [("repA", ORG_A, "S1"), ("repB", ORG_B, "T1")])
def test_a_rep_can_read_their_own(api_client, wired, who, org, store):
    r = _cal(api_client, who, org, store, scope="rep", rep=ME.replace(" ", "%20"))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("who,org,store", [("mgrA", ORG_A, "S1"), ("mgrB", ORG_B, "T1")])
def test_the_manager_can_read_their_own_rep(api_client, wired, who, org, store):
    r = _cal(api_client, who, org, store, scope="rep", rep=OTHER.replace(" ", "%20"))
    assert r.status_code == 200, r.text


def test_a_rep_we_can_place_in_no_store_reads_no_store(api_client, wired):
    """THE NARROW HOLE THE `is not None` CHANGE ACTUALLY CLOSES, and it is pinned separately because
    the broad test below passes either way once the rep's own store resolves.

    `_caller_self_keyset` answers `(True, set())` for a rep nobody has placed — no login pin, no
    roster home_store. The old guard read `if ks and ...`, and an empty set is FALSY, so that viewer
    skipped the store check entirely and could read EVERY store in the tenant. Six live logins sat on
    the same shape in 2026-08-08 on the sibling endpoint. "Sees nothing, and is told why" is the safe
    failure; the summary endpoint's `setup_hint` is what tells them."""
    r = _cal(api_client, "repC", ORG_A, "S1", scope="store")
    assert r.status_code == 403, f"an unplaced rep read a store: {r.status_code}"


def test_a_rep_cannot_read_another_stores_targets_either(api_client, wired):
    """THE SECOND DEFECT, found by this file. The handler's only guard reads

        ks = scope_keyset(authorization, org_id)
        if ks and not in_keyset(ks, store_code): raise 403

    and a self-scoped rep resolves to an EMPTY keyset, which is falsy — so the check never ran for
    exactly the viewer it needed to bound, and any `store_code` in the tenant was readable. The
    comment above it was right that an empty keyset must not DENY-ALL; the answer is to resolve the
    rep to their OWN store(s) (ruling #7, `self_own_store=True`), not to drop the guard."""
    r = _cal(api_client, "repA", ORG_A, "S9-NOT-HERS", scope="store")
    assert r.status_code == 403, f"another store's targets came back: {r.status_code}"


# ── ★ NO LOCKOUT ★ ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("spelling", [ME_POS, ME_ACCENT])
def test_a_rep_whose_name_is_spelled_differently_by_the_feed_still_reads_their_own(
        api_client, wired, spelling):
    """Her roster name is "Diana Antunez"; the POS writes "Antunez, Diana" and once with accents.
    A filter that compares raw strings — or folds without the accent fold — hides her from her own
    targets. That is a worse outage than the leak this closes, so it is pinned as hard."""
    r = _cal(api_client, "repA", ORG_A, "S1", scope="rep",
             rep=spelling.replace(" ", "%20").replace(",", "%2C"))
    assert r.status_code == 200, f"{spelling!r} is her own name: {r.status_code} {r.text[:300]}"


def test_her_own_spellings_are_offered_as_one_name_not_three(api_client, wired):
    """The three feed spellings are one person, so the picker must not show her three times."""
    reps = _cal(api_client, "repA", ORG_A, "S1", scope="store").json()["reps"]
    assert len(reps) == 1, reps


# ── THE SAME PAGE'S OTHER PAYLOAD ────────────────────────────────────────────────────────────────
# `/summary` is the call that fills the store picker and the area roll-up on the SAME screen, and it
# breaks each store down "into the people driving it": one row per rep carrying their achieved
# activations, upgrades, accessories and conversion rate. The store list was already narrowed to the
# caller's own store; the rows inside it were not. Finding this is why the sweep is part of the fix
# and not a follow-up — a picker that offers one name, over a payload that carries ten people's
# numbers, closes nothing at all.
def _summary(client, who, org):
    return client.get(f"/api/v1/commcalc/targets/{PERIOD}/summary"
                      f"?org_id={org}&include_untargeted=1",
                      headers={"Authorization": HDR[who]})


@pytest.mark.parametrize("who,org", [("repA", ORG_A), ("repB", ORG_B)])
def test_the_summary_does_not_carry_a_colleagues_achieved_figures(api_client, wired, who, org):
    r = _summary(api_client, who, org)
    assert r.status_code == 200, r.text
    names = [row.get("rep") for s in (r.json().get("stores") or []) for row in (s.get("reps") or [])]
    assert OTHER not in names, f"a colleague's achieved figures came back: {names}"


@pytest.mark.parametrize("who,org", [("repA", ORG_A), ("repB", ORG_B)])
def test_the_summary_still_carries_the_reps_own_figures(api_client, wired, who, org):
    """★ NO LOCKOUT ★ on this payload too: narrowing to nobody would blank the whole screen."""
    names = [row.get("rep") for s in (_summary(api_client, who, org).json().get("stores") or [])
             for row in (s.get("reps") or [])]
    assert names, "the rep lost their own achieved figures"


@pytest.mark.parametrize("who,org", [("mgrA", ORG_A), ("mgrB", ORG_B)])
def test_the_summary_is_unchanged_for_a_manager(api_client, wired, who, org):
    names = [row.get("rep") for s in (_summary(api_client, who, org).json().get("stores") or [])
             for row in (s.get("reps") or [])]
    assert OTHER in names, f"the manager lost their team's breakdown: {names}"
