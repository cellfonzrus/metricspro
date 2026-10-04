"""DM VISIT PLAN — the daily visit quota, who assigns the stores, and what gets picked when nobody did.

OWNER ASK 2026-10-03, verbatim (OWNER-QUOTE-BEGIN — the owner's own words, kept for
provenance; the carrier and tenant names in it are theirs and appear nowhere in this module's
behaviour, which is what harness_dm_visit_plan.py §I checks):
    "each Dm is required to do 2 store visists every day , confuhgurable by each tenant , set uo 2
     for now editable in the system, the Market manager to monitor the dm performance and assign
     them the stores, if there are no stores assigned by friday evening then assign stores to the dm
     based on where the performance is low in an order of priority specailly where the activations
     and accessories sales are low , then kpi , all these should be confgurable by the tenant ,
     nothing harcoded, create these for now and give options to assign other deliverables as a drop
     down menu to add to the priorty list, it could be sales items like edge in luxelink or xfinity
     in boost , the drop down will be carrier spefici"
(OWNER-QUOTE-END)

WHAT THIS MODULE ADDS, AND WHAT IT REFUSES TO RE-DERIVE. Four facts this needs already have exactly
one home each, and every one of them is DEREFERENCED here rather than copied:

  | the fact                                  | its one home                                        |
  |-------------------------------------------|-----------------------------------------------------|
  | which stores a DM owns                    | `storeops/org_chain.dm_by_store` (index §48.7)      |
  | a store's activations / accessories / …   | `commcalc/targets_engine` via the Daily Targets     |
  |   achieved vs monthly                     |   summary (index §5) — never recounted from sales   |
  | attainment % of a target                  | `targets_engine.attainment_pct` — ONE formula       |
  | which extra deliverables a carrier has    | `commcalc.carrier_kpi_metric` (mig 060), the        |
  |   (the owner named one per tenant)        |   per-carrier KPI registry that already exists    |

So the carrier-specific dropdown is NOT a new list of sales items. It is the existing per-carrier KPI
metric registry, read for the org's own carrier. A tenant that wants a deliverable the registry does
not carry yet adds it on the KPI-metrics screen it already has, and it appears in this dropdown. A
second vocabulary of "things a DM should push" is the duplicate defect the index rules forbid.

NOTHING HARDCODED — the owner said it twice, so every number here is a config row with a house
default in `HOUSE_CONFIG` / `HOUSE_PRIORITY_RULES`:
  • how many visits a day      dm_visit_quota_per_day        default 2   (the owner's "set up 2 for now")
  • which days the quota runs  dm_visit_quota_days           default Mon–Fri
  • the assignment deadline    dm_visit_assign_deadline_dow  default 5 = Friday
  •                            dm_visit_assign_deadline_time default '17:00' (the owner's "evening")
  • how far ahead to fill      dm_visit_assign_horizon_days  default 7
  • the auto-fill switch       dm_visit_assign_auto_enabled  default TRUE (the owner asked for the fill)
  • the PRIORITY ORDER itself  storeops.dm_visit_priority_rule rows, seeded activations → accessories
                               → KPI, each with its own weight and direction

THE HONESTY RULE (§15z). A store whose metric has no target and no measured value has attainment
`None`. It is NOT scored as a perfect store (which would hide it) and NOT scored as a total failure
(which would send a DM to a store nobody set a target for). It contributes nothing to the score, is
named in `unmeasured`, and every payload carries that list. A plan that silently answered for the
stores it could measure would read as "these are the worst stores" when it means "these are the worst
of the stores someone set a target for".

RULE TWO: no carrier, tenant, store, product or person name appears in this file. The two product
examples the owner gave are deliberately absent from everything but their quoted words above — they
are rows in a tenant's own registry, not names this module knows.

PURE: stdlib only. No DB, no framework, no network, no clock of its own — the caller passes `today`.
Proof: `backend/harness_dm_visit_plan.py`. Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §49.
"""
from datetime import date, timedelta

ALERT_SCOPE = "dm_visit_plan"

# Where a priority rule's number comes from. Both are DEREFERENCES, not sources of their own:
#   'target_category' → one of targets_engine.CATEGORIES on the Daily Targets store row
#   'kpi_metric'      → one metric_key of commcalc.carrier_kpi_metric, valued from commcalc.kpi_actual
#   'kpi_all'         → the mean attainment across every ACTIVE metric of that same registry, which is
#                       the owner's "then kpi" without this module choosing which KPIs count
BASES = ("target_category", "kpi_metric", "kpi_all")

# A rule ranks a store by whichever end of its metric the tenant cares about. 'low_first' is what the
# owner asked for (send the DM where performance is low); 'high_first' exists so a tenant can also
# prioritise, say, the stores with the most traffic without this module growing a second mechanism.
DIRECTIONS = ("low_first", "high_first")

ASSIGN_SOURCES = ("manual", "auto")
ASSIGN_STATUSES = ("open", "visited", "skipped")

# The sentinel for a store whose metric nobody has set a target or a value for. A VALUE, not a
# dropped row — see THE HONESTY RULE above.
UNMEASURED = "__unmeasured__"

HOUSE_CONFIG = {
    "quota_enabled": True,
    "quota_per_day": 2,                 # the owner's "2 store visits every day", editable per tenant
    "quota_days": (1, 2, 3, 4, 5),      # ISO weekday: 1 = Monday … 7 = Sunday
    "auto_enabled": True,
    "deadline_dow": 5,                  # Friday
    "deadline_time": "17:00",           # "friday evening", tenant-local, the mig-433 HH:MM convention
    "horizon_days": 7,                  # fill the week that follows the deadline
}

# The seeded priority order — the owner's words turned into rows, not into code. Migration 1050 seeds
# exactly these for the house org and the harness parses them OUT of the migration, so this tuple and
# that seed cannot drift. `weight` is what makes "activations and accessories FIRST, then KPI" an
# order rather than a blend; every field is editable per tenant.
HOUSE_PRIORITY_RULES = (
    {"sort": 10, "basis": "target_category", "metric_key": "activations",
     "weight": 3.0, "direction": "low_first", "label": "Activations below target"},
    {"sort": 20, "basis": "target_category", "metric_key": "accessories",
     "weight": 2.0, "direction": "low_first", "label": "Accessory sales below target"},
    {"sort": 30, "basis": "kpi_all", "metric_key": "",
     "weight": 1.0, "direction": "low_first", "label": "KPI attainment"},
)


# ── tiny coercions (a config row is typed by a person; a bad value falls back, never crashes) ──────
def _f(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=None):
    try:
        if v is None or v == "":
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _s(v):
    return str(v or "").strip()


def normalize_time(value, default="17:00"):
    """'HH:MM' or the default. Same shape as commcalc/manager_digest.normalize_alert_time, kept local
    only because this module is import-free by design; the FORMAT is the one convention."""
    raw = _s(value)
    if not raw:
        return default
    parts = raw.split(":")
    h = _i(parts[0])
    m = _i(parts[1]) if len(parts) > 1 else 0
    if h is None or m is None or not (0 <= h <= 23) or not (0 <= m <= 59):
        return default
    return f"{h:02d}:{m:02d}"


def normalize_days(value, default=HOUSE_CONFIG["quota_days"]):
    """A tuple of ISO weekdays (1–7), de-duplicated and ordered. An empty or wholly invalid value
    falls back to the default rather than to "no day has a quota" — a quota nobody is measured on is
    indistinguishable from the feature being off, and the tenant switch for that is `quota_enabled`."""
    raw = value
    if isinstance(raw, str):
        raw = [p for p in raw.replace("[", "").replace("]", "").split(",")]
    if not isinstance(raw, (list, tuple, set)):
        return tuple(default)
    out = sorted({d for d in (_i(x) for x in raw) if d is not None and 1 <= d <= 7})
    return tuple(out) if out else tuple(default)


def resolve_config(tenant_row=None):
    """The tenant's visit-plan config over the house defaults, every field validated. PURE."""
    row = tenant_row if isinstance(tenant_row, dict) else {}
    out = dict(HOUSE_CONFIG)
    out["quota_enabled"] = bool(row.get("dm_visit_quota_enabled", HOUSE_CONFIG["quota_enabled"]))
    out["auto_enabled"] = bool(row.get("dm_visit_assign_auto_enabled", HOUSE_CONFIG["auto_enabled"]))
    q = _i(row.get("dm_visit_quota_per_day"))
    # 0 is a legal, meaningful quota ("this tenant does not require a daily visit") and must survive;
    # a negative or unparseable one is not, and falls back to the house 2.
    out["quota_per_day"] = HOUSE_CONFIG["quota_per_day"] if q is None or q < 0 else min(q, 50)
    out["quota_days"] = normalize_days(row.get("dm_visit_quota_days"))
    d = _i(row.get("dm_visit_assign_deadline_dow"))
    out["deadline_dow"] = HOUSE_CONFIG["deadline_dow"] if d is None or not (1 <= d <= 7) else d
    out["deadline_time"] = normalize_time(row.get("dm_visit_assign_deadline_time"),
                                          HOUSE_CONFIG["deadline_time"])
    h = _i(row.get("dm_visit_assign_horizon_days"))
    out["horizon_days"] = HOUSE_CONFIG["horizon_days"] if h is None or h < 1 else min(h, 31)
    return out


# ── PRIORITY RULES ────────────────────────────────────────────────────────────────────────────────
def normalize_rules(rows=None):
    """Validated, ordered priority rules. `rows` = storeops.dm_visit_priority_rule rows for the org
    (already narrowed to the org's carrier + the carrier-neutral default set by the caller). No rows
    at all → HOUSE_PRIORITY_RULES, so a tenant that has not configured an order still gets the
    owner's one. A row with an unknown basis or direction is DROPPED and named in the returned
    `rejected` list — never silently coerced into scoring on something else. PURE.

    Returns (rules, rejected)."""
    raw = [r for r in (rows or []) if isinstance(r, dict)]
    if not raw:
        return ([dict(r) for r in HOUSE_PRIORITY_RULES], [])
    rules, rejected = [], []
    for r in raw:
        if not bool(r.get("is_active", True)):
            continue
        basis = _s(r.get("basis")).lower()
        key = _s(r.get("metric_key"))
        if basis not in BASES:
            rejected.append({"basis": _s(r.get("basis")), "metric_key": key, "reason": "unknown basis"})
            continue
        if basis != "kpi_all" and not key:
            rejected.append({"basis": basis, "metric_key": key, "reason": "metric_key required"})
            continue
        direction = _s(r.get("direction")).lower() or "low_first"
        if direction not in DIRECTIONS:
            rejected.append({"basis": basis, "metric_key": key, "reason": "unknown direction"})
            continue
        w = _f(r.get("weight"), 1.0)
        if w is None or w <= 0:
            w = 1.0
        rules.append({"id": r.get("id"), "sort": _i(r.get("sort"), 0) or 0, "basis": basis,
                      "metric_key": ("" if basis == "kpi_all" else key), "weight": float(w),
                      "direction": direction,
                      "label": _s(r.get("label")) or (key or basis)})
    if not rules:
        return ([dict(r) for r in HOUSE_PRIORITY_RULES], rejected)
    rules.sort(key=lambda r: (r["sort"], r["basis"], r["metric_key"]))
    return (rules, rejected)


def rule_key(rule):
    """The identity of a rule as a payload/dedup string. PURE."""
    return f"{rule.get('basis')}:{rule.get('metric_key') or '*'}"


# ── SCORING ───────────────────────────────────────────────────────────────────────────────────────
def metric_attainment(store_metrics, rule):
    """The attainment FRACTION (1.0 = on target) this rule reads for one store, or None when nothing
    measured it. `store_metrics` is the shape `build_store_metrics` produces:

        {"categories": {cat: {"attainment": float|None, "achieved": float, "monthly": float}},
         "kpi":        {metric_key: {"attainment": float|None, "value": …, "target": …}}}

    This function never computes an attainment of its own from an achieved value against a target — the caller got those
    fractions from `targets_engine.attainment_pct`, the one formula. PURE."""
    sm = store_metrics if isinstance(store_metrics, dict) else {}
    basis, key = rule.get("basis"), rule.get("metric_key")
    if basis == "target_category":
        return (sm.get("categories") or {}).get(key, {}).get("attainment")
    if basis == "kpi_metric":
        return (sm.get("kpi") or {}).get(key, {}).get("attainment")
    if basis == "kpi_all":
        vals = [m.get("attainment") for m in (sm.get("kpi") or {}).values()
                if m.get("attainment") is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)
    return None


def _deficit(attainment, direction):
    """How badly this store wants a visit on this rule, in [0, 1]. `low_first`: 1.0 at zero
    attainment, 0.0 at or above target (being 300% of target is not more "on target" than 100%, so
    the deficit floors at 0 and a star store can never earn negative priority that outvotes a rule
    beneath it). `high_first` is the mirror, capped the same way. PURE."""
    if attainment is None:
        return None
    a = max(0.0, float(attainment))
    if direction == "high_first":
        return min(1.0, a)
    return max(0.0, 1.0 - min(1.0, a))


def score_store(store_metrics, rules):
    """(score, detail) for one store. `score` = Σ weight × deficit over the rules that could be
    measured. `detail` names every rule with its attainment, deficit and contribution, and lists the
    rules that could NOT be measured — which is how a caller can say "this store ranks 1st on two of
    three rules and nobody set its third target". PURE."""
    total, parts, unmeasured = 0.0, [], []
    for rule in rules or ():
        att = metric_attainment(store_metrics, rule)
        dfc = _deficit(att, rule.get("direction") or "low_first")
        if dfc is None:
            unmeasured.append(rule_key(rule))
            parts.append({"rule": rule_key(rule), "label": rule.get("label"),
                          "attainment": None, "deficit": None, "contribution": 0.0,
                          "weight": rule.get("weight"), "measured": False})
            continue
        contrib = float(rule.get("weight") or 1.0) * dfc
        total += contrib
        parts.append({"rule": rule_key(rule), "label": rule.get("label"),
                      "attainment": round(float(att), 4), "deficit": round(dfc, 4),
                      "contribution": round(contrib, 4), "weight": rule.get("weight"),
                      "measured": True})
    return (round(total, 6), {"parts": parts, "unmeasured": unmeasured,
                              "measured_rules": len(parts) - len(unmeasured)})


def rank_stores(metrics_by_code, rules):
    """Every store ordered worst-performing FIRST. Ties break on the rules in their own configured
    order (so rule 2 genuinely decides when rule 1 agrees — that is what "an order of priority"
    means), then on store_code so the same inputs always produce the same plan. A store nothing could
    be measured on sorts LAST and carries `measured_rules: 0`; it is present, never dropped. PURE.

    Returns [{store_code, score, detail}, …]."""
    out = []
    for code, sm in (metrics_by_code or {}).items():
        score, detail = score_store(sm, rules)
        tiebreak = tuple(-(p["contribution"] if p["measured"] else -1.0) for p in detail["parts"])
        out.append({"store_code": code, "score": score, "detail": detail,
                    "_tb": tiebreak, "_unmeasured_all": detail["measured_rules"] == 0})
    out.sort(key=lambda r: (r["_unmeasured_all"], -r["score"], r["_tb"], str(r["store_code"])))
    for r in out:
        r.pop("_tb", None)
        r.pop("_unmeasured_all", None)
    for i, r in enumerate(out, start=1):
        r["rank"] = i
    return out


def reason_text(detail, limit=2):
    """One short line saying WHY a store was picked, naming the top contributing rules. The DM reads
    this on the assignment, so it is the only place a label is rendered. PURE."""
    parts = [p for p in (detail or {}).get("parts", []) if p.get("measured") and p.get("contribution")]
    parts.sort(key=lambda p: -float(p.get("contribution") or 0))
    if not parts:
        un = (detail or {}).get("unmeasured") or []
        return ("No target or measured value on any priority rule"
                + (f" ({len(un)} unmeasured)" if un else ""))
    bits = []
    for p in parts[:max(1, int(limit or 1))]:
        att = p.get("attainment")
        bits.append(f"{p.get('label')} at {round(100.0 * float(att), 0):.0f}% of target"
                    if att is not None else f"{p.get('label')}")
    return "; ".join(bits)


# ── QUOTA ─────────────────────────────────────────────────────────────────────────────────────────
def quota_dates(start, days, config):
    """The dates in [start, start+days) that carry a quota under this tenant's `quota_days`. PURE."""
    out = []
    for i in range(max(0, int(days or 0))):
        d = start + timedelta(days=i)
        if d.isoweekday() in (config or {}).get("quota_days", HOUSE_CONFIG["quota_days"]):
            out.append(d)
    return out


def quota_status(assignments, visits, config, dms, dates):
    """THE MARKET MANAGER'S MONITOR — per (DM × date): required, assigned, completed, shortfall.

    `assignments` = [{dm_key, visit_date, store_code, source, status}]
    `visits`      = [{dm_key, visit_date, store_code}] — SUBMITTED visits, the real work done
    `dms`         = [{key, name, email}]

    A DM with nothing assigned and nothing done still appears, as a real present zero — a DM missing
    from the board is the one nobody chased. `completed` counts DISTINCT stores visited on the day, so
    the same store logged twice is one visit against the quota. A visit to a store that was NOT
    assigned still counts toward the quota and is named in `unassigned_visits`: the DM did the work,
    and quietly scoring it zero because the paperwork went the other way would be the board lying.
    PURE."""
    cfg = config or {}
    required = int(cfg.get("quota_per_day", HOUSE_CONFIG["quota_per_day"])) if cfg.get("quota_enabled", True) else 0
    day_set = {d.isoformat() if hasattr(d, "isoformat") else _s(d) for d in (dates or ())}
    a_by = {}
    for a in assignments or ():
        k, d = _s(a.get("dm_key")), _s(a.get("visit_date"))[:10]
        if d in day_set:
            a_by.setdefault((k, d), set()).add(_s(a.get("store_code")).upper())
    v_by = {}
    for v in visits or ():
        k, d = _s(v.get("dm_key")), _s(v.get("visit_date"))[:10]
        if d in day_set:
            v_by.setdefault((k, d), set()).add(_s(v.get("store_code")).upper())
    rows, totals = [], {"required": 0, "assigned": 0, "completed": 0, "shortfall": 0}
    for dm in dms or ():
        k = _s(dm.get("key"))
        for d in sorted(day_set):
            assigned = a_by.get((k, d), set())
            done = v_by.get((k, d), set())
            row = {"dm_key": k, "dm_name": dm.get("name"), "dm_email": dm.get("email"),
                   "visit_date": d, "required": required,
                   "assigned": len(assigned), "completed": len(done),
                   "shortfall": max(0, required - len(done)),
                   "unassigned_visits": sorted(done - assigned),
                   "assigned_not_visited": sorted(assigned - done)}
            rows.append(row)
            for f in ("required", "assigned", "completed", "shortfall"):
                totals[f] += row[f]
    rows.sort(key=lambda r: (r["visit_date"], str(r["dm_name"] or r["dm_key"])))
    return {"rows": rows, "totals": totals, "required_per_day": required,
            "quota_enabled": bool(cfg.get("quota_enabled", True))}


# ── THE FRIDAY-EVENING AUTO-FILL ──────────────────────────────────────────────────────────────────
def deadline_reached(now_local, config):
    """True when the tenant's local clock is at or past this week's assignment deadline. `now_local`
    is a datetime in the TENANT's timezone — this module never reads a clock. PURE."""
    cfg = config or {}
    dow = int(cfg.get("deadline_dow", HOUSE_CONFIG["deadline_dow"]))
    hh, mm = (normalize_time(cfg.get("deadline_time"), HOUSE_CONFIG["deadline_time"]).split(":"))
    if now_local.isoweekday() < dow:
        return False
    if now_local.isoweekday() > dow:
        return True
    return (now_local.hour, now_local.minute) >= (int(hh), int(mm))


def plan_auto_assignments(*, dm_spans, ranked, config, existing, dates, carry_reason=True):
    """WHAT TO ASSIGN when the market manager assigned nothing. One plan, computed once, per DM.

    `dm_spans` = {dm_key: {"name", "email", "store_codes": set(UPPER)}} — the DM's own stores, from
                 `org_chain.dm_by_store` inverted. A store outside a DM's span is NEVER assigned to
                 them, whatever its priority: sending a DM to a store they do not own is not a plan.
    `ranked`   = `rank_stores` output, worst first.
    `existing` = [{dm_key, visit_date, store_code}] — anything already on the calendar, MANUAL or
                 AUTO. The fill never replaces, never duplicates, and is therefore idempotent: run it
                 twice and the second run plans nothing.
    `dates`    = the dates to fill (from `quota_dates`).

    THE RULE THE OWNER ASKED FOR: a DM whose week the market manager already set is LEFT ALONE. The
    fill is per (DM × date) and only tops a day up to the quota — so a manager who assigned one of
    two gets the second filled rather than their choice overwritten.

    Also: a store is assigned to a DM at most ONCE across the whole filled window, so a week does not
    become two visits to the same worst store while the second-worst is never seen. When a DM's span
    has fewer eligible stores than their week needs, the remaining slots come back in `short` with the
    reason — never silently left as a met quota.

    Returns {"assignments": [...], "short": [...], "skipped_days": [...]}. PURE — writes nothing."""
    cfg = config or {}
    quota = int(cfg.get("quota_per_day", HOUSE_CONFIG["quota_per_day"]))
    if not cfg.get("quota_enabled", True) or quota <= 0:
        return {"assignments": [], "short": [], "skipped_days": [],
                "note": "quota disabled or zero for this tenant"}
    day_list = [d.isoformat() if hasattr(d, "isoformat") else _s(d) for d in (dates or ())]
    have = {}
    for e in existing or ():
        have.setdefault((_s(e.get("dm_key")), _s(e.get("visit_date"))[:10]), set()).add(
            _s(e.get("store_code")).upper())
    out, short, skipped = [], [], []
    for dm_key, span in sorted((dm_spans or {}).items()):
        codes = {_s(c).upper() for c in (span.get("store_codes") or ()) if _s(c)}
        if not codes:
            short.append({"dm_key": dm_key, "dm_name": span.get("name"), "slots": 0,
                          "reason": "no store is in this DM's span — the org tree assigns them none"})
            continue
        # The DM's own stores in the org's priority order.
        queue = [r for r in (ranked or ()) if _s(r.get("store_code")).upper() in codes]
        used = {c for d in day_list for c in have.get((dm_key, d), set())}
        qi = 0
        for d in day_list:
            already = have.get((dm_key, d), set())
            if len(already) >= quota:
                skipped.append({"dm_key": dm_key, "visit_date": d, "already": len(already),
                                "reason": "already at quota — a manager's own assignment is never replaced"})
                continue
            need = quota - len(already)
            picked = 0
            while picked < need and qi < len(queue):
                cand = queue[qi]
                qi += 1
                code = _s(cand.get("store_code")).upper()
                if code in used:
                    continue
                used.add(code)
                row = {"dm_key": dm_key, "dm_name": span.get("name"), "dm_email": span.get("email"),
                       "visit_date": d, "store_code": cand.get("store_code"),
                       "source": "auto", "priority_rank": cand.get("rank"),
                       "score": cand.get("score")}
                if carry_reason:
                    row["priority_reason"] = reason_text(cand.get("detail"))
                    row["priority_detail"] = cand.get("detail")
                out.append(row)
                picked += 1
            if picked < need:
                short.append({"dm_key": dm_key, "dm_name": span.get("name"),
                              "visit_date": d, "slots": need - picked,
                              "reason": "this DM's span has fewer un-assigned stores than the week's quota"})
    return {"assignments": out, "short": short, "skipped_days": skipped}


def invert_dm_by_store(dm_by_store, employees_by_id=None):
    """{dm_key: {"name", "email", "store_codes": set}} from `org_chain.dm_by_store`'s output — the ONE
    walk, read the other way round. `dm_key` is the DM's employee_id, the same identity every other
    manager surface keys on. A store whose org tree yields no DM is returned separately in
    `unowned`: it has nobody to assign it to, and that is the finding, not a store to drop. PURE.

    Returns (spans, unowned)."""
    spans, unowned = {}, []
    for code, chain in (dm_by_store or {}).items():
        dms = (chain or {}).get("dm") or []
        if not dms:
            unowned.append(code)
            continue
        for dm in dms:
            key = _s(dm.get("employee_id")) or _s(dm.get("email"))
            if not key:
                continue
            s = spans.setdefault(key, {"name": dm.get("name"), "email": dm.get("email"),
                                       "store_codes": set()})
            s["store_codes"].add(_s(code).upper())
            if not s.get("name"):
                s["name"] = dm.get("name")
            if not s.get("email"):
                s["email"] = dm.get("email")
    return (spans, sorted(unowned))
