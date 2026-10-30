"""An in-memory stand-in for the Supabase/postgrest client — the ONE database double for tests.

WHY IT EXISTS. There is no staging database (project memory, 2026-10-08): every environment points
at the same live Supabase, so a test that builds a real client is one typo away from writing to
production commission money. The double gives an integration test the real FastAPI app, the real
routers and the real request cycle over rows the test itself seeded.

WHY ONE HOME AND NOT A STUB PER TEST FILE. A hand-rolled stub per test is the duplicate defect the
house rules forbid, wearing a test's hat: two doubles answering "what does .eq() do" will drift, and
the day they disagree the suite lies in the direction nobody is looking. Every double lives here and
its semantics are pinned by tests/unit/test_supabase_double.py.

WHAT IT PROMISES (and nothing beyond it — an unimplemented verb raises rather than guessing):
  • `.schema(name).table(name)` keys a relation as "schema.table"; bare `.table()` means `public`.
  • filters `.eq .neq .in_ .gte .lte .gt .lt .is_` chain and AND together, in any order.
  • `.order(col, desc=) .limit(n) .range(a, b) .single() .maybe_single()`.
  • writes `.insert .upsert .update .delete`, each returning the affected rows.
  • **a `.delete()` or `.update()` with NO filter is REFUSED** (OutboundWriteRefused). A rebuild
    that lost its filter emptied 11,000 live payable rows once (index §PAYABLES-01); the double will
    not let a test certify that shape as working.
  • reads return deep copies, so a caller cannot mutate the seeded store through a result.

Stdlib only. No network, no credentials, no postgrest import.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional


class OutboundWriteRefused(RuntimeError):
    """An unfiltered update/delete — the shape that empties a table. Refused, never simulated."""


class FakeResponse:
    """Mirrors postgrest's APIResponse surface as far as this codebase uses it: `.data` / `.count`."""

    def __init__(self, data: Any, count: Optional[int] = None):
        self.data = data
        self.count = count

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FakeResponse(data={self.data!r}, count={self.count!r})"


_MISSING = object()


class _Query:
    """One request builder. Filters accumulate; `.execute()` applies them to the seeded rows."""

    def __init__(self, store: Dict[str, List[dict]], key: str, verb: str, payload: Any = None,
                 reads: Optional[List[tuple]] = None):
        self._store = store
        self._key = key
        self._verb = verb
        self._reads = reads
        self._payload = payload
        self._filters: List[tuple] = []
        self._order: Optional[tuple] = None
        self._limit: Optional[int] = None
        self._range: Optional[tuple] = None
        self._single = False
        self._maybe_single = False

    # ── filters ──────────────────────────────────────────────────────────────────────────────
    def eq(self, col: str, val: Any) -> "_Query":
        self._filters.append(("eq", col, val)); return self

    def neq(self, col: str, val: Any) -> "_Query":
        self._filters.append(("neq", col, val)); return self

    def gt(self, col: str, val: Any) -> "_Query":
        self._filters.append(("gt", col, val)); return self

    def gte(self, col: str, val: Any) -> "_Query":
        self._filters.append(("gte", col, val)); return self

    def lt(self, col: str, val: Any) -> "_Query":
        self._filters.append(("lt", col, val)); return self

    def lte(self, col: str, val: Any) -> "_Query":
        self._filters.append(("lte", col, val)); return self

    def in_(self, col: str, vals: Iterable[Any]) -> "_Query":
        self._filters.append(("in", col, list(vals))); return self

    def is_(self, col: str, val: Any) -> "_Query":
        self._filters.append(("is", col, val)); return self

    def or_(self, expr: str, **_kw) -> "_Query":
        """PostgREST's `or=(a.eq.1,b.is.null)` — a comma-separated list of `col.op.value` terms, of
        which ONE must hold. The real client takes it as the raw string the caller composed, so the
        double parses that same string rather than inventing a kinder signature: a test that proves
        a route works has to prove it works with what the route actually sends.

        Only the term forms this codebase composes are understood (`eq`, `is`, `neq`, `in`); an
        unknown one raises rather than quietly matching nothing, which is how a row filter that
        stopped filtering would otherwise pass a suite.
        """
        terms = []
        for part in str(expr or "").split(","):
            bits = part.strip().split(".", 2)
            if len(bits) != 3:
                raise NotImplementedError(f"the Supabase double cannot read or_ term {part!r}")
            col, op, raw = bits
            if op == "is":
                terms.append(("is", col, None if raw == "null" else raw))
            elif op in ("eq", "neq"):
                terms.append((op, col, raw))
            elif op == "in":
                terms.append(("in", col, [v.strip('"') for v in raw.strip("()").split(",")]))
            else:
                raise NotImplementedError(f"the Supabase double cannot read or_ operator {op!r}")
        self._filters.append(("or", None, terms)); return self

    # ── shaping ──────────────────────────────────────────────────────────────────────────────
    def order(self, col: str, desc: bool = False, **_kw) -> "_Query":
        self._order = (col, desc); return self

    def limit(self, n: int) -> "_Query":
        self._limit = n; return self

    def range(self, start: int, end: int) -> "_Query":
        self._range = (start, end); return self

    def single(self) -> "_Query":
        self._single = True; return self

    def maybe_single(self) -> "_Query":
        self._maybe_single = True; return self

    # ── execution ────────────────────────────────────────────────────────────────────────────
    def _rows(self) -> List[dict]:
        return self._store.setdefault(self._key, [])

    def _matches(self, row: dict) -> bool:
        for op, col, val in self._filters:
            if op == "or":
                if not any(self._term(row, *t) for t in val):
                    return False
                continue
            if not self._term(row, op, col, val):
                return False
        return True

    def _term(self, row: dict, op: str, col: str, val: Any) -> bool:
        """One `col op value` test. Shared by the AND chain and by `or_`, so the two can never
        disagree about what `is null` or `eq` means."""
        got = row.get(col, _MISSING)
        if op == "eq":
            return got == val
        if op == "neq":
            return got != val
        if op == "in":
            return got in val
        if op == "is":
            # PostgREST spells IS NULL as the literal `is.null`, and that is the spelling every
            # caller in this codebase uses — `.is_("store_code", "null")`, not `.is_(…, None)`
            # (app/modules/asset/router.py, commcalc/epay_ingest.py, account/statement_engine.py).
            # The double used to compare the STRING "null" to the column and so answered False for
            # every NULL row, silently returning [] for a read that works in production. Found while
            # testing the §POS-17 settings fix, 2026-10-30.
            if val is None or (isinstance(val, str) and val.strip().lower() == "null"):
                return got in (None, _MISSING)
            if isinstance(val, str) and val.strip().lower() in ("true", "false"):
                return bool(got) is (val.strip().lower() == "true")
            return got == val
        if op in ("gt", "gte", "lt", "lte"):
            if got is _MISSING or got is None:
                return False
            if op == "gt":
                return got > val
            if op == "gte":
                return got >= val
            if op == "lt":
                return got < val
            return got <= val
        raise NotImplementedError(f"the Supabase double does not implement filter '{op}'")

    def execute(self) -> FakeResponse:
        if self._verb == "select":
            # One `.execute()` on a select IS one round trip to PostgREST. Recording it is what
            # lets a test assert a COST (index §CORE-22: a page load never queries once per item).
            if self._reads is not None:
                self._reads.append((self._key, tuple(self._filters)))
            out = [copy.deepcopy(r) for r in self._rows() if self._matches(r)]
            if self._order:
                col, desc = self._order
                out.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
            if self._range:
                a, b = self._range
                out = out[a:b + 1]
            if self._limit is not None:
                out = out[: self._limit]
            if self._single:
                if len(out) != 1:
                    raise ValueError(f"single() expected exactly 1 row for {self._key}, got {len(out)}")
                return FakeResponse(out[0], count=1)
            if self._maybe_single:
                return FakeResponse(out[0] if out else None, count=len(out))
            return FakeResponse(out, count=len(out))

        if self._verb in ("insert", "upsert"):
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            rows = self._rows()
            written = []
            for rec in payload:
                rec = copy.deepcopy(rec)
                if self._verb == "upsert":
                    existing = next((r for r in rows if "id" in rec and r.get("id") == rec.get("id")), None)
                    if existing is not None:
                        existing.update(rec)
                        written.append(copy.deepcopy(existing))
                        continue
                rows.append(rec)
                written.append(copy.deepcopy(rec))
            return FakeResponse(written, count=len(written))

        if self._verb == "update":
            if not self._filters:
                raise OutboundWriteRefused(
                    f"update on {self._key} with no filter would rewrite every row — refused")
            touched = []
            for r in self._rows():
                if self._matches(r):
                    r.update(copy.deepcopy(self._payload))
                    touched.append(copy.deepcopy(r))
            return FakeResponse(touched, count=len(touched))

        if self._verb == "delete":
            if not self._filters:
                raise OutboundWriteRefused(
                    f"delete on {self._key} with no filter would empty the table — refused "
                    "(see index §PAYABLES-01)")
            rows = self._rows()
            removed = [copy.deepcopy(r) for r in rows if self._matches(r)]
            self._store[self._key] = [r for r in rows if not self._matches(r)]
            return FakeResponse(removed, count=len(removed))

        raise NotImplementedError(f"the Supabase double does not implement '{self._verb}'")


class _Table:
    def __init__(self, store: Dict[str, List[dict]], key: str,
                 reads: Optional[List[tuple]] = None):
        self._store = store
        self._key = key
        self._reads = reads

    def select(self, *_cols, **_kw) -> _Query:
        return _Query(self._store, self._key, "select", reads=self._reads)

    def insert(self, payload: Any, **_kw) -> _Query:
        return _Query(self._store, self._key, "insert", payload, reads=self._reads)

    def upsert(self, payload: Any, **_kw) -> _Query:
        return _Query(self._store, self._key, "upsert", payload, reads=self._reads)

    def update(self, payload: dict, **_kw) -> _Query:
        return _Query(self._store, self._key, "update", payload, reads=self._reads)

    def delete(self, **_kw) -> _Query:
        return _Query(self._store, self._key, "delete", reads=self._reads)


class _Rpc:
    """What `rpc()` returns: a builder that does nothing until `.execute()`.

    DELIBERATELY NOT the response itself. The real postgrest client returns a builder here, so every
    caller in this codebase writes `.rpc(...).execute()`; a double that answered `.data` straight
    away would let a caller that forgot `.execute()` pass its tests and fail in production. The
    handler is not run until `.execute()` either, so a route that builds a call and abandons it
    writes nothing — which is what the real client does.
    """

    def __init__(self, handler: Any, params: dict):
        self._handler = handler
        self._params = params

    def execute(self) -> "FakeResponse":
        return FakeResponse(self._handler(self._params))


class _Schema:
    def __init__(self, store: Dict[str, List[dict]], schema: str, rpc_handlers: Dict[str, Any],
                 reads: Optional[List[tuple]] = None):
        self._store = store
        self._schema = schema
        self._rpc = rpc_handlers
        self._reads = reads

    def table(self, name: str) -> _Table:
        return _Table(self._store, f"{self._schema}.{name}", reads=self._reads)

    from_ = table

    def rpc(self, name: str, params: Optional[dict] = None):
        handler = self._rpc.get(f"{self._schema}.{name}", self._rpc.get(name))
        if handler is None:
            raise NotImplementedError(
                f"no rpc handler seeded for {self._schema}.{name}() — seed one with "
                "FakeSupabase(..., rpc={'name': lambda params: rows}) or seed_rpc()")
        return _Rpc(handler, params or {})


class FakeSupabase:
    """Seed with {"schema.table": [rows]} and optionally {"fn": callable} for rpc().

    `rows` is the test's own data. Nothing is shared between FakeSupabase instances, so one test
    cannot leak state into another.
    """

    def __init__(self, tables: Optional[Dict[str, List[dict]]] = None,
                 rpc: Optional[Dict[str, Any]] = None):
        self._store: Dict[str, List[dict]] = {k: copy.deepcopy(v) for k, v in (tables or {}).items()}
        self._rpc = dict(rpc or {})
        #: every select that reached `.execute()`, as (schema.table, filters) — one entry per
        #: PostgREST round trip, so a test can assert what a page COSTS, not only what it returns.
        self.reads: List[tuple] = []

    @property
    def read_count(self) -> int:
        """Round trips made since the last `reset_reads()`."""
        return len(self.reads)

    def reset_reads(self) -> None:
        self.reads.clear()

    def schema(self, name: str) -> _Schema:
        return _Schema(self._store, name, self._rpc, reads=self.reads)

    def table(self, name: str) -> _Table:
        return self.schema("public").table(name)

    def from_(self, name: str) -> _Table:
        return self.table(name)

    def rpc(self, name: str, params: Optional[dict] = None):
        return self.schema("public").rpc(name, params)

    # ── test affordances (not part of the real client) ───────────────────────────────────────
    def rows(self, key: str) -> List[dict]:
        """Read the seeded store directly, to assert on what a route WROTE."""
        return copy.deepcopy(self._store.get(key, []))

    def seed(self, key: str, rows: List[dict]) -> None:
        self._store[key] = copy.deepcopy(rows)

    def seed_rpc(self, name: str, handler: Any) -> None:
        """Seed an `rpc()` handler after construction, the way `seed` does for a table.

        A fixture that builds the double before it knows which function a test needs could otherwise
        only reach into `_rpc`. `name` may be bare or schema-qualified, matching the lookup in
        `_Schema.rpc`; `handler` takes the params dict and returns whatever the function returns.
        """
        self._rpc[name] = handler
