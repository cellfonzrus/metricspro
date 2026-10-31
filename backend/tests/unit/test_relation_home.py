"""Unit: WHICH SCHEMA HOLDS THIS RELATION is one fact with one home (index §CORE-29).

THE DEFECT THIS LOCKS. `router._calc_inputs.fetch(table)` builds every read as
`client.schema('commcalc').table(table)` — the schema is hard-coded and the table is an argument.
Two of the names it is called with, `employees` and `stores`, are `storeops` relations. While that
reader swallowed its exceptions the two reads returned [] and the calculation ran on nothing; when
`core.feed_read` was made to RAISE rather than mistake a failed read for a small feed, the same two
reads began killing the whole Run Calculation at its first line. Measured on production 2026-10-31:
LuxeLink `error` for June-October and Cellfonz R Us for July-October, every one of them
*"Could not find the table 'commcalc.employees' in the schema cache"*.

THE CLASS, NOT THE INSTANCE. The instance is "employees is read from the wrong schema". The class is
**a reader that takes a table NAME cannot know which schema holds it, and nothing checked.** So the
fact gets one home (`core.relation_home`), every such reader dereferences it, and the home is
VERIFIED against `database/migrations/` — the schema of truth — rather than being a second list
somebody keeps in step by hand.
"""
import os
import re

import pytest

from app.modules.core import relation_home


REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MIGRATIONS = os.path.join(REPO, "database", "migrations")
_CREATE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_]+)\.([a-z_]+)", re.I)


def _schemas_from_migrations():
    """{relation: {schema, ...}} as the migrations declare it. The authority, re-read each run."""
    out = {}
    for name in sorted(os.listdir(MIGRATIONS)):
        if not name.endswith(".sql"):
            continue
        body = open(os.path.join(MIGRATIONS, name), encoding="utf-8", errors="replace").read()
        for schema, table in _CREATE.findall(body):
            out.setdefault(table.lower(), set()).add(schema.lower())
    return out


def test_every_claim_matches_the_migration_that_creates_the_relation():
    """The one home may not claim a schema the schema-of-truth does not create it in."""
    truth = _schemas_from_migrations()
    wrong = {t: (s, sorted(truth[t])) for t, s in relation_home.SCHEMA_OF.items()
             if t in truth and s not in truth[t]}
    assert wrong == {}, f"relation_home claims a schema no migration creates: {wrong}"


def test_no_claim_is_invented_for_a_relation_no_migration_creates():
    """A name with no `create table` behind it is a guess, and a guess is how this bug shipped."""
    truth = _schemas_from_migrations()
    invented = sorted(t for t in relation_home.SCHEMA_OF if t not in truth)
    assert invented == [], f"relation_home names relations no migration creates: {invented}"


def test_the_two_relations_that_broke_the_calculation_resolve_to_storeops():
    assert relation_home.schema_of("employees") == "storeops"
    assert relation_home.schema_of("stores") == "storeops"


def test_a_commcalc_feed_still_resolves_to_commcalc():
    for t in ("raw_sales", "raw_payment_detail", "payout_config", "name_map", "store_mapping",
              "raw_catalog", "payment_categories", "raw_mi", "raw_dlar_rep", "raw_dlar_store",
              "carrier", "commission_plan"):
        assert relation_home.schema_of(t) == "commcalc", t


def test_an_undeclared_relation_is_refused_rather_than_assumed():
    """Guessing is the bug. A name the home does not know must raise, never default to a schema."""
    with pytest.raises(relation_home.UnknownRelation):
        relation_home.schema_of("a_table_that_does_not_exist")


def test_reader_builds_the_query_against_the_relations_own_schema(fake_supabase):
    fake_supabase.seed("storeops.employees", [{"id": "e1", "org_id": "o1", "name": "A"}])
    rows = relation_home.reader(fake_supabase, "employees").eq("org_id", "o1").execute().data
    assert [r["name"] for r in rows] == ["A"]
    assert [k for k, _f in fake_supabase.reads] == ["storeops.employees"]
