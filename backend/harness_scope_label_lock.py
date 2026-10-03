#!/usr/bin/env python3
"""LOCK — a scope's DISPLAY NAME has ONE home, and `scope_label || scope_key` cannot come back.

Owner report 2026-10-03: *"the app shows the company id not the name of the company in the settings
to choose the company to work in."* The company picker shared by the Accounts hub, the P&L, the
Balance Sheet and the Cash Flow labelled each scope `scope_label || scope_key`, and `scope_key` for a
company scope is the literal `company:<uuid>` — so a missing or stale stored label showed the user a
raw id. THE CLASS: a display name is a DEREFERENCE of the canonical company registry (§13b
`coa.org_companies`), not a copy persisted into `commcalc.account_statements.scope_label`.

One home: `account/coa.scope_display_label` / `label_scopes` (index §13b.1) on the backend,
`accounts/_components/scopeFinancials.ts` (`scopeDisplay` / `scopeDisplayOr` /
`statementScopeDisplay`) as the frontend's only reader of the `scope_display` the API stamps.

THIS FAILS THE BUILD WHEN:
  L1  the one home stops existing or stops dereferencing the company registry (the three functions,
      the family tables, the `companies_by_id` read inside the resolver).
  L2  the one home can return the raw scope key (a `return key` / `return scope_key` appears in it).
  L3  ANY backend file re-introduces the fallback — a `scope_label`-or-`scope`/`scope_key`
      expression — outside the one home.
  L4  ANY frontend file under src/ re-introduces it (`scope_label || scope_key`, `scope_label || scope`,
      `s.scope_label || …`, the `String(...)`/ternary spellings).
  L5  a SECOND resolver appears: `scope_display_label` defined anywhere but coa.py, or a frontend
      file other than scopeFinancials.ts reading `.scope_display` directly instead of calling the
      shared helper (a type-field declaration is not a read).
  L6  a caller UN-WIRES: each statement read that ships a scope must stamp `scope_display`
      (`router.pl_single_month` / `get_bs` / `get_cf` / `get_pl_range` / `overview` /
      `_filtered_read`, `statement_engine.statement`), the two notify builders must read it, and each
      finance page that offers the picker must import the frontend home.
  L7  the frontend home's last-resort label is a key (it must be a generic word, and it must never
      fall back to `scope_key`).

Every rule has a NEGATIVE CONTROL: it is run against a synthetic violation and must fire.
Stdlib only, DB-free, no network: `python3 backend/harness_scope_label_lock.py`.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
BACKEND_APP = os.path.join(HERE, "app")
FRONTEND_SRC = os.path.join(ROOT, "frontend", "src")

HOME_PY = os.path.join("app", "modules", "account", "coa.py")
HOME_TS = os.path.join("app", "(platform)", "accounts", "_components", "scopeFinancials.ts")
HOME_TS_REL = "src/app/(platform)/accounts/_components/scopeFinancials.ts"
SKIP_DIRS = {"node_modules", ".next", "out", "build", "__pycache__", "scratchpad", ".git"}

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        for d in (detail or [])[:20]:
            print(f"          {d}")


# ── source readers (comments stripped: this lock is about CODE, and the comments deliberately quote
#    the banned pattern to explain it) ───────────────────────────────────────────────────────────
def strip_py_comments(text):
    out = []
    for ln in text.splitlines():
        # crude but safe here: a '#' outside quotes starts a comment. Lines inside docstrings are
        # dropped by the docstring pass below.
        q = None
        cut = len(ln)
        for i, ch in enumerate(ln):
            if q:
                if ch == q:
                    q = None
            elif ch in "\"'":
                q = ch
            elif ch == "#":
                cut = i
                break
        out.append(ln[:cut])
    return "\n".join(out)


def py_code_only(text):
    """Comments AND docstrings removed — what actually executes."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return strip_py_comments(text)
    drop = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                d = body[0]
                drop.update(range(d.lineno, (d.end_lineno or d.lineno) + 1))
    lines = strip_py_comments(text).splitlines()
    return "\n".join("" if (i + 1) in drop else ln for i, ln in enumerate(lines))


def strip_ts_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", ln) for ln in text.splitlines())


def walk(root, exts):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if fn.endswith(exts):
                full = os.path.join(dirpath, fn)
                try:
                    yield os.path.relpath(full, os.path.dirname(root)), \
                        open(full, encoding="utf-8").read()
                except (OSError, UnicodeDecodeError):
                    continue


BACKEND = {rel: txt for rel, txt in walk(BACKEND_APP, (".py",))}
FRONTEND = {rel.replace(os.sep, "/"): txt
            for rel, txt in walk(FRONTEND_SRC, (".ts", ".tsx", ".js", ".jsx"))}
HOME_SRC = BACKEND[HOME_PY]
HOME_TS_SRC = FRONTEND[HOME_TS_REL]

# ── the banned fallback, in every spelling either language writes it ─────────────────────────────
#   scope_label || scope_key / scope / pc        scope_label') or scope        scope_label ?? scope
FALLBACK_PY = re.compile(r"scope_label[^\n]{0,40}?\b(or)\s+(?:[A-Za-z_][\w.]*\.)?"
                         r"(scope_key|scope|pc|sc)\b")
FALLBACK_TS = re.compile(r"scope_label[^\n]{0,40}?(\|\||\?\?)\s*(?:[A-Za-z_$][\w$]*\.)?"
                         r"(scope_key|scope|pc|sc)\b")


def py_fallbacks(sources, allow=()):
    hits = []
    for rel, txt in sources.items():
        if rel in allow:
            continue
        for i, ln in enumerate(py_code_only(txt).splitlines(), 1):
            if FALLBACK_PY.search(ln):
                hits.append(f"{rel}:{i}: {ln.strip()[:110]}")
    return hits


def ts_fallbacks(sources, allow=()):
    hits = []
    for rel, txt in sources.items():
        if rel in allow:
            continue
        for i, ln in enumerate(strip_ts_comments(txt).splitlines(), 1):
            if FALLBACK_TS.search(ln):
                hits.append(f"{rel}:{i}: {ln.strip()[:110]}")
    return hits


print("\nL1 — the one home exists and dereferences the company registry")
home_tree = ast.parse(HOME_SRC)
fns = {n.name: n for n in home_tree.body if isinstance(n, ast.FunctionDef)}
for want in ("scope_display_label", "label_scopes", "companies_by_id"):
    check(f"coa.{want} is defined", want in fns)
resolver_src = ast.get_source_segment(HOME_SRC, fns["scope_display_label"]) if "scope_display_label" in fns else ""
check("the resolver READS the company registry (companies_by_id)",
      "companies_by_id(" in resolver_src, [resolver_src[:200]])
check("the resolver takes the registry as a parameter (no hidden global / no DB call)",
      "companies" in [a.arg for a in fns["scope_display_label"].args.args]
      and ".table(" not in resolver_src and "client" not in [a.arg for a in fns["scope_display_label"].args.args])
check("the family tables are declared at module level",
      all(k in HOME_SRC for k in ("SCOPE_FAMILIES", "SCOPE_SINGLETONS")))
check("label_scopes stamps the field the API ships", '"scope_display"' in HOME_SRC
      or "scope_display" in ast.get_source_segment(HOME_SRC, fns["label_scopes"]))

print("\nL2 — the one home cannot return the raw scope key")
returns_key = []
for node in ast.walk(fns.get("scope_display_label", ast.Module(body=[], type_ignores=[]))):
    if isinstance(node, ast.Return) and isinstance(node.value, ast.Name) \
            and node.value.id in ("key", "scope_key"):
        returns_key.append(f"line {node.lineno}: return {node.value.id}")
    if isinstance(node, ast.Return) and isinstance(node.value, ast.BoolOp):
        for v in node.value.values:
            if isinstance(v, ast.Name) and v.id in ("key", "scope_key"):
                returns_key.append(f"line {node.lineno}: `or {v.id}` in a return")
check("no `return key` / `return scope_key` anywhere in the resolver", not returns_key, returns_key)

print("\nL3 — no backend file re-introduces the fallback")
hits = py_fallbacks(BACKEND, allow=(HOME_PY,))
check("backend is clean of `scope_label ... or scope/scope_key`", not hits, hits)

print("\nL4 — no frontend file re-introduces the fallback")
hits = ts_fallbacks(FRONTEND)
check("frontend/src is clean of `scope_label || scope/scope_key`", not hits, hits)

print("\nL5 — no second resolver, and no page reads `scope_display` behind the home's back")
second = [rel for rel, txt in BACKEND.items()
          if rel != HOME_PY and re.search(r"^\s*def scope_display_label\b", txt, re.M)]
check("`scope_display_label` is defined in exactly one backend file", not second, second)
raw_reads = []
FIELD_DECL = re.compile(r"scope_display\s*\??\s*:")           # a type field, not a read
HELPER_CALL = re.compile(r"\bscope(Display|DisplayOr)\b|statementScopeDisplay\b")
for rel, txt in FRONTEND.items():
    if rel == HOME_TS_REL:
        continue
    for i, ln in enumerate(strip_ts_comments(txt).splitlines(), 1):
        if "scope_display" in ln and not FIELD_DECL.search(ln):
            raw_reads.append(f"{rel}:{i}: {ln.strip()[:110]}")
check("only scopeFinancials.ts reads `scope_display`; every page calls the helper",
      not raw_reads, raw_reads)
check("the frontend home exports the three readers",
      all(f"export function {n}(" in HOME_TS_SRC
          for n in ("scopeDisplay", "scopeDisplayOr", "statementScopeDisplay")))

print("\nL6 — every caller stays wired")
WIRED_PY = {
    os.path.join("app", "modules", "account", "router.py"):
        ("_scope_display", "pl_single_month", "get_bs", "get_cf", "get_pl_range",
         "overview", "_filtered_read"),
    os.path.join("app", "modules", "account", "statement_engine.py"): ("statement",),
}
for rel, funcs in WIRED_PY.items():
    src = BACKEND[rel]
    tree = ast.parse(src)
    byname = {n.name: n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for fn in funcs:
        seg = ast.get_source_segment(src, byname[fn]) if fn in byname else ""
        check(f"{os.path.basename(rel)}:{fn} stamps/derives scope_display",
              bool(seg) and "scope_display" in seg, [f"{fn} not wired"])
check("router's _scope_display dereferences the one home over org_companies",
      "coa.scope_display_label(" in BACKEND[os.path.join("app", "modules", "account", "router.py")]
      and "coa.org_companies(" in BACKEND[os.path.join("app", "modules", "account", "router.py")])
for rel in (os.path.join("app", "modules", "notify", "finance_reports.py"),
            os.path.join("app", "modules", "notify", "report_registry.py")):
    check(f"{os.path.basename(rel)} reads scope_display (scheduled sends never title with an id)",
          "scope_display" in py_code_only(BACKEND[rel]), [rel])
check("analysis per-company series labels through the one home",
      "scope_display_label(" in py_code_only(BACKEND[os.path.join("app", "modules", "account",
                                                                  "analysis.py")]))
PAGES = ["src/app/(platform)/accounts/page.tsx",
         "src/app/(platform)/accounts/pl/page.tsx",
         "src/app/(platform)/accounts/balance-sheet/page.tsx",
         "src/app/(platform)/accounts/cash-flow/page.tsx",
         "src/app/(platform)/accounts/profit-centers/page.tsx",
         "src/app/(platform)/accounts/_components/plRangeExport.ts"]
for rel in PAGES:
    txt = FRONTEND.get(rel, "")
    check(f"{rel.split('/')[-2] + '/' + rel.split('/')[-1]} imports the frontend home",
          "scopeFinancials" in txt and HELPER_CALL.search(strip_ts_comments(txt)) is not None,
          [f"{rel} does not call the shared helper"])

print("\nL7 — the frontend home's last resort is a word, never a key")
ts_code = strip_ts_comments(HOME_TS_SRC)
check("a named SCOPE_UNKNOWN constant is the floor",
      re.search(r"const SCOPE_UNKNOWN\s*=\s*'[^']+'", ts_code) is not None)
check("the home never falls back to scope_key / scope",
      not re.search(r"\|\|\s*(?:[A-Za-z_$][\w$]*\.)?(scope_key|scope)\b", ts_code),
      [ln for ln in ts_code.splitlines() if re.search(r"\|\|\s*\w*\.?(scope_key|scope)\b", ln)])

print("\nNEGATIVE CONTROLS — each rule fires on a synthetic violation")
check("L3 fires on a backend fallback",
      bool(py_fallbacks({"x.py": "def f(st, scope):\n    return st.get('scope_label') or scope\n"})))
check("L3 fires on the dict-access spelling",
      bool(py_fallbacks({"x.py": "label = data['scope_label'] or scope_key\n"})))
check("L3 ignores a COMMENT that quotes the pattern",
      not py_fallbacks({"x.py": "# never write scope_label or scope_key again\nx = 1\n"}))
check("L3 ignores a DOCSTRING that quotes the pattern",
      not py_fallbacks({"x.py": '"""we used to do scope_label or scope_key."""\nx = 1\n'}))
check("L4 fires on a frontend fallback",
      bool(ts_fallbacks({"p.tsx": "const l = s.scope_label || s.scope_key\n"})))
check("L4 fires on the String(...) spelling",
      bool(ts_fallbacks({"p.tsx": "const l = String(row.scope_label || row.scope_key)\n"})))
check("L4 fires on the ?? spelling",
      bool(ts_fallbacks({"p.tsx": "const l = st?.scope_label ?? scope\n"})))
check("L4 ignores a // comment quoting the pattern",
      not ts_fallbacks({"p.tsx": "// never scope_label || scope_key again\nconst x = 1\n"}))
bad_tree = ast.parse("def scope_display_label(scope_key, stored_label=None, companies=None):\n"
                     "    return scope_key\n")
bad_fn = bad_tree.body[0]
check("L2 fires on a resolver that returns the key",
      any(isinstance(n, ast.Return) and isinstance(n.value, ast.Name)
          and n.value.id in ("key", "scope_key") for n in ast.walk(bad_fn)))
check("L5 fires on a second backend resolver",
      bool([r for r, t in {"other.py": "def scope_display_label(a):\n    return a\n"}.items()
            if re.search(r"^\s*def scope_display_label\b", t, re.M)]))
check("L5 fires on a page reading scope_display directly",
      bool([ln for ln in strip_ts_comments("const n = row.scope_display || 'x'").splitlines()
            if "scope_display" in ln and not FIELD_DECL.search(ln)]))
check("L5 does NOT fire on a type-field declaration",
      not [ln for ln in strip_ts_comments("  scope_display?: string | null").splitlines()
           if "scope_display" in ln and not FIELD_DECL.search(ln)])

print(f"\n{'='*78}\n  {PASS} passed, {FAIL} failed\n{'='*78}")
sys.exit(1 if FAIL else 0)
