"""LOCK — ONE definition of an activation / upgrade EVENT, dereferenced by every surface that pays or counts
per activation (owner 2026-09-25: "commisison for teh reps need to be claculated per action and per upgrade
as defined in teh incentive payout, the system sis calculating per line item"). stdlib only; static scan.

THE HOME: `backend/app/modules/commcalc/line_class.py` — `line_event_keys` (which phone line / device a sale
line names, through `customer_identity`'s ONE phone/device rule), `activation_events` (per invoice: one per
phone line, else device, else the invoice; evidence folds in; type by precedence) and `activation_units`
(the per-row COUNT key under the org's `event.count_unit`).

FAILS THE BUILD WHEN:
  (a) any of the three is defined anywhere else under backend/app (a second copy would drift);
  (b) a PAY caller stops dereferencing it — the plan engine must compute events with
      `_lc.activation_events(` and hand `event_of=` to the gate (the payout AND the financing-tier unit
      count); the gate must offer `per_event`, default `auto_event_fields` to `activation_bucket`, and must
      NOT import line_class (the definition is INJECTED, never re-derived inside the gate);
  (c) a COUNT caller stops dereferencing it — `_sales_cell_agg` (Sales Report / Executive MTD / Targets /
      Productivity / zero-sales / box counts), the Boost calculator, the commission-drill replay, the daily
      closing's two activation tallies, the Sales Comparison report: each must call `activation_units(`;
  (d) ANY function under backend/app classifies a sale line with the predicate (`classify_line(` /
      `activation_class(`) and adds the trans id to an activation set (`.add(tid)` into a prem / byod / upg /
      act set) — the retired per-transaction count — unless the file is EXCUSED below, with its reason, and
      the excuse is still true (a stale excuse is RED);
  (e) the breakdown screen stops grouping by the engine's stamp — `planLines.eventsOf` keys on `event_id`,
      both surfaces map rows through `toPlanLine(`, `PlanLineBreakdown` renders `g.events`;
  (f) negative controls — each class of violation, planted in a synthetic tree, turns this lock RED.

    python3 backend/harness_activation_event_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
FE = os.path.join(os.path.dirname(ROOT), "frontend", "src", "app", "(platform)", "commcalc")
HOME = "modules/commcalc/line_class.py"
DEFS = ("line_event_keys", "activation_events", "activation_units")

# (b) pay callers — (file, function or None) → the dereferences it must carry
PAY = {
    ("modules/commcalc/commission_engine.py", "preview"): [
        "_lc.activation_events(", "event_of=_event_of", "event_ok=_event_ok_for(rule)",
        "_gate.select_paying_lines(_m, _b, _u, _a, event_of=_event_of)"],
    ("modules/commcalc/plan_pay_gate.py", None): [
        '"per_event"', '"auto_event_fields": ["activation_bucket"]', "def _select_per_event(",
        'return "per_event", "auto_event_field"'],
}
# (c) count callers — (file, function) → the dereference
COUNT = {
    ("modules/commcalc/router.py", "_sales_cell_agg"): ["_lc.activation_units(rows, line_rules"],
    ("modules/commcalc/router.py", "commission_drill"): ["_lc.activation_units(rows, _line_rules_of(acfg)"],
    ("modules/commcalc/calculator.py", "calc_rep_commissions"): ["_lc.activation_units("],
    ("modules/closing/router.py", "_b2b_counts_by_store"): ["_lcls.activation_units(rows, _lr"],
    ("modules/closing/router.py", "_b2b_day"): ["_lcls.activation_units("],
    ("modules/commcalc/sales_comparison.py", "tally"): ["_lc.activation_units(lines, line_rules"],
}
# (d) files that still classify-and-add per transaction, each with WHY it is not an activation count
EXCUSED = {
    "modules/commcalc/whatif.py":
        "accessory_byod_correlation — a per-store BYOD series fed to a correlation coefficient (analytics, "
        "never pay, never shown as an activation count); _byod_mdns collects the BYOD PHONE NUMBERS (a phone "
        "set is already one per phone line)",
}
SET_ADD = re.compile(r"""(?:_prem|_byod|_upg|prem_set|byod_set|upg_set|\["act"\]|\['act'\]|\["upg"\]|\['upg'\]|"byod"\]|'byod'\])[^\n]*\.add\(\s*tid\s*\)""")
PRED = re.compile(r"\b(?:classify_line|activation_class)\s*\(")

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


def walk(root, exts):
    out = {}
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules", ".next")]
        for f in fs:
            if f.endswith(exts):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, root).replace(os.sep, "/")] = read(p)
    return out


def code(src):
    src = re.sub(r'"""[\s\S]*?"""', '""', src)
    src = re.sub(r"'''[\s\S]*?'''", "''", src)
    return "\n".join(ln.split("#", 1)[0] if not ln.strip().startswith("#") else "" for ln in src.split("\n"))


def functions(src):
    """{name: body} for every top-level / nested def (body = until the next def at the same or lower indent)."""
    out = {}
    lines = src.split("\n")
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\(", ln)
        if not m:
            continue
        ind = len(m.group(1))
        j = i + 1
        while j < len(lines):
            n = lines[j]
            if n.strip() and (len(n) - len(n.lstrip())) <= ind and re.match(r"^\s*(?:async\s+def|def|class|@)\b", n):
                break
            j += 1
        out.setdefault(m.group(2), "\n".join(lines[i:j]))
    return out


def scan(files, fe):
    v = []
    # (a) one home
    for rel, src in sorted(files.items()):
        for d in DEFS:
            if re.search(r"^\s*def\s+%s\s*\(" % d, src, re.M) and rel != HOME:
                v.append(("a", rel, "defines " + d))
    home = files.get(HOME, "")
    for d in DEFS:
        if not re.search(r"^def\s+%s\s*\(" % d, home, re.M):
            v.append(("a", HOME, "the home no longer defines " + d))
    if "_ci.tracking_kind(" not in home or "_ci.norm_phone(" not in home:
        v.append(("a", HOME, "line_event_keys no longer reads THE phone/device rule (customer_identity)"))
    # (b) pay
    for (rel, fn), needs in PAY.items():
        src = files.get(rel)
        if src is None:
            v.append(("b", rel, "missing"))
            continue
        body = code(functions(src).get(fn, "") if fn else src)
        for n in needs:
            if n not in body:
                v.append(("b", rel, "no longer carries: " + n))
    gate = code(files.get("modules/commcalc/plan_pay_gate.py", ""))
    if re.search(r"import\s+line_class|from\s+app\.modules\.commcalc\s+import\s+line_class|activation_events\(", gate):
        v.append(("b", "modules/commcalc/plan_pay_gate.py", "the gate derives events itself (it must be injected)"))
    # (c) count
    for (rel, fn), needs in COUNT.items():
        src = files.get(rel)
        body = code(functions(src or "").get(fn, ""))
        if not body:
            v.append(("c", rel, "function %s missing" % fn))
            continue
        for n in needs:
            if n not in body:
                v.append(("c", rel, "%s no longer carries: %s" % (fn, n)))
    # (d) the retired per-transaction count
    stale = []
    for rel, src in sorted(files.items()):
        if rel == HOME:
            continue
        hit = None
        for name, body in functions(code(src)).items():
            if PRED.search(body) and SET_ADD.search(body):
                hit = name
                break
        if hit and rel not in EXCUSED:
            v.append(("d", rel, "%s classifies a line and adds the trans id to an activation set — count "
                                "through line_class.activation_units" % hit))
        if rel in EXCUSED and not hit:
            stale.append(rel)
    for rel in EXCUSED:
        if rel not in files:
            stale.append(rel)
    # (e) frontend
    pl = fe.get("_lib/planLines.ts", "")
    if "export function eventsOf(" not in pl or "l?.event_id" not in pl or "export function toPlanLine(" not in pl:
        v.append(("e", "_lib/planLines.ts", "eventsOf / toPlanLine no longer group by the engine's event_id"))
    if re.search(r"event_(?:id|key)[^\n]*(?:product|contract_type|/new act|/activation)", pl, re.I):
        v.append(("e", "_lib/planLines.ts", "derives an event from product / contract text"))
    for page in ("reports/page.tsx", "commission-explain/page.tsx"):
        if "toPlanLine(" not in fe.get(page, ""):
            v.append(("e", page, "no longer maps plan lines through toPlanLine"))
    br = fe.get("_lib/PlanLineBreakdown.tsx", "")
    if "g.events" not in br or "EVENT_COUNTERS" not in br:
        v.append(("e", "_lib/PlanLineBreakdown.tsx", "no longer renders the invoice's events"))
    return v, stale


files = walk(APP, (".py",))
fe = walk(FE, (".ts", ".tsx"))
viol, stale = scan(files, fe)
for part, label in (("a", "(a) one home: line_event_keys / activation_events / activation_units defined once, on THE phone rule"),
                    ("b", "(b) pay: the engine computes events and injects them into the gate (payout + financing); the gate offers per_event"),
                    ("c", "(c) count: Sales Report/Exec MTD cells, Boost calculator, commission drill, closing x2, Sales Comparison dereference activation_units"),
                    ("d", "(d) no function classifies a line and adds the trans id to an activation set (the retired count)"),
                    ("e", "(e) the breakdown groups by the engine's event stamp on both surfaces")):
    check(label, not [x for x in viol if x[0] == part], [x[1:] for x in viol if x[0] == part])
check("(d) every excuse is still true (the excused file still counts per transaction)", not stale, stale)

# ── (f) negative controls ───────────────────────────────────────────────────────────────────────
print("\n  negative controls")


def planted(mut_files=None, mut_fe=None):
    f2 = dict(files)
    f2.update(mut_files or {})
    fe2 = dict(fe)
    fe2.update(mut_fe or {})
    return scan(f2, fe2)


v, _s = planted({"modules/commcalc/shadow.py": "def activation_events(rows):\n    return []\n"})
check("(f) a second activation_events → RED", any(x[0] == "a" and "shadow" in x[1] for x in v))
v, _s = planted({"modules/commcalc/calculator.py": files["modules/commcalc/calculator.py"].replace("_lc.activation_units(", "_lc.nothing(")})
check("(f) the calculator counting without activation_units → RED", any(x[0] == "c" and "calculator" in x[1] for x in v))
v, _s = planted({"modules/commcalc/newreport.py": (
    "def count(rows, rules):\n    prem = {'_prem': set()}\n    for r in rows:\n        tid = r['trans_id']\n"
    "        if classify_line(r, rules) == 'premium':\n            prem['_prem'].add(tid)\n    return prem\n")})
check("(f) a new report counting classify_line → .add(tid) → RED", any(x[0] == "d" and "newreport" in x[1] for x in v))
v, _s = planted({"modules/commcalc/commission_engine.py": files["modules/commcalc/commission_engine.py"].replace("event_of=_event_of", "event_of=None")})
check("(f) the engine not injecting the events into the gate → RED", any(x[0] == "b" for x in v))
v, _s = planted({"modules/commcalc/plan_pay_gate.py": files["modules/commcalc/plan_pay_gate.py"] + "\nfrom app.modules.commcalc import line_class\n"})
check("(f) the gate importing line_class to derive events itself → RED", any(x[0] == "b" and "injected" in x[2] for x in v))
v, s = planted({"modules/commcalc/whatif.py": "def x():\n    return 1\n"})
check("(f) a stale excuse → RED", "modules/commcalc/whatif.py" in s)
v, _s = planted(mut_fe={"reports/page.tsx": fe["reports/page.tsx"].replace("toPlanLine(", "mapRow(")})
check("(f) a surface hand-mapping plan lines (dropping the event stamp) → RED", any(x[0] == "e" and "reports" in x[1] for x in v))
v, _s = planted({"modules/commcalc/router.py": files["modules/commcalc/router.py"].replace(
    "_units = _lc.activation_units(rows, line_rules, skip=_line_skip)", "_units = [None] * len(rows)")})
check("(f) _sales_cell_agg no longer counting through the definition → RED", any(x[0] == "c" and "_sales_cell_agg" in x[2] for x in v))
v, _s = planted()
check("(f) the unmodified tree is GREEN (the controls above are not vacuous)", not v)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one activation-event definition; every pay and count surface dereferences it.")
