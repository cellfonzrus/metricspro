"""PAY-INPUT DATA QUALITY — honest surfacing for the numbers a payout is computed FROM.

WHY THIS EXISTS (owner report, luxelink July 2026): the accessory %-of-GP payout looked
"inconsistent" — some $24.99 screen protectors paid $0 while a $14.99 pair of headphones paid a
number nobody could explain. Nothing in the engine was branching; the ENGINE WAS RIGHT and the
INPUTS were wrong in two different, invisible ways:

  1. GP ITSELF is unusable on a chunk of the POS export. `commcalc.raw_sales` stores only
     `ext_price` and `gp` — there is NO cost column — so the line's cost is IMPLIED: cost =
     ext_price - gp. When the POS catalog carries cost == retail on an item (the known "* BYOD"
     class), gp lands at 0 and every %-of-GP payout on that line is $0, correctly and silently.
     When cost is stored NEGATIVE, gp is larger than the price and the payout inflates.
  2. THE RATE'S UNIT is only communicated by a tooltip. `commission_rule.pct` is a FRACTION
     (0.10 = 10%) and the save path stores whatever number arrives (`safe_float(rl.get("pct"))`,
     router.py) with no clamp. A rate typed as `17.5` meaning "17.5%" is stored as 17.5 and the
     engine pays 1750% of GP — which reproduces "GP 12.00 -> $210.00" and "GP 18.00 -> $315.00"
     to the cent.

WHAT THIS MODULE IS: PURE predicates + labels that let a READ surface say so. It computes no
payout, writes nothing, and is never imported by the calculate path. Every threshold is
CONFIG (RULE TWO) — `commcalc.commission_org_config.cost_integrity_config` (migration 255),
degrading to the defaults below when the column/table is absent, so every surface works with no
migration applied.

DELIBERATELY NOT A CLASSIFIER. It never decides what an "accessory" is (see
[[accessory-flow-divergences]] — five classifiers already). Callers hand it the lines a real
Commission-Plan rule actually matched; this module only judges whether those lines' NUMBERS are
believable.
"""

# ── config (RULE TWO: thresholds a human would tune are config, not constants) ─────────────────────
COST_INTEGRITY_DEFAULTS = {
    "enabled": True,
    # a line must be worth at least this before its cost is judged (a $0 bookkeeping line is not a
    # data-quality problem, it is a bookkeeping line)
    "min_ext_price": 0.01,
    # cent-level tolerance for the equality tests below
    "tolerance": 0.005,
    # which conditions are worth flagging — each independently switchable per tenant
    "flags": {
        "cost_equals_price": True,   # gp == 0 while the line sold for money  -> cost == retail
        "cost_negative": True,       # gp  > ext_price                        -> cost < 0 (impossible)
        "cost_zero": True,           # gp == ext_price (> 0)                  -> cost == 0 (free stock?)
        "gp_negative": True,         # gp  < 0                                -> sold below cost
    },
    # a %-payout rate above this is almost certainly a whole-number percent typed into a fraction
    # field (0.10 = 10%). 1.0 = 100%; anything above it pays more than the entire basis.
    "rate_max": 1.0,
}

FLAG_LABELS = {
    "cost_equals_price": "GP is $0 — the POS catalog cost equals the retail price on this item, so a "
                         "%-of-GP payout is $0 by arithmetic.",
    "cost_negative": "GP is LARGER than the price — the implied cost is negative, which is impossible. "
                     "A %-of-GP payout on this line is inflated.",
    "cost_zero": "GP equals the price — the implied cost is $0 (free stock, or a missing cost).",
    "gp_negative": "GP is negative — this line sold below its recorded cost.",
}

RATE_FLAG_LABELS = {
    "rate_over_max": "This rule's rate is stored as a number greater than 1. The engine treats the "
                     "rate as a FRACTION (0.10 = 10%), so a rate of 17.5 pays 1750% of the basis. "
                     "If a percent was intended, the stored value should be that percent ÷ 100.",
    "rate_zero": "This rule pays a percentage but its rate is 0, so every matched line pays $0.",
}

# The payout kinds whose money is a PERCENTAGE OF A BASIS — the only ones this module judges.
PCT_KINDS = ("pct_gp", "pct_price_over_cost", "pct_mrc")
# The kinds whose basis is derived from the POS line's own price/GP (so cost integrity matters).
COST_BASED_KINDS = ("pct_gp", "pct_price_over_cost")


def normalize_cost_config(stored):
    """A stored cost_integrity_config (dict or None) → the full config, defaults filling anything the
    tenant did not state. PURE. None / garbage → COST_INTEGRITY_DEFAULTS."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in COST_INTEGRITY_DEFAULTS.items()}
    if isinstance(stored, dict):
        for k in ("enabled",):
            if k in stored:
                out[k] = bool(stored[k])
        for k in ("min_ext_price", "tolerance", "rate_max"):
            if k in stored:
                try:
                    out[k] = float(stored[k])
                except (TypeError, ValueError):
                    pass
        f = stored.get("flags")
        if isinstance(f, dict):
            for k in COST_INTEGRITY_DEFAULTS["flags"]:
                if k in f:
                    out["flags"][k] = bool(f[k])
    return out


def load_cost_config(client, org_id):
    """The org's cost-integrity config (migration 255). Degrades to the defaults on ANY error — a
    missing column must never break a report. Multi-tenant: org_id is always the caller's."""
    stored = None
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("cost_integrity_config").eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            stored = rows[0].get("cost_integrity_config")
    except Exception:
        stored = None
    cfg = normalize_cost_config(stored)
    cfg["_stored"] = stored is not None
    return cfg


# ── the arithmetic ────────────────────────────────────────────────────────────────────────────────
def _f(v):
    """float() that never raises — mirrors calculator.safe_float's tolerance without importing the
    money module into a display helper."""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(str(v).replace("$", "").replace(",", "").strip() or 0)
        except (TypeError, ValueError):
            return 0.0


def derived_cost(ext_price, gp):
    """The line's IMPLIED unit-extended cost. raw_sales has no cost column: cost = ext_price - gp.
    PURE. This is the same identity every GP surface in the module already relies on."""
    return round(_f(ext_price) - _f(gp), 2)


def line_flags(ext_price, gp, cfg=None):
    """Data-quality flag codes for ONE sale line's money columns. PURE, display-only — returns []
    when the line looks believable or when the guard is switched off.

    Ordered most-severe-first so a caller that wants one label can take flags[0]."""
    cfg = cfg or COST_INTEGRITY_DEFAULTS
    if not cfg.get("enabled", True):
        return []
    on = cfg.get("flags") or {}
    tol = _f(cfg.get("tolerance", 0.005)) or 0.005
    minp = _f(cfg.get("min_ext_price", 0.01))
    ext, g = _f(ext_price), _f(gp)
    if ext < minp:
        return []                       # a $0 line has no cost story worth telling
    out = []
    if on.get("cost_negative", True) and (g - ext) > tol:
        out.append("cost_negative")
    if on.get("gp_negative", True) and g < -tol:
        out.append("gp_negative")
    if on.get("cost_equals_price", True) and abs(g) <= tol:
        out.append("cost_equals_price")
    if on.get("cost_zero", True) and abs(g - ext) <= tol and ext > tol:
        out.append("cost_zero")
    return out


def is_suspect(ext_price, gp, cfg=None):
    """True when this line's cost/GP cannot be trusted as a payout basis. PURE."""
    return bool(line_flags(ext_price, gp, cfg))


def rate_flags(payout_kind, pct, cfg=None):
    """Data-quality flag codes for ONE plan rule's RATE. PURE, display-only.

    Only %-of-basis kinds are judged — a flat_per_unit rule's `pct` is unused and meaningless."""
    cfg = cfg or COST_INTEGRITY_DEFAULTS
    if not cfg.get("enabled", True):
        return []
    kind = str(payout_kind or "").strip().lower()
    if kind not in PCT_KINDS:
        return []
    p = _f(pct)
    rate_max = _f(cfg.get("rate_max", 1.0)) or 1.0
    out = []
    if p > rate_max:
        out.append("rate_over_max")
    elif p == 0:
        out.append("rate_zero")
    return out


def summarize(flagged_lines):
    """Roll a list of {flags, ext_price, gp, amount} up into counts + dollars per flag code.
    PURE. `amount` is what the line PAID under the live rule — reported, never changed."""
    by = {}
    for ln in flagged_lines or []:
        for code in (ln.get("flags") or []):
            b = by.setdefault(code, {"code": code, "label": FLAG_LABELS.get(code, code),
                                     "lines": 0, "ext_price": 0.0, "gp": 0.0, "paid": 0.0})
            b["lines"] += 1
            b["ext_price"] = round(b["ext_price"] + _f(ln.get("ext_price")), 2)
            b["gp"] = round(b["gp"] + _f(ln.get("gp")), 2)
            b["paid"] = round(b["paid"] + _f(ln.get("amount")), 2)
    return sorted(by.values(), key=lambda x: (-x["lines"], x["code"]))
