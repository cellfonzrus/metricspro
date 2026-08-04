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

BULK-FETCH PERFORMANCE PATH (owner directive 2026-08-04, "plan for a bigger tenant")
-------------------------------------------------------------------------------------
The FIRST cut of this package called mod-commission's per-(store,rep) `/calendar?scope=rep` endpoint
once per worked pair — O(employees × stores) internal HTTP round-trips. That endpoint has no bulk/
paged shape (it is inherently single-store, optionally single-rep), so there is no way to "page
through" it for a bulk pull. Instead, the router now makes exactly ONE internal HTTP call —
`GET /commcalc/targets/{period}/summary` (already the org's bulk/all-stores shape; already used
elsewhere in the platform, e.g. My Team) — which returns, per store, the store's MONTHLY accessory
target dollars AND each rep's own ACHIEVED accessory $ there (`reps_in_scope` + `scope_achieved_mtd`,
computed by mod-commission). The ONE piece that call does NOT give per rep is the monthly-target
SHARE (mod-commission only prorates that inside `/calendar?scope=rep`) — so `rep_share_from_shifts`
below LOCALLY reproduces that one small, pure, money-FREE ratio (rep's schedule hours ÷ the store's
schedule hours, both projected to month-end the same way) off `storeops.shifts`, which this module
already owns and reads for its own "worked pairs" list — no additional HTTP calls, no proration
formula change, same final `target = store_monthly × rep_share` computation `/calendar?scope=rep`
would return. `hours_by_day_for_scope` / `project_future_hours` deliberately MIRROR
`commcalc/targets_engine.py`'s same-named functions bit-for-bit (never imported — that file is
mod-commission's — but the algorithm is small, pure, and documented here so a drift is visible on
sight, not silent). If exact byte-parity with a live `/calendar?scope=rep` call ever matters for an
audit, that endpoint still exists and answers the same question for one pair at a time.

SPAN-SCOPING (Gate-1 rework 2026-08-04)
-----------------------------------------
A market-scope caller (a District Manager) must see ONLY the DM card(s) for markets granted to them —
never another DM's per-employee target slice or roster. `visible_dm_keys_for_markets` /
`visible_unassigned` / `visible_ambiguous_markets` / `redact_cross_dm_employees` implement that
narrowing PURELY (no DB) over `attribute_rows_to_dms`'s own output, so the router computes the FULL
org-wide attribution once (needed to even DETECT a cross-DM employee — you cannot know a rep also
works another DM's market without seeing that other row) and then redacts the response for a
market-scope caller at the presentation layer: full detail for their own market(s), a bare
identity label (never totals, never a roster) for any OTHER DM referenced only to explain a split.
"""
from __future__ import annotations

from calendar import month_name
from datetime import date, timedelta
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


def _parse_shift_date(v) -> Optional[date]:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def hours_by_day_for_scope(shifts: List[dict], store_code: str, rep_name: Optional[str]) -> Dict[date, float]:
    """Scheduled hours per day for a (store) or (store, rep) scope. MIRRORS
    `commcalc/targets_engine.py`'s `scope_hours_by_day` exactly (same filters: non-deleted, positive
    hours, exact case-insensitive store/rep match) — see the module docstring for why this is
    duplicated rather than imported. rep_name=None -> whole-store hours."""
    out: Dict[date, float] = {}
    sc = _norm(store_code).upper()
    rn = _norm(rep_name).upper()
    for s in shifts or []:
        if s.get("is_deleted"):
            continue
        if _norm(s.get("store_code")).upper() != sc:
            continue
        if rn and _norm(s.get("employee_name")).upper() != rn:
            continue
        d = _parse_shift_date(s.get("shift_date"))
        if not d:
            continue
        try:
            h = float(s.get("scheduled_hours") or 0)
        except (TypeError, ValueError):
            h = 0.0
        if h <= 0:
            continue
        out[d] = out.get(d, 0.0) + h
    return out


def project_future_hours(hours_by_day: Dict[date, float], today: date, month_end: date) -> Dict[date, float]:
    """Fill not-yet-scheduled future days from the scope's own weekly pattern. MIRRORS
    `commcalc/targets_engine.py`'s `project_future_hours` exactly (average hours per weekday seen so
    far, applied forward to every matching weekday with no concrete shift yet; never touches a
    concrete day; no shifts at all -> projects nothing)."""
    if not hours_by_day or not month_end:
        return {}
    by_wd: Dict[int, list] = {}
    for d, h in hours_by_day.items():
        if h > 0:
            by_wd.setdefault(d.weekday(), []).append(h)
    if not by_wd:
        return {}
    avg_by_wd = {wd: sum(v) / len(v) for wd, v in by_wd.items()}
    out: Dict[date, float] = {}
    d = today
    while d <= month_end:
        if d not in hours_by_day and d.weekday() in avg_by_wd:
            out[d] = avg_by_wd[d.weekday()]
        d += timedelta(days=1)
    return out


def rep_share_from_shifts(shifts: List[dict], store_code: str, rep_name: str, today: date, month_end: date) -> float:
    """The rep's share of a store's scheduled hours (today→month_end projected) — MIRRORS the exact
    proration ratio `GET /commcalc/targets/{period}/calendar?scope=rep` computes inline (router.py's
    own "Employee target PRORATION" block): projected rep-hours ÷ projected store-hours, 0.0 when the
    store has no hours at all this period (never divides by zero). This is the ONE piece of the
    per-rep target `GET /commcalc/targets/{period}/summary`'s bulk response doesn't already carry —
    everything else (the store's monthly target dollars, the rep's own achieved $) comes straight off
    that ONE bulk call; see the module docstring."""
    store_hours = hours_by_day_for_scope(shifts, store_code, None)
    rep_hours = hours_by_day_for_scope(shifts, store_code, rep_name)
    store_eff = {**store_hours, **project_future_hours(store_hours, today, month_end)}
    rep_eff = {**rep_hours, **project_future_hours(rep_hours, today, month_end)}
    sh, rh = sum(store_eff.values()), sum(rep_eff.values())
    return (rh / sh) if sh > 0 else 0.0


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
    Pure post-processing over `attribute_rows_to_dms`'s own output — no new inputs. Each `dms` entry
    carries `label` (not just `dm_key`) so `redact_cross_dm_employees` can show a bare identity for a
    DM being redacted, without the caller needing a separate by_dm lookup."""
    by_emp: Dict[str, Dict[str, list]] = {}
    labels: Dict[str, str] = {}
    for dm_key, d in (attributed.get("by_dm") or {}).items():
        labels[dm_key] = d.get("label", dm_key)
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
                {"dm_key": dm_key, "label": labels.get(dm_key, dm_key), "rows": rows,
                 "total_target": round(sum(float(r.get("target") or 0) for r in rows), 2)}
                for dm_key, rows in per_dm.items()
            ],
        })
    return out


# ── SPAN-SCOPING (Gate-1 rework 2026-08-04) — pure narrowing/redaction over attribute_rows_to_dms's
# own output, so a market-scope caller sees only their own market(s)' slice. ────────────────────────
def visible_dm_keys_for_markets(dm_markets: Dict[str, dict], caller_markets) -> set:
    """DM keys whose granted markets INTERSECT `caller_markets` (case-insensitive). This is deliberately
    market-based, not identity-based: in the (flagged) `ambiguous_markets` case where 2 DMs are BOTH
    granted the same market, a caller holding that market grant sees BOTH cards for it — their
    visibility is defined by the market they were granted, not by which internal dm_key happens to
    hold it. Empty `caller_markets` -> empty set (no visible DMs — a market-scope role with no market
    of its own has no reporting span here, same "empty span" convention every other storeops read
    uses under RBAC)."""
    if not caller_markets:
        return set()
    folded = {_fold(m) for m in caller_markets}
    out = set()
    for dm_key, meta in (dm_markets or {}).items():
        if any(_fold(m) in folded for m in (meta.get("markets") or ())):
            out.add(dm_key)
    return out


def visible_unassigned(unassigned: dict, caller_markets) -> dict:
    """`unassigned` narrowed to rows whose market is one of `caller_markets` — a market-scope DM may
    see "my market has a store nobody's been granted" (useful config guidance) but not another
    market's unassigned rows."""
    if not caller_markets:
        return {"rows": [], "total_target": 0.0}
    folded = {_fold(m) for m in caller_markets}
    rows = [r for r in (unassigned or {}).get("rows") or [] if _fold(r.get("market")) in folded]
    return {"rows": rows, "total_target": round(sum(float(r.get("target") or 0) for r in rows), 2)}


def visible_ambiguous_markets(ambiguous: Dict[str, list], caller_markets) -> Dict[str, list]:
    """`ambiguous_markets` narrowed to markets the caller themselves is granted — they may see that
    THEIR market has a DM-grant collision, not that some other market they can't see does too."""
    if not caller_markets:
        return {}
    folded = {_fold(m) for m in caller_markets}
    return {m: dms for m, dms in (ambiguous or {}).items() if _fold(m) in folded}


def redact_cross_dm_employees(cross_dm: List[dict], visible_keys: set) -> List[dict]:
    """For a market-scope caller: keep FULL detail (rows + total_target) only for the dm entries the
    caller may see; any OTHER dm referenced on the same split row is reduced to a bare identity stub
    (`dm_key` + `label` + `redacted: True`, no rows, no total_target) — enough to explain "this
    employee also works elsewhere" (the split is real and shouldn't look silently wrong) without
    exposing that other DM's totals or roster. An employee with NO row under a visible dm at all is
    dropped entirely (nothing of theirs to show this caller)."""
    out = []
    for e in cross_dm or []:
        dms = e.get("dms") or []
        if not any(d.get("dm_key") in visible_keys for d in dms):
            continue
        redacted = [
            d if d.get("dm_key") in visible_keys
            else {"dm_key": d.get("dm_key"), "label": d.get("label", d.get("dm_key")), "redacted": True}
            for d in dms
        ]
        out.append({**e, "dms": redacted})
    return out
