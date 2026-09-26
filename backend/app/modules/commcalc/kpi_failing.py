"""Failing-KPI report — pure classification logic (owner directive 2026-09-03).

OWNER DIRECTIVE (verbatim excerpt): "Create new report from the KPI for the failing KPI and it
should be a high level overview of failing KPI with the option to drill down with our standard
features."

WHAT THIS IS: a VIEW over the platform's EXISTING KPI machinery — never a second derivation
(duplicate-check gate, CLAUDE.md):
  • KPI definitions + targets = the SAME resolution /coaching and the action plan use:
    `router._kpi_defs(org_id)` (carrier_kpi_metric, mig 060, falling back to ACTION_KPI_DEFS)
    with the per-period `payout_config` target columns winning over `target_default`.
  • STORE-grain actuals = the raw_dlar_store rows `GET /dlar-store/{period}` already serves
    (span-filtered there through storeops scope_keyset — the endpoint reuses that handler
    in-process, so this report can never see a store the KPI Metrics page hides).
  • REP-grain actuals = `rep_commissions.kpi_values` — the values the pay engine actually tiered
    on (exactly what /coaching reads), so a "failing" rep here IS the rep losing tier money.
  • Store→market = the canonical union resolver (`core.scope.store_market_resolver` via
    `router._store_market_resolver` — §13a; the market-resolution CI guard forbids a sibling).

Everything in THIS module is pure + stdlib-only (proof backend/harness_kpi_failing.py). The
endpoint glue lives in commcalc/router.py `GET /kpi-failing/{period}`.

CLASSIFICATION HONESTY: a metric with NO recorded value is `no_data`, never "failing" — the
report must never accuse a store/rep off a blank cell. Only metrics with an actual < target fail.
"""

# THE BUILT-IN KPI SET — (metric_key, label, payout_config_col, default target). ONE HOME.
#
# This is the set the pay engine scores a rep on and writes into `rep_commissions.kpi_values`, and
# the set `_kpi_defs` falls back to when a tenant has defined none of their own. It lived in TWO
# places — `router.ACTION_KPI_DEFS` and a literal dict in `calculator.py` — with the same seven keys,
# the same config columns and the same defaults written out twice. A tenant KPI change that reached
# one and not the other would have moved the displayed score away from the paid score in silence,
# which is the divergence CLAUDE.md's "one fact, one home, dereferenced" rule exists to stop. Both
# now read THIS tuple; `harness_kpi_registry_lock.py` fails the build if a second copy appears.
#
# It is deliberately NOT the same fact as the registry (`carrier_kpi_metric`): the registry says what
# a TENANT has defined, this says what the platform can actually MEASURE without help. A metric in
# the registry but not here (or in STORE_KPI_COLUMNS) has no automated feed — see `auto_fed`.
BUILTIN_KPI_DEFS = (
    ("atu",        "ATU",         "kpi_atu_target",        55),
    ("protect",    "Protect",     "kpi_protect_target",    80),
    ("boostapp",   "Carrier App", "kpi_boostapp_target",   65),
    ("familyplan", "Family Plan", "kpi_familyplan_target", 45),
    ("byod",       "BYOD",        "kpi_byod_target",       35),
    ("tmr3",       "TMR3",        "kpi_tmr3_target",       70),
    ("aal",        "AAL",         "kpi_aal_target",         5),
)

# metric_key → the `raw_dlar_rep` column(s) carrying the REP-grain actual, in fallback order.
# ONE HOME (owner defect 2026-09-26, index §19.28). This map was a literal inside the PAY ENGINE
# (`calculator.calc_rep_commissions` read `dr.get('atu_pct')`, `dr.get('device_insurance_pct') or
# dr.get('protect_pct')`, … by hand) while `STORE_KPI_COLUMNS` below was the store-grain map here —
# so "which column carries this KPI" had two homes at two grains, and only one of them was locked.
# The tuple order IS the fallback order and reproduces the pay engine's own `or` chain exactly.
REP_DLAR_COLUMNS = {
    "atu":      ("atu_pct",),
    "protect":  ("device_insurance_pct", "protect_pct"),
    "boostapp": ("boost_app_pct",),
    "byod":     ("byod_pct",),
}

# metric_key → raw_dlar_store column (the store-grain actual). Keys not named here (e.g.
# `boostapp`, tenant-custom metrics) simply have no store-level DLAR value → no_data at store
# grain; they are still evaluated at rep grain when rep_commissions.kpi_values carries them.
STORE_KPI_COLUMNS = {
    "atu": "atu",
    "protect": "protect_pct",
    "byod": "byod_pct",
    "familyplan": "family_plan_pct",
    "tmr3": "tmr3",
    "aal": "aal_conversion",
}

# The keys the pay engine measures at rep grain (what lands in rep_commissions.kpi_values).
REP_KPI_KEYS = tuple(k for (k, _l, _c, _d) in BUILTIN_KPI_DEFS)


def auto_fed(metric_key):
    """Does this metric arrive on its own, or must somebody type it in?

    TRUE when the platform measures it without help — at rep grain (the pay engine writes it into
    `kpi_values`) or at store grain (a raw_dlar_store column). FALSE for a metric a tenant defined
    that no feed fills: those are the ones that need a hand-entry box, and they are the ONLY ones
    that should get one. Deriving this from the two feed maps is what lets the manual-entry grid
    stop carrying its own list of "metrics with no feed yet" — a list that was wrong the moment a
    tenant defined an eighth metric, and that showed one tenant another tenant's metrics.

    NOT read from `carrier_kpi_metric.source_mode`: that column defaults to 'manual' and is only
    written when somebody touches the toggle, so today it reads 'manual' for all seven built-ins
    that are in fact auto-fed. A column that is wrong rather than absent cannot be the discriminator.
    """
    k = str(metric_key or "").strip()
    return bool(k) and (k in REP_KPI_KEYS or k in STORE_KPI_COLUMNS)


def _num(v):
    """float or None — '', None, non-numeric → None (no data ≠ zero)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        f = float(v)
        return f if f == f else None          # NaN guard
    except (TypeError, ValueError):
        return None


def evaluate(values, defs, targets):
    """One entity's KPI values → (evaluated, no_data).

    `values`  = {metric_key: raw value} (store DLAR columns already key-mapped, or a rep's
                kpi_values dict); `defs` = _kpi_defs tuples (key, label, config_col, default);
    `targets` = {metric_key: numeric target} (the payout_config-resolved targets).

    evaluated = [{kpi, label, target, actual, met, gap}] for every metric with BOTH a target and
    an actual; gap = round(target - actual, 1), positive when failing. no_data = [{kpi, label,
    target}] for metrics whose actual is unknown — reported, never counted as failing."""
    evaluated, no_data = [], []
    for (k, label, _col, dflt) in defs or []:
        tgt = _num(targets.get(k))
        if tgt is None:
            tgt = _num(dflt)
        # A metric with no target cannot fail anyone — AND A FALSY TARGET IS NO TARGET (2026-09-26,
        # §19.28). The comparison is `actual >= target`, so a target of 0 is met by every value
        # including a fabricated one: it is a free pass, never a bar. This was already the convention
        # everywhere else — the `or dflt` chains treat a stored 0 as absent and `GET /kpi-failing`
        # filtered its own target map with `if v` — so the rule now lives HERE, once, where every
        # caller reads it. It matters because the def list is now the TENANT'S registry, and
        # `_kpi_defs` puts a row saved with no `target_default` through `safe_float` → 0.0. All seven
        # built-in defaults are positive, so every existing score is unchanged.
        if tgt is None or tgt <= 0:
            continue
        actual = _num((values or {}).get(k))
        if actual is None:
            no_data.append({"kpi": k, "label": label, "target": round(tgt, 1)})
            continue
        evaluated.append({"kpi": k, "label": label, "target": round(tgt, 1),
                          "actual": round(actual, 1), "met": actual >= tgt,
                          "gap": round(tgt - actual, 1)})
    return evaluated, no_data


# where ONE rep-grain KPI value came from, in resolution order. `None` = nothing fed it.
SOURCE_REP_DLAR   = "rep_dlar"       # the rep's own raw_dlar_rep column — the finest grain there is
SOURCE_STORE_DLAR = "store_dlar"     # the store's raw_dlar_store column, ROLLED DOWN to the rep
SOURCE_ACTUAL     = "kpi_actual"     # a measured value typed in / emailed in, at store grain
SOURCES = (SOURCE_REP_DLAR, SOURCE_STORE_DLAR, SOURCE_ACTUAL)


def resolve_defs(raw=None):
    """A tenant's KPI definitions → the `(key, label, payout_config_col, target_default)` tuples every
    caller already takes, falling back to the built-in seven. Accepts what `router._kpi_defs` returns
    (tuples/lists) or registry dicts, so the PAY engine can be handed the tenant's OWN registry through
    config without a signature change. A row with no `metric_key` is dropped; a duplicate key keeps the
    first. PURE — never raises, and an empty/garbage input yields the built-ins, never nothing."""
    out, seen = [], set()
    for r in raw or ():
        if isinstance(r, dict):
            k = str(r.get("metric_key") or r.get("key") or "").strip()
            lab = r.get("label") or k
            col = r.get("payout_config_col") or (f"kpi_{k}_target" if k else "")
            dflt = r.get("target_default")
        else:
            try:
                k, lab, col, dflt = (list(r) + [None] * 4)[:4]
            except TypeError:
                continue
            k = str(k or "").strip()
            lab = lab or k
            col = col or (f"kpi_{k}_target" if k else "")
        if not k or k in seen:
            continue
        seen.add(k)
        out.append((k, lab, col, dflt))
    return tuple(out) if out else BUILTIN_KPI_DEFS


def rep_kpi_values(defs, rep_row=None, store_row=None, actuals=None,
                   rep_columns=None, store_columns=None):
    """THE ONE rep-grain KPI resolver → ({metric_key: value|None}, {metric_key: source|None}).

    THE GRAIN IS A PROPERTY OF THE METRIC, declared once — not a blanket fallback chain:

      • a metric with a REP-grain feed (`REP_DLAR_COLUMNS`: atu / protect / boostapp / byod) is a REP
        measurement. Its value is the rep's OWN `raw_dlar_rep` column, and if the rep has no row it is
        **`None` — NOT the store's number**. Rolling a store figure down onto a rep nobody measured
        would attribute the store's performance to that rep; it is a different, better-looking lie than
        the 0.0 it replaces. (Caught by replaying this resolver against seven live months: a rep with no
        advocate row read atu 39.53 / protect 91.36 / byod 44.19 off their STORE and their met-count
        went 1 → 3. The old engine said 0 for those, meaning "no rep row"; the truth is `no_data`.)
      • a metric with NO rep-grain feed (`STORE_KPI_COLUMNS` only: familyplan / tmr3 / aal) is a STORE
        measurement and is ROLLED DOWN to every rep at that store. This is not a new rule — it is
        exactly what the Boost pay engine has always done for those three.
      • otherwise a measured `commcalc.kpi_actual` value for that store and period (`actuals` = the
        already org/period/store-scoped {metric_key: value} map) — the home of a metric a tenant defined
        that no carrier feed fills (hand-entered, or the MA door-report email import). A store-grain
        metric whose DLAR column is empty falls through to it.
      • nothing → `None`.

    `None` IS THE POINT. A metric with no basis is NOT a score of zero (owner defect 2026-09-26:
    `boost_app_pct` was written as a measured `0` from an empty denominator for 189 of 516
    `raw_dlar_rep` rows, 122 of which had sold the thing being measured). `score()` below reports it
    as `no_data` and leaves it OUT of the met-count denominator; it can never be a failed zero.

    `defs` = the tenant's own registry (`router._kpi_defs`), so a tenant is scored on the metrics IT
    has defined. A defined metric that none of the four steps can fill resolves to `None` — which is
    why adding a registry metric can never move a payout on its own. PURE, never raises."""
    rcols = rep_columns if rep_columns is not None else REP_DLAR_COLUMNS
    scols = store_columns if store_columns is not None else STORE_KPI_COLUMNS
    act = actuals if isinstance(actuals, dict) else {}
    values, sources = {}, {}
    for (k, _label, _col, _dflt) in defs or []:
        v, src = None, None
        if k in rcols:
            # A REP-GRAIN METRIC IS THE REP'S OWN, OR IT IS NOT MEASURED. No store fallback.
            for col in rcols[k]:
                v = _num((rep_row or {}).get(col))
                if v is not None:
                    src = SOURCE_REP_DLAR
                    break
        else:
            if k in scols:
                v = _num((store_row or {}).get(scols[k]))
                if v is not None:
                    src = SOURCE_STORE_DLAR
            if v is None and k in act:
                v = _num(act.get(k))
                if v is not None:
                    src = SOURCE_ACTUAL
        values[k] = v
        sources[k] = src
    return values, sources


def score(values, defs, targets):
    """THE met-count, with an HONEST denominator → (kpis_met, total_kpis, evaluated, no_data).

    `total_kpis` is the number of metrics that had BOTH a target and a VALUE — never the length of
    the definition list. A metric nothing fed is `no_data`: it is not counted as met and it is not
    counted against the rep either, because "3 of 7" when only 6 were ever measured accuses a rep of
    a failure nobody observed. The pay engine dereferences THIS rather than counting a dict it built
    itself, so the PAID denominator and the SHOWN denominator cannot drift (index §19.28).

    Byte-identical to the retired `sum(1 for k, v in kpi_vals.items() if v >= KPI[k])` whenever every
    metric has a value, which is every live Boost row to date — `harness_kpi_vintage.py` §B pins it."""
    evaluated, no_data = evaluate(values, defs, targets)
    return sum(1 for e in evaluated if e["met"]), len(evaluated), evaluated, no_data


def store_values(dlar_row, columns=None):
    """raw_dlar_store row → {metric_key: value} through the STORE_KPI_COLUMNS map (or a per-call
    override map, so a tenant-custom column mapping can be threaded without touching this file)."""
    cols = columns or STORE_KPI_COLUMNS
    return {k: (dlar_row or {}).get(col) for k, col in cols.items()}


def store_rows(dlar_rows, defs, targets, resolve_market=None, columns=None):
    """The store-grain overview: one row per raw_dlar_store row, with failing metrics first.

    resolve_market = the canonical store→market resolver (router._store_market_resolver's
    `resolve`); called on location/address/store_code in that order until one binds."""
    out = []
    for r in dlar_rows or []:
        r = r or {}
        evaluated, no_data = evaluate(store_values(r, columns), defs, targets)
        failing = sorted([e for e in evaluated if not e["met"]],
                         key=lambda e: -e["gap"])
        market = ""
        if resolve_market:
            for key in (r.get("location"), r.get("address"), r.get("store_code")):
                market = resolve_market(key) if key else ""
                if market:
                    break
        out.append({
            "store_code": (str(r.get("store_code") or "").strip() or None),
            "location": r.get("location") or r.get("address") or r.get("store_code") or "",
            "address": r.get("address") or "",
            "market": market or "",
            "failing": failing,
            "met": [e for e in evaluated if e["met"]],
            "no_data": no_data,
            "failing_count": len(failing),
            "evaluated_count": len(evaluated),
        })
    out.sort(key=lambda x: (-x["failing_count"], x["location"]))
    return out


def rep_rows(comm_rows, defs, targets):
    """The rep-grain drill-down: one row per rep_commissions row that carries kpi_values, keyed by
    the rep's store so the page can nest reps under their store. tier/kpis_met come straight off
    the computed row (the pay engine's own numbers — never recomputed here)."""
    out = []
    for c in comm_rows or []:
        c = c or {}
        kv = c.get("kpi_values") or {}
        if not isinstance(kv, dict) or not kv:
            continue
        evaluated, no_data = evaluate(kv, defs, targets)
        failing = sorted([e for e in evaluated if not e["met"]], key=lambda e: -e["gap"])
        out.append({
            "rep": (c.get("storeops_name") or c.get("epay_salesperson") or "").strip(),
            "store": (c.get("store") or "").strip(),
            "tier": _num(c.get("tier")),
            "kpis_met": c.get("kpis_met"),
            "total_kpis": c.get("total_kpis"),
            "failing": failing,
            "no_data": no_data,
            "failing_count": len(failing),
            "evaluated_count": len(evaluated),
        })
    out.sort(key=lambda x: (-x["failing_count"], x["rep"]))
    return out


def summarize(stores, reps):
    """The high-level overview numbers: how many stores/reps have ≥1 failing KPI, the total
    failing cells, and per-metric failure tallies (worst first)."""
    by_metric = {}
    for s in stores or []:
        for e in s.get("failing", []):
            m = by_metric.setdefault(e["kpi"], {"kpi": e["kpi"], "label": e["label"],
                                                "stores_failing": 0, "reps_failing": 0})
            m["stores_failing"] += 1
    for r in reps or []:
        for e in r.get("failing", []):
            m = by_metric.setdefault(e["kpi"], {"kpi": e["kpi"], "label": e["label"],
                                                "stores_failing": 0, "reps_failing": 0})
            m["reps_failing"] += 1
    metrics = sorted(by_metric.values(),
                     key=lambda m: (-(m["stores_failing"] + m["reps_failing"]), m["kpi"]))
    return {
        "stores_total": len(stores or []),
        "stores_failing": sum(1 for s in stores or [] if s.get("failing_count")),
        "reps_total": len(reps or []),
        "reps_failing": sum(1 for r in reps or [] if r.get("failing_count")),
        "failing_cells": (sum(s.get("failing_count", 0) for s in stores or [])
                          + sum(r.get("failing_count", 0) for r in reps or [])),
        "by_metric": metrics,
    }
