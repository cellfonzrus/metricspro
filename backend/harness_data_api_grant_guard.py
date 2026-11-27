"""BUILD GATE — a relation the Data API must serve is GRANTED by the migration that creates it.

SUPABASE NOTICE, 2026-10-20 (forwarded by the owner): *"On October 30 … Supabase will stop
automatically granting Data API access to new tables in the 'public' schema for existing projects.
Your existing tables are not affected … Any table created in 'public' from that date onwards needs
an explicit GRANT before supabase-js, PostgREST, or GraphQL can reach it."*

WHY THIS IS A BUILD GATE HERE AND NOT A NOTE. This backend does not speak SQL to Postgres. Every
read and every write in `backend/app` goes through PostgREST — `app/core/database.py` builds one
supabase client per schema and the modules chain `.table(...)` on it. So a relation the Data API
cannot serve is not "invisible to the browser": it is invisible to the APPLICATION. The failure
arrives as `42501 permission denied for table <x>` from a route that worked on the author's branch
and dies the first time the migration is applied to a project created under the new default — a
preview branch, a `supabase db reset`, or a new tenant project.

THE CLASS, NOT THE INSTANCE. The instance Supabase names is `public`. The class is **a migration
that leaves a relation's reachability to a default set outside this repository.** Today 366 of the
501 tables this repo creates carry no grant of their own and are reachable only because
`ALTER DEFAULT PRIVILEGES` happens to be set for their schema in the live project — a fact that
lives in the Supabase dashboard, not in `database/migrations/`, and that Supabase is now in the
business of changing. A grant written beside the `create table` is that fact brought home: the
migration states what it needs instead of inheriting it.

WHICH ROLE. `service_role` ONLY. The browser never reads a table here: `@supabase/supabase-js` is
used for auth and realtime channels and nothing else (no `.from()` / `.rpc()` call site exists in
`frontend/src` or `mobile/src`), so every Data API read is the backend's service key. Copying
Supabase's example `grant select … to anon` into this repo would widen exposure, not fix anything,
and this gate deliberately does not ask for it.

WHAT THIS FAILS ON
  A  a migration newer than CUTOFF creates a relation with NO schema qualifier — it lands wherever
     `search_path` points, which on Supabase is `public`.
  B  a migration newer than CUTOFF creates `<schema>.<rel>` in a Data-API schema and the same file
     does not grant it to `service_role` AFTER the create — per relation, or
     `ON ALL TABLES IN SCHEMA <schema>`, which grants only the tables that exist when it runs and so
     is accepted only where it is positioned below the create it has to cover.
  C  DATA_API_SCHEMAS is not exactly the set of schemas the migrations create in. Declared, not
     derived: a brand-new schema has to be written down by a human, who then has to say whether the
     Data API serves it (index §CORE-10 — a guard that derives its own subjects cannot tell a clean
     set from a shrinking one).
  D  CUTOFF was raised. It is a RATCHET and may only go DOWN, as older migrations are given grants.
  E  a migration writes a CHECK constraint that CONTAINS A SUBQUERY. Postgres refuses it outright
     (`0A000 cannot use subquery in check constraint`), so the migration cannot run at all.

WHY E LIVES IN THIS FILE. The same class as A-D: a migration that CANNOT DO ITS JOB, merged green,
because nothing in CI ever asks Postgres to run the SQL in `database/migrations/`. The owner hit it
on 2026-11-27 pasting migration 1132, whose weekday CHECK was written as
`(SELECT bool_and(d BETWEEN 0 AND 6) FROM unnest(open_dows) AS d)` — correct-looking, legal-looking,
and rejected by the server. A whole file rolls back, so the cost is the owner's time and a second
round trip. The rule is narrow and exact rather than a SQL parser: a CHECK expression may never
contain a SELECT, in any migration, old or new (there is nothing to grandfather — a file that cannot
run was never applied). Written as a scalar expression instead: `col <@ ARRAY[...]`.

GRANDFATHERED, DELIBERATELY. Everything numbered <= CUTOFF keeps its current reachability: those
tables exist in the live project and the notice does not touch them. Backfilling 366 grants in one
change is a diff nobody can review, and would be a schema change to production for no live defect.
Measured on 2026-10-20: zero migrations in this repo create anything in `public` and zero create an
unqualified relation, so the gate starts GREEN and nothing in the backlog is at risk.

Stdlib only, no DB, no network:  python3 backend/harness_data_api_grant_guard.py
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS = os.path.join(HERE, "..", "database", "migrations")

# The highest migration number on origin/main when this gate shipped (2026-10-20). A migration
# numbered at or below this keeps whatever reachability it already has; a newer one must say.
CUTOFF = 1089
CUTOFF_CEILING = 1089   # check D: CUTOFF may only ever be LOWERED, never raised past this.

# Declared, not derived (check C). Every schema this repo creates relations in; all of them are
# served by the Data API — `app/core/database.py` reaches each one through PostgREST.
DATA_API_SCHEMAS = {"public", "core", "commcalc", "storeops", "pos", "notify", "assets"}

# The one role that must be able to reach a new relation: the backend's key. See WHICH ROLE above.
REQUIRED_ROLE = "service_role"

_CREATE = re.compile(
    r"\bcreate\s+(?:or\s+replace\s+)?(?:unlogged\s+)?(?:materialized\s+)?(?:table|view)\s+"
    r"(?:if\s+not\s+exists\s+)?([a-z_][a-z_0-9]*)\s*(\.\s*([a-z_][a-z_0-9]*))?",
    re.I,
)
_GRANT_ONE = re.compile(
    r"\bgrant\b[^;]*?\bon\s+(?:table\s+)?([a-z_][a-z_0-9]*)\s*\.\s*([a-z_][a-z_0-9]*)\b[^;]*?;",
    re.I | re.S,
)
_GRANT_ALL = re.compile(
    r"\bgrant\b[^;]*?\bon\s+all\s+tables\s+in\s+schema\s+([a-z_][a-z_0-9]*)\b[^;]*?;",
    re.I | re.S,
)

PASS = 0
FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {msg}")



def check_expressions(sql: str):
    """[(offset, expression)] for every `CHECK (...)` in this SQL, parens balanced.

    Scanning for the matching paren rather than regexing to the next `)` is the whole point: a real
    CHECK is full of nested calls, and a lazy match would read only `(open_dows IS NULL OR (`.
    """
    body = code(sql)
    out = []
    for m in re.finditer(r"\bcheck\s*\(", body, re.I):
        i = m.end() - 1
        depth = 0
        for j in range(i, len(body)):
            if body[j] == "(":
                depth += 1
            elif body[j] == ")":
                depth -= 1
                if depth == 0:
                    out.append((m.start(), body[i:j + 1]))
                    break
    return out


def _unquoted(expr: str) -> str:
    """The expression with single-quoted literals blanked. `field_type IN ('select','date')` is a
    legal CHECK whose VALUE happens to be the word select — the literals have to go before the
    keyword is looked for, or this gate fails a migration that has been applied since 2026."""
    return re.sub(r"'(?:[^']|'')*'", "''", expr)


def subquery_checks(files):
    """E — every CHECK constraint holding a SELECT. Postgres refuses each one, so the file is dead."""
    bad = []
    for name in sorted(files):
        for _off, expr in check_expressions(files[name]):
            if re.search(r"\bselect\b", _unquoted(expr), re.I):
                flat = " ".join(expr.split())
                bad.append(f"{name}: a CHECK constraint contains a subquery, which Postgres refuses "
                           f"(0A000) — the whole migration cannot run. Write it as a scalar "
                           f"expression (e.g. `col <@ ARRAY[0,1,2]`): {flat[:160]}")
    return bad


def code(sql: str) -> str:
    """The SQL with `--` comments removed — a migration header may DESCRIBE what it must not do."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def number(name: str):
    m = re.match(r"(\d+)_", name)
    return int(m.group(1)) if m else None


def created(sql: str):
    """[(schema|None, relation, offset)] created by this SQL. None schema = unqualified (check A)."""
    out = []
    for m in _CREATE.finditer(code(sql)):
        if m.group(3):
            out.append((m.group(1).lower(), m.group(3).lower(), m.start()))
        else:
            out.append((None, m.group(1).lower(), m.start()))
    return out


def granted(sql: str):
    """({(schema, relation): offset}, {schema: offset}) granted to REQUIRED_ROLE by this SQL.

    The offset is where the grant sits: `GRANT ON ALL TABLES IN SCHEMA` covers only the tables that
    already exist when it runs, so a create BELOW it is not covered by it.
    """
    body = code(sql)
    one, whole = {}, {}
    for m in _GRANT_ONE.finditer(body):
        if REQUIRED_ROLE in m.group(0).lower():
            key = (m.group(1).lower(), m.group(2).lower())
            one[key] = min(one.get(key, m.end()), m.end())
    for m in _GRANT_ALL.finditer(body):
        if REQUIRED_ROLE in m.group(0).lower():
            s_ = m.group(1).lower()
            whole[s_] = min(whole.get(s_, m.end()), m.end())
    return one, whole


def violations(files):
    """files: {name: sql} -> [plain sentences]; [] means the gate holds."""
    out = []
    for name in sorted(files):
        n = number(name)
        if n is None or n <= CUTOFF:
            continue
        one, whole = granted(files[name])
        for schema, rel, at in created(files[name]):
            if schema is None:
                out.append(
                    f"{name}: `create … {rel}` has no schema — on Supabase it lands in `public`, "
                    f"where from 2026-10-30 a new table carries no Data API grant. Qualify it "
                    f"(`create table core.{rel}`) and grant it to {REQUIRED_ROLE}."
                )
                continue
            if schema not in DATA_API_SCHEMAS:
                continue
            if one.get((schema, rel), -1) > at or whole.get(schema, -1) > at:
                continue
            out.append(
                f"{name}: creates {schema}.{rel} and does not grant it. Add, in this same file:\n"
                f"        grant select, insert, update, delete on {schema}.{rel} to {REQUIRED_ROLE};"
            )
    return out


def read_migrations():
    return {f: open(os.path.join(MIGRATIONS, f), encoding="utf-8", errors="replace").read()
            for f in os.listdir(MIGRATIONS) if f.endswith(".sql")}


def controls():
    """Z — each check planted with its own defect. A check that cannot fail is not a check."""
    after = f"{CUTOFF + 1}_x.sql"
    before = f"{CUTOFF}_x.sql"

    ok(violations({after: "create table core.widget (id uuid);"}),
       "Z1: a new migration creating an ungranted table passes the gate")
    ok(not violations({after: "create table core.widget (id uuid);\n"
                              "grant select, insert, update, delete on core.widget to service_role;"}),
       "Z2: a new migration that DOES grant its table is reported")
    ok(not violations({before: "create table core.widget (id uuid);"}),
       "Z3: a grandfathered migration (<= CUTOFF) is reported")
    ok(violations({after: "create table widget (id uuid);"}),
       "Z4: an unqualified create passes the gate")
    ok(not violations({after: "create table core.widget (id uuid);\n"
                              "grant all on all tables in schema core to service_role;"}),
       "Z5: a schema-wide grant BELOW the create does not satisfy it")
    ok(violations({after: "grant all on all tables in schema core to service_role;\n"
                          "create table core.widget (id uuid);"}),
       "Z5b: a schema-wide grant ABOVE the create satisfies it — but it runs before the table "
       "exists, so it grants nothing")
    ok(violations({after: "grant select, insert, update, delete on core.widget to service_role;\n"
                          "create table core.widget (id uuid);"}),
       "Z5c: a per-relation grant ABOVE its own create satisfies the gate")
    ok(violations({after: "create table core.widget (id uuid);\n"
                          "grant select on core.widget to anon, authenticated;"}),
       "Z6: a grant that omits service_role satisfies the gate")
    ok(not violations({after: "-- create table core.widget (id uuid);"}),
       "Z7: a create inside a `--` comment is treated as real SQL")
    ok(not violations({after: "create table audit_private.widget (id uuid);"}),
       "Z8: a schema the Data API does not serve is required to grant")
    ok(violations({after: "create materialized view commcalc.widget_mv as select 1;"}),
       "Z9: a materialized view passes the gate")
    ok(len(violations({after: "create table core.a (id uuid);\ncreate table core.b (id uuid);\n"
                              "grant all on core.a to service_role;"})) == 1,
       "Z10: only the ungranted one of two tables is named")

    # E's controls — the shape the owner actually hit, and the shape that replaced it.
    ok(subquery_checks({"9999_x.sql": "alter table s.t add constraint c check (d is null or "
                                      "(select bool_and(x between 0 and 6) from unnest(d) as x));"}),
       "Z11: a CHECK holding a subquery is caught")
    ok(not subquery_checks({"9999_x.sql": "alter table s.t add constraint c check "
                                          "(d is null or d <@ array[0,1,2,3,4,5,6]);"}),
       "Z12: the same rule as a scalar expression is fine")
    ok(not subquery_checks({"9999_x.sql": "-- check (select 1) in a comment\n"
                                          "create table s.t (id uuid);"}),
       "Z13: a CHECK described in a comment is not a constraint")
    ok(not subquery_checks({"9999_x.sql": "create table s.t (id uuid, "
                                          "d int check (d between 0 and 6));\n"
                                          "insert into s.t select 1;"}),
       "Z14: a SELECT elsewhere in the file is not inside the CHECK")
    ok(not subquery_checks({"9999_x.sql": "create table s.t (k text check "
                                          "(k in ('text','select','date')));"}),
       "Z16: the WORD select as a declared VALUE is not a subquery (migration 053's real shape)")
    ok(len(check_expressions("alter table s.t add constraint c check (a is null or (b and c));")) == 1
       and check_expressions("alter table s.t add constraint c check (a is null or (b and c));"
                             )[0][1].endswith("))"),
       "Z15: the expression is read to its MATCHING paren, not the first one")


def main():
    files = read_migrations()

    # C — the declared schema set is exactly the one the migrations create in, BOTH ways.
    derived = {s for sql in files.values() for s, _r, _at in created(sql) if s}
    missing = sorted(derived - DATA_API_SCHEMAS)
    stale = sorted(DATA_API_SCHEMAS - derived)
    for s in missing:
        ok(False, f"schema `{s}` is created in by a migration and is not in DATA_API_SCHEMAS — "
                  f"declare it (and say whether the Data API serves it) in "
                  f"harness_data_api_grant_guard.py")
    # `public` is declared and nothing creates in it: that is the point of the gate, not drift.
    for s in stale:
        ok(s == "public", f"DATA_API_SCHEMAS declares `{s}`, which no migration creates in — "
                          f"prune it so the declaration stays an honest inventory")
    ok(not missing, f"{len(missing)} undeclared schema(s)")

    # D — the ratchet.
    ok(CUTOFF <= CUTOFF_CEILING,
       f"CUTOFF was raised to {CUTOFF}: it may only be LOWERED (ceiling {CUTOFF_CEILING}). "
       f"Grandfathering a migration you are writing is not what this is for.")

    # E — a CHECK Postgres refuses. Every migration, nothing grandfathered: a file that cannot run
    # was never applied, so there is no live state to protect by excusing it.
    dead = subquery_checks(files)
    for v in dead:
        ok(False, v)
    ok(not dead, f"{len(dead)} migration(s) write a CHECK constraint Postgres refuses")

    # A + B — the gate itself.
    bad = violations(files)
    for v in bad:
        ok(False, v)
    ok(not bad, f"{len(bad)} migration(s) create a Data API relation without granting it")

    controls()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
