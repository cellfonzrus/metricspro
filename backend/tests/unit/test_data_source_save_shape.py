"""A login SAVE must not fail because the database is behind the code.

`_SOURCE_FIELDS` grows with every additive migration and the settings form posts every field this
build knows about, so one column whose migration has not been applied yet would make PostgREST
refuse the WHOLE row (PGRST204) and break editing EVERY login for EVERY tenant between a merge and
its migration. The arithmetic is dereferenced from `payables/write_shape` (index §PAYABLES-01), and
what could not be stored is NAMED back rather than dropped in silence.
"""
import pytest

from app.modules.commcalc import router as r

pytestmark = pytest.mark.unit

OLD_SHAPE = {"id": "s1", "org_id": r.ORG_ID, "processor": "epay", "label": "whole company",
             "username": "u", "enabled": True}
NEW_SHAPE = dict(OLD_SHAPE, store_code=None, history_from=None, history_chunk_days=None)


def _wire(monkeypatch, fake):
    monkeypatch.setattr(r, "sb", lambda: fake)
    monkeypatch.setattr(r, "require_org", lambda org_id: None)
    monkeypatch.setattr(r, "_require_import_admin", lambda authorization, org_id: None)


def _body(**kw):
    return r.SaveDataSourceIn(**kw)


def test_a_pending_migration_does_not_refuse_the_whole_save(monkeypatch, fake_supabase):
    """The declaration cannot land, but the rest of the login still saves."""
    fake_supabase.seed("commcalc.data_source", [dict(OLD_SHAPE)])
    _wire(monkeypatch, fake_supabase)
    out = r.save_data_source(_body(id="s1", label="renamed", store_code="B-1115",
                                   history_from="2026-01-01"), org_id=r.ORG_ID)
    assert out["ok"] is True
    row = [x for x in fake_supabase.rows("commcalc.data_source") if x["id"] == "s1"][0]
    assert row["label"] == "renamed"
    assert "store_code" not in row


def test_what_could_not_be_stored_is_named_not_silently_dropped(monkeypatch, fake_supabase):
    fake_supabase.seed("commcalc.data_source", [dict(OLD_SHAPE)])
    _wire(monkeypatch, fake_supabase)
    out = r.save_data_source(_body(id="s1", store_code="B-1115", history_from="2026-01-01",
                                   history_chunk_days=7), org_id=r.ORG_ID)
    assert out["unstorable"] == ["history_chunk_days", "history_from", "store_code"]
    assert "1116" not in out["note"]            # the note names COLUMNS, not a migration number
    for col in ("store_code", "history_from", "history_chunk_days"):
        assert col in out["note"]


def test_once_the_migration_is_applied_the_declaration_lands_and_nothing_is_reported(
        monkeypatch, fake_supabase):
    fake_supabase.seed("commcalc.data_source", [dict(NEW_SHAPE)])
    _wire(monkeypatch, fake_supabase)
    out = r.save_data_source(_body(id="s1", store_code="B-1115", history_from="2026-01-01",
                                   history_chunk_days=7), org_id=r.ORG_ID)
    assert "unstorable" not in out and "note" not in out
    row = [x for x in fake_supabase.rows("commcalc.data_source") if x["id"] == "s1"][0]
    assert row["store_code"] == "B-1115"
    assert str(row["history_from"]) == "2026-01-01"
    assert row["history_chunk_days"] == 7


def test_an_empty_table_cannot_be_inspected_so_nothing_is_dropped(monkeypatch, fake_supabase):
    """"I could not look" is not "it has no columns" — with nothing to lose the save proceeds whole,
    which is the contract `write_shape.live_columns` states."""
    fake_supabase.seed("commcalc.data_source", [])
    _wire(monkeypatch, fake_supabase)
    out = r.save_data_source(_body(processor="processor_backoffice", store_code="B-1115"),
                             org_id=r.ORG_ID)
    assert "unstorable" not in out
    row = fake_supabase.rows("commcalc.data_source")[0]
    assert row["store_code"] == "B-1115"
