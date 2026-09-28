"""LOCK — "who did this" is a UUID or NULL, never a sentinel string (index §19.34, 2026-09-28).

THE DEFECT (found live 2026-09-28, saving tenant device rules). `commcalc/router.py::_caller_uid` returned the
literal 'web' when no signed-in user resolved. `save_category_rule` writes it into
`commcalc.installment_category_rule.updated_by`, which migration 245 declares UUID — so every save by a caller
with no token (RBAC off, automation, agents, the auto-calc poller) died with
`invalid input syntax for type uuid: "web"`: a 500, nothing saved. The scratchpad proof for that endpoint had
stubbed `_caller_uid` with the string "harness", which is exactly why nothing caught it.

THE CLASS. An actor identity ("who did this") was written as a SENTINEL STRING where the database's own
"unknown" is NULL. It only fails where the column is UUID; on a TEXT column the sentinel is silently stored and
later displayed as if it were a person.

THE DESIGN FIX.
  * ONE home: `_caller_uid(authorization)` returns the signed-in uid as a canonical UUID string, or None. It
    never returns a sentinel, and a resolved id that is not a UUID is None too. Every "who is acting" wrapper in
    commcalc (`_mpc_who`, `_xc_who`, `_agency_who`) dereferences it; downstream helpers (`_installment_audit`,
    `discrepancy_appeals.apply_appeal`) no longer re-add 'web'. The UI reads NULL (and the retired 'web') as
    "system" through one helper, `frontend/src/lib/actor.ts::actorLabel`.

WHAT THIS FAILS THE BUILD ON (stdlib + ast, no DB, no pip install):
  A. `_caller_uid` behaviour: exec'd from router.py's own source against a stub token resolver, it must return
     None for no token / empty / a raising resolver / a NON-UUID id, and the canonical uid for a real one.
  B. One home: `_caller_uid` is defined exactly once; no other function under backend/app/modules/commcalc
     re-implements it (returns `_uid_from_token(...)` directly); nothing under backend/app writes
     `_uid_from_token(...) or '<literal>'`; no proof under backend/ stubs `_caller_uid` with a non-UUID literal.
  C. The schema-driven sibling scan: the migrations are parsed for every actor column (a name with a `by`
     token: updated_by, changed_by, recorded_by_auth_id, …) with its declared type and nullability. Every write
     under backend/app of an actor key (dict literal key, `row["x_by"] = …`, `x_by=` keyword) is traced to the
     string literals it can carry — directly, through `a or 'lit'` / `x if c else 'lit'`, through a local
     variable, a parameter default, or a same-module helper's return (so `_caller_uid(...)` itself is traced).
     A non-UUID literal reaching a column declared UUID FAILS. A literal whose target table cannot be resolved
     fails if the column name is UUID in any table. TEXT columns carrying a sentinel are printed as the excuse
     inventory (with the table and type), not failed — see §19.34 for why each is left.
  D. DB-free proof of the REAL `save_category_rule` handler (exec'd from router.py's source with the real
     `_caller_uid` and the real installment_category module) against a recording client that ENFORCES the
     migration-declared column types, the way Postgres does: no Authorization -> saved, updated_by None; a token
     -> updated_by is the uid; update path the same. REGRESSION: the pre-fix `_caller_uid` source reproduces the
     live 500 `invalid input syntax for type uuid: "web"` against the same client.
  E. Negative controls plant each violation (UUID column + sentinel, `or 'system'` fallback, sentinel through a
     variable and through a helper's return, a second helper copy, a non-UUID proof stub, a sentinel-returning
     `_caller_uid`, a new UUID column added by migration) and require this lock to catch it — and plant a TEXT
     column + sentinel and require it NOT to fail (no over-catching).

Run: python3 harness_actor_uid_lock.py
"""
import ast
import datetime as _dt
import importlib
import os
import re
import sys
import types
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
MIGRATIONS = os.path.join(ROOT, "database", "migrations")
ROUTER = os.path.join(APP, "modules", "commcalc", "router.py")
COMMCALC = os.path.join(APP, "modules", "commcalc")

ACTOR_COL = re.compile(r"(?:^|_)by(?:_|$)")
# keys that carry a `by` token but are not "who did this" (ordering/grouping/matching words)
NOT_ACTOR = {"order_by", "group_by", "sort_by", "split_by", "filter_by", "partition_by", "gated_by",
             "matched_by", "paired_by", "placed_by", "resolved_by"}

# (file relative to backend/app, function) -> reason. A wrapper that answers a DIFFERENT question than
# `_caller_uid` (so it is not a second copy). May only shrink.
# The wrapper rule is judged in commcalc (where `_caller_uid` lives); elsewhere only the sentinel rule applies.
EXCUSED_WRAPPERS = {
    ("modules/commcalc/pay_simulator.py", "_uid"):
        "answers 'whose pay may this caller SEE' (the self-scope gate of the pay simulator) — an authorization "
        "identity that is never written to a column; returns None when unresolved, never a sentinel",
}

# (file relative to backend/app, function, key) -> (the tables the dynamic `.table(x)` can name, reason).
# A write whose target table the scan cannot resolve statically, and whose column NAME is UUID somewhere.
# The harness VERIFIES that every listed table declares the column and not as UUID. May only shrink.
EXCUSED_UNRESOLVED = {
    ("modules/marketing/router.py", "update_child", "updated_by"): (
        ("marketing_event_staff", "marketing_event_vendor", "marketing_event_checklist_item",
         "marketing_event_link", "marketing_event_giveaway"),
        "the table is `_CHILD[collection]`; `updated_by` is stamped only for _CHILD_STAMPED, all TEXT (mig 986)"),
}

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def is_uuid(s):
    try:
        uuid.UUID(str(s))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def is_actor_key(k):
    return isinstance(k, str) and bool(ACTOR_COL.search(k)) and k not in NOT_ACTOR


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 1. THE SCHEMA — every column's declared type + nullability, read from the migrations (never restated)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _mig_key(name):
    m = re.match(r"^(\d+)([a-z]?)_", name)
    return (int(m.group(1)), m.group(2)) if m else (10 ** 9, name)


def _strip_sql_comments(sql):
    return re.sub(r"--[^\n]*", "", sql)


def _split_top(body):
    out, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return [p.strip() for p in out if p.strip()]


def _bare(tbl):
    return tbl.replace('"', "").split(".")[-1].lower()


_SKIP_DEF = re.compile(r"^(constraint|primary|unique|foreign|check|exclude|like)\b", re.I)


def parse_schema(files):
    """files: [(name, sql)] in run order -> {table: {column: {"type", "not_null", "file"}}}."""
    schema = {}
    for name, raw in sorted(files, key=lambda f: _mig_key(f[0])):
        sql = _strip_sql_comments(raw)
        for m in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([\w\.\"]+)\s*\(", sql, re.I):
            i, depth = m.end(), 1
            while i < len(sql) and depth:
                depth += {"(": 1, ")": -1}.get(sql[i], 0)
                i += 1
            cols = schema.setdefault(_bare(m.group(1)), {})
            for part in _split_top(sql[m.end():i - 1]):
                if _SKIP_DEF.match(part):
                    continue
                toks = part.split()
                if len(toks) < 2:
                    continue
                low = part.lower()
                cols[toks[0].strip('"').lower()] = {
                    "type": toks[1].lower().rstrip(","), "file": name,
                    "not_null": ("not null" in low) or ("primary key" in low)}
        for m in re.finditer(r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?([\w\.\"]+)\s+(.*?);", sql, re.I | re.S):
            cols = schema.setdefault(_bare(m.group(1)), {})
            for act in _split_top(m.group(2)):
                a = re.match(r"add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?\"?(\w+)\"?\s+(\w+)", act, re.I)
                if a and a.group(1).lower() not in ("constraint", "primary", "unique", "foreign", "check"):
                    low = act.lower()
                    cols[a.group(1).lower()] = {"type": a.group(2).lower(), "file": name,
                                                "not_null": "not null" in low or "primary key" in low}
                    continue
                t = re.match(r"alter\s+(?:column\s+)?\"?(\w+)\"?\s+(?:set\s+data\s+)?type\s+(\w+)", act, re.I)
                if t and t.group(1).lower() in cols:
                    cols[t.group(1).lower()]["type"] = t.group(2).lower()
                    continue
                n = re.match(r"alter\s+(?:column\s+)?\"?(\w+)\"?\s+(set|drop)\s+not\s+null", act, re.I)
                if n and n.group(1).lower() in cols:
                    cols[n.group(1).lower()]["not_null"] = n.group(2).lower() == "set"
    return schema


def load_migrations():
    out = []
    for n in os.listdir(MIGRATIONS):
        if n.endswith(".sql"):
            with open(os.path.join(MIGRATIONS, n), encoding="utf-8", errors="replace") as f:
                out.append((n, f.read()))
    return out


def uuid_actor_columns(schema):
    """{column_name: {table, …}} for every actor column declared UUID."""
    out = {}
    for t, cols in schema.items():
        for c, meta in cols.items():
            if is_actor_key(c) and meta["type"] == "uuid":
                out.setdefault(c, set()).add(t)
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 2. THE CODE — every actor-key write, traced to the string literals it can carry
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def module_str_constants(trees):
    """NAME -> {values} for every module-level `NAME = "str"` (resolves `.table(_mod.TARGET_TABLE)`)."""
    out = {}
    for tree in trees.values():
        for node in tree.body:
            if isinstance(node, ast.Assign) and _const_str(node.value) is not None:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out.setdefault(t.id, set()).add(node.value.value)
    return out


class _Fn:
    def __init__(self, node):
        self.node = node
        self.assigns = {}                      # local name -> [value nodes]
        self.params = {}                       # param name -> default node
        a = node.args
        pos = a.posonlyargs + a.args
        for arg, d in zip(pos[len(pos) - len(a.defaults):], a.defaults):
            self.params[arg.arg] = d
        for arg, d in zip(a.kwonlyargs, a.kw_defaults):
            if d is not None:
                self.params[arg.arg] = d
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign):
                for t in sub.targets:
                    if isinstance(t, ast.Name):
                        self.assigns.setdefault(t.id, []).append(sub.value)
            elif isinstance(sub, (ast.AnnAssign, ast.AugAssign)) and isinstance(sub.target, ast.Name) and sub.value:
                self.assigns.setdefault(sub.target.id, []).append(sub.value)


def literals_of(node, fn, fns, depth=0, seen=None):
    """The string literals (non-UUID) an expression can evaluate to. '<f-string>' for an f-string."""
    seen = seen if seen is not None else set()
    if node is None or depth > 6:
        return set()
    if isinstance(node, ast.Constant):
        return {node.value} if isinstance(node.value, str) and not is_uuid(node.value) else set()
    if isinstance(node, ast.JoinedStr):
        return {"<f-string>"}
    if isinstance(node, ast.BoolOp):
        out = set()
        for v in node.values:
            out |= literals_of(v, fn, fns, depth + 1, seen)
        return out
    if isinstance(node, ast.IfExp):
        return literals_of(node.body, fn, fns, depth + 1, seen) | literals_of(node.orelse, fn, fns, depth + 1, seen)
    if isinstance(node, ast.Name) and fn is not None:
        key = (id(fn), node.id)
        if key in seen:
            return set()
        seen.add(key)
        out = set()
        for v in fn.assigns.get(node.id, []):
            out |= literals_of(v, fn, fns, depth + 1, seen)
        if node.id in fn.params:
            out |= literals_of(fn.params[node.id], fn, fns, depth + 1, seen)
        return out
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name and name in fns:
            key = ("ret", name)
            if key in seen:
                return set()
            seen.add(key)
            callee = fns[name]
            out = set()
            for sub in ast.walk(callee.node):
                if isinstance(sub, ast.Return):
                    out |= literals_of(sub.value, callee, fns, depth + 1, seen)
            return out
        return set()
    return set()


def _table_names(fn_node, consts):
    out = set()
    for sub in ast.walk(fn_node):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "table"
                and sub.args):
            a = sub.args[0]
            s = _const_str(a)
            if s is not None:
                out.add(s.lower())
            elif isinstance(a, ast.Name):
                out |= {v.lower() for v in consts.get(a.id, ())}
            elif isinstance(a, ast.Attribute):
                out |= {v.lower() for v in consts.get(a.attr, ())}
    return out


def actor_writes(rel, tree, consts):
    """[(rel, function, lineno, key, literals, tables, kind)] for every actor-key write carrying a literal."""
    fns = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fns.setdefault(node.name, _Fn(node))
    out = []
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fnode in funcs:
        fn = fns.get(fnode.name) if fns.get(fnode.name) and fns[fnode.name].node is fnode else _Fn(fnode)
        tables = _table_names(fnode, consts)
        # only nodes that belong to THIS function (not a nested def — that one is walked on its own)
        own = []
        stack = list(ast.iter_child_nodes(fnode))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            own.append(n)
            stack.extend(ast.iter_child_nodes(n))
        for n in own:
            pairs = []
            if isinstance(n, ast.Dict):
                pairs = [(_const_str(k), v, "dict") for k, v in zip(n.keys, n.values) if k is not None]
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Subscript):
                        pairs.append((_const_str(t.slice), n.value, "subscript"))
            elif isinstance(n, ast.Call):
                pairs = [(kw.arg, kw.value, "keyword") for kw in n.keywords if kw.arg]
            for key, val, kind in pairs:
                if not is_actor_key(key):
                    continue
                lits = literals_of(val, fn, fns)
                if lits:
                    out.append((rel, fnode.name, getattr(val, "lineno", fnode.lineno), key, sorted(lits),
                                sorted(tables), kind))
    return out


def judge_writes(writes, schema, excused=None):
    """-> (violations, text_inventory). A violation: a non-UUID literal reaching a UUID-declared column."""
    uuid_cols = uuid_actor_columns(schema)
    excused = EXCUSED_UNRESOLVED if excused is None else excused
    bad, text = [], []
    for w in writes:
        rel, fn, line, key, lits, tables, kind = w
        if (rel, fn, key) in excused:
            tables = sorted(excused[(rel, fn, key)][0])
        hit = [t for t in tables if schema.get(t, {}).get(key, {}).get("type") == "uuid"]
        if kind == "keyword":
            # a keyword argument hands the value to a helper: the helper's own writes are scanned where they
            # happen; here the only certain fact is the column NAME
            hit = sorted(uuid_cols.get(key, ())) if key in uuid_cols and not tables else hit
        if not tables and key in uuid_cols:
            hit = sorted(uuid_cols[key])
        if hit:
            bad.append(f"{rel}::{fn} line {line}: {lits} -> {key} (UUID in {hit})")
        else:
            types_ = sorted({schema.get(t, {}).get(key, {}).get("type", "?") for t in tables} or {"?"})
            text.append((rel, fn, line, key, lits, tables, types_))
    return bad, text


# The sentinel `_caller_uid` used to return. Retired everywhere (TEXT columns included) so the fixed siblings
# cannot drift back; the UI reads a legacy 'web' row as "system" (frontend/src/lib/actor.ts).
RETIRED_SENTINELS = {"web"}
FRONTEND = os.path.join(ROOT, "frontend", "src")
_FE_SEND = re.compile(r"\b(\w*_by)\s*:\s*['\"](\w+)['\"]")


def retired_writes(text_inventory):
    return [f"{rel}::{fn}:{line} {key} <- {lits}" for rel, fn, line, key, lits, _t, _ty in text_inventory
            if set(lits) & RETIRED_SENTINELS]


def frontend_sentinel_sends(texts=None):
    """{path: text} (default: frontend/src/**/*.ts[x]) -> ["path:line x_by: 'web'"] for retired sentinels."""
    if texts is None:
        texts = {}
        if os.path.isdir(FRONTEND):
            for dp, dn, fnames in os.walk(FRONTEND):
                dn[:] = [d for d in dn if d != "node_modules"]
                for f in fnames:
                    if f.endswith((".ts", ".tsx")):
                        p = os.path.join(dp, f)
                        with open(p, encoding="utf-8", errors="replace") as fh:
                            texts[os.path.relpath(p, ROOT)] = fh.read()
    out = []
    for path, txt in texts.items():
        for i, line in enumerate(txt.splitlines(), 1):
            for m in _FE_SEND.finditer(line):
                if is_actor_key(m.group(1)) and m.group(2) in RETIRED_SENTINELS:
                    out.append(f"{path}:{i} {m.group(0)}")
    return out


def load_app_trees():
    trees = {}
    for dp, _dn, fnames in os.walk(APP):
        for f in fnames:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                try:
                    with open(p, encoding="utf-8") as fh:
                        trees[os.path.relpath(p, APP)] = ast.parse(fh.read())
                except SyntaxError:
                    pass
    return trees


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 3. ONE HOME — `_caller_uid` exists once, nothing re-implements it, nothing re-adds a sentinel
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _is_uid_call(n):
    return (isinstance(n, ast.Call) and
            ((isinstance(n.func, ast.Name) and n.func.id in ("_uid_from_token", "_real_uid_from_token")) or
             (isinstance(n.func, ast.Attribute) and n.func.attr in ("_uid_from_token", "_real_uid_from_token"))))


def one_home_violations(trees, commcalc_prefix="modules/commcalc/"):
    out = []
    defs = [(rel, n) for rel, t in trees.items() for n in ast.walk(t)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_caller_uid"]
    if len(defs) != 1:
        out.append(f"_caller_uid must be defined exactly once under backend/app, found {len(defs)}: "
                   f"{[r for r, _ in defs]}")
    for rel, tree in trees.items():
        for n in ast.walk(tree):
            # `_uid_from_token(...) or '<literal>'` anywhere = a sentinel copy
            if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or) and any(_is_uid_call(v) for v in n.values):
                lits = [v.value for v in n.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
                if lits:
                    out.append(f"{rel} line {n.lineno}: _uid_from_token(...) or {lits[0]!r} — a sentinel actor")
            if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name != "_caller_uid"
                    and rel.startswith(commcalc_prefix)):
                for r in ast.walk(n):
                    if not isinstance(r, ast.Return) or r.value is None:
                        continue
                    v = r.value
                    wrapper = _is_uid_call(v) or (isinstance(v, ast.BoolOp) and any(_is_uid_call(x) for x in v.values))
                    if wrapper and (rel, n.name) not in EXCUSED_WRAPPERS:
                        out.append(f"{rel}::{n.name} line {r.lineno}: returns the token uid itself — a second copy "
                                   f"of _caller_uid; call _caller_uid(authorization) instead")
    return out


def proof_stub_violations(sources):
    """sources: {name: python text}. A proof that stubs `_caller_uid` with a non-UUID literal hides the class."""
    out = []
    for name, src in sources.items():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "_caller_uid"
                                                 for t in n.targets):
                v = n.value.body if isinstance(n.value, ast.Lambda) else n.value
                s = _const_str(v)
                if s is not None and not is_uuid(s):
                    out.append(f"{name} line {n.lineno}: stubs _caller_uid with {s!r} (not a UUID)")
    return out


def load_proof_sources():
    out = {}
    for d in (HERE, os.path.join(HERE, "scratchpad")):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.endswith(".py") and f != os.path.basename(__file__):
                with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                    out[os.path.relpath(os.path.join(d, f), HERE)] = fh.read()
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. BEHAVIOUR — the real `_caller_uid` and the real `save_category_rule`, exec'd from router.py's source
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def function_source(src, name, kind=(ast.FunctionDef, ast.AsyncFunctionDef)):
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, kind) and n.name == name:
            n.decorator_list = []
            return ast.unparse(n)
    return None


class _StubTokenModule:
    """Installs `app.modules.core.router` with a controllable `_uid_from_token` for the duration."""
    def __init__(self, resolver):
        self.resolver = resolver
        self.saved = {}

    def __enter__(self):
        for name in ("app", "app.modules", "app.modules.core", "app.modules.core.router"):
            self.saved[name] = sys.modules.get(name)
        core = types.ModuleType("app.modules.core.router")
        core._uid_from_token = self.resolver
        pkg_core = types.ModuleType("app.modules.core"); pkg_core.router = core
        sys.modules["app.modules.core"] = pkg_core
        sys.modules["app.modules.core.router"] = core
        if self.saved["app"] is None:
            sys.modules["app"] = types.ModuleType("app"); sys.modules["app"].__path__ = [APP]
        if self.saved["app.modules"] is None:
            sys.modules["app.modules"] = types.ModuleType("app.modules")
            sys.modules["app.modules"].__path__ = [os.path.join(APP, "modules")]
        return self

    def __exit__(self, *a):
        for name, mod in self.saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def build_caller_uid(src):
    ns = {"_uuid_mod": uuid, "uuid": uuid}
    exec(compile(src, "<_caller_uid>", "exec"), ns)
    return ns["_caller_uid"]


REAL_UID = "5b3f0c1e-9a4d-4e7b-8c2a-1f6d9e0a7b34"


def caller_uid_problems(fn_src):
    """-> [problems]; [] = returns only a UUID or None across every resolver shape."""
    probs = []
    if fn_src is None:
        return ["_caller_uid not found in commcalc/router.py"]
    cu = build_caller_uid(fn_src)

    def boom(_a):
        raise RuntimeError("auth down")
    cases = [("no token (resolver None)", lambda a: None, ""),
             ("empty id", lambda a: "", "Bearer x"),
             ("resolver raises", boom, "Bearer x"),
             ("resolved id is not a UUID", lambda a: "service-role", "Bearer x"),
             ("FastAPI Header sentinel object", lambda a: None, object())]
    for label, res, auth in cases:
        with _StubTokenModule(res):
            got = cu(auth)
        if got is not None:
            probs.append(f"{label}: returned {got!r}, want None")
    with _StubTokenModule(lambda a: REAL_UID.upper() if a == "Bearer good" else None):
        got = cu("Bearer good")
    if got != REAL_UID:
        probs.append(f"real token: returned {got!r}, want {REAL_UID!r}")
    # static: no string literal can be returned
    for n in ast.walk(ast.parse(fn_src)):
        if isinstance(n, ast.Return) and n.value is not None:
            lits = literals_of(n.value, None, {})
            if lits:
                probs.append(f"returns the literal(s) {sorted(lits)}")
    return probs


class _DBError(Exception):
    pass


class RecordingClient:
    """A PostgREST-shaped client that ENFORCES the migration-declared column types, the way Postgres does:
    a non-UUID into a UUID column raises `invalid input syntax for type uuid`, NULL into NOT NULL raises."""
    def __init__(self, schema):
        self.schema_map, self.writes = schema, []
        self._t = self._op = self._row = None

    def schema(self, _s):
        return self

    def table(self, t):
        self._t, self._op, self._row, self._eq = t, None, None, []
        return self

    def insert(self, row):
        self._op, self._row = "insert", row
        return self

    def update(self, row):
        self._op, self._row = "update", row
        return self

    def eq(self, c, v):
        self._eq.append((c, v))
        return self

    def execute(self):
        cols = self.schema_map.get(self._t, {})
        rows = self._row if isinstance(self._row, list) else [self._row]
        for r in rows:
            for c, v in r.items():
                meta = cols.get(c)
                if meta is None:
                    continue
                if meta["type"] == "uuid" and v is not None and not is_uuid(v):
                    raise _DBError(f'invalid input syntax for type uuid: "{v}"')
                if meta["not_null"] and v is None and self._op == "insert" and c not in ("id",):
                    raise _DBError(f'null value in column "{c}" violates not-null constraint')
        self.writes.append((self._t, self._op, rows, list(self._eq)))
        return types.SimpleNamespace(data=[{**rows[0], "id": "11111111-1111-4111-8111-111111111111"}])


class _HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(f"{status_code}: {detail}")
        self.status_code, self.detail = status_code, detail


class _Lax:
    def __init__(self, **kw):
        for k, v in type(self).__dict__.items():
            if not k.startswith("_") and not callable(v):
                setattr(self, k, v)
        for k, v in kw.items():
            setattr(self, k, v)


def build_save_category_rule(router_src, caller_uid_src, client):
    ns = {"LaxModel": _Lax, "Any": object, "HTTPException": _HTTPException,
          "Header": lambda default=None, **k: default, "ORG_ID": None,
          "require_org": lambda org_id: None, "_require_commission_admin": lambda a, o: None,
          "sb": lambda: client, "_invalidate_accessory_config": lambda org_id: 0,
          "_datetime": _dt.datetime, "_timezone": _dt.timezone, "_uuid_mod": uuid}
    exec(compile(caller_uid_src, "<_caller_uid>", "exec"), ns)
    exec(compile(function_source(router_src, "SaveCategoryRuleIn", ast.ClassDef), "<SaveCategoryRuleIn>", "exec"), ns)
    exec(compile(function_source(router_src, "save_category_rule"), "<save_category_rule>", "exec"), ns)
    return ns["save_category_rule"], ns["SaveCategoryRuleIn"]


OLD_CALLER_UID = '''
def _caller_uid(authorization):
    try:
        from app.modules.core.router import _uid_from_token
        return _uid_from_token(authorization) or 'web'
    except Exception:
        return 'web'
'''


def _import_real_icat():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    return importlib.import_module("app.modules.commcalc.installment_category")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
def main():
    with open(ROUTER, encoding="utf-8") as f:
        router_src = f.read()
    schema = parse_schema(load_migrations())
    trees = load_app_trees()
    consts = module_str_constants(trees)

    print("A. _caller_uid returns a UUID or None — never a sentinel")
    cu_src = function_source(router_src, "_caller_uid")
    probs = caller_uid_problems(cu_src)
    check("the real _caller_uid (exec'd from router.py): None for no token / empty / raising / non-UUID / "
          "Header object; the canonical uid for a real token", not probs, probs)

    print("B. one home")
    oh = one_home_violations(trees)
    check("_caller_uid defined once; no second copy of the token lookup; no `_uid_from_token(...) or 'lit'`",
          not oh, "\n        ".join(oh))
    stubs = proof_stub_violations(load_proof_sources())
    check("no proof stubs _caller_uid with a non-UUID literal (the stub that hid the live defect)",
          not stubs, stubs)
    for fn in ("_mpc_who", "_xc_who", "_agency_who"):
        src = function_source(router_src, fn) or ""
        check(f"{fn} dereferences _caller_uid", "_caller_uid(" in src and "_uid_from_token" not in src, src[:120])
    for rel, fn in EXCUSED_WRAPPERS:
        t = trees.get(rel)
        check(f"excused wrapper {rel}::{fn} still exists (the excuse list may only shrink)",
              t is not None and any(isinstance(n, ast.FunctionDef) and n.name == fn for n in ast.walk(t)))

    print("C. schema-driven sibling scan (migrations -> column types; code -> actor literals)")
    for (rel, fn, key), (tables, _why) in EXCUSED_UNRESOLVED.items():
        t = trees.get(rel)
        live = t is not None and any(isinstance(x, ast.FunctionDef) and x.name == fn for x in ast.walk(t))
        types_ = {tb: schema.get(tb, {}).get(key, {}).get("type") for tb in tables}
        check(f"excuse {rel}::{fn} {key}: still exists and no table it can name declares {key} UUID",
              live and all(v != "uuid" for v in types_.values()), (live, types_))
        for tb, v in types_.items():
            if v is None:
                print(f"        NOTE (not this lock's class — reported, not fixed): {rel}::{fn} writes {key} to "
                      f"{tb}, which declares NO such column in the migrations")
    ucols = uuid_actor_columns(schema)
    check("the migrations parse: installment_category_rule.updated_by is UUID (mig 245)",
          schema.get("installment_category_rule", {}).get("updated_by", {}).get("type") == "uuid",
          schema.get("installment_category_rule"))
    check("…and commission_org_config.updated_by is TEXT (mig 201) — the parser tells them apart",
          schema.get("commission_org_config", {}).get("updated_by", {}).get("type") == "text")
    print(f"        UUID actor columns in the migrations: "
          f"{ {c: sorted(t) for c, t in sorted(ucols.items())} }")
    writes = []
    for rel, tree in sorted(trees.items()):
        writes += actor_writes(rel, tree, consts)
    bad, text = judge_writes(writes, schema)
    check("no writer puts a non-UUID literal into a column declared UUID", not bad, "\n        ".join(bad))
    retired = retired_writes(text)
    check(f"the retired sentinel(s) {sorted(RETIRED_SENTINELS)} are written by NO actor column, TEXT or UUID",
          not retired, retired)
    fe = frontend_sentinel_sends()
    check("the frontend sends no retired sentinel as an actor (`x_by: 'web'`)", not fe, fe)
    print(f"        TEXT-column sentinel inventory ({len(text)} writes; excused in index §19.34 — see there):")
    for rel, fn, line, key, lits, tables, types_ in text:
        print(f"          {rel}::{fn}:{line}  {key} <- {lits}  tables={tables or '?'} types={types_}")

    print("D. the REAL save_category_rule against a client that enforces the declared column types")
    icat = _import_real_icat()
    check("the real installment_category module is loaded (pure, stdlib)", "tablet" in icat.CATEGORY_KEYS)
    body = dict(category_key="tablet", match_field="department", match_op="equals", match_value="Tablets")
    org = "7c1e2d3f-0000-4000-8000-00000000abcd"

    client = RecordingClient(schema)
    with _StubTokenModule(lambda a: REAL_UID if a == "Bearer good" else None):
        fn, Model = build_save_category_rule(router_src, cu_src, client)
        err = None
        try:
            r1 = fn(Model(**body), authorization="", org_id=org)
        except Exception as e:  # noqa: BLE001
            err, r1 = e, None
        check("no Authorization header: the save SUCCEEDS (was a 500) and writes updated_by = None",
              err is None and r1 and r1.get("saved") and client.writes
              and client.writes[-1][2][0]["updated_by"] is None and client.writes[-1][2][0]["org_id"] == org,
              (err, client.writes[-1:] if client.writes else None))
        r2 = fn(Model(**body), authorization="Bearer good", org_id=org)
        check("with a token: updated_by is the signed-in uid",
              r2.get("saved") and client.writes[-1][2][0]["updated_by"] == REAL_UID, client.writes[-1])
        r3 = fn(Model(**{**body, "id": "22222222-2222-4222-8222-222222222222"}), authorization="", org_id=org)
        check("update path with no token: saved, updated_by None, org-scoped (.eq org_id)",
              r3.get("saved") and client.writes[-1][1] == "update" and client.writes[-1][2][0]["updated_by"] is None
              and ("org_id", org) in client.writes[-1][3], client.writes[-1])

    old_client = RecordingClient(schema)
    with _StubTokenModule(lambda a: None):
        fn_old, Model = build_save_category_rule(router_src, OLD_CALLER_UID, old_client)
        try:
            fn_old(Model(**body), authorization="", org_id=org)
            got = None
        except _HTTPException as e:
            got = e
    check("REGRESSION: the pre-fix _caller_uid reproduces the live 500 — invalid input syntax for type uuid: \"web\"",
          got is not None and got.status_code == 500 and 'type uuid: "web"' in str(got.detail), got)

    print("E. negative controls — each planted violation must be caught")
    plant_mig = [("001_x.sql", "CREATE TABLE IF NOT EXISTS s.rule (id UUID PRIMARY KEY, updated_by UUID, note TEXT);"),
                 ("002_x.sql", "CREATE TABLE s.logt (id uuid, changed_by TEXT NOT NULL);\n"
                               "ALTER TABLE s.rule ADD COLUMN IF NOT EXISTS approved_by UUID, ADD COLUMN x_by TEXT;")]
    ps = parse_schema(plant_mig)
    check("control: planted schema parses (UUID, TEXT, NOT NULL, ALTER ADD COLUMN)",
          ps["rule"]["updated_by"]["type"] == "uuid" and ps["rule"]["approved_by"]["type"] == "uuid"
          and ps["logt"]["changed_by"]["not_null"] and ps["rule"]["x_by"]["type"] == "text", ps)

    def scan(src):
        t = ast.parse(src)
        return judge_writes(actor_writes("planted.py", t, module_str_constants({"p": t})), ps)

    planted = {
        "direct literal into a UUID column":
            "def f(c):\n    c.schema('s').table('rule').insert({'updated_by': 'web'}).execute()\n",
        "`x or 'system'` fallback into a UUID column":
            "def f(c, who):\n    row = {}\n    row['updated_by'] = who or 'system'\n    c.table('rule').upsert(row).execute()\n",
        "sentinel through a local variable":
            "def f(c, who):\n    w = str(who or '').strip() or 'api'\n    c.table('rule').update({'updated_by': w}).execute()\n",
        "sentinel through a same-module helper's return (the original defect's shape)":
            "def _who(a):\n    try:\n        return tok(a) or 'web'\n    except Exception:\n        return 'web'\n"
            "def f(c, a):\n    c.table('rule').insert({'updated_by': _who(a)}).execute()\n",
        "a NEW UUID column added by ALTER TABLE (read from the migrations, not a hardcoded list)":
            "def f(c):\n    c.table('rule').update({'approved_by': 'admin'}).execute()\n",
        "table named through a module constant":
            "RULE_TABLE = 'rule'\ndef f(c):\n    c.table(RULE_TABLE).insert({'updated_by': 'unknown'}).execute()\n",
        "parameter default sentinel":
            "def f(c, who='system'):\n    c.table('rule').insert({'updated_by': who}).execute()\n",
    }
    for label, src in planted.items():
        b, _ = scan(src)
        check(f"control CAUGHT: {label}", bool(b), b)
    t_ex = ast.parse("def g(c, tb):\n    c.table(tb).insert({'updated_by': 'web'}).execute()\n")
    w_ex = actor_writes("planted.py", t_ex, {})
    b_un, _ = judge_writes(w_ex, ps, excused={})
    check("control CAUGHT: an unresolved table + a column name that is UUID somewhere", bool(b_un), b_un)
    b_ex, _ = judge_writes(w_ex, ps, excused={("planted.py", "g", "updated_by"): (("rule",), "x")})
    check("control CAUGHT: an excuse naming a table whose column IS UUID is not honoured", bool(b_ex), b_ex)
    b, txt = scan("def f(c):\n    c.table('logt').insert({'changed_by': 'web'}).execute()\n")
    check("control NOT over-caught: a sentinel into a TEXT column is inventory, not a UUID failure",
          not b and txt, (b, txt))
    check("control CAUGHT: …but the RETIRED sentinel 'web' into that TEXT column fails the retired rule",
          bool(retired_writes(txt)))
    _b2, txt2 = scan("def f(c):\n    c.table('logt').insert({'changed_by': 'seed'}).execute()\n")
    check("control NOT over-caught: a non-retired marker ('seed') into a TEXT column", not retired_writes(txt2))
    b, _ = scan("def f(c, who):\n    c.table('rule').insert({'updated_by': who or None, "
                "'x': '00000000-0000-0000-0000-000000000001'}).execute()\n")
    check("control NOT over-caught: `who or None` and a UUID-valued literal are fine", not b, b)

    check("control CAUGHT: the frontend sending `updated_by: 'web'`",
          bool(frontend_sentinel_sends({"x.tsx": "body: JSON.stringify({ updated_by: 'web' })"})))
    check("control NOT over-caught: the frontend's `platform: 'web'` (not an actor key)",
          not frontend_sentinel_sends({"x.tsx": "register({ platform: 'web' })"}))
    bad_cu = OLD_CALLER_UID
    check("control CAUGHT: a _caller_uid that returns 'web'", bool(caller_uid_problems(bad_cu)))
    unvalidated = ("def _caller_uid(authorization):\n    try:\n        from app.modules.core.router import "
                   "_uid_from_token\n        return _uid_from_token(authorization) or None\n    except Exception:\n"
                   "        return None\n")
    check("control CAUGHT: a _caller_uid that passes a non-UUID resolved id straight through",
          any("not a UUID" in p for p in caller_uid_problems(unvalidated)), caller_uid_problems(unvalidated))
    t_two = {"modules/commcalc/a.py": ast.parse("def _caller_uid(a):\n    return None\n"),
             "modules/commcalc/b.py": ast.parse("def _new_who(a):\n    from x import _uid_from_token\n"
                                                "    return _uid_from_token(a) or None\n"),
             "modules/other.py": ast.parse("def g(a):\n    who = _uid_from_token(a) or 'web'\n    return who\n")}
    oh2 = one_home_violations(t_two)
    check("control CAUGHT: a second copy of the token lookup", any("_new_who" in v for v in oh2), oh2)
    check("control CAUGHT: `_uid_from_token(...) or 'web'` anywhere", any("'web'" in v for v in oh2), oh2)
    oh3 = one_home_violations({**t_two, "modules/commcalc/c.py": ast.parse("def _caller_uid(a):\n    return None\n")})
    check("control CAUGHT: _caller_uid defined twice", any("exactly once" in v for v in oh3), oh3)
    check("control CAUGHT: a proof stubbing _caller_uid with 'harness'",
          bool(proof_stub_violations({"p.py": 'R._caller_uid = lambda *a, **k: "harness"\n'})))
    check("control NOT over-caught: a proof stubbing _caller_uid with a UUID",
          not proof_stub_violations({"p.py": f'R._caller_uid = lambda *a, **k: "{REAL_UID}"\n'}))
    rc = RecordingClient(ps)
    try:
        rc.table("rule").insert({"updated_by": "web"}).execute(); caught = False
    except _DBError:
        caught = True
    check("control: the recording client rejects a non-UUID in a UUID column (as Postgres does)", caught)
    rc.table("logt").insert({"changed_by": "web"}).execute()
    check("control: …and accepts a string in a TEXT column", rc.writes[-1][0] == "logt")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
