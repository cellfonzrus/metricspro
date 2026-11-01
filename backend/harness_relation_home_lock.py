"""LOCK — a relation's SCHEMA is one fact with one home, checked against the schema of truth
(index §CORE-30; owner report 2026-10-31, "the commission did not get auto updated").

THE MEASURED DEFECT. `commcalc/router._calc_inputs` gathers every read the full Run Calculation makes
through one local `fetch(table)` that hard-codes `client.schema('commcalc')`. Two of the names passed
to it — `employees` and `stores` — are `storeops` relations. While that reader swallowed its
exceptions the reads returned [] and the calculation ran on nothing; once `core/feed_read.read_all`
was made to RAISE rather than mistake a failed read for a small feed, the same two reads killed the
whole calculation at its first line. Production, 2026-10-31: LuxeLink `error` for June-October,
Cellfonz R Us for July-October, each one "Could not find the table 'commcalc.employees' in the
schema cache". For about eight weeks nothing could recalculate, on any tenant.

Asking the general question — WHERE ELSE IS A SCHEMA ASSUMED? — found two more, and the second is
not about money at all: `_caller_rep_keys`, the one answer to "which rep rows are mine", read
`app_users` and `employees` from `public`. The read raised, the outer `except` returned None, and
None is that function's word for "not self-scoped". Every `sales_rep` was handed the unrestricted
view. The third, `clockin_evidence._employee`, resolved people against the stale four-row
`public.employees` copy instead of the 142-row live roster.

WHAT THIS LOCK FAILS THE BUILD ON:
  A. the one home disagrees with `database/migrations/` — a claim no `create table` backs, a relation
     named that no migration creates, a relation the migrations create that is not named, or a
     relation name appearing in TWO schemas (the assumption one-name-one-schema rests on);
  B. `schema_of` gains a default instead of raising — a default is how the defect shipped;
  C. a reader whose SCHEMA is hard-coded and whose TABLE is a parameter is CALLED with a table that
     lives in another schema. DERIVED from the call sites, not a pinned list of readers: the 93 such
     readers in this repo are legitimate where they are only ever called with their own schema's
     tables, and this goes red the moment one is not;
  D. a BARE-CLIENT read (`sb()` / `get_supabase()` in a module whose `sb()` is not schema-qualified)
     names a relation the migrations place outside `public`. This was exactly three sites and is now
     zero;
  E. the three measured sites in particular dereference this module — pinned, so a revert is red;
  F. registration: the index section, the CI job, and the unit test exist.

NEGATIVE CONTROLS (§N) plant each violation into a copy of the real sources in memory and require
RED. Stdlib only, static, no imports from the app — it runs in the no-deps job, and it parses the
sources rather than importing them so a broken tree still reports.

    python3 backend/harness_relation_home_lock.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.core import module_graph as MG         # noqa: E402 — pure, stdlib-only

REPO = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
#: WHICH FILE IS THE ONE HOME is not spelled here — it is read from the module graph, itself the one
#: home for "which file answers this question". A lock carrying its own copy of that fact is the
#: §19.18 defect wearing a lock's hat, and the graph guard's check H2 fails the build over it.
HOME = MG.FACTS["relation_schema"]["homes"][0]
MIGRATIONS = os.path.join(REPO, "database", "migrations")
INDEX = os.path.join(REPO, "docs", "SYSTEM_DATA_FLOW_INDEX.md")
WORKFLOWS = os.path.join(REPO, ".github", "workflows")
UNIT_TEST = os.path.join(HERE, "tests", "unit", "test_relation_home.py")

#: The three sites the defect was measured at, each with the file and the function. PINNED: a revert
#: that puts the hard-coded schema back is RED by name, not merely by the derived rules above.
MEASURED = (
    ("app/modules/commcalc/router.py", "_calc_inputs",
     "the Run Calculation's input gathering — `employees` / `stores` from `commcalc`"),
    ("app/modules/commcalc/router.py", "_caller_rep_keys",
     "the rep self-scope — `app_users` / `employees` from `public`, so no rep was scoped"),
    ("app/modules/storeops/clockin_evidence.py", "_employee",
     "the clock-in subject — the stale `public.employees` copy instead of the live roster"),
    ("app/modules/commcalc/payout_accrual.py", "_compute_day_boost",
     "the daily accrual's sibling of `_calc_inputs.fetch`, which swallowed the same failure"),
)

_CREATE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_]+)\.([a-z_]+)", re.I)
#: Clients that answer on the DEFAULT (public) schema unless `.schema()` is chained onto them.
BARE_CLIENTS = ("sb", "get_supabase", "get_supabase_admin")

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print(f"  PASS  {label}")
    else:
        F += 1
        print(f"  FAIL  {label}   {str(detail)[:900]}")


def load_tree():
    out = {}
    for root, _d, files in os.walk(APP):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                out[os.path.relpath(p, HERE).replace(os.sep, "/")] = open(p, encoding="utf-8").read()
    return out


def migration_schemas():
    """{relation: {schema, ...}} as database/migrations/ declares it — the authority."""
    out = {}
    for name in sorted(os.listdir(MIGRATIONS)):
        if not name.endswith(".sql"):
            continue
        body = open(os.path.join(MIGRATIONS, name), encoding="utf-8", errors="replace").read()
        for schema, table in _CREATE.findall(body):
            out.setdefault(table.lower(), set()).add(schema.lower())
    return out


def home_map(src):
    """relation_home.SCHEMA_OF, read out of the source rather than imported."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SCHEMA_OF" for t in node.targets):
            return {k.value: v.value for k, v in zip(node.value.keys, node.value.values)
                    if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)}
    return {}


def functions(src):
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    return [(n.name, n) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def seg(src, node):
    return ast.get_source_segment(src, node) or ""


def _schema_literal_of(call):
    """`X.schema("lit").table(...)` → "lit"; anything else → None."""
    recv = call.func.value
    if (isinstance(recv, ast.Call) and isinstance(recv.func, ast.Attribute)
            and recv.func.attr == "schema" and recv.args
            and isinstance(recv.args[0], ast.Constant)):
        return recv.args[0].value
    return None


def param_readers(src):
    """[(fn, schema, param_name, param_pos)] — readers whose schema is hard-coded and whose table is
    one of their own parameters. The shape that CANNOT know where the table lives."""
    out = []
    for _name, fn in functions(src):
        params = [a.arg for a in list(fn.args.posonlyargs) + list(fn.args.args)
                  + list(fn.args.kwonlyargs)]
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("table", "from_") and n.args):
                continue
            a = n.args[0]
            if not (isinstance(a, ast.Name) and a.id in params):
                continue
            schema = _schema_literal_of(n)
            if schema:
                out.append((fn.name, schema, a.id, params.index(a.id)))
    return out


def literal_calls(src):
    """{fn_name: {(arg_position_or_keyword, literal), ...}} for every direct call in this module."""
    out = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
            continue
        for i, arg in enumerate(n.args):
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.setdefault(n.func.id, set()).add((i, arg.value))
        for kw in n.keywords:
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                out.setdefault(n.func.id, set()).add((kw.arg, kw.value.value))
    return out


def bare_reads(path, src):
    """[(fn, table, lineno)] — `.table("literal")` on a client that is NOT schema-qualified."""
    qualified = any(name == "sb" and ".schema(" in seg(src, fn) for name, fn in functions(src))
    out = []
    for name, fn in functions(src):
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("table", "from_") and n.args):
                continue
            a = n.args[0]
            if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
                continue
            recv = n.func.value
            if not (isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name)
                    and recv.func.id in BARE_CLIENTS):
                continue
            if qualified and recv.func.id == "sb":
                continue                      # this module's own sb() carries the schema
            out.append((name, a.value, n.lineno))
    return out


# ── the checks, each a pure function of (tree, truth) so §N can mutate sources in memory ───────────
def run(tree, truth):
    src_home = tree.get(HOME, "")
    claims = home_map(src_home)

    print("\n§A THE ONE HOME AGREES WITH THE SCHEMA OF TRUTH (database/migrations/)")
    check("A0 the one home exists and parses into a map", len(claims) > 100, len(claims))
    wrong = {t: (s, sorted(truth[t])) for t, s in claims.items() if t in truth and s not in truth[t]}
    check("A1 every claim matches a `create table <schema>.<relation>` in the migrations",
          not wrong, wrong)
    invented = sorted(t for t in claims if t not in truth)
    check("A2 no relation is named that no migration creates — a guess is how this shipped",
          not invented, invented)
    unnamed = sorted(t for t in truth if t not in claims)
    check("A3 every relation the migrations create is named — a gap is a reader with nowhere to look",
          not unnamed, unnamed[:20])
    ambiguous = {t: sorted(s) for t, s in truth.items() if len(s) > 1}
    check("A4 no relation name appears in two schemas — the assumption one name maps to one schema "
          "rests on this, so it is asserted and not believed", not ambiguous, ambiguous)

    print("\n§B NOTHING IS ASSUMED")
    fn_schema_of = next((seg(src_home, n) for nm, n in functions(src_home) if nm == "schema_of"), "")
    check("B1 schema_of RAISES on an unknown relation rather than defaulting to a schema",
          "raise" in fn_schema_of and "UnknownRelation" in fn_schema_of, fn_schema_of[:200])
    check("B2 …and never reads the map with a fallback value (`.get(x, 'commcalc')` is a default "
          "wearing a method call)", ".get(" not in fn_schema_of, fn_schema_of[:200])

    print("\n§C A READER THAT TAKES THE TABLE BY NAME IS NEVER CALLED ACROSS A SCHEMA")
    mismatch = []
    for path, src in sorted(tree.items()):
        if path == HOME:
            continue
        readers = param_readers(src)
        if not readers:
            continue
        calls = literal_calls(src)
        for (fn, schema, param, pos) in readers:
            for key, val in sorted(calls.get(fn, ()), key=lambda kv: (str(kv[0]), kv[1])):
                if key != pos and key != param:
                    continue
                home = claims.get(str(val).lower())
                if home and home != schema:
                    mismatch.append((path, fn, val, f"reads {schema}.", f"lives in {home}"))
    check("C1 no reader hard-coding one schema is called with a table that lives in another — "
          "DERIVED from the call sites, which is what makes it survive a new reader",
          not mismatch, sorted(set(mismatch)))

    print("\n§D A BARE CLIENT NEVER NAMES A RELATION THAT IS NOT PUBLIC")
    stray = []
    for path, src in sorted(tree.items()):
        if path == HOME:
            continue
        for (fn, table, line) in bare_reads(path, src):
            home = claims.get(str(table).lower())
            if home and home != "public":
                stray.append((path, fn, table, f"lives in {home}", line))
    check("D1 no read on an unqualified client names a relation the migrations place outside public "
          "— this was 3 sites on 2026-10-31, one of them the rep self-scope", not stray, stray)

    print("\n§E THE MEASURED SITES DEREFERENCE THE ONE HOME (pinned — a revert is red by name)")
    for (path, fn, what) in MEASURED:
        src = tree.get(path, "")
        body = next((seg(src, n) for nm, n in functions(src) if nm == fn), None)
        if body is None:
            check(f"E {fn} still exists to be checked ({what})", False, path)
            continue
        check(f"E {fn} reads through relation_home — {what}",
              "_relation_home." in body or "relation_home." in body, body[:160])
    return P, F


def main():
    global P, F
    tree = load_tree()
    truth = migration_schemas()
    print("§0 SOURCES")
    check("0a the one home is on disk", HOME in tree)
    check("0b the migrations parse into a relation map", len(truth) > 400, len(truth))
    run(tree, truth)

    print("\n§F REGISTRATION")
    idx = open(INDEX, encoding="utf-8").read() if os.path.exists(INDEX) else ""
    check("F1 the index carries §CORE-30", "CORE-30" in idx)
    check("F2 …and names the one home", "relation_home" in idx)
    wf = ""
    for name in sorted(os.listdir(WORKFLOWS)) if os.path.isdir(WORKFLOWS) else []:
        if name.endswith((".yml", ".yaml")):
            wf += open(os.path.join(WORKFLOWS, name), encoding="utf-8").read()
    check("F3 this lock runs in CI — a lock no job runs is documentation with a stopwatch",
          "harness_relation_home_lock.py" in wf, WORKFLOWS)
    check("F4 the unit test exists beside it", os.path.exists(UNIT_TEST), UNIT_TEST)

    # ── §N negative controls ──────────────────────────────────────────────────────────────────────
    print("\n§N NEGATIVE CONTROLS — each planted violation must turn this lock RED")
    base_tree, base_truth = load_tree(), migration_schemas()

    def mutate(path, old, new):
        t = dict(base_tree)
        if old not in t.get(path, ""):
            return None
        t[path] = t[path].replace(old, new, 1)
        return t

    controls = [
        ("the one home claims a schema the migrations do not create",
         mutate(HOME, '"employees": "storeops",', '"employees": "commcalc",'), base_truth),
        ("a relation is named that no migration creates",
         mutate(HOME, '"employees": "storeops",',
                '"employees": "storeops",\n    "a_table_nobody_creates": "commcalc",'), base_truth),
        ("schema_of gains a default instead of raising",
         mutate(HOME, "        return SCHEMA_OF[key]",
                '        return SCHEMA_OF.get(key, "commcalc")'), base_truth),
        ("the Run Calculation's gathering goes back to a hard-coded commcalc",
         mutate("app/modules/commcalc/router.py",
                "            q = (_relation_home.reader(client, table)\n                 .eq('org_id', org_id))",
                "            q = client.schema('commcalc').table(table).select('*').eq('org_id', org_id)"),
         base_truth),
        ("the rep self-scope goes back to reading app_users from public",
         mutate("app/modules/commcalc/router.py",
                '_relation_home.reader(sb(), "app_users", "role,employee_id")',
                'sb().table("app_users").select("role,employee_id")'), base_truth),
        ("the clock-in subject goes back to the stale public roster",
         mutate("app/modules/storeops/clockin_evidence.py",
                '_relation_home.reader(sb(), "employees", "employee_id,name,home_store")',
                'sb().table("employees").select("employee_id,name,home_store")'), base_truth),
        ("a relation name exists in two schemas, so one name no longer means one schema",
         base_tree, {**base_truth, "employees": {"storeops", "commcalc"}}),
    ]
    reds = 0
    for (label, t, tr) in controls:
        if t is None:
            print(f"  FAIL  N control could not be planted (the source it mutates has moved): {label}")
            F += 1
            continue
        saveP, saveF = P, F
        P = F = 0
        sys.stdout = open(os.devnull, "w")
        try:
            run(t, tr)
            red = F > 0
        finally:
            sys.stdout.close()
            sys.stdout = sys.__stdout__
        P, F = saveP, saveF
        if red:
            reds += 1
            print(f"  PASS  N planted violation is caught: {label}")
        else:
            F += 1
            print(f"  FAIL  N planted violation PASSED — this lock does not catch it: {label}")
    check("N every control was planted and caught", reds == len(controls), f"{reds}/{len(controls)}")

    print(f"\n{P} passed, {F} failed")
    return 1 if F else 0


if __name__ == "__main__":
    sys.exit(main())
