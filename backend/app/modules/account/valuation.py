"""Company valuation — a defensible, assumption-driven ESTIMATE range (finance roadmap Phase 5;
owner directive 2026-09-02: "act as we are building an earnest and young financial analyst system
with a probable company valuation").

WHAT THIS IS (and is NOT)
-------------------------
Standard small-business methods computed from the org's OWN stored statements — presented as a
RANGE with the method breakdown and every assumption listed inline (the "basis of preparation"
block). It is an ESTIMATE for planning, explicitly NOT an appraisal, a fairness opinion, or tax
advice; the payload carries that disclaimer and the UI must show it.

METHODS
  1. Revenue multiple   — TTM revenue × [lo, hi]. A sanity band, not the headline.
  2. SDE multiple       — TTM SDE × [lo, hi]. SDE (seller's discretionary earnings) = EBITDA +
                          configured owner addbacks (e.g. an owner salary already sitting in OPEX).
  3. EBITDA multiple    — TTM EBITDA × [lo, hi]. On these cash-basis books EBITDA ≈ net income +
                          the P&L `other` section (interest/taxes journal lines); there is no
                          booked D&A to add back — stated in the assumptions.
  4. Asset floor        — net assets from the latest Balance Sheet (assets − liabilities): the
                          liquidation-flavoured floor under any going-concern number.
  5. DCF                — the Phase-4 projection engine's DETERMINISTIC net-income series as the
                          free-cash-flow proxy (cash-basis books make NI the honest stand-in;
                          stated), discounted monthly; terminal value = terminal multiple × the
                          final projected year's FCF. A 3×3 sensitivity grid (discount rate ×
                          terminal multiple) renders alongside — a range, never one number.

EVERY NUMBER IS CONFIG (RULE TWO): multiple ranges, addbacks, discount rates, terminal multiples
and DCF horizon come from `account_config.valuation_config` (mig 941) with house defaults resolved
by `resolve_valuation_config` below — generic young-small-business placeholder ranges, published
here and cited on the page with their source ('house default' vs 'org config'). No industry number
is hard-coded into any formula; change the config and every figure moves.

PURE + STDLIB-ONLY: monthly series + config in, valuation payload out. DB reads live in the router
and `load_valuation_config`. Proof: backend/harness_valuation.py. DETERMINISTIC — no LLM anywhere.
"""
from app.modules.account import analysis

DEFAULTS = {
    # House-default placeholder ranges for a young owner-operated retail business — CONFIG, not
    # doctrine: an org overrides any of these per its own market comps (mig 941).
    "revenue_multiple_range": [0.3, 0.6],     # × TTM revenue
    "sde_multiple_range": [2.5, 4.0],         # × TTM SDE
    "ebitda_multiple_range": [3.0, 5.0],      # × TTM EBITDA
    "owner_addbacks_annual": 0.0,             # $ added back to EBITDA for SDE
    "discount_rate_range": [0.20, 0.30],      # annual, DCF (small-private-company rates)
    "terminal_multiple_range": [2.0, 3.0],    # × terminal-year projected FCF
    "dcf_horizon_months": 36,
}
_RANGE_BOUNDS = {
    "revenue_multiple_range": (0.0, 20.0),
    "sde_multiple_range": (0.0, 20.0),
    "ebitda_multiple_range": (0.0, 20.0),
    "discount_rate_range": (0.01, 1.0),
    "terminal_multiple_range": (0.0, 20.0),
}


def _r2(x):
    return analysis._r2(x)


def resolve_valuation_config(raw):
    """House defaults overlaid with the org's `valuation_config` JSONB. Per-field validation —
    a malformed field keeps ONLY that field's default (config degrades, never crashes). Each
    resolved field carries its source in the companion dict returned second."""
    cfg, src = dict(DEFAULTS), {k: "house default" for k in DEFAULTS}
    if not isinstance(raw, dict):
        return cfg, src
    for key, (lo, hi) in _RANGE_BOUNDS.items():
        v = raw.get(key)
        try:
            a, b = float(v[0]), float(v[1])
            if lo <= a <= b <= hi:
                cfg[key], src[key] = [a, b], "org config"
        except (TypeError, ValueError, IndexError, KeyError):
            pass
    v = raw.get("owner_addbacks_annual")
    try:
        f = float(v)
        if 0.0 <= f <= 10_000_000.0:
            cfg["owner_addbacks_annual"], src["owner_addbacks_annual"] = f, "org config"
    except (TypeError, ValueError):
        pass
    try:
        h = int(raw.get("dcf_horizon_months"))
        if 12 <= h <= 120:
            cfg["dcf_horizon_months"], src["dcf_horizon_months"] = h, "org config"
    except (TypeError, ValueError):
        pass
    return cfg, src


def load_valuation_config(client, org_id):
    """Per-org valuation config, ADAPTIVE (pre-mig-941 schema or no row ⇒ house defaults). Never
    raises — the load_bs_config posture."""
    raw = None
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("valuation_config").eq("org_id", org_id).limit(1).execute().data) or []
        raw = rows[0].get("valuation_config") if rows else None
    except Exception:
        raw = None
    return resolve_valuation_config(raw)


def ttm_metrics(monthly, owner_addbacks_annual=0.0):
    """Trailing-twelve-month basis from the consolidated monthly actuals (analysis.assemble shape).
    Fewer than 12 computed months ⇒ ANNUALIZED (×12/n) and flagged — never silently presented as a
    full year. EBITDA ≈ NI + `other` (interest/taxes journal lines; no booked D&A on cash books);
    SDE = EBITDA + configured owner addbacks."""
    hist = list(monthly or [])[-12:]
    n = len(hist)
    if n == 0:
        return {"months_used": 0, "annualized": False}
    scale = 12.0 / n
    rev = _r2(sum(_r2(m.get("revenue")) for m in hist) * scale)
    ni = _r2(sum(_r2(m.get("net_income")) for m in hist) * scale)
    other = _r2(sum(_r2(m.get("other")) for m in hist) * scale)
    ebitda = _r2(ni + other)
    sde = _r2(ebitda + _r2(owner_addbacks_annual))
    last = hist[-1]
    return {"months_used": n, "annualized": n < 12,
            "period_start": hist[0].get("period"), "period_end": last.get("period"),
            "ttm_revenue": rev, "ttm_net_income": ni, "ttm_other_addback": other,
            "ebitda": ebitda, "sde": sde,
            "owner_addbacks_annual": _r2(owner_addbacks_annual),
            "net_assets": _r2(_r2(last.get("assets")) - _r2(last.get("liabilities"))),
            "cash": _r2(last.get("cash"))}


def dcf_value(monthly_fcfs, annual_rate, terminal_multiple):
    """PV of a monthly FCF series + a terminal value, discounted at the annual rate compounded
    monthly. Terminal FCF year = the final 12 projected months (or the whole series when shorter),
    valued at `terminal_multiple` × that year and discounted from the horizon. Pure arithmetic."""
    fcfs = [_r2(f) for f in (monthly_fcfs or [])]
    if not fcfs:
        return 0.0
    d = (1.0 + float(annual_rate)) ** (1.0 / 12.0) - 1.0
    pv = sum(f / ((1.0 + d) ** t) for t, f in enumerate(fcfs, start=1))
    terminal_year = sum(fcfs[-12:]) * (12.0 / min(12, len(fcfs)))
    terminal = float(terminal_multiple) * terminal_year / ((1.0 + d) ** len(fcfs))
    return _r2(pv + terminal)


def _mult_method(key, label, basis_label, basis_value, rng, source, note=None):
    lo, hi = rng
    m = {"key": key, "label": label, "basis_label": basis_label,
         "basis_value": _r2(basis_value), "multiple_range": [lo, hi], "source": source,
         "low": _r2(basis_value * lo), "high": _r2(basis_value * hi),
         "mid": _r2(basis_value * (lo + hi) / 2.0)}
    if basis_value <= 0:
        m["note"] = note or ("Basis is zero/negative on the trailing period — this method is not "
                             "meaningful until earnings turn positive.")
        m["meaningful"] = False
    else:
        m["meaningful"] = True
        if note:
            m["note"] = note
    return m


def valuation(monthly, cfg=None, cfg_source=None, projected_fcfs=None, projection_meta=None):
    """The full valuation payload. `monthly` = consolidated actuals (analysis.assemble shape);
    `projected_fcfs` = the Phase-4 projection's monthly net-income series over the DCF horizon
    (deterministic; may be None ⇒ DCF skipped with the reason). Returns methods + summary range +
    the complete assumptions block. Pure; deterministic."""
    if cfg is None or cfg_source is None:
        cfg, cfg_source = resolve_valuation_config(cfg if isinstance(cfg, dict) else None)
    basis = ttm_metrics(monthly, cfg["owner_addbacks_annual"])
    if not basis.get("months_used"):
        return {"computed": False,
                "note": "No computed statements to value — compute a period first."}

    methods = [
        _mult_method("revenue_multiple", "Revenue multiple", "TTM revenue",
                     basis["ttm_revenue"], cfg["revenue_multiple_range"],
                     cfg_source["revenue_multiple_range"],
                     note="A top-line sanity band — earnings methods carry the headline."),
        _mult_method("sde_multiple", "SDE multiple", "TTM SDE (EBITDA + owner addbacks)",
                     basis["sde"], cfg["sde_multiple_range"], cfg_source["sde_multiple_range"]),
        _mult_method("ebitda_multiple", "EBITDA multiple", "TTM EBITDA (NI + interest/taxes)",
                     basis["ebitda"], cfg["ebitda_multiple_range"],
                     cfg_source["ebitda_multiple_range"]),
        {"key": "asset_floor", "label": "Asset-based floor",
         "basis_label": "Net assets (latest Balance Sheet: assets − liabilities)",
         "basis_value": basis["net_assets"], "source": "balance sheet",
         "low": basis["net_assets"], "mid": basis["net_assets"], "high": basis["net_assets"],
         "meaningful": True,
         "note": "The liquidation-flavoured floor under any going-concern estimate."},
    ]

    # ── DCF (projection-fed) ────────────────────────────────────────────────────────────────────
    r_lo, r_hi = cfg["discount_rate_range"]
    t_lo, t_hi = cfg["terminal_multiple_range"]
    if projected_fcfs:
        r_mid, t_mid = (r_lo + r_hi) / 2.0, (t_lo + t_hi) / 2.0
        rates, mults = [r_lo, r_mid, r_hi], [t_lo, t_mid, t_hi]
        grid = [{"discount_rate": r,
                 "values": [{"terminal_multiple": t, "value": dcf_value(projected_fcfs, r, t)}
                            for t in mults]} for r in rates]
        dcf_low = dcf_value(projected_fcfs, r_hi, t_lo)     # harshest corner
        dcf_high = dcf_value(projected_fcfs, r_lo, t_hi)    # friendliest corner
        dcf_mid = dcf_value(projected_fcfs, r_mid, t_mid)
        methods.append({"key": "dcf", "label": "Discounted cash flow (projection-fed)",
                        "basis_label": f"{len(projected_fcfs)} projected months of net income "
                                       f"(FCF proxy on cash-basis books)",
                        "basis_value": _r2(sum(projected_fcfs)),
                        "discount_rate_range": [r_lo, r_hi],
                        "terminal_multiple_range": [t_lo, t_hi],
                        "source": cfg_source["discount_rate_range"],
                        "low": dcf_low, "mid": dcf_mid, "high": dcf_high,
                        "meaningful": dcf_mid > 0, "sensitivity": grid,
                        "projection": projection_meta or {}})
    else:
        methods.append({"key": "dcf", "label": "Discounted cash flow (projection-fed)",
                        "meaningful": False, "low": None, "mid": None, "high": None,
                        "note": "No projection available (insufficient computed history)."})

    # ── summary range: earnings methods carry it; the asset floor lifts the low end ─────────────
    earning = [m for m in methods if m["key"] in ("sde_multiple", "ebitda_multiple", "dcf")
               and m.get("meaningful")]
    floor = basis["net_assets"]
    if earning:
        low = min(m["low"] for m in earning)
        high = max(m["high"] for m in earning)
        mids = sorted(m["mid"] for m in earning)
        mid = mids[len(mids) // 2] if len(mids) % 2 else _r2((mids[len(mids) // 2 - 1]
                                                              + mids[len(mids) // 2]) / 2.0)
        floored = floor > low
        summary = {"low": _r2(max(low, floor) if floor > 0 else low), "mid": _r2(max(mid, low)),
                   "high": _r2(high), "asset_floor": floor,
                   "asset_floor_applied": bool(floored and floor > 0),
                   "basis": "min/median/max across the meaningful earnings methods "
                            "(SDE, EBITDA, DCF); the asset floor lifts the low end when higher."}
    else:
        summary = {"low": floor, "mid": floor, "high": floor, "asset_floor": floor,
                   "asset_floor_applied": True,
                   "basis": "No earnings method is meaningful on the trailing period — the range "
                            "collapses to the asset-based floor."}

    assumptions = [
        (f"Trailing basis: {basis['months_used']} computed month(s) "
         f"({basis.get('period_start')} – {basis.get('period_end')})"
         + (" — ANNUALIZED ×12/n; treat with extra care." if basis["annualized"] else ".")),
        "EBITDA ≈ net income + the P&L 'other' section (interest/taxes journal lines); these "
        "cash-basis books carry no D&A to add back.",
        f"SDE = EBITDA + owner addbacks (${basis['owner_addbacks_annual']:,.0f}/yr — "
        f"{cfg_source['owner_addbacks_annual']}).",
        "DCF uses the deterministic Phase-4 projection's net income as the free-cash-flow proxy; "
        "terminal value = terminal multiple × the final projected year, discounted monthly.",
        "Every multiple, rate and horizon is per-org config with house defaults (mig 941) — no "
        "industry number is hard-coded; each method cites its source.",
    ]
    return {"computed": True, "as_of": basis.get("period_end"), "basis": basis,
            "config": cfg, "config_source": cfg_source, "methods": methods, "summary": summary,
            "assumptions": assumptions,
            "disclaimer": "This is a planning ESTIMATE computed from this organization's own "
                          "statements under stated assumptions — not an appraisal, fairness "
                          "opinion, or tax/legal advice. An actual sale price depends on buyer, "
                          "terms, diligence and market conditions."}
