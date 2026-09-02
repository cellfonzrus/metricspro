"""Projection engine — deterministic forward-looking statements (finance roadmap Phase 4; owner
directive 2026-09-02: "projections, etc whatever a top of the line system should have").

WHAT THIS IS
------------
A PURE, config-driven trend projector over the SAME consolidated monthly series the Financial
Analysis page charts (`analysis.assemble(...)['monthly']` — i.e. the stored statement snapshots;
one math path, never a second computation from raw sources). It projects each P&L building block
(revenue, COGS, OPEX, other) forward and DERIVES the rest (GP = revenue − COGS, NI = GP − OPEX −
other), so a projected month can never carry an internally inconsistent P&L.

DETERMINISTIC ONLY — deliberately NO Claude/LLM anywhere in the math (roadmap rule): the same
inputs + config always produce the same projection, provable by the stdlib harness
(backend/harness_projection_engine.py).

METHODS (per-org config, RULE TWO — mig 941 `account_config.projection_config`, house defaults
resolved here; every choice is echoed in the payload's `assumptions` block):
  • linear          — least-squares trend over the trailing `trailing_months` window, extended
                      forward. The house workhorse for young-company data.
  • seasonal_naive  — same-month-last-year value scaled by the recent year-over-year level
                      (mean of the last 3 months ÷ mean of the same 3 months a year earlier).
                      Needs ≥ 15 months of history; otherwise falls back to linear (noted).
  • auto            — seasonal_naive when history allows, else linear.
Config OVERRIDES replace the fit where set (config wins over fit, never both):
  • growth_rate_override — revenue compounds from its last actual at this monthly fraction;
  • expense_inflation    — COGS + OPEX compound from their last actuals at this monthly fraction.
Revenue/COGS/OPEX are magnitude lines: a trend crossing zero is FLOORED at 0 and the clamp is
reported in `assumptions` (never silently). `other` (taxes/interest journal lines) keeps its sign.

DISPLAY-ONLY: every projected row is flagged `projected: true`; nothing here writes a snapshot,
books a line, or feeds a payout. Cash runway = latest cash & equivalents ÷ average projected burn
(only when the projection IS a burn — profitable trends report runway: null, reason given).
"""
from app.modules.account import _period, analysis

DEFAULTS = {
    "method": "auto",             # 'auto' | 'linear' | 'seasonal_naive'
    "trailing_months": 6,          # linear-fit window (2..24)
    "horizon_months": 3,           # months projected forward (1..24)
    "growth_rate_override": None,  # monthly fraction (e.g. 0.02 = +2%/mo) applied to REVENUE
    "expense_inflation": 0.0,      # monthly fraction applied to COGS + OPEX
}
_METHODS = ("auto", "linear", "seasonal_naive")
_PROJECTED_METRICS = ("revenue", "cogs", "opex", "other")
_FLOOR_ZERO = {"revenue", "cogs", "opex"}     # magnitude lines — a trend below 0 clamps (reported)
_SEASONAL_MIN_MONTHS = 15                      # 12 back + a 3-month recent level on both sides


def resolve_projection_config(raw):
    """House defaults overlaid with a tenant's `projection_config` JSONB — every field validated,
    anything malformed falls back to the default for THAT field (config can degrade, never crash
    a projection). PURE; the DB read lives in load_projection_config."""
    cfg = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return cfg
    m = str(raw.get("method") or "").strip().lower()
    if m in _METHODS:
        cfg["method"] = m
    for key, lo, hi in (("trailing_months", 2, 24), ("horizon_months", 1, 24)):
        try:
            v = int(raw.get(key))
            if lo <= v <= hi:
                cfg[key] = v
        except (TypeError, ValueError):
            pass
    for key, lo, hi in (("growth_rate_override", -0.5, 2.0), ("expense_inflation", -0.5, 2.0)):
        v = raw.get(key)
        if v is None:
            continue
        try:
            f = float(v)
            if lo <= f <= hi:
                cfg[key] = f
        except (TypeError, ValueError):
            pass
    return cfg


def load_projection_config(client, org_id):
    """Per-org projection config, ADAPTIVE (pre-mig-941 schema or no row ⇒ house defaults). Never
    raises — the load_bs_config posture."""
    raw = None
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("projection_config").eq("org_id", org_id).limit(1).execute().data) or []
        raw = rows[0].get("projection_config") if rows else None
    except Exception:
        raw = None
    return resolve_projection_config(raw)


# ── pure math ───────────────────────────────────────────────────────────────────────────────────
def linear_fit(vals):
    """Least-squares (intercept, slope) over vals at x = 0..n−1. n==1 ⇒ flat line at the value."""
    n = len(vals)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return float(vals[0]), 0.0
    xs = range(n)
    mean_x = (n - 1) / 2.0
    mean_y = sum(vals) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (v - mean_y) for x, v in zip(xs, vals)) / denom if denom else 0.0
    return mean_y - slope * mean_x, slope


def next_period_labels(last_label, k):
    """The k month-name labels after `last_label` ('August 2026' → ['September 2026', …])."""
    m, y = _period.parse_period(last_label or "")
    out = []
    for _ in range(max(0, k)):
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
        out.append(f"{_period._MONTHS[m]} {y}")
    return out


def _seasonal_level(vals):
    """Recent-level growth factor: mean(last 3) ÷ mean(same 3 a year earlier); 1.0 when the base
    is unusable (zero/short)."""
    if len(vals) < _SEASONAL_MIN_MONTHS:
        return 1.0
    recent = vals[-3:]
    year_ago = vals[-15:-12]
    base = sum(year_ago) / 3.0
    if not base:
        return 1.0
    return (sum(recent) / 3.0) / base


def project(monthly, cfg=None):
    """The projection payload. `monthly` = analysis.assemble(...)['monthly'] (consolidated actuals,
    chronological). Returns projected months flagged `projected: true`, with the method + every
    assumption spelled out. Deterministic; no I/O."""
    cfg = resolve_projection_config(cfg)   # idempotent on an already-resolved config
    hist = [m for m in (monthly or []) if _period.parse_period(str(m.get("period") or ""))[0]]
    if not hist:
        return {"computed": False, "series": [], "assumptions": [],
                "note": "No computed history to project from."}

    n = len(hist)
    method = cfg["method"]
    fallback_note = None
    if method == "auto":
        method = "seasonal_naive" if n >= _SEASONAL_MIN_MONTHS else "linear"
    elif method == "seasonal_naive" and n < _SEASONAL_MIN_MONTHS:
        method, fallback_note = "linear", (
            f"seasonal_naive needs ≥ {_SEASONAL_MIN_MONTHS} months of history (have {n}) — "
            f"fell back to linear.")

    window = min(cfg["trailing_months"], n)
    horizon = cfg["horizon_months"]
    labels = next_period_labels(hist[-1]["period"], horizon)
    clamped = set()

    def _metric_vals(key):
        return [analysis._r2(m.get(key)) for m in hist]

    projections = {}
    for key in _PROJECTED_METRICS:
        vals = _metric_vals(key)
        last = vals[-1]
        out = []
        if key == "revenue" and cfg["growth_rate_override"] is not None:
            g = cfg["growth_rate_override"]
            out = [last * ((1.0 + g) ** t) for t in range(1, horizon + 1)]
        elif key in ("cogs", "opex") and cfg["expense_inflation"]:
            i = cfg["expense_inflation"]
            out = [last * ((1.0 + i) ** t) for t in range(1, horizon + 1)]
        elif method == "seasonal_naive":
            level = _seasonal_level(vals)
            for t in range(1, horizon + 1):
                # projected month's absolute index is (n − 1 + t); same month last year is 12
                # earlier → n + t − 13. (The harness pinned the off-by-one: May 2026 must read
                # May 2025, not June.)
                back = n + t - 13
                base = vals[back] if 0 <= back < n else vals[-1]
                out.append(base * level)
        else:                              # linear
            b, a = linear_fit(vals[-window:])
            out = [b + a * (window - 1 + t) for t in range(1, horizon + 1)]
        cleaned = []
        for v in out:
            v = analysis._r2(v)
            if key in _FLOOR_ZERO and v < 0:
                clamped.add(key)
                v = 0.0
            cleaned.append(v)
        projections[key] = cleaned

    series = []
    for t, label in enumerate(labels):
        rev = projections["revenue"][t]
        cogs = projections["cogs"][t]
        opex = projections["opex"][t]
        other = projections["other"][t]
        gp = analysis._r2(rev - cogs)
        ni = analysis._r2(gp - opex - other)
        series.append({"period": label, "projected": True,
                       "revenue": rev, "cogs": cogs, "opex": opex, "other": other,
                       "gross_profit": gp, "net_income": ni,
                       "gross_margin_pct": analysis._pct(gp, rev),
                       "net_margin_pct": analysis._pct(ni, rev)})

    # cash runway — only meaningful when the projection is a burn
    cash = analysis._r2(hist[-1].get("cash"))
    avg_ni = analysis._r2(sum(p["net_income"] for p in series) / len(series)) if series else 0.0
    if avg_ni < 0:
        runway = {"months": round(cash / abs(avg_ni), 1) if avg_ni else None,
                  "cash": cash, "avg_projected_net_income": avg_ni}
    else:
        runway = {"months": None, "cash": cash, "avg_projected_net_income": avg_ni,
                  "reason": "profitable at the projected trend — no burn to run down"}

    assumptions = [
        f"Method: {method} over the trailing {window} month(s) of computed statements "
        f"({hist[max(0, n - window)]['period']} – {hist[-1]['period']}).",
        "Gross profit and net income are DERIVED (revenue − COGS − OPEX − other) each projected "
        "month — never trended independently.",
        "Projections are a deterministic extrapolation of the stored statements — a planning "
        "view, not a forecast promise; no booked number reads them.",
    ]
    if cfg["growth_rate_override"] is not None:
        assumptions.append(f"Revenue growth OVERRIDE: {cfg['growth_rate_override'] * 100:+.2f}%/mo "
                           f"compounded from the last actual (replaces the fitted trend — org config).")
    if cfg["expense_inflation"]:
        assumptions.append(f"Expense inflation: COGS + OPEX compound at "
                           f"{cfg['expense_inflation'] * 100:+.2f}%/mo from their last actuals "
                           f"(replaces the fitted trend — org config).")
    if fallback_note:
        assumptions.append(fallback_note)
    if clamped:
        assumptions.append("Floored at $0 where the trend crossed below zero: "
                           + ", ".join(sorted(clamped)) + " (magnitude lines cannot book negative).")

    return {"computed": True, "method": method, "config": cfg, "history_months": n,
            "window_months": window, "horizon_months": horizon,
            "series": series, "cash_runway": runway, "assumptions": assumptions}
