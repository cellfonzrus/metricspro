"""Shared in-memory stand-in for the Supabase/PostgREST client, used by the commcalc harnesses.

It reproduces the behaviours that actually bit production rather than a convenient abstraction:
  * an INTEGER column rejects a Python float with the real 22P02 text, which is how the comp
    upload died on row 0;
  * inserts return the representation (id + created_at) the real client returns, because
    safe_replace depends on it to tell the new load from the old;
  * deletes honour the same filter chain, so a scope bug shows up here too.
Extracted from harness_comp_upload_safety.py so more than one harness can drive it.
"""
import uuid
from datetime import datetime, timedelta, timezone


# ── a fake PostgREST that enforces the column types Postgres enforces ───────────────────────
class PgError(Exception):
    pass


class FakeTable:
    """Column types per table. 'int' rejects a non-integral float exactly as Postgres does."""

    def __init__(self, cols, has_created_at=True):
        self.cols = cols
        self.has_created_at = has_created_at
        self.rows = []


class Query:
    def __init__(self, db, table, op, payload=None, count=None):
        self.db, self.t, self.op, self.payload, self.count_mode = db, table, op, payload, count
        self.filters = []
        self._order = None
        self._range = None
        self._limit = None

    # -- filter builders ------------------------------------------------------------------
    def eq(self, c, v):
        self.filters.append(lambda r, c=c, v=v: r.get(c) == v)
        return self

    def neq(self, c, v):
        self.filters.append(lambda r, c=c, v=v: r.get(c) != v)
        return self

    def in_(self, c, vs):
        vs = list(vs)
        self.filters.append(lambda r, c=c, vs=vs: r.get(c) in vs)
        return self

    def gte(self, c, v):
        self.filters.append(lambda r, c=c, v=v: r.get(c) is not None and r[c] >= v)
        return self

    def lt(self, c, v):
        self.filters.append(lambda r, c=c, v=v: r.get(c) is not None and r[c] < v)
        return self

    def order(self, c):
        self._order = c
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _match(self):
        return [r for r in self.t.rows if all(f(r) for f in self.filters)]

    class _Resp:
        def __init__(self, data, count=None):
            self.data, self.count = data, count

    def execute(self):
        self.db.calls.append((self.op, self.t.name))
        if self.op == "select":
            # PostgREST rejects a select naming a column the table does not have (42703). The
            # sweep's graceful degradation for "migration 290 not applied yet" depends on that
            # being an ERROR, not a silently-missing key, so model it.
            wanted = getattr(self, "sel_cols", "*")
            if wanted and wanted != "*":
                known = set(self.t.cols) | {"id", "created_at"}
                missing = [c for c in str(wanted).split(",") if c.strip() and c.strip() not in known]
                if missing:
                    raise PgError(
                        '{\'message\': \'column %s.%s does not exist\', \'code\': \'42703\'}'
                        % (self.t.name, missing[0].strip()))
            rows = self._match()
            n = len(rows)
            if self._order:
                rows = sorted(rows, key=lambda r: str(r.get(self._order)))
            if self._range:
                rows = rows[self._range[0]:self._range[1] + 1]
            if self._limit is not None:
                rows = rows[:self._limit]
            return Query._Resp([dict(r) for r in rows], n if self.count_mode else None)
        if self.op == "delete":
            doomed = self._match()
            if self.db.fail_delete:
                raise PgError("simulated delete failure")
            for r in doomed:
                self.t.rows.remove(r)
            return Query._Resp([dict(r) for r in doomed])
        if self.op == "update":
            hit = self._match()
            for r in hit:
                r.update(self.payload)
            return Query._Resp([dict(r) for r in hit])
        if self.op == "insert":
            batch = self.payload
            self.db.insert_batches += 1
            if self.db.fail_on_batch is not None and self.db.insert_batches == self.db.fail_on_batch:
                raise PgError("simulated mid-insert failure (connection reset)")
            out = []
            for row in batch:
                # THE REAL CHECK: integer columns reject a float that carries a fraction part,
                # and PostgREST serialises Python 1.0 as 1.0 — the live 22P02.
                for c, typ in self.t.cols.items():
                    if typ == "int" and c in row and row[c] is not None:
                        v = row[c]
                        if isinstance(v, float):
                            raise PgError(
                                '{\'message\': \'invalid input syntax for type integer: "%r"\', '
                                '\'code\': \'22P02\'}' % v)
                new = dict(row)
                new["id"] = str(uuid.uuid4())
                if self.t.has_created_at:
                    self.db.clock += timedelta(milliseconds=1)
                    new["created_at"] = self.db.clock.isoformat()
                self.t.rows.append(new)
                out.append(dict(new))
            return Query._Resp(out)
        raise AssertionError(self.op)


class FakeSchema:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        t = self.db.tables[name]
        t.name = name
        return FakeTableAPI(self.db, t)


class FakeTableAPI:
    def __init__(self, db, t):
        self.db, self.t = db, t

    def select(self, cols="*", count=None):
        q = Query(self.db, self.t, "select", count=count)
        q.sel_cols = cols
        return q

    def insert(self, payload):
        return Query(self.db, self.t, "insert", payload=payload)

    def delete(self):
        return Query(self.db, self.t, "delete")

    def update(self, payload):
        return Query(self.db, self.t, "update", payload=payload)


class FakeClient:
    def __init__(self):
        self.tables = {}
        self.calls = []
        self.insert_batches = 0
        self.fail_on_batch = None
        self.fail_delete = False
        self.clock = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)

    def schema(self, _s):
        return FakeSchema(self)


