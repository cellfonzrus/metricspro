"""ZERO SALES — store-days and rep-days with no activation and no upgrade. READ-ONLY, BOOKS NOTHING.

OWNER REQUEST (2026-09-22, verbatim): *"need a zero sales report in management overview dashboard
capturing no activations or upgrades using standard filters and date range and notification
options"*. Grain and trigger answered by the owner the same day: **both store and rep** (a store row
with its rep rows underneath), and **alert after N consecutive zero days**, N config with a house
default of 2.

THE CORRECTNESS PROBLEM THIS REPORT EXISTS TO NOT HAVE
──────────────────────────────────────────────────────
A store whose feed did not land is NOT a store with zero sales. Printing it as zero sends a manager
to chase a rep who sold fine, and the report is never trusted again. So a day is one of FIVE states
and never two, and the two that mean "no number" are said in words:

  'had_sales'      the counted classes (below) have at least one distinct transaction. Not a finding.
  'measured_zero'  the feed DID carry this store on this day — rows landed — and none of them is an
                   activation or an upgrade. A real zero, stated as measured. THIS is the finding.
  'not_reported'   nothing landed for this store on this day. The count is None, NEVER 0. A missing
                   upload is reported, never absorbed into the number.
  'closed'         the store was not trading that day (see TRADING DAYS). Excluded, not a zero.
  'in_progress'    the day is not over yet. Excluded, not a zero.

and one org-level state that suspends the whole report:

  'rule_refused'   the org's activation-type rule is REFUSED as too broad (#271 —
                   `line_class.rules_refused` / `refusal_sentence`). If "activation" names 95% of the
                   lines, a zero this report prints is not trustworthy in either direction, so it
                   claims nothing and says why.

The vocabulary is `carrier_vs_pay`'s verbatim (reported / measured_zero / not_reported) and the
shape is `exec_metric_defs.bucket_coverage`'s ({'scanned', …, 'note'}), so every silent-zero banner
on the platform reads alike. This module adds NO third notion of "no data".

THE ACTIVATION PREDICATE IS READ, NEVER RESTATED
────────────────────────────────────────────────
There is exactly ONE predicate for "is this line an activation / an upgrade" — `line_class`
(`activation_class` → `bucket_of`), resolved per org by `router._line_rules_resolve` over
`accessory_config.activation_details_rules` (mig 313) + `contract_type_map` (mig 213) +
`activation_rules` (mig 224), and applied by the ONE aggregation `router._sales_cell_agg`. This
module never classifies a line. It is handed `_sales_cell_agg`'s cells — the same objects the Sales
Report, Executive MTD and Daily Targets count — and only asks WHICH BUCKETS count as a sale here.
A second copy of "what counts as an activation" inside a zero-sales report would drift from the
commission figures within a month; `harness_zero_sales_lock.py` fails the build if one appears.

WHICH BUCKETS COUNT (RULE TWO — config, never code)
  `count_classes`, house default ALL THREE activation-type buckets `premium` (= new activation +
  port), `upgrade` and `byod`. A BYOD line IS an activation; a store that sold five of them and
  nothing else is not a zero-sales store. An org may narrow the list; a value that is not one of
  `line_class.BUCKETS` is REFUSED at resolve time rather than silently counting nothing — a typo in
  config that made every store read zero would be this report's own silent-zero defect.

TRADING DAYS — DEREFERENCED, NOT INVENTED
─────────────────────────────────────────
A store shut on Sundays would otherwise read zero every week and alert forever. THE PLATFORM HAS NO
STORE TRADING-CALENDAR TABLE — `storeops.stores` carries no hours/open-days column (checked
2026-09-22: mig 003 + every later ALTER). What it DOES have is the SCHEDULE, and the Daily Targets
engine already treats "scheduled hours > 0 on that day" as the store being open
(`targets_engine.scope_hours_by_day` → `compute_scope`'s `open_days`). This module dereferences that
same fact — it does not build a second calendar:

  `trading_day_source='schedule'` (house default) — a day with scheduled hours is a trading day.
  `trading_day_source='all_days'` — every day in the window trades.
  `excluded_weekdays` — an explicit per-org list (0=Mon … 6=Sun), applied under either source, for a
  tenant that does not schedule in the platform.

HONESTY FALLBACK: a store with NO scheduled hours ANYWHERE in the window has an UNKNOWN calendar,
not a closed one. Calling it closed would silence the report for exactly the stores whose data is
thinnest. Such a store falls back to all-days and its row carries `trading_calendar='unknown'`,
which the page states. The same rule applies to a rep: no shift anywhere in the window means their
working days are unknown, so they are evaluated rather than excluded.

CONSECUTIVE DAYS, AND WHAT AN UNKNOWN DAY DOES TO A RUN
───────────────────────────────────────────────────────
The alert asserts "N consecutive days with no activation". Days are walked in order; `closed`,
`in_progress` and (at rep grain) `off` days are SKIPPED — they neither count nor break, because a
store that does not trade on Sunday has a Saturday and a Monday that are consecutive TRADING days.
A `had_sales` day resets the run. A `measured_zero` day extends it. A `not_reported` day is the one
that needs a decision, and it is config:

  `gap_policy='break'`  (HOUSE DEFAULT) — the run ends at the gap. We cannot assert that two zero
                        days were consecutive across a day nobody measured. The gap is NOT silent:
                        the row carries `gap_days` and the note names them, so the manager reads
                        "2 zero days, run ended by 1 not-reported day (2026-09-15)" rather than
                        either a false alert or nothing at all.
  `gap_policy='bridge'` — the run continues across the gap. The unknown day adds NOTHING to the
                        count (it is not a zero day), and any alert built from a bridged run must
                        name the gap — `alert_items` puts the gap days in the item and the digest
                        prints them.

DELIBERATELY NOT OFFERED: "treat a not-reported day as a zero day". That is the silent zero this
whole report exists to prevent, and making it a config row would let a tenant switch the defect back
on. It is refused at resolve time, by name, with the reason.

NOTIFICATIONS ARE A NEW KIND ON THE EXISTING PATH
─────────────────────────────────────────────────
No second alerting mechanism, no second dedup rule, no second recipient resolution. The fan-out is
`manager_digest.plan_digests` (factored out of `epay_alerts` in this same change so both dereference
one home): DM ∪ every manager above the DM, one digest per manager, managers with no email skipped,
a `manager_digest.ref_key` per (recipient, store, date, kind) written to `storeops.alert_log` under
scope 'zero_sales' so a finding escalates ONCE per day.

GRAIN IS A SCOPE KEY, NEVER A BRANCH
  `grains` is config (house default both). The engine loops over the requested grains and emits rows
  keyed `(grain, scope_key)`; nothing in this file branches on which grain it is computing — except
  the ONE rule that makes the absence rule work at rep grain:

  A REP INHERITS THE STORE'S NOT-REPORTED STATE. If the store's feed did not land, every rep under
  it is 'not_reported' — never 'measured_zero', never zero. A missing feed must surface as ONE store
  problem, not as every rep appearing to have sold nothing. The rep row does not re-derive absence;
  it reads the store day's state (`rep_day_state`).

Everything here is PURE (rows + config in, rows out): no DB, no framework, no network, stdlib only.
Proof `backend/harness_zero_sales.py` (including the negative control that a missing feed reports
"not reported" and never a zero); lock `backend/harness_zero_sales_lock.py`.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §15 + §16–18.
"""
from datetime import date, timedelta

from app.modules.commcalc import line_class as _lc
from app.modules.commcalc import manager_digest as _md

# ── THE STATES. Three for a measured/unmeasured day, two for a day that is not a day. ─────────────
HAD_SALES = "had_sales"
MEASURED_ZERO = "measured_zero"
NOT_REPORTED = "not_reported"
CLOSED = "closed"
IN_PROGRESS = "in_progress"
OFF = "off"                    # rep grain only: nobody scheduled this person that day
RULE_REFUSED = "rule_refused"  # org-level: #271 refused the activation rule — no zero is claimable

#: Days that are a finding. Exactly one state qualifies, and it is the MEASURED one.
FINDING_STATES = frozenset({MEASURED_ZERO})
#: Days that carry a number. Every other state's count is None — never 0.
COUNTED_STATES = frozenset({HAD_SALES, MEASURED_ZERO})
#: Days skipped when walking a run: they neither count nor break it.
SKIPPED_STATES = frozenset({CLOSED, IN_PROGRESS, OFF})

STATE_NOTES = {
    HAD_SALES: (
        "The feed carried this store on this day and it sold at least one of the counted activation "
        "types. Not a finding."),
    MEASURED_ZERO: (
        "The feed DID carry this store on this day — rows landed — and not one of them is an "
        "activation or an upgrade. This is a MEASURED zero: the day was looked at, and it was "
        "empty of sales. It is the only state this report treats as a finding."),
    NOT_REPORTED: (
        "Nothing landed for this store on this day, so nothing is known about it. It is NEVER shown "
        "as 0: a store that sold nothing and a store whose feed did not arrive are different "
        "answers, and a report that prints the first when it means the second sends a manager to "
        "chase a rep who sold fine. The fix for this row is an upload, not a conversation."),
    CLOSED: (
        "The store was not trading that day, so there was nothing to sell. Excluded from the run "
        "rather than counted as a zero."),
    IN_PROGRESS: (
        "The day is not over yet. A quiet morning is not a zero day, so today is excluded until it "
        "closes."),
    OFF: (
        "This person had no scheduled shift that day. Excluded from their run rather than counted "
        "as a zero against them."),
    RULE_REFUSED: (
        "This organisation's activation-type rule is refused as too broad, so what counts as an "
        "activation is not trustworthy for it yet. A zero printed here would be meaningless in "
        "either direction, so nothing is claimed until the rule is fixed."),
}

# ── GAP POLICY ────────────────────────────────────────────────────────────────────────────────────
GAP_BREAK = "break"
GAP_BRIDGE = "bridge"
GAP_POLICIES = (GAP_BREAK, GAP_BRIDGE)
#: Refused by name. Counting an unmeasured day as a zero is the defect this report exists to prevent.
GAP_REFUSED = {
    "count": ("A not-reported day may not be counted as a zero day. Absence is not zero — that is "
              "the whole reason this report exists, and a config row that switched it back on would "
              "make every missing upload look like a dead store."),
}

TRADING_SCHEDULE = "schedule"
TRADING_ALL_DAYS = "all_days"
TRADING_SOURCES = (TRADING_SCHEDULE, TRADING_ALL_DAYS)

GRAIN_STORE = "store"
GRAIN_REP = "rep"
GRAINS = (GRAIN_STORE, GRAIN_REP)

#: `_sales_cell_agg`'s distinct-transaction accumulator for each bucket the ONE predicate emits.
#: The bucket NAMES are `line_class.BUCKETS` — read from there, never spelled a second time.
_CELL_SET_OF = {"premium": "_prem", "upgrade": "_upg", "byod": "_byod"}

HOUSE_CONFIG = {
    # (a) the owner's answer: a store row with its rep rows underneath. A grain is a scope key.
    "grains": list(GRAINS),
    # Every activation-type bucket the ONE predicate emits. A BYOD line IS an activation.
    "count_classes": list(_lc.BUCKETS),
    "trading_day_source": TRADING_SCHEDULE,
    "excluded_weekdays": [],
    # (b) the owner's answer: alert after N consecutive zero days. One quiet day is noise.
    "consecutive_days": 2,
    "gap_policy": GAP_BREAK,
    # A rep with no shift that day is 'off', not a zero — unless the tenant does not schedule.
    "rep_requires_shift": True,
    # Today is still trading; a zero at 9am is not a zero day.
    "include_today": False,
    # Alerts are opt-in per tenant, exactly like storeops.tenants.epay_alerts_enabled.
    "alerts_enabled": False,
}

#: The house tenant, whose config row every org inherits until it saves its own (the platform-wide
#: default-row convention — see tile_layout.HOUSE_ORG / connector_route_policy.HOUSE_ORG).
HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

ALERT_SCOPE = "zero_sales"


class ConfigRefused(ValueError):
    """A stored config value this report refuses to honour, named with the reason. Raised at resolve
    time so a bad row is a loud failure, never a silently empty report."""


# ── config ────────────────────────────────────────────────────────────────────────────────────────
def resolve_config(row=None):
    """PURE. One org's stored config row (or None) -> the full config, house defaults filling every
    missing key. House defaults ARE the shipped behaviour, so an org with no row — and the whole
    platform before the migration is applied — behaves exactly as documented above.

    REFUSES, by name and with the reason, rather than degrading quietly:
      · a `count_classes` value that is not one of `line_class.BUCKETS` (a typo would make every
        store read zero — this report's own silent zero);
      · `gap_policy='count'` (absence counted as zero — see GAP_REFUSED);
      · an unknown `gap_policy` / `trading_day_source` / grain;
      · `consecutive_days` < 1.
    """
    cfg = dict(HOUSE_CONFIG)
    src = row if isinstance(row, dict) else {}
    for k in HOUSE_CONFIG:
        v = src.get(k)
        if v is None or (isinstance(v, (list, tuple)) and len(v) == 0 and k != "excluded_weekdays"):
            continue
        cfg[k] = list(v) if isinstance(v, (list, tuple)) else v

    grains = [str(g).strip().lower() for g in (cfg["grains"] or [])]
    bad = [g for g in grains if g not in GRAINS]
    if bad:
        raise ConfigRefused("unknown grain(s) {b}: a grain is a scope key, one of {ok}"
                            .format(b=bad, ok=list(GRAINS)))
    cfg["grains"] = [g for g in GRAINS if g in grains] or list(GRAINS)

    classes = [str(c).strip().lower() for c in (cfg["count_classes"] or [])]
    bad = [c for c in classes if c not in _lc.BUCKETS]
    if bad:
        raise ConfigRefused(
            "count_classes {b} are not activation buckets {ok}. Refused rather than honoured: a "
            "bucket name this report cannot count would make every store read zero."
            .format(b=bad, ok=list(_lc.BUCKETS)))
    if not classes:
        raise ConfigRefused("count_classes is empty — no bucket would count, so every store would "
                            "read zero. Refused.")
    cfg["count_classes"] = classes

    gp = str(cfg["gap_policy"]).strip().lower()
    if gp in GAP_REFUSED:
        raise ConfigRefused("gap_policy '{g}' is refused: {why}".format(g=gp, why=GAP_REFUSED[gp]))
    if gp not in GAP_POLICIES:
        raise ConfigRefused("unknown gap_policy '{g}' (one of {ok})".format(g=gp, ok=list(GAP_POLICIES)))
    cfg["gap_policy"] = gp

    ts = str(cfg["trading_day_source"]).strip().lower()
    if ts not in TRADING_SOURCES:
        raise ConfigRefused("unknown trading_day_source '{t}' (one of {ok})"
                            .format(t=ts, ok=list(TRADING_SOURCES)))
    cfg["trading_day_source"] = ts

    try:
        n = int(cfg["consecutive_days"])
    except (TypeError, ValueError):
        raise ConfigRefused("consecutive_days must be a whole number of days")
    if n < 1:
        raise ConfigRefused("consecutive_days must be at least 1")
    cfg["consecutive_days"] = n

    wd = []
    for d in (cfg["excluded_weekdays"] or []):
        try:
            i = int(d)
        except (TypeError, ValueError):
            raise ConfigRefused("excluded_weekdays takes whole numbers 0=Mon … 6=Sun")
        if not 0 <= i <= 6:
            raise ConfigRefused("excluded_weekdays takes 0=Mon … 6=Sun, got {d}".format(d=d))
        wd.append(i)
    cfg["excluded_weekdays"] = sorted(set(wd))
    cfg["rep_requires_shift"] = bool(cfg["rep_requires_shift"])
    cfg["include_today"] = bool(cfg["include_today"])
    cfg["alerts_enabled"] = bool(cfg["alerts_enabled"])
    return cfg


# ── small pure helpers ────────────────────────────────────────────────────────────────────────────
def _as_date(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def day_range(start, end):
    """PURE. The inclusive list of ISO day strings from `start` to `end`. Empty when either is
    unparseable or the range is inverted (a caller's bad input never raises here)."""
    s, e = _as_date(start), _as_date(end)
    if not s or not e or e < s:
        return []
    out, d = [], s
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def counts_from_cells(cells, count_classes, scope_of):
    """PURE. `_sales_cell_agg` cells -> {(scope_key, day): distinct-txn count in the counted buckets}.

    The cells were classified by THE predicate (`line_class` inside `_sales_cell_agg`); this only
    unions the per-bucket DISTINCT-TRANSACTION sets the counted classes name, so a 4-device AAL under
    one trans_id counts 1 here exactly as it does on the Sales Report. `scope_of(cell_key, cell)`
    returns the scope key for the grain being computed — that is the ONLY thing that differs between
    store grain and rep grain."""
    sets = [_CELL_SET_OF[c] for c in count_classes]
    out = {}
    for k, cell in (cells or {}).items():
        day = str((cell or {}).get("trans_date") or (k[2] if len(k) > 2 else ""))[:10]
        if not day:
            continue
        scope = scope_of(k, cell)
        if scope is None:
            continue
        tids = set()
        for s in sets:
            tids |= (cell.get(s) or set())
        slot = out.setdefault((scope, day), set())
        slot |= tids
    return {k: len(v) for k, v in out.items()}


def trading_days(scope_key, days, hours_by_scope_day, cfg):
    """PURE. -> ({day: True/False}, calendar_state).

    `hours_by_scope_day` is {(scope_key, day): scheduled_hours} — built by the caller from
    `targets_engine.scope_hours_by_day`, THE existing home of "is this store open that day". This
    function does not look at sales and invents no calendar of its own.

    calendar_state: 'scheduled' when the scope has scheduled hours somewhere in the window;
    'unknown' when it has none (the honesty fallback — every day is evaluated and the row says the
    calendar is unknown, because calling a store closed all month would silence exactly the stores
    whose data is thinnest); 'all_days' when the org does not use the schedule source."""
    ex = set(cfg["excluded_weekdays"])

    def _not_excluded(d):
        dd = _as_date(d)
        return not (dd and dd.weekday() in ex)

    if cfg["trading_day_source"] != TRADING_SCHEDULE:
        return {d: _not_excluded(d) for d in days}, "all_days"
    any_hours = any((hours_by_scope_day.get((scope_key, d)) or 0) > 0 for d in days)
    if not any_hours:
        return {d: _not_excluded(d) for d in days}, "unknown"
    return ({d: (_not_excluded(d) and (hours_by_scope_day.get((scope_key, d)) or 0) > 0)
             for d in days}, "scheduled")


# ── THE STATE OF ONE DAY ──────────────────────────────────────────────────────────────────────────
def store_day_state(counted, landed, trading, over, rules_refused=False):
    """PURE. THE classification, and the whole point of this module.

      counted        distinct transactions in the counted buckets for this store-day (0 is allowed).
      landed         did ANY row arrive for this store on this day — including a voided line or a
                     bill payment. This is the evidence that the feed covered the store, and it is
                     measured BEFORE the aggregation's skip rules, because a store-day whose only
                     rows were voided still proves the feed arrived.
      trading        was the store open (see `trading_days`).
      over           is the day finished.

    Note what is NOT evidence: other stores reporting that day. A feed that carries six of nineteen
    stores (the July 2026 partial-feed incident, §2/§3) landed for six stores and for nobody else, so a
    store's coverage is only ever its OWN rows."""
    if rules_refused:
        return RULE_REFUSED
    if not trading:
        return CLOSED
    if not over:
        return IN_PROGRESS
    if (counted or 0) > 0:
        return HAD_SALES
    if landed:
        return MEASURED_ZERO
    return NOT_REPORTED


def rep_day_state(store_state, rep_counted, rep_scheduled, schedule_known, cfg):
    """PURE. A rep-day's state — which INHERITS the store's answer to the absence question.

    If the store's feed did not land (or the org's rule is refused, or the store was shut, or the
    day is not over), the rep is that SAME state. A missing feed is ONE store-level problem, never
    every rep under it appearing to have sold nothing, and the rep row does not re-derive absence.

    Only once the store day is known to be measured does the rep's own number decide:
      sold something -> had_sales; scheduled and sold nothing -> MEASURED zero; not scheduled -> off.
    A rep with no shift ANYWHERE in the window has unknown working days (`schedule_known` False), so
    they are evaluated rather than excluded — a tenant that does not schedule in the platform must
    still get a report."""
    if store_state != HAD_SALES and store_state != MEASURED_ZERO:
        return store_state
    if (rep_counted or 0) > 0:
        return HAD_SALES
    if cfg["rep_requires_shift"] and schedule_known and not rep_scheduled:
        return OFF
    return MEASURED_ZERO


# ── RUNS ──────────────────────────────────────────────────────────────────────────────────────────
def walk_run(day_states, days, cfg):
    """PURE. Walk the window in order and return the CURRENT run (the one that includes the most
    recent evaluated day) plus the longest run in the window.

    Skipped states (closed / in-progress / off) are stepped over: they neither count nor break,
    because Saturday and Monday are consecutive TRADING days for a store shut on Sunday.
    `had_sales` resets. `measured_zero` extends. `not_reported` follows `gap_policy` — and under
    EITHER policy the gap days are recorded and reported, so the run is never silently wrong.

    Returns {'zero_days', 'first_day', 'last_day', 'gap_days', 'ended_by_gap', 'longest',
             'last_evaluated_state'}. `zero_days` counts MEASURED zeros only: a bridged gap day adds
    nothing to the number, it only fails to break it."""
    cur = {"zero_days": 0, "first_day": None, "last_day": None, "gap_days": [], "ended_by_gap": False}
    longest = 0
    last_eval = None

    def _reset(ended_by_gap=False, gap_day=None):
        return {"zero_days": 0, "first_day": None, "last_day": None,
                "gap_days": ([gap_day] if gap_day else []), "ended_by_gap": ended_by_gap}

    for d in days:
        st = day_states.get(d)
        if st is None or st in SKIPPED_STATES:
            continue
        if st == RULE_REFUSED:
            return {"zero_days": 0, "first_day": None, "last_day": None, "gap_days": [],
                    "ended_by_gap": False, "longest": 0, "last_evaluated_state": RULE_REFUSED}
        last_eval = st
        if st == HAD_SALES:
            longest = max(longest, cur["zero_days"])
            cur = _reset()
        elif st == MEASURED_ZERO:
            cur["zero_days"] += 1
            cur["first_day"] = cur["first_day"] or d
            cur["last_day"] = d
        elif st == NOT_REPORTED:
            if cfg["gap_policy"] == GAP_BRIDGE:
                # The run survives the gap, and the gap is named on it. The unknown day adds NOTHING
                # to the count — it is not a zero day, it is a day nobody measured.
                cur["gap_days"].append(d)
            else:
                longest = max(longest, cur["zero_days"])
                had = cur["zero_days"] > 0
                cur = _reset(ended_by_gap=had, gap_day=d)
    longest = max(longest, cur["zero_days"])
    out = dict(cur)
    out["longest"] = longest
    out["last_evaluated_state"] = last_eval
    return out


def run_note(run, state, cfg, trading_calendar="scheduled"):
    """PURE. The row's sentence. It names the gap whenever one touched the run, so a reader is never
    left to assume that a short run means a quiet store rather than a missing upload."""
    if state == RULE_REFUSED:
        return STATE_NOTES[RULE_REFUSED]
    if state == NOT_REPORTED:
        return STATE_NOTES[NOT_REPORTED]
    bits = []
    n = run["zero_days"]
    if n:
        bits.append("{n} consecutive day{s} with no activation or upgrade ({a} to {b})".format(
            n=n, s="" if n == 1 else "s", a=run["first_day"], b=run["last_day"]))
    else:
        bits.append("no current run of zero days")
    gaps = run["gap_days"]
    if gaps and cfg["gap_policy"] == GAP_BRIDGE:
        bits.append("the run is bridged across {n} not-reported day{s} ({d}) — those days were not "
                    "measured and are not counted as zeros".format(
                        n=len(gaps), s="" if len(gaps) == 1 else "s", d=", ".join(gaps)))
    elif run["ended_by_gap"] and gaps:
        bits.append("an earlier run ended at a not-reported day ({d}) rather than at a sale — "
                    "consecutiveness cannot be asserted across a day nobody measured".format(
                        d=", ".join(gaps)))
    elif gaps:
        bits.append("{n} day(s) in this window were not reported ({d})".format(
            n=len(gaps), d=", ".join(gaps)))
    if trading_calendar == "unknown":
        bits.append("this scope has no schedule in the window, so its trading days are unknown and "
                    "every day was evaluated")
    return "; ".join(bits) + "."


# ── THE REPORT ────────────────────────────────────────────────────────────────────────────────────
def build_report(days, scopes, counted, landed, hours, cfg, rules_refused=False,
                 refusal_note=None, as_of=None, parent_of=None, labels=None):
    """PURE. THE report, for ONE grain at a time or for both — a grain is a scope key, not a branch.

      days      the window, ISO day strings ascending (`day_range`).
      scopes    {grain: [scope_key, …]} — the stores / (store, rep) pairs in scope AFTER the
                caller's standard filters have been applied, org-scoped by the caller.
      counted   {grain: {(scope_key, day): int}} — from `counts_from_cells`.
      landed    {grain: set of (scope_key, day)} — feed coverage evidence (see `store_day_state`).
      hours     {grain: {(scope_key, day): scheduled_hours}}.
      parent_of {rep scope_key: store scope_key} — how a rep row inherits its store's day states.
      labels    {grain: {scope_key: display label}} — display only.

    Returns {'period', 'grains', 'rows', 'totals', 'config', 'rules_refused', 'note'}, where each row
    carries its per-day states, its current run, and — for every state that is not a measured number
    — a `count` of None. Never 0 for an unmeasured day: that is the invariant this whole module is
    for, and the harness arms a negative control against it."""
    days = list(days or [])
    as_of = _as_date(as_of) or (_as_date(days[-1]) if days else None)
    labels = labels or {}
    parent_of = parent_of or {}

    def _over(d):
        dd = _as_date(d)
        if not dd or not as_of:
            return True
        return dd < as_of or (dd == as_of and cfg["include_today"])

    rows = []
    store_states = {}      # store scope_key -> {day: state} (the rep grain inherits these)
    for grain in cfg["grains"]:
        if grain not in (scopes or {}):
            continue
        g_counted = (counted or {}).get(grain) or {}
        g_landed = (landed or {}).get(grain) or set()
        g_hours = (hours or {}).get(grain) or {}
        for key in (scopes.get(grain) or []):
            trading, calendar = trading_days(key, days, g_hours, cfg)
            states, counts = {}, {}
            for d in days:
                c = g_counted.get((key, d), 0)
                if grain == GRAIN_STORE:
                    st = store_day_state(c, (key, d) in g_landed, trading.get(d, True), _over(d),
                                         rules_refused)
                    store_states.setdefault(key, {})[d] = st
                else:
                    parent = parent_of.get(key)
                    pst = (store_states.get(parent) or {}).get(d)
                    if pst is None:
                        # The rep's store is not in scope (a filter narrowed it away). Fall back to
                        # this rep's OWN coverage — still never a zero for an unmeasured day.
                        pst = store_day_state(c, (key, d) in g_landed, trading.get(d, True),
                                              _over(d), rules_refused)
                    st = rep_day_state(pst, c, (g_hours.get((key, d)) or 0) > 0,
                                       calendar != "unknown", cfg)
                states[d] = st
                # THE INVARIANT: only a measured state carries a number. Everything else is None.
                counts[d] = c if st in COUNTED_STATES else None
            run = walk_run(states, days, cfg)
            tally = {}
            for st in states.values():
                tally[st] = tally.get(st, 0) + 1
            state = (RULE_REFUSED if rules_refused
                     else MEASURED_ZERO if run["zero_days"] >= 1
                     else NOT_REPORTED if tally.get(NOT_REPORTED) and not tally.get(HAD_SALES)
                     else HAD_SALES)
            rows.append({
                "grain": grain,
                "scope_key": key,
                "label": (labels.get(grain) or {}).get(key, key if isinstance(key, str)
                                                       else " · ".join(str(x) for x in key)),
                "parent": parent_of.get(key) if grain == GRAIN_REP else None,
                "state": state,
                "day_states": states,
                "day_counts": counts,
                "zero_days": run["zero_days"],
                "first_zero_day": run["first_day"],
                "last_zero_day": run["last_day"],
                "longest_zero_run": run["longest"],
                "gap_days": run["gap_days"],
                "ended_by_gap": run["ended_by_gap"],
                "trading_calendar": calendar,
                "days_measured_zero": tally.get(MEASURED_ZERO, 0),
                "days_not_reported": tally.get(NOT_REPORTED, 0),
                "days_had_sales": tally.get(HAD_SALES, 0),
                "days_closed": tally.get(CLOSED, 0) + tally.get(OFF, 0),
                "alerting": (not rules_refused and run["zero_days"] >= cfg["consecutive_days"]
                             and run["last_evaluated_state"] == MEASURED_ZERO),
                "note": run_note(run, state, cfg, calendar),
            })
    rows.sort(key=lambda r: (r["grain"] != GRAIN_STORE, -r["zero_days"], str(r["label"])))

    totals = {
        "scopes": len(rows),
        "store_days_measured_zero": sum(r["days_measured_zero"] for r in rows
                                        if r["grain"] == GRAIN_STORE),
        "store_days_not_reported": sum(r["days_not_reported"] for r in rows
                                       if r["grain"] == GRAIN_STORE),
        "stores_alerting": sum(1 for r in rows if r["grain"] == GRAIN_STORE and r["alerting"]),
        "stores_unknown_calendar": sum(1 for r in rows if r["grain"] == GRAIN_STORE
                                       and r["trading_calendar"] == "unknown"),
    }
    note = None
    if rules_refused:
        note = refusal_note or STATE_NOTES[RULE_REFUSED]
    elif totals["store_days_not_reported"]:
        note = ("{n} store-day(s) in this window were NOT REPORTED — no rows landed for that store "
                "on that day, so nothing is known about them. They are shown as 'not reported', "
                "never as zero sales, and they are excluded from every run of consecutive zero "
                "days. The fix for those rows is an upload.").format(n=totals["store_days_not_reported"])
    return {"days": days, "grains": list(cfg["grains"]), "rows": rows, "totals": totals,
            "config": dict(cfg), "rules_refused": bool(rules_refused), "note": note,
            "state_notes": dict(STATE_NOTES), "books_to": []}


# ── NOTIFICATIONS — a new KIND on the existing path, never a second mechanism ──────────────────────
def alert_items(report, cfg, store_of=None):
    """PURE. The report -> the findings worth an email: scopes whose CURRENT run has reached the
    org's N consecutive MEASURED zero days, where the most recent evaluated day is itself a measured
    zero. A run whose latest day is unknown does not alert — we do not email a manager an assertion
    we cannot make.

    A 'not_reported' scope never produces an alert item here. That is deliberate and it is not
    silence: a missing feed is a data-pipeline failure with its own existing surface (the
    import-health / connector attention providers, §20), and dressing it up as a sales alert to a
    District Manager would be exactly the false chase this report exists to prevent. The report ROW
    says it in words, and the digest footer says how many scopes could not be assessed."""
    out = []
    for r in report.get("rows") or []:
        if not r.get("alerting"):
            continue
        store = (store_of(r) if store_of else
                 (r["parent"] if r["grain"] == GRAIN_REP and r["parent"] else r["scope_key"]))
        out.append({
            "store_code": store if isinstance(store, str) else str(store),
            "grain": r["grain"],
            "label": r["label"],
            "zero_days": r["zero_days"],
            "first_zero_day": r["first_zero_day"],
            "last_zero_day": r["last_zero_day"],
            "gap_days": list(r["gap_days"]),
            "threshold": cfg["consecutive_days"],
            "gap_policy": cfg["gap_policy"],
            "note": r["note"],
        })
    out.sort(key=lambda i: (str(i["store_code"]), i["grain"] != GRAIN_STORE, str(i["label"])))
    return out


def key_parts(item):
    """This alert's identity INSIDE a store-day, for `manager_digest.ref_key`: the store, the last
    zero day, and the grain+scope. Escalates ONCE per day per recipient, exactly like ePay's."""
    return (item.get("store_code"), item.get("last_zero_day"),
            "{g}:{l}".format(g=item.get("grain"), l=item.get("label")))


def ref_key_for(today, email, item):
    """The dedup key for ONE recipient. The SPELLING lives in `manager_digest.ref_key` — the one home
    for every alert kind — and this only says which parts identify a zero-sales finding."""
    return _md.ref_key(ALERT_SCOPE, today, email, *key_parts(item))


def _esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_digest(name, items, not_assessed=0):
    """PURE. ONE manager's zero-sales digest. The caller re-invokes this with the not-yet-sent subset
    after the alert_log dedup filter — the same two-pass shape ePay uses.

    The footer states how many scopes could NOT be assessed, so a thin email is never mistaken for a
    healthy estate: "nothing to report" and "we could not look" are different sentences."""
    by_store = {}
    for it in items:
        by_store.setdefault(it.get("store_code"), []).append(it)
    blocks = []
    for store in sorted(by_store, key=lambda s: str(s)):
        trs = []
        for it in sorted(by_store[store], key=lambda x: (x.get("grain") != GRAIN_STORE,
                                                         str(x.get("label")))):
            gaps = it.get("gap_days") or []
            gap_txt = ("" if not gaps else
                       " <span style='color:#b45309'>(bridged across {n} not-reported day{s}: {d})"
                       "</span>".format(n=len(gaps), s="" if len(gaps) == 1 else "s",
                                        d=_esc(", ".join(gaps))))
            trs.append(
                "<tr>"
                "<td style='padding:3px 12px 3px 0'>{lab}</td>"
                "<td style='padding:3px 12px 3px 0'>{gr}</td>"
                "<td style='padding:3px 12px 3px 0'><strong>{n}</strong> day{s}</td>"
                "<td style='padding:3px 12px 3px 0'>{a} &rarr; {b}{gap}</td>"
                "</tr>".format(lab=_esc(it.get("label")), gr=_esc(it.get("grain")),
                               n=it.get("zero_days"), s="" if it.get("zero_days") == 1 else "s",
                               a=_esc(it.get("first_zero_day")), b=_esc(it.get("last_zero_day")),
                               gap=gap_txt))
        blocks.append("<h3 style='margin:16px 0 2px;font-size:15px'>Store {s}</h3>"
                      "<table style='border-collapse:collapse;font-size:13px'>{r}</table>"
                      .format(s=_esc(store), r="".join(trs)))
    n_items, n_stores = len(items), len(by_store)
    thr = items[0].get("threshold") if items else ""
    foot = ("Automated zero-sales alert — surfaced so the day can be looked into, not a penalty. A "
            "day counts here only when the sales feed DID carry that store and none of its "
            "transactions was an activation or an upgrade. Days with no feed at all are reported as "
            "\"not reported\" and are never counted as zeros.")
    if not_assessed:
        foot += (" {n} scope(s) could not be assessed this run because nothing landed for them — "
                 "that is a data gap, not a sales figure.".format(n=not_assessed))
    html = ("<p>Hi {nm},</p>"
            "<p>These stores you oversee have gone {thr} or more consecutive trading days with no "
            "activation and no upgrade:</p>{blocks}"
            "<p style='color:#6b7280;font-size:12px;margin-top:16px'>{foot}</p>"
            .format(nm=_esc(name or "there"), thr=_esc(thr), blocks="".join(blocks), foot=foot))
    subject = ("Zero sales — {i} scope{isuf} across {s} store{ssuf}"
               .format(i=n_items, isuf="" if n_items == 1 else "s",
                       s=n_stores, ssuf="" if n_stores == 1 else "s"))
    return {"subject": subject, "html": html}


def plan_emails(items, hierarchy_by_store, today, not_assessed=0):
    """Decide the emails to send for ONE tenant. THE FAN-OUT IS NOT HERE: who receives an alert about
    a store, that a manager with no email is skipped, that one recipient gets one digest, that an
    item reachable by two hierarchy paths is listed once, and the ref_key spelling all live in
    `manager_digest.plan_digests` — the same one ePay dereferences. This supplies only what is
    zero-sales-specific."""
    return _md.plan_digests(
        items, hierarchy_by_store, today, scope=ALERT_SCOPE, key_parts=key_parts,
        build=lambda nm, its: build_digest(nm, its, not_assessed=not_assessed),
        kind="zero_sales_digest")
