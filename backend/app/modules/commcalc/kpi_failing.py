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

# ── A KPI THIS PLATFORM DERIVES — metric_key → (numerator column on the rep row, basis key) ──────
# OWNER RULING 2026-09-27, verbatim: *"Denominator should be the total of new activations excluding
# upgrade and swap as reported in exec mats - data source is the same for all reports"*.
#
# `boostapp` (the carrier's Ready App rate) is the one KPI the platform COMPUTES rather than reads. Its
# numerator is a COUNT the feed carries (`boost_ready_bounty` — how many Ready App installs the rep was
# paid a bounty for). Its denominator is NOT on the feed: the owner has ruled it is the store's own
# transactions, counted exactly as Executive MTD counts them — `line_class.new_activation_units`, the
# ONE home. So the rate is resolved HERE, at score time, from a `basis` the caller supplies.
#
# WHY NOT AT INGEST, WHERE IT USED TO BE. `dlar_sweep.normalize_rep` divided the bounty by the feed's own
# `ga_prepaid`, baking a derived rate into a raw_* column. That column stopped arriving in July 2026 and
# 189 of 516 rows were written a measured `0` (index §19.28) — a denominator nobody chose, invisible in
# the table it was stored in. Ingest records what arrived; compute derives what it means. The stored
# `boost_app_pct` is LEGACY: still read for historical rows on the legacy basis, never the definition.
REP_DERIVED_RATES = {"boostapp": ("boost_ready_bounty", "new_activations")}
# the basis names a tenant may put the Ready App rate on. `exec_new_activations` is the owner's ruling;
# `feed_prepaid` is the pre-ruling behaviour (the stored `boost_app_pct`), kept so a flip is a config row
# and a closed month is never silently re-scored. RULE TWO: the choice is config, never a code branch.
BOOSTAPP_BASIS_EXEC = "exec_new_activations"     # the owner's ruling: the store's own transactions
BOOSTAPP_BASIS_FEED = "feed_prepaid"             # pre-ruling: the stored, ingest-derived boost_app_pct
BOOSTAPP_BASES = (BOOSTAPP_BASIS_EXEC, BOOSTAPP_BASIS_FEED)
BOOSTAPP_BASIS_DEFAULT = BOOSTAPP_BASIS_FEED


def resolve_boostapp_basis(raw=None):
    """A stored `payout_config.kpi_boostapp_basis` → one of `BOOSTAPP_BASES`. PURE.

    MISSING BEATS WRONG ON A MONEY GATE (§19.26): an unrecognised or absent value resolves to the
    PRE-RULING default, never to the new basis. The column arrives with mig `1029` and is NULL until the
    owner sets it, so every existing period keeps the score it was paid on until he says otherwise."""
    v = str(raw or "").strip().lower()
    return v if v in BOOSTAPP_BASES else BOOSTAPP_BASIS_DEFAULT

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


# ── THE CARRIER'S PORT-IN RATE — ONE HOME, because its meaning was wrong at the only place that
# ── read it (owner directive 2026-10-09, index §61; defect found 2026-10-09) ──────────────────────
#
# `commcalc.raw_dlar_store.port_pct` has existed since migration 002 and NOTHING on the platform
# displays it. The one consumer, `commcalc/flags.py`, read it as a port-OUT rate, multiplied it by
# 100 and flagged any store above 15. Both halves are wrong, and both were assumptions about a column
# nobody had written down:
#
#   DIRECTION. The value is PORT-INS. `dlar_sweep.normalize_store` fills it from the portal's own
#   `port_ins` field and the manual upload from the column headed 'Port %' — the share of this
#   store's activations that came in from another carrier. There is no port-OUT figure anywhere on
#   this feed. So a HIGH value is the carrier's best news about a store, and the flag was accusing
#   the estate's best porting doors.
#
#   SCALE. It is already a PERCENT, 0–100. Measured live 2026-10-09 over June 2026: values 0.0,
#   66.67, 7.69, 30.77, 53.33 … The consumer's `* 100` turned 66.67% into 6667, so EVERY store with
#   a single port-in cleared a threshold of 15 and the flag fired on all of them, every month.
#
# The fix is this function, and callers DEREFERENCE it rather than reading the column. The direction
# and the scale are stated once, here, next to the column map the rest of this module already owns —
# so the next reader of `port_pct` cannot repeat either mistake. `harness_product_mix.py` fails the
# build if a second reader of the raw column appears.
PORT_IN_RATE_COLUMN = "port_pct"
PORT_IN_RATE_MEANING = (
    "The carrier's own port-in rate for the store: the share of its activations that came in from "
    "another carrier, as a percent (0-100). HIGHER IS BETTER. The feed carries no port-out figure at "
    "all, so this number can never answer a churn question.")
# Below this the carrier's own feed says the store is not winning numbers from other carriers. House
# default; a tenant's own threshold is a config row (`watchdog_rule`, mig 1056), never a code branch.
LOW_PORT_IN_PCT = 15.0


def port_in_rate(store_row):
    """One `raw_dlar_store` row → the carrier's port-in rate as a PERCENT (0-100), or None when the
    feed did not report one. PURE.

    None, not 0.0, for a missing or unparseable value: a store the carrier did not report on has no
    rate, and a 0.0 standing in for that would read as "won no numbers" and get somebody coached."""
    if not isinstance(store_row, dict):
        return None
    raw = store_row.get(PORT_IN_RATE_COLUMN)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return round(float(str(raw).replace("%", "").replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def low_port_in(store_row, threshold=LOW_PORT_IN_PCT):
    """Is the carrier reporting a LOW port-in rate for this store? (rate, is_low) — `is_low` is False
    whenever the rate is None, because absence of a figure is never a finding. PURE."""
    rate = port_in_rate(store_row)
    if rate is None:
        return None, False
    try:
        t = float(threshold)
    except (TypeError, ValueError):
        t = LOW_PORT_IN_PCT
    return rate, bool(rate < t)


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


GRAIN_REP, GRAIN_STORE, GRAIN_NONE = "rep", "store", None


def grain_of(metric_key):
    """AT WHICH GRAIN IS THIS KPI ACTUALLY MEASURED — 'rep' | 'store' | None. DERIVED from the two feed
    maps, never stored, so it cannot drift from where the value really comes from.

    THE FACT THIS MAKES SAYABLE (owner's question, 2026-09-26): three of the seven KPIs a Boost rep is
    TIERED on — familyplan, tmr3, aal — have never been published at rep grain. Measured: all 516
    `raw_dlar_rep` rows carry NULL in `family_plan_pct`, `tmr3` and `aal_conversion`, every period since
    March. A rep reaches them only through their STORE's row. So "this rep met 3 of 7" is partly a claim
    about their store's performance, and a surface that shows the score should be able to say which is
    which. Whether the carrier publishes those three only per door is the carrier's business; that the
    platform can no longer hide the difference is ours."""
    k = str(metric_key or "").strip()
    if k in REP_DLAR_COLUMNS:
        return GRAIN_REP
    if k in STORE_KPI_COLUMNS:
        return GRAIN_STORE
    return GRAIN_NONE


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
SOURCE_REP_DERIVED = "rep_derived"   # computed here: a feed COUNT over a basis the caller supplied
SOURCES = (SOURCE_REP_DLAR, SOURCE_STORE_DLAR, SOURCE_ACTUAL, SOURCE_REP_DERIVED)


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


def derived_rate(numerator, denominator):
    """A rate the platform computes itself → float, or **None when there is no basis to compute it**.

    THE ONE arithmetic for a derived KPI. `dlar_sweep.derived_rate` is the ingest-side twin with the same
    contract (a zero, negative, absent or non-numeric denominator yields None, never a 0% failure); this
    is the score-side one, so a surface that has the denominator in hand never writes the division out.
    PURE."""
    n, d = _num(numerator), _num(denominator)
    if n is None or d is None or d <= 0:
        return None
    return n / d * 100.0


def rep_kpi_values(defs, rep_row=None, store_row=None, actuals=None,
                   rep_columns=None, store_columns=None, basis=None,
                   derived_rates=None):
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
    drates = derived_rates if derived_rates is not None else REP_DERIVED_RATES
    bas = basis if isinstance(basis, dict) else {}
    act = actuals if isinstance(actuals, dict) else {}
    values, sources = {}, {}
    for (k, _label, _col, _dflt) in defs or []:
        v, src = None, None
        # A DERIVED rep-grain rate (owner ruling 2026-09-27): the numerator is a COUNT on the rep's own
        # feed row, the denominator comes from the caller through `basis` — the ONE activation count
        # (`line_class.new_activation_units`), the same number Executive MTD prints. A caller that has no
        # such basis passes none and the metric falls through to its stored column below, so every
        # pre-ruling caller is byte-identical. No basis and no stored column → `None` → `no_data`, which
        # is the honest answer and never a 0% failure.
        if k in drates and k in bas:
            num_col, _bkey = drates[k]
            v = derived_rate((rep_row or {}).get(num_col), bas.get(k))
            if v is not None:
                values[k], sources[k] = v, SOURCE_REP_DERIVED
                continue
            # the basis was offered and is unusable (no transactions, or a zero count). That is NOT the
            # stored column's cue to stand in: the tenant chose this basis, so the answer is no_data.
            values[k], sources[k] = None, None
            continue
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
