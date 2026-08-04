"""DM accessory-target ATTRIBUTION — pure, DB-free functions.

Owner directive 2026-08-04 (answering ledger Q7), verbatim intent: "my team accessory numbers are
the accessory target for the [stores] calculated by the schedule and for the dm it is the total of
employees which run under him for the stores they worked in, if an employee works under 2 dms then
their target for that store goes under the dm for that market."

THE RULE, decomposed
---------------------
1. A rep's accessory NUMBER = their accessory TARGET, schedule-derived (mod-commission's Daily
   Targets engine already computes this per store per rep — a monthly-target proration off the
   rep's share of the store's scheduled hours; see `GET /commcalc/targets/{period}/calendar
   ?scope=rep`). This module does NOT recompute that math (money-adjacent, mod-commission-owned) —
   the router calls that endpoint and feeds its result in here as a plain ROW.
2. A DM's total = sum of every (employee, store) row whose STORE'S MARKET is a market granted to
   that DM.
3. Cross-DM employee: attribution is PER (employee, store) ROW, not per employee. An employee who
   worked stores in two different DMs' markets contributes ONE row to each DM — never merged, never
   dropped, never double-counted **unless the org's own config grants the SAME market to two DMs**
   (an operator data-quality issue, not an attribution bug — flagged explicitly as `ambiguous_markets`
   rather than silently over- or under-counting).

Everything here is pure (no Supabase, no HTTP) so it is unit-testable without a database or a live
commcalc endpoint — see harness_dm_target_attribution.py. The router (router.py) is the only place
that talks to Postgres or makes the internal HTTP call to mod-commission's Daily Targets endpoints.
"""
from __future__ import annotations

from calendar import month_name
from datetime import date
from typing import Dict, List, Optional


def _norm(v) -> str:
    return str(v or "").strip()


def _fold(v) -> str:
    return _norm(v).lower()


_MONTH_BY_NAME = {name.lower(): i for i, name in enumerate(month_name) if name}


def parse_period_to_ym(period: str) -> str:
    """Accepts EITHER spelling the platform's period selector can emit — 'August 2026' or
    '2026-08' — and returns the canonical 'YYYY-MM'. Raises ValueError on anything else (the
    router turns that into a clean 400, never a 500). Pure string/int math — no JS-Date pitfall,
    this never touches a `Date` object."""
    p = _norm(period)
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
        y, m = int(p[:4]), int(p[5:7])
        if 1 <= m <= 12:
            return f"{y:04d}-{m:02d}"
        raise ValueError(f"bad month in period: {period!r}")
    parts = p.split()
    if len(parts) == 2 and parts[1].isdigit():
        mo = _MONTH_BY_NAME.get(parts[0].strip().lower())
        if mo:
            return f"{int(parts[1]):04d}-{mo:02d}"
    raise ValueError(f"unrecognized period: {period!r}")


def worked_pairs_from_shifts(shifts: List[dict]) -> List[dict]:
    """Distinct (employee_name, store_code) pairs that had at least one non-deleted, positive-hour
    shift — this IS "the stores they worked in" from the owner's rule. One row per pair (order
    stable: first-seen), each carrying the employee_id off the FIRST shift row seen for that pair
    (all rows for one real employee should agree; a mismatch just keeps whichever came first —
    display-only, never affects the money side)."""
    seen: Dict[tuple, dict] = {}
    for s in shifts or []:
        if s.get("is_deleted"):
            continue
        name = _norm(s.get("employee_name"))
        store = _norm(s.get("store_code")).upper()
        if not name or not store:
            continue
        try:
            hours = float(s.get("scheduled_hours") or 0)
        except (TypeError, ValueError):
            hours = 0.0
        if hours <= 0:
            continue
        key = (name.upper(), store)
        if key not in seen:
            seen[key] = {"employee_name": name, "store_code": store,
                        "employee_id": s.get("employee_id")}
    return list(seen.values())


def dm_roster_from_app_users(app_user_rows: List[dict], role_scope_by_name: Dict[str, str],
                             employee_name_by_id: Dict[str, str]) -> Dict[str, dict]:
    """Every app_user whose ROLE's reporting scope is 'market' (§ AGENT_CONTRACT / core.scope —
    "DM role's reporting grants = markets" is the shipped DM setup convention, ledger Q9/11) and who
    has at least one market granted. Returns {dm_key: {'label', 'markets': set(canonical-cased)}}.

    dm_key = the app_user row's own 'id' (stable, unique) — never the auth_id (nullable pre-invite)
    or email alone (an app_user can predate having one filled in consistently)."""
    out: Dict[str, dict] = {}
    for au in app_user_rows or []:
        key = au.get("id")
        if not key:
            continue
        role = _norm(au.get("role"))
        if _fold(role_scope_by_name.get(role, "")) != "market":
            continue
        markets = {_norm(m) for m in _norm(au.get("market")).split(",") if _norm(m)}
        if not markets:
            continue
        label = (_norm(au.get("full_name")) or employee_name_by_id.get(_norm(au.get("employee_id")))
                or _norm(au.get("email")) or str(key))
        out[str(key)] = {"label": label, "markets": markets, "role": role}
    return out


def attribute_rows_to_dms(rows: List[dict], dm_markets: Dict[str, dict]) -> dict:
    """THE core rollup (rule 2+3). `rows` — one dict per (employee, store) attribution row, each
    carrying at least 'market' (the store's market) and 'target' (the rep's schedule-derived
    accessory target at that store, dollars); anything else on the row (employee name, store_code,
    achieved $, …) rides along untouched into the output for the drill-down.

    `dm_markets` — {dm_key: {'label', 'markets': set(...)}} from dm_roster_from_app_users.

    Market matching is CASE-INSENSITIVE (store markets and DM grants are free-typed in two different
    admin screens — see RULE THREE — and must not silently diverge on casing alone).

    Returns:
      by_dm             {dm_key: {'label', 'markets': [...], 'rows': [...], 'total_target',
                                  'total_achieved'}}   — one entry per DM in `dm_markets`, even one
                         with zero matching rows (so a DM with nothing this period still SHOWS as a
                         real, present zero rather than being absent from the payload).
      unassigned        {'rows': [...], 'total_target'} — rows whose store market has NO DM grant
                         at all (never silently dropped from the payload; a store yet to be handed
                         to a DM shows up here instead of vanishing).
      ambiguous_markets {market: [dm_key, ...]} — a market granted to MORE than one DM. Those rows
                         are attributed to EVERY matching DM (so nothing silently disappears) and
                         are the one case where `total_target_all_rows` will NOT equal
                         Σ(by_dm totals) + unassigned total — flagged here so that's never mistaken
                         for a bug in this function; it is an operator config collision upstream.
      total_target_all_rows / total_achieved_all_rows — straight sums over `rows`, computed ONCE
                         off the input so a caller can always sanity-check the split against the
                         un-split source total.
    """
    market_to_dms: Dict[str, set] = {}
    for dm_key, meta in (dm_markets or {}).items():
        for m in meta.get("markets") or ():
            market_to_dms.setdefault(_fold(m), set()).add(dm_key)
    ambiguous = {m: sorted(dms) for m, dms in market_to_dms.items() if len(dms) > 1}

    by_dm: Dict[str, dict] = {
        dm_key: {"label": meta.get("label", dm_key), "markets": sorted(meta.get("markets") or []),
                 "rows": [], "total_target": 0.0, "total_achieved": 0.0}
        for dm_key, meta in (dm_markets or {}).items()
    }
    unassigned_rows: List[dict] = []

    for r in rows or []:
        mkt = _fold(r.get("market"))
        dms = market_to_dms.get(mkt) if mkt else None
        target = float(r.get("target") or 0)
        achieved = float(r.get("achieved") or 0)
        if not dms:
            unassigned_rows.append(r)
            continue
        for dm_key in dms:
            d = by_dm.setdefault(dm_key, {"label": dm_key, "markets": [], "rows": [],
                                          "total_target": 0.0, "total_achieved": 0.0})
            row_out = dict(r)
            row_out["routed_dm"] = dm_key
            row_out["routed_dm_label"] = by_dm[dm_key]["label"]
            row_out["ambiguous"] = mkt in ambiguous
            d["rows"].append(row_out)
            d["total_target"] += target
            d["total_achieved"] += achieved

    for d in by_dm.values():
        d["total_target"] = round(d["total_target"], 2)
        d["total_achieved"] = round(d["total_achieved"], 2)

    total_target_all_rows = round(sum(float(r.get("target") or 0) for r in (rows or [])), 2)
    total_achieved_all_rows = round(sum(float(r.get("achieved") or 0) for r in (rows or [])), 2)
    unassigned_total = round(sum(float(r.get("target") or 0) for r in unassigned_rows), 2)

    return {
        "by_dm": by_dm,
        "unassigned": {"rows": unassigned_rows, "total_target": unassigned_total},
        "ambiguous_markets": ambiguous,
        "total_target_all_rows": total_target_all_rows,
        "total_achieved_all_rows": total_achieved_all_rows,
    }


def cross_dm_employees(attributed: dict) -> List[dict]:
    """Convenience view for the "2-DM employee, verify at a glance" ask: every employee name that
    appears in MORE THAN ONE dm's row set, with the per-DM store(s)/target(s) that landed there.
    Pure post-processing over `attribute_rows_to_dms`'s own output — no new inputs."""
    by_emp: Dict[str, Dict[str, list]] = {}
    for dm_key, d in (attributed.get("by_dm") or {}).items():
        for row in d.get("rows") or []:
            emp = _norm(row.get("employee_name") or row.get("employee"))
            if not emp:
                continue
            by_emp.setdefault(emp, {}).setdefault(dm_key, []).append(row)
    out = []
    for emp, per_dm in sorted(by_emp.items()):
        if len(per_dm) < 2:
            continue
        out.append({
            "employee_name": emp,
            "dms": [
                {"dm_key": dm_key, "rows": rows,
                 "total_target": round(sum(float(r.get("target") or 0) for r in rows), 2)}
                for dm_key, rows in per_dm.items()
            ],
        })
    return out
