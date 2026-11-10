"""Integration: the super-admin tenant-gate board, through the REAL app, for EVERY tenant.

OWNER (2026-11-10): *"why is vidapay connector showing in boost ? we need to get these things under
control before we roll out for outside customers — have an agent asses and create a dashboard for the
super admin whereby these things can be gated out for the respective tenants right from the get go"*.

The unit proof (`backend/harness_feature_gate.py`) pins the truth table and the three mechanisms
dereferencing it. This pins what a REAL request lands on:

  · a non-super-admin cannot read or write the board AT ALL — it is cross-tenant by nature;
  · the board carries every tenant, each with its own posture;
  · a GATED tenant's grid reports everything off, in the gate's own words;
  · a grant reaches that tenant and ONLY that tenant — the leak the owner was looking at;
  · clearing a decision returns the thing to the posture rather than storing the opposite;
  · the connector-keyed report-pull rows are connector-scoped on a real GET, and what is withheld
    is reported rather than silently dropped.

TWO TENANTS throughout, with DIFFERENT declared carriers, because one tenant's vocabulary reading as
another's is the defect class this feature exists to close (CLAUDE.md, 2026-10-30).
"""
import pytest

ORG_A = "11111111-1111-1111-1111-111111111111"   # declares carrier 'boost'
ORG_B = "22222222-2222-2222-2222-222222222222"   # declares carrier 'total'
SUPER, PLAIN = "Bearer super", "Bearer plain"
UID = {SUPER: "uid-super", PLAIN: "uid-plain"}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """Both tenants registered, one super admin, one ordinary admin, and every reader the board
    touches pointed at the double."""
    from app.modules.core import feature_gate as FG

    monkeypatch.setattr("app.modules.core.router._uid_from_token",
                        lambda a: UID.get((a or "").strip()))
    monkeypatch.setattr("app.modules.core.router._memberships",
                        lambda client, uid: [
                            {"org_id": ORG_A, "auth_id": uid, "super_admin": uid == "uid-super",
                             "role": "admin", "is_active": True}])
    for dotted in ("app.modules.core.tenant_gates_api.get_supabase",
                   "app.modules.core.entitlements.get_supabase",
                   "app.modules.core.router.get_supabase"):
        monkeypatch.setattr(dotted, lambda: fake_supabase, raising=False)
    monkeypatch.setattr("app.modules.core.router.sb", lambda: fake_supabase, raising=False)

    fake_supabase.seed("storeops.tenants", [
        {"org_id": ORG_A, "name": "Tenant A", "is_active": True,
         "created_at": "2026-01-01T00:00:00Z", FG.COLUMN: "inherit"},
        {"org_id": ORG_B, "name": "Tenant B", "is_active": True,
         "created_at": "2026-11-11T00:00:00Z", FG.COLUMN: "gated"}])
    # Each tenant's OWN declared carrier — the fact the registries gate on.
    fake_supabase.seed("commcalc.carrier", [
        {"id": "c1", "org_id": ORG_A, "name": "Carrier One", "code": "boost", "is_default": True},
        {"id": "c2", "org_id": ORG_B, "name": "Carrier Two", "code": "total", "is_default": True}])
    fake_supabase.seed("commcalc.pos_profile", [])
    fake_supabase.seed("commcalc.ui_label_override", [])
    # The registry AS PRODUCTION HAS IT (mig 1014 applied): seeded from its own house mirror, so
    # `load_registry` reports ready. An EMPTY table is the unreadable case, and a registry that could
    # not be read must withhold nothing — pinned separately below.
    from app.modules.commcalc import connector_registry as _CR
    fake_supabase.seed("commcalc.connector_registry",
                       [{**r, "org_id": _CR.HOUSE_ORG, "is_active": True} for r in _CR.house_mirror()])
    fake_supabase.seed("commcalc.report_kind", [])
    fake_supabase.seed("storeops.billing_plan", [])
    fake_supabase.seed("storeops.tenant_modules", [])
    fake_supabase.seed("core.module_catalog", [])
    return fake_supabase


def _get(client, auth, qs=""):
    return client.get(f"/api/v1/core/tenant-gates{qs}", headers={"Authorization": auth})


def _put(client, auth, path, body):
    return client.put(f"/api/v1/core/tenant-gates{path}", headers={"Authorization": auth}, json=body)


# ── authority: cross-tenant by nature, so nobody but a super admin reaches it ─────────────────────
def test_an_ordinary_admin_cannot_read_the_board(api_client, wired):
    assert _get(api_client, PLAIN).status_code in (401, 403)


def test_an_ordinary_admin_cannot_write_a_posture_or_a_grant(api_client, wired):
    assert _put(api_client, PLAIN, "/posture", {"org": ORG_A, "posture": "gated"}).status_code in (401, 403)
    assert _put(api_client, PLAIN, "/grant",
                {"org": ORG_A, "kind": "module", "key": "closing", "grant": True}).status_code in (401, 403)


def test_an_unregistered_org_is_refused_rather_than_silently_created(api_client, wired):
    assert _put(api_client, SUPER, "/posture",
                {"org": "33333333-3333-3333-3333-333333333333", "posture": "gated"}).status_code == 404


def test_a_posture_the_vocabulary_does_not_name_is_refused(api_client, wired):
    assert _put(api_client, SUPER, "/posture", {"org": ORG_A, "posture": "all_access"}).status_code == 400


# ── the board ─────────────────────────────────────────────────────────────────────────────────────
def test_the_board_lists_every_tenant_with_its_own_posture(api_client, wired):
    r = _get(api_client, SUPER)
    assert r.status_code == 200, r.text
    body = r.json()
    by_org = {t["org_id"]: t for t in body["tenants"]}
    assert by_org[ORG_A]["posture"] == "inherit"
    assert by_org[ORG_B]["posture"] == "gated"
    # The owner's ask: a NEW tenant starts gated, and the board says so rather than leaving it to
    # whoever reads the code.
    assert body["posture_default_new_tenant"] == "gated"


def test_a_gated_tenant_is_offered_nothing_and_says_why(api_client, wired):
    from app.modules.core import feature_gate as FG
    body = _get(api_client, SUPER, f"?org={ORG_B}").json()
    assert body["posture"] == "gated"
    assert body["rows"], "the grid must still list what COULD be granted"
    assert body["summary"]["on"] == 0, body["summary"]
    assert {r["basis"] for r in body["rows"]} == {FG.BASIS_GATED_DEFAULT}


def test_an_inherit_tenant_is_offered_what_the_rules_already_gave_it(api_client, wired):
    """The no-change property, stated as what it is: on `inherit` the gate adds nothing of its own,
    so every verdict equals the platform rule (which legitimately withholds a module that belongs to
    another kind of business — mig 1020 — and that is the RULE, not the gate)."""
    from app.modules.core import feature_gate as FG
    body = _get(api_client, SUPER, f"?org={ORG_A}").json()
    assert body["posture"] == "inherit"
    mods = [r for r in body["rows"] if r["kind"] == "module"]
    assert mods
    assert all(r["allowed"] == r["by_rule"] for r in mods), \
        [r["key"] for r in mods if r["allowed"] != r["by_rule"]]
    assert all(r["basis"] == FG.BASIS_RULE for r in mods)


# ── a grant reaches ONE tenant ────────────────────────────────────────────────────────────────────
def test_a_grant_opens_one_thing_for_one_tenant_only(api_client, wired):
    before = _get(api_client, SUPER, f"?org={ORG_B}").json()
    key = next(r["key"] for r in before["rows"] if r["kind"] == "module")
    assert _put(api_client, SUPER, "/grant",
                {"org": ORG_B, "kind": "module", "key": key, "grant": True}).status_code == 200
    after = _get(api_client, SUPER, f"?org={ORG_B}").json()
    on = [r["key"] for r in after["rows"] if r["kind"] == "module" and r["allowed"]]
    assert on == [key], on
    # …and tenant A, which shares nothing but the platform, is untouched.
    other = _get(api_client, SUPER, f"?org={ORG_A}").json()
    assert all(r["allowed"] == r["by_rule"] for r in other["rows"] if r["kind"] == "module")


def test_gating_a_thing_off_beats_the_platform_rule(api_client, wired):
    from app.modules.core import feature_gate as FG
    body = _get(api_client, SUPER, f"?org={ORG_A}").json()
    key = next(r["key"] for r in body["rows"] if r["kind"] == "module" and r["allowed"])  # noqa: E501
    assert _put(api_client, SUPER, "/grant",
                {"org": ORG_A, "kind": "module", "key": key, "grant": False}).status_code == 200
    after = _get(api_client, SUPER, f"?org={ORG_A}").json()
    row = next(r for r in after["rows"] if r["kind"] == "module" and r["key"] == key)
    assert row["allowed"] is False and row["basis"] == FG.BASIS_DENY and row["decided"] is True


def test_clearing_a_decision_returns_the_thing_to_the_posture(api_client, wired):
    from app.modules.core import feature_gate as FG
    body = _get(api_client, SUPER, f"?org={ORG_A}").json()
    key = next(r["key"] for r in body["rows"] if r["kind"] == "module" and r["by_rule"])
    _put(api_client, SUPER, "/grant", {"org": ORG_A, "kind": "module", "key": key, "grant": False})
    assert _put(api_client, SUPER, "/grant",
                {"org": ORG_A, "kind": "module", "key": key, "grant": None}).status_code == 200
    row = next(r for r in _get(api_client, SUPER, f"?org={ORG_A}").json()["rows"]
               if r["kind"] == "module" and r["key"] == key)
    assert row["grant"] is None and row["basis"] == FG.BASIS_RULE and row["allowed"] is True


def test_a_kind_the_vocabulary_does_not_name_is_refused(api_client, wired):
    assert _put(api_client, SUPER, "/grant",
                {"org": ORG_A, "kind": "whatever", "key": "x", "grant": True}).status_code == 400


# ── THE ESCALATION PATH THE SECURITY PASS FOUND ──────────────────────────────────────────────────
# The grant home is `ui_label_override` scope 'cap', a table a TENANT admin can already write through
# `POST /commcalc/nav-labels`. That writer has refused to WIDEN a gated option since the owner's
# 2026-09-13 directive — but it enumerated the namespaces inline, and the enumeration had already
# fallen behind `connector:`. Making `module:` a grant namespace would therefore have handed any
# menu-layout admin a way to grant their own tenant a module its plan or business type excludes.
# These two tests are the regression: the refusal is derived from the gate's own namespaces now.
@pytest.mark.parametrize("label", ["show", "yes", "on", "1", "true", "SHOW"])
def test_a_synonym_for_yes_is_refused_too(api_client, monkeypatch, wired, label):
    """The first version of the fix compared the label to the one word `show`; the security pass
    pointed out that `grant_state` also reads yes / on / 1 / true as a grant. The refusal asks what
    the value MEANS now, so every spelling of it is one refusal."""
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: wired, raising=False)
    monkeypatch.setattr("app.modules.commcalc.router._require_menu_layout_admin",
                        lambda auth, org: {"super_admin": False, "org_id": ORG_A}, raising=False)
    r = api_client.post(f"/api/v1/commcalc/nav-labels?org_id={ORG_A}",
                        headers={"Authorization": PLAIN},
                        json={"scope": "cap", "key": "module:account", "label": label})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("key", ["module:account", "connector:anything", "kind:anything"])
def test_a_tenant_admin_cannot_grant_itself_a_gated_thing(api_client, monkeypatch, wired, key):
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: wired, raising=False)
    monkeypatch.setattr("app.modules.commcalc.router._require_menu_layout_admin",
                        lambda auth, org: {"super_admin": False, "org_id": ORG_A}, raising=False)
    r = api_client.post(f"/api/v1/commcalc/nav-labels?org_id={ORG_A}",
                        headers={"Authorization": PLAIN},
                        json={"scope": "cap", "key": key, "label": "show"})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("key", ["module:account", "connector:anything"])
def test_a_tenant_admin_may_still_narrow_its_own_reach(api_client, monkeypatch, wired, key):
    """The asymmetry IS the safety property: hiding can only ever show less, so it stays theirs."""
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: wired, raising=False)
    monkeypatch.setattr("app.modules.commcalc.router._require_menu_layout_admin",
                        lambda auth, org: {"super_admin": False, "org_id": ORG_A}, raising=False)
    r = api_client.post(f"/api/v1/commcalc/nav-labels?org_id={ORG_A}",
                        headers={"Authorization": PLAIN},
                        json={"scope": "cap", "key": key, "label": "hide"})
    assert r.status_code == 200, r.text


# ── the owner's own surface: the connector-keyed report-pull rows ─────────────────────────────────
def test_the_report_pull_map_is_connector_scoped_per_tenant(api_client, monkeypatch, wired):
    """THE OWNER'S CASE, end to end. The house defaults include rows belonging to a connector that
    applies to one carrier only. The tenant that declares the OTHER carrier must not be shown them,
    and must be TOLD they exist rather than left to wonder."""
    from app.modules.commcalc import connector_registry as CR
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: wired, raising=False)

    vendor_key = next(r["key"] for r in CR.house_mirror() if "total" in (r["applies_to_carrier"] or []))
    rows = _get(api_client, SUPER, f"?org={ORG_B}").json()["rows"]
    conn = {r["key"]: r for r in rows if r["kind"] == "connector"}
    assert vendor_key in conn, "the board must list it so it can be granted"

    r = api_client.get(f"/api/v1/commcalc/report-pull-map?org_id={ORG_A}",
                       headers={"Authorization": SUPER})
    assert r.status_code == 200, r.text
    body = r.json()
    shown = {s.get("processor") for s in body["reports"]}
    assert vendor_key not in shown, f"the other carrier's connector leaked: {shown}"
    assert any(w.get("processor") == vendor_key for w in body.get("withheld") or []), \
        "a withheld row must be reported, never silently dropped"


def test_a_registry_that_could_not_be_read_withholds_nothing(api_client, monkeypatch, wired):
    """THE DEGRADE DIRECTION, pinned. `connector_registry.load_registry` reports ready=False when the
    table is absent or unreadable and its own house mirror answered instead. A verdict derived from a
    mirror is not a decision a super admin made, so it must never hide a tenant's own configuration —
    the same direction the posture takes (an unknown posture resolves to `inherit`).

    This is not a hypothetical: it is how the MA cost-mapping panel and the manual-upload catalog
    broke when the gate first shipped, on a database where mig 1014 had not run.
    """
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: wired, raising=False)
    wired.seed("commcalc.connector_registry", [])          # nothing readable → the mirror answers

    r = api_client.get(f"/api/v1/commcalc/report-pull-map?org_id={ORG_A}",
                       headers={"Authorization": SUPER})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["withheld"] == [], "an unreadable registry said nothing; it must withhold nothing"
    assert body["reports"], "and the tenant still sees its own configuration"
