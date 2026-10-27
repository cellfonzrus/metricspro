"""THE LOCK — the built-in KPI set is ONE fact, the PAID score and the SHOWN score read it, and which
metrics a tenant hand-enters is the tenant's registry answer, never a list in a file.

CLAUDE.md, "A fix is a DESIGN fix": *"One fact, one home, dereferenced — never copied… Lock it so it
cannot un-wire."*

TWO INSTANCES, one class.

  1. Owner 2026-09-24: *"what are the seven kpis, it shows only 6"*. The seven keys, their config
     columns and their defaults were written out in FOUR places — `router.ACTION_KPI_DEFS`, a literal
     dict in `calculator.py` (THE PAY ENGINE), `commcalc/kpi/page.tsx` and
     `components/EmployeeWidgets.impl.tsx`. The KPI report was fixed to seven; the rep's own card was
     NOT, so a rep tiered on seven kept reading their card as six. Measured when this lock was
     written: EmployeeWidgets still had six, ten months of "4/7" against six columns.

  2. Owner 2026-09-25, on a Boost estate: *"Manual KPI entry — Zulu · TWP · Address Checks … these
     dont belong on boost tenant, remove from cellfnz rus"*. Those three were a literal array in the
     page, so EVERY tenant was asked to hand-enter another tenant's metrics — and the mode toggle
     POSTed a definition, so touching it would have CREATED a TWP row for an org with no TWP.

THE CLASS: **a fact about which KPIs exist, copied into the screen that shows it.** Copy it and the
displayed score drifts from the paid score, or one tenant is shown another tenant's metrics — with
nothing failing, because each copy is internally consistent.

WHAT FAILS THE BUILD
  (a) ONE HOME. `kpi_failing.BUILTIN_KPI_DEFS` is the only literal of the built-in set.
      `router.ACTION_KPI_DEFS` dereferences it (no tuple list of its own) and `calculator.py` builds
      its KPI dict from it (no literal dict) — the PAY path reading the same fact as the display.
  (b) PAID == SHOWN, behaviourally: the dereferenced pay-engine dict is byte-identical to the literal
      it replaced — same keys, same columns, same defaults, same ORDER — over a default cfg, a
      configured one, and one carrying a real 0.
  (c) NO FOURTH COPY DRIFTS. Every frontend list of the scored set carries EXACTLY the built-in keys
      with matching default targets. A key added to one side and not the other → RED. (This is the
      guard that was missing when EmployeeWidgets kept six.)
  (d) THE MANUAL GRID HOLDS NO LIST. `kpi/page.tsx` has no `EXTRA_METRICS`, names no metric, carrier
      or tenant in that section, reads the registry, and filters on `auto_fed === false`.
  (e) THE DISCRIMINATOR IS DERIVED, NOT STORED. `auto_fed` comes from the feed maps, never from
      `carrier_kpi_metric.source_mode` — that column defaults to 'manual' and so reads WRONG (not
      merely absent) for all seven built-ins, which are auto-fed. Behavioural: the built-ins are
      auto-fed, the door-report metrics are not.
  (f) THE ENDPOINT STAMPS IT, so no screen has to work it out.
  (g) NEGATIVE CONTROLS: a re-introduced literal, a drifted frontend list, a manual grid naming a
      metric, a discriminator read from source_mode — each must go RED.

Runs beside the carrier-vocab / report-kind / paramount locks (.github/workflows/carrier-vocab-guard.yml).
Stdlib only, DB-free.

  python3 backend/harness_kpi_registry_lock.py
"""
import os
import re
import sys

import harnesslib as _hl   # the ONE comment/docstring stripper (§24) — locks read CODE, not prose

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend", "app", "modules", "commcalc")
FE = os.path.join(ROOT, "frontend", "src")
KPI_PAGE = os.path.join(FE, "app", "(platform)", "commcalc", "kpi", "page.tsx")
WIDGETS = os.path.join(FE, "components", "EmployeeWidgets.impl.tsx")
SETTINGS = os.path.join(FE, "app", "(platform)", "commcalc", "settings", "page.tsx")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:400]))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def fn_body(src, name):
    m = re.search(r"^def %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    rest = src[m.start():]
    nxt = re.search(r"\n(?=\S)", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


sys.path.insert(0, os.path.join(ROOT, "backend"))
import types
# stdlib-only import of `dlar_sweep`: its portal I/O needs requests/bs4, its rules need nothing. Same
# stub `harness_kpi_vintage.py` installs, for the same reason — this proof is DB-free AND wheel-free,
# so it runs in the no-install job beside every other lock in it (`harness_ci_pipefail_lock.py` §2).
for _n, _attrs in (("requests", ("Session",)), ("bs4", ("BeautifulSoup",))):
    if _n not in sys.modules:
        _m = types.ModuleType(_n)
        for _a in _attrs:
            setattr(_m, _a, lambda *a, **k: None)
        sys.modules[_n] = _m

from app.modules.commcalc import kpi_failing as _KF          # noqa: E402  (pure, stdlib)
from app.modules.commcalc import dlar_sweep as _ds           # noqa: E402  (the declared field maps)

ROUTER = read(os.path.join(BE, "router.py"))
KPIF = read(os.path.join(BE, "kpi_failing.py"))
SWEEP = read(os.path.join(BE, "dlar_sweep.py"))
CALC = read(os.path.join(BE, "calculator.py"))
PAGE = read(KPI_PAGE)
WID = read(WIDGETS)
SET = read(SETTINGS)

BUILTIN = list(_KF.BUILTIN_KPI_DEFS)
KEYS = [k for (k, _l, _c, _d) in BUILTIN]
DEFAULTS = {k: d for (k, _l, _c, d) in BUILTIN}

print("\nKPI registry lock — one built-in set, the paid score and the shown score on it,\n"
      "and the hand-entry grid driven by the tenant's own registry\n")


# ── (a) one home ──────────────────────────────────────────────────────────────────────────────────
def scan_second_literal(src, label):
    """A re-written literal of the built-in set: >=4 of the seven kpi_*_target columns in one block."""
    bad = []
    for m in re.finditer(r"[\[\{]([^\[\]\{\}]{0,2000})[\]\}]", src, re.S):
        blk = m.group(1)
        hits = [k for (k, _l, c, _d) in BUILTIN if c in blk]
        if len(hits) >= 4:
            bad.append("%s re-writes the built-in set (%s)" % (label, ", ".join(hits)))
    return bad


check("(a) BUILTIN_KPI_DEFS is the one literal, and carries all seven",
      len(BUILTIN) == 7 and len(set(KEYS)) == 7, KEYS)
check("(a) router.ACTION_KPI_DEFS dereferences it — it holds no tuple list of its own",
      "ACTION_KPI_DEFS = [tuple(d) for d in _kpi_failing.BUILTIN_KPI_DEFS]" in ROUTER
      and not re.search(r"ACTION_KPI_DEFS\s*=\s*\[\s*\n\s*\('atu'", ROUTER))
# STRENGTHENED 2026-09-26 (index §19.28): the pay engine no longer names the built-in set at all — it
# resolves the TENANT'S registry through the one home, whose documented fallback IS the built-in seven.
# That is a stricter property than "it dereferences BUILTIN_KPI_DEFS": it also forbids the engine from
# having a set at all, and requires the registry to actually reach it.
check("(a) the PAY ENGINE holds NO KPI set of its own — it resolves the tenant's registry through the"
      " one home, which falls back to the built-in seven",
      "_kpi_failing.resolve_defs(cfg.get('kpi_defs'))" in CALC
      and "BUILTIN_KPI_DEFS" in fn_body(KPIF, "resolve_defs")
      and not re.search(r"'atu'\s*:\s*float\(cfg\.get", CALC))
# The PAY ENGINE is where a second literal does the damage (the paid score silently leaving the
# shown one), and it names these columns for no other reason. router.py names them legitimately all
# over — /targets, payout config, the action plan — so its own copy is caught by the assertion above
# rather than by this scan.
check("(a) no second literal of the set in the pay engine",
      not scan_second_literal(CALC, "calculator.py"), scan_second_literal(CALC, "calculator.py"))


# ── (b) paid == shown, behaviourally ──────────────────────────────────────────────────────────────
def pay_dict(cfg):
    return {k: float(cfg.get(col) or dflt) for (k, _l, col, dflt) in BUILTIN}


def pay_dict_literal(cfg):
    """The exact expression calculator.py carried before the dereference."""
    return {'atu': float(cfg.get('kpi_atu_target') or 55), 'protect': float(cfg.get('kpi_protect_target') or 80),
            'boostapp': float(cfg.get('kpi_boostapp_target') or 65), 'familyplan': float(cfg.get('kpi_familyplan_target') or 45),
            'byod': float(cfg.get('kpi_byod_target') or 35), 'tmr3': float(cfg.get('kpi_tmr3_target') or 70),
            'aal': float(cfg.get('kpi_aal_target') or 5)}


for cfg, what in (({}, "an unconfigured tenant (defaults)"),
                  ({"kpi_atu_target": 61, "kpi_aal_target": 9}, "a configured tenant"),
                  ({"kpi_tmr3_target": 0}, "a target of a real 0")):
    check("(b) the pay dict is identical to the literal it replaced — %s" % what,
          pay_dict(cfg) == pay_dict_literal(cfg) and list(pay_dict(cfg)) == list(pay_dict_literal(cfg)),
          (pay_dict(cfg), pay_dict_literal(cfg)))


# ── (c) no fourth copy drifts ─────────────────────────────────────────────────────────────────────
def fe_keys(src, var):
    """Keys of a frontend scored list `const <var> = [ … ]`."""
    m = re.search(r"const %s = \[(.*?)\n\]" % re.escape(var), src, re.S)
    if not m:
        return None, {}
    blk = m.group(1)
    keys = re.findall(r"\bk:\s*'([a-z0-9_]+)'", blk)
    tgts = dict((k, float(t)) for k, t in re.findall(r"\bk:\s*'([a-z0-9_]+)'[^}]*?\b(?:t|def):\s*([0-9.]+)", blk))
    return keys, tgts


for path, src, var, name in ((KPI_PAGE, PAGE, "KPIS", "commcalc/kpi/page.tsx"),
                             (WIDGETS, WID, "KPIS", "EmployeeWidgets.impl.tsx")):
    keys, tgts = fe_keys(src, var)
    # The SET is the fact; the ORDER of the columns on screen is presentation, and a page is free to
    # put BYOD before Family Plan. Where order carries meaning — the pay dict — (b) pins it exactly.
    check("(c) %s scores EXACTLY the built-in seven" % name,
          keys is not None and sorted(keys) == sorted(KEYS),
          ("missing: %s / extra: %s" % (sorted(set(KEYS) - set(keys or [])),
                                        sorted(set(keys or []) - set(KEYS)))))
    drift = {k: (tgts.get(k), DEFAULTS[k]) for k in DEFAULTS if k in tgts and tgts[k] != DEFAULTS[k]}
    check("(c) %s default targets match the built-in set" % name, not drift, drift)

set_cols = set(re.findall(r"kpi_[a-z0-9]+_target", SET))
check("(c) the Settings KPI tab edits exactly the built-in target columns",
      set_cols == {c for (_k, _l, c, _d) in BUILTIN}, sorted(set_cols))


# ── (d) the manual grid holds no list ─────────────────────────────────────────────────────────────
MANUAL = PAGE[PAGE.index("function ManualKpiSection"):] if "function ManualKpiSection" in PAGE else ""
check("(d) EXTRA_METRICS is gone from the page entirely", "EXTRA_METRICS" not in PAGE)
check("(d) the manual grid reads the registry", "carrier-kpi-metrics" in MANUAL)
check("(d) …and filters on the derived discriminator, not a list", "auto_fed === false" in MANUAL)
NAMED = re.compile(r"'(?:zulu|twp|twp_plus|address_checks|boostapp|atu|protect|familyplan|byod|aal|tmr3)'")
check("(d) the manual grid names NO metric key of its own", not NAMED.search(MANUAL),
      NAMED.findall(MANUAL))
check("(d) the mode toggle edits a registry row it is already showing — it cannot invent one",
      "metrics.find(x => x.key === metric)" in MANUAL and "EXTRA_METRICS.find" not in MANUAL)
check("(d) an org with nothing to hand-enter gets no empty grid",
      "if (metrics.length === 0) return null" in MANUAL)


# ── (e) the discriminator is derived, not stored ──────────────────────────────────────────────────
AF = fn_body(read(os.path.join(BE, "kpi_failing.py")), "auto_fed")
check("(e) auto_fed is derived from the feed maps", "REP_KPI_KEYS" in AF and "STORE_KPI_COLUMNS" in AF)
check("(e) auto_fed never reads source_mode (a column that is WRONG, not absent, for the built-ins)",
      "source_mode" not in AF.split('"""')[-1])
check("(e) every built-in is auto-fed", all(_KF.auto_fed(k) for k in KEYS))
check("(e) the door-report metrics are NOT auto-fed → they are the hand-entry set",
      not any(_KF.auto_fed(k) for k in ("zulu", "twp", "twp_plus", "address_checks", "acts", "autopay_all")))
check("(e) a blank / unknown key is not auto-fed", not _KF.auto_fed("") and not _KF.auto_fed(None)
      and not _KF.auto_fed("something_nobody_defined"))
check("(e) boostapp is auto-fed at REP grain although it has no STORE column",
      _KF.auto_fed("boostapp") and "boostapp" not in _KF.STORE_KPI_COLUMNS)


# ── (f) the endpoint stamps it ────────────────────────────────────────────────────────────────────
EP = fn_body(ROUTER, "list_carrier_kpi_metrics")
check("(f) GET /carrier-kpi-metrics stamps auto_fed + store_column on every row",
      '_kpi_failing.auto_fed(' in EP and '_kpi_failing.STORE_KPI_COLUMNS.get(' in EP)


# ── (g) negative controls ─────────────────────────────────────────────────────────────────────────
check("(g) a re-introduced literal of the set in the pay engine → RED",
      bool(scan_second_literal(CALC + "\n" + re.sub(r"^def ", "", str(pay_dict_literal.__doc__ or "")) +
                               """
    KPI = {'atu': float(cfg.get('kpi_atu_target') or 55), 'protect': float(cfg.get('kpi_protect_target') or 80),
           'byod': float(cfg.get('kpi_byod_target') or 35), 'tmr3': float(cfg.get('kpi_tmr3_target') or 70)}
""", "calculator.py")))
bad_keys, _ = fe_keys(WID.replace("{ k: 'boostapp', label: 'Carrier App', t: 65 },\n  ", ""), "KPIS")
check("(g) a frontend list that drops a KPI → RED  ← the six-of-seven defect", bad_keys != KEYS, bad_keys)
_, bad_t = fe_keys(WID.replace("label: 'Carrier App', t: 65", "label: 'Carrier App', t: 60"), "KPIS")
check("(g) a frontend default target that drifts → RED", bad_t.get("boostapp") != DEFAULTS["boostapp"])
check("(g) a manual grid that names a metric again → RED",
      bool(NAMED.search(MANUAL + "\nconst X = [{ key: 'zulu' }]".replace("key:", "k:"))))
check("(g) a manual grid that goes back to a literal list → RED",
      "EXTRA_METRICS" in (PAGE + "\nconst EXTRA_METRICS = []"))


# ── (h) A KPI VALUE'S FEED, ITS VINTAGE AND ITS DENOMINATOR (owner defect 2026-09-26, index §19.28) ──
# THREE MORE INSTANCES OF THE SAME CLASS the sections above close. (1) WHICH COLUMN carries a KPI was a
# literal inside the PAY ENGINE at rep grain (`dr.get('atu_pct')`, `dr.get('device_insurance_pct') or
# dr.get('protect_pct')`, the store roll-down) while `STORE_KPI_COLUMNS` was the home of the same fact at
# store grain. (2) An ABSENT measurement was read with `safe_float`, which returns 0.0, so a KPI nobody
# measured was a KPI failed at 0% — against a denominator that was the literal `7`. (3) The sweep's own
# derived rate divided by a denominator it had not got and wrote the quotient as `0`.
print("\n(h) the feed map, the vintage and the denominator")

KPI_RATE_COLS = ("atu_pct", "device_insurance_pct", "boost_app_pct", "byod_pct",
                 "family_plan_pct", "aal_conversion")
check("(h) REP_DLAR_COLUMNS is the one rep-grain feed map, and it lives in kpi_failing",
      "REP_DLAR_COLUMNS = {" in KPIF and "REP_DLAR_COLUMNS" not in CALC.split("import")[-1].split("REP_DLAR")[0]
      or "REP_DLAR_COLUMNS = {" in KPIF)
CALC_CODE, SWEEP_CODE = _hl.py_code_only(CALC), _hl.py_code_only(SWEEP)
check("(h) the PAY ENGINE names no DLAR column of its own — not one of them appears in its CODE",
      not [c for c in KPI_RATE_COLS if c in CALC_CODE], [c for c in KPI_RATE_COLS if c in CALC_CODE])
check("(h) the PAY ENGINE never reads a KPI through safe_float (which turns an absence into a 0.0)",
      "safe_float(dr.get" not in CALC_CODE and "safe_float(sr.get" not in CALC_CODE)
check("(h) the PAY ENGINE counts the met-set through the one scorer, not a sum of its own dict",
      "_kpi_failing.score(" in CALC_CODE
      and not re.search(r"sum\(1 for k\s*,\s*v in kpi_vals", CALC_CODE))
check("(h) the paid denominator is what was MEASURED — no literal total_kpis in the pay engine",
      not re.search(r"'total_kpis'\s*:\s*\d", CALC_CODE),
      re.findall(r"'total_kpis'\s*:\s*\d+", CALC_CODE))
check("(h) every built-in KPI still has a feed: the rep-grain and store-grain maps cover all seven",
      set(re.findall(r'"(\w+)":\s*\(', KPIF.split("REP_DLAR_COLUMNS = {")[1].split("}")[0]))
      | set(re.findall(r'"(\w+)":\s*"', KPIF.split("STORE_KPI_COLUMNS = {")[1].split("}")[0]))
      == set(KEYS))
check("(h) the SWEEP derives its one rate through derived_rate — the `else 0` expression is gone",
      'row["boost_app_pct"] = derived_rate(' in SWEEP_CODE
      and "if ga_prepaid > 0 else 0" not in SWEEP_CODE)
# Since 2026-10-16 the source field for each column is DECLARED (`dlar_sweep.FIELD_MAPS`) rather than
# written as a `rec.get("…")` literal inside the normalizer, so this asks the declaration: every KPI
# rate must be declared RATE. Scanning for the old literal would now pass vacuously, which is the way
# a lock quietly stops locking.
_RATE_SOURCE_FIELDS = ("atu_loading_rate", "insurance_take_rate", "byod_rate", "family_plan_percent",
                       "three_mr", "aal_conversion", "protect_total_attach", "atu_loading_percent")
_DECLARED_KIND = {}
for _m in (_ds.STORE_FIELDS, _ds.REP_FIELDS):
    for _row in _m:
        for _k in _row[2:]:
            _DECLARED_KIND.setdefault(_k, set()).add(_row[1])
check("(h) every KPI RATE field on both DLAR reports is DECLARED a rate, never a count",
      not [c for c in _RATE_SOURCE_FIELDS if _DECLARED_KIND.get(c) != {_ds.RATE}],
      {c: sorted(_DECLARED_KIND.get(c) or ()) for c in _RATE_SOURCE_FIELDS
       if _DECLARED_KIND.get(c) != {_ds.RATE}})
check("(h) and the UNIT COUNTS are declared counts, so an unsent count lands absent and not 0 "
      "(owner report 2026-10-16: 28 stores filed October with 0 activations beside a 66.67% ATU)",
      all(_DECLARED_KIND.get(c) == {_ds.COUNT}
          for c in ("gross_activation_quantity", "upgrades", "prepaid_activations",
                    "postpaid_activations", "activations", "atu_activations")),
      {c: sorted(_DECLARED_KIND.get(c) or ())
       for c in ("gross_activation_quantity", "upgrades", "prepaid_activations",
                 "postpaid_activations", "activations", "atu_activations")})
check("(h) NEITHER reading can turn an absent cell into a zero — both delegate to the one home",
      _ds._num("") is None and _ds._num(None) is None and _ds._rate("") is None
      and _ds._num("0") == 0.0 and _ds._rate("0") == 0.0)
check("(h) each GRAIN carries its own as-of date — the rep report's import_date is no longer discarded",
      "rep_recs, rep_import_date = fetch_report" in SWEEP and "def vintage(" in SWEEP
      and '"as_of_date": as_of[tbl]' in SWEEP)
check("(h) the sweep's STATUS is derived from what was WRITTEN, not spelled from what was pulled",
      "dlar_sweep.status_sentence(res)" in ROUTER
      and "{res['stores']} stores, {res['reps']} reps" not in _hl.py_code_only(ROUTER))
check("(h) no surface fabricates a denominator of seven any more",
      "total_kpis') or 7" not in ROUTER and "total_kpis ?? 7" not in WID
      and "total_kpis\") or 7" not in ROUTER)
LIN = read(os.path.join(BE, "data_lineage_registry.py"))
check("(h) WHICH COLUMNS MUST CARRY A VALUE lives in the lineage registry, and the SWEEP DEREFERENCES it "
      "— no second list of required columns in the sweep (index §19.28 completeness axis)",
      "REQUIRED_CONTENT_COLUMNS = {" in LIN and "def content_arrival(" in LIN
      and "_lineage.required_content_columns(" in SWEEP_CODE
      and "_lineage.content_arrival(" in SWEEP_CODE
      and "REQUIRED_CONTENT_COLUMNS" not in SWEEP_CODE)
# RE-AIMED 2026-10-27, NOT RELAXED (index §COMMCALC-40). This pin used to scan for the literal
# `if not (skipped or gone or shape)` inside `status_sentence`. That condition still decides the
# verdict — it moved into `dlar_sweep.status_verdict`, the one home the sentence AND the router's
# stored status word now both dereference, because the router was storing the literal 'ok' beside a
# detail line reading "⚠ PARTIAL". A pin whose subject moves is re-aimed at the new form; left as it
# was it would have failed for the right reason and been "fixed" by deleting it. It is now a
# BEHAVIOURAL assertion on each of the three conditions, so no later refactor can satisfy it by
# spelling.
check("(h) a column that stopped arriving CANNOT sit under a green OK",
      'COLUMN STOPPED ARRIVING' in SWEEP
      and _ds.status_verdict({"content": {"raw_dlar_store": {"rows": 28,
                                                             "empty_columns": ["gross_adds"]}}}) == "partial"
      and _ds.status_verdict({"skipped_guard": ["raw_dlar_rep"]}) == "partial"
      and _ds.status_verdict({"fields": {"dlar": {"missing": ["gross_activation_quantity"]}}}) == "partial"
      and _ds.status_verdict({}) == "ok"
      # …and the WORD stored beside the sentence is that same verdict, never a literal.
      and 'dlar_sweep.status_verdict(res)' in ROUTER
      and "_dlar_set_status(client, org_id, 'ok'" not in ROUTER)
check("(h) nor can a PORTAL FIELD that stopped arriving — and the fields the portal sends that "
      "nothing reads are named beside it, which is where the new spelling is (owner 2026-10-16)",
      'PORTAL FIELD CHANGED' in SWEEP and 'NOTHING READS' in SWEEP
      and '_cell.field_arrival(' in SWEEP_CODE
      and 'declared_source_fields("dlar")' in SWEEP_CODE
      and 'declared_source_fields("advocate")' in SWEEP_CODE)
check("(h) WHICH SOURCE FIELD FEEDS WHICH COLUMN has ONE home — the normalizers dereference the "
      "declaration and no longer carry `rec.get(\"…\")` literals of their own",
      'FIELD_MAPS = {' in SWEEP_CODE
      and SWEEP_CODE.count('rec.get("') == 0,
      SWEEP_CODE.count('rec.get("'))
check("(h) THE CALCULATION RECORDS THE KPI VINTAGE IT TIERED ON — a mid-month slice is never paid on as "
      "a closed month in silence, and it is the ONE home of the question, not a re-derivation",
      "dlar_sweep.slice_vintage(" in ROUTER
      and "KPI slice this run tiered on is as of" in ROUTER
      and "DIFFERENT vintages" in ROUTER
      and "calc_notices.append(" in ROUTER)
# The vintage block ends where the run hands its inputs to THE calculation — since index §6k that call is
# `_calc_rep_rows(_in, period)` (the shared helper `_run_calculation` and `recompute_rep` both use). The end
# marker must be FOUND, or the slice would run to the end of the file and read unrelated raises.
_VINT_END = "result = _calc_rep_rows("
_after_vint = _hl.py_code_only(ROUTER).split("slice_vintage(")[1]
check("(h) …and it does NOT refuse the run (blocking a payroll recalculation is the owner's policy call)",
      _VINT_END in _after_vint and "raise" not in _after_vint.split(_VINT_END)[0])
check("(h) THE GRAIN a KPI is measured at is DERIVED from the feed maps, never stored",
      "def grain_of(" in KPIF and "REP_DLAR_COLUMNS" in fn_body(KPIF, "grain_of")
      and "STORE_KPI_COLUMNS" in fn_body(KPIF, "grain_of"))
check("(h) …and every surface that shows a KPI set STAMPS it, so no screen works the grain out",
      '_kpif.grain_of(k)' in ROUTER and '_kpi_failing.grain_of(k)' in ROUTER)
check("(h) NEG the sweep carrying its own required-column list again → RED",
      "REQUIRED_CONTENT_COLUMNS" in (SWEEP_CODE + '\nREQUIRED_CONTENT_COLUMNS = {"raw_dlar_rep": ()}'))
check("(h) NEG a stopped column back under a green OK → RED",
      'if not skipped' in (SWEEP_CODE.replace("if not (skipped or gone)", "")
                           + '\n head = "OK" if not skipped else "x"'))
check("(h) the DISPLAY siblings read the one classifier — neither turns a missing value into a 0%",
      "safe_float(kv.get(k))" not in ROUTER and "_kpi_failing.evaluate(kv," in ROUTER,
      ROUTER.count("safe_float(kv.get(k))"))

# negative controls — each re-introduction must go RED
check("(h) NEG a re-introduced DLAR column literal in the pay engine → RED",
      bool([c for c in KPI_RATE_COLS
            if c in (CALC_CODE + "\n    rep_atu = safe_float(dr.get('atu_pct'))")]))
check("(h) NEG a re-introduced safe_float read of a KPI → RED",
      "safe_float(dr.get" in (CALC_CODE + "\nx = safe_float(dr.get('atu_pct'))"))
check("(h) NEG a literal total_kpis back in the pay engine → RED",
      bool(re.search(r"'total_kpis'\s*:\s*\d", CALC_CODE + "\n 'total_kpis': 7,")))
check("(h) NEG the `else 0` derived rate back in the sweep → RED",
      "if ga_prepaid > 0 else 0" in (SWEEP_CODE + "\nx = (b / ga_prepaid * 100) if ga_prepaid > 0 else 0"))
check("(h) NEG a rate column parsed by _num again → RED",
      '_num(rec.get("three_mr"' in (SWEEP_CODE + '\n x = _num(rec.get("three_mr")),'))
check("(h) NEG a display surface fabricating 7 again → RED",
      "total_kpis ?? 7" in (WID + "\n{data.report_card.total_kpis ?? 7}"))


# ── wired, or it is not a lock ────────────────────────────────────────────────────────────────────
wf = read(WORKFLOW) if os.path.exists(WORKFLOW) else ""
check("(wired) this lock runs in carrier-vocab-guard.yml", "harness_kpi_registry_lock.py" in wf)
check("(wired) the guard re-runs when the KPI home or its readers change",
      "backend/app/modules/commcalc/kpi_failing.py" in wf)
check("(wired) the guard re-runs when the pay engine or the DLAR sweep changes",
      "backend/app/modules/commcalc/calculator.py" in wf
      and "backend/app/modules/commcalc/dlar_sweep.py" in wf)
check("(wired) …and when the lineage registry that declares the required columns changes",
      "backend/app/modules/commcalc/data_lineage_registry.py" in wf)
check("(wired) the MONEY proof for this class runs in CI too",
      "harness_kpi_vintage.py" in wf)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one built-in KPI set; the paid score and the shown score read it; the hand-entry grid is the tenant's own.")
