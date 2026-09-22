#!/usr/bin/env python3
"""THE LOCK — a reader tolerates ANY subset of a table's columns (index §4b.1, owner defect 2026-09-22).
Fails the build on a column-block ladder anywhere under backend/app, on the P&L config reader leaving
the one reading rule, on a second copy of that rule, and on a credentials-holding row escaping its
projection.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that
FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

THE CLASS. A per-org config reader that selected column SETS as blocks, newest set first, falling back to
an older block when the select failed, let ONE missing OLDER column hide every NEWER one: on the live
tenant `commission_org_config` carried the mig-1013 switch and not the mig-996 column, both blocks
failed, the ladder fell to the mig-934 block, and the P&L read `feeds` after the owner chose `ledger`.
Migrations are applied by hand and not always in order. The one reading rule lives in
backend/app/core/column_tolerant.py: a config row is read with `select("*")` and the reader takes the
keys present (`read_row`); a wide table probes each optional column on its own (`present_columns`) before
the one real select (`select_list`); what is missing is REPORTED.

WHAT FAILS THE BUILD (a dependency-free static scan — stdlib only, no app import):
  (a) NO LADDER. Under backend/app, no `for <v> in (<column-list strings>)` loop whose body selects
      `<v>`; no `try: .select("a,b,…") except: .select(<strict subset>)` on the same table; no tier table
      (a `.select(<v>)` inside a `while` where `<v>` is re-assigned from a subscript). Every one of the
      19 that existed on 2026-09-22 is gone; a new one anywhere → RED.
  (b) THE P&L CONFIG READER USES THE RULE. `ma_store_pnl.load_config` reads through `_ct.read_row(` and
      issues no `.select(` of its own; `ledger_pnl.load_source_meta` dereferences `_msp.load_config(`
      (one reader for the panel and the statement) and issues no `.select(`; `ledger_pnl.MIGRATION`
      dereferences `PL_CONFIG_MIGRATION` (one home for "which migration adds the column").
  (c) ONE HOME. `def read_row(`, `def present_columns(` and `def select_list(` are each defined exactly
      once under backend/app, in core/column_tolerant.py.
  (d) THE NAMED READERS DEREFERENCE IT. Each reader this design fixed reads through the rule or
      `select("*")`: the P&L / bill-pay / residual / pay-path / classification config readers, the ledger
      readers (P&L, what-if, provenance), the expenses read, the memberships, the connector status, the
      pull diagnostic, the accessory config, the tender tokens.
  (e) PROJECTED. The two readers of rows that also hold credentials (`_connector_status`,
      `data_source_pull_diagnostic`) never return the row itself — each projects through its column list.
  (f) REGISTERED. The index names §4b.1 and this lock; CI runs it.
  (g) NEGATIVE CONTROLS over synthetic sources — each rule is broken in memory and must go RED.

  python3 backend/harness_any_columns_lock.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend", "app")
HOME = "core/column_tolerant.py"
MSP = "modules/account/ma_store_pnl.py"
LP = "modules/account/ledger_pnl.py"
COA = "modules/account/coa.py"
BILLPAY = "modules/account/billpay_pl.py"
RESID = "modules/account/residual_subs.py"
ENGINE = "modules/commcalc/commission_engine.py"
WHATIF = "modules/commcalc/whatif.py"
EXPENSES = "modules/commcalc/expenses_effective.py"
TENANT = "core/tenant_middleware.py"
ROUTER = "modules/commcalc/router.py"
INDEX = os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md")
CI = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((bool(ok), label, detail))
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else f"   {detail}"))
    return ok


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def walk(root, exts):
    for base, _d, files in os.walk(root):
        for f in files:
            if f.endswith(exts):
                yield os.path.join(base, f)


def function_body(src, name):
    lines = src.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^(async )?def %s\(" % re.escape(name), ln)), None)
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if ln and not ln[0].isspace() and ln.startswith(("def ", "class ", "@", "async def ")):
            end = j
            break
    return "\n".join(lines[start:end])


# ── (a) the ladder scan — AST, so a comment or a docstring can never trip or hide it ────────────
def _colset(s):
    return frozenset(c.strip() for c in s.split(",") if c.strip())


def _select_calls(node):
    """(table|None, cols, lineno, kind) for every `.select(<str | Name>)` under node."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "select" and n.args:
            a = n.args[0]
            t, cur = None, n.func.value
            while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
                if cur.func.attr == "table" and cur.args and isinstance(cur.args[0], ast.Constant):
                    t = cur.args[0].value
                    break
                cur = cur.func.value
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.append((t, a.value, n.lineno, "const"))
            elif isinstance(a, ast.Name):
                out.append((t, a.id, n.lineno, "name"))
    return out


def ladders_in(src, rel):
    """Every column-block ladder in one module's source: FOR-LADDER, TRY-FALLBACK, TIER-TABLE."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [("PARSE", rel, 0, str(e))]
    consts = {n.targets[0].id: n.value for n in tree.body
              if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}

    def const_str(e):
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            return e.value
        if isinstance(e, ast.Name) and e.id in consts:
            return const_str(consts[e.id])
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            l, r = const_str(e.left), const_str(e.right)
            if l is not None and r is not None:
                return l + r
        return None

    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.For) and isinstance(n.target, ast.Name):
            it = n.iter
            if isinstance(it, ast.Name) and it.id in consts:
                it = consts[it.id]
            if isinstance(it, (ast.Tuple, ast.List)) and len(it.elts) > 1:
                strs = [const_str(e.elts[0] if isinstance(e, ast.Tuple) and e.elts else e) for e in it.elts]
                if all(s is not None for s in strs) and any("," in s for s in strs):
                    if any(s[3] == "name" and s[1] == n.target.id for s in _select_calls(n)):
                        hits.append(("FOR-LADDER", rel, n.lineno, strs))
        if isinstance(n, ast.Try):
            body = [s for stmt in n.body for s in _select_calls(stmt)]
            hand = [s for h in n.handlers for stmt in h.body for s in _select_calls(stmt)]
            for tb, cb, lb, kb in body:
                for th, ch, lh, kh in hand:
                    if kb == kh == "const" and tb == th and "," in cb and _colset(ch) < _colset(cb):
                        hits.append(("TRY-FALLBACK", rel, lb, [cb, ch]))
        if isinstance(n, ast.While):
            for tb, cb, lb, kb in _select_calls(n):
                if kb == "name":
                    for m in ast.walk(n):
                        if isinstance(m, ast.Assign) and isinstance(m.value, ast.Subscript) and any(
                                (isinstance(t, ast.Tuple) and any(isinstance(e, ast.Name) and e.id == cb for e in t.elts))
                                or (isinstance(t, ast.Name) and t.id == cb) for t in m.targets):
                            hits.append(("TIER-TABLE", rel, lb, [cb, ast.unparse(m.value)]))
    return hits


def rule_no_ladder(be):
    hits = []
    for rel, src in be.items():
        hits += ladders_in(src, rel)
    return hits == [], f"ladders={[(h[0], h[1], h[2]) for h in hits]}"


# ── (b) the P&L config reader uses the rule; the panel's reader dereferences it ──────────────────
def rule_pl_reader(be):
    lc = function_body(be.get(MSP, ""), "load_config")
    a = "_ct.read_row(" in lc and ".select(" not in lc and "PL_CONFIG_COLUMNS" in lc
    sm = function_body(be.get(LP, ""), "load_source_meta")
    b = "_msp.load_config(" in sm and ".select(" not in sm and "config_columns_missing" in sm
    c = bool(re.search(r"^MIGRATION = _msp\.PL_CONFIG_MIGRATION\[CONFIG_COLUMN\]", be.get(LP, ""), re.M))
    d = "config_columns_missing=" in function_body(be.get(COA, ""), "build_inputs")
    return a and b and c and d, f"load_config={a} load_source_meta={b} migration_deref={c} coa_passes_missing={d}"


# ── (c) one home ─────────────────────────────────────────────────────────────────────────────────
def rule_one_home(be):
    out = {}
    for name in ("read_row", "present_columns", "select_list"):
        out[name] = sorted(rel for rel, src in be.items() if re.search(r"^def %s\(" % name, src, re.M))
    ok = all(v == [HOME] for v in out.values())
    return ok, f"defs={out}"


# ── (d) the named readers dereference it ─────────────────────────────────────────────────────────
READERS = (
    (MSP, "load_config", ("_ct.read_row(",)),
    (BILLPAY, "load_config", ("_ct.read_row(",)),
    (RESID, "load_ma_pnl_config", ("_ct.read_row(",)),
    (ENGINE, "_plan_pay_config", ("_ct.read_row(",)),
    (ENGINE, "_read_ct_classification_config", ("_ct.read_row(",)),
    (ENGINE, "_read_employee_roster", ('select("*")',)),
    (LP, "load_ledger_rows", ("_ct.present_columns(", "_ct.select_list(")),
    (WHATIF, "_ledger_income_rows", ("_ct.present_columns(", "_ct.select_list(")),
    (EXPENSES, "effective_expense_rows", ("_ct.present_columns(",)),
    (TENANT, "_fetch_memberships", ('select("*")',)),
    (COA, "wages_by_store", ('select("*")',)),
    (ROUTER, "_accessory_config_uncached", ("_ct.read_row(",)),
    (ROUTER, "_billpay_tender_tokens", ("_ct.read_row(",)),
    (ROUTER, "_activation_details_rules", ("_accessory_config(client, org_id)",)),
    (ROUTER, "_connector_status", ("_ct.read_row(",)),
    (ROUTER, "data_source_pull_diagnostic", ("_ct.read_row(",)),
    (ROUTER, "_ledger_existing_by_origin", ("_ct.present_columns(", "_ct.select_list(")),
    (ROUTER, "commission_ledger_provenance", ("_ct.present_columns(", "_ct.select_list(")),
    (ROUTER, "_registry_auto_map", ("select('*')",)),
    ("modules/commcalc/pay_simulator.py", "_memberships", ('select("*")',)),
    ("modules/commcalc/pay_simulator.py", "employee_roster", ('select("*")',)),
)


def rule_readers(be):
    bad = []
    for rel, fn, needles in READERS:
        body = function_body(be.get(rel, ""), fn)
        if not body:
            bad.append(f"{rel}:{fn} (def missing)")
            continue
        if not all(n in body for n in needles):
            bad.append(f"{rel}:{fn}")
    return bad == [], f"readers off the rule: {bad}"


# ── (e) the credential-holding rows are projected ────────────────────────────────────────────────
def rule_projected(be):
    cs = function_body(be.get(ROUTER, ""), "_connector_status")
    a = "_CONNECTOR_STATUS_COLS" in cs and not re.search(r"return (rd\.row|row)\b", cs)
    dg = function_body(be.get(ROUTER, ""), "data_source_pull_diagnostic")
    b = "_DIAG_COLS" in dg and "rd.row[k] for k in _DIAG_COLS" in dg and '"row": rd.row' not in dg
    return a and b, f"connector_status={a} pull_diagnostic={b}"


# ── (f) registered ───────────────────────────────────────────────────────────────────────────────
def rule_registered(files):
    idx, ci = files.get("index", ""), files.get("ci", "")
    a = "4b.1" in idx and "column_tolerant" in idx and "harness_any_columns_lock" in idx
    b = "harness_any_columns_lock.py" in ci
    return a and b, f"index={a} ci={b}"


def main():
    print("ANY-SUBSET COLUMN READS — THE LOCK")
    print("=" * 78)
    be = {os.path.relpath(p, BE).replace(os.sep, "/"): read(p) for p in walk(BE, (".py",))}
    files = {"index": read(INDEX) if os.path.exists(INDEX) else "", "ci": read(CI) if os.path.exists(CI) else ""}

    ok, d = rule_no_ladder(be);    check("(a) NO column-block ladder under backend/app (for-ladder / try-fallback / tier table)", ok, d)
    ok, d = rule_pl_reader(be);    check("(b) the P&L config reader reads through the rule; the panel's reader dereferences it; the migration name has one home; coa passes the report", ok, d)
    ok, d = rule_one_home(be);     check("(c) ONE home: read_row / present_columns / select_list defined once, in core/column_tolerant.py", ok, d)
    ok, d = rule_readers(be);      check("(d) every named reader dereferences the rule or reads the row whole", ok, d)
    ok, d = rule_projected(be);    check("(e) the two credential-holding rows are projected, never returned whole", ok, d)
    ok, d = rule_registered(files); check("(f) registered: the index names §4b.1 and this lock; CI runs it", ok, d)

    print("\n(g) NEGATIVE CONTROLS — each rule must go RED on a broken source")
    b2 = dict(be); b2["modules/x/ladder.py"] = (
        'def load(client, org):\n    for cols in ("a,b,c", "a,b", "a"):\n        try:\n'
        '            return client.schema("s").table("t").select(cols).eq("org_id", org).execute().data\n'
        '        except Exception:\n            continue\n')
    check("g1 a for-ladder in a new module → (a) RED", not rule_no_ladder(b2)[0])
    b2 = dict(be); b2["modules/x/fallback.py"] = (
        'def load(client, org):\n    try:\n        rows = client.schema("s").table("t").select("a,b,c").eq("org_id", org).execute().data\n'
        '    except Exception:\n        rows = client.schema("s").table("t").select("a,b").eq("org_id", org).execute().data\n    return rows\n')
    check("g2 a try/except block-then-subset fallback → (a) RED", not rule_no_ladder(b2)[0])
    b2 = dict(be); b2["modules/x/tiers.py"] = (
        'TIERS = (("a,b,c", True), ("a,b", False))\n\ndef load(client, org):\n    tier = 0\n    cols, ready = TIERS[0]\n'
        '    while True:\n        try:\n            return client.schema("s").table("t").select(cols).eq("org_id", org).execute().data\n'
        '        except Exception:\n            tier += 1\n            cols, ready = TIERS[tier]\n')
    check("g3 a tier table → (a) RED", not rule_no_ladder(b2)[0])
    b2 = dict(be); b2["modules/x/fine.py"] = (
        'def load(client, org):\n    try:\n        return client.schema("s").table("t").select("*").eq("org_id", org).execute().data\n'
        '    except Exception:\n        return []\n')
    check("g4 a whole-row read with a plain except → (a) stays GREEN (the rule, not a ladder)", rule_no_ladder(b2)[0])
    b2 = dict(be); b2[MSP] = be[MSP].replace("_ct.read_row(lambda: client.schema(\"commcalc\").table(\"commission_org_config\"),",
                                             "_own(client.schema(\"commcalc\").table(\"commission_org_config\").select(\"a,b\"),")
    check("g5 load_config back on a block select of its own → (b) RED", not rule_pl_reader(b2)[0])
    b2 = dict(be); b2[LP] = be[LP].replace("cfg = _msp.load_config(client, org_id)", 'cfg = {"commission_source": client.schema("commcalc").table("commission_org_config").select("pl_commission_source").eq("org_id", org_id).execute().data[0]["pl_commission_source"], "config_columns_missing": []}')
    check("g6 the panel's reader reading the column itself again → (b) RED", not rule_pl_reader(b2)[0])
    b2 = dict(be); b2["modules/x/copy.py"] = "def read_row(table, scope, expected=()):\n    return None\n"
    check("g7 a second read_row → (c) RED", not rule_one_home(b2)[0])
    b2 = dict(be); b2[ENGINE] = be[ENGINE].replace("rd = _ct.read_row(lambda: client.schema(\"commcalc\").table(\"commission_org_config\"),", "rd = _mine(client.schema(\"commcalc\").table(\"commission_org_config\").select(\"plan_ct_resolution\"),")
    check("g8 the pay-path config reader off the rule → (d) RED", not rule_readers(b2)[0])
    b2 = dict(be); b2[ROUTER] = be[ROUTER].replace("return {k: row[k] for k in _CONNECTOR_STATUS_COLS if k in row}", "return row")
    check("g9 _connector_status returning the whole sweep-config row (credentials and all) → (e) RED", not rule_projected(b2)[0])
    check("g10 the lock missing from CI → (f) RED", not rule_registered({**files, "ci": ""})[0])

    print("\n" + "=" * 78)
    failed = [r for r in RESULTS if not r[0]]
    print(f"{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAIL — a column-block ladder is back, or a reader left the one reading rule; see above.")
        return 1
    print("OK — no ladder under backend/app; one reading rule, every named reader on it, the credential rows projected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
