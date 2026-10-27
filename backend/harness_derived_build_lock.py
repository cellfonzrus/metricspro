"""LOCK — every derived snapshot is LIVE, rebuilt by the one post-landing hook, and cannot un-wire
(owner 2026-10-27: *"the data should be live update as soon as something changes"*; index §COMMCALC-41).

Stdlib only, static — it imports nothing from the app, so it runs in the no-deps job.

It FAILS THE BUILD when:
  A. a function anywhere under backend/app DELETE-THEN-INSERTS a snapshot into a commcalc table (the shape of
     a derived build) and is in neither `derived_builds.BUILDS` nor `derived_builds.EXCUSED_BUILDS` — a new
     snapshot nobody made live; or an EXCUSED_BUILDS entry is stale (the function no longer exists);
  B. a registered build's input tables are not reachable: no static input and no `inputs_fn`, so no landing
     could ever make it stale and "live" would be a claim nothing backs;
  C. a function anywhere WRITES one of those input tables and does not call `auto_calc.landed(` — the landing
     that makes a snapshot stale must say so (this is what was missing for `asset_ledger`, measured 53 days
     stale on 2026-10-27);
  D. the manual door and the hook diverge: a registered build's `run` target and the endpoint's handler must
     reach the SAME refresh function, and the endpoint may not call the engine's build directly;
  E. a SECOND scheduler appears: `derived_builds.run_due` referenced anywhere but `auto_calc.run_due`, or a
     thread/poller started inside `derived_builds.py`;
  F. the shared facts stop being dereferenced: the registry's input tables spelled as literals instead of
     `data_lineage_registry` constants, the debounce restated instead of imported from `auto_calc`, or a
     second copy of the auto-calc config keys;
  G. registration: the index section, the migration, the CI job, and the proof harness.

NEGATIVE CONTROLS (§N) plant each violation into a copy of the real sources and require RED.

    python3 backend/harness_derived_build_lock.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
REG = "app/modules/commcalc/derived_builds.py"
AUTO = "app/modules/commcalc/auto_calc.py"
LINEAGE = "app/modules/commcalc/data_lineage_registry.py"
INDEX = os.path.join(REPO, "docs", "SYSTEM_DATA_FLOW_INDEX.md")
MIGRATION = os.path.join(REPO, "database", "migrations", "1100_derived_build_queue.sql")
PROOF = os.path.join(HERE, "harness_derived_builds.py")

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


def _eval(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, ast.Attribute):                      # _lineage.ASSET_LEDGER -> its value
        return env.get(f"@{node.attr}", f"@{node.attr}")
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_eval(e, env) for e in node.elts)
    if isinstance(node, ast.Dict):
        return {_eval(k, env): _eval(v, env) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.BinOp):
        l, r = _eval(node.left, env), _eval(node.right, env)
        if isinstance(l, tuple) and isinstance(r, tuple):
            return l + r
    raise ValueError("not a literal")


def module_consts(src, extra=None):
    env = dict(extra or {})
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                env[node.targets[0].id] = _eval(node.value, env)
            except ValueError:
                pass
    return env


_PARSED, _FNS, _SEG = {}, {}, {}


def parse(src):
    t = _PARSED.get(src)
    if t is None:
        t = _PARSED[src] = ast.parse(src)
    return t


def functions(src):
    """[(outermost_name, node)] for every def; a method reports 'Class.method'. Cached per source text, so
    the negative controls re-parse only the file they mutated."""
    if src in _FNS:
        return _FNS[src]
    out = _FNS[src] = []

    def walk(node, outer):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = outer or ch.name
                out.append((name, ch))
                walk(ch, name)
            elif isinstance(ch, ast.ClassDef):
                for m in ch.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.append((outer or f"{ch.name}.{m.name}", m))
                        walk(m, outer or f"{ch.name}.{m.name}")
            else:
                walk(ch, outer)
    walk(parse(src), None)
    return out


def seg(src, node):
    k = (id(node), len(src))
    v = _SEG.get(k)
    if v is None:
        v = _SEG[k] = ast.get_source_segment(src, node) or ""
    return v


def fn_src(src, name):
    for n, node in functions(src):
        if n == name:
            return seg(src, node)
    return None


def table_calls(node, kinds):
    """The table names this function calls `.table(<literal>).<kind>(` on, for kind in `kinds`."""
    out = set()
    for ch in ast.walk(node):
        if not (isinstance(ch, ast.Call) and isinstance(ch.func, ast.Attribute) and ch.func.attr in kinds):
            continue
        cur = ch.func.value
        while isinstance(cur, ast.Call):
            if isinstance(cur.func, ast.Attribute) and cur.func.attr == "table" and cur.args:
                a = cur.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.add(a.value)
                break
            cur = cur.func.value if isinstance(cur.func, ast.Attribute) else None
            if cur is None:
                break
    return out


def run(tree=None):
    global P, F
    P = F = 0
    tree = tree or load_tree()
    lineage_env = module_consts(tree[LINEAGE])
    reg_src = tree[REG]
    # `_lineage.X` inside the registry resolves through the lineage module's own constants.
    env = module_consts(reg_src, extra={f"@{k}": v for k, v in lineage_env.items() if isinstance(v, str)})
    builds = tuple(env.get("BUILDS") or ())
    excused = {(b[0], b[1]) for b in (env.get("EXCUSED_BUILDS") or ())}

    # ═══ §A every derived snapshot build is classified ═══════════════════════════════════════════════════
    print("\n§A EVERY DERIVED SNAPSHOT BUILD IS CLASSIFIED")
    registered = set()
    for b in builds:
        mod, _, fn = str(b.get("run", "")).partition(":")
        path = mod.replace("app.", "app/", 1).replace(".", "/") + ".py"
        registered.add((path, fn))
    # discovery: a function that both deletes from and inserts into the SAME commcalc table is a snapshot build
    found, stale_excuse = [], []
    for path, src in sorted(tree.items()):
        for name, node in functions(src):
            dels, ins = table_calls(node, {"delete"}), table_calls(node, {"insert", "upsert"})
            both = dels & ins
            if not both:
                continue
            found.append((path, name, sorted(both)))
    unclassified = [(p, n, t) for (p, n, t) in found
                    if (p, n) not in excused and not any(p.endswith(rp) and n == rf for rp, rf in registered)
                    # the refresh one-homes are the registered runners' own call targets
                    and not p.endswith("refresh.py")]
    check("A1 every delete-then-insert snapshot build is registered or excused, with its reason",
          not unclassified, unclassified)
    for (p, fn, _w) in (env.get("EXCUSED_BUILDS") or ()):
        if p not in tree or fn_src(tree[p], fn) is None:
            stale_excuse.append((p, fn))
    check("A2 no EXCUSED_BUILDS entry is stale (the function still exists)", not stale_excuse, stale_excuse)
    check("A3 the discovery is not vacuous — it finds the builds we know about",
          len(found) >= 3, [(p, n) for p, n, _t in found])

    # ═══ §B a registered build can actually be made stale ════════════════════════════════════════════════
    print("\n§B A REGISTERED BUILD CAN BE MADE STALE")
    no_inputs = [b["key"] for b in builds if not (b.get("inputs") or b.get("inputs_fn"))]
    check("B1 every build declares inputs — static, or a declared resolver", not no_inputs, no_inputs)
    unresolved = [b["key"] for b in builds
                  for t in (b.get("inputs") or ())
                  if isinstance(t, str) and t.startswith("@")]
    check("B2 every static input is a data_lineage_registry constant, never a literal here "
          "(one fact, one home)", not unresolved, unresolved)
    bad_fn = []
    for b in builds:
        if not b.get("inputs_fn"):
            continue
        mod, _, fn = str(b["inputs_fn"]).partition(":")
        p = mod.replace("app.", "app/", 1).replace(".", "/") + ".py"
        if p not in tree or fn_src(tree[p], fn) is None:
            bad_fn.append(b["key"])
    check("B3 every declared-input resolver exists", not bad_fn, bad_fn)
    bad_run = []
    for (p, fn) in registered:
        hit = [k for k in tree if k.endswith(p)]
        if not hit or fn_src(tree[hit[0]], fn) is None:
            bad_run.append((p, fn))
    check("B4 every build's runner exists", not bad_run, bad_run)

    # ═══ §C the landing of an input table fires the hook ═════════════════════════════════════════════════
    print("\n§C A LANDING OF AN INPUT TABLE FIRES THE HOOK")
    inputs = set()
    for b in builds:
        inputs |= {t for t in (b.get("inputs") or ()) if isinstance(t, str) and not t.startswith("@")}
    check("C1 the input set resolved (the registry's constants were followed, not skipped)",
          "asset_ledger" in inputs and "raw_payment_detail" in inputs, sorted(inputs))
    # Writers of an input table that neither call the hook nor are a known non-landing shape.
    from_lineage = tuple(module_consts(tree[AUTO]).get("EXCUSED") or ()) + \
        tuple(module_consts(tree[AUTO]).get("REVIEWED_NON_LANDERS") or ())
    auto_excused = {(p, f) for (p, f, _w) in from_lineage}
    silent = []
    for path, src in sorted(tree.items()):
        if path in (REG, AUTO) or path.endswith("refresh.py"):
            continue
        for name, node in functions(src):
            wrote = table_calls(node, {"insert", "upsert"}) & inputs
            if not wrote:
                continue
            whole = seg(src, node)
            if "landed(" in whole or (path, name) in auto_excused or (path, name) in excused:
                continue
            silent.append((path, name, sorted(wrote)))
    check("C2 every writer of a derived build's input table calls auto_calc.landed(", not silent, silent)
    asset = fn_src(tree.get("app/modules/asset/router.py", ""), "process_asset_ledger_bytes") or ""
    check("C3 …and the asset-ledger landing in particular does — the measured defect, pinned",
          "landed(" in asset and "asset_ledger" in asset)

    # ═══ §D the button and the hook are the same rebuild ═════════════════════════════════════════════════
    print("\n§D THE BUTTON AND THE HOOK ARE THE SAME REBUILD")
    pay_ep = fn_src(tree.get("app/modules/payables/router.py", ""), "rebuild") or ""
    check("D1 POST /payables/rebuild goes through the refresh one home",
          "refresh.refresh(" in pay_ep.replace("_refresh.refresh(", "refresh.refresh("), pay_ep[-300:])
    check("D2 …and does NOT call the engine's build directly any more (that was the second answer)",
          "build_ledger(" not in pay_ep and "sync_payable_flags(" not in pay_ep)
    rec_ep = fn_src(tree.get("app/modules/recovery/router.py", ""), "rebuild") or ""
    check("D3 POST /recovery/rebuild goes through its refresh one home",
          "refresh.refresh(" in rec_ep.replace("_refresh.refresh(", "refresh.refresh("), rec_ep[-300:])
    check("D4 …and does NOT call build_recovery_ledger directly",
          "build_recovery_ledger(" not in rec_ep)
    pay_refresh = fn_src(tree.get("app/modules/payables/refresh.py", ""), "refresh") or ""
    check("D5 the payable refresh is the composition the button always was: build + flag sync",
          "build_ledger(" in pay_refresh and "sync_payable_flags(" in pay_refresh)
    check("D6 the refresh returns a STATUS, so its caller reads a verdict and never a row count "
          "(§COMMCALC-40)", '"status"' in pay_refresh and '"error"' in pay_refresh)

    # ═══ §E one scheduler ═══════════════════════════════════════════════════════════════════════════════
    print("\n§E ONE SCHEDULER, ONE POLLER")
    callers = [(p, n) for p, src in tree.items() for n, node in functions(src)
               if re.search(r"_builds\.run_due\(|derived_builds\.run_due\(", seg(src, node))]
    check("E1 derived_builds.run_due is called from auto_calc.run_due and nowhere else",
          callers == [(AUTO, "run_due")], callers)
    check("E2 derived_builds.py starts no thread of its own",
          "threading.Thread" not in reg_src and "Timer(" not in reg_src)
    check("E3 …and registers no scheduled job",
          "cron.schedule" not in reg_src and "add_job(" not in reg_src)
    starters = [(p, n) for p, src in tree.items() for n, node in functions(src)
                if "_builds.queue(" in seg(src, node)]
    check("E4 only the hook queues a build request", starters == [(AUTO, "_queue_builds")], starters)

    # ═══ §F the shared facts stay dereferenced ══════════════════════════════════════════════════════════
    print("\n§F THE SHARED FACTS STAY DEREFERENCED")
    check("F1 the registry imports the lineage registry for its table names",
          "data_lineage_registry as _lineage" in reg_src)
    check("F2 the debounce is IMPORTED from auto_calc, never restated",
          "_ac.is_due(" in reg_src and "_ac.merge_landings(" in reg_src and "_ac.due_at(" in reg_src)
    check("F3 …and the registry defines no debounce of its own",
          not re.search(r"^(DEFAULT_DEBOUNCE|MAX_WAIT_FACTOR|DEBOUNCE_BOUNDS)", reg_src, re.M))
    check("F4 the auto-rebuild switch is the SAME config commission rides — the registry reads it through "
          "auto_calc.load_config and spells no config key of its own",
          "_ac.load_config(" in reg_src
          and "auto_calc_on_landing" not in reg_src and "auto_calc_debounce_minutes" not in reg_src)
    check("F5 the hook asks the registry what a landing made stale — it lists no build of its own",
          "_builds.stale_builds(" in (fn_src(tree[AUTO], "landed") or ""))
    check("F6 the state table is named once", reg_src.count('"derived_build_status"') == 1)

    # ═══ §G registration ════════════════════════════════════════════════════════════════════════════════
    print("\n§G REGISTRATION")
    idx = open(INDEX, encoding="utf-8").read() if os.path.exists(INDEX) else ""
    check("G1 the index carries §COMMCALC-41", "COMMCALC-41" in idx)
    check("G2 …with a register row", re.search(r"\|\s*COMMCALC-41\s*\|\s*commcalc\s*\|", idx) is not None)
    check("G3 migration 1100 exists, grants what it creates and carries a REVERT note",
          os.path.exists(MIGRATION)
          and "grant select, insert, update, delete on commcalc.derived_build_status to service_role"
          in open(MIGRATION, encoding="utf-8").read()
          and "-- REVERT:" in open(MIGRATION, encoding="utf-8").read())
    check("G4 the registry names the migration, so an unapplied number is findable from the code",
          "1100_derived_build_queue.sql" in reg_src)
    check("G5 the proof harness exists", os.path.exists(PROOF))
    wf = os.path.join(REPO, ".github", "workflows", "org-scope-guard.yml")
    wfs = open(wf, encoding="utf-8").read() if os.path.exists(wf) else ""
    check("G6 both this lock and the proof run in CI",
          "harness_derived_build_lock.py" in wfs and "harness_derived_builds.py" in wfs)
    return P, F


def mutate(tree, path, old, new):
    """A copy of the real sources with ONE violation planted. The result must still PARSE: a control that
    breaks the syntax would turn the lock red for the wrong reason and prove nothing."""
    t = dict(tree)
    assert old in t[path], f"negative control anchor not found in {path}: {old[:70]}"
    t[path] = t[path].replace(old, new, 1)
    ast.parse(t[path])
    return t


if __name__ == "__main__":
    base = load_tree()
    p, f = run(base)
    print("\n§N NEGATIVE CONTROLS — each planted violation must turn this lock RED")
    controls = [
        ("a new snapshot build nobody made live",
         mutate(base, "app/modules/payables/unsold_inventory.py",
                "def ", 'def _rebuild_shadow_snapshot(client, org_id, rows):\n'
                        '    client.schema("commcalc").table("device_payable_ledger").delete()'
                        '.eq("org_id", org_id).execute()\n'
                        '    client.schema("commcalc").table("device_payable_ledger").insert(rows).execute()\n\n\n'
                        'def ')),
        ("the asset-ledger landing stops calling the hook",
         mutate(base, "app/modules/asset/router.py",
                'auto = _auto.landed(client, org_id, table="asset_ledger", periods=[], '
                'source="asset_ledger_upload",\n                        rows=len(rows))',
                'auto = None')),
        ("the payables button rebuilds its own way again",
         mutate(base, "app/modules/payables/router.py",
                "built = _refresh.refresh(client, org_id, carrier_id or None)",
                "built = engine.build_ledger(client, org_id, carrier_id or None)")),
        ("a second scheduler is started inside the registry",
         mutate(base, REG, "import threading",
                "import threading\n_T = threading.Thread(target=lambda: None)")),
        ("the debounce is restated instead of imported",
         mutate(base, REG, "MIGRATION = ", "MAX_WAIT_FACTOR = 6\nMIGRATION = ")),
        ("a build loses its inputs, so no landing could make it stale",
         mutate(base, REG, '"inputs": (_lineage.PAYMENT_DETAIL, _lineage.MI_RESIDUAL),', '"inputs": (),')),
        ("an input table is spelled as a literal instead of dereferenced",
         mutate(base, REG, '"inputs": (_lineage.PAYMENT_DETAIL, _lineage.MI_RESIDUAL),',
                '"inputs": ("raw_payment_detail", "@MI_RESIDUAL"),')),
    ]
    reds = 0
    for label, t in controls:
        try:
            _p, nf = run(t)
        except Exception as e:                               # a control that cannot run proves nothing
            print(f"  ERROR {label}: {type(e).__name__}: {e}")
            nf = 0
        ok = nf > 0
        reds += 1 if ok else 0
        print(f"  {'RED  ' if ok else 'GREEN'} {label}" + ("" if ok else "   <-- the lock did not catch it"))
    print(f"\n{'='*78}\n  {p} passed, {f} failed · {reds}/{len(controls)} negative controls RED\n{'='*78}")
    sys.exit(1 if (f or reds != len(controls)) else 0)
