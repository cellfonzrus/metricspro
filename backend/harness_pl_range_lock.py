#!/usr/bin/env python3
"""LOCK — a multi-month P&L is the single-month P&L, looped; it never computes a line of its own (owner 2026-09-26:
"also need the p&L report to be exported for multiple months … all these need to be platform wide"). Index §4c.
stdlib only; a static scan (AST for Python, code-only text for TS) — runs in the dependency-free guard job.

THE SHARED FACT: `account/router.pl_single_month(period, scope, stores, markets, org_id)` is THE single-month P&L
read (`GET /account/pl/{period}` returns it). `GET /account/pl-range` (`router.get_pl_range`) loops it over
`_period.month_range`; `account/pl_range` lays the months side by side and only sums the Total.

FAILS THE BUILD WHEN:
  L1  `get_pl` stops returning `pl_single_month(...)`, or reads a snapshot itself (a second single-month path);
  L2  `pl_single_month` stops being the P&L read (the stored snapshot / the store-market filtered view);
  L3  `get_pl_range` stops enumerating through `_period.month_range`, stops calling `pl_single_month` inside its
      month loop, or calls anything that COMPUTES or READS statement lines itself (a snapshot read, the filter
      aggregation, the statement engine, the booking engine, a table);
  L4  `pl_range.py` stops being pure: an import beyond the stdlib, a table / client reference, any subtraction
      (a derived gross profit / net income), or a sum / Decimal outside the one Total helper `_sum_cents`;
  L5  ANYWHERE in backend/app, a function enumerates months (`month_range(`) AND reads a P&L without going through
      `pl_single_month` / `get_pl_range`, or loops the single-month read outside `get_pl_range`, or a route
      whose path names a P&L range / months is not `get_pl_range`;
  L6  the scheduled-report entry `account_pl_range` stops calling `get_pl_range` (or builds its own rows);
  L7  the frontend range export stops reading `/api/v1/account/pl-range`, calls the single-month endpoint
      itself, or adds up a number; a NEW frontend file calls the single-month endpoint (the place a per-month
      loop would hide) outside the allow-list below;
  L8  negative controls — each planted violation turns the matching check RED.

    python3 backend/harness_pl_range_lock.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
FE = os.path.join(os.path.dirname(ROOT), "frontend", "src")
ROUTER = os.path.join(APP, "modules", "account", "router.py")
PURE = os.path.join(APP, "modules", "account", "pl_range.py")
NOTIFY = os.path.join(APP, "modules", "notify", "finance_reports.py")
FE_HELPER = "app/(platform)/accounts/_components/plRangeExport.ts"
FE_COMPONENT = "app/(platform)/accounts/_components/PLRangeExport.tsx"
FE_PAGE = "app/(platform)/accounts/pl/page.tsx"
# frontend files that read the SINGLE-month endpoint — each reads ONE month, with WHY it is not a range path
FE_SINGLE_ALLOWED = {
    FE_PAGE: "the P&L page itself — one month, the section period switcher",
    "app/(platform)/accounts/_components/scopeFinancials.ts": "the Account hub's per-scope drill-down — one month",
}
FORBIDDEN_IN_RANGE = ("_read(", "_filtered_read(", "filtered_statement(", "statement_engine.", "_assemble(",
                      "build_inputs(", "aggregate(", "compute_and_store", "account_statements", ".table(", "sb()")
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:400]))


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def fn_src(src, name):
    """The source of top-level function `name` (decorators excluded), or ''."""
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return ast.get_source_segment(src, n) or ""
    return ""


def calls_in(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.append(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return out


def strip_docstrings(src):
    """Code with docstrings removed — a check must read CODE, not the prose that explains it."""
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and n.body:
            if isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant) \
                    and isinstance(n.body[0].value.value, str):
                n.body = n.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def ts_code(src):
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return "\n".join(ln.split("//", 1)[0] if "://" not in ln else ln for ln in src.split("\n"))


# ── the checks, as functions so the negative controls can re-run them on planted sources ─────────────────
def l1_get_pl(router_src):
    body = strip_docstrings(fn_src(router_src, "get_pl"))
    return bool(body) and "return pl_single_month(" in body and "_read(" not in body


def l2_single(router_src):
    body = strip_docstrings(fn_src(router_src, "pl_single_month"))
    return bool(body) and "_filtered_read(period, 'pl'" in body and "_read(period, 'pl'" in body


def l3_range(router_src):
    raw = fn_src(router_src, "get_pl_range")
    if not raw:
        return False, "get_pl_range missing"
    code = strip_docstrings(raw)
    tree = ast.parse(code)
    loops_single = any(isinstance(n, ast.For) and "pl_single_month" in calls_in(n) for n in ast.walk(tree))
    bad = [f for f in FORBIDDEN_IN_RANGE if f in code]
    ok = ("_period.month_range(" in code and loops_single and "pl_range.assemble(" in code and not bad)
    return ok, {"loops_single": loops_single, "forbidden": bad}


def _is_text(s):
    """A string operand: a str literal, an f-string, str(...), or a conditional between str literals."""
    if isinstance(s, ast.JoinedStr) or (isinstance(s, ast.Constant) and isinstance(s.value, str)):
        return True
    if isinstance(s, ast.Call) and getattr(s.func, "id", "") == "str":
        return True
    if isinstance(s, ast.IfExp):
        return _is_text(s.body) and _is_text(s.orelse)
    return False


def l4_pure(pure_src):
    tree = ast.parse(pure_src)
    problems = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            problems += [a.name for a in n.names if a.name.split(".")[0] not in ("decimal",)]
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] not in ("decimal",):
            problems.append(n.module)
        if isinstance(n, ast.Name) and n.id in ("sb", "client", "get_supabase"):
            problems.append(n.id)
        if isinstance(n, ast.Attribute) and n.attr in ("table", "schema", "execute", "rpc"):
            problems.append("." + n.attr)
        if isinstance(n, ast.Constant) and n.value == "account_statements":
            problems.append("account_statements")
        if isinstance(n, (ast.BinOp, ast.AugAssign)) and isinstance(n.op, ast.Sub):
            problems.append("subtraction")
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        if fn.name == "_sum_cents":
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("sum", "Decimal"):
                problems.append(f"{fn.name}: {n.func.id}(")
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                sides = (n.left, n.right)
                textual = any(_is_text(s) for s in sides)      # label building, never money
                counter = fn.name in ("_merge_order", "_line_ids") and any(
                    isinstance(s, ast.Constant) and s.value == 1 for s in sides)
                if not (textual or counter):
                    problems.append(f"{fn.name}: + on money")
            if isinstance(n, ast.AugAssign) and isinstance(n.op, ast.Add) and fn.name not in ("_merge_order",):
                problems.append(f"{fn.name}: +=")
    return not problems, problems


def l5_global(files):
    """files = {relpath: src}. Every month-enumerating P&L reader goes through the one read."""
    problems = []
    route_re = re.compile(r"/pl[-_/]?(range|months|multi|by-month|quarter)")
    for rel, src in files.items():
        # cheap text pre-filter: only a file that could hold one of the three shapes is parsed
        if not ("month_range(" in src or "pl_single_month" in src or "get_pl(" in src or route_re.search(src)):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines()
        home = [(n.lineno, n.end_lineno) for n in tree.body if rel.endswith("account/router.py")
                and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "get_pl_range"]
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            seg = "\n".join(lines[fn.lineno - 1:getattr(fn, "end_lineno", fn.lineno)])
            name = f"{rel}:{fn.name}"
            # the one range endpoint, and the worker function nested inside it
            is_range_home = any(lo <= fn.lineno <= hi for lo, hi in home)
            # (a) the single-month read looped anywhere but the one range endpoint
            if ("pl_single_month" in seg or "get_pl(" in seg) and not is_range_home:
                for n in ast.walk(fn):
                    if isinstance(n, (ast.For, ast.ListComp, ast.DictComp, ast.GeneratorExp, ast.SetComp)):
                        cs = calls_in(n)
                        if "pl_single_month" in cs or "get_pl" in cs:
                            problems.append(f"{name}: loops the single-month P&L read outside get_pl_range")
                            break
            # (b) enumerates months AND reads a P&L without the one read
            if "month_range(" in seg and not is_range_home:
                reads_pl = ("account_statements" in seg and ("'pl'" in seg or '"pl"' in seg)) \
                    or "statement_engine.statement(" in seg or "filtered_statement(" in seg
                if reads_pl and "pl_single_month(" not in seg and "get_pl_range(" not in seg:
                    problems.append(f"{name}: enumerates months and reads a P&L without pl_single_month")
            # (c) a route whose path names a P&L range / by-month P&L must be the one endpoint
            for d in getattr(fn, "decorator_list", []):
                if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant):
                    path = str(d.args[0].value)
                    if re.search(r"/pl[-_/]?(range|months|multi|by-month|quarter)", path) and not is_range_home:
                        problems.append(f"{name}: route {path} is a P&L range outside get_pl_range")
    return not problems, problems


def l6_notify(notify_src):
    body = strip_docstrings(fn_src(notify_src, "_account_pl_range"))
    return (bool(body) and "get_pl_range(" in body and "get_pl(" not in body and "_stmt_rows" not in body
            and "export_sheet(" not in body and "data.get('sheets')" in body)


def l7_frontend(fe_files):
    problems = []
    helper = ts_code(fe_files.get(FE_HELPER, ""))
    comp = ts_code(fe_files.get(FE_COMPONENT, ""))
    page = ts_code(fe_files.get(FE_PAGE, ""))
    if "PL_RANGE_ENDPOINT = '/api/v1/account/pl-range'" not in helper:
        problems.append("the helper no longer names the range endpoint")
    if "plRangeQuery(" not in comp:
        problems.append("the component no longer asks the range endpoint")
    for label, code in (("helper", helper), ("component", comp)):
        if "/account/pl/" in code:
            problems.append(f"the range {label} calls the single-month endpoint")
        if re.search(r"\.reduce\(|\+=|Math\.round|toFixed\(", code):
            problems.append(f"the range {label} adds up / rounds a number")
    if "<PLRangeExport" not in page:
        problems.append("the P&L page no longer renders the month-range export")
    for rel, src in fe_files.items():
        if "/api/v1/account/pl/" in ts_code(src) and rel not in FE_SINGLE_ALLOWED:
            problems.append(f"{rel} reads the single-month P&L endpoint (a per-month loop hides here) — use "
                            f"/api/v1/account/pl-range, or add it to the allow-list with its reason")
    return not problems, problems


def walk(root, exts):
    out = {}
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules", ".next")]
        for f in fs:
            if f.endswith(exts):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, root).replace(os.sep, "/")] = read(p)
    return out


router_src, pure_src, notify_src = read(ROUTER), read(PURE), read(NOTIFY)
py_files = walk(APP, (".py",))
fe_files = walk(FE, (".ts", ".tsx"))

print("\n── L1–L7: the wiring ─────────────────────────────────────────────────────────────────────────")
check("L1 get_pl returns THE single-month read and reads no snapshot itself", l1_get_pl(router_src))
check("L2 pl_single_month IS the P&L read (stored snapshot / store-market filtered view)", l2_single(router_src))
ok, d = l3_range(router_src)
check("L3 get_pl_range: month_range → pl_single_month in the loop → pl_range.assemble; computes nothing", ok, d)
ok, d = l4_pure(pure_src)
check("L4 pl_range.py is pure: stdlib only, no table, no subtraction, sums only in _sum_cents", ok, d)
ok, d = l5_global(py_files)
check("L5 nowhere else in backend/app does a month-range P&L bypass the single-month read", ok, d)
check("L6 the scheduled report account_pl_range reads get_pl_range and ships its sheets", l6_notify(notify_src))
ok, d = l7_frontend(fe_files)
check("L7 the frontend range export reads /account/pl-range, adds nothing; no new single-month reader", ok, d)
excuses_true = all(("/api/v1/account/pl/" in ts_code(fe_files.get(rel, ""))) for rel in FE_SINGLE_ALLOWED)
check("L7b every allow-listed single-month reader still exists and still reads it (no stale excuse)", excuses_true)

print("\n── L8: negative controls (each plant must turn its check RED) ────────────────────────────────")
planted = router_src.replace("    return pl_single_month(period, scope, stores, markets, org_id)",
                             "    return _read(period, 'pl', scope, org_id)")
check("L8a get_pl reading the snapshot itself → L1 RED", planted != router_src and not l1_get_pl(planted))
planted = router_src.replace("per_month[m] = pl_single_month(m, scope, stores, markets, org_id)",
                             "per_month[m] = _filtered_read(m, 'pl', scope, stores, markets, org_id)")
check("L8b the range reading the filtered view itself → L3 RED", planted != router_src and not l3_range(planted)[0])
planted = router_src.replace("_period.month_range(period_from", "_my_months(period_from")
check("L8c the range enumerating months its own way → L3 RED", planted != router_src and not l3_range(planted)[0])
planted = pure_src.replace('"total": _sum_cents(amounts)}', '"total": sum(a or 0 for a in amounts)}')
check("L8d a sum outside _sum_cents in pl_range → L4 RED", planted != pure_src and not l4_pure(planted)[0])
planted = pure_src + "\n\ndef _gp(st):\n    return st['rev'] - st['cogs']\n"
check("L8e a derived (subtracted) figure in pl_range → L4 RED", not l4_pure(planted)[0])
planted = pure_src + "\n\nfrom app.modules.account import coa\n"
check("L8f an app import in pl_range → L4 RED", not l4_pure(planted)[0])
sibling = {"modules/account/quarterly.py": (
    "from app.modules.account import _period\n"
    "def quarter_pl(client, org, q):\n"
    "    out = {}\n"
    "    for m in _period.month_range(q[0], q[1]):\n"
    "        out[m] = client.schema('commcalc').table('account_statements').select('*')"
    ".eq('org_id', org).eq('statement_type', 'pl').eq('period', m).execute().data\n"
    "    return out\n")}
check("L8g a sibling quarterly P&L reading snapshots itself → L5 RED", not l5_global(sibling)[0])
sibling = {"modules/notify/x.py": "async def many(org, months):\n"
                                  "    return [await AC.get_pl(m, org_id=org) for m in months]\n"}
check("L8h a registry builder looping the single-month handler → L5 RED", not l5_global(sibling)[0])
sibling = {"modules/account/router2.py": "@router.get('/pl-months')\nasync def pl_months(a):\n    return a\n"}
check("L8i a second P&L-range route → L5 RED", not l5_global(sibling)[0])
planted = notify_src.replace("data = await AC.get_pl_range(", "data = await AC.get_pl(")
check("L8j the scheduled report not reading the range endpoint → L6 RED",
      planted != notify_src and not l6_notify(planted))
fe_bad = dict(fe_files)
fe_bad["app/(platform)/accounts/quarterly/page.tsx"] = (
    "export default function Q(){ const ms=['a','b']; ms.map(m => api(`/api/v1/account/pl/${m}`)); return null }")
check("L8k a new page looping the single-month endpoint → L7 RED", not l7_frontend(fe_bad)[0])
fe_bad = dict(fe_files)
fe_bad[FE_HELPER] = fe_bad[FE_HELPER] + "\nexport const t = (xs: number[]) => xs.reduce((a, b) => a + b, 0)\n"
check("L8l the frontend helper adding up a Total → L7 RED", not l7_frontend(fe_bad)[0])

print("\n%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
