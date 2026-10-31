"""Integration: a SELF-scoped rep is actually scoped (index §CORE-30, found by the same question).

THE THIRD INSTANCE OF ONE CLASS. `commcalc/router._caller_rep_keys` is the platform's one answer to
"which rep rows are mine" — its own docstring says so, and five commission surfaces call it. It
reads the signed-in user and their roster name through `sb()`, which in THIS module is the plain
client: the `public` schema. `app_users` and `employees` are `storeops` relations (migration 003).

So the read raised `PGRST205` on every call, the function's outer `except Exception: return None`
swallowed it, and **None is this function's word for "not self-scoped — unrestricted"**. A
`sales_rep` was therefore handed every rep's commission rows. There are live `sales_rep` accounts
(`storeops.app_users`, checked 2026-10-31).

Why it hid: `public.employees` really does exist — a stale four-row copy that predates the storeops
schema — so the public read LOOKED plausible, and the module docstring still claims "(public
schema)". `public.app_users` never existed at all.

This is the same wrong fact as the Run Calculation's `commcalc.employees`: **a relation's schema was
assumed at the call site.** Fixing one and not the others is the patchwork the house forbids, so all
three sites dereference `core.relation_home` in one commit, and the lock fails the build on a fourth.
"""
import pytest

ORG = "00000000-0000-0000-0000-000000000001"
UID = "996c23c9-8939-4e87-bf23-0befaea912bf"
AUTH = "Bearer a-token-the-stub-resolves"


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """RBAC on, one sales_rep, that rep on the storeops roster — all in the schemas that hold them."""
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: fake_supabase)
    monkeypatch.setattr("app.modules.storeops.router.sb",
                        lambda: fake_supabase.schema("storeops"))
    monkeypatch.setattr("app.modules.core.router._uid_from_token", lambda _a: UID)
    fake_supabase.seed("storeops.app_config", [{"id": 1, "rbac_enabled": True}])
    fake_supabase.seed("storeops.app_users", [
        {"id": "u1", "org_id": ORG, "auth_id": UID, "role": "sales_rep", "employee_id": "E229"}])
    fake_supabase.seed("storeops.employees", [
        {"id": "e1", "org_id": ORG, "employee_id": "E229", "name": "Ali, Moaz"}])
    # The stale legacy copy really is there in production, holding somebody else entirely. A read
    # that lands here instead of storeops must not be able to look like a success.
    fake_supabase.seed("public.employees", [
        {"id": 4, "org_id": ORG, "employee_id": "E004", "name": "Obed"}])
    return fake_supabase


def test_a_sales_rep_is_scoped_to_their_own_rows_and_not_handed_everyones(wired):
    from app.modules.commcalc.router import _caller_rep_keys
    keys = _caller_rep_keys(AUTH, ORG)
    assert keys is not None, (
        "None means UNRESTRICTED here — a self-scoped rep would see every rep's commission")
    assert "ALI, MOAZ" in keys


def test_the_identity_never_comes_from_the_stale_public_copy(wired):
    from app.modules.commcalc.router import _caller_rep_keys
    keys = _caller_rep_keys(AUTH, ORG) or set()
    assert "OBED" not in keys
    asked = {key for key, _f in wired.reads}
    assert "public.app_users" not in asked
    assert "public.employees" not in asked
    assert "storeops.app_users" in asked


def test_rbac_off_is_still_unrestricted(wired):
    """The legitimate None: enforcement off. Unchanged by the fix."""
    wired.seed("storeops.app_config", [{"id": 1, "rbac_enabled": False}])
    from app.modules.commcalc.router import _caller_rep_keys
    assert _caller_rep_keys(AUTH, ORG) is None


def test_a_rep_with_no_roster_row_sees_nothing_rather_than_everything(wired):
    """The function's own contract: an empty set, never None, when the person cannot be mapped."""
    wired.seed("storeops.employees", [])
    from app.modules.commcalc.router import _caller_rep_keys
    assert _caller_rep_keys(AUTH, ORG) == set()
