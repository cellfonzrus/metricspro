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
        if tgt is None:
            continue                           # a metric with no target cannot fail anyone
        actual = _num((values or {}).get(k))
        if actual is None:
            no_data.append({"kpi": k, "label": label, "target": round(tgt, 1)})
            continue
        evaluated.append({"kpi": k, "label": label, "target": round(tgt, 1),
                          "actual": round(actual, 1), "met": actual >= tgt,
                          "gap": round(tgt - actual, 1)})
    return evaluated, no_data


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
