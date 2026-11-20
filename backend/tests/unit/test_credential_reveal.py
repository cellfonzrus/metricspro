"""SHOWING A STORED CREDENTIAL IS AN AUDITED EVENT — or it does not happen (index §CORE-45).

Written RED. Owner, 2026-11-20, chose "show it on screen" for the per-store download screen's
passwords, which is the first time this platform hands a stored credential back to a browser. The
rules this proves are the ones that must not become per-screen:

  · a reveal that could not be RECORDED is refused, not served unrecorded;
  · only a column the credential registry calls a credential can be revealed;
  · a row belonging to another organisation is not found, whatever the caller passes;
  · the audit row never carries the secret — by construction, not by care.
"""
import pytest

from app.modules.core import credential_reveal as cr

pytestmark = pytest.mark.unit

ORG = "00000000-0000-0000-0000-000000000001"
OTHER = "ba084e25-adcf-4d4d-9cc5-d3728775513f"
SECRET = "n0t-a-real-password-9f3c"
ROW = {"id": "src-1", "org_id": ORG, "password": SECRET, "username": "someone"}


class _Writes:
    """A client that accepts the audit insert and remembers what it was handed."""

    def __init__(self):
        self.rows = []

    def schema(self, name):
        self.schema_name = name
        return self

    def table(self, name):
        self.table_name = name
        return self

    def insert(self, row):
        self.rows.append(row)
        return self

    def execute(self):
        return type("R", (), {"data": [self.rows[-1]]})()


class _Refuses(_Writes):
    def insert(self, row):
        raise RuntimeError("relation does not exist")


def test_a_reveal_that_can_be_recorded_returns_the_credential():
    c = _Writes()
    out = cr.reveal(c, ORG, relation="data_source", column="password", row=ROW, who="a@b.c")
    assert out["ok"] is True and out["value"] == SECRET
    assert (c.schema_name, c.table_name) == (cr.SCHEMA, cr.TABLE)


def test_a_reveal_that_cannot_be_recorded_is_refused_rather_than_served_unrecorded():
    """The defect this exists to prevent: an un-run migration silently becoming the normal path."""
    out = cr.reveal(_Refuses(), ORG, relation="data_source", column="password", row=ROW, who="a@b.c")
    assert out["ok"] is False and out["value"] == ""
    assert "1128" in out["note"]


def test_the_audit_row_records_who_asked_and_never_the_secret():
    c = _Writes()
    cr.reveal(c, ORG, relation="data_source", column="password", row=ROW, who="a@b.c",
              context="per-store download screen")
    row = c.rows[-1]
    assert row["revealed_to"] == "a@b.c" and row["row_id"] == "src-1"
    assert row["relation"] == "data_source" and row["column"] == "password"
    assert SECRET not in " ".join(str(v) for v in row.values())


def test_only_a_registered_credential_column_can_be_revealed():
    """A caller cannot reach an arbitrary column through this door."""
    c = _Writes()
    out = cr.reveal(c, ORG, relation="data_source", column="username", row=ROW, who="a@b.c")
    assert out["ok"] is False and c.rows == []
    assert cr.is_credential("data_source", "password") is True
    assert cr.is_credential("data_source", "username") is False


def test_another_tenants_row_is_not_found_whatever_the_caller_passes():
    """The org check is on the ROW, not on a filter the caller promises it applied."""
    c = _Writes()
    out = cr.reveal(c, OTHER, relation="data_source", column="password", row=ROW, who="a@b.c")
    assert out["ok"] is False and out["note"] == cr.NOT_FOUND and c.rows == []


def test_a_login_with_no_password_stored_says_so_rather_than_showing_an_empty_box():
    c = _Writes()
    out = cr.reveal(c, ORG, relation="data_source", column="password",
                    row={**ROW, "password": ""}, who="a@b.c")
    assert out["ok"] is False and out["note"] == cr.EMPTY and c.rows == []


def test_a_missing_row_is_not_found_rather_than_an_exception():
    c = _Writes()
    assert cr.reveal(c, ORG, relation="data_source", column="password", row=None,
                     who="a@b.c")["note"] == cr.NOT_FOUND
