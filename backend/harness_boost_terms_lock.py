"""LOCK — "what the Boost KPI-tier engine's terms ARE" has ONE home, and both the thing that PAYS and
the thing that TELLS AN EMPLOYEE read it from there (owner directive 2026-10-05, index §6p).

THE HOME: `backend/app/modules/commcalc/boost_terms.py` — `resolve_terms` (every rate, spiff and tier
threshold with its exact fallback) and `resolve_kpi_targets` (the bar each measure is scored against).

WHY A LOCK AND NOT JUST A HARNESS. The house has written a registry without wiring its callers to it
three times (index §19.18), and the failure mode here is the worst kind: the employee handout would
keep printing a rate while the pay engine quietly moved to another one, and NOTHING would be red.
`harness_boost_terms.py` proves the two agree TODAY; this file fails the build the day they stop.

FAILS THE BUILD WHEN:
  (a) the pay engine stops dereferencing the home — `calculator.calc_rep_commissions` must build its
      `G` from `boost_terms.resolve_terms` and its KPI target map from `boost_terms.resolve_kpi_targets`,
      not from a dict literal or a loop of its own;
  (b) the employee document stops dereferencing the home — `payout_structure.build_boost_doc` must
      read both through `boost_terms`, so the PDF cannot print a rate the engine does not pay;
  (c) A SECOND COPY APPEARS. Any module under `backend/app` outside the home that writes the engine's
      own default literals against the engine's own config keys (e.g. `cfg.get('premium_flat') … else 5`,
      `tier_100_min_kpis') or 7`) is a future divergence, and is named here rather than discovered
      later by an employee holding a wrong handout;
  (d) the home stops being PURE — an import of a DB client, a network library or reportlab inside
      `boost_terms` makes it un-runnable from the proof harness, which is how a one-home quietly stops
      being checked;
  (e) the handout grows RATE HISTORY. The owner's directive is explicit — *"keep how the commission has
      moved as a second module on that page so the pdf is not the same, this pdf could also be used to
      share with employees"*. `build_boost_doc` must not read the history endpoint's shape, and the
      history endpoint must not build a document.

stdlib only; a static AST scan. No DB, no network.
Run: python3 harness_boost_terms_lock.py
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
APP = os.path.join(_HERE, "app")

from app.modules.core.module_graph import home_under_app   # noqa: E402

# THE GRAPH IS THE ONE HOME FOR THE RULING (index §19.18 / §50). This lock does not carry its own
# `"modules/commcalc/boost_terms.py"` literal — it asks the module graph, so a move cannot leave the
# lock guarding a file that is no longer there while still printing OK.
HOME_REL = home_under_app("boost_payout_terms")
HOME = os.path.join(APP, HOME_REL)
CALC = os.path.join(APP, "modules", "commcalc", "calculator.py")
DOC = os.path.join(APP, "modules", "commcalc", "payout_structure.py")
ROUTER = os.path.join(APP, "modules", "commcalc", "router.py")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS.append(name) if cond else FAIL.append(f"{name}{(' — ' + detail) if detail else ''}"))


def section(t):
    print(f"\n── {t} " + "─" * max(0, 76 - len(t)))


def _src(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _tree(path):
    return ast.parse(_src(path), filename=path)


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _calls_in(node):
    """Every dotted call name inside a function, e.g. 'boost_terms.resolve_terms' or '_bt.resolve_terms'."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            base = n.func.value
            if isinstance(base, ast.Name):
                out.add(f"{base.id}.{n.func.attr}")
    return out


def _alias_for(tree, module_name):
    """The local name a module is imported under ('_bt' for `from … import boost_terms as _bt'),
    searched at module scope AND inside functions, because this codebase imports lazily on purpose."""
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name == module_name:
                    names.add(a.asname or a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.endswith("." + module_name) or a.name == module_name:
                    names.add(a.asname or a.name.split(".")[-1])
    return names


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("a. THE PAY ENGINE DEREFERENCES THE HOME")
calc_tree = _tree(CALC)
calc_alias = _alias_for(calc_tree, "boost_terms")
ok("a1 calculator imports boost_terms", bool(calc_alias),
   "calc_rep_commissions must read its terms from the one home")
fn = _func(calc_tree, "calc_rep_commissions")
ok("a2 calc_rep_commissions exists", fn is not None)
calls = _calls_in(fn) if fn else set()
ok("a3 ...and calls resolve_terms",
   any(f"{a}.resolve_terms" in calls for a in calc_alias),
   "the `G` dict must come from boost_terms.resolve_terms, not a literal")
ok("a4 ...and calls resolve_kpi_targets",
   any(f"{a}.resolve_kpi_targets" in calls for a in calc_alias),
   "the KPI target map must come from boost_terms.resolve_kpi_targets, not a loop of its own")

# The literal `G = {...}` must be GONE from the engine: a dict literal assigned to G whose keys are
# the term keys is the exact second copy this lock exists to prevent.
TERM_KEYS = {"upgrade_flat", "premium_flat", "byod_flat", "byod_extra", "trade_in_spiff",
             "acima_spiff", "acc_rate", "setup_rate", "t100", "t75", "t75pct", "t50pct"}


def _term_dict_literals(tree):
    """Dict literals whose keys look like the engine's own term keys (3+ overlapping)."""
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            keys = {k.value for k in n.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if len(keys & TERM_KEYS) >= 3:
                hits.append(n.lineno)
    return hits


ok("a5 the engine holds NO term-dict literal any more", _term_dict_literals(calc_tree) == [],
   f"lines {_term_dict_literals(calc_tree)}")


section("b. THE EMPLOYEE DOCUMENT DEREFERENCES THE HOME")
doc_tree = _tree(DOC)
bd = _func(doc_tree, "build_boost_doc")
ok("b1 build_boost_doc exists", bd is not None)
doc_calls = _calls_in(bd) if bd else set()
ok("b2 the document reads resolve_terms",
   any(c.endswith(".resolve_terms") for c in doc_calls),
   "the PDF must print the engine's resolved rates, never its own reading of the config")
ok("b3 the document reads resolve_kpi_targets",
   any(c.endswith(".resolve_kpi_targets") for c in doc_calls))
ok("b4 the document holds NO term-dict literal", _term_dict_literals(doc_tree) == [],
   f"lines {_term_dict_literals(doc_tree)}")
# It must not reach into payout_config columns directly — that is how it would drift.
raw_cols = sorted({
    n.args[0].value
    for n in ast.walk(bd or ast.Module(body=[], type_ignores=[]))
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
    and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
    and n.args[0].value in {"premium_flat", "byod_flat", "upgrade_flat", "acc_rate",
                            "setup_fee_rate", "trade_in_spiff", "acima_spiff",
                            "tier_100_min_kpis", "tier_75_min_kpis", "tier_75_pct",
                            "tier_50_pct", "straight_line", "byod_extra_spiff"}})
ok("b5 the document never reads a payout_config column directly", raw_cols == [],
   f"reads {raw_cols} — must go through resolve_terms")


section("c. NO SECOND COPY ANYWHERE UNDER backend/app")
# The signature of a second copy: the engine's own config KEY paired with the engine's own DEFAULT
# literal in the same expression. Scanning for the key alone would flag every legitimate reader
# (the settings save, the config endpoint); the key AND the magic number together is the copy.
DEFAULT_OF = {"premium_flat": {5}, "byod_flat": {3}, "upgrade_flat": {20},
              "trade_in_spiff": {20}, "acima_spiff": {25},
              "tier_100_min_kpis": {7}, "tier_75_min_kpis": {5},
              "tier_75_pct": {0.75}, "tier_50_pct": {0.5, 0.50},
              "acc_rate": {0.1, 0.10}, "setup_fee_rate": {0.1, 0.10}}
# Excused by name, with the reason — never by a blanket path prefix.
EXCUSED = {
    HOME_REL: "the home itself",
}
copies = []
for root, _dirs, files in os.walk(APP):
    for f in files:
        if not f.endswith(".py"):
            continue
        path = os.path.join(root, f)
        rel = os.path.relpath(path, APP)
        if rel in EXCUSED:
            continue
        try:
            t = _tree(path)
        except SyntaxError:
            continue
        for n in ast.walk(t):
            # Any expression that mentions a term key as a string constant AND one of its default
            # literals as a number constant, within the same BoolOp/IfExp — the `or 7` / `else 5` shape.
            if not isinstance(n, (ast.BoolOp, ast.IfExp)):
                continue
            consts = [c for c in ast.walk(n) if isinstance(c, ast.Constant)]
            strs = {c.value for c in consts if isinstance(c.value, str)}
            nums = {c.value for c in consts if isinstance(c.value, (int, float))
                    and not isinstance(c.value, bool)}
            for key, defaults in DEFAULT_OF.items():
                if key in strs and (nums & defaults):
                    copies.append(f"{rel}:{n.lineno} ({key})")
copies = sorted(set(copies))
ok("c1 no module outside the home pairs a term key with its default literal", copies == [],
   "; ".join(copies))


section("d. THE HOME STAYS PURE")
home_tree = _tree(HOME)
impure = []
for n in ast.walk(home_tree):
    if isinstance(n, ast.Import):
        impure += [a.name for a in n.names]
    elif isinstance(n, ast.ImportFrom) and n.module:
        impure.append(n.module)
BANNED = ("supabase", "reportlab", "requests", "httpx", "pandas", "app.core.database",
          "psycopg", "sqlalchemy")
hits = sorted({m for m in impure if any(m.startswith(b) for b in BANNED)})
ok("d1 the home imports no DB client, no HTTP library and no PDF stack", hits == [], str(hits))
ok("d2 the home defines both resolvers",
   _func(home_tree, "resolve_terms") is not None
   and _func(home_tree, "resolve_kpi_targets") is not None)
ok("d3 the home publishes DEFAULTS for the surfaces that need an empty state",
   any(isinstance(n, ast.Assign)
       and any(isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in n.targets)
       for n in home_tree.body))


section("e. THE HANDOUT CARRIES NO RATE HISTORY")
router_tree = _tree(ROUTER)
hist = _func(router_tree, "payout_terms_history")
ok("e1 the history endpoint exists and is separate", hist is not None)
hist_calls = _calls_in(hist) if hist else set()
ok("e2 the history endpoint builds NO document",
   not any("build_boost_doc" in c or "render_pdf" in c for c in hist_calls),
   "rate history must never become a page of the employee handout")
doc_src = ast.get_source_segment(_src(DOC), bd) if bd else ""
ok("e3 the document reads no history",
   "payout-terms/history" not in (doc_src or "") and "history" not in (doc_src or "").lower(),
   "build_boost_doc must describe the terms in force, nothing about how they moved")
# And the endpoint that serves the PDF must route by the engine that actually pays, not assume one.
pd = _func(router_tree, "payout_structure_document")
ok("e4 the document endpoint resolves the paying engine",
   pd is not None and any(c.endswith("_resolve_carrier_mode") or "_resolve_carrier_mode" in c
                          for c in {n.func.id for n in ast.walk(pd)
                                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}),
   "a Boost tenant has no commission_plan rows; assuming the plan engine is what made this 400")


section("f. ARMED — the scans must fire on a planted violation")
_f_before = len(FAIL)
_fake = ast.parse("G = {'premium_flat': 1, 'byod_flat': 2, 'acc_rate': 3}")
ok("f-armed term-dict detector", _term_dict_literals(_fake) == [], "planted literal was not seen")
_fake2 = ast.parse("x = cfg.get('premium_flat') or 5")
_seen = False
for n in ast.walk(_fake2):
    if isinstance(n, (ast.BoolOp, ast.IfExp)):
        cs = [c for c in ast.walk(n) if isinstance(c, ast.Constant)]
        if "premium_flat" in {c.value for c in cs if isinstance(c.value, str)} and \
           5 in {c.value for c in cs if isinstance(c.value, (int, float))}:
            _seen = True
ok("f-armed second-copy detector", not _seen, "planted copy was not seen")
_fired = len(FAIL) - _f_before
if _fired == 2:
    FAIL[:] = FAIL[:_f_before]
    PASS.append("f1 both detectors fired on planted violations (the scans are live)")
else:
    FAIL.append(f"f1 ARMING FAILED — {_fired}/2 planted violations were missed. The scans above "
                f"prove nothing.")

print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
