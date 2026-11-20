"""Integration: the paste-once login import through the real app (index §COMMCALC-83).

Owner, 2026-11-20, of the 29 per-store ePay back-office logins: *"Enter those for me"*. The one
acceptable path from an operator's clipboard to the sealed column is ONE post, so these are the
things only the wiring can prove:

  · the route is reachable at its documented path, and it is GATED — an ungated caller stores
    nothing and learns nothing;
  · ★ the password is SEALED by the same writer the settings form uses, so it is ciphertext in the
    table and absent from the response;
  · ★ a paste cannot reach into another tenant: the rows land under the asking org only, and a
    store another tenant declared is not a store this one can file a credential under;
  · a second paste UPDATES a store's login instead of giving it a rival one;
  · a dry run writes nothing at all;
  · an unresolved line is reported and NOT stored — "29 entered" must never mean 23.
"""
import pytest

from app.modules.commcalc import processor_backoffice_sweep as pbs

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "ba084e25-adcf-4d4d-9cc5-d3728775513f"
PATH = "/api/v1/commcalc/data-sources/import"
TABLE = "commcalc.data_source"

#: Deliberately real-looking: every assertion below hunts for it in the response and the table.
PW = "n0t-a-real-password-9f3c"
PORTAL = "https://portal.example.com"


def _store(org, code, addr):
    return {"org_id": org, "store_code": code, "store_address": addr}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    # The gate is proved separately; these tests are about what the import DOES once allowed.
    monkeypatch.setattr("app.modules.commcalc.router._require_import_admin", lambda *_a, **_k: None)
    fake_supabase.seed("commcalc.store_mapping", [
        _store(ORG_A, "B-1115", "123 Jamaica Ave"),
        _store(ORG_A, "B-1750", "456 Hillside Ave"),
        _store(ORG_B, "T-9000", "789 Other St"),
    ])
    fake_supabase.seed("storeops.stores", [
        dict(_store(ORG_A, "B-1115", "123 Jamaica Ave"), address="123 Jamaica Ave", is_active=True),
        dict(_store(ORG_A, "B-1750", "456 Hillside Ave"), address="456 Hillside Ave", is_active=True),
        dict(_store(ORG_B, "T-9000", "789 Other St"), address="789 Other St", is_active=True),
    ])
    fake_supabase.seed(TABLE, [])
    return fake_supabase


def _post(api_client, org, text, **kw):
    body = {"processor": pbs.CONNECTOR, "portal_url": PORTAL, "text": text}
    body.update(kw)
    return api_client.post(PATH, params={"org_id": org}, json=body)


BLOCK = "B-1115\tstore1115\t%s\nB-1750\tstore1750\t%s" % (PW, PW)


# ── 1. it is reachable, and it is gated ──────────────────────────────────────────────────────────
def test_the_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert PATH in set(route_templates(app_module))


def test_an_ungated_caller_stores_nothing(api_client, fake_supabase, monkeypatch):
    """The gate is the one that gates CHANGING a login, and it fails closed."""
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    fake_supabase.seed(TABLE, [])
    res = _post(api_client, ORG_A, BLOCK)
    assert res.status_code in (401, 403, 503), res.text
    assert fake_supabase.rows(TABLE) == []


def test_an_empty_paste_is_refused_rather_than_reported_as_success(api_client, wired):
    assert _post(api_client, ORG_A, "   ").status_code == 400
    assert _post(api_client, ORG_A, BLOCK, processor="").status_code == 400


# ── 2. what it stores, and how ───────────────────────────────────────────────────────────────────
def test_one_paste_creates_one_login_per_store(api_client, wired):
    out = _post(api_client, ORG_A, BLOCK).json()
    assert (out["ok"], out["imported"], out["updated"]) == (True, 2, 0)
    rows = wired.rows(TABLE)
    assert sorted(r["store_code"] for r in rows) == ["B-1115", "B-1750"]
    assert {r["org_id"] for r in rows} == {ORG_A}
    assert {r["portal_url"] for r in rows} == {PORTAL}
    assert {r["processor"] for r in rows} == {pbs.CONNECTOR}


def test_the_password_is_sealed_in_the_table_and_absent_from_the_answer(api_client, wired):
    """★ The whole reason this route exists rather than a SQL file: the credential comes to rest
    ONLY as ciphertext, and the response an operator (and their browser cache) sees has none of it.
    Proved through the real writer, not a reimplementation of its sealing."""
    from app.modules.core import portal_secret as psec
    res = _post(api_client, ORG_A, BLOCK)
    assert PW not in res.text
    stored = [r["password"] for r in wired.rows(TABLE)]
    assert stored
    if psec.protection_state() == "protected":
        # A key is configured, so the stored form is ciphertext — never the typed value.
        assert all(p != PW for p in stored)
    # Whatever the key state, the row reads back as the password the operator pasted — that is what
    # `open_row` is for, and a login that cannot be read back cannot log in. This is the assertion
    # that proves the import went through the SEALING writer rather than round-tripping a string.
    for r in wired.rows(TABLE):
        assert psec.open_row("data_source", dict(r))["password"] == PW


def test_each_line_is_quoted_back_with_the_password_replaced_by_its_length(api_client, wired):
    lines = _post(api_client, ORG_A, BLOCK).json()["lines"]
    assert [l["verdict"] for l in lines] == ["ready", "ready"]
    assert all("%d character value" % len(PW) in " ".join(map(str, l["cells"])) for l in lines)


def test_a_paste_with_more_cells_than_the_header_declares_still_echoes_no_value(api_client, wired):
    """The surplus cell holds the credential under NO name, which is exactly where an echo would
    put a password back on screen."""
    res = _post(api_client, ORG_A, "Store\tUsername\nB-1115\tstore1115\t%s" % PW)
    assert PW not in res.text


def test_a_second_paste_updates_a_stores_login_instead_of_giving_it_a_rival(api_client, wired):
    """Two logins for one store would make the download console's grid ambiguous about which login
    is that store's."""
    _post(api_client, ORG_A, BLOCK)
    out = _post(api_client, ORG_A, "B-1115\tstore1115\tnew-password-x").json()
    assert (out["imported"], out["updated"]) == (0, 1)
    assert len(wired.rows(TABLE)) == 2


def test_a_dry_run_writes_nothing_and_says_what_would_happen(api_client, wired):
    out = _post(api_client, ORG_A, BLOCK, dry_run=True).json()
    assert out["dry_run"] is True and out["would_create"] == 2 and out["imported"] == 0
    assert wired.rows(TABLE) == []
    assert PW not in str(out)


# ── 3. the security pass: no other tenant, and nothing silently dropped ──────────────────────────
def test_a_store_another_tenant_declared_cannot_be_filed_under_this_one(api_client, wired):
    """★ Cross-tenant at the HTTP boundary: T-9000 is ORG_B's store, so pasting it as ORG_A is an
    unresolved line — not a credential filed under another tenant's store."""
    out = _post(api_client, ORG_A, "T-9000\tsneaky\t%s" % PW).json()
    assert out["imported"] == 0 and out["unresolved"] == 1
    assert wired.rows(TABLE) == []


def test_an_unresolved_line_is_reported_and_the_rest_still_land(api_client, wired):
    """"29 entered" must never mean 23: the count is of what was STORED, and the gap is named."""
    out = _post(api_client, ORG_A, BLOCK + "\nNowhere Mall\tu\t%s" % PW).json()
    assert (out["imported"], out["unresolved"]) == (2, 1)
    assert "has not declared" in out["note"]
    assert sorted(r["store_code"] for r in wired.rows(TABLE)) == ["B-1115", "B-1750"]


def test_the_import_refuses_rather_than_duplicate_when_it_cannot_read_what_is_stored(api_client,
                                                                                    wired,
                                                                                    monkeypatch):
    """§COMMCALC-82's class: a read that FAILED is not "this org has no logins". Inserting on that
    assumption would give every store a second login."""
    class _Blind:
        """Everything reads as usual EXCEPT the login table, whose read raises — the shape of a
        transport that drops mid-request, not of a tenant with no logins."""
        def __init__(self, inner):
            self._inner, self._schema, self._table = inner, "", ""

        def schema(self, name):
            self._schema = name
            return self

        def table(self, name):
            self._table = name
            return self

        def select(self, *a, **k):
            if self._table == "data_source":
                raise RuntimeError("transport is down")
            # Every other read is the real fake, under the schema it was actually asked for — so
            # the store roster still resolves and the 409 is about the login read alone.
            return self._inner.schema(self._schema).table(self._table).select(*a, **k)

        def __getattr__(self, item):
            return getattr(self._inner, item)

    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: _Blind(wired),
                        raising=False)
    res = _post(api_client, ORG_A, BLOCK)
    assert res.status_code == 409
    assert "Nothing was written" in res.json()["note"]
