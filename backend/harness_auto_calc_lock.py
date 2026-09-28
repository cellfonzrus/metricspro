"""LOCK — "data landed for org X, period P" has ONE home, every lander calls it, and nothing else starts a
calculation (owner 2026-09-28: "when sept is uploaded the system should calculate automatically without manual
intervention"; index §6l). Stdlib only, static — it imports nothing from the app, so it runs in the no-deps job.

It FAILS THE BUILD when:
  A. a function anywhere under backend/app WRITES rows into a table the Run Calculation reads (or a sales sibling
     of one) — a literal `.table('<feed>').insert/upsert(`, a variable-table insert/upsert into the commcalc schema,
     a generic writer (`safe_replace(` / `ingest_report_rows(`) or the POS promotion RPC — and is in NEITHER
     `auto_calc.LANDERS` nor `auto_calc.EXCUSED` (a new lander nobody wired);
  B. a listed lander stops calling the hook (`auto_calc.landed(`), or a LANDERS entry is stale (the function no
     longer writes, or no longer exists);
  C. a second auto-calc trigger appears: `_run_calculation` referenced anywhere but the Run Calculation button
     (`calculate`) and the hook's runner (`auto_calc._default_runner`); or `_calc_rep_rows` (the calculation
     body) reached from anywhere but `_run_calculation` / `recompute_rep`;
  D. the hook bypasses `_run_calculation`: the runner must call exactly `_run_calculation(period, org_id)` — no
     `force` (R1 + zero-wipe stay armed), no `guard_token` (the single-flight claim stays armed) — and auto_calc.py
     may not compute or write pay itself;
  E. the config is read anywhere but its one home: the keys `auto_calc_on_landing` / `auto_calc_debounce_minutes`
     spelled outside `auto_calc.py`'s two constants, the constants referenced outside `resolve_config`, or the
     frontend reading the keys;
  F. the calculation reads a period-keyed feed the registry (`COMMISSION_CALC_FEEDS`) does not list — its landing
     would re-run nothing;
  G. registration: the index, the migration, the CI jobs, the boot hook and the Rep Incentive page.

NEGATIVE CONTROLS (§N) plant each violation into a copy of the real sources and require RED.

    python3 backend/harness_auto_calc_lock.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
AUTO = "app/modules/commcalc/auto_calc.py"
ROUTER = "app/modules/commcalc/router.py"
REGISTRY = "app/modules/commcalc/data_lineage_registry.py"

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


# ── the registry and the two lists, read STATICALLY (literal_eval over the module's own assignments) ──────────
def module_consts(src):
    tree = ast.parse(src)
    env = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            try:
                env[name] = _eval(node.value, env)
            except ValueError:
                pass
    return env


def _eval(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_eval(e, env) for e in node.elts)
    if isinstance(node, ast.JoinedStr) or isinstance(node, ast.BinOp):
        raise ValueError("not a literal")
    raise ValueError("not a literal")


def registry_tables(tree_src):
    env = module_consts(tree_src[REGISTRY])
    feeds = tuple(env.get("COMMISSION_CALC_FEEDS") or ())
    siblings = tuple(env.get("SALES_SIBLING_TABLES") or ())
    return feeds, siblings


def lists(tree_src):
    env = module_consts(tree_src[AUTO])
    landers = {(p, f) for (p, f, _w) in env.get("LANDERS", ())}
    excused = {(p, f) for (p, f, _w) in env.get("EXCUSED", ())}
    starters = {(p, f) for (p, f) in env.get("CALC_STARTERS", ())}
    return landers, excused, starters


def reviewed(tree_src):
    return {(p, f) for (p, f, _w) in module_consts(tree_src[AUTO]).get("REVIEWED_NON_LANDERS", ())}


# ── functions and their spans ─────────────────────────────────────────────────────────────────────────────
_PARSED, _FNS = {}, {}


def parse(src):
    t = _PARSED.get(src)
    if t is None:
        t = _PARSED[src] = ast.parse(src)
    return t


def functions(src):
    """[(outermost_name, node)] for every def (nested defs report their OUTERMOST function's name; a method
    reports 'Class.method')."""
    if src in _FNS:
        return _FNS[src]
    tree = parse(src)
    out = []
    _FNS[src] = out

    def walk(node, outer):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = outer or ch.name
                out.append((name, ch))
                walk(ch, name)
            elif isinstance(ch, ast.ClassDef):
                for m in ch.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        name = outer or f"{ch.name}.{m.name}"
                        out.append((name, m))
                        walk(m, name)
            else:
                walk(ch, outer)
    walk(tree, None)
    return out


def owner_of(src, pos, fns=None):
    """The outermost function enclosing character offset `pos`, or None (module level)."""
    line = src.count("\n", 0, pos) + 1
    best = None
    for name, node in (fns if fns is not None else functions(src)):
        if node.lineno <= line <= (node.end_lineno or node.lineno):
            if best is None or node.lineno < best[1].lineno:
                best = (name, node)
    return best[0] if best else None


def fn_node(src, name):
    for n, node in functions(src):
        if n == name and (node.name == name or n.endswith("." + node.name)):
            return node
    return None


def fn_src(src, name):
    node = fn_node(src, name)
    if node is None:
        return None
    lines = src.splitlines(True)
    return "".join(lines[node.lineno - 1:node.end_lineno])


# ── A. DISCOVERY — every function that lands rows into a watched table ────────────────────────────────────────
# `safe_replace` is a generic insert-first writer with no hook of its own, so its CALLERS are the landers.
# (`report_pull.ingest_report_rows` calls the hook itself, so it is a lander, found by its own insert.)
GENERIC_WRITERS = re.compile(r"(?<![\w.])_?safe_replace\(")
_CONSTS = {}


def _consts(src):
    if src not in _CONSTS:
        _CONSTS[src] = module_consts(src)
    return _CONSTS[src]


def _module_aliases(src, path=""):
    """{alias: 'app/…/module.py'} for `from app.x import y [as z]`, `from . import y` and `import app.x.y as z`."""
    out = {}
    for n in ast.walk(parse(src)):
        if isinstance(n, ast.ImportFrom) and n.level and path:
            base = os.path.dirname(path)
            for _ in range(n.level - 1):
                base = os.path.dirname(base)
            pkg = base + ("/" + n.module.replace(".", "/") if n.module else "")
            for a in n.names:
                out[a.asname or a.name] = f"{pkg}/{a.name}.py"
        elif isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app"):
            for a in n.names:
                out[a.asname or a.name] = (n.module.replace(".", "/") + "/" + a.name + ".py")
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith("app.") and a.asname:
                    out[a.asname] = a.name.replace(".", "/") + ".py"
    return out


def resolve_table(tree_src, path, src, owner_node, var):
    """The literal table a variable names, when it can be known statically: a module constant (`CAT_TABLE`), an
    imported module's constant (`deposit_recon.ADJ_TABLE`), or a local `name = "literal"` in the function.
    None = unknowable (a parameter, spec["table"], a loop variable) — such a writer must be classified."""
    if "." in var and "[" not in var:
        alias, name = var.rsplit(".", 1)
        mod = _module_aliases(src, path).get(alias)
        if mod and mod in tree_src:
            v = _consts(tree_src[mod]).get(name)
            return v if isinstance(v, str) else None
        return None
    if not re.fullmatch(r"[A-Za-z_]\w*", var):
        return None
    if owner_node is not None:
        for n in ast.walk(owner_node):
            if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                    and n.targets[0].id == var):
                return n.value.value if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str) else None
    v = _consts(src).get(var)
    return v if isinstance(v, str) else None


def other_schema(src, pos):
    """True when the `.table(` at `pos` is provably NOT on the commcalc schema: the chain reads
    `schema("pos"|"storeops"|…)` right before it, or its receiver is a bare client (`sb()`, `get_supabase()`)
    or a helper defined in this file that never names the commcalc schema (`_so()`)."""
    pre = src[max(0, pos - 80):pos]
    m = re.search(r"""schema\(\s*["'](\w+)["']\s*\)\s*$""", pre)
    if m:
        return m.group(1) != "commcalc"
    m = re.search(r"""(?<![\w.])(\w+)\(\)\s*$""", pre)
    if m:
        name = m.group(1)
        if name in ("sb", "get_supabase"):
            return True
        body = re.search(r"\ndef %s\(\)[^\n]*\n((?:[ \t]+[^\n]*\n)+)" % re.escape(name), src)
        if body:
            return "commcalc" not in body.group(1)
    return False


def writers(tree_src, watched):
    """{(relpath, outermost function): [reason, …]} — every landing write site under backend/app."""
    lit = re.compile(r"""\.table\(\s*["'](%s)["']\s*\)\s*(?:\\\s*)?\.\s*(?:insert|upsert)\(""" %
                     "|".join(re.escape(t) for t in sorted(watched)))
    var = re.compile(r"""\.table\(\s*(?![\"'])([A-Za-z_][\w\.]*(?:\[[^\]]*\])?)\s*\)\s*(?:\\\s*)?\.\s*(?:insert|upsert)\(""")
    rpc = re.compile(r"""rpc\(\s*["']pos_promote_period["']""")
    out = {}
    for path, src in tree_src.items():
        if not path.startswith("app/"):
            continue
        if ".table(" not in src and "safe_replace(" not in src and "pos_promote_period" not in src:
            continue
        uses_commcalc = re.search(r"""schema\(\s*["']commcalc["']\s*\)""", src) is not None
        fns = functions(src)
        hits = []
        hits += [(m.start(), f"writes {m.group(1)}") for m in lit.finditer(src)]
        hits += [(m.start(), "calls the POS promotion RPC") for m in rpc.finditer(src)]
        for m in GENERIC_WRITERS.finditer(src):
            if src[max(0, m.start() - 4):m.start()].endswith("def "):
                continue
            hits.append((m.start(), f"calls {m.group(0)[:-1]}"))
        if uses_commcalc:
            for m in var.finditer(src):
                own = owner_of(src, m.start(), fns)
                node = next((nd for nm, nd in fns if nm == own and nd.name == own.split(".")[-1]), None)
                if other_schema(src, m.start()):
                    continue                       # the chain is on another schema (pos / storeops / public)
                t = resolve_table(tree_src, path, src, node, m.group(1))
                if t is not None and t not in watched:
                    continue                       # provably another table — not a landing of calc input
                hits.append((m.start(), f"inserts into commcalc.<{m.group(1)}"
                                        + (f" = {t}>" if t else ">")))
        for pos, why in hits:
            own = owner_of(src, pos, fns)
            out.setdefault((path, own or "<module>"), []).append(why)
    return out


def calls_hook(fsrc):
    """True when the function's CODE (not a comment or string) calls `auto_calc.landed(` / `_auto_calc.landed(`."""
    try:
        tree = ast.parse(_dedent(fsrc))
    except SyntaxError:
        return False
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "landed"
                and isinstance(n.func.value, ast.Name) and n.func.value.id in ("auto_calc", "_auto_calc")):
            return True
    return False


def _dedent(s):
    import textwrap
    return textwrap.dedent(s)


def lander_failures(tree_src):
    feeds, siblings = registry_tables(tree_src)
    landers, excused, _st = lists(tree_src)
    found = writers(tree_src, set(feeds) | set(siblings))
    bad = []
    for key, why in sorted(found.items()):
        if key not in landers and key not in excused:
            bad.append(f"UNCLASSIFIED WRITER {key[0]}::{key[1]} ({'; '.join(sorted(set(why)))}) — call "
                       f"auto_calc.landed(...) after the write and list it in auto_calc.LANDERS, or excuse it in "
                       f"auto_calc.EXCUSED with the reason")
    for (path, name) in sorted(landers):
        src = tree_src.get(path)
        f = fn_src(src, name) if src else None
        if f is None:
            bad.append(f"STALE LANDER {path}::{name} — no such function")
            continue
        if (path, name) not in found:
            bad.append(f"STALE LANDER {path}::{name} — it no longer writes a watched table")
        if not calls_hook(f):
            bad.append(f"LANDER DOES NOT CALL THE HOOK {path}::{name} — auto_calc.landed(...) is missing")
    for (path, name) in sorted(excused):
        src = tree_src.get(path)
        if src is None or fn_src(src, name) is None:
            bad.append(f"STALE EXCUSE {path}::{name} — no such function")
        elif (path, name) not in found:
            bad.append(f"STALE EXCUSE {path}::{name} — it no longer writes anything the discovery watches; "
                       f"drop the entry")
    for (path, name) in sorted(reviewed(tree_src)):
        src = tree_src.get(path)
        if src is None or fn_src(src, name) is None:
            bad.append(f"STALE REVIEW {path}::{name} — no such function")
        elif (path, name) in found:
            bad.append(f"A REVIEWED NON-LANDER NOW WRITES CALCULATION INPUT {path}::{name} — wire it to "
                       f"auto_calc.landed(...) and move it to LANDERS")
    return bad, found


# ── C. ONE TRIGGER ────────────────────────────────────────────────────────────────────────────────────────────
def refs(src, ident):
    """Offsets of every CODE reference to `ident` (a Name, an Attribute, or an imported name) — not its def."""
    if ident not in src:
        return []
    tree = parse(src)
    lines = src.splitlines(True)
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))
    out = []
    if ident not in src:
        return out
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id == ident:
            out.append(starts[n.lineno - 1] + n.col_offset)
        elif isinstance(n, ast.Attribute) and n.attr == ident:
            out.append(starts[n.lineno - 1] + n.col_offset)
        elif isinstance(n, ast.ImportFrom) and any(a.name == ident for a in n.names):
            out.append(starts[n.lineno - 1] + n.col_offset)
    return out


def trigger_failures(tree_src):
    _l, _e, starters = lists(tree_src)
    bad = []
    for path, src in tree_src.items():
        fns = functions(src)
        for pos in refs(src, "_run_calculation"):
            own = owner_of(src, pos, fns)
            if own == "_run_calculation" and path == ROUTER:
                continue
            if (path, own) not in starters:
                bad.append(f"SECOND CALCULATION TRIGGER {path}::{own or '<module>'} references _run_calculation — "
                           f"a landing must go through auto_calc.landed(...); only {sorted(starters)} may start one")
        for pos in refs(src, "_calc_rep_rows"):
            own = owner_of(src, pos, fns)
            if not (path == ROUTER and own in ("_calc_rep_rows", "_run_calculation", "recompute_rep")):
                bad.append(f"A PARALLEL CALCULATION {path}::{own or '<module>'} reaches _calc_rep_rows")
    return bad


# ── D. THE HOOK RUNS THE STANDARD CALCULATION ─────────────────────────────────────────────────────────────────
def runner_failures(tree_src):
    src = tree_src[AUTO]
    bad = []
    f = fn_src(src, "_default_runner")
    if f is None:
        return ["auto_calc._default_runner is missing"]
    calls = [n for n in ast.walk(ast.parse(_dedent(f)))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_run_calculation"]
    if len(calls) != 1:
        bad.append(f"_default_runner must call _run_calculation exactly once (found {len(calls)})")
    for c in calls:
        if c.keywords or [a.id for a in c.args if isinstance(a, ast.Name)] != ["period", "org_id"] or len(c.args) != 2:
            bad.append("_default_runner must call _run_calculation(period, org_id) — no force, no guard_token, "
                       "nothing else (every refusal guard stays armed)")
    ro = fn_src(src, "run_one") or ""
    if "runner = runner or _default_runner" not in ro or "runner(period, org_id)" not in ro:
        bad.append("run_one must run `runner or _default_runner` as runner(period, org_id)")
    code = ast.parse(src)
    for n in ast.walk(code):
        if isinstance(n, ast.Call):
            nm = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if nm in ("calc_rep_commissions", "_calc_rep_rows", "_apply_new_engines", "_calc_inputs", "calculate"):
                bad.append(f"auto_calc.py computes pay itself ({nm}) — it must only run _run_calculation")
            for k in n.keywords:
                if k.arg in ("force", "guard_token"):
                    bad.append(f"auto_calc.py passes {k.arg}= — a guard would be bypassed")
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in ("rep_commissions",
                                                                                    "chargeback_items"):
            bad.append(f"auto_calc.py names the pay table {n.value!r} — it may not write pay")
    return bad


# ── E. THE CONFIG HAS ONE READER ──────────────────────────────────────────────────────────────────────────────
KEYS = ("auto_calc_on_landing", "auto_calc_debounce_minutes")


def config_failures(tree_src, frontend_src):
    bad = []
    for path, src in tree_src.items():
        if not any(k in src for k in KEYS + ("CONFIG_ON", "CONFIG_DEBOUNCE")):
            continue
        tree = parse(src)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and any(k in n.value for k in KEYS):
                if path == AUTO and n.value not in KEYS:
                    continue                    # auto_calc.py's own prose / migration filename (the exact keys are counted below)
                if path == AUTO:
                    continue
                bad.append(f"CONFIG KEY SPELLED OUTSIDE ITS HOME {path}:{getattr(n, 'lineno', '?')} — read it "
                           f"through auto_calc.load_config")
            if path != AUTO and isinstance(n, ast.Attribute) and n.attr in ("CONFIG_ON", "CONFIG_DEBOUNCE"):
                bad.append(f"CONFIG CONSTANT READ OUTSIDE ITS HOME {path}:{n.lineno} ({n.attr})")
    src = tree_src[AUTO]
    exact = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and n.value in KEYS]
    if len(exact) != 2:
        bad.append(f"auto_calc.py must spell each config key exactly once (found {len(exact)})")
    fns = functions(src)
    for ident in ("CONFIG_ON", "CONFIG_DEBOUNCE"):
        for pos in refs(src, ident):
            own = owner_of(src, pos, fns)
            if own not in (None, "resolve_config"):          # None = the assignment at module level
                bad.append(f"auto_calc.{ident} referenced in {own} — only resolve_config reads the keys")
    for path, text in frontend_src.items():
        if any(k in text for k in KEYS):
            bad.append(f"THE FRONTEND READS THE CONFIG KEY {path} — it renders GET /calc-status's auto_calc")
    # load_config is the only commission_org_config read in auto_calc.py
    if src.count("table(CONFIG_TABLE)") != 1 or "table(CONFIG_TABLE)" not in (fn_src(src, "load_config") or ""):
        bad.append("auto_calc.load_config must be the ONE read of commission_org_config for these keys")
    return bad


# ── F. THE REGISTRY COVERS WHAT THE CALCULATION READS ─────────────────────────────────────────────────────────
def registry_failures(tree_src):
    feeds, _sib = registry_tables(tree_src)
    bad = []
    r = tree_src[ROUTER]
    ci = fn_src(r, "_calc_inputs") or ""
    # the period-keyed FEEDS (raw_*); payout_config is a person's config, not a landing
    period_fetches = {t for t in re.findall(r"""fetch\(\s*['"](\w+)['"]\s*,\s*\{\s*['"]period['"]""", ci)
                      if t.startswith("raw_")}
    if not period_fetches:
        bad.append("could not find _calc_inputs' period-keyed fetches — the detection is broken")
    for t in sorted(period_fetches - set(feeds)):
        bad.append(f"_calc_inputs reads period-keyed {t!r} but COMMISSION_CALC_FEEDS does not list it — its "
                   f"landing would re-run nothing")
    ane = fn_src(r, "_apply_new_engines") or ""
    for t in set(re.findall(r"""table\(\s*['"](\w+)['"]\s*\)""", ane)):
        if (t.startswith("raw_") or t == "carrier_commission") and t not in feeds:
            bad.append(f"_apply_new_engines reads {t!r}, which COMMISSION_CALC_FEEDS does not list")
    for mod in ("app/modules/commcalc/sale_installment_engine.py", "app/modules/commcalc/installment_engine.py",
                "app/modules/commcalc/commission_engine.py"):
        for t in set(re.findall(r"""table\(\s*['"](raw_\w+)['"]\s*\)""", tree_src.get(mod, ""))):
            if t not in feeds and t != "raw_catalog":            # raw_catalog: a periodless snapshot
                bad.append(f"{mod} reads {t!r}, which COMMISSION_CALC_FEEDS does not list")
    for t in ("daily_sales_feed", "raw_sales"):
        if t not in feeds:
            bad.append(f"COMMISSION_CALC_FEEDS lost the sales basis {t!r}")
    a = tree_src[AUTO]
    if "_lineage.COMMISSION_CALC_FEEDS" not in (fn_src(a, "is_calc_feed") or ""):
        bad.append("auto_calc.is_calc_feed must dereference data_lineage_registry.COMMISSION_CALC_FEEDS")
    lits = {n.value for n in ast.walk(ast.parse(a)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    copies = sorted(t for t in lits if t in set(feeds) | set(_sib))
    if copies:
        bad.append(f"auto_calc.py carries a second copy of the feed list: {copies}")
    return bad


# ══════════════════════════════════════════════════════════════════════════════════════════════════════════
def frontend_sources():
    out = {}
    root = os.path.join(REPO, "frontend", "src")
    for dp, _d, files in os.walk(root):
        for f in files:
            if f.endswith((".ts", ".tsx")):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, REPO)] = open(p, encoding="utf-8").read()
    return out


def main():
    tree_src = load_tree()
    fe = frontend_sources()

    print("── A/B. every lander calls the ONE hook; every writer is classified ──")
    bad, found = lander_failures(tree_src)
    landers, excused, starters = lists(tree_src)
    check(f"A/B {len(found)} landing writer(s) discovered under backend/app — each a wired lander ({len(landers)}) "
          f"or an excused writer ({len(excused)}), no stale entry, every lander calls auto_calc.landed(...)",
          not bad, bad)
    check("A0 the discovery is live: it finds the sales landers the owner named (upload, intake core, promotion)",
          all((ROUTER, n) in found for n in ("_upload_file_impl", "_ingest_mapped_df", "_promote_feed_impl")),
          sorted(k for k in found if k[0] == ROUTER))

    print("── C. one trigger ──")
    tb = trigger_failures(tree_src)
    check("C nothing but the Run Calculation button and the hook's runner starts _run_calculation; nothing but "
          "_run_calculation / recompute_rep reaches the calculation body", not tb, tb)

    print("── D. the hook runs the standard calculation, every guard armed ──")
    rb = runner_failures(tree_src)
    check("D the runner calls _run_calculation(period, org_id) — no force, no guard_token — and auto_calc.py "
          "never computes or writes pay itself", not rb, rb)

    print("── E. the config has one home ──")
    cb = config_failures(tree_src, fe)
    check("E auto_calc_on_landing / auto_calc_debounce_minutes are spelled once, read by load_config → "
          "resolve_config only, never by the frontend", not cb, cb)

    print("── F. the registry covers what the calculation reads ──")
    fb = registry_failures(tree_src)
    check("F every period-keyed feed the Run Calculation reads is in COMMISSION_CALC_FEEDS, and the hook "
          "dereferences it (no copy)", not fb, fb)

    print("── G. registration ──")
    idx = open(os.path.join(REPO, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
    check("G1 the index registers the hook, both harnesses, the migration and the config (§6l + §16–18)",
          all(s in idx for s in ("### 6l.", "auto_calc.py", "harness_auto_calc_lock.py",
                                 "harness_auto_calc_on_landing.py", "1030_auto_calc_on_landing",
                                 "auto_calc_on_landing", "COMMISSION_CALC_FEEDS"))
          and idx.count("auto_calc") >= 8)
    mig = os.path.join(REPO, "database", "migrations", "1030_auto_calc_on_landing.sql")
    mtxt = open(mig, encoding="utf-8").read() if os.path.exists(mig) else ""
    check("G2 mig 1030 is additive + idempotent with a REVERT note",
          "-- REVERT:" in mtxt and "ADD COLUMN IF NOT EXISTS auto_calc_on_landing" in mtxt
          and "DROP TABLE" not in mtxt.split("-- REVERT:")[0].upper().replace("-- ", ""))
    wf = os.path.join(REPO, ".github", "workflows")
    osg = open(os.path.join(wf, "org-scope-guard.yml"), encoding="utf-8").read()
    cvg = open(os.path.join(wf, "carrier-vocab-guard.yml"), encoding="utf-8").read()
    proof_job = cvg[cvg.find("customer-master-proof:"):]
    proof_job = proof_job[:proof_job.find("\n  closing-filter-contract-proof:")]
    check("G3 CI runs this lock (no-deps job) and the proof (the pip job)",
          "harness_auto_calc_lock.py" in osg and "harness_auto_calc_on_landing.py" in proof_job
          and "pip install" in proof_job)
    main_py = tree_src.get("app/main.py", "")
    check("G4 every boot starts the poller (a request queued before a restart still runs)",
          "start_poller" in main_py and "_auto_calc_poller_startup" in main_py)
    rpt = fe.get("frontend/src/app/(platform)/commcalc/reports/page.tsx", "")
    check("G5 the Rep Incentive page renders what the hook did (auto_calc sentence from /calc-status)",
          "calc-status" in rpt and "auto_calc" in rpt and "AutoCalcNotice" in rpt)

    print("── N. negative controls — each planted violation turns the lock RED ──")

    def mutate(path, old, new):
        t = dict(tree_src)
        assert t[path].count(old) >= 1, (path, old)
        t[path] = t[path].replace(old, new, 1)
        return t

    t1 = dict(tree_src)
    t1["app/modules/commcalc/new_sweep.py"] = ("def pull(client, org_id, rows):\n"
                                               "    client.schema('commcalc').table('daily_sales_feed').insert(rows).execute()\n")
    check("N1 a NEW sales lander that never calls the hook → RED", bool(lander_failures(t1)[0]))
    t2 = mutate(ROUTER, "        out[\"auto_calc\"] = _auto_calc.landed(", "        out[\"auto_calc\"] = _auto_calc_off(")
    check("N2 the upload lander stops calling the hook → RED", bool(lander_failures(t2)[0]))
    t3 = mutate("app/modules/commcalc/dlar_sweep.py", "            auto_calc = _auto_calc.landed(",
                "            auto_calc = _run_calculation(period, org_id) or _auto_calc.landed(")
    check("N3 a sweep that recalculates inline again (a second trigger) → RED", bool(trigger_failures(t3)))
    t4 = mutate(AUTO, "    return _run_calculation(period, org_id)\n",
                "    return _run_calculation(period, org_id, force=True)\n")
    check("N4 the hook's runner forcing past the zero-wipe / R1 guards → RED", bool(runner_failures(t4)))
    t5 = mutate(AUTO, "    from app.modules.commcalc.router import _run_calculation\n    return _run_calculation(period, org_id)\n",
                "    from app.modules.commcalc.router import _calc_inputs, _calc_rep_rows\n"
                "    return _calc_rep_rows(_calc_inputs(None, org_id, period), period)\n")
    check("N5 the hook computing pay itself (bypassing _run_calculation) → RED",
          bool(runner_failures(t5)) and bool(trigger_failures(t5)))
    t6 = mutate(ROUTER, "def get_calc_status(", "def _peek(c, o):\n    return _commission_org_config(c, o).get('auto_calc_on_landing')\n\n\ndef get_calc_status(")
    check("N6 a second reader of the config key → RED", bool(config_failures(t6, fe)))
    fe7 = dict(fe)
    fe7["frontend/src/x.tsx"] = "const on = cfg.auto_calc_on_landing"
    check("N7 the frontend reading the config key → RED", bool(config_failures(tree_src, fe7)))
    t8 = mutate(ROUTER, "    mi_rows    = fetch('raw_mi', {'period': _pvariants(period)})\n",
                "    mi_rows    = fetch('raw_mi', {'period': _pvariants(period)})\n"
                "    _x = fetch('raw_new_feed', {'period': _pvariants(period)})\n")
    check("N8 the calculation reading a period feed the registry does not list → RED", bool(registry_failures(t8)))
    t9 = mutate(AUTO, '    ("app/modules/commcalc/router.py", "decide_ingest_guard_item",',
                '    ("app/modules/commcalc/router.py", "no_such_function",')
    check("N9 a stale LANDERS entry (and the real lander left unclassified) → RED", bool(lander_failures(t9)[0]))
    t10 = mutate(AUTO, "    return str(table or \"\").strip() in _lineage.COMMISSION_CALC_FEEDS",
                 "    return str(table or \"\").strip() in (\"raw_sales\", \"daily_sales_feed\")")
    check("N10 the hook carrying its own copy of the feed list → RED", bool(registry_failures(t10)))
    check("N11 the unmodified tree is GREEN (the controls are not vacuous)",
          not lander_failures(tree_src)[0] and not trigger_failures(tree_src) and not runner_failures(tree_src)
          and not config_failures(tree_src, fe) and not registry_failures(tree_src))

    print(f"\n{P} passed, {F} failed")
    sys.exit(1 if F else 0)


if __name__ == "__main__":
    main()
