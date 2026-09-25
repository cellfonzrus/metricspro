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
from app.modules.commcalc import kpi_failing as _KF          # noqa: E402  (pure, stdlib)

ROUTER = read(os.path.join(BE, "router.py"))
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
check("(a) the PAY ENGINE builds its KPI dict from the same home — no literal dict",
      "_kpi_failing.BUILTIN_KPI_DEFS" in CALC and not re.search(r"'atu'\s*:\s*float\(cfg\.get", CALC))
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


# ── wired, or it is not a lock ────────────────────────────────────────────────────────────────────
wf = read(WORKFLOW) if os.path.exists(WORKFLOW) else ""
check("(wired) this lock runs in carrier-vocab-guard.yml", "harness_kpi_registry_lock.py" in wf)
check("(wired) the guard re-runs when the KPI home or its readers change",
      "backend/app/modules/commcalc/kpi_failing.py" in wf)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one built-in KPI set; the paid score and the shown score read it; the hand-entry grid is the tenant's own.")
