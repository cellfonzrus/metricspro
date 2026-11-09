"""Unit: a setup gap carries the STEPS that close it, the page that closes it, and who may.

OWNER ASK 2026-11-09 (verbatim): *"to fix any such problem going forward shpow it on the screen hpow
the user can fix teh problem so it is taken care of right away and if it is not under tehoer role
permissionsm, have them send a link ot thier manager from the chat menu so it can be fixed"*.

THE CLASS, NOT THE INSTANCE (CLAUDE.md "A fix is a DESIGN fix"). The instance was a financing row
reading "⚠️ Detection not configured" with nothing to press. The class is: **the platform knows a
check could not run, and tells the one person on the screen nothing they can act on.** Dozens of
surfaces say "not configured" / "not measured" / "needs setup"; every one of them is a dead end, and
wording them one at a time is the patchwork the house rules forbid.

So the fact a caller needs — *what closes this gap, where, and may I* — lives in ONE registry
(`app.modules.core.setup_fix`) and every surface READS it. These tests are the contract:

  §A  the registry is well formed: every gap names its steps, its page and its gate
  §B  the gate is answered by the HOUSE one home (`self_lockout.can_edit_setting`), never a copy
  §C  a caller who may fix it is handed the page; one who may not is handed PEOPLE
  §D  the people are DERIVED from the tenant's own roles — manager first, asker never themselves
  §E  nobody in the company can close a platform gap, and the guide says so rather than offering a
      button that would 403
  §F  MULTI-TENANT (project rule 2026-10-30): three tenant shapes, and one tenant's roles or people
      can never reach another tenant's guide
  §G  the customer-facing words name no migration, no table and no carrier (RULE TWO + §19.36)
"""
import pytest

from app.modules.core import setup_fix as sf
from app.modules.core import self_lockout

HOUSE = "00000000-0000-0000-0000-000000000001"

# Three tenant shapes. The third is what every tenant looks like on the day this ships.
ROLES_FULL = {                     # a tenant with a real role ladder
    "admin": {"scope": "all"},
    "dm": {"scope": "market", "settings": {"targets": True}},
    "rep": {"scope": "self"},
}
ROLES_GRANTED = {                  # a tenant that granted ONE area to a manager and nothing else
    "owner": {"scope": "all", "settings": {"targets": False}},
    "manager": {"scope": "store", "settings": {"targets": True}},
}
ROLES_BARE = {"rep": {"scope": "self"}}   # nobody can edit anything


def _members(role_of):
    return [{"employee_id": eid, "name": name, "role": role}
            for eid, name, role in role_of]


# ── §A the registry is well formed ───────────────────────────────────────────────────────────────
def test_A1_there_is_a_registry_and_it_is_not_empty():
    assert sf.FIX_KEYS, "no setup gap is declared — the registry is the whole mechanism"
    assert len(set(sf.FIX_KEYS)) == len(sf.FIX_KEYS), "a key is declared twice"


@pytest.mark.parametrize("key", sf.FIX_KEYS)
def test_A2_every_gap_names_its_steps_its_gate_and_its_consequence(key):
    e = sf.fix(key)
    assert e["title"].strip(), f"{key}: no title"
    assert e["consequence"].strip(), f"{key}: nothing says what the surface cannot answer"
    assert len(e["steps"]) >= 1, f"{key}: a gap with no steps is the dead end this replaces"
    for s in e["steps"]:
        assert s.strip() and s[0].isupper(), f"{key}: a step must read as a sentence: {s!r}"
    assert e["gate"], f"{key}: no gate — nothing says who may close it"


@pytest.mark.parametrize("key", sf.FIX_KEYS)
def test_A3_a_gap_a_company_can_close_names_the_page_that_closes_it(key):
    e = sf.fix(key)
    if e["gate"] == sf.PLATFORM:
        assert not e["page"], f"{key}: a platform gap must offer no in-app page"
    else:
        assert str(e["page"] or "").startswith("/"), f"{key}: no page to send anyone to"


def test_A4_an_unknown_key_is_not_invented():
    assert sf.fix("no_such_gap") is None
    assert sf.guide("no_such_gap", can_fix=True) is None


# ── §B the gate is the house one home, never a copy ──────────────────────────────────────────────
def test_B1_a_company_gap_asks_about_a_settings_area_or_degrades_to_admin():
    for key in sf.FIX_KEYS:
        e = sf.fix(key)
        area = sf.gate_area(e)
        if e["gate"] == sf.PLATFORM:
            assert area is None
        else:
            assert isinstance(area, str) and area


def test_B2_the_admin_gate_is_answered_by_the_house_check_and_means_full_scope_admin():
    area = sf.gate_area({"gate": sf.ADMIN})
    assert self_lockout.can_edit_setting({"role": "admin", "perms": {}}, area) is True
    assert self_lockout.can_edit_setting({"role": "rep", "perms": {"scope": "self"}}, area) is False
    assert self_lockout.can_edit_setting({"super_admin": True}, area) is True


def test_B3_a_declared_gate_is_one_of_the_three_kinds_and_nothing_else():
    """That a declared area is a GRANTABLE settings area, and that every page is a real route, is
    asserted by the lock (`backend/harness_setup_fix.py` §B/§C) — it reads both registries out of
    their own source, so this unit stays pure and imports no app module."""
    for key in sf.FIX_KEYS:
        gate = sf.fix(key)["gate"]
        assert gate == sf.PLATFORM or gate == sf.ADMIN or (isinstance(gate, str) and "_" in gate or gate.isalpha())


# ── §C may-fix gets a page; may-not gets people ──────────────────────────────────────────────────
def _company_key():
    for k in sf.FIX_KEYS:
        if sf.fix(k)["gate"] != sf.PLATFORM:
            return k
    raise AssertionError("no company-closable gap declared")


def test_C1_a_permitted_caller_is_told_to_fix_it_now():
    g = sf.guide(_company_key(), can_fix=True)
    assert g["can_fix"] is True
    assert g["page"].startswith("/")
    assert g["ask"] is None, "somebody who can fix it is not asked to bother anybody"
    assert g["steps"]


def test_C2_a_refused_caller_keeps_the_steps_and_is_handed_someone_to_ask():
    key = _company_key()
    g = sf.guide(key, can_fix=False,
                 fixers=[{"employee_id": "E1", "name": "Dana Admin", "role": "admin"}],
                 asker_name="Rab Rep")
    assert g["can_fix"] is False
    assert g["steps"] == sf.fix(key)["steps"], "the steps are shown either way — the person can relay them"
    assert [r["employee_id"] for r in g["ask"]["recipients"]] == ["E1"]
    assert g["ask"]["message"]
    assert g["page"] in g["ask"]["message"], "the message must carry the deep link to the exact fix"
    assert g["support"] is False


def test_C3_a_refused_caller_with_nobody_to_ask_is_sent_to_support_not_to_a_dead_button():
    g = sf.guide(_company_key(), can_fix=False, fixers=[], asker_name="Rab Rep")
    assert g["ask"] is None
    assert g["support"] is True


# ── §D the people are derived from the tenant's own roles ────────────────────────────────────────
def test_D1_the_roles_that_can_close_a_gap_are_derived_not_declared():
    assert sf.fixer_roles(ROLES_FULL, "targets") == ["admin", "dm"]
    # an explicit deny beats full scope (the house precedence, not a second copy of it)
    assert sf.fixer_roles(ROLES_GRANTED, "targets") == ["manager"]
    assert sf.fixer_roles(ROLES_BARE, "targets") == []


def test_D2_the_manager_comes_first_and_the_asker_is_never_their_own_recipient():
    members = _members([("E9", "Zoe Admin", "admin"), ("E2", "Al Admin", "admin"),
                        ("E5", "My Manager", "admin"), ("E1", "Rab Rep", "rep")])
    out = sf.ask_recipients(["admin"], members, manager_ids=["E5"], asker_id="E2")
    assert [r["employee_id"] for r in out] == ["E5", "E9"], "manager first, then by name, never the asker"
    assert out[0]["is_manager"] is True and out[1]["is_manager"] is False


def test_D3_a_manager_who_cannot_close_the_gap_is_not_offered_as_the_fixer():
    members = _members([("E5", "My Manager", "rep"), ("E2", "Al Admin", "admin")])
    out = sf.ask_recipients(["admin"], members, manager_ids=["E5"], asker_id="E1")
    assert [r["employee_id"] for r in out] == ["E2"]


def test_D4_the_recipient_list_is_capped_so_one_question_never_pages_the_whole_company():
    members = _members([(f"E{i}", f"P{i:02d} Admin", "admin") for i in range(30)])
    out = sf.ask_recipients(["admin"], members, manager_ids=[], asker_id=None)
    assert 1 <= len(out) <= sf.MAX_RECIPIENTS


# ── §E a platform gap offers no button that would 403 ────────────────────────────────────────────
def test_E1_a_platform_gap_is_support_shaped_for_everyone_including_an_admin():
    plat = [k for k in sf.FIX_KEYS if sf.fix(k)["gate"] == sf.PLATFORM]
    assert plat, "at least one gap is genuinely ours to close, and must say so"
    for key in plat:
        for can in (True, False):
            g = sf.guide(key, can_fix=can,
                         fixers=[{"employee_id": "E1", "name": "Dana Admin", "role": "admin"}])
            assert g["can_fix"] is False, f"{key}: no tenant can close a platform gap"
            assert g["page"] is None and g["ask"] is None
            assert g["support"] is True


# ── §F multi-tenant ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("roles,expect", [(ROLES_FULL, ["admin", "dm"]),
                                          (ROLES_GRANTED, ["manager"]),
                                          (ROLES_BARE, [])])
def test_F1_each_tenant_shape_is_answered_against_its_own_roles(roles, expect):
    assert sf.fixer_roles(roles, "targets") == expect


def test_F2_a_tenant_with_no_fixer_is_told_so_rather_than_shown_another_tenants_admin():
    g = sf.guide(_company_key(), can_fix=False,
                 fixers=sf.ask_recipients(sf.fixer_roles(ROLES_BARE, "targets"),
                                          _members([("E1", "Rab Rep", "rep")]), asker_id=None))
    assert g["ask"] is None and g["support"] is True


def test_F3_a_member_of_another_tenant_cannot_appear_as_a_recipient():
    """`ask_recipients` is handed ONE tenant's members on purpose: it has no way to name anybody
    else, which is what keeps a cross-tenant leak out of this surface by construction."""
    members = _members([("E2", "Al Admin", "admin")])
    out = sf.ask_recipients(["admin"], members, manager_ids=["FOREIGN"], asker_id=None)
    assert [r["employee_id"] for r in out] == ["E2"]


# ── §G the words are customer-facing ─────────────────────────────────────────────────────────────
def test_G1_no_step_names_a_migration_a_table_or_the_sql_editor():
    from app.core import setup_notice
    for key in sf.FIX_KEYS:
        e = sf.fix(key)
        for text in [e["title"], e["consequence"], *e["steps"]]:
            assert not setup_notice.is_setup_internal(text), f"{key}: internal setup fact in {text!r}"
            assert not setup_notice.is_system_internal(text), f"{key}: a database error in {text!r}"


def test_G2_no_step_names_a_carrier_a_tenant_or_a_product_RULE_TWO():
    banned = ("boost", "luxelink", "cellfonz", "vzone", "total wireless", "acima", "edge ",
              "metro", "t-mobile", "verizon", "at&t")
    for key in sf.FIX_KEYS:
        e = sf.fix(key)
        blob = " ".join([e["title"], e["consequence"], *e["steps"]]).lower()
        for word in banned:
            assert word not in blob, f"{key}: RULE TWO — {word!r} is config, never code"


def test_G3_the_message_to_a_manager_says_who_asked_and_what_for():
    key = _company_key()
    msg = sf.ask_message(sf.fix(key), asker_name="Rab Rep", origin="Financing report")
    assert "Rab Rep" in msg and "Financing report" in msg
    assert sf.fix(key)["page"] in msg
    for step in sf.fix(key)["steps"]:
        assert step in msg, "the manager gets the same steps, not a vaguer version"
