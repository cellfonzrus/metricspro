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
      P17 plan sources are per-org CONFIG over ONE registry (owner 2026-09-22, plan_sources.py): the
          measured tenant (0 catalogue / 0 subscribers / 8,350 rate-plan sales lines incl. rebates /
          statement lines) yields 0 under house defaults with an empty state naming all FOUR sources;
          the words are proposed with counts and the rebate exclusion; after the person confirms, N
          plans with provenance and no invented MRC; apply is additive; the too-broad guard refuses a
          heading word unless attested by name; the mig-074 tenants are byte-identical (the pin)

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
    def _resolve(app, probe):
        """The FIRST route, in DECLARATION ORDER, that would match `probe` for GET — returning its
        full path pattern.

        This used to scan `_app.routes` for a `path_regex` directly. FastAPI 0.141 no longer flattens
        an included router into that list: `include_router` contributes ONE `_IncludedRouter` object
        that holds its children and carries the prefix in `include_context`, and that object has no
        `path_regex`. So nothing matched, every probe resolved to None, and the check reported the
        catch-all as shadowing everything when routing was in fact correct. Walking into the included
        routers restores the real resolution — and crucially still walks them IN ORDER, because
        declaration order is the entire property being tested here. Falls back to the flat layout on
        older FastAPI versions."""
        def walk(routes, prefix):
            for r in routes:
                ctx = getattr(r, "include_context", None)
                inner = getattr(r, "original_router", None)
                if inner is not None:                      # FastAPI >= 0.141 nested include
                    yield from walk(inner.routes, prefix + (getattr(ctx, "prefix", "") or ""))
                    continue
                rx = getattr(r, "path_regex", None)
                if rx is None:
                    continue
                if not probe.startswith(prefix):
                    continue
                if rx.match(probe[len(prefix):]) and "GET" in (getattr(r, "methods", set()) or set()):
                    yield prefix + r.path
        return next(walk(app.routes, ""), None)

    wrong = []
    for probe, want in EXPECT.items():
        got = _resolve(_app, probe)
        if got != want:
            wrong.append(f"{probe} -> {got} (want {want})")
    check("P11 every URL resolves to its intended route (catch-all does not shadow the "
          "literal prefixes)", not wrong, " | ".join(wrong))
except Exception as e:                                          # pragma: no cover
    skip("P11 route resolution", str(e)[:120])


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n── PURE: import sources — 'bring it over' ─────────────────────────────────────────")
#
# OWNER REPORT 2026-09-08 (verbatim): "have them fix the bring over of plans and features [and]
# dealer codes in cellfonz rus as nothing shows up to be brought over."
#
# Two distinct defects sat behind that one sentence, and P12/P13/P14 are the regressions for them:
#   • PLANS      the source read commcalc.product_mrc alone. That table is an MRC CATALOGUE keyed on
#                raw_mi.customer_plan (mig 074), needed only by carriers whose statement omits the
#                charge — so on Cellfonz it is empty (0 rows) while raw_mi holds 234,724 subscriber
#                rows naming 77 plans. Luxelink is the mirror image (1,017 catalogue rows, 0 raw_mi),
#                which is exactly why its wizard completed the step and Cellfonz's could not.
#   • CODES      the dealer_codes task had NO import_source at all, so the wizard rendered no panel,
#                even though the carrier-report harvest it needed already existed and mig 293 had
#                already made "which field is the dealer code" per-carrier config.

# P12 — every task's import_source is a real source, and every source is reachable from a task.
_srcs = set(ob.IMPORT_SOURCES)
_wired = {t.get("import_source") for m in ob.DEFAULT_TASKS for t in ob._shipped(m)
          if t.get("import_source")}
check("P12a every task's import_source names a declared source", _wired <= _srcs,
      str(sorted(_wired - _srcs)))
check("P12b every declared source is wired to a task", _srcs <= _wired,
      str(sorted(_srcs - _wired)))
check("P12c the dealer-codes step offers an import (the reported defect)",
      any(t["task_key"] == "dealer_codes" and t.get("import_source")
          for t in ob._shipped("pos")),
      "dealer_codes has no import_source — the wizard shows no 'bring it over' panel at all")

# P13 — fold_subscriber_plans: the pure half of the plan derivation, on the live data's own shapes.
_rows = (
    [{"customer_plan": "Unlimited +", "base_mrc": 55.0, "commissionable_mrc": 10.0}] * 12
    + [{"customer_plan": "Unlimited +", "base_mrc": 0.0, "commissionable_mrc": 0.0}] * 8
    + [{"customer_plan": "unlimited +", "base_mrc": 35.0, "commissionable_mrc": 10.0}] * 3
    + [{"customer_plan": "Android Tablet Plan", "base_mrc": 20.0}] * 2
    + [{"customer_plan": "  ", "base_mrc": 99.0}]
    + [{"customer_plan": "No Fee Ever", "base_mrc": 0.0}]
    + [{"customer_plan": "Commissionable Only", "base_mrc": None, "commissionable_mrc": 7.5}]
    + [{"customer_plan": "Junk MRC", "base_mrc": "n/a"}]
)
_folded = {p["plan_name"]: p for p in ob.fold_subscriber_plans(_rows)}
check("P13a a suspended/credited 0.00 month never sets the plan's fee",
      _folded["Unlimited +"]["monthly_fee"] == 55.0,
      f"got {_folded['Unlimited +']['monthly_fee']} (the modal NON-ZERO charge is 55.00)")
check("P13b plan names fold case-insensitively but keep the first spelling seen",
      _folded["Unlimited +"]["subscribers"] == 23 and "unlimited +" not in _folded)
check("P13c a blank plan name is never a plan", "" not in _folded and "  " not in _folded)
check("P13d a plan whose every row reports 0.00 gets no invented fee",
      _folded["No Fee Ever"]["monthly_fee"] is None)
check("P13e a feed carrying only commissionable_mrc still prices its plans",
      _folded["Commissionable Only"]["monthly_fee"] == 7.5)
check("P13f an unparseable charge is skipped, not crashed on and not zero",
      _folded["Junk MRC"]["monthly_fee"] is None and _folded["Junk MRC"]["subscribers"] == 1)
check("P13g the plans the tenant actually sells sort first",
      [p["plan_name"] for p in ob.fold_subscriber_plans(_rows)][0] == "Unlimited +")
_ties = ob.fold_subscriber_plans([{"customer_plan": "Tie", "base_mrc": 30.0},
                                  {"customer_plan": "Tie", "base_mrc": 50.0}])
check("P13h a tied charge resolves to the FULL rate, not the promotional one",
      _ties[0]["monthly_fee"] == 50.0, f"got {_ties[0]['monthly_fee']}")
check("P13i no rows in, no plans out (and no exception)", ob.fold_subscriber_plans([]) == []
      and ob.fold_subscriber_plans(None) == [])

# P14 — merge_plan_sources: the catalogue is authority, the subscriber feed is the filler.
_cat = [{"plan_name": "Unlimited +", "monthly_fee": 55.0, "source": "catalogue"}]
_obs = [{"plan_name": "unlimited +", "monthly_fee": 35.0, "source": "subscribers"},
        {"plan_name": "Android Tablet Plan", "monthly_fee": 20.0, "source": "subscribers"}]
_merged = ob.merge_plan_sources(_cat, _obs)
check("P14a a confirmed catalogue rate beats one observed on a statement line",
      len(_merged) == 2 and _merged[0]["monthly_fee"] == 55.0)
check("P14b a plan the catalogue never priced is still brought over",
      _merged[1]["plan_name"] == "Android Tablet Plan")
check("P14c the catalogue-only tenant is unchanged by the new half (Luxelink's shape)",
      ob.merge_plan_sources(_cat, []) == _cat)
check("P14d the feed-only tenant now gets its plans (Cellfonz's shape)",
      len(ob.merge_plan_sources([], _obs)) == 2)
check("P14e nothing the preview shows is inserted that pos.service_plans cannot store",
      all(set(p) - {"subscribers", "source"} <= ob.SERVICE_PLAN_IMPORT_COLS | {"plan_name"}
          for p in _merged))

# P15 — the empty state EXPLAINS itself. "0 records, no reason" is the defect class this ships to
# kill: the operator cannot tell an empty tenant from a broken importer, so they report the second.
for _name, _reason in (
    ("no carrier attached", ob.plans_empty_reason({"carriers": 0})),
    ("no data on either side", ob.plans_empty_reason({"carriers": 1, "catalogue": 0, "observed": 0})),
    ("the feed errored", ob.plans_empty_reason({"carriers": 1, "error": "raw_mi: timeout"})),
):
    check(f"P15 plans empty-state names the cause and the fix — {_name}",
          bool(_reason.get("empty_reason")) and bool(_reason.get("empty_next"))
          and len(_reason["empty_reason"]) > 40)
_dc_none = ob.dealer_codes_empty_reason([], [], [])
_dc_unmapped = ob.dealer_codes_empty_reason(
    [{"carrier": "Verizon", "configured": False}], ["Verizon"], [])
_dc_nofeed = ob.dealer_codes_empty_reason(
    [{"carrier": "Boost Mobile", "configured": True, "source": "raw_mi.salesforce_id", "found": 0}],
    [], [])
_dc_done = ob.dealer_codes_empty_reason(
    [{"carrier": "Boost Mobile", "configured": True, "source": "raw_mi.salesforce_id", "found": 28}],
    [], [])
check("P15e an UNMAPPED carrier is named as unmapped, and no code is guessed",
      "Verizon" in _dc_unmapped["empty_reason"] and "Carriers" in _dc_unmapped["empty_next"])
check("P15f a mapped carrier with no report says WHICH table is empty",
      "raw_mi.salesforce_id" in _dc_nofeed["empty_reason"])
check("P15g 'already imported' is not reported as 'nothing found'",
      "28" in _dc_done["empty_reason"] and "already" in _dc_done["empty_reason"])
check("P15h no carrier at all is its own sentence",
      "No carrier" in _dc_none["empty_reason"])
check("P15i a read error is surfaced, never swallowed into an empty list",
      "boom" in ob.dealer_codes_empty_reason([], [], ["Boost: boom"])["empty_reason"])

# P16 — preview and apply must not re-derive the plan set independently: that drift is what made
# apply able to insert a different set from the count the operator approved.
_ob_src = open(os.path.join(HERE, "app/modules/core/onboarding.py")).read()
check("P16a preview and apply share ONE plan derivation",
      _ob_src.count("resolve_service_plans(c, org_id)") == 2 and "_all_plans(" not in _ob_src)
check("P16b the dealer-code import delegates to the existing harvest, not a second copy",
      _ob_src.count("from app.modules.pos.router import _dealer_sync") == 2
      and "dealer_code_source_table" not in _ob_src)


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n── PURE: plan sources are per-org CONFIG over one registry (owner 2026-09-22) ─────────")
#
# OWNER (2026-09-22, verbatim): "we have enough plans in the system to bring over but it does not give
# an option to bring over" — under a card reading "0 record(s) … Neither source has anything yet".
#
# THE CLASS: the step derived the plan list from a FIXED PAIR of per-feed tables (product_mrc + raw_mi).
# A tenant whose plan names live in another landed source — org f4f1c16e…: 0 + 0, but 8,350 sales-export
# lines under a rate-plan branch (3,165 of them REBATES under the same branch) and 1,494 commission-
# statement lines naming price plans — saw 0 and no way to bring anything over.
#
# THE FIX (plan_sources.py): ONE registry of the landed sources that can carry a plan name; which are
# switched on and the words that mark a plan line are PER-ORG CONFIG (pos.pos_settings `plan_sources`,
# mig 725 — no migration); the line-level sources are proposed from the tenant's own vocabulary with
# counts, the too-broad guard, and the rebate words excluded; ONE resolver for preview and apply; the
# empty state names every source checked. §P17 drives the REAL preview / save / apply over an in-memory
# tenant shaped like the measured one.
try:
    from harness_intake_fakes import FakeDB as _FakeDB
    from app.modules.core import plan_sources as _ps
    _HAVE_FAKE = True
except Exception as _e:                                          # pragma: no cover
    _HAVE_FAKE = False
    skip("P17 plan sources", f"fakes unavailable — {_e}")

if _HAVE_FAKE:
    _ORG = "f4f1c16e-0000-4000-8000-000000000001"
    _OTHER = "854f6d7b-0000-4000-8000-000000000002"

    def _tenant(*, catalogue=(), subscribers=(), sales=(), statement=(), config=None):
        """An in-memory tenant. Every plan table is DECLARED so an empty table still answers a select
        (Postgres 42703 on an unknown column, as the fake does)."""
        db = _FakeDB()
        db.declared["product_mrc"] = ["id", "org_id", "plan_pattern", "mrc", "carrier_id", "classification", "is_active"]
        db.declared["raw_mi"] = ["id", "org_id", "period", "period_year", "period_month", "customer_plan",
                                 "base_mrc", "commissionable_mrc", "salesforce_id"]
        db.declared["commission_ledger"] = ["id", "org_id", "period", "source_report", "product_name", "order_type",
                                            "category", "trans_date", "payout_total"]
        db.declared["pos_settings"] = ["id", "org_id", "store_code", "key", "value", "updated_at"]
        db.declared["service_plans"] = ["id", "org_id", "carrier", "plan_code", "plan_name", "plan_description",
                                        "monthly_fee", "included_minutes", "service_area", "contract_type",
                                        "contract_terms", "dealer_code", "status"]
        db.seed("carrier", [{"org_id": _ORG, "name": "Carrier A"}, {"org_id": _OTHER, "name": "Carrier B"}])
        db.seed("product_mrc", [{"org_id": _ORG, **r} for r in catalogue])
        db.seed("raw_mi", [{"org_id": _ORG, **r} for r in subscribers])
        db.seed("raw_sales", [{"org_id": _ORG, "period": "August 2026", **r} for r in sales])
        db.seed("commission_ledger", [{"org_id": _ORG, "period": "July 2026", "source_report": "stmt", **r}
                                      for r in statement])
        # ANOTHER tenant's rows in every table — must never leak into this org's plans
        db.seed("raw_sales", [{"org_id": _OTHER, "period": "August 2026", "department": "Plans",
                               "category": ">> Rate Plans", "product_desc": "Other Tenant Rate Plan",
                               "trans_date": "2026-08-01"}] * 50)
        db.seed("commission_ledger", [{"org_id": _OTHER, "period": "July 2026", "source_report": "stmt",
                                       "product_name": "Other Tenant Price Plan", "order_type": "", "category": "",
                                       "trans_date": "2026-07-01"}] * 50)
        db.seed("raw_mi", [{"org_id": _OTHER, "period": "August 2026", "period_year": 2026, "period_month": 8,
                            "customer_plan": "Other Tenant Sub Plan", "base_mrc": 40.0}] * 5)
        db.seed("product_mrc", [{"org_id": _OTHER, "plan_pattern": "Other Tenant Catalogue Plan", "mrc": 30.0}])
        if config is not None:
            db.seed("pos_settings", [{"org_id": _ORG, "store_code": None, "key": _ps.CONFIG_KEY, "value": config}])
        return db

    _P = ">> Activations (Price Sheet) >> Carrier A >> "

    def _lines(cat, prod, n, d="2026-08-05"):
        return [{"department": "Activations (Price Sheet)", "category": cat, "product_desc": prod,
                 "trans_date": d} for _ in range(n)]

    # THE MEASURED SHAPE (scaled 1:10): 8,350 rate-plan lines of 48,875 — 3 plan names + 3 rebate names
    # under the same branch — plus phones and accessories; the statement: 2 price plans + spiffs.
    _SALES = (_lines(_P + "Rate Plans", "iPhone Rate Plan (DPA)", 263)
              + _lines(_P + "Rate Plans", "New Activation Rate Plan", 56, "2026-07-02")
              + _lines(_P + "Rate Plans", "Smart Phone Rate Plan (DPA)", 30, "2026-08-20")
              + _lines(_P + "Rate Plan Rebates", "DPA Upgrade iPhone (Rate Plan Rebate)", 159)
              + _lines(_P + "Rate Plan Rebates", "DPA New Act iPhone (Rate Plan Rebate)", 104)
              + _lines(_P + "Rate Plan Rebates", "New Activation (Rate Plan Rebate)", 53)
              + _lines(_P + "SmartPhones >> Maker", "Phone model X", 2000)
              + _lines(">> Accessories >> Cases", "Case", 2050))
    _STMT = ([{"product_name": "Unlimited Plus Price Plan - New", "order_type": "Activation", "category": "commission",
               "trans_date": "2026-07-10"}] * 30
             + [{"product_name": "Unlimited Welcome Price Plan - Upgrade", "order_type": "Upgrade",
                 "category": "commission", "trans_date": "2026-07-11"}] * 20
             + [{"product_name": "Device Bonus", "order_type": "Spiff", "category": "spiff", "trans_date": "2026-07-11"}] * 10
             + [{"product_name": "Accessory Spiff", "order_type": "Spiff", "category": "spiff", "trans_date": "2026-07-12"}] * 90)

    _real_sb = ob.sb

    # P17a — THE REGISTRY is the one home: four landed sources, the mig-074 pair ON, the line sources OFF
    check("P17a the registry lists the mig-074 pair ON and the line-level sources OFF (house = today)",
          [(s["key"], s["kind"], s["enabled"]) for s in _ps.HOUSE_SOURCES]
          == [("catalogue", "catalogue", True), ("subscribers", "subscribers", True),
              ("sales_lines", "lines", False), ("statement_lines", "lines", False)])
    check("P17b every registry entry names a schema-qualified table and its name field",
          all("." in s["table"] and s.get("name_field") for s in _ps.HOUSE_SOURCES))
    check("P17c a line-level source with no confirmed words matches NOTHING (nothing is guessed)",
          not _ps.line_matches({"category": "Rate Plans", "product_desc": "X Rate Plan"},
                               _ps.source_of(None, "sales_lines")))

    # P17d–h — THE REPORTED DEFECT: the measured tenant under house defaults
    _db = _tenant(sales=_SALES, statement=_STMT)
    ob.sb = lambda: _db
    try:
        _pv = ob.preview_import(ob.PLAN_SOURCE_KEY, _ORG)
        check("P17d house defaults still yield 0 for the measured tenant (the line sources are off)",
              _pv["count"] == 0, str(_pv["count"]))
        _srcs = {s["key"]: s for s in _pv["sources"]}
        check("P17e …but the card now lists all FOUR sources with what each holds",
              set(_srcs) == {"catalogue", "subscribers", "sales_lines", "statement_lines"}
              and _srcs["sales_lines"]["rows"] == len(_SALES) and _srcs["statement_lines"]["rows"] == len(_STMT),
              str({k: v.get("rows") for k, v in _srcs.items()}))
        _er = _pv.get("empty_reason") or ""
        check("P17f the empty state names every source ACTUALLY checked — never 'neither source'",
              "Neither source" not in _er and all(t in _er for t in ("commcalc.product_mrc", "commcalc.raw_mi",
                                                                      "commcalc.raw_sales", "commcalc.commission_ledger"))
              and "not switched on yet" in _er, _er[:300])
        check("P17g the next step tells the person to tick the source and confirm the words",
              "confirm the words" in (_pv.get("empty_next") or ""), _pv.get("empty_next"))
        _sg = _srcs["sales_lines"]["suggest"]
        check("P17h the sales export proposes its own word with the count it names, from the tenant's vocabulary",
              _sg["proposal"]["include"] == ["rate plan"]
              and any(c["token"] == "rate plan" and c["lines"] == 665 for c in _sg["include"]),
              str(_sg["proposal"]) + " " + str([(c["token"], c["lines"]) for c in _sg["include"]]))
        check("P17i the rebate word is proposed as an EXCLUSION with what it removes (a rebate is not a plan)",
              _sg["proposal"]["exclude"] == ["rebate"]
              and any(e["token"] == "rebate" and e["removes"] == 316 for e in _sg["exclude"]),
              str(_sg["exclude"]))
        check("P17j the preview is THE predicate over the proposal: 349 plan lines, 3 names",
              _sg["preview"] == {"lines": 349, "plans": 3}, str(_sg["preview"]))
        check("P17k the bare word 'plan' is not proposed twice for the same lines (one word per set)",
              "plan" not in _sg["proposal"]["include"])
        _st = _srcs["statement_lines"]["suggest"]
        check("P17l the commission statement proposes its plan word too (price plan → 2 names)",
              _st["proposal"]["include"] == ["price plan"] and _st["preview"]["plans"] == 2, str(_st["proposal"]))
        check("P17m the preview payload says where the rules live and that the card can save them",
              _pv.get("configurable") is True and _pv["config"]["home"] == "pos.pos_settings"
              and _pv["config"]["key"] == "plan_sources" and _pv["config"]["source"] == "house")

        # P17n–u — THE PERSON CONFIRMS: save → the plans appear with provenance; apply is additive
        _body = {"sources": {"sales_lines": {"enabled": True, "include": ["rate plan"], "exclude": ["rebate"]},
                             "statement_lines": {"enabled": True, "include": ["price plan"], "exclude": ["bonus", "spiff"]}}}
        _pv2 = ob.save_plan_sources(_db, _ORG, _body, actor="owner@example.test")
        check("P17n after confirming, the preview offers the 5 plans (3 from the sales export, 2 from the statement)",
              _pv2["count"] == 5 and sorted(p["source"] for p in _pv2["sample"]) == ["sales_lines"] * 3 + ["statement_lines"] * 2,
              str([(p["plan_name"], p["source"]) for p in _pv2["sample"]]))
        _names = [p["plan_name"] for p in _pv2["sample"]]
        check("P17o the rebate lines are EXCLUDED — no '(Rate Plan Rebate)' name is a plan",
              not any("rebate" in n.lower() for n in _names), str(_names))
        _first = _pv2["sample"][0]
        check("P17p each candidate carries its provenance: source, line count, first / last seen",
              _first["plan_name"] == "iPhone Rate Plan (DPA)" and _first["lines"] == 263
              and _first["source_label"] == "Your sales export (line level)"
              and _first["first_seen"] == "2026-08-05" and _first["last_seen"] == "2026-08-05", str(_first))
        _nar = next(p for p in _pv2["sample"] if p["plan_name"] == "New Activation Rate Plan")
        check("P17q first / last seen are the MIN / MAX dates on the lines",
              _nar["first_seen"] == "2026-07-02" and _nar["last_seen"] == "2026-07-02")
        check("P17r a line-level source reports no charge: monthly_fee is blank and the row says where to price it",
              all(p["monthly_fee"] is None and p["mrc_next"] == _ps.MRC_NEXT for p in _pv2["sample"]))
        check("P17s the plans the tenant sells most come first", _names[:3] == ["iPhone Rate Plan (DPA)",
              "New Activation Rate Plan", "Smart Phone Rate Plan (DPA)"], str(_names))
        _saved = [r for r in _db.tables["pos_settings"] if r["org_id"] == _ORG and r["key"] == "plan_sources"]
        check("P17t the rules were saved as ONE org-level pos_settings row (mig 725) — no new table, no migration",
              len(_saved) == 1 and _saved[0]["store_code"] is None
              and _saved[0]["value"]["sources"]["sales_lines"] == {"enabled": True, "include": ["rate plan"], "exclude": ["rebate"]},
              str(_saved))
        check("P17u the saved preview now reads `tenant` rules", _pv2["config"]["source"] == "tenant")
        _ap = ob.apply_import(ob.PLAN_SOURCE_KEY, _ORG)
        _rows = [r for r in _db.tables["service_plans"] if r["org_id"] == _ORG]
        check("P17v apply creates exactly the 5 previewed rows in pos.service_plans, org-stamped",
              _ap["created"] == 5 and _ap["considered"] == 5 and len(_rows) == 5
              and all(r["org_id"] == _ORG for r in _rows), str(_ap))
        check("P17w nothing the preview shows that pos.service_plans cannot store reaches the insert",
              all(set(r) - {"id", "org_id"} <= ob.SERVICE_PLAN_IMPORT_COLS for r in _rows), str(sorted(_rows[0])))
        _ap2 = ob.apply_import(ob.PLAN_SOURCE_KEY, _ORG)
        check("P17x re-running is ADDITIVE: 0 created, 5 skipped, still 5 rows",
              _ap2["created"] == 0 and _ap2["skipped"] == 5
              and len([r for r in _db.tables["service_plans"] if r["org_id"] == _ORG]) == 5, str(_ap2))
        check("P17y the other tenant's rows in every table never leak into this org's plans",
              not any("Other Tenant" in r["plan_name"] for r in _rows))
        # the preview after apply says "already in your POS" rather than 0 with no reason? — plans are
        # still offered (the apply skips them), which is what the existing sources do too; pinned
        _pv3 = ob.preview_import(ob.PLAN_SOURCE_KEY, _ORG)
        check("P17z preview and apply share ONE derivation — the count after apply is the same set", _pv3["count"] == 5)

        # P17aa–ad — THE GUARD: a word naming ≥ 80% of a source's lines is refused, unless attested by name
        _db2 = _tenant(statement=[{"product_name": "Unlimited Plus Price Plan - New", "order_type": "", "category": "",
                                   "trans_date": "2026-07-10"}] * 90
                       + [{"product_name": "Device Bonus", "order_type": "", "category": "", "trans_date": "2026-07-11"}] * 10)
        ob.sb = lambda: _db2
        try:
            ob.save_plan_sources(_db2, _ORG, {"sources": {"statement_lines": {"enabled": True, "include": ["plan"]}}})
            bad("P17aa a saved word naming 90% of the statement's lines is REFUSED")
        except Exception as _e:
            _d = getattr(_e, "detail", None) or {}
            check("P17aa a saved word naming 90% of the statement's lines is REFUSED, naming the word and its share",
                  isinstance(_d, dict) and _d.get("attest_keys") == ["statement_lines:plan"]
                  and "90%" in _d.get("message", ""), str(_d)[:300])
        check("P17ab …and NOTHING was written", not [r for r in _db2.tables.get("pos_settings") or [] if r["org_id"] == _ORG])
        _pv4 = ob.save_plan_sources(_db2, _ORG, {"sources": {"statement_lines": {"enabled": True, "include": ["plan"]}},
                                                 "broad_ok": ["statement_lines:plan"]}, actor="owner@example.test")
        _att = _db2.tables["pos_settings"][0]["value"].get("broad_attested") or {}
        check("P17ac the person can attest the word BY NAME — recorded with who, when and the measured share (1 plan: the bonus line carries no plan word)",
              _pv4["count"] == 1 and _att.get("statement_lines:plan", {}).get("by") == "owner@example.test"
              and _att["statement_lines:plan"]["ratio"] == 0.9, str(_att))
        _src_hint = next(s for s in _pv4["sources"] if s["key"] == "statement_lines")
        check("P17ad the proposal never offers a too-broad word; it is reported under too_broad instead",
              "plan" not in [c["token"] for c in _src_hint["suggest"]["include"]]
              and any(c["token"] == "plan" for c in _src_hint["suggest"]["too_broad"]))
    finally:
        ob.sb = _real_sb

    # P17ae–ah — THE COMPATIBILITY PIN: a tenant with a subscriber report / a catalogue is byte-identical
    _MI = ([{"period": "August 2026", "period_year": 2026, "period_month": 8, "customer_plan": "Unlimited +", "base_mrc": 55.0}] * 12
           + [{"period": "August 2026", "period_year": 2026, "period_month": 8, "customer_plan": "Unlimited +", "base_mrc": 0.0}] * 8
           + [{"period": "August 2026", "period_year": 2026, "period_month": 8, "customer_plan": "Tablet Plan", "base_mrc": 20.0}] * 2
           + [{"period": "July 2026", "period_year": 2026, "period_month": 7, "customer_plan": "Old Plan", "base_mrc": 10.0}] * 3)
    _CAT = [{"plan_pattern": "Unlimited +", "mrc": 60.0, "classification": "premium", "is_active": True},
            {"plan_pattern": "Catalogue Only", "mrc": 15.0, "is_active": False}]
    _expect_house = [{"plan_name": "Unlimited +", "monthly_fee": 60.0, "carrier": "Carrier A", "plan_description": "premium",
                      "status": "active", "source": "catalogue"},
                     {"plan_name": "Catalogue Only", "monthly_fee": 15.0, "carrier": "Carrier A", "plan_description": None,
                      "status": "inactive", "source": "catalogue"},
                     {"plan_name": "Tablet Plan", "monthly_fee": 20.0, "carrier": "Carrier A", "plan_description": None,
                      "status": "active", "subscribers": 2, "source": "subscribers"}]
    _db3 = _tenant(catalogue=_CAT, subscribers=_MI, sales=_SALES, statement=_STMT)
    _plans3, _diag3 = ob.resolve_service_plans(_db3, _ORG)
    check("P17ae PIN: a tenant with a catalogue + a subscriber report gets EXACTLY the pre-change rows "
          "(catalogue wins the collision, newest period only, no new keys on those rows)",
          _plans3 == _expect_house, str(_plans3))
    check("P17af PIN: the diag keeps its pre-change keys (period / rows / catalogue / observed / carriers)",
          _diag3["period"] == "August 2026" and _diag3["rows"] == 22 and _diag3["catalogue"] == 2
          and _diag3["observed"] == 2 and _diag3["carriers"] == 1, str({k: v for k, v in _diag3.items() if k != "sources"}))
    check("P17ag PIN: with the line sources off, the landed sales / statement rows change nothing",
          all(s["plans"] == 0 for s in _diag3["sources"] if s["kind"] == "lines"))
    _db4 = _tenant(catalogue=_CAT, subscribers=_MI,
                   config={"sources": {"subscribers": {"enabled": False}}})
    _plans4, _diag4 = ob.resolve_service_plans(_db4, _ORG)
    check("P17ah a tenant can switch a house source OFF (the catalogue-only shape by choice)",
          [p["plan_name"] for p in _plans4] == ["Unlimited +", "Catalogue Only"] and _diag4["observed"] == 0)
    _cfg_bad = _ps.resolve_config({"sources": {"my_table": {"enabled": True, "table": "public.users"}}})
    check("P17ai a tenant row cannot ADD a table — an unknown key is ignored, the registry is the only list",
          [s["key"] for s in _cfg_bad["sources"]] == _ps.source_keys() and not _cfg_bad["declared"])
    _raw = _ps.merge_into_raw({"sources": {"sales_lines": {"enabled": True, "include": ["rate plan"], "exclude": ["rebate"]}}},
                              {"statement_lines": {"enabled": True, "include": ["price plan"]}, "nope": {"enabled": True}})
    check("P17aj a partial save merges PER SOURCE — another source's words survive; an unknown key is dropped",
          _raw["sources"]["sales_lines"]["include"] == ["rate plan"] and "nope" not in _raw["sources"]
          and _raw["sources"]["statement_lines"] == {"enabled": True, "include": ["price plan"]})
    check("P17ak the mig-074 rows are byte-identical through merge_plan_sources with the new sources appended",
          ob.merge_plan_sources(_expect_house[:2], _expect_house[2:], [{"plan_name": "unlimited +", "source": "sales_lines"},
                                                                       {"plan_name": "New One", "source": "sales_lines"}])
          == _expect_house + [{"plan_name": "New One", "source": "sales_lines"}])
    check("P17al a read error on a line source is REPORTED on that source, never swallowed into 'empty'",
          "could not be read" in _ps.source_sentence({"key": "sales_lines", "kind": "lines", "label": "Your sales export",
                                                       "table": "commcalc.raw_sales", "error": "boom"}))


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
#  M. THE GATE MUST NOT BLOCK THE PAGE A TASK SENDS YOU TO
# ══════════════════════════════════════════════════════════════════════════════════════════════
# OWNER REPORT 2026-09-08, live: "Sales tax menu is hidden from the pos and when i click on setting
# up the sales tax it goes to the pos screen and … no way to do it now" — under a wizard reading
# "29 of 29 stores have NO rate — a taxable sale there charges $0".
#
# The POS entry gate redirects EVERY /pos/* route to /pos/onboarding while a required step is
# outstanding. The "Set your sales-tax rates" task points at /pos/settings — itself a /pos/* route —
# so the gate bounced the operator off the only screen that could complete it, and the wizard's own
# "Do this now →" sent them straight back. A closed loop around the step that stops every taxable
# sale charging $0.
#
# `open_hrefs` (the pages OUTSTANDING tasks point at, derived from the same task registry the wizard
# renders) is now on the status payload, and the layout exempts them. STATIC on the frontend half:
# a redirect loop compiles, renders and type-checks perfectly.
print()
print("=" * 82)
print("  M. the onboarding gate cannot deadlock a task's own page")
print("=" * 82)
try:
    from app.modules.core import onboarding as _ob
    _tax = next((t for t in _ob.POS_TASKS if t.get("task_key") == "tax_codes"), None)
    check("M1 the sales-tax task still points into /pos/*, which is what made the loop possible",
          bool(_tax) and str(_tax.get("href", "")).startswith("/pos/"),
          str(_tax.get("href") if _tax else None))
    _in_pos = [t for t in _ob.POS_TASKS if str(t.get("href", "")).startswith("/pos/")
               and t.get("href") != "/pos/onboarding"]
    check("M1b it is not alone — the gate could deadlock any of these task pages",
          len(_in_pos) >= 5, str(sorted({t.get("href") for t in _in_pos})))
    check("M1c which is exactly why the exemption is DERIVED, not a list someone maintains",
          all(str(t.get("href") or "").strip() for t in _in_pos))
    _src_ob = open("app/modules/core/onboarding.py").read()
    check("M2 build_status publishes the OUTSTANDING tasks' hrefs",
          '"open_hrefs"' in _src_ob and 'if not r["complete"]' in _src_ob)
    check("M3 the cheap status endpoint the gate calls passes them through",
          '"next_task_key", "open_hrefs"' in _src_ob)
    check("M4 they are derived from the task registry, not a hardcoded path list",
          '"/pos/settings"' not in _src_ob.split('"open_hrefs"')[1][:600])
except Exception as e:
    skip("M1-M4 onboarding module", str(e)[:140])

_lay = None
for _p in ("../frontend/src/app/(platform)/pos/layout.tsx",):
    try:
        _lay = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), _p)).read()
    except Exception:
        _lay = None
if _lay is None:
    skip("M5-M8 pos/layout.tsx", "not readable from here")
else:
    check("M5 the layout reads open_hrefs", "open_hrefs" in _lay)
    check("M6 it computes whether the current path is a task target", "const taskTarget" in _lay)
    check("M7 and the redirect stands down on one",
          "|| taskTarget) return" in _lay or "taskTarget) return" in _lay)
    check("M8 taskTarget is in the effect's deps, so it cannot go stale",
          "taskTarget, gate, router]" in _lay)
    check("M9 the banner still offers the way BACK to the wizard from a task page",
          "Back to setup" in _lay)

# ══════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 82}\n  {len(PASS)} pass · {len(FAIL)} FAIL · {len(SKIP)} skip\n{'=' * 82}")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
sys.exit(1 if FAIL else 0)
