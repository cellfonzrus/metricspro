"""DB-FREE PROOF — the SINGLE-REP recompute resolves pay exactly like the FULL RUN.

THE DEFECT (owner-reported class, 2026-09-17). `POST /commcalc/recompute-rep` carried its own, shorter
copy of "what does this rep's plan pay them", and the copy had drifted from the full run's in two ways:

  1. it never applied `_override_plan_by_rep_with_mtd`, so for a rep whose plan has
     `commission_basis='exec_mtd'` the RULES engine alone pays $0.00 — and the endpoint would UPSERT
     `total_payout = 0.00` over a correct figure, in a row that looks legitimately calculated;
  2. it omitted `source_mode`, so under `commission_org_config.sales_source='union'` (mig 306) it read
     a DIFFERENT sales basis than the full run for the same rep.

Two paths answering the same question is a defect — they drift, and this one drifted into destroying
money. The fix is ONE shared resolver, `router._resolve_plan_by_rep`, called by `_apply_new_engines`
and by `recompute_rep`. §A reproduces the $0.00 and pins the repair; §E pins the sharing structurally
so the copy cannot come back.

CARRIER/TENANT-AGNOSTIC: the only thing the resolver consults is each plan's own `commission_basis`.
§F asserts no org, market, carrier or tenant name appears in its executable lines — "we are only
working in LuxeLink" is a statement about whose data matters, never a licence to branch on a tenant.

NO DATABASE: `commission_engine.preview`, `_load_plans`, `_commission_mtd_result`, `_rep_canon_map`
and `_sales_source_mode` are all module-level names, so they are substituted with stubs and the REAL
`_resolve_plan_by_rep` runs against them. Nothing is mocked that the function under test owns.

Run:  cd backend && python3 harness_recompute_rep_parity.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.modules.commcalc.router as RT
import app.modules.commcalc.commission_engine as CE

FAILS = []
N = [0]


def ok(label, cond, detail=""):
    N[0] += 1
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"   {detail}"))
    if not cond:
        FAILS.append(label)


def eq(label, got, want):
    ok(label, got == want, f"got={got!r} want={want!r}")


# ── the world under test ─────────────────────────────────────────────────────────────────────────
# TWO plans, distinguished ONLY by commission_basis — which is the whole point.
MTD_PLAN = {"id": "p-mtd", "name": "PLAN ON THE MTD BASIS", "commission_basis": "exec_mtd",
            "is_active": True}
RULES_PLAN = {"id": "p-rules", "name": "PLAN ON THE RULES BASIS", "commission_basis": "rules",
              "is_active": True}
# The rules engine's own view. The rep on the exec_mtd plan resolves to $0.00 through it — THAT is the
# number the old single-rep copy would have written over a correct figure.
PREVIEW_ROWS = [
    {"rep": "Mtd Rep", "plan_name": MTD_PLAN["name"], "total_payout": 0.0,
     "setup_fee_comm": 0.0, "acc_comm": 0.0},
    {"rep": "Rules Rep", "plan_name": RULES_PLAN["name"], "total_payout": 434.94,
     "setup_fee_comm": 0.0, "acc_comm": 359.94},
]
# What the exec-MTD basis actually pays that rep.
MTD_ROWS = [{"employee": "Mtd Rep", "commission": 777.78, "setup_fee_comm": 75.0, "acc_comm": 57.78}]

CALLS = {"preview_kwargs": [], "mtd_plans": []}


def install(sales_source="union"):
    """Substitute the resolver's collaborators. Returns a restore callable."""
    saved = (CE.preview, CE._load_plans, RT._commission_mtd_result, RT._rep_canon_map,
             RT._sales_source_mode)

    def _preview(client, org_id, period, **kw):
        CALLS["preview_kwargs"].append(dict(kw))
        rows = PREVIEW_ROWS
        if kw.get("only_rep"):
            want = str(kw["only_rep"]).strip().lower()
            rows = [r for r in PREVIEW_ROWS if str(r["rep"]).strip().lower() == want]
        return {"by_rep": [dict(r) for r in rows], "setup_fee": None}

    def _mtd(client, org_id, period, plan, *a, **k):
        CALLS["mtd_plans"].append(plan.get("id"))
        return {"by_rep": [dict(r) for r in MTD_ROWS]}

    CE.preview = _preview
    CE._load_plans = lambda client, org_id: ([dict(MTD_PLAN), dict(RULES_PLAN)], True)
    RT._commission_mtd_result = _mtd
    RT._rep_canon_map = lambda client, org_id, *a, **k: {}
    RT._sales_source_mode = lambda client, org_id, *a, **k: sales_source

    def restore():
        (CE.preview, CE._load_plans, RT._commission_mtd_result, RT._rep_canon_map,
         RT._sales_source_mode) = saved
    return restore


def resolve(only_rep=None, sales_source="union"):
    CALLS["preview_kwargs"].clear()
    CALLS["mtd_plans"].clear()
    restore = install(sales_source)
    try:
        return RT._resolve_plan_by_rep(object(), "org-under-test", "SOME PERIOD", only_rep=only_rep)
    finally:
        restore()


# ══ §A  THE REGRESSION — the $0.00 write, reproduced then repaired ═══════════════════════════════
print("\n§A  a rep on an exec_mtd-basis plan: the rules engine says $0.00; the resolver must not")
eq("A1 DEFECT CONFIRMED: the rules engine alone pays this rep $0.00",
   [r["total_payout"] for r in PREVIEW_ROWS if r["rep"] == "Mtd Rep"], [0.0])
single = resolve(only_rep="Mtd Rep")
eq("A2 the single-rep resolution now returns the exec-MTD figure, NOT $0.00",
   single["MTD REP"]["amount"], 777.78)
ok("A3 so the endpoint can no longer write a zero over a correct figure",
   single["MTD REP"]["amount"] != 0.0)
eq("A4 the plan is named from the paying basis", single["MTD REP"]["plan_name"], MTD_PLAN["name"])
eq("A5 the named slices ride along", (single["MTD REP"]["setup_fee_comm"],
                                      single["MTD REP"]["acc_comm"]), (75.0, 57.78))

# ══ §B  PARITY — single-rep == that rep's slice of the full run ══════════════════════════════════
print("\n§B  the single-rep answer equals the full-run answer, for the same rep")
full = resolve(only_rep=None)
for key in ("MTD REP", "RULES REP"):
    one = resolve(only_rep=key.title())
    eq(f"B1 {key}: single {one.get(key)} == full {full.get(key)}", one.get(key), full.get(key))
eq("B2 the full run resolves both reps", sorted(full), ["MTD REP", "RULES REP"])
eq("B3 a single-rep call resolves only that rep", sorted(resolve(only_rep="Mtd Rep")), ["MTD REP"])

# ══ §C  THE CONTROL — a rules-basis rep is untouched ═════════════════════════════════════════════
print("\n§C  a rules-basis rep (and any tenant with no exec_mtd plan) is byte-identical")
eq("C1 the rules-basis rep keeps the rules-engine amount",
   resolve(only_rep="Rules Rep")["RULES REP"],
   {"amount": 434.94, "plan_name": RULES_PLAN["name"], "setup_fee_comm": 0.0, "acc_comm": 359.94})
# a world with NO exec_mtd plan at all: the override must be a no-op
CALLS["preview_kwargs"].clear()
CALLS["mtd_plans"].clear()
_restore = install()
CE._load_plans = lambda client, org_id: ([dict(RULES_PLAN)], True)
try:
    no_mtd = RT._resolve_plan_by_rep(object(), "org", "P")
finally:
    _restore()
eq("C2 no exec_mtd plan -> nothing is overridden, both reps keep their rules amounts",
   {k: v["amount"] for k, v in no_mtd.items()}, {"MTD REP": 0.0, "RULES REP": 434.94})
ok("C3 ...and no exec-MTD computation is even attempted", CALLS["mtd_plans"] == [],
   f"called for {CALLS['mtd_plans']}")

# ══ §D  THE SECOND HALF OF THE DRIFT — source_mode must reach preview ════════════════════════════
print("\n§D  the sales basis is threaded on BOTH paths (mig 306)")
resolve(only_rep="Mtd Rep", sales_source="union")
eq("D1 single-rep forwards source_mode", CALLS["preview_kwargs"][0].get("source_mode"), "union")
eq("D2 single-rep forwards only_rep", CALLS["preview_kwargs"][0].get("only_rep"), "Mtd Rep")
resolve(only_rep=None, sales_source="legacy")
eq("D3 the full run forwards source_mode too", CALLS["preview_kwargs"][0].get("source_mode"), "legacy")
ok("D4 and does NOT pass only_rep", "only_rep" not in CALLS["preview_kwargs"][0],
   f"kwargs={CALLS['preview_kwargs'][0]}")

# ══ §E  THE COPY CANNOT COME BACK (duplicate-check build gate) ═══════════════════════════════════
print("\n§E  ONE resolver, structurally")
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "app", "modules", "commcalc", "router.py")).read()
_code = "\n".join(l for l in _src.splitlines() if not l.strip().startswith("#"))
eq("E1 _override_plan_by_rep_with_mtd is CALLED in exactly one place",
   len(re.findall(r"^\s*_override_plan_by_rep_with_mtd\(", _code, re.M)), 1)
eq("E2 ...and that place is inside the shared resolver",
   _code.index("_override_plan_by_rep_with_mtd(\n") > _code.index("def _resolve_plan_by_rep("), True)
_calls = [m for m in re.findall(r"^(.*_resolve_plan_by_rep\(client, org_id, period.*)$", _code, re.M)
          if not m.strip().startswith("def ")]
eq("E3 both money paths — and only those two — call the shared resolver", len(_calls), 2)
# `plan_by_rep[rn] = {` legitimately appears twice: once in the shared resolver (built from a PREVIEW
# row, keyed on total_payout) and once inside _override_plan_by_rep_with_mtd, which is the exec-MTD
# basis writing its OWN entries (keyed on commission). What must be unique is the PREVIEW-sourced one.
_blocks = re.findall(r"plan_by_rep\[rn\]\s*=\s*\{(?:[^}]|\n)*?\}", _code)
_from_preview = [b for b in _blocks if "total_payout" in b]
_from_mtd = [b for b in _blocks if '"commission"' in b or "get(\"commission\")" in b]
eq("E4 exactly ONE place builds plan_by_rep from a preview row", len(_from_preview), 1)
eq("E5 ...and exactly one builds it from the exec-MTD basis (the override itself)",
   len(_from_mtd), 1)

# ══ §F  RULE TWO ═════════════════════════════════════════════════════════════════════════════════
print("\n§F  the resolver names no tenant, market or carrier")
import ast as _ast
_tree = _ast.parse(_src)
_fn = next(n for n in _tree.body
           if isinstance(n, _ast.FunctionDef) and n.name == "_resolve_plan_by_rep")
_doc = set()
if _fn.body and isinstance(_fn.body[0], _ast.Expr) and isinstance(_fn.body[0].value, _ast.Constant):
    _doc = set(range(_fn.body[0].lineno, _fn.body[0].end_lineno + 1))
_lines = _src.splitlines()
_body = "\n".join(_lines[i - 1] for i in range(_fn.lineno, _fn.end_lineno + 1)
                  if i not in _doc and not _lines[i - 1].strip().startswith("#")).lower()
_banned = ("luxelink", "boost", "cricket", "total wireless", "vidapay", "chicago",
           '"ny"', "'ny'", "854f6d7b")
eq("F1 no tenant / carrier / market / org-id literal in its executable lines",
   [w for w in _banned if w in _body], [])
ok("F2 the ONLY plan attribute it branches on is commission_basis",
   "commission_basis" in _body and "org_id ==" not in _body)

print("\n" + "=" * 78)
print(f"{N[0]} checks, {len(FAILS)} failed" + ("" if not FAILS else f"  -> {FAILS}"))
print("=" * 78)
sys.exit(1 if FAILS else 0)
