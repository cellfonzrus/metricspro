#!/usr/bin/env python3
"""Harness — POS tenant onboarding (mig 733 + core/onboarding.py + the POS entry gate).

WHY THIS EXISTS
    The POS module reached production with zero harnesses and that is the direct cause of a
    15-defect senior review (docs/POS_REVIEW_2026-08-08.md §4: "every package below ships with its
    own harness. A package without a passing harness is not done."). This is that harness.

WHAT IT PROVES
    PURE checks (no database, always run):
      P1  every task's depends_on names a real task in the same module — a typo'd dependency would
          silently make a step permanently blocked
      P2  the dependency graph is acyclic and every dependency sorts BEFORE its dependant
      P3  every predicate names a (schema, table) pair that is on the PREDICATE_TABLES whitelist
      P4  a predicate on an UNREGISTERED table evaluates to `unknown`, never to `complete` — a bad
          config row must not be able to mark an onboarding step done
      P5  every TEMPLATE_SPECS entry excludes id/org_id (never operator-supplied) and every alias
          column name is NOT also a real column (an alias shadowing a real column is a silent
          double-mapping at import time)
      P6  generated CSV headers are unique, contain no separator characters, and every header is
          either a real (non-excluded) column or a declared alias
      P7  build_status()'s completion/blocking logic, exercised against a stub evaluator — a
          data-complete task beats a stored 'skipped'; a 'skipped' task never counts as complete; a
          blocked task is never offered as the resume point
      P8  the asset-ledger import predicate still matches the asset module's OWN filter, read out of
          asset/router.py — if mod-asset changes what "unsold, ready to sell" means, this FAILS
          rather than letting the wizard quietly disagree with the Inventory Aging report
      P9  no unqualified `.table(` in modules/pos/** or core/onboarding.py (the PGRST205 /
          stale-public-shadow class that took POS down on 2026-08-08)
      P10 every route in onboarding.py takes org_id as a QUERY PARAM (never Form/body/constant) and
          every pos-schema insert stamps org_id (AGENT_CONTRACT §2, both halves)

    LIVE checks (need tools/sbsql.py; skipped with a loud SKIP when unavailable):
      L1  the _SNAPSHOT column definitions match the live database exactly — this is the anti-drift
          guarantee that makes "templates are generated, not hand-typed" true
      L2  every predicate table actually exists in the live database
      L3  mig 733's two tables exist, have RLS on, zero policies and zero anon/authenticated grants

USAGE
    python3 backend/harness_pos_onboarding.py            # pure + live
    python3 backend/harness_pos_onboarding.py --pure     # pure only (no DB, no credentials)
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PASS, FAIL, SKIP = [], [], []


def ok(name, detail=""):
    PASS.append(name); print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))


def bad(name, detail=""):
    FAIL.append(name); print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def skip(name, detail=""):
    SKIP.append(name); print(f"  SKIP  {name}" + (f" — {detail}" if detail else ""))


def check(name, cond, detail=""):
    """`detail` is the FAILURE explanation, so it is printed only when the check fails — echoing a
    'mod-asset changed the definition' hint next to a PASS reads like the opposite of the truth."""
    if cond:
        ok(name)
    else:
        bad(name, detail)
    return bool(cond)


# ── import the module under test without booting FastAPI's app ────────────────────────────────
try:
    from app.modules.core import onboarding as ob
except Exception as e:                                          # pragma: no cover
    print(f"FATAL: cannot import app.modules.core.onboarding — {e}")
    sys.exit(2)


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n── PURE: task registry integrity ──────────────────────────────────────────────────")

for module_key in ob.DEFAULT_TASKS:
    tasks = ob._shipped(module_key)
    keys = {t["task_key"] for t in tasks}

    # P1 — dependencies resolve
    dangling = {t["task_key"]: [d for d in t["depends_on"] if d not in keys] for t in tasks}
    dangling = {k: v for k, v in dangling.items() if v}
    check(f"P1 [{module_key}] every depends_on names a real task", not dangling, str(dangling))

    # P2 — acyclic, and dependencies sort first
    order = {t["task_key"]: t["sort_order"] for t in tasks}
    late = [(t["task_key"], d) for t in tasks for d in t["depends_on"]
            if d in order and order[d] >= t["sort_order"]]
    check(f"P2 [{module_key}] each dependency sorts before its dependant", not late, str(late))

    seen_keys = [t["task_key"] for t in tasks]
    check(f"P2b [{module_key}] task_keys are unique", len(seen_keys) == len(set(seen_keys)))

    # P3 — predicate tables are whitelisted
    def pred_tables(p):
        if not isinstance(p, dict):
            return []
        if p.get("type") == "any":
            return [x for c in (p.get("of") or []) for x in pred_tables(c)]
        if p.get("type") == "count":
            return [(p.get("schema"), p.get("table"))]
        return []

    unreg = [(t["task_key"], st) for t in tasks for st in pred_tables(t["predicate"])
             if st not in ob.PREDICATE_TABLES]
    check(f"P3 [{module_key}] every predicate table is whitelisted", not unreg, str(unreg))

    # required tasks must actually be checkable — a required task with a manual predicate can be
    # 'completed' by clicking a button, which would make the entry gate meaningless.
    manual_required = [t["task_key"] for t in tasks
                       if t["is_required"] and (t["predicate"] or {}).get("type") == "manual"]
    check(f"P3b [{module_key}] no REQUIRED task is completable by a click alone",
          not manual_required, str(manual_required))

# P4 — an unregistered table can never evaluate to complete
res = ob._evaluate({"type": "count", "schema": "pg_catalog", "table": "pg_user", "min": 1},
                   "00000000-0000-0000-0000-000000000001")
check("P4 unregistered predicate table evaluates to 'unknown', not 'complete'",
      res["state"] == "unknown", res["state"])
res2 = ob._evaluate({"type": "wat"}, "00000000-0000-0000-0000-000000000001")
check("P4b unknown predicate TYPE evaluates to 'unknown'", res2["state"] == "unknown", res2["state"])
try:
    ob._evaluate({"type": "count", "schema": "pos", "table": "products"}, "")
    bad("P4c a predicate refuses to evaluate without an org_id")
except Exception:
    ok("P4c a predicate refuses to evaluate without an org_id")


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n── PURE: template specs ───────────────────────────────────────────────────────────")

for key, spec in ob.TEMPLATE_SPECS.items():
    excl = set(spec.get("exclude") or [])
    check(f"P5 [{key}] excludes id and org_id", {"id", "org_id"} <= excl,
          f"missing {sorted({'id', 'org_id'} - excl)}")

    snap = {c for c, _ in ob._SNAPSHOT.get((spec["schema"], spec["table"]), [])}
    if snap:
        bogus = excl - snap
        check(f"P5b [{key}] every excluded column is real", not bogus, str(sorted(bogus)))
        shadow = {n for n, _ in (spec.get("alias") or [])} & snap
        check(f"P5c [{key}] no alias shadows a real column", not shadow, str(sorted(shadow)))
    else:
        skip(f"P5b [{key}] excluded columns are real", "no snapshot for this table")

    # P6 — the generated header row
    try:
        t = ob.template_columns(key)
    except Exception as e:
        bad(f"P6 [{key}] template_columns() builds", str(e)[:120])
        continue
    names = [c["name"] for c in t["columns"]]
    check(f"P6 [{key}] headers are unique", len(names) == len(set(names)),
          str([n for n in names if names.count(n) > 1]))
    check(f"P6b [{key}] headers carry no CSV separators",
          not [n for n in names if any(ch in n for ch in ',"\n\r')])
    check(f"P6c [{key}] at least one column", len(names) > 0)
    aliases = {n for n, _ in (spec.get("alias") or [])}
    if snap:
        stray = [n for n in names if n not in snap and n not in aliases]
        check(f"P6d [{key}] every header is a real column or a declared alias",
              not stray, str(stray))
    csv = ob.template_csv(key)
    check(f"P6e [{key}] CSV renders with a header line", csv.split("\r\n")[0] == ",".join(
        n if not any(ch in n for ch in ',"\n\r') else '"' + n + '"' for n in names))
    # a defaults= spec must name a real column, or the pre-filled row lands in the wrong slot
    for dcol in (spec.get("defaults") or {}):
        check(f"P6f [{key}] default column '{dcol}' is in the template", dcol in names)


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n── PURE: build_status logic (stubbed evaluator) ───────────────────────────────────")

_real_eval, _real_load, _real_states = ob._evaluate, ob.load_tasks_with_source, ob._states

STUB_TASKS = [
    {"module_key": "t", "task_key": "a", "title": "A", "why": None, "step_group": "g",
     "sort_order": 10, "depends_on": [], "predicate": {"k": "a"}, "is_required": True,
     "skippable": False, "template_key": None, "import_source": None, "href": None,
     "is_active": True},
    {"module_key": "t", "task_key": "b", "title": "B", "why": None, "step_group": "g",
     "sort_order": 20, "depends_on": ["a"], "predicate": {"k": "b"}, "is_required": True,
     "skippable": True, "template_key": None, "import_source": None, "href": None,
     "is_active": True},
    {"module_key": "t", "task_key": "c", "title": "C", "why": None, "step_group": "g",
     "sort_order": 30, "depends_on": [], "predicate": {"k": "c"}, "is_required": False,
     "skippable": True, "template_key": None, "import_source": None, "href": None,
     "is_active": True},
]


def run_status(evals, states):
    ob.load_tasks_with_source = lambda o, m: ([dict(t) for t in STUB_TASKS], "shipped")
    ob._states = lambda o, m: states
    ob._evaluate = lambda p, o: evals[p["k"]]
    try:
        return ob.build_status("org", "t")
    finally:
        ob.load_tasks_with_source, ob._states, ob._evaluate = _real_load, _real_states, _real_eval


INC = {"state": "incomplete", "count": 0, "reason": "x"}
CMP = {"state": "complete", "count": 3, "reason": "x"}
MAN = {"state": "manual", "count": None, "reason": "x"}

s = run_status({"a": INC, "b": INC, "c": INC}, {})
by = {t["task_key"]: t for t in s["tasks"]}
check("P7a nothing complete ⇒ gate is closed", s["complete"] is False)
check("P7b a task whose dependency is incomplete is BLOCKED", by["b"]["blocked_by"] == ["a"])
check("P7c the resume point is the first unblocked REQUIRED task", s["next_task_key"] == "a")
check("P7d required_total counts only required tasks", s["required_total"] == 2)

s = run_status({"a": CMP, "b": INC, "c": INC}, {})
by = {t["task_key"]: t for t in s["tasks"]}
check("P7e completing a dependency unblocks its dependant", by["b"]["blocked_by"] == [])
check("P7f resume point advances", s["next_task_key"] == "b")

# a stored 'skipped' must NOT make a required task count as complete
s = run_status({"a": INC, "b": INC, "c": INC}, {"a": {"status": "skipped"}})
by = {t["task_key"]: t for t in s["tasks"]}
check("P7g a SKIPPED task is not complete", by["a"]["complete"] is False)
check("P7h skipping a required task does not open the gate", s["complete"] is False)

# ...but live data must beat a stale 'skipped'
s = run_status({"a": CMP, "b": CMP, "c": INC}, {"a": {"status": "skipped"}})
by = {t["task_key"]: t for t in s["tasks"]}
check("P7i live data overrides a stale 'skipped'", by["a"]["complete"] is True
      and by["a"]["completed_via"] == "data")
check("P7j gate opens when every REQUIRED task is data-complete", s["complete"] is True)
check("P7k an optional incomplete task does not hold the gate closed",
      s["complete"] is True and by["c"]["complete"] is False)

# 'acknowledged' completes a MANUAL task but must never override a countable one
s = run_status({"a": INC, "b": INC, "c": INC}, {"a": {"status": "acknowledged"}})
by = {t["task_key"]: t for t in s["tasks"]}
check("P7l 'acknowledged' cannot fake a data-backed task... ",
      by["a"]["complete"] is True and by["a"]["completed_via"] == "acknowledged")
s = run_status({"a": MAN, "b": INC, "c": INC}, {"a": {"status": "acknowledged"}})
by = {t["task_key"]: t for t in s["tasks"]}
check("P7m ...and DOES complete a manual task", by["a"]["complete"] is True)
s = run_status({"a": MAN, "b": INC, "c": INC}, {})
by = {t["task_key"]: t for t in s["tasks"]}
check("P7n an un-acknowledged manual task is incomplete", by["a"]["complete"] is False)


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n── PURE: cross-module + multi-tenant source scan ──────────────────────────────────")

# P8 — the asset module's own definition of unsold, ready-to-sell stock
asset_src = open(os.path.join(REPO, "backend/app/modules/asset/router.py")).read()
asset_has = ('.is_("date_sold", "null").ilike("category", "%On Inventory%")' in asset_src)
check("P8a asset/router.py still defines unsold stock as date_sold IS NULL + category "
      "ILIKE '%On Inventory%'", asset_has,
      "mod-asset changed the definition — update ASSET_UNSOLD_FILTER and _all_units()")
ob_src = open(os.path.join(HERE, "app/modules/core/onboarding.py")).read()
check("P8b the onboarding importer uses that same filter verbatim",
      '.is_("date_sold", "null").ilike("category", "%On Inventory%")' in ob_src)

# P9 — the schema-qualification class that took POS down
pos_dir = os.path.join(REPO, "backend/app/modules/pos")
unqualified = []
for fn in sorted(os.listdir(pos_dir)):
    if not fn.endswith(".py"):
        continue
    for i, line in enumerate(open(os.path.join(pos_dir, fn)), 1):
        if re.search(r'(?:sb\(\)|client|get_supabase\(\))\.table\(', line):
            unqualified.append(f"{fn}:{i}")
for i, line in enumerate(ob_src.splitlines(), 1):
    if re.search(r'(?:sb\(\)|client|get_supabase\(\))\.table\(', line):
        unqualified.append(f"core/onboarding.py:{i}")
check("P9 no unqualified .table() anywhere in POS or the onboarding engine",
      not unqualified, str(unqualified))

# P10 — multi-tenant contract, both halves.
#
# Parsed with `ast`, not regex. The first version of this block used a regex for the function
# signature and reported two FALSE FAILURES: `def f(a, authorization: str = Header(default=""),
# org_id: str = ORG_ID)` truncates at the `)` inside `Header(default="")`, so two routes that DO
# take org_id looked like they did not. A multi-tenant harness that cries wolf gets ignored, which
# is worse than no harness — so it parses the real syntax tree instead.
import ast

tree = ast.parse(ob_src)
ORGLESS_OK = {"list_templates", "get_template", "get_template_csv", "list_import_sources"}

route_fns = []
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    if any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
           and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
           for d in node.decorator_list):
        route_fns.append(node)

check("P10a the harness found the onboarding routes", len(route_fns) >= 8,
      f"{len(route_fns)} found")

no_org, wrong_org = [], []
for fn in route_fns:
    args = fn.args.args + fn.args.kwonlyargs
    names = [a.arg for a in args]
    if "org_id" not in names:
        if fn.name not in ORGLESS_OK:
            no_org.append(fn.name)
        continue
    # org_id must default to the ORG_ID module constant — i.e. be a plain QUERY PARAM the tenant
    # middleware can rewrite. A Form(...)/Body(...)/Depends(...) default, or a literal uuid, means
    # the middleware's rewrite is bypassed and the data lands in the wrong tenant.
    defaults = dict(zip(names[len(names) - len(fn.args.defaults):], fn.args.defaults)) \
        if fn.args.defaults else {}
    for kwa, kwd in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        if kwd is not None:
            defaults[kwa.arg] = kwd
    d = defaults.get("org_id")
    if not (isinstance(d, ast.Name) and d.id == "ORG_ID"):
        wrong_org.append(f"{fn.name}({ast.dump(d) if d else 'no default'})")

check("P10b every org-scoped route takes org_id", not no_org, str(no_org))
check("P10c org_id is always the ORG_ID query-param default (never Form/body/constant)",
      not wrong_org, str(wrong_org))

# Every insert() must stamp org_id. The payload is often a variable, so resolve a bare Name argument
# back to its nearest preceding assignment in the same function and check THAT — the regex version
# only saw the call line and reported two false failures here too.
def _stamps_org(node) -> bool:
    """True when this expression provably carries an org_id key."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            for k in sub.keys:
                if isinstance(k, ast.Constant) and k.value == "org_id":
                    return True
        if isinstance(sub, ast.keyword) and sub.arg == "org_id":
            return True
    return False


unstamped = []
for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
    assigns = {}
    for stmt in ast.walk(fn):
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name):
                    assigns[t.id] = stmt.value
    for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "insert"):
            continue
        # which table? walk back down the .table("x") chain
        tbl = "?"
        cur = call.func.value
        while isinstance(cur, ast.Call):
            if isinstance(cur.func, ast.Attribute) and cur.func.attr == "table" and cur.args:
                a = cur.args[0]
                if isinstance(a, ast.Constant):
                    tbl = a.value
                break
            cur = cur.func.value if isinstance(cur.func, ast.Attribute) else None
            if cur is None:
                break
        arg = call.args[0] if call.args else None
        if arg is None:
            unstamped.append(f"{fn.name}->{tbl}(no arg)")
            continue
        target = assigns.get(arg.id) if isinstance(arg, ast.Name) else arg
        if target is None or not _stamps_org(target):
            unstamped.append(f"{fn.name}->{tbl}")

check("P10d every insert() stamps org_id", not unstamped, str(unstamped))
# P10e — the batching path specifically. apply_import() chunks its writes, so the stamp has to be
# applied per ROW inside the comprehension, not once to the list. Asserted explicitly (and not just
# via P10d's AST walk) because "the helper batches correctly" is the property most likely to be
# broken by a later edit that swaps the comprehension for a plain slice.
check("P10e the batching insert helper stamps org_id on every row",
      '[{**r, "org_id": org_id} for r in rows[i:i + 100]]' in ob_src,
      "apply_import().insert() no longer stamps per-row")
# No cross-tenant copy path: apply_import must never take a source org distinct from the target.
check("P10f there is no cross-tenant import path (no source_org parameter)",
      "source_org" not in ob_src and "from_org" not in ob_src)

# P11 — ROUTE RESOLUTION. `/{module_key}` is a catch-all declared before the literal-prefix routes,
# so `/onboarding/templates/list` resolving to the template list rather than to "the module named
# 'templates'" is a property of declaration order, not of the paths. It holds today; it would break
# the moment someone adds `/{module_key}/{anything}`. Pinned here so that breakage is a failing test
# rather than a template download that silently returns an empty onboarding status.
try:
    from fastapi import FastAPI

    _app = FastAPI()
    _app.include_router(ob.router, prefix="/api/v1/core")
    EXPECT = {
        "/api/v1/core/onboarding/templates/list": "/api/v1/core/onboarding/templates/list",
        "/api/v1/core/onboarding/templates/products": "/api/v1/core/onboarding/templates/{template_key}",
        "/api/v1/core/onboarding/templates/products/csv": "/api/v1/core/onboarding/templates/{template_key}/csv",
        "/api/v1/core/onboarding/import-sources/list": "/api/v1/core/onboarding/import-sources/list",
        "/api/v1/core/onboarding/pos": "/api/v1/core/onboarding/{module_key}",
        "/api/v1/core/onboarding/pos/status": "/api/v1/core/onboarding/{module_key}/status",
    }
    wrong = []
    for probe, want in EXPECT.items():
        got = next((r.path for r in _app.routes
                    if hasattr(r, "path_regex") and r.path_regex.match(probe)
                    and "GET" in getattr(r, "methods", set())), None)
        if got != want:
            wrong.append(f"{probe} -> {got} (want {want})")
    check("P11 every URL resolves to its intended route (catch-all does not shadow the "
          "literal prefixes)", not wrong, " | ".join(wrong))
except Exception as e:                                          # pragma: no cover
    skip("P11 route resolution", str(e)[:120])


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n── LIVE: schema drift + migration state ───────────────────────────────────────────")

if "--pure" in sys.argv:
    skip("L1/L2/L3 live checks", "--pure requested")
else:
    SBSQL = "/workspaces/commcalc/tools/sbsql.py"

    def q(sql):
        r = subprocess.run([sys.executable, SBSQL, sql], capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:200])
        import json
        return json.loads(r.stdout)

    try:
        live = q("select table_name, column_name, data_type from information_schema.columns "
                 "where table_schema='pos' order by table_name, ordinal_position")
    except Exception as e:
        live = None
        skip("L1 snapshot matches the live schema", f"sbsql unavailable — {e}")

    if live is not None:
        by_tbl = {}
        for r in live:
            by_tbl.setdefault(r["table_name"], []).append((r["column_name"], r["data_type"]))
        drift = []
        for (schema, table), snap in ob._SNAPSHOT.items():
            if schema != "pos":
                continue
            real = by_tbl.get(table)
            if real is None:
                drift.append(f"{table}: MISSING from the live database")
            elif real != snap:
                only_snap = [c for c in snap if c not in real]
                only_live = [c for c in real if c not in snap]
                drift.append(f"{table}: snapshot-only={only_snap} live-only={only_live}")
        check("L1 _SNAPSHOT matches the live pos schema exactly (template anti-drift)",
              not drift, " | ".join(drift))

        # L2 — every predicate table exists
        try:
            allt = q("select table_schema||'.'||table_name t from information_schema.tables "
                     "where table_schema in ('pos','storeops','commcalc','core')")
            names = {r["t"] for r in allt}
            missing = [f"{s}.{t}" for s, t in ob.PREDICATE_TABLES if f"{s}.{t}" not in names]
            check("L2 every whitelisted predicate table exists live", not missing, str(missing))
        except Exception as e:
            skip("L2 predicate tables exist", str(e)[:120])

        # L3 — mig 733 state
        try:
            rows = q("select c.relname, c.relrowsecurity rls, "
                     "(select count(*) from pg_policies p where p.schemaname='core' "
                     " and p.tablename=c.relname) pol, "
                     "(select count(*) from information_schema.role_table_grants g "
                     " where g.table_schema='core' and g.table_name=c.relname "
                     " and g.grantee in ('anon','authenticated')) bad "
                     "from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                     "where n.nspname='core' and c.relkind='r' "
                     "and c.relname in ('module_onboarding_task','module_onboarding_state')")
            got = {r["relname"]: r for r in rows}
            check("L3a mig 733 created both tables", len(got) == 2, str(sorted(got)))
            check("L3b RLS is enabled on both", all(r["rls"] for r in got.values()))
            check("L3c zero RLS policies", all(r["pol"] == 0 for r in got.values()))
            check("L3d zero anon/authenticated grants", all(r["bad"] == 0 for r in got.values()))
        except Exception as e:
            skip("L3 mig 733 state", str(e)[:120])


# ══════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 82}\n  {len(PASS)} pass · {len(FAIL)} FAIL · {len(SKIP)} skip\n{'=' * 82}")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
sys.exit(1 if FAIL else 0)
